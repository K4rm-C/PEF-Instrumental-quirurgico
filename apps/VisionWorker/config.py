import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
WEIGHTS_PATH = Path(os.getenv("VISION_WEIGHTS_PATH", str(ROOT / "weights" / "best.pt")))
HOST = os.getenv("VISION_WORKER_HOST", "0.0.0.0")
PORT = int(os.getenv("VISION_WORKER_PORT", "5002"))

# Recall (caja existe) — permisivo para demo externos
BOX_RECALL_CONF = float(os.getenv("VISION_BOX_RECALL_CONF", "0.15"))
# Overlay: familia vs Type (kept_raw / naming principal)
CONF_DISPLAY_THRESHOLD = float(os.getenv("VISION_CONF_DISPLAY_THRESHOLD", "0.70"))
# Overlay naming for reassigned: raw top-1 >= display AND assigned visual >= this
CONF_DISPLAY_REASSIGN = float(os.getenv("VISION_CONF_DISPLAY_REASSIGN", "0.60"))
# Reassign: piso absoluto + cercanía al raw top-1 (scores sigmoid independientes)
REASSIGN_MIN_SCORE = float(os.getenv("VISION_REASSIGN_MIN_SCORE", "0.45"))
REASSIGN_MAX_GAP = float(os.getenv("VISION_REASSIGN_MAX_GAP", "0.15"))
TOPK = max(1, int(os.getenv("VISION_TOPK", "5")))
# Compact audit trail in payloads / metrics (class row after NMS, not 8400 anchors)
TOPK_PERSIST = max(1, min(TOPK, int(os.getenv("VISION_TOPK_PERSIST", "4"))))
VISUAL_WEIGHT = float(os.getenv("VISION_VISUAL_WEIGHT", "0.75"))
CONTEXT_WEIGHT = float(os.getenv("VISION_CONTEXT_WEIGHT", "0.25"))
NMS_IOU = float(os.getenv("VISION_NMS_IOU", "0.45"))
IMGSZ = int(os.getenv("VISION_IMGSZ", "640"))

FRAME_STRIDE = max(1, int(os.getenv("VISION_FRAME_STRIDE", "5")))
# Keep last good family tally/boxes for this many seconds of *video time*
HOLD_SECONDS = float(os.getenv("VISION_HOLD_SECONDS", "2.5"))
# Keep family *name* on overlay longer than count-hold (model jitter)
NAME_HOLD_SECONDS = float(os.getenv("VISION_NAME_HOLD_SECONDS", "4.0"))
# Do not seed/refresh holds with weaker ghosts
HOLD_MIN_SCORE = float(os.getenv("VISION_HOLD_MIN_SCORE", "0.35"))
# When family is confirmed absent (0 live), shrink count hold to this TTL (video seconds).
# Longer than a blink; shorter than full HOLD so removals clear faster than occlusions.
HOLD_ABSENT_SECONDS = float(os.getenv("VISION_HOLD_ABSENT_SECONDS", "1.1"))
# Consecutive processed frames with 0 live before applying absent TTL (hand grace).
HOLD_ABSENT_STREAK = max(1, int(os.getenv("VISION_HOLD_ABSENT_STREAK", "3")))
# Consecutive live assignments required before seeding a new hold (anti 1-frame ghosts).
HOLD_SEED_FRAMES = max(1, int(os.getenv("VISION_HOLD_SEED_FRAMES", "3")))
# If any live box overlaps last hold geometry ≥ this IoU, treat as occlusion (keep long TTL).
HOLD_ZONE_IOU = float(os.getenv("VISION_HOLD_ZONE_IOU", "0.12"))
# Sample tallies into timeline NDJSON (+ PG video_window via backend) every N *processed* frames.
# Default 10 ≈ 50 video frames with stride 5 (~1.7 s @ 30 fps). Set 1 for every inference (demo cost).
COUNT_SAMPLE_EVERY = max(1, int(os.getenv("VISION_COUNT_SAMPLE_EVERY", "10")))
MAX_DET = int(os.getenv("VISION_MAX_DET", "100"))
UPLOAD_DIR = Path(os.getenv("VISION_UPLOAD_DIR", str(ROOT / "uploads")))
METRICS_DIR = Path(os.getenv("VISION_METRICS_DIR", str(UPLOAD_DIR / "metrics")))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
METRICS_DIR.mkdir(parents=True, exist_ok=True)

WORKER_PIPELINE = "matrix_v1"
# Matching solver over the same edge matrix: hungarian (default) | greedy
_ASSIGN = (os.getenv("VISION_ASSIGN_SOLVER", "hungarian") or "hungarian").strip().lower()
ASSIGN_SOLVER = _ASSIGN if _ASSIGN in {"greedy", "hungarian"} else "hungarian"
