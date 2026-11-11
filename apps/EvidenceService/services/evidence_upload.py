"""POST /api/v1/evidence — Phase C (PEF-DS09 §1.3.5 v0.1.8, PEF-DS05 v0.2.0 §1.3.2).

Storage-first flow (no PostgreSQL lock is held during storage I/O):

  A. first read of the session: tenant, assigned operator, capture_mode = vision, allowed state;
  B. stream the temporary file once: size ≤ limit, SHA-256, signature = kind;
  C. server-generated asset UUID and object key, PUT through the internal endpoint;
  D. HeadObject: stored ContentLength must equal the computed size;
  E. ONE transaction: lock work_session (row alone, then joined read), RE-VALIDATE A, INSERT
     media_asset + INSERT access_audit `evidence.upload`, COMMIT.

Any failure after C (HEAD, size mismatch, re-validation, INSERT, audit, commit) deletes the object
(compensation); if that delete also fails, an "orphan candidate" is logged for the future
reconciliation job and the original error is kept. A session that closes while the object is being
uploaded is detected in E (409, nothing persisted, object deleted).

SHA-256 semantics: Garage exposes no verifiable SHA-256 (the ETag is not one), so `sha256` is the
digest of the exact temporary file handed to the PUT; HeadObject verifies the stored size.
No idempotency key exists: two identical uploads create two assets and two objects (no SHA dedupe).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from adapters import audit_adapter
from auth import Principal
from config import Settings
from database import Database
from errors import ApiError
from repositories import evidence_repository as repo
from services.evidence_service import evidence_item
from storage.s3_adapter import S3Storage, StorageOperationError
from uploads import UploadForm, inspect_file, validate_kind

logger = logging.getLogger("evidence.upload")

UPLOAD_STATES = ("in_progress", "correction_required", "awaiting_spd_review")
VISION = "vision"
STORAGE_PROVIDER = "other"   # Garage (S3-compatible) in the current deployment; DS05 §1.3.2 rule 3
BASE_RETENTION = timedelta(days=90)
EXTENDED_RETENTION = timedelta(days=180)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EvidenceUploadService:
    def __init__(self, database: Database, storage: S3Storage, settings: Settings):
        self.database = database
        self.storage = storage
        self.limits = {"jpeg": settings.jpeg_max_bytes, "gif": settings.gif_max_bytes}

    @staticmethod
    def _check_session(context: dict[str, Any] | None, principal: Principal) -> dict[str, Any]:
        # Unknown, other tenant, not assigned or without operation: one indistinguishable 404.
        if (context is None or context["institution_id"] != principal.institution_id
                or context["user_id"] != principal.user_id):
            raise ApiError.not_found("Session not found")
        if context["capture_mode"] != VISION:
            raise ApiError.conflict("Evidence can only be stored for vision sessions",
                                    details={"capture_mode": context["capture_mode"]})
        if context["status_code"] not in UPLOAD_STATES:
            raise ApiError.conflict("Session does not accept evidence in its current state",
                                    details={"status_code": context["status_code"]})
        return context

    def upload(self, principal: Principal, form: UploadForm, correlation_id: str | None) -> dict[str, Any]:
        try:
            session_id = uuid.UUID(form.session_id)
        except ValueError:
            raise ApiError(400, "session_id must be a UUID", details={"field": "session_id"}) from None
        content_type, extension = validate_kind(form.kind)

        # A. First read (no lock): reject early, before touching the file or the storage.
        with self.database.connect() as connection:
            self._check_session(repo.get_upload_context(connection, session_id), principal)

        # B. Stream the temporary file once (size, SHA-256, signature).
        stream = form.file.stream
        inspected = inspect_file(stream, form.kind, self.limits[form.kind])

        # C. Server-generated identity and key; the client filename is never used.
        asset_id = uuid.uuid4()
        created_at = _utc_now()
        object_key = f"{principal.institution_id}/{session_id}/{created_at:%Y}/{asset_id}.{extension}"
        try:
            self.storage.put_file(object_key, stream, inspected.size, content_type)
        except StorageOperationError:
            logger.error("evidence upload: storage put failed asset=%s", asset_id)
            raise ApiError.dependency_unavailable("storage") from None

        try:
            # D. Verify what the storage holds.
            try:
                stored_size = self.storage.head_object_size(object_key)
            except StorageOperationError:
                logger.error("evidence upload: storage head failed asset=%s", asset_id)
                raise ApiError.dependency_unavailable("storage") from None
            if stored_size != inspected.size:
                logger.error("evidence upload: stored size mismatch asset=%s", asset_id)
                raise ApiError.dependency_unavailable("storage")

            # E. Re-validate under lock and persist metadata + audit atomically.
            with self.database.begin() as connection:
                context = self._check_session(repo.lock_upload_context(connection, session_id), principal)
                retention = EXTENDED_RETENTION if context["extended_retention"] else BASE_RETENTION
                row = {
                    "id": asset_id,
                    "session_id": session_id,
                    "gcs_uri": f"s3://{self.storage.bucket}/{object_key}",
                    "bucket": self.storage.bucket,
                    "object_key": object_key,
                    "content_type": content_type,
                    "kind": form.kind,
                    "storage_provider": STORAGE_PROVIDER,
                    "sha256": inspected.sha256,
                    "size_bytes": inspected.size,
                    "retention_until": created_at + retention,
                    "created_at": created_at,
                    "updated_at": created_at,
                }
                repo.insert_media_asset(connection, row)
                audit_adapter.record(
                    connection, action=audit_adapter.ACTION_UPLOAD, actor_user_id=principal.user_id,
                    asset_id=asset_id, institution_id=principal.institution_id, correlation_id=correlation_id,
                    occurred_at=created_at,
                )
        except Exception:
            self._compensate(asset_id, object_key)
            raise

        logger.info("evidence uploaded asset=%s kind=%s size=%d", asset_id, form.kind, inspected.size)
        return evidence_item({**row, "blocked_at": None, "purged_at": None})   # no signed URL on POST

    def _compensate(self, asset_id: uuid.UUID, object_key: str) -> None:
        try:
            self.storage.delete_object(object_key)
            logger.warning("evidence upload rolled back; object deleted asset=%s", asset_id)
        except StorageOperationError:
            # Structured enough for the future reconciliation job; never contents, tokens or URLs.
            logger.error("evidence orphan candidate: bucket=%s object_key=%s asset=%s",
                         self.storage.bucket, object_key, asset_id)
