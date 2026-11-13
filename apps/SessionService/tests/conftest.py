"""Shared fixtures for Session Service tests.

Isolation (nothing is left in the real stores):
- PostgreSQL: each test runs on ONE connection inside an outer transaction rolled back at
  teardown. All rows (institutions, users, roles, operations, sessions, counts, discrepancies)
  are created there with random codes; seeds are neither required nor touched.
- Redis: tokens use fresh random ``jti`` values with a short TTL and are deleted at teardown.

Tests needing PostgreSQL/Redis are skipped (with the reason) when they are unreachable.
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

TEST_JWT_SECRET = "pytest-session-secret-0123456789abcdef-not-for-production"
TEST_JWT_ALGORITHM = "HS256"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _database_url() -> str:
    return os.getenv("SESSION_TEST_DATABASE_URL") or os.getenv(
        "DATABASE_URL", "postgresql+psycopg://pef_user:CAMBIAR_PASSWORD_POSTGRES@localhost:5433/InstruMed"
    )


def _redis_url() -> str:
    return os.getenv("SESSION_TEST_REDIS_URL") or os.getenv("REDIS_URL", "redis://:CAMBIAR_PASSWORD_REDIS@localhost:6379/0")


def make_settings(**overrides) -> Settings:
    values = dict(
        database_url=_database_url(),
        redis_url=_redis_url(),
        jwt_secret_key=TEST_JWT_SECRET,
        jwt_algorithm=TEST_JWT_ALGORITHM,
        log_level="WARNING",
        dependency_timeout_seconds=1.0,
        # The legacy Operator write suites run in compatibility mode; test_operator_writes_disabled.py
        # checks the production default (410).
        operator_writes_enabled=True,
    )
    values.update(overrides)
    return Settings(**values)


class TransactionalDatabase(Database):
    """Database bound to a single connection (reads only in Phase A/B)."""

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

    @contextmanager
    def read(self):
        # SAVEPOINT inside the test's outer transaction (READ ONLY is checked on a real engine in
        # test_reports.py::test_reports_run_in_read_only_transactions).
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


# --- Test data ---------------------------------------------------------------------------


def _run(connection, sql: str, **params):
    connection.execute(text(sql), params)


def _code_id(connection, table: str, code: str):
    return connection.execute(text(f"SELECT id FROM {table} WHERE code = :c"), {"c": code}).scalar_one()


@pytest.fixture
def world(db_conn):
    """Institutions A, B (active) and OFF (inactive) with users, roles and sessions."""
    c = db_conn
    tag = uuid.uuid4().hex[:8]
    w = SimpleNamespace(tag=tag, users={}, sessions={}, families={}, events={}, discrepancies={})
    w.institutions = {key: uuid.uuid4() for key in ("A", "B", "OFF")}
    for key, inst_id in w.institutions.items():
        _run(c, "INSERT INTO institution (id, name, active) VALUES (:id, :n, :a)",
             id=inst_id, n=f"Session test {key} {tag}", a=key != "OFF")

    roles = {}
    for inst_key in ("A", "B", "OFF"):
        for code in ("station_operator", "spd_supervisor", "it_admin"):
            role_id = uuid.uuid4()
            _run(c, "INSERT INTO role (id, code, description, institution_id) VALUES (:id, :c, 'test', :i)",
                 id=role_id, c=code, i=w.institutions[inst_key])
            roles[(inst_key, code)] = role_id

    def add_user(key, inst_key, role_keys, active=True):
        user_id = uuid.uuid4()
        _run(c, 'INSERT INTO "user" (id, name, email, password_hash, active, institution_id) '
                "VALUES (:id, :n, :e, 'x', :a, :i)",
             id=user_id, n=f"Test {key}", e=f"{key}.{tag}@session-test.invalid", a=active, i=w.institutions[inst_key])
        for role_inst, role_code in role_keys:
            _run(c, "INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)", u=user_id, r=roles[(role_inst, role_code)])
        w.users[key] = SimpleNamespace(id=user_id, institution_id=w.institutions[inst_key])

    add_user("op_a", "A", [("A", "station_operator")])
    add_user("op_a2", "A", [("A", "station_operator")])
    add_user("spd_a", "A", [("A", "spd_supervisor")])
    add_user("admin_a", "A", [("A", "it_admin")])
    add_user("multi_a", "A", [("A", "station_operator"), ("A", "spd_supervisor")])
    add_user("op_admin_a", "A", [("A", "station_operator"), ("A", "it_admin")])
    add_user("foreign_role_a", "A", [("B", "spd_supervisor")])   # role of another institution only
    add_user("norole_a", "A", [])
    add_user("inactive_a", "A", [("A", "spd_supervisor")], active=False)
    add_user("off_user", "OFF", [("OFF", "spd_supervisor")])
    add_user("op_b", "B", [("B", "station_operator")])
    add_user("spd_b", "B", [("B", "spd_supervisor")])

    category_id = uuid.uuid4()
    _run(c, "INSERT INTO cat_instrument_category (id, code, name) VALUES (:id, :c, 'test')",
         id=category_id, c=f"zz_ses_cat_{tag}")
    for suffix in ("a", "b", "c", "d"):
        family_id = uuid.uuid4()
        _run(c, "INSERT INTO instrument_family (id, code, name, category_id) VALUES (:id, :c, :n, :cat)",
             id=family_id, c=f"zz_ses_{tag}_{suffix}", n=f"Family {suffix}", cat=category_id)
        w.families[suffix] = family_id

    w.kits, w.stations, w.rooms = {}, {}, {}
    for inst_key in ("A", "B"):
        kit_id, room_id, station_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        _run(c, "INSERT INTO kit (id, name, version, institution_id) VALUES (:id, :n, 1, :i)",
             id=kit_id, n=f"Kit {inst_key} {tag}", i=w.institutions[inst_key])
        _run(c, "INSERT INTO operating_room (id, code, name, institution_id) VALUES (:id, :c, 'Room', :i)",
             id=room_id, c=f"R{inst_key}-{tag}", i=w.institutions[inst_key])
        _run(c, "INSERT INTO capture_station (id, name, room_id) VALUES (:id, 'Station', :r)", id=station_id, r=room_id)
        w.kits[inst_key], w.stations[inst_key], w.rooms[inst_key] = kit_id, station_id, room_id

    w.notice_id = uuid.uuid4()
    _run(c, "INSERT INTO privacy_notice_version (id, version, effective_at, document_uri) "
            "VALUES (:id, :v, now(), 'https://example.invalid/notice')", id=w.notice_id, v=f"ses-{tag}")
    status = {code: _code_id(c, "cat_session_status", code) for code in (
        "scheduled", "in_progress", "awaiting_spd_review", "correction_required", "closed")}
    op_status = _code_id(c, "cat_operation_status", "scheduled")
    phase_start = _code_id(c, "cat_operation_phase", "start")
    w.event_type = {code: _code_id(c, "cat_event_type", code) for code in (
        "session_open", "auto_count", "manual_count", "manual_close")}
    reason = {code: _code_id(c, "cat_discrepancy_reason", code) for code in ("shortage", "surplus")}

    def add_session(key, inst_key, status_code, user_key, *, scheduled_at=None, capture_mode=None,
                    started=False, with_operation=True, agreement=False, phase=True):
        operation_id = None
        if with_operation:
            operation_id = uuid.uuid4()
            _run(c, "INSERT INTO operation (id, scheduled_at, status_id, institution_id) VALUES (:id, :s, :st, :i)",
                 id=operation_id, s=scheduled_at, st=op_status, i=w.institutions[inst_key])
        session_id = uuid.uuid4()
        _run(c, "INSERT INTO work_session (id, started_at, status_id, user_id, operation_id, station_id, kit_id, "
                "current_phase_id, capture_mode) VALUES (:id, :st, :s, :u, :o, :stn, :k, :ph, :cm)",
             id=session_id, st=NOW - timedelta(hours=2) if started else None, s=status[status_code],
             u=w.users[user_key].id, o=operation_id, stn=w.stations[inst_key], k=w.kits[inst_key],
             ph=phase_start if phase else None, cm=capture_mode)
        if agreement:
            _run(c, "INSERT INTO session_processing_agreement (session_id, privacy_notice_version_id) VALUES (:s, :n)",
                 s=session_id, n=w.notice_id)
        w.sessions[key] = SimpleNamespace(id=session_id, operation_id=operation_id)
        return session_id

    add_session("a1", "A", "scheduled", "op_a", scheduled_at=NOW + timedelta(days=2))
    add_session("a2", "A", "in_progress", "op_a2", scheduled_at=NOW + timedelta(days=1), capture_mode="vision",
                started=True, agreement=True)
    a3 = add_session("a3", "A", "correction_required", "op_a", scheduled_at=NOW + timedelta(days=3),
                     capture_mode="manual_no_privacy", started=True)
    add_session("a_unscheduled", "A", "closed", "op_a2", scheduled_at=None, capture_mode="manual_no_privacy",
                started=True, phase=False)
    add_session("no_operation", "A", "scheduled", "op_a", with_operation=False)
    b1 = add_session("b1", "B", "correction_required", "op_b", scheduled_at=NOW + timedelta(days=5),
                     capture_mode="manual_no_privacy", started=True)

    # Expected inventory of a3: all four real sources (DS04 v0.2.1).
    for suffix, qty, source in (("a", 2, "kit_snapshot"), ("b", 1, "schedule_additional"),
                                ("c", 3, "live_add"), ("d", 1, "manual")):
        _run(c, "INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source) "
                "VALUES (:s, :f, :q, :src)", s=a3, f=w.families[suffix], q=qty, src=source)
    _run(c, "INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source) "
            "VALUES (:s, :f, 5, 'kit_snapshot')", s=b1, f=w.families["a"])

    def add_event(key, session_id, type_code, minute, *, family=None, expected=None, detected=None,
                  payload=None, user_key=None, client_event_id=None):
        event_id = uuid.uuid4()
        _run(c, "INSERT INTO count_event (id, event_type_id, client_event_id, family_id, expected_quantity, "
                "detected_quantity, payload, occurred_at, session_id, user_id) VALUES (:id, :t, :ce, :f, :e, :d, "
                "CAST(:p AS jsonb), :o, :s, :u)",
             id=event_id, t=w.event_type[type_code], ce=client_event_id, f=w.families[family] if family else None,
             e=expected, d=detected, p=json.dumps(payload or {}), o=NOW + timedelta(minutes=minute), s=session_id,
             u=w.users[user_key].id if user_key else None)
        w.events[key] = event_id
        return event_id

    add_event("open", a3, "session_open", 0, user_key="op_a",
              payload={"phase_code": "start", "capture_mode": "manual_no_privacy", "source": "station"})
    add_event("auto_legacy", a3, "auto_count", 1, expected=7, detected=3, user_key="op_a", payload={
        "ai": True, "capture_mode": "vision", "scope": "board", "reason": "video_window",
        "model_version": "yolo26l-demo", "pipeline": "matrix_v1", "run_id": "run-123",
        "frame_index": 42, "frames_processed": 40, "video_t": 12.5,
        "board": {"families": [
            {"family_id": str(w.families["a"]), "family_code": "x", "detected_quantity": 2, "best_confidence": 0.93,
             "boxes": [{"xyxy": [1, 2, 3, 4], "topk": [{"family_code": "x", "score": 0.9}]}]},
            {"family_id": str(w.families["b"]), "detected_quantity": 1, "box_count": 1},
            {"family_id": "not-a-uuid", "detected_quantity": 9},
        ], "totals": {"expected": 7, "detected": 3}},
        "assignment_metrics": {"solver": "hungarian"},
    })
    w.client_event_id = uuid.uuid4()
    add_event("auto_final", a3, "auto_count", 2, expected=7, detected=2, client_event_id=w.client_event_id,
              payload={"model_version": "yolo26l-demo", "trigger": "final", "counts": [
                  {"family_id": str(w.families["b"]), "detected_quantity": 1},
                  {"family_id": str(w.families["a"]), "detected_quantity": 1},
              ]})
    modern = add_event("manual_modern", a3, "manual_count", 3, family="a", expected=2, detected=1, user_key="op_a",
                       payload={"reason_code": "shortage", "notes": "missing"})
    add_event("manual_legacy", a3, "manual_count", 4, family="b", expected=1, detected=None, user_key="op_a",
              payload={"ai": False, "reported_quantity": 1, "reason_code": "other", "notes": "legacy other",
                       "close_path": "manual_count"})
    add_event("manual_bad_legacy", a3, "manual_count", 5, family="c", expected=3, detected=None, user_key="op_a",
              payload={"reported_quantity": "3"})
    add_event("close", a3, "manual_close", 6, user_key="op_a", payload={"open_discrepancies": 2})
    w.hc_recorded_at = NOW + timedelta(minutes=3, seconds=30)
    _run(c, "INSERT INTO human_correction (justification, recorded_at, count_event_id, user_id) "
            "VALUES (:j, :r, :e, :u)", j="La pieza estaba bajo una gasa", r=w.hc_recorded_at, e=modern,
         u=w.users["op_a"].id)
    add_event("b_count", b1, "manual_count", 0, family="a", expected=5, detected=4, user_key="op_b")

    def add_discrepancy(key, session_id, *, family, reason_code, expected, detected, resolved=False,
                        description, origin=None):
        disc_id = uuid.uuid4()
        _run(c, "INSERT INTO discrepancy (id, description, resolved, resolved_at, reason_id, family_id, "
                "expected_quantity, detected_quantity, session_id, origin_event_id) "
                "VALUES (:id, :d, :r, :ra, :reason, :f, :e, :det, :s, :o)",
             id=disc_id, d=description, r=resolved, ra=NOW + timedelta(hours=1) if resolved else None,
             reason=reason[reason_code] if reason_code else None, f=w.families[family] if family else None,
             e=expected, det=detected, s=session_id, o=origin)
        w.discrepancies[key] = disc_id

    add_discrepancy("open_a", a3, family="a", reason_code="shortage", expected=2, detected=1,
                    description="Faltante en Portaagujas: esperado 2, reportado 1.", origin=modern)
    add_discrepancy("resolved_b", a3, family="b", reason_code="surplus", expected=1, detected=2, resolved=True,
                    description="Extra on Family b.\nSPD resolution (resolved): encontrada en charola auxiliar")
    add_discrepancy("open_nofamily", a3, family=None, reason_code=None, expected=None, detected=None,
                    description="Unclassified discrepancy")
    add_discrepancy("b_open", b1, family="a", reason_code="shortage", expected=5, detected=4,
                    description="B shortage")
    return w


# --- Tokens -----------------------------------------------------------------------------


class TokenFactory:
    """Mints tokens exactly like BackendAuthService.issue_token + RedisTokenStore.store_token."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.keys: list[str] = []

    def issue(self, user_id, institution_id=None, *, token_type="access", stored_token_type=None,
              lifetime=timedelta(minutes=5), revoked=False, store=True, secret=TEST_JWT_SECRET,
              algorithm=TEST_JWT_ALGORITHM, role="default", stored_user_id=None) -> str:
        now = datetime.now(timezone.utc)
        jti = str(uuid.uuid4())
        claims = {"sub": str(user_id), "jti": jti, "type": token_type, "iat": now, "exp": now + lifetime,
                  "role": role, "sas": int(now.timestamp()), "la": int(now.timestamp())}
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
    """as_user('spd_a', Accept='application/xml') → headers with a Bearer token for that user."""

    def build(user_key: str, **headers) -> dict[str, str]:
        user = world.users[user_key]
        return {"Authorization": f"Bearer {tokens.issue(user.id, user.institution_id)}", **headers}

    return build
