import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
WEIGHTS_PATH = Path(os.getenv("VISION_WEIGHTS_PATH", str(ROOT / "weights" / "best.pt")))
HOST = os.getenv("VISION_WORKER_HOST", "0.0.0.0")
PORT = int(os.getenv("VISION_WORKER_PORT", "5002"))
CONF_DISPLAY_THRESHOLD = float(os.getenv("VISION_CONF_DISPLAY_THRESHOLD", "0.70"))
FRAME_STRIDE = max(1, int(os.getenv("VISION_FRAME_STRIDE", "15")))
MAX_DET = int(os.getenv("VISION_MAX_DET", "100"))
UPLOAD_DIR = Path(os.getenv("VISION_UPLOAD_DIR", str(ROOT / "uploads")))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
