"""PostgreSQL and Redis connections; idempotent schema migration (007_monitoring.sql)."""

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

logger = logging.getLogger("monitoring.database")
SERVICE_DIR = Path(__file__).resolve().parent
# In the repository the migration lives in data/migrations; the Docker image carries a copy.
MIGRATION_CANDIDATES = (SERVICE_DIR / "migrations" / "007_monitoring.sql",
                        *(parent / "data" / "migrations" / "007_monitoring.sql" for parent in SERVICE_DIR.parents[1:2]))


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
    raise FileNotFoundError("007_monitoring.sql not found")


def apply_migrations(engine: Engine) -> None:
    """Runs the idempotent migration under an advisory lock (API and collector may start together)."""
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('pef:monitoring:migration'))"))
        # DBAPI cursor without parameters: the SQL (PL/pgSQL RAISE '%' formats) is sent verbatim.
        cursor = connection.connection.cursor()
        try:
            cursor.execute(migration_sql())
        finally:
            cursor.close()
    logger.info("monitoring schema ready (007_monitoring.sql)")


def create_database(settings: Settings) -> Database:
    connect_args = {"connect_timeout": max(1, int(settings.dependency_timeout_seconds)),
                    "application_name": "pef-monitoring-collector" if settings.worker_only else "pef-monitoring"} \
        if settings.database_url.startswith("postgresql") else {}
    engine = create_engine(settings.database_url, pool_pre_ping=True, future=True, connect_args=connect_args)
    if settings.apply_migrations and os.getenv("MONITORING_SKIP_MIGRATIONS") != "1":
        apply_migrations(engine)
    return Database(engine)


def create_redis(settings: Settings) -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True,
                                socket_connect_timeout=settings.dependency_timeout_seconds,
                                socket_timeout=settings.dependency_timeout_seconds)
