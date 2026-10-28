"""Health checks from PEF-DS09 §1.3.7 that apply to Catalog (PostgreSQL + Redis only)."""

from __future__ import annotations

import logging

from flask import Blueprint, current_app, g
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from serializers import render

logger = logging.getLogger("catalog.health")

health_bp = Blueprint("health", __name__, url_prefix="/health")


def _check_database() -> str:
    try:
        current_app.extensions["catalog"]["database"].ping()
        return "ok"
    except SQLAlchemyError as exc:
        logger.warning("health: database check failed (%s)", type(exc).__name__)
        return "error"


def _check_redis() -> str:
    try:
        return "ok" if current_app.extensions["catalog"]["redis"].ping() else "error"
    except RedisError as exc:
        logger.warning("health: redis check failed (%s)", type(exc).__name__)
        return "error"


def _health(status_ok: str, status_failed: str, checks: dict[str, str]):
    ok = all(value == "ok" for value in checks.values())
    payload = {
        "status": status_ok if ok else status_failed,
        "checks": checks,
        "correlation_id": g.correlation_id,
    }
    return render(payload, root="health", status=200 if ok else 503)


@health_bp.get("/live")
def live():
    """Process is up; never touches dependencies."""
    return _health("live", "live", {})


@health_bp.get("/database")
def database():
    return _health("ok", "unavailable", {"database": _check_database()})


@health_bp.get("/redis")
def redis_health():
    return _health("ok", "unavailable", {"redis": _check_redis()})


@health_bp.get("/ready")
def ready():
    return _health("ready", "not_ready", {"database": _check_database(), "redis": _check_redis()})
