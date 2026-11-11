"""RF-AD-08 pending check: compare the class names the portal reads from a REAL best.pt (safe reader,
never executes the file) with the names Ultralytics uses at inference time.

    python scripts/verify_weights_names.py path/to/best.pt
    # Optional, only for a TRUSTED file and inside the VisionWorker environment (ultralytics installed):
    python scripts/verify_weights_names.py path/to/best.pt --ultralytics

Prints SHA-256 (must equal the checksum shown in Administration after the upload), the names / nc /
architecture / size read by admin_local.yolo_weights and, with --ultralytics, YOLO(path).names. Exit 1
if they differ. Never modifies the file.
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps' / 'BackendWebFlask'))
from services.admin_local import yolo_weights  # noqa: E402


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = Path(sys.argv[1])
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    print(f'file:   {path} ({path.stat().st_size} bytes)')
    print(f'sha256: {digest.hexdigest()}')
    try:
        parsed = yolo_weights.read_checkpoint(str(path))
    except yolo_weights.WeightsParseError as exc:
        print(f'safe reader: cannot read class names ({exc}) -> Administration will ask for names.yaml / JSON')
        sys.exit(1)
    print(f"safe reader: nc={parsed['nc']} architecture={parsed['architecture']} size={parsed['model_size']}")
    for class_id, name in parsed['names'].items():
        print(f'  {class_id}: {name}')
    if '--ultralytics' in sys.argv:
        from ultralytics import YOLO  # executes the checkpoint: trusted files only
        reference = {int(k): str(v) for k, v in YOLO(str(path)).names.items()}
        same = reference == parsed['names']
        print(f"ultralytics names {'MATCH' if same else 'DIFFER'}: {reference if not same else ''}")
        sys.exit(0 if same else 1)


if __name__ == '__main__':
    main()
