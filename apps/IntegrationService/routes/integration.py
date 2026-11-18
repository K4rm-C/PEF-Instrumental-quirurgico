"""/api/v1/integration — FHIR delivery destinations and deliveries (it_admin of the institution only).

Not published on the API Gateway: it is an administration console API (see GUIA_INTEGRACION_FHIR.md).
"""

from __future__ import annotations

import uuid

from flask import Blueprint, Response, current_app, request

from auth import ADMIN, current_principal, require
from errors import ApiError
from serializers import json_body, render

integration_bp = Blueprint("integration", __name__, url_prefix="/api/v1/integration")


def _deliveries():
    return current_app.extensions["integration"]["deliveries"]


def _uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ApiError.not_found() from None


@integration_bp.get("/endpoints")
@require(roles=(ADMIN,))
def endpoints():
    return render(_deliveries().endpoints(current_principal()), root="endpoint_list")


@integration_bp.post("/endpoints")
@require(roles=(ADMIN,))
def create_endpoint():
    body = json_body(allowed={"name", "base_url", "resource_types"}, required=("name", "base_url"))
    return render(_deliveries().create_endpoint(current_principal(), body), root="endpoint", status=201)


@integration_bp.patch("/endpoints/<raw_id>")
@require(roles=(ADMIN,))
def update_endpoint(raw_id: str):
    body = json_body(allowed={"active"}, required=("active",))
    if not isinstance(body["active"], bool):
        raise ApiError.invalid_request("active must be a boolean", details={"field": "active"})
    return render(_deliveries().set_endpoint_active(current_principal(), _uuid(raw_id), body["active"]), root="endpoint")


@integration_bp.post("/deliveries")
@require(roles=(ADMIN,))
def request_delivery():
    body = json_body(allowed={"endpoint_id", "encounter_id"}, required=("endpoint_id", "encounter_id"))
    item, created = _deliveries().request_delivery(current_principal(), request.headers.get("Idempotency-Key"), body)
    return render(item, root="delivery", status=202 if created else 200,
                  headers={"Idempotent-Replayed": "false" if created else "true"})


@integration_bp.get("/deliveries")
@require(roles=(ADMIN,))
def deliveries():
    unknown = sorted(set(request.args) - {"status"})
    if unknown:
        raise ApiError.invalid_request("Unknown query parameter(s)", details={"unknown_parameters": unknown})
    return render(_deliveries().deliveries(current_principal(), request.args.get("status")), root="delivery_list")


@integration_bp.get("/deliveries/<raw_id>")
@require(roles=(ADMIN,))
def delivery(raw_id: str):
    return render(_deliveries().delivery(current_principal(), _uuid(raw_id)), root="delivery")


# --- MS-12: traceable batch exports of closed sessions (it_admin or client scope export.sessions.batch) -----------
EXPORT_SCOPE = "export.sessions.batch"


def _exports():
    return current_app.extensions["integration"]["exports"]


@integration_bp.post("/exports")
@require(roles=(ADMIN,), scopes=(EXPORT_SCOPE,))
def create_export():
    body = json_body(allowed={"format", "period_from", "period_to"}, required=("period_from", "period_to"))
    return render(_exports().create(current_principal(), body), root="export_batch", status=201)


@integration_bp.get("/exports")
@require(roles=(ADMIN,), scopes=(EXPORT_SCOPE,))
def list_exports():
    return render(_exports().list(current_principal(), request.args), root="export_list")


@integration_bp.get("/exports/<raw_id>")
@require(roles=(ADMIN,), scopes=(EXPORT_SCOPE,))
def get_export(raw_id: str):
    return render(_exports().get(current_principal(), _uuid(raw_id)), root="export_batch")


@integration_bp.get("/exports/<raw_id>/content")
@require(roles=(ADMIN,), scopes=(EXPORT_SCOPE,))
def export_content(raw_id: str):
    """The exact stored bytes (same SHA-256 as when the batch was created); every download is audited."""
    content, sha, fmt = _exports().content(current_principal(), _uuid(raw_id))
    response = Response(content, mimetype="application/json" if fmt == "json" else "application/xml")
    response.headers["Content-Disposition"] = f'attachment; filename="pef-batch-{raw_id}.{fmt}"'
    response.headers["X-Content-SHA256"] = sha
    response.headers["Cache-Control"] = "no-store"
    return response
