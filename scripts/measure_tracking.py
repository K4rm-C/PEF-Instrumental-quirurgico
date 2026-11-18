"""MS-11 (R11): measures the instrument tracker on REAL videos with the REAL provisional model (best.pt), inside the
VisionWorker container. Nothing is trained, changed or persisted; the counting rules are not involved.

    docker run --rm -v <repo>/apps/VisionWorker:/app:ro -v <repo>/scripts:/scripts:ro -v <repo>/.e2e/videos:/videos:ro \
        -v <best.pt>:/models/best.pt:ro -e VISION_WEIGHTS_PATH=/models/best.pt -e VISION_ALLOW_UNPINNED_BIND=1 \
        -e VISION_UPLOAD_DIR=/tmp/u -e TELEMETRY_SPOOL_DIR=/tmp/s pef-ms-test/vision:latest \
        python /scripts/measure_tracking.py /videos/ms031_full.avi /videos/ms031_one_removed.avi ...

For each video (and an occluded copy of the first one, generated here: a black rectangle over one instrument for
~1 s, like a hand) it runs the real pipeline (process_video_file: YOLO + assignment + tracker) twice:
  * `ms11`    — the tracker of tracking.py (Hungarian + constant velocity + recovery + class hysteresis);
  * `iou_only`— the same code reduced to pure IoU association without motion prediction, recovery window or
                class hysteresis (an approximation of the previous IoU+EMA helper, which was never wired in).
It prints one JSON line per (video, variant) with the identity metrics. Ground truth is the number of instruments
visible at the start and at the end of each video (known from the MS-03.1 test material).
"""
import json
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, '/app')
import inference  # noqa: E402
from tracking import InstrumentTracker, TrackerConfig  # noqa: E402

VARIANTS = {
    'ms11': TrackerConfig(),
    'iou_only': TrackerConfig(min_iou=0.3, center_gate=0.0, lost_center_gate=0.0, new_center_gate=0.0,
                              min_hits=1, min_create_conf=0.0, lost_after_s=0.2, forget_after_s=0.2,
                              max_extrapolate_s=0.0, class_switch_margin=0.0, ema_alpha=1.0),
}


def bound_state(name: str) -> "inference.SessionState":
    """A state bound like the portal does: every class of the model is a family of the kit (expected 1 each)."""
    names = inference.get_model(None).names
    state = inference.SessionState(session_id=name)
    for cid, label in (names.items() if isinstance(names, dict) else enumerate(names)):
        meta = {"family_id": None, "family_code": str(label), "family_name": str(label), "category_name": "Type"}
        state.class_map[int(cid)] = meta
        state.expected[str(label)] = {**meta, "expected_quantity": 1}
    return state


def occluded_copy(source: str, target: str) -> str:
    """Black rectangle over the first detected instrument from 40 % to 40 % + ~1 s of the video."""
    cap = cv2.VideoCapture(source)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    ok, first = cap.read()
    state = bound_state('probe')
    box = inference.analyze_single_frame(first, state, frame_index=0, video_t=0.0, position='first')['boxes'][0]['xyxy']
    x1, y1, x2, y2 = [int(v) for v in box]
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    writer = cv2.VideoWriter(target, cv2.VideoWriter_fourcc(*'MJPG'), fps, (first.shape[1], first.shape[0]))
    start, end = int(total * 0.4), int(total * 0.4 + fps)
    index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if start <= index < end:
            cv2.rectangle(frame, (x1 - 10, y1 - 10), (x2 + 10, y2 + 10), (0, 0, 0), -1)
        writer.write(frame)
        index += 1
    writer.release()
    cap.release()
    return target


def long_copy(source: str, target: str, seconds: float = 10.0, occlude=(3.0, 4.0)) -> str:
    """Reproducible longer clip from the SAME real frames (forward / backward, so motion is real), with an occlusion
    of one instrument between `occlude` seconds (black rectangle, like a hand). Ground truth: same objects as the
    source; one of them hidden for one second, then visible again."""
    cap = cv2.VideoCapture(source)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    state = bound_state('probe-long')
    box = inference.analyze_single_frame(frames[0], state, frame_index=0, video_t=0.0, position='first')['boxes'][0]['xyxy']
    x1, y1, x2, y2 = [int(v) for v in box]
    sequence = []
    while len(sequence) < seconds * fps:
        sequence += frames + frames[::-1]
    sequence = sequence[:int(seconds * fps)]
    writer = cv2.VideoWriter(target, cv2.VideoWriter_fourcc(*'MJPG'), fps, (frames[0].shape[1], frames[0].shape[0]))
    for index, frame in enumerate(sequence):
        frame = frame.copy()
        if occlude[0] * fps <= index < occlude[1] * fps:
            cv2.rectangle(frame, (x1 - 10, y1 - 10), (x2 + 10, y2 + 10), (0, 0, 0), -1)
        writer.write(frame)
    writer.release()
    return target


def measure(path: str, variant: str) -> dict:
    config = VARIANTS[variant]
    timings = []
    original_update = InstrumentTracker.update

    def timed(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return original_update(self, *args, **kwargs)
        finally:
            timings.append((time.perf_counter() - started) * 1000)
    InstrumentTracker.update = timed
    inference.new_tracker = lambda state, w, h: InstrumentTracker(config)
    try:
        state = bound_state(f'measure-{variant}')
        inference.analyze_first_frame(path, state)
        first_total = (state.initial_frame or {}).get('total')
        inference.process_video_file(path, state)
        final_total = (state.final_frame or {}).get('total')
        summary = state.tracker.summary(final_frame_total=final_total) if state.tracker else {}
    finally:
        InstrumentTracker.update = original_update
    stats = summary.get('stats', {})
    return {
        'video': Path(path).name, 'variant': variant, 'status': state.status,
        'first_frame_total': first_total, 'final_frame_total': final_total,
        'frames_tracked': summary.get('frames'), 'unique_confirmed_tracks': summary.get('unique_confirmed_tracks'),
        'created_tracks': stats.get('created'), 'max_concurrent_confirmed': stats.get('max_concurrent_confirmed'),
        'active_at_end': summary.get('active_confirmed_tracks'),
        'active_tracks_match_final_frame': summary.get('active_tracks_match_final_frame'),
        'lost': stats.get('lost'), 'recovered': stats.get('recovered'), 'terminated': stats.get('terminated'),
        'class_changes': stats.get('class_changes'), 'low_conf_ignored': stats.get('low_conf_ignored'),
        'mean_track_seconds': summary.get('mean_track_seconds'),
        'tracker_ms_per_frame': round(sum(timings) / len(timings), 3) if timings else None,
        'model_version': state.model_version,
    }


def main():
    videos = list(sys.argv[1:])
    if videos:
        videos.insert(1, occluded_copy(videos[0], '/tmp/occluded_' + Path(videos[0]).stem + '.avi'))
        videos.insert(2, long_copy(videos[0], '/tmp/long10s_occluded_' + Path(videos[0]).stem + '.avi'))
    for path in videos:
        for variant in VARIANTS:
            print('RESULT ' + json.dumps(measure(path, variant)), flush=True)


if __name__ == '__main__':
    main()
