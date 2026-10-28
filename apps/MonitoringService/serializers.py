"""JSON / XML representation (same conventions as the other PEF services).

Element names = JSON field names; lists are containers whose entries are named by context
(`items/service`, `items/check`, `dependencies/dependency`...); null = xsi:nil="true". Every XML
document is described by schemas/monitoring.xsd. The API has no request bodies (all GET).
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
LIST_ITEM_NAMES = {"recent": "check", "dependencies": "dependency", "components": "component", "series": "component_series",
                   "points": "point", "unknown_parameters": "parameter", "allowed": "value"}
ITEMS_BY_ROOT = {"service_list": "service", "check_page": "check"}


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
    _fill(element, data, ITEMS_BY_ROOT.get(root, "item"))
    return ET.tostring(element, encoding="utf-8", xml_declaration=True)


def _fill(element: ET.Element, value: Any, items_name: str) -> None:
    if value is None:
        element.set(XSI_NIL, "true")
    elif isinstance(value, bool):
        element.text = "true" if value else "false"
    elif isinstance(value, dict):
        for key, child in value.items():
            _fill(ET.SubElement(element, str(key)), child, items_name)
    elif isinstance(value, (list, tuple)):
        name = items_name if element.tag == "items" else LIST_ITEM_NAMES.get(element.tag, "item")
        for entry in value:
            _fill(ET.SubElement(element, name), entry, items_name)
    else:
        element.text = str(value)
