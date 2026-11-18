"""JSON / XML representation (same conventions as the other PEF services) and FHIR JSON.

Element names = JSON field names; lists are containers whose entries are named by context; null =
xsi:nil="true". Every XML document is described by schemas/integration.xsd. FHIR resources are always
`application/fhir+json` (FHIR R4 JSON; the FHIR XML format is not offered).
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

from flask import Response, g, request

from errors import ApiError

JSON_MIMETYPE = "application/json"
XML_MIMETYPE = "application/xml"
FHIR_MIMETYPE = "application/fhir+json"
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
XSI_NIL = f"{{{XSI_NAMESPACE}}}nil"
ET.register_namespace("xsi", XSI_NAMESPACE)
LIST_ITEM_NAMES = {"changes": "change", "resources": "resource", "items": "item", "unknown_fields": "field",
                   "unknown_parameters": "parameter", "allowed": "value", "attempts": "attempt",
                   "resource_types": "resource_type", "scopes": "scope", "types": "type", "session_ids": "session_id",
                   "export_list": "export_batch"}
ITEMS_BY_ROOT = {"endpoint_list": "endpoint", "delivery_list": "delivery", "export_list": "export_batch"}


def response_format() -> str:
    return "xml" if request.accept_mimetypes.best_match([JSON_MIMETYPE, XML_MIMETYPE]) == XML_MIMETYPE else "json"


def render(data: Any, *, root: str, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    if response_format() == "xml":
        response = Response(to_xml(data, root), status=status, content_type=f"{XML_MIMETYPE}; charset=utf-8")
    else:
        response = Response(json.dumps(data, ensure_ascii=False), status=status, mimetype=JSON_MIMETYPE)
    for key, value in (headers or {}).items():
        response.headers[key] = value
    return response


def render_fhir(resource: dict[str, Any], status: int = 200) -> Response:
    return Response(json.dumps(resource, ensure_ascii=False), status=status, content_type=f"{FHIR_MIMETYPE}; charset=utf-8")


def error_response(exc: ApiError) -> Response:
    if request.path.startswith("/fhir/"):
        severity = "error" if exc.status < 500 else "fatal"
        code = {400: "invalid", 401: "login", 403: "forbidden", 404: "not-found", 410: "deleted",
                413: "too-costly", 415: "not-supported", 422: "processing"}.get(exc.status, "exception")
        return render_fhir({"resourceType": "OperationOutcome",
                            "issue": [{"severity": severity, "code": code, "diagnostics": exc.message}]}, exc.status)
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


def json_body(*, allowed: set[str], required: tuple[str, ...] = ()) -> dict[str, Any]:
    """Flat JSON object (application/json only). Unknown fields 400; missing required 400."""
    if request.mimetype != JSON_MIMETYPE:
        raise ApiError(415, "Content-Type must be application/json")
    try:
        payload = json.loads(request.get_data(cache=True).decode("utf-8") or "null")
    except (UnicodeDecodeError, ValueError):
        raise ApiError.invalid_request("Request body is not valid JSON") from None
    if not isinstance(payload, dict):
        raise ApiError.invalid_request("Request body must be a JSON object")
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ApiError.invalid_request("Unknown fields in request body", details={"unknown_fields": unknown})
    missing = [name for name in required if payload.get(name) in (None, "")]
    if missing:
        raise ApiError.invalid_request("Required fields are missing", details={"missing_fields": missing})
    return payload
