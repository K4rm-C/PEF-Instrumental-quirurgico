"""Administration object storage (ADMIN-01): server-generated keys, path traversal, magic-byte sniffing."""
import hashlib
import io

import pytest
from werkzeug.datastructures import FileStorage

from services.admin_local import storage
from services.admin_local.errors import ApiError


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_STORAGE_BACKEND', 'local')
    monkeypatch.setenv('ADMIN_STORAGE_DIR', str(tmp_path / 'store'))
    (tmp_path / 'secret.txt').write_text('outside the storage root')
    return tmp_path


def test_keys_are_generated_by_the_server_and_round_trip(local):
    upload = storage.receive(FileStorage(io.BytesIO(b'%PDF-1.7 x'), '../../evil name.pdf'), max_bytes=1024,
                             allowed={'application/pdf'}, field='file')
    try:
        stored = storage.put(upload, 'privacy-notices/')
    finally:
        upload.discard()
    assert stored.key.startswith('privacy-notices/') and '..' not in stored.key and 'evil' not in stored.key
    assert upload.sha256 == hashlib.sha256(b'%PDF-1.7 x').hexdigest()
    assert storage.get('local', stored.key) == b'%PDF-1.7 x'


@pytest.mark.parametrize('key', ['../secret.txt', '../../secret.txt', 'privacy-notices/../../secret.txt'])
def test_reads_cannot_leave_the_storage_root(local, key):
    with pytest.raises(storage.StorageUnavailable):
        storage.get('local', key)


@pytest.mark.parametrize('content, allowed, expected', [
    (b'\xff\xd8\xff\xe0data', {'image/jpeg'}, 'image/jpeg'),
    (b'\x89PNG\r\n\x1a\ndata', {'image/png'}, 'image/png'),
    (b'RIFF\x00\x00\x00\x00WEBPdata', {'image/webp'}, 'image/webp'),
    (b'%PDF-1.4', {'application/pdf'}, 'application/pdf'),
    (b'\xef\xbb\xbf  <!DOCTYPE html><html>', {'text/html'}, 'text/html'),
    (b'PK\x03\x04zip', {'application/x-pytorch'}, 'application/octet-stream'),
    (b'%PDF-1.4', {'image/png'}, None),                 # a PDF is not accepted where only images are allowed
    (b'<script>alert(1)</script>', {'text/html'}, None),  # not an HTML document start
    (b'MZ\x90\x00', {'application/pdf', 'text/html'}, None),
])
def test_type_comes_from_magic_bytes(content, allowed, expected):
    assert storage.sniff(content, allowed) == expected


def test_size_limit_is_enforced_while_reading(local):
    with pytest.raises(ApiError) as exc:
        storage.receive(FileStorage(io.BytesIO(b'%PDF-1.4' + b'0' * 2048), 'a.pdf'), max_bytes=1024,
                        allowed={'application/pdf'}, field='file')
    assert exc.value.code == 'file_too_large'


@pytest.mark.skipif(not __import__('os').environ.get('PEF_TEST_S3_ENDPOINT'), reason='S3 test storage (Garage) not configured')
def test_large_objects_round_trip_through_garage(monkeypatch):
    """ADMIN-09: uploads above the multipart threshold (8 MB) must be readable again. Garage answers them with a
    part-level CRC that recent botocore rejects as a full-object checksum (FlexibleChecksumError)."""
    import os
    for name, value in (('ADMIN_STORAGE_BACKEND', 's3'), ('S3_ENDPOINT', os.environ['PEF_TEST_S3_ENDPOINT']),
                        ('S3_ACCESS_KEY', os.environ['PEF_TEST_S3_ACCESS_KEY']),
                        ('S3_SECRET_KEY', os.environ['PEF_TEST_S3_SECRET_KEY']),
                        ('ADMIN_S3_BUCKET', os.environ['PEF_TEST_S3_BUCKET'])):
        monkeypatch.setenv(name, value)
    body = b'%PDF-1.7\n' + os.urandom(9 * 1024 * 1024)
    upload = storage.receive(FileStorage(io.BytesIO(body), 'big.pdf'), max_bytes=len(body) + 1,
                             allowed={'application/pdf'}, field='file')
    try:
        stored = storage.put(upload, 'privacy-notices/')
    finally:
        upload.discard()
    assert hashlib.sha256(storage.get(stored.bucket, stored.key)).hexdigest() == hashlib.sha256(body).hexdigest()
