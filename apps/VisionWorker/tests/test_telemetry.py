"""DS06 session_telemetry writer against a real MongoDB (dedicated test database, dropped).

Needs PEF_TEST_MONGO_URL; skipped otherwise. Also measures a real (synthetic) video run with a
stub detector, so the samples come from the processing loop, not from fixed numbers.
"""

import json
import os
import uuid
from datetime import timedelta

import cv2
import numpy as np
import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError, WriteError

import inference
import telemetry
from telemetry import TelemetrySink, ensure_schema, utc_now

TEST_DB = 'pef_test_telemetry'


@pytest.fixture
def mongo():
    url = os.environ.get('PEF_TEST_MONGO_URL')
    if not url:
        pytest.skip('MongoDB not configured for tests (PEF_TEST_MONGO_URL)')
    client = MongoClient(url, serverSelectionTimeoutMS=1500, tz_aware=True)
    try:
        client.admin.command('ping')
    except PyMongoError as exc:
        pytest.skip(f'MongoDB not reachable: {type(exc).__name__}')
    client.drop_database(TEST_DB)
    yield url, client[TEST_DB]
    client.drop_database(TEST_DB)


def test_schema_ttl_and_validation(mongo):
    _, db = mongo
    ensure_schema(db)
    ensure_schema(db)
    indexes = db.session_telemetry.index_information()
    assert indexes['session_ts']['key'] == [('session_id', 1), ('ts', 1)]
    assert indexes['ttl_ts']['expireAfterSeconds'] == 7 * 24 * 3600
    with pytest.raises(WriteError):
        db.session_telemetry.insert_one({'session_id': str(uuid.uuid4()), 'ts': utc_now(), 'fps_avg': -1})
    with pytest.raises(WriteError):
        db.session_telemetry.insert_one({'session_id': str(uuid.uuid4()), 'ts': utc_now(), 'frame': 'base64'})


def test_emit_flush_and_idempotent_retry(mongo, tmp_path):
    url, db = mongo
    sink = TelemetrySink(tmp_path, mongo_url=url, db_name=TEST_DB)
    session_id = str(uuid.uuid4())
    sink.emit(session_id=session_id, ts=utc_now(), fps_avg=12.5, latency_ms=48.2, drops=4)
    path = sink.pending()[0]
    copy = path.read_text()
    assert sink.flush() == 0
    path.write_text(copy)                       # the same sample again (lost acknowledgement)
    assert sink.flush() == 0
    docs = list(db.session_telemetry.find({'session_id': session_id}))
    assert len(docs) == 1 and docs[0]['fps_avg'] == 12.5 and docs[0]['drops'] == 4
    assert 'roi_ok' not in docs[0]              # unknown -> absent, never invented


def test_mongo_down_keeps_samples_until_it_is_back(mongo, tmp_path):
    url, db = mongo
    session_id = str(uuid.uuid4())
    down = TelemetrySink(tmp_path, mongo_url='mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=300', db_name=TEST_DB)
    down.emit(session_id=session_id, ts=utc_now(), fps_avg=10.0, latency_ms=50.0, drops=1)
    assert down.flush() == 1 and down.stats['failures'] == 1 and down.stats['last_error']
    back = TelemetrySink(tmp_path, mongo_url=url, db_name=TEST_DB)   # e.g. after a worker restart
    assert back.flush() == 0
    assert db.session_telemetry.count_documents({'session_id': session_id}) == 1


def test_spool_limit_drops_oldest(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry, 'SPOOL_MAX', 3)
    sink = TelemetrySink(tmp_path, mongo_url='mongodb://127.0.0.1:1', db_name=TEST_DB)
    for index in range(5):
        sink.emit(session_id=str(uuid.uuid4()), ts=utc_now(), fps_avg=float(index))
    fps = sorted(json.loads(p.read_text())['fps_avg'] for p in sink.pending())
    assert fps == [2.0, 3.0, 4.0] and sink.stats['dropped'] == 2


def test_video_run_emits_measured_samples(mongo, tmp_path, monkeypatch):
    """Real processing loop on a synthetic video; YOLO replaced by a stub of known cost."""
    url, db = mongo
    sink = TelemetrySink(tmp_path / 'spool', mongo_url=url, db_name=TEST_DB)
    monkeypatch.setattr(inference, 'TELEMETRY', sink)
    monkeypatch.setattr(inference, 'COUNT_SAMPLE_EVERY', 4)

    def stub_run_frame(frame, state, video_t=0.0):
        cv2.GaussianBlur(frame, (31, 31), 0)    # real work so latency is measurable
        state.frames_processed += 1
    monkeypatch.setattr(inference, 'run_frame', stub_run_frame)
    video = str(tmp_path / 'tray.mp4')
    writer = cv2.VideoWriter(video, cv2.VideoWriter_fourcc(*'mp4v'), 30, (320, 240))
    for i in range(50):
        writer.write(np.full((240, 320, 3), i % 255, np.uint8))
    writer.release()
    state = inference.SessionState(session_id=str(uuid.uuid4()))
    inference.process_video_file(video, state)
    # This uniform synthetic clip has no usable tray frame. Telemetry must
    # survive that error as well; it must not manufacture a successful count.
    assert state.status == 'error' and state.error_stage == 'last_frame'
    assert sink.flush() == 0
    docs = list(db.session_telemetry.find({'session_id': state.session_id}).sort('ts', 1))
    stride = inference.FRAME_STRIDE
    analysed = len(range(0, 50, stride))
    assert len(docs) == -(-analysed // 4)                       # one sample per 4 analysed frames (+ rest)
    assert sum(d['drops'] for d in docs) == 50 - analysed      # frames skipped by the stride
    assert all(d['fps_avg'] > 0 and d['latency_ms'] > 0 for d in docs)
    assert docs[-1]['ts'] - docs[0]['ts'] < timedelta(minutes=1)
