"""Single-video flow (initial count = first frame, final count = last frame) and the Operator close.

Real PostgreSQL schema inside a rolled-back transaction (test_multitenant fixtures); VisionWorker is
replaced by the worker state it would expose (vision_bridge.fetch_state), so the routes, rf_session
and the database run for real. The tray holds families A (8) and B (7): 15 instruments.
"""

import uuid

import pytest
from flask import g
from sqlalchemy import text

from controllers.rf_ui import past_session_rows
from controllers import view_data
from services import rf_session, session_remote, vision_bridge
from services.rf_session import RfSessionError

from .test_multitenant import login_as, pg, world  # noqa: F401  (pytest fixtures)
from .test_vision_count_rule import tray  # noqa: F401  (pytest fixture)

SHA = 'a' * 64


# --- worker states ----------------------------------------------------------------------------


def _counts(tray, a, b):
    return {
        tray.codes[tray.a]: {'family_id': str(tray.a), 'family_code': tray.codes[tray.a],
                             'expected_quantity': 8, 'detected_quantity': a, 'difference': a - 8},
        tray.codes[tray.b]: {'family_id': str(tray.b), 'family_code': tray.codes[tray.b],
                             'expected_quantity': 7, 'detected_quantity': b, 'difference': b - 7},
    }


def _boxes(n):
    return [{'xyxy': [10.0 * i, 0.0, 10.0 * i + 8, 8.0], 'confidence': 0.9, 'family_code': None} for i in range(n)]


def frame(tray, a, b, *, position, index, boxes=None, run_id=None, out_of_kit=None, video_id='vid-1'):
    """VisionWorker single-frame analysis (initial_frame / final_frame)."""
    total = a + b if boxes is None else boxes
    return {'ok': True, 'position': position, 'video_id': video_id, 'run_id': run_id, 'frame_index': index,
            'video_t': round(index / 30, 3), 'model_version': 'test-model', 'pipeline': 'matrix_v1',
            'quality': {'ok': True}, 'boxes': _boxes(total), 'total': total, 'counts': _counts(tray, a, b),
            'out_of_kit': out_of_kit or [], 'frame_jpeg_b64': 'AAAA', 'skipped_frames': []}


def worker(tray, *, status, first=None, last=None, run_id=None, sha=SHA, video_id='vid-1', live=(3, 2, 30)):
    """Worker state. `live` = tallies of the frame being processed (never a count)."""
    return {'status': status, 'video_id': video_id,
            'video': {'sha256': sha, 'filename': 'tray.mp4', 'size_bytes': 1000, 'frame_count': 300, 'fps': 30.0},
            'initial_frame': first, 'final_frame': last, 'run_id': run_id,
            'frames_processed': 60 if run_id else 0, 'frame_index': 299 if run_id else 0,
            'counts': _counts(tray, live[0], live[1]), 'boxes': _boxes(live[2]), 'model_version': 'test-model',
            'error_stage': None, 'last_error': None}


@pytest.fixture
def flow(tray, client, login_as, monkeypatch):
    """Operator logged in; `flow.state` is what VisionWorker answers."""
    holder = type('Flow', (), {})()
    holder.tray, holder.client = tray, client
    holder.state = worker(tray, status='awaiting_initial', first=frame(tray, 8, 7, position='first', index=0))
    holder.process_calls = []
    holder.discarded = []
    monkeypatch.setattr(vision_bridge, 'fetch_state', lambda sid: {'ok': True, 'session': holder.state})
    monkeypatch.setattr(vision_bridge, 'bind_session', lambda sid: {'ok': True})
    monkeypatch.setattr(vision_bridge, 'worker_health', lambda: {'ok': True})
    monkeypatch.setattr(vision_bridge, 'unbind_session', lambda sid: {'ok': True})
    monkeypatch.setattr(vision_bridge, 'discard_video', lambda sid: holder.discarded.append(sid))

    def process(sid, video_id):
        holder.process_calls.append(video_id)
        return {'ok': True, 'video_id': video_id, 'session': holder.state}
    monkeypatch.setattr(vision_bridge, 'process_stored_video', process)
    monkeypatch.setattr(session_remote, 'SESSION_SERVICE_URL', '')  # in-process close (no SessionService here)
    login_as(tray.t.operator_id, tray.t.institution_id, 'station_operator')
    holder.base = f'/operator/sessions/{tray.session.id}'
    return holder


def _post(flow, path, **form):
    return flow.client.post(flow.base + path, data=form)


def _lots(tray):
    return tray.c.execute(text(
        "SELECT ce.detected_quantity, ce.payload, ce.user_id FROM count_event ce JOIN cat_event_type t "
        "ON t.id = ce.event_type_id WHERE ce.session_id = :s AND t.code = 'auto_count' "
        "AND ce.payload->>'trigger' IN ('initial', 'final') ORDER BY ce.occurred_at"),
        {'s': tray.session.id}).mappings().all()


def _status(tray):
    return tray.c.execute(text(
        'SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id WHERE w.id = :id'),
        {'id': tray.session.id}).scalar_one()


def _events(tray, code):
    return tray.c.execute(text(
        'SELECT ce.user_id, ce.payload FROM count_event ce JOIN cat_event_type t ON t.id = ce.event_type_id '
        'WHERE ce.session_id = :s AND t.code = :c ORDER BY ce.occurred_at'),
        {'s': tray.session.id, 'c': code}).mappings().all()


def _audits(tray):
    return tray.c.execute(text('SELECT action FROM access_audit WHERE resource_id = :s ORDER BY occurred_at'),
                          {'s': tray.session.id}).scalars().all()


def _register_initial(flow, **form):
    return _post(flow, '/capture/initial', **form)


def _finish_run(flow, a=8, b=7, *, boxes=None, out_of_kit=None, run_id='run-1'):
    """VisionWorker finished the run of the same video; its last valid frame shows a + b instruments."""
    flow.state = worker(flow.tray, status='ready', run_id=run_id,
                        first=flow.state['initial_frame'],
                        last=frame(flow.tray, a, b, position='last', index=297, boxes=boxes, run_id=run_id,
                                   out_of_kit=out_of_kit))


def _validate(flow, qty_a=8, qty_b=7, *, extra=0, **notes):
    tray = flow.tray
    form = {f'validated_{tray.a}': qty_a, f'validated_{tray.b}': qty_b, 'extra_instruments': extra}
    for family, values in notes.items():
        fid = tray.a if family == 'a' else tray.b
        form.update({f'reason_{fid}': values.get('reason', ''), f'notes_{fid}': values.get('notes', '')})
    return _post(flow, '/vision-validation', **form)


def _happy_path(flow, *, final=(8, 7), validated=(8, 7), **notes):
    assert _register_initial(flow).status_code == 200
    assert _post(flow, '/capture/play').status_code == 202
    _finish_run(flow, *final)
    assert _post(flow, '/capture/final').status_code == 200
    return _validate(flow, *validated, **notes)


def _close(flow, **form):
    form.setdefault('physical_integrity_confirmed', '1')
    form.setdefault('material_recovered', '1')
    return _post(flow, '/close-session', **form)


# --- Ajuste 1: video and YOLO -------------------------------------------------------------------


def test_initial_count_is_the_first_frame_not_the_live_tallies(app, flow):
    tray = flow.tray
    # The worker also exposes live tallies (30 boxes): they must not leak into the initial count.
    response = _register_initial(flow)
    assert response.status_code == 200 and response.get_json()['initial_total'] == 15
    (lot,) = _lots(tray)
    payload = lot['payload']
    assert lot['detected_quantity'] == 15 and lot['user_id'] == tray.t.operator_id
    assert (payload['trigger'], payload['source'], payload['frame_index']) == ('initial', 'first_frame', 0)
    assert payload['video'] == {'video_id': 'vid-1', 'sha256': SHA, 'filename': 'tray.mp4', 'size_bytes': 1000,
                                'frame_count': 300, 'fps': 30.0, 'duration_s': None}
    assert payload['confirmed_by_user_id'] == str(tray.t.operator_id) and payload['confirmed_at']
    assert {f['family_id']: f['detected_quantity'] for f in payload['board']['families']} == {
        str(tray.a): 8, str(tray.b): 7}
    assert 'session.initial_count' in _audits(tray)
    assert _status(tray) == 'in_progress'


def test_initial_count_waits_for_the_first_frame_and_never_invents_one(app, flow):
    tray = flow.tray
    flow.state = worker(tray, status='loading')
    response = _register_initial(flow)
    assert response.status_code == 409 and 'still being analysed' in response.get_json()['error']
    flow.state = worker(tray, status='error', first={'ok': False, 'position': 'first', 'reason': 'no_valid_frame',
                                                     'error': 'No usable frame in the first 15 frames'})
    flow.state['error_stage'] = 'first_frame'
    response = _register_initial(flow)
    assert response.status_code == 409 and 'could not be analysed' in response.get_json()['error']
    flow.state = worker(tray, status='awaiting_initial', first=frame(tray, 0, 0, position='first', index=0))
    assert 'No instruments were detected' in _register_initial(flow).get_json()['error']
    assert _lots(tray) == []


def test_initial_count_differing_from_the_kit_needs_explicit_acknowledgment(app, flow):
    tray = flow.tray
    flow.state['initial_frame'] = frame(tray, 8, 6, position='first', index=0)
    response = _register_initial(flow)
    assert response.status_code == 409 and 'the kit expects 15' in response.get_json()['error']
    assert _lots(tray) == []
    assert _register_initial(flow, acknowledge_mismatch='1').status_code == 200
    (lot,) = _lots(tray)
    assert lot['detected_quantity'] == 14 and lot['payload']['mismatch_acknowledged'] is True


def test_double_click_and_another_video_never_replace_the_initial_count(app, flow):
    tray = flow.tray
    assert _register_initial(flow).status_code == 200
    again = _register_initial(flow)
    assert again.status_code == 200 and again.get_json()['already_registered'] is True
    flow.state = worker(tray, status='awaiting_initial', sha='b' * 64,
                        first=frame(tray, 8, 6, position='first', index=0))
    response = _register_initial(flow, acknowledge_mismatch='1')
    assert response.status_code == 409 and 'cannot be replaced' in response.get_json()['error']
    assert [lot['detected_quantity'] for lot in _lots(tray)] == [15]


def test_play_is_refused_before_the_initial_count(app, flow):
    response = _post(flow, '/capture/play')
    assert response.status_code == 409 and 'Register the initial count' in response.get_json()['error']
    assert flow.process_calls == []
    _register_initial(flow)
    assert _post(flow, '/capture/play').status_code == 202
    assert flow.process_calls == ['vid-1']
    # Double click while processing: no second run.
    flow.state['status'] = 'processing'
    second = _post(flow, '/capture/play')
    assert second.status_code == 200 and second.get_json()['already_running'] is True
    assert flow.process_calls == ['vid-1']


def test_only_the_video_of_the_initial_count_can_continue(app, flow, monkeypatch):
    tray = flow.tray
    _register_initial(flow)
    flow.state = worker(tray, status='awaiting_initial', sha='c' * 64, first=frame(tray, 8, 7, position='first', index=0))
    response = _post(flow, '/capture/play')
    assert response.status_code == 409 and flow.process_calls == []

    uploaded = {'session': worker(tray, status='loading', sha='c' * 64)}
    monkeypatch.setattr(vision_bridge, 'store_video', lambda sid, f: uploaded)
    upload = flow.client.post(flow.base + '/capture/upload', data={'video': (__import__('io').BytesIO(b'x'), 'o.mp4')},
                              content_type='multipart/form-data')
    assert upload.status_code == 409 and flow.discarded == [tray.session.id]
    uploaded['session'] = worker(tray, status='loading', sha=SHA)   # same file again (worker restarted)
    upload = flow.client.post(flow.base + '/capture/upload', data={'video': (__import__('io').BytesIO(b'x'), 's.mp4')},
                              content_type='multipart/form-data')
    assert upload.status_code == 202


def test_final_count_waits_for_the_end_of_processing(app, flow):
    tray = flow.tray
    _register_initial(flow)
    _post(flow, '/capture/play')
    flow.state = worker(tray, status='processing', run_id='run-1', first=flow.state['initial_frame'])
    response = _post(flow, '/capture/final')
    assert response.status_code == 409 and 'still being processed' in response.get_json()['error']
    flow.state['status'], flow.state['last_error'] = 'error', 'Inference error: boom'
    flow.state['final_frame'] = {'ok': False, 'position': 'last', 'error': 'Inference error: boom'}
    response = _post(flow, '/capture/final')
    assert response.status_code == 409 and 'No final count was generated' in response.get_json()['error']
    assert [lot['payload']['trigger'] for lot in _lots(tray)] == ['initial']
    assert flow.client.get(flow.base + '/vision-validation').status_code == 302


def test_final_count_is_the_last_frame_and_keeps_the_initial_count(app, flow):
    tray = flow.tray
    _register_initial(flow)
    initial_before = _lots(tray)[0]['payload']
    _post(flow, '/capture/play')
    _finish_run(flow, 8, 7)   # live tallies of the worker say 3 + 2 / 30 boxes: ignored
    response = _post(flow, '/capture/final')
    assert response.status_code == 200 and response.get_json()['next_url'].endswith('/vision-validation')
    initial, final = _lots(tray)
    assert initial['payload'] == initial_before
    payload = final['payload']
    assert final['detected_quantity'] == 15
    assert (payload['trigger'], payload['source'], payload['frame_index'], payload['run_id']) == (
        'final', 'last_frame', 297, 'run-1')
    assert payload['video']['sha256'] == initial['payload']['video']['sha256']
    assert payload['frame_jpeg_b64'] == 'AAAA' and payload['frame_sha256']
    assert payload['matches_reference'] is True and payload['initial_count_event_id']
    assert 'session.final_count' in _audits(tray)
    # Double click: same answer, one final lot.
    again = _post(flow, '/capture/final')
    assert again.status_code == 200 and again.get_json()['already_confirmed'] is True
    assert len(_lots(tray)) == 2
    page = flow.client.get(flow.base + '/vision-validation').get_data(as_text=True)
    assert 'Initial (first frame)' in page and 'Final count (last frame): 15' in page


def test_initial_frame_quality_and_position_are_checked_on_server(app, flow):
    flow.state['initial_frame']['frame_index'] = 1
    assert _register_initial(flow).status_code == 409
    flow.state['initial_frame']['frame_index'] = 0
    flow.state['initial_frame']['quality'] = {'ok': False}
    assert _register_initial(flow).status_code == 409
    assert _lots(flow.tray) == []


def test_rejected_final_retry_does_not_duplicate_the_incident(app, flow):
    _register_initial(flow)
    _finish_run(flow, 8, 6)
    assert _post(flow, '/capture/final').status_code == 409
    assert _post(flow, '/capture/final').status_code == 409
    assert len(_events(flow.tray, 'close_blocked')) == 1


def test_final_count_from_another_video_is_refused(app, flow):
    _register_initial(flow)
    _finish_run(flow)
    flow.state['video']['sha256'] = 'd' * 64
    response = _post(flow, '/capture/final')
    assert response.status_code == 409 and 'same video' in response.get_json()['error']


def test_final_difference_needs_acknowledgment_and_goes_to_the_supervisor(app, flow):
    tray = flow.tray
    _register_initial(flow)
    _finish_run(flow, 8, 6)
    response = _post(flow, '/capture/final')
    assert response.status_code == 409 and 'reference count is 15' in response.get_json()['error']
    assert _post(flow, '/capture/final', acknowledge_mismatch='1').status_code == 200
    state = rf_session.vision_final_state(tray.session.id)
    assert state['confirmed'] and not state['matches'] and state['known_mismatch']['final'] == 14
    # Human validation cannot hide it: claiming 8 + 7 still documents the instrument YOLO did not see.
    assert _validate(flow, 8, 7).status_code == 400   # the correction needs a justification
    response = _validate(flow, 8, 7, b={'notes': 'Seven on the tray'})
    assert response.status_code == 302 and _status(tray) == 'correction_required'
    reasons = tray.c.execute(text(
        'SELECT r.code FROM discrepancy d JOIN cat_discrepancy_reason r ON r.id = d.reason_id '
        'WHERE d.session_id = :s'), {'s': tray.session.id}).scalars().all()
    assert reasons == ['shortage']
    assert rf_session.operator_close_eligibility(tray.session.id)['eligible'] is False


def test_unacknowledged_final_difference_cannot_be_hidden_by_a_contingency_report(app, flow):
    tray = flow.tray
    _register_initial(flow)
    _finish_run(flow, 8, 6)
    assert _post(flow, '/capture/final').status_code == 409   # rejected, recorded as close_blocked
    assert [e['payload']['reason'] for e in _events(tray, 'close_blocked')] == ['final_count_mismatch']
    rf_session.submit_manual_close(
        session_id=tray.session.id, operator_user_id=tray.t.operator_id, institution_id=tray.t.institution_id,
        reports=[{'family_id': str(tray.a), 'reported_quantity': 8}, {'family_id': str(tray.b), 'reported_quantity': 7}],
        close_path='manual_report', contingency={'reason_code': 'worker_unavailable'})
    assert _status(tray) == 'correction_required'
    reasons = tray.c.execute(text(
        'SELECT r.code FROM discrepancy d JOIN cat_discrepancy_reason r ON r.id = d.reason_id '
        'WHERE d.session_id = :s'), {'s': tray.session.id}).scalars().all()
    assert reasons == ['shortage']


def test_retired_two_capture_finish_freezes_nothing(app, flow):
    response = flow.client.post(flow.base + '/capture/finish')
    assert response.status_code == 302 and _lots(flow.tray) == []


def test_capture_page_shows_the_new_steps(app, flow):
    page = flow.client.get(flow.base + '/capture').get_data(as_text=True)
    for label in ('Upload video', 'Register Initial Count', 'Run YOLO', 'Confirm Final Count'):
        assert label in page
    assert 'autoplay' not in page
    assert '<video' not in page and 'capture-player' not in page
    assert 'capture-frame' in page
    assert 'player.ended' not in page and 'createObjectURL' not in page
    _register_initial(flow)
    page = flow.client.get(flow.base + '/capture').get_data(as_text=True)
    assert 'Initial count registered: 15 instruments.' in page
    state = flow.client.get(flow.base + '/capture/state').get_json()
    assert state['pef']['initial'] == {'total': 15, 'reference': 15, 'frame_index': 0, 'video_sha256': SHA,
                                       'source': 'first_frame'}


# --- Ajuste 2: Operator close --------------------------------------------------------------------


def _reserved_piece(tray):
    c, w, t = tray.c, tray.w, tray.t
    piece = t.add_instrument('reserved')
    operation_id = c.execute(text('SELECT operation_id FROM work_session WHERE id = :s'),
                             {'s': tray.session.id}).scalar_one()
    c.execute(text('INSERT INTO instrument_reservation (id, operation_id, instrument_id, reserved_at, active) '
                   'VALUES (:id, :o, :p, now(), TRUE)'), {'id': uuid.uuid4(), 'o': operation_id, 'p': piece})
    return piece


def test_happy_path_operator_closes_safely(app, flow):
    tray = flow.tray
    piece = _reserved_piece(tray)
    response = _happy_path(flow)
    assert response.status_code == 302 and _status(tray) == 'awaiting_spd_review'
    done = flow.client.get(flow.base + '/manual-report/done').get_data(as_text=True)
    assert 'Close Session' in done and 'Supervisor approval is not required' in done

    response = _close(flow)
    assert response.status_code == 302 and _status(tray) == 'closed'
    (close,) = _events(tray, 'session_close')
    assert close['user_id'] == tray.t.operator_id
    payload = close['payload']
    assert (payload['closed_by'], payload['safe_close_confirmed'], payload['physical_integrity_confirmed']) == (
        'station_operator', True, True)
    assert payload['unreconciled_physical_shortages'] == []
    assert payload['reconciliation']['validated_total'] == payload['reconciliation']['final_total'] == 15
    closed_by = tray.c.execute(text('SELECT closed_by_user_id FROM work_session WHERE id = :s'),
                               {'s': tray.session.id}).scalar_one()
    assert closed_by == tray.t.operator_id
    assert 'session.operator_close' in _audits(tray)
    cycle = tray.c.execute(text(
        'SELECT s.code FROM instrument i JOIN cat_instrument_cycle_status s ON s.id = i.cycle_status_id '
        'WHERE i.id = :p'), {'p': piece}).scalar_one()
    active = tray.c.execute(text('SELECT active FROM instrument_reservation WHERE instrument_id = :p'),
                            {'p': piece}).scalar_one()
    assert (cycle, active) == ('sterilization', False)
    # History of the Operator and the Supervisor's read both show it closed.
    with app.test_request_context():
        rows = past_session_rows(view_data.operator_my_sessions(tray.t.operator_id, tray.t.institution_id))
        assert any(row['id'] == str(tray.session.id) and row['status_code'] == 'closed' for row in rows)
        detail = view_data.supervisor_session_detail(tray.session.id, tray.t.institution_id)
        assert detail['status_code'] == 'closed'


def test_corrected_yolo_false_positive_still_closes(app, flow):
    """YOLO's last frame calls one B instrument an A (9 + 6); the Operator corrects it with a reason."""
    tray = flow.tray
    _happy_path(flow, final=(9, 6), validated=(8, 7), a={'notes': 'One forceps detected as family A'},
                b={'notes': 'Same instrument, belongs to B'})
    corrections = tray.c.execute(text(
        'SELECT hc.justification, hc.user_id FROM human_correction hc JOIN count_event ce '
        'ON ce.id = hc.count_event_id WHERE ce.session_id = :s'), {'s': tray.session.id}).all()
    assert len(corrections) == 2 and all(user == tray.t.operator_id for _, user in corrections)
    final = _lots(tray)[-1]['payload']
    assert {f['family_id']: f['detected_quantity'] for f in final['board']['families']} == {
        str(tray.a): 9, str(tray.b): 6}   # YOLO's prediction is kept as it was
    assert _close(flow).status_code == 302 and _status(tray) == 'closed'


def test_physical_shortage_goes_to_the_supervisor_and_keeps_the_unsafe_close_rule(app, flow):
    tray = flow.tray
    _register_initial(flow)
    _finish_run(flow, 8, 6)
    _post(flow, '/capture/final', acknowledge_mismatch='1')
    _validate(flow, 8, 6, b={'reason': 'shortage', 'notes': 'Missing after the procedure'})
    assert _status(tray) == 'correction_required'
    response = _close(flow)
    assert response.status_code == 302 and _status(tray) == 'correction_required'
    assert _events(tray, 'session_close') == []
    (disc_id,) = tray.c.execute(text('SELECT id FROM discrepancy WHERE session_id = :s'),
                                {'s': tray.session.id}).scalars().all()
    kwargs = dict(discrepancy_id=disc_id, session_id=tray.session.id, supervisor_user_id=tray.t.supervisor_id,
                  institution_id=tray.t.institution_id)
    with pytest.raises(RfSessionError) as exc:
        rf_session.resolve_discrepancy(notes='Found it', **kwargs)
    assert 'cannot be resolved with notes only' in exc.value.message
    rf_session.resolve_discrepancy(notes='Not found', mark_lost=True, **kwargs)
    close = dict(session_id=tray.session.id, supervisor_user_id=tray.t.supervisor_id,
                 institution_id=tray.t.institution_id, material_recovered=True)
    with pytest.raises(RfSessionError):
        rf_session.confirm_spd_close(**close)
    rf_session.confirm_spd_close(unreconciled_shortage_acknowledged=True, **close)
    (event,) = _events(tray, 'session_close')
    assert event['payload']['safe_close_confirmed'] is False
    assert event['payload']['unreconciled_physical_shortages'] == [str(disc_id)]


def test_same_total_but_different_families_needs_the_supervisor(app, flow):
    tray = flow.tray
    _happy_path(flow, validated=(9, 6), a={'reason': 'surplus', 'notes': 'Nine on the tray'},
                b={'reason': 'shortage', 'notes': 'Six on the tray'})
    assert _status(tray) == 'correction_required'
    eligibility = rf_session.operator_close_eligibility(tray.session.id)
    assert {'family_mismatch', 'discrepancies_recorded'} <= set(eligibility['reasons'])
    assert _close(flow).status_code == 302 and _status(tray) == 'correction_required'


def test_instrument_outside_the_kit_needs_the_supervisor(app, flow):
    tray = flow.tray
    _register_initial(flow)
    _finish_run(flow, 8, 7)
    _post(flow, '/capture/final')
    _validate(flow, 8, 7, extra=1)
    assert _status(tray) == 'correction_required'
    with pytest.raises(RfSessionError):
        rf_session.operator_close_session(session_id=tray.session.id, operator_user_id=tray.t.operator_id,
                                          institution_id=tray.t.institution_id, physical_integrity_confirmed=True,
                                          material_recovered=True)


def test_out_of_kit_detection_dismissed_by_the_human_is_not_autonomous(app, flow):
    tray = flow.tray
    _register_initial(flow)
    _finish_run(flow, 8, 6, boxes=15, out_of_kit=[{'family_code': 'X', 'count': 1}])
    _post(flow, '/capture/final')
    _validate(flow, 8, 7, b={'notes': 'YOLO missed one; the foreign box is a towel clip'})
    assert _status(tray) == 'awaiting_spd_review'   # no discrepancy, but not an Operator close
    eligibility = rf_session.operator_close_eligibility(tray.session.id)
    assert eligibility['reasons'] == ['out_of_kit_detected']
    done = flow.client.get(flow.base + '/manual-report/done').get_data(as_text=True)
    assert 'Supervisor review required' in done and 'YOLO detected instruments outside the kit' in done
    assert _close(flow).status_code == 302 and _status(tray) == 'awaiting_spd_review'


def test_other_operator_cannot_close(app, flow, login_as):
    tray = flow.tray
    _happy_path(flow)
    other = tray.t.supervisor_id   # any user that is not the assigned Operator
    with pytest.raises(RfSessionError) as exc:
        rf_session.operator_close_session(session_id=tray.session.id, operator_user_id=other,
                                          institution_id=tray.t.institution_id, physical_integrity_confirmed=True,
                                          material_recovered=True)
    assert exc.value.status_code == 403
    with pytest.raises(RfSessionError) as exc:   # another institution: not found
        rf_session.operator_close_session(session_id=tray.session.id, operator_user_id=tray.w.b.operator_id,
                                          institution_id=tray.w.b.institution_id, physical_integrity_confirmed=True,
                                          material_recovered=True)
    assert exc.value.status_code == 404
    login_as(tray.w.b.operator_id, tray.w.b.institution_id, 'station_operator')
    g.pop('verified_user', None)   # the pg fixture keeps one app context: drop the cached identity
    response = flow.client.post(flow.base + '/close-session', data={'physical_integrity_confirmed': '1',
                                                                    'material_recovered': '1'})
    assert response.status_code == 404
    assert _status(tray) == 'awaiting_spd_review'


def test_close_requires_the_physical_confirmation(app, flow):
    tray = flow.tray
    _happy_path(flow)
    for missing in ('physical_integrity_confirmed', 'material_recovered'):
        _close(flow, **{missing: ''})
        assert _status(tray) == 'awaiting_spd_review'
    assert _events(tray, 'session_close') == []


def test_duplicate_close_is_idempotent(app, flow):
    tray = flow.tray
    _happy_path(flow)
    assert _close(flow).status_code == 302
    second = rf_session.operator_close_session(
        session_id=tray.session.id, operator_user_id=tray.t.operator_id, institution_id=tray.t.institution_id,
        physical_integrity_confirmed=True, material_recovered=True)
    assert second['already_closed'] is True
    assert len(_events(tray, 'session_close')) == 1
    assert _audits(tray).count('session.operator_close') == 1


def test_contingency_report_is_never_an_operator_close(app, flow):
    tray = flow.tray
    _register_initial(flow)
    rf_session.submit_manual_close(
        session_id=tray.session.id, operator_user_id=tray.t.operator_id, institution_id=tray.t.institution_id,
        reports=[{'family_id': str(tray.a), 'reported_quantity': 8}, {'family_id': str(tray.b), 'reported_quantity': 7}],
        close_path='manual_report', contingency={'reason_code': 'worker_unavailable'})
    assert _status(tray) == 'awaiting_spd_review'
    reasons = rf_session.operator_close_eligibility(tray.session.id)['reasons']
    assert 'contingency' in reasons and 'missing_final' in reasons
