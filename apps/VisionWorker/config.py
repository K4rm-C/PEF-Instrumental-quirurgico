from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
WEIGHTS_PATH = Path(os.getenv("VISION_WEIGHTS_PATH", str(ROOT / "weights" / "best.pt")))
HOST = os.getenv("VISION_WORKER_HOST", "0.0.0.0")
PORT = int(os.getenv("VISION_WORKER_PORT", "5002"))
# HMAC ticket for browser → worker WS (must match Flask VISION_WS_SECRET).
WS_SECRET = (os.getenv("VISION_WS_SECRET") or "pef-demo-vision-ws").strip()
_ws_origins = os.getenv(
    "VISION_WS_ORIGINS",
    "http://127.0.0.1:5000,http://localhost:5000",
)
WS_ORIGINS = {o.strip() for o in _ws_origins.split(",") if o.strip()}

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


def _env_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return None
    return max(1, int(raw))


# CUDA ~60 ms/infer → stride 1 (cada frame). CPU ~750 ms → stride 5 (~6 fps @ 30 fps).
# VISION_FRAME_STRIDE pinea ambos si esta set; VISION_FRAME_STRIDE_CUDA / _CPU ganan si existen.
_stride_pin = _env_int("VISION_FRAME_STRIDE")
FRAME_STRIDE_CUDA = _env_int("VISION_FRAME_STRIDE_CUDA") or _stride_pin or 1
FRAME_STRIDE_CPU = _env_int("VISION_FRAME_STRIDE_CPU") or _stride_pin or 5
FRAME_STRIDE = FRAME_STRIDE_CPU
# Keep last good family tally/boxes for this many seconds of *video time*
HOLD_SECONDS = float(os.getenv("VISION_HOLD_SECONDS", "2.5"))
# Keep family *name* on overlay longer than count-hold (model jitter)
NAME_HOLD_SECONDS = float(os.getenv("VISION_NAME_HOLD_SECONDS", "4.0"))
# Do not seed/refresh holds with weaker ghosts
HOLD_MIN_SCORE = float(os.getenv("VISION_HOLD_MIN_SCORE", "0.35"))
# When family is confirmed absent (0 live), shrink count hold to this TTL (video seconds).
# Longer than a blink; shorter than full HOLD so removals clear faster than occlusions.
HOLD_ABSENT_SECONDS = float(os.getenv("VISION_HOLD_ABSENT_SECONDS", "1.1"))
# Seed / absent grace: N inferences *at CPU stride*, converted to tape seconds via fps.
# CUDA (stride 1) waits the same video time as CPU (stride 5), not 3 raw frames.
HOLD_ABSENT_STREAK = max(1, int(os.getenv("VISION_HOLD_ABSENT_STREAK", "3")))
HOLD_SEED_FRAMES = max(1, int(os.getenv("VISION_HOLD_SEED_FRAMES", "3")))
# If any live box overlaps last hold geometry ≥ this IoU, treat as occlusion
# (delay absent shrink; do not refresh the hold clock).
HOLD_ZONE_IOU = float(os.getenv("VISION_HOLD_ZONE_IOU", "0.15"))
# Sample tallies into timeline NDJSON (+ PG video_window) at most once per this many *video* seconds.
# CUDA folds many inferences into that window; CPU may write almost every inference without exceeding 1 Hz of tape.
COUNT_SAMPLE_INTERVAL_S = max(0.05, float(os.getenv("VISION_COUNT_SAMPLE_INTERVAL_S", "1.0")))
# Deprecated alias (not used for sampling). Kept so old env files still load.
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
