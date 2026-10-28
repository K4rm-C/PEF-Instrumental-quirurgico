"""Initial / final YOLO count rule of the RF Operator vision flow.

The first completed run of a vision session is the initial reference count; human validation
opens only after a later capture detects the same total (totals only, not per family).
Real PostgreSQL schema inside a rolled-back transaction (see test_multitenant); skipped
when PostgreSQL is unreachable.
"""

import uuid

import pytest
from sqlalchemy import text

from services import rf_session, vision_bridge
from services.rf_session import RfSessionError

from .test_multitenant import login_as, pg, world  # noqa: F401  (pytest fixtures)


# --- helpers ------------------------------------------------------------------------------


@pytest.fixture
def tray(pg, world):
    """A vision session of institution A whose tray holds families A (8) and B (7): 15 pieces."""
    c, w, t = pg, world, world.a
    category_id = c.execute(text('SELECT category_id FROM instrument_family WHERE id = :id'),
                            {'id': w.family_id}).scalar_one()
    family_b = uuid.uuid4()
    c.execute(text('INSERT INTO instrument_family (id, code, name, category_id, active) '
                   'VALUES (:id, :code, :n, :cat, TRUE)'),
              {'id': family_b, 'code': f'ZZ_B_{w.tag}', 'n': 'MT family B', 'cat': category_id})
    session = t.add_session('in_progress')
    c.execute(text("UPDATE work_session SET capture_mode = 'vision' WHERE id = :id"), {'id': session.id})
    c.execute(text('UPDATE expected_inventory SET expected_quantity = 8 WHERE session_id = :s'), {'s': session.id})
    c.execute(text("INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source) "
                   "VALUES (:s, :f, 7, 'kit_snapshot')"), {'s': session.id, 'f': family_b})
    codes = {fid: c.execute(text('SELECT code FROM instrument_family WHERE id = :id'), {'id': fid}).scalar_one()
             for fid in (w.family_id, family_b)}
    tray = type('Tray', (), {})()
    tray.c, tray.w, tray.t, tray.session = c, w, t, session
    tray.a, tray.b, tray.codes = w.family_id, family_b, codes
    yield tray
    rf_session.clear_vision_snapshot(session.id)


def _run(tray, *, run_id, a, b, boxes=None, status='ready'):
    """Worker state of one run: per-family tally (a, b) and `boxes` instruments in the last capture."""
    counts = {
        tray.codes[tray.a]: {'family_id': str(tray.a), 'family_code': tray.codes[tray.a],
                             'expected_quantity': 8, 'detected_quantity': a},
        tray.codes[tray.b]: {'family_id': str(tray.b), 'family_code': tray.codes[tray.b],
                             'expected_quantity': 7, 'detected_quantity': b},
    }
    total = a + b if boxes is None else boxes
    return {'status': status, 'run_id': run_id, 'frames_processed': 20, 'model_version': 'test-model',
            'counts': counts,
            # Like the real worker: every box carries YOLO's own family (raw), assigned or not.
            'boxes': [{'family_code': None, 'raw': {'family_code': tray.codes[tray.a]}, 'confidence': 0.9,
                       'xyxy': [10.0 * i, 0.0, 10.0 * i + 8, 8.0]} for i in range(total)]}


def _finish(tray, worker):
    return rf_session.finish_vision_counting(session_id=tray.session.id, operator_user_id=tray.t.operator_id,
                                             worker_session=worker)


def _events(tray, code):
    return tray.c.execute(text(
        'SELECT ce.expected_quantity, ce.detected_quantity, ce.payload FROM count_event ce '
        'JOIN cat_event_type t ON t.id = ce.event_type_id WHERE ce.session_id = :s AND t.code = :code '
        'ORDER BY ce.occurred_at'), {'s': tray.session.id, 'code': code}).mappings().all()


def _status(tray):
    return tray.c.execute(text(
        'SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id WHERE w.id = :id'),
        {'id': tray.session.id}).scalar_one()


def _report(tray, qty_a, qty_b, a=None, b=None):
    """Operator report: quantities of families A and B, with optional reason/notes per family."""
    return rf_session.submit_manual_close(
        session_id=tray.session.id, operator_user_id=tray.t.operator_id, institution_id=tray.t.institution_id,
        reports=[{'family_id': str(tray.a), 'reported_quantity': qty_a, **(a or {})},
                 {'family_id': str(tray.b), 'reported_quantity': qty_b, **(b or {})}],
        close_path='vision_validation',
    )


def _open_discrepancies(tray):
    return tray.c.execute(text(
        'SELECT family_id, expected_quantity, detected_quantity FROM discrepancy WHERE session_id = :s '
        'AND NOT resolved'), {'s': tray.session.id}).all()


# --- Required cases ---------------------------------------------------------------------------


def test_first_completed_run_registers_the_initial_count(app, tray):
    result = _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    assert result == {'stage': 'initial', 'session_id': str(tray.session.id), 'initial_total': 15}
    initial = rf_session.initial_vision_count(tray.session.id)
    assert (initial['total'], initial['run_id']) == (15, 'r-initial')
    assert rf_session.get_vision_snapshot(tray.session.id) is None  # no human validation yet
    assert _status(tray) == 'in_progress'


def test_initial_15_final_14_blocks_validation(app, tray):
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    with pytest.raises(RfSessionError) as exc:
        _finish(tray, _run(tray, run_id='r-final', a=8, b=6))
    assert exc.value.status_code == 409
    assert exc.value.params == {'final': 14, 'initial': 15}
    assert rf_session.get_vision_snapshot(tray.session.id) is None
    assert [e['payload']['trigger'] for e in _events(tray, 'auto_count')] == ['initial']
    blocked = _events(tray, 'close_blocked')
    assert [(e['expected_quantity'], e['detected_quantity'], e['payload']['reason']) for e in blocked] == [
        (15, 14, 'final_count_mismatch')]
    assert _status(tray) == 'in_progress'


def test_initial_15_final_15_allows_validation(app, tray, client, login_as, monkeypatch):
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    snapshot = _finish(tray, _run(tray, run_id='r-final', a=8, b=7))
    assert (snapshot['stage'], snapshot['initial_total'], snapshot['final_total']) == ('final', 15, 15)
    final = _events(tray, 'auto_count')[-1]
    assert (final['payload']['trigger'], final['detected_quantity'], final['payload']['run_id']) == ('final', 15, 'r-final')
    login_as(tray.t.operator_id, tray.t.institution_id, 'station_operator')
    assert client.get(f'/operator/sessions/{tray.session.id}/vision-validation').status_code == 200


def test_matching_total_with_wrong_classification_goes_to_human_review(app, tray):
    """YOLO 15/15 but 10 A + 5 B: allowed; the Operator corrects the classification to 8 + 7."""
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    snapshot = _finish(tray, _run(tray, run_id='r-final', a=10, b=5))
    assert snapshot['stage'] == 'final'
    result = _report(tray, 8, 7, a={'notes': 'Two retractors labelled as forceps'},
                     b={'notes': 'Two forceps labelled as retractors'})
    corrections = tray.c.execute(text(
        "SELECT ce.payload->>'ai_suggested_quantity', ce.detected_quantity FROM human_correction hc "
        'JOIN count_event ce ON ce.id = hc.count_event_id WHERE ce.session_id = :s ORDER BY 1'),
        {'s': tray.session.id}).all()
    assert sorted(corrections) == [('10', 8), ('5', 7)]
    assert result['status_code'] == 'awaiting_spd_review' and _open_discrepancies(tray) == []


def test_yolo_error_never_uses_previous_counts(app, tray):
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    # The worker still exposes the 15 boxes of an earlier run, but the current run failed.
    stale = _run(tray, run_id='r-failed', a=8, b=7, status='error')
    with pytest.raises(RfSessionError) as exc:
        _finish(tray, stale)
    assert 'processing failed' in exc.value.message
    assert [e['payload']['trigger'] for e in _events(tray, 'auto_count')] == ['initial']
    assert _events(tray, 'close_blocked') == []  # technical error, not a count mismatch
    assert rf_session.get_vision_snapshot(tray.session.id) is None


def test_retry_after_14_of_15_uses_only_the_new_capture(app, tray):
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    with pytest.raises(RfSessionError):
        _finish(tray, _run(tray, run_id='r-try-1', a=8, b=6))
    with pytest.raises(RfSessionError):  # same rejected run again: still 14
        _finish(tray, _run(tray, run_id='r-try-1', a=8, b=6))
    snapshot = _finish(tray, _run(tray, run_id='r-try-2', a=8, b=7))
    assert snapshot['final_total'] == 15
    final = _events(tray, 'auto_count')[-1]
    assert (final['payload']['run_id'], final['payload']['trigger']) == ('r-try-2', 'final')
    assert [e['detected_quantity'] for e in _events(tray, 'close_blocked')] == [14, 14]
    assert rf_session.ai_suggestion_for_session(tray.session.id)[1] == {str(tray.a): 8, str(tray.b): 7}


def test_initial_capture_cannot_be_reused_as_final(app, tray):
    initial = _run(tray, run_id='r-initial', a=8, b=7)
    _finish(tray, initial)
    with pytest.raises(RfSessionError) as exc:
        _finish(tray, initial)
    assert 'initial capture cannot be used' in exc.value.message
    assert rf_session.get_vision_snapshot(tray.session.id) is None


def test_total_counts_unclassified_instruments(app, tray):
    """16 pieces on a 15-piece kit: family tallies are capped at the quotas (8 + 7), the capture has 16."""
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    with pytest.raises(RfSessionError) as exc:
        _finish(tray, _run(tray, run_id='r-final', a=8, b=7, boxes=16))
    assert exc.value.params == {'final': 16, 'initial': 15}


def test_empty_capture_is_not_an_initial_count(app, tray):
    with pytest.raises(RfSessionError) as exc:
        _finish(tray, _run(tray, run_id='r-initial', a=0, b=0))
    assert 'No instruments were detected' in exc.value.message
    assert rf_session.initial_vision_count(tray.session.id) is None


def test_manual_sessions_keep_their_behaviour(app, world, pg):
    c, w, t = pg, world, world.a
    session = t.add_session('in_progress')  # manual_no_privacy
    with pytest.raises(RfSessionError) as exc:
        rf_session.finish_vision_counting(session_id=session.id, operator_user_id=t.operator_id,
                                          worker_session={'status': 'ready', 'run_id': 'r', 'frames_processed': 1,
                                                          'counts': {'X': {}}, 'boxes': [{}]})
    assert exc.value.status_code == 409
    result = rf_session.submit_manual_close(
        session_id=session.id, operator_user_id=t.operator_id, institution_id=t.institution_id,
        reports=[{'family_id': str(w.family_id), 'reported_quantity': 0, 'reason_code': 'shortage', 'notes': 'lost'}])
    assert result['status_code'] == 'correction_required' and result['open_discrepancies'] == 1
    assert c.execute(text('SELECT count(*) FROM discrepancy WHERE session_id = :s AND family_id IS NULL'),
                     {'s': session.id}).scalar_one() == 0


# --- Point 8: a physically missing instrument is never a safe close ---------------------------


def test_missing_instrument_found_in_review_escalates(app, tray):
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    _finish(tray, _run(tray, run_id='r-final', a=8, b=7))
    result = _report(tray, 8, 6, b={'reason_code': 'shortage', 'notes': 'Retractor not on the tray'})
    assert result['status_code'] == 'correction_required'
    assert _open_discrepancies(tray) == [(tray.b, 7, 6)]  # one discrepancy, no duplicate session-level row
    with pytest.raises(RfSessionError):  # SPD cannot close while it is open
        rf_session.confirm_spd_close(session_id=tray.session.id, supervisor_user_id=tray.t.supervisor_id,
                                     institution_id=tray.t.institution_id, material_recovered=True)


def test_human_total_below_initial_escalates_even_if_families_match(app, tray, client, login_as):
    """Initial 16 (an extra piece at the start), kit 15, human confirms 15: one piece is unaccounted for."""
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7, boxes=16))
    _finish(tray, _run(tray, run_id='r-final', a=8, b=7, boxes=16))
    result = _report(tray, 8, 7, a={'notes': 'Extra piece not found'})
    assert result['status_code'] == 'correction_required'
    assert _open_discrepancies(tray) == [(None, 16, 15)]
    # The SPD reconciliation flow handles the session-level discrepancy.
    login_as(tray.t.supervisor_id, tray.t.institution_id, 'spd_supervisor')
    assert client.get(f'/supervisor/discrepancies/{tray.session.id}/review').status_code == 200
    disc_id = tray.c.execute(text('SELECT id FROM discrepancy WHERE session_id = :s'), {'s': tray.session.id}).scalar_one()
    rf_session.resolve_discrepancy(discrepancy_id=disc_id, session_id=tray.session.id,
                                   supervisor_user_id=tray.t.supervisor_id, institution_id=tray.t.institution_id,
                                   notes='Piece found in the sterile field', mark_recovered=True)
    assert _status(tray) == 'awaiting_spd_review'


# --- Capture screen: reason shown to the Operator --------------------------------------------


def test_capture_page_shows_stage_and_mismatch_reason(app, tray, client, login_as, monkeypatch):
    """Single-video flow on the capture page (detailed cases: test_single_video_flow.py)."""
    monkeypatch.setattr(vision_bridge, 'worker_health', lambda: {'ok': False, 'error': 'down'})

    def edge(position, a, b, run_id=None):
        analysed = {'ok': True, 'position': position, 'video_id': 'v', 'run_id': run_id, 'frame_index': 0,
                    **{k: v for k, v in _run(tray, run_id=run_id, a=a, b=b).items() if k in ('counts', 'boxes')}}
        return {'status': 'ready' if run_id else 'awaiting_initial', 'video_id': 'v', 'run_id': run_id,
                'video': {'sha256': 'e' * 64}, 'initial_frame': analysed if position == 'first' else None,
                'final_frame': analysed if position == 'last' else None}
    worker = {'session': edge('first', 8, 7)}
    monkeypatch.setattr(vision_bridge, 'fetch_state', lambda sid: {'ok': True, **worker})
    login_as(tray.t.operator_id, tray.t.institution_id, 'station_operator')
    url = f'/operator/sessions/{tray.session.id}/capture'

    assert 'Register Initial Count' in client.get(url).get_data(as_text=True)
    client.post(url + '/initial')
    page = client.get(url).get_data(as_text=True)
    assert 'Initial count registered: 15 instruments.' in page

    worker['session'] = edge('last', 8, 6, run_id='r-final')
    response = client.post(url + '/final')
    assert response.status_code == 409
    assert 'The last frame shows 14 instruments; the reference count is 15' in response.get_json()['error']
    assert client.get(f'/operator/sessions/{tray.session.id}/vision-validation').status_code == 302
