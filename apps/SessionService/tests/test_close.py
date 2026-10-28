"""POST /api/v1/sessions/{id}/close (Phase E)."""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import text

from count_fixtures import set_status
from review_fixtures import ENDED_AT, T0, add_discrepancy, ds03_snapshot, rdata  # noqa: F401  (fixture)

BASE = "/api/v1/sessions"
BODY = {"material_recovered": True}


def _url(session_id):
    return f"{BASE}/{session_id}/close"


def _post(client, as_user, session_id, body=BODY, user="spd_a", **headers):
    return client.post(_url(session_id), json=body, headers=as_user(user, **headers))


def _session(db_conn, session_id):
    return db_conn.execute(text(
        "SELECT s.code AS status, w.closed_by_user_id, w.ended_at FROM work_session w "
        "JOIN cat_session_status s ON s.id = w.status_id WHERE w.id = :s"), {"s": session_id}).mappings().one()


def _session_close_events(db_conn, session_id):
    return db_conn.execute(text(
        "SELECT e.user_id, e.payload, e.occurred_at, e.family_id FROM count_event e "
        "JOIN cat_event_type t ON t.id = e.event_type_id WHERE e.session_id = :s AND t.code = 'session_close'"),
        {"s": session_id}).mappings().all()


def _audits(db_conn, session_id):
    return [tuple(r) for r in db_conn.execute(text(
        "SELECT action, correlation_id, actor_user_id FROM access_audit WHERE resource_id = :s AND outcome = 'success'"), {"s": session_id})]


def _cycle_events(db_conn, piece_id):
    return db_conn.execute(text(
        "SELECT s.code, e.session_id, e.operation_id, e.notes FROM instrument_cycle_event e "
        "JOIN cat_instrument_cycle_status s ON s.id = e.cycle_status_id WHERE e.instrument_id = :p"),
        {"p": piece_id}).all()


def _operation_status(db_conn, operation_id):
    return db_conn.execute(text("SELECT s.code FROM operation o JOIN cat_operation_status s ON s.id = o.status_id "
                                "WHERE o.id = :o"), {"o": operation_id}).scalar_one()


def _assert_untouched(db_conn, rdata, before, session_id=None):
    session_id = session_id or rdata.aw
    assert ds03_snapshot(db_conn) == before
    state = _session(db_conn, session_id)
    assert state["status"] != "closed" and state["closed_by_user_id"] is None
    assert _session_close_events(db_conn, session_id) == [] and _audits(db_conn, session_id) == []


# --- Happy path + DS03 side effects ---------------------------------------------------------------


def test_close_happy_path(client, world, rdata, as_user, db_conn):
    before = ds03_snapshot(db_conn)
    response = _post(client, as_user, rdata.aw, **{"X-Correlation-ID": "corr-close-1"})
    assert response.status_code == 200, response.get_data(as_text=True)
    detail = response.get_json()
    assert detail["id"] == str(rdata.aw) and detail["status_code"] == "closed"
    assert detail["closed_by_user_id"] == str(world.users["spd_a"].id)
    assert detail["ended_at"] == "2026-10-14T05:55:00Z"                    # set by the count report, preserved
    state = _session(db_conn, rdata.aw)
    assert state["status"] == "closed" and state["closed_by_user_id"] == world.users["spd_a"].id
    assert state["ended_at"] == ENDED_AT
    [event] = _session_close_events(db_conn, rdata.aw)
    assert event["user_id"] == world.users["spd_a"].id and event["family_id"] is None
    assert event["payload"] == {"closed_by": "spd_supervisor", "material_recovered": True, "safe_close_confirmed": True, "contingency_reviewed": False, "released_reservations": 6, "sterilized_instruments": 3, "unreconciled_physical_shortages": []}
    assert _audits(db_conn, rdata.aw) == [("session.spd_close", "corr-close-1", world.users["spd_a"].id)]
    assert _operation_status(db_conn, rdata.aw_op) == "scheduled"           # never synchronized here

    after = ds03_snapshot(db_conn)
    p, r = rdata.pieces, rdata.reservations
    # Compatible pieces → sterilization, one cycle event each, linked to this session/operation.
    for key in ("a_available", "b_in_use", "c_reserved"):
        assert after["instrument"][p[key]][:2] == ("sterilization", True), key
        assert _cycle_events(db_conn, p[key]) == [("sterilization", rdata.aw, rdata.aw_op, "SPD close: reprocessing started")]
    # Lost / maintenance / inactive pieces: reservation released, piece and history untouched.
    for key in ("a_lost", "b_maintenance", "c_inactive"):
        assert after["instrument"][p[key]] == before["instrument"][p[key]], key
        assert _cycle_events(db_conn, p[key]) == []
    for key in ("a_available", "b_in_use", "c_reserved", "a_lost", "b_maintenance", "c_inactive"):
        active, released_at = after["reservation"][r[key]]
        assert active is False and released_at == event["occurred_at"], key
    # Out of scope: already released, other operation, other tenant (even if reserved for this operation).
    for key in ("a_old_res", "a_other_op", "a_B_reserved", "a_B_other_op"):
        assert after["reservation"][r[key]] == before["reservation"][r[key]], key
    for key in ("a_old_res", "a_other_op", "a_B_reserved", "a_B_free", "a_B_other_op"):
        assert after["instrument"][p[key]] == before["instrument"][p[key]], key
        assert _cycle_events(db_conn, p[key]) == []
    assert after["cycle_events"] == before["cycle_events"] + 3


def test_tenant_b_stock_of_the_same_family_is_never_touched(client, world, rdata, as_user, db_conn):
    b_pieces = [rdata.pieces[k] for k in ("a_B_reserved", "a_B_free", "a_B_other_op")]
    before = {pid: ds03_snapshot(db_conn)["instrument"][pid] for pid in b_pieces}
    b_res = [rdata.reservations[k] for k in ("a_B_reserved", "a_B_other_op")]
    before_res = {rid: ds03_snapshot(db_conn)["reservation"][rid] for rid in b_res}
    assert _post(client, as_user, rdata.aw).status_code == 200
    snapshot = ds03_snapshot(db_conn)
    assert {pid: snapshot["instrument"][pid] for pid in b_pieces} == before
    assert {rid: snapshot["reservation"][rid] for rid in b_res} == before_res
    assert all(_cycle_events(db_conn, pid) == [] for pid in b_pieces)


def test_close_without_reservations(client, world, rdata, as_user, db_conn):
    before = ds03_snapshot(db_conn)
    response = _post(client, as_user, rdata.aw_legacy)
    assert response.status_code == 200
    assert ds03_snapshot(db_conn) == before
    assert _session_close_events(db_conn, rdata.aw_legacy)[0]["payload"] == {
        "closed_by": "spd_supervisor", "material_recovered": True, "safe_close_confirmed": True,
        "contingency_reviewed": False, "released_reservations": 0, "sterilized_instruments": 0,
        "unreconciled_physical_shortages": []}


def test_legacy_session_without_ended_at_gets_it_at_close(client, world, rdata, as_user, db_conn):
    response = _post(client, as_user, rdata.aw_legacy)
    state = _session(db_conn, rdata.aw_legacy)
    assert response.status_code == 200 and state["ended_at"] is not None
    assert state["ended_at"] == _session_close_events(db_conn, rdata.aw_legacy)[0]["occurred_at"]


def test_second_close_is_409(client, world, rdata, as_user, db_conn):
    assert _post(client, as_user, rdata.aw).status_code == 200
    after_first = ds03_snapshot(db_conn)
    again = _post(client, as_user, rdata.aw)
    assert again.status_code == 409 and again.get_json()["details"] == {"status_code": "closed", "reason": "not_awaiting_spd_review"}
    assert ds03_snapshot(db_conn) == after_first
    assert len(_session_close_events(db_conn, rdata.aw)) == 1 and len(_audits(db_conn, rdata.aw)) == 1


def test_multi_role_supervisor_can_close(client, world, rdata, as_user):
    assert _post(client, as_user, rdata.aw, user="multi_a").status_code == 200


# --- Preconditions ------------------------------------------------------------------------------------


@pytest.mark.parametrize("body", [{"material_recovered": False}, {"material_recovered": None}, {},
                                  {"material_recovered": "true"}, {"material_recovered": 1},
                                  {"material_recovered": True, "closed_by_user_id": "x"}])
def test_material_recovered_must_be_true(client, world, rdata, as_user, db_conn, body):
    before = ds03_snapshot(db_conn)
    response = _post(client, as_user, rdata.aw, body)
    assert response.status_code == 400 and response.get_json()["error"] == "invalid_request"
    _assert_untouched(db_conn, rdata, before)


@pytest.mark.parametrize("status", ["scheduled", "in_progress", "correction_required", "closed", "aborted"])
def test_wrong_status_is_409(client, world, rdata, as_user, db_conn, status):
    set_status(db_conn, rdata.aw, status)
    before = ds03_snapshot(db_conn)
    response = _post(client, as_user, rdata.aw)
    assert response.status_code == 409 and response.get_json()["details"] == {"status_code": status, "reason": "not_awaiting_spd_review"}
    assert ds03_snapshot(db_conn) == before and _session_close_events(db_conn, rdata.aw) == []


def test_open_discrepancy_blocks_close(client, world, rdata, as_user, db_conn):
    """awaiting_spd_review with an open discrepancy (inconsistent legacy data) must still not close."""
    add_discrepancy(db_conn, rdata.aw, world.families["a"])
    before = ds03_snapshot(db_conn)
    response = _post(client, as_user, rdata.aw)
    assert response.status_code == 409 and response.get_json()["details"] == {"open_discrepancies": 1, "reason": "open_discrepancies"}
    _assert_untouched(db_conn, rdata, before)


@pytest.mark.parametrize("user_key, status", [("op_a", 403), ("admin_a", 403), ("spd_b", 404)])
def test_access_rules(client, world, rdata, as_user, db_conn, user_key, status):
    before = ds03_snapshot(db_conn)
    assert _post(client, as_user, rdata.aw, user=user_key).status_code == status
    _assert_untouched(db_conn, rdata, before)


@pytest.mark.parametrize("bad_id", ["not-a-uuid", str(uuid.uuid4())])
def test_unknown_session_is_404(client, world, rdata, as_user, bad_id):
    assert _post(client, as_user, bad_id).status_code == 404


def test_cross_tenant_session_is_404(client, world, rdata, as_user, db_conn):
    set_status(db_conn, world.sessions["b1"].id, "awaiting_spd_review")
    db_conn.execute(text("UPDATE discrepancy SET resolved = TRUE, resolved_at = now() WHERE session_id = :s"),
                    {"s": world.sessions["b1"].id})
    assert _post(client, as_user, world.sessions["b1"].id).status_code == 404
    assert _post(client, as_user, world.sessions["b1"].id, user="spd_b").status_code == 409   # physical shortage
    assert _post(client, as_user, world.sessions["b1"].id, user="spd_b",
                 body={"material_recovered": True, "unreconciled_shortage_acknowledged": True}).status_code == 200


# --- XML ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["true", "1"])
def test_xml_request_and_response(client, world, rdata, as_user, value):
    response = client.post(_url(rdata.aw), data=f"<close_request><material_recovered>{value}</material_recovered></close_request>",
                           headers=as_user("spd_a", **{"Content-Type": "application/xml", "Accept": "application/xml"}))
    assert response.status_code == 200, response.get_data(as_text=True)
    root = ET.fromstring(response.data)
    assert root.tag == "session" and root.findtext("status_code") == "closed"
    assert root.findtext("closed_by_user_id") == str(world.users["spd_a"].id)


@pytest.mark.parametrize("value", ["false", "0", "yes", ""])
def test_xml_material_not_recovered_is_400(client, world, rdata, as_user, db_conn, value):
    response = client.post(_url(rdata.aw), data=f"<close_request><material_recovered>{value}</material_recovered></close_request>",
                           headers=as_user("spd_a", **{"Content-Type": "application/xml", "Accept": "application/xml"}))
    root = ET.fromstring(response.data)
    assert response.status_code == 400 and root.tag == "error_response"
    assert _session(db_conn, rdata.aw)["status"] == "awaiting_spd_review"


# --- GETs after close ---------------------------------------------------------------------------------


def test_get_detail_and_counts_after_close(client, world, rdata, as_user):
    assert _post(client, as_user, rdata.aw).status_code == 200
    detail = client.get(f"{BASE}/{rdata.aw}", headers=as_user("spd_a")).get_json()
    assert detail["status_code"] == "closed" and detail["open_discrepancies"] == 0
    counts = client.get(f"{BASE}/{rdata.aw}/counts", headers=as_user("spd_a")).get_json()
    assert [c["event_type_code"] for c in counts] == ["session_close"]
    assert counts[0]["user_id"] == str(world.users["spd_a"].id)


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_manual_close_precedes_session_close_on_equal_timestamp(client, world, rdata, as_user, db_conn, accept):
    """Ids chosen so that id order alone would put session_close first."""
    first, second = uuid.UUID("00000000-0000-4000-8000-000000000001"), uuid.UUID("ffffffff-0000-4000-8000-000000000001")
    for event_id, code in ((first, "session_close"), (second, "manual_close")):
        db_conn.execute(text("INSERT INTO count_event (id, event_type_id, occurred_at, session_id, user_id) VALUES "
                             "(:id, (SELECT id FROM cat_event_type WHERE code = :c), :o, :s, :u)"),
                        {"id": event_id, "c": code, "o": T0, "s": rdata.aw, "u": world.users["spd_a"].id})
    response = client.get(f"{BASE}/{rdata.aw}/counts", headers=as_user("spd_a", Accept=accept))
    if accept == "application/json":
        got = [c["event_type_code"] for c in response.get_json()]
    else:
        got = [c.findtext("event_type_code") for c in ET.fromstring(response.data)]
    assert got == ["manual_close", "session_close"]


# --- Atomicity ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["release_reservation", "update_instrument_cycle", "insert_cycle_event",
                                    "session_close", "update_session", "audit"])
def test_failure_rolls_back_everything(client, world, rdata, as_user, db_conn, monkeypatch, target):
    from adapters import audit_adapter
    from repositories import write_repository

    before = ds03_snapshot(db_conn)
    if target == "audit":
        monkeypatch.setattr(audit_adapter, "ACTOR_TYPE", "robot")
    else:
        name = "insert_count_event" if target == "session_close" else target
        original = getattr(write_repository, name)
        calls = {"n": 0}

        def failing(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2 or name in ("insert_count_event", "update_session"):
                raise RuntimeError(f"injected failure in {name}")   # mid-way through the side effects
            return original(*args, **kwargs)
        monkeypatch.setattr(write_repository, name, failing)
    response = _post(client, as_user, rdata.aw)
    assert response.status_code == 500 and "injected" not in response.get_data(as_text=True)
    _assert_untouched(db_conn, rdata, before)
    assert _session(db_conn, rdata.aw)["status"] == "awaiting_spd_review"
    assert _session(db_conn, rdata.aw)["ended_at"] == ENDED_AT


@pytest.mark.parametrize("corr, preserved", [("w" * 64, True), ("w" * 65, False)])
def test_audit_correlation_id_equals_response_header(client, world, rdata, as_user, db_conn, corr, preserved):
    response = _post(client, as_user, rdata.aw, **{"X-Correlation-ID": corr})
    answered = response.headers["X-Correlation-ID"]
    assert response.status_code == 200 and (answered == corr) is preserved
    assert [a[1] for a in _audits(db_conn, rdata.aw)] == [answered]
