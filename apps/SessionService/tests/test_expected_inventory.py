"""GET /api/v1/sessions/{id}/expected-inventory."""

from __future__ import annotations

import xml.etree.ElementTree as ET

BASE = "/api/v1/sessions"


def test_expected_inventory_lines_and_sources(client, world, as_user):
    response = client.get(f"{BASE}/{world.sessions['a3'].id}/expected-inventory", headers=as_user("op_a"))
    assert response.status_code == 200
    f, tag = world.families, world.tag
    assert response.get_json() == [
        {"family_id": str(f["a"]), "family_code": f"zz_ses_{tag}_a", "expected_quantity": 2, "source": "kit_snapshot"},
        {"family_id": str(f["b"]), "family_code": f"zz_ses_{tag}_b", "expected_quantity": 1,
         "source": "schedule_additional"},
        {"family_id": str(f["c"]), "family_code": f"zz_ses_{tag}_c", "expected_quantity": 3, "source": "live_add"},
        {"family_id": str(f["d"]), "family_code": f"zz_ses_{tag}_d", "expected_quantity": 1, "source": "manual"},
    ]


def test_expected_inventory_has_no_stock_or_kit_data(client, world, as_user):
    lines = client.get(f"{BASE}/{world.sessions['a3'].id}/expected-inventory", headers=as_user("spd_a")).get_json()
    for line in lines:
        assert list(line) == ["family_id", "family_code", "expected_quantity", "source"]


def test_expected_inventory_empty(client, world, as_user):
    response = client.get(f"{BASE}/{world.sessions['a1'].id}/expected-inventory", headers=as_user("op_a"))
    assert response.status_code == 200 and response.get_json() == []


def test_expected_inventory_xml(client, world, as_user):
    response = client.get(f"{BASE}/{world.sessions['a3'].id}/expected-inventory",
                          headers=as_user("spd_a", Accept="application/xml"))
    root = ET.fromstring(response.data)
    assert root.tag == "expected_inventory"
    assert [c.tag for c in root] == ["line"] * 4
    assert root[2].findtext("source") == "live_add"
    assert root[0].findtext("expected_quantity") == "2"


def test_expected_inventory_tenant_and_ownership(client, world, as_user):
    path_b = f"{BASE}/{world.sessions['b1'].id}/expected-inventory"
    assert client.get(path_b, headers=as_user("spd_a")).status_code == 404
    assert client.get(path_b, headers=as_user("spd_b")).status_code == 200
    path_a2 = f"{BASE}/{world.sessions['a2'].id}/expected-inventory"
    assert client.get(path_a2, headers=as_user("op_a")).status_code == 404
    assert client.get(f"{BASE}/not-a-uuid/expected-inventory", headers=as_user("spd_a")).status_code == 404
