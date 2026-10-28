"""PostgreSQL and Redis connections for Evidence Service.

The service works on the existing PEF schema (data/migrations/001_init.sql + 004); it never
creates, alters or drops tables.
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

    def __init__(self, engine: Engine):
        self.engine = engine

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        with self.engine.connect() as connection:
            yield connection

    @contextmanager
    def begin(self) -> Iterator[Connection]:
        """Transactional unit of work: commits on success, rolls back on any exception."""
        with self.engine.begin() as connection:
            yield connection

    def ping(self) -> None:
        with self.connect() as connection:
            connection.execute(text("SELECT 1"))


def create_database(settings: Settings) -> Database:
    connect_args = {}
    if settings.database_url.startswith("postgresql"):
        connect_args["connect_timeout"] = max(1, int(settings.dependency_timeout_seconds))
    engine = create_engine(settings.database_url, pool_pre_ping=True, future=True, connect_args=connect_args)
    return Database(engine)


def create_redis(settings: Settings) -> redis.Redis:
    return redis.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.dependency_timeout_seconds,
        socket_timeout=settings.dependency_timeout_seconds,
    )
