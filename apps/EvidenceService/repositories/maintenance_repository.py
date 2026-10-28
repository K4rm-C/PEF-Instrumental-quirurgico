"""PostgreSQL access of the internal jobs (Phase D: purge / retention and storage reconciliation).

Scope: clinical evidence rows of the evidence bucket (``bucket`` = S3_BUCKET_EVIDENCE and
``session_id`` IS NOT NULL — PEF-DS05 §1.3.2). DS03/MLOps rows (weights, family photos) are never
purged here (DS05 lifecycle rule 4). Rows are never deleted: purge only sets ``purged_at``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Iterator

from sqlalchemy import and_, collate, or_, select, update
from sqlalchemy.engine import Connection

from repositories.tables import media_asset


def _purge_condition(bucket: str, now: datetime):
    """Purge candidate: clinical, in the evidence bucket, not purged, and purge requested or retention expired.
    ``blocked_at`` alone is NOT a purge condition (blocked ≠ purged)."""
    return and_(
        media_asset.c.bucket == bucket,
        media_asset.c.session_id.is_not(None),
        media_asset.c.purged_at.is_(None),
        or_(
            media_asset.c.purge_requested_at.is_not(None),
            and_(media_asset.c.retention_until.is_not(None), media_asset.c.retention_until <= now),
        ),
    )


def purge_candidates(connection: Connection, bucket: str, now: datetime, after: uuid.UUID | None,
                     limit: int) -> list[dict[str, Any]]:
    """One batch of candidates (plain read, keyset by id): id and reason, no lock."""
    condition = _purge_condition(bucket, now)
    if after is not None:
        condition = and_(condition, media_asset.c.id > after)
    rows = connection.execute(
        select(media_asset.c.id, media_asset.c.purge_requested_at)
        .where(condition)
        .order_by(media_asset.c.id)
        .limit(limit)
    ).mappings().all()
    return [{"id": row["id"], "reason": "purge_requested" if row["purge_requested_at"] else "retention"}
            for row in rows]


def lock_purge_candidate(connection: Connection, asset_id: uuid.UUID, bucket: str,
                         now: datetime) -> dict[str, Any] | None:
    """Locks the row alone (FOR UPDATE SKIP LOCKED) only if it is STILL a candidate.

    None = already purged, no longer a candidate, or being purged by another worker right now.
    """
    row = connection.execute(
        select(media_asset.c.id, media_asset.c.object_key)
        .where(and_(media_asset.c.id == asset_id, _purge_condition(bucket, now)))
        .with_for_update(skip_locked=True)
    ).mappings().first()
    return dict(row) if row else None


def mark_purged(connection: Connection, asset_id: uuid.UUID, now: datetime) -> int:
    """Sets purged_at once (guarded by ``purged_at IS NULL``). Every other column is kept."""
    return connection.execute(
        update(media_asset)
        .where(and_(media_asset.c.id == asset_id, media_asset.c.purged_at.is_(None)))
        .values(purged_at=now, updated_at=now)
    ).rowcount


# --- Reconciliation -----------------------------------------------------------------------------------


def _key_c(column):
    return collate(column, "C")   # byte order = the order in which S3 ListObjectsV2 returns keys


def bucket_rows(connection: Connection, bucket: str, prefix: str, after_key: str | None,
                limit: int) -> list[dict[str, Any]]:
    """One batch of media_asset rows of the bucket (any session), ascending object_key (byte order)."""
    condition = media_asset.c.bucket == bucket
    if prefix:
        condition = and_(condition, media_asset.c.object_key.startswith(prefix, autoescape=True))
    if after_key is not None:
        condition = and_(condition, _key_c(media_asset.c.object_key) > after_key)
    rows = connection.execute(
        select(media_asset.c.id, media_asset.c.object_key, media_asset.c.purged_at)
        .where(condition)
        .order_by(_key_c(media_asset.c.object_key))
        .limit(limit)
    ).mappings().all()
    return [dict(row) for row in rows]


def asset_by_key(connection: Connection, bucket: str, object_key: str) -> dict[str, Any] | None:
    row = connection.execute(
        select(media_asset.c.id, media_asset.c.purged_at)
        .where(and_(media_asset.c.bucket == bucket, media_asset.c.object_key == object_key))
    ).mappings().first()
    return dict(row) if row else None


def iter_bucket_rows(database, bucket: str, prefix: str, batch_size: int) -> Iterator[dict[str, Any]]:
    """Streams the bucket's rows in object_key order, ``batch_size`` rows per query (bounded memory)."""
    after = None
    while True:
        with database.connect() as connection:
            rows = bucket_rows(connection, bucket, prefix, after, batch_size)
        yield from rows
        if len(rows) < batch_size:
            return
        after = rows[-1]["object_key"]
