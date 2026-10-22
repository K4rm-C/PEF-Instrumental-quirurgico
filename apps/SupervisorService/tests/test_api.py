import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest

os.environ.setdefault('JWT_SECRET_KEY', 'supervisor-test-secret-key-0123456789')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import security  # noqa: E402
from config import Config  # noqa: E402
from main import app  # noqa: E402


USER_ID = uuid.uuid4()
INSTITUTION_ID = uuid.uuid4()


class MemoryRedis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def ping(self):
        return True


class ScalarResult:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class QueryResult:
    def __init__(self, row=None, values=None):
        self.row = row
        self.values = values or []

    def first(self):
        return self.row

    def scalars(self):
        return ScalarResult(self.values)


@pytest.fixture
def client(monkeypatch):
    app.config.update(TESTING=True)
    store = MemoryRedis()
    security.set_redis_client(store)
    user = SimpleNamespace(
        id=USER_ID,
        name='SPD Supervisor',
        email='supervisor@example.test',
        institution_id=INSTITUTION_ID,
        active=True,
    )
    role_codes = ['spd_supervisor']

    def execute(_query):
        if not hasattr(execute, 'calls'):
            execute.calls = 0
        execute.calls += 1
        if execute.calls % 2:
            return QueryResult(row=(user, True))
        return QueryResult(values=role_codes)

    monkeypatch.setattr(security.db, 'execute', execute)
    monkeypatch.setattr(security.audit, 'record_denied', lambda *args: None)
    yield app.test_client(), store, execute, role_codes
    security.set_redis_client(None)


def auth_header(store, user_id=USER_ID, *, role='spd_supervisor', token_type='access', revoked=False):
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    store.values[f'auth:jwt:{jti}'] = json.dumps({
        'jti': jti,
        'user_id': str(user_id),
        'institution_id': str(INSTITUTION_ID),
        'token_type': token_type,
        'revoked': revoked,
    })
    token = jwt.encode({
        'sub': str(user_id),
        'jti': jti,
        'type': token_type,
        'iat': now,
        'exp': now + timedelta(minutes=5),
    }, Config.JWT_SECRET_KEY, algorithm=Config.JWT_ALGORITHM)
    return {'Authorization': f'Bearer {token}'}


def test_health_is_public_and_reports_dependencies(client, monkeypatch):
    test_client, store, _execute, _roles = client
    import main

    monkeypatch.setattr(main.db, 'execute', lambda _query: object())
    response = test_client.get('/health')
    assert response.status_code == 200
    assert response.get_json() == {
        'ok': True,
        'service': 'supervisor',
        'checks': {'postgres': True, 'redis': True},
    }
    assert store.ping()


def test_every_api_route_is_supervisor_protected():
    protected_routes = [
        rule for rule in app.url_map.iter_rules()
        if rule.rule.startswith('/api/v1/')
    ]
    assert protected_routes
    assert all(
        hasattr(app.view_functions[rule.endpoint], '__wrapped__')
        for rule in protected_routes
    )


def test_protected_endpoint_requires_access_token(client):
    test_client, _store, _execute, _roles = client
    response = test_client.get('/api/v1/me')
    assert response.status_code == 401
    assert response.get_json()['error']['code'] == 'token_required'


def test_me_requires_valid_redis_session_and_database_supervisor_role(client):
    test_client, store, execute, role_codes = client
    headers = auth_header(store)
    response = test_client.get('/api/v1/me', headers=headers)
    assert response.status_code == 200
    assert response.get_json()['id'] == str(USER_ID)
    assert response.get_json()['roles'] == ['spd_supervisor']

    execute.calls = 0
    role_codes[:] = ['station_operator']
    denied = test_client.get('/api/v1/me', headers=auth_header(store))
    assert denied.status_code == 403
    assert denied.get_json()['error']['code'] == 'forbidden'


def test_revoked_or_missing_redis_token_is_rejected(client):
    test_client, store, execute, _roles = client
    headers = auth_header(store, revoked=True)
    response = test_client.get('/api/v1/me', headers=headers)
    assert response.status_code == 401
    assert response.get_json()['error']['code'] == 'token_revoked'
    assert not hasattr(execute, 'calls') or execute.calls == 0


def test_redis_subject_must_match_signed_token_subject(client):
    test_client, store, execute, _roles = client
    headers = auth_header(store)
    token = headers['Authorization'][7:]
    claims = jwt.decode(token, Config.JWT_SECRET_KEY, algorithms=[Config.JWT_ALGORITHM])
    store.values[f"auth:jwt:{claims['jti']}"] = json.dumps({
        'jti': claims['jti'],
        'user_id': str(uuid.uuid4()),
        'institution_id': str(INSTITUTION_ID),
        'token_type': 'access',
        'revoked': False,
    })
    response = test_client.get('/api/v1/me', headers=headers)
    assert response.status_code == 401
    assert response.get_json()['error']['code'] == 'token_revoked'
    assert not hasattr(execute, 'calls') or execute.calls == 0


def test_refresh_token_cannot_access_supervisor_api(client):
    test_client, store, execute, _roles = client
    response = test_client.get(
        '/api/v1/instruments',
        headers=auth_header(store, token_type='refresh'),
    )
    assert response.status_code == 401
    assert response.get_json()['error']['code'] == 'invalid_token'
    assert not hasattr(execute, 'calls') or execute.calls == 0
