"""Supervisor operations (PEF-DS09 §1.3.4 complement v0.2.0): the portal's RF-SP rules in SessionService.

Real PostgreSQL (rolled-back transaction) and Redis; see conftest.py / write_fixtures.py. The
cross-process test commits its own rows and removes them.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from lxml import etree
from sqlalchemy import text

from conftest import TransactionalDatabase, make_settings
from write_fixtures import committed_env, wdata  # noqa: F401 - fixtures
from main import create_app

BASE = "/api/v1/sessions"
XML = {"Content-Type": "application/xml", "Accept": "application/xml"}
XSD = etree.XMLSchema(etree.parse(str(Path(__file__).resolve().parents[1] / "schemas" / "session-requests.xsd")))


def _id(c, table, code):
    return c.execute(text(f"SELECT id FROM {table} WHERE code = :c"), {"c": code}).scalar_one()


@pytest.fixture
def plan(world, wdata, db_conn):
    """Physicians, a patient and a procedure of tenant A (plus a physician of B)."""
    c, w = db_conn, world
    p = type("Plan", (), {})()
    p.physician, p.physician2, p.physician_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for pid, inst in ((p.physician, "A"), (p.physician2, "A"), (p.physician_b, "B")):
        c.execute(text("INSERT INTO physician (id, name, institution_id) VALUES (:id, :n, :i)"),
                  {"id": pid, "n": f"Dr {pid.hex[:4]}", "i": w.institutions[inst]})
    p.patient = uuid.uuid4()
    c.execute(text("INSERT INTO patient (id, display_name, institution_id) VALUES (:id, 'Paciente E', :i)"),
              {"id": p.patient, "i": w.institutions["A"]})
    p.procedure = c.execute(text("SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1")).scalar_one()
    return p


def schedule_body(world, plan, **overrides):
    body = {
        "operator_user_id": str(world.users["op_a"].id), "procedure_type_id": str(plan.procedure),
        "room_id": str(world.rooms["A"]), "station_id": str(world.stations["A"]), "kit_id": str(world.kits["A"]),
        "patient_id": str(plan.patient), "scheduled_at": "2026-11-02T09:30:00Z", "phase_code": "start",
        "physicians": [{"physician_id": str(plan.physician), "surgical_role_code": "surgeon"}],
        "expected_lines": [{"family_id": str(world.families["a"]), "expected_quantity": 2},
                           {"family_id": str(world.families["b"]), "expected_quantity": 1}],
    }
    body.update(overrides)
    return body


def audits(db_conn, session_id, outcome="success"):
    return db_conn.execute(text("SELECT action FROM access_audit WHERE resource_id = :s AND outcome = :o "
                                "ORDER BY occurred_at, action"), {"s": session_id, "o": outcome}).scalars().all()


def reason(response):
    return response.get_json()["details"].get("reason")


# --- Schedule ----------------------------------------------------------------------------------------


def test_schedule_creates_operation_team_session_and_inventory(client, world, plan, as_user, db_conn):
    body = schedule_body(world, plan, privacy_notice_version_id=str(world.notice_id), purpose_model_improvement=True)
    response = client.post(f"{BASE}/schedule", json=body, headers=as_user("spd_a"))
    assert response.status_code == 201, response.get_data(as_text=True)
    snap = response.get_json()
    assert snap["status"]["code"] == "scheduled" and snap["capture_mode"] is None
    assert snap["operator"]["id"] == str(world.users["op_a"].id) and snap["phase"]["code"] == "start"
    assert snap["operation"]["scheduled_at"] == "2026-11-02T09:30:00Z"
    assert snap["patient"] == {"id": str(plan.patient), "display_name": "Paciente E"}
    assert [m["physician_id"] for m in snap["surgical_team"]] == [str(plan.physician)]
    assert {(l["family_id"], l["expected_quantity"], l["source"]) for l in snap["expected_inventory"]} == {
        (str(world.families["a"]), 2, "kit_snapshot"), (str(world.families["b"]), 1, "kit_snapshot")}
    assert snap["privacy"]["purpose_model_improvement"] is True and snap["has_privacy_agreement"] is True
    op_status = db_conn.execute(text("SELECT s.code FROM operation o JOIN cat_operation_status s ON s.id = o.status_id "
                                     "WHERE o.id = :id"), {"id": snap["operation"]["id"]}).scalar_one()
    assert op_status == "scheduled"
    assert audits(db_conn, snap["id"]) == ["privacy.session_agreement", "session.scheduled"]


def test_schedule_xml_conforms_to_the_request_schema(client, world, plan, as_user):
    body = (f"<schedule_request><operator_user_id>{world.users['op_a'].id}</operator_user_id>"
            f"<procedure_type_id>{plan.procedure}</procedure_type_id><room_id>{world.rooms['A']}</room_id>"
            f"<station_id>{world.stations['A']}</station_id><kit_id>{world.kits['A']}</kit_id>"
            "<scheduled_at>2026-11-02T09:30:00Z</scheduled_at><phase_code>start</phase_code>"
            f"<physicians><physician><physician_id>{plan.physician}</physician_id></physician></physicians>"
            f"<expected_lines><line><family_id>{world.families['a']}</family_id><expected_quantity>3</expected_quantity>"
            "</line></expected_lines></schedule_request>").encode()
    assert XSD.validate(etree.fromstring(body))
    response = client.post(f"{BASE}/schedule", data=body, headers={**as_user("spd_a"), **XML})
    assert response.status_code == 201, response.get_data(as_text=True)
    document = etree.fromstring(response.data)
    assert document.tag == "session" and document.findtext("expected_inventory/line/source") == "schedule_additional"
    assert document.findtext("surgical_team/member/role_code") == "surgeon"


@pytest.mark.parametrize("change, status, expected", [
    (lambda w, p: {"operator_user_id": str(w.users["spd_a"].id)}, 422, "operator_role_required"),
    (lambda w, p: {"operator_user_id": str(w.users["op_b"].id)}, 422, "invalid_operator"),
    (lambda w, p: {"room_id": str(w.rooms["B"])}, 422, "invalid_room"),
    (lambda w, p: {"station_id": str(w.stations["B"])}, 422, "invalid_station"),
    (lambda w, p: {"kit_id": str(w.kits["B"])}, 422, "invalid_kit"),
    (lambda w, p: {"patient_id": str(uuid.uuid4())}, 422, "invalid_patient"),
    (lambda w, p: {"procedure_type_id": str(uuid.uuid4())}, 422, "invalid_procedure"),
    (lambda w, p: {"physicians": []}, 400, "physicians_required"),
    (lambda w, p: {"physicians": [{"physician_id": ""}]}, 400, "physician_required"),
    (lambda w, p: {"physicians": [{"physician_id": str(p.physician)}, {"physician_id": str(p.physician)}]}, 400,
     "duplicate_physician"),
    (lambda w, p: {"physicians": [{"physician_id": str(p.physician_b)}]}, 422, "invalid_physician"),
    (lambda w, p: {"physicians": [{"physician_id": str(p.physician), "surgical_role_code": "juggler"}]}, 422,
     "invalid_surgical_role"),
    (lambda w, p: {"phase_code": "nope"}, 422, "invalid_phase"),
    (lambda w, p: {"scheduled_at": "tomorrow"}, 422, "invalid_scheduled_at"),
    (lambda w, p: {"expected_lines": []}, 400, "lines_required"),
    (lambda w, p: {"expected_lines": [{"family_id": str(w.families["a"]), "expected_quantity": 0}]}, 422,
     "invalid_quantity"),
    (lambda w, p: {"expected_lines": [{"family_id": str(w.families["a"]), "expected_quantity": 99}]}, 422,
     "stock_block"),
])
def test_schedule_rules_of_the_portal(client, world, plan, as_user, db_conn, change, status, expected):
    before = db_conn.execute(text("SELECT count(*) FROM work_session")).scalar_one()
    response = client.post(f"{BASE}/schedule", json=schedule_body(world, plan, **change(world, plan)),
                           headers=as_user("spd_a"))
    assert response.status_code == status and reason(response) == expected, response.get_data(as_text=True)
    assert db_conn.execute(text("SELECT count(*) FROM work_session")).scalar_one() == before


@pytest.mark.parametrize("user", ["op_a", "admin_a", "op_admin_a"])
def test_only_the_supervisor_schedules_and_denials_are_audited(client, world, plan, as_user, db_conn, user):
    response = client.post(f"{BASE}/schedule", json=schedule_body(world, plan), headers=as_user(user))
    assert response.status_code == 403
    denied = db_conn.execute(text("SELECT action, actor_user_id FROM access_audit WHERE outcome = 'denied' "
                                  "AND actor_user_id = :u"), {"u": world.users[user].id}).all()
    assert [tuple(row) for row in denied] == [("DENIED_POST_WORK_SESSION", world.users[user].id)]


def test_headers_are_not_identity(client, world, plan):
    response = client.post(f"{BASE}/schedule", json=schedule_body(world, plan),
                           headers={"X-User-Id": str(world.users["spd_a"].id), "X-User-Role": "spd_supervisor"})
    assert response.status_code == 401


# --- Full edit and privacy --------------------------------------------------------------------------


def _scheduled(client, world, plan, as_user):
    response = client.post(f"{BASE}/schedule", json=schedule_body(world, plan), headers=as_user("spd_a"))
    assert response.status_code == 201
    return response.get_json()["id"]


def test_full_edit_while_scheduled(client, world, plan, as_user, db_conn):
    session_id = _scheduled(client, world, plan, as_user)
    body = schedule_body(world, plan, operator_user_id=str(world.users["op_a2"].id), patient_id=None,
                         physicians=[{"physician_id": str(plan.physician2), "surgical_role_code": "first_assistant"}],
                         expected_lines=[{"family_id": str(world.families["a"]), "expected_quantity": 1}])
    del body["phase_code"]
    response = client.put(f"{BASE}/{session_id}", json=body, headers=as_user("spd_a"))
    assert response.status_code == 200, response.get_data(as_text=True)
    snap = response.get_json()
    assert snap["operator"]["id"] == str(world.users["op_a2"].id) and snap["patient"] is None
    assert [(m["physician_id"], m["role_code"]) for m in snap["surgical_team"]] == [(str(plan.physician2), "first_assistant")]
    assert [(l["expected_quantity"], l["source"]) for l in snap["expected_inventory"]] == [(1, "schedule_additional")]
    assert audits(db_conn, session_id) == ["session.scheduled", "session.scheduled_updated"]


def test_full_edit_and_privacy_need_a_scheduled_session(client, world, plan, as_user):
    body = schedule_body(world, plan)
    del body["phase_code"]
    in_progress = world.sessions["a2"].id
    response = client.put(f"{BASE}/{in_progress}", json=body, headers=as_user("spd_a"))
    assert response.status_code == 409 and reason(response) == "not_scheduled"
    response = client.put(f"{BASE}/{in_progress}/privacy-agreement",
                          json={"privacy_notice_version_id": str(world.notice_id)}, headers=as_user("spd_a"))
    assert response.status_code == 409 and reason(response) == "privacy_not_scheduled"
    assert client.put(f"{BASE}/{uuid.uuid4()}", json=body, headers=as_user("spd_a")).status_code == 404


def test_privacy_agreement(client, world, plan, as_user, db_conn):
    session_id = _scheduled(client, world, plan, as_user)
    bad = client.put(f"{BASE}/{session_id}/privacy-agreement", json={"privacy_notice_version_id": str(uuid.uuid4())},
                     headers=as_user("spd_a"))
    assert bad.status_code == 422 and reason(bad) == "invalid_privacy_notice"
    xml = (f"<privacy_agreement_request><privacy_notice_version_id>{world.notice_id}</privacy_notice_version_id>"
           "<purpose_model_improvement>false</purpose_model_improvement></privacy_agreement_request>").encode()
    response = client.put(f"{BASE}/{session_id}/privacy-agreement", data=xml, headers={**as_user("spd_a"), **XML})
    assert response.status_code == 200 and etree.fromstring(response.data).findtext("has_privacy_agreement") == "true"
    assert "privacy.session_agreement" in audits(db_conn, session_id)


# --- Inventory and confirmed additions ----------------------------------------------------------------


def _add_initial_count(db_conn, world, session_id, total, minutes_ago=10):
    db_conn.execute(text(
        "INSERT INTO count_event (event_type_id, payload, occurred_at, session_id, detected_quantity) "
        "VALUES (:t, CAST(:p AS jsonb), :o, :s, :d)"),
        {"t": world.event_type["auto_count"], "p": json.dumps({"trigger": "initial", "scope": "board"}),
         "o": datetime.now(timezone.utc) - timedelta(minutes=minutes_ago), "s": session_id, "d": total})


def _added_events(db_conn, session_id):
    return db_conn.execute(text(
        "SELECT ce.expected_quantity, ce.detected_quantity, ce.payload, ce.user_id FROM count_event ce "
        "JOIN cat_event_type t ON t.id = ce.event_type_id WHERE t.code = 'instruments_added' AND ce.session_id = :s"),
        {"s": session_id}).all()


def test_confirmed_addition_is_an_instruments_added_event_and_idempotent(client, world, wdata, as_user, db_conn):
    session_id = world.sessions["a2"].id   # in_progress, vision
    db_conn.execute(text("INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source) "
                         "VALUES (:s, :f, 1, 'kit_snapshot')"), {"s": session_id, "f": world.families["a"]})
    _add_initial_count(db_conn, world, session_id, 1)
    body = {"lines": [{"family_id": str(world.families["a"]), "expected_quantity": 3}], "physically_added": True}
    response = client.put(f"{BASE}/{session_id}/expected-inventory", json=body, headers=as_user("spd_a"))
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()[0]["source"] == "live_add"
    [event] = _added_events(db_conn, session_id)
    assert (event[0], event[1], event[3]) == (1, 2, world.users["spd_a"].id)
    assert event[2] == {"added": [{"family_id": str(world.families["a"]), "previous": 1, "new": 3, "quantity": 2}],
                        "confirmed_by_role": "spd_supervisor", "reference_before": 1}
    # The same request again (double submit): nothing more is added.
    assert client.put(f"{BASE}/{session_id}/expected-inventory", json=body, headers=as_user("spd_a")).status_code == 200
    assert len(_added_events(db_conn, session_id)) == 1
    assert audits(db_conn, session_id) == ["session.instruments_added", "session.inventory_updated"]


def test_administrative_edit_never_changes_the_reference(client, world, wdata, as_user, db_conn):
    session_id = world.sessions["a2"].id
    body = {"lines": [{"family_id": str(world.families["b"]), "expected_quantity": 2}]}
    assert client.put(f"{BASE}/{session_id}/expected-inventory", json=body, headers=as_user("spd_a")).status_code == 200
    assert _added_events(db_conn, session_id) == []
    scheduled = world.sessions["a1"].id   # physically_added means nothing before the session starts
    body["physically_added"] = True
    assert client.put(f"{BASE}/{scheduled}/expected-inventory", json=body, headers=as_user("spd_a")).status_code == 200
    assert _added_events(db_conn, scheduled) == []


def test_inventory_frozen_after_the_operator_finished(client, world, wdata, as_user):
    body = {"lines": [{"family_id": str(world.families["a"]), "expected_quantity": 1}], "physically_added": True}
    response = client.put(f"{BASE}/{world.sessions['a3'].id}/expected-inventory", json=body, headers=as_user("spd_a"))
    assert response.status_code == 409 and reason(response) == "inventory_locked"


# --- Discrepancies and close ---------------------------------------------------------------------------


@pytest.fixture
def review(world, db_conn):
    """a3 (correction_required): two open discrepancies (family a, no family) + one resolved; a lost and an
    available piece of family a."""
    cycle = {code: _id(db_conn, "cat_instrument_cycle_status", code) for code in ("available", "lost")}
    pieces = {}
    for code in ("available", "lost"):
        piece = uuid.uuid4()
        db_conn.execute(text("INSERT INTO instrument (id, family_id, cycle_status_id, institution_id, active) "
                             "VALUES (:id, :f, :s, :i, :a)"),
                        {"id": piece, "f": world.families["a"], "s": cycle[code], "i": world.institutions["A"],
                         "a": code != "lost"})
        pieces[code] = piece
    return pieces


def _resolve(client, as_user, session_id, discrepancy_id, outcome="resolved", **extra):
    return client.post(f"{BASE}/{session_id}/discrepancies/{discrepancy_id}/resolve",
                       json={"notes": "Checked the tray", "outcome": outcome, **extra}, headers=as_user("spd_a"))


def _piece(db_conn, piece):
    return tuple(db_conn.execute(text("SELECT s.code, i.active FROM instrument i JOIN cat_instrument_cycle_status s "
                                      "ON s.id = i.cycle_status_id WHERE i.id = :id"), {"id": piece}).one())


def test_lost_without_instrument_uses_the_family_rule(client, world, review, as_user, db_conn):
    s = world.sessions["a3"].id
    response = _resolve(client, as_user, s, world.discrepancies["open_a"], "lost")
    assert response.status_code == 200, response.get_data(as_text=True)
    assert _piece(db_conn, review["available"]) == ("lost", False)
    description = db_conn.execute(text("SELECT description FROM discrepancy WHERE id = :d"),
                                  {"d": world.discrepancies["open_a"]}).scalar_one()
    assert description.endswith("\nSPD resolution (marked lost): Checked the tray")


def test_recovered_without_a_lost_piece_is_noted(client, world, as_user, db_conn):
    s = world.sessions["a3"].id
    assert _resolve(client, as_user, s, world.discrepancies["open_a"], "recovered").status_code == 200
    description = db_conn.execute(text("SELECT description FROM discrepancy WHERE id = :d"),
                                  {"d": world.discrepancies["open_a"]}).scalar_one()
    assert description.endswith("Note: no lost piece of this family was found to reactivate.")


def test_last_resolution_moves_to_awaiting_review_and_awaiting_is_still_reviewable(client, world, as_user, db_conn):
    s = world.sessions["a3"].id
    note = _resolve(client, as_user, s, world.discrepancies["open_a"])
    assert note.status_code == 409 and reason(note) == "physical_shortage_needs_piece_outcome"
    first = _resolve(client, as_user, s, world.discrepancies["open_a"], "lost").get_json()
    assert first["open_remaining"] == 1 and first["session_status_code"] == "correction_required"
    second = _resolve(client, as_user, s, world.discrepancies["open_nofamily"]).get_json()
    assert second["open_remaining"] == 0 and second["session_status_code"] == "awaiting_spd_review"
    again = _resolve(client, as_user, s, world.discrepancies["open_a"], "lost")
    assert again.status_code == 409 and reason(again) == "already_resolved"
    unknown = _resolve(client, as_user, s, uuid.uuid4())
    assert unknown.status_code == 404 and reason(unknown) == "discrepancy_not_found"


def test_closed_session_is_not_reviewable(client, world, as_user):
    response = _resolve(client, as_user, world.sessions["a_unscheduled"].id, uuid.uuid4())
    assert response.status_code == 409 and reason(response) == "session_not_reviewable"


def _to_awaiting_with_contingency(db_conn, world, session_id):
    db_conn.execute(text("UPDATE discrepancy SET resolved = TRUE, resolved_at = now() WHERE session_id = :s"),
                    {"s": session_id})
    db_conn.execute(text("UPDATE work_session SET status_id = (SELECT id FROM cat_session_status "
                         "WHERE code = 'awaiting_spd_review') WHERE id = :s"), {"s": session_id})
    db_conn.execute(text(
        "INSERT INTO count_event (event_type_id, payload, occurred_at, session_id, user_id) "
        "VALUES (:t, CAST(:p AS jsonb), :o, :s, :u)"),
        {"t": world.event_type["manual_close"], "s": session_id, "u": world.users["op_a"].id,
         "o": datetime.now(timezone.utc),
         "p": json.dumps({"contingency": {"reason_code": "worker_unavailable", "notes": None}, "verified_total": 6})})


def test_contingency_count_needs_the_review_confirmation(client, world, as_user, db_conn):
    s = world.sessions["a3"].id
    _to_awaiting_with_contingency(db_conn, world, s)
    missing = client.post(f"{BASE}/{s}/close", json={"material_recovered": True}, headers=as_user("spd_a"))
    assert missing.status_code == 400 and reason(missing) == "contingency_review_required"
    detail = client.get(f"{BASE}/{s}?expand=detail", headers=as_user("spd_a")).get_json()
    assert detail["contingency"]["reason_code"] == "worker_unavailable" and detail["contingency"]["verified_total"] == 6
    assert detail["contingency"]["user_name"] == "Test op_a"
    # a3 has a physical shortage (open_a): never a safe close, only an acknowledged unreconciled one.
    blocked = client.post(f"{BASE}/{s}/close", json={"material_recovered": True, "contingency_reviewed": True},
                          headers=as_user("spd_a"))
    assert blocked.status_code == 409 and reason(blocked) == "unreconciled_physical_shortage"
    ok = client.post(f"{BASE}/{s}/close", json={"material_recovered": True, "contingency_reviewed": True,
                                                "unreconciled_shortage_acknowledged": True}, headers=as_user("spd_a"))
    assert ok.status_code == 200 and ok.get_json()["status_code"] == "closed"
    payload = db_conn.execute(text("SELECT ce.payload FROM count_event ce JOIN cat_event_type t ON t.id = ce.event_type_id "
                                   "WHERE t.code = 'session_close' AND ce.session_id = :s"), {"s": s}).scalar_one()
    assert payload["contingency_reviewed"] is True and payload["safe_close_confirmed"] is False
    assert payload["unreconciled_physical_shortages"] == [str(world.discrepancies["open_a"])]
    twice = client.post(f"{BASE}/{s}/close", json={"material_recovered": True, "contingency_reviewed": True},
                        headers=as_user("spd_a"))
    assert twice.status_code == 409 and reason(twice) == "not_awaiting_spd_review"


def test_close_xml_with_contingency_reviewed(client, world, as_user, db_conn):
    s = world.sessions["a3"].id
    _to_awaiting_with_contingency(db_conn, world, s)
    xml = (b"<close_request><material_recovered>true</material_recovered><contingency_reviewed>1</contingency_reviewed>"
           b"<unreconciled_shortage_acknowledged>true</unreconciled_shortage_acknowledged></close_request>")
    response = client.post(f"{BASE}/{s}/close", data=xml, headers={**as_user("spd_a"), **XML})
    assert response.status_code == 200 and etree.fromstring(response.data).findtext("status_code") == "closed"


# --- Reads (expand=detail) ----------------------------------------------------------------------------


def test_expanded_list_filters_and_pagination(client, world, as_user):
    response = client.get(f"{BASE}?expand=detail&status=correction_required,scheduled", headers=as_user("spd_a"))
    rows = response.get_json()
    assert response.status_code == 200 and {row["status"]["code"] for row in rows} == {"correction_required", "scheduled"}
    assert all(row["operation"]["institution_id"] == str(world.institutions["A"]) for row in rows)
    paged = client.get(f"{BASE}?expand=detail&page=1&per_page=1", headers=as_user("spd_a"))
    assert len(paged.get_json()) == 1 and int(paged.headers["X-Total-Count"]) >= 3 and paged.headers["X-Page"] == "1"
    assert client.get(f"{BASE}?expand=detail&status=bogus", headers=as_user("spd_a")).status_code == 400
    assert client.get(f"{BASE}?expand=everything", headers=as_user("spd_a")).status_code == 400
    # An operator sees only their own sessions in the expanded list too.
    own = client.get(f"{BASE}?expand=detail", headers=as_user("op_a2")).get_json()
    assert {row["operator"]["id"] for row in own} == {str(world.users["op_a2"].id)}


def test_expanded_detail_and_queue(client, world, as_user):
    snap = client.get(f"{BASE}/{world.sessions['a3'].id}?expand=detail", headers=as_user("spd_a")).get_json()
    assert [l["source"] for l in snap["expected_inventory"]] == ["kit_snapshot", "schedule_additional", "live_add", "manual"]
    assert snap["events"][0]["event_code"] == "session_open" and snap["events"][-1]["event_code"] == "manual_close"
    legacy = next(e for e in snap["events"] if e["id"] == str(world.events["auto_legacy"]))
    # Only the projected payload: no boxes, scores, run ids or assignment metrics.
    assert set(legacy["payload"]) == {"reason", "scope", "model_version", "pipeline", "frames_processed", "video_t", "board"}
    assert "boxes" not in json.dumps(legacy["payload"]) and "best_confidence" not in json.dumps(legacy["payload"])
    assert [d["resolved"] for d in snap["discrepancies"]] == [False, False, True]
    queue = client.get(f"{BASE}/discrepancies?status=open", headers=as_user("spd_a")).get_json()
    assert {d["id"] for d in queue} == {str(world.discrepancies["open_a"]), str(world.discrepancies["open_nofamily"])}
    assert client.get(f"{BASE}/discrepancies", headers=as_user("op_a")).status_code == 403
    xml = client.get(f"{BASE}/{world.sessions['a3'].id}?expand=detail", headers={**as_user("spd_a"), **XML})
    assert etree.fromstring(xml.data).find("events/event/payload") is not None
    assert client.get(f"{BASE}/{world.sessions['b1'].id}?expand=detail", headers=as_user("spd_a")).status_code == 404


# --- Contract, health and the legacy Operator writes -------------------------------------------------


def test_invalid_xml_bodies_are_400(client, world, as_user):
    s = world.sessions["a2"].id
    for body in (b"<expected_inventory_update><lines><line><family_id lang='x'>1</family_id></line></lines>"
                 b"</expected_inventory_update>",
                 b"<expected_inventory_update><lines><item/></lines></expected_inventory_update>"):
        response = client.put(f"{BASE}/{s}/expected-inventory", data=body, headers={**as_user("spd_a"), **XML})
        assert response.status_code == 400 and etree.fromstring(response.data).findtext("error") == "invalid_request"
    unknown = client.put(f"{BASE}/{s}/expected-inventory", data=b"<expected_inventory_update><status>x</status>"
                         b"</expected_inventory_update>", headers={**as_user("spd_a"), "Content-Type": "application/xml"})
    assert unknown.get_json()["details"] == {"unknown_fields": ["status"]}
    nested = client.put(f"{BASE}/{s}/expected-inventory", headers={**as_user("spd_a"), "Content-Type": "application/xml"},
                        data=b"<expected_inventory_update><lines><line><x>1</x></line></lines></expected_inventory_update>")
    assert nested.status_code == 400 and nested.get_json()["details"]["reason"] == "xsd_validation_failed"


def test_contract_documents_and_health(client):
    assert b"/api/v1/sessions/schedule" in client.get(f"{BASE}/openapi.yaml").data
    assert client.get(f"{BASE}/schemas/session-requests.xsd").status_code == 200
    assert b"swagger-ui" in client.get(f"{BASE}/docs").data
    health = client.get("/health")
    assert health.status_code == 200 and health.get_json()["checks"] == {"database": "ok", "redis": "ok"}


@pytest.fixture
def production_client(db_conn, redis_client):
    """The production default: legacy Operator writes disabled."""
    app = create_app(make_settings(operator_writes_enabled=False), database=TransactionalDatabase(db_conn),
                     redis_client=redis_client)
    return app.test_client()


@pytest.mark.parametrize("path, body", [("start", {}), ("vision-counts", {"counts": []}),
                                         ("count-report", {"reports": []})])
def test_legacy_operator_writes_are_disabled_by_default(production_client, world, as_user, db_conn, path, body):
    s = world.sessions["a1"].id
    response = production_client.post(f"{BASE}/{s}/{path}", json=body, headers=as_user("op_a"))
    assert response.status_code == 410 and reason(response) == "operator_flow_in_portal"
    status = db_conn.execute(text("SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id "
                                  "WHERE w.id = :id"), {"id": s}).scalar_one()
    assert status == "scheduled"
    # Reads stay available to the Operator (scoped to their sessions).
    assert production_client.get(f"{BASE}/{s}", headers=as_user("op_a")).status_code == 200


# --- Concurrency with the portal's Operator transaction (two processes, one PostgreSQL row) -----------


def test_inventory_waits_for_the_operator_lock_then_sees_the_new_state(committed_env, pg_engine):
    """The portal's Operator report locks work_session FOR UPDATE; a Supervisor edit sent meanwhile waits,
    then sees the committed status and answers 409 — it never edits an inventory the report already used."""
    env = committed_env
    headers = {"Authorization": f"Bearer {env.spd_token}"}
    body = {"lines": [{"family_id": str(env.families[0]), "expected_quantity": 2}], "physically_added": True}
    result = {}
    operator = pg_engine.connect()
    tx = operator.begin()
    try:
        operator.execute(text("SELECT id FROM work_session WHERE id = :id FOR UPDATE"), {"id": env.session_id})

        def supervisor():
            result["response"] = env.app.test_client().put(f"{BASE}/{env.session_id}/expected-inventory", json=body,
                                                           headers=headers)

        worker = threading.Thread(target=supervisor)
        worker.start()
        time.sleep(0.8)
        assert worker.is_alive(), "the Supervisor edit must wait for the Operator's lock"
        operator.execute(text("UPDATE work_session SET status_id = (SELECT id FROM cat_session_status "
                              "WHERE code = 'awaiting_spd_review') WHERE id = :id"), {"id": env.session_id})
        tx.commit()
        worker.join(10)
    finally:
        operator.close()
    response = result["response"]
    assert response.status_code == 409 and response.get_json()["details"]["reason"] == "inventory_locked"
    with pg_engine.connect() as c:
        quantity = c.execute(text("SELECT expected_quantity FROM expected_inventory WHERE session_id = :s"),
                             {"s": env.session_id}).scalar_one()
    assert quantity == 1


def test_concurrent_confirmed_additions_count_once(committed_env, pg_engine):
    """Two Supervisors confirm the same addition at the same time: one instruments_added event."""
    env = committed_env
    with pg_engine.begin() as c:
        c.execute(text("UPDATE work_session SET status_id = (SELECT id FROM cat_session_status WHERE code = 'in_progress'), "
                       "capture_mode = 'vision', started_at = now() WHERE id = :id"), {"id": env.session_id})
    headers = {"Authorization": f"Bearer {env.spd_token}"}
    body = {"lines": [{"family_id": str(env.families[0]), "expected_quantity": 3}], "physically_added": True}
    responses = []
    barrier = threading.Barrier(2)

    def supervisor():
        barrier.wait()
        responses.append(env.app.test_client().put(f"{BASE}/{env.session_id}/expected-inventory", json=body,
                                                   headers=headers))

    workers = [threading.Thread(target=supervisor) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
    assert [r.status_code for r in responses] == [200, 200]
    with pg_engine.connect() as c:
        added = c.execute(text("SELECT detected_quantity FROM count_event ce JOIN cat_event_type t ON t.id = ce.event_type_id "
                               "WHERE t.code = 'instruments_added' AND ce.session_id = :s"), {"s": env.session_id}).scalars().all()
    assert added == [2]
