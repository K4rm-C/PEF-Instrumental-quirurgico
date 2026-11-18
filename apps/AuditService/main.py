"""PEF Audit / Event Service — read API over the existing audit trail (PEF-DS09 §1.3.6).

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
from routes.events import events_bp
from routes.health import health_bp
from services.event_service import EventService

CORRELATION_HEADER = "X-Correlation-ID"
_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")
logger = logging.getLogger("audit.request")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.correlation_id = getattr(g, "correlation_id", "-")
        except RuntimeError:
            record.correlation_id = "-"
        return True


def configure_logging(level: str) -> None:
    root = logging.getLogger("audit")
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
        elapsed_ms = (time.perf_counter() - g.request_started) * 1000 if getattr(g, "request_started", None) else 0.0
        principal = getattr(g, "principal", None)
        level = logging.DEBUG if request.path.startswith("/health") and response.status_code < 400 else logging.INFO
        # Query strings are logged (filters); they never carry tokens (Bearer header only).
        logger.log(level, "%s %s -> %s %.1fms user=%s", request.method, request.full_path.rstrip("?"),
                   response.status_code, elapsed_ms, principal.user_id if principal else "-")
        return response


def create_app(settings: Settings | None = None, *, database: Database | None = None,
               redis_client: redis.Redis | None = None) -> Flask:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)
    app = Flask(__name__)
    app.config["AUDIT_SETTINGS"] = settings
    database = database or create_database(settings)
    app.extensions["audit"] = {
        "database": database,
        "redis": redis_client or create_redis(settings),
        "events": EventService(database),
    }
    _register_correlation(app)
    register_error_handlers(app)
    app.register_blueprint(events_bp)
    app.register_blueprint(health_bp)
    return app


if __name__ == "__main__":
    runtime = Settings.from_env()
    create_app(runtime).run(host=runtime.host, port=runtime.port)
