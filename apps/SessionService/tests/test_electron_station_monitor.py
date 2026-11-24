"""ELECTRON-01: read-only station monitor endpoints for the desktop client.

Real PostgreSQL/Redis (conftest `world`): institution isolation, station association, own-session rule,
ambiguity (never picks a session arbitrarily), official result mirroring and observe-only live tickets.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid

import pytest
from sqlalchemy import text

from conftest import TransactionalDatabase, _code_id, make_settings
from main import create_app

SECRET = "pytest-monitor-secret"


def _url(station_id, session_id=None, suffix=""):
    base = f"/api/v1/sessions/stations/{station_id}/monitor"
    return base + (f"/{session_id}" if session_id else "") + suffix


@pytest.fixture
def live_client(db_conn, redis_client):
    app = create_app(make_settings(vision_monitor_secret=SECRET, vision_monitor_ws_url="ws://gw.test/ws",
                                   vision_monitor_ticket_ttl=45),
                     database=TransactionalDatabase(db_conn), redis_client=redis_client)
    return app.test_client()


def _add_expected(db_conn, session_id, family_id, qty):
    db_conn.execute(text("INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source) "
                         "VALUES (:s, :f, :q, 'kit_snapshot')"), {"s": session_id, "f": family_id, "q": qty})


def _verify_monitor_ticket(ticket: str, session_id: str) -> bool:
    """Independent re-implementation of VisionWorker/monitor_ticket.py (no cross-service import)."""
    prefix, sid, exp, nonce, sig = ticket.split(".")
    msg = f"pef-monitor:v1:{sid}:{exp}:{nonce}".encode()
    expected = hmac.new(SECRET.encode(), msg, hashlib.sha256).hexdigest()
    return prefix == "m1" and sid == session_id and int(exp) > time.time() and hmac.compare_digest(expected, sig)


# --- Station overview ---------------------------------------------------------------------------


def test_active_session_of_the_station(client, world, as_user, db_conn):
    a2 = world.sessions["a2"].id
    _add_expected(db_conn, a2, world.families["a"], 3)
    _add_expected(db_conn, a2, world.families["b"], 2)
    res = client.get(_url(world.stations["A"]), headers=as_user("op_a2"))
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["state"] == "active"
    assert body["station"]["id"] == str(world.stations["A"])
    session = body["session"]
    assert session["session_id"] == str(a2)
    assert session["status_code"] == "in_progress"
    assert session["phase_code"] == "start"
    assert session["capture_mode"] == "vision"
    assert session["reference"]["total"] == 5
    assert {f["expected_quantity"] for f in session["reference"]["families"]} == {3, 2}
    assert session["official"]["result"] == "pending"
    assert session["official"]["validated"] is False
    # No secret configured in the default app: the live monitor is honestly reported as unavailable.
    assert session["live_available"] is False
    assert body["live_monitor_enabled"] is False
    assert session["patient_name"] is None
    assert session["doctor_name"] is None


def test_clinical_names_for_assigned_operator(client, world, as_user, db_conn):
    operation_id = world.sessions["a2"].operation_id
    patient_id, physician_id = uuid.uuid4(), uuid.uuid4()
    tenant = world.institutions["A"]
    db_conn.execute(text("INSERT INTO patient (id, display_name, institution_id) VALUES (:id, :name, :tenant)"),
                    {"id": patient_id, "name": "María García", "tenant": tenant})
    db_conn.execute(text("INSERT INTO operation_patient (operation_id, patient_id) VALUES (:op, :patient)"),
                    {"op": operation_id, "patient": patient_id})
    db_conn.execute(text("INSERT INTO physician (id, name, institution_id) VALUES (:id, :name, :tenant)"),
                    {"id": physician_id, "name": "Dra. Ana Ruiz", "tenant": tenant})
    db_conn.execute(text("INSERT INTO operation_physician (operation_id, physician_id, surgical_role_id) "
                         "VALUES (:op, :doctor, :role)"),
                    {"op": operation_id, "doctor": physician_id,
                     "role": _code_id(db_conn, "cat_surgical_role", "surgeon")})
    db_conn.execute(text("UPDATE operation SET procedure_type_id = :procedure WHERE id = :op"),
                    {"op": operation_id, "procedure": _code_id(db_conn, "cat_procedure_type", "appendectomy")})
    response = client.get(_url(world.stations["A"]), headers=as_user("op_a2"))
    assert response.status_code == 200
    session = response.get_json()["session"]
    assert session["patient_name"] == "María García"
    assert session["doctor_name"] == "Dra. Ana Ruiz"
    assert session["procedure_code"] == "appendectomy"
    assert session["procedure_name"]


def test_no_active_session_for_this_operator_is_idle(client, world, as_user):
    # op_a has sessions at station A but none in progress.
    body = client.get(_url(world.stations["A"]), headers=as_user("op_a")).get_json()
    assert body["state"] == "idle"
    assert body["session"] is None
    assert body["candidates"] == []


def test_station_of_another_institution_is_not_found(client, world, as_user):
    assert client.get(_url(world.stations["A"]), headers=as_user("op_b")).status_code == 404
    assert client.get(_url(world.stations["B"]), headers=as_user("op_a2")).status_code == 404


def test_unknown_or_malformed_station_is_not_found(client, world, as_user):
    assert client.get(_url(uuid.uuid4()), headers=as_user("op_a2")).status_code == 404
    assert client.get(_url("not-a-uuid"), headers=as_user("op_a2")).status_code == 404


def test_disabled_station_is_conflict(client, world, as_user, db_conn):
    db_conn.execute(text("UPDATE capture_station SET active = FALSE WHERE id = :id"), {"id": world.stations["A"]})
    res = client.get(_url(world.stations["A"]), headers=as_user("op_a2"))
    assert res.status_code == 409
    assert res.get_json()["error"] == "station_disabled"


@pytest.mark.parametrize("user_key", ["spd_a", "admin_a", "floor_a", "norole_a"])
def test_only_station_operators(client, world, as_user, user_key):
    assert client.get(_url(world.stations["A"]), headers=as_user(user_key)).status_code == 403


def test_authentication_required_and_revocation(client, world, tokens):
    assert client.get(_url(world.stations["A"])).status_code == 401
    user = world.users["op_a2"]
    revoked = tokens.issue(user.id, user.institution_id, revoked=True)
    assert client.get(_url(world.stations["A"]), headers={"Authorization": f"Bearer {revoked}"}).status_code == 401


def test_two_sessions_in_progress_are_ambiguous(client, world, as_user, db_conn):
    a2 = world.sessions["a2"].id
    # Clone a2 as a second in-progress session of the same operator at the same station.
    db_conn.execute(text("""
        INSERT INTO work_session (id, started_at, status_id, user_id, station_id, kit_id, current_phase_id, capture_mode)
        SELECT :new, started_at, status_id, user_id, station_id, kit_id, current_phase_id, capture_mode
        FROM work_session WHERE id = :a2
    """), {"new": uuid.uuid4(), "a2": a2})
    body = client.get(_url(world.stations["A"]), headers=as_user("op_a2")).get_json()
    assert body["state"] == "ambiguous"
    assert body["session"] is None
    assert len(body["candidates"]) == 2


def test_phase_change_is_reflected(client, world, as_user, db_conn):
    final = _code_id(db_conn, "cat_operation_phase", "final_count")
    db_conn.execute(text("UPDATE work_session SET current_phase_id = :p WHERE id = :id"),
                    {"p": final, "id": world.sessions["a2"].id})
    session = client.get(_url(world.stations["A"]), headers=as_user("op_a2")).get_json()["session"]
    assert session["phase_code"] == "final_count"


def test_reference_version_changes_with_expected_inventory(client, world, as_user, db_conn):
    a2 = world.sessions["a2"].id
    _add_expected(db_conn, a2, world.families["a"], 3)
    v1 = client.get(_url(world.stations["A"]), headers=as_user("op_a2")).get_json()["session"]["reference"]
    db_conn.execute(text("UPDATE expected_inventory SET expected_quantity = 4 WHERE session_id = :s"), {"s": a2})
    v2 = client.get(_url(world.stations["A"]), headers=as_user("op_a2")).get_json()["session"]["reference"]
    assert (v1["total"], v2["total"]) == (3, 4)
    assert v1["version"] != v2["version"]


def test_xml_rendering(client, world, as_user):
    res = client.get(_url(world.stations["A"]), headers=as_user("op_a2", Accept="application/xml"))
    assert res.status_code == 200
    assert res.data.startswith(b"<?xml")


# --- Followed session -----------------------------------------------------------------------------


def test_followed_session_mirrors_the_official_result(client, world, as_user):
    # a3: correction_required, manual_close recorded, one open discrepancy (conftest world).
    body = client.get(_url(world.stations["A"], world.sessions["a3"].id), headers=as_user("op_a")).get_json()
    official = body["session"]["official"]
    assert body["session"]["status_code"] == "correction_required"
    assert official["validated"] is True
    assert official["result"] == "discrepancy"
    assert official["open_discrepancies"] >= 1


def test_followed_session_validated_without_discrepancies(client, world, as_user, db_conn):
    a2 = world.sessions["a2"].id
    review = _code_id(db_conn, "cat_session_status", "awaiting_spd_review")
    db_conn.execute(text("UPDATE work_session SET status_id = :s, ended_at = now() WHERE id = :id"),
                    {"s": review, "id": a2})
    db_conn.execute(text("INSERT INTO count_event (event_type_id, payload, occurred_at, session_id, user_id) "
                         "VALUES (:t, CAST('{\"open_discrepancies\": 0, \"close_path\": \"manual_count\"}' AS jsonb), "
                         "now(), :s, :u)"),
                    {"t": world.event_type["manual_close"], "s": a2, "u": world.users["op_a2"].id})
    session = client.get(_url(world.stations["A"], a2), headers=as_user("op_a2")).get_json()["session"]
    assert session["official"]["result"] == "validated_match"
    assert session["live_available"] is False
    # And it is no longer the active session of the station.
    assert client.get(_url(world.stations["A"]), headers=as_user("op_a2")).get_json()["state"] == "idle"


def test_followed_session_closed_states(client, world, as_user, db_conn):
    closed = _code_id(db_conn, "cat_session_status", "closed")
    a3 = world.sessions["a3"].id
    db_conn.execute(text("UPDATE work_session SET status_id = :s WHERE id = :id"), {"s": closed, "id": a3})
    session = client.get(_url(world.stations["A"], a3), headers=as_user("op_a")).get_json()["session"]
    # Closed after discrepancies is never presented as a clean close.
    assert session["official"]["result"] == "closed_with_discrepancies"


def test_session_of_other_station_operator_or_institution_is_not_found(client, world, as_user, db_conn):
    a2 = world.sessions["a2"].id
    other_station = uuid.uuid4()
    db_conn.execute(text("INSERT INTO capture_station (id, name, room_id) VALUES (:id, 'Other', :r)"),
                    {"id": other_station, "r": world.rooms["A"]})
    assert client.get(_url(other_station, a2), headers=as_user("op_a2")).status_code == 404   # other station
    assert client.get(_url(world.stations["A"], a2), headers=as_user("op_a")).status_code == 404  # other operator
    assert client.get(_url(world.stations["B"], world.sessions["b1"].id),
                      headers=as_user("op_a2")).status_code == 404                             # other institution


# --- Observe-only live ticket -------------------------------------------------------------------------


def test_live_ticket_requires_configuration(client, world, as_user):
    res = client.post(_url(world.stations["A"], world.sessions["a2"].id, "/live-ticket"), headers=as_user("op_a2"))
    assert res.status_code == 503
    assert res.get_json()["error"] == "monitor_unavailable"


def test_live_ticket_issued_for_active_vision_session(live_client, world, as_user):
    a2 = str(world.sessions["a2"].id)
    overview = live_client.get(_url(world.stations["A"]), headers=as_user("op_a2")).get_json()
    assert overview["session"]["live_available"] is True
    res = live_client.post(_url(world.stations["A"], a2, "/live-ticket"), headers=as_user("op_a2"))
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["scope"] == "observe"
    assert body["ws_url"] == "ws://gw.test/ws"
    assert body["ttl_s"] == 45
    assert _verify_monitor_ticket(body["ticket"], a2)
    # It is not a portal frame-submit ticket (4 parts) and cannot be confused with one.
    assert len(body["ticket"].split(".")) == 5


def test_live_ticket_refused_outside_in_progress(live_client, world, as_user):
    res = live_client.post(_url(world.stations["A"], world.sessions["a3"].id, "/live-ticket"),
                           headers=as_user("op_a"))
    assert res.status_code == 409
    assert res.get_json()["error"] == "live_not_available"


def test_live_ticket_other_operator_or_station(live_client, world, as_user):
    a2 = world.sessions["a2"].id
    assert live_client.post(_url(world.stations["A"], a2, "/live-ticket"), headers=as_user("op_a")).status_code == 404
    assert live_client.post(_url(world.stations["A"], a2, "/live-ticket"), headers=as_user("op_b")).status_code == 404
    assert live_client.post(_url(world.stations["A"], a2, "/live-ticket"),
                            headers=as_user("spd_a")).status_code == 403
