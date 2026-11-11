"""Phase D alignment: count-report `notes` rule, GET /counts order on equal timestamps, no audit per vision batch."""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import text

from count_fixtures import T0, add_auto_count, cdata, report_line, vision_body  # noqa: F401  (fixture)

BASE = "/api/v1/sessions"
XSI = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'


def _report_url(session_id):
    return f"{BASE}/{session_id}/count-report"


def _xml_line(line):
    parts = [f"<family_id>{line['family_id']}</family_id>"]
    for field in ("suggested_quantity", "reported_quantity", "reason_code", "notes"):
        value = line.get(field)
        parts.append(f'<{field} xsi:nil="true"/>' if value is None else f"<{field}>{value}</{field}>")
    return "<report>" + "".join(parts) + "</report>"


def _send(client, as_user, session_id, lines, fmt):
    if fmt == "json":
        return client.post(_report_url(session_id), json={"reports": lines}, headers=as_user("op_a"))
    xml = f"<count_report {XSI}><reports>" + "".join(_xml_line(line) for line in lines) + "</reports></count_report>"
    return client.post(_report_url(session_id), data=xml,
                       headers=as_user("op_a", **{"Content-Type": "application/xml", "Accept": "application/xml"}))


def _error_field(response, fmt):
    if fmt == "json":
        return response.get_json()["details"].get("field")
    return ET.fromstring(response.data).find("details").findtext("field")


def _justifications(db_conn, session_id):
    return [r[0] for r in db_conn.execute(text(
        "SELECT h.justification FROM human_correction h JOIN count_event e ON e.id = h.count_event_id "
        "WHERE e.session_id = :s"), {"s": session_id})]


def _manual_payloads(db_conn, session_id):
    return [r[0] for r in db_conn.execute(text(
        "SELECT e.payload FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
        "WHERE e.session_id = :s AND t.code = 'manual_count'"), {"s": session_id})]


def _status(db_conn, session_id):
    return db_conn.execute(text("SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id "
                                "WHERE w.id = :s"), {"s": session_id}).scalar_one()


def _final(db_conn, world, session_id, **quantities):
    add_auto_count(db_conn, session_id, "final", [(world.families[s], q) for s, q in quantities.items()], minute=10)


# --- notes ------------------------------------------------------------------------------------------

FORMATS = pytest.mark.parametrize("fmt", ["json", "xml"])


@FORMATS
def test_correction_with_notes_is_stored_as_justification(client, world, cdata, as_user, db_conn, fmt):
    _final(db_conn, world, cdata.vision, a=1, b=1, c=3)
    response = _send(client, as_user, cdata.vision, [
        report_line(world, "a", 2, suggested=1, notes="Second piece under a gauze"),
        report_line(world, "b", 1, suggested=1), report_line(world, "c", 3, suggested=3)], fmt)
    assert response.status_code == 200, response.get_data(as_text=True)
    assert _justifications(db_conn, cdata.vision) == ["Second piece under a gauze"]
    assert all("notes" not in payload for payload in _manual_payloads(db_conn, cdata.vision))


@FORMATS
@pytest.mark.parametrize("notes", [None, "   "])
def test_correction_without_valid_notes_is_400(client, world, cdata, as_user, db_conn, fmt, notes):
    _final(db_conn, world, cdata.vision, a=1, b=1, c=3)
    response = _send(client, as_user, cdata.vision, [
        report_line(world, "a", 2, suggested=1, notes=notes),
        report_line(world, "b", 1, suggested=1), report_line(world, "c", 3, suggested=3)], fmt)
    assert response.status_code == 400 and _error_field(response, fmt) == "notes"
    assert _status(db_conn, cdata.vision) == "in_progress" and _justifications(db_conn, cdata.vision) == []


@FORMATS
def test_no_correction_with_null_notes_is_ok(client, world, cdata, as_user, db_conn, fmt):
    _final(db_conn, world, cdata.vision, a=2, b=1, c=3)
    response = _send(client, as_user, cdata.vision, [
        report_line(world, "a", 2, suggested=2), report_line(world, "b", 1, suggested=1),
        report_line(world, "c", 3, suggested=3)], fmt)
    assert response.status_code == 200 and _justifications(db_conn, cdata.vision) == []


@FORMATS
@pytest.mark.parametrize("notes", ["Looks fine", "   "])
def test_no_correction_with_notes_is_400(client, world, cdata, as_user, db_conn, fmt, notes):
    """AI = human: there is no correction, so notes would be discarded → rejected."""
    _final(db_conn, world, cdata.vision, a=2, b=1, c=3)
    response = _send(client, as_user, cdata.vision, [
        report_line(world, "a", 2, suggested=2, notes=notes), report_line(world, "b", 1, suggested=1),
        report_line(world, "c", 3, suggested=3)], fmt)
    assert response.status_code == 400 and _error_field(response, fmt) == "notes"
    assert _status(db_conn, cdata.vision) == "in_progress"


@FORMATS
def test_vision_without_final_rejects_notes(client, world, cdata, as_user, db_conn, fmt):
    response = _send(client, as_user, cdata.vision, [
        report_line(world, "a", 1, reason_code="occluded", notes="Only one seen"),
        report_line(world, "b", 1), report_line(world, "c", 3)], fmt)
    assert response.status_code == 400 and _error_field(response, fmt) == "notes"


@FORMATS
def test_manual_session_rejects_notes(client, world, cdata, as_user, db_conn, fmt):
    response = _send(client, as_user, cdata.manual, [
        report_line(world, "a", 2, notes="All good"), report_line(world, "b", 1)], fmt)
    assert response.status_code == 400 and _error_field(response, fmt) == "notes"
    assert _status(db_conn, cdata.manual) == "in_progress"


@FORMATS
def test_discrepancy_without_correction_uses_reason_code_and_null_notes(client, world, cdata, as_user, db_conn, fmt):
    response = _send(client, as_user, cdata.manual, [
        report_line(world, "a", 1, reason_code="occluded"), report_line(world, "b", 1)], fmt)
    assert response.status_code == 200, response.get_data(as_text=True)
    assert _justifications(db_conn, cdata.manual) == []
    reasons = db_conn.execute(text(
        "SELECT r.code FROM discrepancy d JOIN cat_discrepancy_reason r ON r.id = d.reason_id WHERE d.session_id = :s"),
        {"s": cdata.manual}).scalars().all()
    assert reasons == ["shortage"]
    assert sorted(map(str, _manual_payloads(db_conn, cdata.manual))) == sorted(["{}", "{'reason_code': 'occluded'}"])


# --- GET /counts order on equal timestamps ---------------------------------------------------------


def _insert_event(db_conn, event_id, session_id, code, *, family=None, user=None, detected=None):
    db_conn.execute(text(
        "INSERT INTO count_event (id, event_type_id, family_id, expected_quantity, detected_quantity, occurred_at, "
        "session_id, user_id) VALUES (:id, (SELECT id FROM cat_event_type WHERE code = :c), :f, :e, :d, :o, :s, :u)"),
        {"id": event_id, "c": code, "f": family, "e": 2 if family else None, "d": detected, "o": T0,
         "s": session_id, "u": user})


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_same_timestamp_orders_manual_counts_before_manual_close(client, world, cdata, as_user, db_conn, accept):
    """Ids are chosen so that id order alone would put manual_close first and session_open last."""
    s, op = cdata.manual, world.users["op_a"].id
    close_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
    count_ids = [uuid.UUID("ffffffff-0000-4000-8000-000000000002"), uuid.UUID("ffffffff-0000-4000-8000-000000000001")]
    open_id = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
    _insert_event(db_conn, close_id, s, "manual_close", user=op)
    _insert_event(db_conn, count_ids[0], s, "manual_count", family=world.families["a"], user=op, detected=2)
    _insert_event(db_conn, count_ids[1], s, "manual_count", family=world.families["b"], user=op, detected=1)
    _insert_event(db_conn, open_id, s, "session_open", user=op)
    expected = [("session_open", str(open_id)), ("manual_count", str(count_ids[1])),
                ("manual_count", str(count_ids[0])), ("manual_close", str(close_id))]
    for _ in range(2):   # deterministic across calls
        response = client.get(f"{BASE}/{s}/counts", headers=as_user("op_a", Accept=accept))
        assert response.status_code == 200
        if accept == "application/json":
            got = [(c["event_type_code"], c["id"]) for c in response.get_json()]
        else:
            got = [(c.findtext("event_type_code"), c.findtext("id")) for c in ET.fromstring(response.data)]
        assert got == expected


def test_report_events_are_listed_counts_then_close(client, world, cdata, as_user):
    """End to end: the report writes all events with one instant; GET lists manual_count × 2, manual_close."""
    assert client.post(_report_url(cdata.manual), headers=as_user("op_a"), json={"reports": [
        report_line(world, "a", 2), report_line(world, "b", 1)]}).status_code == 200
    counts = client.get(f"{BASE}/{cdata.manual}/counts", headers=as_user("op_a")).get_json()
    assert [c["event_type_code"] for c in counts] == ["manual_count", "manual_count", "manual_close"]
    assert len({c["occurred_at"] for c in counts}) == 1


# --- vision-counts: still no access_audit ----------------------------------------------------------------


def test_vision_batches_never_write_access_audit(client, world, cdata, as_user, db_conn):
    for trigger in ("stable_window", "stable_window", "final"):
        body = vision_body(world, ("a", 1), trigger=trigger)
        assert client.post(f"{BASE}/{cdata.vision}/vision-counts", json=body, headers=as_user("op_a")).status_code == 201
    audits = db_conn.execute(text("SELECT count(*) FROM access_audit WHERE resource_id = :s"),
                             {"s": cdata.vision}).scalar_one()
    assert audits == 0
