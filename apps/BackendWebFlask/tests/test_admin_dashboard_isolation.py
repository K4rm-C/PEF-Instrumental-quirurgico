"""Administration dashboard: operational indicators are scoped to the administrator's institution.

Real PostgreSQL, one outer transaction rolled back at teardown. Sessions, discrepancies, human
corrections, AI-human pairs and resolution times of institution B must not change what the
administrator of institution A sees; the same facts in A change it by exactly their amount. Users,
roles, institutions, families, procedures and vision models remain global by design (the
administrator manages them for every institution).
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import scoped_session, sessionmaker

from extensions import db
from services.admin_local import dashboard

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def pg(app, monkeypatch):
    with app.app_context():
        try:
            connection = db.engine.connect()
        except OperationalError as exc:  # pragma: no cover - environment dependent
            pytest.skip(f'PostgreSQL not reachable: {type(exc).__name__}')
        outer = connection.begin()
        session = scoped_session(sessionmaker(bind=connection, join_transaction_mode='create_savepoint'))
        monkeypatch.setattr(dashboard, 'db', session)
        try:
            yield connection
        finally:
            session.remove()
            outer.rollback()
            connection.close()


def _code(c, table, code):
    return c.execute(text(f'SELECT id FROM {table} WHERE code = :c'), {'c': code}).scalar_one()


@pytest.fixture
def tenants(pg):
    c, tag = pg, uuid.uuid4().hex[:8]
    t = SimpleNamespace(inst={}, users={})
    for key in ('A', 'B'):
        t.inst[key] = uuid.uuid4()
        c.execute(text('INSERT INTO institution (id, name) VALUES (:i, :n)'), {'i': t.inst[key], 'n': f'Dash {key} {tag}'})
        t.users[key] = uuid.uuid4()
        c.execute(text('INSERT INTO "user" (id, name, email, password_hash, institution_id) VALUES (:u, :n, :e, :p, :i)'),
                  {'u': t.users[key], 'n': f'Op {key}', 'e': f'dash.{key}.{tag}@test.invalid', 'p': 'x', 'i': t.inst[key]})
    family = c.execute(text('SELECT id FROM instrument_family LIMIT 1')).scalar_one()
    op_status = _code(c, 'cat_operation_status', 'scheduled')
    closed = _code(c, 'cat_session_status', 'closed')
    manual = _code(c, 'cat_event_type', 'manual_count')
    shortage = _code(c, 'cat_discrepancy_reason', 'shortage')

    def facts(key):
        """One session of `key` with: 1 discrepancy (resolved in 30 min), 1 AI pair (disagreeing), 1 human correction."""
        operation, session, event = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        c.execute(text('INSERT INTO operation (id, scheduled_at, status_id, institution_id) VALUES (:o, :s, :st, :i)'),
                  {'o': operation, 's': NOW, 'st': op_status, 'i': t.inst[key]})
        c.execute(text('INSERT INTO work_session (id, started_at, ended_at, status_id, user_id, operation_id, capture_mode) '
                       "VALUES (:s, :a, :b, :st, :u, :o, 'vision')"),
                  {'s': session, 'a': NOW, 'b': NOW + timedelta(minutes=20), 'st': closed, 'u': t.users[key], 'o': operation})
        c.execute(text('INSERT INTO count_event (id, event_type_id, family_id, detected_quantity, payload, occurred_at, session_id) '
                       'VALUES (:id, :t, :f, 1, CAST(:p AS jsonb), :at, :s)'),
                  {'id': event, 't': manual, 'f': family, 'p': json.dumps({'ai': True, 'ai_suggested_quantity': 2}),
                   'at': NOW + timedelta(minutes=10), 's': session})
        c.execute(text('INSERT INTO discrepancy (description, resolved, resolved_at, reason_id, family_id, session_id, origin_event_id) '
                       "VALUES ('dash test', TRUE, :r, :reason, :f, :s, :e)"),
                  {'r': NOW + timedelta(minutes=40), 'reason': shortage, 'f': family, 's': session, 'e': event})
        c.execute(text("INSERT INTO human_correction (justification, count_event_id) VALUES ('dash test', :e)"), {'e': event})
    t.facts = facts
    return t


def operations(tenants, key):
    return dashboard.overview(SimpleNamespace(institution_id=tenants.inst[key]))['operations']


def test_another_institution_does_not_change_the_admin_indicators(tenants):
    before = operations(tenants, 'A')
    tenants.facts('B')
    tenants.facts('B')
    assert operations(tenants, 'A') == before
    b = operations(tenants, 'B')
    assert (b['total_sessions'], b['total_discrepancies'], b['open_discrepancies'], b['human_corrections']) == (2, 2, 0, 2)
    assert b['avg_resolution_minutes'] == 30.0


def test_own_institution_changes_them_by_exactly_its_facts(tenants):
    tenants.facts('A')
    a = operations(tenants, 'A')
    assert (a['total_sessions'], a['total_discrepancies'], a['open_discrepancies'], a['human_corrections']) == (1, 1, 0, 1)
    assert a['avg_resolution_minutes'] == 30.0
    assert a['ai_human_agreement'] == 0.0            # 1 pair: human 1 vs YOLO 2 (not vs the expected inventory)


def test_global_administration_indicators_stay_global(tenants):
    a = dashboard.overview(SimpleNamespace(institution_id=tenants.inst['A']))['stats']
    b = dashboard.overview(SimpleNamespace(institution_id=tenants.inst['B']))['stats']
    for key in ('active_users', 'inactive_users', 'roles', 'active_institutions', 'active_families', 'procedures', 'vision_models'):
        assert a[key] == b[key], key


def test_institution_scoped_catalog_indicators(tenants, pg):
    pg.execute(text("INSERT INTO kit (name, institution_id) VALUES ('Dash kit B', :i)"), {'i': tenants.inst['B']})
    a = dashboard.overview(SimpleNamespace(institution_id=tenants.inst['A']))['stats']
    b = dashboard.overview(SimpleNamespace(institution_id=tenants.inst['B']))['stats']
    assert (a['active_kits'], b['active_kits']) == (0, 1)
