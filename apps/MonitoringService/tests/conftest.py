"""Fixtures for Monitoring Service tests (real PostgreSQL and Redis; real local HTTP servers).

The idempotent migration is applied once (committed, as in production); every test then runs in one
outer transaction rolled back at teardown. Probe tests talk HTTP to small real servers started on
127.0.0.1 that answer like the components do (these are unit tests of the classification: the live
components are checked end to end by scripts/e2e_monitoring.py).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

load_dotenv(SERVICE_DIR / ".env")
load_dotenv(SERVICE_DIR.parents[1] / ".env")
TEST_JWT_SECRET = "pytest-monitoring-secret-0123456789abcdef"


def _database_url() -> str:
    return os.getenv("MONITORING_TEST_DATABASE_URL") or os.getenv("DATABASE_URL", "")


def _redis_url() -> str:
    return os.getenv("MONITORING_TEST_REDIS_URL") or os.getenv("REDIS_URL", "redis://localhost:6379/0")


def make_settings(**overrides) -> Settings:
    values = dict(database_url=_database_url(), redis_url=_redis_url(), jwt_secret_key=TEST_JWT_SECRET,
                  log_level="WARNING", dependency_timeout_seconds=1.0, apply_migrations=False,
                  interval_seconds=30.0, check_timeout_seconds=1.0)
    values.update(overrides)
    return Settings(**values)


class _EngineShim:
    """What the probes need from an engine (connect(), url), bound to the test's transaction."""

    def __init__(self, connection, url):
        self.connection, self.url = connection, url

    @contextmanager
    def connect(self):
        with self.connection.begin_nested():
            yield self.connection


class TransactionalDatabase(Database):
    def __init__(self, connection, url):
        self.connection = connection
        self.engine = _EngineShim(connection, url)

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
        pytest.skip("MONITORING_TEST_DATABASE_URL / DATABASE_URL not configured")
    engine = create_engine(_database_url(), future=True, connect_args={"connect_timeout": 3})
    try:
        apply_migrations(engine)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL not reachable: {type(exc).__name__}")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def redis_client():
    client = redis.Redis.from_url(_redis_url(), decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
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
        # Each test starts from an empty history (only inside this transaction).
        connection.execute(text("DELETE FROM monitoring_run"))
        yield connection
    finally:
        outer.rollback()
        connection.close()


@pytest.fixture
def database(db_conn, pg_engine):
    db = TransactionalDatabase(db_conn, pg_engine.url)
    db.real_engine = pg_engine
    return db


@pytest.fixture
def app(database, redis_client):
    return create_app(make_settings(), database=database, redis_client=redis_client)


@pytest.fixture
def client(app):
    return app.test_client()


def _run(conn, sql, **params):
    conn.execute(text(sql), params)


@pytest.fixture
def world(db_conn):
    """Institution A with an administrator, a supervisor, an operator and an inactive administrator."""
    c, tag = db_conn, uuid.uuid4().hex[:8]
    w = SimpleNamespace(users={}, inst=uuid.uuid4())
    _run(c, "INSERT INTO institution (id, name) VALUES (:id, :n)", id=w.inst, n=f"Monitoring {tag}")
    roles = {}
    for code in ("station_operator", "spd_supervisor", "it_admin"):
        roles[code] = uuid.uuid4()
        _run(c, "INSERT INTO role (id, code, description, institution_id) VALUES (:id, :code, 't', :i)",
             id=roles[code], code=code, i=w.inst)
    for key, codes, active in (("admin", ["it_admin"], True), ("spd", ["spd_supervisor"], True),
                               ("op", ["station_operator"], True), ("off_admin", ["it_admin"], False),
                               ("both", ["spd_supervisor", "it_admin"], True)):
        user_id = uuid.uuid4()
        _run(c, 'INSERT INTO "user" (id, name, email, password_hash, active, institution_id) '
                "VALUES (:id, :n, :e, 'x', :a, :i)", id=user_id, n=key, e=f"{key}.{tag}@mon-test.invalid", a=active, i=w.inst)
        for code in codes:
            _run(c, "INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)", u=user_id, r=roles[code])
        w.users[key] = SimpleNamespace(id=user_id, institution_id=w.inst)
    return w


@pytest.fixture
def history(db_conn):
    """record(component, status, latency, minutes_ago, ...) inserts one stored check (with its run)."""
    def record(component, status, latency=None, minutes_ago=0.0, *, http=None, deps=None, error=None, details=None):
        run_id, at = uuid.uuid4(), datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        _run(db_conn, "INSERT INTO monitoring_run (id, started_at, finished_at, components, failures) "
                      "VALUES (:id, :at, :at, 1, 0)", id=run_id, at=at)
        check_id = uuid.uuid4()
        _run(db_conn, "INSERT INTO monitoring_check (id, run_id, component, checked_at, status, latency_ms, http_status, "
                      "dependencies, error_type, endpoint, details) VALUES (:id, :run, :c, :at, :s, :l, :h, "
                      "CAST(:d AS jsonb), :e, :ep, CAST(:det AS jsonb))",
             id=check_id, run=run_id, c=component, at=at, s=status, l=latency, h=http, d=json.dumps(deps or {}), e=error,
             ep=f"http://{component}:1/health/ready", det=json.dumps(details or {}))
        return check_id
    return record


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


# --- real local HTTP servers for the probe tests ------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        status, body, content_type, delay = self.server.answer(self.path)
        if delay:
            time.sleep(delay)
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def http_server():
    """http_server(status, body, content_type='application/json', delay=0) -> base URL of a real server."""
    servers = []

    def start(status=200, body=None, content_type="application/json", delay=0.0):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        server.answer = lambda path: (status, body if body is not None else {}, content_type, delay)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"
