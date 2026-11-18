"""MS-01 / P5 on the Supervisor's LOCAL fallback (services/rf_session.schedule_session / update_scheduled_session,
used only without SESSION_SERVICE_URL). Same rule as SessionService: a NEW room, station, kit or initial phase
must be active; a session keeps the references it already had even if they are deactivated later.
Operator flows (start, capture, validation, close) are not touched by MS-01.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from services import rf_session
from services.rf_session import RfSessionError

from .test_multitenant import _run, _schedule_kwargs, _status, pg, world  # noqa: F401 - fixtures


@pytest.fixture
def retired(world, pg):
    t = world.a
    r = type('Retired', (), {})()
    r.station = uuid.uuid4()
    _run(pg, 'INSERT INTO capture_station (id, name, active, room_id) VALUES (:id, :n, FALSE, :r)',
         id=r.station, n='Retired station', r=t.room_id)
    r.kit = uuid.uuid4()
    _run(pg, 'INSERT INTO kit (id, name, version, active, institution_id) VALUES (:id, :n, 1, FALSE, :i)',
         id=r.kit, n=f'Retired kit {world.tag}', i=t.institution_id)
    r.phase = f'zz_off_{world.tag}'
    _run(pg, "INSERT INTO cat_operation_phase (id, code, name, active) VALUES (:id, :c, 'Retired phase', FALSE)",
         id=uuid.uuid4(), c=r.phase)
    return r


def _refused(app, message, **overrides):
    with app.test_request_context(), pytest.raises(RfSessionError) as exc:
        rf_session.schedule_session(**_schedule_kwargs(**overrides))
    assert exc.value.status_code == 422 and message in str(exc.value.message), exc.value.message


def test_local_schedule_refuses_inactive_resources(app, world, pg, retired):
    w, t = world, world.a
    _run(pg, 'UPDATE operating_room SET active = FALSE WHERE id = :r', r=t.other_room_id)
    _refused(app, 'operating room is inactive', w=w, t=t, room_id=t.other_room_id, station_id=t.other_station_id)
    _refused(app, 'capture station is inactive', w=w, t=t, station_id=retired.station)
    _refused(app, 'kit is inactive', w=w, t=t, kit_id=retired.kit)
    _refused(app, 'phase is inactive', w=w, t=t, phase_code=retired.phase)


def test_local_unknown_resources_keep_their_answer(app, world):
    with app.test_request_context(), pytest.raises(RfSessionError) as exc:
        rf_session.schedule_session(**_schedule_kwargs(world, world.a, kit_id=uuid.uuid4()))
    assert exc.value.status_code == 400


def _update_kwargs(world, t, session_id, **overrides):
    values = _schedule_kwargs(world, t, **overrides)
    for key in ('phase_code',):
        values.pop(key)
    values['session_id'] = session_id
    values['physicians'] = [{'physician_id': t.physician_id, 'surgical_role_code': 'surgeon'}]
    return values


def test_local_edit_keeps_references_deactivated_later(app, world, pg, retired):
    t = world.a
    session = t.add_session('scheduled')
    for table, column in (('operating_room', t.room_id), ('capture_station', t.station_id), ('kit', t.kit_id)):
        _run(pg, f'UPDATE {table} SET active = FALSE WHERE id = :i', i=column)
    with app.test_request_context():
        rf_session.update_scheduled_session(**_update_kwargs(
            world, t, session.id, scheduled_at=datetime.now(timezone.utc) + timedelta(days=2)))
    assert _status(pg, session.id) == 'scheduled'
    row = pg.execute(text('SELECT station_id, kit_id FROM work_session WHERE id = :s'), {'s': session.id}).one()
    assert (row.station_id, row.kit_id) == (t.station_id, t.kit_id)
    # ...but a NEW inactive reference is refused.
    with app.test_request_context(), pytest.raises(RfSessionError) as exc:
        rf_session.update_scheduled_session(**_update_kwargs(world, t, session.id, kit_id=retired.kit))
    assert exc.value.status_code == 422
