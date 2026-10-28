"""PostgreSQL and Redis connections; idempotent schema migration (006_notifications.sql)."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import redis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from config import Settings

logger = logging.getLogger("notifications.database")
SERVICE_DIR = Path(__file__).resolve().parent
# In the repository the migration lives in data/migrations; the Docker image carries a copy.
MIGRATION_CANDIDATES = (SERVICE_DIR / "migrations" / "006_notifications.sql",
                        *(parent / "data" / "migrations" / "006_notifications.sql" for parent in SERVICE_DIR.parents[1:2]))


class Database:
    def __init__(self, engine: Engine):
        self.engine = engine

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        with self.engine.connect() as connection:
            yield connection

    @contextmanager
    def begin(self) -> Iterator[Connection]:
        with self.engine.begin() as connection:
            yield connection

    def ping(self) -> None:
        with self.connect() as connection:
            connection.execute(text("SELECT 1"))


def migration_sql() -> str:
    for candidate in MIGRATION_CANDIDATES:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError("006_notifications.sql not found")


def apply_migrations(engine: Engine) -> None:
    """Runs the idempotent migration under an advisory lock (API and worker may start together)."""
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('pef:notifications:migration'))"))
        # DBAPI cursor without parameters: the SQL (PL/pgSQL RAISE '%' formats) is sent verbatim.
        cursor = connection.connection.cursor()
        try:
            cursor.execute(migration_sql())
        finally:
            cursor.close()
    logger.info("notifications schema ready (006_notifications.sql)")


def create_database(settings: Settings) -> Database:
    # application_name makes each process visible in pg_stat_activity: MonitoringService checks that
    # the outbox consumer (pef-notifications-worker) holds a live connection.
    connect_args = {"connect_timeout": max(1, int(settings.dependency_timeout_seconds)),
                    "application_name": "pef-notifications-worker" if settings.worker_only else "pef-notifications"} \
        if settings.database_url.startswith("postgresql") else {}
    engine = create_engine(settings.database_url, pool_pre_ping=True, future=True, connect_args=connect_args)
    if settings.apply_migrations and os.getenv("NOTIFICATIONS_SKIP_MIGRATIONS") != "1":
        apply_migrations(engine)
    return Database(engine)


def create_redis(settings: Settings) -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True,
                                socket_connect_timeout=settings.dependency_timeout_seconds,
                                socket_timeout=settings.dependency_timeout_seconds)
