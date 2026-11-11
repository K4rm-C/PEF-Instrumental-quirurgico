"""RF-AD-02 Instrument families and example photos (ADMIN-01), real PostgreSQL + object storage."""
import hashlib
import io
import os
import uuid

import pytest

from services.admin_local import facade
from services.admin_local.messages import AdminDomainError

from .admin_fixtures import as_user, login_as, no_remote_http, pg, tenants  # noqa: F401

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 200
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 200


@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_STORAGE_BACKEND', 'local')
    monkeypatch.setenv('ADMIN_STORAGE_DIR', str(tmp_path))
    return tmp_path


@pytest.fixture
def family(tenants):
    category = tenants.q('SELECT id FROM cat_instrument_category LIMIT 1').scalar_one()
    family_id = uuid.uuid4()
    tenants.q('INSERT INTO instrument_family (id, code, name, category_id) VALUES (:id, :c, :n, :cat)',
              id=family_id, c=f'ZZ_FAM_{tenants.tag}', n='Test clamp', cat=category)
    return {'id': family_id, 'category': category}


def form(family, **overrides):
    data = {'name': 'Test clamp', 'category': str(family['category']), 'status': 'active'}
    data.update(overrides)
    return data


def test_create_edit_and_duplicate_code(client, login_as, tenants, family, no_remote_http):
    login_as('admin', 'A')
    code = f'ZZ_NEW_{tenants.tag}'
    created = client.post('/admin/instrument-families/new', data={'code': code, **form(family, name='New one')})
    assert created.status_code == 302
    duplicate = client.post('/admin/instrument-families/new', data={'code': code, **form(family)})
    assert duplicate.status_code == 409 and 'That family code already exists.' in duplicate.get_data(as_text=True)
    new_id = tenants.q('SELECT id FROM instrument_family WHERE code = :c', c=code).scalar_one()
    assert client.post(f'/admin/instrument-families/{new_id}/edit', data=form(family, name='Renamed')).status_code == 302
    assert tenants.q('SELECT name FROM instrument_family WHERE id = :i', i=new_id).scalar_one() == 'Renamed'
    assert [r.resource_id for r in tenants.audit('CREATE_INSTRUMENT_FAMILY')] == [new_id]
    assert [r.resource_id for r in tenants.audit('UPDATE_INSTRUMENT_FAMILY')] == [new_id]


def test_deactivating_a_family_of_the_active_model_needs_confirmation(client, login_as, tenants, family):
    tenants.q('UPDATE yolo_model SET active = FALSE')
    model = uuid.uuid4()
    tenants.q("INSERT INTO yolo_model (id, version_tag, active) VALUES (:id, :v, TRUE)", id=model, v=f't{tenants.tag}')
    tenants.q('INSERT INTO model_class (model_id, family_id, yolo_class_id) VALUES (:m, :f, 0)', m=model, f=family['id'])
    login_as('admin', 'A')
    page = client.get(f"/admin/instrument-families/{family['id']}/edit").get_data(as_text=True)
    assert 'confirm_active_model' in page
    refused = client.post(f"/admin/instrument-families/{family['id']}/edit", data=form(family, status='inactive'))
    assert refused.status_code == 409 and 'active vision model' in refused.get_data(as_text=True)
    assert tenants.q('SELECT active FROM instrument_family WHERE id = :i', i=family['id']).scalar_one() is True
    confirmed = client.post(f"/admin/instrument-families/{family['id']}/edit",
                            data=form(family, status='inactive', confirm_active_model='1'))
    assert confirmed.status_code == 302
    assert tenants.q('SELECT active FROM instrument_family WHERE id = :i', i=family['id']).scalar_one() is False
    assert [r.resource_id for r in tenants.audit('DEACTIVATE_INSTRUMENT_FAMILY')] == [family['id']]


def test_family_outside_the_active_model_deactivates_without_confirmation(as_user, tenants, family):
    with as_user('admin', 'A', 'PUT'):
        result = facade.put(f"/families/{family['id']}", {'name': 'Test clamp', 'category_id': str(family['category']),
                                                          'status': 'inactive'})
    assert result['active'] is False


def test_example_photo_upload_view_edit_and_remove(client, login_as, tenants, family, local_storage, no_remote_http):
    login_as('admin', 'A')
    base = f"/admin/instrument-families/{family['id']}"
    response = client.post(f'{base}/examples', data={'image': (io.BytesIO(PNG), 'photo.png'), 'example_text': 'Tip view',
                                                     'sort_order': '2'}, content_type='multipart/form-data')
    assert response.status_code == 302
    row = tenants.q('SELECT fe.id, fe.example_text, fe.sort_order, ma.sha256, ma.size_bytes, ma.content_type, ma.object_key, '
                    'ma.bucket FROM family_example fe JOIN media_asset ma ON ma.id = fe.media_asset_id '
                    'WHERE fe.family_id = :f', f=family['id']).one()
    assert (row.example_text, row.sort_order, row.content_type) == ('Tip view', 2, 'image/png')
    assert row.sha256 == hashlib.sha256(PNG).hexdigest() and row.size_bytes == len(PNG)
    assert row.object_key.startswith('family-examples/') and row.bucket == 'local'
    assert (local_storage / row.object_key).read_bytes() == PNG
    image = client.get(f'{base}/examples/{row.id}/image')
    assert image.data == PNG and image.mimetype == 'image/png' and image.headers['X-Content-Type-Options'] == 'nosniff'
    assert 'Tip view' in client.get(f'{base}/edit').get_data(as_text=True)
    client.post(f'{base}/examples/{row.id}/edit', data={'example_text': 'Side view', 'sort_order': '1'})
    assert tenants.q('SELECT example_text FROM family_example WHERE id = :i', i=row.id).scalar_one() == 'Side view'
    client.post(f'{base}/examples/{row.id}/delete')
    assert tenants.q('SELECT count(*) FROM family_example WHERE family_id = :f', f=family['id']).scalar_one() == 0
    assert len(tenants.audit('UPDATE_INSTRUMENT_FAMILY')) == 3


@pytest.mark.parametrize('content, name, code', [
    (b'<script>alert(1)</script>', 'photo.png', 'invalid_file_type'),   # declared PNG, is not an image
    (b'%PDF-1.4 not an image', 'photo.jpg', 'invalid_file_type'),
    (b'', 'photo.png', 'empty_file'),
])
def test_example_rejects_files_that_are_not_allowed_images(as_user, tenants, family, local_storage, content, name, code):
    from werkzeug.datastructures import FileStorage
    with as_user('admin', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        facade.post(f"/families/{family['id']}/examples", {'file': FileStorage(io.BytesIO(content), name)})
    assert (exc.value.status, exc.value.code) == (400, code)
    assert tenants.q('SELECT count(*) FROM family_example WHERE family_id = :f', f=family['id']).scalar_one() == 0


def test_example_size_and_mime_limits_come_from_settings(as_user, tenants, family, local_storage):
    from werkzeug.datastructures import FileStorage
    tenants.q("INSERT INTO app_setting (institution_id, key, value) VALUES (:i, 'family_example_max_mb', '1'), "
              "(:i, 'family_example_allowed_mime', '[\"image/png\"]')", i=tenants.inst['A'])
    with as_user('admin', 'A', 'POST'):
        with pytest.raises(AdminDomainError) as too_big:
            facade.post(f"/families/{family['id']}/examples",
                        {'file': FileStorage(io.BytesIO(PNG + b'\x00' * (1024 * 1024)), 'big.png')})
        with pytest.raises(AdminDomainError) as jpeg:
            facade.post(f"/families/{family['id']}/examples", {'file': FileStorage(io.BytesIO(JPEG), 'p.jpg')})
    assert too_big.value.code == 'file_too_large' and jpeg.value.code == 'invalid_file_type'


@pytest.mark.skipif(not os.environ.get('PEF_TEST_S3_ENDPOINT'), reason='S3 test storage (Garage) not configured')
def test_example_photo_in_s3_object_storage(as_user, client, login_as, tenants, family, monkeypatch):
    from werkzeug.datastructures import FileStorage
    monkeypatch.setenv('ADMIN_STORAGE_BACKEND', 's3')
    monkeypatch.setenv('S3_ENDPOINT', os.environ['PEF_TEST_S3_ENDPOINT'])
    monkeypatch.setenv('S3_ACCESS_KEY', os.environ['PEF_TEST_S3_ACCESS_KEY'])
    monkeypatch.setenv('S3_SECRET_KEY', os.environ['PEF_TEST_S3_SECRET_KEY'])
    monkeypatch.setenv('ADMIN_S3_BUCKET', os.environ['PEF_TEST_S3_BUCKET'])
    with as_user('admin', 'A', 'POST'):
        data = facade.post(f"/families/{family['id']}/examples", {'file': FileStorage(io.BytesIO(JPEG), 'p.jpg')})
    example = data['examples'][0]
    row = tenants.q('SELECT bucket, object_key, kind FROM media_asset WHERE id = :i', i=example['media_asset_id']).one()
    assert row.bucket == os.environ['PEF_TEST_S3_BUCKET'] and row.kind == 'jpeg'
    login_as('admin', 'A')
    assert client.get(f"/admin/instrument-families/{family['id']}/examples/{example['id']}/image").data == JPEG


def test_only_it_admin(client, login_as, tenants, family):
    login_as('supervisor', 'A')
    assert client.get('/admin/instrument-families').status_code == 403
    assert client.post(f"/admin/instrument-families/{family['id']}/examples",
                       data={'image': (io.BytesIO(PNG), 'p.png')}, content_type='multipart/form-data').status_code == 403
