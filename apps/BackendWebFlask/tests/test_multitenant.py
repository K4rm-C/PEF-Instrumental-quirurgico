"""Multi-tenant isolation of the RF session workflow (rf_session + view_data + routes).

Runs against the real PostgreSQL schema with two temporary institutions (A and B) built
inside one outer transaction that is rolled back at teardown; rf_session's own commits become
SAVEPOINTs (join_transaction_mode="create_savepoint"). Seeds are neither required nor touched.
Skipped (with the reason) when PostgreSQL is unreachable.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import scoped_session, sessionmaker

from controllers import routes, view_data
from extensions import db
from services import rf_session
from services.rf_session import RfSessionError

from .conftest import FakeResponse


# --- Infrastructure ----------------------------------------------------------------------


@pytest.fixture
def pg(app, monkeypatch):
    monkeypatch.setitem(app.config, 'FRONTEND_DEMO_MODE', False)
    with app.app_context():
        try:
            connection = db.engine.connect()
        except OperationalError as exc:  # pragma: no cover - environment dependent
            pytest.skip(f'PostgreSQL not reachable for multi-tenant tests: {type(exc).__name__}')
        outer = connection.begin()
        session = scoped_session(sessionmaker(bind=connection, join_transaction_mode='create_savepoint'))
        monkeypatch.setattr(db, 'session', session)
        try:
            yield connection
        finally:
            session.remove()
            outer.rollback()
            connection.close()


def _code(connection, table, code):
    return connection.execute(text(f'SELECT id FROM {table} WHERE code = :c'), {'c': code}).scalar_one()


def _run(connection, sql, **params):
    connection.execute(text(sql), params)


def _new_id():
    return uuid.uuid4()


@pytest.fixture
def world(pg):
    """Two institutions with parallel resources; one global instrument family shared by both."""
    c = pg
    tag = uuid.uuid4().hex[:8]
    w = SimpleNamespace(tag=tag)
    category_id = _new_id()
    _run(c, 'INSERT INTO cat_instrument_category (id, code, name) VALUES (:id, :code, :name)',
         id=category_id, code=f'zz_mt_{tag}', name='MT category')
    w.family_id = _new_id()
    _run(c, 'INSERT INTO instrument_family (id, code, name, category_id, active) VALUES (:id, :code, :n, :cat, TRUE)',
         id=w.family_id, code=f'ZZ_MT_{tag}', n='MT family', cat=category_id)
    w.status = {code: _code(c, 'cat_session_status', code) for code in (
        'scheduled', 'in_progress', 'awaiting_spd_review', 'correction_required', 'closed')}
    w.cycle = {code: _code(c, 'cat_instrument_cycle_status', code) for code in (
        'available', 'lost', 'sterilization', 'reserved')}
    w.procedure_type_id = c.execute(text('SELECT id FROM cat_procedure_type LIMIT 1')).scalar_one()
    op_status = _code(c, 'cat_operation_status', 'scheduled')
    w.notice_id = _new_id()
    _run(c, "INSERT INTO privacy_notice_version (id, version, effective_at, document_uri, active) "
            "VALUES (:id, :v, now(), 'https://example.invalid/notice', TRUE)", id=w.notice_id, v=f'mt-{tag}')

    def tenant(label):
        t = SimpleNamespace(label=label)
        t.institution_id = _new_id()
        _run(c, 'INSERT INTO institution (id, name, active) VALUES (:id, :n, TRUE)',
             id=t.institution_id, n=f'MT {label} {tag}')
        roles = {}
        for code in ('station_operator', 'spd_supervisor'):
            roles[code] = _new_id()
            _run(c, 'INSERT INTO role (id, code, description, institution_id) VALUES (:id, :c, :d, :i)',
                 id=roles[code], c=code, d=f'{code} {label}', i=t.institution_id)
        for key, role_code in (('operator_id', 'station_operator'), ('supervisor_id', 'spd_supervisor')):
            user_id = _new_id()
            _run(c, 'INSERT INTO "user" (id, name, email, password_hash, institution_id) '
                    "VALUES (:id, :n, :e, 'x', :i)",
                 id=user_id, n=f'MT {key} {label}', e=f'{key}.{label}.{tag}@mt.invalid', i=t.institution_id)
            _run(c, 'INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)', u=user_id, r=roles[role_code])
            setattr(t, key, user_id)
        t.room_id, t.station_id = _new_id(), _new_id()
        t.other_room_id, t.other_station_id = _new_id(), _new_id()
        for room_id, station_id, suffix in ((t.room_id, t.station_id, '1'), (t.other_room_id, t.other_station_id, '2')):
            _run(c, 'INSERT INTO operating_room (id, code, name, institution_id) VALUES (:id, :c, :n, :i)',
                 id=room_id, c=f'MT-{label}{suffix}-{tag}', n=f'Room {label}{suffix}', i=t.institution_id)
            _run(c, 'INSERT INTO capture_station (id, name, room_id) VALUES (:id, :n, :r)',
                 id=station_id, n=f'Station {label}{suffix}', r=room_id)
        t.kit_id = _new_id()
        _run(c, 'INSERT INTO kit (id, name, version, institution_id) VALUES (:id, :n, 1, :i)',
             id=t.kit_id, n=f'MT kit {label} {tag}', i=t.institution_id)
        _run(c, 'INSERT INTO kit_item (kit_id, family_id, quantity) VALUES (:k, :f, 1)', k=t.kit_id, f=w.family_id)
        t.patient_id, t.physician_id = _new_id(), _new_id()
        _run(c, 'INSERT INTO patient (id, display_name, institution_id) VALUES (:id, :n, :i)',
             id=t.patient_id, n=f'Patient {label}', i=t.institution_id)
        _run(c, 'INSERT INTO physician (id, name, institution_id) VALUES (:id, :n, :i)',
             id=t.physician_id, n=f'Physician {label}', i=t.institution_id)

        def add_session(status_code, *, user_id=None):
            operation_id, session_id = _new_id(), _new_id()
            _run(c, 'INSERT INTO operation (id, scheduled_at, status_id, procedure_type_id, room_id, institution_id) '
                    'VALUES (:id, now(), :s, :p, :r, :i)',
                 id=operation_id, s=op_status, p=w.procedure_type_id, r=t.room_id, i=t.institution_id)
            started = None if status_code == 'scheduled' else datetime.now(timezone.utc) - timedelta(hours=1)
            _run(c, 'INSERT INTO work_session (id, started_at, status_id, user_id, operation_id, station_id, kit_id, '
                    'capture_mode) VALUES (:id, :st, :s, :u, :o, :stn, :k, :cm)',
                 id=session_id, st=started, s=w.status[status_code], u=user_id or t.operator_id, o=operation_id,
                 stn=t.station_id, k=t.kit_id, cm=None if status_code == 'scheduled' else 'manual_no_privacy')
            _run(c, "INSERT INTO expected_inventory (session_id, family_id, expected_quantity, source) "
                    "VALUES (:s, :f, 1, 'kit_snapshot')", s=session_id, f=w.family_id)
            return SimpleNamespace(id=session_id, operation_id=operation_id)

        def add_discrepancy(session):
            disc_id = _new_id()
            _run(c, 'INSERT INTO discrepancy (id, description, resolved, family_id, expected_quantity, '
                    'detected_quantity, session_id) VALUES (:id, :d, FALSE, :f, 1, 0, :s)',
                 id=disc_id, d=f'MT shortage {label}', f=w.family_id, s=session.id)
            return disc_id

        def add_instrument(cycle_code, *, active=True):
            instrument_id = _new_id()
            _run(c, 'INSERT INTO instrument (id, internal_code, family_id, cycle_status_id, institution_id, active) '
                    'VALUES (:id, :code, :f, :cs, :i, :a)',
                 id=instrument_id, code=f'MT-{label}-{instrument_id.hex[:6]}', f=w.family_id,
                 cs=w.cycle[cycle_code], i=t.institution_id, a=active)
            return instrument_id

        t.add_session, t.add_discrepancy, t.add_instrument = add_session, add_discrepancy, add_instrument
        return t

    w.a, w.b = tenant('A'), tenant('B')
    return w


def _instrument(c, instrument_id):
    return c.execute(text(
        'SELECT s.code AS cycle, i.active FROM instrument i JOIN cat_instrument_cycle_status s '
        'ON s.id = i.cycle_status_id WHERE i.id = :id'), {'id': instrument_id}).mappings().one()


def _status(c, session_id):
    return c.execute(text(
        'SELECT s.code FROM work_session w JOIN cat_session_status s ON s.id = w.status_id WHERE w.id = :id'),
        {'id': session_id}).scalar_one()


def _schedule_kwargs(w, t, **overrides):
    values = dict(
        supervisor_user_id=t.supervisor_id, institution_id=t.institution_id, operator_user_id=t.operator_id,
        procedure_type_id=w.procedure_type_id, room_id=t.room_id, station_id=t.station_id, kit_id=t.kit_id,
        patient_id=t.patient_id, physicians=[{'physician_id': t.physician_id, 'surgical_role_code': 'surgeon'}],
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=1), phase_code='start', expected_lines=[],
    )
    values.update(overrides)
    return values


@pytest.fixture
def login_as(client, monkeypatch):
    """Fake Auth /verify for a temporary user of a temporary institution."""
    def _login(user_id, institution_id, role):
        def fake_auth(method, path, token=None, **kwargs):
            if path == '/verify':
                return FakeResponse(200, {'valid': True, 'user': {
                    'id': str(user_id), 'name': 'MT user', 'email': 'mt@example.invalid',
                    'institution_id': str(institution_id), 'ui_preferences': {'locale': 'en', 'theme': 'light'},
                    'roles': [{'code': role, 'description': None}],
                }})
            return FakeResponse(404)
        monkeypatch.setattr(routes, '_auth_request', fake_auth)
        client.set_cookie('access_token', 'mt-access-token')
    return _login


# --- 1-3. Visibility -----------------------------------------------------------------------


def test_supervisor_list_is_tenant_scoped(app, world, client, login_as):
    session_a = world.a.add_session('scheduled')
    session_b = world.b.add_session('scheduled')
    with app.test_request_context():
        ids = {row['id'] for row in view_data.sessions_data(world.a.institution_id)}
    assert str(session_a.id) in ids and str(session_b.id) not in ids
    assert view_data.sessions_data(None) == []

    login_as(world.a.supervisor_id, world.a.institution_id, 'spd_supervisor')
    page = client.get('/supervisor/sessions').get_data(as_text=True)
    assert view_data.display_session_id(session_a.id) in page
    assert view_data.display_session_id(session_b.id) not in page
    history = client.get('/supervisor/sessions/history').get_data(as_text=True)
    assert view_data.display_session_id(session_b.id) not in history


def test_dashboard_and_discrepancy_lists_are_tenant_scoped(app, world):
    session_a = world.a.add_session('correction_required')
    session_b = world.b.add_session('correction_required')
    world.a.add_discrepancy(session_a)
    disc_b = world.b.add_discrepancy(session_b)
    with app.test_request_context():
        rows = view_data.discrepancies_data(world.a.institution_id)
        assert str(disc_b) not in {row['id'] for row in rows}
        assert {row['session_uuid'] for row in rows} == {str(session_a.id)}
        _, sessions, discrepancies = view_data.dashboard_data('supervisor', world.a.institution_id)
        assert {row['id'] for row in sessions} == {str(session_a.id)}
        assert len(discrepancies) == 1


def test_supervisor_cannot_open_other_tenant_detail(app, world, client, login_as):
    session_b = world.b.add_session('awaiting_spd_review')
    with app.test_request_context():
        assert view_data.supervisor_session_detail(session_b.id, world.a.institution_id) is None
        assert view_data.supervisor_session_detail(session_b.id, world.b.institution_id) is not None
    login_as(world.a.supervisor_id, world.a.institution_id, 'spd_supervisor')
    assert client.get(f'/supervisor/sessions/{session_b.id}').status_code == 404
    assert client.get(f'/supervisor/sessions/{session_b.id}/inventory').status_code == 404
    assert client.get(f'/supervisor/discrepancies/{session_b.id}/review').status_code == 404


def test_operator_cannot_open_other_tenant_session_even_if_assigned(app, world, pg, client, login_as):
    # Corrupt data: a session of institution B whose user_id points to operator A.
    session_b = world.b.add_session('scheduled', user_id=world.a.operator_id)
    session_a = world.a.add_session('scheduled')
    with app.test_request_context():
        assert view_data.operator_session_detail(session_b.id, world.a.operator_id, world.a.institution_id) is None
        assert view_data.operator_session_detail(session_a.id, world.a.operator_id, world.a.institution_id) is not None
        mine = {row['id'] for row in view_data.operator_my_sessions(world.a.operator_id, world.a.institution_id)}
    assert mine == {str(session_a.id)}
    login_as(world.a.operator_id, world.a.institution_id, 'station_operator')
    assert client.get(f'/operator/sessions/{session_b.id}').status_code == 404
    with pytest.raises(RfSessionError) as excinfo:
        rf_session.start_session(session_id=session_b.id, operator_user_id=world.a.operator_id,
                                 institution_id=world.a.institution_id)
    assert excinfo.value.status_code == 404
    assert _status(pg, session_b.id) == 'scheduled'


# --- 4-8. Schedule references ------------------------------------------------------------


@pytest.mark.parametrize('field, other', [
    ('operator_user_id', 'operator_id'),
    ('kit_id', 'kit_id'),
    ('patient_id', 'patient_id'),
])
def test_schedule_rejects_other_tenant_reference(app, world, field, other):
    with app.test_request_context(), pytest.raises(RfSessionError) as excinfo:
        rf_session.schedule_session(**_schedule_kwargs(world, world.a, **{field: getattr(world.b, other)}))
    assert excinfo.value.status_code == 400


def test_schedule_rejects_operator_whose_role_belongs_to_other_tenant(app, world, pg):
    # Operator A loses its own station_operator role but holds the one of institution B.
    _run(pg, 'DELETE FROM user_role WHERE user_id = :u', u=world.a.operator_id)
    role_b = pg.execute(text("SELECT id FROM role WHERE institution_id = :i AND code = 'station_operator'"),
                        {'i': world.b.institution_id}).scalar_one()
    _run(pg, 'INSERT INTO user_role (user_id, role_id) VALUES (:u, :r)', u=world.a.operator_id, r=role_b)
    with app.test_request_context(), pytest.raises(RfSessionError) as excinfo:
        rf_session.schedule_session(**_schedule_kwargs(world, world.a))
    assert excinfo.value.status_code == 400


@pytest.mark.parametrize('room, station', [
    ('b.room_id', 'b.station_id'),          # room and station of B
    ('a.room_id', 'b.station_id'),          # station of B in a room of A
    ('a.room_id', 'a.other_station_id'),    # station of A but in another room of A
])
def test_schedule_rejects_room_station_outside_tenant_or_room(app, world, room, station):
    def resolve(path):
        tenant, attr = path.split('.')
        return getattr(getattr(world, tenant), attr)
    with app.test_request_context(), pytest.raises(RfSessionError) as excinfo:
        rf_session.schedule_session(**_schedule_kwargs(world, world.a, room_id=resolve(room), station_id=resolve(station)))
    assert excinfo.value.status_code == 400


def test_schedule_rejects_other_tenant_physician(app, world):
    team = [{'physician_id': world.b.physician_id, 'surgical_role_code': 'surgeon'}]
    with app.test_request_context(), pytest.raises(RfSessionError) as excinfo:
        rf_session.schedule_session(**_schedule_kwargs(world, world.a, physicians=team))
    assert excinfo.value.status_code == 400


def test_schedule_same_tenant_still_works(app, world, pg):
    world.a.add_instrument('available')
    with app.test_request_context():
        result = rf_session.schedule_session(**_schedule_kwargs(
            world, world.a, expected_lines=[{'family_id': world.family_id, 'expected_quantity': 1}]))
    assert _status(pg, result['session_id']) == 'scheduled'


def test_schedule_form_options_only_list_tenant_resources(app, world):
    with app.test_request_context():
        options = rf_session.schedule_form_options(world.a.institution_id)
    station_ids = {row['id'] for row in options['stations']}
    assert str(world.a.station_id) in station_ids and str(world.b.station_id) not in station_ids
    operator_ids = {row['id'] for row in options['operators']}
    assert str(world.a.operator_id) in operator_ids and str(world.b.operator_id) not in operator_ids


# --- 9. Stock ----------------------------------------------------------------------------


def test_stock_counts_only_own_tenant(app, world):
    world.b.add_instrument('available')
    world.b.add_instrument('available')
    world.a.add_instrument('available')
    with app.test_request_context():
        stock = rf_session.stock_for_families([world.family_id], world.a.institution_id)[str(world.family_id)]
        assert stock == {'stock_total': 1, 'stock_available': 1}
        recheck = rf_session.stock_recheck_lines(
            [{'family_id': str(world.family_id), 'expected_quantity': 2}], world.a.institution_id)
        assert recheck[0]['stock_available'] == 1 and recheck[0]['semaphore'] == 'BLOCK'
        assert rf_session.kit_expected_lines(world.b.kit_id, world.a.institution_id) == []
        assert len(rf_session.kit_expected_lines(world.a.kit_id, world.a.institution_id)) == 1
    # Two pieces in B do not make room for a quantity of 2 in A.
    with app.test_request_context(), pytest.raises(RfSessionError):
        rf_session.schedule_session(**_schedule_kwargs(
            world, world.a, expected_lines=[{'family_id': world.family_id, 'expected_quantity': 2}]))


# --- 10-12. Discrepancies, Lost, Recovered -----------------------------------------------


def test_supervisor_cannot_resolve_other_tenant_discrepancy(app, world, pg):
    session_b = world.b.add_session('correction_required')
    disc_b = world.b.add_discrepancy(session_b)
    with app.test_request_context(), pytest.raises(RfSessionError) as excinfo:
        rf_session.resolve_discrepancy(discrepancy_id=disc_b, supervisor_user_id=world.a.supervisor_id,
                                       institution_id=world.a.institution_id, notes='cross tenant attempt')
    assert excinfo.value.status_code == 404
    assert pg.execute(text('SELECT resolved FROM discrepancy WHERE id = :id'), {'id': disc_b}).scalar_one() is False


def test_resolve_requires_discrepancy_of_the_url_session(app, world, pg, client, login_as):
    session_a1 = world.a.add_session('correction_required')
    session_a2 = world.a.add_session('correction_required')
    disc_a2 = world.a.add_discrepancy(session_a2)
    with app.test_request_context(), pytest.raises(RfSessionError) as excinfo:
        rf_session.resolve_discrepancy(discrepancy_id=disc_a2, session_id=session_a1.id,
                                       supervisor_user_id=world.a.supervisor_id,
                                       institution_id=world.a.institution_id, notes='wrong session')
    assert excinfo.value.status_code == 404
    # Through the portal: a discrepancy id of B posted on a review page of A.
    session_b = world.b.add_session('correction_required')
    disc_b = world.b.add_discrepancy(session_b)
    world.a.add_discrepancy(session_a1)
    login_as(world.a.supervisor_id, world.a.institution_id, 'spd_supervisor')
    client.post(f'/supervisor/discrepancies/{session_a1.id}/review',
                data={'discrepancy_id': str(disc_b), 'notes': 'tamper', 'resolution_outcome': 'lost'})
    assert pg.execute(text('SELECT resolved FROM discrepancy WHERE id = :id'), {'id': disc_b}).scalar_one() is False


def test_mark_lost_never_touches_other_tenant_instrument(app, world, pg):
    instrument_b = world.b.add_instrument('available')   # inserted first: would be "first" without a tenant filter
    instrument_a = world.a.add_instrument('available')
    session_a = world.a.add_session('correction_required')
    disc_a = world.a.add_discrepancy(session_a)
    with app.test_request_context():
        rf_session.resolve_discrepancy(discrepancy_id=disc_a, session_id=session_a.id,
                                       supervisor_user_id=world.a.supervisor_id,
                                       institution_id=world.a.institution_id, notes='Piece lost', mark_lost=True)
    assert dict(_instrument(pg, instrument_a)) == {'cycle': 'lost', 'active': False}
    assert dict(_instrument(pg, instrument_b)) == {'cycle': 'available', 'active': True}
    assert _status(pg, session_a.id) == 'awaiting_spd_review'


def test_mark_lost_without_tenant_piece_changes_nothing_elsewhere(app, world, pg):
    instrument_b = world.b.add_instrument('available')
    session_a = world.a.add_session('correction_required')
    disc_a = world.a.add_discrepancy(session_a)
    with app.test_request_context():
        rf_session.resolve_discrepancy(discrepancy_id=disc_a, session_id=session_a.id,
                                       supervisor_user_id=world.a.supervisor_id,
                                       institution_id=world.a.institution_id, notes='Piece lost', mark_lost=True)
    assert dict(_instrument(pg, instrument_b)) == {'cycle': 'available', 'active': True}


def test_mark_recovered_never_touches_other_tenant_instrument(app, world, pg):
    lost_b = world.b.add_instrument('lost', active=False)
    lost_a = world.a.add_instrument('lost', active=False)
    session_a = world.a.add_session('correction_required')
    disc_a = world.a.add_discrepancy(session_a)
    with app.test_request_context():
        rf_session.resolve_discrepancy(discrepancy_id=disc_a, session_id=session_a.id,
                                       supervisor_user_id=world.a.supervisor_id,
                                       institution_id=world.a.institution_id, notes='Found it', mark_recovered=True)
    assert dict(_instrument(pg, lost_a)) == {'cycle': 'available', 'active': True}
    assert dict(_instrument(pg, lost_b)) == {'cycle': 'lost', 'active': False}


# --- 13. Close ---------------------------------------------------------------------------


def test_close_only_touches_own_tenant_reservations(app, world, pg):
    session_a = world.a.add_session('awaiting_spd_review')
    piece_a = world.a.add_instrument('reserved')
    piece_b = world.b.add_instrument('reserved')
    for piece in (piece_a, piece_b):   # piece_b on A's operation = corrupt cross-tenant reservation
        _run(pg, 'INSERT INTO instrument_reservation (operation_id, instrument_id) VALUES (:o, :i)',
             o=session_a.operation_id, i=piece)
    with app.test_request_context():
        rf_session.confirm_spd_close(session_id=session_a.id, supervisor_user_id=world.a.supervisor_id,
                                     institution_id=world.a.institution_id, material_recovered=True)
    assert _status(pg, session_a.id) == 'closed'
    assert _instrument(pg, piece_a)['cycle'] == 'sterilization'
    assert _instrument(pg, piece_b)['cycle'] == 'reserved'
    active_b = pg.execute(text('SELECT active FROM instrument_reservation WHERE instrument_id = :i'),
                          {'i': piece_b}).scalar_one()
    assert active_b is True


def test_cannot_close_other_tenant_session(app, world, pg, client, login_as):
    session_b = world.b.add_session('awaiting_spd_review')
    with app.test_request_context(), pytest.raises(RfSessionError) as excinfo:
        rf_session.confirm_spd_close(session_id=session_b.id, supervisor_user_id=world.a.supervisor_id,
                                     institution_id=world.a.institution_id, material_recovered=True)
    assert excinfo.value.status_code == 404
    login_as(world.a.supervisor_id, world.a.institution_id, 'spd_supervisor')
    response = client.post(f'/supervisor/sessions/{session_b.id}/close', data={'material_recovered': '1'})
    assert response.status_code == 404
    assert _status(pg, session_b.id) == 'awaiting_spd_review'


# --- 14. Privacy -------------------------------------------------------------------------


def test_cannot_set_privacy_agreement_on_other_tenant_session(app, world, pg, client, login_as):
    session_b = world.b.add_session('scheduled')
    with app.test_request_context(), pytest.raises(RfSessionError) as excinfo:
        rf_session.confirm_privacy_via_a(session_id=session_b.id, supervisor_user_id=world.a.supervisor_id,
                                         institution_id=world.a.institution_id,
                                         privacy_notice_version_id=world.notice_id, purpose_model_improvement=False)
    assert excinfo.value.status_code == 404
    login_as(world.a.supervisor_id, world.a.institution_id, 'spd_supervisor')
    client.post(f'/supervisor/sessions/{session_b.id}/privacy',
                data={'privacy_notice_version_id': str(world.notice_id)})
    count = pg.execute(text('SELECT count(*) FROM session_processing_agreement WHERE session_id = :s'),
                       {'s': session_b.id}).scalar_one()
    assert count == 0


# --- 15. Edit session / inventory ---------------------------------------------------------


def test_cannot_edit_other_tenant_session_or_inventory(app, world, pg):
    session_b = world.b.add_session('scheduled')
    lines = [{'family_id': world.family_id, 'expected_quantity': 1}]
    with app.test_request_context():
        with pytest.raises(RfSessionError) as excinfo:
            rf_session.update_session_inventory(session_id=session_b.id, supervisor_user_id=world.a.supervisor_id,
                                                institution_id=world.a.institution_id, lines=lines)
        assert excinfo.value.status_code == 404
        with pytest.raises(RfSessionError) as excinfo:
            rf_session.update_scheduled_session(
                session_id=session_b.id, supervisor_user_id=world.a.supervisor_id,
                institution_id=world.a.institution_id, operator_user_id=world.a.operator_id,
                procedure_type_id=world.procedure_type_id, room_id=world.a.room_id, station_id=world.a.station_id,
                kit_id=world.a.kit_id, patient_id=None,
                physicians=[{'physician_id': world.a.physician_id, 'surgical_role_code': 'surgeon'}],
                scheduled_at=datetime.now(timezone.utc), expected_lines=lines)
        assert excinfo.value.status_code == 404
    user_b = pg.execute(text('SELECT user_id FROM work_session WHERE id = :s'), {'s': session_b.id}).scalar_one()
    assert user_b == world.b.operator_id


def test_update_scheduled_session_rejects_other_tenant_references(app, world):
    session_a = world.a.add_session('scheduled')
    base = dict(session_id=session_a.id, supervisor_user_id=world.a.supervisor_id,
                institution_id=world.a.institution_id, operator_user_id=world.a.operator_id,
                procedure_type_id=world.procedure_type_id, room_id=world.a.room_id, station_id=world.a.station_id,
                kit_id=world.a.kit_id, patient_id=None,
                physicians=[{'physician_id': world.a.physician_id, 'surgical_role_code': 'surgeon'}],
                scheduled_at=datetime.now(timezone.utc), expected_lines=[])
    for override in ({'operator_user_id': world.b.operator_id}, {'kit_id': world.b.kit_id},
                     {'room_id': world.b.room_id, 'station_id': world.b.station_id},
                     {'patient_id': world.b.patient_id},
                     {'physicians': [{'physician_id': world.b.physician_id, 'surgical_role_code': 'surgeon'}]}):
        with app.test_request_context(), pytest.raises(RfSessionError) as excinfo:
            rf_session.update_scheduled_session(**{**base, **override})
        assert excinfo.value.status_code == 400, override


# --- Same-tenant regression: the whole RF flow still works ---------------------------------


def test_same_tenant_full_flow(app, world, pg):
    world.a.add_instrument('available')
    with app.test_request_context():
        session_id = rf_session.schedule_session(**_schedule_kwargs(
            world, world.a, expected_lines=[{'family_id': world.family_id, 'expected_quantity': 1}]))['session_id']
        session_id = uuid.UUID(session_id)
        rf_session.update_session_inventory(session_id=session_id, supervisor_user_id=world.a.supervisor_id,
                                            institution_id=world.a.institution_id,
                                            lines=[{'family_id': world.family_id, 'expected_quantity': 1}])
        result = rf_session.start_session(session_id=session_id, operator_user_id=world.a.operator_id,
                                          institution_id=world.a.institution_id)
        assert result['capture_mode'] == 'manual_no_privacy'
        report = rf_session.submit_manual_close(
            session_id=session_id, operator_user_id=world.a.operator_id, institution_id=world.a.institution_id,
            reports=[{'family_id': world.family_id, 'reported_quantity': 0, 'reason_code': 'shortage',
                      'notes': 'missing piece'}])
        assert report['status_code'] == 'correction_required'
        disc_id = pg.execute(text('SELECT id FROM discrepancy WHERE session_id = :s'), {'s': session_id}).scalar_one()
        # A physically missing instrument: a note ("found in tray") is not evidence of recovery.
        with pytest.raises(RfSessionError) as noted:
            rf_session.resolve_discrepancy(discrepancy_id=disc_id, session_id=session_id,
                                           supervisor_user_id=world.a.supervisor_id,
                                           institution_id=world.a.institution_id, notes='Found in tray')
        assert noted.value.status_code == 409
        rf_session.resolve_discrepancy(discrepancy_id=disc_id, session_id=session_id,
                                       supervisor_user_id=world.a.supervisor_id,
                                       institution_id=world.a.institution_id, notes='Not found', mark_lost=True)
        with pytest.raises(RfSessionError) as unsafe:
            rf_session.confirm_spd_close(session_id=session_id, supervisor_user_id=world.a.supervisor_id,
                                         institution_id=world.a.institution_id, material_recovered=True)
        assert unsafe.value.status_code == 409
        rf_session.confirm_spd_close(session_id=session_id, supervisor_user_id=world.a.supervisor_id,
                                     institution_id=world.a.institution_id, material_recovered=True,
                                     unreconciled_shortage_acknowledged=True)
    assert _status(pg, session_id) == 'closed'
    close_payload = pg.execute(text(
        "SELECT e.payload FROM count_event e JOIN cat_event_type t ON t.id = e.event_type_id "
        "WHERE e.session_id = :s AND t.code = 'session_close'"), {'s': session_id}).scalar_one()
    assert close_payload['safe_close_confirmed'] is False
    assert close_payload['unreconciled_physical_shortages'] == [str(disc_id)]
