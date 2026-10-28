"""Vision finish guard, AI / human count traceability and the RF Supervisor Audit Log.

DB tests reuse the multi-tenant fixtures: real PostgreSQL schema, temporary institutions inside
one outer transaction rolled back at teardown (seeds are neither required nor modified).
Skipped (with the reason) when PostgreSQL is unreachable.
"""

import uuid

import pytest
from sqlalchemy import text

from services import rf_session, vision_bridge
from services.admin_local.dashboard import AI_HUMAN_AGREEMENT_SQL
from services.rf_session import RfSessionError, vision_result_problem

from .test_multitenant import login_as, pg, world  # noqa: F401  (pytest fixtures)


def _worker_session(family_id, family_code, detected, *, status='ready', run_id='run-1', frames=12, boxes=None):
    """Worker state of a run. boxes = instruments in the last capture (default: one per detection;
    more boxes than detections = instruments YOLO saw but did not classify into the family)."""
    total = detected if boxes is None else boxes
    return {
        'status': status, 'run_id': run_id, 'frames_processed': frames,
        'model_version': 'test-model', 'pipeline': 'matrix_v1',
        'counts': {family_code: {'family_id': str(family_id), 'family_code': family_code,
                                 'expected_quantity': 1, 'detected_quantity': detected}},
        'boxes': [{'family_code': family_code if i < detected else None} for i in range(total)],
    }


# --- 1. Finish guard (no database) --------------------------------------------------------


@pytest.mark.parametrize('state, expected', [
    ({'status': 'processing', 'run_id': 'r', 'frames_processed': 3, 'counts': {'X': {}}}, 'still being processed'),
    # Stale tallies of an earlier run must not make a failed run usable.
    ({'status': 'error', 'run_id': 'r', 'frames_processed': 9, 'counts': {'X': {}}}, 'processing failed'),
    ({'status': 'idle', 'run_id': None, 'counts': {}}, 'Upload and process a video'),
    ({'status': 'idle', 'run_id': 'r', 'frames_processed': 9, 'counts': {'X': {}}}, 'Upload and process a video'),
    ({}, 'Upload and process a video'),
    ({'status': 'ready', 'run_id': 'r', 'frames_processed': 0, 'counts': {'X': {}}}, 'produced no count'),
    ({'status': 'ready', 'run_id': 'r', 'frames_processed': 5, 'counts': {}}, 'produced no count'),
])
def test_vision_result_problem_blocks_unusable_runs(state, expected):
    assert expected in vision_result_problem(state)


def test_vision_result_problem_accepts_completed_run():
    assert vision_result_problem(_worker_session(uuid.uuid4(), 'F', 1)) is None


# --- helpers ------------------------------------------------------------------------------


def _vision_session(c, w, t):
    session = t.add_session('in_progress')
    c.execute(text("UPDATE work_session SET capture_mode = 'vision' WHERE id = :id"), {'id': session.id})
    return session


def _family_code(c, w):
    return c.execute(text('SELECT code FROM instrument_family WHERE id = :id'), {'id': w.family_id}).scalar_one()


def _auto_counts(c, session_id):
    return c.execute(text(
        "SELECT ce.payload FROM count_event ce JOIN cat_event_type t ON t.id = ce.event_type_id "
        "WHERE ce.session_id = :s AND t.code = 'auto_count' ORDER BY ce.occurred_at"), {'s': session_id}).scalars().all()


def _manual_count(c, session_id):
    return c.execute(text(
        "SELECT ce.id, ce.expected_quantity, ce.detected_quantity, ce.payload FROM count_event ce "
        "JOIN cat_event_type t ON t.id = ce.event_type_id WHERE ce.session_id = :s AND t.code = 'manual_count'"),
        {'s': session_id}).mappings().one()


def _corrections(c, session_id):
    return c.execute(text(
        'SELECT hc.justification FROM human_correction hc JOIN count_event ce ON ce.id = hc.count_event_id '
        'WHERE ce.session_id = :s'), {'s': session_id}).scalars().all()


def _open_discrepancies(c, session_id):
    return c.execute(text('SELECT count(*) FROM discrepancy WHERE session_id = :s AND NOT resolved'),
                     {'s': session_id}).scalar_one()


def _close(t, session, reported, **extra):
    """Human validation after a final YOLO count (the path is ignored by manual sessions)."""
    return rf_session.submit_manual_close(
        session_id=session.id, operator_user_id=t.operator_id, institution_id=t.institution_id,
        reports=[{'family_id': str(extra.pop('family_id')), 'reported_quantity': reported, **extra}],
        close_path='vision_validation',
    )


# --- 2. Finish route: backend validation ----------------------------------------------------


def _edge_worker(family_id, family_code, detected, *, status, position, run_id=None):
    """Single-video worker state: the edge frame (first / last) analysed by YOLO."""
    analysed = {'ok': True, 'position': position, 'video_id': 'vid-1', 'run_id': run_id, 'frame_index': 0,
                'quality': {'ok': True}, 'frame_jpeg_b64': 'AAAA',
                'model_version': 'test-model', 'boxes': [{'family_code': family_code}] * detected,
                'counts': {family_code: {'family_id': str(family_id), 'family_code': family_code,
                                         'expected_quantity': 1, 'detected_quantity': detected}}}
    return {'status': status, 'video_id': 'vid-1', 'run_id': run_id, 'video': {'sha256': 'f' * 64},
            'initial_frame': analysed if position == 'first' else None,
            'final_frame': analysed if position == 'last' else None}


def test_initial_count_with_worker_error_keeps_session_and_stores_nothing(app, world, client, login_as,
                                                                         monkeypatch, pg):
    c, w, t = pg, world, world.a
    session = _vision_session(c, w, t)
    failed = {'status': 'error', 'error_stage': 'first_frame', 'video_id': 'vid-1', 'last_error': 'decode',
              'initial_frame': {'ok': False, 'position': 'first', 'error': 'Could not open video file'}}
    monkeypatch.setattr(vision_bridge, 'fetch_state', lambda sid: {'ok': True, 'session': failed})
    login_as(t.operator_id, t.institution_id, 'station_operator')

    response = client.post(f'/operator/sessions/{session.id}/capture/initial')

    assert response.status_code == 409
    assert 'could not be analysed' in response.get_json()['error']  # controlled message
    assert rf_session.get_vision_snapshot(session.id) is None
    assert _auto_counts(c, session.id) == []
    page = client.get(f'/operator/sessions/{session.id}/capture').get_data(as_text=True)
    # Retry and manual fallback remain available on the capture page.
    assert 'capture-upload-form' in page and f'/operator/sessions/{session.id}/manual-report' in page
    assert client.get(f'/operator/sessions/{session.id}/detection-summary').status_code == 302


def test_edge_counts_with_unreachable_worker_show_controlled_message(app, world, client, login_as, monkeypatch, pg):
    c, w, t = pg, world, world.a
    session = _vision_session(c, w, t)

    def down(sid):
        raise vision_bridge.VisionBridgeError('Vision worker unreachable: connection refused', 503)
    monkeypatch.setattr(vision_bridge, 'fetch_state', down)
    login_as(t.operator_id, t.institution_id, 'station_operator')

    for step in ('initial', 'final'):
        response = client.post(f'/operator/sessions/{session.id}/capture/{step}')
        assert response.status_code == 503
        assert 'The vision worker is not available' in response.get_json()['error']
        assert 'connection refused' not in response.get_json()['error']
    assert _auto_counts(c, session.id) == []


def test_edge_counts_freeze_initial_then_final_ai_count(app, world, client, login_as, monkeypatch, pg):
    c, w, t = pg, world, world.a
    session = _vision_session(c, w, t)
    code = _family_code(c, w)
    worker = {'session': _edge_worker(w.family_id, code, 1, status='awaiting_initial', position='first')}
    monkeypatch.setattr(vision_bridge, 'fetch_state', lambda sid: {'ok': True, **worker})
    login_as(t.operator_id, t.institution_id, 'station_operator')
    try:
        response = client.post(f'/operator/sessions/{session.id}/capture/initial')
        assert response.status_code == 200 and response.get_json()['initial_total'] == 1
        worker['session'] = _edge_worker(w.family_id, code, 1, status='ready', position='last', run_id='run-1')
        response = client.post(f'/operator/sessions/{session.id}/capture/final')
        assert response.get_json()['next_url'].endswith(f'/operator/sessions/{session.id}/vision-validation')
        payloads = _auto_counts(c, session.id)
        assert [(p['trigger'], p['source']) for p in payloads] == [('initial', 'first_frame'), ('final', 'last_frame')]
        assert rf_session.ai_suggestion_for_session(session.id)[1] == {str(w.family_id): 1}
    finally:
        rf_session.clear_vision_snapshot(session.id)


def test_finish_service_rejects_error_run_directly(app, world, pg):
    c, w, t = pg, world, world.a
    session = _vision_session(c, w, t)
    with pytest.raises(RfSessionError) as exc:
        rf_session.finish_vision_counting(session_id=session.id, operator_user_id=t.operator_id,
                                          worker_session=_worker_session(w.family_id, 'X', 1, status='error'))
    assert exc.value.status_code == 409
    assert _auto_counts(c, session.id) == []


# --- 3. Expected vs AI vs human ---------------------------------------------------------------


def _finish(w, t, session, detected, code):
    """Initial count, then a matching final count whose family tally is `detected` (at least one
    instrument is always on the tray; an unclassified one when detected is 0)."""
    total = max(detected, 1)
    for run_id in ('run-initial', 'run-final'):
        rf_session.finish_vision_counting(
            session_id=session.id, operator_user_id=t.operator_id,
            worker_session=_worker_session(w.family_id, code, detected, run_id=run_id, boxes=total))


def test_vision_close_keeps_expected_ai_and_human_apart(app, world, pg):
    """Expected 1, AI 0, human 1 -> human_correction (AI != human), no discrepancy (human = expected)."""
    c, w, t = pg, world, world.a
    session = _vision_session(c, w, t)
    _finish(w, t, session, 0, _family_code(c, w))
    try:
        with pytest.raises(RfSessionError) as exc:  # a correction needs a justification
            _close(t, session, 1, family_id=w.family_id)
        assert 'differs from the AI detection' in exc.value.message
        _close(t, session, 1, family_id=w.family_id, notes='Piece hidden under a towel')
    finally:
        rf_session.clear_vision_snapshot(session.id)

    row = _manual_count(c, session.id)
    assert (row['expected_quantity'], row['detected_quantity']) == (1, 1)  # expected, human (DS04)
    assert row['payload']['ai_suggested_quantity'] == 0                   # AI, kept apart
    assert row['payload']['reported_quantity'] == 1                       # legacy readers
    assert uuid.UUID(row['payload']['ai_count_event_id'])
    assert _corrections(c, session.id) == ['Piece hidden under a towel']
    assert _open_discrepancies(c, session.id) == 0


def test_vision_close_ai_equals_human_records_no_correction(app, world, pg):
    """Expected 1, AI 0, human 0 -> discrepancy (human < expected) but no human_correction."""
    c, w, t = pg, world, world.a
    session = _vision_session(c, w, t)
    _finish(w, t, session, 0, _family_code(c, w))
    try:
        result = _close(t, session, 0, family_id=w.family_id, reason_code='shortage')
    finally:
        rf_session.clear_vision_snapshot(session.id)
    assert result['status_code'] == 'correction_required'
    assert _corrections(c, session.id) == []
    assert _open_discrepancies(c, session.id) == 1
    assert _manual_count(c, session.id)['detected_quantity'] == 0


def test_manual_session_results_are_unchanged(app, world, pg):
    """manual_no_privacy keeps the legacy rules: discrepancy and correction vs the expected quantity."""
    c, w, t = pg, world, world.a
    session = t.add_session('in_progress')  # manual_no_privacy
    result = _close(t, session, 0, family_id=w.family_id, reason_code='shortage', notes='lost')
    row = _manual_count(c, session.id)
    assert result == {'session_id': str(session.id), 'status_code': 'correction_required', 'open_discrepancies': 1}
    assert row['payload']['reported_quantity'] == 0 and row['detected_quantity'] == 0
    assert 'ai_suggested_quantity' not in row['payload'] and row['payload']['ai'] is False
    assert _corrections(c, session.id) == ['shortage: lost']
    assert _open_discrepancies(c, session.id) == 1


def test_manual_session_match_creates_no_correction(app, world, pg):
    c, w, t = pg, world, world.a
    session = t.add_session('in_progress')
    assert _close(t, session, 1, family_id=w.family_id)['status_code'] == 'awaiting_spd_review'
    assert _corrections(c, session.id) == [] and _open_discrepancies(c, session.id) == 0


def test_session_detail_shows_ai_and_human_separately(app, world, pg):
    c, w, t = pg, world, world.a
    session = _vision_session(c, w, t)
    _finish(w, t, session, 0, _family_code(c, w))
    try:
        _close(t, session, 1, family_id=w.family_id, notes='Piece hidden under a towel')
    finally:
        rf_session.clear_vision_snapshot(session.id)
    with app.test_request_context():
        detail = __import__('controllers.view_data', fromlist=['x']).supervisor_session_detail(
            str(session.id), t.institution_id)
    item = detail['expected_items'][0]
    assert (item['expected_quantity'], item['ai_detected_quantity'], item['reported_quantity']) == (1, 0, 1)


def _agreement(c, institution_id):
    # The dashboard indicator is scoped to the administrator's institution.
    return c.execute(text(AI_HUMAN_AGREEMENT_SQL), {'inst': institution_id}).mappings().one()


def test_agreement_compares_ai_with_human_not_expected(app, world, pg):
    c, w, t = pg, world, world.a
    before = _agreement(c, t.institution_id)
    code = _family_code(c, w)
    # AI 0 vs human 1 (human = expected): disagreement. The old indicator counted it as agreement.
    s1 = _vision_session(c, w, t)
    _finish(w, t, s1, 0, code)
    # AI 1 vs human 1: agreement.
    s2 = _vision_session(c, w, t)
    _finish(w, t, s2, 1, code)
    # Manual session: never part of the indicator.
    s3 = t.add_session('in_progress')
    try:
        _close(t, s1, 1, family_id=w.family_id, notes='Piece hidden under a towel')
        _close(t, s2, 1, family_id=w.family_id)
        _close(t, s3, 1, family_id=w.family_id)
    finally:
        rf_session.clear_vision_snapshot(s1.id)
        rf_session.clear_vision_snapshot(s2.id)
    after = _agreement(c, t.institution_id)
    assert after['total'] - before['total'] == 2
    assert after['agreed'] - before['agreed'] == 1


def test_agreement_reads_legacy_rows_from_final_lot(app, world, pg):
    """Rows closed before ai_suggested_quantity existed: AI comes from the last final lot."""
    c, w, t = pg, world, world.a
    session = _vision_session(c, w, t)
    _finish(w, t, session, 1, _family_code(c, w))
    try:
        _close(t, session, 1, family_id=w.family_id)
    finally:
        rf_session.clear_vision_snapshot(session.id)
    before = _agreement(c, t.institution_id)
    c.execute(text("UPDATE count_event SET detected_quantity = NULL, payload = payload - 'ai_suggested_quantity' "
                   "WHERE session_id = :s AND family_id IS NOT NULL"), {'s': session.id})
    c.execute(text("UPDATE count_event SET payload = payload - 'trigger' WHERE session_id = :s AND family_id IS NULL"),
              {'s': session.id})
    assert _agreement(c, t.institution_id) == before  # same pair (1, 1) recovered from legacy fields


# --- 4. Supervisor Audit Log ------------------------------------------------------------------


def _audit(c, *, institution_id, user_id, action, resource_type='work_session', resource_id=None, outcome='success'):
    c.execute(text(
        "INSERT INTO access_audit (id, occurred_at, actor_type, actor_user_id, action, resource_type, resource_id, "
        "institution_id, outcome) VALUES (:id, now(), 'user', :u, :a, :rt, :rid, :i, :o)"),
        {'id': uuid.uuid4(), 'u': user_id, 'a': action, 'rt': resource_type,
         'rid': resource_id or uuid.uuid4(), 'i': institution_id, 'o': outcome})


def test_supervisor_audit_log_is_real_and_tenant_scoped(app, world, client, login_as, pg):
    c, w = pg, world
    sa = w.a.add_session('scheduled')
    tag = w.tag
    _audit(c, institution_id=w.a.institution_id, user_id=w.a.supervisor_id, action=f'session.mt_a_{tag}',
           resource_id=sa.id)
    _audit(c, institution_id=w.a.institution_id, user_id=w.a.supervisor_id, action=f'DENIED_KIT_{tag}',
           resource_type='kit', outcome='denied')
    _audit(c, institution_id=w.a.institution_id, user_id=w.a.supervisor_id, action=f'CREATE_USER_{tag}',
           resource_type='user')  # administration event: Administrator audit log only
    _audit(c, institution_id=w.b.institution_id, user_id=w.b.supervisor_id, action=f'session.mt_b_{tag}')
    login_as(w.a.supervisor_id, w.a.institution_id, 'spd_supervisor')

    def rows(url):
        """Event codes rendered as table rows (filter dropdowns also list them as options)."""
        html = client.get(url).get_data(as_text=True)
        return html, {chunk.split('</code>')[0] for chunk in html.split('<td><code>')[1:]}

    page, events = rows('/supervisor/audit-log')
    assert {f'session.mt_a_{tag}', f'DENIED_KIT_{tag}'} <= events
    assert f'session.mt_b_{tag}' not in page   # other institution: neither row nor option
    assert f'CREATE_USER_{tag}' not in page     # administration event: not an operational one
    assert 'WS-026' not in page                 # no fixture rows
    assert 'text-danger' in page                # denied outcome shown as such

    _, events = rows(f'/supervisor/audit-log?event=DENIED_KIT_{tag}')
    assert events == {f'DENIED_KIT_{tag}'}
    _, events = rows(f'/supervisor/audit-log?session={sa.id}')
    assert events == {f'session.mt_a_{tag}'}
    _, events = rows('/supervisor/audit-log?search=' + rf_session_display(sa.id))
    assert events == {f'session.mt_a_{tag}'}


def rf_session_display(session_id):
    return 'WS-' + str(session_id).replace('-', '')[:8].upper()


def test_supervisor_audit_log_paginates(app, world, client, login_as, pg):
    c, w = pg, world
    for index in range(12):
        _audit(c, institution_id=w.a.institution_id, user_id=w.a.supervisor_id, action=f'session.page_{index:02d}')
    login_as(w.a.supervisor_id, w.a.institution_id, 'spd_supervisor')
    first = client.get('/supervisor/audit-log?per_page=10').get_data(as_text=True)
    assert 'Page 1 of 2' in first and 'page=2' in first
    second = client.get('/supervisor/audit-log?per_page=10&page=2').get_data(as_text=True)
    assert 'Page 2 of 2' in second and 'session.page_00' in second
