"""/fhir/r4 — read-only FHIR R4 projection for authorized machine clients (PEF-DS08).

GET /fhir/r4/metadata                          CapabilityStatement (public, no data)
GET /fhir/r4/{Organization|Location|Practitioner|Patient|Encounter}/{id}
GET /fhir/r4/{Location|Practitioner|Encounter}?_count=&_offset=[&status=]   searchset Bundle
GET /fhir/r4/Encounter/{id}/$pef-bundle        collection Bundle (scope fhir.export.bundle)

Only machine clients (client credentials) are accepted, with the DS08 scope of the resource; the data of
another institution does not exist for the client (404). Every read is audited with actor_type = client.
"""

from __future__ import annotations

import uuid

from flask import Blueprint, current_app, request

from auth import current_principal, require
from errors import ApiError
from serializers import render_fhir
from services import audit
from services.fhir import SCOPES, FhirProjection, capability_statement

fhir_bp = Blueprint("fhir", __name__, url_prefix="/fhir/r4")
SEARCHABLE = ("Location", "Practitioner", "Encounter")


def _db():
    return current_app.extensions["integration"]["database"]


def _check_scope(resource_type: str) -> None:
    principal = current_principal()
    needed = SCOPES.get(resource_type)
    if needed and not principal.has_scope(needed):
        raise ApiError.forbidden(f"Scope {needed} is required")


def _id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ApiError.not_found(f"Unknown id {raw}") from None


@fhir_bp.get("/metadata")
def metadata():
    return render_fhir(capability_statement(request.host_url.rstrip("/") + "/fhir/r4"))


@fhir_bp.get("/<resource_type>/<raw_id>")
@require(any_scope_prefix="fhir.")
def read(resource_type: str, raw_id: str):
    if resource_type not in SCOPES:
        raise ApiError.not_found(f"Resource type {resource_type} is not supported")
    _check_scope(resource_type)
    principal = current_principal()
    rid = _id(raw_id)
    with _db().begin() as connection:
        resource = FhirProjection(connection, principal.institution_id).read(
            resource_type, rid, with_patient=principal.has_scope("fhir.patient.read"))
        if resource is None:
            raise ApiError.not_found(f"{resource_type}/{raw_id} not found")
        audit.record(connection, principal, f"FHIR_READ_{resource_type.upper()}", resource_type.lower(), rid)
    return render_fhir(resource)


@fhir_bp.get("/<resource_type>")
@require(any_scope_prefix="fhir.")
def search(resource_type: str):
    if resource_type not in SEARCHABLE:
        raise ApiError.not_found(f"Search on {resource_type} is not supported")
    _check_scope(resource_type)
    unknown = sorted(set(request.args) - {"_count", "_offset", "status"})
    if unknown or ("status" in request.args and resource_type != "Encounter"):
        raise ApiError.invalid_request("Unsupported search parameter(s)", details={"unknown_parameters": unknown or ["status"]})
    try:
        count, offset = int(request.args.get("_count", 20)), int(request.args.get("_offset", 0))
    except ValueError:
        raise ApiError.invalid_request("_count and _offset must be integers") from None
    if not 1 <= count <= 100 or offset < 0:
        raise ApiError.invalid_request("_count must be 1-100 and _offset >= 0")
    principal = current_principal()
    with _db().begin() as connection:
        total, resources = FhirProjection(connection, principal.institution_id).search(
            resource_type, count=count, offset=offset, status=request.args.get("status"),
            with_patient=principal.has_scope("fhir.patient.read"))
        audit.record(connection, principal, f"FHIR_SEARCH_{resource_type.upper()}", resource_type.lower())
    base = request.base_url
    links = [{"relation": "self", "url": f"{base}?_count={count}&_offset={offset}"}]
    if offset + count < total:
        links.append({"relation": "next", "url": f"{base}?_count={count}&_offset={offset + count}"})
    return render_fhir({"resourceType": "Bundle", "type": "searchset", "total": total, "link": links,
                        "entry": [{"fullUrl": f"{request.host_url.rstrip('/')}/fhir/r4/{r['resourceType']}/{r['id']}",
                                   "resource": r, "search": {"mode": "match"}} for r in resources]})


@fhir_bp.get("/Encounter/<raw_id>/$pef-bundle")
@require(scopes=("fhir.export.bundle",))
def encounter_bundle(raw_id: str):
    principal = current_principal()
    rid = _id(raw_id)
    with _db().begin() as connection:
        bundle = FhirProjection(connection, principal.institution_id).encounter_bundle(
            rid, with_patient=principal.has_scope("fhir.patient.read"))
        if bundle is None:
            raise ApiError.not_found(f"Encounter/{raw_id} not found")
        audit.record(connection, principal, "FHIR_EXPORT_BUNDLE", "operation", rid)
    return render_fhir(bundle)
