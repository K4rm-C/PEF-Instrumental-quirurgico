"""ADMIN-01 integration audit: institution scope of every Administration resource, the shared-catalog
steward control, and what the vision bridge sends to VisionWorker after an Administration activation."""
import io
import uuid

import pytest
from werkzeug.datastructures import FileStorage

from services import vision_bridge
from services.admin_local import facade
from services.admin_local.messages import AdminDomainError

from .admin_fixtures import as_user, login_as, no_remote_http, pg, tenants  # noqa: F401
from .test_admin_vision_models import make_checkpoint


@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_STORAGE_BACKEND', 'local')
    monkeypatch.setenv('ADMIN_STORAGE_DIR', str(tmp_path))


@pytest.fixture
def b_resources(tenants):
    """Resources owned by institution B that an administrator of A must never reach."""
    station = uuid.uuid4()
    tenants.q("INSERT INTO capture_station (id, name, room_id) VALUES (:s, 'B station', :r)", s=station, r=tenants.room['B'])
    role = tenants.q("SELECT id FROM role WHERE institution_id = :i AND code = 'it_admin'", i=tenants.inst['B']).scalar_one()
    return {'user': tenants.users['operator']['B'], 'role': role, 'room': tenants.room['B'], 'station': station}


@pytest.mark.parametrize('method, path, payload', [
    ('GET', '/users/{user}', None),
    ('PUT', '/users/{user}', {'name': 'x', 'email': 'x@test.invalid', 'role_codes': ['station_operator']}),
    ('POST', '/users/{user}/deactivate', {}),
    ('POST', '/users/{user}/password', {'new_password': 'Long-Enough-123'}),
    ('GET', '/roles/{role}', None),
    ('PUT', '/roles/{role}', {'description': 'hijack'}),
    ('GET', '/operating-rooms/{room}', None),
    ('PUT', '/operating-rooms/{room}', {'code': 'X', 'name': 'hijack'}),
    ('GET', '/capture-stations/{station}', None),
    ('PUT', '/capture-stations/{station}', {'name': 'hijack', 'room_id': '{room}'}),
])
def test_direct_domain_calls_never_reach_another_institution(as_user, tenants, b_resources, method, path, payload):
    path = path.format(**b_resources)
    if payload and payload.get('room_id'):
        payload = {**payload, 'room_id': str(b_resources['room'])}
    with as_user('admin', 'A', method), pytest.raises(AdminDomainError) as exc:
        facade.call(method, path, json=payload)
    assert exc.value.status in (400, 404), (path, exc.value.code)


def test_listings_and_settings_are_scoped(as_user, tenants, b_resources):
    tenants.q("INSERT INTO app_setting (institution_id, key, value) VALUES (:i, 'password_min_length', '20')",
              i=tenants.inst['B'])
    with as_user('admin', 'A'):
        users = {item['id'] for item in facade.get('/users', status='all', per_page=50)['items']}
        roles = {item['institution']['id'] for item in facade.get('/roles')['items']}
        rooms = {item['id'] for item in facade.get('/operating-rooms')['items']}
        stations = {item['id'] for item in facade.get('/capture-stations')['items']}
        settings = {item['key']: item['value'] for item in facade.get('/settings')['items']}
        institutions = facade.get('/catalogs', names='institutions')['institutions']
    assert str(b_resources['user']) not in users and roles == {str(tenants.inst['A'])}
    assert str(b_resources['room']) not in rooms and str(b_resources['station']) not in stations
    assert settings['password_min_length'] == 8
    assert [item['id'] for item in institutions] == [str(tenants.inst['A'])]


# --- Shared catalogs: optional steward institution --------------------------------------------------

@pytest.fixture
def steward_b(monkeypatch, tenants):
    monkeypatch.setenv('ADMIN_CATALOG_STEWARD_INSTITUTION_ID', str(tenants.inst['B']))


def _category(tenants):
    return str(tenants.q('SELECT id FROM cat_instrument_category LIMIT 1').scalar_one())


def test_non_steward_administrators_cannot_change_shared_catalogs(as_user, tenants, steward_b, local_storage):
    family = str(tenants.q('SELECT id FROM instrument_family LIMIT 1').scalar_one())
    model = str(tenants.q('SELECT id FROM yolo_model LIMIT 1').scalar_one())
    notice = tenants.q('SELECT id FROM privacy_notice_version WHERE active ORDER BY created_at LIMIT 1').scalar()
    if notice is None:  # shared test DB without an active notice: make one for this test
        notice = tenants.q("INSERT INTO privacy_notice_version (version, effective_at, document_uri) "
                           "VALUES (:v, now(), 'test://notice') RETURNING id", v=f'st-active-{tenants.tag}').scalar_one()
    notice = str(notice)
    writes = [
        ('POST', '/families', {'code': f'ZZ_ST_{tenants.tag}', 'name': 'x', 'category_id': _category(tenants)}),
        ('PUT', f'/families/{family}', {'name': 'hijack', 'category_id': _category(tenants)}),
        ('POST', f'/families/{family}/examples', {'file': FileStorage(io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'0' * 50), 'p.png')}),
        ('POST', '/procedures', {'code': f'zz_st_{tenants.tag}', 'name': 'x'}),
        ('POST', '/selection-catalogs/reasons', {'code': f'zz_st_{tenants.tag}', 'name': 'x'}),
        ('POST', '/privacy-notices', {'version': f'st-{tenants.tag}', 'effective_at': '2026-11-03',
                                      'file': FileStorage(io.BytesIO(b'%PDF-1.4 x'), 'a.pdf')}),
        ('POST', f'/privacy-notices/{notice}/deactivate', {}),
        ('POST', '/vision-models', {'version_tag': f'st{tenants.tag}', 'file': FileStorage(io.BytesIO(b'PK'), 'b.pt')}),
        ('POST', f'/vision-models/{model}/activate', {}),
    ]
    for method, path, payload in writes:
        with as_user('admin', 'A', method), pytest.raises(AdminDomainError) as exc:
            facade.call(method, path, json=payload)
        assert (exc.value.status, exc.value.code) == (403, 'shared_catalog_read_only'), path
    assert tenants.q('SELECT count(*) FROM instrument_family WHERE code = :c', c=f'ZZ_ST_{tenants.tag}').scalar_one() == 0
    assert tenants.q("SELECT active FROM privacy_notice_version WHERE id = :i", i=notice).scalar_one() is True
    denied = tenants.q("SELECT count(*) FROM access_audit WHERE outcome = 'denied' AND institution_id = :i "
                       "AND action LIKE 'DENIED_%'", i=tenants.inst['A']).scalar_one()
    assert denied == len(writes)
    with as_user('admin', 'A'):  # reading stays available
        assert facade.get('/families', status='all', per_page=50)['total'] >= 1
        assert facade.get('/procedures')['total'] >= 1


def test_non_steward_sees_why_the_save_was_refused(client, login_as, tenants, steward_b):
    """ADMIN-09 browser finding: the refusal is explained on the form (403 kept), not a bare Forbidden page."""
    family = tenants.q('SELECT id, code FROM instrument_family LIMIT 1').one()
    login_as('admin', 'A')
    response = client.post(f'/admin/instrument-families/{family.id}/edit', data={
        'name': 'hijack', 'category': _category(tenants), 'status': 'active'})
    page = response.get_data(as_text=True)
    assert response.status_code == 403
    assert 'Shared catalogs are maintained' in page and 'family-name' in page
    assert tenants.q('SELECT name FROM instrument_family WHERE id = :i', i=family.id).scalar_one() != 'hijack'
    reason = client.post('/admin/selection-catalogs/reasons', data={'code': f'zz_why_{tenants.tag}', 'name': 'x'},
                         follow_redirects=True)
    assert 'Shared catalogs are maintained' in reason.get_data(as_text=True)


def test_role_denials_still_get_the_plain_forbidden_page(client, login_as):
    login_as('supervisor', 'A')
    assert client.get('/admin/users').status_code == 403


def test_steward_administrators_keep_writing(as_user, tenants, steward_b):
    with as_user('admin', 'B', 'POST'):
        created = facade.post('/families', {'code': f'ZZ_STB_{tenants.tag}', 'name': 'Steward family',
                                            'category_id': _category(tenants)})
    assert created['code'] == f'ZZ_STB_{tenants.tag}'


def test_without_steward_any_administrator_maintains_shared_catalogs(as_user, tenants, monkeypatch):
    monkeypatch.delenv('ADMIN_CATALOG_STEWARD_INSTITUTION_ID', raising=False)
    with as_user('admin', 'A', 'POST'):
        assert facade.post('/selection-catalogs/reasons', {'code': f'zz_free_{tenants.tag}', 'name': 'x'})['code']


# --- VisionWorker compatibility: what the bridge sends after an Administration activation --------------

def test_vision_bridge_sends_the_model_activated_by_administration(as_user, tenants, local_storage, app):
    category = tenants.q('SELECT id FROM cat_instrument_category LIMIT 1').scalar_one()
    family = uuid.uuid4()
    tenants.q('INSERT INTO instrument_family (id, code, name, category_id) VALUES (:id, :c, :n, :cat)',
              id=family, c=f'ZZ_VB_{tenants.tag}', n='Bridge family', cat=category)
    content = make_checkpoint({0: f'zz_vb_{tenants.tag}', 1: 'not_in_catalog'})
    with as_user('admin', 'A', 'POST'):
        model = facade.post('/vision-models', {'version_tag': f'vb{tenants.tag}', 'file': FileStorage(io.BytesIO(content), 'best.pt')})
        facade.post(f"/vision-models/{model['id']}/publish", {'mapping': {'0': str(family), '1': 'reject'},
                                                              'confirm_missing_families': '1'})
        facade.post(f"/vision-models/{model['id']}/activate")
        payload = vision_bridge.active_model_payload()
    assert payload['model_version'] == f'vb{tenants.tag}' and payload['model_id'] == model['id']
    assert payload['class_map'] == [{'yolo_class_id': 0, 'family_id': str(family), 'family_code': f'ZZ_VB_{tenants.tag}',
                                     'family_name': 'Bridge family', 'category_name': payload['class_map'][0]['category_name']}]
