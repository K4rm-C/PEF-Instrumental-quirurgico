"""FHIR deliveries to authorized external servers (MS-07B).

- Destinations (`integration_endpoint`) are registered per institution by its IT administrator. The base URL
  must use https (plain http only with INTEGRATION_ALLOW_PLAIN_HTTP=true, isolated test stack) and its host
  must be in INTEGRATION_ALLOWED_HOSTS: no request ever leaves for a server nobody configured (nothing is
  sent to real hospitals from this repository; there is no default host).
- A delivery request (`POST /api/v1/integration/deliveries`, Idempotency-Key) records the Encounter (operation)
  to send; the same key + same body returns the same delivery; a different body is 422.
- The worker builds, at send time, a FHIR transaction Bundle with PUT Type/<PEF id> entries (update-as-create):
  a retry, or a second delivery of the same data, overwrites the same resources and never creates duplicates
  in the target. The Patient is only included if the endpoint lists "Patient" in its resource types.
- Outcomes: 2xx sent; 408/429/5xx/timeout/connection error = retry with exponential backoff up to
  INTEGRATION_DELIVERY_MAX_ATTEMPTS, then failed; any other 4xx = failed (the target refused the data). Every
  attempt is kept (integration_delivery_attempt) and the final outcome is audited (requesting user).
- The PEF never writes into a HIS database: only the FHIR REST API of the destination.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from typing import Any
from urllib.parse import urlsplit

import requests
from sqlalchemy import text

from auth import Principal
from errors import ApiError
from services import audit
from services.fhir import FhirProjection, transaction_bundle, validate

logger = logging.getLogger("integration.deliveries")
KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]{8,128}$")
ALLOWED_TYPES = ("Encounter", "Location", "Practitioner", "Organization", "Patient")


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


class DeliveryService:
    def __init__(self, database, settings):
        self.database, self.settings = database, settings

    # --- endpoints -----------------------------------------------------------------------------------------
    def check_url(self, raw: str) -> str:
        parts = urlsplit((raw or "").strip())
        host = (parts.hostname or "").lower()
        hostport = f"{host}:{parts.port}" if parts.port else host
        if parts.scheme not in ("https", "http") or not host or parts.username or parts.password or parts.query or parts.fragment:
            raise ApiError.validation_error("base_url must be an absolute http(s) URL without credentials, query or "
                                            "fragment", details={"field": "base_url", "reason": "invalid_url"})
        if parts.scheme == "http" and not self.settings.allow_plain_http:
            raise ApiError.validation_error("base_url must use https", details={"field": "base_url", "reason": "https_required"})
        if host not in self.settings.allowed_hosts and hostport not in self.settings.allowed_hosts:
            raise ApiError.validation_error("This host is not an authorized integration destination",
                                            details={"field": "base_url", "reason": "host_not_allowed"})
        return raw.strip().rstrip("/")

    def create_endpoint(self, principal: Principal, body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        if not 1 <= len(name) <= 160:
            raise ApiError.validation_error("name is required (max 160 characters)", details={"field": "name"})
        base_url = self.check_url(str(body.get("base_url") or ""))
        types = body.get("resource_types") or ["Encounter", "Location", "Practitioner", "Organization"]
        if not isinstance(types, list) or not types or any(t not in ALLOWED_TYPES for t in types) or "Encounter" not in types:
            raise ApiError.validation_error("resource_types must list FHIR types among " + ", ".join(ALLOWED_TYPES)
                                            + " and include Encounter", details={"field": "resource_types"})
        endpoint_id = uuid.uuid4()
        with self.database.begin() as connection:
            exists = connection.execute(text("SELECT 1 FROM integration_endpoint WHERE institution_id = :i AND name = :n"),
                                        {"i": principal.institution_id, "n": name}).first()
            if exists:
                raise ApiError.conflict("An endpoint with this name already exists", details={"field": "name"})
            connection.execute(text("""
                INSERT INTO integration_endpoint (id, institution_id, name, base_url, resource_types, created_by_user_id)
                VALUES (:id, :i, :n, :u, CAST(:t AS jsonb), :by)"""),
                               {"id": endpoint_id, "i": principal.institution_id, "n": name, "u": base_url,
                                "t": json.dumps(sorted(set(types))), "by": principal.id})
            audit.record(connection, principal, "CREATE_INTEGRATION_ENDPOINT", "integration_endpoint", endpoint_id)
        return self.endpoint(principal, endpoint_id)

    def endpoint(self, principal: Principal, endpoint_id) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(text("SELECT * FROM integration_endpoint WHERE id = :id AND institution_id = :i"),
                                     {"id": endpoint_id, "i": principal.institution_id}).mappings().first()
        if not row:
            raise ApiError.not_found("Endpoint not found")
        return self._endpoint(row)

    @staticmethod
    def _endpoint(row) -> dict[str, Any]:
        return {"id": str(row["id"]), "name": row["name"], "base_url": row["base_url"],
                "resource_types": list(row["resource_types"]), "active": row["active"],
                "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"])}

    def endpoints(self, principal: Principal) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute(text("SELECT * FROM integration_endpoint WHERE institution_id = :i ORDER BY name"),
                                      {"i": principal.institution_id}).mappings().all()
        return {"items": [self._endpoint(r) for r in rows]}

    def set_endpoint_active(self, principal: Principal, endpoint_id, active: bool) -> dict[str, Any]:
        with self.database.begin() as connection:
            updated = connection.execute(text("UPDATE integration_endpoint SET active = :a, updated_at = now() "
                                              "WHERE id = :id AND institution_id = :i RETURNING id"),
                                         {"a": active, "id": endpoint_id, "i": principal.institution_id}).first()
            if not updated:
                raise ApiError.not_found("Endpoint not found")
            audit.record(connection, principal, ("ACTIVATE" if active else "DEACTIVATE") + "_INTEGRATION_ENDPOINT",
                         "integration_endpoint", endpoint_id)
        return self.endpoint(principal, endpoint_id)

    # --- deliveries ----------------------------------------------------------------------------------------
    def request_delivery(self, principal: Principal, key: str | None, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if not key or not KEY_PATTERN.fullmatch(key):
            raise ApiError.invalid_request("Idempotency-Key header is required", details={"field": "Idempotency-Key"})
        try:
            endpoint_id, encounter_id = uuid.UUID(str(body.get("endpoint_id"))), uuid.UUID(str(body.get("encounter_id")))
        except (TypeError, ValueError):
            raise ApiError.invalid_request("endpoint_id and encounter_id must be UUIDs") from None
        request_hash = hashlib.sha256(f"{endpoint_id}|Encounter|{encounter_id}".encode()).hexdigest()
        with self.database.begin() as connection:
            endpoint = connection.execute(text("SELECT id, active FROM integration_endpoint WHERE id = :id AND institution_id = :i"),
                                          {"id": endpoint_id, "i": principal.institution_id}).mappings().first()
            if not endpoint:
                raise ApiError.invalid_reference("Unknown endpoint", details={"field": "endpoint_id"})
            existing = connection.execute(text("SELECT id, request_hash FROM integration_delivery "
                                               "WHERE endpoint_id = :e AND idempotency_key = :k"),
                                          {"e": endpoint_id, "k": key}).mappings().first()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise ApiError(422, "This Idempotency-Key was already used with a different request",
                                   error="idempotency_key_mismatch", details={"field": "Idempotency-Key"})
                return self.delivery(principal, existing["id"]), False
            if not endpoint["active"]:
                raise ApiError.conflict("The endpoint is inactive", details={"field": "endpoint_id"})
            if not FhirProjection(connection, principal.institution_id).encounter(encounter_id, with_patient=False):
                raise ApiError.invalid_reference("Unknown encounter (operation) of this institution",
                                                 details={"field": "encounter_id"})
            delivery_id = uuid.uuid4()
            connection.execute(text("""
                INSERT INTO integration_delivery (id, endpoint_id, institution_id, resource_type, resource_id,
                                                  idempotency_key, request_hash, requested_by_user_id, requested_by_client_id)
                VALUES (:id, :e, :i, 'Encounter', :r, :k, :h, :u, :c)"""),
                               {"id": delivery_id, "e": endpoint_id, "i": principal.institution_id, "r": encounter_id,
                                "k": key, "h": request_hash, "u": principal.id if principal.is_user else None,
                                "c": None if principal.is_user else principal.id})
            audit.record(connection, principal, "REQUEST_FHIR_DELIVERY", "integration_delivery", delivery_id)
        return self.delivery(principal, delivery_id), True

    def delivery(self, principal: Principal, delivery_id) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(text("SELECT * FROM integration_delivery WHERE id = :id AND institution_id = :i"),
                                     {"id": delivery_id, "i": principal.institution_id}).mappings().first()
            if not row:
                raise ApiError.not_found("Delivery not found")
            attempts = connection.execute(text("SELECT attempted_at, status_code, outcome, error FROM integration_delivery_attempt "
                                               "WHERE delivery_id = :id ORDER BY id"), {"id": delivery_id}).mappings().all()
        return {"id": str(row["id"]), "endpoint_id": str(row["endpoint_id"]), "resource_type": row["resource_type"],
                "resource_id": str(row["resource_id"]), "status": row["status"], "attempts_count": row["attempts"],
                "last_status_code": row["last_status_code"], "last_error": row["last_error"],
                "created_at": _iso(row["created_at"]), "sent_at": _iso(row["sent_at"]),
                "attempts": [{"attempted_at": _iso(a["attempted_at"]), "status_code": a["status_code"],
                              "outcome": a["outcome"], "error": a["error"]} for a in attempts]}

    def deliveries(self, principal: Principal, status: str | None) -> dict[str, Any]:
        if status not in (None, "pending", "sent", "failed"):
            raise ApiError.invalid_request("status must be pending, sent or failed", details={"field": "status"})
        with self.database.connect() as connection:
            ids = connection.execute(text("SELECT id FROM integration_delivery WHERE institution_id = :i "
                                          "AND (CAST(:s AS text) IS NULL OR status = :s) ORDER BY created_at DESC LIMIT 200"),
                                     {"i": principal.institution_id, "s": status}).scalars().all()
        return {"items": [self.delivery(principal, i) for i in ids]}

    # --- worker --------------------------------------------------------------------------------------------
    def deliver_due(self, limit: int = 10) -> dict[str, int]:
        stats = {"sent": 0, "retry": 0, "failed": 0}
        for _ in range(limit):
            outcome = self._deliver_one()
            if outcome is None:
                break
            stats[outcome] += 1
        return stats

    def _deliver_one(self) -> str | None:
        with self.database.begin() as connection:
            row = connection.execute(text("""
                SELECT d.*, e.base_url, e.resource_types, e.active AS endpoint_active
                FROM integration_delivery d JOIN integration_endpoint e ON e.id = d.endpoint_id
                WHERE d.status = 'pending' AND d.next_attempt_at <= now()
                ORDER BY d.next_attempt_at FOR UPDATE OF d SKIP LOCKED LIMIT 1""")).mappings().first()
            if not row:
                return None
            status_code, error, excerpt = None, None, None
            if not row["endpoint_active"]:
                outcome, error = "failed", "endpoint deactivated"
            else:
                bundle, problems = self._bundle(connection, row)
                if problems:
                    outcome, error = "failed", "invalid FHIR: " + "; ".join(problems)[:400]
                else:
                    outcome, status_code, error, excerpt = self._send(row["base_url"], bundle)
            attempts = row["attempts"] + 1
            if outcome == "retry" and attempts >= self.settings.delivery_max_attempts:
                outcome = "failed"
            delay = self.settings.delivery_backoff_seconds * (2 ** (attempts - 1))
            connection.execute(text("""
                UPDATE integration_delivery SET attempts = :n, last_status_code = :sc, last_error = :err,
                       response_excerpt = :ex, status = CAST(:st AS varchar), next_attempt_at = now() + make_interval(secs => :delay),
                       sent_at = CASE WHEN CAST(:st AS varchar) = 'sent' THEN now() ELSE sent_at END
                WHERE id = :id"""), {"n": attempts, "sc": status_code, "err": error, "ex": excerpt, "delay": delay,
                                     "st": {"sent": "sent", "failed": "failed"}.get(outcome, "pending"), "id": row["id"]})
            connection.execute(text("INSERT INTO integration_delivery_attempt (delivery_id, status_code, outcome, error) "
                                    "VALUES (:id, :sc, :o, :e)"), {"id": row["id"], "sc": status_code, "o": outcome, "e": error})
            if outcome in ("sent", "failed") and row["requested_by_user_id"]:
                audit.record_as(connection, actor_user_id=row["requested_by_user_id"], institution_id=row["institution_id"],
                                action="FHIR_DELIVERY_" + outcome.upper(), resource_type="integration_delivery",
                                resource_id=row["id"], outcome="success" if outcome == "sent" else "denied")
        logger.info("delivery %s attempt %s -> %s %s", row["id"], attempts, outcome, status_code or error or "")
        return outcome

    @staticmethod
    def _bundle(connection, row) -> tuple[dict[str, Any], list[str]]:
        types = set(row["resource_types"])
        projection = FhirProjection(connection, row["institution_id"])
        collection = projection.encounter_bundle(row["resource_id"], with_patient="Patient" in types)
        if not collection:
            return {}, ["encounter no longer exists"]
        resources = [e["resource"] for e in collection["entry"] if e["resource"]["resourceType"] in types]
        # Dependencies first (Organization, Location, Practitioner, Patient), then the Encounter.
        order = {"Organization": 0, "Location": 1, "Practitioner": 2, "Patient": 3, "Encounter": 4}
        resources.sort(key=lambda r: order[r["resourceType"]])
        for resource in resources:      # references to types the endpoint does not receive are removed
            if resource["resourceType"] == "Encounter":
                if "Patient" not in types:
                    resource.pop("subject", None)
                if "Practitioner" not in types:
                    resource.pop("participant", None)
                if "Location" not in types:
                    resource.pop("location", None)
                if "Organization" not in types:
                    resource.pop("serviceProvider", None)
            elif "Organization" not in types:
                resource.pop("managingOrganization", None)
        bundle = transaction_bundle(resources)
        return bundle, validate(bundle)

    def _send(self, base_url: str, bundle: dict[str, Any]):
        try:
            response = requests.post(base_url, data=json.dumps(bundle), timeout=self.settings.delivery_timeout_seconds,
                                     headers={"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"})
        except requests.RequestException as exc:
            return "retry", None, type(exc).__name__, None
        excerpt = response.text[:500]
        if 200 <= response.status_code < 300:
            return "sent", response.status_code, None, excerpt
        if response.status_code in (408, 429) or response.status_code >= 500:
            return "retry", response.status_code, f"HTTP {response.status_code}", excerpt
        return "failed", response.status_code, f"HTTP {response.status_code}", excerpt
