"""PEF Evidence / File Service — independent REST microservice (PEF-DS09 §1.3.5, PEF-DS05).

Phase A: infrastructure — configuration, JWT auth context, PostgreSQL, Redis, S3/Garage adapter,
X-Correlation-ID, DS09 error envelope and health checks.
Phase B: read API — GET /api/v1/evidence/{id} (metadata + audited signed URL) and
GET /api/v1/sessions/{id}/evidence (metadata list).
Phase C: POST /api/v1/evidence — multipart upload (streamed to a temporary file, signature-checked,
SHA-256, storage-first with locked session re-validation and compensating delete).

Run locally:   python main.py
Run in Docker: gunicorn "main:create_app()"
"""

from __future__ import annotations

import redis
from flask import Flask

from config import Settings
from correlation import configure_logging, register_correlation
from database import Database, create_database, create_redis
from errors import register_error_handlers
from routes.evidence import evidence_bp
from routes.health import health_bp
from services.evidence_service import EvidenceService
from services.evidence_upload import EvidenceUploadService
from storage.s3_adapter import S3Storage
from uploads import EvidenceRequest, max_request_bytes


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    redis_client: redis.Redis | None = None,
    storage: S3Storage | None = None,
) -> Flask:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)

    app = Flask(__name__)
    app.request_class = EvidenceRequest
    app.config["EVIDENCE_SETTINGS"] = settings
    # Multipart envelope only; the per-file limits (JPEG/GIF) are enforced while streaming.
    app.config["MAX_CONTENT_LENGTH"] = max_request_bytes(settings.jpeg_max_bytes, settings.gif_max_bytes)
    database = database or create_database(settings)
    storage = storage or S3Storage(settings)
    app.extensions["evidence_service"] = {
        "database": database,
        "redis": redis_client or create_redis(settings),
        "storage": storage,
        "evidence": EvidenceService(database, storage, settings.signed_url_ttl_seconds),
        "uploads": EvidenceUploadService(database, storage, settings),
    }

    register_correlation(app)
    register_error_handlers(app)
    app.register_blueprint(evidence_bp)
    app.register_blueprint(health_bp)
    return app


if __name__ == "__main__":
    runtime_settings = Settings.from_env()
    create_app(runtime_settings).run(host=runtime_settings.host, port=runtime_settings.port)
