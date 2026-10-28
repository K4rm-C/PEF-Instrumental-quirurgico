"""A physically missing instrument can never end in a safe close (real PostgreSQL).

Physical shortage = discrepancy with reason `shortage` (human-confirmed count below the expected quantity
or the reference count). Classification errors the human corrected never create a discrepancy; a surplus
(`surplus`) is an extra piece and keeps the note resolution.

Rules:
1. a note (`resolved`) cannot resolve a physical shortage -> 409, nothing changes;
2. `lost` records it (the piece leaves the stock, traceable), the incident stays unreconciled;
3. the close of a session with a physical shortage requires the explicit acknowledgment and is recorded
   safe_close_confirmed = false with the shortage ids; without it -> 409, nothing changes;
4. no shortage -> safe close as before (no acknowledgment needed);
5. the Operator still finishes and the case goes to the Supervisor (correction_required -> review).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from review_fixtures import add_discrepancy, rdata  # noqa: F401  (fixture)

BASE = "/api/v1/sessions"


def resolve(client, as_user, session_id, discrepancy_id, outcome, **extra):
    return client.post(f"{BASE}/{session_id}/discrepancies/{discrepancy_id}/resolve",
                       json={"notes": "Checked the tray and the room", "outcome": outcome, **extra}, headers=as_user("spd_a"))


def close(client, as_user, session_id, **body):
    return client.post(f"{BASE}/{session_id}/close", json={"material_recovered": True, **body}, headers=as_user("spd_a"))


def state(db_conn, session_id):
    return db_conn.execute(text("SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id "
                                "WHERE w.id = :s"), {"s": session_id}).scalar_one()


def close_payload(db_conn, session_id):
    return db_conn.execute(text("SELECT e.payload FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
                                "WHERE e.session_id = :s AND t.code = 'session_close'"), {"s": session_id}).scalar_one()


@pytest.fixture
def shortage(rdata, world, db_conn):
    """A session the Operator finished with one physically missing instrument (correction_required)."""
    rdata.short = add_discrepancy(db_conn, rdata.aw, world.families["a"], reason="shortage",
                                  description="Shortfall on Family a: expected 2, reported 1.")
    db_conn.execute(text("UPDATE work_session SET status_id = (SELECT id FROM cat_session_status "
                         "WHERE code = 'correction_required') WHERE id = :s"), {"s": rdata.aw})
    return rdata


def test_a_note_cannot_resolve_a_physical_shortage(client, shortage, as_user, db_conn):
    response = resolve(client, as_user, shortage.aw, shortage.short, "resolved")
    body = response.get_json()
    assert response.status_code == 409 and body["details"]["reason"] == "physical_shortage_needs_piece_outcome"
    assert body["details"]["allowed"] == ["lost", "recovered"]
    row = db_conn.execute(text("SELECT resolved, description FROM discrepancy WHERE id = :d"), {"d": shortage.short}).one()
    assert row[0] is False and "SPD resolution" not in row[1]                    # nothing written
    assert state(db_conn, shortage.aw) == "correction_required"


def test_a_note_still_resolves_a_surplus(client, rdata, as_user):
    assert resolve(client, as_user, rdata.cr, rdata.d2, "resolved").status_code == 200   # extra piece, not missing


def test_lost_records_the_incident_and_keeps_it_traceable(client, shortage, as_user, db_conn):
    response = resolve(client, as_user, shortage.aw, shortage.short, "lost")
    assert response.status_code == 200 and response.get_json()["session_status_code"] == "awaiting_spd_review"
    description = db_conn.execute(text("SELECT description FROM discrepancy WHERE id = :d"), {"d": shortage.short}).scalar_one()
    assert "SPD resolution (marked lost): Checked the tray and the room" in description
    audit = db_conn.execute(text("SELECT count(*) FROM access_audit WHERE resource_id = :s AND action = 'discrepancy.resolve'"),
                            {"s": shortage.aw}).scalar_one()
    assert audit == 1


def test_lost_without_a_piece_in_service_says_so(client, shortage, as_user, db_conn, world):
    db_conn.execute(text("UPDATE instrument SET active = FALSE WHERE family_id = :f"), {"f": world.families["a"]})
    assert resolve(client, as_user, shortage.aw, shortage.short, "lost").status_code == 200
    description = db_conn.execute(text("SELECT description FROM discrepancy WHERE id = :d"), {"d": shortage.short}).scalar_one()
    assert description.endswith("Note: no piece of this family in service was found to mark lost.")


def test_close_with_a_physical_shortage_is_never_a_safe_close(client, shortage, as_user, db_conn):
    assert resolve(client, as_user, shortage.aw, shortage.short, "lost").status_code == 200
    # A generic confirmation (material recovered, contingency reviewed) is not enough.
    blocked = close(client, as_user, shortage.aw, contingency_reviewed=True)
    details = blocked.get_json()["details"]
    assert blocked.status_code == 409 and details["reason"] == "unreconciled_physical_shortage"
    assert details["discrepancy_ids"] == [str(shortage.short)] and state(db_conn, shortage.aw) == "awaiting_spd_review"
    ok = close(client, as_user, shortage.aw, unreconciled_shortage_acknowledged=True)
    assert ok.status_code == 200 and ok.get_json()["status_code"] == "closed"
    payload = close_payload(db_conn, shortage.aw)
    assert payload["safe_close_confirmed"] is False
    assert payload["unreconciled_physical_shortages"] == [str(shortage.short)]


def test_recovered_does_not_make_the_shortage_safe_either(client, shortage, as_user, db_conn):
    assert resolve(client, as_user, shortage.aw, shortage.short, "recovered").status_code == 200
    assert close(client, as_user, shortage.aw).status_code == 409
    assert close(client, as_user, shortage.aw, unreconciled_shortage_acknowledged=True).status_code == 200
    assert close_payload(db_conn, shortage.aw)["safe_close_confirmed"] is False


def test_an_open_shortage_blocks_the_close_even_with_the_acknowledgment(client, shortage, as_user, db_conn):
    db_conn.execute(text("UPDATE work_session SET status_id = (SELECT id FROM cat_session_status "
                         "WHERE code = 'awaiting_spd_review') WHERE id = :s"), {"s": shortage.aw})
    response = close(client, as_user, shortage.aw, unreconciled_shortage_acknowledged=True)
    assert response.status_code == 409 and response.get_json()["details"]["reason"] == "open_discrepancies"


def test_without_shortage_the_safe_close_needs_no_acknowledgment(client, rdata, as_user, db_conn):
    assert close(client, as_user, rdata.aw).status_code == 200
    payload = close_payload(db_conn, rdata.aw)
    assert payload["safe_close_confirmed"] is True and payload["unreconciled_physical_shortages"] == []


@pytest.mark.parametrize("value", ["yes", 1, "true"])
def test_acknowledgment_must_be_a_boolean(client, shortage, as_user, value):
    response = close(client, as_user, shortage.aw, unreconciled_shortage_acknowledged=value)
    assert response.status_code == 400 and response.get_json()["details"]["field"] == "unreconciled_shortage_acknowledged"


def test_xml_acknowledgment(client, shortage, as_user, db_conn):
    assert resolve(client, as_user, shortage.aw, shortage.short, "lost").status_code == 200
    xml = (b"<close_request><material_recovered>true</material_recovered>"
           b"<unreconciled_shortage_acknowledged>true</unreconciled_shortage_acknowledged></close_request>")
    response = client.post(f"{BASE}/{shortage.aw}/close", data=xml,
                           headers={**as_user("spd_a"), "Content-Type": "application/xml", "Accept": "application/xml"})
    assert response.status_code == 200 and close_payload(db_conn, shortage.aw)["safe_close_confirmed"] is False


@pytest.mark.parametrize("outcome", ["resolved", "lost", "recovered"])
def test_no_administrative_decision_sets_safe_close_true(client, shortage, as_user, db_conn, outcome):
    """Every outcome on a confirmed physical shortage: never safe_close_confirmed = true.

    `resolved` is refused; `lost` / `recovered` resolve it but are not evidence of THIS piece being found.
    All the generic confirmations together (material recovered, contingency reviewed) are not enough either.
    """
    resolution = resolve(client, as_user, shortage.aw, shortage.short, outcome)
    if outcome == "resolved":
        assert resolution.status_code == 409
        resolve(client, as_user, shortage.aw, shortage.short, "lost")           # the only way forward
    else:
        assert resolution.status_code == 200
    generic = close(client, as_user, shortage.aw, contingency_reviewed=True)
    assert generic.status_code == 409 and generic.get_json()["details"]["reason"] == "unreconciled_physical_shortage"
    assert close(client, as_user, shortage.aw, contingency_reviewed=True,
                 unreconciled_shortage_acknowledged=True).status_code == 200
    payload = close_payload(db_conn, shortage.aw)
    assert payload["safe_close_confirmed"] is False and payload["unreconciled_physical_shortages"] == [str(shortage.short)]
    audit = db_conn.execute(text("SELECT count(*) FROM access_audit WHERE resource_id = :s AND action = 'session.spd_close'"),
                            {"s": shortage.aw}).scalar_one()
    assert audit == 1                                                           # the decision stays audited
