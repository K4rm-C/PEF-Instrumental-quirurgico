"""MS-08: physical trays (real PostgreSQL, rolled back): identity distinct from the kit, institution isolation,
optimistic concurrency, history, audit, kit versions kept as historical references, XML contract."""

from __future__ import annotations

import uuid
from pathlib import Path

from lxml import etree
from sqlalchemy import text

XSD = etree.XMLSchema(etree.parse(str(Path(__file__).resolve().parents[1] / "schemas" / "catalog.xsd")))


def create(client, auth_header, world, code, **extra):
    return client.post("/api/v1/catalog/trays", json={"code": code, **extra}, headers=auth_header("supervisor_a"))


def test_two_trays_with_the_same_kit_are_two_entities(client, world, auth_header, db_conn):
    kit = str(world.kits_a["v2"])
    first = create(client, auth_header, world, f"TR-{world.tag}-1", kit_id=kit, name="Laparotomy tray 1")
    second = create(client, auth_header, world, f"TR-{world.tag}-2", kit_id=kit)
    assert first.status_code == second.status_code == 201
    a, b = first.get_json(), second.get_json()
    assert a["id"] != b["id"] and a["kit_id"] == b["kit_id"] == kit and a["version"] == 1 and a["status"] == "active"
    assert a["history"][0]["change_type"] == "create" and a["history"][0]["changed_by_user_id"] == str(world.users["supervisor_a"])
    audit = db_conn.execute(text("SELECT action FROM access_audit WHERE resource_id = :id"), {"id": a["id"]}).scalars().all()
    assert audit == ["CREATE_TRAY"]
    duplicate = create(client, auth_header, world, f"TR-{world.tag}-1")
    assert duplicate.status_code == 409 and duplicate.get_json()["details"]["reason"] == "tray_code_taken"


def test_only_the_spd_writes_and_every_role_reads_its_institution(client, world, auth_header):
    tray = create(client, auth_header, world, f"TR-{world.tag}-R").get_json()
    for role in ("operator_a", "admin_a"):
        assert client.post("/api/v1/catalog/trays", json={"code": f"X-{role}"}, headers=auth_header(role)).status_code == 403
        assert client.get(f"/api/v1/catalog/trays/{tray['id']}", headers=auth_header(role)).status_code == 200
    assert client.get(f"/api/v1/catalog/trays/{tray['id']}", headers=auth_header("operator_b")).status_code == 404
    listed = client.get("/api/v1/catalog/trays", headers=auth_header("operator_b")).get_json()
    assert all(t["id"] != tray["id"] for t in listed)
    foreign_kit = create(client, auth_header, world, f"TR-{world.tag}-F", kit_id=str(world.kits_b["v1"]))
    assert foreign_kit.status_code == 422 and foreign_kit.get_json()["details"]["reason"] == "invalid_kit"


def test_version_conflict_and_history(client, world, auth_header):
    tray = create(client, auth_header, world, f"TR-{world.tag}-V", kit_id=str(world.kits_a["v2"])).get_json()
    url = f"/api/v1/catalog/trays/{tray['id']}"
    updated = client.put(url, json={"version": 1, "status": "out_of_service", "notes": "Hinge damaged"},
                         headers=auth_header("supervisor_a"))
    assert updated.status_code == 200 and updated.get_json()["version"] == 2
    stale = client.put(url, json={"version": 1, "name": "Other"}, headers=auth_header("supervisor_a"))
    assert stale.status_code == 409 and stale.get_json()["error"] == "version_conflict"
    assert stale.get_json()["details"]["current_version"] == 2
    missing = client.put(url, json={"name": "x"}, headers=auth_header("supervisor_a"))
    assert missing.status_code == 400
    history = client.get(url, headers=auth_header("supervisor_a")).get_json()["history"]
    assert [h["change_type"] for h in history] == ["create", "status"]
    assert history[1]["before"]["status"] == "active" and history[1]["after"]["status"] == "out_of_service"
    same = client.put(url, json={"version": 2, "status": "out_of_service"}, headers=auth_header("supervisor_a"))
    assert same.get_json()["version"] == 2                       # nothing changed: no new version


def test_a_superseded_kit_stays_as_the_historical_reference(client, world, auth_header, db_conn):
    kit = world.kits_a["v2"]
    tray = create(client, auth_header, world, f"TR-{world.tag}-K", kit_id=str(kit)).get_json()
    new_kit = client.put(f"/api/v1/catalog/kits/{kit}", headers=auth_header("supervisor_a"), json={
        "name": f"Test Kit {world.tag}", "items": [{"family_id": str(world.families["a"]), "quantity": 9}]})
    assert new_kit.status_code == 201
    after = client.get(f"/api/v1/catalog/trays/{tray['id']}", headers=auth_header("supervisor_a")).get_json()
    assert after["kit_id"] == str(kit) and after["kit_superseded"] is True and after["version"] == 1
    refused = client.put(f"/api/v1/catalog/trays/{tray['id']}", json={"version": 1, "kit_id": str(kit)},
                         headers=auth_header("supervisor_a"))
    assert refused.status_code == 200                              # unchanged kit: allowed (no change)
    stale_kit = client.post("/api/v1/catalog/trays", json={"code": f"TR-{world.tag}-S", "kit_id": str(kit)},
                            headers=auth_header("supervisor_a"))
    assert stale_kit.status_code == 422 and stale_kit.get_json()["details"]["reason"] == "inactive_kit"


def test_validation_and_xml(client, world, auth_header):
    bad = client.post("/api/v1/catalog/trays", json={"code": " "}, headers=auth_header("supervisor_a"))
    assert bad.status_code == 422
    bad_status = client.post("/api/v1/catalog/trays", json={"code": f"TR-{world.tag}-X", "status": "sterilizing"},
                             headers=auth_header("supervisor_a"))
    assert bad_status.status_code == 422 and bad_status.get_json()["details"]["reason"] == "invalid_status"
    xml = client.post("/api/v1/catalog/trays", data=f"<tray><code>TR-{world.tag}-XML</code><name>Xml</name></tray>",
                      headers={**auth_header("supervisor_a"), "Content-Type": "application/xml", "Accept": "application/xml"})
    assert xml.status_code == 201 and XSD.validate(etree.fromstring(xml.data)), XSD.error_log
    listed = client.get("/api/v1/catalog/trays", headers={**auth_header("supervisor_a"), "Accept": "application/xml"})
    assert XSD.validate(etree.fromstring(listed.data)), XSD.error_log
    unknown = client.post("/api/v1/catalog/trays", json={"code": "Y", "institution_id": str(uuid.uuid4())},
                          headers=auth_header("supervisor_a"))
    assert unknown.status_code == 400
