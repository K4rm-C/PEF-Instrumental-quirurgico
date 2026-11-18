"""RF-AD-08 Vision models (ADMIN-01): safe class-name extraction, upload, mapping, activation.

The checkpoints are synthetic: a PyTorch-style zip whose data.pkl references Ultralytics classes and
tensor storages (persistent ids), like a real best.pt. A real best.pt was not available in this
environment (see docs/ADMIN_MATRIZ_CUMPLIMIENTO.md).
"""
import hashlib
import io
import json
import os
import pickle
import sys
import types
import uuid
import zipfile

import pytest
from werkzeug.datastructures import FileStorage

from services.admin_local import facade, yolo_weights
from services.admin_local.messages import AdminDomainError

from .admin_fixtures import as_user, login_as, no_remote_http, pg, tenants  # noqa: F401


class _Storage:
    """Stands for a tensor storage: pickled as a persistent id, never as data."""


def make_checkpoint(names, *, yaml_file='yolov8n.yaml', scale='n', nc=None, extra=b''):
    modules = {name: types.ModuleType(name) for name in ('ultralytics', 'ultralytics.nn', 'ultralytics.nn.tasks')}
    detection = type('DetectionModel', (), {'__module__': 'ultralytics.nn.tasks'})
    modules['ultralytics.nn.tasks'].DetectionModel = detection
    saved = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        model = detection()
        model.names = names
        model.yaml = {'yaml_file': yaml_file, 'scale': scale, 'nc': nc if nc is not None else len(names)}
        model.weights = [_Storage(), _Storage()]
        buffer = io.BytesIO()
        pickler = pickle.Pickler(buffer, protocol=2)
        pickler.persistent_id = lambda obj: ('storage', 'FloatStorage', str(id(obj)), 'cpu', 4) \
            if isinstance(obj, _Storage) else None
        pickler.dump({'epoch': -1, 'model': model, 'train_args': {'imgsz': 640}, 'date': '2026-10-01'})
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('best/data.pkl', buffer.getvalue())
        zf.writestr('best/data/0', b'\x00' * 64 + extra)
        zf.writestr('best/version', '3')
    return archive.getvalue()


def write_tmp(tmp_path, content, name='best.pt'):
    path = tmp_path / name
    path.write_bytes(content)
    return str(path)


# --- Safe reader (no database) ---------------------------------------------------------------------

def test_reads_names_architecture_and_size_without_executing(tmp_path):
    parsed = yolo_weights.read_checkpoint(write_tmp(tmp_path, make_checkpoint({0: 'kelly', 1: 'mayo_scissors'})))
    assert parsed == {'names': {0: 'kelly', 1: 'mayo_scissors'}, 'nc': 2, 'architecture': 'yolov8n', 'model_size': 'n'}
    assert 'ultralytics.nn.tasks' not in sys.modules  # the file's classes were never imported


def test_list_names_and_inconsistent_nc(tmp_path):
    assert yolo_weights.read_checkpoint(write_tmp(tmp_path, make_checkpoint(['a', 'b', 'c'])))['names'] == {0: 'a', 1: 'b', 2: 'c'}
    with pytest.raises(yolo_weights.WeightsParseError):
        yolo_weights.read_checkpoint(write_tmp(tmp_path, make_checkpoint({0: 'a'}, nc=5), 'bad.pt'))


def test_a_malicious_pickle_is_never_executed(tmp_path):
    marker = tmp_path / 'pwned.txt'

    class Evil:
        def __reduce__(self):
            return (os.system, (f'echo pwned > "{marker}"',))
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('best/data.pkl', pickle.dumps({'model': Evil(), 'names': Evil()}, protocol=2))
    with pytest.raises(yolo_weights.WeightsParseError):
        yolo_weights.read_checkpoint(write_tmp(tmp_path, archive.getvalue()))
    assert not marker.exists()


@pytest.mark.parametrize('content, expected', [
    (b'{"0": "kelly", "1": "mayo"}', {0: 'kelly', 1: 'mayo'}),
    (b'["kelly", "mayo"]', {0: 'kelly', 1: 'mayo'}),
    (b'{"names": {"0": "kelly"}}', {0: 'kelly'}),
    (b'path: data\nnames:\n  0: kelly   # clamp\n  1: "mayo scissors"\nnc: 2\n', {0: 'kelly', 1: 'mayo scissors'}),
    (b'names: [kelly, mayo]\n', {0: 'kelly', 1: 'mayo'}),
    (b'names:\n  - kelly\n  - mayo\n', {0: 'kelly', 1: 'mayo'}),
])
def test_names_file_formats(content, expected):
    assert yolo_weights.parse_names_file(content) == expected


@pytest.mark.parametrize('content', [b'', b'{"1": "gap"}', b'not names at all', b'\xff\xfe\x00', b'{"0": ""}'])
def test_invalid_names_files(content):
    with pytest.raises(yolo_weights.WeightsParseError):
        yolo_weights.parse_names_file(content)


# --- Upload, mapping, activation (real PostgreSQL + storage) ------------------------------------------

@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_STORAGE_BACKEND', 'local')
    monkeypatch.setenv('ADMIN_STORAGE_DIR', str(tmp_path / 'storage'))
    return tmp_path / 'storage'


@pytest.fixture
def families(tenants):
    category = tenants.q('SELECT id FROM cat_instrument_category LIMIT 1').scalar_one()
    ids = {}
    for key in ('a', 'b'):
        ids[key] = uuid.uuid4()
        tenants.q('INSERT INTO instrument_family (id, code, name, category_id) VALUES (:id, :c, :n, :cat)',
                  id=ids[key], c=f'ZZ_{key.upper()}_{tenants.tag}', n=f'Family {key}', cat=category)
    return ids


def weights(tenants, extra=b''):
    return make_checkpoint({0: f'zz_a_{tenants.tag}', 1: f'Family b', 2: 'unknown_thing'}, extra=extra)


def upload(tenants, content=None, version=None, names_file=None, name='best.pt'):
    payload = {'version_tag': version or f'v{tenants.tag}',
               'file': FileStorage(io.BytesIO(content if content is not None else weights(tenants)), name)}
    if names_file is not None:
        payload['names_file'] = FileStorage(io.BytesIO(names_file), 'names.yaml')
    return facade.post('/vision-models', payload)


def test_upload_stores_weights_hash_and_class_names(as_user, tenants, families, local_storage, no_remote_http):
    content = weights(tenants)
    with as_user('admin', 'A', 'POST'):
        model = upload(tenants, content)
    row = tenants.q('SELECT ym.active, ym.checksum, ym.architecture, ym.model_size, ym.class_names, ma.kind, ma.sha256, '
                    'ma.size_bytes, ma.object_key FROM yolo_model ym JOIN media_asset ma ON ma.id = ym.media_asset_id '
                    'WHERE ym.id = :i', i=model['id']).one()
    digest = hashlib.sha256(content).hexdigest()
    assert row.active is False and row.checksum == digest == row.sha256 and row.kind == 'weights'
    assert (row.architecture, row.model_size, row.size_bytes) == ('yolov8n', 'n', len(content))
    assert row.class_names == {'0': f'zz_a_{tenants.tag}', '1': 'Family b', '2': 'unknown_thing'}
    assert (local_storage / row.object_key).read_bytes() == content and row.object_key.startswith('models/')
    suggestions = {r['yolo_class_id']: r['suggested_family_id'] for r in model['class_names']}
    assert suggestions == {0: str(families['a']), 1: str(families['b']), 2: None}  # by code and by name
    assert len(tenants.audit('CREATE_VISION_MODEL')) == 1


def test_upload_rejections(as_user, tenants, families, local_storage):
    content = weights(tenants)
    tenants.q("INSERT INTO app_setting (institution_id, key, value) VALUES (:i, 'model_weights_max_mb', '1')",
              i=tenants.inst['A'])
    with as_user('admin', 'A', 'POST'):
        upload(tenants, content)
        cases = {
            'checksum_exists': lambda: upload(tenants, content, version=f'other{tenants.tag}'),
            'version_tag_exists': lambda: upload(tenants, weights(tenants, extra=b'x')),
            'invalid_file_type': lambda: upload(tenants, content, version=f'x{tenants.tag}', name='best.onnx'),
            'file_too_large': lambda: upload(tenants, weights(tenants, extra=os.urandom(1024 * 1024)), version=f'y{tenants.tag}'),
        }
        for code, call in cases.items():
            with pytest.raises(AdminDomainError) as exc:
                call()
            assert exc.value.code == code, code
        with pytest.raises(AdminDomainError) as not_zip:
            upload(tenants, b'\x80\x02 legacy pickle', version=f'z{tenants.tag}')
    assert not_zip.value.code == 'invalid_file_type'
    assert tenants.q('SELECT count(*) FROM yolo_model WHERE version_tag LIKE :t', t=f'%{tenants.tag}').scalar_one() == 1


def test_unreadable_names_require_a_names_file(as_user, tenants, families, local_storage):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('best/data.pkl', pickle.dumps({'model': 'opaque'}, protocol=2))
    content = archive.getvalue()
    with as_user('admin', 'A', 'POST'):
        with pytest.raises(AdminDomainError) as blocked:
            upload(tenants, content)
        with pytest.raises(AdminDomainError) as bad_names:
            upload(tenants, content, names_file=b'nothing useful')
        model = upload(tenants, content, names_file=b'names:\n  0: kelly\n  1: mayo\n')
    assert blocked.value.code == 'names_required' and bad_names.value.code == 'invalid_names_file'
    assert [r['name'] for r in model['class_names']] == ['kelly', 'mayo']


def test_mapping_rules_and_publication(as_user, tenants, families, local_storage, no_remote_http):
    with as_user('admin', 'A', 'POST'):
        model = upload(tenants)
        publish = lambda mapping, **extra: facade.post(f"/vision-models/{model['id']}/publish",  # noqa: E731
                                                       {'mapping': mapping, **extra})
        errors = {}
        for name, mapping in (('unmapped', {'0': str(families['a'])}),
                              ('duplicate', {'0': str(families['a']), '1': str(families['a']), '2': 'reject'}),
                              ('all_rejected', {'0': 'reject', '1': 'reject', '2': 'reject'})):
            with pytest.raises(AdminDomainError) as exc:
                publish(mapping)
            errors[name] = exc.value.code
        good = {'0': str(families['a']), '1': str(families['b']), '2': 'reject'}
        with pytest.raises(AdminDomainError) as overlay:
            publish(good)  # the seeded active families are not detected by this model
        published = publish(good, confirm_missing_families='1')
    assert errors == {'unmapped': 'unmapped_classes', 'duplicate': 'duplicate_class_family',
                      'all_rejected': 'classes_required'}
    assert (overlay.value.status, overlay.value.code) == (409, 'missing_families')
    assert 'not detected by this model' in overlay.value.message
    assert published['rejected_classes'] == [2] and published['class_count'] == 2
    assert sorted(tenants.q('SELECT yolo_class_id FROM model_class WHERE model_id = :m', m=model['id']).scalars()) == [0, 1]
    assert len(tenants.audit('PUBLISH_VISION_MODEL')) == 1


def test_activation_keeps_pinned_sessions_and_never_leaves_zero_active(as_user, tenants, families, local_storage):
    previous = tenants.q('SELECT id FROM yolo_model WHERE active').scalar_one()
    session_status = tenants.q("SELECT id FROM cat_session_status WHERE code = 'in_progress'").scalar_one()
    op_status = tenants.q("SELECT id FROM cat_operation_status WHERE code = 'scheduled'").scalar_one()
    operation, session = uuid.uuid4(), uuid.uuid4()
    tenants.q('INSERT INTO operation (id, status_id, institution_id) VALUES (:o, :s, :i)',
              o=operation, s=op_status, i=tenants.inst['A'])
    tenants.q("INSERT INTO work_session (id, status_id, user_id, operation_id, capture_mode, yolo_model_id) "
              "VALUES (:s, :st, :u, :o, 'vision', :m)", s=session, st=session_status,
              u=tenants.users['operator']['A'], o=operation, m=previous)
    with as_user('admin', 'A', 'POST'):
        model = upload(tenants)
        with pytest.raises(AdminDomainError) as unmapped:
            facade.post(f"/vision-models/{model['id']}/activate")
        facade.post(f"/vision-models/{model['id']}/publish", {
            'mapping': {'0': str(families['a']), '1': str(families['b']), '2': 'reject'}, 'confirm_missing_families': '1'})
        facade.post(f"/vision-models/{model['id']}/activate")
        with pytest.raises(AdminDomainError) as last:
            facade.post(f"/vision-models/{model['id']}/deactivate")  # active capture stations exist
    assert unmapped.value.code == 'classes_required' and last.value.code == 'last_active_model'
    assert [str(i) for i in tenants.q('SELECT id FROM yolo_model WHERE active').scalars()] == [model['id']]
    assert tenants.q('SELECT yolo_model_id FROM work_session WHERE id = :s', s=session).scalar_one() == previous
    assert len(tenants.audit('ACTIVATE_VISION_MODEL')) == 1 and len(tenants.audit('DEACTIVATE_VISION_MODEL')) == 1


def test_the_active_model_mapping_is_locked(as_user, tenants, families, local_storage):
    with as_user('admin', 'A', 'POST'):
        model = upload(tenants)
        mapping = {'0': str(families['a']), '1': str(families['b']), '2': 'reject'}
        facade.post(f"/vision-models/{model['id']}/publish", {'mapping': mapping, 'confirm_missing_families': '1'})
        facade.post(f"/vision-models/{model['id']}/activate")
        with pytest.raises(AdminDomainError) as exc:
            facade.post(f"/vision-models/{model['id']}/publish", {'mapping': {**mapping, '2': str(families['a'])},
                                                                   'confirm_missing_families': '1'})
    assert exc.value.code == 'active_model_classes_locked'


def test_screens_upload_and_map(client, login_as, tenants, families, local_storage, no_remote_http):
    login_as('admin', 'A')
    assert 'name="weights"' in client.get('/admin/vision-models/new').get_data(as_text=True)
    response = client.post('/admin/vision-models/new', data={
        'weights': (io.BytesIO(weights(tenants)), 'best.pt'), 'version_tag': f'web{tenants.tag}'},
        content_type='multipart/form-data')
    assert response.status_code == 302
    model_id = tenants.q('SELECT id FROM yolo_model WHERE version_tag = :v', v=f'web{tenants.tag}').scalar_one()
    page = client.get(f'/admin/vision-models/{model_id}/edit').get_data(as_text=True)
    assert 'unknown_thing' in page and f'value="{families["a"]}" selected' in page and 'Reject this class' in page
    refused = client.post(f'/admin/vision-models/{model_id}/edit', data={
        'class_0': str(families['a']), 'class_1': str(families['b']), 'class_2': 'reject'})
    assert refused.status_code == 409 and 'confirm_missing_families' in refused.get_data(as_text=True)
    done = client.post(f'/admin/vision-models/{model_id}/edit', data={
        'class_0': str(families['a']), 'class_1': str(families['b']), 'class_2': 'reject', 'confirm_missing_families': '1'})
    assert done.status_code == 302
    assert tenants.q('SELECT count(*) FROM model_class WHERE model_id = :m', m=model_id).scalar_one() == 2


@pytest.mark.skipif(not os.environ.get('PEF_TEST_S3_ENDPOINT'), reason='S3 test storage (Garage) not configured')
def test_weights_in_s3_object_storage(as_user, tenants, families, monkeypatch):
    for name, value in (('ADMIN_STORAGE_BACKEND', 's3'), ('S3_ENDPOINT', os.environ['PEF_TEST_S3_ENDPOINT']),
                        ('S3_ACCESS_KEY', os.environ['PEF_TEST_S3_ACCESS_KEY']),
                        ('S3_SECRET_KEY', os.environ['PEF_TEST_S3_SECRET_KEY']),
                        ('ADMIN_S3_BUCKET', os.environ['PEF_TEST_S3_BUCKET'])):
        monkeypatch.setenv(name, value)
    content = weights(tenants)
    with as_user('admin', 'A', 'POST'):
        model = upload(tenants, content)
    from services.admin_local import storage
    row = tenants.q('SELECT ma.bucket, ma.object_key FROM yolo_model ym JOIN media_asset ma ON ma.id = ym.media_asset_id '
                    'WHERE ym.id = :i', i=model['id']).one()
    assert row.bucket == os.environ['PEF_TEST_S3_BUCKET'] and row.object_key.startswith('models/')
    assert storage.get(row.bucket, row.object_key) == content


def test_only_it_admin(client, login_as, as_user, tenants, local_storage):
    login_as('supervisor', 'A')
    assert client.get('/admin/vision-models/new').status_code == 403
    with as_user('supervisor', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        upload(tenants)
    assert exc.value.status == 403
