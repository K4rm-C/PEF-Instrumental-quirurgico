"""PEF-DS09 conventions for the versioned Auth API (/api/v1/auth/*) and health checks.

Self-contained on purpose (same rules as apps/CatalogService, no shared import):
- DS09 error envelope {"error", "message", "correlation_id", "details"} in JSON or XML;
- content negotiation: Accept application/xml → XML, anything else → JSON;
- JSON / XML request bodies parsed into one plain dict;
- X-Correlation-ID accepted when safe, generated otherwise, echoed in header/body/logs.

XML conventions: element names equal JSON field names; lists are wrappers with one child
per entry (roles/role); booleans true/false; null = empty element with xsi:nil="true".
XML bodies of the identity administration endpoints are also validated against
schemas/identity.xsd (parse_object).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, g, request
from lxml import etree as lxml_etree

JSON_MIMETYPE = "application/json"
XML_MIMETYPE = "application/xml"
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
XSI_NIL = f"{{{XSI_NAMESPACE}}}nil"
CORRELATION_HEADER = "X-Correlation-ID"
MAX_BODY_BYTES = 64 * 1024

ET.register_namespace("xsi", XSI_NAMESPACE)

LIST_ITEM_NAMES = {
	"roles": "role",
	# Identity administration (DS09 §1.3.2 complement v0.1.4).
	"managed_users": "managed_user",
	"managed_roles": "managed_role",
	"role_codes": "role_code",
	"errors": "error",
}

IDENTITY_SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "identity.xsd"
IDENTITY_SCHEMA_URL = "/api/v1/auth/schemas/identity.xsd"
MAX_REPORTED_SCHEMA_ERRORS = 10
_XML_BOOLEANS = {"true": True, "1": True, "false": False, "0": False}

_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")
_DTD_PATTERN = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)

ERROR_CODE_BY_STATUS = {
	400: "invalid_request",
	401: "unauthorized",
	403: "forbidden",
	404: "not_found",
	405: "method_not_allowed",
	409: "conflict",
	413: "payload_too_large",
	422: "validation_error",
	429: "rate_limited",
	500: "internal_error",
	503: "dependency_unavailable",
}

DEFAULT_MESSAGE_BY_STATUS = {
	400: "The request is invalid",
	401: "Authentication required",
	403: "You do not have permission to perform this action",
	404: "Resource not found",
	405: "Method not allowed for this resource",
	413: "Request body is too large",
	429: "Too many requests",
	500: "Unexpected internal error",
	503: "A required dependency is unavailable",
}

request_logger = logging.getLogger("auth.api")


class ApiError(Exception):
	def __init__(self, status: int, message: str | None = None, *, error: str | None = None,
				 details: dict[str, Any] | None = None):
		self.status = status
		self.error = error or ERROR_CODE_BY_STATUS.get(status, "error")
		self.message = message or DEFAULT_MESSAGE_BY_STATUS.get(status, "Error")
		self.details = details or {}
		super().__init__(self.message)

	@classmethod
	def dependency_unavailable(cls, dependency: str) -> "ApiError":
		return cls(503, f"{dependency.capitalize()} is unavailable", details={"dependency": dependency})


# --- Correlation id ---------------------------------------------------------------------


def ensure_correlation_id() -> str:
	correlation_id = getattr(g, "correlation_id", None)
	if not correlation_id:
		incoming = request.headers.get(CORRELATION_HEADER, "")
		correlation_id = incoming if _CORRELATION_ID_PATTERN.fullmatch(incoming) else str(uuid.uuid4())
		g.correlation_id = correlation_id
	return correlation_id


class CorrelationIdFilter(logging.Filter):
	def filter(self, record: logging.LogRecord) -> bool:
		try:
			record.correlation_id = getattr(g, "correlation_id", "-")
		except RuntimeError:  # outside a request
			record.correlation_id = "-"
		return True


def configure_api_logging() -> None:
	root = logging.getLogger("auth")
	if root.handlers:
		return
	handler = logging.StreamHandler()
	handler.addFilter(CorrelationIdFilter())
	handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [corr=%(correlation_id)s] %(message)s"))
	root.addHandler(handler)
	root.setLevel(logging.INFO)
	root.propagate = False


def register_correlation(blueprint: Blueprint) -> None:
	@blueprint.before_request
	def _assign():
		ensure_correlation_id()
		g.request_started = time.perf_counter()

	@blueprint.after_request
	def _emit(response):
		response.headers[CORRELATION_HEADER] = ensure_correlation_id()
		started = getattr(g, "request_started", None)
		elapsed_ms = (time.perf_counter() - started) * 1000 if started else 0.0
		user_id = getattr(g, "api_user_id", None) or "-"
		level = logging.DEBUG if request.path.startswith("/health/") and response.status_code < 400 else logging.INFO
		request_logger.log(level, "%s %s -> %s %.1fms user=%s", request.method, request.path,
						   response.status_code, elapsed_ms, user_id)
		return response


# --- Rendering --------------------------------------------------------------------------


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
		"correlation_id": ensure_correlation_id(),
		"details": exc.details,
	}
	response = render(payload, root="error_response", status=exc.status)
	response.headers[CORRELATION_HEADER] = payload["correlation_id"]
	return response


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
		item_name = LIST_ITEM_NAMES.get(element.tag, "item")
		for entry in value:
			_fill(ET.SubElement(element, item_name), entry)
	else:
		element.text = str(value)


# --- Request bodies ---------------------------------------------------------------------


def parse_string_fields(xml_root: str, allowed: tuple[str, ...], required: tuple[str, ...] = ()) -> dict[str, str | None]:
	"""Parse a flat JSON object or XML document whose fields are all strings (or null).

	Unknown fields are rejected; required fields must be non-empty strings.
	"""
	if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
		raise ApiError(413)
	raw = request.get_data(cache=True)
	if len(raw) > MAX_BODY_BYTES:
		raise ApiError(413)

	if request.mimetype == JSON_MIMETYPE:
		payload = _parse_json(raw)
	elif request.mimetype == XML_MIMETYPE:
		payload = _parse_xml(raw, xml_root)
	else:
		raise ApiError(400, "Content-Type must be application/json or application/xml",
					   details={"content_type": request.mimetype or None})

	unknown = sorted(set(payload) - set(allowed))
	if unknown:
		raise ApiError(400, "Unknown fields in request body", details={"unknown_fields": unknown})
	for name, value in payload.items():
		if value is not None and not isinstance(value, str):
			raise ApiError(400, "Field must be a string", details={"field": name})
	missing = [name for name in required if not payload.get(name)]
	if missing:
		raise ApiError(400, "Required fields are missing", details={"missing_fields": missing})
	return payload


def _parse_json(raw: bytes) -> dict[str, Any]:
	try:
		payload = json.loads(raw.decode("utf-8")) if raw else None
	except (UnicodeDecodeError, ValueError):
		raise ApiError(400, "Request body is not valid JSON") from None
	if not isinstance(payload, dict):
		raise ApiError(400, "Request body must be a JSON object")
	return payload


def _parse_xml(raw: bytes, expected_root: str) -> dict[str, Any]:
	if not raw or not raw.strip():
		raise ApiError(400, "Request body is empty")
	if _DTD_PATTERN.search(raw):
		raise ApiError(400, "XML DTD / entity declarations are not allowed")
	try:
		root = ET.fromstring(raw)
	except ET.ParseError:
		raise ApiError(400, "Request body is not well-formed XML") from None
	if root.tag != expected_root:
		raise ApiError(400, f"XML root element must be <{expected_root}>", details={"root": root.tag})
	payload: dict[str, Any] = {}
	for child in root:
		if child.tag in payload:
			raise ApiError(400, "Duplicate XML element", details={"field": child.tag})
		if len(child):
			raise ApiError(400, "XML field must not contain nested elements", details={"field": child.tag})
		payload[child.tag] = None if child.get(XSI_NIL) in ("true", "1") else (child.text or "")
	return payload


# --- Typed request bodies (identity administration) ----------------------------------------
# Field kinds: "string" (string or null), "boolean", "string_list" (list of strings; XML
# container with one child per entry, named by LIST_ITEM_NAMES).


def parse_object(xml_root: str, fields: dict[str, str]) -> dict[str, Any]:
	"""JSON object or XML <xml_root> → dict with the declared field kinds. Unknown fields → 400.

	Which fields an operation requires (and their values) is decided by the use case, which answers
	422 with details.reason; this layer only rejects malformed bodies (400).
	"""
	if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
		raise ApiError(413)
	raw = request.get_data(cache=True)
	if len(raw) > MAX_BODY_BYTES:
		raise ApiError(413)
	if request.mimetype == JSON_MIMETYPE:
		payload = _parse_json(raw)
		_check_unknown(payload, fields)
		for name, value in payload.items():
			_check_kind(name, value, fields[name])
		return payload
	if request.mimetype == XML_MIMETYPE:
		root = _parse_xml_root(raw, xml_root)
		payload = {}
		for child in root:
			if child.tag in payload:
				raise ApiError(400, "Duplicate XML element", details={"field": child.tag})
			payload[child.tag] = _xml_value(child, fields.get(child.tag))
		_check_unknown(payload, fields)
		validate_identity_xml(raw, root)
		return payload
	raise ApiError(400, "Content-Type must be application/json or application/xml",
				   details={"content_type": request.mimetype or None})


def _check_unknown(payload: dict[str, Any], fields: dict[str, str]) -> None:
	unknown = sorted(set(payload) - set(fields))
	if unknown:
		raise ApiError(400, "Unknown fields in request body", details={"unknown_fields": unknown})


def _check_kind(name: str, value: Any, kind: str) -> None:
	if value is None:
		return
	if kind == "boolean" and not isinstance(value, bool):
		raise ApiError(400, "Field must be a boolean", details={"field": name})
	if kind == "string" and not isinstance(value, str):
		raise ApiError(400, "Field must be a string", details={"field": name})
	if kind == "string_list" and (not isinstance(value, list) or not all(isinstance(item, str) for item in value)):
		raise ApiError(400, "Field must be a list of strings", details={"field": name})


def _parse_xml_root(raw: bytes, expected_root: str) -> ET.Element:
	if not raw or not raw.strip():
		raise ApiError(400, "Request body is empty")
	if _DTD_PATTERN.search(raw):
		raise ApiError(400, "XML DTD / entity declarations are not allowed")
	try:
		root = ET.fromstring(raw)
	except ET.ParseError:
		raise ApiError(400, "Request body is not well-formed XML") from None
	if root.tag != expected_root:
		raise ApiError(400, f"XML root element must be <{expected_root}>", details={"root": root.tag})
	return root


def _xml_value(child: ET.Element, kind: str | None) -> Any:
	if child.get(XSI_NIL) in ("true", "1"):
		return None
	if kind == "string_list":
		item_name = LIST_ITEM_NAMES.get(child.tag, "item")
		values = []
		for entry in child:
			if entry.tag != item_name or len(entry):
				raise ApiError(400, f"{child.tag} may only contain <{item_name}> text elements", details={"field": child.tag})
			values.append(entry.text or "")
		return values
	if len(child):
		raise ApiError(400, "XML field must not contain nested elements", details={"field": child.tag})
	text = child.text or ""
	if kind == "boolean":
		if text.strip() not in _XML_BOOLEANS:
			raise ApiError(400, "Field must be a boolean (true/false)", details={"field": child.tag})
		return _XML_BOOLEANS[text.strip()]
	return text


@lru_cache(maxsize=1)
def _identity_schema() -> lxml_etree.XMLSchema:
	return lxml_etree.XMLSchema(lxml_etree.parse(str(IDENTITY_SCHEMA_PATH)))


def validate_identity_xml(raw: bytes, root: ET.Element) -> None:
	"""400 (details.reason = xsd_validation_failed) when the body does not conform to identity.xsd."""
	schema = _identity_schema()
	parser = lxml_etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
	if schema.validate(lxml_etree.fromstring(raw, parser)):
		return
	raise ApiError(400, "XML body does not conform to the request schema", details={
		"reason": "xsd_validation_failed",
		"schema": IDENTITY_SCHEMA_URL,
		"errors": [{"line": entry.line, "message": entry.message}
				   for entry in list(schema.error_log)[:MAX_REPORTED_SCHEMA_ERRORS]],
	})
