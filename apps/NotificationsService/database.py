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
# 006: notifications and outbox. 013 (MS-06): new types, station alerts, e-mail deliveries.
MIGRATIONS = ("006_notifications.sql", "013_notificaciones_ms06.sql", "017_ms11_notificaciones_servicios.sql")


def _candidates(name: str):
    return (SERVICE_DIR / "migrations" / name, *(parent / "data" / "migrations" / name for parent in SERVICE_DIR.parents[1:2]))


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


def migration_sql(name: str = MIGRATIONS[0]) -> str:
    for candidate in _candidates(name):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(f"{name} not found")


RETRYABLE_SQLSTATES = {"40P01", "55P03"}   # deadlock_detected, lock_not_available (lock_timeout)
PRELOCK_TABLES = ('access_audit', 'count_event', 'station_event', 'service_event')


def _sqlstate(exc: BaseException) -> str | None:
    return getattr(exc, "sqlstate", None) or getattr(getattr(exc, "orig", None), "sqlstate", None)


def apply_migrations(engine: Engine, attempts: int = 5) -> None:
    """Runs the idempotent migration under the advisory lock SHARED by every PEF service (MS-09): their migrations
    create triggers on common tables (012 / 013 / 014), so two services starting together must not run DDL at once.

    MS-11: the advisory lock does not order the migration against NORMAL writes of the running services (a trigger
    on a source table writing into a table this migration already locked = deadlock; observed with NotificationsService
    starting while the collector wrote service_event). So the source tables are locked first, in the order the DML
    path takes them, with a bounded lock_timeout; and a deadlock / lock timeout is retried with backoff instead of
    aborting the start-up. Nothing is applied partially: every attempt is one transaction."""
    import time

    for attempt in range(1, attempts + 1):
        try:
            with engine.begin() as connection:
                connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('pef:schema:migration'))"))
                connection.execute(text("SET LOCAL lock_timeout = '15s'"))
                for table in PRELOCK_TABLES:
                    if connection.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table}).scalar():
                        connection.execute(text(f'LOCK TABLE "{table}" IN ACCESS EXCLUSIVE MODE'))
                # DBAPI cursor without parameters: the SQL (PL/pgSQL RAISE '%' formats) is sent verbatim.
                cursor = connection.connection.cursor()
                try:
                    for name in MIGRATIONS:
                        cursor.execute(migration_sql(name))
                finally:
                    cursor.close()
            break
        except Exception as exc:  # noqa: BLE001 - only lock conflicts are retried
            if _sqlstate(exc) not in RETRYABLE_SQLSTATES or attempt == attempts:
                raise
            logger.warning("schema migration hit a lock conflict (%s); retry %d/%d", _sqlstate(exc), attempt, attempts - 1)
            time.sleep(0.5 * 2 ** (attempt - 1))
    logger.info("notifications schema ready (%s)", ", ".join(MIGRATIONS))


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
