"""PEF-DS06 session_telemetry writer (runtime metrics of real video processing; not evidence).

Samples are measured by process_video_file (one per COUNT_SAMPLE_EVERY processed frames and one at
the end of the run) and handed to emit(): the inference loop never waits for MongoDB.

    emit() -> durable spool (one JSON line file per sample, _id fixed) -> flush thread -> MongoDB

- MongoDB down: samples stay in the spool; the thread retries with exponential backoff (max 60 s)
  and logs the failure; insert_many(ordered=False) + fixed _id makes a retry after a lost
  acknowledgement a no-op (duplicate keys are ignored).
- Limit: at most TELEMETRY_SPOOL_MAX pending samples; beyond that the OLDEST are dropped (and
  counted): telemetry is diagnostic, never legal evidence (DS06 §1.3.3).
- Restart: pending samples are on disk and are sent when MongoDB is reachable again.
- No MONGO_URL: telemetry is disabled (nothing is spooled).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId

logger = logging.getLogger("pef.telemetry")

MONGO_URL = os.getenv("MONGO_URL", "").strip()
MONGO_DB = os.getenv("MONGO_DB", "pef").strip() or "pef"
MONGO_TIMEOUT_MS = int(os.getenv("MONGO_TIMEOUT_MS", "1500"))
COLLECTION = "session_telemetry"
TTL_SECONDS = 7 * 24 * 3600            # DS06: "TTL 7 días"
SPOOL_MAX = int(os.getenv("TELEMETRY_SPOOL_MAX", "5000"))
UUID_PATTERN = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["session_id", "ts"],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "objectId"},
            "session_id": {"bsonType": "string", "pattern": UUID_PATTERN},
            "ts": {"bsonType": "date"},
            "fps_avg": {"bsonType": ["double", "int"], "minimum": 0},
            "latency_ms": {"bsonType": ["double", "int"], "minimum": 0},
            "drops": {"bsonType": "int", "minimum": 0},
            "roi_ok": {"bsonType": "bool"},
        },
    }
}
INDEXES = [
    ([("session_id", 1), ("ts", 1)], {"name": "session_ts"}),
    ([("ts", 1)], {"name": "ttl_ts", "expireAfterSeconds": TTL_SECONDS}),
]


def ensure_schema(db) -> None:
    from pymongo.errors import CollectionInvalid
    try:
        db.create_collection(COLLECTION, validator=VALIDATOR, validationLevel="strict", validationAction="error")
    except CollectionInvalid:
        db.command("collMod", COLLECTION, validator=VALIDATOR, validationLevel="strict", validationAction="error")
    for keys, options in INDEXES:
        db[COLLECTION].create_index(keys, **options)


class TelemetrySink:
    def __init__(self, spool_dir: Path, mongo_url: str = MONGO_URL, db_name: str = MONGO_DB):
        self.spool_dir = Path(spool_dir)
        self.mongo_url = mongo_url
        self.db_name = db_name
        self._client = None
        self._schema_ready = False
        self._wakeup = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.stats = {"emitted": 0, "inserted": 0, "dropped": 0, "failures": 0, "last_error": None}

    @property
    def enabled(self) -> bool:
        return bool(self.mongo_url)

    def _db(self):
        if self._client is None:
            from pymongo import MongoClient
            self._client = MongoClient(
                self.mongo_url, tz_aware=True, appname="pef-visionworker",
                serverSelectionTimeoutMS=MONGO_TIMEOUT_MS, connectTimeoutMS=MONGO_TIMEOUT_MS,
                socketTimeoutMS=max(MONGO_TIMEOUT_MS * 3, 3000),
            )
        return self._client[self.db_name]

    def pending(self) -> list[Path]:
        return sorted(self.spool_dir.glob("*.json")) if self.spool_dir.is_dir() else []

    def emit(self, *, session_id: str, ts: datetime, fps_avg=None, latency_ms=None, drops=None, roi_ok=None) -> None:
        """Queue one measured sample (never raises, never blocks on MongoDB)."""
        if not self.enabled:
            return
        sample = {"session_id": str(session_id), "ts": ts.astimezone(timezone.utc).isoformat()}
        for key, value in (("fps_avg", fps_avg), ("latency_ms", latency_ms), ("drops", drops), ("roi_ok", roi_ok)):
            if value is not None:   # unknown -> absent (DS06 nullable), never invented
                sample[key] = value
        try:
            with self._lock:
                self.spool_dir.mkdir(parents=True, exist_ok=True)
                pending = self.pending()
                for old in pending[: max(0, len(pending) - SPOOL_MAX + 1)]:
                    old.unlink(missing_ok=True)
                    self.stats["dropped"] += 1
                oid = str(ObjectId())
                tmp = self.spool_dir / f"{oid}.tmp"
                tmp.write_text(json.dumps(sample), encoding="utf-8")
                os.replace(tmp, self.spool_dir / f"{oid}.json")
            self.stats["emitted"] += 1
            self._wakeup.set()
        except OSError:
            logger.exception("telemetry sample of session %s not spooled", session_id)

    def flush(self) -> int:
        """Send every spooled sample; returns how many remain pending."""
        from pymongo.errors import BulkWriteError, PyMongoError
        files = self.pending()
        if not files:
            return 0
        try:
            db = self._db()
            if not self._schema_ready:
                ensure_schema(db)
                self._schema_ready = True
            docs, sent = [], []
            for path in files[:500]:
                try:
                    sample = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    path.rename(path.with_suffix(".bad"))
                    continue
                sample["_id"] = ObjectId(path.stem)
                sample["ts"] = datetime.fromisoformat(sample["ts"])
                docs.append(sample)
                sent.append(path)
            if docs:
                try:
                    db[COLLECTION].insert_many(docs, ordered=False)
                    self.stats["inserted"] += len(docs)
                except BulkWriteError as exc:
                    errors = exc.details.get("writeErrors", [])
                    if any(error.get("code") != 11000 for error in errors):
                        raise
                    self.stats["inserted"] += exc.details.get("nInserted", 0)
            for path in sent:
                path.unlink(missing_ok=True)
        except PyMongoError as exc:
            self.stats["failures"] += 1
            self.stats["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
            logger.warning("MongoDB unavailable; %s telemetry sample(s) pending: %s", len(files), type(exc).__name__)
        return len(self.pending())

    def _loop(self) -> None:
        delay = 2.0
        while True:
            self._wakeup.wait(timeout=delay)
            self._wakeup.clear()
            left = self.flush()
            delay = 2.0 if not left else min(delay * 2, 60.0)

    def start(self) -> None:
        if self.enabled and (self._thread is None or not self._thread.is_alive()):
            self._thread = threading.Thread(target=self._loop, name="pef-telemetry", daemon=True)
            self._thread.start()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_spool() -> Path:
    from config import UPLOAD_DIR
    return Path(os.getenv("TELEMETRY_SPOOL_DIR") or (UPLOAD_DIR / "telemetry_spool"))


TELEMETRY = TelemetrySink(_default_spool())
