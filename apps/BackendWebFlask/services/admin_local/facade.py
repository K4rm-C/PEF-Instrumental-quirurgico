"""Entry point of the Administration RF screens (same get/post/put/delete API for every view).

- Users and roles (and the `roles` entry of /catalogs): BackendAuthService /api/v1/auth over HTTP
  (identity_remote), always. The portal writes no "user", user_role or role row.
- Families, instruments and kits (and their /catalogs entries): CatalogService over HTTP when
  CATALOG_SERVICE_URL is configured (catalog_remote); otherwise the in-process admin_local modules.
  Exactly one of the two runs for a given request: no fallback, no double write.
- The audit log (/audit-events): AuditService over HTTP when AUDIT_SERVICE_URL is configured
  (services/audit_remote.py), otherwise admin_local.audit_log. Read only either way.
- Everything else (procedures, vision models, configuration, dashboard): in-process admin_local
  modules on the portal's PostgreSQL session. No AdminService exists.
"""
from flask import g, current_app
from sqlalchemy.exc import SQLAlchemyError
from services import audit_remote

from . import catalog_remote, identity_remote
from .api import api as router
from .errors import ApiError
from .messages import AdminDomainError
from .database import db


IDENTITY_CATALOG_NAMES = {'roles'}


def call(method, path, *, params=None, json=None):
    if path == '/catalogs':
        return _catalogs(params or {})
    if identity_remote.handles(path):
        return _call_identity(method, path, params or {}, json if json is not None else {})
    if catalog_remote.enabled() and catalog_remote.handles(path):
        return _call_catalog(method, path, params or {}, json if json is not None else {})
    if path == '/audit-events' and audit_remote.enabled():
        return _call_audit(params or {})
    return _call_local(method, path, params=params, json=json)


def _catalogs(params):
    """Each /catalogs entry from its owner: AuthService, CatalogService (when enabled) or local."""
    names = [name.strip() for name in (params.get('names') or '').split(',') if name.strip()]
    remote_catalog = catalog_remote.REMOTE_CATALOG_NAMES if catalog_remote.enabled() else set()
    local = [name for name in names if name not in IDENTITY_CATALOG_NAMES and name not in remote_catalog]
    result = _call_local('GET', '/catalogs', params={'names': ','.join(local)}) if local else {}
    if 'roles' in names:
        result['roles'] = _remote(identity_remote.catalog_roles)
    wanted = [name for name in names if name in remote_catalog]
    if wanted:
        result.update(catalog_remote.catalogs(wanted))
    return result


def _remote(function, *args, **kwargs):
    """Remote adapters may reuse admin_local presentation helpers that raise ApiError."""
    try:
        return function(*args, **kwargs)
    except ApiError as exc:
        raise AdminDomainError(exc.status, exc.code, exc.message, exc.field) from exc


def _call_audit(params):
    """Administration audit log: AuditService over HTTP (it applies the institution scope)."""
    try:
        return audit_remote.admin_events({k: v for k, v in params.items() if v not in (None, '')})
    except audit_remote.AuditServiceError as exc:
        raise AdminDomainError(exc.status, exc.code, exc.message, exc.field) from exc


def _call_identity(method, path, params, body):
    """Users and roles: AuthService over HTTP (never locally)."""
    params = {k: v for k, v in params.items() if v not in (None, '')}
    return _remote(identity_remote.dispatch, method, path, params, body)


def _is_admin():
    # Lazy import: routes.py imports admin_routes.py, which imports this module.
    from controllers.routes import _current_user
    return 'it_admin' in {role.get('code') for role in ((_current_user() or {}).get('roles') or [])}


def _call_catalog(method, path, params, body):
    """Families, instruments and kits: CatalogService over HTTP (never also locally)."""
    params = {k: v for k, v in params.items() if v not in (None, '')}
    procedure_types = None
    if method == 'GET' and path == '/kits':
        # Procedures stay in local Administration (DS02 catalog, read only here).
        procedure_types = _call_local('GET', '/catalogs', params={'names': 'procedure_types'})['procedure_types']
    return _remote(catalog_remote.dispatch, method, path, params, body, is_admin=_is_admin(),
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
