import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest

os.environ.setdefault('JWT_SECRET_KEY', 'pytest-only-secret-key-0123456789abcdef')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

import security  # noqa: E402
from config import Config  # noqa: E402
from database import engine  # noqa: E402
from main import app  # noqa: E402


ADMIN_ID = '22222222-2222-2222-2222-222222222223'
SUPERVISOR_ID = '22222222-2222-2222-2222-222222222222'
OPERATOR_ID = '22222222-2222-2222-2222-222222222221'
INSTITUTION_ID = '11111111-1111-1111-1111-111111111111'
SUFFIX = uuid.uuid4().hex[:6]

try:
    with engine.connect() as connection:
        connection.execute(text('SELECT 1 FROM access_audit LIMIT 1'))
except Exception as exc:  # noqa: BLE001
    pytest.skip(f'PostgreSQL demo database not reachable: {exc}', allow_module_level=True)


class MemoryStore:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def ping(self):
        return True


STORE = MemoryStore()
security.set_redis_client(STORE)


def token(user_id, *, revoked=False, token_type='access'):
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    STORE.data[f'auth:jwt:{jti}'] = json.dumps({
        'jti': jti, 'user_id': user_id, 'institution_id': INSTITUTION_ID,
        'token_type': token_type, 'revoked': revoked,
    })
    raw = jwt.encode({'sub': user_id, 'jti': jti, 'type': token_type, 'iat': now,
                      'exp': now + timedelta(minutes=5)}, Config.JWT_SECRET_KEY, algorithm=Config.JWT_ALGORITHM)
    return {'Authorization': f'Bearer {raw}'}


@pytest.fixture(scope='module')
def client():
    return app.test_client()


@pytest.fixture(scope='module')
def admin():
    return token(ADMIN_ID)


@pytest.fixture(scope='module')
def category(client, admin):
    return client.get('/api/v1/catalogs?names=categories', headers=admin).get_json()['categories'][0]['id']


@pytest.fixture(scope='module')
def family(client, admin, category):
    response = client.post('/api/v1/families', headers=admin, json={
        'code': f'TST_{SUFFIX}', 'name': f'Test family {SUFFIX}', 'category_id': category})
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def audit_actions(resource_id):
    with engine.connect() as connection:
        rows = connection.execute(text(
            'SELECT action, actor_user_id::text, outcome FROM access_audit WHERE resource_id = :id'
        ), {'id': resource_id}).all()
    return {(action, actor, outcome) for action, actor, outcome in rows}


# --- Authentication and authorization -------------------------------------------------------

def test_requires_a_valid_access_token(client):
    assert client.get('/api/v1/families').status_code == 401
    assert client.get('/api/v1/families', headers={'Authorization': 'Bearer nope'}).status_code == 401
    assert client.get('/api/v1/families', headers=token(ADMIN_ID, revoked=True)).status_code == 401
    assert client.get('/api/v1/families', headers=token(ADMIN_ID, token_type='refresh')).status_code == 401


def test_roles_come_from_the_database(client):
    assert client.get('/api/v1/families', headers=token(OPERATOR_ID)).status_code == 403
    assert client.get('/api/v1/users', headers=token(SUPERVISOR_ID)).status_code == 403
    assert client.get('/api/v1/instruments', headers=token(SUPERVISOR_ID)).status_code == 200
    kits = client.get('/api/v1/kits?status=all', headers=token(SUPERVISOR_ID)).get_json()
    assert kits['active_only'] is True and all(kit['active'] for kit in kits['items'])


def test_denied_attempts_are_audited(client):
    client.post('/api/v1/families', headers=token(OPERATOR_ID), json={})
    with engine.connect() as connection:
        denied = connection.execute(text(
            "SELECT count(*) FROM access_audit WHERE actor_user_id = :id AND outcome = 'denied' "
            "AND action LIKE 'DENIED_%'"), {'id': OPERATOR_ID}).scalar()
    assert denied >= 1


# --- Catalog CRUD -----------------------------------------------------------------------------

def test_family_create_update_and_audit(client, admin, family, category):
    duplicate = client.post('/api/v1/families', headers=admin, json={
        'code': family['code'], 'name': 'x', 'category_id': category})
    assert duplicate.status_code == 409 and duplicate.get_json()['error']['code'] == 'family_code_exists'
    updated = client.put(f"/api/v1/families/{family['id']}", headers=admin, json={
        'name': 'Renamed', 'category_id': category, 'status': 'inactive'})
    assert updated.status_code == 200 and updated.get_json()['active'] is False
    actions = {action for action, actor, _ in audit_actions(family['id']) if actor == ADMIN_ID}
    assert {'CREATE_INSTRUMENT_FAMILY', 'UPDATE_INSTRUMENT_FAMILY', 'DEACTIVATE_INSTRUMENT_FAMILY'} <= actions
    client.put(f"/api/v1/families/{family['id']}", headers=admin, json={
        'name': 'Renamed', 'category_id': category, 'status': 'active'})


def test_instrument_status_change_needs_a_reason(client, admin, family):
    created = client.post('/api/v1/instruments', headers=admin, json={
        'internal_code': f'TST-{SUFFIX}', 'family_id': family['id'], 'cycle_status': 'available'})
    assert created.status_code == 201
    instrument = created.get_json()
    no_reason = client.put(f"/api/v1/instruments/{instrument['id']}", headers=admin, json={
        'family_id': family['id'], 'cycle_status': 'maintenance', 'status': 'active'})
    assert no_reason.status_code == 400
    ok = client.put(f"/api/v1/instruments/{instrument['id']}", headers=admin, json={
        'family_id': family['id'], 'cycle_status': 'maintenance', 'status': 'active', 'change_reason': 'repair'})
    assert ok.status_code == 200
    assert ok.get_json()['cycle_events'][0]['cycle_status']['code'] == 'maintenance'


def test_kit_changes_create_versions_and_keep_history(client, admin, family):
    created = client.post('/api/v1/kits', headers=admin, json={
        'name': f'Test kit {SUFFIX}', 'items': [{'family_id': family['id'], 'quantity': 6}]})
    assert created.status_code == 201
    v1 = created.get_json()
    v2 = client.put(f"/api/v1/kits/{v1['id']}", headers=admin, json={
        'name': v1['name'], 'status': 'active', 'items': [{'family_id': family['id'], 'quantity': 8}]}).get_json()
    assert v2['id'] != v1['id'] and v2['version'] == v1['version'] + 1
    old = client.get(f"/api/v1/kits/{v1['id']}", headers=admin).get_json()
    assert old['active'] is False and old['items'][0]['quantity'] == 6
    # A status-only change is also a new version; saving without changes is not.
    v3 = client.put(f"/api/v1/kits/{v2['id']}", headers=admin, json={
        'name': v2['name'], 'status': 'inactive', 'items': [{'family_id': family['id'], 'quantity': 8}]}).get_json()
    assert v3['id'] != v2['id'] and v3['version'] == v2['version'] + 1 and v3['active'] is False
    assert client.get(f"/api/v1/kits/{v2['id']}", headers=admin).get_json()['active'] is False
    same = client.put(f"/api/v1/kits/{v3['id']}", headers=admin, json={
        'name': v3['name'], 'status': 'inactive', 'items': [{'family_id': family['id'], 'quantity': 8}]}).get_json()
    assert same['id'] == v3['id']
    empty = client.post('/api/v1/kits', headers=admin, json={'name': f'Empty {SUFFIX}', 'items': []})
    assert empty.status_code == 400


# --- Users and roles ------------------------------------------------------------------------

def test_user_lifecycle_and_admin_guards(client, admin):
    created = client.post('/api/v1/users', headers=admin, json={
        'name': f'Test User {SUFFIX}', 'email': f'test.{SUFFIX}@instrumed.com', 'institution_id': INSTITUTION_ID,
        'role_codes': ['station_operator'], 'password': 'Password123', 'confirm_password': 'Password123'})
    assert created.status_code == 201
    user = created.get_json()
    with engine.connect() as connection:
        stored = connection.execute(text('SELECT password_hash FROM "user" WHERE id = :id'), {'id': user['id']}).scalar()
    assert stored and 'Password123' not in stored
    assert client.post(f"/api/v1/users/{user['id']}/password", headers=admin,
                       json={'new_password': 'short', 'confirm_new_password': 'short'}).status_code == 400
    assert client.post(f"/api/v1/users/{user['id']}/deactivate", headers=admin).get_json()['active'] is False
    assert client.post(f'/api/v1/users/{ADMIN_ID}/deactivate', headers=admin).status_code == 400
    self_demote = client.put(f'/api/v1/users/{ADMIN_ID}', headers=admin, json={
        'name': 'Admin Demo', 'email': 'admin@instrumed.com', 'institution_id': INSTITUTION_ID,
        'role_codes': ['spd_supervisor'], 'status': 'active'})
    assert self_demote.status_code == 400
    actions = {action for action, _, _ in audit_actions(user['id'])}
    assert {'CREATE_USER', 'DEACTIVATE_USER'} <= actions


def test_roles_can_only_be_deleted_when_unused(client, admin):
    system_role = next(role for role in client.get('/api/v1/roles', headers=admin).get_json()['items']
                       if role['code'] == 'it_admin')
    assert client.delete(f"/api/v1/roles/{system_role['id']}", headers=admin).status_code == 400
    created = client.post('/api/v1/roles', headers=admin, json={'code': f'tst_{SUFFIX}', 'description': 'Test'})
    assert created.status_code == 201
    assert client.post('/api/v1/roles', headers=admin, json={'code': 'Bad Code', 'description': 'x'}).status_code == 400
    assert client.delete(f"/api/v1/roles/{created.get_json()['id']}", headers=admin).status_code == 200


# --- Vision models, configuration, dashboard and audit log ----------------------------------

def test_only_one_vision_model_is_active(client, admin, family):
    before = next(m for m in client.get('/api/v1/vision-models', headers=admin).get_json()['items'] if m['active'])
    created = client.post('/api/v1/vision-models', headers=admin, json={
        'version_tag': f'tst-{SUFFIX}', 'checksum': 'a' * 64,
        'classes': [{'yolo_class_id': 0, 'family_id': family['id']}]}).get_json()
    client.post(f"/api/v1/vision-models/{created['id']}/activate", headers=admin)
    active = [m for m in client.get('/api/v1/vision-models', headers=admin).get_json()['items'] if m['active']]
    assert [m['id'] for m in active] == [created['id']]
    locked = client.put(f"/api/v1/vision-models/{created['id']}", headers=admin, json={
        'classes': [{'yolo_class_id': 1, 'family_id': family['id']}]})
    assert locked.status_code == 400
    client.post(f"/api/v1/vision-models/{before['id']}/activate", headers=admin)


def test_configuration_rules(client, admin):
    assert client.put('/api/v1/institution', headers=admin,
                      json={'name': 'Hospital Demo PEF', 'status': 'inactive'}).status_code == 400
    room = client.post('/api/v1/operating-rooms', headers=admin, json={'code': f'T-{SUFFIX}', 'name': 'Test'})
    assert room.status_code == 201
    station = client.post('/api/v1/capture-stations', headers=admin, json={
        'name': f'Test station {SUFFIX}', 'room_id': room.get_json()['id']})
    assert station.status_code == 201 and station.get_json()['roi_configured'] is False


def test_dashboard_and_audit_log(client, admin):
    dashboard = client.get('/api/v1/dashboard', headers=admin).get_json()
    assert dashboard['stats']['active_users'] >= 1
    assert set(dashboard['operations']) >= {'total_sessions', 'ai_human_agreement', 'avg_resolution_minutes'}
    events = client.get(f'/api/v1/audit-events?user={ADMIN_ID}&per_page=10', headers=admin).get_json()
    assert events['items'] and all(item['user']['id'] == ADMIN_ID for item in events['items'])
    assert client.get('/api/v1/audit-events?date=20-10-2026', headers=admin).status_code == 400


def test_search_accepts_localized_matches(client, admin):
    # The portal sends codes whose Spanish label matched; stored names stay in English.
    plain = client.get('/api/v1/procedures?search=colecistectom', headers=admin).get_json()
    assert plain['total'] == 0
    localized = client.get('/api/v1/procedures?search=colecistectom&match=lap_chole&match=open_chole',
                           headers=admin).get_json()
    assert {item['code'] for item in localized['items']} == {'lap_chole', 'open_chole'}
