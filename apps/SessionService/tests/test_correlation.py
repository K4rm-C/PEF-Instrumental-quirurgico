"""X-Correlation-ID handling (same policy as Auth/Catalog)."""

from __future__ import annotations

import logging
import uuid
import xml.etree.ElementTree as ET

import pytest

from main import CorrelationIdFilter

LIST = "/api/v1/sessions"


def test_valid_correlation_id_is_echoed(client, world, as_user):
    response = client.get(LIST, headers=as_user("spd_a", **{"X-Correlation-ID": "corr-session-1"}))
    assert response.headers["X-Correlation-ID"] == "corr-session-1"


@pytest.mark.parametrize("incoming", [None, "", "bad value <x>", "x" * 65, "a;b"])
def test_missing_or_invalid_correlation_id_is_generated(client, world, incoming):
    headers = {} if incoming is None else {"X-Correlation-ID": incoming}
    response = client.get("/health/live", headers=headers)
    generated = response.headers["X-Correlation-ID"]
    assert generated != incoming and uuid.UUID(generated)
    assert response.get_json()["correlation_id"] == generated


def test_error_contains_correlation_id(client, world):
    response = client.get(LIST, headers={"X-Correlation-ID": "corr-err-9"})
    assert response.status_code == 401
    assert response.headers["X-Correlation-ID"] == "corr-err-9"
    assert response.get_json()["correlation_id"] == "corr-err-9"


def test_xml_error_contains_correlation_id(client, world):
    response = client.get(LIST, headers={"X-Correlation-ID": "corr-xml-9", "Accept": "application/xml"})
    assert ET.fromstring(response.data).findtext("correlation_id") == "corr-xml-9"


def test_unknown_route_and_method_use_envelope(client, world, as_user):
    response = client.get("/api/v1/sessions/x/unknown", headers={"X-Correlation-ID": "corr-404"})
    assert response.status_code == 404 and response.get_json()["correlation_id"] == "corr-404"
    response = client.delete(LIST, headers=as_user("spd_a"))
    assert response.status_code == 405 and response.get_json()["error"] == "method_not_allowed"


# --- DS09 v0.1.5: maximum 64 characters, never truncated -------------------------------------

VALID_64 = "c" * 30 + "-._:" + "9" * 30          # exactly 64 characters from the allowed charset
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
    target = logging.getLogger("session.request")
    capture, previous = _Capture(), target.level
    target.addHandler(capture)
    target.setLevel(logging.INFO)
    yield capture.records
    target.removeHandler(capture)
    target.setLevel(previous)


def test_64_chars_is_preserved_json(client, world, request_log):
    assert len(VALID_64) == 64
    response = client.get(LIST, headers={"X-Correlation-ID": VALID_64})
    assert response.status_code == 401
    assert response.headers["X-Correlation-ID"] == VALID_64 and response.get_json()["correlation_id"] == VALID_64
    assert [r.correlation_id for r in request_log] == [VALID_64]


def test_64_chars_is_preserved_xml(client, world):
    response = client.get(LIST, headers={"X-Correlation-ID": VALID_64, "Accept": "application/xml"})
    assert response.headers["X-Correlation-ID"] == VALID_64
    assert ET.fromstring(response.data).findtext("correlation_id") == VALID_64


def test_65_chars_is_replaced_by_uuid_json(client, world, request_log):
    response = client.get(LIST, headers={"X-Correlation-ID": TOO_LONG_65})
    generated = response.headers["X-Correlation-ID"]
    assert str(uuid.UUID(generated)) == generated and not TOO_LONG_65.startswith(generated)
    assert response.get_json()["correlation_id"] == generated
    assert [r.correlation_id for r in request_log] == [generated]


def test_65_chars_is_replaced_by_uuid_xml(client, world):
    response = client.get(LIST, headers={"X-Correlation-ID": TOO_LONG_65, "Accept": "application/xml"})
    generated = response.headers["X-Correlation-ID"]
    assert str(uuid.UUID(generated)) == generated
    assert ET.fromstring(response.data).findtext("correlation_id") == generated


def test_trailing_newline_is_not_accepted(client, world):
    """`$` would accept a trailing newline; the pattern must match the whole header."""
    from main import _CORRELATION_ID_PATTERN

    assert _CORRELATION_ID_PATTERN.fullmatch("corr-1\n") is None
    assert _CORRELATION_ID_PATTERN.fullmatch(VALID_64) and not _CORRELATION_ID_PATTERN.fullmatch(TOO_LONG_65)


def test_request_with_trailing_newline_gets_a_new_uuid(client, world):
    """Real request: `^...$` with re.match would accept "abc\\n"; the service must use a full match."""
    response = client.get(LIST, environ_overrides={"HTTP_X_CORRELATION_ID": "abc\n"})
    generated = response.headers["X-Correlation-ID"]
    assert str(uuid.UUID(generated)) == generated
    assert response.get_json()["correlation_id"] == generated
