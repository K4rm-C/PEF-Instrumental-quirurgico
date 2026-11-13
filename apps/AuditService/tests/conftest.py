"""Fixtures for Audit Service integration tests (real PostgreSQL and Redis).

PostgreSQL: one connection inside an outer transaction rolled back at teardown; Database.read() and
begin() become SAVEPOINTs (READ ONLY enforcement is checked separately in test_read_only.py on a
real engine). Redis: tokens minted exactly like BackendAuthService, keys deleted at teardown.
Tests are skipped (with the reason) if PostgreSQL or Redis are unreachable.
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
from database import Database  # noqa: E402
from main import create_app  # noqa: E402

load_dotenv(SERVICE_DIR / ".env")
load_dotenv(SERVICE_DIR.parents[1] / ".env")

TEST_JWT_SECRET = "pytest-audit-secret-0123456789abcdef-not-for-production"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def _database_url() -> str:
    return os.getenv("AUDIT_TEST_DATABASE_URL") or os.getenv("DATABASE_URL", "")


def _redis_url() -> str:
    return os.getenv("AUDIT_TEST_REDIS_URL") or os.getenv("REDIS_URL", "redis://localhost:6379/0")


def make_settings(**overrides) -> Settings:
    values = dict(database_url=_database_url(), redis_url=_redis_url(), jwt_secret_key=TEST_JWT_SECRET,
                  log_level="WARNING", dependency_timeout_seconds=1.0, enforce_read_only=False)
    values.update(overrides)
    return Settings(**values)


class TransactionalDatabase(Database):
    def __init__(self, connection):
        self.connection = connection
        self.enforce_read_only = False

    @contextmanager
    def read(self):
        with self.connection.begin_nested():
            yield self.connection

    @contextmanager
    def begin(self):
        with self.connection.begin_nested():
            yield self.connection

    def ping(self) -> None:
        self.connection.execute(text("SELECT 1"))


@pytest.fixture(scope="session")
def pg_engine():
    if not _database_url():
        pytest.skip("AUDIT_TEST_DATABASE_URL / DATABASE_URL not configured")
    engine = create_engine(_database_url(), future=True, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL not reachable: {type(exc).__name__}")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def redis_client():
    client = redis.Redis.from_url(_redis_url(), decode_responses=True, socket_connect_timeout=2)
    try:
        client.ping()
    except redis.RedisError as exc:  # pragma: no cover - environment dependent
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
def app(db_conn, redis_client):
    return create_app(make_settings(), database=TransactionalDatabase(db_conn), redis_client=redis_client)


@pytest.fixture
def client(app):
    return app.test_client()


def _run(conn, sql, **params):
    conn.execute(text(sql), params)


def _code(c, table, code):
    return c.execute(text(f"SELECT id FROM {table} WHERE code = :c"), {"c": code}).scalar_one()


@pytest.fixture
def world(db_conn):
    """Institutions A and B with users, a counting session and real-shaped audit rows of every producer."""
    c, tag = db_conn, uuid.uuid4().hex[:8]
    w = SimpleNamespace(tag=tag, users={}, inst={}, events={})
    for key in ("A", "B"):
        w.inst[key] = uuid.uuid4()
        _run(c, "INSERT INTO institution (id, name) VALUES (:id, :n)", id=w.inst[key], n=f"Audit {key} {tag}")
    roles = {}
    for key in ("A", "B"):
        for code in ("it_admin", "spd_supervisor", "station_operator"):
            roles[(key, code)] = uuid.uuid4()
            _run(c, "INSERT INTO role (id, code, description, institution_id) VALUES (:id, :code, 't', :i)",
                 id=roles[(key, code)], code=code, i=w.inst[key])
    for user_key, inst, role_codes in (("admin_a", "A", ["it_admin"]), ("spd_a", "A", ["spd_supervisor"]),
                                       ("op_a", "A", ["station_operator"]), ("admin_b", "B", ["it_admin"]),
                                       ("norole_a", "A", [])):
        user_id = uuid.uuid4()
        _run(c, 'INSERT INTO "user" (id, name, email, password_hash, institution_id) VALUES (:id, :n, :e, \'x\', :i)',
             id=user_id, n=f"Audit {user_key}", e=f"{user_key}.{tag}@audit-test.invalid", i=w.inst[inst])
        for code in role_codes:
            _run(c, "INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)", u=user_id, r=roles[(inst, code)])
        w.users[user_key] = SimpleNamespace(id=user_id, institution_id=w.inst[inst])

    # A counting session of A (operation → institution A).
    w.operation, w.session = uuid.uuid4(), uuid.uuid4()
    _run(c, "INSERT INTO operation (id, scheduled_at, status_id, institution_id) VALUES (:id, :s, :st, :i)",
         id=w.operation, s=NOW, st=_code(c, "cat_operation_status", "scheduled"), i=w.inst["A"])
    _run(c, "INSERT INTO work_session (id, status_id, user_id, operation_id) VALUES (:id, :s, :u, :o)",
         id=w.session, s=_code(c, "cat_session_status", "in_progress"), u=w.users["op_a"].id, o=w.operation)
    w.family = uuid.uuid4()
    category = uuid.uuid4()
    _run(c, "INSERT INTO cat_instrument_category (id, code, name) VALUES (:id, :code, 'x')", id=category, code=f"zz_aud_{tag}")
    _run(c, "INSERT INTO instrument_family (id, code, name, category_id) VALUES (:id, :code, 'Family', :cat)",
         id=w.family, code=f"ZZ-AUD-{tag}", cat=category)

    def audit(key, minute, user, action, resource_type, resource_id, inst="A", outcome="success", corr=None, ip="10.1.2.3"):
        event_id = uuid.uuid4()
        _run(c, "INSERT INTO access_audit (id, occurred_at, actor_type, actor_user_id, action, resource_type, resource_id, "
                "institution_id, outcome, correlation_id, ip) VALUES (:id, :o, 'user', :u, :a, :rt, :ri, :i, :out, :corr, "
                "CAST(:ip AS inet))",
             id=event_id, o=NOW + timedelta(minutes=minute), u=w.users[user].id, a=action, rt=resource_type,
             ri=resource_id, i=w.inst[inst], out=outcome, corr=corr, ip=ip)
        w.events[key] = event_id

    def count(key, minute, code, user=None, expected=None, detected=None, payload=None, family=None):
        event_id = uuid.uuid4()
        _run(c, "INSERT INTO count_event (id, event_type_id, family_id, expected_quantity, detected_quantity, payload, "
                "occurred_at, session_id, user_id) VALUES (:id, :t, :f, :e, :d, CAST(:p AS jsonb), :o, :s, :u)",
             id=event_id, t=_code(c, "cat_event_type", code), f=family, e=expected, d=detected,
             p=json.dumps(payload or {}), o=NOW + timedelta(minutes=minute), s=w.session,
             u=w.users[user].id if user else None)
        w.events[key] = event_id

    audit("login", 0, "op_a", "LOGIN", "user", w.users["op_a"].id, corr="corr-login")
    audit("family", 1, "admin_a", "CREATE_INSTRUMENT_FAMILY", "instrument_family", w.family, corr="corr-fam")
    audit("scheduled", 2, "spd_a", "session.scheduled", "work_session", w.session, corr="corr-sched")
    audit("start", 3, "op_a", "session.start", "work_session", w.session)
    count("open", 3, "session_open", "op_a", payload={"phase_code": "start", "capture_mode": "vision"})
    count("initial", 4, "auto_count", None, expected=14, detected=5,
          payload={"trigger": "initial", "scope": "board", "board": {"families": [{"boxes": [[1, 2, 3, 4]]}]},
                   "run_id": "run-1", "notes": "free text"})
    count("blocked", 5, "close_blocked", "op_a", expected=6, detected=5, payload={"reason": "final_count_mismatch"})
    count("manual", 6, "manual_count", "op_a", expected=2, detected=1, family=w.family,
          payload={"reason_code": "shortage", "notes": "Operator note that must not leak"})
    audit("denied", 7, "op_a", "DENIED_POST_WORK_SESSION", "work_session", w.session, outcome="denied")
    audit("user_admin", 8, "admin_a", "CREATE_USER", "user", w.users["spd_a"].id)
    audit("foreign", 9, "admin_b", "CREATE_KIT", "kit", uuid.uuid4(), inst="B")
    return w


class TokenFactory:
    """Mints tokens exactly like BackendAuthService.issue_token + RedisTokenStore.store_token."""

    def __init__(self, client):
        self.redis = client
        self.keys = []

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
