"""MS-02 / P1 (+ the portal side of P2): each vision session keeps the YOLO model it started with.

Real PostgreSQL inside a rolled-back transaction (fixtures of test_multitenant). The VisionWorker is replaced by
a fake HTTP endpoint that checks the HMAC signature and answers with the SHA-256 it "loaded"; real inference is
covered by apps/VisionWorker/tests/test_model_store.py and scripts/e2e_ms02_model_pin.py.
"""
import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from services import model_pinning, rf_session, vision_bridge
from services.vision_bridge import VisionBridgeError

from .test_multitenant import _run, pg, world  # noqa: F401 - fixtures


def _model(pg, tag, *, active, bucket='local', with_asset=True, families=()):
    model_id, sha = uuid.uuid4(), hashlib.sha256(tag.encode()).hexdigest()
    asset_id = None
    if with_asset:
        asset_id = uuid.uuid4()
        _run(pg, "INSERT INTO media_asset (id, gcs_uri, bucket, object_key, content_type, kind, storage_provider, size_bytes) "
                 "VALUES (:id, :u, :b, :k, 'application/octet-stream', 'other', 'other', 10)",
             id=asset_id, u=f's3://{bucket}/models/{sha[:32]}.pt', b=bucket, k=f'models/{sha[:32]}.pt')
    _run(pg, "INSERT INTO yolo_model (id, version_tag, media_asset_id, checksum, active, class_names) "
             "VALUES (:id, :v, :a, :c, :act, CAST(:n AS jsonb))",
         id=model_id, v=f'ms02-{tag}', a=asset_id, c=sha, act=active, n=json.dumps({"0": "Kelly"}))
    for family in families:
        _run(pg, 'INSERT INTO model_class (model_id, family_id, yolo_class_id) VALUES (:m, :f, 0)', m=model_id, f=family)
    return SimpleNamespace(id=model_id, sha=sha, version=f'ms02-{tag}')


@pytest.fixture
def models(pg, world):
    _run(pg, 'UPDATE yolo_model SET active = FALSE WHERE active')
    m1 = _model(pg, f'one-{world.tag}', active=True, families=[world.family_id])
    m2 = _model(pg, f'two-{world.tag}', active=False, families=[world.family_id])
    return SimpleNamespace(m1=m1, m2=m2)


def _activate(pg, model):
    _run(pg, 'UPDATE yolo_model SET active = FALSE WHERE active')
    _run(pg, 'UPDATE yolo_model SET active = TRUE WHERE id = :i', i=model.id)


def _vision_session(world, pg, status='scheduled'):
    t = world.a
    session = t.add_session(status)
    _run(pg, 'INSERT INTO session_processing_agreement (session_id, privacy_notice_version_id, purpose_quality_ops, '
             'purpose_model_improvement, agreed_by_user_id) VALUES (:s, :n, TRUE, FALSE, :u)',
         s=session.id, n=world.notice_id, u=t.supervisor_id)
    return session


class FakeWorker:
    """Records signed binds; answers with the SHA-256 it was asked to load (or a forced one)."""

    def __init__(self, secret, loaded=None):
        self.secret, self.loaded, self.binds = secret, loaded, []

    def post(self, url, data=None, timeout=None, headers=None, **kw):
        expected = 'sha256=' + hmac.new(self.secret.encode(), data, hashlib.sha256).hexdigest()
        assert headers['X-PEF-Signature'] == expected, 'bind must be signed with VISION_WS_SECRET'
        body = json.loads(data)
        self.binds.append(body)
        sha = self.loaded or body['model']['sha256']
        return SimpleNamespace(status_code=200, headers={'content-type': 'application/json'},
                               json=lambda: {'ok': True, 'model': {'sha256': sha}, 'session': {}}, text='')


@pytest.fixture
def worker(monkeypatch):
    fake = FakeWorker(vision_bridge.VISION_WS_SECRET)
    monkeypatch.setattr(vision_bridge.requests, 'post', fake.post)
    return fake


def _start(app, world, session):
    with app.test_request_context():
        return rf_session.start_session(session_id=session.id, operator_user_id=world.a.operator_id,
                                        institution_id=world.a.institution_id)


def _pinned(pg, session_id):
    return pg.execute(text('SELECT yolo_model_id FROM work_session WHERE id = :s'), {'s': session_id}).scalar()


def _pin_audits(pg, session_id):
    return pg.execute(text("SELECT count(*) FROM access_audit WHERE action = 'session.model_pinned' AND resource_id = :s"),
                      {'s': session_id}).scalar_one()


def test_new_vision_session_pins_the_active_model_at_start(app, world, pg, models, worker):
    session = _vision_session(world, pg)
    result = _start(app, world, session)
    assert result['capture_mode'] == 'vision'
    assert _pinned(pg, session.id) == models.m1.id and _pin_audits(pg, session.id) == 1
    payload = pg.execute(text("SELECT e.payload FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
                              "WHERE e.session_id = :s AND t.code = 'session_open'"), {'s': session.id}).scalar_one()
    assert (payload['yolo_model_id'], payload['model_version'], payload['model_sha256']) == (
        str(models.m1.id), models.m1.version, models.m1.sha)
    # The start's bind went to the worker, signed, with the pinned model and its storage reference.
    assert worker.binds[-1]['model']['sha256'] == models.m1.sha
    assert worker.binds[-1]['model']['storage'] == {'bucket': 'local', 'key': f'models/{models.m1.sha[:32]}.pt'}


def test_manual_session_pins_nothing(app, world, pg, models, worker):
    session = world.a.add_session('scheduled')          # no privacy agreement → manual_no_privacy
    assert _start(app, world, session)['capture_mode'] == 'manual_no_privacy'
    assert _pinned(pg, session.id) is None and _pin_audits(pg, session.id) == 0


def test_activating_another_model_never_changes_a_started_session(app, world, pg, models, worker):
    session = _vision_session(world, pg)
    _start(app, world, session)
    _activate(pg, models.m2)
    with app.test_request_context():
        payload = vision_bridge.session_model_payload(session.id)
        vision_bridge.bind_session(session.id)          # e.g. capture page reload / worker restart re-bind
    assert payload['model']['model_id'] == str(models.m1.id) and payload['model_version'] == models.m1.version
    assert worker.binds[-1]['model']['sha256'] == models.m1.sha
    assert _pinned(pg, session.id) == models.m1.id and _pin_audits(pg, session.id) == 1


def test_two_sessions_use_different_versions(app, world, pg, models, worker):
    first = _vision_session(world, pg)
    _start(app, world, first)
    _activate(pg, models.m2)
    second = _vision_session(world, pg)
    _start(app, world, second)
    assert (_pinned(pg, first.id), _pinned(pg, second.id)) == (models.m1.id, models.m2.id)
    with app.test_request_context():
        assert vision_bridge.session_model_payload(first.id)['model']['sha256'] == models.m1.sha
        assert vision_bridge.session_model_payload(second.id)['model']['sha256'] == models.m2.sha


def test_without_active_model_the_session_starts_and_pins_at_first_bind(app, world, pg, models, worker):
    _run(pg, 'UPDATE yolo_model SET active = FALSE WHERE active')
    session = _vision_session(world, pg)
    assert _start(app, world, session)['capture_mode'] == 'vision'      # start is not blocked
    assert _pinned(pg, session.id) is None
    with app.test_request_context(), pytest.raises(VisionBridgeError) as exc:
        vision_bridge.bind_session(session.id)
    assert exc.value.status_code == 409 and 'No vision model is active' in exc.value.message
    _activate(pg, models.m2)
    with app.test_request_context():
        vision_bridge.bind_session(session.id)
    assert _pinned(pg, session.id) == models.m2.id and _pin_audits(pg, session.id) == 1


def test_historical_sessions(app, world, pg, models, worker):
    closed = world.a.add_session('closed')
    in_progress = _vision_session(world, pg, status='in_progress')     # started before MS-02, no model
    assert _pinned(pg, closed.id) is None                                # closed history is never touched
    with app.test_request_context():
        vision_bridge.bind_session(in_progress.id)
    assert _pinned(pg, in_progress.id) == models.m1.id and _pin_audits(pg, in_progress.id) == 1
    assert _pinned(pg, closed.id) is None


def test_lost_pin_is_never_replaced(app, world, pg, models, worker):
    session = _vision_session(world, pg, status='in_progress')
    _run(pg, "INSERT INTO access_audit (actor_type, actor_user_id, action, resource_type, resource_id, outcome) "
             "VALUES ('user', :u, 'session.model_pinned', 'work_session', :s, 'success')",
         u=world.a.operator_id, s=session.id)
    with app.test_request_context(), pytest.raises(VisionBridgeError) as exc:
        vision_bridge.bind_session(session.id)
    assert exc.value.status_code == 409 and 'not replaced automatically' in exc.value.message
    assert _pinned(pg, session.id) is None and not worker.binds


def test_pinned_model_cannot_change_in_the_database(app, world, pg, models, worker):
    session = _vision_session(world, pg)
    _start(app, world, session)
    pg.execute(text('SAVEPOINT pin'))
    with pytest.raises(DBAPIError) as exc:
        pg.execute(text('UPDATE work_session SET yolo_model_id = :m WHERE id = :s'), {'m': models.m2.id, 's': session.id})
    pg.execute(text('ROLLBACK TO SAVEPOINT pin'))
    assert 'pinned model cannot change' in str(exc.value)
    assert _pinned(pg, session.id) == models.m1.id


def test_bind_refuses_a_worker_that_loaded_other_weights(app, world, pg, models, monkeypatch):
    fake = FakeWorker(vision_bridge.VISION_WS_SECRET, loaded='f' * 64)
    monkeypatch.setattr(vision_bridge.requests, 'post', fake.post)
    session = _vision_session(world, pg, status='in_progress')
    with app.test_request_context(), pytest.raises(VisionBridgeError) as exc:
        vision_bridge.bind_session(session.id)
    assert exc.value.status_code == 409


@pytest.mark.parametrize('reported, ok', [(None, False), ('f' * 64, False), ('PINNED', True)])
def test_counts_only_from_the_pinned_weights(app, world, pg, models, worker, monkeypatch, reported, ok):
    session = _vision_session(world, pg)
    _start(app, world, session)
    sha = models.m1.sha if reported == 'PINNED' else reported
    state = {'session': {'status': 'ready', 'frames_processed': 3, 'model_sha256': sha}}
    monkeypatch.setattr(vision_bridge.requests, 'get',
                        lambda url, timeout=None, **kw: SimpleNamespace(status_code=200, json=lambda: state, text=''))
    with app.test_request_context():
        if ok:
            assert vision_bridge.fetch_state(session.id)['session']['model_sha256'] == models.m1.sha
            assert vision_bridge.fetch_state_quick(session.id) is not None
        else:
            with pytest.raises(VisionBridgeError) as exc:
                vision_bridge.fetch_state(session.id)
            assert exc.value.status_code == 409
            assert vision_bridge.fetch_state_quick(session.id) is None     # no evidence checkpoint either


def test_s3_weights_travel_as_a_short_lived_presigned_link(app, world, pg, worker, monkeypatch):
    _run(pg, 'UPDATE yolo_model SET active = FALSE WHERE active')
    model = _model(pg, f's3-{world.tag}', active=True, bucket='pef-admin', families=[world.family_id])
    from services.admin_local import storage
    monkeypatch.setattr(storage, 'presigned_get', lambda bucket, key, expires: f'http://garage:3900/{bucket}/{key}?X-Amz-Expires={expires}')
    session = _vision_session(world, pg)
    _start(app, world, session)
    reference = worker.binds[-1]['model']['storage']
    assert reference['bucket'] == 'pef-admin' and reference['url'].startswith('http://garage:3900/pef-admin/models/')
    assert worker.binds[-1]['model']['sha256'] == model.sha


def test_model_without_weights_reaches_the_worker_without_a_reference(app, world, pg, worker):
    """Seed-like model (no media_asset): the bind carries no storage reference, so the worker refuses it
    (model_weights_missing) instead of falling back to any other file."""
    _run(pg, 'UPDATE yolo_model SET active = FALSE WHERE active')
    _model(pg, f'seed-{world.tag}', active=True, with_asset=False, families=[world.family_id])
    session = _vision_session(world, pg)
    _start(app, world, session)
    assert worker.binds[-1]['model']['storage'] == {}


def test_revoked_operator_cannot_get_a_ws_ticket(app, client, world, pg, models, monkeypatch):
    """Revocation during a connection: new tickets need a valid session (the portal re-verifies the JWT with
    AuthService on every request); the open WS itself is closed only when it ends (documented limitation)."""
    from controllers import routes
    from .conftest import FakeResponse
    session = _vision_session(world, pg, status='in_progress')
    monkeypatch.setattr(routes, '_auth_request', lambda *a, **k: FakeResponse(401, {'valid': False}))
    client.set_cookie('access_token', 'revoked-token')
    response = client.get(f'/operator/sessions/{session.id}/capture/ws-ticket')
    assert response.status_code in (302, 401)
