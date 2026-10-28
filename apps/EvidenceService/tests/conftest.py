"""Shared fixtures for Evidence Service tests.

Isolation (nothing is left in the real stores):
- PostgreSQL: each test runs on ONE connection inside an outer transaction rolled back at teardown;
  institutions/users/roles are created there with random codes. Seeds are neither required nor touched.
- Redis: tokens use fresh random ``jti`` values with a short TTL and are deleted at teardown.
- Storage: a fake adapter by default; the real Garage is used only by the optional integration
  tests (skipped when Garage or its credentials are not available) and only for HeadBucket.

Phase A has no business endpoint, so the auth layer is exercised through a TEST-ONLY blueprint
registered on the test app (never part of the production application).
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
from dotenv import dotenv_values
from flask import Blueprint, jsonify
from sqlalchemy import create_engine, text

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parents[1]
sys.path.insert(0, str(SERVICE_DIR))

from auth import EVIDENCE_ROLE_CODES, current_principal, require_auth  # noqa: E402
from config import Settings  # noqa: E402
from database import Database  # noqa: E402
from main import create_app  # noqa: E402
from storage.s3_adapter import StorageOperationError, StorageSigningError, StorageUnavailable  # noqa: E402

ROOT_ENV = dotenv_values(REPO_ROOT / ".env")
# Garage integration tests: EVIDENCE_TEST_S3_* (e.g. the isolated E2E environment) override the repository .env.
for _key in ("S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_API_PORT", "S3_REGION", "S3_BUCKET_EVIDENCE"):
    if os.getenv(f"EVIDENCE_TEST_{_key}"):
        ROOT_ENV[_key] = os.environ[f"EVIDENCE_TEST_{_key}"]

# The suite always runs on its OWN database, recreated at the start of every run from the repository's
# migrations (the same files as the postgres initdb). Shared databases accumulate committed rows from
# other runs and E2E scripts (evidence audits, media_asset rows that the purge / reconcile jobs scan
# globally by design), which made results depend on history. Only a database whose name ends with this
# suffix is ever dropped.
TEST_DB_SUFFIX = "_evidence_test"
SCHEMA_FILES = ("data/migrations/001_init.sql", "data/seeds/002_seed_catalogs.sql", "data/seeds/003_seed_demo.sql",
                "data/migrations/004_media_asset_session.sql", "data/migrations/005_count_reference_events.sql",
                "data/migrations/006_notifications.sql", "data/migrations/007_monitoring.sql")
TEST_JWT_SECRET = "pytest-evidence-secret-0123456789abcdef-not-for-production"
TEST_JWT_ALGORITHM = "HS256"
TEST_S3_SECRET = "pytest-s3-secret-not-real"


def _database_url() -> str:
    """The suite's dedicated database (every app, engine and job of the tests uses it)."""
    return _dedicated_url(_base_database_url())[1].render_as_string(hide_password=False)


def _base_database_url() -> str:
    """Server and base name the dedicated database is derived from (never written by the tests)."""
    if os.getenv("EVIDENCE_TEST_DATABASE_URL"):
        return os.environ["EVIDENCE_TEST_DATABASE_URL"]
    user, password = ROOT_ENV.get("POSTGRES_USER", "MedAdmin"), ROOT_ENV.get("POSTGRES_PASSWORD", "")
    port, name = ROOT_ENV.get("POSTGRES_PORT", "5433"), ROOT_ENV.get("POSTGRES_DB", "InstruMed")
    return f"postgresql+psycopg://{user}:{password}@localhost:{port}/{name}"


def _redis_url() -> str:
    if os.getenv("EVIDENCE_TEST_REDIS_URL"):
        return os.environ["EVIDENCE_TEST_REDIS_URL"]
    return f"redis://:{ROOT_ENV.get('REDIS_PASSWORD', '')}@localhost:{ROOT_ENV.get('REDIS_PORT', '6379')}/0"


def make_settings(**overrides) -> Settings:
    values = dict(
        database_url=_database_url(),
        redis_url=_redis_url(),
        jwt_secret_key=TEST_JWT_SECRET,
        jwt_algorithm=TEST_JWT_ALGORITHM,
        s3_internal_endpoint="http://storage.invalid:3900",
        s3_public_endpoint="http://localhost:3900",
        s3_region="garage",
        s3_access_key_id="pytest-access-key",
        s3_secret_access_key=TEST_S3_SECRET,
        s3_bucket_evidence="evidence",
        log_level="WARNING",
        dependency_timeout_seconds=1.0,
    )
    values.update(overrides)
    return Settings(**values)


class FakeStorage:
    """In-memory stand-in for S3Storage that records every call.

    ``failure``: StorageUnavailable reason for check_bucket. ``sign_error``: signing fails.
    ``put_error`` / ``head_error`` / ``delete_error``: that operation fails. ``size_delta``: HeadObject
    reports a different size. ``on_put``: callable run right after a successful PUT (race tests).
    """

    bucket = "evidence"

    def __init__(self, failure: str | None = None, sign_error: bool = False, *, put_error: bool = False,
                 head_error: bool = False, delete_error: bool = False, size_delta: int = 0, on_put=None):
        self.failure = failure
        self.sign_error = sign_error
        self.put_error, self.head_error, self.delete_error = put_error, head_error, delete_error
        self.size_delta = size_delta
        self.on_put = on_put
        self.calls: list[str] = []
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.modified: dict[str, datetime] = {}

    def check_bucket(self) -> None:
        self.calls.append("check_bucket")
        if self.failure:
            raise StorageUnavailable(self.failure)

    def generate_presigned_get(self, bucket: str, object_key: str, expires_in: int) -> str:
        self.calls.append(f"sign:{bucket}/{object_key}:{expires_in}")
        if self.sign_error:
            raise StorageSigningError()
        return f"http://localhost:3900/{bucket}/{object_key}?X-Amz-Expires={expires_in}&X-Amz-Signature=fake"

    def put_file(self, object_key: str, fileobj, size: int, content_type: str) -> None:
        self.calls.append(f"put:{object_key}")
        if self.put_error:
            raise StorageOperationError("put")
        self.objects[object_key] = (fileobj.read(), content_type)
        self.modified[object_key] = datetime.now(timezone.utc)
        if self.on_put:
            self.on_put()

    def head_object_size(self, object_key: str) -> int:
        self.calls.append(f"head:{object_key}")
        if self.head_error or object_key not in self.objects:
            raise StorageOperationError("head")
        return len(self.objects[object_key][0]) + self.size_delta

    def delete_object(self, object_key: str) -> None:
        self.calls.append(f"delete:{object_key}")
        if self.delete_error:
            raise StorageOperationError("delete")
        self.objects.pop(object_key, None)
        self.modified.pop(object_key, None)

    def add_object(self, object_key: str, age_seconds: float = 0, data: bytes = b"x") -> None:
        """Test helper: an object with a given age (LastModified = now - age)."""
        self.objects[object_key] = (data, "application/octet-stream")
        self.modified[object_key] = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)

    def iter_objects(self, prefix: str = "", page_size: int = 1000):
        self.calls.append(f"list:{prefix}")
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield key, self.modified[key]

    def object_exists(self, object_key: str) -> bool:
        self.calls.append(f"exists:{object_key}")
        return object_key in self.objects


class TransactionalDatabase(Database):
    """Database bound to a single connection that the fixture rolls back."""

    def __init__(self, connection):
        super().__init__(connection.engine)
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection

    @contextmanager
    def begin(self):
        with self.connection.begin_nested():
            yield self.connection

    def ping(self) -> None:
        self.connection.execute(text("SELECT 1"))


def _test_blueprint() -> Blueprint:
    bp = Blueprint("pytest_only", __name__, url_prefix="/__test__")

    @bp.get("/whoami")
    @require_auth()
    def whoami():
        principal = current_principal()
        return jsonify({"user_id": str(principal.user_id), "institution_id": str(principal.institution_id),
                        "roles": sorted(principal.roles)})

    @bp.get("/clinical")
    @require_auth(*EVIDENCE_ROLE_CODES)
    def clinical():
        return jsonify({"ok": True})

    return bp


# --- Infrastructure -------------------------------------------------------------------


def _dedicated_url(base_url: str):
    """URL of the suite's own database: <base database name> + TEST_DB_SUFFIX on the same server."""
    from sqlalchemy.engine import make_url
    base = make_url(base_url)
    name = base.database if base.database.endswith(TEST_DB_SUFFIX) else base.database + TEST_DB_SUFFIX
    return base, base.set(database=name)


def _recreate_database(admin_engine, name: str) -> None:
    assert name.endswith(TEST_DB_SUFFIX), name           # never drop anything else
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))


def _apply_schema(engine) -> None:
    with engine.begin() as connection:
        cursor = connection.connection.cursor()        # raw DBAPI: SQL files are sent verbatim
        try:
            for relative in SCHEMA_FILES:
                cursor.execute((REPO_ROOT / relative).read_text(encoding="utf-8"))
        finally:
            cursor.close()


@pytest.fixture(scope="session")
def pg_engine():
    base, dedicated = _dedicated_url(_base_database_url())
    admin = create_engine(base.set(database=base.database if not base.database.endswith(TEST_DB_SUFFIX)
                                   else "postgres"), future=True, connect_args={"connect_timeout": 3})
    try:
        _recreate_database(admin, dedicated.database)
    except Exception as exc:  # pragma: no cover - environment dependent
        admin.dispose()
        pytest.skip(f"PostgreSQL not reachable for integration tests: {type(exc).__name__}")
    engine = create_engine(dedicated, future=True, connect_args={"connect_timeout": 3})
    _apply_schema(engine)
    yield engine
    engine.dispose()
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{dedicated.database}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def redis_client():
    client = redis.Redis.from_url(_redis_url(), decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
    try:
        client.ping()
    except redis.RedisError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Redis not reachable for integration tests: {type(exc).__name__}")
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
def storage():
    return FakeStorage()


@pytest.fixture
def app(db_conn, redis_client, storage):
    application = create_app(make_settings(), database=TransactionalDatabase(db_conn), redis_client=redis_client,
                             storage=storage)
    application.register_blueprint(_test_blueprint())
    return application


@pytest.fixture
def client(app):
    return app.test_client()


# --- Identity data -----------------------------------------------------------------------


def _run(connection, sql: str, **params):
    connection.execute(text(sql), params)


@pytest.fixture
def world(db_conn):
    """Institutions A, B (active) and OFF (inactive) with users and per-institution roles."""
    c, tag = db_conn, uuid.uuid4().hex[:8]
    w = SimpleNamespace(users={}, institutions={key: uuid.uuid4() for key in ("A", "B", "OFF")})
    for key, inst_id in w.institutions.items():
        _run(c, "INSERT INTO institution (id, name, active) VALUES (:id, :n, :a)",
             id=inst_id, n=f"Evidence test {key} {tag}", a=key != "OFF")
    roles = {}
    for inst_key in w.institutions:
        for code in ("station_operator", "spd_supervisor", "it_admin", "quality_viewer"):   # last: non-clinical
            role_id = uuid.uuid4()
            _run(c, "INSERT INTO role (id, code, description, institution_id) VALUES (:id, :c, 'test', :i)",
                 id=role_id, c=code, i=w.institutions[inst_key])
            roles[(inst_key, code)] = role_id

    def add_user(key, inst_key, role_keys, active=True):
        user_id = uuid.uuid4()
        _run(c, 'INSERT INTO "user" (id, name, email, password_hash, active, institution_id) '
                "VALUES (:id, :n, :e, 'x', :a, :i)",
             id=user_id, n=f"Test {key}", e=f"{key}.{tag}@evidence-test.invalid", a=active, i=w.institutions[inst_key])
        for role_inst, role_code in role_keys:
            _run(c, "INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)", u=user_id, r=roles[(role_inst, role_code)])
        w.users[key] = SimpleNamespace(id=user_id, institution_id=w.institutions[inst_key])

    add_user("op_a", "A", [("A", "station_operator")])
    add_user("spd_a", "A", [("A", "spd_supervisor")])
    add_user("admin_a", "A", [("A", "it_admin")])
    add_user("multi_a", "A", [("A", "station_operator"), ("A", "spd_supervisor")])
    add_user("foreign_role_a", "A", [("B", "spd_supervisor")])   # role of another institution only
    add_user("inactive_a", "A", [("A", "spd_supervisor")], active=False)
    add_user("off_user", "OFF", [("OFF", "spd_supervisor")])
    add_user("op_b", "B", [("B", "station_operator")])
    add_user("op_a2", "A", [("A", "station_operator")])
    add_user("spd_b", "B", [("B", "spd_supervisor")])
    add_user("viewer_a", "A", [("A", "quality_viewer")])
    return w


# --- Tokens -----------------------------------------------------------------------------


class TokenFactory:
    """Mints tokens exactly like BackendAuthService.issue_token + RedisTokenStore.store_token."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.keys: list[str] = []

    def issue(self, user_id, institution_id=None, *, token_type="access", stored_token_type=None,
              lifetime=timedelta(minutes=5), revoked=False, store=True, secret=TEST_JWT_SECRET,
              algorithm=TEST_JWT_ALGORITHM, role="default", stored_user_id=None, drop_claims=()) -> str:
        now = datetime.now(timezone.utc)
        jti = str(uuid.uuid4())
        claims = {"sub": str(user_id), "jti": jti, "type": token_type, "iat": now, "exp": now + lifetime,
                  "role": role}
        for name in drop_claims:
            claims.pop(name, None)
        token = jwt.encode(claims, None if algorithm == "none" else secret, algorithm=algorithm)
        if store:
            key = f"auth:jwt:{jti}"
            self.redis.set(key, json.dumps({
                "jti": jti, "user_id": str(stored_user_id or user_id),
                "institution_id": str(institution_id) if institution_id else None,
                "token_type": stored_token_type or token_type, "revoked": revoked,
                "exp": (now + lifetime).isoformat(),
            }), ex=300)
            self.keys.append(key)
        return token

    def cleanup(self) -> None:
        if self.keys:
            self.redis.delete(*self.keys)


@pytest.fixture
def tokens(redis_client):
    factory = TokenFactory(redis_client)
    yield factory
    factory.cleanup()


@pytest.fixture
def as_user(tokens, world):
    def build(user_key: str, **headers) -> dict[str, str]:
        user = world.users[user_key]
        return {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id)}", **headers}

    return build
