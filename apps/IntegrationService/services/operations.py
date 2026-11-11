"""Idempotent operations sent by desktop / Android clients (MS-07).

    POST /api/v1/sync/operations      Idempotency-Key: <8..128 chars [A-Za-z0-9._:-]>
    {"type": "session.schedule" | "tray.create" | "tray.update" | "notification.read",
     "target_id": "<uuid>" (update / read), "payload": {...}}

The Integration Service never applies business rules itself: the operation is FORWARDED to the service that
owns it (SessionService, CatalogService, NotificationsService) with the caller's own Bearer token, so the
same roles, institution checks, validations, version checks (409 version_conflict) and audit apply as for any
other client. Counting, final counts, validation, closes, audit history and YOLO model references are NOT
operations of this API (400 unsupported_operation): they only exist in the portal flows with their rules.

Idempotency (per authenticated principal + key):
- first request: row `in_progress`, forwarded with X-Correlation-ID = op-<hash(principal, key)>;
- a definitive answer (2xx, or 4xx other than 401/408/429) is stored and REPLAYED for every retry with the
  same key and the same request (header Idempotent-Replayed: true): no duplicate session, tray or event;
- the same key with a different request -> 422 idempotency_key_mismatch; a concurrent duplicate while the
  first is running -> 409 operation_in_progress (Retry-After);
- an uncertain outcome (timeout, connection error, 5xx: the owner may or may not have applied it) is stored
  as `unknown` and answered 502 operation_outcome_unknown. On retry the service RECONCILES: it looks for the
  owner's own audit row with that correlation id (session.scheduled / CREATE_TRAY / UPDATE_TRAY); if found,
  the operation is completed with the recovered resource id, never applied a second time; if not, it is
  forwarded again. notification.read is naturally idempotent.
- 401 / 408 / 429 / 503 answers are not stored (the client may retry with a fresh token / later).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

import requests
from sqlalchemy import text

from auth import Principal
from errors import ApiError
from services import audit

logger = logging.getLogger("integration.operations")
KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]{8,128}$")
NOT_STORED = {401, 408, 429, 503}


@dataclass(frozen=True)
class OperationType:
    upstream: str            # session | catalog | notifications
    method: str
    path: str                # may contain {target_id}
    needs_target: bool
    owner_audit_action: str | None    # action the owner writes with our correlation id (reconciliation)


OPERATIONS = {
    "session.schedule": OperationType("session", "POST", "/api/v1/sessions/schedule", False, "session.scheduled"),
    "tray.create": OperationType("catalog", "POST", "/api/v1/catalog/trays", False, "CREATE_TRAY"),
    "tray.update": OperationType("catalog", "PUT", "/api/v1/catalog/trays/{target_id}", True, "UPDATE_TRAY"),
    "notification.read": OperationType("notifications", "PATCH", "/api/v1/notifications/{target_id}/read", True, None),
}
# Explicitly refused (they belong to the counting rules of the portal); listed so the answer is clear.
REFUSED = ("count", "vision", "final", "validation", "close", "audit", "model")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class OperationService:
    def __init__(self, database, upstreams: dict[str, str], timeout: float, lease_seconds: float):
        self.database, self.upstreams, self.timeout, self.lease = database, upstreams, timeout, lease_seconds

    # --- request validation --------------------------------------------------------------------------------
    @staticmethod
    def parse(principal: Principal, key: str | None, body: dict[str, Any]) -> tuple[OperationType, str, uuid.UUID | None]:
        if not principal.is_user:
            raise ApiError.forbidden("Machine clients cannot send operations")
        if not key or not KEY_PATTERN.fullmatch(key):
            raise ApiError.invalid_request("Idempotency-Key header is required (8-128 characters [A-Za-z0-9._:-])",
                                           details={"field": "Idempotency-Key"})
        kind = body.get("type")
        if not isinstance(kind, str) or kind not in OPERATIONS:
            reason = "refused_operation" if isinstance(kind, str) and any(w in kind for w in REFUSED) else "unknown_operation"
            raise ApiError(400, "Unsupported operation", error="unsupported_operation",
                           details={"field": "type", "reason": reason, "allowed": sorted(OPERATIONS)})
        op = OPERATIONS[kind]
        target = None
        if op.needs_target:
            try:
                target = uuid.UUID(str(body.get("target_id")))
            except (TypeError, ValueError):
                raise ApiError.invalid_request("target_id must be a UUID", details={"field": "target_id"}) from None
        elif body.get("target_id") is not None:
            raise ApiError.invalid_request("target_id is not used by this operation", details={"field": "target_id"})
        payload = body.get("payload")
        if payload is not None and not isinstance(payload, dict):
            raise ApiError.invalid_request("payload must be an object", details={"field": "payload"})
        return op, kind, target

    # --- execution -----------------------------------------------------------------------------------------
    def execute(self, principal: Principal, key: str, body: dict[str, Any]) -> tuple[int, Any, dict[str, str]]:
        op, kind, target = self.parse(principal, key, body)
        payload = body.get("payload") or {}
        request_hash = hashlib.sha256(_canonical({"type": kind, "target_id": str(target) if target else None,
                                                  "payload": payload}).encode()).hexdigest()
        correlation = "op-" + hashlib.sha256(f"{principal.kind}:{principal.id}:{key}".encode()).hexdigest()[:40]
        claimed, stored = self._claim(principal, key, kind, request_hash, correlation)
        if not claimed:
            return stored
        return self._forward(principal, key, op, kind, target, payload, correlation)

    def _claim(self, principal, key, kind, request_hash, correlation):
        """(True, None) when this request must be forwarded; (False, response) otherwise."""
        params = {"pt": principal.kind, "pid": principal.id, "key": key}
        with self.database.begin() as connection:
            inserted = connection.execute(text("""
                INSERT INTO sync_operation (principal_type, principal_id, idempotency_key, institution_id, operation_type,
                                            request_hash, correlation_id)
                VALUES (:pt, :pid, :key, :inst, :kind, :hash, :corr)
                ON CONFLICT (principal_type, principal_id, idempotency_key) DO NOTHING RETURNING 1
            """), {**params, "inst": principal.institution_id, "kind": kind, "hash": request_hash,
                   "corr": correlation}).first()
            if inserted:
                return True, None
            row = connection.execute(text("""
                SELECT request_hash, status, response_status, response_body, correlation_id, operation_type,
                       EXTRACT(EPOCH FROM (now() - updated_at)) AS age
                FROM sync_operation WHERE principal_type = :pt AND principal_id = :pid AND idempotency_key = :key
                FOR UPDATE
            """), params).mappings().one()
            if row["request_hash"] != request_hash:
                raise ApiError(422, "This Idempotency-Key was already used with a different request",
                               error="idempotency_key_mismatch", details={"field": "Idempotency-Key"})
            if row["status"] == "completed":
                return False, (row["response_status"], row["response_body"], {"Idempotent-Replayed": "true"})
            if row["status"] == "in_progress" and row["age"] < self.lease:
                raise ApiError(409, "The same operation is still being processed", error="operation_in_progress",
                               details={"retry_after_seconds": 2})
            # unknown outcome, or an in-progress attempt that died: reconcile with the owner's audit trail.
            recovered = self._recover(connection, OPERATIONS[row["operation_type"]], row["correlation_id"])
            if recovered is not None:
                response = {"id": str(recovered), "recovered": True}
                self._complete(connection, params, 200, response)
                logger.info("operation %s recovered from the owner audit (resource %s)", kind, recovered)
                return False, (200, response, {"Idempotent-Replayed": "true", "Idempotent-Recovered": "true"})
            connection.execute(text("UPDATE sync_operation SET status = 'in_progress', updated_at = now() "
                                    "WHERE principal_type = :pt AND principal_id = :pid AND idempotency_key = :key"), params)
            return True, None

    @staticmethod
    def _recover(connection, op: OperationType, correlation: str):
        if not op.owner_audit_action:
            return None
        return connection.execute(text(
            "SELECT resource_id FROM access_audit WHERE correlation_id = :c AND action = :a AND outcome = 'success' "
            "ORDER BY occurred_at DESC LIMIT 1"), {"c": correlation, "a": op.owner_audit_action}).scalar()

    @staticmethod
    def _complete(connection, params, status, body):
        connection.execute(text("""
            UPDATE sync_operation SET status = 'completed', response_status = :st, response_body = CAST(:b AS jsonb),
                   updated_at = now()
            WHERE principal_type = :pt AND principal_id = :pid AND idempotency_key = :key
        """), {**params, "st": status, "b": json.dumps(body)})

    def _forward(self, principal, key, op, kind, target, payload, correlation):
        params = {"pt": principal.kind, "pid": principal.id, "key": key}
        base = self.upstreams.get(op.upstream)
        if not base:
            with self.database.begin() as connection:
                connection.execute(text("DELETE FROM sync_operation WHERE principal_type = :pt AND principal_id = :pid "
                                        "AND idempotency_key = :key"), params)
            raise ApiError.dependency_unavailable(op.upstream)
        url = base + op.path.format(target_id=target)
        try:
            response = requests.request(op.method, url, json=payload if op.method != "PATCH" else None, timeout=self.timeout,
                                        headers={"Authorization": f"Bearer {principal.token}", "X-Correlation-ID": correlation,
                                                 "Accept": "application/json"})
        except requests.RequestException as exc:
            return self._uncertain(principal, params, kind, f"{type(exc).__name__}")
        if response.status_code >= 500 and response.status_code != 503:
            return self._uncertain(principal, params, kind, f"HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            body = {"message": response.text[:500]}
        with self.database.begin() as connection:
            if response.status_code in NOT_STORED:
                connection.execute(text("DELETE FROM sync_operation WHERE principal_type = :pt AND principal_id = :pid "
                                        "AND idempotency_key = :key"), params)
            else:
                self._complete(connection, params, response.status_code, body)
            audit.record(connection, principal, f"SYNC_OPERATION_{kind.upper().replace('.', '_')}", "sync_operation",
                         _uuid(body.get("id")) if isinstance(body, dict) else None,
                         outcome="success" if response.status_code < 400 else "denied", correlation_id=correlation)
        logger.info("operation %s -> %s %s", kind, op.upstream, response.status_code)
        return response.status_code, body, {"Idempotent-Replayed": "false"}

    def _uncertain(self, principal, params, kind, reason):
        with self.database.begin() as connection:
            connection.execute(text("UPDATE sync_operation SET status = 'unknown', updated_at = now() "
                                    "WHERE principal_type = :pt AND principal_id = :pid AND idempotency_key = :key"), params)
        logger.warning("operation %s outcome unknown (%s)", kind, reason)
        raise ApiError(502, "The owner service did not answer; retry with the same Idempotency-Key",
                       error="operation_outcome_unknown", details={"reason": reason})


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
