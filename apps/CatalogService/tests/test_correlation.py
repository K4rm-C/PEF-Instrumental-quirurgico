"""X-Correlation-ID handling (PEF-DS09 §1.3.1)."""

from __future__ import annotations

import logging
import uuid
import xml.etree.ElementTree as ET

import pytest

from main import CorrelationIdFilter


def test_existing_correlation_id_is_echoed(client, world, auth_header):
    headers = {**auth_header("operator_a"), "X-Correlation-ID": "corr-demo-1001"}
    response = client.get("/api/v1/catalog/families", headers=headers)
    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "corr-demo-1001"


def test_correlation_id_is_generated_when_missing(client, world):
    response = client.get("/health/live")
    generated = response.headers["X-Correlation-ID"]
    assert str(uuid.UUID(generated)) == generated
    assert response.get_json()["correlation_id"] == generated


@pytest.mark.parametrize("unsafe", ["bad value <script>", "x" * 65, "corr;drop", ""])
def test_unsafe_correlation_id_is_replaced(client, world, unsafe):
    response = client.get("/health/live", headers={"X-Correlation-ID": unsafe})
    generated = response.headers["X-Correlation-ID"]
    assert generated != unsafe
    assert uuid.UUID(generated)


def test_error_envelope_contains_correlation_id(client, world):
    response = client.get("/api/v1/catalog/kits", headers={"X-Correlation-ID": "corr-err-1"})
    assert response.status_code == 401
    assert response.headers["X-Correlation-ID"] == "corr-err-1"
    assert response.get_json() == {
        "error": "unauthorized",
        "message": "Bearer access token is required",
        "correlation_id": "corr-err-1",
        "details": {},
    }


def test_unknown_route_uses_envelope(client, world):
    response = client.get("/api/v1/catalog/unknown", headers={"X-Correlation-ID": "corr-404"})
    assert response.status_code == 404
    body = response.get_json()
    assert body["error"] == "not_found"
    assert body["correlation_id"] == "corr-404"


def test_method_not_allowed_uses_envelope(client, world, auth_header):
    response = client.delete("/api/v1/catalog/families", headers=auth_header("admin_a"))
    assert response.status_code == 405
    assert response.get_json()["error"] == "method_not_allowed"


def test_xml_error_envelope_contains_correlation_id(client, world):
    response = client.get(
        "/api/v1/catalog/kits", headers={"Accept": "application/xml", "X-Correlation-ID": "corr-xml-1"}
    )
    assert response.status_code == 401
    assert response.mimetype == "application/xml"
    body = response.get_data(as_text=True)
    assert "<error_response>" in body
    assert "<error>unauthorized</error>" in body
    assert "<correlation_id>corr-xml-1</correlation_id>" in body


# --- DS09 v0.1.5: maximum 64 characters, never truncated -------------------------------------

VALID_64 = "c" * 30 + "-._:" + "9" * 30
TOO_LONG_65 = VALID_64 + "x"


class _Capture(logging.Handler):
    """Request-log capture that applies the service's own correlation filter."""

    def __init__(self):
        super().__init__()
        self.records = []
        self.addFilter(CorrelationIdFilter())

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def request_log():
    target = logging.getLogger("catalog.request")
    capture, previous = _Capture(), target.level
    target.addHandler(capture)
    target.setLevel(logging.INFO)
    yield capture.records
    target.removeHandler(capture)
    target.setLevel(previous)


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_64_chars_is_preserved(client, world, request_log, accept):
    assert len(VALID_64) == 64
    response = client.get("/api/v1/catalog/kits", headers={"X-Correlation-ID": VALID_64, "Accept": accept})
    assert response.status_code == 401 and response.headers["X-Correlation-ID"] == VALID_64
    body = response.get_json()["correlation_id"] if accept.endswith("json") else         ET.fromstring(response.data).findtext("correlation_id")
    assert body == VALID_64
    assert [r.correlation_id for r in request_log] == [VALID_64]


@pytest.mark.parametrize("accept", ["application/json", "application/xml"])
def test_65_chars_is_replaced_by_uuid(client, world, request_log, accept):
    response = client.get("/api/v1/catalog/kits", headers={"X-Correlation-ID": TOO_LONG_65, "Accept": accept})
    generated = response.headers["X-Correlation-ID"]
    assert str(uuid.UUID(generated)) == generated
    body = response.get_json()["correlation_id"] if accept.endswith("json") else         ET.fromstring(response.data).findtext("correlation_id")
    assert body == generated
    assert [r.correlation_id for r in request_log] == [generated]


def test_pattern_matches_whole_header():
    from main import _CORRELATION_ID_PATTERN

    assert _CORRELATION_ID_PATTERN.fullmatch("corr-1\n") is None
    assert _CORRELATION_ID_PATTERN.fullmatch(VALID_64) and not _CORRELATION_ID_PATTERN.fullmatch(TOO_LONG_65)


def test_request_with_trailing_newline_gets_a_new_uuid(client, world):
    """Real request: `^...$` with re.match would accept "abc\\n"; the service must use a full match."""
    response = client.get("/api/v1/catalog/kits", environ_overrides={"HTTP_X_CORRELATION_ID": "abc\n"})
    generated = response.headers["X-Correlation-ID"]
    assert str(uuid.UUID(generated)) == generated
    assert response.get_json()["correlation_id"] == generated
