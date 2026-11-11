"""PEF Integration Service (MS-07): synchronization for desktop / Android / machine clients, idempotent operations
forwarded to the owner services, read-only FHIR R4 projection (PEF-DS08) and FHIR deliveries.

It owns no business rule of sessions, catalog or counting: those stay in SessionService, CatalogService and the
portal; this service only publishes changes, forwards authorized operations exactly once and projects data.

Run locally:   python main.py           (API)     python worker.py   (deliveries + compaction)
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
from routes.fhir import fhir_bp
from routes.health import health_bp
from routes.integration import integration_bp
from services.exports import ExportService
from routes.sync import sync_bp
from services.deliveries import DeliveryService
from services.operations import OperationService
from services.sync_feed import CursorCodec, SyncFeed

CORRELATION_HEADER = "X-Correlation-ID"
_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")
logger = logging.getLogger("integration.request")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.correlation_id = getattr(g, "correlation_id", "-")
        except RuntimeError:
            record.correlation_id = "-"
        return True


def configure_logging(level: str) -> None:
    root = logging.getLogger("integration")
    if any(isinstance(f, CorrelationIdFilter) for h in root.handlers for f in h.filters):
        return
    handler = logging.StreamHandler()
    handler.addFilter(CorrelationIdFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [corr=%(correlation_id)s] %(message)s"))
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
        response.headers[CORRELATION_HEADER] = g.correlation_id
        response.headers.setdefault("Cache-Control", "no-store")
        elapsed_ms = (time.perf_counter() - g.request_started) * 1000 if getattr(g, "request_started", None) else 0.0
        principal = getattr(g, "principal", None)
        level = logging.DEBUG if request.path.startswith("/health") and response.status_code < 400 else logging.INFO
        # Cursors and query strings are logged without tokens (Bearer header only).
        logger.log(level, "%s %s -> %s %.1fms %s=%s", request.method, request.path, response.status_code, elapsed_ms,
                   principal.kind if principal else "anon", principal.id if principal else "-")
        return response


def create_app(settings: Settings | None = None, *, database: Database | None = None,
               redis_client: redis.Redis | None = None) -> Flask:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)
    app = Flask(__name__)
    app.config["INTEGRATION_SETTINGS"] = settings
    app.config["MAX_CONTENT_LENGTH"] = settings.max_content_length
    database = database or create_database(settings)
    app.extensions["integration"] = {
        "database": database,
        "redis": redis_client or create_redis(settings),
        "feed": SyncFeed(database, CursorCodec(settings.cursor_key, settings.cursor_max_age_days), settings.page_max),
        "operations": OperationService(database, settings.upstreams, settings.upstream_timeout_seconds,
                                       settings.operation_lease_seconds),
        "deliveries": DeliveryService(database, settings),
        "exports": ExportService(database),   # MS-12
    }
    _register_correlation(app)
    register_error_handlers(app)
    for blueprint in (sync_bp, fhir_bp, integration_bp, health_bp):
        app.register_blueprint(blueprint)
    return app


if __name__ == "__main__":
    runtime = Settings.from_env()
    create_app(runtime).run(host=runtime.host, port=runtime.port)
