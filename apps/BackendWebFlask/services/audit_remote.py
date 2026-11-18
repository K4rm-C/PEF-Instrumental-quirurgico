"""RF audit screens (Administration and Supervisor) served by AuditService (HTTP, PEF-DS09 §1.3.6).

With AUDIT_SERVICE_URL configured, both audit screens read /api/v1/audit with the access token
AuthService verified for this request; AuditService validates it again and applies the scope itself
(it_admin: its institution; spd_supervisor: the operational trail of its institution), whatever
the portal asks for. Without it, the in-process readers are used (development fallback). Exactly
one path per request; no fallback when the service fails (controlled message instead).

Nothing here writes audit rows: every producer keeps writing access_audit / count_event inside its
own transaction, so AuditService being down never affects the Operator or any other operation.
This module only adapts representations to what the RF templates already receive.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from flask import current_app, g

from i18n import N_

AUDIT_SERVICE_URL = os.getenv("AUDIT_SERVICE_URL", "").strip().rstrip("/")
AUDIT_TIMEOUT = float(os.getenv("AUDIT_SERVICE_TIMEOUT", "5"))
BASE = "/api/v1/audit"
UNAVAILABLE = N_("The audit service is temporarily unavailable. Try again in a moment.")
NIL = str(uuid.UUID(int=0))


class AuditServiceError(Exception):
    def __init__(self, status: int, code: str, message: str = "", field: str | None = None):
        super().__init__(message or code)
        self.status, self.code, self.message, self.field = status, code, message, field


def enabled() -> bool:
    return bool(AUDIT_SERVICE_URL)


def _request(path: str, params: dict) -> dict:
    token = getattr(g, "auth_access_token", None)
    if not token:
        raise AuditServiceError(401, "token_required")
    if not getattr(g, "audit_correlation_id", None):
        g.audit_correlation_id = uuid.uuid4().hex
    try:
        response = requests.get(f"{AUDIT_SERVICE_URL}{BASE}{path}", params=params, timeout=AUDIT_TIMEOUT,
                                headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                                         "X-Correlation-ID": g.audit_correlation_id})
    except requests.RequestException as exc:
        raise AuditServiceError(503, "audit_unavailable", type(exc).__name__) from exc
    if response.status_code >= 500:
        raise AuditServiceError(503, "audit_unavailable", f"HTTP {response.status_code}")
    if response.status_code >= 400:
        try:
            body = response.json() or {}
        except ValueError:
            body = {}
        code = {401: "token_required", 403: "forbidden", 404: "not_found"}.get(response.status_code, "invalid_request")
        raise AuditServiceError(response.status_code, code, body.get("message", ""),
                                (body.get("details") or {}).get("field"))
    return response.json()


def _zone(config_key: str) -> ZoneInfo:
    try:
        return ZoneInfo(current_app.config.get(config_key) or "UTC")
    except Exception:  # noqa: BLE001 - unknown zone name
        return ZoneInfo("UTC")


def _local(iso_value: str | None, zone: ZoneInfo) -> str:
    if not iso_value:
        return ""
    return datetime.fromisoformat(iso_value.replace("Z", "+00:00")).astimezone(zone).strftime("%Y-%m-%d %H:%M:%S")


def _day_bounds(day: date, zone: ZoneInfo) -> tuple[str, str]:
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


def _label(item: dict) -> str:
    resource = item.get("resource") or {}
    if resource.get("label"):
        return resource["label"]
    return "—" if resource.get("id") == NIL else (resource.get("id") or "")[:8].upper()


# --- Administration: /admin/audit-log (same structure as admin_local.audit_log.list_events) ------------


def admin_events(params: dict) -> dict:
    zone = _zone("ADMIN_DISPLAY_TIMEZONE")
    query = {"record_type": "access_audit", "page": params.get("page", 1), "per_page": params.get("per_page", 50)}
    for source, target in (("search", "q"), ("user", "user_id"), ("action", "action"), ("entity", "resource_type"),
                           ("outcome", "outcome")):
        if params.get(source):
            query[target] = params[source]
    wanted_day = None
    if params.get("date"):
        try:
            wanted_day = date.fromisoformat(params["date"])
        except ValueError:
            raise AuditServiceError(400, "invalid_date", "Use the YYYY-MM-DD date format.", "date") from None
        query["from"], query["to"] = _day_bounds(wanted_day, zone)
    if query.get("outcome") not in (None, "success", "denied"):
        query.pop("outcome")
    page = _request("/events", query)
    if page["page"] > page["total_pages"]:   # same clamp as the local pagination
        page = _request("/events", {**query, "page": page["total_pages"]})
    summary = _request("/events/summary", {"record_type": "access_audit"})
    items = [{
        "id": item["id"],
        "occurred_at": _local(item["occurred_at"], zone),
        "actor_type": (item.get("actor") or {}).get("type", "user"),
        "user": ({"id": item["actor"]["user_id"], "name": item["actor"]["name"], "email": item["actor"]["email"]}
                 if item.get("actor") else None),
        "role_codes": (item.get("actor") or {}).get("role_codes") or [],
        "action": item["action"],
        "entity": item["resource"]["type"],
        "record_id": item["resource"]["id"],
        "record_label": _label(item),
        "outcome": item["outcome"],
        "ip": item.get("ip"),
        "correlation_id": item.get("correlation_id"),
    } for item in page["items"]]
    return {
        "items": items,
        "page": page["page"],
        "per_page": page["per_page"],
        "total": page["total"],
        "total_pages": page["total_pages"],
        "shown": len(items),
        "filters": {"search": params.get("search") or "", "user": params.get("user") or "",
                    "action": params.get("action") or "", "entity": params.get("entity") or "",
                    "outcome": params.get("outcome") or "", "date": wanted_day.isoformat() if wanted_day else ""},
        "options": {
            "users": sorted(({"id": u["user_id"], "name": u["name"], "email": u["email"]} for u in summary["by_user"]),
                            key=lambda u: (u["name"] or "", u["id"])),
            "actions": sorted(group["value"] for group in summary["by_action"]),
            "entities": sorted(group["value"] for group in summary["by_resource_type"]),
        },
    }


# --- Supervisor: /supervisor/audit-log (same structure as view_data.supervisor_audit_log_data) --------

SUPERVISOR_DAYS = {"today": 0, "7d": 6, "30d": 29}


def supervisor_log(*, search="", user="", event="", session="", date="", page=1, per_page=20,
                   role_labels=None, entity_labels=None) -> dict:
    zone = _zone("ADMIN_DISPLAY_TIMEZONE")
    try:
        per_page = int(per_page)
    except (TypeError, ValueError):
        per_page = 20
    per_page = per_page if per_page in (10, 20, 50) else 20
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    query = {"record_type": "access_audit", "per_page": per_page}
    for source, target in ((search, "q"), (event, "action")):
        if source:
            query[target] = source
    for source, target in ((user, "user_id"), (session, "session_id")):
        try:
            if source:
                query[target] = str(uuid.UUID(str(source)))
        except ValueError:
            pass
    if date in SUPERVISOR_DAYS:
        today = datetime.now(zone).date()
        query["from"] = _day_bounds(today - timedelta(days=SUPERVISOR_DAYS[date]), zone)[0]
    first = _request("/events", {**query, "page": 1})
    pages = first["total_pages"]
    current = min(page, pages)
    data = first if current == 1 else _request("/events", {**query, "page": current})
    summary = _request("/events/summary", {"record_type": "access_audit"})
    role_labels, entity_labels = role_labels or {}, entity_labels or {}
    from flask_babel import gettext
    rows = []
    for item in data["items"]:
        actor = item.get("actor") or {}
        denied = item["outcome"] == "denied"
        rows.append({
            "time": _local(item["occurred_at"], zone),
            "user": actor.get("name") or N_("System"),
            "role": ", ".join(gettext(role_labels.get(code, code)) for code in actor.get("role_codes") or []) or "—",
            "event": item["action"],
            "entity": entity_labels.get(item["resource"]["type"], item["resource"]["type"]),
            "record": item["resource"].get("label") or item["resource"]["id"][:8].upper(),
            "result": "DENIED" if denied else "SUCCESS",
            "result_variant": "danger" if denied else "success",
            "details": item.get("ip") or "",
        })
    return {
        "rows": rows, "total": data["total"], "page": current, "pages": pages, "per_page": per_page,
        "options": {
            "users": sorted(({"value": u["user_id"], "label": u["name"] or u["user_id"]} for u in summary["by_user"]),
                            key=lambda u: u["label"]),
            "events": sorted(group["value"] for group in summary["by_action"]),
            "sessions": [{"value": s["session_id"], "label": s["label"]} for s in summary["by_session"]],
        },
    }
