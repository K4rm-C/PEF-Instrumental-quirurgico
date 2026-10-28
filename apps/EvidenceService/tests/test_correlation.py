"""X-Correlation-ID: 64-char rule with fullmatch, same value in header, envelope and logs."""

from __future__ import annotations

import logging
import uuid
import xml.etree.ElementTree as ET

import pytest

from correlation import CorrelationIdFilter

LIVE = "/health/live"
PROTECTED = "/__test__/whoami"
VALID_64 = "c" * 30 + "-._:" + "9" * 30
TOO_LONG_65 = VALID_64 + "x"


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []
        self.addFilter(CorrelationIdFilter())

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def request_log():
    target = logging.getLogger("evidence.request")
    capture, previous = _Capture(), target.level
    target.addHandler(capture)
    target.setLevel(logging.INFO)
    yield capture.records
    target.removeHandler(capture)
    target.setLevel(previous)


def _is_uuid(value):
    return str(uuid.UUID(value)) == value


def test_64_chars_preserved(client):
    assert len(VALID_64) == 64
    response = client.get(LIVE, headers={"X-Correlation-ID": VALID_64})
    assert response.headers["X-Correlation-ID"] == VALID_64 and response.get_json()["correlation_id"] == VALID_64


def test_65_chars_replaced_by_uuid(client):
    response = client.get(LIVE, headers={"X-Correlation-ID": TOO_LONG_65})
    generated = response.headers["X-Correlation-ID"]
    assert _is_uuid(generated) and response.get_json()["correlation_id"] == generated


@pytest.mark.parametrize("incoming", ["", "bad value <x>", "a;b", "é"])
def test_invalid_replaced(client, incoming):
    response = client.get(LIVE, headers={"X-Correlation-ID": incoming})
    assert _is_uuid(response.headers["X-Correlation-ID"])


def test_trailing_newline_rejected_on_real_request(client):
    """`$` with re.match would accept "abc\\n"; the service uses fullmatch."""
    response = client.get(LIVE, environ_overrides={"HTTP_X_CORRELATION_ID": "abc\n"})
    assert _is_uuid(response.headers["X-Correlation-ID"])


def test_header_always_returned(client):
    assert _is_uuid(client.get(LIVE).headers["X-Correlation-ID"])
    assert _is_uuid(client.get("/nope").headers["X-Correlation-ID"])


def test_json_error_uses_the_same_correlation(client, world, request_log):
    response = client.get(PROTECTED, headers={"X-Correlation-ID": VALID_64})
    assert response.status_code == 401
    assert response.headers["X-Correlation-ID"] == VALID_64 == response.get_json()["correlation_id"]
    assert [r.correlation_id for r in request_log] == [VALID_64]


def test_xml_error_uses_the_same_correlation(client, world):
    response = client.get(PROTECTED, headers={"X-Correlation-ID": TOO_LONG_65, "Accept": "application/xml"})
    generated = response.headers["X-Correlation-ID"]
    root = ET.fromstring(response.data)
    assert response.status_code == 401 and root.tag == "error_response"
    assert [c.tag for c in root] == ["error", "message", "correlation_id", "details"]
    assert _is_uuid(generated) and root.findtext("correlation_id") == generated


def test_unknown_route_and_method_use_the_envelope(client):
    response = client.get("/api/v1/unknown", headers={"X-Correlation-ID": "corr-404"})
    assert response.status_code == 404 and response.get_json()["correlation_id"] == "corr-404"
    response = client.post("/health/live")
    assert response.status_code == 405 and response.get_json()["error"] == "method_not_allowed"
