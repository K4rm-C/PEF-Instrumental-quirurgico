"""MS-08 Supervisor tray screens (CatalogService and SessionService answers simulated; the services are
tested on real PostgreSQL in their suites and end to end in scripts/e2e_ms07_ms08.py)."""

import json
import uuid

import pytest
import requests

from services import session_remote, trays_remote

TRAY = str(uuid.uuid4())
KIT = str(uuid.uuid4())
ITEM = {"id": TRAY, "code": "TR-001", "name": "Lap tray", "status": "active", "version": 2, "notes": None,
        "kit_id": KIT, "kit_name": "Basic Laparotomy", "kit_version": 3, "kit_superseded": True,
        "created_at": "2026-11-09T10:00:00Z", "updated_at": "2026-11-09T11:00:00Z",
        "history": [{"version": 1, "changed_at": "2026-11-09T10:00:00Z", "change_type": "create", "changed_by_user_id": None,
                     "changed_by_name": "Super Visor", "before": None,
                     "after": {"code": "TR-001", "name": "Lap tray", "kit_id": KIT, "status": "active", "notes": None}}]}


class Fake:
    def __init__(self, answers):
        self.calls, self.answers = [], answers

    def __call__(self, method, url, json=None, params=None, timeout=None, headers=None):
        self.calls.append((method, url, json, params, headers))
        key = (method, url.split("/api/v1", 1)[1])
        status, body = self.answers.get(key, (404, {"error": "not_found"}))
        response = requests.Response()
        response.status_code = status
        response._content = __import__("json").dumps(body).encode()
        return response


@pytest.fixture
def services(app, monkeypatch):
    def install(answers=None):
        base = {("GET", "/catalog/trays"): (200, [ITEM]), ("GET", f"/catalog/trays/{TRAY}"): (200, ITEM),
                ("GET", "/catalog/kits"): (200, [{"id": KIT, "name": "Basic Laparotomy", "version": 4, "active": True}]),
                ("GET", f"/sessions/trays/{TRAY}"): (200, {"tray_id": TRAY, "tray_code": "TR-001", "sessions": [
                    {"id": str(uuid.uuid4()), "status": "closed", "scheduled_at": "2026-11-09T09:00:00Z", "started_at": None,
                     "ended_at": None, "kit_id": KIT, "kit_name": "Basic Laparotomy", "kit_version": 3,
                     "operator_name": "Opera Dora"}]})}
        base.update(answers or {})
        fake = Fake(base)
        monkeypatch.setattr(trays_remote, "CATALOG_SERVICE_URL", "http://catalog.test")
        monkeypatch.setattr(session_remote, "SESSION_SERVICE_URL", "http://session.test")
        monkeypatch.setattr(requests, "request", fake)
        return fake
    return install


def test_list_and_detail(client, login, services):
    fake = services()
    login("spd_supervisor")
    page = client.get("/supervisor/trays").get_data(as_text=True)
    assert 'data-tray="TR-001"' in page and "Kit superseded" in page
    detail = client.get(f"/supervisor/trays/{TRAY}").get_data(as_text=True)
    assert "data-tray-history" in detail and "Super Visor" in detail and "Opera Dora" in detail
    assert 'name="version" value="2"' in detail and "(superseded)" in detail
    assert all(call[4]["Authorization"] == "Bearer test-access-token" for call in fake.calls)


def test_create_forwards_the_form_and_shows_service_errors(client, login, services):
    fake = services({("POST", "/catalog/trays"): (409, {"error": "conflict", "details": {"reason": "tray_code_taken"}})})
    login("spd_supervisor")
    response = client.post("/supervisor/trays/new", data={"code": "TR-001", "name": "", "kit_id": KIT, "status": "active"})
    assert response.status_code == 409 and "A tray with this code already exists." in response.get_data(as_text=True)
    sent = next(c for c in fake.calls if c[0] == "POST")[2]
    assert sent == {"code": "TR-001", "name": None, "kit_id": KIT, "status": "active", "notes": None}


def test_edit_sends_the_version_and_reports_conflicts(client, login, services):
    fake = services({("PUT", f"/catalog/trays/{TRAY}"): (409, {"error": "version_conflict",
                                                             "details": {"current_version": 3}})})
    login("spd_supervisor")
    response = client.post(f"/supervisor/trays/{TRAY}", data={"version": "2", "code": "TR-001", "kit_id": KIT,
                                                               "status": "out_of_service"})
    assert response.status_code == 409 and "Someone else changed this tray" in response.get_data(as_text=True)
    assert next(c for c in fake.calls if c[0] == "PUT")[2]["version"] == 2


def test_only_the_supervisor_and_unavailable_service(client, login, services, monkeypatch):
    services()
    for role in ("station_operator", "it_admin"):
        login(role)
        assert client.get("/supervisor/trays").status_code == 403
    login("spd_supervisor")
    monkeypatch.setattr(requests, "request", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("x")))
    page = client.get("/supervisor/trays")
    assert page.status_code == 503 and "data-tray-unavailable" in page.get_data(as_text=True)


def test_spanish(client, login, services):
    services()
    login("spd_supervisor", locale="es-MX")
    html = client.get("/supervisor/trays").get_data(as_text=True)
    assert "Charolas" in html and "Kit reemplazado" in html
