"""Garage / PG / Mongo registration. Soft-fail; local clip kept for retry."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from config import (
    DATABASE_URL,
    GARAGE_ENABLED,
    MONGO_DB,
    MONGO_URL,
    PRESIGN_EXPIRES,
    RETENTION_DAYS,
    S3_ACCESS_KEY,
    S3_BUCKET_EVIDENCE,
    S3_ENDPOINT,
    S3_FORCE_PATH_STYLE,
    S3_REGION,
    S3_SECRET_KEY,
)

logger = logging.getLogger(__name__)


def _psycopg_dsn(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url.split("postgresql+psycopg://", 1)[1]
    return url


def _s3_client():
    import boto3
    from botocore.client import Config

    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if S3_FORCE_PATH_STYLE else "auto"},
        ),
    )


def _object_key(session_id: str, filename: str) -> str:
    year = datetime.now(timezone.utc).strftime("%Y")
    month = datetime.now(timezone.utc).strftime("%m")
    return f"{session_id}/{year}/{month}/{filename}"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _mongo_reason(reason: str) -> str:
    if reason == "phase":
        return "state_change"
    return reason


def _ui_reason(reason: str) -> str:
    """Portal-facing reason (phase stays phase; Mongo stores state_change)."""
    r = (reason or "").strip().lower()
    if r == "state_change":
        return "phase"
    return r or "unknown"


def _lookup_session_context(session_id: str) -> dict[str, Any]:
    """Best-effort PG: operation_id + model_version for DS06 denormalization."""
    out: dict[str, Any] = {}
    try:
        import psycopg

        dsn = _psycopg_dsn(DATABASE_URL)
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ws.operation_id::text,
                           ym.version_tag
                    FROM work_session ws
                    LEFT JOIN yolo_model ym ON ym.id = ws.yolo_model_id
                    WHERE ws.id = %s::uuid
                    """,
                    (session_id,),
                )
                row = cur.fetchone()
                if row:
                    out["operation_id"] = row[0]
                    out["model_version"] = row[1]
    except Exception as exc:  # noqa: BLE001
        logger.debug("session context lookup skipped: %s", exc)
    return out


def _media_kind_content_type(meta: dict[str, Any], local_path: Path) -> tuple[str, str]:
    suffix = local_path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "jpeg", "image/jpeg"
    if suffix == ".webm":
        return "webm", "video/webm"
    kind = str(meta.get("kind") or "mp4")
    if kind == "webm":
        return "webm", "video/webm"
    if kind == "jpeg":
        return "jpeg", "image/jpeg"
    return "mp4", "video/mp4"


def publish_clip_to_garage(meta: dict[str, Any]) -> dict[str, Any]:
    """Upload local file → media_asset → checkpoint_frame (full DS06 fields). Soft-fail each step."""
    result: dict[str, Any] = {
        "garage_ok": False,
        "media_asset_id": None,
        "gcs_uri": None,
        "signed_url": None,
        "mongo_ok": False,
        "pg_ok": False,
        "error": None,
        "clip_id": meta.get("clip_id"),
    }
    if not GARAGE_ENABLED:
        result["error"] = "garage_disabled"
        return result

    local_path = Path(meta.get("local_path") or meta.get("path") or "")
    if not local_path.is_file():
        result["error"] = "local_missing"
        return result

    kind, content_type = _media_kind_content_type(meta, local_path)
    session_id = str(meta["session_id"])
    clip_id = str(meta.get("clip_id") or uuid.uuid4().hex)
    object_key = _object_key(session_id, local_path.name)
    gcs_uri = f"s3://{S3_BUCKET_EVIDENCE}/{object_key}"
    asset_id = uuid.uuid4()
    retention_until = datetime.now(timezone.utc) + timedelta(days=RETENTION_DAYS)
    sha = _sha256_file(local_path)
    size = int(meta.get("size_bytes") or local_path.stat().st_size)
    ctx = _lookup_session_context(session_id)
    ui_reason = _ui_reason(str(meta.get("reason") or "state_change"))
    mongo_reason = _mongo_reason(ui_reason)

    try:
        client = _s3_client()
        client.upload_file(
            str(local_path),
            S3_BUCKET_EVIDENCE,
            object_key,
            ExtraArgs={"ContentType": content_type},
        )
        try:
            signed = client.generate_presigned_url(
                "get_object",
                Params={"Bucket": S3_BUCKET_EVIDENCE, "Key": object_key},
                ExpiresIn=PRESIGN_EXPIRES,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("presign failed: %s", exc)
            signed = None
        result["garage_ok"] = True
        result["gcs_uri"] = gcs_uri
        result["signed_url"] = signed
        result["object_key"] = object_key
        result["bucket"] = S3_BUCKET_EVIDENCE
        result["kind"] = kind
        result["content_type"] = content_type
    except Exception as exc:  # noqa: BLE001
        logger.warning("S3 upload failed (local clip kept): %s", exc)
        result["error"] = f"s3:{exc}"
        return result

    try:
        import psycopg

        dsn = _psycopg_dsn(DATABASE_URL)
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO media_asset (
                        id, gcs_uri, bucket, object_key, content_type, kind,
                        storage_provider, sha256, size_bytes, retention_until,
                        created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        'minio', %s, %s, %s,
                        now(), now()
                    )
                    ON CONFLICT (bucket, object_key) DO UPDATE
                      SET gcs_uri = EXCLUDED.gcs_uri,
                          content_type = EXCLUDED.content_type,
                          kind = EXCLUDED.kind,
                          sha256 = EXCLUDED.sha256,
                          size_bytes = EXCLUDED.size_bytes,
                          retention_until = EXCLUDED.retention_until,
                          updated_at = now()
                    RETURNING id
                    """,
                    (
                        str(asset_id),
                        gcs_uri,
                        S3_BUCKET_EVIDENCE,
                        object_key,
                        content_type,
                        kind,
                        sha,
                        size,
                        retention_until,
                    ),
                )
                row = cur.fetchone()
                if row:
                    asset_id = row[0]
            conn.commit()
        result["media_asset_id"] = str(asset_id)
        result["pg_ok"] = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("media_asset insert failed (S3+local kept): %s", exc)
        result["error"] = f"pg:{exc}"

    try:
        from pymongo import MongoClient

        client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
        db = client[MONGO_DB]
        captured = datetime.now(timezone.utc)
        occurred = meta.get("occurred_at")
        if isinstance(occurred, str) and occurred:
            try:
                captured = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
            except ValueError:
                pass
        doc = {
            "clip_id": clip_id,
            "session_id": session_id,
            "operation_id": ctx.get("operation_id") or meta.get("operation_id"),
            "media_asset_id": result.get("media_asset_id"),
            "gcs_uri": gcs_uri,
            "object_key": object_key,
            "bucket": S3_BUCKET_EVIDENCE,
            "model_version": ctx.get("model_version") or meta.get("model_version"),
            "reason": mongo_reason,
            "reason_ui": ui_reason,
            "phase_id": str(meta["phase_id"]) if meta.get("phase_id") else None,
            "phase_code": meta.get("phase_code"),
            "phase_name": meta.get("phase_name"),
            "ratio_label": meta.get("ratio_label"),
            "detected_total": meta.get("detected_total"),
            "expected_total": meta.get("expected_total"),
            "burst_frame_count": int(meta.get("frame_count") or 0),
            "burst_interval_ms": meta.get("burst_interval_ms"),
            "kind": kind,
            "content_type": content_type,
            "encoder": meta.get("encoder"),
            "size_bytes": size,
            "sha256": sha,
            "captured_at": captured,
            "occurred_at": meta.get("occurred_at") or captured.isoformat(),
            "detections": meta.get("detections") if isinstance(meta.get("detections"), list) else [],
            "retention_until": retention_until,
            "signed_url": result.get("signed_url"),
            "signed_url_expires_at": (
                datetime.now(timezone.utc) + timedelta(seconds=PRESIGN_EXPIRES)
            ),
            "filename": local_path.name,
            "local_path": str(local_path),
            "upload_status": "uploaded",
        }
        existing = db.checkpoint_frame.find_one({"clip_id": clip_id, "session_id": session_id})
        if existing:
            db.checkpoint_frame.update_one(
                {"_id": existing["_id"]},
                {"$set": {k: v for k, v in doc.items() if k != "session_checkpoint_seq"}},
            )
            result["mongo_id"] = str(existing["_id"])
        else:
            seq = db.checkpoint_frame.count_documents({"session_id": session_id}) + 1
            doc["session_checkpoint_seq"] = seq
            inserted = db.checkpoint_frame.insert_one(doc)
            result["mongo_id"] = str(inserted.inserted_id)
        result["mongo_ok"] = True
        client.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("mongo checkpoint failed (local+S3 kept): %s", exc)
        if not result.get("error"):
            result["error"] = f"mongo:{exc}"

    # Fully OK only when object store + mongo are registered (PG preferred but soft).
    result["garage_ok"] = bool(result.get("gcs_uri") and result.get("mongo_ok"))
    if result["garage_ok"] and not result.get("pg_ok") and not result.get("error"):
        result["error"] = "pg_soft_fail"
    return result


def endpoint_host() -> str:
    try:
        return urlparse(S3_ENDPOINT).hostname or "localhost"
    except Exception:
        return "localhost"
