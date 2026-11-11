"""GET / POST /api/v1/catalog/families."""

from __future__ import annotations

import json
import uuid
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import text

FAMILIES = "/api/v1/catalog/families"
FAMILY_FIELDS = ["id", "code", "name", "category_code", "active"]


def _post_json(client, headers, payload):
    return client.post(FAMILIES, headers={**headers, "Content-Type": "application/json"}, data=json.dumps(payload))


def _family_count(db_conn, code):
    return db_conn.execute(text("SELECT count(*) FROM instrument_family WHERE code = :c"), {"c": code}).scalar_one()


# --- GET ------------------------------------------------------------------------------


def test_get_families_json_fields_and_values(client, world, auth_header):
    response = client.get(FAMILIES, headers=auth_header("operator_a"))
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    families = response.get_json()
    assert isinstance(families, list)
    for item in families:
        assert list(item) == FAMILY_FIELDS
    by_code = {item["code"]: item for item in families}
    family_a = by_code[world.family_code("a")]
    assert family_a == {
        "id": str(world.families["a"]),
        "code": world.family_code("a"),
        "name": "Test family a",
        "category_code": world.category_code,
        "active": True,
    }
    assert by_code[world.family_code("c")]["active"] is False


def test_get_families_deterministic_order_by_code(client, world, auth_header):
    families = client.get(FAMILIES, headers=auth_header("operator_a")).get_json()
    codes = [item["code"] for item in families]
    assert codes == sorted(codes, key=lambda code: code.encode("utf-8"))
    ours = [code for code in codes if code.startswith(f"zz_test_{world.tag}_")]
    assert ours == [world.family_code("a"), world.family_code("b"), world.family_code("c")]


def test_get_families_xml(client, world, auth_header):
    response = client.get(FAMILIES, headers={**auth_header("operator_a"), "Accept": "application/xml"})
    assert response.status_code == 200
    assert response.mimetype == "application/xml"
    root = ET.fromstring(response.data)
    assert root.tag == "families"
    assert all(child.tag == "family" for child in root)
    match = [f for f in root if f.findtext("code") == world.family_code("c")][0]
    assert [child.tag for child in match] == FAMILY_FIELDS
    assert match.findtext("id") == str(world.families["c"])
    assert match.findtext("category_code") == world.category_code
    assert match.findtext("active") == "false"


def test_families_are_global_for_every_tenant(client, world, auth_header):
    codes_a = {f["code"] for f in client.get(FAMILIES, headers=auth_header("operator_a")).get_json()}
    codes_b = {f["code"] for f in client.get(FAMILIES, headers=auth_header("operator_b")).get_json()}
    assert codes_a == codes_b
    assert world.family_code("a") in codes_b


# --- POST: authorization ----------------------------------------------------------------


@pytest.mark.parametrize("user_key", ["operator_a", "supervisor_a", "foreign_role_a"])
def test_post_family_without_it_admin_is_403(client, world, auth_header, db_conn, user_key):
    code = world.family_code("forbidden")
    response = _post_json(client, auth_header(user_key), {"code": code, "name": "X", "category_code": world.category_code})
    assert response.status_code == 403
    assert response.get_json()["error"] == "forbidden"
    assert _family_count(db_conn, code) == 0


def test_post_family_without_token_is_401(client, world):
    response = client.post(FAMILIES, json={"code": "x", "name": "x", "category_code": "x"})
    assert response.status_code == 401


def test_post_family_with_it_admin_is_201(client, world, auth_header, db_conn):
    code = world.family_code("new")
    payload = {
        "code": code,
        "name": "Portaagujas Mayo-Hegar",
        "category_code": world.category_code,
        "identify_text": "Texto opcional",
        "classify_text": "  ",
        "active": False,
    }
    response = _post_json(client, auth_header("admin_a"), payload)
    assert response.status_code == 201
    body = response.get_json()
    assert list(body) == FAMILY_FIELDS
    assert body["code"] == code
    assert body["name"] == "Portaagujas Mayo-Hegar"
    assert body["category_code"] == world.category_code
    assert body["active"] is False
    row = db_conn.execute(
        text("SELECT id, identify_text, classify_text, function_text, active FROM instrument_family WHERE code = :c"),
        {"c": code},
    ).mappings().one()
    assert str(row["id"]) == body["id"]
    assert row["identify_text"] == "Texto opcional"
    assert row["classify_text"] is None
    assert row["function_text"] is None
    assert row["active"] is False


def test_post_family_effective_admin_role_not_primary_claim(client, world, auth_header):
    """multi_a has station_operator (JWT primary role) AND it_admin in user_role."""
    response = _post_json(
        client,
        auth_header("multi_a", role="station_operator"),
        {"code": world.family_code("multi"), "name": "Multi", "category_code": world.category_code},
    )
    assert response.status_code == 201


def test_post_family_role_claim_alone_does_not_grant_admin(client, world, auth_header):
    response = _post_json(
        client,
        auth_header("operator_a", role="it_admin"),
        {"code": world.family_code("claim"), "name": "Claim", "category_code": world.category_code},
    )
    assert response.status_code == 403


def test_post_family_active_defaults_to_true(client, world, auth_header):
    response = _post_json(
        client, auth_header("admin_a"), {"code": world.family_code("d"), "name": "D", "category_code": world.category_code}
    )
    assert response.status_code == 201
    assert response.get_json()["active"] is True


def test_post_family_trims_values(client, world, auth_header):
    response = _post_json(
        client,
        auth_header("admin_a"),
        {"code": f"  {world.family_code('trim')} ", "name": " Trim ", "category_code": f" {world.category_code} "},
    )
    assert response.status_code == 201
    assert response.get_json()["code"] == world.family_code("trim")
    assert response.get_json()["name"] == "Trim"


def test_post_family_is_visible_in_list(client, world, auth_header):
    code = world.family_code("listed")
    _post_json(client, auth_header("admin_a"), {"code": code, "name": "Listed", "category_code": world.category_code})
    codes = [f["code"] for f in client.get(FAMILIES, headers=auth_header("operator_a")).get_json()]
    assert code in codes


# --- POST: validation -------------------------------------------------------------------


def test_post_family_unknown_category_is_422(client, world, auth_header, db_conn):
    code = world.family_code("badcat")
    response = _post_json(client, auth_header("admin_a"), {"code": code, "name": "X", "category_code": "zz_missing_cat"})
    assert response.status_code == 422
    body = response.get_json()
    assert body["error"] == "invalid_reference"
    assert body["details"]["field"] == "category_code"
    assert _family_count(db_conn, code) == 0


def test_post_family_duplicate_code_is_409(client, world, auth_header, db_conn):
    code = world.family_code("a")
    response = _post_json(client, auth_header("admin_a"), {"code": code, "name": "Dup", "category_code": world.category_code})
    assert response.status_code == 409
    assert response.get_json()["error"] == "conflict"
    assert _family_count(db_conn, code) == 1


def test_post_family_duplicate_race_hits_unique_constraint(client, world, auth_header, monkeypatch):
    """If another request inserts the same code after our check, the DB constraint still yields 409."""
    from repositories import catalog_repository

    monkeypatch.setattr(catalog_repository, "family_code_exists", lambda connection, code: False)
    response = _post_json(
        client, auth_header("admin_a"), {"code": world.family_code("a"), "name": "Dup", "category_code": world.category_code}
    )
    assert response.status_code == 409
    # The savepoint was rolled back; the connection is still usable.
    assert client.get(FAMILIES, headers=auth_header("operator_a")).status_code == 200


def test_post_family_category_id_is_not_a_substitute(client, world, auth_header):
    response = _post_json(
        client, auth_header("admin_a"), {"code": world.family_code("cid"), "name": "X", "category_id": str(uuid.uuid4())}
    )
    assert response.status_code == 400
    assert response.get_json()["details"] == {"unknown_fields": ["category_id"]}


@pytest.mark.parametrize(
    "payload, expected_details",
    [
        ({"name": "X", "category_code": "c"}, {"missing_fields": ["code"]}),
        ({}, {"missing_fields": ["code", "name", "category_code"]}),
        ({"code": None, "name": "X", "category_code": "c"}, {"missing_fields": ["code"]}),
        ({"code": 123, "name": "X", "category_code": "c"}, {"field": "code"}),
        ({"code": "x", "name": ["X"], "category_code": "c"}, {"field": "name"}),
        ({"code": "x", "name": "X", "category_code": "c", "active": "true"}, {"field": "active"}),
        ({"code": "x", "name": "X", "category_code": "c", "active": 1}, {"field": "active"}),
        ({"code": "x", "name": "X", "category_code": "c", "active": None}, {"field": "active"}),
        ({"code": "x", "name": "X", "category_code": "c", "identify_text": 5}, {"field": "identify_text"}),
    ],
)
def test_post_family_invalid_body_is_400(client, world, auth_header, payload, expected_details):
    response = _post_json(client, auth_header("admin_a"), payload)
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] == "invalid_request"
    assert body["details"] == expected_details


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", "\"text\"", ""])
def test_post_family_malformed_json_is_400(client, world, auth_header, raw):
    response = client.post(FAMILIES, headers={**auth_header("admin_a"), "Content-Type": "application/json"}, data=raw)
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_request"


def test_post_family_unsupported_content_type_is_400(client, world, auth_header):
    response = client.post(FAMILIES, headers={**auth_header("admin_a"), "Content-Type": "text/plain"}, data="code=x")
    assert response.status_code == 400


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"code": "   ", "name": "X", "category_code": "c"}, "code"),
        ({"code": "x", "name": "", "category_code": "c"}, "name"),
        ({"code": "x" * 65, "name": "X", "category_code": "c"}, "code"),
        ({"code": "x", "name": "n" * 161, "category_code": "c"}, "name"),
    ],
)
def test_post_family_invalid_values_are_422(client, world, auth_header, payload, field):
    response = _post_json(client, auth_header("admin_a"), payload)
    assert response.status_code == 422
    body = response.get_json()
    assert body["error"] == "validation_error"
    assert body["details"]["field"] == field


def test_post_family_database_unavailable_is_503(world, tokens, redis_client):
    from conftest import make_settings
    from main import create_app

    app = create_app(
        make_settings(database_url="postgresql+psycopg://nobody:nothing@127.0.0.1:1/none"),
        redis_client=redis_client,
    )
    token = tokens.issue(world.users["admin_a"], world.institution_a)
    response = app.test_client().post(
        FAMILIES,
        headers={"Authorization": f"Bearer {token}"},
        json={"code": "x", "name": "x", "category_code": "x"},
    )
    assert response.status_code == 503
    body = response.get_json()
    assert body["error"] == "dependency_unavailable"
    assert body["details"] == {"dependency": "database"}
    text_body = response.get_data(as_text=True)
    assert "nothing" not in text_body and "SELECT" not in text_body
