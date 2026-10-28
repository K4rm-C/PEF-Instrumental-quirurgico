"""Supervisor Reports and Indicators screens — adapter tests (SessionService answers simulated).

The report figures are computed and tested on real PostgreSQL in apps/SessionService/tests/
test_reports.py and end to end in scripts/e2e_reports.py. Here: the portal forwards the verified token
and the filters, shows exactly what it received (nulls stay "no data"), feeds the charts with those
numbers, and degrades in a controlled way.
"""

import json
import re
import uuid

import pytest
import requests

from controllers import report_routes
from services import session_remote

KIT, OPERATOR = str(uuid.uuid4()), str(uuid.uuid4())
FILTERS = {"from": "2026-09-22T06:00:00Z", "to": "2026-10-22T06:00:00Z", "tz": "America/Mexico_City", "kit_id": None,
           "operator_id": None, "status": None}
STATUSES = ("scheduled", "in_progress", "awaiting_spd_review", "correction_required", "closed", "aborted")
SUMMARY = {
    "filters": FILTERS,
    "sessions": {"total": 7, "by_status": [{"code": c, "count": n} for c, n in zip(STATUSES, (1, 1, 2, 1, 2, 0))],
                 "scheduled": 1, "in_progress": 1, "pending_review": 3, "closed": 2, "reported": 5, "timed": 4,
                 "avg_duration_minutes": 23.5, "reported_with_discrepancies": 2, "with_discrepancies_pct": 40.0},
    "discrepancies": {"total": 3, "open": 1, "resolved": 2, "timed": 2, "avg_resolution_minutes": 12.3,
                      "by_reason": [{"code": "shortage", "name": "Shortage versus expected inventory", "total": 3, "open": 1}],
                      "by_family": [{"family_id": str(uuid.uuid4()), "code": "NH", "name": "Needle holder", "total": 2, "open": 1},
                                    {"family_id": None, "code": None, "name": None, "total": 1, "open": 0}]},
    "ai_agreement": {"pairs": 3, "agreed": 2, "corrections": 1, "min_pairs": 5, "sufficient": False, "agreement_pct": None},
    "activity": [{"date": "2026-10-20", "sessions": 4, "reported": 3, "with_discrepancies": 1},
                 {"date": "2026-10-21", "sessions": 3, "reported": 2, "with_discrepancies": 1}],
}
KITS = {"filters": FILTERS, "page": 1, "per_page": 10, "total": 1, "total_pages": 1, "kits": [
    {"kit_id": KIT, "name": "Basic Laparotomy Kit", "version": 4, "active": True, "sessions": 7, "reported_sessions": 5,
     "sessions_with_discrepancies": 2, "discrepancies": 3, "expected_quantity": 91, "expected_quantity_counted": 65,
     "reported_quantity": 63}]}
OPERATORS = {"filters": FILTERS, "page": 1, "per_page": 10, "total": 1, "total_pages": 1, "min_pairs": 5, "operators": [
    {"operator_id": OPERATOR, "name": "Operadora Uno", "sessions": 7, "started_sessions": 6, "counts_completed": 5,
     "family_counts": 40, "ai_pairs": 3, "ai_agreed": 2, "human_corrections": 1, "discrepancies": 3, "agreement_pct": None}]}
OPTIONS = {"kits": [{"id": KIT, "name": "Basic Laparotomy Kit", "version": 4, "active": True}],
           "operators": [{"id": OPERATOR, "name": "Operadora Uno", "active": True}], "statuses": list(STATUSES)}
EMPTY = {**SUMMARY, "sessions": {**SUMMARY["sessions"], "total": 0, "by_status": [{"code": c, "count": 0} for c in STATUSES],
                                 "reported": 0, "timed": 0, "avg_duration_minutes": None, "with_discrepancies_pct": None,
                                 "reported_with_discrepancies": 0, "scheduled": 0, "in_progress": 0, "pending_review": 0,
                                 "closed": 0},
         "discrepancies": {"total": 0, "open": 0, "resolved": 0, "timed": 0, "avg_resolution_minutes": None,
                           "by_reason": [], "by_family": []},
         "ai_agreement": {"pairs": 0, "agreed": 0, "corrections": 0, "min_pairs": 5, "sufficient": False, "agreement_pct": None},
         "activity": []}


class FakeSession:
    def __init__(self, bodies):
        self.calls, self.bodies = [], bodies

    def __call__(self, method, url, json=None, params=None, timeout=None, headers=None):
        path = url.split("/api/v1/sessions", 1)[1]
        self.calls.append((method, path, dict(params or {}), headers))
        body = self.bodies[path]
        response = requests.Response()
        if isinstance(body, tuple):
            response.status_code, body = body
        else:
            response.status_code = 200
        response._content = __import__("json").dumps(body).encode()
        return response


@pytest.fixture
def service(app, monkeypatch):
    def install(**overrides):
        bodies = {"/reports/summary": SUMMARY, "/reports/kits": KITS, "/reports/operators": OPERATORS,
                  "/reports/options": OPTIONS, **overrides}
        fake = FakeSession(bodies)
        monkeypatch.setattr(session_remote, "SESSION_SERVICE_URL", "http://session.test")
        monkeypatch.setattr(session_remote.requests, "request", fake)
        monkeypatch.setattr(report_routes, "use_v3_fixture", lambda *a: False)
        return fake
    return install


def charts(html):
    return json.loads(re.search(r'<script type="application/json" id="report-charts">(.*?)</script>', html, re.S).group(1))


def test_reports_show_the_service_figures(client, login, service):
    fake = service()
    login("spd_supervisor")
    page = client.get(f"/supervisor/reports?date_range=7d&kit={KIT}&operator={OPERATOR}&session_status=closed")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert 'data-metric="avg-duration">23.5 min' in html and 'data-metric="with-discrepancies">40 %' in html
    assert 'data-metric="avg-resolution">12.3 min' in html and "Basic Laparotomy Kit" in html and "Operadora Uno" in html
    assert "63" in html and "of 65 expected in counted sessions" in html and "Outside the kit / no family" in html
    assert 'data-metric="agreement"><span class="report-empty-value">Insufficient data' in html      # 3 < 5 pairs
    paths = [c[1] for c in fake.calls]
    assert paths == ["/reports/options", "/reports/summary", "/reports/kits", "/reports/operators"]
    params = fake.calls[1][2]
    assert params["kit_id"] == KIT and params["operator_id"] == OPERATOR and params["status"] == "closed"
    assert params["tz"] == "America/Mexico_City" and params["from"] < params["to"]
    assert fake.calls[1][3]["Authorization"] == "Bearer test-access-token"


def test_charts_are_fed_with_the_report_numbers(client, login, service):
    service()
    login("spd_supervisor")
    data = charts(client.get("/supervisor/reports").get_data(as_text=True))
    assert [p["y"] for p in data["status"]] == [1, 1, 2, 1, 2]                  # aborted (0) is not drawn
    assert data["activity"] == {"categories": ["2026-10-20", "2026-10-21"], "sessions": [4, 3], "with_discrepancies": [1, 1]}
    assert data["reasons"]["values"] == [3] and data["kits"]["values"] == [7]
    assert data["operators"] == {"categories": ["Operadora Uno"], "sessions": [7], "discrepancies": [3]}


def test_empty_report_keeps_nulls_and_shows_empty_states(client, login, service):
    service(**{"/reports/summary": EMPTY, "/reports/kits": {**KITS, "total": 0, "kits": []},
               "/reports/operators": {**OPERATORS, "total": 0, "operators": []}})
    login("spd_supervisor")
    html = client.get("/supervisor/reports").get_data(as_text=True)
    assert 'data-metric="avg-duration"><span class="report-empty-value">No data' in html
    assert 'data-metric="with-discrepancies"><span class="report-empty-value">No data' in html
    assert "No sessions match these filters" in html and "No kits were used" in html and "No operator activity" in html
    assert 'id="chart-status"' not in html


def test_service_down_and_not_configured(client, login, service, monkeypatch):
    service()

    def down(*args, **kwargs):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(session_remote.requests, "request", down)
    login("spd_supervisor")
    page = client.get("/supervisor/reports")
    assert page.status_code == 503 and "data-report-unavailable" in page.get_data(as_text=True)
    monkeypatch.setattr(session_remote, "SESSION_SERVICE_URL", "")
    assert "not configured in this installation" in client.get("/supervisor/reports").get_data(as_text=True)


def test_rejected_filter_is_a_controlled_message(client, login, service):
    service(**{"/reports/summary": (400, {"error": "invalid_request", "message": "Unknown kit",
                                          "details": {"field": "kit_id"}})})
    login("spd_supervisor")
    page = client.get(f"/supervisor/reports?kit={uuid.uuid4()}")
    assert page.status_code == 400 and "Unknown kit" in page.get_data(as_text=True)


def test_only_the_supervisor(client, login, service):
    fake = service()
    for role in ("station_operator", "it_admin"):
        login(role)
        assert client.get("/supervisor/reports").status_code == 403
        assert client.get("/supervisor/indicators").status_code == 403
    assert fake.calls == []


def test_indicators_use_the_same_summary(client, login, service):
    fake = service()
    login("spd_supervisor")
    html = client.get("/supervisor/indicators").get_data(as_text=True)
    assert "Insufficient data" in html and "12.3 min" in html and "Needle holder" in html
    params = fake.calls[0][2]
    assert params["from"].endswith("-01") and fake.calls[0][1] == "/reports/summary"


def test_spanish(client, login, service):
    service()
    login("spd_supervisor", locale="es-MX")
    html = client.get("/supervisor/reports").get_data(as_text=True)
    assert "Resumen de sesiones" in html and "Datos insuficientes" in html and "Faltante" in html


# --- Indicators never fall back silently to another source -------------------------------------


@pytest.fixture
def no_local(monkeypatch):
    from controllers import routes
    for name in ("sessions_data", "discrepancies_data"):
        monkeypatch.setattr(routes, name, lambda *a, **k: pytest.fail("Indicators must not use the local aggregates"))


@pytest.mark.parametrize("failure", ["down", "http_500", "http_403", "http_400"])
def test_configured_but_failing_session_service_is_a_notice(client, login, service, monkeypatch, no_local, failure):
    if failure == "down":
        service()

        def down(*args, **kwargs):
            raise requests.ConnectionError("refused")
        monkeypatch.setattr(session_remote.requests, "request", down)
    else:
        code = int(failure.split("_")[1])
        service(**{"/reports/summary": (code, {"error": "x", "message": "", "details": {}})})
    login("spd_supervisor")
    page = client.get("/supervisor/indicators")
    html = page.get_data(as_text=True)
    assert page.status_code == 503 and "data-report-unavailable" in html
    assert "stat-card__value" not in html                      # no figures at all, not partial ones


def test_not_configured_without_the_development_flag_is_a_notice(client, login, app, monkeypatch, no_local):
    monkeypatch.setattr(session_remote, "SESSION_SERVICE_URL", "")
    monkeypatch.setattr(report_routes, "use_v3_fixture", lambda *a: False)
    monkeypatch.setitem(app.config, "SUPERVISOR_INDICATORS_LOCAL_FALLBACK", False)
    login("spd_supervisor")
    page = client.get("/supervisor/indicators")
    assert page.status_code == 503 and "not configured in this installation" in page.get_data(as_text=True)


def test_local_aggregates_only_with_the_explicit_development_flag(client, login, app, monkeypatch):
    from controllers import routes
    monkeypatch.setattr(session_remote, "SESSION_SERVICE_URL", "")
    monkeypatch.setattr(report_routes, "use_v3_fixture", lambda *a: False)
    monkeypatch.setitem(app.config, "SUPERVISOR_INDICATORS_LOCAL_FALLBACK", True)
    monkeypatch.setattr(routes, "sessions_data", lambda *a, **k: [{"status_label": "Closed"}])
    monkeypatch.setattr(routes, "discrepancies_data", lambda *a, **k: [])
    login("spd_supervisor")
    page = client.get("/supervisor/indicators")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "data-indicators-local" in html and "Development mode" in html
