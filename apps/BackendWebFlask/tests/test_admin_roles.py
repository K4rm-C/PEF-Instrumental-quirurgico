"""RF-AD-06 Roles in the monolith (ADMIN-01.1), against real PostgreSQL.

One outer transaction rolled back at teardown; every admin_local module shares a SAVEPOINT session
bound to it. Two temporary institutions A and B, each with the four product roles (created by the
body of data/migrations/009_roles_fijos.sql, which is also what is being tested) and an it_admin.
AuthService /verify is simulated (identity of the temporary admin); AuthService itself is never
called for roles: its HTTP client fails the test if used.
"""

import re
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import scoped_session, sessionmaker

from controllers import routes
from extensions import db
from services.admin_local import facade, identity_remote
from services.admin_local.messages import AdminDomainError
from services.admin_local.roles import PRODUCT_ROLES

from .conftest import FakeResponse

MIGRATION_009 = Path(__file__).resolve().parents[3] / 'data' / 'migrations' / '009_roles_fijos.sql'
SEED_INSTITUTION = '11111111-1111-1111-1111-111111111111'


def migration_009_body():
    """The migration without its own BEGIN/COMMIT, so it runs inside the test transaction."""
    sql = MIGRATION_009.read_text(encoding='utf-8')
    return '\n'.join(line for line in sql.splitlines() if line.strip().upper() not in ('BEGIN;', 'COMMIT;'))


@pytest.fixture
def pg(app, monkeypatch):
    with app.app_context():
        try:
            connection = db.engine.connect()
        except OperationalError as exc:  # pragma: no cover - environment dependent
            pytest.skip(f'PostgreSQL not reachable: {type(exc).__name__}')
        outer = connection.begin()
        session = scoped_session(sessionmaker(bind=connection, join_transaction_mode='create_savepoint'))
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
def no_auth_http(monkeypatch):
    """Roles must never reach AuthService over HTTP."""
    def fail(*args, **kwargs):
        raise AssertionError(f'unexpected AuthService call: {args[:2]}')
    monkeypatch.setattr(identity_remote.requests, 'request', fail)


@pytest.fixture
def tenants(pg):
    c, tag = pg, uuid.uuid4().hex[:8]
    t = SimpleNamespace(inst={}, admin={}, supervisor={})
    for key in ('A', 'B'):
        t.inst[key] = uuid.uuid4()
        c.execute(text('INSERT INTO institution (id, name) VALUES (:i, :n)'), {'i': t.inst[key], 'n': f'Roles {key} {tag}'})
    # An institution that already has one role with its own description, as an existing database would.
    c.execute(text("INSERT INTO role (code, description, institution_id) VALUES ('station_operator', 'Custom A', :i)"),
              {'i': t.inst['A']})
    c.execute(text(migration_009_body()))
    for key in ('A', 'B'):
        for kind, code in (('admin', 'it_admin'), ('supervisor', 'spd_supervisor')):
            user_id = uuid.uuid4()
            c.execute(text('INSERT INTO "user" (id, name, email, password_hash, institution_id) VALUES (:u, :n, :e, :p, :i)'),
                      {'u': user_id, 'n': f'{kind} {key}', 'e': f'roles.{kind}.{key}.{tag}@test.invalid', 'p': 'x',
                       'i': t.inst[key]})
            c.execute(text('INSERT INTO user_role (user_id, role_id) SELECT :u, id FROM role '
                           'WHERE institution_id = :i AND code = :c'), {'u': user_id, 'i': t.inst[key], 'c': code})
            getattr(t, kind)[key] = user_id
    t.role = lambda key, code: c.execute(text('SELECT id, code, description FROM role WHERE institution_id = :i AND code = :c'),
                                         {'i': t.inst[key], 'c': code}).one()
    t.count = lambda: c.execute(text('SELECT count(*) FROM role WHERE institution_id IN (:a, :b)'),
                                {'a': t.inst['A'], 'b': t.inst['B']}).scalar_one()
    t.audit = lambda action: c.execute(text('SELECT actor_user_id, institution_id, resource_id, outcome FROM access_audit '
                                            'WHERE action = :a AND actor_user_id IN (:x, :y, :z, :w) ORDER BY occurred_at'),
                                       {'a': action, 'x': t.admin['A'], 'y': t.admin['B'], 'z': t.supervisor['A'],
                                        'w': t.supervisor['B']}).all()
    return t


@pytest.fixture
def login_as(client, monkeypatch, tenants):
    def _login(kind, key):
        role = {'admin': 'it_admin', 'supervisor': 'spd_supervisor'}[kind]
        user_id = getattr(tenants, kind)[key]

        def fake_auth(method, path, token=None, **kwargs):
            if path == '/verify':
                return FakeResponse(200, {'valid': True, 'user': {
                    'id': str(user_id), 'name': f'{kind} {key}', 'email': f'{kind}@example.invalid',
                    'institution_id': str(tenants.inst[key]), 'ui_preferences': {'locale': 'en', 'theme': 'light'},
                    'roles': [{'code': role, 'description': None}]}})
            return FakeResponse(404)
        monkeypatch.setattr(routes, '_auth_request', fake_auth)
        client.set_cookie('access_token', 'roles-test-token')
    return _login


# --- Migration 009 ----------------------------------------------------------------------------


def test_migration_009_adds_the_missing_product_roles_without_touching_existing_ones(tenants, pg):
    for key in ('A', 'B'):
        codes = pg.execute(text('SELECT code FROM role WHERE institution_id = :i'), {'i': tenants.inst[key]}).scalars().all()
        assert sorted(codes) == sorted(PRODUCT_ROLES)
    assert tenants.role('A', 'station_operator').description == 'Custom A'
    assert tenants.role('B', 'or_charge').description.startswith('Floor supervisor.')
    before = tenants.count()
    pg.execute(text(migration_009_body()))
    pg.execute(text(migration_009_body()))
    assert tenants.count() == before == 8


def test_existing_seed_institution_has_the_four_product_roles(pg):
    """009 was applied to the test database (an existing one): see PLAN_TRABAJO.md, ADMIN-01.1."""
    codes = pg.execute(text('SELECT code FROM role WHERE institution_id = :i'), {'i': SEED_INSTITUTION}).scalars().all()
    assert sorted(codes) == sorted(PRODUCT_ROLES)


# --- Screens ----------------------------------------------------------------------------------


def test_list_shows_the_four_roles_of_the_own_institution_only(client, login_as, tenants, no_auth_http):
    login_as('admin', 'A')
    html = client.get('/admin/roles').get_data(as_text=True)
    for code in PRODUCT_ROLES:
        assert f'>{code}</th>' in html
    assert str(tenants.role('A', 'it_admin').id) in html
    assert str(tenants.role('B', 'it_admin').id) not in html
    assert f'Roles B' not in html
    codes = re.findall(r'<th scope="row">([a-z_]+)</th>', html)
    assert codes == list(PRODUCT_ROLES)  # fixed display order


def test_screens_offer_no_create_or_delete(client, login_as, tenants, no_auth_http):
    login_as('admin', 'A')
    listing = client.get('/admin/roles').get_data(as_text=True)
    assert 'New Role' not in listing and '/admin/roles/new' not in listing and '/delete' not in listing
    form = client.get(f"/admin/roles/{tenants.role('A', 'or_charge').id}/edit").get_data(as_text=True)
    assert 'name="code"' not in form and 'Delete Role' not in form and '/delete' not in form
    assert 'readonly' in form and 'name="description"' in form


def test_edit_description_persists_and_is_audited(client, login_as, tenants, no_auth_http):
    login_as('admin', 'A')
    role = tenants.role('A', 'or_charge')
    response = client.post(f'/admin/roles/{role.id}/edit', data={'description': '  Floor supervisor, OR 1-4  '})
    assert response.status_code == 302 and response.headers['Location'].endswith('/admin/roles')
    assert tenants.role('A', 'or_charge').description == 'Floor supervisor, OR 1-4'
    assert tenants.role('B', 'or_charge').description == role.description  # same code, other institution: untouched
    assert tenants.audit('UPDATE_ROLE') == [(tenants.admin['A'], tenants.inst['A'], role.id, 'success')]
    # Saving the same text again changes nothing and records nothing.
    client.post(f'/admin/roles/{role.id}/edit', data={'description': 'Floor supervisor, OR 1-4'})
    assert len(tenants.audit('UPDATE_ROLE')) == 1


@pytest.mark.parametrize('description, message', [
    ('   ', 'Description is required.'),
    ('x' * 256, 'One of the fields is too long.'),
])
def test_edit_validation(client, login_as, tenants, description, message):
    login_as('admin', 'A')
    role = tenants.role('A', 'it_admin')
    response = client.post(f'/admin/roles/{role.id}/edit', data={'description': description})
    assert response.status_code == 400 and message in response.get_data(as_text=True)
    assert tenants.role('A', 'it_admin').description == role.description


def test_admin_cannot_see_or_edit_roles_of_another_institution(client, login_as, tenants):
    login_as('admin', 'A')
    foreign = tenants.role('B', 'it_admin')
    assert client.get(f'/admin/roles/{foreign.id}/edit').status_code == 404
    assert client.post(f'/admin/roles/{foreign.id}/edit', data={'description': 'Hijacked'}).status_code == 404
    assert tenants.role('B', 'it_admin').description == foreign.description
    assert tenants.audit('UPDATE_ROLE') == []


def test_create_and_delete_routes_do_not_exist(client, login_as, tenants):
    login_as('admin', 'A')
    before = tenants.count()
    role_id = tenants.role('A', 'or_charge').id
    assert client.get('/admin/roles/new').status_code == 404
    assert client.post('/admin/roles/new', data={'code': 'nurse_reviewer', 'description': 'x'}).status_code == 404
    assert client.post(f'/admin/roles/{role_id}/delete').status_code == 404
    assert tenants.count() == before


def test_domain_rejects_create_delete_and_code_change(app, client, login_as, tenants, no_auth_http):
    """Backend enforcement, independent of the screens: direct calls to the Administration domain."""
    login_as('admin', 'A')
    role = tenants.role('A', 'spd_supervisor')
    before = tenants.count()
    with app.test_request_context(headers={'Cookie': 'access_token=roles-test-token'}):
        with pytest.raises(AdminDomainError) as created:
            facade.post('/roles', {'code': 'nurse_reviewer', 'description': 'Nurse'})
        with pytest.raises(AdminDomainError) as deleted:
            facade.delete(f'/roles/{role.id}')
        with pytest.raises(AdminDomainError) as renamed:
            facade.put(f'/roles/{role.id}', {'code': 'supervisor', 'description': 'Changed'})
        assert created.value.user_message == 'Role codes are fixed: roles cannot be created or deleted.'
        assert renamed.value.user_message == 'The role code cannot be changed.'
    assert (created.value.status, created.value.code) == (405, 'role_codes_fixed')
    assert (deleted.value.status, deleted.value.code) == (405, 'role_codes_fixed')
    assert (renamed.value.status, renamed.value.code) == (400, 'role_code_immutable')
    assert tenants.count() == before
    assert tenants.role('A', 'spd_supervisor') == role
    assert [row.outcome for row in tenants.audit('DENIED_CREATE_ROLE')] == ['denied']
    assert [(row.resource_id, row.outcome) for row in tenants.audit('DENIED_DELETE_ROLE')] == [(role.id, 'denied')]


def test_domain_hides_roles_of_another_institution(app, client, login_as, tenants):
    login_as('admin', 'A')
    foreign = tenants.role('B', 'station_operator')
    with app.test_request_context(headers={'Cookie': 'access_token=roles-test-token'}):
        for call in (lambda: facade.get(f'/roles/{foreign.id}'),
                     lambda: facade.put(f'/roles/{foreign.id}', {'description': 'Hijacked'}),
                     lambda: facade.delete(f'/roles/{foreign.id}')):
            with pytest.raises(AdminDomainError) as exc:
                call()
            assert exc.value.status == 404
        listed = facade.get('/roles')
    assert {item['institution']['id'] for item in listed['items']} == {str(tenants.inst['A'])}
    assert tenants.role('B', 'station_operator') == foreign


def test_supervisor_is_forbidden_on_screens_and_domain(app, client, login_as, tenants):
    login_as('supervisor', 'A')
    role = tenants.role('A', 'or_charge')
    assert client.get('/admin/roles').status_code == 403
    assert client.post(f'/admin/roles/{role.id}/edit', data={'description': 'x'}).status_code == 403
    with app.test_request_context(method='PUT', headers={'Cookie': 'access_token=roles-test-token'}):
        with pytest.raises(AdminDomainError) as exc:
            facade.put(f'/roles/{role.id}', {'description': 'x'})
    assert exc.value.status == 403
    assert tenants.role('A', 'or_charge') == role
    assert [(row.actor_user_id, row.outcome) for row in tenants.audit('DENIED_PUT_ROLE')] == [
        (tenants.supervisor['A'], 'denied')]


def test_user_form_role_catalog_is_local_and_scoped(app, client, login_as, tenants, no_auth_http):
    login_as('admin', 'A')
    with app.test_request_context(headers={'Cookie': 'access_token=roles-test-token'}):
        roles = facade.get('/catalogs', names='roles')['roles']
    assert sorted(item['code'] for item in roles) == sorted(PRODUCT_ROLES)
    assert {item['institution_id'] for item in roles} == {str(tenants.inst['A'])}
