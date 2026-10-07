import os
import sys
from pathlib import Path

import pytest

# Demo mode keeps view_data off PostgreSQL; auth calls are faked per test (see FakeAuth).
os.environ['FRONTEND_DEMO_MODE'] = 'true'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app as flask_app  # noqa: E402
from controllers import routes  # noqa: E402

INSTITUTION_ID = '11111111-1111-1111-1111-111111111111'


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.cookies = {}
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeAuth:
    """Stand-in for BackendAuthService /verify and /preferences (partial merge)."""

    def __init__(self, role='station_operator', locale='en', theme='dark'):
        self.role = role
        self.preferences = {'locale': locale, 'theme': theme}
        self.calls = []

    def __call__(self, method, path, token=None, **kwargs):
        self.calls.append((method, path, token, kwargs))
        if path == '/verify':
            return FakeResponse(200, {'valid': True, 'user': {
                'id': '22222222-2222-2222-2222-222222222222',
                'name': 'Test User',
                'email': 'test@example.org',
                'institution_id': INSTITUTION_ID,
                'ui_preferences': dict(self.preferences),
                'roles': [{'code': self.role, 'description': None}],
            }})
        if path == '/preferences':
            self.preferences.update(kwargs.get('json') or {})
            return FakeResponse(200, {'ui_preferences': dict(self.preferences)})
        return FakeResponse(404)


@pytest.fixture
def app():
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def login(client, monkeypatch):
    """login(role, locale) -> FakeAuth; the client then carries an access_token cookie."""
    def _login(role='station_operator', locale='en', theme='dark'):
        fake = FakeAuth(role=role, locale=locale, theme=theme)
        monkeypatch.setattr(routes, '_auth_request', fake)
        client.set_cookie('access_token', 'test-access-token')
        return fake
    return _login
