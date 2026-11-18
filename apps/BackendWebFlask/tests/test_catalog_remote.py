"""SPD supervisor screens of instruments / kits served by CatalogService (adapter; HTTP simulated).

CatalogService itself is tested in apps/CatalogService (real PostgreSQL/Redis) and end to end in
scripts/e2e_catalog.py. Here: for the SPD supervisor the portal sends the verified token, never
touches the catalog tables locally, keeps the RF data shapes and maps Catalog errors to messages.
Administration (it_admin) is always local since ADMIN-01 (RF-AD-02 families): even with
CATALOG_SERVICE_URL configured it never calls CatalogService.
"""

import json
import uuid

import pytest
import requests

from services.admin_local import catalog_remote

CATEGORY = {'id': str(uuid.uuid4()), 'code': 'hemostasis', 'name': 'Hemostasis'}
FAMILY = {'id': str(uuid.uuid4()), 'code': 'KELLY', 'name': 'Kelly clamp', 'category_code': 'hemostasis',
          'active': True, 'category_id': CATEGORY['id'], 'category_name': 'Hemostasis', 'identify_text': None,
          'classify_text': None, 'function_text': 'Clamps', 'available_count': 3}


class FakeCatalog:
    def __init__(self):
        self.calls = []
        self.routes = {
            ('GET', '/categories'): (200, [CATEGORY]),
            ('GET', '/cycle-statuses'): (200, [{'id': str(uuid.uuid4()), 'code': 'available', 'name': 'Available'}]),
            ('GET', '/families'): (200, [FAMILY]),
            ('GET', '/kits'): (200, []),
        }

    def __call__(self, method, url, json=None, params=None, timeout=None, headers=None):
        path = url.split('/api/v1/catalog', 1)[1]
        self.calls.append((method, path, json, params, headers))
        status, body = self.routes.get((method, path), (404, {'error': 'not_found', 'message': 'Resource not found',
                                                             'details': {}}))
        response = requests.Response()
        response.status_code = status
        response._content = (body if isinstance(body, bytes) else json_dumps(body))
        response.headers['Content-Type'] = 'application/json'
        return response


def json_dumps(value):
    return json.dumps(value).encode()


@pytest.fixture
def catalog(app, monkeypatch):
    fake = FakeCatalog()
    monkeypatch.setattr(catalog_remote, 'CATALOG_SERVICE_URL', 'http://catalog.test')
    monkeypatch.setattr(catalog_remote.requests, 'request', fake)

    def no_local_catalog_writes(*args, **kwargs):
        raise AssertionError('catalog tables must not be used locally')
    from services.admin_local import families, instruments, kits
    for module in (families, instruments, kits):
        for name in dir(module):
            if name.startswith(('create_', 'update_', 'list_', 'get_')):
                monkeypatch.setattr(module, name, no_local_catalog_writes)
    return fake


def test_admin_family_screens_never_call_catalog(client, login, catalog, monkeypatch):
    from services.admin_local import families
    monkeypatch.setattr(families, 'list_families', lambda **kw: {
        'items': [{'id': FAMILY['id'], 'code': 'KELLY', 'name': 'Local Kelly', 'active': True, 'function_text': '',
                   'category': None, 'example_count': 0}],
        'page': 1, 'per_page': 20, 'total': 1, 'total_pages': 1, 'shown': 1,
        'filters': {'search': '', 'category': '', 'status': 'all'}})
    login('it_admin')
    page = client.get('/admin/instrument-families?status=all')
    assert page.status_code == 200 and 'Local Kelly' in page.get_data(as_text=True)
    assert catalog.calls == []


def test_instrument_change_reason_error_is_shown(client, login, catalog):
    login('spd_supervisor')
    instrument_id = str(uuid.uuid4())
    detail = {'id': instrument_id, 'internal_code': 'I-1', 'family_id': FAMILY['id'], 'family_code': 'KELLY',
              'cycle_status_code': 'available', 'institution_id': str(uuid.uuid4()), 'active': True,
              'family_name': 'Kelly clamp', 'category_code': 'hemostasis', 'category_name': 'Hemostasis',
              'cycle_status_name': 'Available', 'cycle_events': []}
    catalog.routes[('GET', f'/instruments/{instrument_id}')] = (200, detail)
    catalog.routes[('PUT', f'/instruments/{instrument_id}')] = (422, {
        'error': 'validation_error', 'message': 'A change reason is required',
        'details': {'field': 'change_reason', 'reason': 'change_reason_required'}})
    response = client.post(f'/admin/instruments/{instrument_id}/edit', data={
        'instrument_family': FAMILY['id'], 'cycle_status': 'maintenance', 'active_status': 'active'})
    assert response.status_code == 400
    assert 'A change reason is required when cycle status or active status changes.' in response.get_data(as_text=True)


def test_kit_new_version_message(client, login, catalog):
    login('spd_supervisor')
    kit_id, new_id = str(uuid.uuid4()), str(uuid.uuid4())
    kit = {'id': kit_id, 'name': 'Kit', 'version': 1, 'active': True, 'institution_id': str(uuid.uuid4()),
           'created_at': None, 'family_count': 1, 'total_expected': 2, 'procedures': [],
           'items': [{'family_id': FAMILY['id'], 'family_code': 'KELLY', 'quantity': 2, 'family_name': 'Kelly clamp',
                      'family_active': True, 'category_code': 'hemostasis', 'category_name': 'Hemostasis',
                      'available_count': 3}]}
    catalog.routes[('GET', f'/kits/{kit_id}')] = (200, kit)
    catalog.routes[('PUT', f'/kits/{kit_id}')] = (201, {**kit, 'id': new_id, 'version': 2})
    response = client.post(f'/admin/kits/{kit_id}/edit', data={
        'name': 'Kit', 'status': 'active', 'instrument_family[]': [FAMILY['id']], 'expected_quantity[]': ['3']})
    assert response.status_code == 302
    put = next(call for call in catalog.calls if call[0] == 'PUT')
    assert put[2]['items'] == [{'family_id': FAMILY['id'], 'quantity': 3}]
    assert 'saved as version 2' in client.get('/admin/kits').get_data(as_text=True)


def test_catalog_down_shows_controlled_message(client, login, monkeypatch, catalog):
    login('spd_supervisor')

    def down(*args, **kwargs):
        raise requests.ConnectionError('refused')
    monkeypatch.setattr(catalog_remote.requests, 'request', down)
    response = client.post('/admin/kits/new', data={'name': 'X', 'status': 'active',
                                                    'instrument_family[]': [FAMILY['id']], 'expected_quantity[]': ['1']})
    assert response.status_code == 503
    assert 'The catalog service is temporarily unavailable' in response.get_data(as_text=True)


def test_other_admin_areas_stay_local(app, login, client, catalog, fake_admin):
    """Adapter scope: only families/instruments/kits paths (and facade sends them only for the SPD supervisor)."""
    assert catalog_remote.handles('/families') and catalog_remote.handles('/kits/1')
    assert not any(catalog_remote.handles(path) for path in ('/users', '/roles', '/procedures', '/vision-models',
                                                             '/institution', '/audit-events', '/dashboard'))


def test_disabled_without_url(monkeypatch):
    monkeypatch.setattr(catalog_remote, 'CATALOG_SERVICE_URL', '')
    assert not catalog_remote.enabled() and not catalog_remote.handles('/families')
