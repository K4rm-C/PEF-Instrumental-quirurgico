"""System status (Administration) and services health (Supervisor) screens — adapter tests.

The MonitoringService answers are simulated here. The service itself is tested in
apps/MonitoringService (real PostgreSQL, Redis and HTTP servers), and the live stack in
scripts/e2e_monitoring.py. These tests check that the portal forwards the verified token, shows
exactly what it received (no invented figures), keeps the Supervisor's view reduced, degrades in a
controlled way, and that no other page calls the service.
"""

import json

import pytest
import requests

from services import monitoring_remote

CHECK = {"id": "c1", "run_id": "r1", "component": "catalog", "status": "down", "checked_at": "2026-10-21T18:00:00Z",
         "latency_ms": 2001.5, "http_status": None, "error_type": "dns_error", "endpoint": "http://catalog:5003/health/ready",
         "dependencies": [], "details": {}}
SERVICES_ADMIN = {"items": [
    {"name": "catalog", "label": "Catalog Service", "status": "down", "checked_at": "2026-10-21T18:00:00Z", "freshness": "current",
     "kind": "http_health", "measured_status": "down", "check": CHECK},
    {"name": "session", "label": "Session", "status": "up", "checked_at": "2026-10-21T18:00:00Z", "freshness": "current",
     "kind": "http_health", "measured_status": "up",
     "check": {**CHECK, "component": "session", "status": "up", "latency_ms": 17.25, "http_status": 200, "error_type": None,
               "dependencies": [{"name": "database", "status": "ok"}, {"name": "redis", "status": "ok"}]}},
    {"name": "mongo", "label": "MongoDB", "status": "unknown", "checked_at": None, "freshness": "never_checked",
     "kind": "mongo", "measured_status": None, "check": None}],
    "counts": {"up": 1, "degraded": 0, "down": 1, "unknown": 1},
    "collector": {"last_run_at": "2026-10-21T18:00:00Z", "interval_seconds": 30.0, "stale_after_seconds": 90.0}}
SUMMARY_ADMIN = {"window": {"from": "x", "to": "y"}, "components": [
    {"name": "session", "label": "Session", "checks": 120, "up": 119, "degraded": 0, "down": 1, "unknown": 0,
     "availability_pct": 99.17, "up_pct": 99.17, "avg_latency_ms": 18.4, "p95_latency_ms": 31.2, "min_latency_ms": 9.0,
     "max_latency_ms": 44.0}], "bucket": "1h",
    "series": [{"name": "session", "points": [{"bucket_start": "2026-10-21T17:00:00Z", "checks": 120,
                                               "availability_pct": 99.17, "avg_latency_ms": 18.4}]},
               {"name": "catalog", "points": []}]}
HISTORY = {"items": [CHECK], "page": 1, "per_page": 20, "total": 1, "total_pages": 1, "sort": "-checked_at"}


class FakeMonitoring:
    def __init__(self, role="admin"):
        self.calls, self.role = [], role

    def __call__(self, url, params=None, timeout=None, headers=None):
        path = url.split("/api/v1/monitoring", 1)[1]
        self.calls.append((path, dict(params or {}), headers))
        if self.role == "supervisor":
            body = {"/services": {"items": [{k: i[k] for k in ("name", "label", "status", "checked_at", "freshness")}
                                            for i in SERVICES_ADMIN["items"]], "counts": SERVICES_ADMIN["counts"]},
                    "/summary": {"window": {}, "components": [{"name": "session", "label": "Session", "checks": 120,
                                                               "availability_pct": 99.17, "up_pct": 99.17}]}}[path]
        else:
            body = {"/services": SERVICES_ADMIN, "/summary": SUMMARY_ADMIN, "/history": HISTORY}[path]
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(body).encode()
        return response


@pytest.fixture
def monitoring(app, monkeypatch):
    def install(role="admin"):
        fake = FakeMonitoring(role)
        monkeypatch.setattr(monitoring_remote, "MONITORING_SERVICE_URL", "http://monitoring.test")
        monkeypatch.setattr(monitoring_remote.requests, "get", fake)
        return fake
    return install


def test_admin_system_status_shows_the_measured_state(client, login, monitoring):
    fake = monitoring()
    login("it_admin")
    page = client.get("/admin/system-status?window=24h&component=catalog&status=down")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert 'data-component="catalog" data-status="down"' in html and "Host not found" in html and "dns_error" in html
    assert "17.2 ms" in html and "database: ok" in html and "99.2 %" in html and "31.2 ms" in html
    assert 'data-component="mongo" data-status="unknown"' in html and "Never checked" in html
    assert "highcharts@11.4.8" in html and '"latency"' in html          # chart fed with the real series
    paths = [c[0] for c in fake.calls]
    assert paths == ["/services", "/summary", "/history"] and fake.calls[0][2]["Authorization"] == "Bearer test-access-token"
    assert fake.calls[1][1]["bucket"] == "1h" and fake.calls[2][1] == {"page": 1, "per_page": 20, "component": "catalog",
                                                                       "status": "down"}


def test_no_measurement_no_chart(client, login, monitoring, monkeypatch):
    monitoring()
    monkeypatch.setitem(SUMMARY_ADMIN, "series", [{"name": "session", "points": []}])
    login("it_admin")
    html = client.get("/admin/system-status").get_data(as_text=True)
    assert "No measurements in this window yet." in html and "highcharts" not in html


def test_supervisor_sees_the_availability_summary_only(client, login, monitoring):
    fake = monitoring("supervisor")
    login("spd_supervisor")
    page = client.get("/supervisor/services-health")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and 'data-component="catalog" data-status="down"' in html and "99.2 %" in html
    assert "ms" not in html.split("data-services-health-table", 1)[1].split("</table>", 1)[0]
    assert "catalog:5003" not in html and [c[0] for c in fake.calls] == ["/services", "/summary"]


def test_roles(client, login, monitoring):
    monitoring()
    login("spd_supervisor")
    assert client.get("/admin/system-status").status_code == 403
    login("station_operator")
    assert client.get("/admin/system-status").status_code == 403
    assert client.get("/supervisor/services-health").status_code == 403


def test_monitoring_down_is_a_controlled_notice(client, login, monitoring, monkeypatch):
    monitoring()

    def down(*args, **kwargs):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(monitoring_remote.requests, "get", down)
    login("it_admin")
    page = client.get("/admin/system-status")
    assert page.status_code == 503 and "The monitoring service cannot be reached right now" in page.get_data(as_text=True)
    login("spd_supervisor")
    page = client.get("/supervisor/services-health")
    assert page.status_code == 503 and "data-monitoring-unavailable" in page.get_data(as_text=True)


def test_disabled_without_url(client, login, monkeypatch):
    monkeypatch.setattr(monitoring_remote, "MONITORING_SERVICE_URL", "")
    login("it_admin")
    page = client.get("/admin/system-status")
    assert page.status_code == 200 and "Monitoring is not enabled" in page.get_data(as_text=True)


def test_spanish(client, login, monitoring):
    monitoring()
    login("it_admin", locale="es-MX")
    html = client.get("/admin/system-status").get_data(as_text=True)
    assert "Estado del sistema" in html and "No disponible" in html and "Host no encontrado" in html


@pytest.mark.parametrize("role, path", [
    ("station_operator", "/operator/profile"), ("spd_supervisor", "/supervisor/profile"), ("it_admin", "/admin/profile"),
])
def test_other_pages_never_call_the_monitoring_service(client, login, monitoring, role, path):
    fake = monitoring()
    login(role)
    assert client.get(path).status_code == 200 and fake.calls == []


def test_portal_health_is_public_and_cheap(client, monkeypatch):
    from controllers import routes
    monkeypatch.setattr(routes, "_auth_request", lambda *a, **k: pytest.fail("health must not call AuthService"))
    for path in ("/health", "/health/ready", "/health/live"):
        response = client.get(path, headers={"X-Correlation-ID": "corr-web-1"})
        assert response.status_code in (200, 503) and response.headers["X-Correlation-ID"] == "corr-web-1"
        assert response.get_json()["status"] in ("ready", "not_ready", "live")
