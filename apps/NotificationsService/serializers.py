"""JSON / XML representation and the request body of PATCH /notifications/read-all.

XML conventions (same as the other services): element names = JSON field names; lists are
containers (`items/notification`); null = xsi:nil="true". Responses are described by
schemas/notifications.xsd, which also validates the only XML request body (`read_all_request`).
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
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "notifications.xsd"
SCHEMA_URL = "/api/v1/notifications/schemas/notifications.xsd"
_DTD = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
LIST_ITEM_NAMES = {"items": "notification", "unknown_parameters": "parameter", "unknown_fields": "field",
                   "allowed": "value", "errors": "error"}


def response_format() -> str:
    return "xml" if request.accept_mimetypes.best_match([JSON_MIMETYPE, XML_MIMETYPE]) == XML_MIMETYPE else "json"


def render(data: Any, *, root: str, status: int = 200) -> Response:
    if response_format() == "xml":
        return Response(to_xml(data, root), status=status, content_type=f"{XML_MIMETYPE}; charset=utf-8")
    return Response(json.dumps(data, ensure_ascii=False), status=status, mimetype=JSON_MIMETYPE)


def error_response(exc: ApiError) -> Response:
    return render({"error": exc.error, "message": exc.message, "correlation_id": getattr(g, "correlation_id", None),
                   "details": exc.details}, root="error_response", status=exc.status)


def to_xml(data: Any, root: str) -> bytes:
    element = ET.Element(root)
    _fill(element, data)
    return ET.tostring(element, encoding="utf-8", xml_declaration=True)


def _fill(element: ET.Element, value: Any) -> None:
    if value is None:
        element.set(XSI_NIL, "true")
    elif isinstance(value, bool):
        element.text = "true" if value else "false"
    elif isinstance(value, dict):
        for key, child in value.items():
            _fill(ET.SubElement(element, str(key)), child)
    elif isinstance(value, (list, tuple)):
        for entry in value:
            _fill(ET.SubElement(element, LIST_ITEM_NAMES.get(element.tag, "item")), entry)
    else:
        element.text = str(value)


@lru_cache(maxsize=1)
def _schema() -> lxml_etree.XMLSchema:
    return lxml_etree.XMLSchema(lxml_etree.parse(str(SCHEMA_PATH)))


def parse_read_all_body() -> dict[str, Any]:
    """Optional body {type?, before?} as JSON or XML <read_all_request> (validated against the XSD)."""
    raw = request.get_data(cache=True)
    if not raw.strip():
        return {}
    if request.mimetype == JSON_MIMETYPE:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError.invalid_request("Request body is not valid JSON") from None
        if not isinstance(payload, dict):
            raise ApiError.invalid_request("Request body must be a JSON object")
        return payload
    if request.mimetype == XML_MIMETYPE:
        if _DTD.search(raw):
            raise ApiError.invalid_request("XML DTD / entity declarations are not allowed")
        try:
            document = lxml_etree.fromstring(raw, lxml_etree.XMLParser(resolve_entities=False, no_network=True,
                                                                      load_dtd=False))
        except lxml_etree.XMLSyntaxError:
            raise ApiError.invalid_request("Request body is not well-formed XML") from None
        if document.tag != "read_all_request":
            raise ApiError.invalid_request("XML root element must be <read_all_request>", details={"root": document.tag})
        schema = _schema()
        if not schema.validate(document):
            raise ApiError.invalid_request("XML body does not conform to the request schema", details={
                "reason": "xsd_validation_failed", "schema": SCHEMA_URL,
                "errors": [{"line": e.line, "message": e.message} for e in list(schema.error_log)[:10]]})
        return {child.tag: (None if child.get(XSI_NIL) in ("true", "1") else (child.text or "").strip())
                for child in document}
    raise ApiError.invalid_request("Content-Type must be application/json or application/xml",
                                   details={"content_type": request.mimetype or None})
