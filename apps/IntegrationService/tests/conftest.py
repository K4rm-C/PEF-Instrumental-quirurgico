"""Fixtures for Integration Service tests (real PostgreSQL and Redis).

Two kinds of data:
- `world` (most tests): inside one outer transaction rolled back at teardown, like the other services;
- `committed` (change-feed tests): the feed only publishes changes of COMMITTED transactions (xid8 watermark),
  so those tests commit a private institution and remove every row they created at teardown (sync_change,
  sync_resource_version and access_audit rows of that institution included).

Upstream owner services and FHIR servers are real local HTTP servers started on 127.0.0.1 that answer like
the services do (test doubles: the real services and a real HAPI FHIR server are used by the E2E scripts).
"""

from __future__ import annotations

import json
import os
import sys
import threading
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
TEST_JWT_SECRET = "pytest-integration-secret-0123456789abcdef"


def _database_url() -> str:
    return os.getenv("INTEGRATION_TEST_DATABASE_URL") or os.getenv("DATABASE_URL", "")


def _redis_url() -> str:
    return os.getenv("INTEGRATION_TEST_REDIS_URL") or os.getenv("REDIS_URL", "redis://localhost:6379/0")


def make_settings(**overrides) -> Settings:
    values = dict(database_url=_database_url(), redis_url=_redis_url(), jwt_secret_key=TEST_JWT_SECRET,
                  log_level="WARNING", dependency_timeout_seconds=1.0, apply_migrations=False,
                  upstreams={"session": "", "catalog": "", "notifications": ""}, upstream_timeout_seconds=2.0,
                  allowed_hosts=("127.0.0.1",), allow_plain_http=True, delivery_timeout_seconds=2.0,
                  delivery_backoff_seconds=0.1, delivery_max_attempts=3, operation_lease_seconds=30.0)
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
        pytest.skip("INTEGRATION_TEST_DATABASE_URL / DATABASE_URL not configured")
    engine = create_engine(_database_url(), future=True, connect_args={"connect_timeout": 3})
    try:
        apply_migrations(engine)
    except Exception as exc:  # pragma: no cover
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
        yield connection
    finally:
        outer.rollback()
        connection.close()


@pytest.fixture
def database(db_conn):
    return TransactionalDatabase(db_conn)


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def app(database, redis_client, settings):
    return create_app(settings, database=database, redis_client=redis_client)


@pytest.fixture
def client(app):
    return app.test_client()


def _run(conn, sql, **params):
    return conn.execute(text(sql), params)


def build_world(c, tag: str) -> SimpleNamespace:
    """Institutions A and B with users, a room/station, physicians, an operation (encounter) and a client."""
    w = SimpleNamespace(tag=tag, users={}, inst={}, clients={}, rooms={}, physicians={}, operations={}, patients={})
    for key in ("A", "B"):
        w.inst[key] = uuid.uuid4()
        _run(c, "INSERT INTO institution (id, name) VALUES (:id, :n)", id=w.inst[key], n=f"Integration {key} {tag}")
        roles = {}
        for code in ("station_operator", "spd_supervisor", "it_admin"):
            roles[code] = uuid.uuid4()
            _run(c, "INSERT INTO role (id, code, description, institution_id) VALUES (:id, :code, 't', :i)",
                 id=roles[code], code=code, i=w.inst[key])
        for user, code in (("admin", "it_admin"), ("spd", "spd_supervisor"), ("op", "station_operator")):
            user_id = uuid.uuid4()
            _run(c, 'INSERT INTO "user" (id, name, email, password_hash, institution_id) VALUES (:id, :n, :e, \'x\', :i)',
                 id=user_id, n=f"{user}{key}", e=f"{user}{key}.{tag}@int-test.invalid", i=w.inst[key])
            _run(c, "INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)", u=user_id, r=roles[code])
            w.users[f"{user}_{key.lower()}"] = SimpleNamespace(id=user_id, institution_id=w.inst[key])
        room = uuid.uuid4()
        _run(c, "INSERT INTO operating_room (id, code, name, institution_id) VALUES (:id, :c, 'OR 1', :i)",
             id=room, c=f"OR-{key}-{tag}", i=w.inst[key])
        w.rooms[key] = room
        physician = uuid.uuid4()
        _run(c, "INSERT INTO physician (id, name, institution_id) VALUES (:id, :n, :i)", id=physician,
             n=f"Dr. Test {key}", i=w.inst[key])
        w.physicians[key] = physician
        patient = uuid.uuid4()
        _run(c, "INSERT INTO patient (id, display_name, birth_date, gender_id, institution_id) VALUES "
                "(:id, :n, '1980-02-03', (SELECT id FROM cat_gender WHERE code = 'female'), :i)",
             id=patient, n=f"Test Patient {key}", i=w.inst[key])
        w.patients[key] = patient
        operation = uuid.uuid4()
        _run(c, "INSERT INTO operation (id, scheduled_at, started_at, status_id, procedure_type_id, room_id, institution_id) "
                "VALUES (:id, '2026-11-03T09:00:00Z', '2026-11-03T09:10:00Z', "
                "(SELECT id FROM cat_operation_status WHERE code = 'in_progress'), "
                "(SELECT id FROM cat_procedure_type ORDER BY code LIMIT 1), :room, :i)",
             id=operation, room=room, i=w.inst[key])
        _run(c, "INSERT INTO operation_physician (operation_id, physician_id, surgical_role_id) VALUES "
                "(:o, :p, (SELECT id FROM cat_surgical_role ORDER BY code LIMIT 1))", o=operation, p=physician)
        _run(c, "INSERT INTO operation_patient (operation_id, patient_id) VALUES (:o, :p)", o=operation, p=patient)
        w.operations[key] = operation
    return w


@pytest.fixture
def world(db_conn):
    return build_world(db_conn, uuid.uuid4().hex[:8])


@pytest.fixture
def machine(db_conn, world):
    """machine(key, inst, scopes, active=True) -> integration_client row id."""
    def add(key, inst="A", scopes=(), active=True):
        row_id = uuid.uuid4()
        _run(db_conn, "INSERT INTO integration_client (id, client_id, name, secret_hash, scopes, active, institution_id) "
                      "VALUES (:id, :cid, :n, 'x', CAST(:s AS jsonb), :a, :i)",
             id=row_id, cid=f"{key}-{world.tag}", n=key, s=json.dumps(list(scopes)), a=active, i=world.inst[inst])
        world.clients[key] = row_id
        return row_id
    return add


class TokenFactory:
    def __init__(self, client):
        self.redis, self.keys = client, []

    def _store(self, jti, payload):
        key = f"auth:jwt:{jti}"
        self.redis.set(key, json.dumps(payload), ex=300)
        self.keys.append(key)

    def user(self, user_id, institution_id, *, revoked=False):
        now, jti = datetime.now(timezone.utc), str(uuid.uuid4())
        token = jwt.encode({"sub": str(user_id), "jti": jti, "type": "access", "iat": now,
                            "exp": now + timedelta(minutes=5)}, TEST_JWT_SECRET, algorithm="HS256")
        self._store(jti, {"jti": jti, "user_id": str(user_id), "institution_id": str(institution_id),
                          "token_type": "access", "revoked": revoked})
        return token

    def client(self, row_id, institution_id, scopes, *, revoked=False, lifetime=timedelta(minutes=5)):
        now, jti = datetime.now(timezone.utc), str(uuid.uuid4())
        token = jwt.encode({"sub": str(row_id), "jti": jti, "type": "client_access", "iat": now, "exp": now + lifetime,
                            "scope": " ".join(scopes), "inst": str(institution_id)}, TEST_JWT_SECRET, algorithm="HS256")
        self._store(jti, {"jti": jti, "client_id": str(row_id), "user_id": None, "institution_id": str(institution_id),
                          "token_type": "client_access", "revoked": revoked})
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
        return {"Authorization": f"Bearer {tokens.user(user.id, user.institution_id)}", **headers}
    return build


@pytest.fixture
def as_client(tokens, world, db_conn):
    def build(key, scopes=None, inst="A", **headers):
        row_id = world.clients[key]
        granted = scopes if scopes is not None else json.loads(json.dumps(db_conn.execute(
            text("SELECT scopes FROM integration_client WHERE id = :id"), {"id": row_id}).scalar_one()))
        return {"Authorization": f"Bearer {tokens.client(row_id, world.inst[inst], granted)}", **headers}
    return build


# --- committed data for the change feed --------------------------------------------------------------------


@pytest.fixture
def committed(pg_engine, redis_client):
    """A committed world (institutions A/B); everything it created is deleted at teardown."""
    tag = uuid.uuid4().hex[:8]
    with pg_engine.begin() as c:
        w = build_world(c, tag)
    w.engine = pg_engine
    yield w
    insts = [w.inst["A"], w.inst["B"]]
    with pg_engine.begin() as c:
        ids = {"i": insts}
        c.execute(text("DELETE FROM access_audit WHERE institution_id = ANY(:i)"), ids)
        c.execute(text("DELETE FROM sync_operation WHERE institution_id = ANY(:i)"), ids)
        c.execute(text("DELETE FROM surgical_tray WHERE institution_id = ANY(:i)"), ids)
        c.execute(text("DELETE FROM operation WHERE institution_id = ANY(:i)"), ids)
        c.execute(text("DELETE FROM patient WHERE institution_id = ANY(:i)"), ids)
        c.execute(text("DELETE FROM physician WHERE institution_id = ANY(:i)"), ids)
        c.execute(text("DELETE FROM capture_station WHERE room_id IN (SELECT id FROM operating_room WHERE institution_id = ANY(:i))"), ids)
        c.execute(text("DELETE FROM operating_room WHERE institution_id = ANY(:i)"), ids)
        c.execute(text("DELETE FROM kit_item WHERE kit_id IN (SELECT id FROM kit WHERE institution_id = ANY(:i))"), ids)
        c.execute(text("DELETE FROM kit WHERE institution_id = ANY(:i)"), ids)
        c.execute(text('DELETE FROM user_role WHERE user_id IN (SELECT id FROM "user" WHERE institution_id = ANY(:i))'), ids)
        c.execute(text('DELETE FROM "user" WHERE institution_id = ANY(:i)'), ids)
        c.execute(text("DELETE FROM role WHERE institution_id = ANY(:i)"), ids)
        c.execute(text("DELETE FROM sync_resource_version WHERE resource_id IN (SELECT resource_id FROM sync_change "
                       "WHERE institution_id = ANY(:i))"), ids)
        c.execute(text("DELETE FROM sync_change WHERE institution_id = ANY(:i)"), ids)
        c.execute(text("DELETE FROM institution WHERE id = ANY(:i)"), ids)


@pytest.fixture
def committed_app(pg_engine, redis_client):
    return create_app(make_settings(), database=Database(pg_engine), redis_client=redis_client)


# --- real local HTTP servers (owner-service and FHIR-server doubles) ------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def _answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.calls.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                  "body": json.loads(body) if body else None})
        status, payload, delay = self.server.script(self.command, self.path, len(self.server.calls))
        if delay:
            import time
            time.sleep(delay)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = _answer

    def log_message(self, *args):
        pass


@pytest.fixture
def upstream():
    """upstream(script) -> server; script(method, path, n) -> (status, body, delay). server.calls records requests."""
    servers = []

    def start(script):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads, server.calls, server.script = True, [], script
        threading.Thread(target=server.serve_forever, daemon=True).start()
        server.url = f"http://127.0.0.1:{server.server_address[1]}"
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()
