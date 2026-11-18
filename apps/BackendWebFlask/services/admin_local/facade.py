"""Entry point of the Administration RF screens (same get/post/put/delete API for every view).

Administration (it_admin) runs every business operation in-process (admin_local modules on the
portal's PostgreSQL session): users, roles, families, procedures and catalogs, privacy notices,
rooms/stations, vision models, audit log, settings and dashboard. No AdminService, no CRUD call to
AuthService, CatalogService or AuditService. The only AuthService call is session revocation after
a deactivation or password reset (admin_local.auth_sessions, D-1); sign-in, /verify, refresh and
logout stay in controllers/routes.py for every role.

The SPD supervisor reaches the shared /admin/instruments and /admin/kits screens (they own pieces
and kits). For them only, when CATALOG_SERVICE_URL is configured, those operations and their
/catalogs entries go to CatalogService over HTTP (catalog_remote): their microservice architecture
is unchanged. Exactly one of the two paths runs for a request: no fallback, no double write.
"""
from flask import g, current_app
from sqlalchemy.exc import SQLAlchemyError

from . import catalog_remote
from .api import api as router
from .errors import ApiError
from .messages import AdminDomainError
from .database import db


def call(method, path, *, params=None, json=None):
    if path == '/catalogs':
        return _catalogs(params or {})
    if _supervisor_catalog_remote() and catalog_remote.handles(path) and path.split('/')[1] != 'families':
        return _call_catalog(method, path, params or {}, json if json is not None else {})
    return _call_local(method, path, params=params, json=json)


def _is_admin():
    # Lazy import: routes.py imports admin_routes.py, which imports this module.
    from controllers.routes import _current_user
    return 'it_admin' in {role.get('code') for role in ((_current_user() or {}).get('roles') or [])}


def _supervisor_catalog_remote():
    """CatalogService serves the SPD supervisor's pieces/kits when configured; never Administration."""
    return catalog_remote.enabled() and not _is_admin()


def _catalogs(params):
    """Each /catalogs entry from its owner: local, or CatalogService for the SPD supervisor."""
    names = [name.strip() for name in (params.get('names') or '').split(',') if name.strip()]
    remote_catalog = catalog_remote.REMOTE_CATALOG_NAMES if _supervisor_catalog_remote() else set()
    local = [name for name in names if name not in remote_catalog]
    result = _call_local('GET', '/catalogs', params={'names': ','.join(local)}) if local else {}
    wanted = [name for name in names if name in remote_catalog]
    if wanted:
        result.update(_remote(catalog_remote.catalogs, wanted))
    return result


def _remote(function, *args, **kwargs):
    """Remote adapters may reuse admin_local presentation helpers that raise ApiError."""
    try:
        return function(*args, **kwargs)
    except ApiError as exc:
        raise AdminDomainError(exc.status, exc.code, exc.message, exc.field) from exc


def _call_catalog(method, path, params, body):
    """SPD supervisor's instruments and kits: CatalogService over HTTP (never also locally)."""
    params = {k: v for k, v in params.items() if v not in (None, '')}
    procedure_types = None
    if method == 'GET' and path == '/kits':
        # Procedures stay in local Administration (DS02 catalog, read only here).
        procedure_types = _call_local('GET', '/catalogs', params={'names': 'procedure_types'})['procedure_types']
    return _remote(catalog_remote.dispatch, method, path, params, body, is_admin=False,
                   procedure_types=procedure_types)


def _call_local(method, path, *, params=None, json=None):
    # Every Admin endpoint verifies the AuthService-verified portal user and roles.
    if getattr(g, 'admin_local_active', False):
        raise RuntimeError('Nested Admin domain dispatch is not supported')
    g.admin_local_active = True
    g.admin_local_params = {k: v for k, v in (params or {}).items() if v not in (None, '')}
    g.admin_local_body = json if json is not None else {}
    try:
        return router.dispatch(method, path)
    except ApiError as exc:
        db.rollback()
        raise AdminDomainError(exc.status, exc.code, exc.message, exc.field) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        current_app.logger.exception('Error en Administración (PostgreSQL)')
        raise AdminDomainError(503, 'database_unavailable') from exc
    finally:
        g.admin_local_active = False
        g.pop('admin_local_params', None)
        g.pop('admin_local_body', None)
        # g.actor is valid only within request; Flask clears g at end.


def get(path, **params):
    return call('GET', path, params=params)


def post(path, json=None):
    return call('POST', path, json=json if json is not None else {})


def put(path, json):
    return call('PUT', path, json=json)


def delete(path):
    return call('DELETE', path)
