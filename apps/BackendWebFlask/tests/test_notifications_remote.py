"""Notifications page and header bell (adapter; NotificationsService answers simulated).

NotificationsService itself is tested in apps/NotificationsService (real PostgreSQL/Redis) and end to
end in scripts/e2e_notifications.py. Here: the portal forwards the verified token, links each
notification to the session screen of the recipient's role, marks read, and degrades in a controlled
way — no page other than /notifications ever calls the service while rendering.
"""

import json
import uuid

import pytest
import requests

from services import notifications_remote

SESSION = str(uuid.uuid4())
ITEM = {"id": str(uuid.uuid4()), "type": "correction_required", "status": "unread", "title": "Correction required",
        "message": "x", "session_id": SESSION, "data": {"session_label": "WS-ABCDEF12", "open_discrepancies": 2},
        "created_at": "2026-10-21T17:40:02Z", "read_at": None}


class FakeNotifications:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, params=None, json_body=None, timeout=None, headers=None, **kwargs):
        path = url.split("/api/v1/notifications", 1)[1]
        self.calls.append((method, path, dict(params or {}), headers))
        if path == "/unread-count":
            body = {"unread": 1}
        elif path == "/read-all":
            body = {"updated": 1, "unread": 0}
        elif path.endswith("/read"):
            body = {**ITEM, "status": "read", "read_at": "2026-10-21T17:41:00Z"}
        else:
            body = {"items": [ITEM], "page": 1, "per_page": 20, "total": 1, "total_pages": 1, "unread": 1,
                    "sort": "-created_at"}
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(body).encode()
        return response


@pytest.fixture
def notifications(app, monkeypatch):
    fake = FakeNotifications()
    monkeypatch.setattr(notifications_remote, "NOTIFICATIONS_SERVICE_URL", "http://notifications.test")
    monkeypatch.setattr(notifications_remote.requests, "request", fake)
    return fake


@pytest.mark.parametrize("role, link", [
    ("station_operator", f"/operator/sessions/{SESSION}"),
    ("spd_supervisor", f"/supervisor/sessions/{SESSION}"),
])
def test_page_lists_own_notifications_with_the_role_session_link(client, login, notifications, role, link):
    login(role)
    page = client.get("/notifications?status=unread&type=correction_required")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "Correction required" in html and "WS-ABCDEF12" in html
    assert link in html and "Open discrepancies: 2" in html
    method, path, params, headers = notifications.calls[0]
    assert (method, path) == ("GET", "") and headers["Authorization"] == "Bearer test-access-token"
    assert params["status"] == "unread" and params["type"] == "correction_required"


def test_admin_page_has_no_session_link(client, login, notifications):
    login("it_admin")
    html = client.get("/notifications").get_data(as_text=True)
    assert "WS-ABCDEF12" in html and f"/sessions/{SESSION}" not in html


def test_page_in_spanish(client, login, notifications):
    login("station_operator", locale="es-MX")
    html = client.get("/notifications").get_data(as_text=True)
    assert "Corrección requerida" in html and "La sesión de conteo WS-ABCDEF12 requiere corrección" in html


def test_unknown_filters_are_not_forwarded(client, login, notifications):
    login("station_operator")
    client.get("/notifications?status=deleted&type=other&recipient=x")
    assert notifications.calls[0][2] == {"page": 1, "per_page": 20}


def test_mark_read_and_open_the_session(client, login, notifications):
    login("station_operator")
    response = client.post(f"/notifications/{ITEM['id']}/read", data={"open": f"/operator/sessions/{SESSION}"})
    assert response.status_code == 302 and response.headers["Location"].endswith(f"/operator/sessions/{SESSION}")
    assert notifications.calls[-1][:2] == ("PATCH", f"/{ITEM['id']}/read")
    external = client.post(f"/notifications/{ITEM['id']}/read", data={"open": "//evil.example/x"})
    assert external.headers["Location"].endswith("/notifications")


def test_read_all(client, login, notifications):
    login("spd_supervisor")
    response = client.post("/notifications/read-all", follow_redirects=True)
    assert notifications.calls[0][:2] == ("PATCH", "/read-all")
    assert "1 notification(s) marked as read." in response.get_data(as_text=True)


def test_bell_count_json(client, login, notifications):
    login("station_operator")
    assert client.get("/notifications/unread-count").get_json() == {"available": True, "enabled": True, "unread": 1}


def test_bell_count_requires_a_session(client, notifications):
    response = client.get("/notifications/unread-count")
    assert response.status_code == 401 and notifications.calls == []


def test_service_down_is_a_controlled_notice(client, login, notifications, monkeypatch):
    def down(*args, **kwargs):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(notifications_remote.requests, "request", down)
    login("station_operator")
    page = client.get("/notifications")
    assert page.status_code == 503 and "Notifications are temporarily unavailable" in page.get_data(as_text=True)
    assert client.get("/notifications/unread-count").get_json() == {"available": False, "enabled": True, "unread": 0}
    marked = client.post(f"/notifications/{ITEM['id']}/read", follow_redirects=True)
    assert "Notifications are temporarily unavailable" in marked.get_data(as_text=True)


def test_disabled_without_service_url(client, login, monkeypatch):
    monkeypatch.setattr(notifications_remote, "NOTIFICATIONS_SERVICE_URL", "")
    login("spd_supervisor")
    assert "Notifications are not enabled" in client.get("/notifications").get_data(as_text=True)
    assert client.get("/notifications/unread-count").get_json()["enabled"] is False


@pytest.mark.parametrize("role, path", [
    ("station_operator", "/operator/profile"), ("spd_supervisor", "/supervisor/profile"), ("it_admin", "/admin/profile"),
])
def test_other_pages_render_the_bell_without_calling_the_service(client, login, notifications, role, path):
    login(role)
    html = client.get(path).get_data(as_text=True)
    assert 'data-notifications-bell' in html and 'href="/notifications"' in html and "js/notifications.js" in html
    assert notifications.calls == []


def test_unread_count_is_not_called_with_a_frontend_identity_header(client, notifications):
    response = client.get("/notifications/unread-count", headers={"X-User-Id": str(uuid.uuid4())})
    assert response.status_code == 401 and notifications.calls == []
