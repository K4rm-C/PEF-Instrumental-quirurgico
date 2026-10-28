"""Health checks (PEF-DS09 §1.3.7): Monitoring uses PostgreSQL and Redis (+ age of the last collector cycle)."""

from __future__ import annotations

import logging

from flask import Blueprint, current_app, g
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from serializers import render

logger = logging.getLogger("monitoring.health")
health_bp = Blueprint("health", __name__, url_prefix="/health")


def _database() -> str:
    try:
        current_app.extensions["monitoring"]["database"].ping()
        return "ok"
    except SQLAlchemyError as exc:
        logger.warning("health: database check failed (%s)", type(exc).__name__)
        return "error"


def _redis() -> str:
    try:
        return "ok" if current_app.extensions["monitoring"]["redis"].ping() else "error"
    except RedisError as exc:
        logger.warning("health: redis check failed (%s)", type(exc).__name__)
        return "error"


def _health(ok_status: str, failed_status: str, checks: dict[str, str]):
    ok = all(value == "ok" for value in checks.values())
    return render({"status": ok_status if ok else failed_status, "checks": checks,
                   "correlation_id": g.correlation_id}, root="health", status=200 if ok else 503)


@health_bp.get("")
def health():
    response = _health("ok", "unavailable", {"database": _database(), "redis": _redis()})
    try:   # how long ago the collector stored its last cycle (observable, not a failure condition)
        from sqlalchemy import text
        with current_app.extensions["monitoring"]["database"].connect() as connection:
            age = connection.execute(text(
                "SELECT EXTRACT(EPOCH FROM (now() - max(started_at))) FROM monitoring_run")).scalar()
        response.headers["X-Last-Run-Age-Seconds"] = "none" if age is None else str(int(age))
    except Exception:  # noqa: BLE001
        pass
    return response


@health_bp.get("/live")
def live():
    return _health("live", "live", {})


@health_bp.get("/ready")
def ready():
    return _health("ready", "not_ready", {"database": _database(), "redis": _redis()})
