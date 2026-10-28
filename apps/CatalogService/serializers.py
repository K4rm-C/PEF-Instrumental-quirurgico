"""JSON / XML representation (PEF-DS09: Android → JSON, Desktop → XML, same semantics).

Routes and services work with one logical representation (dicts, lists, str, int, bool,
None). This module is the only place that knows about wire formats:

- ``render``: logical value → JSON or XML response, chosen from ``Accept``.
- ``parse_family_create_body`` / ``parse_resource_body``: JSON or XML request body → logical
  dict. XML bodies are validated against ``schemas/catalog-requests.xsd`` (published at
  ``/api/v1/catalog/schemas/catalog-requests.xsd``); a non-conforming document is a 400.

XML conventions (documented in PEF-DS09 §1.3.3):
- element names equal JSON field names;
- a list is a container element with one child per entry (``families/family``,
  ``items/item``);
- booleans are ``true``/``false``; ``null`` is an empty element with ``xsi:nil="true"``.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Any

from flask import Response, g, request
from lxml import etree as lxml_etree

from errors import ApiError

JSON_MIMETYPE = "application/json"
XML_MIMETYPE = "application/xml"
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
XSI_NIL = f"{{{XSI_NAMESPACE}}}nil"

ET.register_namespace("xsi", XSI_NAMESPACE)

# Child element name used for entries of a list, keyed by the list's element name.
LIST_ITEM_NAMES = {
    "families": "family",
    "instruments": "instrument",
    "kits": "kit",
    "items": "item",
    # Management endpoints (DS09 §1.3.3 complement v0.1.2).
    "procedures": "procedure",
    "cycle_events": "cycle_event",
    "categories": "category",
    "cycle_statuses": "cycle_status",
}

_DTD_PATTERN = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)

REQUEST_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "catalog-requests.xsd"
REQUEST_SCHEMA_URL = "/api/v1/catalog/schemas/catalog-requests.xsd"
_XS = "{http://www.w3.org/2001/XMLSchema}"
MAX_REPORTED_SCHEMA_ERRORS = 10


# --- Content negotiation ----------------------------------------------------------------


def response_format() -> str:
    """'xml' only when the client prefers application/xml; JSON otherwise (also for */*)."""
    best = request.accept_mimetypes.best_match([JSON_MIMETYPE, XML_MIMETYPE])
    return "xml" if best == XML_MIMETYPE else "json"


def render(data: Any, *, root: str, status: int = 200) -> Response:
    if response_format() == "xml":
        body = to_xml(data, root)
        return Response(body, status=status, content_type=f"{XML_MIMETYPE}; charset=utf-8")
    body = json.dumps(data, ensure_ascii=False)
    return Response(body, status=status, mimetype=JSON_MIMETYPE)


def error_response(exc: ApiError) -> Response:
    payload = {
        "error": exc.error,
        "message": exc.message,
        "correlation_id": getattr(g, "correlation_id", None),
        "details": exc.details,
    }
    return render(payload, root="error_response", status=exc.status)


# --- XML output -------------------------------------------------------------------------


def to_xml(data: Any, root: str) -> bytes:
    element = ET.Element(root)
    _fill_element(element, data)
    return ET.tostring(element, encoding="utf-8", xml_declaration=True)


def _fill_element(element: ET.Element, value: Any) -> None:
    if value is None:
        element.set(XSI_NIL, "true")
    elif isinstance(value, bool):
        element.text = "true" if value else "false"
    elif isinstance(value, dict):
        for key, child_value in value.items():
            _fill_element(ET.SubElement(element, str(key)), child_value)
    elif isinstance(value, (list, tuple)):
        item_name = LIST_ITEM_NAMES.get(element.tag, "item")
        for entry in value:
            _fill_element(ET.SubElement(element, item_name), entry)
    else:
        element.text = str(value)


# --- Request bodies ---------------------------------------------------------------------

FAMILY_CREATE_FIELDS = (
    "code",
    "name",
    "category_code",
    "identify_text",
    "classify_text",
    "function_text",
    "active",
)
_XML_BOOLEANS = {"true": True, "1": True, "false": False, "0": False}


def parse_family_create_body() -> dict[str, Any]:
    """Return the POST /families body as a logical dict, regardless of wire format."""
    mimetype = request.mimetype
    if mimetype == JSON_MIMETYPE:
        return _parse_json_object()
    if mimetype == XML_MIMETYPE:
        return _parse_family_xml(request.get_data(cache=False))
    raise ApiError.invalid_request(
        "Content-Type must be application/json or application/xml",
        details={"content_type": mimetype or None},
    )


def _parse_json_object() -> dict[str, Any]:
    raw = request.get_data(cache=False)
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, ValueError):
        raise ApiError.invalid_request("Request body is not valid JSON") from None
    if not isinstance(payload, dict):
        raise ApiError.invalid_request("Request body must be a JSON object")
    return payload


def _parse_xml_document(raw: bytes) -> ET.Element:
    if not raw or not raw.strip():
        raise ApiError.invalid_request("Request body is empty")
    # No DTDs at all: rules out entity expansion and external entity tricks.
    if _DTD_PATTERN.search(raw):
        raise ApiError.invalid_request("XML DTD / entity declarations are not allowed")
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise ApiError.invalid_request("Request body is not well-formed XML") from None


# --- XSD validation of XML request bodies ------------------------------------------------


@lru_cache(maxsize=1)
def _request_schema() -> tuple[lxml_etree.XMLSchema, dict[str, frozenset[str]]]:
    """The request XSD and, per root element, the child elements it accepts."""
    document = lxml_etree.parse(str(REQUEST_SCHEMA_PATH))
    accepted = {
        element.get("name"): frozenset(child.get("name") for child in element.iterfind(f"{_XS}complexType/{_XS}all/{_XS}element"))
        for element in document.getroot().iterfind(f"{_XS}element")
    }
    return lxml_etree.XMLSchema(document), accepted


def validate_request_xml(raw: bytes, root: ET.Element) -> None:
    """Raise 400 (reason xsd_validation_failed) when the body does not conform to the request XSD.

    Runs after the well-formedness / root / duplicate / type checks, which keep their own messages.
    """
    schema, accepted = _request_schema()
    # DTDs were already refused; no entity resolution and no network access in any case.
    parser = lxml_etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    document = lxml_etree.fromstring(raw, parser)
    if schema.validate(document):
        return
    details: dict[str, Any] = {
        "reason": "xsd_validation_failed",
        "schema": REQUEST_SCHEMA_URL,
        "errors": [{"line": entry.line, "message": entry.message}
                   for entry in list(schema.error_log)[:MAX_REPORTED_SCHEMA_ERRORS]],
    }
    unknown = sorted({child.tag for child in root} - accepted.get(root.tag, frozenset()))
    if unknown:
        details["unknown_fields"] = unknown
    raise ApiError.invalid_request("XML body does not conform to the request schema", details=details)


def _parse_family_xml(raw: bytes) -> dict[str, Any]:
    root = _parse_xml_document(raw)
    if root.tag != "family":
        raise ApiError.invalid_request("XML root element must be <family>", details={"root": root.tag})

    payload: dict[str, Any] = {}
    for child in root:
        name = child.tag
        if name in payload:
            raise ApiError.invalid_request("Duplicate XML element", details={"field": name})
        if len(child):
            raise ApiError.invalid_request("XML field must not contain nested elements", details={"field": name})
        if child.get(XSI_NIL) in ("true", "1"):
            payload[name] = None
            continue
        text = child.text or ""
        if name == "active":
            lexical = text.strip()
            if lexical not in _XML_BOOLEANS:
                raise ApiError.invalid_request("Field must be a boolean (true/false)", details={"field": "active"})
            payload[name] = _XML_BOOLEANS[lexical]
        else:
            payload[name] = text
    validate_request_xml(raw, root)
    return payload


# --- Management request bodies (DS09 §1.3.3 complement v0.1.2) ----------------------------
# Same XML rules as POST /families: root element = resource name, flat fields, booleans
# true/false/1/0, xsi:nil for null; a kit's composition is <items><item>...</item></items>.

_XML_INTEGER_FIELDS = {"quantity"}


def parse_resource_body(root_name: str) -> dict[str, Any]:
    """POST/PUT body of a management endpoint as a logical dict (JSON object or XML <root_name>)."""
    mimetype = request.mimetype
    if mimetype == JSON_MIMETYPE:
        return _parse_json_object()
    if mimetype == XML_MIMETYPE:
        raw = request.get_data(cache=False)
        root = _parse_xml_document(raw)
        if root.tag != root_name:
            raise ApiError.invalid_request(f"XML root element must be <{root_name}>", details={"root": root.tag})
        payload = _xml_object(root)
        validate_request_xml(raw, root)
        return payload
    raise ApiError.invalid_request(
        "Content-Type must be application/json or application/xml",
        details={"content_type": mimetype or None},
    )


def _xml_scalar(child: ET.Element) -> Any:
    if child.get(XSI_NIL) in ("true", "1"):
        return None
    text = child.text or ""
    if child.tag in ("active",):
        lexical = text.strip()
        if lexical not in _XML_BOOLEANS:
            raise ApiError.invalid_request("Field must be a boolean (true/false)", details={"field": child.tag})
        return _XML_BOOLEANS[lexical]
    if child.tag in _XML_INTEGER_FIELDS:
        try:
            return int(text.strip())
        except ValueError:
            raise ApiError.invalid_request("Field must be an integer", details={"field": child.tag}) from None
    return text


def _xml_object(element: ET.Element) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for child in element:
        name = child.tag
        if name in payload:
            raise ApiError.invalid_request("Duplicate XML element", details={"field": name})
        if name == "items":
            payload[name] = []
            for entry in child:
                if entry.tag != "item":
                    raise ApiError.invalid_request("items may only contain <item>", details={"field": entry.tag})
                payload[name].append(_xml_object(entry))
            continue
        if len(child):
            raise ApiError.invalid_request("XML field must not contain nested elements", details={"field": name})
        payload[name] = _xml_scalar(child)
    return payload
