"""Session capture evidence: private local storage + MediaAsset metadata.

Association WorkSession <-> MediaAsset (no FK exists in the schema): every capture is stored
under the deterministic object_key namespace

    institutions/<institution_uuid>/sessions/<session_uuid>/captures/<asset_uuid>.jpg

inside the configured bucket, and a session's captures are queried by that exact prefix only.
The institution is the session operator's institution (WorkSession.user.institution_id).

Consistency (filesystem and PostgreSQL share no transaction): all validation happens before
any byte is written; the file is written atomically (temp file + os.replace); if staging the
DB rows fails the file is removed here, and the caller removes it if the commit fails
(discard_file). Earlier captures are never touched.
"""
import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app
from flask_babel import gettext as _
from sqlalchemy import select

from extensions import db
from models.CatSessionStatus import CatSessionStatus
from models.MediaAsset import MediaAsset
from models.User import User
from services.admin_service import ConflictError, FormError, parse_uuid
from services.audit import record_audit

JPEG_CONTENT_TYPES = {'image/jpeg', 'image/jpg', 'image/pjpeg'}
CAPTURE_ALLOWED_STATUSES = {'open', 'counting'}  # 002_seed_catalogs: open -> counting -> validating -> closed
MIN_JPEG_BYTES = 125  # smallest valid baseline JPEG is ~125 bytes


class PayloadTooLarge(FormError):
    status = 413


@dataclass(frozen=True)
class CaptureFile:
    """What the inference block needs about a stored capture."""
    media_asset_id: uuid.UUID
    path: Path
    content_type: str
    sha256: str
    size_bytes: int
    created_at: datetime


# --------------------------------------------------------------------------- storage location

def storage_root():
    configured = current_app.config.get('CAPTURE_STORAGE_ROOT')
    root = Path(configured) if configured else Path(current_app.instance_path) / 'evidence'
    return root.resolve()


def storage_bucket():
    return current_app.config.get('CAPTURE_STORAGE_BUCKET') or 'pef-evidence'


def _session_institution_id(work_session):
    return db.session.get(User, work_session.user_id).institution_id


def session_prefix(work_session):
    return f'institutions/{_session_institution_id(work_session)}/sessions/{work_session.id}/captures/'


def resolve_object_path(object_key):
    """Physical path for an object_key, refusing anything that escapes the storage root."""
    root = storage_root()
    path = (root / object_key).resolve()
    if path == root or root not in path.parents:
        raise ValueError('object_key escapes the storage root')
    return path


# --------------------------------------------------------------------------- validation

def validate_jpeg(data, content_type):
    max_bytes = int(current_app.config.get('CAPTURE_MAX_BYTES', 10 * 1024 * 1024))
    if not data:
        raise FormError(_('The selected file is empty.'))
    if len(data) > max_bytes:
        raise PayloadTooLarge(_('The image exceeds the maximum size of %(size)s MB.', size=max_bytes // (1024 * 1024)))
    if (content_type or '').split(';')[0].strip().lower() not in JPEG_CONTENT_TYPES:
        raise FormError(_('Only JPEG images are accepted.'))
    # magic bytes: SOI + marker at the start, EOI near the end (some cameras pad after EOI)
    if len(data) < MIN_JPEG_BYTES or not data.startswith(b'\xff\xd8\xff') or b'\xff\xd9' not in data[-2048:]:
        raise FormError(_('The file is not a valid JPEG image.'))


# --------------------------------------------------------------------------- capture

def _write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with open(temp, 'xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def discard_file(path):
    """Compensation: remove a just-written capture (never raises)."""
    try:
        if path is not None and Path(path).exists():
            Path(path).unlink()
    except OSError:
        current_app.logger.exception('Could not remove orphan capture %s', path)


def store_capture(work_session, upload, ctx):
    """Validate + persist one JPEG for the session. Returns (MediaAsset, written_path).

    Stages MediaAsset, the open -> counting transition and audit rows in the current
    transaction; the caller commits and must call discard_file(path) if the commit fails.
    """
    status = db.session.get(CatSessionStatus, work_session.status_id)
    if status is None or status.code not in CAPTURE_ALLOWED_STATUSES:
        raise ConflictError(_('Captures can only be added while the session is open or counting.'))
    counting = None
    if status.code == 'open':
        counting = db.session.execute(
            select(CatSessionStatus).where(CatSessionStatus.code == 'counting')).scalar_one_or_none()
        if counting is None:
            raise ConflictError(_('Session status "%(code)s" is not configured. Contact the administrator.', code='counting'))
    if upload is None or not upload.filename:
        raise FormError(_('Select or take a photo of the instrument tray.'))
    data = upload.read(int(current_app.config.get('CAPTURE_MAX_BYTES', 10 * 1024 * 1024)) + 1)
    validate_jpeg(data, upload.mimetype)

    asset_id = uuid.uuid4()
    object_key = f'{session_prefix(work_session)}{asset_id}.jpg'  # never derived from the user's filename
    path = resolve_object_path(object_key)
    _write_atomic(path, data)
    try:
        asset = MediaAsset(
            id=asset_id, bucket=storage_bucket(), object_key=object_key,
            gcs_uri=f'local://{storage_bucket()}/{object_key}',
            content_type='image/jpeg', kind='jpeg', storage_provider='other',
            sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
            created_at=datetime.now(timezone.utc),
        )
        db.session.add(asset)
        audit = {'actor': ctx['user'], 'institution_id': ctx['institution_id']}
        if counting is not None:
            work_session.status_id = counting.id  # updated_at bumped by the ORM onupdate
            record_audit('START_COUNT', 'work_session', work_session.id, **audit)
        db.session.flush()
        record_audit('CAPTURE_EVIDENCE', 'media_asset', asset.id, **audit)
        db.session.flush()
    except Exception:
        discard_file(path)
        raise
    return asset, path


# --------------------------------------------------------------------------- reading

def _session_assets(work_session):
    return (
        select(MediaAsset)
        .where(MediaAsset.bucket == storage_bucket(),
               MediaAsset.object_key.startswith(session_prefix(work_session), autoescape=True),
               MediaAsset.purged_at.is_(None), MediaAsset.blocked_at.is_(None))
        .order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
    )


def session_captures(work_session):
    return db.session.execute(_session_assets(work_session)).scalars().all()


def get_capture_for_session(work_session, raw_asset_id):
    """The MediaAsset only if it lives exactly in this session's namespace (else None)."""
    asset_id = parse_uuid(raw_asset_id)
    if asset_id is None:
        return None
    return db.session.execute(_session_assets(work_session).where(MediaAsset.id == asset_id)).scalar_one_or_none()


def capture_file(asset):
    return CaptureFile(asset.id, resolve_object_path(asset.object_key), asset.content_type or 'image/jpeg',
                       asset.sha256, asset.size_bytes, asset.created_at)


def get_latest_capture_for_session(work_session):
    """Latest stored capture of the session as a CaptureFile (None if there is none)."""
    asset = db.session.execute(_session_assets(work_session).limit(1)).scalar_one_or_none()
    return capture_file(asset) if asset else None
