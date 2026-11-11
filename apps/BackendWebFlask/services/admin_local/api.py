from flask import g

from .errors import bad_request
from .security import ADMIN, SUPERVISOR, authenticate, require_catalog_steward, require_roles
from . import (
    audit_log, catalogs, configuration, dashboard, families, instruments, integration_clients, kits, media, privacy,
    procedures, roles,
    selection_catalogs, settings, users, vision_models, model_evaluations, model_regressions,
)


from .router import LocalAdminRouter

api = LocalAdminRouter()


def body():
    payload = getattr(g, "admin_local_body", None)
    if not isinstance(payload, dict):
        raise bad_request('invalid_json', 'Request body must be a JSON object.')
    return payload


def arg(name, default=''):
    return str((getattr(g, 'admin_local_params', {}) or {}).get(name) or default).strip()


def search_matches():
    return (getattr(g, 'admin_local_params', {}) or {}).get('match', [])


def page_args(default_per_page=20):
    return {'page': (getattr(g, 'admin_local_params', {}) or {}).get('page', 1),
            'per_page': (getattr(g, 'admin_local_params', {}) or {}).get('per_page', default_per_page)}


def ok(data, status=200):
    return data


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
@require_catalog_steward('instrument_family')
def create_family():
    return ok(families.create_family(body()), 201)


@api.get('/families/<family_id>')
@require_roles(ADMIN, resource_type='instrument_family')
def get_family(family_id):
    return ok(families.get_family(family_id))


@api.put('/families/<family_id>')
@require_roles(ADMIN, resource_type='instrument_family')
@require_catalog_steward('instrument_family')
def update_family(family_id):
    return ok(families.update_family(family_id, body()))


@api.post('/families/<family_id>/examples')
@require_roles(ADMIN, resource_type='instrument_family')
@require_catalog_steward('instrument_family')
def add_family_example(family_id):
    return ok(families.add_example(g.actor, family_id, body()), 201)


@api.put('/families/<family_id>/examples/<example_id>')
@require_roles(ADMIN, resource_type='instrument_family')
@require_catalog_steward('instrument_family')
def update_family_example(family_id, example_id):
    return ok(families.update_example(g.actor, family_id, example_id, body()))


@api.delete('/families/<family_id>/examples/<example_id>')
@require_roles(ADMIN, resource_type='instrument_family')
@require_catalog_steward('instrument_family')
def delete_family_example(family_id, example_id):
    return ok(families.delete_example(g.actor, family_id, example_id))


@api.get('/families/<family_id>/examples/<example_id>/image')
@require_roles(ADMIN, resource_type='instrument_family')
def family_example_image(family_id, example_id):
    return ok(media.family_example_image(family_id, example_id))


# --- Instruments and kits: SPD supervisor only (RACI of rf_admin_app.md: Admin has no CRUD) --

@api.get('/instruments')
@require_roles(SUPERVISOR, resource_type='instrument')
def list_instruments():
    return ok(instruments.list_instruments(
        g.actor, search=arg('search'), type_code=arg('type'), cycle_status=arg('cycle_status'),
        status=arg('status', 'active'), matches=search_matches(), **page_args(),
    ))


@api.post('/instruments')
@require_roles(SUPERVISOR, resource_type='instrument')
def create_instrument():
    return ok(instruments.create_instrument(g.actor, body()), 201)


@api.get('/instruments/<instrument_id>')
@require_roles(SUPERVISOR, resource_type='instrument')
def get_instrument(instrument_id):
    return ok(instruments.get_instrument(g.actor, instrument_id))


@api.put('/instruments/<instrument_id>')
@require_roles(SUPERVISOR, resource_type='instrument')
def update_instrument(instrument_id):
    return ok(instruments.update_instrument(g.actor, instrument_id, body()))



@api.get('/kits')
@require_roles(SUPERVISOR, resource_type='kit')
def list_kits():
    active_only = not g.actor.has_any(ADMIN)
    return ok(kits.list_kits(g.actor, search=arg('search'), procedure_type=arg('procedure_type'),
                             status=arg('status', 'active'), active_only=active_only,
                             matches=search_matches(), **page_args()))


@api.post('/kits')
@require_roles(SUPERVISOR, resource_type='kit')
def create_kit():
    return ok(kits.create_kit(g.actor, body()), 201)


@api.get('/kits/<kit_id>')
@require_roles(SUPERVISOR, resource_type='kit')
def get_kit(kit_id):
    return ok(kits.get_kit(g.actor, kit_id))


@api.put('/kits/<kit_id>')
@require_roles(SUPERVISOR, resource_type='kit')
def update_kit(kit_id):
    return ok(kits.update_kit(g.actor, kit_id, body()))


# --- Procedures -----------------------------------------------------------------------------

@api.get('/procedures')
@require_roles(ADMIN, resource_type='procedure')
def list_procedures():
    return ok(procedures.list_procedures(g.actor, search=arg('search'), matches=search_matches(), **page_args(50)))


@api.post('/procedures')
@require_roles(ADMIN, resource_type='procedure')
@require_catalog_steward('procedure')
def create_procedure():
    return ok(procedures.create_procedure(g.actor, body()), 201)


@api.get('/procedures/<procedure_id>')
@require_roles(ADMIN, resource_type='procedure')
def get_procedure(procedure_id):
    return ok(procedures.get_procedure(g.actor, procedure_id))


@api.put('/procedures/<procedure_id>')
@require_roles(ADMIN, resource_type='procedure')
@require_catalog_steward('procedure')
def update_procedure(procedure_id):
    return ok(procedures.update_procedure(g.actor, procedure_id, body()))


# --- Users (RF-AD-05: own institution; sessions revoked through AuthService) ----------------

@api.get('/users')
@require_roles(ADMIN, resource_type='user')
def list_users():
    return ok(users.list_users(g.actor, search=arg('search'), role=arg('role'), status=arg('status', 'active'),
                               **page_args()))


@api.post('/users')
@require_roles(ADMIN, resource_type='user')
def create_user():
    return ok(users.create_user(g.actor, body()), 201)


@api.get('/users/<user_id>')
@require_roles(ADMIN, resource_type='user')
def get_user(user_id):
    return ok(users.get_user(g.actor, user_id))


@api.put('/users/<user_id>')
@require_roles(ADMIN, resource_type='user')
def update_user(user_id):
    return ok(users.update_user(g.actor, user_id, body()))


@api.post('/users/<user_id>/activate')
@require_roles(ADMIN, resource_type='user')
def activate_user(user_id):
    return ok(users.set_status(g.actor, user_id, True))


@api.post('/users/<user_id>/deactivate')
@require_roles(ADMIN, resource_type='user')
def deactivate_user(user_id):
    return ok(users.set_status(g.actor, user_id, False))


@api.post('/users/<user_id>/password')
@require_roles(ADMIN, resource_type='user')
def reset_user_password(user_id):
    return ok(users.set_password(g.actor, user_id, body()))


# --- Settings (RF-AD-10: own institution) ---------------------------------------------------

@api.get('/settings')
@require_roles(ADMIN, resource_type='app_setting')
def get_settings():
    return ok(settings.describe(g.actor.institution_id))


@api.put('/settings')
@require_roles(ADMIN, resource_type='app_setting')
def update_settings():
    return ok(settings.update(g.actor, body()))


# --- Roles (RF-AD-06: fixed codes, description only; own institution) -----------------------

@api.get('/roles')
@require_roles(ADMIN, resource_type='role')
def list_roles():
    return ok(roles.list_roles(g.actor, search=arg('search'), **page_args(50)))


@api.get('/roles/<role_id>')
@require_roles(ADMIN, resource_type='role')
def get_role(role_id):
    return ok(roles.get_role(g.actor, role_id))


@api.put('/roles/<role_id>')
@require_roles(ADMIN, resource_type='role')
def update_role(role_id):
    return ok(roles.update_role(g.actor, role_id, body()))


@api.post('/roles')
@require_roles(ADMIN, resource_type='role')
def create_role():
    return roles.reject_code_change(g.actor, 'CREATE')


@api.delete('/roles/<role_id>')
@require_roles(ADMIN, resource_type='role')
def delete_role(role_id):
    return roles.reject_code_change(g.actor, 'DELETE', role_id)


# --- Selection catalogs (RF-AD-03: phases, categories, discrepancy reasons) -----------------

@api.get('/selection-catalogs/<kind>')
@require_roles(ADMIN, resource_type='selection_catalog')
def list_selection_catalog(kind):
    return ok(selection_catalogs.list_items(kind, search=arg('search')))


@api.post('/selection-catalogs/<kind>')
@require_roles(ADMIN, resource_type='selection_catalog')
@require_catalog_steward('selection_catalog')
def create_selection_catalog_item(kind):
    return ok(selection_catalogs.create_item(kind, body()), 201)


@api.put('/selection-catalogs/<kind>/<item_id>')
@require_roles(ADMIN, resource_type='selection_catalog')
@require_catalog_steward('selection_catalog')
def update_selection_catalog_item(kind, item_id):
    return ok(selection_catalogs.update_item(kind, item_id, body()))


# --- Privacy notices (RF-AD-04) -------------------------------------------------------------

@api.get('/privacy-notices')
@require_roles(ADMIN, resource_type='privacy_notice')
def list_privacy_notices():
    return ok(privacy.list_notices(search=arg('search'), status=arg('status', 'all'), **page_args()))


@api.post('/privacy-notices')
@require_roles(ADMIN, resource_type='privacy_notice')
@require_catalog_steward('privacy_notice')
def publish_privacy_notice():
    return ok(privacy.publish(g.actor, body()), 201)


@api.post('/privacy-notices/<notice_id>/activate')
@require_roles(ADMIN, resource_type='privacy_notice')
@require_catalog_steward('privacy_notice')
def activate_privacy_notice(notice_id):
    return ok(privacy.set_active(g.actor, notice_id, True))


@api.post('/privacy-notices/<notice_id>/deactivate')
@require_roles(ADMIN, resource_type='privacy_notice')
@require_catalog_steward('privacy_notice')
def deactivate_privacy_notice(notice_id):
    return ok(privacy.set_active(g.actor, notice_id, False))


@api.get('/privacy-notices/<notice_id>/document')
@require_roles(ADMIN, resource_type='privacy_notice')
def privacy_notice_document(notice_id):
    return ok(media.privacy_document(notice_id))


# --- Vision models --------------------------------------------------------------------------

@api.get('/vision-models')
@require_roles(ADMIN, resource_type='vision_model')
def list_vision_models():
    return ok(vision_models.list_models(search=arg('search'), status=arg('status', 'all'), **page_args(50)))


@api.post('/vision-models')
@require_roles(ADMIN, resource_type='vision_model')
@require_catalog_steward('vision_model')
def create_vision_model():
    return ok(vision_models.upload_model(g.actor, body()), 201)


@api.get('/vision-models/<model_id>')
@require_roles(ADMIN, resource_type='vision_model')
def get_vision_model(model_id):
    return ok(vision_models.get_model(model_id))


@api.put('/vision-models/<model_id>')
@require_roles(ADMIN, resource_type='vision_model')
@require_catalog_steward('vision_model')
def update_vision_model(model_id):
    return ok(vision_models.update_model(g.actor, model_id, body()))


@api.post('/vision-models/<model_id>/activate')
@require_roles(ADMIN, resource_type='vision_model')
@require_catalog_steward('vision_model')
def activate_vision_model(model_id):
    return ok(vision_models.activate_model(g.actor, model_id))


@api.post('/vision-models/<model_id>/deactivate')
@require_roles(ADMIN, resource_type='vision_model')
@require_catalog_steward('vision_model')
def deactivate_vision_model(model_id):
    return ok(vision_models.deactivate_model(g.actor, model_id))


@api.post('/vision-models/<model_id>/publish')
@require_roles(ADMIN, resource_type='vision_model')
@require_catalog_steward('vision_model')
def publish_vision_model(model_id):
    return ok(vision_models.publish_mapping(g.actor, model_id, body()))


# MS-11 (R17): recorded evaluations (datasets, metrics, thresholds) and history of a model.
@api.get('/vision-models/<model_id>/evaluations')
@require_roles(ADMIN, resource_type='vision_model')
def vision_model_evaluations(model_id):
    return ok(model_evaluations.model_overview(model_id))


@api.post('/vision-models/<model_id>/evaluations')
@require_roles(ADMIN, resource_type='vision_model')
@require_catalog_steward('vision_model')
def record_vision_model_evaluation(model_id):
    return ok(model_evaluations.record_evaluation(g.actor, model_id, body()), 201)


# MS-12: regression runs (real inference outside the portal) against a baseline model; immutable.
@api.post('/vision-models/<model_id>/regressions')
@require_roles(ADMIN, resource_type='vision_model')
@require_catalog_steward('vision_model')
def record_vision_model_regression(model_id):
    return ok(model_regressions.record_regression(g.actor, model_id, body()), 201)


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


@api.get('/cameras')
@require_roles(ADMIN, resource_type='capture_camera')
def list_cameras():
    return ok(configuration.list_cameras(g.actor))


@api.post('/cameras')
@require_roles(ADMIN, resource_type='capture_camera')
def create_camera():
    return ok(configuration.create_camera(g.actor, body()), 201)


@api.get('/cameras/<camera_id>')
@require_roles(ADMIN, resource_type='capture_camera')
def get_camera(camera_id):
    return ok(configuration.get_camera(g.actor, camera_id))


@api.put('/cameras/<camera_id>')
@require_roles(ADMIN, resource_type='capture_camera')
def update_camera(camera_id):
    return ok(configuration.update_camera(g.actor, camera_id, body()))


# MS-12 (ms-camaras): calibration measured by a technician; immutable history.
@api.get('/cameras/<camera_id>/calibrations')
@require_roles(ADMIN, resource_type='capture_camera')
def list_camera_calibrations(camera_id):
    return ok(configuration.list_calibrations(g.actor, camera_id))


@api.post('/cameras/<camera_id>/calibrations')
@require_roles(ADMIN, resource_type='capture_camera')
def record_camera_calibration(camera_id):
    return ok(configuration.record_calibration(g.actor, camera_id, body()), 201)


# --- Integration clients (MS-07, PEF-DS08 machine clients) ----------------------------------

@api.get('/integration-clients')
@require_roles(ADMIN, resource_type='integration_client')
def list_integration_clients():
    return ok(integration_clients.list_clients(g.actor))


@api.post('/integration-clients')
@require_roles(ADMIN, resource_type='integration_client')
def create_integration_client():
    return ok(integration_clients.create_client(g.actor, body()), 201)


@api.get('/integration-clients/<client_id>')
@require_roles(ADMIN, resource_type='integration_client')
def get_integration_client(client_id):
    return ok(integration_clients.get_client(g.actor, client_id))


@api.put('/integration-clients/<client_id>')
@require_roles(ADMIN, resource_type='integration_client')
def update_integration_client(client_id):
    return ok(integration_clients.update_client(g.actor, client_id, body()))


@api.post('/integration-clients/<client_id>/rotate-secret')
@require_roles(ADMIN, resource_type='integration_client')
def rotate_integration_client_secret(client_id):
    return ok(integration_clients.rotate_secret(g.actor, client_id))


# --- Audit ----------------------------------------------------------------------------------

@api.get('/audit-events')
@require_roles(ADMIN, resource_type='audit_log')
def list_audit_events():
    return ok(audit_log.list_events(
        g.actor, search=arg('search'), user=arg('user'), action=arg('action'), entity=arg('entity'),
        outcome=arg('outcome'), day=arg('date'), date_from=arg('date_from'), date_to=arg('date_to'),
        **page_args(50),
    ))
