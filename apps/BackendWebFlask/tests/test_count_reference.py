"""Count reference (initial + confirmed additions), contingency count and out-of-kit instruments.

Real PostgreSQL schema inside a rolled-back transaction (see test_multitenant); skipped when
PostgreSQL is unreachable. Needs cat_event_type 'instruments_added' (seed / migration 005).
"""

import uuid

import pytest
from flask import g
from sqlalchemy import text

from services import rf_session, vision_bridge
from services.rf_session import RfSessionError

from .test_multitenant import login_as, pg, world  # noqa: F401  (pytest fixtures)
from .test_vision_count_rule import _events, _finish, _open_discrepancies, _run, _status, tray  # noqa: F401


# --- helpers ------------------------------------------------------------------------------


def _stock(tray, family_id, n):
    """Available pieces of a family, so the SPD can raise its expected quantity."""
    status = tray.c.execute(text("SELECT id FROM cat_instrument_cycle_status WHERE code = 'available'")).scalar_one()
    for _ in range(n):
        tray.c.execute(text('INSERT INTO instrument (id, internal_code, family_id, cycle_status_id, institution_id, '
                            'active) VALUES (:id, :code, :f, :cs, :i, TRUE)'),
                       {'id': uuid.uuid4(), 'code': f'REF-{uuid.uuid4().hex[:8]}', 'f': family_id, 'cs': status,
                        'i': tray.t.institution_id})


def _inventory(tray, a, b, *, physically_added):
    return rf_session.update_session_inventory(
        session_id=tray.session.id, supervisor_user_id=tray.t.supervisor_id, institution_id=tray.t.institution_id,
        lines=[{'family_id': str(tray.a), 'expected_quantity': a}, {'family_id': str(tray.b), 'expected_quantity': b}],
        physically_added=physically_added,
    )


def _validate(tray, qty_a, qty_b, *, extra=0, **kwargs):
    return rf_session.submit_manual_close(
        session_id=tray.session.id, operator_user_id=tray.t.operator_id, institution_id=tray.t.institution_id,
        reports=[{'family_id': str(tray.a), 'reported_quantity': qty_a, **kwargs.get('a', {})},
                 {'family_id': str(tray.b), 'reported_quantity': qty_b, **kwargs.get('b', {})}],
        close_path=kwargs.get('close_path', 'vision_validation'),
        contingency=kwargs.get('contingency'),
        extra_instruments=extra, extra_notes=kwargs.get('extra_notes', ''),
    )


def _manual_close_payload(tray):
    return _events(tray, 'manual_close')[-1]['payload']


@pytest.fixture
def stocked(tray):
    _stock(tray, tray.a, 12)
    _stock(tray, tray.b, 12)
    return tray


# --- 1. Confirmed additions raise the reference -------------------------------------------------


def test_initial_15_plus_2_confirmed_then_final_17(app, stocked):
    tray = stocked
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    result = _inventory(tray, 10, 7, physically_added=True)
    assert result['added_total'] == 2
    reference = rf_session.count_reference(tray.session.id)
    assert (reference['initial_total'], reference['added_total'], reference['total']) == (15, 2, 17)
    added = _events(tray, 'instruments_added')
    assert [(e['expected_quantity'], e['detected_quantity']) for e in added] == [(15, 2)]
    assert added[0]['payload']['added'] == [{'family_id': str(tray.a), 'previous': 8, 'new': 10, 'quantity': 2}]
    snapshot = _finish(tray, _run(tray, run_id='r-final', a=10, b=7))
    assert (snapshot['stage'], snapshot['reference_total'], snapshot['final_total']) == ('final', 17, 17)
    assert _validate(tray, 10, 7)['status_code'] == 'awaiting_spd_review'


def test_final_16_against_adjusted_reference_17_is_blocked(app, stocked):
    tray = stocked
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    _inventory(tray, 10, 7, physically_added=True)
    with pytest.raises(RfSessionError) as exc:
        _finish(tray, _run(tray, run_id='r-final', a=9, b=7))
    assert exc.value.params == {'final': 16, 'reference': 17, 'initial': 15, 'added': 2}
    blocked = _events(tray, 'close_blocked')[-1]
    assert (blocked['expected_quantity'], blocked['detected_quantity']) == (17, 16)
    assert (blocked['payload']['initial_total'], blocked['payload']['added_total']) == (15, 2)


def test_duplicates_and_administrative_edits_do_not_raise_the_reference(app, stocked):
    tray = stocked
    _inventory(tray, 9, 7, physically_added=True)       # before the initial count: on the initial capture
    _finish(tray, _run(tray, run_id='r-initial', a=9, b=7))
    assert rf_session.count_reference(tray.session.id)['total'] == 16
    _inventory(tray, 11, 7, physically_added=True)      # +2 confirmed
    _inventory(tray, 11, 7, physically_added=True)      # double submit: nothing new
    _inventory(tray, 11, 9, physically_added=False)     # administrative correction
    _inventory(tray, 10, 9, physically_added=True)      # a decrease is never an addition
    reference = rf_session.count_reference(tray.session.id)
    assert (reference['added_total'], reference['total']) == (2, 18)
    assert len(_events(tray, 'instruments_added')) == 2  # the pre-initial one is kept for traceability only
    audit = tray.c.execute(text("SELECT action FROM access_audit WHERE resource_id = :s ORDER BY occurred_at"),
                           {'s': tray.session.id}).scalars().all()
    assert audit.count('session.instruments_added') == 2 and audit.count('session.inventory_updated') == 3


def test_addition_after_final_requires_a_new_final_capture(app, stocked):
    tray = stocked
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    _finish(tray, _run(tray, run_id='r-final', a=8, b=7))
    _inventory(tray, 10, 7, physically_added=True)
    assert rf_session.get_vision_snapshot(tray.session.id) is None
    with pytest.raises(RfSessionError) as exc:
        _validate(tray, 10, 7)
    assert exc.value.status_code == 409 and 'current reference count' in exc.value.message
    _finish(tray, _run(tray, run_id='r-final-2', a=10, b=7))
    assert _validate(tray, 10, 7)['status_code'] == 'awaiting_spd_review'


# --- 2. Instruments outside the kit --------------------------------------------------------------


def test_out_of_kit_instrument_counts_in_total_and_needs_spd(app, tray):
    """15 kit pieces + 1 instrument of another family: initial 16, final 16, human confirms 15 + 1."""
    foreign = [{'family_code': 'OTHER', 'family_name': 'Other kit forceps', 'count': 1, 'best_confidence': 0.81}]
    initial = _run(tray, run_id='r-initial', a=8, b=7, boxes=16)
    initial['out_of_kit'] = foreign
    _finish(tray, initial)
    final = _run(tray, run_id='r-final', a=8, b=7, boxes=16)
    final['out_of_kit'] = foreign
    snapshot = _finish(tray, final)
    assert snapshot['final_total'] == 16 and snapshot['out_of_kit'] == foreign
    assert _events(tray, 'auto_count')[-1]['payload']['out_of_kit'][0]['family_name'] == 'Other kit forceps'
    result = _validate(tray, 8, 7, extra=1, extra_notes='Forceps from another tray')
    assert result['status_code'] == 'correction_required'
    # No "missing" discrepancy (16 = 15 + 1); one surplus discrepancy for the foreign piece.
    assert _open_discrepancies(tray) == [(None, 0, 1)]


def test_unconfirmed_out_of_kit_instrument_is_a_missing_piece(app, tray):
    initial = _run(tray, run_id='r-initial', a=8, b=7, boxes=16)
    _finish(tray, initial)
    _finish(tray, _run(tray, run_id='r-final', a=8, b=7, boxes=16))
    _validate(tray, 8, 7, a={'notes': 'Extra box not found'})
    assert _open_discrepancies(tray) == [(None, 16, 15)]


# --- 3. Contingency count --------------------------------------------------------------------


def test_contingency_after_yolo_technical_error(app, tray, client, login_as, monkeypatch):
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))

    def down(sid):
        raise vision_bridge.VisionBridgeError('Vision worker unreachable', 503)
    monkeypatch.setattr(vision_bridge, 'fetch_state', down)
    login_as(tray.t.operator_id, tray.t.institution_id, 'station_operator')
    url = f'/operator/sessions/{tray.session.id}/manual-report'
    page = client.get(url).get_data(as_text=True)
    assert 'name="contingency_reason"' in page
    form = {f'reported_{tray.a}': '8', f'reported_{tray.b}': '7'}
    response = client.post(url, data=form)
    assert response.status_code == 400 and 'Choose why YOLO could not be used' in response.get_data(as_text=True)

    response = client.post(url, data={**form, 'contingency_reason': 'worker_unavailable'})
    assert response.status_code == 302
    assert _status(tray) == 'awaiting_spd_review'  # Operator finished; not a safe close yet
    payload = _manual_close_payload(tray)
    assert payload['contingency']['reason_code'] == 'worker_unavailable'
    assert payload['contingency']['worker_state']['status'] == 'unreachable'
    assert (payload['verified_total'], payload['operator_finished'], payload['safe_close']) == (15, True, False)
    close = _events(tray, 'manual_close')[-1]
    assert close['payload']['reference']['total'] == 15
    audit = tray.c.execute(text("SELECT action FROM access_audit WHERE resource_id = :s"),
                           {'s': tray.session.id}).scalars().all()
    assert 'session.contingency_close' in audit

    # Safe close: SPD must confirm they reviewed the contingency count.
    with pytest.raises(RfSessionError) as exc:
        rf_session.confirm_spd_close(session_id=tray.session.id, supervisor_user_id=tray.t.supervisor_id,
                                     institution_id=tray.t.institution_id, material_recovered=True)
    assert 'contingency count' in exc.value.message
    g.pop('verified_user', None)  # the pg fixture keeps one app context (and g) for the whole test
    login_as(tray.t.supervisor_id, tray.t.institution_id, 'spd_supervisor')
    assert 'name="contingency_reviewed"' in client.get(f'/supervisor/sessions/{tray.session.id}').get_data(as_text=True)
    rf_session.confirm_spd_close(session_id=tray.session.id, supervisor_user_id=tray.t.supervisor_id,
                                 institution_id=tray.t.institution_id, material_recovered=True,
                                 contingency_reviewed=True)
    assert _status(tray) == 'closed'
    assert _events(tray, 'session_close')[-1]['payload']['safe_close_confirmed'] is True


def test_contingency_needs_notes_for_unreliable_detection(app, tray):
    with pytest.raises(RfSessionError) as exc:
        _validate(tray, 8, 7, close_path='manual_report', contingency={'reason_code': 'unreliable_detection'})
    assert 'at least 10 characters' in exc.value.message


def test_manual_report_cannot_hide_a_known_physical_shortage(app, tray):
    """YOLO rejected 14/15; the Operator then reports 8 + 7 = 15 through the contingency path."""
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    with pytest.raises(RfSessionError):
        _finish(tray, _run(tray, run_id='r-final', a=8, b=6))
    with pytest.raises(RfSessionError):  # human validation is not available: no valid final count
        _validate(tray, 8, 7)
    result = _validate(tray, 8, 7, close_path='manual_report',
                       contingency={'reason_code': 'other', 'notes': 'YOLO keeps missing one piece'})
    assert result['status_code'] == 'correction_required'
    assert _open_discrepancies(tray) == [(None, 15, 15)]
    description = tray.c.execute(text('SELECT description FROM discrepancy WHERE session_id = :s'),
                                 {'s': tray.session.id}).scalar_one()
    assert 'YOLO final capture 14 of 15' in description
    assert _manual_close_payload(tray)['known_final_mismatch']['final'] == 14
    with pytest.raises(RfSessionError):
        rf_session.confirm_spd_close(session_id=tray.session.id, supervisor_user_id=tray.t.supervisor_id,
                                     institution_id=tray.t.institution_id, material_recovered=True,
                                     contingency_reviewed=True)


def test_contingency_report_with_the_missing_piece_has_a_single_discrepancy(app, tray):
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    with pytest.raises(RfSessionError):
        _finish(tray, _run(tray, run_id='r-final', a=8, b=6))
    _validate(tray, 8, 6, close_path='manual_report', b={'reason_code': 'shortage'},
              contingency={'reason_code': 'processing_error'})
    assert _open_discrepancies(tray) == [(tray.b, 7, 6)]  # the family shortage explains the YOLO gap


# --- 4. Manual sessions -----------------------------------------------------------------------


def test_manual_sessions_have_no_contingency_or_reference(app, world, pg, client, login_as):
    c, w, t = pg, world, world.a
    session = t.add_session('in_progress')  # manual_no_privacy
    login_as(t.operator_id, t.institution_id, 'station_operator')
    url = f'/operator/sessions/{session.id}/manual-report'
    assert 'name="contingency_reason"' not in client.get(url).get_data(as_text=True)
    response = client.post(url, data={f'reported_{w.family_id}': '1'})
    assert response.status_code == 302
    payload = c.execute(text(
        "SELECT ce.payload FROM count_event ce JOIN cat_event_type et ON et.id = ce.event_type_id "
        "WHERE ce.session_id = :s AND et.code = 'manual_close'"), {'s': session.id}).scalar_one()
    assert payload['contingency'] is None and payload['reference'] is None
    assert payload['operator_close_path'] == 'manual_report'
    rf_session.confirm_spd_close(session_id=session.id, supervisor_user_id=t.supervisor_id,
                                 institution_id=t.institution_id, material_recovered=True)


def test_supervisor_form_confirms_physical_addition(app, stocked, client, login_as):
    tray = stocked
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    login_as(tray.t.supervisor_id, tray.t.institution_id, 'spd_supervisor')
    url = f'/supervisor/sessions/{tray.session.id}/inventory'
    assert 'name="physically_added"' in client.get(url).get_data(as_text=True)
    lines = {'family_id[]': [str(tray.a), str(tray.b)], 'expected_quantity[]': ['9', '7'], 'edit_mode': 'inventory'}
    assert client.post(url, data=lines).status_code == 302  # administrative: reference unchanged
    assert rf_session.count_reference(tray.session.id)['total'] == 15
    lines['expected_quantity[]'] = ['11', '7']
    assert client.post(url, data={**lines, 'physically_added': '1'}).status_code == 302
    assert rf_session.count_reference(tray.session.id)['total'] == 17
