"""Build the two test tray videos used by e2e_mongo_ds06.py (PEF-DS06 technical demo).

Source: the "Original Capture" photo of the Operator UI reference screen
(apps/BackendWebFlask/ui_reference_v3/operator/operator-active-session-ai-detection.png).
tray_full.mp4 = the tray; tray_masked.mp4 = the same tray with the centre scalpel covered by drape.

These videos demonstrate persistence (PostgreSQL -> MongoDB -> EvidenceService) with the real
model; they are NOT an authentic capture and do not validate the accuracy of best.pt.

usage: python scripts/make_test_tray_videos.py <output_dir>     (needs opencv + numpy)
"""
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'apps/BackendWebFlask/ui_reference_v3/operator/operator-active-session-ai-detection.png'


def write(path: Path, frame, frames=60):
    height, width = frame.shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 30, (width, height))
    for _ in range(frames):
        writer.write(frame)
    writer.release()


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    tray = cv2.resize(cv2.imread(str(SOURCE))[262:438, 298:818], None, fx=2.4, fy=2.4,
                      interpolation=cv2.INTER_CUBIC)
    masked = tray.copy()
    x1, y1, x2, y2 = 480, 250, 735, 292                               # centre scalpel
    masked[y1:y2, x1:x2] = cv2.resize(tray[200:240, 560:640], (x2 - x1, y2 - y1))   # plain drape
    write(out / 'tray_full.mp4', tray)
    write(out / 'tray_masked.mp4', masked)
    print(f'videos written to {out}')


if __name__ == '__main__':
    main(Path(sys.argv[1] if len(sys.argv) > 1 else 'test_videos'))
