"""Monitoring API v1 (base /api/v1/monitoring). Read-only: there is no endpoint that writes checks.

| Endpoint                     | it_admin                              | spd_supervisor                  |
| GET /services                | current state with technical detail   | status and last check only      |
| GET /services/{name}         | detail, recent checks, last 24 h      | 403                             |
| GET /history                 | paginated checks with filters         | 403                             |
| GET /summary                 | availability + latency aggregates     | availability aggregates only    |
Any other role: 403.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, current_app, request

from auth import ADMIN, SUPERVISOR, current_principal, is_admin, require_roles
from serializers import render
from services.monitoring_service import MonitoringService

monitoring_bp = Blueprint("monitoring", __name__, url_prefix="/api/v1/monitoring")
SERVICE_DIR = Path(__file__).resolve().parents[1]


def _service() -> MonitoringService:
    return current_app.extensions["monitoring"]["service"]


@monitoring_bp.get("/services")
@require_roles(ADMIN, SUPERVISOR)
def services():
    return render(_service().services(request.args, admin=is_admin(current_principal())), root="service_list")


@monitoring_bp.get("/services/<name>")
@require_roles(ADMIN)
def service(name: str):
    return render(_service().service(name, request.args), root="service")


@monitoring_bp.get("/history")
@require_roles(ADMIN)
def history():
    return render(_service().history(request.args), root="check_page")


@monitoring_bp.get("/summary")
@require_roles(ADMIN, SUPERVISOR)
def summary():
    return render(_service().summary(request.args, admin=is_admin(current_principal())), root="summary")


@monitoring_bp.get("/openapi.yaml")
def openapi_document():
    return Response((SERVICE_DIR / "openapi.yaml").read_bytes(), mimetype="application/yaml")


@monitoring_bp.get("/schemas/monitoring.xsd")
def xml_schema():
    return Response((SERVICE_DIR / "schemas" / "monitoring.xsd").read_bytes(), mimetype="application/xml")


@monitoring_bp.get("/docs")
def swagger_ui():
    return Response(SWAGGER_HTML, mimetype="text/html")


SWAGGER_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>PEF Monitoring Service — API v1</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css"></head>
<body><div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
<script>window.ui = SwaggerUIBundle({url: "openapi.yaml", dom_id: "#swagger-ui", persistAuthorization: true});</script>
</body></html>"""
