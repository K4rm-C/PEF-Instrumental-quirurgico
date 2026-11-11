"""Health checks (PEF-DS09 §1.3.7): Audit uses PostgreSQL and Redis only."""

from __future__ import annotations

import logging

from flask import Blueprint, current_app, g
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from serializers import render

logger = logging.getLogger("audit.health")
health_bp = Blueprint("health", __name__, url_prefix="/health")


def _database() -> str:
    try:
        current_app.extensions["audit"]["database"].ping()
        return "ok"
    except SQLAlchemyError as exc:
        logger.warning("health: database check failed (%s)", type(exc).__name__)
        return "error"


def _redis() -> str:
    try:
        return "ok" if current_app.extensions["audit"]["redis"].ping() else "error"
    except RedisError as exc:
        logger.warning("health: redis check failed (%s)", type(exc).__name__)
        return "error"


def _health(ok_status: str, failed_status: str, checks: dict[str, str]):
    ok = all(value == "ok" for value in checks.values())
    return render({"status": ok_status if ok else failed_status, "checks": checks,
                   "correlation_id": g.correlation_id}, root="health", status=200 if ok else 503)


@health_bp.get("")
def health():
    return _health("ok", "unavailable", {"database": _database(), "redis": _redis()})


@health_bp.get("/live")
def live():
    return _health("live", "live", {})


@health_bp.get("/ready")
def ready():
    return _health("ready", "not_ready", {"database": _database(), "redis": _redis()})
