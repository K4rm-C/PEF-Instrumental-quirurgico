"""PEF-DS06 checkpoint_frame adapter against a real MongoDB (dedicated test database, dropped).

Needs PEF_TEST_MONGO_URL (or MONGO_URL when the suite starts); skipped otherwise. The outbox is
exercised with MongoDB up, down (unreachable URL) and back; EvidenceService is faked here (its
real contract is exercised end to end in the E2E script).
"""

import base64
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import PyMongoError, WriteError

from services import evidence_client, mongo_checkpoints as mc

TEST_DB = 'pef_test_checkpoints'
DEAD_URL = 'mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=300'


@pytest.fixture
def mongo(monkeypatch, tmp_path):
    url = os.environ.get('PEF_TEST_MONGO_URL')
    if not url:
        pytest.skip('MongoDB not configured for tests (PEF_TEST_MONGO_URL)')
    client = MongoClient(url, serverSelectionTimeoutMS=1500, tz_aware=True)
    try:
        client.admin.command('ping')
    except PyMongoError as exc:
        pytest.skip(f'MongoDB not reachable: {type(exc).__name__}')
    client.drop_database(TEST_DB)
    monkeypatch.setattr(mc, 'MONGO_URL', url)
    monkeypatch.setattr(mc, 'MONGO_DB', TEST_DB)
    monkeypatch.setattr(mc, 'OUTBOX_DIR', tmp_path / 'outbox')
    monkeypatch.setattr(mc, '_client', None)
    monkeypatch.setattr(mc, '_schema_ready', False)
    monkeypatch.setattr(mc, '_tokens', {})
    monkeypatch.setattr(mc, 'STATS', {key: (0 if key not in ('last_error', 'last_error_at') else None)
                                      for key in mc.STATS})
    yield client[TEST_DB]
    client.drop_database(TEST_DB)


def _worker(boxes=2, frame=True):
    jpeg = b'\xff\xd8\xff\xe0' + b'test-frame' + b'\xff\xd9'
    return {
        'model_version': 'yolo26l-demo',
        'boxes': [{'xyxy': [10.0 + i, 20.0, 60.0 + i, 90.0], 'family_code': 'KELLY', 'confidence': 0.91}
                  for i in range(boxes)] + [{'xyxy': [200, 20, 260, 90], 'family_code': None, 'confidence': 0.7,
                                             'out_of_kit': {'family_code': 'RONGEUR'}}],
        'frame_jpeg_b64': base64.b64encode(jpeg).decode() if frame else None,
    }


def _record(session_id, reason='start', **kwargs):
    return mc.record_checkpoint(session_id=session_id, reason=reason, worker_session=kwargs.pop('worker', _worker()),
                                operation_id=kwargs.pop('operation_id', uuid.uuid4()), phase_code='start', **kwargs)


# --- schema, indexes, validation -----------------------------------------------------------


def test_schema_and_indexes_follow_ds06(mongo):
    mc.ensure_schema(mongo)
    mc.ensure_schema(mongo)  # idempotent
    options = mongo.command('listCollections', filter={'name': 'checkpoint_frame'})['cursor']['firstBatch'][0]['options']
    assert options['validationLevel'] == 'strict' and options['validationAction'] == 'error'
    indexes = mongo.checkpoint_frame.index_information()
    assert indexes['session_captured_at']['key'] == [('session_id', 1), ('captured_at', 1)]
    assert indexes['ttl_retention_until']['expireAfterSeconds'] == 0
    assert indexes['operation_id_sparse']['sparse'] is True and indexes['phase_code_sparse']['sparse'] is True
    assert indexes['session_checkpoint_seq_unique']['unique'] is True


@pytest.mark.parametrize('bad', [
    {'reason': 'random'},
    {'frame_jpeg_b64': 'AAAA'},              # images never live in MongoDB
    {'session_id': 'not-a-uuid'},
    {'detections': [{'confidence': 0.5}]},    # family_code is mandatory
])
def test_validator_rejects_documents_outside_ds06(mongo, bad):
    mc.ensure_schema(mongo)
    now = datetime.now(timezone.utc)
    document = {'session_id': str(uuid.uuid4()), 'reason': 'start', 'captured_at': now, 'detections': [],
                'retention_until': now + timedelta(days=90), 'session_checkpoint_seq': 1, **bad}
    with pytest.raises(WriteError):
        mongo.checkpoint_frame.insert_one(document)


# --- outbox: insert, idempotency, sequence, cap ------------------------------------------------


def test_checkpoint_is_written_from_the_outbox(mongo):
    session_id = uuid.uuid4()
    checkpoint_id = _record(session_id, worker=_worker(frame=False))
    assert mc.pending_count() == 1                     # queued, nothing sent yet
    assert mc.process_pending() == 0
    doc = mongo.checkpoint_frame.find_one({'_id': ObjectId(checkpoint_id)})
    assert doc['session_id'] == str(session_id) and doc['reason'] == 'start' and doc['session_checkpoint_seq'] == 1
    assert [d['family_code'] for d in doc['detections']] == ['KELLY', 'KELLY', 'RONGEUR']
    assert doc['detections'][0]['bbox'] == [10.0, 20.0, 50.0, 70.0]          # [x, y, w, h]
    assert doc['retention_until'] - doc['captured_at'] == timedelta(days=90)
    assert 'media_asset_id' not in doc and 'frame_jpeg_b64' not in doc


def test_retry_after_lost_ack_does_not_duplicate(mongo):
    session_id = uuid.uuid4()
    job_id = _record(session_id, worker=_worker(frame=False))
    job = mc.json.loads(mc._job_path(job_id).read_text())
    mc.ensure_schema(mongo)
    mc._insert(mongo, job)            # inserted, but the job file still says inserted = False
    assert mc.process_pending() == 0  # the retry finds the same _id
    assert mongo.checkpoint_frame.count_documents({'session_id': str(session_id)}) == 1


def test_sequence_is_ascending_per_session(mongo):
    session_id = uuid.uuid4()
    for reason in ('start', 'state_change', 'discrepancy', 'close'):
        _record(session_id, reason=reason, worker=_worker(frame=False))
    mc.process_pending()
    rows = list(mongo.checkpoint_frame.find({'session_id': str(session_id)}).sort('captured_at', 1))
    assert [(r['reason'], r['session_checkpoint_seq']) for r in rows] == [
        ('start', 1), ('state_change', 2), ('discrepancy', 3), ('close', 4)]


def test_extended_retention_is_180_days(mongo):
    _record(uuid.uuid4(), worker=_worker(frame=False), extended_retention=True)
    mc.process_pending()
    doc = mongo.checkpoint_frame.find_one()
    assert doc['retention_until'] - doc['captured_at'] == timedelta(days=180)


def test_outbox_limit_refuses_and_counts(mongo, monkeypatch):
    monkeypatch.setattr(mc, 'OUTBOX_MAX_JOBS', 2)
    session_id = uuid.uuid4()
    assert _record(session_id, worker=_worker(frame=False)) and _record(session_id, worker=_worker(frame=False))
    assert _record(session_id, worker=_worker(frame=False)) is None
    assert mc.STATS['refused'] == 1


# --- MongoDB down, then back -----------------------------------------------------------------


def test_mongo_down_keeps_pending_and_recovers(mongo, monkeypatch):
    url = mc.MONGO_URL
    monkeypatch.setattr(mc, 'MONGO_URL', DEAD_URL)
    monkeypatch.setattr(mc, '_client', None)
    session_id = uuid.uuid4()
    started = datetime.now(timezone.utc)
    _record(session_id, worker=_worker(frame=False))
    _record(session_id, reason='close', worker=_worker(frame=False))
    assert (datetime.now(timezone.utc) - started).total_seconds() < 1   # queuing never waits for MongoDB
    assert mc.process_pending() == 2
    assert mc.STATS['mongo_failures'] >= 1 and mc.STATS['last_error']
    monkeypatch.setattr(mc, 'MONGO_URL', url)
    monkeypatch.setattr(mc, '_client', None)
    assert mc.process_pending() == 0
    assert mongo.checkpoint_frame.count_documents({'session_id': str(session_id)}) == 2


def test_pending_jobs_survive_a_restart(mongo, monkeypatch):
    session_id = uuid.uuid4()
    _record(session_id, worker=_worker(frame=False))
    monkeypatch.setattr(mc, '_client', None)        # "new process": only the outbox folder remains
    monkeypatch.setattr(mc, '_schema_ready', False)
    assert mc.process_pending() == 0
    assert mongo.checkpoint_frame.count_documents({'session_id': str(session_id)}) == 1


# --- evidence reference: set once, after the upload ------------------------------------------


def test_evidence_reference_is_attached_once(mongo, monkeypatch):
    asset_id = str(uuid.uuid4())
    retention = (datetime.now(timezone.utc) + timedelta(days=90)).replace(microsecond=0)
    monkeypatch.setattr(evidence_client, 'EVIDENCE_SERVICE_URL', 'http://evidence.invalid')
    monkeypatch.setattr(evidence_client, 'upload_jpeg', lambda sid, jpeg, token: {
        'id': asset_id, 'sha256': 'x', 'retention_until': retention.isoformat().replace('+00:00', 'Z')})
    monkeypatch.setattr(mc, '_media_asset_uri', lambda aid: f's3://evidence/inst/session/2026/{aid}.jpg')
    checkpoint_id = _record(uuid.uuid4(), access_token='operator-token')
    assert mc.process_pending() == 0
    doc = mongo.checkpoint_frame.find_one({'_id': ObjectId(checkpoint_id)})
    assert doc['media_asset_id'] == asset_id and doc['gcs_uri'].endswith(f'{asset_id}.jpg')
    assert doc['retention_until'] == retention                    # aligned with the media retention
    assert mc.attach_evidence(checkpoint_id, {'media_asset_id': str(uuid.uuid4())}, db=mongo) is False
    assert mc.attach_evidence(checkpoint_id, {'detections': []}, db=mongo) is False   # not an allowed field
    assert mongo.checkpoint_frame.find_one({'_id': ObjectId(checkpoint_id)})['media_asset_id'] == asset_id
    assert mc.find_by_media_asset(asset_id, db=mongo)['_id'] == ObjectId(checkpoint_id)
    assert not list((mc.OUTBOX_DIR).glob('*.jpg'))                # key frame removed from the outbox


def test_evidence_down_keeps_the_checkpoint_and_retries(mongo, monkeypatch):
    calls = []
    monkeypatch.setattr(evidence_client, 'EVIDENCE_SERVICE_URL', 'http://evidence.invalid')

    def down(sid, jpeg, token):
        calls.append(1)
        raise evidence_client.EvidenceError('down', retryable=True)
    monkeypatch.setattr(evidence_client, 'upload_jpeg', down)
    monkeypatch.setattr(evidence_client, 'find_by_sha256', lambda sid, sha, token: None)
    checkpoint_id = _record(uuid.uuid4(), access_token='operator-token')
    assert mc.process_pending() == 1                               # checkpoint stored, evidence pending
    assert mongo.checkpoint_frame.count_documents({'_id': ObjectId(checkpoint_id)}) == 1
    for _ in range(mc.EVIDENCE_MAX_ATTEMPTS):
        mc.process_pending()
    assert mc.pending_count() == 0 and len(calls) == mc.EVIDENCE_MAX_ATTEMPTS
    assert mc.STATS['evidence_skipped'] == 1
    assert 'media_asset_id' not in mongo.checkpoint_frame.find_one({'_id': ObjectId(checkpoint_id)})


def test_lost_token_after_restart_drops_only_the_media(mongo, monkeypatch):
    monkeypatch.setattr(evidence_client, 'EVIDENCE_SERVICE_URL', 'http://evidence.invalid')
    checkpoint_id = _record(uuid.uuid4(), access_token='operator-token')
    mc._tokens.clear()                                             # tokens are never written to disk
    assert mc.process_pending() == 0
    assert mongo.checkpoint_frame.count_documents({'_id': ObjectId(checkpoint_id)}) == 1
    assert mc.STATS['evidence_skipped'] == 1


# --- queries, filters, aggregation, controlled update -----------------------------------------


def test_queries_filters_aggregation_and_retention_update(mongo):
    session_id, operation_id = uuid.uuid4(), uuid.uuid4()
    for reason in ('start', 'discrepancy', 'close'):
        _record(session_id, reason=reason, worker=_worker(frame=False), operation_id=operation_id)
    _record(uuid.uuid4(), worker=_worker(frame=False))            # another session
    mc.process_pending()
    assert len(mc.list_checkpoints(session_id, db=mongo)) == 3
    assert [d['reason'] for d in mc.list_checkpoints(session_id, reason='close', db=mongo)] == ['close']
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    assert mc.list_checkpoints(session_id, since=future, db=mongo) == []
    assert len(mc.find_by_operation(operation_id, db=mongo)) == 3
    summary = {row['_id']: row for row in mc.checkpoint_summary(session_id, db=mongo)}
    assert set(summary) == {'start', 'discrepancy', 'close'} and summary['close']['detections'] == 3
    plan = mongo.checkpoint_frame.find({'session_id': str(session_id)}).sort('captured_at', 1).explain()
    assert 'session_captured_at' in str(plan['queryPlanner']['winningPlan'])
    assert mc.extend_retention(session_id, db=mongo) == 3
    assert mc.extend_retention(session_id, db=mongo) == 0
    for doc in mc.list_checkpoints(session_id, db=mongo):
        assert doc['retention_until'] - doc['captured_at'] == timedelta(days=180)
        assert [d['family_code'] for d in doc['detections']] == ['KELLY', 'KELLY', 'RONGEUR']   # untouched


def test_disabled_without_mongo_url(monkeypatch, tmp_path):
    monkeypatch.setattr(mc, 'MONGO_URL', '')
    monkeypatch.setattr(mc, 'OUTBOX_DIR', tmp_path)
    assert mc.record_checkpoint(session_id=uuid.uuid4(), reason='start') is None
    assert not list(tmp_path.iterdir())
