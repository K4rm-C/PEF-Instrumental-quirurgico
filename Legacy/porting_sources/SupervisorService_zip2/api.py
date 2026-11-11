from flask import Blueprint, g, jsonify, request
from sqlalchemy import func, select

from database import db
from errors import bad_request
from models import CatSessionStatus, Discrepancy, Operation, WorkSession
from security import authenticate, require_supervisor
from services import catalogs, discrepancies, instruments, kits, sessions


api = Blueprint('api', __name__, url_prefix='/api/v1')
CATALOG_NAMES = {
    'operators',
    'procedures',
    'rooms',
    'stations',
    'kits',
    'patients',
    'physicians',
    'surgical_roles',
    'phases',
    'privacy_notices',
    'families',
    'active_families',
    'categories',
    'cycle_statuses',
    'institutions',
    'roles',
    'procedure_types',
    'operating_rooms',
}


def _json_object():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise bad_request('invalid_json', 'Request body must be a JSON object.')
    return value


def _int_arg(name, default):
    value = request.args.get(name, default)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise bad_request('invalid_pagination', f'{name} must be an integer.', name) from exc


def _ok(payload, status=200):
    return jsonify(payload), status


@api.get('/me')
@require_supervisor('identity')
def me():
    actor = authenticate()
    return _ok({
        'id': str(actor.id),
        'name': actor.name,
        'email': actor.email,
        'institution_id': str(actor.institution_id),
        'roles': actor.role_codes,
    })


@api.get('/catalogs')
@require_supervisor('catalog')
def get_catalogs():
    available = catalogs.scheduling_catalogs(g.actor)
    raw_names = request.args.get('names', '')
    names = [name.strip() for name in raw_names.split(',') if name.strip()]
    if not names:
        return _ok(available)
    unknown = set(names) - CATALOG_NAMES
    if unknown:
        raise bad_request('unknown_catalog', f'Unknown catalog name: {sorted(unknown)[0]}.', 'names')
    return _ok({name: available[name] for name in names})


@api.get('/kits/<kit_id>/expected-inventory')
@require_supervisor('kit')
def kit_expected_inventory(kit_id):
    from services.sessions import _uuid

    return _ok({'items': catalogs.kit_expected_lines(g.actor, _uuid(kit_id, 'kit_id'))})


@api.post('/inventory/stock-recheck')
@require_supervisor('inventory')
def stock_recheck():
    return _ok({'lines': sessions.stock_recheck(g.actor, _json_object().get('lines'))})


@api.get('/instruments')
@require_supervisor('instrument')
def list_instruments():
    return _ok(instruments.list_instruments(
        g.actor,
        search=(request.args.get('search') or '').strip(),
        type_code=(request.args.get('type') or '').strip(),
        cycle_status=(request.args.get('cycle_status') or '').strip(),
        status=(request.args.get('status') or 'active').strip(),
        page=_int_arg('page', 1),
        per_page=_int_arg('per_page', 20),
        matches=request.args.getlist('match'),
    ))


@api.post('/instruments')
@require_supervisor('instrument')
def create_instrument():
    return _ok(instruments.create_instrument(g.actor, _json_object()), 201)


@api.get('/instruments/<instrument_id>')
@require_supervisor('instrument')
def get_instrument(instrument_id):
    from services.sessions import _uuid

    return _ok(instruments.get_instrument(g.actor, _uuid(instrument_id, 'instrument_id')))


@api.put('/instruments/<instrument_id>')
@require_supervisor('instrument')
def update_instrument(instrument_id):
    from services.sessions import _uuid

    return _ok(instruments.update_instrument(
        g.actor, _uuid(instrument_id, 'instrument_id'), _json_object(),
    ))


@api.get('/kits')
@require_supervisor('kit')
def list_kits():
    return _ok(kits.list_kits(
        g.actor,
        search=(request.args.get('search') or '').strip(),
        procedure_type=(request.args.get('procedure_type') or '').strip(),
        status=(request.args.get('status') or 'active').strip(),
        page=_int_arg('page', 1),
        per_page=_int_arg('per_page', 20),
        matches=request.args.getlist('match'),
    ))


@api.post('/kits')
@require_supervisor('kit')
def create_kit():
    return _ok(kits.create_kit(g.actor, _json_object()), 201)


@api.get('/kits/<kit_id>')
@require_supervisor('kit')
def get_kit(kit_id):
    from services.sessions import _uuid

    return _ok(kits.get_kit(g.actor, _uuid(kit_id, 'kit_id')))


@api.put('/kits/<kit_id>')
@require_supervisor('kit')
def update_kit(kit_id):
    from services.sessions import _uuid

    return _ok(kits.update_kit(g.actor, _uuid(kit_id, 'kit_id'), _json_object()))


@api.get('/dashboard')
@require_supervisor('dashboard')
def dashboard():
    institution_id = g.actor.institution_id
    session_counts = db.execute(
        select(CatSessionStatus.code, func.count(WorkSession.id))
        .join(WorkSession, WorkSession.status_id == CatSessionStatus.id)
        .join(Operation, Operation.id == WorkSession.operation_id)
        .where(Operation.institution_id == institution_id)
        .group_by(CatSessionStatus.code)
    ).all()
    open_discrepancies = int(db.scalar(
        select(func.count(Discrepancy.id))
        .join(WorkSession, WorkSession.id == Discrepancy.session_id)
        .join(Operation, Operation.id == WorkSession.operation_id)
        .where(Operation.institution_id == institution_id, Discrepancy.resolved.is_(False))
    ) or 0)
    return _ok({
        'sessions_by_status': {code: int(count) for code, count in session_counts},
        'open_discrepancies': open_discrepancies,
    })


@api.get('/sessions')
@require_supervisor('work_session')
def list_sessions():
    return _ok(sessions.list_sessions(
        g.actor,
        status=(request.args.get('status') or '').strip(),
        search=(request.args.get('search') or '').strip(),
        date_value=(request.args.get('date') or '').strip(),
        page=_int_arg('page', 1),
        per_page=_int_arg('per_page', 20),
    ))


@api.post('/sessions')
@require_supervisor('work_session')
def create_session():
    return _ok(sessions.schedule(g.actor, _json_object()), 201)


@api.get('/sessions/<session_id>')
@require_supervisor('work_session')
def get_session(session_id):
    from services.sessions import _uuid

    return _ok(sessions.get_session(g.actor, _uuid(session_id, 'session_id')))


@api.put('/sessions/<session_id>')
@require_supervisor('work_session')
def update_session(session_id):
    from services.sessions import _uuid

    return _ok(sessions.update_scheduled(
        g.actor, _uuid(session_id, 'session_id'), _json_object(),
    ))


@api.put('/sessions/<session_id>/inventory')
@require_supervisor('work_session')
def update_session_inventory(session_id):
    from services.sessions import _uuid

    return _ok(sessions.update_inventory(
        g.actor, _uuid(session_id, 'session_id'), _json_object(),
    ))


@api.post('/sessions/<session_id>/privacy-agreement')
@require_supervisor('work_session')
def confirm_privacy_agreement(session_id):
    from services.sessions import _uuid

    return _ok(sessions.confirm_privacy(
        g.actor, _uuid(session_id, 'session_id'), _json_object(),
    ))


@api.post('/sessions/<session_id>/close')
@require_supervisor('work_session')
def close_session(session_id):
    from services.sessions import _uuid

    return _ok(sessions.close_session(
        g.actor, _uuid(session_id, 'session_id'), _json_object(),
    ))


@api.get('/discrepancies')
@require_supervisor('discrepancy')
def list_discrepancies():
    return _ok(discrepancies.list_discrepancies(
        g.actor,
        status=(request.args.get('status') or '').strip(),
        search=(request.args.get('search') or '').strip(),
        page=_int_arg('page', 1),
        per_page=_int_arg('per_page', 20),
    ))


@api.get('/discrepancies/<discrepancy_id>')
@require_supervisor('discrepancy')
def get_discrepancy(discrepancy_id):
    from services.sessions import _uuid

    return _ok(discrepancies.get_discrepancy(
        g.actor, _uuid(discrepancy_id, 'discrepancy_id'),
    ))


@api.post('/discrepancies/<discrepancy_id>/resolve')
@require_supervisor('discrepancy')
def resolve_discrepancy(discrepancy_id):
    from services.sessions import _uuid

    return _ok(discrepancies.resolve(
        g.actor, _uuid(discrepancy_id, 'discrepancy_id'), _json_object(),
    ))
