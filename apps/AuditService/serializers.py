"""JSON / XML representation (PEF-DS09: Android → JSON, Desktop → XML, same semantics).

XML conventions (same as Catalog / Session): element names = JSON field names; a list is a container
with one child per entry (`items/event`, `by_action/group`, `role_codes/role_code`…); booleans
true/false; null = empty element with xsi:nil="true". Every XML document this service emits is
described by schemas/audit.xsd. The API takes no request bodies (all endpoints are GET), so there is
no XML input to validate.
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

LIST_ITEM_NAMES = {
    "items": "event",
    "role_codes": "role_code",
    "by_category": "group",
    "by_source_service": "group",
    "by_outcome": "group",
    "by_record_type": "group",
    "by_action": "group",
    "by_resource_type": "group",
    "by_user": "user",
    "by_session": "session",
    "unknown_parameters": "parameter",
    "allowed": "value",
}


def response_format() -> str:
    best = request.accept_mimetypes.best_match([JSON_MIMETYPE, XML_MIMETYPE])
    return "xml" if best == XML_MIMETYPE else "json"


def render(data: Any, *, root: str, status: int = 200) -> Response:
    if response_format() == "xml":
        return Response(to_xml(data, root), status=status, content_type=f"{XML_MIMETYPE}; charset=utf-8")
    return Response(json.dumps(data, ensure_ascii=False), status=status, mimetype=JSON_MIMETYPE)


def error_response(exc: ApiError) -> Response:
    payload = {"error": exc.error, "message": exc.message, "correlation_id": getattr(g, "correlation_id", None),
               "details": exc.details}
    return render(payload, root="error_response", status=exc.status)


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
        name = LIST_ITEM_NAMES.get(element.tag, "item")
        for entry in value:
            _fill(ET.SubElement(element, name), entry)
    else:
        element.text = str(value)
