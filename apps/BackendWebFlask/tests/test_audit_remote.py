"""RF audit screens served by AuditService (adapter; HTTP answers simulated).

AuditService itself is tested in apps/AuditService (real PostgreSQL/Redis) and end to end in
scripts/e2e_audit.py. Here: the portal sends the verified token, asks for the same rows the screens
showed (access_audit, filters translated), renders them unchanged and fails in a controlled way.
"""

import json
import uuid

import pytest
import requests

from controllers import routes
from services import audit_remote

EVENT = {
    "id": str(uuid.uuid4()), "record_type": "access_audit", "occurred_at": "2026-10-21T15:00:00Z",
    "category": "business", "source_service": "session", "action": "session.scheduled", "outcome": "success",
    "actor": {"type": "user", "user_id": str(uuid.uuid4()), "name": "Supervisora Remota", "email": "spd@x.org",
              "role_codes": ["spd_supervisor"]},
    "institution_id": str(uuid.uuid4()),
    "resource": {"type": "work_session", "id": str(uuid.uuid4()), "label": "WS-ABCDEF12"},
    "session_id": None, "correlation_id": "corr-1", "ip": "10.0.0.9", "details": None,
}
SUMMARY = {"total": 1, "first_at": None, "last_at": None, "by_category": [], "by_source_service": [], "by_outcome": [],
           "by_record_type": [], "by_action": [{"value": "session.scheduled", "count": 1}],
           "by_resource_type": [{"value": "work_session", "count": 1}],
           "by_user": [{"user_id": EVENT["actor"]["user_id"], "name": "Supervisora Remota", "email": None, "count": 1}],
           "by_session": [{"session_id": EVENT["resource"]["id"], "label": "WS-ABCDEF12", "last_at": "2026-10-21T15:00:00Z",
                           "count": 1}]}


class FakeAudit:
    def __init__(self):
        self.calls = []

    def __call__(self, url, params=None, timeout=None, headers=None):
        path = url.split("/api/v1/audit", 1)[1]
        self.calls.append((path, dict(params or {}), headers))
        body = SUMMARY if path == "/events/summary" else {
            "items": [EVENT], "page": 1, "per_page": int((params or {}).get("per_page", 20)), "total": 1,
            "total_pages": 1, "sort": "-occurred_at"}
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(body).encode()
        return response


@pytest.fixture
def audit(app, monkeypatch):
    fake = FakeAudit()
    monkeypatch.setattr(audit_remote, "AUDIT_SERVICE_URL", "http://audit.test")
    monkeypatch.setattr(audit_remote.requests, "get", fake)
    from services.admin_local import audit_log
    monkeypatch.setattr(audit_log, "list_events", lambda **kw: pytest.fail("the audit log must not be read locally"))
    monkeypatch.setattr(routes, "use_v3_fixture", lambda *args: False)
    return fake


def test_admin_audit_log_reads_audit_service(client, login, audit):
    login("it_admin")
    page = client.get("/admin/audit-log?search=sched&outcome=success&date=2026-10-21")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "Supervisora Remota" in html and "WS-ABCDEF12" in html and "10.0.0.9" in html
    path, params, headers = audit.calls[0]
    assert path == "/events" and headers["Authorization"] == "Bearer test-access-token"
    assert params["record_type"] == "access_audit" and params["q"] == "sched" and params["outcome"] == "success"
    # The local day (America/Mexico_City by default) becomes a UTC range.
    assert params["from"] == "2026-10-21T06:00:00Z" and params["to"] == "2026-10-22T06:00:00Z"


def test_supervisor_audit_log_reads_audit_service(client, login, audit):
    login("spd_supervisor")
    session_id = EVENT["resource"]["id"]
    page = client.get(f"/supervisor/audit-log?session={session_id}&event=session.scheduled&date=7d")
    assert page.status_code == 200 and "Supervisora Remota" in page.get_data(as_text=True)
    params = audit.calls[0][1]
    assert params["session_id"] == session_id and params["action"] == "session.scheduled" and "from" in params


def test_audit_service_down_is_a_controlled_error(client, login, audit, monkeypatch):
    def down(*args, **kwargs):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(audit_remote.requests, "get", down)
    login("spd_supervisor")
    page = client.get("/supervisor/audit-log")
    assert page.status_code == 503 and "The audit service is temporarily unavailable" in page.get_data(as_text=True)
    login("it_admin")
    admin = client.get("/admin/audit-log")
    assert "The audit service is temporarily unavailable" in admin.get_data(as_text=True)


def test_operator_has_no_audit_screen(client, login, audit):
    login("station_operator")
    assert client.get("/supervisor/audit-log").status_code == 403
    assert client.get("/admin/audit-log").status_code == 403
    assert audit.calls == []
