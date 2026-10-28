"""PostgreSQL access for clinical evidence (PEF-DS05 v0.2.0 §1.3.2, PEF-DS09 §1.3.5).

Reads for the API, plus the upload context (locked re-validation) and the media_asset INSERT.

Tenant of a clinical asset = media_asset.session_id → work_session.operation_id →
operation.institution_id. Both lookups use INNER joins: an asset without session, or a session
without operation, has no resolvable tenant and never matches. bucket / object_key / gcs_uri are
never used to authorize.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Connection

from repositories.tables import cat_session_status, media_asset, operation, work_session

_METADATA_COLUMNS = (
    media_asset.c.id,
    media_asset.c.session_id,
    media_asset.c.kind,
    media_asset.c.content_type,
    media_asset.c.size_bytes,
    media_asset.c.sha256,
    media_asset.c.storage_provider,
    media_asset.c.retention_until,
    media_asset.c.created_at,
    media_asset.c.blocked_at,
    media_asset.c.purged_at,
)


def get_session_scope(connection: Connection, session_id: uuid.UUID) -> dict[str, Any] | None:
    """The session's assigned operator and tenant; None if unknown or without operation."""
    row = connection.execute(
        select(work_session.c.id, work_session.c.user_id, operation.c.institution_id)
        .select_from(work_session.join(operation, operation.c.id == work_session.c.operation_id))
        .where(work_session.c.id == session_id)
    ).mappings().first()
    return dict(row) if row else None


def list_session_assets(connection: Connection, session_id: uuid.UUID) -> list[dict[str, Any]]:
    """Every asset of the session (blocked and purged included), created_at ASC, id ASC."""
    rows = connection.execute(
        select(*_METADATA_COLUMNS)
        .where(media_asset.c.session_id == session_id)
        .order_by(media_asset.c.created_at, media_asset.c.id)
    ).mappings().all()
    return [dict(row) for row in rows]


def get_clinical_asset(connection: Connection, asset_id: uuid.UUID) -> dict[str, Any] | None:
    """The asset with its storage location and its session scope; None if not clinical/resolvable."""
    row = connection.execute(
        select(
            *_METADATA_COLUMNS,
            media_asset.c.bucket,
            media_asset.c.object_key,
            work_session.c.user_id.label("session_user_id"),
            operation.c.institution_id,
        )
        .select_from(
            media_asset.join(work_session, work_session.c.id == media_asset.c.session_id)
            .join(operation, operation.c.id == work_session.c.operation_id)
        )
        .where(media_asset.c.id == asset_id)
    ).mappings().first()
    return dict(row) if row else None


# --- Upload (Phase C) ---------------------------------------------------------------------------


def _upload_context(connection: Connection, session_id: uuid.UUID) -> dict[str, Any] | None:
    row = connection.execute(
        select(
            work_session.c.id,
            work_session.c.user_id,
            work_session.c.capture_mode,
            work_session.c.extended_retention,
            cat_session_status.c.code.label("status_code"),
            operation.c.institution_id,
        )
        .select_from(
            work_session.join(operation, operation.c.id == work_session.c.operation_id)
            .join(cat_session_status, cat_session_status.c.id == work_session.c.status_id)
        )
        .where(work_session.c.id == session_id)
    ).mappings().first()
    return dict(row) if row else None


def get_upload_context(connection: Connection, session_id: uuid.UUID) -> dict[str, Any] | None:
    """First (unlocked) read: owner, tenant, capture_mode, status, retention flag; None without operation."""
    return _upload_context(connection, session_id)


def lock_upload_context(connection: Connection, session_id: uuid.UUID) -> dict[str, Any] | None:
    """Final re-validation under lock: the work_session row is locked ALONE (FOR UPDATE), then the
    joined context is read. A join inside FOR UPDATE could make a concurrently updated row vanish
    under PostgreSQL's re-check; locking the row first and reading afterwards sees the committed state."""
    locked = connection.execute(
        select(work_session.c.id).where(work_session.c.id == session_id).with_for_update()
    ).first()
    if locked is None:
        return None
    return _upload_context(connection, session_id)


def insert_media_asset(connection: Connection, values: dict[str, Any]) -> None:
    connection.execute(media_asset.insert().values(**values))
