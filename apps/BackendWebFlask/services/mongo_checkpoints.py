"""PEF-DS06 checkpoint_frame adapter of the portal (DS06 §1.4: "Worker (+ adaptador)").

The portal decides the vision facts (initial / final count, rejected final count, phase change)
in PostgreSQL (DS04); right after each commit it records the DS06 checkpoint of that moment:

    record_checkpoint() --(request thread, no network)--> durable on-disk outbox
    outbox thread: 1. insert the checkpoint in MongoDB (idempotent: _id fixed at enqueue time)
                   2. store the JPEG in EvidenceService (Operator token, kept in memory only)
                   3. attach media_asset_id / gcs_uri / retention_until (set once)

Neither MongoDB nor EvidenceService can block or fail an Operator request: they are only reached
from the outbox thread, with short timeouts and retries. PostgreSQL stays the source of truth;
MongoDB holds visual detail only (no video, no base64 image: the JPEG lives in object storage).

Outbox limits (documented in GUIA_MONGODB.md): one JSON file per checkpoint (+ its JPEG) under
PEF_OUTBOX_DIR; at most OUTBOX_MAX_JOBS pending (new checkpoints are refused and logged beyond
that); a restart keeps every pending checkpoint, but tokens are not persisted, so a JPEG whose
upload had not finished before the restart is discarded (the checkpoint keeps no media).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import CollectionInvalid, DuplicateKeyError, PyMongoError

from services import evidence_client

logger = logging.getLogger("pef.checkpoints")

MONGO_URL = os.getenv("MONGO_URL", "").strip()
MONGO_DB = os.getenv("MONGO_DB", "pef").strip() or "pef"
MONGO_TIMEOUT_MS = int(os.getenv("MONGO_TIMEOUT_MS", "1500"))
OUTBOX_DIR = Path(os.getenv("PEF_OUTBOX_DIR") or (Path(__file__).resolve().parents[1] / "instance" / "mongo_outbox"))
OUTBOX_MAX_JOBS = int(os.getenv("PEF_OUTBOX_MAX_JOBS", "500"))
EVIDENCE_MAX_ATTEMPTS = int(os.getenv("PEF_EVIDENCE_MAX_ATTEMPTS", "5"))

COLLECTION = "checkpoint_frame"
REASONS = ("start", "close", "hourly", "state_change", "discrepancy", "manual_pin")
# DS06 rule 1: at most 30-50 checkpoints per session; interval/hourly ones go first.
SESSION_CAP = 50
EVICTABLE_REASONS = ("hourly",)
BASE_RETENTION = timedelta(days=90)
EXTENDED_RETENTION = timedelta(days=180)
UUID_PATTERN = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

# DS06 §1.3.2, field by field (types, required fields; no extra fields).
CHECKPOINT_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["session_id", "reason", "captured_at", "detections", "retention_until",
                     "session_checkpoint_seq"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "objectId"},
            "session_id": {"bsonType": "string", "pattern": UUID_PATTERN},
            "operation_id": {"bsonType": "string", "pattern": UUID_PATTERN},
            "media_asset_id": {"bsonType": "string", "pattern": UUID_PATTERN},
            "gcs_uri": {"bsonType": "string"},
            "model_version": {"bsonType": "string"},
            "reason": {"enum": list(REASONS)},
            "phase_id": {"bsonType": "string", "pattern": UUID_PATTERN},
            "phase_code": {"bsonType": "string"},
            "burst_frame_count": {"bsonType": "int", "minimum": 1},
            "burst_interval_ms": {"bsonType": "int", "minimum": 0},
            "captured_at": {"bsonType": "date"},
            "detections": {
                "bsonType": "array",
                "items": {
                    "bsonType": "object",
                    "required": ["family_code"],
                    "additionalProperties": False,
                    "properties": {
                        "family_code": {"bsonType": "string"},
                        "confidence": {"bsonType": ["double", "int"], "minimum": 0, "maximum": 1},
                        "bbox": {"bsonType": "array", "minItems": 4, "maxItems": 4,
                                 "items": {"bsonType": ["double", "int"]}},
                    },
                },
            },
            "retention_until": {"bsonType": "date"},
            "session_checkpoint_seq": {"bsonType": "int", "minimum": 1},
        },
    }
}

# DS06 indexes + the unique sequence that keeps session_checkpoint_seq ascending without gaps
# being reused (needed for concurrent writers; DS06 defines the field as "secuencia ascendente").
CHECKPOINT_INDEXES = [
    ([("session_id", ASCENDING), ("captured_at", ASCENDING)], {"name": "session_captured_at"}),
    ([("retention_until", ASCENDING)], {"name": "ttl_retention_until", "expireAfterSeconds": 0}),
    ([("operation_id", ASCENDING)], {"name": "operation_id_sparse", "sparse": True}),
    ([("phase_code", ASCENDING)], {"name": "phase_code_sparse", "sparse": True}),
    ([("session_id", ASCENDING), ("session_checkpoint_seq", ASCENDING)],
     {"name": "session_checkpoint_seq_unique", "unique": True}),
]

_client = None
_client_lock = threading.Lock()
_tokens: dict[str, str] = {}          # job id -> Operator access token (memory only)
_wakeup = threading.Event()
_thread: threading.Thread | None = None
_app = None                           # Flask app, for the PostgreSQL lookup of the stored asset
_schema_ready = False
STATS = {"enqueued": 0, "inserted": 0, "evidence_stored": 0, "evidence_skipped": 0,
         "mongo_failures": 0, "refused": 0, "last_error": None, "last_error_at": None}


def enabled() -> bool:
    return bool(MONGO_URL)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# --- MongoDB connection and schema ------------------------------------------------------------


def database():
    global _client
    with _client_lock:
        if _client is None:
            _client = MongoClient(
                MONGO_URL, tz_aware=True, appname="pef-backendweb",
                serverSelectionTimeoutMS=MONGO_TIMEOUT_MS, connectTimeoutMS=MONGO_TIMEOUT_MS,
                socketTimeoutMS=max(MONGO_TIMEOUT_MS * 3, 3000),
            )
    return _client[MONGO_DB]


def ensure_schema(db=None) -> None:
    """Create checkpoint_frame with its DS06 validator and indexes (idempotent)."""
    global _schema_ready
    db = db if db is not None else database()
    try:
        db.create_collection(COLLECTION, validator=CHECKPOINT_VALIDATOR, validationLevel="strict",
                             validationAction="error")
    except CollectionInvalid:
        db.command("collMod", COLLECTION, validator=CHECKPOINT_VALIDATOR, validationLevel="strict",
                   validationAction="error")
    for keys, options in CHECKPOINT_INDEXES:
        db[COLLECTION].create_index(keys, **options)
    _schema_ready = True


def _note_failure(message: str) -> None:
    STATS["mongo_failures"] += 1
    STATS["last_error"] = message[:300]
    STATS["last_error_at"] = utc_now().isoformat()


# --- Building a DS06 document ---------------------------------------------------------------


def _bbox(box: dict) -> list[float] | None:
    xyxy = box.get("xyxy") or []
    if len(xyxy) != 4:
        return None
    x1, y1, x2, y2 = (float(v) for v in xyxy)
    return [round(x1, 1), round(y1, 1), round(x2 - x1, 1), round(y2 - y1, 1)]   # [x, y, w, h] in pixels


def detections_from_boxes(boxes: list[dict]) -> list[dict]:
    """Detections of the key frame: assigned family, else YOLO's own family (type / out-of-kit)."""
    values = []
    for box in boxes or []:
        if not isinstance(box, dict):
            continue
        code = (box.get("family_code") or (box.get("out_of_kit") or {}).get("family_code")
                or (box.get("raw") or {}).get("family_code"))
        if not code:
            continue
        item = {"family_code": str(code)}
        confidence = box.get("confidence")
        if confidence is not None:
            item["confidence"] = round(min(max(float(confidence), 0.0), 1.0), 4)
        bbox = _bbox(box)
        if bbox is not None:
            item["bbox"] = bbox
        values.append(item)
    return values


def build_document(*, session_id, reason, captured_at, operation_id=None, phase_id=None, phase_code=None,
                   model_version=None, detections=(), extended_retention=False) -> dict:
    if reason not in REASONS:
        raise ValueError(f"unknown checkpoint reason {reason}")
    document = {
        "session_id": str(session_id),
        "reason": reason,
        "captured_at": captured_at,
        "detections": list(detections),
        "retention_until": captured_at + (EXTENDED_RETENTION if extended_retention else BASE_RETENTION),
    }
    optional = {"operation_id": operation_id, "phase_id": phase_id, "phase_code": phase_code,
                "model_version": model_version}
    # Missing (not null) when unknown, so the DS06 sparse indexes skip them.
    document.update({key: str(value) for key, value in optional.items() if value})
    return document


# --- Durable outbox -------------------------------------------------------------------------


def _job_path(job_id: str) -> Path:
    return OUTBOX_DIR / f"{job_id}.json"


def _frame_path(job_id: str) -> Path:
    return OUTBOX_DIR / f"{job_id}.jpg"


def _write_job(job: dict) -> None:
    path = _job_path(job["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _pending_ids() -> list[str]:
    if not OUTBOX_DIR.is_dir():
        return []
    return sorted(path.stem for path in OUTBOX_DIR.glob("*.json"))


def pending_count() -> int:
    return len(_pending_ids())


def record_checkpoint(*, session_id, reason, worker_session=None, operation_id=None, phase_id=None,
                      phase_code=None, extended_retention=False, access_token=None) -> str | None:
    """Queue the DS06 checkpoint of a vision fact. Never raises; returns the checkpoint _id.

    worker_session: VisionWorker state of the frozen run (boxes = detections of the key frame,
    frame_jpeg_b64 = key frame, stored as evidence; it never reaches MongoDB).
    """
    if not enabled():
        return None
    try:
        OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
        if pending_count() >= OUTBOX_MAX_JOBS:
            STATS["refused"] += 1
            logger.error("checkpoint outbox full (%s jobs): %s checkpoint of session %s not recorded",
                         OUTBOX_MAX_JOBS, reason, session_id)
            return None
        worker_session = worker_session or {}
        job_id = str(ObjectId())           # time-ordered; also the MongoDB _id (idempotent insert)
        document = build_document(
            session_id=session_id, reason=reason, captured_at=utc_now(), operation_id=operation_id,
            phase_id=phase_id, phase_code=phase_code, model_version=worker_session.get("model_version"),
            detections=detections_from_boxes(worker_session.get("boxes") or []),
            extended_retention=extended_retention,
        )
        frame = worker_session.get("frame_jpeg_b64")
        has_frame = False
        if frame and evidence_client.configured() and access_token:
            _frame_path(job_id).write_bytes(base64.b64decode(frame))
            has_frame = True
            _tokens[job_id] = access_token
        _write_job({"id": job_id, "document": document, "inserted": False, "has_frame": has_frame,
                    "evidence_attempts": 0, "enqueued_at": utc_now().isoformat()})
        STATS["enqueued"] += 1
        _wakeup.set()
        return job_id
    except Exception:  # noqa: BLE001 - evidence must never break the counting flow
        logger.exception("checkpoint %s of session %s could not be queued", reason, session_id)
        return None


def _decode_document(raw: dict) -> dict:
    document = dict(raw)
    for key in ("captured_at", "retention_until"):
        document[key] = datetime.fromisoformat(document[key])
    return document


def _insert(db, job: dict) -> None:
    """Insert once: a retry after a lost acknowledgement finds the same _id (no duplicate)."""
    document = _decode_document(job["document"])
    document["_id"] = ObjectId(job["id"])
    collection = db[COLLECTION]
    for _ in range(5):
        if collection.count_documents({"_id": document["_id"]}, limit=1):
            return
        last = collection.find_one({"session_id": document["session_id"]}, sort=[("session_checkpoint_seq", DESCENDING)],
                                   projection={"session_checkpoint_seq": 1})
        document["session_checkpoint_seq"] = int((last or {}).get("session_checkpoint_seq") or 0) + 1
        try:
            collection.insert_one(document)
            STATS["inserted"] += 1
            _enforce_cap(collection, document["session_id"])
            return
        except DuplicateKeyError as exc:
            if "_id" in (exc.details or {}).get("keyPattern", {}):
                return
            continue   # another writer took this sequence number: take the next one
    raise PyMongoError("could not allocate session_checkpoint_seq")


def _enforce_cap(collection, session_id: str) -> None:
    excess = collection.count_documents({"session_id": session_id}) - SESSION_CAP
    if excess <= 0:
        return
    old = collection.find({"session_id": session_id, "reason": {"$in": list(EVICTABLE_REASONS)}},
                          sort=[("captured_at", ASCENDING)], projection={"_id": 1}).limit(excess)
    ids = [row["_id"] for row in old]
    if ids:
        collection.delete_many({"_id": {"$in": ids}})
    if excess > len(ids):
        logger.warning("session %s exceeds %s checkpoints; only %s evictable", session_id, SESSION_CAP, len(ids))


def _media_asset_uri(asset_id: str) -> str | None:
    """gcs_uri of the stored asset (PostgreSQL media_asset, written by EvidenceService)."""
    from sqlalchemy import text as sql

    from extensions import db as sql_db
    if _app is None:
        return None
    with _app.app_context():
        try:
            return sql_db.session.execute(sql("SELECT gcs_uri FROM media_asset WHERE id = :id"),
                                          {"id": asset_id}).scalar()
        finally:
            sql_db.session.remove()


def _store_evidence(db, job: dict) -> None:
    """JPEG -> EvidenceService -> attach the reference (set once, never overwritten)."""
    job_id = job["id"]
    token = _tokens.get(job_id)
    frame = _frame_path(job_id)
    if not token or not frame.exists():
        STATS["evidence_skipped"] += 1
        logger.warning("checkpoint %s: evidence not stored (token lost after restart or frame missing)", job_id)
        job["has_frame"] = False
        return
    jpeg = frame.read_bytes()
    session_id = job["document"]["session_id"]
    try:
        import hashlib
        item = evidence_client.find_by_sha256(session_id, hashlib.sha256(jpeg).hexdigest(), token) \
            if job["evidence_attempts"] else None
        item = item or evidence_client.upload_jpeg(session_id, jpeg, token)
    except evidence_client.EvidenceError as exc:
        job["evidence_attempts"] += 1
        if not exc.retryable or job["evidence_attempts"] >= EVIDENCE_MAX_ATTEMPTS:
            STATS["evidence_skipped"] += 1
            logger.error("checkpoint %s: evidence not stored after %s attempts (%s)", job_id,
                         job["evidence_attempts"], exc)
            job["has_frame"] = False
            return
        raise
    attach = {"media_asset_id": item["id"]}
    uri = _media_asset_uri(item["id"])
    if uri:
        attach["gcs_uri"] = uri
    if item.get("retention_until"):
        # DS06: Mongo TTL aligned with the media retention.
        attach["retention_until"] = datetime.fromisoformat(item["retention_until"].replace("Z", "+00:00"))
    attach_evidence(job_id, attach, db=db)
    STATS["evidence_stored"] += 1
    job["has_frame"] = False


def process_pending(db=None) -> int:
    """Process every pending job once; returns how many are still pending (raises nothing)."""
    try:
        db = db if db is not None else database()
        if not _schema_ready:
            ensure_schema(db)
    except PyMongoError as exc:
        _note_failure(f"{type(exc).__name__}: {exc}")
        logger.warning("MongoDB unavailable; %s checkpoint(s) pending: %s", pending_count(), type(exc).__name__)
        return pending_count()
    for job_id in _pending_ids():
        try:
            job = json.loads(_job_path(job_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.error("checkpoint job %s unreadable; moved aside", job_id)
            _job_path(job_id).rename(_job_path(job_id).with_suffix(".bad"))
            continue
        try:
            if not job["inserted"]:
                _insert(db, job)
                job["inserted"] = True
                _write_job(job)
            if job["has_frame"]:
                _store_evidence(db, job)
                _write_job(job)
        except evidence_client.EvidenceError as exc:
            logger.warning("checkpoint %s: EvidenceService retry later (%s)", job_id, exc)
            _write_job(job)
            continue
        except PyMongoError as exc:
            _note_failure(f"{type(exc).__name__}: {exc}")
            logger.warning("MongoDB write failed; %s checkpoint(s) pending: %s", pending_count(), type(exc).__name__)
            return pending_count()
        _job_path(job_id).unlink(missing_ok=True)
        _frame_path(job_id).unlink(missing_ok=True)
        _tokens.pop(job_id, None)
    return pending_count()


def _loop() -> None:
    delay = 1.0
    while True:
        _wakeup.wait(timeout=delay)
        _wakeup.clear()
        left = process_pending()
        delay = 1.0 if not left else min(delay * 2, 60.0)   # exponential backoff while failing


def start_outbox_thread(app) -> None:
    global _thread, _app
    _app = app
    if not enabled() or (_thread is not None and _thread.is_alive()):
        return
    _thread = threading.Thread(target=_loop, name="pef-checkpoint-outbox", daemon=True)
    _thread.start()


# --- Reads, controlled updates and aggregations (portal panels; never the browser) -------------


def attach_evidence(checkpoint_id: str, fields: dict, *, db=None) -> bool:
    """Set the evidence reference once. Historic content (detections, reason, captured_at, seq)
    is never updated; a second attach does nothing."""
    allowed = {key: value for key, value in fields.items() if key in ("media_asset_id", "gcs_uri", "retention_until")}
    db = db if db is not None else database()
    result = db[COLLECTION].update_one({"_id": ObjectId(checkpoint_id), "media_asset_id": {"$exists": False}},
                                       {"$set": allowed})
    return result.modified_count == 1


def extend_retention(session_id, *, db=None) -> int:
    """Align checkpoints with work_session.extended_retention (DS06 rule 5: +180 days)."""
    db = db if db is not None else database()
    updated = 0
    for row in db[COLLECTION].find({"session_id": str(session_id)}, projection={"captured_at": 1, "retention_until": 1}):
        target = row["captured_at"] + EXTENDED_RETENTION
        if row["retention_until"] < target:
            db[COLLECTION].update_one({"_id": row["_id"]}, {"$set": {"retention_until": target}})
            updated += 1
    return updated


def list_checkpoints(session_id, *, reason=None, since=None, until=None, db=None) -> list[dict]:
    query = {"session_id": str(session_id)}
    if reason:
        query["reason"] = reason
    if since or until:
        query["captured_at"] = {key: value for key, value in (("$gte", since), ("$lt", until)) if value}
    db = db if db is not None else database()
    return list(db[COLLECTION].find(query).sort("captured_at", ASCENDING))


def find_by_operation(operation_id, *, db=None) -> list[dict]:
    db = db if db is not None else database()
    return list(db[COLLECTION].find({"operation_id": str(operation_id)}).sort("captured_at", ASCENDING))


def find_by_media_asset(media_asset_id, *, db=None) -> dict | None:
    db = db if db is not None else database()
    return db[COLLECTION].find_one({"media_asset_id": str(media_asset_id)})


def checkpoint_summary(session_id, *, db=None) -> list[dict]:
    """Aggregation: checkpoints per reason with detections and evidence counts."""
    db = db if db is not None else database()
    return list(db[COLLECTION].aggregate([
        {"$match": {"session_id": str(session_id)}},
        {"$group": {"_id": "$reason", "checkpoints": {"$sum": 1},
                    "detections": {"$sum": {"$size": "$detections"}},
                    "with_evidence": {"$sum": {"$cond": [{"$ifNull": ["$media_asset_id", False]}, 1, 0]}},
                    "first": {"$min": "$captured_at"}, "last": {"$max": "$captured_at"}}},
        {"$sort": {"first": 1}},
    ]))


def telemetry_summary(session_id, *, db=None) -> dict | None:
    """Aggregation over session_telemetry (written by VisionWorker)."""
    db = db if db is not None else database()
    rows = list(db["session_telemetry"].aggregate([
        {"$match": {"session_id": str(session_id)}},
        {"$group": {"_id": "$session_id", "samples": {"$sum": 1}, "fps_avg": {"$avg": "$fps_avg"},
                    "fps_min": {"$min": "$fps_avg"}, "latency_ms_avg": {"$avg": "$latency_ms"},
                    "latency_ms_max": {"$max": "$latency_ms"}, "drops": {"$sum": "$drops"},
                    "first": {"$min": "$ts"}, "last": {"$max": "$ts"}}},
    ]))
    return rows[0] if rows else None


def session_panel(session_id) -> dict:
    """Read-only Supervisor panel data; MongoDB down never breaks the page."""
    if not enabled():
        return {"available": False, "reason": "disabled"}
    try:
        db = database()
        return {
            "available": True,
            "checkpoints": list_checkpoints(session_id, db=db),
            "by_reason": checkpoint_summary(session_id, db=db),
            "telemetry": telemetry_summary(session_id, db=db),
            "pending": pending_count(),
        }
    except PyMongoError as exc:
        _note_failure(f"{type(exc).__name__}: {exc}")
        return {"available": False, "reason": type(exc).__name__, "pending": pending_count()}

