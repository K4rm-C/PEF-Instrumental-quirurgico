"""Vision facts (PostgreSQL) -> DS06 checkpoints (MongoDB), Operator flow with MongoDB down, and
the vision summary rebuilt from PostgreSQL after a Flask restart.

Real PostgreSQL (rolled-back transaction) + real MongoDB test database; see the fixtures used.
"""

import os
import time
import uuid

import pytest
from sqlalchemy import text

from services import mongo_checkpoints as mc, rf_session

from .test_mongo_checkpoints import DEAD_URL, mongo  # noqa: F401  (pytest fixture)
from .test_multitenant import login_as, pg, world  # noqa: F401
from .test_vision_count_rule import _finish, _run, _status, tray  # noqa: F401


def _checkpoints(db, session_id):
    return list(db.checkpoint_frame.find({'session_id': str(session_id)}).sort('session_checkpoint_seq', 1))


def test_vision_facts_produce_ds06_checkpoints_linked_to_postgresql(app, tray, mongo):
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))                 # start
    with pytest.raises(rf_session.RfSessionError):
        _finish(tray, _run(tray, run_id='r-final-1', a=8, b=6))             # discrepancy
    with app.test_request_context():   # the route always runs it inside a request (localized labels)
        rf_session.change_phase_stub(session_id=tray.session.id, operator_user_id=tray.t.operator_id,
                                     institution_id=tray.t.institution_id, to_phase_code='start',
                                     worker_session=_run(tray, run_id='r-final-1', a=8, b=6))   # state_change
    _finish(tray, _run(tray, run_id='r-final-2', a=8, b=7))                 # close
    assert mc.process_pending() == 0

    docs = _checkpoints(mongo, tray.session.id)
    assert [(d['reason'], d['session_checkpoint_seq']) for d in docs] == [
        ('start', 1), ('discrepancy', 2), ('state_change', 3), ('close', 4)]
    operation_id, phase = tray.c.execute(text(
        'SELECT w.operation_id, p.code FROM work_session w LEFT JOIN cat_operation_phase p ON p.id = w.current_phase_id '
        'WHERE w.id = :id'), {'id': tray.session.id}).one()
    assert {d['operation_id'] for d in docs} == {str(operation_id)}     # same identifiers as PostgreSQL
    assert docs[-1]['phase_code'] == phase
    assert [len(d['detections']) for d in docs] == [15, 14, 14, 15]
    # The rejected final count is close_blocked in PostgreSQL and a discrepancy checkpoint in MongoDB.
    blocked = tray.c.execute(text(
        "SELECT ce.detected_quantity FROM count_event ce JOIN cat_event_type t ON t.id = ce.event_type_id "
        "WHERE ce.session_id = :s AND t.code = 'close_blocked'"), {'s': tray.session.id}).scalar_one()
    assert blocked == len(docs[1]['detections']) == 14


def test_operator_counts_with_mongodb_down(app, tray, mongo, monkeypatch, client, login_as):
    monkeypatch.setattr(mc, 'MONGO_URL', DEAD_URL)
    monkeypatch.setattr(mc, '_client', None)
    started = time.monotonic()
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    _finish(tray, _run(tray, run_id='r-final', a=8, b=7))
    assert time.monotonic() - started < 2              # no MongoDB call in the counting request
    login_as(tray.t.operator_id, tray.t.institution_id, 'station_operator')
    form = {f'validated_{tray.a}': '8', f'validated_{tray.b}': '7'}
    response = client.post(f'/operator/sessions/{tray.session.id}/vision-validation', data=form)
    assert response.status_code == 302 and _status(tray) == 'awaiting_spd_review'
    assert mc.process_pending() == 2 and mc.STATS['mongo_failures'] >= 1      # kept for retry, logged
    monkeypatch.setattr(mc, 'MONGO_URL', os.environ['PEF_TEST_MONGO_URL'])   # MongoDB is back
    monkeypatch.setattr(mc, '_client', None)
    assert mc.process_pending() == 0
    assert [d['reason'] for d in _checkpoints(mongo, tray.session.id)] == ['start', 'close']


def test_vision_summary_is_rebuilt_from_postgresql_after_restart(app, tray, client, login_as):
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    _finish(tray, _run(tray, run_id='r-final', a=7, b=8))
    rf_session._VISION_SNAPSHOTS.clear()                  # what a Flask restart loses
    snapshot = rf_session.get_vision_snapshot(tray.session.id)
    assert snapshot['restored_from'] == 'postgresql' and snapshot['final_total'] == 15
    assert {c['family_id']: c['detected_quantity'] for c in snapshot['counts'].values()} == {
        str(tray.a): 7, str(tray.b): 8}
    login_as(tray.t.operator_id, tray.t.institution_id, 'station_operator')
    assert client.get(f'/operator/sessions/{tray.session.id}/detection-summary').status_code == 200
    form = {f'validated_{tray.a}': '8', f'validated_{tray.b}': '7',
            f'notes_{tray.a}': 'One forceps labelled as retractor', f'notes_{tray.b}': 'Same piece'}
    response = client.post(f'/operator/sessions/{tray.session.id}/vision-validation', data=form)
    assert response.status_code == 302 and _status(tray) == 'awaiting_spd_review'
    corrections = tray.c.execute(text(
        'SELECT count(*) FROM human_correction hc JOIN count_event ce ON ce.id = hc.count_event_id '
        'WHERE ce.session_id = :s'), {'s': tray.session.id}).scalar_one()
    assert corrections == 2                                # AI 7/8 vs human 8/7, from the PostgreSQL lot


def test_no_snapshot_without_a_valid_final_count(app, tray):
    _finish(tray, _run(tray, run_id='r-initial', a=8, b=7))
    rf_session._VISION_SNAPSHOTS.clear()
    assert rf_session.get_vision_snapshot(tray.session.id) is None
    assert rf_session.get_vision_snapshot('WS-026') is None
    assert rf_session.get_vision_snapshot(str(uuid.uuid4())) is None
