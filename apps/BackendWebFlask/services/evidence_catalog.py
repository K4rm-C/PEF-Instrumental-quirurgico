"""List / resolve evidence clips: Mongo+PG first; local only as upload-failure fallback."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CLIPS = _REPO_ROOT / "apps" / "EvidenceWorker" / "clips"
CLIPS_DIR = Path(os.getenv("EVIDENCE_CLIPS_DIR", str(_DEFAULT_CLIPS)))
MONGO_URL = os.getenv(
    "MONGO_URL",
    "mongodb://MedAdmin2:MedMongo027@localhost:27017/?authSource=admin",
)
MONGO_DB = os.getenv("EVIDENCE_MONGO_DB", "pef_evidence")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://127.0.0.1:3900")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "MedAdmin3")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "StorageKeySecret027")
S3_REGION = os.getenv("S3_REGION", "garage")
S3_BUCKET_EVIDENCE = os.getenv("S3_BUCKET_EVIDENCE", "evidence")
S3_FORCE_PATH_STYLE = str(os.getenv("S3_FORCE_PATH_STYLE", "true")).lower() in {
    "1", "true", "yes", "on",
}
PRESIGN_EXPIRES = int(os.getenv("EVIDENCE_PRESIGN_EXPIRES", "3600"))


def _reason_rank(reason: str) -> int:
    order = {"start": 0, "phase": 1, "hourly": 2, "close": 3, "state_change": 1}
    return order.get((reason or "").lower(), 9)


def _ui_reason(reason: str | None) -> str:
    r = (reason or "unknown").lower()
    if r == "state_change":
        return "phase"
    return r


def _reason_label(ui_reason: str | None) -> str:
    """Localized label for evidence clip reason (current request locale)."""
    from i18n import localize_db_label

    code = _ui_reason(ui_reason)
    return localize_db_label("checkpoint_reason", code, code.replace("_", " "))


def _phase_label(phase_code: str | None, phase_name: str | None) -> str | None:
    """Re-localize phase by code so drawer follows viewer locale, not capture locale."""
    if not phase_code and not phase_name:
        return None
    from i18n import localize_db_label

    return localize_db_label("operation_phase", phase_code, phase_name)


def _presign(object_key: str | None, bucket: str | None = None) -> str | None:
    if not object_key:
        return None
    try:
        import boto3
        from botocore.client import Config

        client = boto3.client(
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
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket or S3_BUCKET_EVIDENCE, "Key": object_key},
            ExpiresIn=PRESIGN_EXPIRES,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("presign skipped: %s", exc)
        return None


def _object_key_from_uri(gcs_uri: str | None) -> str | None:
    if not gcs_uri:
        return None
    # s3://bucket/key...
    if gcs_uri.startswith("s3://"):
        rest = gcs_uri[5:]
        parts = rest.split("/", 1)
        if len(parts) == 2:
            return parts[1]
    return None


def _signed_fresh(doc: dict[str, Any]) -> str | None:
    signed = doc.get("signed_url")
    exp = doc.get("signed_url_expires_at")
    if signed and exp is not None:
        try:
            if hasattr(exp, "timestamp"):
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp.timestamp() > datetime.now(timezone.utc).timestamp() + 60:
                    return signed
            elif isinstance(exp, str):
                dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                if dt.timestamp() > datetime.now(timezone.utc).timestamp() + 60:
                    return signed
        except Exception:
            pass
    key = doc.get("object_key") or _object_key_from_uri(doc.get("gcs_uri"))
    return _presign(key, doc.get("bucket")) or signed


def _load_local_clips(session_id: str) -> list[dict[str, Any]]:
    root = CLIPS_DIR / str(session_id)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for side in sorted(root.glob("*.json")):
        try:
            data = json.loads(side.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        clip_id = str(data.get("clip_id") or side.stem)
        media = Path(data.get("local_path") or "")
        if not media.is_file():
            guess = side.with_suffix("")
            if guess.is_file():
                media = guess
                data["local_path"] = str(media)
        kind = data.get("kind") or (
            "jpeg" if media.suffix.lower() in {".jpg", ".jpeg"}
            else "webm" if media.suffix.lower() == ".webm"
            else "mp4"
        )
        garage = data.get("garage") or {}
        garage_ok = garage.get("garage_ok")
        object_key = garage.get("object_key") or _object_key_from_uri(
            garage.get("gcs_uri") or data.get("gcs_uri")
        )
        # Refresh signed URL from Garage when media was purged (sidecar is the fallback catalog).
        signed = None
        if object_key or garage.get("signed_url") or garage.get("gcs_uri"):
            signed = _signed_fresh({
                "signed_url": garage.get("signed_url"),
                "signed_url_expires_at": garage.get("signed_url_expires_at"),
                "object_key": object_key,
                "bucket": garage.get("bucket"),
                "gcs_uri": garage.get("gcs_uri") or data.get("gcs_uri"),
            })
        reason = _ui_reason(data.get("reason"))
        phase_code = data.get("phase_code")
        phase_name = data.get("phase_name")
        out.append({
            "clip_id": clip_id,
            "session_id": str(session_id),
            "reason": reason,
            "reason_label": _reason_label(reason),
            "phase_id": data.get("phase_id"),
            "phase_code": phase_code,
            "phase_name": _phase_label(phase_code, phase_name),
            "ratio_label": data.get("ratio_label"),
            "detected_total": data.get("detected_total"),
            "expected_total": data.get("expected_total"),
            "occurred_at": data.get("occurred_at"),
            "frame_count": data.get("frame_count"),
            "kind": kind,
            "source": "local",
            "has_file": media.is_file(),
            "upload_status": data.get("upload_status") or (
                "uploaded" if garage_ok else "pending_upload" if garage_ok is False else "local_only"
            ),
            "signed_url": signed or garage.get("signed_url"),
            "gcs_uri": garage.get("gcs_uri") or data.get("gcs_uri"),
            "object_key": object_key,
            "media_asset_id": garage.get("media_asset_id"),
            "garage_ok": garage_ok,
            "garage_error": garage.get("error"),
            "filename": media.name if media.name else side.name,
            "local_purged": bool(data.get("local_purged")),
        })
    return out


def _load_mongo_clips(session_id: str) -> list[dict[str, Any]]:
    try:
        from pymongo import MongoClient
    except ImportError:
        return []
    try:
        client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=1500)
        db = client[MONGO_DB]
        docs = list(
            db.checkpoint_frame.find({"session_id": str(session_id)}).sort("captured_at", 1)
        )
        client.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("mongo evidence list skipped: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for doc in docs:
        reason = _ui_reason(doc.get("reason_ui") or doc.get("reason"))
        captured = doc.get("captured_at")
        occurred = doc.get("occurred_at")
        if not occurred:
            occurred = captured.isoformat() if hasattr(captured, "isoformat") else captured
        kind = doc.get("kind") or "mp4"
        signed = _signed_fresh(doc)
        clip_id = str(doc.get("clip_id") or doc.get("_id"))
        phase_code = doc.get("phase_code")
        phase_name = doc.get("phase_name")
        out.append({
            "clip_id": clip_id,
            "mongo_id": str(doc.get("_id")),
            "session_id": str(session_id),
            "reason": reason,
            "reason_label": _reason_label(reason),
            "phase_id": doc.get("phase_id"),
            "phase_code": phase_code,
            "phase_name": _phase_label(phase_code, phase_name),
            "ratio_label": doc.get("ratio_label"),
            "detected_total": doc.get("detected_total"),
            "expected_total": doc.get("expected_total"),
            "occurred_at": occurred,
            "frame_count": doc.get("burst_frame_count"),
            "kind": kind,
            "source": "mongo",
            "has_file": False,
            "upload_status": doc.get("upload_status") or "uploaded",
            "signed_url": signed,
            "gcs_uri": doc.get("gcs_uri"),
            "object_key": doc.get("object_key") or _object_key_from_uri(doc.get("gcs_uri")),
            "media_asset_id": doc.get("media_asset_id"),
            "garage_ok": True,
            "garage_error": None,
            "filename": doc.get("filename"),
            "model_version": doc.get("model_version"),
            "operation_id": doc.get("operation_id"),
            "local_purged": True,
        })
    return out


def list_session_clips(session_id: UUID | str) -> list[dict[str, Any]]:
    """Mongo/BD first; merge local pending/fallback clips that are not yet uploaded."""
    sid = str(session_id)
    mongo = _load_mongo_clips(sid)
    local = _load_local_clips(sid)
    by_key: dict[str, dict[str, Any]] = {}

    for row in mongo:
        key = str(row.get("clip_id") or row.get("mongo_id") or len(by_key))
        by_key[key] = row

    for row in local:
        key = str(row.get("clip_id") or row.get("filename") or len(by_key))
        # Match mongo by clip_id / filename / gcs_uri
        matched = None
        for mkey, mrow in by_key.items():
            if mrow.get("source") != "mongo" and mrow.get("source") != "mongo+local":
                continue
            if row.get("clip_id") and row["clip_id"] == mrow.get("clip_id"):
                matched = mkey
                break
            if row.get("gcs_uri") and row.get("gcs_uri") == mrow.get("gcs_uri"):
                matched = mkey
                break
            if row.get("filename") and row.get("filename") == mrow.get("filename"):
                matched = mkey
                break
        if matched:
            merged = dict(by_key[matched])
            # Prefer BD signed URL; keep local file as playback fallback.
            if row.get("has_file"):
                merged["has_file"] = True
            if not merged.get("signed_url") and row.get("signed_url"):
                merged["signed_url"] = row["signed_url"]
            for field in (
                "phase_name", "phase_code", "ratio_label",
                "detected_total", "expected_total", "occurred_at",
            ):
                if not merged.get(field) and row.get(field) is not None:
                    merged[field] = row[field]
            merged["source"] = "mongo+local"
            by_key[matched] = merged
            continue
        # Local / sidecar fallback: keep pending uploads AND purged sidecars with
        # Garage signed_url when Mongo is empty/unavailable (Flask missing pymongo, etc.).
        if row.get("upload_status") == "uploaded" and not row.get("has_file"):
            if not (row.get("signed_url") or row.get("gcs_uri") or row.get("object_key")):
                continue
            row = dict(row)
            row["source"] = "sidecar+garage"
        by_key[key] = row

    rows = list(by_key.values())
    rows.sort(key=lambda r: (
        r.get("occurred_at") or "",
        _reason_rank(str(r.get("reason") or "")),
    ))
    return rows


def resolve_local_media(session_id: UUID | str, clip_id: str) -> Path | None:
    sid = str(session_id)
    root = CLIPS_DIR / sid
    if not root.is_dir():
        return None
    for side in root.glob("*.json"):
        try:
            data = json.loads(side.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if str(data.get("clip_id")) != str(clip_id):
            continue
        media = Path(data.get("local_path") or "")
        if media.is_file():
            return media
        guess = side.with_suffix("")
        if guess.is_file():
            return guess
    for path in root.iterdir():
        if path.suffix.lower() in {".mp4", ".jpg", ".jpeg", ".webm", ".mkv"} and clip_id in path.name:
            return path
    return None


def resolve_play_url(
    row: dict[str, Any],
    *,
    local_file_url: str | None,
) -> str | None:
    """Prefer Garage signed URL; fall back to auth-gated local file route."""
    if row.get("signed_url"):
        return row["signed_url"]
    if row.get("has_file") and local_file_url:
        return local_file_url
    key = row.get("object_key") or _object_key_from_uri(row.get("gcs_uri"))
    fresh = _presign(key)
    if fresh:
        return fresh
    return None
