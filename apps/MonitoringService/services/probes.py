"""One real check per component kind. Every probe measures its own latency and never raises.

Classification (the same for every HTTP component):
- `up`: the component answered and reports every dependency ok, within `degraded_latency_ms`;
- `degraded`: it answered but some dependency failed (e.g. /health/ready 503 with database ok and
  redis error), it is slower than the threshold, the answer is not what the component documents,
  or (VisionWorker) its weights are missing;
- `down`: no answer (refused, timeout, DNS) or it answered that nothing works;
- `unknown`: not configured — it was not checked, so it is never reported as available.

What is measured is only what the components really expose: reachability, HTTP code, the
`checks` of their health endpoint and the round-trip latency of the check itself. Nothing else
(no CPU, memory or request-rate metrics exist in the current infrastructure).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from sqlalchemy import text

USER_AGENT = "pef-monitoring/1.0"
WORKER_APPLICATION_NAME = "pef-notifications-worker"


@dataclass
class Result:
    component: str
    status: str
    checked_at: datetime
    latency_ms: float | None = None
    http_status: int | None = None
    dependencies: dict[str, str] = field(default_factory=dict)
    error_type: str | None = None
    endpoint: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def now() -> datetime:
    return datetime.now(timezone.utc)


def safe_endpoint(url: str) -> str:
    """scheme://host:port/path — never userinfo, query strings or fragments (no secrets in the history)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))[:200]


def _elapsed(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _http_error(exc: requests.RequestException) -> str:
    if isinstance(exc, requests.ConnectTimeout):
        return "connect_timeout"     # no connection within the timeout (on Windows also a refused local port)
    if isinstance(exc, requests.Timeout):
        return "timeout"             # connected, but no answer within the timeout
    if isinstance(exc, requests.ConnectionError):
        text_ = str(exc).lower()
        if "name or service not known" in text_ or "getaddrinfo" in text_ or "nodename" in text_ or "name resolution" in text_:
            return "dns_error"
        if "refused" in text_ or "actively refused" in text_ or "10061" in text_:
            return "connection_refused"
        return "connection_error"
    return "request_error"


def _slow(result: Result, threshold_ms: float) -> Result:
    if result.status == "up" and result.latency_ms is not None and result.latency_ms > threshold_ms:
        result.status, result.error_type = "degraded", "slow_response"
    return result


def http_health(component: str, base_url: str, timeout: float, threshold_ms: float, path: str = "/health/ready") -> Result:
    """DS09 health endpoint: {"status", "checks": {dependency: "ok"|...}}; 200 ready, 503 not ready."""
    url = base_url + path
    result = Result(component, "down", now(), endpoint=safe_endpoint(url))
    started = time.perf_counter()
    try:
        response = requests.get(url, timeout=timeout, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    except requests.RequestException as exc:
        result.latency_ms, result.error_type = _elapsed(started), _http_error(exc)
        return result
    result.latency_ms, result.http_status = _elapsed(started), response.status_code
    try:
        body = response.json()
    except ValueError:
        body = None
    checks = body.get("checks") if isinstance(body, dict) else None
    if isinstance(checks, dict):
        result.dependencies = {str(k): ("ok" if v in ("ok", True) else "error") for k, v in checks.items()}
    healthy = [v == "ok" for v in result.dependencies.values()]
    if response.status_code == 200 and isinstance(checks, dict):
        result.status = "up" if all(healthy) else "degraded"
        result.error_type = None if all(healthy) else "dependency_failed"
    elif response.status_code == 503 and isinstance(checks, dict):
        # The process answers but is not ready: degraded while some dependency still works.
        result.status = "degraded" if any(healthy) else "down"
        result.error_type = "dependency_failed"
    elif response.status_code == 200:
        result.status, result.error_type = "degraded", "invalid_response"
    else:
        result.status, result.error_type = "down", "unexpected_status"
    return _slow(result, threshold_ms)


def vision(component: str, base_url: str, timeout: float, threshold_ms: float) -> Result:
    """VisionWorker /health: {"ok": true, "weights_present": bool, ...}. Without weights it cannot count."""
    url = base_url + "/health"
    result = Result(component, "down", now(), endpoint=safe_endpoint(url))
    started = time.perf_counter()
    try:
        response = requests.get(url, timeout=timeout, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    except requests.RequestException as exc:
        result.latency_ms, result.error_type = _elapsed(started), _http_error(exc)
        return result
    result.latency_ms, result.http_status = _elapsed(started), response.status_code
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code != 200 or not isinstance(body, dict):
        result.error_type = "unexpected_status" if response.status_code != 200 else "invalid_response"
        result.status = "down" if response.status_code != 200 else "degraded"
        return result
    weights = bool(body.get("weights_present"))
    result.dependencies = {"model_weights": "ok" if weights else "error"}
    result.details = {"bound_sessions": body.get("bound_sessions")} if isinstance(body.get("bound_sessions"), int) else {}
    if body.get("ok") is True and weights:
        result.status = "up"
    else:
        result.status, result.error_type = "degraded", "model_weights_missing" if not weights else "invalid_response"
    return _slow(result, threshold_ms)


def garage(component: str, base_url: str, timeout: float, threshold_ms: float) -> Result:
    """Garage admin API GET /health (no token): 200 fully operational, 503 degraded cluster."""
    url = base_url + "/health"
    result = Result(component, "down", now(), endpoint=safe_endpoint(url))
    started = time.perf_counter()
    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    except requests.RequestException as exc:
        result.latency_ms, result.error_type = _elapsed(started), _http_error(exc)
        return result
    result.latency_ms, result.http_status = _elapsed(started), response.status_code
    if response.status_code == 200:
        result.status = "up"
    elif response.status_code == 503:
        result.status, result.error_type = "degraded", "dependency_failed"
    else:
        result.error_type = "unexpected_status"
    return _slow(result, threshold_ms)


def postgres(component: str, engine, timeout: float, threshold_ms: float) -> Result:
    url = engine.url
    result = Result(component, "down", now(), endpoint=f"postgresql://{url.host or ''}:{url.port or 5432}/{url.database or ''}")
    started = time.perf_counter()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT set_config('statement_timeout', :ms, true)"), {"ms": str(int(timeout * 1000))})
            connection.execute(text("SELECT 1")).scalar_one()
        result.status = "up"
    except Exception as exc:  # noqa: BLE001 - classified, never raised
        result.error_type = "timeout" if "timeout" in str(exc).lower() else "connection_error"
    result.latency_ms = _elapsed(started)
    return _slow(result, threshold_ms)


def redis_ping(component: str, client, timeout: float, threshold_ms: float) -> Result:
    pool = client.connection_pool.connection_kwargs
    result = Result(component, "down", now(), endpoint=f"redis://{pool.get('host', '')}:{pool.get('port', 6379)}")
    started = time.perf_counter()
    try:
        result.status = "up" if client.ping() else "down"
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__.lower()
        result.error_type = "timeout" if "timeout" in name else (
            "authentication_failed" if "auth" in name else "connection_error")
    result.latency_ms = _elapsed(started)
    return _slow(result, threshold_ms)


def mongo(component: str, client, endpoint: str | None, timeout: float, threshold_ms: float) -> Result:
    result = Result(component, "down", now(), endpoint=endpoint)
    started = time.perf_counter()
    try:
        ok = client.admin.command("ping").get("ok")
        result.status = "up" if ok in (1, 1.0, True) else "degraded"
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        result.error_type = {"ServerSelectionTimeoutError": "timeout", "OperationFailure": "authentication_failed",
                             "ConfigurationError": "invalid_configuration"}.get(name, "connection_error")
    result.latency_ms = _elapsed(started)
    return _slow(result, threshold_ms)


def notifications_worker(component: str, engine, timeout: float, threshold_ms: float, backlog_seconds: float,
                         application_name: str = WORKER_APPLICATION_NAME) -> Result:
    """The outbox consumer has no HTTP port. Real signals, both from PostgreSQL:
    - its live connection (application_name `pef-notifications-worker` in pg_stat_activity);
    - the age of the oldest unprocessed outbox event (it keeps up, or it is stuck).
    """
    result = Result(component, "down", now(), endpoint=f"postgresql:pg_stat_activity/{application_name}")
    started = time.perf_counter()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT set_config('statement_timeout', :ms, true)"), {"ms": str(int(timeout * 1000))})
            connections = connection.execute(text(
                "SELECT count(*) FROM pg_stat_activity WHERE application_name = :app"), {"app": application_name}).scalar_one()
            backlog = (0, None)
            if connection.execute(text("SELECT to_regclass('notification_outbox')")).scalar():
                backlog = connection.execute(text("""
                    SELECT count(*), EXTRACT(EPOCH FROM (now() - min(enqueued_at)))
                    FROM notification_outbox WHERE processed_at IS NULL AND attempts < 10
                """)).one()
    except Exception:  # noqa: BLE001
        result.latency_ms = _elapsed(started)
        result.error_type = "dependency_failed"
        result.dependencies = {"database": "error"}
        return result
    result.latency_ms = _elapsed(started)
    pending, oldest = int(backlog[0] or 0), (float(backlog[1]) if backlog[1] is not None else None)
    stuck = oldest is not None and oldest > backlog_seconds
    result.dependencies = {"database_connection": "ok" if connections else "error", "outbox_backlog": "error" if stuck else "ok"}
    result.details = {"pending_events": pending, "oldest_pending_seconds": round(oldest, 1) if oldest is not None else None}
    if not connections:
        result.error_type = "process_not_connected"
    elif stuck:
        result.status, result.error_type = "degraded", "backlog_stalled"
    else:
        result.status = "up"
    return result


def not_configured(component: str) -> Result:
    return Result(component, "unknown", now(), error_type="not_configured")
