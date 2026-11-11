"""Shared real-PostgreSQL fixtures for the Administration RF tests (RF-AD-01..10).

Import the fixtures into a test module with:
    from .admin_fixtures import pg, tenants, login_as, as_user, no_remote_http  # noqa: F401

- pg: one outer transaction rolled back at teardown; every admin_local module and the portal's
  db.session share a SAVEPOINT session bound to it.
- tenants: institutions A and B (+ the four product roles via migration 009), one it_admin,
  spd_supervisor, station_operator and or_charge user per institution, one operating room each.
- login_as(kind, key): the portal's AuthService /verify answers for that user (the token is fake).
- as_user(kind, key, method='GET'): request context for direct calls to the Administration domain.
- no_remote_http: any HTTP from Administration (AuthService CRUD, Catalog, Audit) fails the test.
"""
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from flask import g
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import scoped_session, sessionmaker

from controllers import routes
from extensions import db

from .conftest import FakeResponse

MIGRATION_009 = Path(__file__).resolve().parents[3] / 'data' / 'migrations' / '009_roles_fijos.sql'
KIND_ROLE = {'admin': 'it_admin', 'supervisor': 'spd_supervisor', 'operator': 'station_operator', 'floor': 'or_charge'}


def migration_body(path):
    sql = Path(path).read_text(encoding='utf-8')
    return '\n'.join(line for line in sql.splitlines() if line.strip().upper() not in ('BEGIN;', 'COMMIT;'))


@pytest.fixture
def pg(app, monkeypatch):
    monkeypatch.setitem(app.config, 'FRONTEND_DEMO_MODE', False)
    with app.app_context():
        try:
            connection = db.engine.connect()
        except OperationalError as exc:  # pragma: no cover - environment dependent
            pytest.skip(f'PostgreSQL not reachable: {type(exc).__name__}')
        outer = connection.begin()
        session = scoped_session(sessionmaker(bind=connection, join_transaction_mode='create_savepoint'))
        monkeypatch.setattr(db, 'session', session)
        for name, module in list(sys.modules.items()):
            if name.startswith('services.admin_local') and hasattr(module, 'db'):
                monkeypatch.setattr(module, 'db', session)
        try:
            yield connection
        finally:
            session.remove()
            outer.rollback()
            connection.close()


@pytest.fixture
def tenants(pg):
    c, tag = pg, uuid.uuid4().hex[:8]
    t = SimpleNamespace(tag=tag, inst={}, users={kind: {} for kind in KIND_ROLE}, room={})
    for key in ('A', 'B'):
        t.inst[key] = uuid.uuid4()
        c.execute(text('INSERT INTO institution (id, name) VALUES (:i, :n)'), {'i': t.inst[key], 'n': f'Inst {key} {tag}'})
    c.execute(text(migration_body(MIGRATION_009)))
    for key in ('A', 'B'):
        for kind, code in KIND_ROLE.items():
            user_id = uuid.uuid4()
            c.execute(text('INSERT INTO "user" (id, name, email, password_hash, institution_id) VALUES (:u, :n, :e, :p, :i)'),
                      {'u': user_id, 'n': f'{kind} {key} {tag}', 'e': f'{kind}.{key}.{tag}@test.invalid', 'p': 'x',
                       'i': t.inst[key]})
            c.execute(text('INSERT INTO user_role (user_id, role_id) SELECT :u, id FROM role WHERE institution_id = :i AND code = :c'),
                      {'u': user_id, 'i': t.inst[key], 'c': code})
            t.users[kind][key] = user_id
        t.room[key] = uuid.uuid4()
        c.execute(text("INSERT INTO operating_room (id, code, name, institution_id) VALUES (:r, :c, 'Room', :i)"),
                  {'r': t.room[key], 'c': f'OR-{key}-{tag}', 'i': t.inst[key]})
    t.admin = t.users['admin']
    t.q = lambda sql, **p: c.execute(text(sql), p)
    t.audit = lambda action, key='A': c.execute(
        text('SELECT actor_user_id, resource_id, outcome FROM access_audit WHERE action = :a AND institution_id = :i '
             'ORDER BY occurred_at'), {'a': action, 'i': t.inst[key]}).all()
    return t


def _forget_identity():
    """pg keeps one app context open, so `g` (verified user, actor) would leak between requests."""
    for name in ('verified_user', 'actor', 'auth_access_token'):
        g.pop(name, None)


def _verify_payload(tenants, kind, key):
    return {'valid': True, 'user': {
        'id': str(tenants.users[kind][key]), 'name': f'{kind} {key}', 'email': f'{kind}.{key}@test.invalid',
        'institution_id': str(tenants.inst[key]), 'ui_preferences': {'locale': 'en', 'theme': 'light'},
        'roles': [{'code': KIND_ROLE[kind], 'description': None}]}}


@pytest.fixture
def login_as(client, monkeypatch, tenants):
    def _login(kind, key='A'):
        def fake_auth(method, path, token=None, **kwargs):
            if path == '/verify':
                return FakeResponse(200, _verify_payload(tenants, kind, key))
            return FakeResponse(404)
        monkeypatch.setattr(routes, '_auth_request', fake_auth)
        client.set_cookie('access_token', 'admin-test-token')
        _forget_identity()
    return _login


@pytest.fixture
def as_user(app, monkeypatch, tenants):
    @contextmanager
    def _ctx(kind, key='A', method='GET'):
        monkeypatch.setattr(routes, '_auth_request', lambda m, path, token=None, **kw: FakeResponse(
            200, _verify_payload(tenants, kind, key)) if path == '/verify' else FakeResponse(404))
        with app.test_request_context(method=method, headers={'Cookie': 'access_token=admin-test-token'}):
            _forget_identity()
            yield
        _forget_identity()
    return _ctx


@pytest.fixture
def no_remote_http(monkeypatch):
    """Administration must not call any service over HTTP for business operations."""
    calls = []

    def fail(*args, **kwargs):
        calls.append(args[:2])
        raise AssertionError(f'unexpected HTTP call from Administration: {args[:2]}')
    monkeypatch.setattr(requests, 'request', fail)
    monkeypatch.setattr(requests, 'get', fail)
    monkeypatch.setattr(requests, 'post', fail)
    return calls
