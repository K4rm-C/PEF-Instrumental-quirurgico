"""Inactive kits are an it_admin concern; XML request bodies are validated against their XSD.

Real PostgreSQL/Redis (rolled-back transaction, see conftest). Calls go straight to CatalogService,
the way any client other than the portal would make them.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from lxml import etree
from sqlalchemy import text

BASE = "/api/v1/catalog"
SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
RESPONSE_XSD = etree.XMLSchema(etree.parse(str(SCHEMAS / "catalog.xsd")))
REQUEST_XSD = etree.XMLSchema(etree.parse(str(SCHEMAS / "catalog-requests.xsd")))
XML = {"Content-Type": "application/xml", "Accept": "application/xml"}
XML_IN = {"Content-Type": "application/xml"}


@pytest.fixture
def retired_kit(db_conn, world):
    kit_id = uuid.uuid4()
    db_conn.execute(text("INSERT INTO kit (id, name, version, active, institution_id) "
                         "VALUES (:id, :name, 1, FALSE, :inst)"),
                    {"id": kit_id, "name": f"Retired kit {world.tag}", "inst": world.institution_a})
    db_conn.execute(text("INSERT INTO kit_item (kit_id, family_id, quantity) VALUES (:k, :f, 2)"),
                    {"k": kit_id, "f": world.families["a"]})
    return str(kit_id)


def kit_count(db_conn, world) -> int:
    return db_conn.execute(text("SELECT count(*) FROM kit WHERE institution_id = :i"),
                           {"i": world.institution_a}).scalar_one()


# --- Kit visibility -------------------------------------------------------------------------


@pytest.mark.parametrize("query", ["", "?expand=detail"])
@pytest.mark.parametrize("user", ["supervisor_a", "operator_a"])
def test_non_admin_lists_only_active_kits(client, world, auth_header, retired_kit, query, user):
    ids = [kit["id"] for kit in client.get(f"{BASE}/kits{query}", headers=auth_header(user)).get_json()]
    assert retired_kit not in ids
    assert set(ids) == {str(world.kits_a["v1"]), str(world.kits_a["v2"])}


@pytest.mark.parametrize("query", ["", "?expand=detail"])
def test_admin_still_lists_inactive_kits(client, world, auth_header, retired_kit, query):
    kits = client.get(f"{BASE}/kits{query}", headers=auth_header("admin_a")).get_json()
    assert {kit["id"]: kit["active"] for kit in kits}[retired_kit] is False


@pytest.mark.parametrize("query", ["", "?expand=detail"])
def test_supervisor_cannot_read_an_inactive_kit(client, world, auth_header, retired_kit, query):
    response = client.get(f"{BASE}/kits/{retired_kit}{query}", headers=auth_header("supervisor_a"))
    assert response.status_code == 404 and response.get_json()["error"] == "not_found"
    xml = client.get(f"{BASE}/kits/{retired_kit}{query}", headers={**auth_header("supervisor_a"), **XML})
    assert xml.status_code == 404 and RESPONSE_XSD.validate(etree.fromstring(xml.data))
    assert client.get(f"{BASE}/kits/{retired_kit}{query}", headers=auth_header("admin_a")).status_code == 200
    # An active kit is still readable by the supervisor.
    assert client.get(f"{BASE}/kits/{world.kits_a['v2']}{query}", headers=auth_header("supervisor_a")).status_code == 200


def test_supervisor_cannot_edit_an_inactive_kit(client, world, auth_header, retired_kit, db_conn):
    before = kit_count(db_conn, world)
    body = {"name": f"Revived {world.tag}", "active": True, "items": [{"family_id": str(world.families["a"]), "quantity": 1}]}
    response = client.put(f"{BASE}/kits/{retired_kit}", json=body, headers=auth_header("supervisor_a"))
    assert response.status_code == 404
    assert kit_count(db_conn, world) == before
    # Administration permissions are unchanged: it_admin can still create a new version from it.
    assert client.put(f"{BASE}/kits/{retired_kit}", json=body, headers=auth_header("admin_a")).status_code == 201


def test_supervisor_deactivating_a_kit_keeps_working(client, world, auth_header):
    kit_id = world.kits_a["v2"]
    detail = client.get(f"{BASE}/kits/{kit_id}", headers=auth_header("supervisor_a")).get_json()
    body = {"name": detail["name"], "active": False,
            "items": [{"family_id": item["family_id"], "quantity": item["quantity"]} for item in detail["items"]]}
    response = client.put(f"{BASE}/kits/{kit_id}", json=body, headers=auth_header("supervisor_a"))
    assert response.status_code == 201 and response.get_json()["active"] is False
    listed = [kit["id"] for kit in client.get(f"{BASE}/kits", headers=auth_header("supervisor_a")).get_json()]
    assert str(kit_id) not in listed and response.get_json()["id"] not in listed


# --- XSD validation of XML request bodies ----------------------------------------------------


def schema_error(response):
    assert response.status_code == 400
    document = etree.fromstring(response.data)
    assert RESPONSE_XSD.validate(document), "error_response must conform to catalog.xsd"
    assert document.findtext("error") == "invalid_request"
    assert document.findtext("details/reason") == "xsd_validation_failed"
    assert document.findtext("details/schema") == f"{BASE}/schemas/catalog-requests.xsd"
    assert document.find("details/errors/item/message").text
    return document


def test_unknown_instrument_element_is_rejected_by_the_xsd(client, world, auth_header, db_conn):
    body = (f"<instrument><internal_code>XSD-{world.tag}</internal_code><family_id>{world.families['a']}</family_id>"
            f"<cycle_status_code>{world.cycle_status_code}</cycle_status_code><institution_id>{world.institution_b}"
            "</institution_id></instrument>").encode()
    document = schema_error(client.post(f"{BASE}/instruments", data=body, headers={**auth_header("admin_a"), **XML}))
    assert [item.text for item in document.findall("details/unknown_fields/item")] == ["institution_id"]
    assert not db_conn.execute(text("SELECT 1 FROM instrument WHERE internal_code = :c"),
                               {"c": f"XSD-{world.tag}"}).first()


@pytest.mark.parametrize("body", [
    # item without quantity
    "<kit><name>K</name><items><item><family_id>{fam}</family_id></item></items></kit>",
    # attributes are not part of the contract
    "<kit><name lang='en'>K</name><items><item><family_id>{fam}</family_id><quantity>1</quantity></item></items></kit>",
    # mixed content
    "<kit>text<name>K</name><items><item><family_id>{fam}</family_id><quantity>1</quantity></item></items></kit>",
])
def test_malformed_kit_documents_are_400(client, world, auth_header, db_conn, body):
    before = kit_count(db_conn, world)
    payload = body.format(fam=world.families["a"]).encode()
    assert not REQUEST_XSD.validate(etree.fromstring(payload))
    schema_error(client.post(f"{BASE}/kits", data=payload, headers={**auth_header("admin_a"), **XML}))
    assert kit_count(db_conn, world) == before


@pytest.mark.parametrize("path, body", [
    # Caught by the structural checks that run before the XSD (they keep their own messages).
    ("families/{family}", "<family><name>x</name><function_text>a<b>b</b></function_text></family>"),
    ("kits", "<kit><name>K</name><items><entry><family_id>{fam}</family_id><quantity>1</quantity></entry></items></kit>"),
])
def test_structural_errors_are_400(client, world, auth_header, path, body):
    url = f"{BASE}/{path.format(family=world.families['b'])}"
    data = body.format(fam=world.families["a"]).encode()
    send = client.put if "{family}" in path else client.post
    response = send(url, data=data, headers={**auth_header("admin_a"), **XML})
    document = etree.fromstring(response.data)
    assert response.status_code == 400 and document.findtext("error") == "invalid_request"
    assert RESPONSE_XSD.validate(document) and document.findtext("details/field")


def test_family_create_unknown_field_json_error_details(client, world, auth_header):
    body = f"<family><code>X{world.tag}</code><name>n</name><category_code>{world.category_code}</category_code><id>1</id></family>"
    response = client.post(f"{BASE}/families", data=body.encode(), headers={**auth_header("admin_a"), **XML_IN})
    payload = response.get_json()
    assert response.status_code == 400 and payload["details"]["reason"] == "xsd_validation_failed"
    assert payload["details"]["unknown_fields"] == ["id"]


def test_business_rules_answer_the_same_reason_for_json_and_xml(client, world, auth_header):
    fam = world.families["a"]
    xml = (f"<kit><name>Zero {world.tag}</name><items><item><family_id>{fam}</family_id><quantity>0</quantity>"
           "</item></items></kit>").encode()
    assert REQUEST_XSD.validate(etree.fromstring(xml))
    from_xml = client.post(f"{BASE}/kits", data=xml, headers={**auth_header("admin_a"), **XML_IN})
    from_json = client.post(f"{BASE}/kits", json={"name": f"Zero {world.tag}", "items": [{"family_id": str(fam), "quantity": 0}]},
                            headers=auth_header("admin_a"))
    assert from_xml.status_code == from_json.status_code == 422
    assert from_xml.get_json()["details"]["reason"] == from_json.get_json()["details"]["reason"] == "invalid_quantity"


def test_valid_xml_bodies_still_work_and_the_request_schema_is_served(client, world, auth_header):
    body = (f"<instrument><internal_code>XOK-{world.tag}</internal_code><family_id>{world.families['a']}</family_id>"
            f"<cycle_status_code>{world.cycle_status_code}</cycle_status_code><active>1</active></instrument>").encode()
    assert REQUEST_XSD.validate(etree.fromstring(body))
    response = client.post(f"{BASE}/instruments", data=body, headers={**auth_header("supervisor_a"), **XML})
    assert response.status_code == 201 and RESPONSE_XSD.validate(etree.fromstring(response.data))
    served = client.get(f"{BASE}/schemas/catalog-requests.xsd")
    assert served.status_code == 200 and served.data == (SCHEMAS / "catalog-requests.xsd").read_bytes()
