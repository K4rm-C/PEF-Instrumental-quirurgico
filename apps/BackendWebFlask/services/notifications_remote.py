"""Internal notifications of the signed-in user, served by NotificationsService (HTTP, /api/v1/notifications).

Read side only: the portal lists the user's notifications, shows the unread count and marks them read,
always with the access token AuthService verified for this request; NotificationsService validates it
again and only ever answers with the token user's own notifications of their institution.

Nothing here produces notifications. They are generated asynchronously by the service's worker from
the business rows the portal and SessionService already write (database outbox), so no Operator or
Supervisor operation calls this module or waits on it. If the service is down, only the bell and the
/notifications page degrade (controlled notice); every other screen works as before.
Without NOTIFICATIONS_SERVICE_URL notifications are disabled in the portal (there is no local fallback).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import current_app, g

from i18n import N_

NOTIFICATIONS_SERVICE_URL = os.getenv("NOTIFICATIONS_SERVICE_URL", "").strip().rstrip("/")
NOTIFICATIONS_TIMEOUT = float(os.getenv("NOTIFICATIONS_SERVICE_TIMEOUT", "3"))
BASE = "/api/v1/notifications"
UNAVAILABLE = N_("Notifications are temporarily unavailable. Your work is not affected; try again in a moment.")
DISABLED = N_("Notifications are not enabled in this installation.")
TYPES = ("session_scheduled", "discrepancy_pending", "correction_required", "discrepancy_resolved", "session_closed")
# Localized in the portal from the type (the service's English title/message are a neutral fallback).
TITLES = {
    "session_scheduled": N_("Session scheduled"),
    "discrepancy_pending": N_("Discrepancy pending review"),
    "correction_required": N_("Correction required"),
    "discrepancy_resolved": N_("Discrepancy resolved"),
    "session_closed": N_("Session closed"),
}
MESSAGES = {
    "session_scheduled": N_("Counting session %(label)s has been scheduled for you."),
    "discrepancy_pending": N_("Counting session %(label)s was reported with discrepancies and awaits your review."),
    "correction_required": N_("Counting session %(label)s requires correction: its discrepancies are under SPD review."),
    "discrepancy_resolved": N_("The discrepancies of counting session %(label)s have been resolved."),
    "session_closed": N_("Counting session %(label)s has been closed."),
}


class NotificationsServiceError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.status, self.code, self.message = status, code, message


def enabled() -> bool:
    return bool(NOTIFICATIONS_SERVICE_URL)


def _request(method: str, path: str, *, params: dict | None = None, json_body: dict | None = None) -> dict:
    if not enabled():
        raise NotificationsServiceError(503, "notifications_disabled")
    token = getattr(g, "auth_access_token", None)
    if not token:
        raise NotificationsServiceError(401, "token_required")
    if not getattr(g, "notifications_correlation_id", None):
        g.notifications_correlation_id = uuid.uuid4().hex
    try:
        response = requests.request(method, f"{NOTIFICATIONS_SERVICE_URL}{BASE}{path}", params=params, json=json_body,
                                    timeout=NOTIFICATIONS_TIMEOUT,
                                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                                             "X-Correlation-ID": g.notifications_correlation_id})
    except requests.RequestException as exc:
        raise NotificationsServiceError(503, "notifications_unavailable", type(exc).__name__) from exc
    if response.status_code >= 500:
        raise NotificationsServiceError(503, "notifications_unavailable", f"HTTP {response.status_code}")
    if response.status_code >= 400:
        try:
            body = response.json() or {}
        except ValueError:
            body = {}
        code = {401: "token_required", 404: "not_found"}.get(response.status_code, "invalid_request")
        raise NotificationsServiceError(response.status_code, code, body.get("message", ""))
    return response.json()


def _local(iso_value: str | None) -> str:
    if not iso_value:
        return ""
    try:
        zone = ZoneInfo(current_app.config.get("ADMIN_DISPLAY_TIMEZONE") or "UTC")
    except Exception:  # noqa: BLE001 - unknown zone name
        zone = ZoneInfo("UTC")
    return datetime.fromisoformat(iso_value.replace("Z", "+00:00")).astimezone(zone).strftime("%Y-%m-%d %H:%M")


def _row(item: dict, session_url) -> dict:
    data = item.get("data") or {}
    kind = item.get("type")
    return {
        "id": item["id"],
        "type": kind,
        "unread": item.get("status") == "unread",
        "title": TITLES.get(kind) or item.get("title") or kind,
        "message": MESSAGES.get(kind) or item.get("message") or "",
        "label": data.get("session_label") or "",
        "scheduled_at": _local(data.get("scheduled_at")),
        "open_discrepancies": data.get("open_discrepancies"),
        "created_at": _local(item.get("created_at")),
        "read_at": _local(item.get("read_at")),
        "session_url": session_url(item["session_id"]) if item.get("session_id") else None,
    }


def page(*, status: str = "", kind: str = "", page_number=1, session_url=lambda session_id: None) -> dict:
    params = {"page": page_number, "per_page": 20}
    if status in ("unread", "read"):
        params["status"] = status
    if kind in TYPES:
        params["type"] = kind
    body = _request("GET", "", params=params)
    if body["page"] > max(body["total_pages"], 1):
        body = _request("GET", "", params={**params, "page": max(body["total_pages"], 1)})
    return {"rows": [_row(item, session_url) for item in body["items"]], "page": body["page"],
            "pages": max(body["total_pages"], 1), "total": body["total"], "unread": body["unread"]}


def unread_count() -> int:
    return int(_request("GET", "/unread-count")["unread"])


def mark_read(notification_id: str) -> dict:
    return _request("PATCH", f"/{notification_id}/read")


def mark_all_read() -> dict:
    return _request("PATCH", "/read-all")
