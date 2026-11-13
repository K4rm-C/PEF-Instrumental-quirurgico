"""RF-AD-04 Privacy notice library (ADMIN-01), real PostgreSQL + object storage."""
import hashlib
import io
import os

import pytest
from werkzeug.datastructures import FileStorage

from services.admin_local import facade
from services.admin_local.messages import AdminDomainError

from .admin_fixtures import as_user, login_as, no_remote_http, pg, tenants  # noqa: F401

PDF = b'%PDF-1.7\n' + b'0' * 300
HTML = b'<!DOCTYPE html><html><body>Aviso de privacidad</body></html>'


@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_STORAGE_BACKEND', 'local')
    monkeypatch.setenv('ADMIN_STORAGE_DIR', str(tmp_path))
    return tmp_path


def publish(tenants, content=PDF, name='aviso.pdf', **extra):
    return facade.post('/privacy-notices', {'version': extra.pop('version', f'v-{tenants.tag}'), 'effective_at': '2026-11-01',
                                            'file': FileStorage(io.BytesIO(content), name), **extra})


def test_publish_pdf_stores_file_and_hash(as_user, tenants, local_storage, no_remote_http):
    before = tenants.q('SELECT count(*) FROM session_processing_agreement').scalar_one()
    with as_user('admin', 'A', 'POST'):
        notice = publish(tenants)
    row = tenants.q('SELECT version, document_uri, content_sha256, active, effective_at FROM privacy_notice_version '
                    'WHERE id = :i', i=notice['id']).one()
    assert row.content_sha256 == hashlib.sha256(PDF).hexdigest() and row.active is True
    assert row.document_uri.startswith('local://admin-storage/privacy-notices/') and row.document_uri.endswith('.pdf')
    assert (local_storage / row.document_uri.split('admin-storage/', 1)[1]).read_bytes() == PDF
    assert row.effective_at.isoformat().startswith('2026-11-01T06:00')  # local midnight, America/Mexico_City
    assert tenants.q('SELECT count(*) FROM session_processing_agreement').scalar_one() == before  # never written here
    assert [r.resource_id.hex for r in tenants.audit('PUBLISH_PRIVACY_NOTICE')] == [notice['id'].replace('-', '')]


@pytest.mark.parametrize('content, name, code', [
    (b'MZ\x90\x00 executable', 'aviso.pdf', 'invalid_file_type'),
    (b'%PDF-1.4 renamed', 'aviso.exe', 'invalid_file_type'),
    (b'plain text, not html', 'aviso.html', 'invalid_file_type'),
])
def test_rejects_files_that_are_not_pdf_or_html(as_user, tenants, local_storage, content, name, code):
    with as_user('admin', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        publish(tenants, content, name)
    assert (exc.value.status, exc.value.code) == (400, code)


def test_settings_limit_size_and_types(as_user, tenants, local_storage):
    tenants.q("INSERT INTO app_setting (institution_id, key, value) VALUES (:i, 'privacy_notice_max_mb', '1'), "
              "(:i, 'privacy_notice_allowed_mime', '[\"application/pdf\"]')", i=tenants.inst['A'])
    with as_user('admin', 'A', 'POST'):
        with pytest.raises(AdminDomainError) as html:
            publish(tenants, HTML, 'aviso.html')
        with pytest.raises(AdminDomainError) as big:
            publish(tenants, PDF + b'0' * (1024 * 1024), version=f'big-{tenants.tag}')
    assert html.value.code == 'invalid_file_type' and big.value.code == 'file_too_large'


def test_duplicate_version_and_hash(as_user, tenants, local_storage):
    with as_user('admin', 'A', 'POST'):
        publish(tenants, HTML, 'aviso.html')
        with pytest.raises(AdminDomainError) as version:
            publish(tenants)
        with pytest.raises(AdminDomainError) as same_hash:
            publish(tenants, HTML, 'aviso.html', version=f'v2-{tenants.tag}')
        confirmed = publish(tenants, HTML, 'aviso.html', version=f'v2-{tenants.tag}', confirm_duplicate='1')
    assert (version.value.status, version.value.code) == (409, 'privacy_version_exists')
    assert (same_hash.value.status, same_hash.value.code) == (409, 'privacy_notice_duplicate_hash')
    assert confirmed['content_sha256'] == hashlib.sha256(HTML).hexdigest()


def test_activation_and_single_active_setting(as_user, tenants, local_storage):
    tenants.q('UPDATE privacy_notice_version SET active = FALSE')
    with as_user('admin', 'A', 'POST'):
        first = publish(tenants, version=f'a-{tenants.tag}')
        second = publish(tenants, HTML, 'aviso.html', version=f'b-{tenants.tag}')
        assert tenants.q('SELECT count(*) FROM privacy_notice_version WHERE active').scalar_one() == 2  # default: several
        facade.post(f"/privacy-notices/{first['id']}/deactivate")
    tenants.q("INSERT INTO app_setting (institution_id, key, value) VALUES (:i, 'privacy_notice_single_active', 'true')",
              i=tenants.inst['A'])
    with as_user('admin', 'A', 'POST'):
        facade.post(f"/privacy-notices/{first['id']}/activate")
    active = tenants.q('SELECT id FROM privacy_notice_version WHERE active').scalars().all()
    assert [str(a) for a in active] == [first['id']]
    assert len(tenants.audit('DEACTIVATE_PRIVACY_NOTICE')) == 2 and len(tenants.audit('ACTIVATE_PRIVACY_NOTICE')) == 1
    assert second['id'] != first['id']


def test_existing_sessions_keep_their_version(as_user, tenants, local_storage):
    """Versions are immutable: deactivating one does not touch agreements that reference it."""
    with as_user('admin', 'A', 'POST'):
        notice = publish(tenants)
        facade.post(f"/privacy-notices/{notice['id']}/deactivate")
    row = tenants.q('SELECT version, content_sha256 FROM privacy_notice_version WHERE id = :i', i=notice['id']).one()
    assert row.version == f'v-{tenants.tag}' and row.content_sha256 == hashlib.sha256(PDF).hexdigest()


def test_screens_publish_download_and_toggle(client, login_as, tenants, local_storage, no_remote_http):
    login_as('admin', 'A')
    assert 'Publish Version' in client.get('/admin/privacy-notices').get_data(as_text=True)
    response = client.post('/admin/privacy-notices/new', data={
        'document': (io.BytesIO(HTML), 'aviso.html'), 'version': f'web-{tenants.tag}', 'effective_at': '2026-12-01',
        'status': 'active'}, content_type='multipart/form-data')
    assert response.status_code == 302
    notice_id = tenants.q('SELECT id FROM privacy_notice_version WHERE version = :v', v=f'web-{tenants.tag}').scalar_one()
    document = client.get(f'/admin/privacy-notices/{notice_id}/document')
    assert document.data == HTML and document.headers['Content-Disposition'].startswith('attachment')
    assert 'sandbox' in document.headers['Content-Security-Policy']
    assert document.headers['X-Content-Type-Options'] == 'nosniff'
    assert client.post(f'/admin/privacy-notices/{notice_id}/deactivate').status_code == 302
    assert tenants.q('SELECT active FROM privacy_notice_version WHERE id = :i', i=notice_id).scalar_one() is False
    duplicate = client.post('/admin/privacy-notices/new', data={
        'document': (io.BytesIO(HTML), 'aviso.html'), 'version': f'web2-{tenants.tag}', 'effective_at': '2026-12-01'},
        content_type='multipart/form-data')
    assert duplicate.status_code == 409 and 'confirm_duplicate' in duplicate.get_data(as_text=True)


@pytest.mark.skipif(not os.environ.get('PEF_TEST_S3_ENDPOINT'), reason='S3 test storage (Garage) not configured')
def test_publish_into_s3_object_storage(as_user, client, login_as, tenants, monkeypatch):
    for name, value in (('ADMIN_STORAGE_BACKEND', 's3'), ('S3_ENDPOINT', os.environ['PEF_TEST_S3_ENDPOINT']),
                        ('S3_ACCESS_KEY', os.environ['PEF_TEST_S3_ACCESS_KEY']),
                        ('S3_SECRET_KEY', os.environ['PEF_TEST_S3_SECRET_KEY']),
                        ('ADMIN_S3_BUCKET', os.environ['PEF_TEST_S3_BUCKET'])):
        monkeypatch.setenv(name, value)
    with as_user('admin', 'A', 'POST'):
        notice = publish(tenants)
    assert notice['document_uri'].startswith(f"s3://{os.environ['PEF_TEST_S3_BUCKET']}/privacy-notices/")
    login_as('admin', 'A')
    assert client.get(f"/admin/privacy-notices/{notice['id']}/document").data == PDF


def test_only_it_admin(client, login_as, as_user, tenants, local_storage):
    login_as('supervisor', 'A')
    assert client.get('/admin/privacy-notices').status_code == 403
    with as_user('supervisor', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        publish(tenants)
    assert exc.value.status == 403
