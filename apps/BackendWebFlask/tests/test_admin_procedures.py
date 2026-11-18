"""RF-AD-03 Procedures and selection catalogs (ADMIN-01), real PostgreSQL."""
import uuid

import pytest

from services.admin_local import facade
from services.admin_local.messages import AdminDomainError

from .admin_fixtures import as_user, login_as, no_remote_http, pg, tenants  # noqa: F401


@pytest.fixture
def kits(tenants):
    ids = {}
    for key in ('A', 'B'):
        ids[key] = uuid.uuid4()
        tenants.q('INSERT INTO kit (id, name, version, institution_id) VALUES (:id, :n, 1, :i)',
                  id=ids[key], n=f'Kit {key} {tenants.tag}', i=tenants.inst[key])
    return ids


@pytest.fixture
def procedure(tenants, kits):
    proc = uuid.uuid4()
    tenants.q('INSERT INTO cat_procedure_type (id, code, name) VALUES (:id, :c, :n)',
              id=proc, c=f'zz_proc_{tenants.tag}', n='Test procedure')
    for key in ('A', 'B'):
        tenants.q('INSERT INTO procedure_kit (procedure_type_id, kit_id, is_default) VALUES (:p, :k, :d)',
                  p=proc, k=kits[key], d=key == 'A')
    return proc


def phase_id(tenants):
    return str(tenants.q('SELECT id FROM cat_operation_phase LIMIT 1').scalar_one())


def test_saving_a_procedure_keeps_spd_kit_links_and_replaces_phases(as_user, tenants, procedure, kits):
    with as_user('admin', 'A', 'PUT'):
        saved = facade.put(f'/procedures/{procedure}', {
            'code': f'zz_proc_{tenants.tag}', 'name': 'Renamed',
            'phases': [{'phase_id': phase_id(tenants), 'sort_order': 1, 'is_count_required': True, 'active': True}]})
    assert saved['name'] == 'Renamed' and len(saved['phases']) == 1
    links = tenants.q('SELECT kit_id FROM procedure_kit WHERE procedure_type_id = :p', p=procedure).scalars().all()
    assert sorted(map(str, links)) == sorted(map(str, kits.values()))  # SPD links untouched (D-4)
    # D-2: only the admin's institution kits are shown.
    assert [k['kit']['id'] for k in saved['kits']] == [str(kits['A'])]


def test_admin_cannot_write_kit_links(as_user, tenants, procedure, kits):
    with as_user('admin', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        facade.post('/procedures', {'code': f'zz_new_{tenants.tag}', 'name': 'New',
                                    'kits': [{'kit_id': str(kits['A']), 'is_default': True}]})
    assert (exc.value.status, exc.value.code) == (400, 'procedure_kits_owned_by_spd')
    assert tenants.q('SELECT count(*) FROM cat_procedure_type WHERE code = :c', c=f'zz_new_{tenants.tag}').scalar_one() == 0


def test_procedure_screen_shows_kits_read_only(client, login_as, tenants, procedure, kits, no_remote_http):
    login_as('admin', 'A')
    page = client.get(f'/admin/procedures/{procedure}/edit').get_data(as_text=True)
    assert f'Kit A {tenants.tag}' in page and f'Kit B {tenants.tag}' not in page
    assert 'name="kit[]"' not in page and 'add-kit-btn' not in page
    response = client.post(f'/admin/procedures/{procedure}/edit', data={
        'code': f'zz_proc_{tenants.tag}', 'name': 'Via screen', 'phase[]': [phase_id(tenants)], 'phase_row_index[]': ['0'],
        'sort_order[]': ['1'], 'count_required[]': ['0'], 'phase_active[]': ['0']})
    assert response.status_code == 302
    assert tenants.q('SELECT count(*) FROM procedure_kit WHERE procedure_type_id = :p', p=procedure).scalar_one() == 2


def test_duplicate_procedure_code(as_user, tenants, procedure):
    with as_user('admin', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        facade.post('/procedures', {'code': f'zz_proc_{tenants.tag}', 'name': 'Dup'})
    assert (exc.value.status, exc.value.code) == (409, 'procedure_code_exists')


@pytest.mark.parametrize('kind, table, entity', [
    ('phases', 'cat_operation_phase', 'OPERATION_PHASE'),
    ('categories', 'cat_instrument_category', 'INSTRUMENT_CATEGORY'),
    ('reasons', 'cat_discrepancy_reason', 'DISCREPANCY_REASON'),
])
def test_selection_catalog_crud(as_user, tenants, kind, table, entity, no_remote_http):
    code = f'zz_{tenants.tag}'
    with as_user('admin', 'A', 'POST'):
        created = facade.post(f'/selection-catalogs/{kind}', {'code': code.upper(), 'name': 'First'})
        with pytest.raises(AdminDomainError) as duplicate:
            facade.post(f'/selection-catalogs/{kind}', {'code': code, 'name': 'Again'})
        with pytest.raises(AdminDomainError) as bad_code:
            facade.post(f'/selection-catalogs/{kind}', {'code': '9 bad', 'name': 'x'})
        updated = facade.put(f"/selection-catalogs/{kind}/{created['id']}", {'name': 'Renamed', 'status': 'inactive'})
        with pytest.raises(AdminDomainError) as recode:
            facade.put(f"/selection-catalogs/{kind}/{created['id']}", {'code': 'other', 'name': 'x'})
        listed = facade.get(f'/selection-catalogs/{kind}', search=code)
    assert created['code'] == code and updated['name'] == 'Renamed'
    assert (duplicate.value.status, duplicate.value.code) == (409, 'catalog_code_exists')
    assert bad_code.value.code == 'invalid_catalog_code' and recode.value.code == 'catalog_code_immutable'
    assert [item['code'] for item in listed['items']] == [code]
    assert tenants.q(f'SELECT name FROM {table} WHERE code = :c', c=code).scalar_one() == 'Renamed'
    assert len(tenants.audit(f'CREATE_{entity}')) == 1 and len(tenants.audit(f'UPDATE_{entity}')) == 1
    if kind == 'phases':
        assert updated['active'] is False and len(tenants.audit('DEACTIVATE_OPERATION_PHASE')) == 1


def test_selection_catalog_screens(client, login_as, tenants):
    login_as('admin', 'A')
    assert client.get('/admin/selection-catalogs').status_code == 302
    page = client.get('/admin/selection-catalogs/reasons').get_data(as_text=True)
    assert 'shortage' in page and 'Discrepancy Reasons' in page
    response = client.post('/admin/selection-catalogs/reasons', data={'code': f'zz_r_{tenants.tag}', 'name': 'New reason'})
    assert response.status_code == 302
    assert client.get('/admin/selection-catalogs/unknown').status_code == 404
    login_as('supervisor', 'A')
    assert client.get('/admin/selection-catalogs/reasons').status_code == 403
