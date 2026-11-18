"""FHIR R4 projection of PEF data (PEF-DS08 §1.3.2) — read-only, no second persistence model.

Mappings (DS08 table; everything else is out of scope):

    Organization  <- institution           id, name, active
    Location      <- operating_room        id, identifier (code), name, status (active|inactive), managingOrganization
    Practitioner  <- physician             id, name.text, active (no identifiers are invented)
    Patient       <- patient (+ resource_identifier, cat_gender)   only with scope fhir.patient.read
    Encounter     <- operation             status (scheduled->planned, in_progress->in-progress, closed->finished,
                                           cancelled->cancelled), class = v3 NullFlavor UNK (the PEF does not record the
                                           encounter class: nothing is invented), type.text = procedure type, period,
                                           location, participant (physicians, role text), subject (Patient, only with
                                           the patient scope), serviceProvider

NOT mapped (DS08: "Device / Observation ... fuera del MVP"): counting sessions, counts, discrepancies and evidence.
A counting session is NOT presented as any clinical event; the semantic approval of a mapping for it is pending.

Identifiers: logical id = PEF UUID; every resource also carries identifier `urn:pef:<table>|<uuid>` plus the rows of
`resource_identifier` (system|value) that exist. No patient identifier is created.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text

ENCOUNTER_STATUS = {"scheduled": "planned", "in_progress": "in-progress", "closed": "finished", "cancelled": "cancelled"}
NULL_FLAVOR = "http://terminology.hl7.org/CodeSystem/v3-NullFlavor"
ID_PATTERN = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")
REFERENCE_PATTERN = re.compile(r"^(Organization|Location|Practitioner|Patient|Encounter)/[A-Za-z0-9\-.]{1,64}$")
DATETIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TYPES = ("Organization", "Location", "Practitioner", "Patient", "Encounter")
SCOPES = {"Location": "fhir.location.read", "Practitioner": "fhir.practitioner.read", "Encounter": "fhir.encounter.read",
          "Patient": "fhir.patient.read", "Organization": None}


def _dt(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if value else None


def _pef_identifier(table: str, value: uuid.UUID) -> dict[str, str]:
    return {"system": f"urn:pef:{table}", "value": str(value)}


def _identifiers(connection, resource_type: str, resource_id) -> list[dict[str, str]]:
    rows = connection.execute(text(
        "SELECT system, value, use_code FROM resource_identifier WHERE resource_type = :t AND resource_id = :id "
        "AND active ORDER BY first_seen_at"), {"t": resource_type, "id": resource_id}).mappings()
    return [{"use": r["use_code"], "system": r["system"], "value": r["value"]} for r in rows]


class FhirProjection:
    def __init__(self, connection, institution_id):
        self.c, self.inst = connection, institution_id

    # --- single resources (None = absent or of another institution) ----------------------------------------
    def organization(self, rid) -> dict | None:
        row = self.c.execute(text("SELECT id, name, active FROM institution WHERE id = :id AND id = :inst"),
                             {"id": rid, "inst": self.inst}).mappings().first()
        if not row:
            return None
        return {"resourceType": "Organization", "id": str(row["id"]),
                "identifier": [_pef_identifier("institution", row["id"]), *_identifiers(self.c, "institution", row["id"])],
                "active": bool(row["active"]), "name": row["name"]}

    def location(self, rid) -> dict | None:
        row = self.c.execute(text("SELECT id, code, name, active, institution_id FROM operating_room "
                                  "WHERE id = :id AND institution_id = :inst"), {"id": rid, "inst": self.inst}).mappings().first()
        return self._location(row) if row else None

    def _location(self, row) -> dict:
        return {"resourceType": "Location", "id": str(row["id"]),
                "identifier": [_pef_identifier("operating_room", row["id"]),
                               {"system": "urn:pef:operating_room:code", "value": row["code"]},
                               *_identifiers(self.c, "operating_room", row["id"])],
                "status": "active" if row["active"] else "inactive", "name": row["name"], "mode": "instance",
                "managingOrganization": {"reference": f"Organization/{row['institution_id']}"}}

    def practitioner(self, rid) -> dict | None:
        row = self.c.execute(text("SELECT id, name, active FROM physician WHERE id = :id AND institution_id = :inst"),
                             {"id": rid, "inst": self.inst}).mappings().first()
        return self._practitioner(row) if row else None

    def _practitioner(self, row) -> dict:
        return {"resourceType": "Practitioner", "id": str(row["id"]),
                "identifier": [_pef_identifier("physician", row["id"]), *_identifiers(self.c, "physician", row["id"])],
                "active": bool(row["active"]), "name": [{"text": row["name"]}]}

    def patient(self, rid) -> dict | None:
        row = self.c.execute(text("SELECT p.id, p.display_name, p.birth_date, p.active, g.code AS gender FROM patient p "
                                  "LEFT JOIN cat_gender g ON g.id = p.gender_id WHERE p.id = :id AND p.institution_id = :inst"),
                             {"id": rid, "inst": self.inst}).mappings().first()
        if not row:
            return None
        resource = {"resourceType": "Patient", "id": str(row["id"]),
                    "identifier": [_pef_identifier("patient", row["id"]), *_identifiers(self.c, "patient", row["id"])],
                    "active": bool(row["active"]), "name": [{"text": row["display_name"]}],
                    "managingOrganization": {"reference": f"Organization/{self.inst}"}}
        if row["gender"] in ("female", "male", "other", "unknown"):
            resource["gender"] = row["gender"]
        if row["birth_date"]:
            resource["birthDate"] = row["birth_date"].isoformat()
        return resource

    def encounter(self, rid, *, with_patient: bool) -> dict | None:
        row = self.c.execute(text("""
            SELECT o.id, o.scheduled_at, o.started_at, o.ended_at, o.room_id, o.institution_id, s.code AS status,
                   pt.code AS procedure_code, pt.name AS procedure_name
            FROM operation o JOIN cat_operation_status s ON s.id = o.status_id
            LEFT JOIN cat_procedure_type pt ON pt.id = o.procedure_type_id
            WHERE o.id = :id AND o.institution_id = :inst"""), {"id": rid, "inst": self.inst}).mappings().first()
        if not row:
            return None
        resource: dict[str, Any] = {
            "resourceType": "Encounter", "id": str(row["id"]),
            "identifier": [_pef_identifier("operation", row["id"]), *_identifiers(self.c, "operation", row["id"])],
            "status": ENCOUNTER_STATUS.get(row["status"], "unknown"),
            "class": {"system": NULL_FLAVOR, "code": "UNK", "display": "unknown"},
            "serviceProvider": {"reference": f"Organization/{row['institution_id']}"},
        }
        if row["procedure_name"]:
            resource["type"] = [{"text": row["procedure_name"]}]
        start, end = row["started_at"] or row["scheduled_at"], row["ended_at"]
        if start or end:
            resource["period"] = {k: v for k, v in (("start", _dt(start)), ("end", _dt(end))) if v}
        if row["room_id"]:
            resource["location"] = [{"location": {"reference": f"Location/{row['room_id']}"}}]
        participants = self.c.execute(text(
            "SELECT op.physician_id, sr.name AS role FROM operation_physician op "
            "LEFT JOIN cat_surgical_role sr ON sr.id = op.surgical_role_id WHERE op.operation_id = :id ORDER BY op.physician_id"),
            {"id": row["id"]}).mappings().all()
        if participants:
            resource["participant"] = [{**({"type": [{"text": p["role"]}]} if p["role"] else {}),
                                        "individual": {"reference": f"Practitioner/{p['physician_id']}"}}
                                       for p in participants]
        if with_patient:
            patient_id = self.c.execute(text("SELECT patient_id FROM operation_patient WHERE operation_id = :id "
                                             "ORDER BY patient_id LIMIT 1"), {"id": row["id"]}).scalar()
            if patient_id:
                resource["subject"] = {"reference": f"Patient/{patient_id}"}
        return resource

    def read(self, resource_type: str, rid, *, with_patient: bool = False) -> dict | None:
        if resource_type == "Encounter":
            return self.encounter(rid, with_patient=with_patient)
        return {"Organization": self.organization, "Location": self.location, "Practitioner": self.practitioner,
                "Patient": self.patient}[resource_type](rid)

    # --- searches ----------------------------------------------------------------------------------------
    def search(self, resource_type: str, *, count: int, offset: int, status: str | None = None,
               with_patient: bool = False) -> tuple[int, list[dict]]:
        table = {"Location": "operating_room", "Practitioner": "physician", "Encounter": "operation"}[resource_type]
        where, params = f"{table}.institution_id = :inst", {"inst": self.inst, "n": count, "o": offset}
        join = ""
        if resource_type == "Encounter" and status:
            reverse = {v: k for k, v in ENCOUNTER_STATUS.items()}
            join = "JOIN cat_operation_status s ON s.id = operation.status_id"
            where += " AND s.code = :status"
            params["status"] = reverse.get(status, "__none__")
        total = self.c.execute(text(f"SELECT count(*) FROM {table} {join} WHERE {where}"), params).scalar_one()
        ids = self.c.execute(text(f"SELECT {table}.id FROM {table} {join} WHERE {where} ORDER BY {table}.id "
                                  "LIMIT :n OFFSET :o"), params).scalars().all()
        return total, [self.read(resource_type, i, with_patient=with_patient) for i in ids]

    def encounter_bundle(self, rid, *, with_patient: bool) -> dict | None:
        encounter = self.encounter(rid, with_patient=with_patient)
        if not encounter:
            return None
        entries = [encounter, self.organization(self.inst)]
        for item in encounter.get("location", []):
            entries.append(self.location(uuid.UUID(item["location"]["reference"].split("/")[1])))
        for participant in encounter.get("participant", []):
            entries.append(self.practitioner(uuid.UUID(participant["individual"]["reference"].split("/")[1])))
        if with_patient and encounter.get("subject"):
            entries.append(self.patient(uuid.UUID(encounter["subject"]["reference"].split("/")[1])))
        return {"resourceType": "Bundle", "type": "collection", "timestamp": _dt(datetime.now(timezone.utc)),
                "entry": [{"fullUrl": f"urn:uuid:{e['id']}", "resource": e} for e in entries if e]}


# --- structural validation (R4 rules for the elements this adapter produces) ---------------------------------


def validate(resource: dict[str, Any]) -> list[str]:
    """Problems found (empty = valid). Checks resourceType, id, required elements, codes and formats."""
    problems: list[str] = []
    kind = resource.get("resourceType")
    if kind == "Bundle":
        if resource.get("type") not in ("collection", "searchset", "transaction"):
            problems.append("Bundle.type invalid")
        for index, entry in enumerate(resource.get("entry", [])):
            problems += [f"entry[{index}].{p}" for p in validate(entry.get("resource") or {})]
        return problems
    if kind not in TYPES:
        return [f"unsupported resourceType {kind!r}"]
    if not isinstance(resource.get("id"), str) or not ID_PATTERN.fullmatch(resource["id"]):
        problems.append("id invalid")
    for ident in resource.get("identifier", []):
        if not ident.get("system") or not ident.get("value"):
            problems.append("identifier without system/value")
    if kind == "Encounter":
        if resource.get("status") not in ("planned", "arrived", "triaged", "in-progress", "onleave", "finished",
                                          "cancelled", "entered-in-error", "unknown"):
            problems.append("Encounter.status invalid")
        if not (resource.get("class") or {}).get("code"):
            problems.append("Encounter.class required")
        for key in ("start", "end"):
            value = (resource.get("period") or {}).get(key)
            if value and not DATETIME_PATTERN.fullmatch(value):
                problems.append(f"Encounter.period.{key} invalid")
    if kind == "Location" and resource.get("status") not in (None, "active", "suspended", "inactive"):
        problems.append("Location.status invalid")
    if kind == "Patient":
        if resource.get("gender") not in (None, "male", "female", "other", "unknown"):
            problems.append("Patient.gender invalid")
        if resource.get("birthDate") and not DATE_PATTERN.fullmatch(resource["birthDate"]):
            problems.append("Patient.birthDate invalid")
    for ref in _references(resource):
        if not REFERENCE_PATTERN.fullmatch(ref):
            problems.append(f"reference {ref!r} invalid")
    return problems


def _references(value) -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "reference" and isinstance(child, str):
                found.append(child)
            else:
                found += _references(child)
    elif isinstance(value, list):
        for child in value:
            found += _references(child)
    return found


def transaction_bundle(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Idempotent delivery: PUT Type/id (update-as-create with the PEF id) — a retry overwrites, never duplicates."""
    return {"resourceType": "Bundle", "type": "transaction",
            "entry": [{"fullUrl": f"urn:uuid:{e['id']}", "resource": e,
                       "request": {"method": "PUT", "url": f"{e['resourceType']}/{e['id']}"}} for e in entries]}


def capability_statement(base_url: str) -> dict[str, Any]:
    read = [{"code": "read"}]
    search = [{"code": "read"}, {"code": "search-type"}]
    return {"resourceType": "CapabilityStatement", "status": "draft", "date": date.today().isoformat(),
            "kind": "instance", "fhirVersion": "4.0.1", "format": ["application/fhir+json"],
            "implementation": {"description": "PEF read-only FHIR R4 projection (PEF-DS08, draft; semantic mapping "
                                              "pending approval)", "url": base_url},
            "rest": [{"mode": "server", "security": {"description": "OAuth2 client credentials (AuthService "
                                                                    "/api/v1/auth/token); scopes fhir.*.read"},
                      "resource": [{"type": "Organization", "interaction": read},
                                   {"type": "Location", "interaction": search},
                                   {"type": "Practitioner", "interaction": search},
                                   {"type": "Patient", "interaction": read},
                                   {"type": "Encounter", "interaction": search,
                                    "operation": [{"name": "pef-bundle", "definition": "urn:pef:operation:encounter-bundle"}]}]}]}
