"""MS-06 in the portal: outcomes / automatic count / interventions / stations on the Reports screen, the station
filter, the CSV / PDF download proxy and the new notification types (SessionService and NotificationsService
answers simulated).

The figures, the export files and their audit are tested on real PostgreSQL in apps/SessionService/tests/
test_ms06_reports.py and end to end in scripts/e2e_ms06_reports.py.
"""

import json
import uuid

import pytest
import requests

from services import notifications_remote, session_remote
from tests.test_reports_remote import KITS, OPERATORS, OPTIONS, SUMMARY, charts

STATION = str(uuid.uuid4())
OPTIONS_MS06 = {**OPTIONS, "stations": [{"id": STATION, "name": "Station 1", "room_code": "OR-1", "active": True}]}
SUMMARY_MS06 = {
    **SUMMARY,
    "outcomes": {"approved": 3, "unresolved": 1, "closed_without_record": 0, "closed_by_operator": 0,
                 "pending_review": 2, "aborted": 0},
    "automatic_counts": {"with_initial_count": 5, "with_final_count": 4, "final_first_attempt": 3,
                         "sessions_with_rejected_final": 1, "rejected_final_attempts": 2, "recaptured_final": 1,
                         "contingency_reports": 1, "final_first_attempt_pct": 75.0, "timed_processing": 4,
                         "avg_initial_to_final_minutes": 17.5, "avg_start_to_initial_minutes": 5.0},
    "interventions": {"human_corrections": 6, "contingency_reports": 1, "discrepancies_resolved": 2,
                      "rejected_final_attempts": 2},
}
STATIONS = {"filters": {}, "page": 1, "per_page": 10, "total": 2, "total_pages": 1, "stations": [
    {"station_id": STATION, "name": "Station 1", "room_code": "OR-1", "active": True, "sessions": 5, "reported": 4,
     "closed": 3, "sessions_with_discrepancies": 1, "with_final_count": 4, "rejected_final_attempts": 2,
     "cameras_used": 1, "avg_duration_minutes": 40.0},
    {"station_id": None, "name": None, "room_code": None, "active": None, "sessions": 2, "reported": 0, "closed": 0,
     "sessions_with_discrepancies": 0, "with_final_count": 0, "rejected_final_attempts": 0, "cameras_used": 0,
     "avg_duration_minutes": None}]}


@pytest.fixture
def reports(app, monkeypatch):
    from controllers import report_routes
    from tests.test_reports_remote import FakeSession
    fake = FakeSession({"/reports/summary": SUMMARY_MS06, "/reports/kits": KITS, "/reports/operators": OPERATORS,
                        "/reports/options": OPTIONS_MS06, "/reports/stations": STATIONS})
    monkeypatch.setattr(session_remote, "SESSION_SERVICE_URL", "http://session.test")
    monkeypatch.setattr(session_remote.requests, "request", fake)
    monkeypatch.setattr(report_routes, "use_v3_fixture", lambda *a: False)
    return fake


def test_outcomes_automatic_count_and_interventions_are_shown_separately(client, login, reports):
    login("spd_supervisor")
    html = client.get("/supervisor/reports").get_data(as_text=True)
    assert "data-report-outcomes" in html and "Approved (safe close)" in html and "Closed as Unresolved" in html
    assert 'data-metric="first-attempt">75 %' in html and 'data-metric="initial-to-final">17.5 min' in html
    assert "This is not the accuracy of the model" in html            # never presented as model precision
    assert "Human interventions" in html and "Contingency Reports" in html


def test_station_activity_table_chart_and_filter(client, login, reports):
    login("spd_supervisor")
    html = client.get(f"/supervisor/reports?station={STATION}").get_data(as_text=True)
    assert f'data-station="{STATION}"' in html and "OR-1 · Station 1" in html and 'data-station="none"' in html
    assert "No station" in html and f'<option value="{STATION}" selected>' in html
    data = charts(html)["stations"]
    assert data == {"categories": ["OR-1 · Station 1", "No station"], "sessions": [5, 2], "rejected": [2, 0]}
    for _, path, params, _ in reports.calls[1:]:
        assert params["station_id"] == STATION, path                  # every report receives the same filter


def test_export_buttons_carry_the_screen_filters(client, login, reports):
    login("spd_supervisor")
    html = client.get(f"/supervisor/reports?date_range=7d&station={STATION}").get_data(as_text=True)
    assert f'/supervisor/reports/export.csv?date_range=7d&amp;station={STATION}' in html
    assert 'data-export="pdf"' in html


class FakeExport:
    def __init__(self, status=200, body=b"\xef\xbb\xbfSection,Metric\r\n", content_type="text/csv; charset=utf-8",
                 disposition='attachment; filename="pef-report-2026-10.csv"'):
        self.calls, self.status, self.body, self.content_type, self.disposition = [], status, body, content_type, disposition

    def __call__(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url.split("/api/v1/sessions", 1)[1], dict(params or {}), headers))
        response = requests.Response()
        response.status_code = self.status
        response._content = self.body
        response.headers["Content-Type"] = self.content_type
        response.headers["Content-Disposition"] = self.disposition
        return response


def test_export_proxies_the_file_with_the_same_filters_and_the_token(client, login, reports, monkeypatch):
    export = FakeExport()
    monkeypatch.setattr(session_remote.requests, "get", export)
    login("spd_supervisor")
    response = client.get(f"/supervisor/reports/export.csv?date_range=7d&station={STATION}&kit=")
    assert response.status_code == 200 and response.data.startswith(b"\xef\xbb\xbf")
    assert response.headers["Content-Disposition"] == 'attachment; filename="pef-report-2026-10.csv"'
    assert response.headers["Cache-Control"] == "no-store" and response.mimetype == "text/csv"
    path, params, headers = export.calls[0]
    assert path == "/reports/export" and params["format"] == "csv" and params["lang"] == "en"
    assert params["station_id"] == STATION and "kit_id" not in params and params["from"] < params["to"]
    assert headers["Authorization"] == "Bearer test-access-token"


def test_export_pdf_in_spanish_and_a_hostile_file_name(client, login, reports, monkeypatch):
    export = FakeExport(body=b"%PDF-1.4", content_type="application/pdf",
                        disposition='attachment; filename="../evil\r\nX-Injected: 1.pdf"')
    monkeypatch.setattr(session_remote.requests, "get", export)
    login("spd_supervisor", locale="es-MX")
    response = client.get("/supervisor/reports/export.pdf")
    assert response.status_code == 200 and response.mimetype == "application/pdf"
    assert response.headers["Content-Disposition"] == 'attachment; filename="evilX-Injected1.pdf"'
    assert "X-Injected" not in response.headers and export.calls[0][1]["lang"] == "es-MX"


def test_export_only_for_the_supervisor_and_known_formats(client, login, reports, monkeypatch):
    export = FakeExport()
    monkeypatch.setattr(session_remote.requests, "get", export)
    for role in ("station_operator", "it_admin"):
        login(role)
        assert client.get("/supervisor/reports/export.csv").status_code == 403
    login("spd_supervisor")
    assert client.get("/supervisor/reports/export.xlsx").status_code == 404
    assert export.calls == []


def test_export_failures_return_to_the_screen_with_a_notice(client, login, reports, monkeypatch):
    def down(*args, **kwargs):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(session_remote.requests, "get", down)
    login("spd_supervisor")
    response = client.get(f"/supervisor/reports/export.csv?station={STATION}")
    assert response.status_code == 302 and f"station={STATION}" in response.headers["Location"]
    rejected = FakeExport(status=400, body=json.dumps({"error": "invalid_request", "message": "Unknown station",
                                                       "details": {"field": "station_id"}}).encode(),
                          content_type="application/json")
    monkeypatch.setattr(session_remote.requests, "get", rejected)
    response = client.get(f"/supervisor/reports/export.csv?station={uuid.uuid4()}", follow_redirects=True)
    assert "Unknown station" in response.get_data(as_text=True)
    monkeypatch.setattr(session_remote, "SESSION_SERVICE_URL", "")
    assert client.get("/supervisor/reports/export.csv").status_code == 404


# --- notifications -----------------------------------------------------------------------------------------


def _item(kind, data, session_id=None):
    return {"id": str(uuid.uuid4()), "type": kind, "status": "unread", "title": "x", "message": "x",
            "session_id": session_id, "data": data, "created_at": "2026-11-03T17:40:02Z", "read_at": None}


@pytest.fixture
def new_types(app, monkeypatch):
    session = str(uuid.uuid4())
    items = [_item("review_pending", {"session_label": "WS-AAAA1111"}, session),
             _item("session_unresolved", {"session_label": "WS-BBBB2222"}, session),
             _item("station_alert", {"station_name": "Station 7", "kind": "heartbeat_lost", "station_id": STATION}),
             _item("station_alert", {"station_name": "Station 8", "kind": "camera_error", "camera_name": "Overhead",
                                     "error": "NotReadableError"})]

    def fake(method, url, params=None, json=None, timeout=None, headers=None, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response._content = __import__("json").dumps(
            {"items": items, "page": 1, "per_page": 20, "total": 4, "total_pages": 1, "unread": 4,
             "sort": "-created_at"}).encode()
        return response
    monkeypatch.setattr(notifications_remote, "NOTIFICATIONS_SERVICE_URL", "http://notifications.test")
    monkeypatch.setattr(notifications_remote.requests, "request", fake)
    return session


def test_new_notification_types_are_rendered(client, login, new_types):
    login("spd_supervisor")
    html = client.get("/notifications").get_data(as_text=True)
    assert "Session pending review" in html and "WS-AAAA1111 is waiting for your review" in html
    assert "Session closed as unresolved" in html and "WS-BBBB2222 was closed with an unreconciled physical" in html
    assert "Capture station alert" in html and "Capture station Station 7 stopped reporting." in html
    assert "Capture station Station 8 reported a camera error." in html
    assert html.count(f"/supervisor/sessions/{new_types}") >= 2           # station alerts have no session link


def test_new_notification_types_in_spanish(client, login, new_types):
    login("spd_supervisor", locale="es-MX")
    html = client.get("/notifications").get_data(as_text=True)
    assert "Sesión pendiente de revisión" in html and "Alerta de estación de captura" in html
    assert "La estación de captura Station 7 dejó de reportar." in html
