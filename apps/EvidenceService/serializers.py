"""JSON / XML representation of metadata responses and errors (PEF-DS09 §1.3.1, §1.3.5).

Accept: application/xml → XML; application/json, */* or none → JSON. XML element names equal the
JSON field names; booleans are true/false; null is an empty element with xsi:nil="true".
Binary uploads (Phase C, multipart) are not handled here.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

from flask import Response, g, request

from errors import ApiError

JSON_MIMETYPE = "application/json"
XML_MIMETYPE = "application/xml"
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
XSI_NIL = f"{{{XSI_NAMESPACE}}}nil"

ET.register_namespace("xsi", XSI_NAMESPACE)

# Child element name used for entries of a list, keyed by the list's element name (DS09 §1.3.5).
LIST_ITEM_NAMES = {"evidence_list": "evidence"}


def response_format() -> str:
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
