"""Read access to files stored by Administration (it_admin only, through the domain routes).

Only objects referenced by Administration records are served: a family example image, a privacy
notice document. The caller never names a bucket or key. The web layer sends them with
`X-Content-Type-Options: nosniff`; documents (HTML/PDF) as attachments under a sandbox CSP.
"""
from .database import db
from .errors import ApiError, not_found
from .models import FamilyExample, MediaAsset, PrivacyNoticeVersion
from .common import parse_uuid
from . import storage


def _read(bucket, key):
    try:
        return storage.get(bucket, key)
    except storage.StorageUnavailable as exc:
        raise ApiError(503, 'storage_unavailable', 'Object storage is not available right now.') from exc


def family_example_image(family_id, example_id):
    example = db.get(FamilyExample, parse_uuid(example_id))
    if example is None or str(example.family_id) != str(parse_uuid(family_id)) or example.media_asset_id is None:
        raise not_found('example_not_found', 'Example not found.')
    asset = db.get(MediaAsset, example.media_asset_id)
    if asset is None:
        raise not_found('example_not_found', 'Example not found.')
    return {'content_type': asset.content_type or 'application/octet-stream', 'data': _read(asset.bucket, asset.object_key),
            'filename': asset.object_key.rsplit('/', 1)[-1], 'attachment': False}


def parse_uri(uri):
    """(bucket, key) of a document_uri written by Administration; None for external/legacy URIs."""
    for prefix, bucket in (('local://admin-storage/', 'local'),):
        if uri.startswith(prefix):
            return bucket, uri[len(prefix):]
    if uri.startswith('s3://'):
        bucket, _, key = uri[len('s3://'):].partition('/')
        return (bucket, key) if bucket and key else None
    return None


def privacy_document(notice_id):
    notice = db.get(PrivacyNoticeVersion, parse_uuid(notice_id))
    if notice is None:
        raise not_found('privacy_notice_not_found', 'Privacy notice not found.')
    location = parse_uri(notice.document_uri or '')
    if location is None:
        raise not_found('document_not_stored', 'This document is not stored by the portal.')
    content_type = 'application/pdf' if location[1].endswith('.pdf') else 'text/html'
    return {'content_type': content_type, 'data': _read(*location), 'filename': f'privacy-notice-{notice.version}'
            + ('.pdf' if content_type == 'application/pdf' else '.html'), 'attachment': True}
