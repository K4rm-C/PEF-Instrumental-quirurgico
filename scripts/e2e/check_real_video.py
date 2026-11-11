"""Run genuine YOLO endpoint frames, without mocks or writes to session/inventory stores.

Usage inside VisionWorker: python /tmp/check_real_video.py /tmp/camino-feliz.mp4
This verifies inference execution, not clinical correctness of detections.
"""
import json
import sys

sys.path.insert(0, '/app')
from inference import SessionState, analyze_first_frame, get_model, process_video_file

model = get_model()
names = model.names
state = SessionState(session_id='isolated-real-video')
state.video_id = 'isolated-real-video'
state.class_map = {int(k): {'family_id': str(k), 'family_code': str(v), 'family_name': str(v)}
                   for k, v in names.items()}
analyze_first_frame(sys.argv[1], state)
first = state.initial_frame
if state.status == 'awaiting_initial':
    process_video_file(sys.argv[1], state)

def summary(frame):
    if not frame:
        return None
    return {k: frame.get(k) for k in ('ok', 'frame_index', 'total', 'quality', 'error', 'skipped_frames')}

print(json.dumps({'status': state.status, 'error': state.last_error,
                  'initial': summary(first), 'final': summary(state.final_frame),
                  'initial_unchanged': first == state.initial_frame,
                  'frames_processed': state.frames_processed}, indent=2))
if state.status != 'ready':
    raise SystemExit(1)
