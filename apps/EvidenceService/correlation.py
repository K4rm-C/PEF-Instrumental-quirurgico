"""X-Correlation-ID (PEF-DS09 §1.3.1 v0.1.5) and request logging.

A valid header (fullmatch of ^[A-Za-z0-9._:-]{1,64}$) is reused; a missing, invalid or longer one
is replaced by a new UUID (never truncated). The same string goes to the response header, the
error envelope and every log line. It is never written into storage object metadata.
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from flask import Flask, g, request

CORRELATION_HEADER = "X-Correlation-ID"
CORRELATION_ID_PATTERN = re.compile(r"[A-Za-z0-9._:\-]{1,64}")

request_logger = logging.getLogger("evidence.request")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.correlation_id = getattr(g, "correlation_id", "-")
        except RuntimeError:  # outside an application/request context
            record.correlation_id = "-"
        return True


def configure_logging(level: str) -> None:
    root = logging.getLogger("evidence")
    if any(isinstance(f, CorrelationIdFilter) for h in root.handlers for f in h.filters):
        return
    handler = logging.StreamHandler()
    handler.addFilter(CorrelationIdFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [corr=%(correlation_id)s] %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def effective_correlation_id(incoming: str) -> str:
    return incoming if CORRELATION_ID_PATTERN.fullmatch(incoming) else str(uuid.uuid4())


def register_correlation(app: Flask) -> None:
    @app.before_request
    def assign_correlation_id():
        g.correlation_id = effective_correlation_id(request.headers.get(CORRELATION_HEADER, ""))
        g.request_started = time.perf_counter()

    @app.after_request
    def emit_correlation_id(response):
        correlation_id = getattr(g, "correlation_id", None)
        if correlation_id:
            response.headers[CORRELATION_HEADER] = correlation_id
        started = getattr(g, "request_started", None)
        elapsed_ms = (time.perf_counter() - started) * 1000 if started else 0.0
        principal = getattr(g, "principal", None)
        # Successful health probes (Docker checks every few seconds) only at DEBUG.
        level = logging.DEBUG if request.path.startswith("/health/") and response.status_code < 400 else logging.INFO
        request_logger.log(level, "%s %s -> %s %.1fms user=%s", request.method, request.path,
                           response.status_code, elapsed_ms, principal.user_id if principal else "-")
        return response
