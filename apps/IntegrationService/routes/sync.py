"""/api/v1/sync — change feed for desktop / Android / machine clients and idempotent operations.

GET  /api/v1/sync/resources     resource types the caller may synchronize
GET  /api/v1/sync/changes       ?cursor=&types=a,b&limit=   (users: it_admin / spd_supervisor / station_operator;
                                machine clients: sync.catalog.read / sync.locations.read / sync.practitioners.read /
                                sync.sessions.read)
POST /api/v1/sync/operations    Idempotency-Key header; users only (see services/operations.py)
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, current_app, request

from auth import ADMIN, OPERATOR, SUPERVISOR, current_principal, require
from serializers import json_body, render
from services import audit
from services.sync_feed import RESOURCES, visible_types

sync_bp = Blueprint("sync", __name__, url_prefix="/api/v1/sync")
USERS = (ADMIN, SUPERVISOR, OPERATOR)
SYNC_SCOPES = ("sync.catalog.read", "sync.locations.read", "sync.practitioners.read", "sync.sessions.read")
SERVICE_DIR = Path(__file__).resolve().parents[1]


def _ext(name):
    return current_app.extensions["integration"][name]


@sync_bp.get("/resources")
@require(roles=USERS, scopes=SYNC_SCOPES)
def resources():
    principal = current_principal()
    return render({"resources": [{"name": name, "href_template": RESOURCES[name].href or None}
                                 for name in visible_types(principal)]}, root="sync_resources")


@sync_bp.get("/changes")
@require(roles=USERS, scopes=SYNC_SCOPES)
def changes():
    principal = current_principal()
    page = _ext("feed").changes(principal, request.args)
    if not principal.is_user:      # machine-client reads are audited (DS07); users read their own portal data
        with _ext("database").begin() as connection:
            audit.record(connection, principal, "SYNC_CHANGES_READ", "sync_change")
    return render(page, root="sync_page")


@sync_bp.post("/operations")
@require(roles=USERS)
def operations():
    body = json_body(allowed={"type", "target_id", "payload"}, required=("type",))
    status, payload, headers = _ext("operations").execute(current_principal(), request.headers.get("Idempotency-Key"), body)
    return render(payload, root="operation_result", status=status, headers=headers)


@sync_bp.get("/openapi.yaml")
def openapi_document():
    return Response((SERVICE_DIR / "openapi.yaml").read_bytes(), mimetype="application/yaml")


@sync_bp.get("/schemas/integration.xsd")
def xml_schema():
    return Response((SERVICE_DIR / "schemas" / "integration.xsd").read_bytes(), mimetype="application/xml")
