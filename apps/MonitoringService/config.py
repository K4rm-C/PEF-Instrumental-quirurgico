"""Monitoring Service configuration, read exclusively from environment variables.

Targets are configured per component (MONITORING_TARGET_<NAME>_URL). An empty target means the
component is not configured: it is reported as `unknown` (never as available). Docker defaults use
the compose DNS names; components that run on the host (the portal and VisionWorker today) are
reached through host.docker.internal.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

SUPPORTED_JWT_ALGORITHMS = {"HS256", "HS384", "HS512"}

# name -> (label, kind). Kinds: http_health (DS09 /health/ready JSON with checks), vision (/health JSON),
# garage (admin API /health, plain text), postgres, redis, mongo, notifications_worker.
COMPONENTS: dict[str, tuple[str, str]] = {
    "web": ("Web portal (BackendWebFlask)", "http_health"),
    "auth": ("Auth / Identity Service", "http_health"),
    "catalog": ("Catalog Service", "http_health"),
    "session": ("Session / Counting Service", "http_health"),
    "evidence": ("Evidence / File Service", "http_health"),
    "audit": ("Audit / Event Service", "http_health"),
    "notifications": ("Notifications Service", "http_health"),
    "notifications-worker": ("Notifications worker", "notifications_worker"),
    "vision": ("VisionWorker", "vision"),
    "postgres": ("PostgreSQL", "postgres"),
    "redis": ("Redis", "redis"),
    "mongo": ("MongoDB", "mongo"),
    "garage": ("Garage (object storage)", "garage"),
}
STATUSES = ("up", "degraded", "down", "unknown")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _database_url_from_env() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url
    return (
        "postgresql+psycopg://"
        f"{os.getenv('DB_USER', 'postgres')}:{os.getenv('DB_PASSWORD', '')}@"
        f"{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}/"
        f"{os.getenv('DB_NAME', 'postgres')}"
    )


def _env_key(name: str) -> str:
    return "MONITORING_TARGET_" + name.upper().replace("-", "_") + "_URL"


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    host: str = "0.0.0.0"
    port: int = 5008
    log_level: str = "INFO"
    max_content_length: int = 16 * 1024
    dependency_timeout_seconds: float = 3.0
    apply_migrations: bool = True               # data/migrations/007_monitoring.sql (idempotent) at start-up
    targets: dict[str, str] = field(default_factory=dict)   # component -> base URL ("" = not configured)
    mongo_url: str = ""                         # MongoDB check (credentials never stored)
    interval_seconds: float = 30.0              # one collector cycle checks every component once
    check_timeout_seconds: float = 3.0          # per component
    degraded_latency_ms: float = 1500.0         # an answering component slower than this is `degraded`
    worker_backlog_seconds: float = 120.0       # oldest unprocessed outbox event older than this: worker degraded
    stale_after_seconds: float = 0.0            # last check older than this is reported `unknown` (0 = 3 x interval)
    retention_days: int = 7
    worker_only: bool = False                   # collector.py: verifies no tokens, so it gets no JWT secret

    def __post_init__(self) -> None:
        if not self.jwt_secret_key and not self.worker_only:
            raise RuntimeError("JWT_SECRET_KEY must be configured for Monitoring Service")
        if self.jwt_algorithm not in SUPPORTED_JWT_ALGORITHMS:
            raise RuntimeError(f"JWT_ALGORITHM must be one of {sorted(SUPPORTED_JWT_ALGORITHMS)}")

    @property
    def stale_seconds(self) -> float:
        return self.stale_after_seconds or 3 * self.interval_seconds

    @classmethod
    def from_env(cls, *, worker_only: bool = False) -> "Settings":
        load_dotenv(override=False)
        return cls(
            database_url=_database_url_from_env(),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            jwt_secret_key=os.getenv("JWT_SECRET_KEY", ""),
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
            host=os.getenv("MONITORING_HOST", "0.0.0.0"),
            port=_int("MONITORING_PORT", 5008),
            log_level=os.getenv("MONITORING_LOG_LEVEL", "INFO").upper(),
            dependency_timeout_seconds=_float("MONITORING_DEPENDENCY_TIMEOUT_SECONDS", 3.0),
            apply_migrations=os.getenv("MONITORING_APPLY_MIGRATIONS", "true").lower() == "true",
            targets={name: os.getenv(_env_key(name), "").strip().rstrip("/") for name, (_, kind) in COMPONENTS.items()
                     if kind in ("http_health", "vision", "garage")},
            mongo_url=os.getenv("MONITORING_MONGO_URL", os.getenv("MONGO_URL", "")).strip(),
            interval_seconds=max(5.0, _float("MONITORING_INTERVAL_SECONDS", 30.0)),
            check_timeout_seconds=max(0.5, _float("MONITORING_CHECK_TIMEOUT_SECONDS", 3.0)),
            degraded_latency_ms=_float("MONITORING_DEGRADED_LATENCY_MS", 1500.0),
            worker_backlog_seconds=_float("MONITORING_WORKER_BACKLOG_SECONDS", 120.0),
            stale_after_seconds=_float("MONITORING_STALE_AFTER_SECONDS", 0.0),
            retention_days=max(1, _int("MONITORING_RETENTION_DAYS", 7)),
            worker_only=worker_only,
        )
