"""POST /api/v1/sessions/{id}/count-report (Phase D).

Fixture sessions (tenant A, operator op_a, in_progress):
- cdata.vision: capture_mode vision, expected a=2, b=1, c=3;
- cdata.manual: capture_mode manual_no_privacy, expected a=2, b=1.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import text

from count_fixtures import add_auto_count, cdata, report_line, set_status  # noqa: F401  (fixture)

BASE = "/api/v1/sessions"


def _url(session_id):
    return f"{BASE}/{session_id}/count-report"


def _post(client, as_user, session_id, lines, user="op_a", **headers):
    return client.post(_url(session_id), json={"reports": lines}, headers=as_user(user, **headers))


def _state(db_conn, session_id):
    return db_conn.execute(text(
        "SELECT s.code AS status, w.ended_at, w.closed_by_user_id, w.capture_mode FROM work_session w "
        "JOIN cat_session_status s ON s.id = w.status_id WHERE w.id = :s"), {"s": session_id}).mappings().one()


def _events(db_conn, session_id, code):
    return db_conn.execute(text(
        "SELECT e.id, e.family_id, e.expected_quantity, e.detected_quantity, e.payload, e.user_id, e.occurred_at, "
        "e.client_event_id FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
        "WHERE e.session_id = :s AND t.code = :c ORDER BY e.id"), {"s": session_id, "c": code}).mappings().all()


def _corrections(db_conn, session_id):
    return db_conn.execute(text(
        "SELECT h.count_event_id, h.justification, h.user_id, h.recorded_at, e.family_id FROM human_correction h "
        "JOIN count_event e ON e.id = h.count_event_id WHERE e.session_id = :s"), {"s": session_id}).mappings().all()


def _discrepancies(db_conn, session_id):
    return db_conn.execute(text(
        "SELECT d.family_id, r.code AS reason, d.expected_quantity, d.detected_quantity, d.resolved, d.resolved_at, "
        "d.origin_event_id, d.description FROM discrepancy d LEFT JOIN cat_discrepancy_reason r ON r.id = d.reason_id "
        "WHERE d.session_id = :s"), {"s": session_id}).mappings().all()


def _audits(db_conn, session_id):
    return [tuple(r) for r in db_conn.execute(text(
        "SELECT action, correlation_id, actor_user_id FROM access_audit WHERE resource_id = :s AND outcome = 'success'"), {"s": session_id})]


def _assert_untouched(db_conn, session_id):
    state = _state(db_conn, session_id)
    assert state["status"] == "in_progress" and state["ended_at"] is None
    assert _events(db_conn, session_id, "manual_count") == [] and _events(db_conn, session_id, "manual_close") == []
    assert _corrections(db_conn, session_id) == [] and _discrepancies(db_conn, session_id) == []
    assert _audits(db_conn, session_id) == []


def _final(db_conn, world, session_id, **quantities):
    return add_auto_count(db_conn, session_id, "final", [(world.families[s], q) for s, q in quantities.items()],
                          minute=10)


# --- Manual sessions ------------------------------------------------------------------------------


def test_manual_matching_report_goes_to_spd_review(client, world, cdata, as_user, db_conn):
    response = _post(client, as_user, cdata.manual, [report_line(world, "a", 2), report_line(world, "b", 1)],
                     **{"X-Correlation-ID": "corr-report-1"})
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json() == {"session_id": str(cdata.manual), "status_code": "awaiting_spd_review",
                                   "open_discrepancies": 0, "human_corrections": 0}
    state = _state(db_conn, cdata.manual)
    assert state["status"] == "awaiting_spd_review" and state["ended_at"] is not None
    assert state["closed_by_user_id"] is None and state["capture_mode"] == "manual_no_privacy"
    counts = _events(db_conn, cdata.manual, "manual_count")
    assert {(r["family_id"], r["expected_quantity"], r["detected_quantity"]) for r in counts} == {
        (world.families["a"], 2, 2), (world.families["b"], 1, 1)}
    assert all(r["user_id"] == world.users["op_a"].id and r["payload"] == {} and r["client_event_id"] is None
               for r in counts)
    closes = _events(db_conn, cdata.manual, "manual_close")
    assert len(closes) == 1 and closes[0]["user_id"] == world.users["op_a"].id
    assert closes[0]["payload"] == {"capture_mode": "manual_no_privacy", "open_discrepancies": 0,
                                    "human_corrections": 0, "next_status": "awaiting_spd_review"}
    # One logical instant for the whole report.
    assert {r["occurred_at"] for r in counts} == {closes[0]["occurred_at"]} == {state["ended_at"]}
    assert _corrections(db_conn, cdata.manual) == [] and _discrepancies(db_conn, cdata.manual) == []
    assert _audits(db_conn, cdata.manual) == [("session.manual_close", "corr-report-1", world.users["op_a"].id)]


@pytest.mark.parametrize("reported, reason", [(1, "shortage"), (3, "surplus")])
def test_manual_mismatch_creates_discrepancy_without_correction(client, world, cdata, as_user, db_conn, reported, reason):
    response = _post(client, as_user, cdata.manual, [
        report_line(world, "a", reported, reason_code="occluded"), report_line(world, "b", 1)])
    assert response.status_code == 200
    assert response.get_json()["status_code"] == "correction_required"
    assert response.get_json()["open_discrepancies"] == 1 and response.get_json()["human_corrections"] == 0
    assert _corrections(db_conn, cdata.manual) == []
    [disc] = _discrepancies(db_conn, cdata.manual)
    origin = next(r for r in _events(db_conn, cdata.manual, "manual_count") if r["family_id"] == world.families["a"])
    assert disc["reason"] == reason                     # derived from the sign, not the operator's "occluded"
    assert (disc["family_id"], disc["expected_quantity"], disc["detected_quantity"]) == (world.families["a"], 2, reported)
    assert disc["resolved"] is False and disc["resolved_at"] is None and disc["origin_event_id"] == origin["id"]
    label = "Shortfall" if reason == "shortage" else "Surplus"
    assert disc["description"] == f"{label} on Family a: expected 2, reported {reported}."
    assert origin["payload"] == {"reason_code": "occluded"} and origin["detected_quantity"] == reported
    assert _state(db_conn, cdata.manual)["status"] == "correction_required"


def test_manual_with_suggested_quantity_is_400(client, world, cdata, as_user, db_conn):
    response = _post(client, as_user, cdata.manual, [report_line(world, "a", 2, suggested=2), report_line(world, "b", 1)])
    assert response.status_code == 400
    assert response.get_json()["details"] == {"family_id": str(world.families["a"]), "field": "suggested_quantity"}
    _assert_untouched(db_conn, cdata.manual)


def test_reported_zero_is_valid(client, world, cdata, as_user, db_conn):
    response = _post(client, as_user, cdata.manual, [report_line(world, "a", 0, reason_code="operator_error"),
                                                      report_line(world, "b", 1)])
    assert response.status_code == 200
    assert _discrepancies(db_conn, cdata.manual)[0]["detected_quantity"] == 0


# --- Vision sessions: Expected vs AI vs Human ------------------------------------------------------


@pytest.mark.parametrize("ai, human, hc, disc, status", [
    (1, 2, True, None, "awaiting_spd_review"),          # AI missed one; human = expected
    (2, 1, True, "shortage", "correction_required"),    # AI = expected; human found less
    (1, 1, False, "shortage", "correction_required"),   # AI agrees with the human; both below expected
    (2, 2, False, None, "awaiting_spd_review"),
    (2, 3, True, "surplus", "correction_required"),
])
def test_vision_semantics(client, world, cdata, as_user, db_conn, ai, human, hc, disc, status):
    _final(db_conn, world, cdata.vision, a=ai, b=1, c=3)
    response = _post(client, as_user, cdata.vision, [
        report_line(world, "a", human, suggested=ai, reason_code="occluded" if human != 2 else None,
                    notes="Second piece was under a gauze" if hc else None),
        report_line(world, "b", 1, suggested=1), report_line(world, "c", 3, suggested=3)])
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["status_code"] == status
    assert response.get_json()["human_corrections"] == (1 if hc else 0)
    corrections = _corrections(db_conn, cdata.vision)
    discrepancies = _discrepancies(db_conn, cdata.vision)
    manual_a = next(r for r in _events(db_conn, cdata.vision, "manual_count") if r["family_id"] == world.families["a"])
    assert manual_a["detected_quantity"] == human and manual_a["expected_quantity"] == 2
    if hc:
        [correction] = corrections
        assert correction["count_event_id"] == manual_a["id"] and correction["user_id"] == world.users["op_a"].id
        assert correction["justification"] == "Second piece was under a gauze"
        assert correction["recorded_at"] == _state(db_conn, cdata.vision)["ended_at"]
    else:
        assert corrections == []
    assert [d["reason"] for d in discrepancies] == ([disc] if disc else [])
    assert _state(db_conn, cdata.vision)["status"] == status


def test_family_absent_from_final_means_ai_zero(client, world, cdata, as_user, db_conn):
    _final(db_conn, world, cdata.vision, a=2, b=1)           # c not detected → suggestion 0
    lines = [report_line(world, "a", 2, suggested=2), report_line(world, "b", 1, suggested=1)]
    wrong = _post(client, as_user, cdata.vision, lines + [report_line(world, "c", 3, suggested=None)])
    assert wrong.status_code == 409
    response = _post(client, as_user, cdata.vision, lines + [
        report_line(world, "c", 3, suggested=0, notes="Vision did not see the retractors")])
    assert response.status_code == 200 and response.get_json()["human_corrections"] == 1
    [correction] = _corrections(db_conn, cdata.vision)
    assert correction["family_id"] == world.families["c"]


@pytest.mark.parametrize("client_value", [3, 1, None])
def test_suggested_tampering_is_409_and_writes_nothing(client, world, cdata, as_user, db_conn, client_value):
    _final(db_conn, world, cdata.vision, a=2, b=1, c=3)
    response = _post(client, as_user, cdata.vision, [
        report_line(world, "a", 2, suggested=client_value, notes="x"),
        report_line(world, "b", 1, suggested=1), report_line(world, "c", 3, suggested=3)])
    assert response.status_code == 409
    assert response.get_json()["details"] == {"family_id": str(world.families["a"]), "field": "suggested_quantity"}
    _assert_untouched(db_conn, cdata.vision)


def test_stable_window_is_not_authoritative(client, world, cdata, as_user, db_conn):
    add_auto_count(db_conn, cdata.vision, "stable_window", [(world.families["a"], 5)], minute=5)
    _final(db_conn, world, cdata.vision, a=2, b=1, c=3)
    add_auto_count(db_conn, cdata.vision, "stable_window", [(world.families["a"], 5)], minute=20)   # later, still not final
    rest = [report_line(world, "b", 1, suggested=1), report_line(world, "c", 3, suggested=3)]
    assert _post(client, as_user, cdata.vision, [report_line(world, "a", 2, suggested=5, notes="x")] + rest).status_code == 409
    assert _post(client, as_user, cdata.vision, [report_line(world, "a", 2, suggested=2)] + rest).status_code == 200


def test_latest_final_wins_by_occurred_at(client, world, cdata, as_user, db_conn):
    add_auto_count(db_conn, cdata.vision, "final", [(world.families["a"], 1)], minute=30)    # latest
    add_auto_count(db_conn, cdata.vision, "final", [(world.families["a"], 2)], minute=10)    # inserted later, older
    rest = [report_line(world, "b", 1, suggested=0, notes="b present"), report_line(world, "c", 3, suggested=0, notes="c")]
    assert _post(client, as_user, cdata.vision, [report_line(world, "a", 2, suggested=2)] + rest).status_code == 409
    assert _post(client, as_user, cdata.vision,
                 [report_line(world, "a", 2, suggested=1, notes="one hidden")] + rest).status_code == 200


def test_final_tie_on_occurred_at_breaks_by_id(client, world, cdata, as_user, db_conn):
    from count_fixtures import T0
    first = add_auto_count(db_conn, cdata.vision, "final", [(world.families["a"], 1)], occurred_at=T0)
    second = add_auto_count(db_conn, cdata.vision, "final", [(world.families["a"], 2)], occurred_at=T0)
    winner_a = 1 if str(first) > str(second) else 2
    rest = [report_line(world, "b", 1, suggested=0, notes="b"), report_line(world, "c", 3, suggested=0, notes="c")]
    line = report_line(world, "a", 2, suggested=winner_a, notes=None if winner_a == 2 else "one hidden")
    assert _post(client, as_user, cdata.vision, [line] + rest).status_code == 200


@pytest.mark.parametrize("history", ["stable_only", "nothing"])
def test_vision_without_final_is_human_fallback(client, world, cdata, as_user, db_conn, history):
    if history == "stable_only":
        add_auto_count(db_conn, cdata.vision, "stable_window", [(world.families["a"], 2)], minute=5)
    with_suggestion = _post(client, as_user, cdata.vision, [
        report_line(world, "a", 2, suggested=2), report_line(world, "b", 1), report_line(world, "c", 3)])
    assert with_suggestion.status_code == 409
    response = _post(client, as_user, cdata.vision, [
        report_line(world, "a", 1, reason_code="occluded"), report_line(world, "b", 1), report_line(world, "c", 3)])
    assert response.status_code == 200
    assert response.get_json() == {"session_id": str(cdata.vision), "status_code": "correction_required",
                                   "open_discrepancies": 1, "human_corrections": 0}
    assert _corrections(db_conn, cdata.vision) == []
    state = _state(db_conn, cdata.vision)
    assert state["capture_mode"] == "vision"           # still records how the session was started


# --- Justification and reason ----------------------------------------------------------------------


@pytest.mark.parametrize("notes", [None, "", "   ", "\n\t"])
def test_notes_required_when_ai_differs(client, world, cdata, as_user, db_conn, notes):
    _final(db_conn, world, cdata.vision, a=1, b=1, c=3)
    response = _post(client, as_user, cdata.vision, [
        report_line(world, "a", 2, suggested=1, notes=notes),
        report_line(world, "b", 1, suggested=1), report_line(world, "c", 3, suggested=3)])
    assert response.status_code == 400
    assert response.get_json()["details"] == {"family_id": str(world.families["a"]), "field": "notes"}
    _assert_untouched(db_conn, cdata.vision)


def test_reason_required_when_human_differs_from_expected(client, world, cdata, as_user, db_conn):
    response = _post(client, as_user, cdata.manual, [report_line(world, "a", 1), report_line(world, "b", 1)])
    assert response.status_code == 400
    assert response.get_json()["details"] == {"family_id": str(world.families["a"]), "field": "reason_code"}
    _assert_untouched(db_conn, cdata.manual)


@pytest.mark.parametrize("reason", ["other", "lost", "SHORTAGE"])
def test_reason_outside_catalog_is_422(client, world, cdata, as_user, db_conn, reason):
    response = _post(client, as_user, cdata.manual, [report_line(world, "a", 1, reason_code=reason),
                                                      report_line(world, "b", 1)])
    assert response.status_code == 422 and response.get_json()["error"] == "invalid_reference"
    _assert_untouched(db_conn, cdata.manual)


def test_optional_reason_on_matching_line_is_validated_and_kept(client, world, cdata, as_user, db_conn):
    assert _post(client, as_user, cdata.manual, [report_line(world, "a", 2, reason_code="other"),
                                                  report_line(world, "b", 1)]).status_code == 422
    assert _post(client, as_user, cdata.manual, [report_line(world, "a", 2, reason_code="misclassified"),
                                                  report_line(world, "b", 1)]).status_code == 200
    assert _discrepancies(db_conn, cdata.manual) == []


# --- Structure ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("lines_key", ["missing", "duplicate", "empty"])
def test_structural_errors_are_400(client, world, cdata, as_user, db_conn, lines_key):
    lines = {
        "missing": [report_line(world, "a", 2)],
        "duplicate": [report_line(world, "a", 2), report_line(world, "a", 2), report_line(world, "b", 1)],
        "empty": [],
    }[lines_key]
    response = _post(client, as_user, cdata.manual, lines)
    assert response.status_code == 400
    if lines_key == "missing":
        assert response.get_json()["details"] == {"missing_family_ids": [str(world.families["b"])]}
    _assert_untouched(db_conn, cdata.manual)


def test_extra_family_is_422(client, world, cdata, as_user, db_conn):
    response = _post(client, as_user, cdata.manual, [report_line(world, "a", 2), report_line(world, "b", 1),
                                                      report_line(world, "d", 1)])
    assert response.status_code == 422
    assert response.get_json()["details"] == {"field": "family_id", "family_ids": [str(world.families["d"])]}
    _assert_untouched(db_conn, cdata.manual)


@pytest.mark.parametrize("field, value", [
    ("reported_quantity", True), ("reported_quantity", -1), ("reported_quantity", 32768), ("reported_quantity", "2"),
    ("reported_quantity", 2.0), ("reported_quantity", None), ("suggested_quantity", -1), ("suggested_quantity", False),
    ("family_id", "nope"), ("family_id", None), ("reason_code", 5), ("notes", ["x"]),
])
def test_invalid_line_values_are_400(client, world, cdata, as_user, db_conn, field, value):
    line = report_line(world, "a", 2)
    line[field] = value
    assert _post(client, as_user, cdata.manual, [line, report_line(world, "b", 1)]).status_code == 400
    _assert_untouched(db_conn, cdata.manual)


@pytest.mark.parametrize("extra", ["expected_quantity", "institution_id", "capture_mode", "status", "user_id"])
def test_unknown_line_fields_are_400(client, world, cdata, as_user, db_conn, extra):
    line = report_line(world, "a", 2)
    line[extra] = 2
    response = _post(client, as_user, cdata.manual, [line, report_line(world, "b", 1)])
    assert response.status_code == 400 and response.get_json()["details"]["unknown_fields"] == [extra]
    _assert_untouched(db_conn, cdata.manual)


@pytest.mark.parametrize("extra", ["capture_mode", "status", "institution_id", "expected"])
def test_unknown_top_level_fields_are_400(client, world, cdata, as_user, db_conn, extra):
    body = {"reports": [report_line(world, "a", 2), report_line(world, "b", 1)], extra: "x"}
    assert client.post(_url(cdata.manual), json=body, headers=as_user("op_a")).status_code == 400
    _assert_untouched(db_conn, cdata.manual)


def test_absent_optional_fields_mean_null(client, world, cdata, as_user):
    lines = [{"family_id": str(world.families["a"]), "reported_quantity": 2},
             {"family_id": str(world.families["b"]), "reported_quantity": 1}]
    assert _post(client, as_user, cdata.manual, lines).status_code == 200


def test_expected_comes_from_server_inventory(client, world, cdata, as_user, db_conn):
    """The client cannot send expected quantities; a changed inventory changes the comparison."""
    db_conn.execute(text("UPDATE expected_inventory SET expected_quantity = 1 WHERE session_id = :s AND family_id = :f"),
                    {"s": cdata.manual, "f": world.families["a"]})
    response = _post(client, as_user, cdata.manual, [report_line(world, "a", 1), report_line(world, "b", 1)])
    assert response.status_code == 200 and response.get_json()["open_discrepancies"] == 0


def test_session_without_expected_inventory_is_409(client, world, cdata, as_user, db_conn):
    db_conn.execute(text("DELETE FROM expected_inventory WHERE session_id = :s"), {"s": cdata.manual})
    response = _post(client, as_user, cdata.manual, [report_line(world, "a", 2)])
    assert response.status_code == 409 and response.get_json()["details"] == {"expected_lines": 0}


# --- Access and state ----------------------------------------------------------------------------------


@pytest.mark.parametrize("user_key, target, status", [
    ("op_a2", "manual", 404), ("op_b", "manual", 404), ("op_a", "vision_b", 404),
    ("spd_a", "manual", 403), ("admin_a", "manual", 403), ("multi_a", "manual", 404),
])
def test_access_rules(client, world, cdata, as_user, db_conn, user_key, target, status):
    session_id = getattr(cdata, target)
    response = _post(client, as_user, session_id, [report_line(world, "a", 2), report_line(world, "b", 1)], user=user_key)
    assert response.status_code == status
    assert _audits(db_conn, session_id) == []


@pytest.mark.parametrize("status", ["scheduled", "awaiting_spd_review", "correction_required", "closed", "aborted"])
def test_non_in_progress_is_409(client, world, cdata, as_user, db_conn, status):
    set_status(db_conn, cdata.manual, status)
    response = _post(client, as_user, cdata.manual, [report_line(world, "a", 2), report_line(world, "b", 1)])
    assert response.status_code == 409 and response.get_json()["details"] == {"status_code": status}
    assert _events(db_conn, cdata.manual, "manual_count") == [] and _audits(db_conn, cdata.manual) == []


def test_second_report_is_409_and_inventory_is_frozen(client, world, cdata, as_user, db_conn):
    lines = [report_line(world, "a", 2), report_line(world, "b", 1)]
    assert _post(client, as_user, cdata.manual, lines).status_code == 200
    again = _post(client, as_user, cdata.manual, lines)
    assert again.status_code == 409 and again.get_json()["details"] == {"status_code": "awaiting_spd_review"}
    put = client.put(f"{BASE}/{cdata.manual}/expected-inventory", headers=as_user("spd_a"),
                     json={"lines": [{"family_id": str(world.families["a"]), "expected_quantity": 1}]})
    assert put.status_code == 409 and put.get_json()["details"] == {"status_code": "awaiting_spd_review", "reason": "inventory_locked"}
    assert len(_events(db_conn, cdata.manual, "manual_count")) == 2 and len(_audits(db_conn, cdata.manual)) == 1


@pytest.mark.parametrize("bad_id", ["not-a-uuid", str(uuid.uuid4())])
def test_invalid_or_unknown_session_is_404(client, world, cdata, as_user, bad_id):
    assert _post(client, as_user, bad_id, [report_line(world, "a", 2)]).status_code == 404


# --- JSON/XML --------------------------------------------------------------------------------------------


def test_xml_request_and_response(client, world, cdata, as_user, db_conn):
    _final(db_conn, world, cdata.vision, a=1, b=1)
    nil = ' xsi:nil="true"'
    xml = (
        '<count_report xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><reports>'
        f"<report><family_id>{world.families['a']}</family_id><suggested_quantity>1</suggested_quantity>"
        "<reported_quantity>2</reported_quantity><reason_code" + nil + "/><notes>Under a gauze</notes></report>"
        f"<report><family_id>{world.families['b']}</family_id><suggested_quantity>1</suggested_quantity>"
        "<reported_quantity>1</reported_quantity></report>"
        f"<report><family_id>{world.families['c']}</family_id><suggested_quantity>0</suggested_quantity>"
        "<reported_quantity>2</reported_quantity><reason_code>occluded</reason_code><notes>Two seen</notes></report>"
        "</reports></count_report>"
    )
    response = client.post(_url(cdata.vision), data=xml,
                           headers=as_user("op_a", **{"Content-Type": "application/xml", "Accept": "application/xml"}))
    assert response.status_code == 200, response.get_data(as_text=True)
    root = ET.fromstring(response.data)
    assert root.tag == "count_report_result"
    assert [(c.tag, c.text) for c in root] == [("session_id", str(cdata.vision)), ("status_code", "correction_required"),
                                               ("open_discrepancies", "1"), ("human_corrections", "2")]


def test_xml_suggested_nil_in_manual_session(client, world, cdata, as_user):
    xml = (
        '<count_report xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><reports>'
        f'<report><family_id>{world.families["a"]}</family_id><suggested_quantity xsi:nil="true"/>'
        "<reported_quantity>2</reported_quantity></report>"
        f'<report><family_id>{world.families["b"]}</family_id><reported_quantity>1</reported_quantity></report>'
        "</reports></count_report>"
    )
    response = client.post(_url(cdata.manual), data=xml, headers=as_user("op_a", **{"Content-Type": "application/xml"}))
    assert response.status_code == 200


def test_xml_error_response(client, world, cdata, as_user):
    response = client.post(_url(cdata.manual), data="<count_report><reports/></count_report>",
                           headers=as_user("op_a", **{"Content-Type": "application/xml", "Accept": "application/xml"}))
    root = ET.fromstring(response.data)
    assert response.status_code == 400 and root.tag == "error_response" and root.findtext("correlation_id")


# --- GET integration --------------------------------------------------------------------------------------


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_gets_reflect_the_report(client, world, cdata, as_user, db_conn, accept):
    _final(db_conn, world, cdata.vision, a=2, b=1, c=3)
    assert _post(client, as_user, cdata.vision, [
        report_line(world, "a", 1, suggested=2, reason_code="occluded", notes="One piece missing on the tray"),
        report_line(world, "b", 1, suggested=1), report_line(world, "c", 3, suggested=3)]).status_code == 200
    manual_a = next(r for r in _events(db_conn, cdata.vision, "manual_count") if r["family_id"] == world.families["a"])
    counts = client.get(f"{BASE}/{cdata.vision}/counts", headers=as_user("op_a", Accept=accept))
    discrepancies = client.get(f"{BASE}/{cdata.vision}/discrepancies", headers=as_user("spd_a", Accept=accept))
    if accept == "application/json":
        items = {c["id"]: c for c in counts.get_json()}
        item = items[str(manual_a["id"])]
        assert (item["event_type_code"], item["expected_quantity"], item["detected_quantity"], item["reason_code"]) == (
            "manual_count", 2, 1, "occluded")
        assert item["human_correction"]["justification"] == "One piece missing on the tray"
        assert item["human_correction"]["user_id"] == str(world.users["op_a"].id)
        assert [c["event_type_code"] for c in items.values()].count("manual_close") == 1
        assert "payload" not in item
        [disc] = discrepancies.get_json()
        assert disc == {
            "id": disc["id"], "reason_code": "shortage", "family_id": str(world.families["a"]),
            "family_code": f"zz_ses_{world.tag}_a", "expected_quantity": 2, "detected_quantity": 1,
            "resolved": False, "resolved_at": None, "origin_event_id": str(manual_a["id"]),
            "description": "Shortfall on Family a: expected 2, reported 1.",
        }
    else:
        root = ET.fromstring(counts.data)
        node = next(c for c in root if c.findtext("id") == str(manual_a["id"]))
        assert node.findtext("detected_quantity") == "1" and node.findtext("reason_code") == "occluded"
        assert node.find("human_correction").findtext("justification") == "One piece missing on the tray"
        disc = ET.fromstring(discrepancies.data).find("discrepancy")
        assert disc.findtext("reason_code") == "shortage" and disc.findtext("origin_event_id") == str(manual_a["id"])
    detail = client.get(f"{BASE}/{cdata.vision}", headers=as_user("op_a")).get_json()
    assert detail["status_code"] == "correction_required" and detail["ended_at"] is not None
    assert detail["open_discrepancies"] == 1


# --- Atomicity ---------------------------------------------------------------------------------------------


def _vision_report(world):
    return [report_line(world, "a", 1, suggested=2, reason_code="occluded", notes="missing"),
            report_line(world, "b", 2, suggested=1, reason_code="misclassified", notes="extra"),
            report_line(world, "c", 3, suggested=3)]


def _fail_on(monkeypatch, name, *, call=1, predicate=None):
    from repositories import write_repository

    original = getattr(write_repository, name)
    calls = {"n": 0}

    def failing(connection, values, *args, **kwargs):
        if predicate is None or predicate(values):
            calls["n"] += 1
            if calls["n"] == call:
                raise RuntimeError(f"injected failure in {name}")
        return original(connection, values, *args, **kwargs)
    monkeypatch.setattr(write_repository, name, failing)


@pytest.mark.parametrize("target", ["second_manual_count", "human_correction", "discrepancy", "manual_close", "audit"])
def test_any_failure_rolls_back_everything(client, world, cdata, as_user, db_conn, monkeypatch, target):
    _final(db_conn, world, cdata.vision, a=2, b=1, c=3)
    if target == "second_manual_count":
        _fail_on(monkeypatch, "insert_count_event", call=2)
    elif target == "human_correction":
        _fail_on(monkeypatch, "insert_human_correction")
    elif target == "discrepancy":
        _fail_on(monkeypatch, "insert_discrepancy", call=2)
    elif target == "manual_close":
        _fail_on(monkeypatch, "insert_count_event", predicate=lambda values: values.get("family_id") is None)
    else:
        from adapters import audit_adapter

        monkeypatch.setattr(audit_adapter, "ACTOR_TYPE", "robot")   # violates the access_audit CHECK
    response = _post(client, as_user, cdata.vision, _vision_report(world))
    assert response.status_code == 500
    body = response.get_json()
    assert body["error"] == "internal_error" and "injected" not in response.get_data(as_text=True)
    _assert_untouched(db_conn, cdata.vision)
    assert len(_events(db_conn, cdata.vision, "auto_count")) == 1    # the vision fact is untouched


def test_database_outage_during_report_is_503(client, world, cdata, as_user, db_conn, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from repositories import write_repository

    def broken(*args, **kwargs):
        raise OperationalError("INSERT ...", {}, Exception("server closed the connection"))
    monkeypatch.setattr(write_repository, "insert_discrepancy", broken)
    response = _post(client, as_user, cdata.manual, [report_line(world, "a", 1, reason_code="occluded"),
                                                      report_line(world, "b", 1)])
    assert response.status_code == 503 and "INSERT" not in response.get_data(as_text=True)
    _assert_untouched(db_conn, cdata.manual)


@pytest.mark.parametrize("corr, preserved", [("r" * 64, True), ("r" * 65, False)])
def test_audit_correlation_id_equals_response_header(client, world, cdata, as_user, db_conn, corr, preserved):
    response = _post(client, as_user, cdata.manual, [report_line(world, "a", 2), report_line(world, "b", 1)],
                     **{"X-Correlation-ID": corr})
    answered = response.headers["X-Correlation-ID"]
    assert response.status_code == 200 and (answered == corr) is preserved
    assert [a[1] for a in _audits(db_conn, cdata.manual)] == [answered]
