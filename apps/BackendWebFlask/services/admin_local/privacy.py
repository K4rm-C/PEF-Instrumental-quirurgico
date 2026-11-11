"""RF-AD-04 Privacy notice library (privacy_notice_version).

Administration publishes versions; the SPD supervisor picks an active one when scheduling and links
it to ONE session (session_processing_agreement, RF-SP-02) - this module never writes that table.
A published version is immutable (version, document, hash, effective date): sessions already agreed
keep their version. Only `active` can change. Several active versions are allowed unless the
`privacy_notice_single_active` setting (RF-AD-10) is on.

Upload: PDF or HTML identified by magic bytes, MIME/size limits from settings (default pdf/html,
10 MB), stored under `privacy-notices/` in object storage, SHA-256 computed on the server. A hash
already published is a warning that needs confirmation (`confirm_duplicate`). Audit names (D-5):
PUBLISH_PRIVACY_NOTICE = privacy.notice.publish; ACTIVATE_/DEACTIVATE_PRIVACY_NOTICE.
The table has no institution column: the library is shared by the deployment (documented limit of D-2).
"""
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import audit, settings, storage
from .common import iso, matches_search, matches_status, paginate, parse_uuid, status_filter, status_flag, text, utc_now
from .config import Config
from .database import db
from .errors import ApiError, bad_request, conflict, not_found
from .media import parse_uri
from .models import PrivacyNoticeVersion

RESOURCE = 'privacy_notice'


def _serialize(row):
    return {'id': str(row.id), 'version': row.version, 'effective_at': iso(row.effective_at), 'active': row.active,
            'content_sha256': (row.content_sha256 or '').strip(), 'document_uri': row.document_uri,
            'stored_by_portal': parse_uri(row.document_uri or '') is not None, 'created_at': iso(row.created_at)}


def list_notices(*, search='', status='all', page=1, per_page=20):
    wanted = status_filter(status, default='all')
    rows = [_serialize(row) for row in db.execute(
        select(PrivacyNoticeVersion).order_by(PrivacyNoticeVersion.effective_at.desc(), PrivacyNoticeVersion.version)
    ).scalars() if matches_status(row.active, wanted) and matches_search(search, row.version, row.content_sha256)]
    result = paginate(rows, page, per_page)
    result['filters'] = {'search': search or '', 'status': wanted}
    return result


def get_notice(notice_id):
    row = db.get(PrivacyNoticeVersion, parse_uuid(notice_id))
    if row is None:
        raise not_found('privacy_notice_not_found', 'Privacy notice not found.')
    return row


def _effective_at(raw):
    value = str(raw or '').strip()
    if not value:
        raise bad_request('effective_at_required', 'Effective date is required.', 'effective_at')
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            return datetime.combine(day, time.min, tzinfo=ZoneInfo(Config.DISPLAY_TIMEZONE)).astimezone(timezone.utc)
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise bad_request('invalid_date', 'Use the YYYY-MM-DD date format.', 'effective_at')
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo(Config.DISPLAY_TIMEZONE))
    return moment.astimezone(timezone.utc)


def _deactivate_others(actor, keep_id):
    for other in db.execute(select(PrivacyNoticeVersion).where(PrivacyNoticeVersion.active.is_(True),
                                                               PrivacyNoticeVersion.id != keep_id)).scalars():
        other.active = False
        audit.record('DEACTIVATE_PRIVACY_NOTICE', RESOURCE, other.id)


def publish(actor, payload):
    version = text(payload, 'version', required=True, max_length=32, label='Version')
    effective_at = _effective_at(payload.get('effective_at'))
    active = status_flag(payload)
    if db.execute(select(PrivacyNoticeVersion.id).where(PrivacyNoticeVersion.version == version)).first():
        raise conflict('privacy_version_exists', 'A privacy notice with this version already exists.', 'version')
    max_mb = settings.value(actor.institution_id, 'privacy_notice_max_mb')
    upload = storage.receive(payload.get('file'), max_bytes=max_mb * 1024 * 1024, field='file',
                             allowed=settings.value(actor.institution_id, 'privacy_notice_allowed_mime'),
                             allowed_extensions={'.pdf', '.html', '.htm'})
    try:
        duplicate = db.execute(select(PrivacyNoticeVersion.version)
                               .where(PrivacyNoticeVersion.content_sha256 == upload.sha256)).scalar_one_or_none()
        if duplicate and str(payload.get('confirm_duplicate') or '').lower() not in {'1', 'true', 'on', 'yes'}:
            raise conflict('privacy_notice_duplicate_hash',
                           f'This document is identical to version {duplicate}. Confirm to publish it again.', 'file')
        try:
            stored = storage.put(upload, 'privacy-notices/')
        except storage.StorageUnavailable as exc:
            raise ApiError(503, 'storage_unavailable', 'Object storage is not available right now.') from exc
        row = PrivacyNoticeVersion(version=version, effective_at=effective_at, document_uri=stored.uri,
                                   content_sha256=upload.sha256, active=active, created_at=utc_now())
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise conflict('privacy_version_exists', 'A privacy notice with this version already exists.', 'version')
        audit.record('PUBLISH_PRIVACY_NOTICE', RESOURCE, row.id)
        if active and settings.value(actor.institution_id, 'privacy_notice_single_active'):
            _deactivate_others(actor, row.id)
        db.commit()
    finally:
        upload.discard()
    return _serialize(row)


def set_active(actor, notice_id, active):
    row = get_notice(notice_id)
    if row.active != active:
        row.active = active
        audit.record(f"{'ACTIVATE' if active else 'DEACTIVATE'}_PRIVACY_NOTICE", RESOURCE, row.id)
        if active and settings.value(actor.institution_id, 'privacy_notice_single_active'):
            _deactivate_others(actor, row.id)
        db.commit()
    return _serialize(row)
