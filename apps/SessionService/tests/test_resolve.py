"""POST /api/v1/sessions/{id}/discrepancies/{discrepancy_id}/resolve (Phase E)."""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import text

from count_fixtures import set_status
from review_fixtures import ds03_snapshot, rdata  # noqa: F401  (fixture)

BASE = "/api/v1/sessions"


def _url(session_id, discrepancy_id):
    return f"{BASE}/{session_id}/discrepancies/{discrepancy_id}/resolve"


def _post(client, as_user, session_id, discrepancy_id, body=None, user="spd_a", **headers):
    body = {"notes": "Found in the auxiliary tray", "outcome": "resolved"} if body is None else body
    return client.post(_url(session_id, discrepancy_id), json=body, headers=as_user(user, **headers))


def _disc(db_conn, disc_id):
    return db_conn.execute(text(
        "SELECT d.resolved, d.resolved_at, d.description, d.family_id, d.expected_quantity, d.detected_quantity, "
        "d.origin_event_id, r.code AS reason, d.updated_at FROM discrepancy d "
        "LEFT JOIN cat_discrepancy_reason r ON r.id = d.reason_id WHERE d.id = :d"), {"d": disc_id}).mappings().one()


def _status(db_conn, session_id):
    return db_conn.execute(text("SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id "
                                "WHERE w.id = :s"), {"s": session_id}).scalar_one()


def _audits(db_conn, session_id):
    return [tuple(r) for r in db_conn.execute(text(
        "SELECT action, correlation_id, actor_user_id, resource_type, institution_id FROM access_audit "
        "WHERE resource_id = :s AND outcome = 'success'"), {"s": session_id})]


def _events(db_conn, session_id):
    return db_conn.execute(text("SELECT count(*) FROM count_event WHERE session_id = :s"), {"s": session_id}).scalar_one()


def _assert_nothing_changed(db_conn, rdata, before, disc_id=None):
    assert ds03_snapshot(db_conn) == before
    assert _status(db_conn, rdata.cr) == "correction_required" and _audits(db_conn, rdata.cr) == []
    if disc_id:
        assert _disc(db_conn, disc_id)["resolved"] is False


# --- Happy paths ----------------------------------------------------------------------------------


def test_resolve_keeps_session_open_while_others_remain(client, world, rdata, as_user, db_conn):
    before_ds03, before_disc, events = ds03_snapshot(db_conn), _disc(db_conn, rdata.d1), _events(db_conn, rdata.cr)
    response = _post(client, as_user, rdata.cr, rdata.d1, **{"X-Correlation-ID": "corr-resolve-1"})
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json() == {"discrepancy_id": str(rdata.d1), "session_id": str(rdata.cr),
                                   "open_remaining": 1, "session_status_code": "correction_required"}
    after = _disc(db_conn, rdata.d1)
    assert after["resolved"] is True and after["resolved_at"] is not None
    # Historical facts are preserved; the untranslated notes are appended (DS04 §1.3.7: no notes column).
    for field in ("family_id", "expected_quantity", "detected_quantity", "origin_event_id", "reason"):
        assert after[field] == before_disc[field]
    assert after["description"] == before_disc["description"] + "\nSPD resolution (resolved): Found in the auxiliary tray"
    assert after["updated_at"] == after["resolved_at"]
    assert _status(db_conn, rdata.cr) == "correction_required"
    assert ds03_snapshot(db_conn) == before_ds03                       # outcome resolved: no physical side effect
    assert _events(db_conn, rdata.cr) == events                        # no correction_applied count_event
    assert _audits(db_conn, rdata.cr) == [("discrepancy.resolve", "corr-resolve-1", world.users["spd_a"].id,
                                           "work_session", world.institutions["A"])]
    assert _disc(db_conn, rdata.d2)["resolved"] is False


def test_last_open_discrepancy_moves_to_spd_review(client, world, rdata, as_user, db_conn):
    assert _post(client, as_user, rdata.cr, rdata.d1).get_json()["open_remaining"] == 1
    response = _post(client, as_user, rdata.cr, rdata.d2)
    assert response.status_code == 200
    assert response.get_json()["open_remaining"] == 0
    assert response.get_json()["session_status_code"] == "awaiting_spd_review"
    assert _status(db_conn, rdata.cr) == "awaiting_spd_review"          # not closed automatically
    assert [a[0] for a in _audits(db_conn, rdata.cr)] == ["discrepancy.resolve"] * 2


def test_notes_are_trimmed_and_untranslated(client, world, rdata, as_user, db_conn):
    _post(client, as_user, rdata.cr, rdata.d1, {"notes": "  Encontrada en la charola auxiliar  ", "outcome": "resolved"})
    assert _disc(db_conn, rdata.d1)["description"].endswith("\nSPD resolution (resolved): Encontrada en la charola auxiliar")


def test_multi_role_supervisor_can_resolve(client, world, rdata, as_user):
    assert _post(client, as_user, rdata.cr, rdata.d1, user="multi_a").status_code == 200


# --- Access, ids and state --------------------------------------------------------------------------


@pytest.mark.parametrize("user_key, status", [("op_a", 403), ("admin_a", 403), ("spd_b", 404), ("op_admin_a", 403)])
def test_access_rules(client, world, rdata, as_user, db_conn, user_key, status):
    before = ds03_snapshot(db_conn)
    assert _post(client, as_user, rdata.cr, rdata.d1, user=user_key).status_code == status
    _assert_nothing_changed(db_conn, rdata, before, rdata.d1)


def test_discrepancy_of_another_session_is_404(client, world, rdata, as_user, db_conn):
    """world a3 is another correction_required session of the same tenant."""
    response = _post(client, as_user, rdata.cr, world.discrepancies["open_a"])
    assert response.status_code == 404
    assert _post(client, as_user, world.sessions["a3"].id, rdata.d1).status_code == 404
    assert _disc(db_conn, rdata.d1)["resolved"] is False


@pytest.mark.parametrize("session_key, disc_key", [("cr", "unknown"), ("cr", "bad"), ("bad", "d1"), ("unknown", "d1")])
def test_unknown_or_malformed_ids_are_404(client, world, rdata, as_user, session_key, disc_key):
    ids = {"cr": rdata.cr, "d1": rdata.d1, "unknown": uuid.uuid4(), "bad": "not-a-uuid"}
    assert _post(client, as_user, ids[session_key], ids[disc_key]).status_code == 404


def test_cross_tenant_session_is_404(client, world, rdata, as_user):
    assert _post(client, as_user, world.sessions["b1"].id, world.discrepancies["b_open"]).status_code == 404


def test_already_resolved_is_409(client, world, rdata, as_user, db_conn):
    response = _post(client, as_user, rdata.cr, rdata.d3)
    assert response.status_code == 409 and response.get_json()["details"] == {"discrepancy_id": str(rdata.d3), "reason": "already_resolved"}
    assert _audits(db_conn, rdata.cr) == []


# awaiting_spd_review is reviewable too (complement v0.2.0): see test_supervisor_flow.py.
@pytest.mark.parametrize("status", ["scheduled", "in_progress", "closed", "aborted"])
def test_wrong_session_status_is_409(client, world, rdata, as_user, db_conn, status):
    set_status(db_conn, rdata.cr, status)
    response = _post(client, as_user, rdata.cr, rdata.d1)
    assert response.status_code == 409 and response.get_json()["details"] == {"status_code": status, "reason": "session_not_reviewable"}
    assert _disc(db_conn, rdata.d1)["resolved"] is False and _audits(db_conn, rdata.cr) == []


# --- Request validation ------------------------------------------------------------------------------


@pytest.mark.parametrize("body", [
    {"outcome": "resolved"},                                     # notes missing
    {"notes": None, "outcome": "resolved"},
    {"notes": "", "outcome": "resolved"},
    {"notes": "   ", "outcome": "resolved"},
    {"notes": 5, "outcome": "resolved"},
    {"notes": "x"},                                              # outcome missing
    {"notes": "x", "outcome": "fixed"},                          # unknown outcome
    {"notes": "x", "outcome": "RESOLVED"},
    {"notes": "x", "outcome": "resolved", "lost": True},         # legacy flags are not part of the contract
    {"notes": "x", "outcome": "resolved", "recovered": True},
    {"notes": "x", "outcome": "resolved", "instrument_id": str(uuid.uuid4())},
    {"notes": "x", "outcome": "resolved", "institution_id": "x"},
])
def test_invalid_bodies_are_400(client, world, rdata, as_user, db_conn, body):
    before = ds03_snapshot(db_conn)
    response = _post(client, as_user, rdata.cr, rdata.d1, body)
    assert response.status_code == 400 and response.get_json()["error"] == "invalid_request"
    _assert_nothing_changed(db_conn, rdata, before, rdata.d1)


@pytest.mark.parametrize("outcome", ["lost", "recovered"])
@pytest.mark.parametrize("instrument_id", ["not-a-uuid", 5])
def test_lost_and_recovered_require_a_valid_instrument_id(client, world, rdata, as_user, db_conn, outcome, instrument_id):
    """An instrument_id, when given, must be a UUID (without it the family rule applies: test_supervisor_flow.py)."""
    before = ds03_snapshot(db_conn)
    body = {"notes": "Not found anywhere", "outcome": outcome}
    if instrument_id is not None:
        body["instrument_id"] = instrument_id
    response = _post(client, as_user, rdata.cr, rdata.d1, body)
    assert response.status_code == 400 and response.get_json()["details"]["field"] == "instrument_id"
    _assert_nothing_changed(db_conn, rdata, before, rdata.d1)


def test_malformed_bodies(client, world, rdata, as_user):
    headers = as_user("spd_a", **{"Content-Type": "application/json"})
    assert client.post(_url(rdata.cr, rdata.d1), data="{", headers=headers).status_code == 400
    assert client.post(_url(rdata.cr, rdata.d1), headers=as_user("spd_a")).status_code == 400   # body required
    xml = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "x">]><resolve_request/>'
    assert client.post(_url(rdata.cr, rdata.d1), data=xml,
                       headers=as_user("spd_a", **{"Content-Type": "application/xml"})).status_code == 400
    big = {"notes": "n" * (70 * 1024), "outcome": "resolved"}
    assert _post(client, as_user, rdata.cr, rdata.d1, big).status_code == 413


# --- XML ---------------------------------------------------------------------------------------------------


def test_xml_request_and_response(client, world, rdata, as_user, db_conn):
    xml = "<resolve_request><notes>Found in the tray</notes><outcome>resolved</outcome></resolve_request>"
    response = client.post(_url(rdata.cr, rdata.d2), data=xml,
                           headers=as_user("spd_a", **{"Content-Type": "application/xml", "Accept": "application/xml"}))
    assert response.status_code == 200, response.get_data(as_text=True)
    root = ET.fromstring(response.data)
    assert root.tag == "resolve_result"
    assert [(c.tag, c.text) for c in root] == [("discrepancy_id", str(rdata.d2)), ("session_id", str(rdata.cr)),
                                               ("open_remaining", "1"), ("session_status_code", "correction_required")]


def test_xml_error(client, world, rdata, as_user):
    xml = "<resolve_request><notes>x</notes><outcome>lost</outcome><instrument_id>x</instrument_id></resolve_request>"
    response = client.post(_url(rdata.cr, rdata.d1), data=xml,
                           headers=as_user("spd_a", **{"Content-Type": "application/xml", "Accept": "application/xml"}))
    root = ET.fromstring(response.data)
    assert response.status_code == 400 and root.tag == "error_response"
    assert root.findtext("error") == "invalid_request" and root.find("details").findtext("field") == "instrument_id"


# --- GET discrepancies -------------------------------------------------------------------------------------


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_get_discrepancies_reflects_resolution(client, world, rdata, as_user, db_conn, accept):
    _post(client, as_user, rdata.cr, rdata.d1)
    response = client.get(f"{BASE}/{rdata.cr}/discrepancies", headers=as_user("spd_a", Accept=accept))
    if accept == "application/json":
        item = next(d for d in response.get_json() if d["id"] == str(rdata.d1))
        assert item["resolved"] is True and item["resolved_at"] is not None
        assert (item["reason_code"], item["family_id"], item["expected_quantity"], item["detected_quantity"]) == (
            "surplus", str(world.families["a"]), 2, 3)
        assert item["description"].endswith("SPD resolution (resolved): Found in the auxiliary tray")
        assert set(item) == {"id", "reason_code", "family_id", "family_code", "expected_quantity", "detected_quantity",
                             "resolved", "resolved_at", "origin_event_id", "description"}
    else:
        node = next(d for d in ET.fromstring(response.data) if d.findtext("id") == str(rdata.d1))
        assert node.findtext("resolved") == "true" and node.findtext("resolved_at")


# --- Atomicity -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["update_discrepancy", "update_session", "audit"])
def test_failure_rolls_back_everything(client, world, rdata, as_user, db_conn, monkeypatch, target):
    from adapters import audit_adapter
    from repositories import write_repository

    _post(client, as_user, rdata.cr, rdata.d1)            # leave d2 as the last open one (status change path)
    before_ds03, before_d2 = ds03_snapshot(db_conn), _disc(db_conn, rdata.d2)
    audits_before = _audits(db_conn, rdata.cr)
    if target == "audit":
        monkeypatch.setattr(audit_adapter, "ACTOR_TYPE", "robot")
    else:
        def boom(*args, **kwargs):
            raise RuntimeError(f"injected failure in {target}")
        monkeypatch.setattr(write_repository, target, boom)
    response = _post(client, as_user, rdata.cr, rdata.d2)
    assert response.status_code == 500 and "injected" not in response.get_data(as_text=True)
    assert _disc(db_conn, rdata.d2) == before_d2
    assert _status(db_conn, rdata.cr) == "correction_required"
    assert ds03_snapshot(db_conn) == before_ds03 and _audits(db_conn, rdata.cr) == audits_before


@pytest.mark.parametrize("corr, preserved", [("v" * 64, True), ("v" * 65, False)])
def test_audit_correlation_id_equals_response_header(client, world, rdata, as_user, db_conn, corr, preserved):
    response = _post(client, as_user, rdata.cr, rdata.d1, **{"X-Correlation-ID": corr})
    answered = response.headers["X-Correlation-ID"]
    assert response.status_code == 200 and (answered == corr) is preserved
    assert [a[1] for a in _audits(db_conn, rdata.cr)] == [answered]
