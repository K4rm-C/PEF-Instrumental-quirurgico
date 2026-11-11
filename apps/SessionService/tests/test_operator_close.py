"""POST /api/v1/sessions/{id}/operator-close — the Operator closes a reconciled vision session.

`oc` builds, inside the rolled-back transaction, a vision session of op_a in awaiting_spd_review whose
facts are exactly what the portal writes in the single-video flow: initial lot (first frame) and final
lot (last frame) of the same video, one manual_count per family and the vision_validation manual_close.
Each blocker test breaks one condition. The concurrency test uses committed data (deleted afterwards).
"""

from __future__ import annotations

import json
import hashlib
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from conftest import NOW
from test_concurrency import HOLD_SECONDS, _hold_after_lock, _run_parallel
from write_fixtures import committed_env  # noqa: F401  (fixture)

BASE = "/api/v1/sessions"
BODY = {"physical_integrity_confirmed": True, "material_recovered": True}
SHA = "a" * 64
EVIDENCE = {"frame_jpeg_b64": "AAAA", "frame_sha256": hashlib.sha256(b"AAAA").hexdigest()}


def _run(conn, sql, **params):
    conn.execute(text(sql), params)


def _id(conn, table, code):
    return conn.execute(text(f"SELECT id FROM {table} WHERE code = :c"), {"c": code}).scalar_one()


def _event(conn, session_id, type_code, minute, *, user_id=None, family_id=None, expected=None, detected=None,
           payload=None):
    _run(conn, "INSERT INTO count_event (id, event_type_id, family_id, expected_quantity, detected_quantity, payload, "
               "occurred_at, session_id, user_id) VALUES (:id, (SELECT id FROM cat_event_type WHERE code = :t), :f, "
               ":e, :d, CAST(:p AS jsonb), :o, :s, :u)",
         id=uuid.uuid4(), t=type_code, f=family_id, e=expected, d=detected, p=json.dumps(payload or {}),
         o=NOW + timedelta(minutes=minute), s=session_id, u=user_id)


def build_reconciled_session(conn, *, institution_id, operator_id, station_id, kit_id, families, status="awaiting_spd_review",
                             initial=None, final=None, human=None, report=None, final_minute=10):
    """families: [(family_id, expected)]. Returns (session_id, operation_id)."""
    operation_id, session_id = uuid.uuid4(), uuid.uuid4()
    _run(conn, "INSERT INTO operation (id, scheduled_at, status_id, institution_id) VALUES (:id, :s, "
               "(SELECT id FROM cat_operation_status WHERE code = 'scheduled'), :i)", id=operation_id, s=NOW,
         i=institution_id)
    _run(conn, "INSERT INTO work_session (id, started_at, ended_at, status_id, user_id, operation_id, station_id, kit_id, "
               "current_phase_id, capture_mode) VALUES (:id, :st, :en, (SELECT id FROM cat_session_status WHERE code = :sc), "
               ":u, :o, :stn, :k, (SELECT id FROM cat_operation_phase WHERE code = 'start'), 'vision')",
         id=session_id, st=NOW, en=NOW + timedelta(minutes=20), sc=status, u=operator_id, o=operation_id,
         stn=station_id, k=kit_id)
    total = sum(qty for _, qty in families)
    board = {"families": [{"family_id": str(f), "detected_quantity": q} for f, q in families]}
    for family_id, qty in families:
        _run(conn, "INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source) "
                   "VALUES (:s, :f, :q, 'kit_snapshot')", s=session_id, f=family_id, q=qty)
    initial = {"trigger": "initial", "scope": "board", "source": "first_frame", "frame_index": 0,
               "video": {"video_id": "v1", "sha256": SHA}, "board": board, **EVIDENCE, **(initial or {})}
    final = {"trigger": "final", "scope": "board", "source": "last_frame", "frame_index": 297, "run_id": "r1",
             "video": {"video_id": "v1", "sha256": SHA}, "board": board, "matches_reference": True, **EVIDENCE, **(final or {})}
    _event(conn, session_id, "auto_count", 1, user_id=operator_id, expected=total,
           detected=initial.pop("_detected", total), payload=initial)
    _event(conn, session_id, "auto_count", final_minute, user_id=operator_id, expected=total,
           detected=final.pop("_detected", total), payload=final)
    for family_id, qty in families:
        _event(conn, session_id, "manual_count", 15, user_id=operator_id, family_id=family_id, expected=qty,
               detected=(human or {}).get(family_id, qty), payload={"close_path": "manual_count"})
    _event(conn, session_id, "manual_close", 16, user_id=operator_id,
           payload={"operator_close_path": "vision_validation", "open_discrepancies": 0, "contingency": None,
                    "extra_instruments": None, "known_final_mismatch": None, **(report or {})})
    return session_id, operation_id


@pytest.fixture
def oc(world, db_conn):
    c, w = db_conn, world
    d = SimpleNamespace(c=c, w=w)

    def make(**overrides):
        return build_reconciled_session(
            c, institution_id=w.institutions["A"], operator_id=w.users["op_a"].id, station_id=w.stations["A"],
            kit_id=w.kits["A"], families=[(w.families["a"], 2), (w.families["b"], 1)], **overrides)

    d.make = make
    d.session_id, d.operation_id = make()
    d.piece = uuid.uuid4()
    _run(c, "INSERT INTO instrument (id, family_id, cycle_status_id, institution_id, active) VALUES "
            "(:id, :f, (SELECT id FROM cat_instrument_cycle_status WHERE code = 'reserved'), :i, TRUE)",
         id=d.piece, f=w.families["a"], i=w.institutions["A"])
    _run(c, "INSERT INTO instrument_reservation (id, operation_id, instrument_id, reserved_at, active) "
            "VALUES (:id, :o, :p, :r, TRUE)", id=uuid.uuid4(), o=d.operation_id, p=d.piece, r=NOW)
    return d


def _close(client, as_user, session_id, body=BODY, user="op_a", **headers):
    return client.post(f"{BASE}/{session_id}/operator-close", json=body, headers=as_user(user, **headers))


def _state(conn, session_id):
    return conn.execute(text(
        "SELECT s.code AS status, w.closed_by_user_id FROM work_session w JOIN cat_session_status s "
        "ON s.id = w.status_id WHERE w.id = :s"), {"s": session_id}).mappings().one()


def _closes(conn, session_id):
    return conn.execute(text(
        "SELECT e.user_id, e.payload FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
        "WHERE e.session_id = :s AND t.code = 'session_close'"), {"s": session_id}).mappings().all()


def test_reconciled_session_is_closed_safely_by_its_operator(client, as_user, oc, world):
    response = _close(client, as_user, oc.session_id)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["status_code"] == "closed"
    state = _state(oc.c, oc.session_id)
    assert (state["status"], state["closed_by_user_id"]) == ("closed", world.users["op_a"].id)
    (close,) = _closes(oc.c, oc.session_id)
    payload = close["payload"]
    assert close["user_id"] == world.users["op_a"].id
    assert (payload["closed_by"], payload["safe_close_confirmed"], payload["physical_integrity_confirmed"]) == (
        "station_operator", True, True)
    assert payload["unreconciled_physical_shortages"] == []
    assert payload["reconciliation"]["final_total"] == payload["reconciliation"]["validated_total"] == 3
    assert (payload["released_reservations"], payload["sterilized_instruments"]) == (1, 1)
    piece = oc.c.execute(text("SELECT s.code FROM instrument i JOIN cat_instrument_cycle_status s "
                              "ON s.id = i.cycle_status_id WHERE i.id = :p"), {"p": oc.piece}).scalar_one()
    assert piece == "sterilization"
    notes = oc.c.execute(text("SELECT notes FROM instrument_cycle_event WHERE instrument_id = :p"),
                         {"p": oc.piece}).scalar_one()
    assert notes == "Operator close: reprocessing started"
    actions = oc.c.execute(text("SELECT action FROM access_audit WHERE resource_id = :s AND outcome = 'success'"),
                           {"s": oc.session_id}).scalars().all()
    assert actions == ["session.operator_close"]


def test_supervisor_reads_the_operator_closed_session(client, as_user, oc, world):
    _close(client, as_user, oc.session_id)
    response = client.get(f"{BASE}/{oc.session_id}", headers=as_user("spd_a"))
    assert response.status_code == 200
    assert response.get_json()["status_code"] == "closed"
    assert response.get_json()["closed_by_user_id"] == str(world.users["op_a"].id)


@pytest.mark.parametrize("change, blocker", [
    ({"report": {"contingency": {"reason_code": "worker_unavailable"}}}, "contingency"),
    ({"report": {"operator_close_path": "manual_report"}}, "not_vision_validation"),
    ({"report": {"extra_instruments": 1}}, "extra_instruments"),
    ({"report": {"known_final_mismatch": {"final": 2, "reference": 3}}}, "final_count_mismatch"),
    ({"final": {"_detected": 2}}, "initial_final_mismatch"),
    ({"final": {"video": {"video_id": "v2", "sha256": "b" * 64}}}, "different_video"),
    ({"final": {"out_of_kit": [{"family_code": "X", "count": 1}]}}, "out_of_kit_detected"),
    ({"final": {"source": None}}, "missing_final"),
    ({"initial": {"source": None}}, "missing_initial"),
    ({"status": "correction_required"}, "not_awaiting_review"),
])
def test_conditions_that_keep_the_session_with_the_supervisor(client, as_user, oc, change, blocker):
    session_id, _ = oc.make(**change)
    response = _close(client, as_user, session_id)
    assert response.status_code == 409
    details = response.get_json()["details"]
    assert details["reason"] == "operator_close_not_allowed" and blocker in details["blockers"]
    assert _state(oc.c, session_id)["status"] != "closed" and _closes(oc.c, session_id) == []


def test_same_total_but_different_families(client, as_user, oc, world):
    session_id, _ = oc.make(human={world.families["a"]: 3, world.families["b"]: 0})
    response = _close(client, as_user, session_id)
    assert response.status_code == 409 and "family_mismatch" in response.get_json()["details"]["blockers"]


def test_initial_family_swap_cannot_be_hidden_by_equal_totals(client, as_user, oc, world):
    board = {"families": [{"family_id": str(world.families["a"]), "detected_quantity": 1},
                          {"family_id": str(world.families["b"]), "detected_quantity": 2}]}
    session_id, _ = oc.make(initial={"board": board})
    response = _close(client, as_user, session_id)
    assert response.status_code == 409
    assert "initial_family_mismatch" in response.get_json()["details"]["blockers"]
    assert _closes(oc.c, session_id) == []


@pytest.mark.parametrize("changes", [{"frame_sha256": "tampered"}, {"frame_jpeg_b64": None}])
def test_incomplete_or_changed_evidence_blocks_safe_close(client, as_user, oc, changes):
    session_id, _ = oc.make(final=changes)
    response = _close(client, as_user, session_id)
    assert response.status_code == 409
    assert "evidence_integrity" in response.get_json()["details"]["blockers"]
    assert _closes(oc.c, session_id) == []


def test_any_discrepancy_even_resolved_blocks(client, as_user, oc, world):
    _run(oc.c, "INSERT INTO discrepancy (id, description, resolved, resolved_at, reason_id, family_id, session_id) "
               "VALUES (:id, 'x', TRUE, :r, (SELECT id FROM cat_discrepancy_reason WHERE code = 'surplus'), :f, :s)",
         id=uuid.uuid4(), r=NOW, f=world.families["a"], s=oc.session_id)
    response = _close(client, as_user, oc.session_id)
    assert response.status_code == 409 and "discrepancies_recorded" in response.get_json()["details"]["blockers"]


def test_addition_after_the_final_count_blocks(client, as_user, oc, world):
    _event(oc.c, oc.session_id, "instruments_added", 12, detected=1, payload={"added": []})
    response = _close(client, as_user, oc.session_id)
    assert response.status_code == 409 and "missing_final" in response.get_json()["details"]["blockers"]


@pytest.mark.parametrize("user, status", [("op_a2", 404), ("op_b", 404), ("spd_a", 403)])
def test_only_the_assigned_operator_can_close(client, as_user, oc, user, status):
    response = _close(client, as_user, oc.session_id, user=user)
    assert response.status_code == status
    assert _state(oc.c, oc.session_id)["status"] == "awaiting_spd_review"


@pytest.mark.parametrize("body, reason", [
    ({"material_recovered": True}, "physical_integrity_not_confirmed"),
    ({"physical_integrity_confirmed": False, "material_recovered": True}, "physical_integrity_not_confirmed"),
    ({"physical_integrity_confirmed": True}, "material_not_recovered"),
])
def test_physical_confirmation_is_mandatory(client, as_user, oc, body, reason):
    response = _close(client, as_user, oc.session_id, body=body)
    assert response.status_code == 400 and response.get_json()["details"]["reason"] == reason
    assert _closes(oc.c, oc.session_id) == []


def test_unknown_fields_and_xml(client, as_user, oc):
    response = _close(client, as_user, oc.session_id, body={**BODY, "safe_close_confirmed": True})
    assert response.status_code == 400
    xml = ("<operator_close_request><physical_integrity_confirmed>true</physical_integrity_confirmed>"
           "<material_recovered>true</material_recovered></operator_close_request>")
    response = client.post(f"{BASE}/{oc.session_id}/operator-close", data=xml,
                           headers=as_user("op_a", **{"Content-Type": "application/xml"}))
    assert response.status_code == 200


def test_repeated_request_is_idempotent(client, as_user, oc):
    assert _close(client, as_user, oc.session_id).status_code == 200
    again = _close(client, as_user, oc.session_id)
    assert again.status_code == 200 and again.get_json()["status_code"] == "closed"
    assert len(_closes(oc.c, oc.session_id)) == 1


def test_session_closed_by_the_supervisor_is_not_reclosed(client, as_user, oc):
    assert client.post(f"{BASE}/{oc.session_id}/close", json={"material_recovered": True},
                       headers=as_user("spd_a")).status_code == 200
    response = _close(client, as_user, oc.session_id)
    assert response.status_code == 409 and response.get_json()["details"]["reason"] == "already_closed"


def test_reports_count_the_operator_close(client, as_user, oc):
    url = f"{BASE}/reports/summary?from=2026-09-30&to=2026-10-03"

    def closed():
        response = client.get(url, headers=as_user("spd_a"))
        assert response.status_code == 200, response.get_json()
        return response.get_json()["sessions"]["closed"]
    before = closed()
    _close(client, as_user, oc.session_id)
    assert closed() == before + 1


# --- Real concurrency (committed data, independent connections) --------------------------------------------


@pytest.fixture
def close_env(committed_env, pg_engine):
    env = committed_env
    with pg_engine.begin() as c:
        env.operation_id = c.execute(text("SELECT operation_id FROM work_session WHERE id = :s"),
                                     {"s": env.session_id}).scalar_one()
        _run(c, "UPDATE work_session SET capture_mode = 'vision', started_at = :t, status_id = (SELECT id FROM "
                "cat_session_status WHERE code = 'awaiting_spd_review') WHERE id = :s", s=env.session_id, t=NOW)
        total = 1
        board = {"families": [{"family_id": str(env.families[0]), "detected_quantity": 1}]}
        for minute, payload in ((1, {"trigger": "initial", "source": "first_frame", "board": board,
                                     "video": {"sha256": SHA}, **EVIDENCE}),
                                (10, {"trigger": "final", "source": "last_frame", "board": board,
                                      "video": {"sha256": SHA}, **EVIDENCE})):
            _event(c, env.session_id, "auto_count", minute, user_id=env.operator, expected=total, detected=total,
                   payload=payload)
        _event(c, env.session_id, "manual_count", 15, user_id=env.operator, family_id=env.families[0], expected=1,
               detected=1)
        _event(c, env.session_id, "manual_close", 16, user_id=env.operator,
               payload={"operator_close_path": "vision_validation"})
        env.piece = c.execute(text("SELECT id FROM instrument WHERE family_id = :f LIMIT 1"),
                              {"f": env.families[0]}).scalar_one()
        _run(c, "INSERT INTO instrument_reservation (id, operation_id, instrument_id, reserved_at, active) "
                "VALUES (:id, :o, :p, :r, TRUE)", id=uuid.uuid4(), o=env.operation_id, p=env.piece, r=NOW)
    try:
        yield env
    finally:
        with pg_engine.begin() as c:
            _run(c, "DELETE FROM instrument_cycle_event WHERE session_id = :s", s=env.session_id)
            _run(c, "DELETE FROM instrument_reservation WHERE operation_id = :o", o=env.operation_id)


def test_double_operator_close_one_close_side_effects_once(close_env, pg_engine, monkeypatch):
    env = close_env
    _hold_after_lock(monkeypatch, "scoped_active_reservations")
    url = f"{BASE}/{env.session_id}/operator-close"
    call = lambda c: c.post(url, json=BODY, headers={"Authorization": f"Bearer {env.op_token}"})  # noqa: E731
    responses = _run_parallel(env.app, [call, call])
    # The second request waits for the row lock, then sees the session it closed itself: same answer.
    assert [r.status_code for r in responses] == [200, 200]
    assert max(r.elapsed for r in responses) >= HOLD_SECONDS * 0.8
    with pg_engine.connect() as c:
        scalar = lambda sql: c.execute(text(sql), {"s": env.session_id, "p": env.piece}).scalar_one()  # noqa: E731
        assert scalar("SELECT count(*) FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
                      "WHERE e.session_id = :s AND t.code = 'session_close'") == 1
        assert scalar("SELECT count(*) FROM access_audit WHERE resource_id = :s AND action = 'session.operator_close'") == 1
        assert scalar("SELECT count(*) FROM instrument_cycle_event WHERE instrument_id = :p") == 1
        assert scalar("SELECT count(*) FROM instrument_reservation WHERE instrument_id = :p AND active") == 0
