"""MS-07 Administration: machine clients (PEF-DS08) of the institution — secret shown once and stored hashed,
closed scope list, institution isolation, rotation and deactivation revoke the tokens through AuthService."""

import uuid

import pytest
import requests
from werkzeug.security import check_password_hash

from services.admin_local import facade
from services.admin_local.messages import AdminDomainError

from .admin_fixtures import as_user, login_as as admin_login, no_remote_http, pg, tenants  # noqa: F401
from .conftest import FakeResponse

BODY = {'name': 'HIS Norte', 'scopes': ['sync.catalog.read', 'fhir.encounter.read'], 'status': 'active'}


@pytest.fixture
def auth_revoke(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return FakeResponse(200, {'revoked': 2})
    monkeypatch.setattr(requests, 'post', fake_post)
    return calls


def test_created_with_a_secret_shown_once_and_stored_hashed(as_user, tenants, no_remote_http):
    with as_user('admin', 'A', 'POST'):
        created = facade.post('/integration-clients', BODY)
        listed = facade.get('/integration-clients')
    row = tenants.q('SELECT secret_hash, scopes, institution_id, created_by_user_id FROM integration_client WHERE id = :i',
                    i=created['id']).mappings().one()
    assert created['client_secret'] and created['client_secret'] not in row['secret_hash']
    assert check_password_hash(row['secret_hash'], created['client_secret'])
    assert row['scopes'] == ['fhir.encounter.read', 'sync.catalog.read'] and row['institution_id'] == tenants.inst['A']
    assert row['created_by_user_id'] == tenants.users['admin']['A']
    assert 'client_secret' not in listed['items'][0] and 'secret_hash' not in listed['items'][0]
    assert {s['code'] for s in listed['scopes']} >= {'fhir.patient.read', 'sync.sessions.read'}
    audit = tenants.audit('CREATE_INTEGRATION_CLIENT')
    assert [a.resource_id for a in audit] == [uuid.UUID(created['id'])]


@pytest.mark.parametrize('scopes, code', [([], 'scopes_required'), (['admin.all'], 'invalid_scope')])
def test_scopes_come_from_the_closed_list(as_user, tenants, scopes, code):
    with as_user('admin', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        facade.post('/integration-clients', {**BODY, 'scopes': scopes})
    assert (exc.value.status, exc.value.code) == (400, code)


def test_other_institution_and_other_roles(as_user, tenants):
    with as_user('admin', 'B', 'POST'):
        foreign = facade.post('/integration-clients', BODY)
    with as_user('admin', 'A', 'PUT'):
        with pytest.raises(AdminDomainError) as edit:
            facade.put(f"/integration-clients/{foreign['id']}", BODY)
        assert facade.get('/integration-clients')['items'] == []
    assert edit.value.status == 404
    with as_user('supervisor', 'A'), pytest.raises(AdminDomainError) as denied:
        facade.get('/integration-clients')
    assert denied.value.status == 403


def test_rotation_and_deactivation_revoke_the_tokens(as_user, tenants, auth_revoke):
    with as_user('admin', 'A', 'POST'):
        created = facade.post('/integration-clients', BODY)
        rotated = facade.post(f"/integration-clients/{created['id']}/rotate-secret", {})
    assert rotated['client_secret'] != created['client_secret'] and rotated['revocation_warning'] is None
    row = tenants.q('SELECT secret_hash FROM integration_client WHERE id = :i', i=created['id']).scalar_one()
    assert check_password_hash(row, rotated['client_secret']) and not check_password_hash(row, created['client_secret'])
    with as_user('admin', 'A', 'PUT'):
        updated = facade.put(f"/integration-clients/{created['id']}", {**BODY, 'status': 'inactive'})
    assert updated['active'] is False
    assert auth_revoke == [f"http://auth.test/api/v1/auth/clients/{created['id']}/revoke"] * 2 or \
        all(url.endswith(f"/api/v1/auth/clients/{created['id']}/revoke") for url in auth_revoke)
    assert len(auth_revoke) == 2
    assert tenants.audit('ROTATE_INTEGRATION_CLIENT_SECRET') and tenants.audit('DEACTIVATE_INTEGRATION_CLIENT')


def test_revocation_failure_never_undoes_the_deactivation(as_user, tenants, monkeypatch):
    monkeypatch.setattr(requests, 'post', lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError('down')))
    with as_user('admin', 'A', 'POST'):
        created = facade.post('/integration-clients', BODY)
    with as_user('admin', 'A', 'PUT'):
        updated = facade.put(f"/integration-clients/{created['id']}", {**BODY, 'status': 'inactive'})
    assert updated['active'] is False and updated['revocation_warning'] == 'revocation_failed'


def test_screens(client, admin_login, tenants, auth_revoke):
    admin_login('admin', 'A')
    page = client.get('/admin/configuration').get_data(as_text=True)
    assert 'data-admin-integration-clients' in page and 'No integration clients registered.' in page
    form = client.get('/admin/configuration/integration-clients/new').get_data(as_text=True)
    assert 'value="fhir.patient.read"' in form
    created = client.post('/admin/configuration/integration-clients/new',
                          data={'name': 'HIS Sur', 'scopes': ['sync.locations.read'], 'status': 'active'})
    html = created.get_data(as_text=True)
    assert created.status_code == 200 and 'data-secret' in html and created.headers['Cache-Control'] == 'no-store'
    row = tenants.q("SELECT id, client_id FROM integration_client WHERE name = 'HIS Sur'").mappings().one()
    assert row['client_id'] in html
    assert 'data-secret' not in client.get(f"/admin/configuration/integration-clients/{row['id']}/edit").get_data(as_text=True)
    rotated = client.post(f"/admin/configuration/integration-clients/{row['id']}/rotate")
    assert rotated.status_code == 200 and 'data-secret' in rotated.get_data(as_text=True)
    bad = client.post('/admin/configuration/integration-clients/new', data={'name': 'X', 'status': 'active'})
    assert bad.status_code == 400 and 'Choose at least one permission' in bad.get_data(as_text=True)
