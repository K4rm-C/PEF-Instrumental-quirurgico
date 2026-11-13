"""Notifications Service configuration, read exclusively from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

SUPPORTED_JWT_ALGORITHMS = {"HS256", "HS384", "HS512"}


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


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    host: str = "0.0.0.0"
    port: int = 5007
    log_level: str = "INFO"
    max_content_length: int = 16 * 1024
    dependency_timeout_seconds: float = 3.0
    apply_migrations: bool = True          # data/migrations/006_notifications.sql (idempotent) at start-up
    worker_poll_seconds: float = 2.0       # idle wait of the outbox consumer
    worker_batch_size: int = 50
    worker_max_attempts: int = 10          # an event that keeps failing stays in the outbox, visible in the logs
    reconcile_every_seconds: float = 60.0  # safety-net sweep of the source tables
    reconcile_window_days: int = 7
    worker_only: bool = False              # worker.py: verifies no tokens, so it gets no JWT secret

    def __post_init__(self) -> None:
        if not self.jwt_secret_key and not self.worker_only:
            raise RuntimeError("JWT_SECRET_KEY must be configured for Notifications Service")
        if self.jwt_algorithm not in SUPPORTED_JWT_ALGORITHMS:
            raise RuntimeError(f"JWT_ALGORITHM must be one of {sorted(SUPPORTED_JWT_ALGORITHMS)}")

    @classmethod
    def from_env(cls, *, worker_only: bool = False) -> "Settings":
        load_dotenv(override=False)
        return cls(
            database_url=_database_url_from_env(),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            jwt_secret_key=os.getenv("JWT_SECRET_KEY", ""),
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
            host=os.getenv("NOTIFICATIONS_HOST", "0.0.0.0"),
            port=_int("NOTIFICATIONS_PORT", 5007),
            log_level=os.getenv("NOTIFICATIONS_LOG_LEVEL", "INFO").upper(),
            dependency_timeout_seconds=_float("NOTIFICATIONS_DEPENDENCY_TIMEOUT_SECONDS", 3.0),
            apply_migrations=os.getenv("NOTIFICATIONS_APPLY_MIGRATIONS", "true").lower() == "true",
            worker_poll_seconds=_float("NOTIFICATIONS_WORKER_POLL_SECONDS", 2.0),
            worker_batch_size=_int("NOTIFICATIONS_WORKER_BATCH_SIZE", 50),
            worker_max_attempts=_int("NOTIFICATIONS_WORKER_MAX_ATTEMPTS", 10),
            reconcile_every_seconds=_float("NOTIFICATIONS_RECONCILE_EVERY_SECONDS", 60.0),
            reconcile_window_days=_int("NOTIFICATIONS_RECONCILE_WINDOW_DAYS", 7),
            worker_only=worker_only,
        )
