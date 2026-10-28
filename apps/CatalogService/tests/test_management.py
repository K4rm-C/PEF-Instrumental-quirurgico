"""Management endpoints (PEF-DS09 §1.3.3 complement v0.1.2): rules moved from the portal's Administration.

Real PostgreSQL/Redis (rolled-back transaction, see conftest). XML responses and request bodies are
validated against schemas/catalog.xsd.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from lxml import etree
from sqlalchemy import text

BASE = "/api/v1/catalog"
XSD = etree.XMLSchema(etree.parse(str(Path(__file__).resolve().parents[1] / "schemas" / "catalog.xsd")))
JSON = {"Content-Type": "application/json"}
XML = {"Content-Type": "application/xml", "Accept": "application/xml"}


def assert_valid_xml(data: bytes):
    document = etree.fromstring(data)
    XSD.assertValid(document)
    return document


def audit_actions(db_conn, resource_id) -> list[str]:
    return db_conn.execute(text("SELECT action FROM access_audit WHERE resource_id = :id ORDER BY action"),
                           {"id": resource_id}).scalars().all()


@pytest.fixture
def second_status(db_conn, world):
    code = f"zz_st2_{world.tag}"
    db_conn.execute(text("INSERT INTO cat_instrument_cycle_status (id, code, name) VALUES (:id, :code, 'Second')"),
                    {"id": uuid.uuid4(), "code": code})
    return code


# --- Reference catalogs and expand ------------------------------------------------------------


def test_reference_catalogs(client, world, auth_header):
    categories = client.get(f"{BASE}/categories", headers=auth_header("operator_a")).get_json()
    assert world.category_code in [c["code"] for c in categories]
    statuses = client.get(f"{BASE}/cycle-statuses", headers=auth_header("operator_a")).get_json()
    assert world.cycle_status_code in [s["code"] for s in statuses]
    response = client.get(f"{BASE}/categories", headers={**auth_header("operator_a"), "Accept": "application/xml"})
    assert_valid_xml(response.data).tag == "categories"


def test_expand_detail_is_opt_in(client, world, auth_header):
    plain = client.get(f"{BASE}/families", headers=auth_header("admin_a")).get_json()
    assert set(plain[0]) == {"id", "code", "name", "category_code", "active"}   # DS09 v0.1.1 unchanged
    detail = client.get(f"{BASE}/families?expand=detail", headers=auth_header("admin_a")).get_json()
    mine = next(f for f in detail if f["code"] == world.family_code("a"))
    assert mine["category_name"] == "Test category" and mine["available_count"] == 0
    assert client.get(f"{BASE}/families?expand=all", headers=auth_header("admin_a")).status_code == 400


# --- Families -----------------------------------------------------------------------------


def test_family_detail_update_and_audit(client, world, auth_header, db_conn):
    family_id = world.families["a"]
    detail = client.get(f"{BASE}/families/{family_id}", headers=auth_header("admin_a")).get_json()
    assert detail["kits_using"] == 2 and detail["identify_text"] is None
    body = {"name": "Renamed", "category_code": world.category_code, "function_text": "Cuts", "active": False}
    response = client.put(f"{BASE}/families/{family_id}", json=body, headers=auth_header("admin_a"))
    assert response.status_code == 200
    updated = response.get_json()
    assert (updated["name"], updated["function_text"], updated["active"]) == ("Renamed", "Cuts", False)
    assert updated["code"] == world.family_code("a")                     # code is immutable
    assert audit_actions(db_conn, family_id) == ["DEACTIVATE_INSTRUMENT_FAMILY", "UPDATE_INSTRUMENT_FAMILY"]


def test_family_update_validation(client, world, auth_header):
    url = f"{BASE}/families/{world.families['a']}"
    response = client.put(url, json={"name": " ", "category_code": world.category_code}, headers=auth_header("admin_a"))
    assert response.status_code == 422 and response.get_json()["details"]["reason"] == "name_required"
    response = client.put(url, json={"name": "X", "category_code": "nope"}, headers=auth_header("admin_a"))
    assert response.status_code == 422 and response.get_json()["details"]["reason"] == "invalid_category"
    response = client.put(url, json={"name": "X", "category_code": world.category_code, "code": "new"},
                          headers=auth_header("admin_a"))
    assert response.status_code == 400 and response.get_json()["details"]["unknown_fields"] == ["code"]
    assert client.put(f"{BASE}/families/{uuid.uuid4()}", json={"name": "X", "category_code": world.category_code},
                      headers=auth_header("admin_a")).status_code == 404


def test_family_create_is_audited(client, world, auth_header, db_conn):
    body = {"code": world.family_code("new"), "name": "New", "category_code": world.category_code}
    created = client.post(f"{BASE}/families", json=body, headers=auth_header("admin_a")).get_json()
    assert audit_actions(db_conn, created["id"]) == ["CREATE_INSTRUMENT_FAMILY"]


def test_family_writes_need_admin_and_denials_are_audited(client, world, auth_header, db_conn):
    url = f"{BASE}/families/{world.families['a']}"
    response = client.put(url, json={"name": "X", "category_code": world.category_code}, headers=auth_header("supervisor_a"))
    assert response.status_code == 403
    assert audit_actions(db_conn, world.families["a"]) == ["DENIED_PUT_INSTRUMENT_FAMILY"]
    assert client.put(url, json={}, headers={"X-User-Role": "it_admin"}).status_code == 401   # headers are not identity


def test_family_update_xml(client, world, auth_header):
    body = (f"<family><name>From XML</name><category_code>{world.category_code}</category_code>"
            "<classify_text xsi:nil='true' xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance'/>"
            "<active>1</active></family>").encode()
    assert_valid_xml(body)
    response = client.put(f"{BASE}/families/{world.families['b']}", data=body, headers={**auth_header("admin_a"), **XML})
    assert response.status_code == 200
    document = assert_valid_xml(response.data)
    assert document.findtext("name") == "From XML" and document.findtext("active") == "true"


# --- Instruments --------------------------------------------------------------------------


def test_instrument_create_detail_and_cycle_event(client, world, auth_header, db_conn):
    body = {"internal_code": f"NEW-{world.tag}", "family_id": str(world.families["a"]),
            "cycle_status_code": world.cycle_status_code}
    response = client.post(f"{BASE}/instruments", json=body, headers=auth_header("supervisor_a"))
    assert response.status_code == 201
    item = response.get_json()
    assert item["institution_id"] == str(world.institution_a) and item["family_name"] == "Test family a"
    assert [e["notes"] for e in item["cycle_events"]] == ["registered"]
    assert audit_actions(db_conn, item["id"]) == ["CREATE_INSTRUMENT"]
    again = client.post(f"{BASE}/instruments", json=body, headers=auth_header("admin_a"))
    assert again.status_code == 409 and again.get_json()["details"]["reason"] == "instrument_code_exists"


def test_instrument_update_requires_reason_for_cycle_or_status(client, world, auth_header, db_conn, second_status):
    instrument_id = world.instruments_a[0]
    url = f"{BASE}/instruments/{instrument_id}"
    body = {"family_id": str(world.families["b"]), "cycle_status_code": second_status, "active": True}
    response = client.put(url, json=body, headers=auth_header("admin_a"))
    assert response.status_code == 422 and response.get_json()["details"]["reason"] == "change_reason_required"
    response = client.put(url, json={**body, "change_reason": "maintenance"}, headers=auth_header("admin_a"))
    assert response.status_code == 200
    item = response.get_json()
    assert item["cycle_status_code"] == second_status
    assert item["cycle_events"][0]["notes"] == f"cycle: {world.cycle_status_code}→{second_status}; reason: maintenance"
    assert audit_actions(db_conn, instrument_id) == ["UPDATE_INSTRUMENT"]
    # Family-only change: no reason needed, no cycle event.
    response = client.put(url, json={**body, "family_id": str(world.families["a"])}, headers=auth_header("admin_a"))
    assert response.status_code == 200 and len(response.get_json()["cycle_events"]) == 1


def test_instrument_tenant_and_role_rules(client, world, auth_header, db_conn):
    foreign = world.instruments_b[0]
    assert client.get(f"{BASE}/instruments/{foreign}", headers=auth_header("admin_a")).status_code == 404
    body = {"family_id": str(world.families["a"]), "cycle_status_code": world.cycle_status_code}
    assert client.put(f"{BASE}/instruments/{foreign}", json=body, headers=auth_header("admin_a")).status_code == 404
    response = client.post(f"{BASE}/instruments", json={**body, "internal_code": "OP-1"}, headers=auth_header("operator_a"))
    assert response.status_code == 403
    response = client.post(f"{BASE}/instruments", json={**body, "internal_code": "X", "family_id": str(uuid.uuid4())},
                           headers=auth_header("admin_a"))
    assert response.status_code == 422 and response.get_json()["details"]["reason"] == "invalid_family_or_cycle"


def test_instrument_xml_create(client, world, auth_header):
    body = (f"<instrument><internal_code>XML-{world.tag}</internal_code><family_id>{world.families['a']}</family_id>"
            f"<cycle_status_code>{world.cycle_status_code}</cycle_status_code><active>true</active></instrument>").encode()
    assert_valid_xml(body)
    response = client.post(f"{BASE}/instruments", data=body, headers={**auth_header("admin_a"), **XML})
    assert response.status_code == 201
    assert assert_valid_xml(response.data).find("cycle_events/cycle_event/notes").text == "registered"


# --- Kits ---------------------------------------------------------------------------------


def _kit_body(world, name, items, active=True):
    return {"name": name, "active": active,
            "items": [{"family_id": str(world.families[k]), "quantity": q} for k, q in items]}


def test_kit_create_and_versioned_update(client, world, auth_header, db_conn):
    procedure_id = uuid.uuid4()
    db_conn.execute(text("INSERT INTO cat_procedure_type (id, code, name) VALUES (:id, :c, 'Test procedure')"),
                    {"id": procedure_id, "c": f"zz_proc_{world.tag}"})
    name = f"New kit {world.tag}"
    created = client.post(f"{BASE}/kits", json=_kit_body(world, name, [("a", 2), ("b", 1)]),
                          headers=auth_header("supervisor_a"))
    assert created.status_code == 201
    v1 = created.get_json()
    assert (v1["version"], v1["family_count"], v1["total_expected"]) == (1, 2, 3)
    db_conn.execute(text("INSERT INTO procedure_kit (procedure_type_id, kit_id, is_default, active) VALUES (:p, :k, TRUE, TRUE)"),
                    {"p": procedure_id, "k": v1["id"]})
    same = client.put(f"{BASE}/kits/{v1['id']}", json=_kit_body(world, name, [("b", 1), ("a", 2)]),
                      headers=auth_header("admin_a"))
    assert same.status_code == 200 and same.get_json()["id"] == v1["id"]          # nothing changed: no version
    changed = client.put(f"{BASE}/kits/{v1['id']}", json=_kit_body(world, name, [("a", 3), ("b", 1)]),
                         headers=auth_header("admin_a"))
    assert changed.status_code == 201
    v2 = changed.get_json()
    assert (v2["version"], v2["superseded_kit_id"], v2["total_expected"]) == (2, v1["id"], 4)
    assert [p["code"] for p in v2["procedures"]] == [f"zz_proc_{world.tag}"]       # procedure link moved
    old = client.get(f"{BASE}/kits/{v1['id']}?expand=detail", headers=auth_header("admin_a")).get_json()
    assert old["active"] is False and old["procedures"] == []                       # history kept, inactive
    assert audit_actions(db_conn, v1["id"]) == ["CREATE_KIT", "SUPERSEDE_KIT"]
    assert audit_actions(db_conn, v2["id"]) == ["UPDATE_KIT"]


@pytest.mark.parametrize("items, reason", [
    ([], "empty_composition"),
    ([("a", 0)], "invalid_quantity"),
    ([("a", 40000)], "invalid_quantity"),
    ([("a", 1), ("a", 2)], "duplicate_family"),
])
def test_kit_composition_rules(client, world, auth_header, items, reason):
    response = client.post(f"{BASE}/kits", json=_kit_body(world, f"K {world.tag}", items), headers=auth_header("admin_a"))
    assert response.status_code == 422 and response.get_json()["details"]["reason"] == reason


def test_kit_unknown_family_and_other_tenant(client, world, auth_header):
    body = {"name": "K", "items": [{"family_id": str(uuid.uuid4()), "quantity": 1}]}
    response = client.post(f"{BASE}/kits", json=body, headers=auth_header("admin_a"))
    assert response.status_code == 422 and response.get_json()["details"]["reason"] == "invalid_family"
    foreign = world.kits_b["v1"]
    assert client.put(f"{BASE}/kits/{foreign}", json=_kit_body(world, "K", [("a", 1)]),
                      headers=auth_header("admin_a")).status_code == 404
    assert client.post(f"{BASE}/kits", json=_kit_body(world, "K", [("a", 1)]),
                       headers=auth_header("operator_a")).status_code == 403


def test_kit_xml_and_expanded_detail(client, world, auth_header):
    body = (f"<kit><name>XML kit {world.tag}</name><active>true</active><items>"
            f"<item><family_id>{world.families['a']}</family_id><quantity>2</quantity></item>"
            f"<item><family_id>{world.families['b']}</family_id><quantity>1</quantity></item></items></kit>").encode()
    assert_valid_xml(body)
    response = client.post(f"{BASE}/kits", data=body, headers={**auth_header("admin_a"), **XML})
    assert response.status_code == 201
    document = assert_valid_xml(response.data)
    assert [i.findtext("quantity") for i in document.findall("items/item")] == ["2", "1"]
    listing = client.get(f"{BASE}/kits?expand=detail", headers={**auth_header("admin_a"), "Accept": "application/xml"})
    assert_valid_xml(listing.data)


def test_existing_contract_xml_is_valid(client, world, auth_header):
    for path in ("families", "instruments", "kits", f"kits/{world.kits_a['v2']}"):
        response = client.get(f"{BASE}/{path}", headers={**auth_header("admin_a"), "Accept": "application/xml"})
        assert_valid_xml(response.data)
    error = client.post(f"{BASE}/kits", data=b"<kit><name>x</name></kit>", headers={**auth_header("admin_a"), **XML})
    assert error.status_code == 400 and assert_valid_xml(error.data).tag == "error_response"


def test_contract_documents_are_served(client):
    assert b"openapi: 3.0.3" in client.get(f"{BASE}/openapi.yaml").data
    assert client.get(f"{BASE}/schemas/catalog.xsd").status_code == 200
    assert b"swagger-ui" in client.get(f"{BASE}/docs").data
