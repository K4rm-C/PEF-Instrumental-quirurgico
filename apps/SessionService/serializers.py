"""JSON / XML representation (PEF-DS09: Android → JSON, Desktop → XML, same semantics).

Routes and services produce one logical value (dicts, lists, str, int, bool, None); this module
is the only place that knows the wire format.

XML conventions (PEF-DS09 §1.3.3 "Representación XML", applied to Session in §1.3.4):
- element names equal JSON field names;
- a list is a container element with one child per entry (``sessions/session``,
  ``expected_inventory/line``, ``counts/count``, ``discrepancies/discrepancy``);
- booleans are ``true``/``false``; ``null`` is an empty element with ``xsi:nil="true"``.

Request bodies (Phase C): ``parse_body`` turns ``application/json`` or ``application/xml`` into
the same plain dict; validation of fields and types happens once, in the service layer.
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
    "sessions": "session",
    "expected_inventory": "line",
    "counts": "count",
    "discrepancies": "discrepancy",
    # Supervisor snapshot (DS09 §1.3.4 complement v0.2.0, expand=detail).
    "events": "event",
    "surgical_team": "member",
    "families": "family",
    "added": "addition",
    "addition_event_ids": "event_id",
    "errors": "error",
    "allowed": "value",
    # Supervisor reports (complement v0.3.0).
    "by_status": "status",
    "statuses": "status",
    "by_reason": "reason",
    "by_family": "family",
    "activity": "day",
    "kits": "kit",
    "operators": "operator",
    "unknown_parameters": "parameter",
}

REQUEST_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "session-requests.xsd"
REQUEST_SCHEMA_URL = "/api/v1/sessions/schemas/session-requests.xsd"
_XS = "{http://www.w3.org/2001/XMLSchema}"
MAX_REPORTED_SCHEMA_ERRORS = 10

_DTD_PATTERN = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_INTEGER_LEXICAL = re.compile(r"^[+-]?\d+$")
_XML_BOOLEANS = {"true": True, "1": True, "false": False, "0": False}


def response_format() -> str:
    """'xml' only when the client prefers application/xml; JSON otherwise (also for */*)."""
    best = request.accept_mimetypes.best_match([JSON_MIMETYPE, XML_MIMETYPE])
    return "xml" if best == XML_MIMETYPE else "json"


def render(data: Any, *, root: str, status: int = 200) -> Response:
    if response_format() == "xml":
        return Response(to_xml(data, root), status=status, content_type=f"{XML_MIMETYPE}; charset=utf-8")
    return Response(json.dumps(data, ensure_ascii=False), status=status, mimetype=JSON_MIMETYPE)


def error_response(exc: ApiError) -> Response:
    payload = {
        "error": exc.error,
        "message": exc.message,
        "correlation_id": getattr(g, "correlation_id", None),
        "details": exc.details,
    }
    return render(payload, root="error_response", status=exc.status)


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


# --- Request bodies -------------------------------------------------------------------------


def parse_body(
    xml_root: str,
    *,
    list_fields: dict[str, str] | None = None,
    int_fields: frozenset[str] = frozenset(),
    bool_fields: frozenset[str] = frozenset(),
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Parse a JSON object or an XML document into one plain dict.

    XML: element names = field names; ``list_fields`` maps a container element to its item element
    (e.g. ``expected_lines`` → ``line``), each item being a flat element with scalar children.
    Integers in ``int_fields`` are converted from their lexical form; anything else stays a string
    so the service reports the wrong type. Booleans in ``bool_fields`` accept true/false/1/0 (DS09).
    ``xsi:nil="true"`` → None. DTDs/entities are rejected.
    The body size limit (MAX_CONTENT_LENGTH) is enforced by Flask (413).
    """
    raw = request.get_data(cache=True)
    if not raw.strip():
        if allow_empty:
            return {}
        raise ApiError(400, "Request body is required")
    if request.mimetype == JSON_MIMETYPE:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "Request body is not valid JSON") from None
        if not isinstance(payload, dict):
            raise ApiError(400, "Request body must be a JSON object")
        return payload
    if request.mimetype == XML_MIMETYPE:
        return _parse_xml(raw, xml_root, list_fields or {}, _ScalarTypes(int_fields, bool_fields))
    raise ApiError(400, "Content-Type must be application/json or application/xml",
                   details={"content_type": request.mimetype or None})


class _ScalarTypes:
    """Fields whose XML text is converted (everything else stays a string)."""

    def __init__(self, int_fields: frozenset[str], bool_fields: frozenset[str] = frozenset()):
        self.int_fields = int_fields
        self.bool_fields = bool_fields


def _parse_xml(raw: bytes, xml_root: str, list_fields: dict[str, str], types: _ScalarTypes) -> dict[str, Any]:
    if _DTD_PATTERN.search(raw):
        raise ApiError(400, "XML DTD / entity declarations are not allowed")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ApiError(400, "Request body is not well-formed XML") from None
    if root.tag != xml_root:
        raise ApiError(400, f"XML root element must be <{xml_root}>", details={"root": root.tag})
    schema, accepted = _request_schema()
    if xml_root in accepted:
        unknown = sorted({child.tag for child in root} - accepted[xml_root])
        if unknown:   # same answer as the service gives for a JSON body
            raise ApiError(400, "Unknown fields in request body", details={"unknown_fields": unknown})
        parser = lxml_etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        if not schema.validate(lxml_etree.fromstring(raw, parser)):
            raise ApiError(400, "XML body does not conform to the request schema", details={
                "reason": "xsd_validation_failed", "schema": REQUEST_SCHEMA_URL,
                "errors": [{"line": entry.line, "message": entry.message}
                           for entry in list(schema.error_log)[:MAX_REPORTED_SCHEMA_ERRORS]],
            })
    payload: dict[str, Any] = {}
    for child in root:
        if child.tag in payload:
            raise ApiError(400, "Duplicate XML element", details={"field": child.tag})
        if child.tag in list_fields:
            item_tag = list_fields[child.tag]
            items = []
            for item in child:
                if item.tag != item_tag:
                    raise ApiError(400, f"XML list items must be <{item_tag}>", details={"field": child.tag})
                items.append(_flat_element(item, types))
            payload[child.tag] = items
        else:
            payload[child.tag] = _scalar(child, types)
    return payload


@lru_cache(maxsize=1)
def _request_schema():
    """The request XSD and, per request root, the child elements it accepts."""
    document = lxml_etree.parse(str(REQUEST_SCHEMA_PATH))
    accepted = {}
    for element in document.getroot().iterfind(f"{_XS}element"):
        children = element.findall(f"{_XS}complexType/{_XS}all/{_XS}element")
        accepted[element.get("name")] = frozenset(child.get("name") for child in children)
    return lxml_etree.XMLSchema(document), accepted


def _flat_element(element: ET.Element, types: _ScalarTypes) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for child in element:
        if child.tag in values:
            raise ApiError(400, "Duplicate XML element", details={"field": child.tag})
        values[child.tag] = _scalar(child, types)
    return values


def _scalar(element: ET.Element, types: _ScalarTypes) -> Any:
    if len(element):
        raise ApiError(400, "XML field must not contain nested elements", details={"field": element.tag})
    if element.get(XSI_NIL) in ("true", "1"):
        return None
    text = (element.text or "").strip()
    if element.tag in types.int_fields and _INTEGER_LEXICAL.match(text):
        return int(text)
    if element.tag in types.bool_fields and text in _XML_BOOLEANS:
        return _XML_BOOLEANS[text]
    return text
