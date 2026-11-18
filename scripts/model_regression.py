"""MS-12 (ms-modelos-ia): reproducible regression run of a CANDIDATE vision model against a BASELINE model on a fixed
set of reference videos, with REAL inference, inside the VisionWorker image. Nothing is trained, changed or persisted.

    docker run --rm -v <repo>/apps/VisionWorker:/app:ro -v <repo>/scripts:/scripts:ro -v <videos>:/videos:ro \
        -v <baseline.pt>:/models/baseline.pt:ro -v <candidate.pt>:/models/candidate.pt:ro \
        -e VISION_ALLOW_UNPINNED_BIND=1 -e VISION_UPLOAD_DIR=/tmp/u -e TELEMETRY_SPOOL_DIR=/tmp/s \
        pef-ms-test/vision:latest python /scripts/model_regression.py --reference-set "ms031-v1" \
        --baseline /models/baseline.pt --candidate /models/candidate.pt --out /out/report.json /videos/a.avi /videos/b.avi   (+ -v <out dir>:/out)

For every video, the first and the last frame are analysed by each model through the SAME worker function used in
production (inference.analyze_single_frame: YOLO + class assignment). The report gives, per video and per model, the
total and the per-class counts of both frames, and whether the candidate gives IDENTICAL results to the baseline.

What it is: an agreement check between two models on fixed material (regression detection before a deployment).
What it is NOT: an accuracy measure (there is no ground truth here; accuracy is a recorded evaluation, MS-11 R17).
The JSON is recorded in Administration (Vision models > Evaluations); PEF verifies that both SHA-256 match registered
models and recomputes the agreement and the reference-set hash itself.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, '/app')


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def edge_frames(video: str):
    import cv2
    cap = cv2.VideoCapture(video)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    ok, first = cap.read()
    last, last_index = None, None
    if total > 1:
        cap.set(cv2.CAP_PROP_POS_FRAMES, total - 1)
        ok_last, frame = cap.read()
        if ok_last:
            last, last_index = frame, total - 1
    if last is None:                       # some containers do not seek: read to the end
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        index = -1
        while True:
            ok_frame, frame = cap.read()
            if not ok_frame:
                break
            index += 1
            last, last_index = frame, index
    cap.release()
    if not ok or last is None:
        raise SystemExit(f'cannot read frames of {video}')
    return first, last, last_index


def run_model(weights: str, frames):
    import inference
    from ultralytics import YOLO
    inference._model = YOLO(weights)       # the legacy unpinned slot; the analysis code is the production one
    names = inference._model.names
    state = inference.SessionState(session_id='regression')
    for cid, label in (names.items() if isinstance(names, dict) else enumerate(names)):
        meta = {'family_id': None, 'family_code': str(label), 'family_name': str(label), 'category_name': 'Type'}
        state.class_map[int(cid)] = meta
        state.expected[str(label)] = {**meta, 'expected_quantity': 0}
    out = {}
    for position, (frame, index) in frames.items():
        result = inference.analyze_single_frame(frame, state, frame_index=index, video_t=0.0, position=position)
        counts = {}
        for box in result.get('boxes') or []:
            code = str(box.get('label') or box.get('family_code') or 'unassigned')   # raw class of the model
            counts[code] = counts.get(code, 0) + 1
        out[position] = {'total': len(result.get('boxes') or []), 'counts': dict(sorted(counts.items()))}
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reference-set', required=True)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--out', help='write the JSON report to this file (Ultralytics also prints to stdout)')
    parser.add_argument('videos', nargs='+')
    args = parser.parse_args()
    material = []
    for video in args.videos:
        first, last, last_index = edge_frames(video)
        material.append((video, sha256_file(video), {'first': (first, 0), 'last': (last, last_index)}))
    results = {}
    for role, weights in (('baseline', args.baseline), ('candidate', args.candidate)):
        results[role] = [run_model(weights, frames) for _, _, frames in material]
    items = [{'video': Path(video).name, 'video_sha256': sha, 'baseline': results['baseline'][i],
              'candidate': results['candidate'][i]} for i, (video, sha, _) in enumerate(material)]
    report = json.dumps({
        'format': 'pef-model-regression/1', 'reference_set': args.reference_set,
        'baseline_sha256': sha256_file(args.baseline), 'candidate_sha256': sha256_file(args.candidate),
        'runner': 'scripts/model_regression.py (VisionWorker image, real inference)',
        'executed_at': datetime.now(timezone.utc).isoformat(), 'items': items,
    }, indent=2)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding='utf-8')
    else:
        print(report)


if __name__ == '__main__':
    main()
