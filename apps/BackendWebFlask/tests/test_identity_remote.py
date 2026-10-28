"""Administration RF screens of users and roles served by AuthService (adapter).

AuthService itself is tested in apps/BackendAuthService (real PostgreSQL/Redis) and end to end in
scripts/e2e_identity.py; here its HTTP answers are simulated to check that the portal: sends the
user's verified token, runs no SQL on "user", user_role or role, keeps the RF data shapes and maps
AuthService errors to the Administration messages.
"""

import json
import re
import uuid

import pytest
import requests
from sqlalchemy import event

from services.admin_local import identity_remote
from tests.conftest import INSTITUTION_ID

ROLE_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
ROLE = {'id': ROLE_ID, 'code': 'station_operator', 'description': 'Station operator',
        'institution': {'id': INSTITUTION_ID, 'name': 'Test hospital'}, 'assigned_users': 1, 'system': True}
USER = {'id': USER_ID, 'name': 'Ana Operadora', 'email': 'ana@example.org', 'active': True,
        'institution': {'id': INSTITUTION_ID, 'name': 'Test hospital'},
        'roles': [{'code': 'station_operator', 'description': 'Station operator'}],
        'last_login_at': None, 'password_updated_at': '2026-10-01T10:00:00Z', 'created_at': '2026-10-01T10:00:00Z',
        'updated_at': '2026-10-01T10:00:00Z'}
IDENTITY_TABLE_WRITE = re.compile(r'^\s*(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+("user"|user_role|role)\b', re.I)


class FakeIdentity:
    def __init__(self):
        self.calls = []
        self.routes = {
            ('GET', '/roles'): (200, [ROLE]),
            ('GET', '/users'): (200, [USER, {**USER, 'id': str(uuid.uuid4()), 'name': 'Retired Zeta', 'active': False}]),
            ('GET', f'/users/{USER_ID}'): (200, USER),
        }

    def __call__(self, method, url, json=None, timeout=None, headers=None, **kwargs):
        if '/api/v1/auth' not in url:
            raise AssertionError(f'unexpected HTTP call {url}')
        path = url.split('/api/v1/auth', 1)[1]
        self.calls.append((method, path, json, headers))
        status, body = self.routes.get((method, path), (404, {'error': 'not_found', 'message': 'Resource not found',
                                                             'details': {}}))
        response = requests.Response()
        response.status_code = status
        response._content = b'' if body is None else json_dumps(body)
        response.headers['Content-Type'] = 'application/json'
        return response


def json_dumps(value):
    return json.dumps(value).encode()


@pytest.fixture
def identity(app, monkeypatch):
    fake = FakeIdentity()
    monkeypatch.setattr(identity_remote.requests, 'request', fake)
    return fake


@pytest.fixture
def no_identity_sql(app):
    """Fail on any INSERT/UPDATE/DELETE of "user", user_role or role issued by the portal."""
    from extensions import db
    with app.app_context():
        engine = db.engine
    seen = []

    def check(conn, cursor, statement, *args):
        if IDENTITY_TABLE_WRITE.match(statement):
            seen.append(statement)
    event.listen(engine, 'before_cursor_execute', check)
    yield seen
    event.remove(engine, 'before_cursor_execute', check)
    assert not seen, seen


def test_user_list_uses_auth_with_the_verified_token(client, login, identity, no_identity_sql):
    login('it_admin')
    page = client.get('/admin/users?status=active')
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'Ana Operadora' in html and 'Retired Zeta' not in html
    method, path, _, headers = identity.calls[0]
    assert (method, path) == ('GET', '/users')
    assert headers['Authorization'] == 'Bearer test-access-token' and headers['X-Correlation-ID']


def test_create_user_sends_the_rf_payload(client, login, identity, no_identity_sql):
    login('it_admin')
    identity.routes[('POST', '/users')] = (201, USER)
    response = client.post('/admin/users/new', data={
        'name': 'Ana', 'email': 'ana@example.org', 'institution': INSTITUTION_ID, 'roles[]': ['station_operator'],
        'status': 'inactive', 'password': 'Secret-123', 'confirm_password': 'Secret-123'})
    assert response.status_code == 302
    body = next(call[2] for call in identity.calls if call[0] == 'POST')
    assert body == {'name': 'Ana', 'email': 'ana@example.org', 'institution_id': INSTITUTION_ID,
                    'role_codes': ['station_operator'], 'active': False, 'password': 'Secret-123',
                    'confirm_password': 'Secret-123'}


@pytest.mark.parametrize('status, reason, expected_status, expected_text', [
    (409, 'email_exists', 409, 'A user with this email already exists.'),
    (422, 'invalid_email', 400, 'Enter a valid email address.'),
    (422, 'password_too_short', 400, 'Password must contain at least 8 characters.'),
    (422, 'institution_inactive', 400, 'Users cannot be added to an inactive institution.'),
    (422, 'invalid_roles', 400, 'Choose a valid institution and at least one role assigned to it.'),
])
def test_create_errors_keep_the_admin_messages(client, login, identity, status, reason, expected_status, expected_text):
    login('it_admin')
    identity.routes[('POST', '/users')] = (status, {'error': 'x', 'message': 'm', 'details': {'reason': reason}})
    response = client.post('/admin/users/new', data={'name': 'A', 'email': 'a@b.c', 'institution': INSTITUTION_ID,
                                                     'roles[]': ['station_operator'], 'password': 'x'})
    assert response.status_code == expected_status
    assert expected_text in response.get_data(as_text=True)


@pytest.mark.parametrize('reason, text', [
    ('last_admin', 'At least one active administrator must remain.'),
    ('cannot_deactivate_self', 'You cannot deactivate your own account.'),
])
def test_guard_errors_are_flashed_on_deactivate(client, login, identity, reason, text):
    login('it_admin')
    identity.routes[('POST', f'/users/{USER_ID}/deactivate')] = (409, {'error': 'conflict', 'message': 'm',
                                                                       'details': {'reason': reason}})
    response = client.post(f'/admin/users/{USER_ID}/deactivate')
    assert response.status_code == 302
    assert text in client.get(response.headers['Location']).get_data(as_text=True)


def test_password_reset_and_status_actions(client, login, identity, no_identity_sql):
    login('it_admin')
    identity.routes[('POST', f'/users/{USER_ID}/password')] = (200, USER)
    identity.routes[('POST', f'/users/{USER_ID}/activate')] = (200, USER)
    client.post(f'/admin/users/{USER_ID}/password', data={'new_password': 'Another-1', 'confirm_new_password': 'Another-1'})
    client.post(f'/admin/users/{USER_ID}/activate')
    posts = [(path, body) for method, path, body, _ in identity.calls if method == 'POST']
    assert posts == [(f'/users/{USER_ID}/password', {'new_password': 'Another-1', 'confirm_new_password': 'Another-1'}),
                     (f'/users/{USER_ID}/activate', None)]


def test_roles_screens(client, login, identity, no_identity_sql):
    login('it_admin')
    assert 'station_operator' in client.get('/admin/roles').get_data(as_text=True)
    identity.routes[('GET', f'/roles/{ROLE_ID}')] = (200, ROLE)
    identity.routes[('DELETE', f'/roles/{ROLE_ID}')] = (409, {'error': 'conflict', 'message': 'm',
                                                              'details': {'reason': 'system_role'}})
    response = client.post(f'/admin/roles/{ROLE_ID}/delete')
    assert 'System roles cannot be deleted.' in client.get(response.headers['Location']).get_data(as_text=True)
    identity.routes[('POST', '/roles')] = (409, {'error': 'conflict', 'message': 'm', 'details': {'reason': 'role_code_exists'}})
    response = client.post('/admin/roles/new', data={'code': 'auditor', 'description': 'Auditor'})
    assert response.status_code == 409
    assert 'A role with this code already exists for the institution.' in response.get_data(as_text=True)


def test_user_form_role_options_come_from_auth(client, login, identity):
    login('it_admin')
    page = client.get('/admin/users/new')
    assert page.status_code == 200
    assert ('GET', '/roles') in {(method, path) for method, path, _, _ in identity.calls}


def test_auth_down_shows_controlled_message(client, login, monkeypatch, identity):
    login('it_admin')

    def down(*args, **kwargs):
        raise requests.ConnectionError('refused')
    monkeypatch.setattr(identity_remote.requests, 'request', down)
    response = client.post('/admin/roles/new', data={'code': 'auditor', 'description': 'Auditor'})
    assert response.status_code == 503
    assert 'The identity service is temporarily unavailable' in response.get_data(as_text=True)


def test_only_users_and_roles_are_identity_paths():
    assert identity_remote.handles('/users') and identity_remote.handles('/roles/1')
    assert not any(identity_remote.handles(path) for path in ('/families', '/kits', '/procedures', '/vision-models',
                                                              '/institution', '/audit-events', '/dashboard'))
