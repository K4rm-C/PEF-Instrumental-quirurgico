"""Single-video flow: initial count from frame zero, final count from the last valid frame.

A synthetic video encodes the number of instruments of each frame in a marker block; a fake
detector (no YOLO weights) returns that many boxes. Black frames at both ends exercise the
"valid frame" rule. Nothing here needs PostgreSQL or the model.
"""

import io
import time

import cv2
import numpy as np
import pytest

import inference
import main
from inference import SessionState, analyze_first_frame, frame_quality, process_video_file

EXPECTED = {'A': {'family_id': 'f-a', 'family_code': 'A', 'family_name': 'Kelly forceps',
                  'category_name': 'Hemostasis', 'expected_quantity': 3}}
CLASS_MAP = {0: {'family_id': 'f-a', 'family_code': 'A', 'family_name': 'Kelly forceps',
                 'category_name': 'Hemostasis'}}
BLACK = None
# Initial tray (3), procedure (5 and 1 instruments in view), final tray (2), 2 black.
PLAN = [3, 3, 3, 3, 5, 5, 5, 1, 1, 5, 2, 2, BLACK, BLACK]


def _frame(n):
    if n is BLACK:
        return np.zeros((120, 160, 3), dtype=np.uint8)
    rng = np.random.default_rng(7)
    frame = rng.integers(60, 200, size=(120, 160, 3), dtype=np.uint8)
    frame[:40, :40] = 20 + 40 * n   # marker block: number of instruments in this frame
    return frame


def _instruments_in(frame):
    return int(round((float(frame[8:32, 8:32].mean()) - 20) / 40))


def _fake_detect(_model, frame):
    return [{'xyxy': [50.0 + 20 * i, 50.0, 65.0 + 20 * i, 90.0], 'class_id': 0, 'confidence': 0.9,
             'scores_by_class': [0.9, 0.01], 'topk': [{'class_id': 0, 'score': 0.9}]}
            for i in range(_instruments_in(frame))]


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    monkeypatch.setattr(inference, 'get_model', lambda: object())
    monkeypatch.setattr(inference, 'detect_with_class_scores', _fake_detect)


def _write_video(path, plan=PLAN):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10.0, (160, 120))
    assert writer.isOpened()
    for n in plan:
        writer.write(_frame(n))
    writer.release()
    return path


def _state():
    return SessionState(session_id='s-1', expected=dict(EXPECTED), class_map=dict(CLASS_MAP))


def test_frame_quality_rejects_frames_that_cannot_show_a_tray():
    assert frame_quality(None)['reason'] == 'not_decodable'
    assert frame_quality(np.zeros((10, 10, 3), np.uint8))['reason'] == 'too_dark'
    assert frame_quality(np.full((10, 10, 3), 255, np.uint8))['reason'] == 'overexposed'
    assert frame_quality(np.full((10, 10, 3), 128, np.uint8))['reason'] == 'uniform'
    assert frame_quality(_frame(3))['ok'] is True


def test_upload_step_analyses_only_the_first_valid_frame(tmp_path):
    state = _state()
    analyze_first_frame(str(_write_video(tmp_path / 'v.avi')), state)
    assert state.status == 'awaiting_initial'
    first = state.initial_frame
    assert (first['ok'], first['position'], first['frame_index'], first['total']) == (True, 'first', 0, 3)
    assert first['skipped_frames'] == []
    assert first['counts']['A']['detected_quantity'] == 3
    assert first['frame_jpeg_b64']
    # No processing of the video happened.
    assert (state.frames_processed, state.run_id, state.final_frame) == (0, None, None)


def test_final_count_is_the_last_valid_frame_not_an_accumulation(tmp_path):
    path = _write_video(tmp_path / 'v.avi')
    state = _state()
    analyze_first_frame(str(path), state)
    initial = dict(state.initial_frame)
    process_video_file(str(path), state)
    assert state.status == 'ready'
    final = state.final_frame
    # Frames 12-13 are black: the last valid frame is 11, with 2 instruments (max over the video is 5).
    assert (final['ok'], final['position'], final['frame_index'], final['total']) == (True, 'last', 11, 2)
    assert final['run_id'] == state.run_id
    assert [f['frame_index'] for f in final['skipped_frames']] == [13, 12]
    # Intermediate frames never change the initial analysis.
    assert state.initial_frame == initial and state.initial_frame['total'] == 3


def test_video_without_a_valid_frame_reports_an_error_and_no_count(tmp_path):
    state = _state()
    analyze_first_frame(str(_write_video(tmp_path / 'black.avi', [BLACK] * 4)), state)
    assert state.status == 'error' and state.error_stage == 'first_frame'
    assert state.initial_frame['ok'] is False and state.initial_frame['reason'] == 'no_valid_frame'
    assert 'total' not in state.initial_frame


def test_invalid_first_frame_is_not_replaced_by_a_later_tray(tmp_path):
    state = _state()
    analyze_first_frame(str(_write_video(tmp_path / 'fade.avi', [BLACK, 3, 5])), state)
    assert state.status == 'error'
    assert state.initial_frame['ok'] is False
    assert 'total' not in state.initial_frame
    assert state.initial_frame['skipped_frames'][0]['frame_index'] == 0


def test_unreadable_file_is_an_error(tmp_path):
    bad = tmp_path / 'bad.mp4'
    bad.write_bytes(b'not a video')
    state = _state()
    analyze_first_frame(str(bad), state)
    assert state.status == 'error' and state.initial_frame['ok'] is False


def test_incomplete_decode_cannot_claim_the_procedure_finished(tmp_path, monkeypatch):
    path = _write_video(tmp_path / 'short.avi', [3, 2])
    state = _state()
    analyze_first_frame(str(path), state)
    initial = dict(state.initial_frame)
    capture = cv2.VideoCapture

    class TruncatedCapture:
        def __init__(self, filename):
            self.cap = capture(filename)

        def isOpened(self):
            return self.cap.isOpened()

        def get(self, prop):
            return 100 if prop == cv2.CAP_PROP_FRAME_COUNT else self.cap.get(prop)

        def read(self):
            return self.cap.read()

        def release(self):
            self.cap.release()

    monkeypatch.setattr(cv2, 'VideoCapture', TruncatedCapture)
    process_video_file(str(path), state)
    assert state.status == 'error' and state.error_stage == 'processing'
    assert state.final_frame is None and state.initial_frame == initial


def test_last_frame_failure_never_produces_a_final_count(tmp_path):
    path = _write_video(tmp_path / 'v.avi', [3, 3] + [BLACK] * (inference.EDGE_FRAME_SCAN + 1))
    state = _state()
    analyze_first_frame(str(path), state)
    process_video_file(str(path), state)
    assert state.status == 'error' and state.error_stage == 'last_frame'
    assert state.final_frame['ok'] is False


# --- HTTP endpoints ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(main, 'UPLOAD_DIR', tmp_path)
    monkeypatch.setattr(main, '_sessions', {})
    monkeypatch.setattr(main, 'get_model', lambda: object())
    app = main.app.test_client()
    app.post('/sessions/s-1/bind', json={
        'expected': [{'family_id': 'f-a', 'family_code': 'A', 'family_name': 'Kelly forceps',
                      'category_name': 'Hemostasis', 'expected_quantity': 3}],
        'class_map': [{'yolo_class_id': 0, 'family_id': 'f-a', 'family_code': 'A',
                       'family_name': 'Kelly forceps', 'category_name': 'Hemostasis'}]})
    return app


def _wait(client, until, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get('/sessions/s-1/state').get_json()['session']
        if until(state):
            return state
        time.sleep(0.05)
    raise AssertionError(f'worker did not reach the expected state: {state["status"]}')


def _upload(client, data, name='tray.avi'):
    return client.post('/sessions/s-1/video', data={'video': (io.BytesIO(data), name)},
                       content_type='multipart/form-data')


def test_endpoints_upload_then_process_the_same_video(client, tmp_path):
    data = _write_video(tmp_path / 'src.avi').read_bytes()
    response = _upload(client, data)
    assert response.status_code == 202
    video_id = response.get_json()['video_id']
    state = _wait(client, lambda s: s['status'] == 'awaiting_initial')
    assert state['video']['sha256'] and state['video']['size_bytes'] == len(data)
    assert state['initial_frame']['total'] == 3 and state['frames_processed'] == 0

    assert client.post('/sessions/s-1/video/not-this-one/process').status_code == 404
    assert client.post(f'/sessions/s-1/video/{video_id}/process').status_code == 202
    state = _wait(client, lambda s: s['status'] in {'ready', 'error'})
    assert state['status'] == 'ready' and state['final_frame']['total'] == 2
    # Duplicate click after completion: no second run.
    again = client.post(f'/sessions/s-1/video/{video_id}/process')
    assert again.status_code == 200 and again.get_json()['already_processed'] is True
    assert again.get_json()['session']['run_id'] == state['run_id']


def test_endpoints_reject_bad_input(client):
    assert _upload(client, b'x', name='tray.exe').status_code == 400
    assert client.post('/sessions/s-1/video', data={}, content_type='multipart/form-data').status_code == 400
    assert _upload(client, b'', name='tray.mp4').status_code == 400
    assert _upload(client, b'garbage', name='tray.mp4').status_code == 202
    state = _wait(client, lambda s: s['status'] == 'error')
    assert state['error_stage'] == 'first_frame'
    video_id = state['video_id']
    # The full run is refused while the first frame was not analysed.
    assert client.post(f'/sessions/s-1/video/{video_id}/process').status_code == 409
