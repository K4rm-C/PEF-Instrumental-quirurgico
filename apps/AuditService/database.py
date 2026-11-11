"""PostgreSQL and Redis connections for Audit Service.

The service reads the existing PEF schema (data/migrations/001_init.sql); it never creates, alters or
drops tables. Event queries run in READ ONLY transactions, so not even a bug can change history; the
only write is the access_audit row of a denied attempt (Database.begin, its own transaction).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import redis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from config import Settings


class Database:
    """Thin wrapper so repositories receive a Connection and tests can swap the source."""

    def __init__(self, engine: Engine, *, enforce_read_only: bool = True):
        self.engine = engine
        self.enforce_read_only = enforce_read_only

    @contextmanager
    def read(self) -> Iterator[Connection]:
        """A READ ONLY transaction: PostgreSQL refuses any INSERT/UPDATE/DELETE inside it."""
        with self.engine.begin() as connection:
            if self.enforce_read_only:
                connection.execute(text("SET TRANSACTION READ ONLY"))
            yield connection

    @contextmanager
    def begin(self) -> Iterator[Connection]:
        """Read-write unit of work (only for the audit row of a denied request)."""
        with self.engine.begin() as connection:
            yield connection

    def ping(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))


def create_database(settings: Settings) -> Database:
    connect_args = {}
    if settings.database_url.startswith("postgresql"):
        connect_args["connect_timeout"] = max(1, int(settings.dependency_timeout_seconds))
    engine = create_engine(settings.database_url, pool_pre_ping=True, future=True, connect_args=connect_args)
    return Database(engine, enforce_read_only=settings.enforce_read_only)


def create_redis(settings: Settings) -> redis.Redis:
    return redis.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.dependency_timeout_seconds,
        socket_timeout=settings.dependency_timeout_seconds,
    )
