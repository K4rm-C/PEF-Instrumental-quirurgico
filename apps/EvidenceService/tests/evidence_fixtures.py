"""Phase B fixtures: sessions and media_asset rows created directly in PostgreSQL (test data only —
there is no write endpoint), inside the rolled-back transaction of ``db_conn``.

edata.sessions:
  a1  tenant A, assigned to op_a   (assets: jpeg, gif, blocked, purged, two with equal created_at)
  a2  tenant A, assigned to op_a2  (one asset)
  b1  tenant B, assigned to op_b   (one asset)
  empty  tenant A, assigned to op_a, no assets
  noop   no operation (legacy/corrupt), assigned to op_a (one asset)
edata.assets["orphan"]: media_asset with session_id NULL (DS03/MLOps-like).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

T0 = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)
SHA = "a" * 64


def _run(conn, sql, **params):
    conn.execute(text(sql), params)


def add_session(conn, world, user_key, inst_key, *, with_operation=True):
    operation_id = None
    if with_operation:
        operation_id = uuid.uuid4()
        _run(conn, "INSERT INTO operation (id, status_id, institution_id) VALUES "
                   "(:id, (SELECT id FROM cat_operation_status WHERE code = 'scheduled'), :i)",
             id=operation_id, i=world.institutions[inst_key])
    session_id = uuid.uuid4()
    _run(conn, "INSERT INTO work_session (id, status_id, user_id, operation_id, capture_mode, started_at) VALUES "
               "(:id, (SELECT id FROM cat_session_status WHERE code = 'in_progress'), :u, :o, 'vision', :st)",
         id=session_id, u=world.users[user_key].id, o=operation_id, st=T0 - timedelta(hours=1))
    return SimpleNamespace(id=session_id, operation_id=operation_id, institution_id=world.institutions[inst_key])


def add_asset(conn, session, *, minute=0, kind="jpeg", blocked=False, purged=False, asset_id=None,
              bucket="evidence", object_key=None, created_at=None, institution_id=None):
    asset_id = asset_id or uuid.uuid4()
    session_id = session.id if session else None
    prefix = f"{institution_id or (session.institution_id if session else 'models')}/{session_id or 'models'}"
    object_key = object_key or f"{prefix}/2026/{asset_id}.{'gif' if kind == 'gif' else 'jpg'}"
    created = created_at or T0 + timedelta(minutes=minute)
    _run(conn, "INSERT INTO media_asset (id, session_id, gcs_uri, bucket, object_key, content_type, kind, "
               "storage_provider, sha256, size_bytes, retention_until, purge_requested_at, purged_at, blocked_at, "
               "created_at, updated_at) VALUES (:id, :s, :uri, :b, :k, :ct, :kind, 'other', :sha, 1234, :ret, :preq, "
               ":purged, :blocked, :c, :c)",
         id=asset_id, s=session_id, uri=f"s3://{bucket}/{object_key}", b=bucket, k=object_key,
         ct="image/gif" if kind == "gif" else "image/jpeg", kind=kind, sha=SHA, ret=created + timedelta(days=90),
         preq=created + timedelta(minutes=1) if purged else None, purged=created + timedelta(minutes=2) if purged else None,
         blocked=created + timedelta(minutes=1) if blocked else None, c=created)
    return asset_id


@pytest.fixture
def edata(world, db_conn):
    c = db_conn
    d = SimpleNamespace(sessions={}, assets={})
    d.sessions["a1"] = add_session(c, world, "op_a", "A")
    d.sessions["a2"] = add_session(c, world, "op_a2", "A")
    d.sessions["b1"] = add_session(c, world, "op_b", "B")
    d.sessions["empty"] = add_session(c, world, "op_a", "A")
    d.sessions["noop"] = add_session(c, world, "op_a", "A", with_operation=False)
    s = d.sessions
    # Inserted out of chronological order on purpose; two share created_at (tie broken by id).
    d.assets["a1_gif"] = add_asset(c, s["a1"], minute=1, kind="gif")
    d.assets["a1_jpeg"] = add_asset(c, s["a1"], minute=0)
    d.assets["a1_blocked"] = add_asset(c, s["a1"], minute=2, blocked=True)
    d.assets["a1_purged"] = add_asset(c, s["a1"], minute=3, purged=True)
    d.assets["a1_tie_hi"] = add_asset(c, s["a1"], minute=4, asset_id=uuid.UUID("ffffffff-0000-4000-8000-000000000001"))
    d.assets["a1_tie_lo"] = add_asset(c, s["a1"], minute=4, asset_id=uuid.UUID("00000000-0000-4000-8000-000000000001"))
    d.assets["a2"] = add_asset(c, s["a2"])
    d.assets["b1"] = add_asset(c, s["b1"])
    d.assets["noop"] = add_asset(c, s["noop"], institution_id=world.institutions["A"])
    d.assets["orphan"] = add_asset(c, None, kind="jpeg")
    return d


def audits(conn, asset_id=None):
    sql = ("SELECT action, resource_type, resource_id, actor_type, actor_user_id, institution_id, outcome, "
           "correlation_id FROM access_audit WHERE action LIKE 'evidence.%'")
    params = {}
    if asset_id is not None:
        sql += " AND resource_id = :r"
        params["r"] = asset_id
    return [dict(r) for r in conn.execute(text(sql), params).mappings()]
