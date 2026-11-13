"""Object storage for Administration uploads: family example photos (RF-AD-02), privacy notices
(RF-AD-04) and vision-model weights (RF-AD-08).

Backends (ADMIN_STORAGE_BACKEND):
- "s3": S3-compatible object storage (Garage in docker-compose.yml, MinIO in docker-compose.test.yml),
  same variables and addressing as EvidenceService: S3_INTERNAL_ENDPOINT (or S3_ENDPOINT), S3_REGION,
  S3_ACCESS_KEY(_ID), S3_SECRET_KEY / S3_SECRET_ACCESS_KEY, path-style SigV4. Bucket ADMIN_S3_BUCKET.
- "local" (default for development): files under ADMIN_STORAGE_DIR (default <instance>/admin_storage).

Uploads are read in chunks with a hard size limit, hashed (SHA-256) on the server and identified by
their magic bytes, never by the client's file name or declared MIME type. Object keys are generated
(prefix + uuid + extension): a client never chooses a path. Nothing here executes uploaded content.
"""
import hashlib
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from flask import current_app

from .errors import bad_request

CHUNK = 1024 * 1024
MIME_EXTENSION = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'application/pdf': '.pdf',
                  'text/html': '.html', 'application/octet-stream': '.pt'}


class StorageUnavailable(Exception):
    pass


@dataclass
class Upload:
    """A received file, spooled to a temporary file, with server-computed facts."""
    path: str
    size: int
    sha256: str
    content_type: str
    original_name: str

    def read(self):
        with open(self.path, 'rb') as handle:
            return handle.read()

    def discard(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass


@dataclass
class StoredObject:
    provider: str
    bucket: str
    key: str
    uri: str


def _env(*names, default=''):
    for name in names:
        value = os.environ.get(name, '').strip()
        if value:
            return value
    return default


def backend_name():
    return _env('ADMIN_STORAGE_BACKEND', default='local').lower()


def bucket_name():
    return _env('ADMIN_S3_BUCKET', default='pef-admin') if backend_name() == 's3' else 'local'


# --- Receiving files ----------------------------------------------------------------------

def sniff(head, allowed):
    """Content type from the first bytes, restricted to `allowed`; None when it is not one of them."""
    if 'image/jpeg' in allowed and head[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if 'image/png' in allowed and head[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if 'image/webp' in allowed and head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    if 'application/pdf' in allowed and head[:5] == b'%PDF-':
        return 'application/pdf'
    if 'text/html' in allowed:
        try:
            text = head.decode('utf-8').lstrip('﻿').lstrip().lower()
        except UnicodeDecodeError:
            text = ''
        if text.startswith(('<!doctype html', '<html')):
            return 'text/html'
    if 'application/x-pytorch' in allowed and head[:4] == b'PK\x03\x04':
        return 'application/octet-stream'  # PyTorch zip checkpoint (torch.save >= 1.6)
    return None


def receive(file_storage, *, max_bytes, allowed, field, allowed_extensions=None):
    """Spool an uploaded file with a size limit; validate extension and magic bytes; hash it."""
    if file_storage is None or not getattr(file_storage, 'filename', ''):
        raise bad_request(f'{field}_required', 'Choose a file to upload.', field)
    name = os.path.basename(file_storage.filename.replace('\\', '/'))
    if allowed_extensions and os.path.splitext(name)[1].lower() not in allowed_extensions:
        raise bad_request('invalid_file_type', 'This file type is not allowed.', field)
    digest, size = hashlib.sha256(), 0
    handle = tempfile.NamedTemporaryFile(prefix='pef-admin-upload-', delete=False)
    head = b''
    try:
        with handle:
            while True:
                chunk = file_storage.stream.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise bad_request('file_too_large', f'The file exceeds the maximum size ({max_bytes // CHUNK} MB).', field)
                if len(head) < 64:
                    head += chunk[:64 - len(head)]
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise bad_request('empty_file', 'The file is empty.', field)
        content_type = sniff(head, allowed)
        if content_type is None:
            raise bad_request('invalid_file_type', 'This file type is not allowed.', field)
    except Exception:
        os.unlink(handle.name)
        raise
    return Upload(handle.name, size, digest.hexdigest(), content_type, name)


# --- Backends ------------------------------------------------------------------------------

def _local_root():
    root = Path(_env('ADMIN_STORAGE_DIR') or Path(current_app.instance_path) / 'admin_storage')
    root.mkdir(parents=True, exist_ok=True)
    return root


def _s3():
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError as exc:  # pragma: no cover - dependency of the s3 backend
        raise StorageUnavailable('boto3 is not installed') from exc
    endpoint = _env('S3_INTERNAL_ENDPOINT', 'S3_ENDPOINT')
    if not endpoint:
        raise StorageUnavailable('S3_INTERNAL_ENDPOINT / S3_ENDPOINT is not configured')
    return boto3.client(
        's3', endpoint_url=endpoint, region_name=_env('S3_REGION', default='garage'),
        aws_access_key_id=_env('S3_ACCESS_KEY_ID', 'S3_ACCESS_KEY'),
        aws_secret_access_key=_env('S3_SECRET_ACCESS_KEY', 'S3_SECRET_KEY'),
        # Checksums only when an operation requires them: Garage answers multipart objects (> 8 MB) with a
        # part-level CRC that botocore >= 1.36 rejects as a full-object checksum (FlexibleChecksumError), so
        # large notices/photos/weights could be stored but never read back. Integrity is the SHA-256 computed
        # on receipt (media_asset / yolo_model.checksum / privacy_notice_version.content_sha256).
        config=BotoConfig(signature_version='s3v4', s3={'addressing_style': 'path'},
                          connect_timeout=3, read_timeout=30, retries={'max_attempts': 2},
                          request_checksum_calculation='when_required',
                          response_checksum_validation='when_required'),
    )


def _s3_call(function, *args, **kwargs):
    try:
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError as exc:  # pragma: no cover
        raise StorageUnavailable('boto3 is not installed') from exc
    try:
        return function(*args, **kwargs)
    except (BotoCoreError, ClientError) as exc:
        raise StorageUnavailable(type(exc).__name__) from exc


def _ensure_bucket(client, bucket):
    try:
        from botocore.exceptions import ClientError
        client.head_bucket(Bucket=bucket)
    except ClientError:
        _s3_call(client.create_bucket, Bucket=bucket)


def put(upload, prefix):
    """Store `upload` under `prefix` (e.g. 'privacy-notices/'); returns where it lives."""
    key = f'{prefix}{uuid.uuid4().hex}{MIME_EXTENSION.get(upload.content_type, "")}'
    if backend_name() == 's3':
        client, bucket = _s3(), bucket_name()
        _ensure_bucket(client, bucket)
        _s3_call(client.upload_file, upload.path, bucket, key, ExtraArgs={'ContentType': upload.content_type})
        return StoredObject('other', bucket, key, f's3://{bucket}/{key}')
    target = _local_root() / key
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(upload.path, 'rb') as source, open(target, 'wb') as destination:
        while chunk := source.read(CHUNK):
            destination.write(chunk)
    return StoredObject('other', 'local', key, f'local://admin-storage/{key}')


def presigned_get(bucket, key, expires=300):
    """MS-02: short-lived read-only link to an S3 object for the vision worker (it holds no S3 credentials).
    None for the local backend (the worker reads Administration's volume, mounted read-only)."""
    if bucket == 'local':
        return None
    return _s3_call(_s3().generate_presigned_url, 'get_object', Params={'Bucket': bucket, 'Key': key},
                    ExpiresIn=int(expires))


def get(bucket, key):
    """Bytes of a stored object (used to serve images/documents to the administrator)."""
    if bucket != 'local':
        response = _s3_call(_s3().get_object, Bucket=bucket, Key=key)
        return response['Body'].read()
    path = (_local_root() / key).resolve()
    if _local_root().resolve() not in path.parents or not path.is_file():
        raise StorageUnavailable('object not found')
    return path.read_bytes()


def ping():
    """Health of the configured backend for the dashboard (RF-AD-01): (ok, detail)."""
    try:
        if backend_name() == 's3':
            client = _s3()
            _s3_call(client.list_buckets)
            return True, f's3 {bucket_name()}'
        root = _local_root()
        probe = root / '.health'
        probe.write_text('ok', encoding='utf-8')
        return True, 'local'
    except (StorageUnavailable, OSError) as exc:
        return False, str(exc) or type(exc).__name__
