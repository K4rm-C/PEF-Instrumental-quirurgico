"""Supervisor RF screens served by SessionService (adapter); Operator screens never call it.

SessionService itself is tested in apps/SessionService (real PostgreSQL/Redis) and end to end in
scripts/e2e_session.py; here its HTTP answers are simulated to check that the portal: sends the
verified token, renders the same RF rows from the remote snapshot, keeps the RF messages for the
service's reasons, shows a controlled 503 when the service is down, and that the Operator's
screens do not depend on it.
"""

import json
import re
import uuid

import pytest
import requests

from services import rf_session, session_remote
from tests.conftest import INSTITUTION_ID

SESSION_ID = str(uuid.uuid4())
DISC_ID = str(uuid.uuid4())
FAMILY_ID = str(uuid.uuid4())


def snapshot(status='correction_required', **extra):
    base = {
        'id': SESSION_ID, 'status': {'code': status, 'name': status}, 'capture_mode': 'manual_no_privacy',
        'operator': {'id': str(uuid.uuid4()), 'name': 'Operador Remoto'},
        'operation': {'id': str(uuid.uuid4()), 'scheduled_at': '2026-10-21T15:00:00Z', 'institution_id': INSTITUTION_ID,
                      'procedure_type': {'id': str(uuid.uuid4()), 'code': 'lap_chole', 'name': 'Colecistectomia'},
                      'room': {'id': str(uuid.uuid4()), 'code': 'OR-09', 'name': 'Room 9'}},
        'kit': {'id': str(uuid.uuid4()), 'name': 'Kit remoto'}, 'station': {'id': str(uuid.uuid4()), 'name': 'Station 9'},
        'started_at': '2026-10-21T15:05:00Z', 'ended_at': None, 'updated_at': '2026-10-21T15:30:00Z',
        'closed_by_user_id': None, 'open_discrepancies': 1, 'has_privacy_agreement': False,
    }
    base.update(extra)
    return base


def detail(status='correction_required'):
    return snapshot(status, phase={'id': str(uuid.uuid4()), 'code': 'start', 'name': 'Start'},
                    expected_inventory=[{'family_id': FAMILY_ID, 'family_code': 'F1', 'family_name': 'Pinza remota',
                                         'category': None, 'expected_quantity': 2, 'source': 'kit_snapshot'}],
                    events=[], privacy=None, patient=None, surgical_team=[], contingency=None,
                    discrepancies=[{'id': DISC_ID, 'session_id': SESSION_ID, 'description': 'Shortfall on Pinza remota',
                                    'resolved': False, 'resolved_at': None, 'expected_quantity': 2,
                                    'detected_quantity': 1, 'family': {'id': FAMILY_ID, 'code': 'F1', 'name': 'Pinza remota'},
                                    'reason_code': 'shortage', 'origin_event_code': 'manual_count'}])


class FakeSessionService:
    def __init__(self):
        self.calls = []
        self.routes = {
            ('GET', ''): (200, [snapshot()]),
            ('GET', f'/{SESSION_ID}'): (200, detail()),
            ('GET', '/discrepancies'): (200, [{**detail()['discrepancies'][0], 'reason_name': 'Shortage'}]),
        }

    def __call__(self, method, url, json=None, params=None, timeout=None, headers=None):
        path = url.split('/api/v1/sessions', 1)[1]
        self.calls.append((method, path, json, params, headers))
        status, body = self.routes.get((method, path), (404, {'error': 'not_found', 'message': 'Session not found',
                                                             'details': {'reason': 'session_not_found'}}))
        response = requests.Response()
        response.status_code = status
        response._content = b'' if body is None else _dumps(body)
        response.headers['Content-Type'] = 'application/json'
        return response


def _dumps(value):
    return json.dumps(value).encode()


@pytest.fixture
def service(app, monkeypatch):
    fake = FakeSessionService()
    monkeypatch.setattr(session_remote, 'SESSION_SERVICE_URL', 'http://session.test')
    monkeypatch.setattr(session_remote.requests, 'request', fake)

    def no_local(*args, **kwargs):
        raise AssertionError('Supervisor session operations must not run locally when SessionService is configured')
    for name in ('schedule_session', 'update_scheduled_session', 'confirm_privacy_via_a', 'update_session_inventory',
                 'resolve_discrepancy', 'confirm_spd_close', 'contingency_for_session'):
        monkeypatch.setattr(rf_session, name, no_local)
    return fake


def test_supervisor_list_comes_from_session_service(client, login, service):
    login('spd_supervisor')
    page = client.get('/supervisor/sessions')
    assert page.status_code == 200 and 'Operador Remoto' in page.get_data(as_text=True)
    method, path, _, params, headers = service.calls[0]
    assert (method, path, params) == ('GET', '', {'expand': 'detail'})
    assert headers['Authorization'] == 'Bearer test-access-token' and headers['X-Correlation-ID']


def test_detail_and_review_render_the_remote_snapshot(client, login, service):
    login('spd_supervisor')
    assert 'Pinza remota' in client.get(f'/supervisor/sessions/{SESSION_ID}').get_data(as_text=True)
    assert 'Pinza remota' in client.get(f'/supervisor/discrepancies/{SESSION_ID}/review').get_data(as_text=True)
    # The queue page uses the V3 demo fixture in FRONTEND_DEMO_MODE (this suite); scripts/e2e_session.py
    # checks the real queue through SessionService.
    assert client.get(f'/supervisor/sessions/{uuid.uuid4()}').status_code == 404


def test_resolve_sends_the_outcome_and_keeps_rf_messages(client, login, service):
    login('spd_supervisor')
    service.routes[('POST', f'/{SESSION_ID}/discrepancies/{DISC_ID}/resolve')] = (
        200, {'discrepancy_id': DISC_ID, 'session_id': SESSION_ID, 'open_remaining': 0,
              'session_status_code': 'awaiting_spd_review'})
    response = client.post(f'/supervisor/discrepancies/{SESSION_ID}/review',
                           data={'discrepancy_id': DISC_ID, 'notes': 'Found it', 'resolution_outcome': 'lost'})
    assert response.status_code == 302
    post = next(call for call in service.calls if call[0] == 'POST')
    assert post[2] == {'notes': 'Found it', 'outcome': 'lost'}
    service.routes[('POST', f'/{SESSION_ID}/discrepancies/{DISC_ID}/resolve')] = (
        409, {'error': 'conflict', 'message': 'x', 'details': {'reason': 'already_resolved'}})
    again = client.post(f'/supervisor/discrepancies/{SESSION_ID}/review', data={'discrepancy_id': DISC_ID, 'notes': 'x'})
    assert again.status_code == 400 and 'Discrepancy already resolved.' in again.get_data(as_text=True)


@pytest.mark.parametrize('reason, text', [
    ('contingency_review_required', 'Confirm that you reviewed the contingency count before closing.'),
    ('open_discrepancies', 'Resolve open discrepancies before closing.'),
    ('not_awaiting_spd_review', 'Session is not awaiting SPD review.'),
    ('unreconciled_physical_shortage', 'Acknowledge that it is closed as an unreconciled shortage.'),
])
def test_close_errors_keep_rf_messages(client, login, service, reason, text):
    login('spd_supervisor')
    status = 400 if reason == 'contingency_review_required' else 409
    service.routes[('POST', f'/{SESSION_ID}/close')] = (status, {'error': 'x', 'message': 'x', 'details': {'reason': reason}})
    response = client.post(f'/supervisor/sessions/{SESSION_ID}/close', data={'material_recovered': '1'})
    assert response.status_code == status and text in response.get_data(as_text=True)
    post = next(call for call in service.calls if call[0] == 'POST')
    assert post[2] == {'material_recovered': True, 'contingency_reviewed': False,
                       'unreconciled_shortage_acknowledged': False}


def test_confirmed_addition_is_sent_and_clears_the_frozen_final_count(client, login, service, monkeypatch):
    login('spd_supervisor')
    service.routes[('GET', f'/{SESSION_ID}')] = (200, detail('in_progress'))
    service.routes[('PUT', f'/{SESSION_ID}/expected-inventory')] = (200, [
        {'family_id': FAMILY_ID, 'family_code': 'F1', 'expected_quantity': 3, 'source': 'live_add'}])
    cleared = []
    monkeypatch.setattr(rf_session, 'clear_vision_snapshot', lambda sid: cleared.append(str(sid)))
    response = client.post(f'/supervisor/sessions/{SESSION_ID}/inventory', data={
        'family_id[]': [FAMILY_ID], 'expected_quantity[]': ['3'], 'physically_added': '1'})
    assert response.status_code == 302
    put = next(call for call in service.calls if call[0] == 'PUT')
    assert put[2] == {'lines': [{'family_id': FAMILY_ID, 'expected_quantity': 3}], 'physically_added': True}
    assert cleared == [SESSION_ID]


def test_session_service_down_is_a_controlled_error(client, login, service, monkeypatch):
    from services import monitoring_remote
    # The 503 page is the services-health page: keep it off any MonitoringService the environment points at
    # (a running deployment on MONITORING_SERVICE_URL would answer 401 to the fake token).
    monkeypatch.setattr(monitoring_remote, 'MONITORING_SERVICE_URL', '')
    login('spd_supervisor')

    def down(*args, **kwargs):
        raise requests.ConnectionError('refused')
    monkeypatch.setattr(session_remote.requests, 'request', down)
    page = client.get('/supervisor/sessions')
    assert page.status_code == 503
    assert 'The session service is temporarily unavailable' in page.get_data(as_text=True)
    close = client.post(f'/supervisor/sessions/{SESSION_ID}/close', data={'material_recovered': '1'})
    assert close.status_code == 503


def test_operator_screens_never_call_session_service(client, login, monkeypatch, app):
    monkeypatch.setattr(session_remote, 'SESSION_SERVICE_URL', 'http://session.test')

    def forbidden(*args, **kwargs):
        raise AssertionError('the Operator must not depend on SessionService')
    monkeypatch.setattr(session_remote.requests, 'request', forbidden)
    monkeypatch.setattr(session_remote.requests, 'get', forbidden)
    login('station_operator')
    for path in ('/operator/sessions', '/operator/sessions/history', '/operator/profile'):
        assert client.get(path).status_code == 200, path


# --- Physical shortage: no note resolution, no safe close -------------------------------------------


def _with_discrepancy(status, *, reason_code, resolved):
    body = detail(status)
    body['discrepancies'][0].update({'reason_code': reason_code, 'resolved': resolved,
                                     'resolved_at': '2026-10-21T10:00:00Z' if resolved else None})
    body['open_discrepancies'] = 0 if resolved else 1
    return body


def test_review_of_a_physical_shortage_offers_no_notes_only_outcome(client, login, service):
    login('spd_supervisor')
    html = client.get(f'/supervisor/discrepancies/{SESSION_ID}/review').get_data(as_text=True)
    assert 'data-physical-shortage' in html and 'value="notes_only"' not in html
    assert re.search(r'value="lost"\s+checked', html)


def test_review_of_a_surplus_keeps_the_notes_outcome(client, login, service):
    service.routes[('GET', f'/{SESSION_ID}')] = (200, _with_discrepancy('correction_required', reason_code='surplus',
                                                                        resolved=False))
    login('spd_supervisor')
    html = client.get(f'/supervisor/discrepancies/{SESSION_ID}/review').get_data(as_text=True)
    assert 'value="notes_only"' in html and 'data-physical-shortage' not in html


def test_rejected_note_resolution_keeps_the_rf_message(client, login, service):
    service.routes[('POST', f'/{SESSION_ID}/discrepancies/{DISC_ID}/resolve')] = (
        409, {'error': 'conflict', 'message': 'x', 'details': {'reason': 'physical_shortage_needs_piece_outcome'}})
    login('spd_supervisor')
    response = client.post(f'/supervisor/discrepancies/{SESSION_ID}/review',
                           data={'discrepancy_id': DISC_ID, 'notes': 'Found', 'resolution_outcome': 'notes_only'})
    assert response.status_code == 400 and 'cannot be resolved with notes only' in response.get_data(as_text=True)


def test_close_of_a_session_with_a_physical_shortage_needs_the_acknowledgment(client, login, service):
    service.routes[('GET', f'/{SESSION_ID}')] = (200, _with_discrepancy('awaiting_spd_review', reason_code='shortage',
                                                                        resolved=True))
    service.routes[('POST', f'/{SESSION_ID}/close')] = (200, snapshot('closed'))
    login('spd_supervisor')
    html = client.get(f'/supervisor/sessions/{SESSION_ID}').get_data(as_text=True)
    assert 'data-unreconciled-shortage' in html and 'name="unreconciled_shortage_acknowledged"' in html
    client.post(f'/supervisor/sessions/{SESSION_ID}/close',
                data={'material_recovered': '1', 'unreconciled_shortage_acknowledged': '1'})
    post = next(call for call in service.calls if call[0] == 'POST')
    assert post[2] == {'material_recovered': True, 'contingency_reviewed': False, 'unreconciled_shortage_acknowledged': True}


def test_close_without_shortage_shows_no_acknowledgment(client, login, service):
    service.routes[('GET', f'/{SESSION_ID}')] = (200, _with_discrepancy('awaiting_spd_review', reason_code='surplus',
                                                                        resolved=True))
    login('spd_supervisor')
    html = client.get(f'/supervisor/sessions/{SESSION_ID}').get_data(as_text=True)
    assert 'data-unreconciled-shortage' not in html and 'unreconciled_shortage_acknowledged' not in html
