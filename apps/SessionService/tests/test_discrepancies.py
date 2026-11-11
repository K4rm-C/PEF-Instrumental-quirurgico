"""GET /api/v1/sessions/{id}/discrepancies."""

from __future__ import annotations

import xml.etree.ElementTree as ET

BASE = "/api/v1/sessions"
FIELDS = ["id", "reason_code", "family_id", "family_code", "expected_quantity", "detected_quantity", "resolved",
          "resolved_at", "origin_event_id", "description"]
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def test_discrepancies_order_and_values(client, world, as_user):
    response = client.get(f"{BASE}/{world.sessions['a3'].id}/discrepancies", headers=as_user("spd_a"))
    assert response.status_code == 200
    items = response.get_json()
    d = world.discrepancies
    # Open first (with family before without), then resolved.
    assert [item["id"] for item in items] == [str(d["open_a"]), str(d["open_nofamily"]), str(d["resolved_b"])]
    for item in items:
        assert list(item) == FIELDS
    assert items[0] == {
        "id": str(d["open_a"]), "reason_code": "shortage", "family_id": str(world.families["a"]),
        "family_code": f"zz_ses_{world.tag}_a", "expected_quantity": 2, "detected_quantity": 1,
        "resolved": False, "resolved_at": None, "origin_event_id": str(world.events["manual_modern"]),
        "description": "Faltante en Portaagujas: esperado 2, reportado 1.",
    }
    assert items[1]["reason_code"] is None and items[1]["family_id"] is None and items[1]["family_code"] is None
    resolved = items[2]
    assert resolved["resolved"] is True and resolved["resolved_at"] == "2026-10-01T13:00:00Z"
    assert resolved["reason_code"] == "surplus" and resolved["origin_event_id"] is None
    # Stored text verbatim (no translation, no reinterpretation of the legacy resolution note).
    assert resolved["description"] == "Extra on Family b.\nSPD resolution (resolved): encontrada en charola auxiliar"


def test_discrepancies_operator_owner(client, world, as_user):
    assert len(client.get(f"{BASE}/{world.sessions['a3'].id}/discrepancies", headers=as_user("op_a")).get_json()) == 3
    assert client.get(f"{BASE}/{world.sessions['a3'].id}/discrepancies", headers=as_user("op_a2")).status_code == 404


def test_discrepancies_empty(client, world, as_user):
    response = client.get(f"{BASE}/{world.sessions['a1'].id}/discrepancies", headers=as_user("spd_a"))
    assert response.status_code == 200 and response.get_json() == []


def test_discrepancies_xml(client, world, as_user):
    response = client.get(f"{BASE}/{world.sessions['a3'].id}/discrepancies",
                          headers=as_user("spd_a", Accept="application/xml"))
    root = ET.fromstring(response.data)
    assert root.tag == "discrepancies" and [c.tag for c in root] == ["discrepancy"] * 3
    assert [c.tag for c in root[0]] == FIELDS
    assert root[0].find("resolved_at").get(XSI_NIL) == "true"
    assert root[0].findtext("resolved") == "false"
    assert root[2].findtext("description") == "Extra on Family b.\nSPD resolution (resolved): encontrada en charola auxiliar"


def test_discrepancies_cross_tenant(client, world, as_user):
    path_b = f"{BASE}/{world.sessions['b1'].id}/discrepancies"
    assert client.get(path_b, headers=as_user("spd_a")).status_code == 404
    assert [d["id"] for d in client.get(path_b, headers=as_user("spd_b")).get_json()] == [
        str(world.discrepancies["b_open"])]
