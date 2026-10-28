"""Resolve with a physical piece — outcomes lost / recovered (Phase E.1, DS09 v0.1.7, DS03 §1.3.9.1).

rdata.cr: correction_required session of tenant A; d1 = family a, d2 = family b (both open).
Pieces are created per test so every lifecycle origin is explicit; siblings of the same family
(and the same global family in tenant B) prove that nothing but the indicated piece changes.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import text

from review_fixtures import add_discrepancy, ds03_snapshot, rdata  # noqa: F401  (fixture)

BASE = "/api/v1/sessions"


def _url(session_id, discrepancy_id):
    return f"{BASE}/{session_id}/discrepancies/{discrepancy_id}/resolve"


def _resolve(client, as_user, rdata, outcome, instrument_id, disc=None, **headers):
    body = {"notes": "Checked tray and room", "outcome": outcome, "instrument_id": str(instrument_id)}
    return client.post(_url(rdata.cr, disc or rdata.d1), json=body, headers=as_user("spd_a", **headers))


def _piece(db_conn, world, rdata, status, *, active=True, inst="A", family="a"):
    piece_id = uuid.uuid4()
    db_conn.execute(text("INSERT INTO instrument (id, family_id, cycle_status_id, institution_id, active) "
                         "VALUES (:id, :f, :s, :i, :a)"),
                    {"id": piece_id, "f": world.families[family], "s": rdata.cycle[status],
                     "i": world.institutions[inst], "a": active})
    return piece_id


def _reserve(db_conn, operation_id, piece_id):
    res_id = uuid.uuid4()
    db_conn.execute(text("INSERT INTO instrument_reservation (id, operation_id, instrument_id, reserved_at) "
                         "VALUES (:id, :o, :p, now() - interval '1 day')"), {"id": res_id, "o": operation_id, "p": piece_id})
    return res_id


def _piece_state(db_conn, piece_id):
    return tuple(db_conn.execute(text(
        "SELECT s.code, i.active FROM instrument i JOIN cat_instrument_cycle_status s ON s.id = i.cycle_status_id "
        "WHERE i.id = :p"), {"p": piece_id}).one())


def _reservation(db_conn, res_id):
    return tuple(db_conn.execute(text("SELECT active, released_at FROM instrument_reservation WHERE id = :r"),
                                 {"r": res_id}).one())


def _cycle_events(db_conn, piece_id):
    return [tuple(r) for r in db_conn.execute(text(
        "SELECT s.code, e.session_id, e.operation_id, e.notes FROM instrument_cycle_event e "
        "JOIN cat_instrument_cycle_status s ON s.id = e.cycle_status_id WHERE e.instrument_id = :p"), {"p": piece_id})]


def _disc(db_conn, disc_id):
    return db_conn.execute(text("SELECT resolved, description FROM discrepancy WHERE id = :d"), {"d": disc_id}).one()


def _audits(db_conn, session_id):
    return [r[0] for r in db_conn.execute(text("SELECT action FROM access_audit WHERE resource_id = :s AND outcome = 'success'"),
                                          {"s": session_id})]


def _assert_no_change(db_conn, rdata, before):
    assert ds03_snapshot(db_conn) == before
    assert _disc(db_conn, rdata.d1)[0] is False and _audits(db_conn, rdata.cr) == []


# --- lost ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("origin", ["available", "reserved", "in_use"])
def test_lost_from_each_allowed_origin(client, world, rdata, as_user, db_conn, origin):
    piece = _piece(db_conn, world, rdata, origin)
    sibling = _piece(db_conn, world, rdata, origin)              # same family, same tenant, same operation
    own_res, sibling_res = _reserve(db_conn, rdata.cr_op, piece), _reserve(db_conn, rdata.cr_op, sibling)
    before = ds03_snapshot(db_conn)
    response = _resolve(client, as_user, rdata, "lost", piece, **{"X-Correlation-ID": "corr-lost-1"})
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json() == {"discrepancy_id": str(rdata.d1), "session_id": str(rdata.cr),
                                   "open_remaining": 1, "session_status_code": "correction_required"}
    assert _piece_state(db_conn, piece) == ("lost", False)
    active, released_at = _reservation(db_conn, own_res)
    assert active is False and released_at is not None
    assert _cycle_events(db_conn, piece) == [("lost", rdata.cr, rdata.cr_op, "SPD discrepancy resolution: lost")]
    resolved, description = _disc(db_conn, rdata.d1)
    assert resolved is True and description.endswith("\nSPD resolution (marked lost): Checked tray and room")
    assert _audits(db_conn, rdata.cr) == ["discrepancy.resolve"]
    # Nothing else changed: the sibling, its reservation and every other piece/reservation.
    after = ds03_snapshot(db_conn)
    assert _piece_state(db_conn, sibling) == (origin, True) and _reservation(db_conn, sibling_res) == before["reservation"][sibling_res]
    changed = {k for k in after["instrument"] if after["instrument"][k] != before["instrument"].get(k)}
    assert changed == {piece}
    assert {k for k in after["reservation"] if after["reservation"][k] != before["reservation"][k]} == {own_res}
    assert after["cycle_events"] == before["cycle_events"] + 1


def test_lost_without_reservation(client, world, rdata, as_user, db_conn):
    piece = _piece(db_conn, world, rdata, "available")
    before = ds03_snapshot(db_conn)
    assert _resolve(client, as_user, rdata, "lost", piece).status_code == 200
    after = ds03_snapshot(db_conn)
    assert after["reservation"] == before["reservation"] and _piece_state(db_conn, piece) == ("lost", False)


def test_lost_releases_the_piece_reservation_of_another_operation_of_the_tenant(client, world, rdata, as_user, db_conn):
    """A lost piece cannot serve any reservation of the institution, whichever operation holds it."""
    piece = _piece(db_conn, world, rdata, "reserved")
    res = _reserve(db_conn, rdata.other_op, piece)
    assert _resolve(client, as_user, rdata, "lost", piece).status_code == 200
    assert _reservation(db_conn, res)[0] is False


def test_lost_never_releases_a_reservation_held_by_another_institution(client, world, rdata, as_user, db_conn):
    piece = _piece(db_conn, world, rdata, "reserved")
    foreign = _reserve(db_conn, rdata.op_b, piece)           # anomalous data: tenant B operation holds A's piece
    before = _reservation(db_conn, foreign)
    assert _resolve(client, as_user, rdata, "lost", piece).status_code == 200
    assert _reservation(db_conn, foreign) == before


@pytest.mark.parametrize("origin, active", [("lost", False), ("maintenance", True), ("sterilization", True),
                                            ("retired", False), ("available", False), ("in_use", False)])
def test_lost_from_a_disallowed_origin_is_409(client, world, rdata, as_user, db_conn, origin, active):
    piece = _piece(db_conn, world, rdata, origin, active=active)
    _reserve(db_conn, rdata.cr_op, piece)
    before = ds03_snapshot(db_conn)
    response = _resolve(client, as_user, rdata, "lost", piece)
    assert response.status_code == 409
    assert response.get_json()["details"] == {"cycle_status_code": origin, "active": active}
    _assert_no_change(db_conn, rdata, before)


# --- recovered ---------------------------------------------------------------------------------------


def test_recovered_lost_piece(client, world, rdata, as_user, db_conn):
    piece = _piece(db_conn, world, rdata, "lost", active=False)
    sibling = _piece(db_conn, world, rdata, "lost", active=False)
    before = ds03_snapshot(db_conn)
    response = _resolve(client, as_user, rdata, "recovered", piece)
    assert response.status_code == 200, response.get_data(as_text=True)
    assert _piece_state(db_conn, piece) == ("available", True)
    assert _cycle_events(db_conn, piece) == [("available", rdata.cr, rdata.cr_op, "SPD discrepancy resolution: recovered")]
    after = ds03_snapshot(db_conn)
    assert after["reservation"] == before["reservation"]                     # no reservation created or changed
    assert _piece_state(db_conn, sibling) == ("lost", False)
    assert {k for k in after["instrument"] if after["instrument"][k] != before["instrument"].get(k)} == {piece}
    assert after["cycle_events"] == before["cycle_events"] + 1
    assert _disc(db_conn, rdata.d1)[0] is True and _audits(db_conn, rdata.cr) == ["discrepancy.resolve"]


def test_recovered_piece_counts_as_available_stock(client, world, rdata, as_user, db_conn):
    """The recovered piece is usable inventory again (the stock check counts active + available)."""
    from repositories import write_repository

    piece = _piece(db_conn, world, rdata, "lost", active=False)
    stock = write_repository.available_stock(db_conn, [world.families["a"]], world.institutions["A"])
    assert _resolve(client, as_user, rdata, "recovered", piece).status_code == 200
    after = write_repository.available_stock(db_conn, [world.families["a"]], world.institutions["A"])
    assert after.get(world.families["a"], 0) == stock.get(world.families["a"], 0) + 1


@pytest.mark.parametrize("origin, active", [("available", True), ("in_use", True), ("reserved", True),
                                            ("maintenance", True), ("sterilization", True), ("retired", False)])
def test_recovered_from_a_non_lost_piece_is_409(client, world, rdata, as_user, db_conn, origin, active):
    piece = _piece(db_conn, world, rdata, origin, active=active)
    before = ds03_snapshot(db_conn)
    response = _resolve(client, as_user, rdata, "recovered", piece)
    assert response.status_code == 409
    assert response.get_json()["details"] == {"cycle_status_code": origin, "active": active}
    _assert_no_change(db_conn, rdata, before)


# --- piece validation / cross-tenant ------------------------------------------------------------------


@pytest.mark.parametrize("outcome, case", [
    ("lost", "tenant_b_same_family"), ("recovered", "tenant_b_lost_same_family"),
    ("lost", "other_family"), ("recovered", "other_family_lost"), ("lost", "unknown"), ("recovered", "unknown"),
])
def test_invalid_piece_is_422_and_changes_nothing(client, world, rdata, as_user, db_conn, outcome, case):
    piece = {
        "tenant_b_same_family": lambda: _piece(db_conn, world, rdata, "available", inst="B"),
        "tenant_b_lost_same_family": lambda: _piece(db_conn, world, rdata, "lost", active=False, inst="B"),
        "other_family": lambda: _piece(db_conn, world, rdata, "available", family="b"),
        "other_family_lost": lambda: _piece(db_conn, world, rdata, "lost", active=False, family="b"),
        "unknown": lambda: uuid.uuid4(),
    }[case]()
    before = ds03_snapshot(db_conn)
    response = _resolve(client, as_user, rdata, outcome, piece)
    assert response.status_code == 422
    body = response.get_json()
    assert body["error"] == "invalid_reference" and body["details"] == {"field": "instrument_id"}
    assert body["message"] == "Instrument is not valid for this discrepancy"      # same answer: nothing revealed
    _assert_no_change(db_conn, rdata, before)


def test_discrepancy_without_family_cannot_name_a_piece(client, world, rdata, as_user, db_conn):
    disc = add_discrepancy(db_conn, rdata.cr, None)
    piece = _piece(db_conn, world, rdata, "available")
    response = _resolve(client, as_user, rdata, "lost", piece, disc=disc)
    assert response.status_code == 422 and _piece_state(db_conn, piece) == ("available", True)


# --- second resolve / last open / rollback / XML ---------------------------------------------------------


def test_second_resolve_changes_nothing(client, world, rdata, as_user, db_conn):
    piece, other = _piece(db_conn, world, rdata, "available"), _piece(db_conn, world, rdata, "available")
    assert _resolve(client, as_user, rdata, "lost", piece).status_code == 200
    after_first = ds03_snapshot(db_conn)
    again = _resolve(client, as_user, rdata, "lost", other)
    assert again.status_code == 409 and again.get_json()["details"] == {"discrepancy_id": str(rdata.d1), "reason": "already_resolved"}
    assert ds03_snapshot(db_conn) == after_first and len(_cycle_events(db_conn, piece)) == 1
    assert _piece_state(db_conn, other) == ("available", True)


def test_lost_on_the_last_open_discrepancy_moves_to_spd_review(client, world, rdata, as_user, db_conn):
    client.post(_url(rdata.cr, rdata.d2), json={"notes": "ok", "outcome": "resolved"}, headers=as_user("spd_a"))
    response = _resolve(client, as_user, rdata, "lost", _piece(db_conn, world, rdata, "in_use"))
    assert response.status_code == 200 and response.get_json()["session_status_code"] == "awaiting_spd_review"


@pytest.mark.parametrize("target", ["release_reservation", "update_instrument_cycle", "insert_cycle_event",
                                    "update_discrepancy", "audit"])
def test_lost_failure_rolls_back_everything(client, world, rdata, as_user, db_conn, monkeypatch, target):
    from adapters import audit_adapter
    from repositories import write_repository

    piece = _piece(db_conn, world, rdata, "reserved")
    _reserve(db_conn, rdata.cr_op, piece)
    before = ds03_snapshot(db_conn)
    if target == "audit":
        monkeypatch.setattr(audit_adapter, "ACTOR_TYPE", "robot")
    else:
        def boom(*args, **kwargs):
            raise RuntimeError(f"injected failure in {target}")
        monkeypatch.setattr(write_repository, target, boom)
    response = _resolve(client, as_user, rdata, "lost", piece)
    assert response.status_code == 500 and "injected" not in response.get_data(as_text=True)
    _assert_no_change(db_conn, rdata, before)


def test_recovered_failure_rolls_back_everything(client, world, rdata, as_user, db_conn, monkeypatch):
    from repositories import write_repository

    piece = _piece(db_conn, world, rdata, "lost", active=False)
    before = ds03_snapshot(db_conn)

    def boom(*args, **kwargs):
        raise RuntimeError("injected failure")
    monkeypatch.setattr(write_repository, "insert_cycle_event", boom)
    assert _resolve(client, as_user, rdata, "recovered", piece).status_code == 500
    _assert_no_change(db_conn, rdata, before)


@pytest.mark.parametrize("outcome, origin, active", [("lost", "available", True), ("recovered", "lost", False)])
def test_xml_request(client, world, rdata, as_user, db_conn, outcome, origin, active):
    piece = _piece(db_conn, world, rdata, origin, active=active)
    xml = (f"<resolve_request><notes>Checked</notes><outcome>{outcome}</outcome>"
           f"<instrument_id>{piece}</instrument_id></resolve_request>")
    response = client.post(_url(rdata.cr, rdata.d1), data=xml,
                           headers=as_user("spd_a", **{"Content-Type": "application/xml", "Accept": "application/xml"}))
    assert response.status_code == 200, response.get_data(as_text=True)
    assert ET.fromstring(response.data).tag == "resolve_result"
    assert _piece_state(db_conn, piece) == (("lost", False) if outcome == "lost" else ("available", True))
