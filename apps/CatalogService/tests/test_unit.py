"""Pure unit tests (no PostgreSQL / Redis needed)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from config import Settings
from errors import ApiError
from serializers import XSI_NIL, to_xml
from services.catalog_service import validate_family_create


def test_to_xml_lists_nulls_and_booleans():
    root = ET.fromstring(
        to_xml(
            {"id": "k1", "active": False, "version": 2, "note": None, "items": [{"quantity": 2}, {"quantity": 1}]},
            "kit",
        )
    )
    assert root.tag == "kit"
    assert root.findtext("active") == "false"
    assert root.findtext("version") == "2"
    assert root.find("note").get(XSI_NIL) == "true"
    assert [item.findtext("quantity") for item in root.find("items")] == ["2", "1"]
    assert [item.tag for item in root.find("items")] == ["item", "item"]


def test_to_xml_escapes_text():
    root = ET.fromstring(to_xml([{"name": "<b>&</b>"}], "families"))
    assert root[0].tag == "family"
    assert root[0].findtext("name") == "<b>&</b>"


def test_validate_family_create_normalizes():
    values = validate_family_create(
        {"code": " c ", "name": " n ", "category_code": " k ", "identify_text": "  ", "function_text": " f "}
    )
    assert values == {
        "code": "c",
        "name": "n",
        "category_code": "k",
        "identify_text": None,
        "classify_text": None,
        "function_text": "f",
        "active": True,
    }


@pytest.mark.parametrize(
    "payload, status",
    [
        ({"code": "c", "name": "n"}, 400),
        ({"code": "c", "name": "n", "category_code": "k", "extra": 1}, 400),
        ({"code": "c", "name": "n", "category_code": "k", "active": "false"}, 400),
        ({"code": "", "name": "n", "category_code": "k"}, 422),
    ],
)
def test_validate_family_create_errors(payload, status):
    with pytest.raises(ApiError) as excinfo:
        validate_family_create(payload)
    assert excinfo.value.status == status


def test_settings_require_secret():
    with pytest.raises(RuntimeError):
        Settings(database_url="postgresql+psycopg://x", redis_url="redis://x", jwt_secret_key="")


def test_settings_reject_unsupported_algorithm():
    with pytest.raises(RuntimeError):
        Settings(database_url="postgresql+psycopg://x", redis_url="redis://x", jwt_secret_key="s", jwt_algorithm="none")
