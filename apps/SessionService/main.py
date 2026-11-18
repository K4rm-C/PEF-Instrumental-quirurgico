"""PEF Session Service — independent REST microservice (PEF-DS09 §1.3.4).

Phase A/B: read API (list, detail, expected inventory, counts, discrepancies) + health.
Phase C: create session, replace expected inventory, start.
Phase D: vision-counts (normalized vision facts), count-report (human validation).
Phase E: resolve discrepancy, SPD close.

Run locally:   python main.py
Run in Docker: gunicorn "main:create_app()"
"""

from __future__ import annotations

import logging
import re
import time
import uuid

import redis
from flask import Flask, g, request

from config import Settings
from database import Database, create_database, create_redis
from errors import register_error_handlers
from routes.sessions import docs_bp, sessions_bp
from routes.health import health_bp
from services.session_service import SessionService
from services.session_counts import SessionCountService
from services.session_reports import SessionReportService
from services.session_operator_close import SessionOperatorCloseService
from services.session_review import SessionReviewService
from services.session_writes import SessionWriteService
from services.session_planning import SessionPlanningService
from services.session_snapshot import SessionSnapshotService

CORRELATION_HEADER = "X-Correlation-ID"
# Client-supplied ids are echoed in headers and logs, so only a safe charset is accepted.
_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")

logger = logging.getLogger("session.request")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.correlation_id = getattr(g, "correlation_id", "-")
        except RuntimeError:  # outside an application/request context
            record.correlation_id = "-"
        return True


def configure_logging(level: str) -> None:
    root = logging.getLogger("session")
    if any(isinstance(f, CorrelationIdFilter) for h in root.handlers for f in h.filters):
        return
    handler = logging.StreamHandler()
    handler.addFilter(CorrelationIdFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s [corr=%(correlation_id)s] %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def _register_correlation(app: Flask) -> None:
    @app.before_request
    def assign_correlation_id():
        incoming = request.headers.get(CORRELATION_HEADER, "")
        g.correlation_id = incoming if _CORRELATION_ID_PATTERN.fullmatch(incoming) else str(uuid.uuid4())
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
        logger.log(
            level,
            "%s %s -> %s %.1fms user=%s",
            request.method,
            request.path,
            response.status_code,
            elapsed_ms,
            principal.user_id if principal else "-",
        )
        return response


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    redis_client: redis.Redis | None = None,
) -> Flask:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)

    app = Flask(__name__)
    app.config["SESSION_SETTINGS"] = settings
    app.config["MAX_CONTENT_LENGTH"] = settings.max_content_length

    database = database or create_database(settings)
    snapshots = SessionSnapshotService(database)
    app.extensions["session_service"] = {
        "snapshots": snapshots,
        "planning": SessionPlanningService(database, snapshots),
        "database": database,
        "redis": redis_client or create_redis(settings),
        "service": SessionService(database),
        "writes": SessionWriteService(database),
        "counts": SessionCountService(database),
        "review": SessionReviewService(database),
        "operator_close": SessionOperatorCloseService(database),
        "reports": SessionReportService(database),
    }

    _register_correlation(app)
    register_error_handlers(app)
    app.register_blueprint(docs_bp)   # static paths first: /openapi.yaml is not a session id
    app.register_blueprint(sessions_bp)
    app.register_blueprint(health_bp)
    return app


if __name__ == "__main__":
    runtime_settings = Settings.from_env()
    create_app(runtime_settings).run(host=runtime_settings.host, port=runtime_settings.port)
