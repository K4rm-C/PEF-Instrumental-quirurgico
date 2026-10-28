import os
import sys
from pathlib import Path

import pytest

# Demo mode keeps view_data off PostgreSQL; auth calls are faked per test (see FakeAuth).
os.environ['FRONTEND_DEMO_MODE'] = 'true'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# MongoDB checkpoints are off for the suite (sessions are rolled back, their checkpoints must not
# land in a real database); test_mongo_checkpoints.py enables a dedicated test database.
os.environ['PEF_TEST_MONGO_URL'] = os.environ.get('PEF_TEST_MONGO_URL') or os.environ.get('MONGO_URL', '')
os.environ['MONGO_URL'] = ''
import tempfile  # noqa: E402
os.environ['PEF_OUTBOX_DIR'] = tempfile.mkdtemp(prefix='pef-outbox-tests-')

# SQLite is used solely for local UI tests; production keeps its PostgreSQL settings.
if os.getenv('PEF_TEST_SQLITE') == '1':
    os.environ['DATABASE_URL'] = 'sqlite:////tmp/pef_web_ui_only.db'
    from config import Config
    Config.SQLALCHEMY_ENGINE_OPTIONS = {}

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


@pytest.fixture
def fake_admin(monkeypatch):
    """RF presentation tests replace the local domain dispatcher, not an HTTP client."""
    from services.admin_local import facade
    from services.admin_local.messages import AdminDomainError

    class FakeDomain:
        def __init__(self):
            self.calls = []
            self.responses = {}

        def call(self, method, path, *, params=None, json=None):
            params = params or {}
            self.calls.append((method, path, params, json))
            value = self.responses.get((method, path))
            if isinstance(value, AdminDomainError):
                raise value
            if value is not None:
                return value
            if path == '/catalogs':
                return {name.strip(): [] for name in (params.get('names') or '').split(',')}
            if path == '/dashboard':
                return {'stats': {}, 'operations': {}, 'active_model': None}
            if path == '/institution':
                return {'id': INSTITUTION_ID, 'code': 'TEST', 'name': 'Test hospital', 'active': True}
            if method == 'GET' and path in ('/operating-rooms', '/capture-stations'):
                return {'items': []}
            return {'items': [], 'page': 1, 'total': 0, 'total_pages': 1, 'per_page': 20, 'shown': 0}

    fake = FakeDomain()
    monkeypatch.setattr(facade, 'call', fake.call)
    return fake
