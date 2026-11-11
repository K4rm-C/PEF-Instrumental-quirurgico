"""MS-12 (ms-kits-charolas): authorized equivalent instrument families of a kit. Real PostgreSQL (rolled back):
SPD-only writes, institution isolation, rules, history on revocation, audit, XML contract, quantities untouched."""

from __future__ import annotations

from pathlib import Path

from lxml import etree
from sqlalchemy import text

XSD = etree.XMLSchema(etree.parse(str(Path(__file__).resolve().parents[1] / "schemas" / "catalog.xsd")))


def url(kit, suffix=""):
    return f"/api/v1/catalog/kits/{kit}/equivalences{suffix}"


def authorize(client, auth_header, kit, family, equivalent, role="supervisor_a", **extra):
    return client.post(url(kit), json={"family_id": str(family), "equivalent_family_id": str(equivalent), **extra},
                       headers=auth_header(role))


def test_spd_authorizes_an_equivalent_family_and_every_role_reads_it(client, world, auth_header, db_conn):
    kit = world.kits_a["v1"]                                         # items: family a
    created = authorize(client, auth_header, kit, world.families["a"], world.families["b"], notes="Same pattern, other maker")
    assert created.status_code == 201
    item = created.get_json()
    assert item["active"] is True and item["family_id"] == str(world.families["a"]) and item["notes"].startswith("Same")
    assert item["authorized_by_user_id"] == str(world.users["supervisor_a"]) and item["revoked_at"] is None
    for role in ("operator_a", "admin_a", "supervisor_a"):
        listed = client.get(url(kit), headers=auth_header(role))
        assert listed.status_code == 200 and [e["id"] for e in listed.get_json()] == [item["id"]]
    audit = db_conn.execute(text("SELECT action FROM access_audit WHERE resource_id = :id"), {"id": item["id"]}).scalars().all()
    assert audit == ["AUTHORIZE_KIT_EQUIVALENCE"]
    quantities = db_conn.execute(text("SELECT family_id, quantity FROM kit_item WHERE kit_id = :k"), {"k": kit}).all()
    assert quantities == [(world.families["a"], 1)]                  # the kit composition never changes


def test_only_the_spd_writes_and_other_institutions_never_see_it(client, world, auth_header):
    kit = world.kits_a["v1"]
    for role in ("operator_a", "admin_a"):
        assert authorize(client, auth_header, kit, world.families["a"], world.families["b"], role=role).status_code == 403
    authorize(client, auth_header, kit, world.families["a"], world.families["b"])
    assert client.get(url(kit), headers=auth_header("operator_b")).status_code == 404
    foreign = authorize(client, auth_header, world.kits_b["v1"], world.families["a"], world.families["b"])
    assert foreign.status_code == 404                                # a kit of institution B does not exist for A


def test_rules_of_an_equivalence(client, world, auth_header):
    kit_v1, kit_v2 = world.kits_a["v1"], world.kits_a["v2"]
    same = authorize(client, auth_header, kit_v1, world.families["a"], world.families["a"])
    assert same.status_code == 422 and same.get_json()["details"]["reason"] == "same_family"
    not_in_kit = authorize(client, auth_header, kit_v1, world.families["b"], world.families["a"])
    assert not_in_kit.status_code == 422 and not_in_kit.get_json()["error"] == "family_not_in_kit"
    already_item = authorize(client, auth_header, kit_v2, world.families["a"], world.families["b"])
    assert already_item.status_code == 422 and already_item.get_json()["error"] == "equivalent_in_kit"
    inactive = authorize(client, auth_header, kit_v1, world.families["a"], world.families["c"])
    assert inactive.status_code == 422 and inactive.get_json()["error"] == "equivalent_unavailable"
    first = authorize(client, auth_header, kit_v1, world.families["a"], world.families["b"])
    duplicate = authorize(client, auth_header, kit_v1, world.families["a"], world.families["b"])
    assert first.status_code == 201 and duplicate.status_code == 409


def test_revocation_keeps_the_history_and_is_idempotent(client, world, auth_header, db_conn):
    kit = world.kits_a["v1"]
    item = authorize(client, auth_header, kit, world.families["a"], world.families["b"]).get_json()
    assert client.delete(url(kit, f"/{item['id']}"), headers=auth_header("operator_a")).status_code == 403
    revoked = client.delete(url(kit, f"/{item['id']}"), headers=auth_header("supervisor_a"))
    assert revoked.status_code == 200 and revoked.get_json()["active"] is False and revoked.get_json()["revoked_at"]
    again = client.delete(url(kit, f"/{item['id']}"), headers=auth_header("supervisor_a"))
    assert again.status_code == 200 and again.get_json()["revoked_at"] == revoked.get_json()["revoked_at"]
    assert client.get(url(kit), headers=auth_header("operator_a")).get_json() == []
    history = client.get(url(kit) + "?status=all", headers=auth_header("operator_a")).get_json()
    assert [e["active"] for e in history] == [False]
    actions = db_conn.execute(text("SELECT action FROM access_audit WHERE resource_id = :id ORDER BY occurred_at"),
                              {"id": item["id"]}).scalars().all()
    assert actions == ["AUTHORIZE_KIT_EQUIVALENCE", "REVOKE_KIT_EQUIVALENCE"]
    reauthorized = authorize(client, auth_header, kit, world.families["a"], world.families["b"])
    assert reauthorized.status_code == 201                            # a revoked one does not block a new authorization


def test_xml_contract(client, world, auth_header):
    kit = world.kits_a["v1"]
    body = (f"<equivalence><family_id>{world.families['a']}</family_id>"
            f"<equivalent_family_id>{world.families['b']}</equivalent_family_id></equivalence>")
    created = client.post(url(kit), data=body, content_type="application/xml",
                          headers={**auth_header("supervisor_a"), "Accept": "application/xml"})
    assert created.status_code == 201
    assert XSD.validate(etree.fromstring(created.data)), XSD.error_log
    listed = client.get(url(kit), headers={**auth_header("operator_a"), "Accept": "application/xml"})
    assert XSD.validate(etree.fromstring(listed.data)), XSD.error_log
