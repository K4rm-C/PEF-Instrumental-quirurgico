"""Accept-based JSON/XML selection and JSON↔XML equivalence."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

LIST = "/api/v1/sessions"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


@pytest.mark.parametrize("accept, expected", [
    (None, "application/json"),
    ("*/*", "application/json"),
    ("application/json", "application/json"),
    ("application/xml", "application/xml"),
    ("application/xml;q=0.9, application/json;q=0.5", "application/xml"),
    ("text/html", "application/json"),
])
def test_accept_selects_format(client, world, as_user, accept, expected):
    headers = as_user("spd_a") if accept is None else as_user("spd_a", Accept=accept)
    response = client.get(LIST, headers=headers)
    assert response.status_code == 200 and response.mimetype == expected


def _xml_value(node):
    if node.get(XSI_NIL) == "true":
        return None
    return node.text


def test_list_json_and_xml_equivalent(client, world, as_user):
    as_json = client.get(LIST, headers=as_user("spd_a")).get_json()
    root = ET.fromstring(client.get(LIST, headers=as_user("spd_a", Accept="application/xml")).data)
    converted = []
    for node in root:
        item = {child.tag: _xml_value(child) for child in node}
        item["open_discrepancies"] = int(item["open_discrepancies"])
        converted.append(item)
    assert converted == as_json


def test_xml_error_envelope(client, world, as_user):
    response = client.get(f"{LIST}/not-a-uuid", headers=as_user("spd_a", Accept="application/xml"))
    assert response.status_code == 404 and response.mimetype == "application/xml"
    root = ET.fromstring(response.data)
    assert root.tag == "error_response"
    assert [c.tag for c in root] == ["error", "message", "correlation_id", "details"]
    assert root.findtext("error") == "not_found"


def test_xml_forbidden_for_admin(client, world, as_user):
    root = ET.fromstring(client.get(LIST, headers=as_user("admin_a", Accept="application/xml")).data)
    assert root.findtext("error") == "forbidden"
