"""PUT /api/v1/sessions/{id}/expected-inventory (Phase C)."""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import text

from write_fixtures import wdata  # noqa: F401  (fixture)

BASE = "/api/v1/sessions"


def _url(session_id):
    return f"{BASE}/{session_id}/expected-inventory"


def _seed_lines(db_conn, session_id, lines):
    for family_id, qty, source in lines:
        db_conn.execute(text("INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source) "
                             "VALUES (:s, :f, :q, :src)"), {"s": session_id, "f": family_id, "q": qty, "src": source})


def _db_lines(db_conn, session_id):
    rows = db_conn.execute(text("SELECT family_id, expected_quantity, source FROM expected_inventory WHERE session_id = :s"),
                           {"s": session_id}).all()
    return {(row[0], row[1], row[2]) for row in rows}


def _set_status(db_conn, session_id, code):
    db_conn.execute(text("UPDATE work_session SET status_id = (SELECT id FROM cat_session_status WHERE code = :c) "
                         "WHERE id = :s"), {"c": code, "s": session_id})


def _body(*pairs):
    return {"lines": [{"family_id": str(f), "expected_quantity": q} for f, q in pairs]}


def _audits(db_conn, session_id):
    return db_conn.execute(text("SELECT action, correlation_id, actor_user_id FROM access_audit WHERE resource_id = :s AND outcome = 'success'"),
                           {"s": session_id}).all()


def test_scheduled_replacement_and_sources(client, world, wdata, as_user, db_conn):
    f, s = world.families, world.sessions["a1"].id
    _seed_lines(db_conn, s, [(f["a"], 2, "kit_snapshot"), (f["c"], 1, "schedule_additional"), (f["d"], 1, "manual")])
    response = client.put(_url(s), json=_body((f["a"], 1), (f["c"], 1), (f["b"], 1)),
                          headers=as_user("spd_a", **{"X-Correlation-ID": "corr-inv-1"}))
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json() == client.get(_url(s), headers=as_user("spd_a")).get_json()
    assert _db_lines(db_conn, s) == {
        (f["a"], 1, "schedule_additional"),   # changed: kit says 2 → recomputed by DS04/DS09 kit rule
        (f["c"], 1, "schedule_additional"),   # unchanged: provenance preserved
        (f["b"], 1, "kit_snapshot"),          # new, matches kit quantity
    }                                         # d removed (total replacement)
    assert [tuple(a) for a in _audits(db_conn, s)] == [("session.inventory_updated", "corr-inv-1", world.users["spd_a"].id)]


def test_scheduled_change_back_to_kit_quantity_is_kit_snapshot(client, world, wdata, as_user, db_conn):
    f, s = world.families, world.sessions["a1"].id
    _seed_lines(db_conn, s, [(f["a"], 1, "schedule_additional")])
    client.put(_url(s), json=_body((f["a"], 2)), headers=as_user("spd_a"))
    assert _db_lines(db_conn, s) == {(f["a"], 2, "kit_snapshot")}


def test_in_progress_sources_are_live_add(client, world, wdata, as_user, db_conn):
    f, s = world.families, world.sessions["a2"].id
    _seed_lines(db_conn, s, [(f["a"], 2, "kit_snapshot"), (f["b"], 1, "schedule_additional"),
                             (f["c"], 1, "schedule_additional")])
    response = client.put(_url(s), json=_body((f["a"], 2), (f["b"], 2), (f["d"], 0 + 1)), headers=as_user("spd_a"))
    # d: A has no available active d → stock fails; use a family with stock instead.
    assert response.status_code == 422
    response = client.put(_url(s), json=_body((f["a"], 2), (f["b"], 2)), headers=as_user("spd_a"))
    assert response.status_code == 200
    assert _db_lines(db_conn, s) == {
        (f["a"], 2, "kit_snapshot"),   # unchanged: preserved
        (f["b"], 2, "live_add"),       # changed while in progress
    }                                  # c removed
    response = client.put(_url(s), json=_body((f["a"], 2), (f["b"], 2), (f["c"], 1)), headers=as_user("spd_a"))
    assert (f["c"], 1, "live_add") in _db_lines(db_conn, s)


@pytest.mark.parametrize("status", ["awaiting_spd_review", "correction_required", "closed", "aborted"])
def test_non_editable_states_are_409_and_unchanged(client, world, wdata, as_user, db_conn, status):
    f, s = world.families, world.sessions["a1"].id
    _seed_lines(db_conn, s, [(f["a"], 2, "kit_snapshot")])
    _set_status(db_conn, s, status)
    response = client.put(_url(s), json=_body((f["b"], 1)), headers=as_user("spd_a"))
    assert response.status_code == 409 and response.get_json()["error"] == "conflict"
    assert response.get_json()["details"] == {"status_code": status, "reason": "inventory_locked"}
    assert _db_lines(db_conn, s) == {(f["a"], 2, "kit_snapshot")}
    assert _audits(db_conn, s) == []


@pytest.mark.parametrize("user_key", ["op_a", "admin_a", "op_admin_a"])
def test_roles_without_supervisor_are_403(client, world, wdata, as_user, user_key):
    response = client.put(_url(world.sessions["a1"].id), json=_body((world.families["a"], 1)), headers=as_user(user_key))
    assert response.status_code == 403


@pytest.mark.parametrize("target", ["b1", "no_operation", "unknown", "not-a-uuid"])
def test_out_of_scope_session_is_404(client, world, wdata, as_user, db_conn, target):
    session_id = {"unknown": uuid.uuid4(), "not-a-uuid": "not-a-uuid"}.get(target) or world.sessions[target].id
    response = client.put(_url(session_id), json=_body((world.families["a"], 1)), headers=as_user("spd_a"))
    assert response.status_code == 404 and response.get_json()["error"] == "not_found"
    if target == "b1":
        assert _db_lines(db_conn, world.sessions["b1"].id) == {(world.families["a"], 5, "kit_snapshot")}


@pytest.mark.parametrize("body, status, error", [
    ({"lines": []}, 400, "invalid_request"),
    ({}, 400, "invalid_request"),
    ({"lines": [{"family_id": "F", "expected_quantity": 1}, {"family_id": "F", "expected_quantity": 1}]}, 400, "invalid_request"),
    ({"lines": [{"family_id": "F", "expected_quantity": 0}]}, 422, "validation_error"),
    ({"lines": [{"family_id": "F", "expected_quantity": False}]}, 400, "invalid_request"),
    ({"lines": [{"family_id": "F", "expected_quantity": 1}], "institution_id": "x"}, 400, "invalid_request"),
    ({"lines": [{"family_id": str(uuid.uuid4()), "expected_quantity": 1}]}, 422, "invalid_reference"),
    ({"lines": [{"family_id": "F", "expected_quantity": 4}]}, 422, "validation_error"),   # stock a = 3
])
def test_invalid_updates_keep_previous_inventory(client, world, wdata, as_user, db_conn, body, status, error):
    f, s = world.families, world.sessions["a1"].id
    _seed_lines(db_conn, s, [(f["b"], 1, "kit_snapshot")])
    if "lines" in body:
        body = {**body, "lines": [{**l, "family_id": str(f["a"]) if l["family_id"] == "F" else l["family_id"]}
                                  for l in body["lines"]]}
    response = client.put(_url(s), json=body, headers=as_user("spd_a"))
    assert response.status_code == status and response.get_json()["error"] == error
    assert _db_lines(db_conn, s) == {(f["b"], 1, "kit_snapshot")}


def test_stock_of_other_tenant_does_not_count(client, world, wdata, as_user):
    # A has no active available d; B has 5.
    response = client.put(_url(world.sessions["a1"].id), json=_body((world.families["d"], 1)), headers=as_user("spd_a"))
    assert response.status_code == 422 and response.get_json()["details"] == {"family_id": str(world.families["d"]), "reason": "stock_block"}


def test_audit_failure_keeps_previous_inventory(client, world, wdata, as_user, db_conn, monkeypatch):
    from adapters import audit_adapter

    f, s = world.families, world.sessions["a1"].id
    _seed_lines(db_conn, s, [(f["b"], 1, "kit_snapshot"), (f["c"], 1, "schedule_additional")])
    monkeypatch.setattr(audit_adapter, "ACTOR_TYPE", "robot")
    response = client.put(_url(s), json=_body((f["a"], 2)), headers=as_user("spd_a"))
    assert response.status_code == 500
    assert _db_lines(db_conn, s) == {(f["b"], 1, "kit_snapshot"), (f["c"], 1, "schedule_additional")}
    assert _audits(db_conn, s) == []


def test_xml_update_and_response(client, world, wdata, as_user, db_conn):
    f, s = world.families, world.sessions["a1"].id
    xml = ("<expected_inventory_update><lines>"
           f"<line><family_id>{f['a']}</family_id><expected_quantity>2</expected_quantity></line>"
           f"<line><family_id>{f['c']}</family_id><expected_quantity>1</expected_quantity></line>"
           "</lines></expected_inventory_update>")
    response = client.put(_url(s), data=xml, headers=as_user("spd_a", **{
        "Content-Type": "application/xml", "Accept": "application/xml"}))
    assert response.status_code == 200, response.get_data(as_text=True)
    root = ET.fromstring(response.data)
    assert root.tag == "expected_inventory" and [c.tag for c in root] == ["line", "line"]
    as_json = client.get(_url(s), headers=as_user("spd_a")).get_json()
    assert [n.findtext("family_id") for n in root] == [l["family_id"] for l in as_json]
    assert _db_lines(db_conn, s) == {(f["a"], 2, "kit_snapshot"), (f["c"], 1, "schedule_additional")}


def test_xml_quantity_must_be_integer(client, world, wdata, as_user):
    xml = (f"<expected_inventory_update><lines><line><family_id>{world.families['a']}</family_id>"
           "<expected_quantity>two</expected_quantity></line></lines></expected_inventory_update>")
    response = client.put(_url(world.sessions["a1"].id), data=xml,
                          headers=as_user("spd_a", **{"Content-Type": "application/xml"}))
    assert response.status_code == 400


@pytest.mark.parametrize("corr, preserved", [("i" * 64, True), ("i" * 65, False)])
def test_audit_correlation_id_equals_response_header(client, world, wdata, as_user, db_conn, corr, preserved):
    f, s = world.families, world.sessions["a1"].id
    response = client.put(_url(s), json=_body((f["a"], 2)), headers=as_user("spd_a", **{"X-Correlation-ID": corr}))
    assert response.status_code == 200
    answered = response.headers["X-Correlation-ID"]
    assert (answered == corr) is preserved and (preserved or str(uuid.UUID(answered)) == answered)
    assert [a[1] for a in _audits(db_conn, s)] == [answered]
