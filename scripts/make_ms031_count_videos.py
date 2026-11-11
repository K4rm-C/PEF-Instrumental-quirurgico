"""Build the MS-03.1 test videos (final count rule) from the same source as make_test_tray_videos.py.

Motion-JPEG (.avi): every frame is encoded on its own, so identical frames decode to identical images and
YOLO sees exactly the same picture in the first and in the last frame (no inter-frame compression drift).

- ms031_masked.avi        the tray with the centre scalpel covered, from start to end   (same total)
- ms031_full.avi          the full tray, from start to end                              (same total)
- ms031_one_removed.avi    starts like ms031_masked, ends with one instrument the provisional model detects
                           (lower-left Crile Wood) covered by drape: one instrument less at the end

These are synthetic test videos (not an authentic capture): they exercise the counting rule with the real
model, they do not validate the accuracy of best.pt.

usage: python scripts/make_ms031_count_videos.py <output_dir>     (needs opencv; e.g. inside pef-ms-test/vision)
"""
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'apps/BackendWebFlask/ui_reference_v3/operator/operator-active-session-ai-detection.png'


def write(path: Path, frames):
    height, width = frames[0].shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 30, (width, height))
    writer.set(cv2.VIDEOWRITER_PROP_QUALITY, 100)
    for frame in frames:
        writer.write(frame)
    writer.release()


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    tray = cv2.resize(cv2.imread(str(SOURCE))[262:438, 298:818], None, fx=2.4, fy=2.4,
                      interpolation=cv2.INTER_CUBIC)
    masked = tray.copy()
    x1, y1, x2, y2 = 480, 250, 735, 292                               # centre scalpel (make_test_tray_videos)
    masked[y1:y2, x1:x2] = cv2.resize(tray[200:240, 560:640], (x2 - x1, y2 - y1))
    write(out / 'ms031_masked.avi', [masked] * 45)
    write(out / 'ms031_full.avi', [tray] * 45)
    removed = masked.copy()
    x1, y1, x2, y2 = 185, 282, 495, 422                               # lower-left Crile Wood
    removed[y1:y2, x1:x2] = cv2.resize(tray[200:240, 560:640], (x2 - x1, y2 - y1))
    write(out / 'ms031_one_removed.avi', [masked] * 30 + [removed] * 30)
    print(f'videos written to {out}')


if __name__ == '__main__':
    main(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / '.e2e' / 'videos'))
