"""Fixtures for the /api/v1/auth integration tests.

Isolation (nothing is left in the real stores):
- PostgreSQL: main.engine is replaced by one connection inside an outer transaction that is
  rolled back; Auth's own transactions become SAVEPOINTs. Test institutions, users and roles
  are created there with random emails; seeds are never touched.
- Redis: main.token_store is replaced by a store that records every key it writes
  (auth:jwt:* and auth:login_fail:*) and deletes them at teardown. Each test uses its own
  fake client IP (X-Forwarded-For) so rate-limit counters never touch real ones.

Tests needing PostgreSQL/Redis are skipped (with the reason) if they are unreachable.
"""

from __future__ import annotations

import os
import random
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
import redis
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash

# Same test secret as test_preferences.py; must be set before main is imported.
os.environ.setdefault("JWT_SECRET_KEY", "pytest-only-secret-key-0123456789abcdef")
SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR))

import main  # noqa: E402
from token_store import RedisTokenStore  # noqa: E402

_FILE_ENV = {**dotenv_values(SERVICE_DIR.parents[1] / ".env"), **dotenv_values(SERVICE_DIR / ".env")}
PASSWORD = "Correct-Horse-9"


def _setting(test_name: str, name: str, default: str) -> str:
	return os.getenv(test_name) or os.getenv(name) or _FILE_ENV.get(name) or default


DATABASE_URL = _setting("AUTH_TEST_DATABASE_URL", "DATABASE_URL", "postgresql+psycopg://pef_user:CAMBIAR_PASSWORD_POSTGRES@localhost:5433/InstruMed")
REDIS_URL = _setting("AUTH_TEST_REDIS_URL", "REDIS_URL", "redis://:CAMBIAR_PASSWORD_REDIS@localhost:6379/0")


class TransactionalEngine:
	"""Stand-in for main.engine bound to one connection; begin() opens a SAVEPOINT."""

	def __init__(self, connection):
		self.connection = connection

	@contextmanager
	def connect(self):
		yield self.connection

	@contextmanager
	def begin(self):
		with self.connection.begin_nested():
			yield self.connection


class TrackingTokenStore(RedisTokenStore):
	def __init__(self, redis_url: str):
		super().__init__(redis_url)
		self.written: set[str] = set()

	def store_token(self, *, jti, **kwargs):
		self.written.add(self._jwt_key(jti))
		self.written.add(self._user_tokens_key(str(kwargs["user_id"])))
		return super().store_token(jti=jti, **kwargs)

	def register_login_failure(self, identity):
		self.written.add(self._fail_key(identity))
		return super().register_login_failure(identity)

	def cleanup(self):
		if self.written:
			self.client.delete(*self.written)


@pytest.fixture(scope="session")
def pg_engine():
	engine = create_engine(DATABASE_URL, future=True, connect_args={"connect_timeout": 3})
	try:
		with engine.connect() as connection:
			connection.execute(text("SELECT 1"))
	except Exception as exc:  # pragma: no cover - environment dependent
		pytest.skip(f"PostgreSQL not reachable for integration tests: {type(exc).__name__}")
	yield engine
	engine.dispose()


@pytest.fixture(scope="session")
def redis_available():
	client = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
	try:
		client.ping()
	except redis.RedisError as exc:  # pragma: no cover - environment dependent
		pytest.skip(f"Redis not reachable for integration tests: {type(exc).__name__}")
	finally:
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
def store(redis_available, monkeypatch):
	tracking = TrackingTokenStore(REDIS_URL)
	monkeypatch.setattr(main, "token_store", tracking)
	yield tracking
	tracking.cleanup()


@pytest.fixture
def live(db_conn, store, monkeypatch):
	"""Auth app wired to the rolled-back transaction and the tracking Redis store."""
	monkeypatch.setattr(main, "engine", TransactionalEngine(db_conn))
	return main.app


@pytest.fixture
def client(live):
	test_client = live.test_client()
	ip = f"198.18.{random.randint(0, 255)}.{random.randint(1, 254)}"
	test_client.environ_base["HTTP_X_FORWARDED_FOR"] = ip
	return test_client


@dataclass
class World:
	tag: str
	institution_a: uuid.UUID
	institution_b: uuid.UUID
	institution_off: uuid.UUID
	users: dict[str, uuid.UUID] = field(default_factory=dict)
	emails: dict[str, str] = field(default_factory=dict)


@pytest.fixture
def world(db_conn) -> World:
	tag = uuid.uuid4().hex[:8]
	w = World(tag=tag, institution_a=uuid.uuid4(), institution_b=uuid.uuid4(), institution_off=uuid.uuid4())
	run = lambda sql, **p: db_conn.execute(text(sql), p)  # noqa: E731
	for inst_id, label, active in ((w.institution_a, "A", True), (w.institution_b, "B", True), (w.institution_off, "OFF", False)):
		run("INSERT INTO institution (id, name, active) VALUES (:id, :n, :a)", id=inst_id, n=f"Auth test {label} {tag}", a=active)

	roles = {
		"a_operator": (uuid.uuid4(), "station_operator", w.institution_a, "Station operator"),
		"a_supervisor": (uuid.uuid4(), "spd_supervisor", w.institution_a, "SPD supervisor"),
		"a_admin": (uuid.uuid4(), "it_admin", w.institution_a, "IT admin"),
		"b_admin": (uuid.uuid4(), "it_admin", w.institution_b, "IT admin B"),
		"b_operator": (uuid.uuid4(), "station_operator", w.institution_b, "Station operator B"),
		"off_operator": (uuid.uuid4(), "station_operator", w.institution_off, "Station operator OFF"),
	}
	for role_id, code, inst, description in roles.values():
		run("INSERT INTO role (id, code, description, institution_id) VALUES (:id, :c, :d, :i)",
			id=role_id, c=code, d=description, i=inst)

	password_hash = generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000")

	def add_user(key, inst, role_keys, active=True, prefs='{"locale": "es-MX", "theme": "dark"}'):
		user_id = uuid.uuid4()
		email = f"{key}.{tag}@auth-test.invalid"
		run(
			'INSERT INTO "user" (id, name, email, password_hash, active, ui_preferences, institution_id) '
			"VALUES (:id, :n, :e, :h, :a, CAST(:p AS jsonb), :i)",
			id=user_id, n=f"Test {key}", e=email, h=password_hash, a=active, p=prefs, i=inst,
		)
		for role_key in role_keys:
			run("INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)", u=user_id, r=roles[role_key][0])
		w.users[key] = user_id
		w.emails[key] = email

	add_user("operator_a", w.institution_a, ["a_operator"])
	add_user("admin_a", w.institution_a, ["a_admin"])
	add_user("supervisor_a", w.institution_a, ["a_supervisor"])
	add_user("multi_a", w.institution_a, ["a_operator", "a_admin"])
	add_user("foreign_a", w.institution_a, ["a_operator", "b_admin"])
	add_user("inactive_a", w.institution_a, ["a_operator"], active=False)
	add_user("off_user", w.institution_off, ["off_operator"])
	add_user("operator_b", w.institution_b, ["b_operator"])
	return w


@pytest.fixture
def mint(store):
	"""Mint a token with arbitrary claims/algorithm and register its Redis session."""

	def build(user_id, institution_id, *, token_type="access", algorithm=None, lifetime=timedelta(minutes=5),
			  revoked=False, register=True, role="default", stored_type=None, key=None):
		now = datetime.now(timezone.utc)
		jti = str(uuid.uuid4())
		claims = {"sub": str(user_id), "jti": jti, "type": token_type, "iat": now, "exp": now + lifetime,
				  "role": role, "sas": int(now.timestamp()), "la": int(now.timestamp())}
		algorithm = algorithm or main.app.config["JWT_ALGORITHM"]
		signing_key = None if algorithm == "none" else (key or main.app.config["JWT_SECRET_KEY"])
		token = jwt.encode(claims, signing_key, algorithm=algorithm)
		if register:
			store.store_token(jti=jti, user_id=str(user_id), institution_id=str(institution_id) if institution_id else None,
							  token_type=stored_type or token_type, expires_at=now + timedelta(minutes=5))
			if revoked:
				store.revoke_token(jti)
		return token

	return build
