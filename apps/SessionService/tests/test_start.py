"""POST /api/v1/sessions/{id}/start (Phase C)."""

from __future__ import annotations

import socket
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from write_fixtures import wdata  # noqa: F401  (fixture)

BASE = "/api/v1/sessions"


def _url(session_id):
    return f"{BASE}/{session_id}/start"


def _state(db_conn, session_id):
    return db_conn.execute(text(
        "SELECT s.code AS status, w.capture_mode, w.started_at, p.code AS phase FROM work_session w "
        "JOIN cat_session_status s ON s.id = w.status_id LEFT JOIN cat_operation_phase p ON p.id = w.current_phase_id "
        "WHERE w.id = :s"), {"s": session_id}).mappings().one()


def _events(db_conn, session_id):
    return db_conn.execute(text(
        "SELECT t.code, e.user_id, e.payload FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
        "WHERE e.session_id = :s"), {"s": session_id}).all()


def _audits(db_conn, session_id):
    return db_conn.execute(text("SELECT action, correlation_id, actor_user_id FROM access_audit WHERE resource_id = :s AND outcome = 'success'"),
                           {"s": session_id}).all()


def _add_agreement(db_conn, world, session_id, *, effective_in_future=False, active_notice=True):
    notice = uuid.uuid4()
    effective = datetime.now(timezone.utc) + (timedelta(days=30) if effective_in_future else timedelta(days=-30))
    db_conn.execute(text("INSERT INTO privacy_notice_version (id, version, effective_at, document_uri, active) "
                         "VALUES (:id, :v, :e, 'https://example.invalid/n', :a)"),
                    {"id": notice, "v": f"st-{uuid.uuid4().hex[:8]}", "e": effective, "a": active_notice})
    db_conn.execute(text("INSERT INTO session_processing_agreement (session_id, privacy_notice_version_id) "
                         "VALUES (:s, :n)"), {"s": session_id, "n": notice})


def test_start_manual_without_agreement(client, world, wdata, as_user, db_conn):
    s = world.sessions["a1"].id
    db_conn.execute(text("INSERT INTO expected_inventory (session_id, family_id, expected_quantity) VALUES (:s, :f, 2)"),
                    {"s": s, "f": world.families["a"]})
    response = client.post(_url(s), headers=as_user("op_a", **{"X-Correlation-ID": "corr-start-1"}))
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["status_code"] == "in_progress" and body["capture_mode"] == "manual_no_privacy"
    assert body["started_at"] is not None and body["current_phase_code"] == "start"
    assert body["ended_at"] is None and body["closed_by_user_id"] is None
    state = _state(db_conn, s)
    assert state["status"] == "in_progress" and state["capture_mode"] == "manual_no_privacy"
    events = _events(db_conn, s)
    assert [(e[0], e[1]) for e in events] == [("session_open", world.users["op_a"].id)]
    assert events[0][2] == {"phase_code": "start", "capture_mode": "manual_no_privacy"}
    assert [tuple(a) for a in _audits(db_conn, s)] == [("session.start", "corr-start-1", world.users["op_a"].id)]
    # Existing expected inventory is left as is.
    assert db_conn.execute(text("SELECT count(*) FROM expected_inventory WHERE session_id = :s"), {"s": s}).scalar_one() == 1


def test_start_with_valid_agreement_is_vision(client, world, wdata, as_user, db_conn):
    s = world.sessions["a1"].id
    _add_agreement(db_conn, world, s)
    body = client.post(_url(s), headers=as_user("op_a")).get_json()
    assert body["capture_mode"] == "vision" and body["privacy_agreement"] is True


def test_agreement_with_notice_no_longer_offered_is_still_valid(client, world, wdata, as_user, db_conn):
    s = world.sessions["a1"].id
    _add_agreement(db_conn, world, s, active_notice=False)   # `active` only governs NEW agreements (DS07)
    assert client.post(_url(s), headers=as_user("op_a")).get_json()["capture_mode"] == "vision"


def test_agreement_with_notice_not_yet_in_force_is_not_valid(client, world, wdata, as_user, db_conn):
    s = world.sessions["a1"].id
    _add_agreement(db_conn, world, s, effective_in_future=True)
    body = client.post(_url(s), headers=as_user("op_a")).get_json()
    assert body["capture_mode"] == "manual_no_privacy"


def test_start_vision_does_not_contact_vision_worker(client, world, wdata, as_user, db_conn, monkeypatch):
    """Session has no runtime dependency on VisionWorker: no socket may be opened during start."""
    s = world.sessions["a1"].id
    _add_agreement(db_conn, world, s)
    opened = []
    real_create_connection = socket.create_connection

    def guard(address, *args, **kwargs):
        opened.append(address)
        return real_create_connection(address, *args, **kwargs)
    monkeypatch.setattr(socket, "create_connection", guard)
    response = client.post(_url(s), headers=as_user("op_a"))
    assert response.status_code == 200 and response.get_json()["capture_mode"] == "vision"
    assert not any(str(port) == "5002" for _, port in [a for a in opened if isinstance(a, tuple)])


def test_start_copies_kit_lines_only_when_inventory_is_empty(client, world, wdata, as_user, db_conn):
    s = world.sessions["a1"].id   # no expected lines; kit A = a×2, b×1
    assert client.post(_url(s), headers=as_user("op_a")).status_code == 200
    rows = db_conn.execute(text("SELECT family_id, expected_quantity, source FROM expected_inventory WHERE session_id = :s"),
                           {"s": s}).all()
    assert {tuple(r) for r in rows} == {(world.families["a"], 2, "kit_snapshot"), (world.families["b"], 1, "kit_snapshot")}


def test_start_sets_start_phase_when_missing(client, world, wdata, as_user, db_conn):
    s = world.sessions["a1"].id
    db_conn.execute(text("UPDATE work_session SET current_phase_id = NULL WHERE id = :s"), {"s": s})
    assert client.post(_url(s), headers=as_user("op_a")).get_json()["current_phase_code"] == "start"


def test_repeat_start_is_409(client, world, wdata, as_user, db_conn):
    s = world.sessions["a1"].id
    assert client.post(_url(s), headers=as_user("op_a")).status_code == 200
    second = client.post(_url(s), headers=as_user("op_a"))
    assert second.status_code == 409 and second.get_json()["details"] == {"status_code": "in_progress"}
    assert len(_events(db_conn, s)) == 1 and len(_audits(db_conn, s)) == 1


@pytest.mark.parametrize("session_key, status", [("a2", "in_progress"), ("a3", "correction_required"),
                                                 ("a_unscheduled", "closed")])
def test_non_scheduled_is_409(client, world, wdata, as_user, db_conn, session_key, status):
    owner = "op_a2" if session_key in ("a2", "a_unscheduled") else "op_a"
    s = world.sessions[session_key].id
    events_before = len(_events(db_conn, s))
    response = client.post(_url(s), headers=as_user(owner))
    assert response.status_code == 409 and response.get_json()["details"] == {"status_code": status}
    assert len(_events(db_conn, s)) == events_before and _audits(db_conn, s) == []


def test_scheduled_with_capture_mode_already_set_is_409(client, world, wdata, as_user, db_conn):
    s = world.sessions["a1"].id
    db_conn.execute(text("UPDATE work_session SET capture_mode = 'vision' WHERE id = :s"), {"s": s})
    response = client.post(_url(s), headers=as_user("op_a"))
    assert response.status_code == 409
    assert _state(db_conn, s)["status"] == "scheduled" and _events(db_conn, s) == []


@pytest.mark.parametrize("user_key, target, status", [
    ("op_a2", "a1", 404),          # other operator, same tenant
    ("op_b", "a1", 404),           # operator of another tenant
    ("op_a", "b1", 404),           # session of another tenant
    ("op_a", "no_operation", 404),  # session without operation
    ("spd_a", "a1", 403),          # supervisor without station_operator
    ("admin_a", "a1", 403),
    ("multi_a", "a1", 404),        # has station_operator but is not the assigned operator
])
def test_start_access_rules(client, world, wdata, as_user, db_conn, user_key, target, status):
    s = world.sessions[target].id
    response = client.post(_url(s), headers=as_user(user_key))
    assert response.status_code == status
    assert _audits(db_conn, s) == []


def test_assigned_multi_role_operator_can_start(client, world, wdata, as_user, db_conn):
    s = world.sessions["a1"].id
    db_conn.execute(text("UPDATE work_session SET user_id = :u WHERE id = :s"), {"u": world.users["multi_a"].id, "s": s})
    assert client.post(_url(s), headers=as_user("multi_a")).status_code == 200


@pytest.mark.parametrize("bad_id", ["not-a-uuid", str(uuid.uuid4())])
def test_invalid_or_unknown_id_is_404(client, world, wdata, as_user, bad_id):
    assert client.post(_url(bad_id), headers=as_user("op_a")).status_code == 404


@pytest.mark.parametrize("body, content_type", [
    ({"capture_mode": "vision"}, None), ({"institution_id": "x"}, None), ({"vision_enabled": True}, None),
])
def test_start_rejects_parameters(client, world, wdata, as_user, db_conn, body, content_type):
    s = world.sessions["a1"].id
    response = client.post(_url(s), json=body, headers=as_user("op_a"))
    assert response.status_code == 400 and response.get_json()["details"] == {"unknown_fields": sorted(body)}
    assert _state(db_conn, s)["status"] == "scheduled"


@pytest.mark.parametrize("kwargs", [
    {},                                                                     # empty body
    {"json": {}},                                                           # {}
    {"data": "<start_request/>", "content_type": "application/xml"},         # XML equivalent
])
def test_start_empty_body_forms(client, world, wdata, as_user, kwargs):
    assert client.post(_url(world.sessions["a1"].id), headers=as_user("op_a"), **kwargs).status_code == 200


def test_start_xml_response(client, world, wdata, as_user):
    response = client.post(_url(world.sessions["a1"].id), data="<start_request/>",
                           headers=as_user("op_a", **{"Content-Type": "application/xml", "Accept": "application/xml"}))
    root = ET.fromstring(response.data)
    assert response.status_code == 200 and root.tag == "session"
    assert root.findtext("status_code") == "in_progress" and root.findtext("capture_mode") == "manual_no_privacy"


def test_start_xml_with_fields_is_400(client, world, wdata, as_user):
    response = client.post(_url(world.sessions["a1"].id), data="<start_request><capture_mode>vision</capture_mode></start_request>",
                           headers=as_user("op_a", **{"Content-Type": "application/xml"}))
    assert response.status_code == 400


def test_audit_failure_rolls_back_start(client, world, wdata, as_user, db_conn, monkeypatch):
    from adapters import audit_adapter

    s = world.sessions["a1"].id
    monkeypatch.setattr(audit_adapter, "ACTOR_TYPE", "robot")
    assert client.post(_url(s), headers=as_user("op_a")).status_code == 500
    state = _state(db_conn, s)
    assert state["status"] == "scheduled" and state["capture_mode"] is None and state["started_at"] is None
    assert _events(db_conn, s) == [] and _audits(db_conn, s) == []
    assert db_conn.execute(text("SELECT count(*) FROM expected_inventory WHERE session_id = :s"), {"s": s}).scalar_one() == 0


def test_service_code_has_no_vision_or_http_client_dependency():
    """Session never talks to VisionWorker: no HTTP/WS client and no worker address in its code."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sources = [p for p in root.rglob("*.py") if ".venv" not in p.parts and "tests" not in p.parts]
    requirements = (root / "requirements.txt").read_text(encoding="utf-8").lower()
    for forbidden in ("requests", "httpx", "websocket", "aiohttp"):
        assert forbidden not in requirements
    for path in sources:
        code = path.read_text(encoding="utf-8")
        for token in ("import requests", "import httpx", "urllib.request", "websocket", ":5002", "VISION_WORKER", "vision_bridge"):
            assert token not in code, f"{token} in {path.name}"


@pytest.mark.parametrize("corr, preserved", [("s" * 64, True), ("s" * 65, False)])
@pytest.mark.parametrize("fmt", ["json", "xml"])
def test_start_audit_correlation_id_equals_response_header(client, world, wdata, as_user, db_conn, corr, preserved, fmt):
    s = world.sessions["a1"].id
    headers = as_user("op_a", **{"X-Correlation-ID": corr})
    if fmt == "xml":
        response = client.post(_url(s), data="<start_request/>",
                               headers={**headers, "Content-Type": "application/xml", "Accept": "application/xml"})
    else:
        response = client.post(_url(s), json={}, headers=headers)
    assert response.status_code == 200
    answered = response.headers["X-Correlation-ID"]
    assert (answered == corr) is preserved and (preserved or str(uuid.UUID(answered)) == answered)
    assert [a[1] for a in _audits(db_conn, s)] == [answered]


def test_error_envelope_uses_the_same_replaced_correlation_id(client, world, wdata, as_user):
    response = client.post(_url(world.sessions["a1"].id), json={"capture_mode": "vision"},
                           headers=as_user("op_a", **{"X-Correlation-ID": "e" * 65, "Accept": "application/xml"}))
    assert response.status_code == 400
    generated = response.headers["X-Correlation-ID"]
    assert str(uuid.UUID(generated)) == generated
    assert ET.fromstring(response.data).findtext("correlation_id") == generated
