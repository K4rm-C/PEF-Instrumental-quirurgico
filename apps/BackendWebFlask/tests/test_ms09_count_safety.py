"""MS-09: count safety — contingency claims verified on the server, escalation of a rejected final count.

Same in-process flow as test_single_video_flow (VisionWorker answers simulated, PostgreSQL real). Rules checked:
- a technical contingency the server can contradict is refused; a declared one is recorded as such and needs an
  explicit acknowledgment of the Supervisor; a contingency never produces a final YOLO count;
- a rejected final count can be escalated as a possible physical difference (15→14 shortage, 15→16 surplus), never
  when the final count matches (15→15) or when the reference changed by confirmed additions; the escalation keeps
  the rejected capture, opens a discrepancy, sends the session to the Supervisor and can never be a normal safe close.
"""

import pytest
from sqlalchemy import text

from services import rf_session, vision_bridge
from services.rf_session import RfSessionError

from .test_single_video_flow import (_audits, _events, _finish_run, _post, _reference_with_additions,  # noqa: F401
                                     _register_initial, _status, flow, frame, worker)
from .test_multitenant import login_as, pg, world  # noqa: F401  (pytest fixtures)
from .test_vision_count_rule import tray  # noqa: F401


def _report(flow, reason, notes='', qty=(8, 7)):
    tray = flow.tray
    return _post(flow, '/manual-report', **{f'reported_{tray.a}': qty[0], f'reported_{tray.b}': qty[1],
                                            'contingency_reason': reason, 'contingency_notes': notes})


def _close(tray, **flags):
    return rf_session.confirm_spd_close(session_id=tray.session.id, supervisor_user_id=tray.t.supervisor_id,
                                        institution_id=tray.t.institution_id, material_recovered=True, **flags)


def _last_close_payload(tray):
    return _events(tray, 'manual_close')[-1]['payload']


# --- 1.1 contingency ---------------------------------------------------------------------------------------


def test_worker_unavailable_is_refused_when_the_worker_answers(app, flow):
    tray = flow.tray
    _register_initial(flow)
    response = _report(flow, 'worker_unavailable')
    assert response.status_code == 409 and 'vision worker is available' in response.get_data(as_text=True)
    assert _events(tray, 'manual_close') == [] and _status(tray) == 'in_progress'


def test_worker_unavailable_is_verified_when_the_worker_is_down(app, flow, monkeypatch):
    tray = flow.tray
    _register_initial(flow)
    monkeypatch.setattr(vision_bridge, 'fetch_state', lambda sid: (_ for _ in ()).throw(vision_bridge.VisionBridgeError('down')))
    monkeypatch.setattr(vision_bridge, 'worker_health', lambda: {'ok': False, 'error': 'connection refused'})
    assert _report(flow, 'worker_unavailable').status_code == 302
    contingency = _last_close_payload(tray)['contingency']
    assert contingency['verification']['status'] == 'verified' and contingency['verification']['basis'] == 'worker_unreachable'
    station = tray.c.execute(text('SELECT station_id FROM work_session WHERE id = :s'), {'s': tray.session.id}).scalar()
    assert contingency['worker_state']['status'] == 'unreachable' and contingency['station_id'] == (str(station) if station else None)
    assert 'session.contingency_close' in _audits(tray)
    assert [e['payload'].get('trigger') for e in _events(tray, 'auto_count')] == ['initial']   # never a final count
    assert rf_session.vision_final_state(tray.session.id)['valid'] is False
    _close(tray, contingency_reviewed=True)                      # verified: no extra acknowledgment
    close = _events(tray, 'session_close')[-1]['payload']
    assert (close['close_basis'], close['contingency_verification']) == ('contingency_count', 'verified')


def test_declared_contingency_needs_the_supervisor_acknowledgment(app, flow):
    tray = flow.tray
    _register_initial(flow)
    response = _report(flow, 'other', notes='Lighting failure over the tray, camera image unusable')
    assert response.status_code == 302
    assert _last_close_payload(tray)['contingency']['verification']['status'] == 'declared'
    with pytest.raises(RfSessionError) as exc:
        _close(tray, contingency_reviewed=True)
    assert exc.value.status_code == 409 and 'could not be verified' in exc.value.message
    _close(tray, contingency_reviewed=True, unverified_contingency_acknowledged=True)
    assert _events(tray, 'session_close')[-1]['payload']['contingency_verification'] == 'declared'


def test_unreliable_detection_needs_a_detection(app, flow):
    tray = flow.tray
    response = _report(flow, 'unreliable_detection', notes='Reflections on the instruments')
    assert response.status_code == 409 and 'has not produced a detection' in response.get_data(as_text=True)
    _register_initial(flow)
    assert _report(flow, 'unreliable_detection', notes='Reflections on the instruments').status_code == 302
    assert _last_close_payload(tray)['contingency']['verification']['status'] == 'declared'


def test_processing_error_is_verified_only_with_a_worker_error(app, flow):
    tray = flow.tray
    _register_initial(flow)
    flow.state = {**flow.state, 'status': 'error', 'last_error': 'decoder failed'}
    assert _report(flow, 'processing_error').status_code == 302
    assert _last_close_payload(tray)['contingency']['verification'] == {
        **_last_close_payload(tray)['contingency']['verification'], 'status': 'verified', 'basis': 'worker_reported_error'}


# --- 1.2 escalation of a rejected final count --------------------------------------------------------------


@pytest.mark.parametrize('final, kind, reason', [((8, 6), 'possible_shortage', 'shortage'),
                                                 ((9, 7), 'possible_surplus', 'surplus')])
def test_rejected_final_count_can_be_escalated(app, flow, final, kind, reason):
    """15 → 14 and 15 → 16: rejected, then reported to the Supervisor without any final count."""
    tray = flow.tray
    _register_initial(flow)
    _finish_run(flow, *final)
    assert _post(flow, '/capture/final').status_code == 409
    blocked_before = _events(tray, 'close_blocked')
    short = flow.client.post(flow.base + '/capture/escalate', json={'notes': 'short'})
    assert short.status_code == 400
    response = flow.client.post(flow.base + '/capture/escalate', json={'notes': 'Checked the tray twice with the nurse'})
    body = response.get_json()
    assert response.status_code == 200 and body['kind'] == kind and (body['final'], body['reference']) == (sum(final), 15)
    assert _status(tray) == 'correction_required'
    assert _events(tray, 'close_blocked') == blocked_before                       # the rejected capture is kept
    assert [e['payload'].get('trigger') for e in _events(tray, 'auto_count')] == ['initial']   # no final count
    disc = tray.c.execute(text("SELECT r.code, d.expected_quantity, d.detected_quantity, d.resolved, d.origin_event_id "
                               "FROM discrepancy d JOIN cat_discrepancy_reason r ON r.id = d.reason_id "
                               "WHERE d.session_id = :s"), {'s': tray.session.id}).one()
    blocked_id = tray.c.execute(text("SELECT ce.id FROM count_event ce JOIN cat_event_type t ON t.id = ce.event_type_id "
                                     "WHERE ce.session_id = :s AND t.code = 'close_blocked'"), {'s': tray.session.id}).scalar_one()
    assert (disc.code, disc.expected_quantity, disc.detected_quantity, disc.resolved, disc.origin_event_id) == (
        reason, 15, sum(final), False, blocked_id)
    payload = _last_close_payload(tray)
    assert payload['close_path'] == 'final_count_escalation' and payload['next_status'] == 'correction_required'
    assert payload['escalation']['kind'] == kind and payload['safe_close'] is False
    assert 'session.final_count_escalated' in _audits(tray)
    outbox = tray.c.execute(text("SELECT event_code FROM notification_outbox WHERE session_id = :s AND event_code = 'manual_close'"),
                            {'s': tray.session.id}).scalars().all()
    assert outbox == ['manual_close']                                             # the Supervisor is notified
    assert flow.client.get(flow.base + '/vision-validation').status_code == 302   # no human validation
    again = flow.client.post(flow.base + '/capture/escalate', json={'notes': 'Checked the tray twice with the nurse'})
    assert again.status_code == 409                                               # not in progress anymore


def test_matching_final_count_cannot_be_escalated(app, flow):
    """15 → 15: the final count is valid; nothing to report."""
    tray = flow.tray
    _register_initial(flow)
    _finish_run(flow, 8, 7)
    assert _post(flow, '/capture/final').status_code == 200
    response = flow.client.post(flow.base + '/capture/escalate', json={'notes': 'Trying to skip the validation'})
    assert response.status_code == 409 and _status(tray) == 'in_progress' and _events(tray, 'manual_close') == []


def test_reference_changed_by_confirmed_additions(app, flow):
    """Rejected against 15, then the Supervisor confirmed +2: escalation refused until the tray is captured again
    against the new reference 17; a capture of 15 against 17 can then be escalated."""
    tray = flow.tray
    _register_initial(flow)
    _finish_run(flow, 8, 6)
    assert _post(flow, '/capture/final').status_code == 409
    _reference_with_additions(flow, 2)
    refused = flow.client.post(flow.base + '/capture/escalate', json={'notes': 'Checked the tray twice with the nurse'})
    assert refused.status_code == 409 and '15' in refused.get_json()['error'] and '17' in refused.get_json()['error']
    _finish_run(flow, 8, 7, run_id='run-2')
    assert _post(flow, '/capture/final').status_code == 409                       # 15 against 17
    response = flow.client.post(flow.base + '/capture/escalate', json={'notes': 'The two added pieces are missing'})
    assert response.status_code == 200 and (response.get_json()['final'], response.get_json()['reference']) == (15, 17)


@pytest.mark.parametrize('final, outcome, flags', [
    ((9, 7), {}, {'escalation_acknowledged': True}),                                  # surplus: escalation only
    # shortage: the existing rule (MS-03.1) also applies: a recorded physical shortage is never a safe close.
    ((8, 6), {'mark_recovered': True}, {'escalation_acknowledged': True, 'unreconciled_shortage_acknowledged': True}),
])
def test_escalated_session_is_never_a_normal_safe_close(app, flow, final, outcome, flags):
    tray = flow.tray
    _register_initial(flow)
    _finish_run(flow, *final)
    _post(flow, '/capture/final')
    flow.client.post(flow.base + '/capture/escalate', json={'notes': 'Checked the tray twice with the nurse'})
    (disc_id,) = tray.c.execute(text('SELECT id FROM discrepancy WHERE session_id = :s'), {'s': tray.session.id}).scalars().all()
    rf_session.resolve_discrepancy(discrepancy_id=disc_id, session_id=tray.session.id, supervisor_user_id=tray.t.supervisor_id,
                                   institution_id=tray.t.institution_id, notes='Reviewed with the OR team', **outcome)
    assert _status(tray) == 'awaiting_spd_review'
    with pytest.raises(RfSessionError) as exc:
        _close(tray, **{k: v for k, v in flags.items() if k != 'escalation_acknowledged'})
    assert exc.value.status_code == 409 and 'without a valid final count' in exc.value.message
    _close(tray, **flags)
    close = _events(tray, 'session_close')[-1]['payload']
    assert close['safe_close_confirmed'] is False and close['close_basis'] == 'final_count_escalation'
