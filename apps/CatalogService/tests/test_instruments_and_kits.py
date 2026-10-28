"""GET /instruments, GET /kits, GET /kits/{id} — including tenant isolation."""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET

import pytest

INSTRUMENTS = "/api/v1/catalog/instruments"
KITS = "/api/v1/catalog/kits"
INSTRUMENT_FIELDS = ["id", "internal_code", "family_id", "family_code", "cycle_status_code", "institution_id", "active"]
KIT_FIELDS = ["id", "name", "version", "active", "institution_id"]
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


# --- Instruments ------------------------------------------------------------------------


def test_instruments_json_only_own_institution(client, world, auth_header):
    response = client.get(INSTRUMENTS, headers=auth_header("operator_a"))
    assert response.status_code == 200
    instruments = response.get_json()
    assert {item["id"] for item in instruments} == {str(i) for i in world.instruments_a}
    assert all(item["institution_id"] == str(world.institution_a) for item in instruments)
    for item in instruments:
        assert list(item) == INSTRUMENT_FIELDS
        assert item["cycle_status_code"] == world.cycle_status_code


def test_instruments_order_family_code_then_internal_code_nulls_last(client, world, auth_header):
    instruments = client.get(INSTRUMENTS, headers=auth_header("operator_a")).get_json()
    assert [(i["family_code"], i["internal_code"]) for i in instruments] == [
        (world.family_code("a"), f"A-{world.tag}-001"),
        (world.family_code("a"), None),
        (world.family_code("b"), f"A-{world.tag}-002"),
    ]
    assert instruments[0]["family_id"] == str(world.families["a"])


def test_instruments_other_tenant_sees_only_its_stock(client, world, auth_header):
    instruments = client.get(INSTRUMENTS, headers=auth_header("operator_b")).get_json()
    assert {item["id"] for item in instruments} == {str(i) for i in world.instruments_b}


def test_instruments_xml(client, world, auth_header):
    response = client.get(INSTRUMENTS, headers={**auth_header("operator_a"), "Accept": "application/xml"})
    assert response.status_code == 200
    root = ET.fromstring(response.data)
    assert root.tag == "instruments"
    assert len(root) == 3
    assert [child.tag for child in root[0]] == INSTRUMENT_FIELDS
    null_code = root[1].find("internal_code")
    assert null_code.get(XSI_NIL) == "true" and not null_code.text
    assert root[0].findtext("active") == "true"


@pytest.mark.parametrize("query", ["?institution_id={b}", "?institution={b}"])
def test_instruments_ignore_institution_in_query(client, world, auth_header, query):
    url = INSTRUMENTS + query.format(b=world.institution_b)
    instruments = client.get(url, headers=auth_header("operator_a")).get_json()
    assert {item["id"] for item in instruments} == {str(i) for i in world.instruments_a}


# --- Kits list --------------------------------------------------------------------------


def test_kits_list_only_own_institution_and_ordered(client, world, auth_header):
    response = client.get(KITS, headers=auth_header("operator_a"))
    assert response.status_code == 200
    kits = response.get_json()
    assert [k["id"] for k in kits] == [str(world.kits_a["v1"]), str(world.kits_a["v2"])]
    assert [k["version"] for k in kits] == [1, 2]
    for kit in kits:
        assert list(kit) == KIT_FIELDS
        assert kit["institution_id"] == str(world.institution_a)
        assert "items" not in kit


def test_kits_list_other_tenant(client, world, auth_header):
    kits = client.get(KITS + f"?institution_id={world.institution_a}", headers=auth_header("operator_b")).get_json()
    assert [k["id"] for k in kits] == [str(world.kits_b["v1"])]


def test_kits_list_xml(client, world, auth_header):
    response = client.get(KITS, headers={**auth_header("operator_a"), "Accept": "application/xml"})
    root = ET.fromstring(response.data)
    assert root.tag == "kits"
    assert [child.tag for child in root] == ["kit", "kit"]
    assert root[0].findtext("version") == "1"


# --- Kit detail -------------------------------------------------------------------------


def test_kit_detail_json(client, world, auth_header):
    kit_id = world.kits_a["v2"]
    response = client.get(f"{KITS}/{kit_id}", headers=auth_header("operator_a"))
    assert response.status_code == 200
    assert response.get_json() == {
        "id": str(kit_id),
        "name": f"Test Kit {world.tag}",
        "version": 2,
        "active": True,
        "institution_id": str(world.institution_a),
        "items": [
            {"family_id": str(world.families["a"]), "family_code": world.family_code("a"), "quantity": 2},
            {"family_id": str(world.families["b"]), "family_code": world.family_code("b"), "quantity": 3},
            {"family_id": str(world.families["c"]), "family_code": world.family_code("c"), "quantity": 1},
        ],
    }


def test_kit_detail_xml(client, world, auth_header):
    kit_id = world.kits_a["v2"]
    response = client.get(f"{KITS}/{kit_id}", headers={**auth_header("operator_a"), "Accept": "application/xml"})
    root = ET.fromstring(response.data)
    assert root.tag == "kit"
    assert [child.tag for child in root] == KIT_FIELDS + ["items"]
    items = root.find("items")
    assert [child.tag for child in items] == ["item", "item", "item"]
    assert [child.tag for child in items[0]] == ["family_id", "family_code", "quantity"]
    assert items[1].findtext("quantity") == "3"


def test_kit_detail_other_institution_is_404(client, world, auth_header):
    response = client.get(f"{KITS}/{world.kits_b['v1']}", headers=auth_header("admin_a"))
    assert response.status_code == 404
    body = response.get_json()
    assert body["error"] == "not_found"
    assert str(world.kits_b["v1"]) not in response.get_data(as_text=True)


def test_kit_detail_unknown_id_is_404(client, world, auth_header):
    response = client.get(f"{KITS}/{uuid.uuid4()}", headers=auth_header("operator_a"))
    assert response.status_code == 404


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "123", "51515151-0000-4000-8000-00000000000Z"])
def test_kit_detail_invalid_uuid_is_404(client, world, auth_header, bad_id):
    response = client.get(f"{KITS}/{bad_id}", headers=auth_header("operator_a"))
    assert response.status_code == 404
    assert response.get_json()["error"] == "not_found"


def test_kit_detail_other_institution_and_unknown_are_indistinguishable(client, world, auth_header):
    headers = auth_header("operator_a")
    foreign = client.get(f"{KITS}/{world.kits_b['v1']}", headers=headers).get_json()
    unknown = client.get(f"{KITS}/{uuid.uuid4()}", headers=headers).get_json()
    foreign.pop("correlation_id")
    unknown.pop("correlation_id")
    assert foreign == unknown


def test_kit_endpoints_require_token(client, world):
    assert client.get(KITS).status_code == 401
    assert client.get(f"{KITS}/{world.kits_a['v1']}").status_code == 401
    assert client.get(INSTRUMENTS).status_code == 401
