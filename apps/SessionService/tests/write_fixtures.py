"""Fixtures for Phase C (writes): references, stock, operations and committed data for concurrency.

`wdata` builds on `world` inside the rolled-back transaction. `committed_env` writes real,
committed rows (needed so independent connections can contend on SELECT ... FOR UPDATE) and
deletes every one of them afterwards.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from conftest import TokenFactory, make_settings
from main import create_app


def _run(conn, sql, **params):
    conn.execute(text(sql), params)


def _id(conn, table, code):
    return conn.execute(text(f"SELECT id FROM {table} WHERE code = :code"), {"code": code}).scalar_one()


@pytest.fixture
def wdata(world, db_conn):
    c, w = db_conn, world
    d = SimpleNamespace()
    op_status = {code: _id(c, "cat_operation_status", code) for code in ("scheduled", "closed")}
    cycle = {code: _id(c, "cat_instrument_cycle_status", code) for code in ("available", "maintenance")}

    # Second room of A with its own station; an inactive station in room A.
    d.room_a2, d.station_a2, d.station_inactive = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _run(c, "INSERT INTO operating_room (id, code, name, institution_id) VALUES (:id, :c, 'Room 2', :i)",
         id=d.room_a2, c=f"RA2-{w.tag}", i=w.institutions["A"])
    _run(c, "INSERT INTO capture_station (id, name, room_id) VALUES (:id, 'Station 2', :r)", id=d.station_a2, r=d.room_a2)
    _run(c, "INSERT INTO capture_station (id, name, room_id, active) VALUES (:id, 'Off', :r, FALSE)",
         id=d.station_inactive, r=w.rooms["A"])

    def operation(inst_key, status_code="scheduled", room=True):
        op_id = uuid.uuid4()
        _run(c, "INSERT INTO operation (id, scheduled_at, status_id, room_id, institution_id) VALUES (:id, :s, :st, :r, :i)",
             id=op_id, s=datetime(2026, 11, 1, 9, tzinfo=timezone.utc), st=op_status[status_code],
             r=w.rooms[inst_key] if room else None, i=w.institutions[inst_key])
        return op_id

    d.op_a = operation("A")
    d.op_a_noroom = operation("A", room=False)
    d.op_a_closed = operation("A", "closed")
    d.op_b = operation("B")

    # Kit A recipe: family a ×2, b ×1. An inactive kit of A.
    for suffix, qty in (("a", 2), ("b", 1)):
        _run(c, "INSERT INTO kit_item (kit_id, family_id, quantity) VALUES (:k, :f, :q)",
             k=w.kits["A"], f=w.families[suffix], q=qty)
    d.kit_inactive = uuid.uuid4()
    _run(c, "INSERT INTO kit (id, name, version, active, institution_id) VALUES (:id, :n, 1, FALSE, :i)",
         id=d.kit_inactive, n=f"Old kit {w.tag}", i=w.institutions["A"])

    # Inactive family and inactive phase.
    d.family_inactive = uuid.uuid4()
    category = c.execute(text("SELECT category_id FROM instrument_family WHERE id = :f"), {"f": w.families["a"]}).scalar_one()
    _run(c, "INSERT INTO instrument_family (id, code, name, category_id, active) VALUES (:id, :c, 'Retired', :cat, FALSE)",
         id=d.family_inactive, c=f"zz_ses_{w.tag}_off", cat=category)
    d.phase_inactive = f"zz_ph_{w.tag}"[:32]
    _run(c, "INSERT INTO cat_operation_phase (code, name, active) VALUES (:c, 'Old phase', FALSE)", c=d.phase_inactive)

    # Stock: A has a×3, b×2, c×1 available (+ a c in maintenance, a d available but inactive);
    # B has 5 available of a and d (must never count for A).
    def pieces(inst_key, suffix, count, status="available", active=True):
        for _ in range(count):
            _run(c, "INSERT INTO instrument (family_id, cycle_status_id, institution_id, active) VALUES (:f, :s, :i, :a)",
                 f=w.families[suffix], s=cycle[status], i=w.institutions[inst_key], a=active)

    pieces("A", "a", 3)
    pieces("A", "b", 2)
    pieces("A", "c", 1)
    pieces("A", "c", 1, status="maintenance")
    pieces("A", "d", 1, active=False)
    pieces("B", "a", 5)
    pieces("B", "d", 5)
    return d


def create_body(world, wdata, **overrides):
    body = {
        "operation_id": str(wdata.op_a),
        "user_id": str(world.users["op_a"].id),
        "station_id": str(world.stations["A"]),
        "kit_id": str(world.kits["A"]),
        "phase_code": "start",
        "expected_lines": [
            {"family_id": str(world.families["a"]), "expected_quantity": 2},
            {"family_id": str(world.families["c"]), "expected_quantity": 1},
        ],
    }
    body.update(overrides)
    return body


# --- Committed data for real concurrency tests -----------------------------------------------


@pytest.fixture
def committed_env(pg_engine, redis_client):
    """Real committed tenant + scheduled session; everything is deleted at teardown."""
    tag = uuid.uuid4().hex[:8]
    env = SimpleNamespace(tag=tag, families=[], session_id=uuid.uuid4())
    inst, room, station, kit = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    op_role, spd_role, operator, supervisor = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    category, operation = uuid.uuid4(), uuid.uuid4()
    env.institution_id, env.operator, env.supervisor = inst, operator, supervisor
    with pg_engine.begin() as c:
        _run(c, "INSERT INTO institution (id, name) VALUES (:id, :n)", id=inst, n=f"Concurrency {tag}")
        for role_id, code in ((op_role, "station_operator"), (spd_role, "spd_supervisor")):
            _run(c, "INSERT INTO role (id, code, description, institution_id) VALUES (:id, :c, 'test', :i)",
                 id=role_id, c=code, i=inst)
        for user_id, role_id, key in ((operator, op_role, "op"), (supervisor, spd_role, "spd")):
            _run(c, 'INSERT INTO "user" (id, name, email, password_hash, institution_id) VALUES (:id, :n, :e, \'x\', :i)',
                 id=user_id, n=f"Conc {key}", e=f"conc.{key}.{tag}@session-test.invalid", i=inst)
            _run(c, "INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)", u=user_id, r=role_id)
        _run(c, "INSERT INTO operating_room (id, code, name, institution_id) VALUES (:id, :c, 'Room', :i)",
             id=room, c=f"CC-{tag}", i=inst)
        _run(c, "INSERT INTO capture_station (id, name, room_id) VALUES (:id, 'Station', :r)", id=station, r=room)
        _run(c, "INSERT INTO kit (id, name, version, institution_id) VALUES (:id, :n, 1, :i)", id=kit, n=f"Kit {tag}", i=inst)
        _run(c, "INSERT INTO cat_instrument_category (id, code, name) VALUES (:id, :c, 'x')", id=category, c=f"zz_cc_{tag}")
        available = _id(c, "cat_instrument_cycle_status", "available")
        for n in range(4):
            family_id = uuid.uuid4()
            _run(c, "INSERT INTO instrument_family (id, code, name, category_id) VALUES (:id, :c, 'x', :cat)",
                 id=family_id, c=f"zz_cc_{tag}_{n}", cat=category)
            for _ in range(3):
                _run(c, "INSERT INTO instrument (family_id, cycle_status_id, institution_id) VALUES (:f, :s, :i)",
                     f=family_id, s=available, i=inst)
            env.families.append(family_id)
        _run(c, "INSERT INTO kit_item (kit_id, family_id, quantity) VALUES (:k, :f, 1)", k=kit, f=env.families[0])
        _run(c, "INSERT INTO operation (id, status_id, room_id, institution_id) VALUES (:id, :s, :r, :i)",
             id=operation, s=_id(c, "cat_operation_status", "scheduled"), r=room, i=inst)
        _run(c, "INSERT INTO work_session (id, status_id, user_id, operation_id, station_id, kit_id, current_phase_id) "
                "VALUES (:id, :s, :u, :o, :st, :k, :ph)",
             id=env.session_id, s=_id(c, "cat_session_status", "scheduled"), u=operator, o=operation, st=station,
             k=kit, ph=_id(c, "cat_operation_phase", "start"))
        _run(c, "INSERT INTO expected_inventory (session_id, family_id, expected_quantity) VALUES (:s, :f, 1)",
             s=env.session_id, f=env.families[0])

    env.app = create_app(make_settings(), redis_client=redis_client)   # real engine: one connection per request
    tokens = TokenFactory(redis_client)
    env.op_token = tokens.issue(operator, inst)
    env.spd_token = tokens.issue(supervisor, inst)
    try:
        yield env
    finally:
        tokens.cleanup()
        env.app.extensions["session_service"]["database"].engine.dispose()
        with pg_engine.begin() as c:
            _run(c, "DELETE FROM access_audit WHERE resource_id = :s", s=env.session_id)
            _run(c, "DELETE FROM work_session WHERE id = :s", s=env.session_id)   # cascades lines/events
            _run(c, "DELETE FROM operation WHERE id = :o", o=operation)
            _run(c, "DELETE FROM kit_item WHERE kit_id = :k", k=kit)
            _run(c, "DELETE FROM kit WHERE id = :k", k=kit)
            _run(c, "DELETE FROM instrument WHERE institution_id = :i", i=inst)
            _run(c, "DELETE FROM instrument_family WHERE category_id = :c", c=category)
            _run(c, "DELETE FROM cat_instrument_category WHERE id = :c", c=category)
            _run(c, "DELETE FROM capture_station WHERE id = :s", s=station)
            _run(c, "DELETE FROM operating_room WHERE id = :r", r=room)
            _run(c, 'DELETE FROM user_role WHERE user_id IN (:a, :b)', a=operator, b=supervisor)
            _run(c, 'DELETE FROM "user" WHERE id IN (:a, :b)', a=operator, b=supervisor)
            _run(c, "DELETE FROM role WHERE institution_id = :i", i=inst)
            _run(c, "DELETE FROM institution WHERE id = :i", i=inst)
