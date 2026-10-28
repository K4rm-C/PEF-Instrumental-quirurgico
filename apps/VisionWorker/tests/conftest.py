"""Worker unit tests run without YOLO weights or video: assignment and hold are pure functions."""

import os
import sys
import tempfile
from pathlib import Path

# config.py creates its upload/metrics folders at import: keep them out of the repository.
os.environ.setdefault('VISION_UPLOAD_DIR', tempfile.mkdtemp(prefix='pef-vision-tests-'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
