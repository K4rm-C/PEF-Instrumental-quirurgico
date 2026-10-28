"""Audit / Event API v1 (PEF-DS09 §1.3.6), base /api/v1/audit. Read only: history is immutable.

Producers keep writing their own access_audit / count_event rows inside their transactions; this
service never receives events (no insert endpoint: no producer needs one), so it can be down without
any producer — the Operator's counting flow included — failing or losing an event.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, current_app, request

from auth import current_principal, require_audit_reader
from serializers import render
from services.event_service import EventService

events_bp = Blueprint("audit_events", __name__, url_prefix="/api/v1/audit")
SERVICE_DIR = Path(__file__).resolve().parents[1]


def _service() -> EventService:
    return current_app.extensions["audit"]["events"]


@events_bp.get("/events")
@require_audit_reader
def list_events():
    return render(_service().list_events(current_principal(), request.args), root="event_page")


@events_bp.get("/events/summary")
@require_audit_reader
def events_summary():
    return render(_service().summary(current_principal(), request.args), root="event_summary")


@events_bp.get("/events/<event_id>")
@require_audit_reader
def get_event(event_id: str):
    return render(_service().get_event(current_principal(), event_id), root="event")


# --- Contract documents (public: the contract is public, the data is not) ---------------------


@events_bp.get("/openapi.yaml")
def openapi_document():
    return Response((SERVICE_DIR / "openapi.yaml").read_bytes(), mimetype="application/yaml")


@events_bp.get("/schemas/audit.xsd")
def xml_schema():
    return Response((SERVICE_DIR / "schemas" / "audit.xsd").read_bytes(), mimetype="application/xml")


@events_bp.get("/docs")
def swagger_ui():
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>PEF Audit Service API</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css"></head>
<body><div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
<script>window.ui = SwaggerUIBundle({url: "openapi.yaml", dom_id: "#swagger-ui", persistAuthorization: true});</script>
</body></html>"""
    return Response(html, mimetype="text/html")
