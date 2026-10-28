"""Clinical evidence read use cases — Phase B (PEF-DS09 §1.3.5 v0.1.8, PEF-DS05 v0.2.0 §1.3.2).

- GET /sessions/{id}/evidence → list_session_evidence: the SESSION is authorized first, then its
  assets are read (no asset-first lookup, so nothing leaks through side channels). Metadata only:
  signed_url / signed_url_expires_at always null, no audit.
- GET /evidence/{id} → get_evidence: asset + session + operation resolved inside the caller's
  scope. Blocked or purged → 200 metadata with null URL, no signing, no audit. Otherwise a presigned
  GET (public endpoint, configured TTL) is produced in memory, the `evidence.download_url` audit row
  is committed, and only then is the URL returned: if the audit fails, the URL is never delivered.

Scope (same as Session): spd_supervisor (also with station_operator) → every session of the tenant;
station_operator only → sessions with work_session.user_id = caller. Unknown, malformed, other
tenant, not assigned, asset without session or session without operation → one indistinguible 404.
No HeadObject per request: media_asset is the authoritative metadata; a missing blob is a
storage↔DB reconciliation concern (PEF-DS05 §1.3.2 rule 10).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from adapters import audit_adapter
from auth import SUPERVISOR_ROLE_CODE, Principal
from database import Database
from errors import ApiError
from repositories import evidence_repository as repo
from storage.s3_adapter import S3Storage, StorageSigningError

logger = logging.getLogger("evidence.service")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _uuid_or_404(raw: str, message: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        raise ApiError.not_found(message) from None


def evidence_item(row: dict[str, Any], signed_url: str | None = None, expires_at: datetime | None = None) -> dict:
    """DS09 Evidence metadata, fixed field set and order (no bucket, object_key, gcs_uri or endpoints)."""
    return {
        "id": str(row["id"]),
        "session_id": str(row["session_id"]),
        "kind": row["kind"],
        "content_type": row["content_type"],
        "size_bytes": row["size_bytes"],
        "sha256": row["sha256"],
        "storage_provider": row["storage_provider"],
        "retention_until": _iso(row["retention_until"]),
        "created_at": _iso(row["created_at"]),
        "blocked": row["blocked_at"] is not None,
        "purged": row["purged_at"] is not None,
        "signed_url": signed_url,
        "signed_url_expires_at": _iso(expires_at),
    }


class EvidenceService:
    def __init__(self, database: Database, storage: S3Storage, signed_url_ttl_seconds: int):
        self.database = database
        self.storage = storage
        self.ttl = signed_url_ttl_seconds

    @staticmethod
    def _in_scope(principal: Principal, institution_id: uuid.UUID, session_user_id: uuid.UUID) -> bool:
        if institution_id != principal.institution_id:
            return False
        return principal.has_role(SUPERVISOR_ROLE_CODE) or session_user_id == principal.user_id

    # --- GET /sessions/{id}/evidence ------------------------------------------------------

    def list_session_evidence(self, principal: Principal, raw_session_id: str) -> list[dict[str, Any]]:
        session_id = _uuid_or_404(raw_session_id, "Session not found")
        with self.database.connect() as connection:
            session = repo.get_session_scope(connection, session_id)
            if session is None or not self._in_scope(principal, session["institution_id"], session["user_id"]):
                raise ApiError.not_found("Session not found")
            rows = repo.list_session_assets(connection, session_id)
        return [evidence_item(row) for row in rows]

    # --- GET /evidence/{id} ------------------------------------------------------------------

    def get_evidence(self, principal: Principal, raw_asset_id: str, correlation_id: str | None) -> dict[str, Any]:
        asset_id = _uuid_or_404(raw_asset_id, "Evidence not found")
        with self.database.connect() as connection:
            asset = repo.get_clinical_asset(connection, asset_id)
        if asset is None or not self._in_scope(principal, asset["institution_id"], asset["session_user_id"]):
            raise ApiError.not_found("Evidence not found")

        if asset["blocked_at"] is not None or asset["purged_at"] is not None:
            return evidence_item(asset)   # metadata only: no signing, no audit (PEF-DS05 §1.3.2 rule 11)

        generated_at = _utc_now()
        try:
            signed_url = self.storage.generate_presigned_get(asset["bucket"], asset["object_key"], self.ttl)
        except StorageSigningError:
            logger.error("signed url could not be generated for evidence %s", asset_id)
            raise ApiError.dependency_unavailable("storage") from None

        # The URL exists only in memory until its emission is audited; if the audit fails the
        # exception propagates (5xx) and the URL is never sent.
        with self.database.begin() as connection:
            audit_adapter.record(
                connection, action=audit_adapter.ACTION_DOWNLOAD_URL, actor_user_id=principal.user_id,
                asset_id=asset_id, institution_id=principal.institution_id, correlation_id=correlation_id,
                occurred_at=generated_at,
            )
        logger.info("signed url issued for evidence %s ttl=%ss", asset_id, self.ttl)
        return evidence_item(asset, signed_url, generated_at + timedelta(seconds=self.ttl))
