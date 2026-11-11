"""MS-12 (recovery after disconnections): an EvidenceService outage longer than a few seconds no longer loses the
checkpoint image; the upload is retried for a time window and stored when the service is back. Real MongoDB (test
database of test_mongo_checkpoints); EvidenceService answers simulated at its client boundary."""
import uuid
from datetime import timedelta

from bson import ObjectId

from services import evidence_client, mongo_checkpoints as mc

from .test_mongo_checkpoints import _record, mongo  # noqa: F401  (fixture)


def test_a_long_outage_within_the_window_is_recovered(mongo, monkeypatch):
    monkeypatch.setattr(evidence_client, 'EVIDENCE_SERVICE_URL', 'http://evidence.invalid')
    state = {'down': True, 'calls': 0}
    asset_id = str(uuid.uuid4())

    def upload(sid, jpeg, token):
        state['calls'] += 1
        if state['down']:
            raise evidence_client.EvidenceError('down', retryable=True)
        return {'id': asset_id}
    monkeypatch.setattr(evidence_client, 'upload_jpeg', upload)
    monkeypatch.setattr(evidence_client, 'find_by_sha256', lambda sid, sha, token: None)
    checkpoint_id = _record(uuid.uuid4(), access_token='operator-token')
    for _ in range(8):                                 # more than the 5 attempts that used to drop the image
        assert mc.process_pending() == 1
    state['down'] = False                              # EvidenceService is back
    assert mc.process_pending() == 0 and state['calls'] == 9
    assert mongo.checkpoint_frame.find_one({'_id': ObjectId(checkpoint_id)})['media_asset_id'] == asset_id


def test_after_the_window_the_image_is_given_up_and_logged(mongo, monkeypatch):
    monkeypatch.setattr(evidence_client, 'EVIDENCE_SERVICE_URL', 'http://evidence.invalid')

    def down(sid, jpeg, token):
        raise evidence_client.EvidenceError('down', retryable=True)
    monkeypatch.setattr(evidence_client, 'upload_jpeg', down)
    monkeypatch.setattr(evidence_client, 'find_by_sha256', lambda sid, sha, token: None)
    checkpoint_id = _record(uuid.uuid4(), access_token='operator-token')
    assert mc.process_pending() == 1
    later = mc.utc_now() + timedelta(seconds=mc.EVIDENCE_RETRY_SECONDS + 1)
    monkeypatch.setattr(mc, 'utc_now', lambda: later)
    assert mc.process_pending() == 0 and mc.STATS['evidence_skipped'] == 1
    document = mongo.checkpoint_frame.find_one({'_id': ObjectId(checkpoint_id)})
    assert document is not None and 'media_asset_id' not in document       # the checkpoint itself is kept
