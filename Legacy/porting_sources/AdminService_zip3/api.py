from flask import Blueprint, g, jsonify, request

from errors import bad_request
from security import ADMIN, SUPERVISOR, authenticate, require_roles
from services import (
    audit_log, catalogs, configuration, dashboard, families, instruments, kits, procedures, roles,
    users, vision_models,
)


api = Blueprint('api', __name__, url_prefix='/api/v1')


def body():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise bad_request('invalid_json', 'Request body must be a JSON object.')
    return payload


def arg(name, default=''):
    return (request.args.get(name) or default).strip()


def search_matches():
    return request.args.getlist('match')


def page_args(default_per_page=20):
    return {'page': request.args.get('page', 1), 'per_page': request.args.get('per_page', default_per_page)}


def ok(data, status=200):
    return jsonify(data), status


# --- Session / catalogs ---------------------------------------------------------------------

@api.get('/me')
def me():
    actor = authenticate()
    return ok({'id': str(actor.id), 'name': actor.name, 'email': actor.email,
               'institution_id': str(actor.institution_id), 'roles': actor.role_codes})


@api.get('/catalogs')
@require_roles(ADMIN, SUPERVISOR, resource_type='catalog')
def get_catalogs():
    names = [name.strip() for name in arg('names').split(',') if name.strip()]
    return ok(catalogs.catalogs(g.actor, names))


@api.get('/dashboard')
@require_roles(ADMIN, resource_type='dashboard')
def get_dashboard():
    return ok(dashboard.overview(g.actor))


# --- Instrument families --------------------------------------------------------------------

@api.get('/families')
@require_roles(ADMIN, resource_type='instrument_family')
def list_families():
    return ok(families.list_families(search=arg('search'), category=arg('category'),
                                     status=arg('status', 'active'), matches=search_matches(), **page_args()))


@api.post('/families')
@require_roles(ADMIN, resource_type='instrument_family')
def create_family():
    return ok(families.create_family(body()), 201)


@api.get('/families/<family_id>')
@require_roles(ADMIN, resource_type='instrument_family')
def get_family(family_id):
    return ok(families.get_family(family_id))


@api.put('/families/<family_id>')
@require_roles(ADMIN, resource_type='instrument_family')
def update_family(family_id):
    return ok(families.update_family(family_id, body()))


# --- Instruments (administrator and SPD supervisor) ----------------------------------------

@api.get('/instruments')
@require_roles(ADMIN, SUPERVISOR, resource_type='instrument')
def list_instruments():
    return ok(instruments.list_instruments(
        g.actor, search=arg('search'), type_code=arg('type'), cycle_status=arg('cycle_status'),
        status=arg('status', 'active'), matches=search_matches(), **page_args(),
    ))


@api.post('/instruments')
@require_roles(ADMIN, SUPERVISOR, resource_type='instrument')
def create_instrument():
    return ok(instruments.create_instrument(g.actor, body()), 201)


@api.get('/instruments/<instrument_id>')
@require_roles(ADMIN, SUPERVISOR, resource_type='instrument')
def get_instrument(instrument_id):
    return ok(instruments.get_instrument(g.actor, instrument_id))


@api.put('/instruments/<instrument_id>')
@require_roles(ADMIN, SUPERVISOR, resource_type='instrument')
def update_instrument(instrument_id):
    return ok(instruments.update_instrument(g.actor, instrument_id, body()))


# --- Kits (administrator and SPD supervisor; SPD lists active kits only) --------------------

@api.get('/kits')
@require_roles(ADMIN, SUPERVISOR, resource_type='kit')
def list_kits():
    active_only = not g.actor.has_any(ADMIN)
    return ok(kits.list_kits(g.actor, search=arg('search'), procedure_type=arg('procedure_type'),
                             status=arg('status', 'active'), active_only=active_only,
                             matches=search_matches(), **page_args()))


@api.post('/kits')
@require_roles(ADMIN, SUPERVISOR, resource_type='kit')
def create_kit():
    return ok(kits.create_kit(g.actor, body()), 201)


@api.get('/kits/<kit_id>')
@require_roles(ADMIN, SUPERVISOR, resource_type='kit')
def get_kit(kit_id):
    return ok(kits.get_kit(g.actor, kit_id))


@api.put('/kits/<kit_id>')
@require_roles(ADMIN, SUPERVISOR, resource_type='kit')
def update_kit(kit_id):
    return ok(kits.update_kit(g.actor, kit_id, body()))


# --- Procedures -----------------------------------------------------------------------------

@api.get('/procedures')
@require_roles(ADMIN, resource_type='procedure')
def list_procedures():
    return ok(procedures.list_procedures(g.actor, search=arg('search'), matches=search_matches(), **page_args(50)))


@api.post('/procedures')
@require_roles(ADMIN, resource_type='procedure')
def create_procedure():
    return ok(procedures.create_procedure(g.actor, body()), 201)


@api.get('/procedures/<procedure_id>')
@require_roles(ADMIN, resource_type='procedure')
def get_procedure(procedure_id):
    return ok(procedures.get_procedure(procedure_id))


@api.put('/procedures/<procedure_id>')
@require_roles(ADMIN, resource_type='procedure')
def update_procedure(procedure_id):
    return ok(procedures.update_procedure(g.actor, procedure_id, body()))


# --- Users ----------------------------------------------------------------------------------

@api.get('/users')
@require_roles(ADMIN, resource_type='user')
def list_users():
    return ok(users.list_users(search=arg('search'), role=arg('role'), institution=arg('institution'),
                               status=arg('status', 'active'), **page_args()))


@api.post('/users')
@require_roles(ADMIN, resource_type='user')
def create_user():
    return ok(users.create_user(g.actor, body()), 201)


@api.get('/users/<user_id>')
@require_roles(ADMIN, resource_type='user')
def get_user(user_id):
    return ok(users.get_user(user_id))


@api.put('/users/<user_id>')
@require_roles(ADMIN, resource_type='user')
def update_user(user_id):
    return ok(users.update_user(g.actor, user_id, body()))


@api.post('/users/<user_id>/password')
@require_roles(ADMIN, resource_type='user')
def reset_user_password(user_id):
    return ok(users.set_password(g.actor, user_id, body()))


@api.post('/users/<user_id>/deactivate')
@require_roles(ADMIN, resource_type='user')
def deactivate_user(user_id):
    return ok(users.set_status(g.actor, user_id, False))


@api.post('/users/<user_id>/activate')
@require_roles(ADMIN, resource_type='user')
def activate_user(user_id):
    return ok(users.set_status(g.actor, user_id, True))


# --- Roles ----------------------------------------------------------------------------------

@api.get('/roles')
@require_roles(ADMIN, resource_type='role')
def list_roles():
    return ok(roles.list_roles(search=arg('search'), **page_args(50)))


@api.post('/roles')
@require_roles(ADMIN, resource_type='role')
def create_role():
    return ok(roles.create_role(g.actor, body()), 201)


@api.get('/roles/<role_id>')
@require_roles(ADMIN, resource_type='role')
def get_role(role_id):
    return ok(roles.get_role(role_id))


@api.put('/roles/<role_id>')
@require_roles(ADMIN, resource_type='role')
def update_role(role_id):
    return ok(roles.update_role(g.actor, role_id, body()))


@api.delete('/roles/<role_id>')
@require_roles(ADMIN, resource_type='role')
def delete_role(role_id):
    return ok(roles.delete_role(g.actor, role_id))


# --- Vision models --------------------------------------------------------------------------

@api.get('/vision-models')
@require_roles(ADMIN, resource_type='vision_model')
def list_vision_models():
    return ok(vision_models.list_models(search=arg('search'), status=arg('status', 'all'), **page_args(50)))


@api.post('/vision-models')
@require_roles(ADMIN, resource_type='vision_model')
def create_vision_model():
    return ok(vision_models.create_model(g.actor, body()), 201)


@api.get('/vision-models/<model_id>')
@require_roles(ADMIN, resource_type='vision_model')
def get_vision_model(model_id):
    return ok(vision_models.get_model(model_id))


@api.put('/vision-models/<model_id>')
@require_roles(ADMIN, resource_type='vision_model')
def update_vision_model(model_id):
    return ok(vision_models.update_model(g.actor, model_id, body()))


@api.post('/vision-models/<model_id>/activate')
@require_roles(ADMIN, resource_type='vision_model')
def activate_vision_model(model_id):
    return ok(vision_models.activate_model(g.actor, model_id))


# --- Configuration --------------------------------------------------------------------------

@api.get('/institution')
@require_roles(ADMIN, resource_type='institution')
def get_institution():
    return ok(configuration.get_institution(g.actor))


@api.put('/institution')
@require_roles(ADMIN, resource_type='institution')
def update_institution():
    return ok(configuration.update_institution(g.actor, body()))


@api.get('/operating-rooms')
@require_roles(ADMIN, resource_type='operating_room')
def list_rooms():
    return ok(configuration.list_rooms(g.actor))


@api.post('/operating-rooms')
@require_roles(ADMIN, resource_type='operating_room')
def create_room():
    return ok(configuration.create_room(g.actor, body()), 201)


@api.get('/operating-rooms/<room_id>')
@require_roles(ADMIN, resource_type='operating_room')
def get_room(room_id):
    return ok(configuration.get_room(g.actor, room_id))


@api.put('/operating-rooms/<room_id>')
@require_roles(ADMIN, resource_type='operating_room')
def update_room(room_id):
    return ok(configuration.update_room(g.actor, room_id, body()))


@api.get('/capture-stations')
@require_roles(ADMIN, resource_type='capture_station')
def list_stations():
    return ok(configuration.list_stations(g.actor))


@api.post('/capture-stations')
@require_roles(ADMIN, resource_type='capture_station')
def create_station():
    return ok(configuration.create_station(g.actor, body()), 201)


@api.get('/capture-stations/<station_id>')
@require_roles(ADMIN, resource_type='capture_station')
def get_station(station_id):
    return ok(configuration.get_station(g.actor, station_id))


@api.put('/capture-stations/<station_id>')
@require_roles(ADMIN, resource_type='capture_station')
def update_station(station_id):
    return ok(configuration.update_station(g.actor, station_id, body()))


# --- Audit ----------------------------------------------------------------------------------

@api.get('/audit-events')
@require_roles(ADMIN, resource_type='audit_log')
def list_audit_events():
    return ok(audit_log.list_events(
        search=arg('search'), user=arg('user'), action=arg('action'), entity=arg('entity'),
        outcome=arg('outcome'), day=arg('date'), **page_args(50),
    ))
