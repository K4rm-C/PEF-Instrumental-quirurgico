"""Notifications API v1 (base /api/v1/notifications): the authenticated user's own notifications.

There is no creation endpoint: notifications are produced only by the outbox worker from real,
persisted business events. The recipient is always the token's user.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, current_app, request

from auth import current_principal, require_user
from serializers import parse_read_all_body, render
from services.notification_service import NotificationService

notifications_bp = Blueprint("notifications", __name__, url_prefix="/api/v1/notifications")
SERVICE_DIR = Path(__file__).resolve().parents[1]


def _service() -> NotificationService:
    return current_app.extensions["notifications"]["service"]


@notifications_bp.get("")
@require_user
def list_notifications():
    return render(_service().list(current_principal(), request.args), root="notification_page")


@notifications_bp.get("/unread-count")
@require_user
def unread_count():
    return render(_service().unread_count(current_principal()), root="unread_count")


@notifications_bp.patch("/read-all")
@require_user
def read_all():
    return render(_service().mark_all_read(current_principal(), parse_read_all_body()), root="read_all_result")


@notifications_bp.get("/<notification_id>")
@require_user
def get_notification(notification_id: str):
    return render(_service().get(current_principal(), notification_id), root="notification")


@notifications_bp.patch("/<notification_id>/read")
@require_user
def mark_read(notification_id: str):
    return render(_service().mark_read(current_principal(), notification_id), root="notification")


@notifications_bp.get("/openapi.yaml")
def openapi_document():
    return Response((SERVICE_DIR / "openapi.yaml").read_bytes(), mimetype="application/yaml")


@notifications_bp.get("/schemas/notifications.xsd")
def xml_schema():
    return Response((SERVICE_DIR / "schemas" / "notifications.xsd").read_bytes(), mimetype="application/xml")


@notifications_bp.get("/docs")
def swagger_ui():
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>PEF Notifications Service API</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css"></head>
<body><div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
<script>window.ui = SwaggerUIBundle({url: "openapi.yaml", dom_id: "#swagger-ui", persistAuthorization: true});</script>
</body></html>"""
    return Response(html, mimetype="text/html")
