"""MS-09: Supervisor close through SessionService — same technical checks as the portal (real PostgreSQL).

- contingency VERIFIED by the server: closed after the review confirmation only; close_basis contingency_count;
  reported apart as approved_contingency (never as a normal approval);
- contingency DECLARED (or older data without verification): needs unverified_contingency_acknowledged;
- escalated rejected final count: needs escalation_acknowledged and is never a safe close; detail exposes it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text

from tests.test_supervisor_flow import BASE, reason


def _awaiting(db_conn, world, session_id, operator_payload):
    db_conn.execute(text("DELETE FROM discrepancy WHERE session_id = :s"), {"s": session_id})
    db_conn.execute(text("UPDATE work_session SET status_id = (SELECT id FROM cat_session_status "
                         "WHERE code = 'awaiting_spd_review') WHERE id = :s"), {"s": session_id})
    db_conn.execute(text("INSERT INTO count_event (event_type_id, payload, occurred_at, session_id, user_id) "
                         "VALUES (:t, CAST(:p AS jsonb), :o, :s, :u)"),
                    {"t": world.event_type["manual_close"], "s": session_id, "u": world.users["op_a"].id,
                     "o": datetime.now(timezone.utc), "p": json.dumps(operator_payload)})


def _close_payload(db_conn, session_id):
    return db_conn.execute(text("SELECT ce.payload FROM count_event ce JOIN cat_event_type t ON t.id = ce.event_type_id "
                                "WHERE t.code = 'session_close' AND ce.session_id = :s"), {"s": session_id}).scalar_one()


def test_verified_contingency_closes_with_the_review_and_is_reported_apart(client, world, as_user, db_conn):
    s = world.sessions["a3"].id
    _awaiting(db_conn, world, s, {"contingency": {"reason_code": "worker_unavailable",
                                                  "verification": {"status": "verified", "basis": "worker_unreachable"}},
                                  "verified_total": 6})
    ok = client.post(f"{BASE}/{s}/close", json={"material_recovered": True, "contingency_reviewed": True},
                     headers=as_user("spd_a"))
    assert ok.status_code == 200, ok.get_json()
    payload = _close_payload(db_conn, s)
    assert (payload["safe_close_confirmed"], payload["close_basis"], payload["contingency_verification"]) == (
        True, "contingency_count", "verified")
    summary = client.get(f"{BASE}/reports/summary", query_string={"from": "2026-01-01", "to": "2026-12-31", "tz": "UTC"},
                         headers=as_user("spd_a")).get_json()
    assert summary["outcomes"]["approved_contingency"] >= 1


def test_declared_contingency_needs_the_acknowledgment(client, world, as_user, db_conn):
    s = world.sessions["a3"].id
    _awaiting(db_conn, world, s, {"contingency": {"reason_code": "other", "notes": "Light failure",
                                                  "verification": {"status": "declared"}}})
    refused = client.post(f"{BASE}/{s}/close", json={"material_recovered": True, "contingency_reviewed": True},
                          headers=as_user("spd_a"))
    assert refused.status_code == 409 and reason(refused) == "unverified_contingency"
    ok = client.post(f"{BASE}/{s}/close", json={"material_recovered": True, "contingency_reviewed": True,
                                                "unverified_contingency_acknowledged": True}, headers=as_user("spd_a"))
    assert ok.status_code == 200 and _close_payload(db_conn, s)["contingency_verification"] == "declared"


def test_escalation_is_never_a_safe_close(client, world, as_user, db_conn):
    s = world.sessions["a3"].id
    escalation = {"kind": "possible_surplus", "final": 16, "reference": 15, "notes": "Extra piece on the tray"}
    _awaiting(db_conn, world, s, {"close_path": "final_count_escalation", "escalation": escalation, "contingency": None})
    detail = client.get(f"{BASE}/{s}?expand=detail", headers=as_user("spd_a")).get_json()
    assert detail["escalation"]["kind"] == "possible_surplus" and detail["contingency"] is None
    refused = client.post(f"{BASE}/{s}/close", json={"material_recovered": True}, headers=as_user("spd_a"))
    assert refused.status_code == 409 and reason(refused) == "final_count_escalation"
    ok = client.post(f"{BASE}/{s}/close", json={"material_recovered": True, "escalation_acknowledged": True},
                     headers=as_user("spd_a"))
    assert ok.status_code == 200
    payload = _close_payload(db_conn, s)
    assert payload["safe_close_confirmed"] is False and payload["close_basis"] == "final_count_escalation"


def test_acknowledgments_must_be_booleans(client, world, as_user):
    s = world.sessions["a3"].id
    response = client.post(f"{BASE}/{s}/close", json={"material_recovered": True, "escalation_acknowledged": "yes"},
                           headers=as_user("spd_a"))
    assert response.status_code == 400
