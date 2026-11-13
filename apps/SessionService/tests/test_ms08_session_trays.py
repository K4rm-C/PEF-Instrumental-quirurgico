"""MS-08: optional physical tray of a session (real PostgreSQL, rolled back)."""

from __future__ import annotations

import uuid
from pathlib import Path

from lxml import etree
from sqlalchemy import text

from tests.test_supervisor_flow import BASE, plan, schedule_body  # noqa: F401  (plan is a fixture)
from tests.write_fixtures import wdata  # noqa: F401  (fixture used by plan)

XSD = etree.XMLSchema(etree.parse(str(Path(__file__).resolve().parents[1] / "schemas" / "session-reports.xsd")))


def tray(db_conn, world, inst="A", *, kit="same", status="active"):
    tray_id = uuid.uuid4()
    kit_id = {"same": world.kits[inst], "none": None}.get(kit, kit)
    db_conn.execute(text("INSERT INTO surgical_tray (id, institution_id, code, kit_id, status) VALUES (:id, :i, :c, :k, :s)"),
                    {"id": tray_id, "i": world.institutions[inst], "c": f"TR-{tray_id.hex[:8]}", "k": kit_id, "s": status})
    return tray_id


def test_schedule_with_a_tray_keeps_the_kit_quantities(client, world, plan, as_user, db_conn):
    tray_id = tray(db_conn, world)
    response = client.post(f"{BASE}/schedule", json=schedule_body(world, plan, tray_id=str(tray_id)), headers=as_user("spd_a"))
    assert response.status_code == 201, response.get_data(as_text=True)
    snap = response.get_json()
    assert snap["tray"]["id"] == str(tray_id) and snap["tray"]["status"] == "active"
    lines = {line["family_id"]: line["expected_quantity"] for line in snap["expected_inventory"]}
    assert lines == {str(world.families["a"]): 2, str(world.families["b"]): 1}       # unchanged by the tray
    # Changing the tray afterwards never changes the session.
    db_conn.execute(text("UPDATE surgical_tray SET kit_id = NULL, status = 'out_of_service', version = version + 1 "
                         "WHERE id = :id"), {"id": tray_id})
    detail = client.get(f"{BASE}/{snap['id']}?expand=detail", headers=as_user("spd_a")).get_json()
    assert {l["family_id"]: l["expected_quantity"] for l in detail["expected_inventory"]} == lines
    assert detail["tray"]["status"] == "out_of_service"
    sessions = client.get(f"{BASE}/trays/{tray_id}", headers=as_user("spd_a"))
    assert sessions.status_code == 200 and [s["id"] for s in sessions.get_json()["sessions"]] == [snap["id"]]
    xml = client.get(f"{BASE}/trays/{tray_id}", headers=as_user("spd_a", Accept="application/xml"))
    assert XSD.validate(etree.fromstring(xml.data)), XSD.error_log


def test_without_a_tray_nothing_changes(client, world, plan, as_user):
    response = client.post(f"{BASE}/schedule", json=schedule_body(world, plan), headers=as_user("spd_a"))
    assert response.status_code == 201 and response.get_json()["tray"] is None


def test_invalid_trays_are_refused(client, world, plan, as_user, db_conn):
    other_kit = uuid.uuid4()
    db_conn.execute(text("INSERT INTO kit (id, name, version, active, institution_id) VALUES (:id, :n, 1, TRUE, :i)"),
                    {"id": other_kit, "n": f"Other {other_kit.hex[:6]}", "i": world.institutions["A"]})
    cases = {"invalid_tray": str(tray(db_conn, world, "B")), "inactive_tray": str(tray(db_conn, world, status="retired")),
             "tray_kit_mismatch": str(tray(db_conn, world, kit=other_kit))}
    cases["invalid_tray "] = "not-a-uuid"
    for reason, tray_id in cases.items():
        response = client.post(f"{BASE}/schedule", json=schedule_body(world, plan, tray_id=tray_id), headers=as_user("spd_a"))
        assert response.status_code == 422 and response.get_json()["details"]["reason"] == reason.strip(), reason
    assert client.post(f"{BASE}/schedule", json=schedule_body(world, plan, tray_id=str(tray(db_conn, world, kit="none"))),
                       headers=as_user("spd_a")).status_code == 201       # a tray without a configured kit is allowed


def test_tray_sessions_only_for_the_supervisor_of_the_institution(client, world, as_user, db_conn):
    tray_id = tray(db_conn, world)
    assert client.get(f"{BASE}/trays/{tray_id}", headers=as_user("op_a")).status_code == 403
    assert client.get(f"{BASE}/trays/{tray(db_conn, world, 'B')}", headers=as_user("spd_a")).status_code == 404


# --- MS-09: consolidation (assignment history, change while scheduled, integrity) ---------------------------


def _update_body(world, plan, **overrides):
    body = schedule_body(world, plan, **overrides)
    del body["phase_code"]
    return body


def test_tray_assignment_history_and_change_while_scheduled(client, world, plan, as_user, db_conn):
    first, second = tray(db_conn, world), tray(db_conn, world)
    created = client.post(f"{BASE}/schedule", json=schedule_body(world, plan, tray_id=str(first)), headers=as_user("spd_a"))
    sid = created.get_json()["id"]
    changed = client.put(f"{BASE}/{sid}", json=_update_body(world, plan, tray_id=str(second)), headers=as_user("spd_a"))
    assert changed.status_code == 200 and changed.get_json()["tray"]["id"] == str(second)
    unchanged = client.put(f"{BASE}/{sid}", json=_update_body(world, plan), headers=as_user("spd_a"))  # tray_id absent
    assert unchanged.status_code == 200 and unchanged.get_json()["tray"]["id"] == str(second)
    removed = client.put(f"{BASE}/{sid}", json=_update_body(world, plan, tray_id=None), headers=as_user("spd_a"))
    assert removed.status_code == 200 and removed.get_json()["tray"] is None
    actions = db_conn.execute(text("SELECT action FROM access_audit WHERE resource_id = :s AND action = 'session.tray_assigned' "
                                   "ORDER BY occurred_at"), {"s": sid}).scalars().all()
    assert actions == ["session.tray_assigned"] * 3            # assigned, changed, removed (who and when in the audit)
    retired = tray(db_conn, world, status="retired")
    refused = client.put(f"{BASE}/{sid}", json=_update_body(world, plan, tray_id=str(retired)), headers=as_user("spd_a"))
    assert refused.status_code == 422 and refused.get_json()["details"]["reason"] == "inactive_tray"


def test_a_kit_change_cannot_leave_an_incompatible_tray(client, world, plan, as_user, db_conn):
    kept = tray(db_conn, world)
    sid = client.post(f"{BASE}/schedule", json=schedule_body(world, plan, tray_id=str(kept)),
                      headers=as_user("spd_a")).get_json()["id"]
    other_kit = uuid.uuid4()
    db_conn.execute(text("INSERT INTO kit (id, name, version, active, institution_id) VALUES (:id, :n, 1, TRUE, :i)"),
                    {"id": other_kit, "n": f"Other {other_kit.hex[:6]}", "i": world.institutions["A"]})
    db_conn.execute(text("INSERT INTO kit_item (kit_id, family_id, quantity) SELECT :k, family_id, quantity FROM kit_item "
                         "WHERE kit_id = :old"), {"k": other_kit, "old": world.kits["A"]})
    refused = client.put(f"{BASE}/{sid}", json=_update_body(world, plan, kit_id=str(other_kit)), headers=as_user("spd_a"))
    assert refused.status_code == 422 and refused.get_json()["details"]["reason"] == "tray_kit_mismatch"
    snapshot = client.get(f"{BASE}/{sid}?expand=detail", headers=as_user("spd_a")).get_json()
    assert snapshot["kit"]["id"] == str(world.kits["A"]) and snapshot["tray"]["id"] == str(kept)   # nothing applied


def test_database_refuses_a_tray_of_another_institution(db_conn, world, plan):
    foreign = tray(db_conn, world, "B")
    session = world.sessions["a3"].id
    import pytest
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        with db_conn.begin_nested():
            db_conn.execute(text("UPDATE work_session SET tray_id = :t WHERE id = :s"), {"t": foreign, "s": session})
