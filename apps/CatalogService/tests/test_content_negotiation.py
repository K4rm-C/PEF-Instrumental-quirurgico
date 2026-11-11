"""JSON / XML equivalence (PEF-DS09: Android JSON, Desktop XML, same semantics)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

FAMILIES = "/api/v1/catalog/families"


def _xml_family(code: str, category: str, *, extra: str = "") -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f"<family><code>{code}</code><name>Portaagujas Mayo-Hegar</name>"
        f"<category_code>{category}</category_code>{extra}</family>"
    ).encode("utf-8")


def _xml_to_dict(element: ET.Element) -> dict:
    result = {}
    for child in element:
        result[child.tag] = child.text
    return result


@pytest.mark.parametrize(
    "accept, expected",
    [
        (None, "application/json"),
        ("*/*", "application/json"),
        ("application/json", "application/json"),
        ("application/xml", "application/xml"),
        ("application/xml;q=0.9, application/json;q=0.5", "application/xml"),
        ("application/json;q=0.4, application/xml;q=0.8", "application/xml"),
        ("text/html", "application/json"),
    ],
)
def test_accept_header_selects_format(client, world, auth_header, accept, expected):
    headers = auth_header("operator_a")
    if accept:
        headers["Accept"] = accept
    response = client.get(FAMILIES, headers=headers)
    assert response.status_code == 200
    assert response.mimetype == expected


def test_json_and_xml_lists_have_same_semantics(client, world, auth_header):
    headers = auth_header("operator_a")
    as_json = client.get(FAMILIES, headers=headers).get_json()
    as_xml = ET.fromstring(client.get(FAMILIES, headers={**headers, "Accept": "application/xml"}).data)
    converted = [
        {**_xml_to_dict(node), "active": node.findtext("active") == "true"} for node in as_xml
    ]
    assert converted == as_json


def test_post_json(client, world, auth_header):
    response = client.post(
        FAMILIES,
        headers=auth_header("admin_a"),
        json={"code": world.family_code("pj"), "name": "Portaagujas Mayo-Hegar", "category_code": world.category_code},
    )
    assert response.status_code == 201
    assert response.mimetype == "application/json"


def test_post_xml_returns_xml(client, world, auth_header, db_conn):
    from sqlalchemy import text

    code = world.family_code("px")
    response = client.post(
        FAMILIES,
        headers={**auth_header("admin_a"), "Content-Type": "application/xml", "Accept": "application/xml"},
        data=_xml_family(code, world.category_code, extra="<identify_text>Texto</identify_text><active>false</active>"),
    )
    assert response.status_code == 201
    assert response.mimetype == "application/xml"
    root = ET.fromstring(response.data)
    assert root.tag == "family"
    assert [c.tag for c in root] == ["id", "code", "name", "category_code", "active"]
    assert root.findtext("code") == code
    assert root.findtext("active") == "false"
    stored = db_conn.execute(
        text("SELECT identify_text, active FROM instrument_family WHERE code = :c"), {"c": code}
    ).one()
    assert tuple(stored) == ("Texto", False)


def test_post_json_and_xml_are_equivalent(client, world, auth_header):
    headers = auth_header("admin_a")
    json_response = client.post(
        FAMILIES,
        headers=headers,
        json={"code": world.family_code("eq1"), "name": "Portaagujas Mayo-Hegar", "category_code": world.category_code},
    ).get_json()
    xml_response = client.post(
        FAMILIES,
        headers={**headers, "Content-Type": "application/xml"},  # no Accept → JSON response
        data=_xml_family(world.family_code("eq2"), world.category_code),
    ).get_json()
    for body in (json_response, xml_response):
        body.pop("id")
        body.pop("code")
    assert json_response == xml_response


def test_post_xml_with_nil_optional_text(client, world, auth_header):
    response = client.post(
        FAMILIES,
        headers={**auth_header("admin_a"), "Content-Type": "application/xml"},
        data=_xml_family(
            world.family_code("nil"),
            world.category_code,
            extra='<function_text xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:nil="true"/>',
        ),
    )
    assert response.status_code == 201


@pytest.mark.parametrize(
    "body, detail_key",
    [
        (b"<family><code>x</code>", None),
        (b"<kit><code>x</code></kit>", "root"),
        (b"<family><code>x</code><code>y</code><name>n</name><category_code>c</category_code></family>", "field"),
        (b"<family><code><b>x</b></code><name>n</name><category_code>c</category_code></family>", "field"),
        (b"<family><code>x</code><name>n</name><category_code>c</category_code><active>yes</active></family>", "field"),
        (b"<family><code>x</code><name>n</name><category_id>c</category_id></family>", "unknown_fields"),
        (b"", None),
    ],
)
def test_post_invalid_xml_is_400(client, world, auth_header, body, detail_key):
    response = client.post(
        FAMILIES, headers={**auth_header("admin_a"), "Content-Type": "application/xml"}, data=body
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error"] == "invalid_request"
    if detail_key:
        assert detail_key in payload["details"]


def test_post_xml_rejects_entities(client, world, auth_header):
    billion_laughs = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
        b"<family><code>&lol2;</code><name>n</name><category_code>c</category_code></family>"
    )
    response = client.post(
        FAMILIES, headers={**auth_header("admin_a"), "Content-Type": "application/xml"}, data=billion_laughs
    )
    assert response.status_code == 400


def test_post_xml_rejects_external_entity(client, world, auth_header):
    xxe = (
        b'<?xml version="1.0"?><!DOCTYPE family [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b"<family><code>&xxe;</code><name>n</name><category_code>c</category_code></family>"
    )
    response = client.post(FAMILIES, headers={**auth_header("admin_a"), "Content-Type": "application/xml"}, data=xxe)
    assert response.status_code == 400


def test_post_body_too_large_is_413(client, world, auth_header):
    response = client.post(
        FAMILIES,
        headers={**auth_header("admin_a"), "Content-Type": "application/json"},
        data=b'{"code": "' + b"x" * 70_000 + b'"}',
    )
    assert response.status_code == 413
    assert response.get_json()["error"] == "payload_too_large"


def test_xml_error_on_forbidden_post(client, world, auth_header):
    response = client.post(
        FAMILIES,
        headers={**auth_header("operator_a"), "Content-Type": "application/xml", "Accept": "application/xml"},
        data=_xml_family(world.family_code("f"), world.category_code),
    )
    assert response.status_code == 403
    root = ET.fromstring(response.data)
    assert root.tag == "error_response"
    assert [c.tag for c in root] == ["error", "message", "correlation_id", "details"]
    assert root.findtext("error") == "forbidden"
