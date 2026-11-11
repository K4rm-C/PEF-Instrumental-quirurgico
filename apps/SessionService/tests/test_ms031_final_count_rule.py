"""MS-03.1 — PEF final count rule in the SessionService domain endpoints.

Physical total of the last stable capture = current reference (initial YOLO count + instruments_added
confirmed by the SPD Supervisor after it). Applied when the session has a reference (initial count):
- POST /vision-counts trigger=final with another total → close_blocked kept, 409 final_count_mismatch,
  nothing stored as a final count, the session stays in progress (the Operator captures again);
- POST /count-report of a vision session → needs a valid final count (409 final_count_required) and its
  total must stay the final total (409 validated_total_mismatch): classification corrections only.
Sessions without an initial count keep the DS09 behaviour (covered by test_vision_counts / test_count_report).

cdata.vision: tenant A, op_a, in_progress, expected a=2, b=1, c=3 (6 instruments).
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from count_fixtures import T0, cdata, counting_env, report_line, vision_body  # noqa: F401  (fixtures)
from test_concurrency import _hold_after_lock, _run_parallel
from write_fixtures import committed_env  # noqa: F401  (fixture used by counting_env)

BASE = "/api/v1/sessions"


def _initial(conn, session_id, total, *, minutes_before=20):
    """Initial count as the portal writes it (auto_count, trigger initial, board total)."""
    conn.execute(text(
        "INSERT INTO count_event (id, event_type_id, payload, occurred_at, session_id, detected_quantity) "
        "VALUES (:id, (SELECT id FROM cat_event_type WHERE code = 'auto_count'), CAST(:p AS jsonb), :o, :s, :q)"),
        {"id": uuid.uuid4(), "p": json.dumps({"trigger": "initial", "source": "first_frame"}),
         "o": T0 - timedelta(minutes=minutes_before), "s": session_id, "q": total})


def _added(conn, session_id, quantity, family_id, *, minutes_before=10):
    conn.execute(text(
        "INSERT INTO count_event (id, event_type_id, payload, occurred_at, session_id, family_id, detected_quantity) "
        "VALUES (:id, (SELECT id FROM cat_event_type WHERE code = 'instruments_added'), '{}'::jsonb, :o, :s, :f, :q)"),
        {"id": uuid.uuid4(), "o": T0 - timedelta(minutes=minutes_before), "s": session_id, "f": family_id,
         "q": quantity})


def _events(conn, session_id, code):
    return conn.execute(text(
        "SELECT e.detected_quantity, e.expected_quantity, e.payload, e.client_event_id FROM count_event e "
        "JOIN cat_event_type t ON t.id = e.event_type_id WHERE e.session_id = :s AND t.code = :c "
        "ORDER BY e.occurred_at, e.id"), {"s": session_id, "c": code}).mappings().all()


def _finals(conn, session_id):
    return [e for e in _events(conn, session_id, "auto_count") if e["payload"].get("trigger") == "final"]


def _status(conn, session_id):
    return conn.execute(text("SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id "
                             "WHERE w.id = :s"), {"s": session_id}).scalar_one()


def _vision(client, as_user, session_id, body):
    return client.post(f"{BASE}/{session_id}/vision-counts", json=body, headers=as_user("op_a"))


def _report(client, as_user, session_id, lines):
    return client.post(f"{BASE}/{session_id}/count-report", json={"reports": lines}, headers=as_user("op_a"))


@pytest.mark.parametrize("counts, allowed", [
    ((("a", 2), ("b", 1), ("c", 3)), True),     # 6 = 6
    ((("a", 2), ("b", 1), ("c", 2)), False),    # 5 ≠ 6
    ((("a", 2), ("b", 1), ("c", 4)), False),    # 7 ≠ 6
])
def test_final_count_must_equal_the_reference(client, world, cdata, db_conn, as_user, counts, allowed):
    _initial(db_conn, cdata.vision, 6)
    response = _vision(client, as_user, cdata.vision, vision_body(world, *counts))
    if allowed:
        assert response.status_code == 201
        assert [e["detected_quantity"] for e in _finals(db_conn, cdata.vision)] == [6]
        assert _events(db_conn, cdata.vision, "close_blocked") == []
    else:
        assert response.status_code == 409
        details = response.get_json()["details"]
        assert details["reason"] == "final_count_mismatch" and details["reference_total"] == 6
        assert _finals(db_conn, cdata.vision) == []                        # never a final count
        (blocked,) = _events(db_conn, cdata.vision, "close_blocked")       # the attempt is kept
        assert blocked["payload"]["reason"] == "final_count_mismatch" and blocked["expected_quantity"] == 6
    assert _status(db_conn, cdata.vision) == "in_progress"


@pytest.mark.parametrize("c_qty, allowed", [(5, True), (3, False)])
def test_confirmed_additions_raise_the_reference(client, world, cdata, db_conn, as_user, c_qty, allowed):
    """Initial 6 + 2 confirmed additions = 8: final 8 accepted, final 6 rejected."""
    _initial(db_conn, cdata.vision, 6)
    _added(db_conn, cdata.vision, 2, world.families["c"])
    response = _vision(client, as_user, cdata.vision, vision_body(world, ("a", 2), ("b", 1), ("c", c_qty)))
    assert (response.status_code == 201) is allowed
    if not allowed:
        details = response.get_json()["details"]
        assert (details["initial_total"], details["added_total"], details["reference_total"]) == (6, 2, 8)


def test_retry_of_a_rejected_final_count_is_one_incident(client, world, cdata, db_conn, as_user):
    _initial(db_conn, cdata.vision, 6)
    body = vision_body(world, ("a", 2), ("b", 1), ("c", 2))
    first, again = (_vision(client, as_user, cdata.vision, body) for _ in range(2))
    assert first.status_code == again.status_code == 409
    assert again.get_json()["details"]["final_total"] == 5
    assert len(_events(db_conn, cdata.vision, "close_blocked")) == 1
    other = dict(body, counts=vision_body(world, ("a", 2), ("b", 1), ("c", 3))["counts"])
    assert _vision(client, as_user, cdata.vision, other).status_code == 409   # same id, other content


def test_capture_again_after_a_rejection(client, world, cdata, db_conn, as_user):
    _initial(db_conn, cdata.vision, 6)
    assert _vision(client, as_user, cdata.vision, vision_body(world, ("a", 2), ("b", 1), ("c", 2))).status_code == 409
    retry = vision_body(world, ("a", 2), ("b", 1), ("c", 3), occurred_at="2026-10-14T05:05:00Z")
    assert _vision(client, as_user, cdata.vision, retry).status_code == 201
    stored = _finals(db_conn, cdata.vision)
    report = [report_line(world, "a", 2, suggested=2), report_line(world, "b", 1, suggested=1),
              report_line(world, "c", 3, suggested=3)]
    assert len(stored) == 1 and _report(client, as_user, cdata.vision, report).status_code == 200
    assert _status(db_conn, cdata.vision) == "awaiting_spd_review"


def test_report_needs_a_valid_final_count(client, world, cdata, db_conn, as_user):
    _initial(db_conn, cdata.vision, 6)
    lines = [report_line(world, "a", 2), report_line(world, "b", 1), report_line(world, "c", 3)]
    response = _report(client, as_user, cdata.vision, lines)
    assert response.status_code == 409 and response.get_json()["details"]["reason"] == "final_count_required"
    assert _events(db_conn, cdata.vision, "manual_close") == [] and _status(db_conn, cdata.vision) == "in_progress"


def test_final_count_older_than_an_addition_is_not_valid(client, world, cdata, db_conn, as_user):
    _initial(db_conn, cdata.vision, 6)
    body = vision_body(world, ("a", 2), ("b", 1), ("c", 3), occurred_at="2026-10-14T04:50:00Z")
    assert _vision(client, as_user, cdata.vision, body).status_code == 201
    _added(db_conn, cdata.vision, 0, world.families["c"], minutes_before=5)   # confirmed after the final count
    lines = [report_line(world, "a", 2, suggested=2), report_line(world, "b", 1, suggested=1),
             report_line(world, "c", 3, suggested=3)]
    response = _report(client, as_user, cdata.vision, lines)
    assert response.status_code == 409 and response.get_json()["details"]["reason"] == "final_count_required"


def test_report_cannot_change_the_physical_total(client, world, cdata, db_conn, as_user):
    _initial(db_conn, cdata.vision, 6)
    assert _vision(client, as_user, cdata.vision, vision_body(world, ("a", 3), ("b", 0), ("c", 3))).status_code == 201
    for b_qty in (0, 2):     # 5 or 7 instead of 6
        lines = [report_line(world, "a", 2, suggested=3, reason_code="shortage", notes="One forceps is a retractor"),
                 report_line(world, "b", b_qty, suggested=0, reason_code="shortage" if b_qty < 1 else "surplus",
                             notes="Recounted" if b_qty else None),
                 report_line(world, "c", 3, suggested=3)]
        if b_qty == 0:
            lines[1]["notes"] = None
        response = _report(client, as_user, cdata.vision, lines)
        assert response.status_code == 409
        assert response.get_json()["details"]["reason"] == "validated_total_mismatch"
    # Classification correction (3 A → 2 A + 1 B) keeps the total of 6: accepted.
    lines = [report_line(world, "a", 2, suggested=3, notes="One forceps is a retractor"),
             report_line(world, "b", 1, suggested=0, notes="The retractor YOLO labelled as forceps"),
             report_line(world, "c", 3, suggested=3)]
    response = _report(client, as_user, cdata.vision, lines)
    assert response.status_code == 200 and response.get_json()["human_corrections"] == 2


def test_historical_session_with_discrepancies_is_untouched(client, world, cdata, db_conn, as_user):
    """A session already closed with an earlier mismatch stays as it was: nothing is rewritten."""
    _initial(db_conn, cdata.vision, 6)
    db_conn.execute(text(
        "INSERT INTO count_event (id, event_type_id, payload, occurred_at, session_id, detected_quantity) "
        "VALUES (:id, (SELECT id FROM cat_event_type WHERE code = 'auto_count'), CAST(:p AS jsonb), :o, :s, 5)"),
        {"id": uuid.uuid4(), "p": json.dumps({"trigger": "final", "mismatch_acknowledged": True}),
         "o": T0, "s": cdata.vision})
    db_conn.execute(text("UPDATE work_session SET status_id = (SELECT id FROM cat_session_status "
                         "WHERE code = 'correction_required') WHERE id = :s"), {"s": cdata.vision})
    detail = client.get(f"{BASE}/{cdata.vision}?expand=detail", headers=as_user("spd_a"))
    assert detail.status_code == 200
    assert [e["detected_quantity"] for e in _finals(db_conn, cdata.vision)] == [5]
    assert _status(db_conn, cdata.vision) == "correction_required"
    late = _vision(client, as_user, cdata.vision, vision_body(world, ("a", 2), ("b", 1), ("c", 3)))
    assert late.status_code == 409   # not in progress: nothing is added to the historical session


def test_concurrent_rejected_final_counts_record_one_incident(counting_env, pg_engine, monkeypatch):
    """Two overlapping identical requests (same client_event_id) with a wrong total: both 409, one row."""
    env = counting_env
    with pg_engine.begin() as c:
        c.execute(text(
            "INSERT INTO count_event (id, event_type_id, payload, occurred_at, session_id, detected_quantity) "
            "VALUES (:id, (SELECT id FROM cat_event_type WHERE code = 'auto_count'), "
            "'{\"trigger\": \"initial\"}'::jsonb, now() - interval '1 hour', :s, 1)"),
            {"id": uuid.uuid4(), "s": env.session_id})
    _hold_after_lock(monkeypatch, "expected_rows")
    body = {"client_event_id": str(uuid.uuid4()), "occurred_at": "2026-10-14T05:00:00Z",
            "model_version": "yolo26l-demo", "trigger": "final",
            "counts": [{"family_id": str(env.families[0]), "detected_quantity": 2}]}
    url = f"{BASE}/{env.session_id}/vision-counts"
    call = lambda c: c.post(url, json=body, headers={"Authorization": f"Bearer {env.op_token}"})  # noqa: E731
    try:
        responses = _run_parallel(env.app, [call, call])
        assert [r.status_code for r in responses] == [409, 409]
        with pg_engine.connect() as c:
            rows = c.execute(text(
                "SELECT t.code, count(*) FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
                "WHERE e.session_id = :s AND t.code IN ('close_blocked', 'auto_count') GROUP BY t.code"),
                {"s": env.session_id}).all()
        assert dict(rows) == {"auto_count": 1, "close_blocked": 1}   # the initial count + one rejection
    finally:
        with pg_engine.begin() as c:
            c.execute(text("DELETE FROM count_event WHERE session_id = :s"), {"s": env.session_id})
