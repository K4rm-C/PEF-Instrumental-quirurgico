from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Prefer repo-root .env when present.
_ROOT = Path(__file__).resolve().parent
_REPO = _ROOT.parents[1]
load_dotenv(_REPO / ".env")
load_dotenv()

ROOT = _ROOT
CLIPS_DIR = Path(os.getenv("EVIDENCE_CLIPS_DIR", str(ROOT / "clips")))
CLIPS_DIR.mkdir(parents=True, exist_ok=True)

REDIS_URL = os.getenv("REDIS_URL", "redis://:MedRedis@localhost:6379/0")
TRIGGER_LIST_KEY = os.getenv("EVIDENCE_TRIGGER_LIST", "pef:evidence:triggers")
FRAME_LIST_PREFIX = os.getenv("EVIDENCE_FRAME_LIST_PREFIX", "pef:evidence:frames:")

RING_SECONDS = float(os.getenv("EVIDENCE_RING_SECONDS", "6"))
PRE_SECONDS = float(os.getenv("EVIDENCE_PRE_SECONDS", "4"))
POST_SECONDS = float(os.getenv("EVIDENCE_POST_SECONDS", "3"))
HOURLY_SECONDS = float(os.getenv("EVIDENCE_HOURLY_SECONDS", "3600"))
# No frames within this gap ⇒ idle (do not fire hourly); catch-up on resume.
IDLE_GAP_SECONDS = float(os.getenv("EVIDENCE_IDLE_GAP_SECONDS", "30"))
FRAME_POLL_MS = int(os.getenv("EVIDENCE_FRAME_POLL_MS", "50"))
TRIGGER_BLOCK_S = float(os.getenv("EVIDENCE_TRIGGER_BLOCK_S", "0.5"))

# Local video always; Garage/Mongo/PG when enabled (PoC default: on).
GARAGE_ENABLED = str(os.getenv("EVIDENCE_GARAGE_ENABLED", "1")).strip().lower() in {
    "1", "true", "yes", "on",
}
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://127.0.0.1:3900")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "MedAdmin3")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "StorageKeySecret027")
S3_REGION = os.getenv("S3_REGION", "garage")
S3_BUCKET_EVIDENCE = os.getenv("S3_BUCKET_EVIDENCE", "evidence")
S3_FORCE_PATH_STYLE = str(os.getenv("S3_FORCE_PATH_STYLE", "true")).lower() in {
    "1", "true", "yes", "on",
}
PRESIGN_EXPIRES = int(os.getenv("EVIDENCE_PRESIGN_EXPIRES", "3600"))

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://MedAdmin:MedMin027@localhost:5433/InstruMed",
)
MONGO_URL = os.getenv(
    "MONGO_URL",
    "mongodb://MedAdmin2:MedMongo027@localhost:27017/?authSource=admin",
)
MONGO_DB = os.getenv("EVIDENCE_MONGO_DB", "pef_evidence")
RETENTION_DAYS = int(os.getenv("EVIDENCE_RETENTION_DAYS", "90"))

# After successful Garage+Mongo: delete local media (sidecar kept as audit). Fallback local if upload fails.
PURGE_LOCAL_ON_UPLOAD = str(os.getenv("EVIDENCE_PURGE_LOCAL_ON_UPLOAD", "1")).strip().lower() in {
    "1", "true", "yes", "on",
}
UPLOAD_RETRY_LIST_KEY = os.getenv("EVIDENCE_UPLOAD_RETRY_LIST", "pef:evidence:upload_retry")
UPLOAD_RETRY_MAX_ATTEMPTS = max(1, int(os.getenv("EVIDENCE_UPLOAD_RETRY_MAX", "8")))
UPLOAD_RETRY_POLL_S = float(os.getenv("EVIDENCE_UPLOAD_RETRY_POLL_S", "15"))

# Hourly ratio refresh (2C): VisionWorker public state.
VISION_WORKER_URL = os.getenv("VISION_WORKER_URL", "http://127.0.0.1:5002").rstrip("/")
VISION_WORKER_TIMEOUT = float(os.getenv("EVIDENCE_VISION_TIMEOUT", "2.5"))

# Optional local ffmpeg from tools/ensure-ffmpeg.ps1
_FFMPEG_LOCAL = _REPO / "tools" / "ffmpeg" / "bin"
if _FFMPEG_LOCAL.is_dir():
    _path = os.environ.get("PATH", "")
    _bin = str(_FFMPEG_LOCAL)
    if _bin not in _path.split(os.pathsep):
        os.environ["PATH"] = _bin + os.pathsep + _path
