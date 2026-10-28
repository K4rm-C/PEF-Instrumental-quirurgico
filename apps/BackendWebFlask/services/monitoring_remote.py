"""Technical status screens served by MonitoringService (HTTP, /api/v1/monitoring).

Read side only, used by two screens:
- Administration "System status": the technical detail;
- the Supervisor's "Services health": an availability summary.

The access token AuthService verified for this request is forwarded, and MonitoringService applies
the role scope itself (it_admin: detail; spd_supervisor: summary; others: 403).

No Operator, Supervisor or Administration operation calls this module: only those two screens do.
If MonitoringService is down, they show a controlled notice and nothing else changes.
Without MONITORING_SERVICE_URL the screens say monitoring is not enabled (there is no local fallback:
the portal never fabricates a status it did not get from a real measurement).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from flask import current_app, g

from i18n import N_

MONITORING_SERVICE_URL = os.getenv("MONITORING_SERVICE_URL", "").strip().rstrip("/")
MONITORING_TIMEOUT = float(os.getenv("MONITORING_SERVICE_TIMEOUT", "3"))
BASE = "/api/v1/monitoring"
UNAVAILABLE = N_("The monitoring service cannot be reached right now. Other screens and counting are not affected.")
DISABLED = N_("Monitoring is not enabled in this installation.")

COMPONENT_LABELS = {
    "web": N_("Web portal"), "auth": N_("Auth / Identity Service"), "catalog": N_("Catalog Service"),
    "session": N_("Session / Counting Service"), "evidence": N_("Evidence / File Service"),
    "audit": N_("Audit / Event Service"), "notifications": N_("Notifications Service"),
    "notifications-worker": N_("Notifications worker"), "vision": N_("Vision worker"), "postgres": "PostgreSQL",
    "redis": "Redis", "mongo": "MongoDB", "garage": N_("Garage (object storage)"),
}
STATUS_LABELS = {"up": N_("Available"), "degraded": N_("Degraded"), "down": N_("Unavailable"), "unknown": N_("Unknown")}
STATUS_VARIANTS = {"up": "success", "degraded": "warning", "down": "danger", "unknown": "neutral"}
FRESHNESS_LABELS = {"stale": N_("No recent check"), "never_checked": N_("Never checked"), "current": ""}
ERROR_LABELS = {
    "not_configured": N_("Not configured"), "timeout": N_("No answer in time"), "connect_timeout": N_("No connection in time"),
    "connection_refused": N_("Connection refused"), "dns_error": N_("Host not found"),
    "connection_error": N_("Connection error"), "request_error": N_("Request error"),
    "dependency_failed": N_("A dependency failed"), "unexpected_status": N_("Unexpected HTTP status"),
    "invalid_response": N_("Invalid health answer"), "slow_response": N_("Slow answer"),
    "model_weights_missing": N_("Model weights missing"), "authentication_failed": N_("Authentication failed"),
    "invalid_configuration": N_("Invalid configuration"), "process_not_connected": N_("Process not connected"),
    "backlog_stalled": N_("Pending events are not being processed"), "probe_error": N_("Check failed"),
}
# Window of the Administration screen -> bucket of its chart.
WINDOWS = {"1h": ("1m", 1), "6h": ("15m", 6), "24h": ("1h", 24), "7d": ("6h", 24 * 7)}


class MonitoringServiceError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.status, self.code, self.message = status, code, message


def enabled() -> bool:
    return bool(MONITORING_SERVICE_URL)


def _request(path: str, params: dict | None = None) -> dict:
    if not enabled():
        raise MonitoringServiceError(503, "monitoring_disabled")
    token = getattr(g, "auth_access_token", None)
    if not token:
        raise MonitoringServiceError(401, "token_required")
    if not getattr(g, "monitoring_correlation_id", None):
        g.monitoring_correlation_id = uuid.uuid4().hex
    try:
        response = requests.get(f"{MONITORING_SERVICE_URL}{BASE}{path}", params=params, timeout=MONITORING_TIMEOUT,
                                headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                                         "X-Correlation-ID": g.monitoring_correlation_id})
    except requests.RequestException as exc:
        raise MonitoringServiceError(503, "monitoring_unavailable", type(exc).__name__) from exc
    if response.status_code >= 500:
        raise MonitoringServiceError(503, "monitoring_unavailable", f"HTTP {response.status_code}")
    if response.status_code >= 400:
        try:
            body = response.json() or {}
        except ValueError:
            body = {}
        code = {401: "token_required", 403: "forbidden", 404: "not_found"}.get(response.status_code, "invalid_request")
        raise MonitoringServiceError(response.status_code, code, body.get("message", ""))
    return response.json()


def _local(iso_value: str | None) -> str:
    if not iso_value:
        return ""
    try:
        zone = ZoneInfo(current_app.config.get("ADMIN_DISPLAY_TIMEZONE") or "UTC")
    except Exception:  # noqa: BLE001 - unknown zone name
        zone = ZoneInfo("UTC")
    return datetime.fromisoformat(iso_value.replace("Z", "+00:00")).astimezone(zone).strftime("%Y-%m-%d %H:%M:%S")


def _utc(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _status(item: dict) -> dict:
    status = item.get("status") or "unknown"
    return {"status": status, "status_label": STATUS_LABELS.get(status, status), "variant": STATUS_VARIANTS.get(status, "neutral"),
            "freshness_label": FRESHNESS_LABELS.get(item.get("freshness") or "", "")}


def _check_row(check: dict | None) -> dict:
    check = check or {}
    return {
        "latency_ms": check.get("latency_ms"),
        "http_status": check.get("http_status"),
        "dependencies": check.get("dependencies") or [],
        "error_type": check.get("error_type"),
        "error_label": ERROR_LABELS.get(check.get("error_type") or "", check.get("error_type") or ""),
        "endpoint": check.get("endpoint"),
        "details": check.get("details") or {},
    }


def admin_overview(window: str = "24h") -> dict:
    """Current state, window aggregates and the latency / availability series of the Administration screen."""
    bucket, hours = WINDOWS.get(window, WINDOWS["24h"])
    services = _request("/services")
    end = datetime.now(timezone.utc).replace(microsecond=0)
    summary = _request("/summary", {"bucket": bucket, "from": _utc(end - timedelta(hours=hours)), "to": _utc(end)})
    stats = {c["name"]: c for c in summary["components"]}
    rows = []
    for item in services["items"]:
        name = item["name"]
        rows.append({"name": name, "label": COMPONENT_LABELS.get(name, item.get("label") or name), **_status(item),
                     "checked_at": _local(item.get("checked_at")), "measured_status": item.get("measured_status"),
                     **_check_row(item.get("check")), "stats": stats.get(name) or {}})
    series = []
    for entry in summary.get("series", []):
        points = [p for p in entry["points"] if p.get("avg_latency_ms") is not None or p.get("availability_pct") is not None]
        if points:
            series.append({"name": str(COMPONENT_LABELS.get(entry["name"], entry["name"])), "key": entry["name"],
                           "latency": [[p["bucket_start"], p.get("avg_latency_ms")] for p in points],
                           "availability": [[p["bucket_start"], p.get("availability_pct")] for p in points]})
    collector = services.get("collector") or {}
    return {"rows": rows, "counts": services["counts"], "series": series, "window": window, "bucket": bucket,
            "collector": {"last_run_at": _local(collector.get("last_run_at")), "interval_seconds": collector.get("interval_seconds")}}


def admin_history(*, component: str = "", status: str = "", page=1) -> dict:
    params = {"page": page, "per_page": 20}
    if component in COMPONENT_LABELS:
        params["component"] = component
    if status in STATUS_LABELS:
        params["status"] = status
    body = _request("/history", params)
    rows = [{"component": i["component"], "label": COMPONENT_LABELS.get(i["component"], i["component"]), **_status(i),
             "checked_at": _local(i["checked_at"]), **_check_row(i)} for i in body["items"]]
    return {"rows": rows, "page": body["page"], "pages": body["total_pages"], "total": body["total"]}


def supervisor_overview() -> dict:
    """Availability summary authorized for the Supervisor (no latencies, endpoints or errors)."""
    services = _request("/services")
    summary = _request("/summary")
    stats = {c["name"]: c for c in summary["components"]}
    rows = [{"name": i["name"], "label": COMPONENT_LABELS.get(i["name"], i.get("label") or i["name"]), **_status(i),
             "checked_at": _local(i.get("checked_at")), "availability_pct": (stats.get(i["name"]) or {}).get("availability_pct")}
            for i in services["items"]]
    return {"rows": rows, "counts": services["counts"]}
