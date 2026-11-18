"""MS-12 (ms-validacion-humana): Supervisor verification of the integrity seal of the Operator's validation.
Real PostgreSQL (rolled back). The canonical form is shared with the portal: the same test vector is asserted in
apps/BackendWebFlask/tests/test_ms12_validation_seal.py."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree
from sqlalchemy import text

from services import validation_seal

BASE = "/api/v1/sessions"
XSD = etree.XMLSchema(etree.parse(str(Path(__file__).resolve().parents[1] / "schemas" / "session-reports.xsd")))
VECTOR = "0fa4703818ac7f6376f2054199f62cd6aa3e8b4be885b5949ca8aeb2fa05e61c"


def vector_seal():
    return validation_seal.seal(
        session_id="11111111-1111-1111-1111-111111111111", operator_user_id="22222222-2222-2222-2222-222222222222",
        closed_at=datetime(2026, 11, 10, 12, 0, 0, 123456, tzinfo=timezone.utc), close_path="vision_validation",
        lines=[{"family_id": "b", "expected_quantity": 2, "reported_quantity": 1, "reason_code": "shortage",
                "notes": " moved ", "ai_suggested_quantity": 2},
               {"family_id": "a", "expected_quantity": 3, "reported_quantity": 4, "reason_code": "", "notes": None,
                "ai_suggested_quantity": None}],
        final_count_event_id="33333333-3333-3333-3333-333333333333", final_frame_sha256="ab" * 32,
        extra_instruments=None)


def test_shared_test_vector():
    assert vector_seal()["sha256"] == VECTOR


def _event(db_conn, session_id, code, at, *, user=None, family=None, expected=None, detected=None, payload=None):
    event_id = uuid.uuid4()
    db_conn.execute(text("INSERT INTO count_event (id, event_type_id, family_id, expected_quantity, detected_quantity, "
                         "payload, occurred_at, session_id, user_id) VALUES (:id, (SELECT id FROM cat_event_type WHERE "
                         "code = :c), :f, :e, :d, CAST(:p AS jsonb), :o, :s, :u)"),
                    {"id": event_id, "c": code, "f": family, "e": expected, "d": detected, "p": json.dumps(payload or {}),
                     "o": at, "s": session_id, "u": user})
    return event_id


def _validated(db_conn, world, *, sealed=True):
    """The rows the portal writes when the Operator validates (a2: vision session of op_a2)."""
    session_id, user = world.sessions["a2"].id, world.users["op_a2"].id
    at = datetime(2026, 11, 10, 9, 30, 0, 654321, tzinfo=timezone.utc)
    final_id = _event(db_conn, session_id, "auto_count", datetime(2026, 11, 10, 9, 29, tzinfo=timezone.utc),
                      detected=3, payload={"trigger": "final", "source": "last_frame", "frame_sha256": "cd" * 32})
    lines = [{"family_id": world.families["a"], "expected_quantity": 2, "reported_quantity": 2, "reason_code": None,
              "notes": None, "ai_suggested_quantity": 1},
             {"family_id": world.families["b"], "expected_quantity": 1, "reported_quantity": 1, "reason_code": None,
              "notes": None, "ai_suggested_quantity": 2}]
    for line in lines:
        _event(db_conn, session_id, "manual_count", at, user=user, family=line["family_id"],
               expected=line["expected_quantity"], detected=line["reported_quantity"],
               payload={"reason_code": None, "notes": None, "ai_suggested_quantity": line["ai_suggested_quantity"]})
    payload = {"operator_finished": True, "operator_close_path": "vision_validation", "extra_instruments": None}
    if sealed:
        payload["validation_seal"] = validation_seal.seal(
            session_id=session_id, operator_user_id=user, closed_at=at, close_path="vision_validation", lines=lines,
            final_count_event_id=final_id, final_frame_sha256="cd" * 32, extra_instruments=None)
    _event(db_conn, session_id, "manual_close", at, user=user, payload=payload)
    return session_id, final_id


def test_an_untouched_validation_is_intact(client, world, as_user, db_conn):
    session_id, final_id = _validated(db_conn, world)
    body = client.get(f"{BASE}/{session_id}/validation-seal", headers=as_user("spd_a")).get_json()
    assert body["status"] == "intact" and body["stored_sha256"] == body["computed_sha256"]
    assert body["lines"] == 2 and body["final_count_event_id"] == str(final_id) and body["close_path"] == "vision_validation"


def test_a_change_to_a_line_or_to_the_evidence_hash_is_detected(client, world, as_user, db_conn):
    session_id, final_id = _validated(db_conn, world)
    db_conn.execute(text("UPDATE count_event SET detected_quantity = 1 WHERE session_id = :s AND family_id = :f AND "
                         "event_type_id = (SELECT id FROM cat_event_type WHERE code = 'manual_count')"),
                    {"s": session_id, "f": world.families["a"]})
    assert client.get(f"{BASE}/{session_id}/validation-seal", headers=as_user("spd_a")).get_json()["status"] == "altered"
    db_conn.execute(text("UPDATE count_event SET detected_quantity = 2 WHERE session_id = :s AND family_id = :f AND "
                         "event_type_id = (SELECT id FROM cat_event_type WHERE code = 'manual_count')"),
                    {"s": session_id, "f": world.families["a"]})
    assert client.get(f"{BASE}/{session_id}/validation-seal", headers=as_user("spd_a")).get_json()["status"] == "intact"
    db_conn.execute(text("UPDATE count_event SET payload = jsonb_set(payload, '{frame_sha256}', '\"00\"') WHERE id = :i"),
                    {"i": final_id})
    assert client.get(f"{BASE}/{session_id}/validation-seal", headers=as_user("spd_a")).get_json()["status"] == "altered"


def test_not_sealed_and_no_validation_are_reported_as_such(client, world, as_user, db_conn):
    assert client.get(f"{BASE}/{world.sessions['a2'].id}/validation-seal",
                      headers=as_user("spd_a")).get_json()["status"] == "no_validation"
    session_id, _ = _validated(db_conn, world, sealed=False)
    body = client.get(f"{BASE}/{session_id}/validation-seal", headers=as_user("spd_a")).get_json()
    assert body["status"] == "not_sealed" and body["computed_sha256"] is None


def test_access_and_xml_contract(client, world, as_user, db_conn):
    session_id, _ = _validated(db_conn, world)
    assert client.get(f"{BASE}/{session_id}/validation-seal", headers=as_user("op_a2")).status_code == 403
    assert client.get(f"{BASE}/{session_id}/validation-seal", headers=as_user("spd_b")).status_code == 404
    xml = client.get(f"{BASE}/{session_id}/validation-seal", headers=as_user("spd_a", Accept="application/xml"))
    assert XSD.validate(etree.fromstring(xml.data)), XSD.error_log
