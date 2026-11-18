"""Fixtures for Notifications Service tests (real PostgreSQL and Redis).

The idempotent migration is applied once (committed, as in production); every test then runs in one
outer transaction rolled back at teardown: the producers' rows it inserts fire the real triggers,
the worker processes the outbox inside the same transaction, nothing is left behind.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
import redis
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR))

from config import Settings  # noqa: E402
from database import Database, apply_migrations  # noqa: E402
from main import create_app  # noqa: E402
from services.outbox_worker import OutboxWorker  # noqa: E402

load_dotenv(SERVICE_DIR / ".env")
load_dotenv(SERVICE_DIR.parents[1] / ".env")
TEST_JWT_SECRET = "pytest-notifications-secret-0123456789abcdef"


def _database_url() -> str:
    return os.getenv("NOTIFICATIONS_TEST_DATABASE_URL") or os.getenv("DATABASE_URL", "")


def _redis_url() -> str:
    return os.getenv("NOTIFICATIONS_TEST_REDIS_URL") or os.getenv("REDIS_URL", "redis://localhost:6379/0")


def make_settings(**overrides) -> Settings:
    values = dict(database_url=_database_url(), redis_url=_redis_url(), jwt_secret_key=TEST_JWT_SECRET,
                  log_level="WARNING", dependency_timeout_seconds=1.0, apply_migrations=False)
    values.update(overrides)
    return Settings(**values)


class TransactionalDatabase(Database):
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection

    @contextmanager
    def begin(self):
        with self.connection.begin_nested():
            yield self.connection

    def ping(self):
        self.connection.execute(text("SELECT 1"))


@pytest.fixture(scope="session")
def pg_engine():
    if not _database_url():
        pytest.skip("NOTIFICATIONS_TEST_DATABASE_URL / DATABASE_URL not configured")
    engine = create_engine(_database_url(), future=True, connect_args={"connect_timeout": 3})
    try:
        apply_migrations(engine)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL not reachable: {type(exc).__name__}")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def redis_client():
    client = redis.Redis.from_url(_redis_url(), decode_responses=True, socket_connect_timeout=2)
    try:
        client.ping()
    except redis.RedisError as exc:  # pragma: no cover
        pytest.skip(f"Redis not reachable: {type(exc).__name__}")
    yield client
    client.close()


@pytest.fixture
def db_conn(pg_engine):
    connection = pg_engine.connect()
    outer = connection.begin()
    try:
        yield connection
    finally:
        outer.rollback()
        connection.close()


@pytest.fixture
def database(db_conn):
    return TransactionalDatabase(db_conn)


@pytest.fixture
def worker(database):
    return OutboxWorker(database)


@pytest.fixture
def app(database, redis_client):
    return create_app(make_settings(), database=database, redis_client=redis_client)


@pytest.fixture
def client(app):
    return app.test_client()


def _run(conn, sql, **params):
    conn.execute(text(sql), params)


def _code(conn, table, code):
    return conn.execute(text(f"SELECT id FROM {table} WHERE code = :code"), {"code": code}).scalar_one()


@pytest.fixture
def world(db_conn):
    """Institutions A and B; operator, two supervisors and an admin in A; a session of A scheduled by spd_a."""
    c, tag = db_conn, uuid.uuid4().hex[:8]
    w = SimpleNamespace(tag=tag, users={}, inst={})
    for key in ("A", "B"):
        w.inst[key] = uuid.uuid4()
        _run(c, "INSERT INTO institution (id, name) VALUES (:id, :n)", id=w.inst[key], n=f"Notif {key} {tag}")
    roles = {}
    for key in ("A", "B"):
        for code in ("station_operator", "spd_supervisor", "it_admin"):
            roles[(key, code)] = uuid.uuid4()
            _run(c, "INSERT INTO role (id, code, description, institution_id) VALUES (:id, :code, 't', :i)",
                 id=roles[(key, code)], code=code, i=w.inst[key])
    for user_key, inst, codes, active in (("op_a", "A", ["station_operator"], True), ("spd_a", "A", ["spd_supervisor"], True),
                                          ("spd_a2", "A", ["spd_supervisor"], True), ("admin_a", "A", ["it_admin"], True),
                                          ("off_spd_a", "A", ["spd_supervisor"], False), ("op_b", "B", ["station_operator"], True)):
        user_id = uuid.uuid4()
        _run(c, 'INSERT INTO "user" (id, name, email, password_hash, active, institution_id) '
                "VALUES (:id, :n, :e, 'x', :a, :i)",
             id=user_id, n=f"N {user_key}", e=f"{user_key}.{tag}@notif-test.invalid", a=active, i=w.inst[inst])
        for code in codes:
            _run(c, "INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)", u=user_id, r=roles[(inst, code)])
        w.users[user_key] = SimpleNamespace(id=user_id, institution_id=w.inst[inst])

    def session(inst="A", operator="op_a"):
        operation, session_id = uuid.uuid4(), uuid.uuid4()
        _run(c, "INSERT INTO operation (id, scheduled_at, status_id, institution_id) VALUES (:id, :s, :st, :i)",
             id=operation, s=datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc),
             st=_code(c, "cat_operation_status", "scheduled"), i=w.inst[inst])
        _run(c, "INSERT INTO work_session (id, status_id, user_id, operation_id) VALUES (:id, :s, :u, :o)",
             id=session_id, s=_code(c, "cat_session_status", "scheduled"), u=w.users[operator].id, o=operation)
        return session_id

    def audit(action, session_id, actor, inst="A"):
        event_id = uuid.uuid4()
        _run(c, "INSERT INTO access_audit (id, occurred_at, actor_type, actor_user_id, action, resource_type, resource_id, "
                "institution_id, outcome) VALUES (:id, :at, 'user', :u, :a, 'work_session', :s, :i, 'success')",
             id=event_id, at=datetime.now(timezone.utc), u=w.users[actor].id, a=action, s=session_id, i=w.inst[inst])
        return event_id

    def count(code, session_id, actor=None, payload=None):
        event_id = uuid.uuid4()
        _run(c, "INSERT INTO count_event (id, event_type_id, payload, occurred_at, session_id, user_id) "
                "VALUES (:id, :t, CAST(:p AS jsonb), :at, :s, :u)",
             id=event_id, t=_code(c, "cat_event_type", code), p=json.dumps(payload or {}), at=datetime.now(timezone.utc),
             s=session_id, u=w.users[actor].id if actor else None)
        return event_id

    w.new_session, w.audit, w.count = session, audit, count
    w.session = session()
    w.scheduled = audit("session.scheduled", w.session, "spd_a")
    return w


class TokenFactory:
    def __init__(self, client):
        self.redis, self.keys = client, []

    def issue(self, user_id, institution_id, *, revoked=False, lifetime=timedelta(minutes=5), secret=TEST_JWT_SECRET,
              store=True):
        now = datetime.now(timezone.utc)
        jti = str(uuid.uuid4())
        token = jwt.encode({"sub": str(user_id), "jti": jti, "type": "access", "iat": now, "exp": now + lifetime},
                           secret, algorithm="HS256")
        if store:
            key = f"auth:jwt:{jti}"
            self.redis.set(key, json.dumps({"jti": jti, "user_id": str(user_id), "institution_id": str(institution_id),
                                            "token_type": "access", "revoked": revoked}), ex=300)
            self.keys.append(key)
        return token

    def cleanup(self):
        if self.keys:
            self.redis.delete(*self.keys)


@pytest.fixture
def tokens(redis_client):
    factory = TokenFactory(redis_client)
    yield factory
    factory.cleanup()


@pytest.fixture
def as_user(tokens, world):
    def build(key, **headers):
        user = world.users[key]
        return {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id)}", **headers}
    return build
