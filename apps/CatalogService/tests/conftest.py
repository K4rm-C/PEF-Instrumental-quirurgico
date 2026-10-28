"""Shared fixtures for Catalog Service tests.

Isolation guarantees (nothing is left in the real stores):
- PostgreSQL: each test runs on ONE connection inside an outer transaction that is rolled
  back at teardown. The service's own transactions become SAVEPOINTs on that connection.
  All rows the tests need (institutions, users, roles, families, instruments, kits) are
  created inside that transaction with random codes; seeds are never touched.
- Redis: tokens are written under fresh random ``jti`` values with a short TTL and deleted
  at teardown.

Tests that need PostgreSQL/Redis are skipped (with the reason) when they are unreachable.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

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

TEST_JWT_SECRET = "pytest-catalog-secret-0123456789abcdef-not-for-production"
TEST_JWT_ALGORITHM = "HS256"


def _database_url() -> str:
    return os.getenv("CATALOG_TEST_DATABASE_URL") or os.getenv(
        "DATABASE_URL", "postgresql+psycopg://pef_user:CAMBIAR_PASSWORD_POSTGRES@localhost:5433/InstruMed"
    )


def _redis_url() -> str:
    return os.getenv("CATALOG_TEST_REDIS_URL") or os.getenv("REDIS_URL", "redis://:CAMBIAR_PASSWORD_REDIS@localhost:6379/0")


def make_settings(**overrides) -> Settings:
    values = dict(
        database_url=_database_url(),
        redis_url=_redis_url(),
        jwt_secret_key=TEST_JWT_SECRET,
        jwt_algorithm=TEST_JWT_ALGORITHM,
        log_level="WARNING",
        dependency_timeout_seconds=1.0,
    )
    values.update(overrides)
    return Settings(**values)


class TransactionalDatabase(Database):
    """Database bound to a single connection; service transactions become SAVEPOINTs."""

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


# --- Infrastructure -------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_engine():
    engine = create_engine(_database_url(), future=True, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL not reachable for integration tests: {type(exc).__name__}")
    yield engine
    engine.dispose()


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
def app(db_conn, redis_client):
    return create_app(make_settings(), database=TransactionalDatabase(db_conn), redis_client=redis_client)


@pytest.fixture
def client(app):
    return app.test_client()


# --- Test data (inside the rolled-back transaction) ----------------------------------------


@dataclass
class World:
    tag: str
    institution_a: uuid.UUID
    institution_b: uuid.UUID
    category_code: str
    cycle_status_code: str
    users: dict[str, uuid.UUID] = field(default_factory=dict)
    families: dict[str, uuid.UUID] = field(default_factory=dict)
    instruments_a: list[uuid.UUID] = field(default_factory=list)
    instruments_b: list[uuid.UUID] = field(default_factory=list)
    kits_a: dict[str, uuid.UUID] = field(default_factory=dict)
    kits_b: dict[str, uuid.UUID] = field(default_factory=dict)

    def family_code(self, suffix: str) -> str:
        return f"zz_test_{self.tag}_{suffix}"


def _insert(connection, sql: str, **params):
    connection.execute(text(sql), params)


@pytest.fixture
def world(db_conn) -> World:
    tag = uuid.uuid4().hex[:8]
    w = World(
        tag=tag,
        institution_a=uuid.uuid4(),
        institution_b=uuid.uuid4(),
        category_code=f"zz_test_cat_{tag}",
        cycle_status_code=f"zz_st_{tag}",
    )
    c = db_conn
    for inst_id, name in ((w.institution_a, "A"), (w.institution_b, "B")):
        _insert(c, "INSERT INTO institution (id, name, active) VALUES (:id, :name, TRUE)",
                id=inst_id, name=f"Test Institution {name} {tag}")

    roles = {
        "a_it_admin": (uuid.uuid4(), "it_admin", w.institution_a),
        "a_operator": (uuid.uuid4(), "station_operator", w.institution_a),
        "a_supervisor": (uuid.uuid4(), "spd_supervisor", w.institution_a),
        "b_it_admin": (uuid.uuid4(), "it_admin", w.institution_b),
        "b_operator": (uuid.uuid4(), "station_operator", w.institution_b),
    }
    for role_id, code, inst_id in roles.values():
        _insert(c, "INSERT INTO role (id, code, description, institution_id) VALUES (:id, :code, 'test', :inst)",
                id=role_id, code=code, inst=inst_id)

    def add_user(key: str, inst_id: uuid.UUID, role_keys: list[str], active: bool = True):
        user_id = uuid.uuid4()
        _insert(
            c,
            'INSERT INTO "user" (id, name, email, password_hash, active, institution_id) '
            "VALUES (:id, :name, :email, 'not-a-real-hash', :active, :inst)",
            id=user_id, name=f"Test {key}", email=f"{key}.{tag}@catalog-test.invalid", active=active, inst=inst_id,
        )
        for role_key in role_keys:
            _insert(c, "INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)", u=user_id, r=roles[role_key][0])
        w.users[key] = user_id

    add_user("admin_a", w.institution_a, ["a_it_admin"])
    # Primary role in the JWT would be station_operator, but it_admin is also effective.
    add_user("multi_a", w.institution_a, ["a_operator", "a_it_admin"])
    add_user("operator_a", w.institution_a, ["a_operator"])
    add_user("supervisor_a", w.institution_a, ["a_supervisor"])
    add_user("inactive_a", w.institution_a, ["a_it_admin"], active=False)
    # it_admin role of ANOTHER institution must not grant admin in A.
    add_user("foreign_role_a", w.institution_a, ["a_operator", "b_it_admin"])
    add_user("operator_b", w.institution_b, ["b_operator"])

    category_id = uuid.uuid4()
    _insert(c, "INSERT INTO cat_instrument_category (id, code, name) VALUES (:id, :code, 'Test category')",
            id=category_id, code=w.category_code)
    status_id = uuid.uuid4()
    _insert(c, "INSERT INTO cat_instrument_cycle_status (id, code, name) VALUES (:id, :code, 'Test status')",
            id=status_id, code=w.cycle_status_code)

    for suffix in ("b", "a", "c"):
        family_id = uuid.uuid4()
        _insert(
            c,
            "INSERT INTO instrument_family (id, code, name, category_id, active) "
            "VALUES (:id, :code, :name, :cat, :active)",
            id=family_id, code=w.family_code(suffix), name=f"Test family {suffix}", cat=category_id,
            active=suffix != "c",
        )
        w.families[suffix] = family_id

    def add_instrument(inst_id, family_key, internal_code, bucket):
        instrument_id = uuid.uuid4()
        _insert(
            c,
            "INSERT INTO instrument (id, internal_code, family_id, cycle_status_id, institution_id, active) "
            "VALUES (:id, :code, :fam, :st, :inst, TRUE)",
            id=instrument_id, code=internal_code, fam=w.families[family_key], st=status_id, inst=inst_id,
        )
        bucket.append(instrument_id)

    add_instrument(w.institution_a, "b", f"A-{tag}-002", w.instruments_a)
    add_instrument(w.institution_a, "a", f"A-{tag}-001", w.instruments_a)
    add_instrument(w.institution_a, "a", None, w.instruments_a)
    add_instrument(w.institution_b, "a", f"B-{tag}-001", w.instruments_b)
    add_instrument(w.institution_b, "c", f"B-{tag}-002", w.instruments_b)

    def add_kit(inst_id, name, version, items, bucket, key):
        kit_id = uuid.uuid4()
        _insert(c, "INSERT INTO kit (id, name, version, active, institution_id) VALUES (:id, :name, :v, TRUE, :inst)",
                id=kit_id, name=name, v=version, inst=inst_id)
        for family_key, quantity in items:
            _insert(c, "INSERT INTO kit_item (kit_id, family_id, quantity) VALUES (:k, :f, :q)",
                    k=kit_id, f=w.families[family_key], q=quantity)
        bucket[key] = kit_id

    add_kit(w.institution_a, f"Test Kit {tag}", 2, [("c", 1), ("a", 2), ("b", 3)], w.kits_a, "v2")
    add_kit(w.institution_a, f"Test Kit {tag}", 1, [("a", 1)], w.kits_a, "v1")
    add_kit(w.institution_b, f"Test Kit B {tag}", 1, [("a", 4)], w.kits_b, "v1")
    return w


# --- Tokens -----------------------------------------------------------------------------


class TokenFactory:
    """Mints tokens exactly like BackendAuthService.issue_token + RedisTokenStore.store_token."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.keys: list[str] = []

    def issue(
        self,
        user_id,
        institution_id=None,
        *,
        token_type: str = "access",
        stored_token_type: str | None = None,
        lifetime: timedelta = timedelta(minutes=5),
        revoked: bool = False,
        store: bool = True,
        secret: str = TEST_JWT_SECRET,
        algorithm: str = TEST_JWT_ALGORITHM,
        role: str = "default",
        stored_user_id=None,
    ) -> str:
        now = datetime.now(timezone.utc)
        jti = str(uuid.uuid4())
        claims = {
            "sub": str(user_id),
            "jti": jti,
            "type": token_type,
            "iat": now,
            "exp": now + lifetime,
            "role": role,
            "sas": int(now.timestamp()),
            "la": int(now.timestamp()),
        }
        token = jwt.encode(claims, None if algorithm == "none" else secret, algorithm=algorithm)
        if store:
            key = f"auth:jwt:{jti}"
            payload = {
                "jti": jti,
                "user_id": str(stored_user_id or user_id),
                "institution_id": str(institution_id) if institution_id else None,
                "token_type": stored_token_type or token_type,
                "revoked": revoked,
                "exp": (now + lifetime).isoformat(),
            }
            self.redis.set(key, json.dumps(payload), ex=300)
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
def auth_header(tokens, world):
    """auth_header('admin_a') → {'Authorization': 'Bearer ...'} for a World user."""

    def build(user_key: str, **kwargs) -> dict[str, str]:
        user_id = world.users[user_key]
        institution = world.institution_b if user_key.endswith("_b") else world.institution_a
        return {"Authorization": f"Bearer {tokens.issue(user_id, institution, **kwargs)}"}

    return build
