"""MS-03: the Supervisor sees the REAL evidence of a session; live WebSocket revocation hooks of the portal.

Real PostgreSQL (rolled-back transaction, fixtures of test_multitenant) for the session, its pinned model and its
count events; MongoDB checkpoints and EvidenceService are replaced by in-memory fakes here (the real services are
exercised by scripts/e2e_ms03_evidence.py).
"""
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services import evidence_client, mongo_checkpoints, vision_bridge

from .test_multitenant import _run, login_as, pg, world  # noqa: F401 - fixtures

SHA = hashlib.sha256(b'ms03-model').hexdigest()


@pytest.fixture
def vision_session(pg, world):
    t = world.a
    session = t.add_session('in_progress')
    model_id = uuid.uuid4()
    _run(pg, "INSERT INTO yolo_model (id, version_tag, checksum, active) VALUES (:i, :v, :c, FALSE)",
         i=model_id, v=f'ms03-{world.tag}', c=SHA)
    _run(pg, "UPDATE work_session SET capture_mode = 'vision', yolo_model_id = :m WHERE id = :s", m=model_id, s=session.id)
    _run(pg, "INSERT INTO count_event (event_type_id, session_id, user_id, occurred_at, expected_quantity, detected_quantity, payload) "
             "SELECT id, :s, :u, now(), 14, 4, CAST(:p AS jsonb) FROM cat_event_type WHERE code = 'auto_count'",
         s=session.id, u=t.operator_id, p=json.dumps({'reason': 'detection_initial', 'trigger': 'initial',
                                                       'model_version': f'ms03-{world.tag}'}))
    return SimpleNamespace(id=session.id, model_id=model_id, version=f'ms03-{world.tag}', tenant=t)


ASSET_OK, ASSET_PURGED, FOREIGN = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


@pytest.fixture
def services(monkeypatch, vision_session):
    """Fake DS06 checkpoints + EvidenceService answers for the session."""
    state = SimpleNamespace(evidence_down=False, scope=True, signed=True, calls=[])
    checkpoints = [
        {'session_id': str(vision_session.id), 'session_checkpoint_seq': 1, 'reason': 'start',
         'captured_at': datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc), 'phase_code': 'start',
         'model_version': vision_session.version, 'media_asset_id': ASSET_OK,
         'detections': [{'family_code': 'KELLY', 'confidence': 0.91, 'bbox': [10.0, 20.0, 30.0, 40.0]}]},
        {'session_id': str(vision_session.id), 'session_checkpoint_seq': 2, 'reason': 'close',
         'captured_at': datetime(2026, 11, 4, 9, 5, tzinfo=timezone.utc), 'phase_code': 'start',
         'model_version': vision_session.version, 'media_asset_id': ASSET_PURGED, 'detections': []},
    ]
    assets = [{'id': ASSET_OK, 'kind': 'jpeg', 'sha256': 'c' * 64, 'size_bytes': 1234, 'blocked': False, 'purged': False},
              {'id': ASSET_PURGED, 'kind': 'jpeg', 'sha256': 'd' * 64, 'size_bytes': 99, 'blocked': False, 'purged': True}]
    monkeypatch.setattr(mongo_checkpoints, 'enabled', lambda: True)
    monkeypatch.setattr(mongo_checkpoints, 'session_panel',
                        lambda sid: {'available': True, 'checkpoints': checkpoints, 'by_reason': [], 'telemetry': None})
    monkeypatch.setattr(mongo_checkpoints, 'list_checkpoints', lambda sid, **kw: checkpoints)
    monkeypatch.setattr(evidence_client, 'configured', lambda: True)

    def list_session_evidence(session_id, token):
        state.calls.append(('list', session_id, token))
        if state.evidence_down:
            raise evidence_client.EvidenceError('down', retryable=True, status=503)
        return assets if state.scope else None

    def get_evidence(asset_id, token):
        state.calls.append(('get', asset_id, token))
        item = next((a for a in assets if a['id'] == asset_id), None)
        if item is None:
            return None
        live = state.signed and not item['purged']
        return {**item, 'signed_url': 'http://garage.test/pef-evidence/x?X-Amz-Expires=300' if live else None}
    monkeypatch.setattr(evidence_client, 'list_session_evidence', list_session_evidence)
    monkeypatch.setattr(evidence_client, 'get_evidence', get_evidence)
    return state


def _supervisor(login_as, vision_session):
    login_as(vision_session.tenant.supervisor_id, vision_session.tenant.institution_id, 'spd_supervisor')


@pytest.mark.parametrize('path', ['/supervisor/sessions/{sid}', '/supervisor/discrepancies/{sid}/review'])
def test_supervisor_pages_show_real_evidence(app, client, login_as, vision_session, services, path):
    _supervisor(login_as, vision_session)
    response = client.get(path.format(sid=vision_session.id))
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert html.count('data-evidence-figure') == 2 and 'data-evidence-traceability' in html
    assert f'/supervisor/sessions/{vision_session.id}/evidence/{ASSET_OK}/image' in html     # authorized portal route
    assert 'garage.test' not in html and 'X-Amz' not in html                              # never a URL in the HTML
    assert 'The image was removed by the retention policy.' in html                       # purged → placeholder
    assert vision_session.version in html and SHA[:12] in html                            # pinned model + weights hash
    assert 'data-bbox="10.0,20.0,30.0,40.0"' in html and 'KELLY' in html                  # real YOLO detections
    assert 'WS-026' not in html and 'in this demo' not in html                            # no demo data
    assert services.calls and services.calls[0][2] == 'mt-access-token'                  # Supervisor's own token


def test_no_evidence_shows_an_empty_state(app, client, login_as, vision_session, services, monkeypatch):
    monkeypatch.setattr(mongo_checkpoints, 'session_panel', lambda sid: {'available': True, 'checkpoints': []})
    monkeypatch.setattr(mongo_checkpoints, 'list_checkpoints', lambda sid, **kw: [])
    monkeypatch.setattr(evidence_client, 'list_session_evidence', lambda sid, token: [])
    _supervisor(login_as, vision_session)
    html = client.get(f'/supervisor/discrepancies/{vision_session.id}/review').get_data(as_text=True)
    assert 'No evidence images have been recorded for this session.' in html and 'data-evidence-figure' not in html


def test_evidence_service_down_never_breaks_the_page(app, client, login_as, vision_session, services):
    services.evidence_down = True
    _supervisor(login_as, vision_session)
    response = client.get(f'/supervisor/sessions/{vision_session.id}')
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and 'Evidence is not available right now' in html
    assert 'The image of this checkpoint is not available.' in html                       # checkpoints still listed


def test_image_route_redirects_to_a_short_lived_link(app, client, login_as, vision_session, services):
    _supervisor(login_as, vision_session)
    response = client.get(f'/supervisor/sessions/{vision_session.id}/evidence/{ASSET_OK}/image')
    assert response.status_code == 302 and response.headers['Location'].startswith('http://garage.test/')
    assert response.headers['Cache-Control'] == 'no-store'
    assert [c[0] for c in services.calls] == ['list', 'get']                              # session authorized first


@pytest.mark.parametrize('case, expected', [
    ('foreign_asset', 404), ('session_out_of_scope', 404), ('purged', 410), ('evidence_down', 503), ('bad_uuid', 404),
])
def test_image_route_refusals(app, client, login_as, vision_session, services, case, expected):
    _supervisor(login_as, vision_session)
    asset = ASSET_OK
    if case == 'foreign_asset':
        asset = FOREIGN                       # an asset that is not one of this session's
    elif case == 'session_out_of_scope':
        services.scope = False
    elif case == 'purged':
        asset = ASSET_PURGED
    elif case == 'evidence_down':
        services.evidence_down = True
    url = f'/supervisor/sessions/{vision_session.id}/evidence/{asset}/image'
    if case == 'bad_uuid':
        url = f'/supervisor/sessions/{vision_session.id}/evidence/..%2F..%2Fetc%2Fpasswd/image'
    assert client.get(url).status_code == expected
    if case in ('foreign_asset', 'session_out_of_scope'):
        assert not any(c[0] == 'get' for c in services.calls)                              # never signs


@pytest.mark.parametrize('role', ['station_operator', 'it_admin'])
def test_image_route_is_supervisor_only(app, client, login_as, vision_session, services, role):
    login_as(vision_session.tenant.operator_id, vision_session.tenant.institution_id, role)
    assert client.get(f'/supervisor/sessions/{vision_session.id}/evidence/{ASSET_OK}/image').status_code == 403
    assert not services.calls


def test_other_institution_cannot_open_the_session_evidence(app, client, login_as, vision_session, services, world):
    login_as(world.b.supervisor_id, world.b.institution_id, 'spd_supervisor')
    assert client.get(f'/supervisor/sessions/{vision_session.id}').status_code == 404
    services.scope = False                    # EvidenceService itself answers 404 for another institution
    assert client.get(f'/supervisor/sessions/{vision_session.id}/evidence/{ASSET_OK}/image').status_code == 404


# --- live WebSocket revocation hooks (portal side) --------------------------------------------------------

def test_ws_ticket_names_the_user_and_is_renewable(app, client, login_as, vision_session):
    t = vision_session.tenant
    login_as(t.operator_id, t.institution_id, 'station_operator')
    payload = client.get(f'/operator/sessions/{vision_session.id}/capture/ws-ticket').get_json()
    parts = payload['ticket'].split('.')
    assert len(parts) == 5 and parts[0] == str(vision_session.id) and parts[1] == str(t.operator_id)
    assert payload['renew_after_s'] == max(10, payload['ttl_s'] // 3)


class Recorder:
    def __init__(self):
        self.posts = []

    def post(self, url, data=None, timeout=None, headers=None, **kw):
        self.posts.append((url, json.loads(data), headers))
        return SimpleNamespace(status_code=200, json=lambda: {'ok': True, 'revoked_connections': 1})


def test_revoke_live_user_is_signed(app, monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(vision_bridge.requests, 'post', recorder.post)
    assert vision_bridge.revoke_live_user('u-1') == 1
    url, body, headers = recorder.posts[0]
    raw = json.dumps(body, separators=(',', ':'), sort_keys=True).encode()
    assert url.endswith('/auth/revoke') and body == {'user_id': 'u-1'}
    assert headers['X-PEF-Signature'] == 'sha256=' + hmac.new(vision_bridge.VISION_WS_SECRET.encode(), raw,
                                                               hashlib.sha256).hexdigest()


def test_revoke_live_user_never_raises(app, monkeypatch):
    def down(*a, **k):
        raise vision_bridge.requests.ConnectionError('worker down')
    monkeypatch.setattr(vision_bridge.requests, 'post', down)
    assert vision_bridge.revoke_live_user('u-1') is None


def test_sign_out_and_administration_revoke_the_live_connection(app, client, login_as, vision_session, monkeypatch):
    revoked = []
    monkeypatch.setattr(vision_bridge, 'revoke_live_user', lambda user_id: revoked.append(str(user_id)))
    t = vision_session.tenant
    login_as(t.operator_id, t.institution_id, 'station_operator')
    client.get('/sign-out')
    assert revoked == [str(t.operator_id)]
    from services.admin_local import auth_sessions
    with app.test_request_context():
        with pytest.raises(auth_sessions.RevocationFailed):      # no admin token here: AuthService is not called
            auth_sessions.revoke_user_sessions(t.operator_id)
    assert revoked == [str(t.operator_id), str(t.operator_id)]   # ... but the live WS revocation was sent first
