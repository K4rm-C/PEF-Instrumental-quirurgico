"""RF-AD-06 Roles: the four product codes are fixed; the administrator only edits descriptions.

Same `role` / `user_role` tables AuthService reads on every /verify and /refresh, so a new
description is visible to it immediately; nothing about authentication changes here.
Scope (D-2): the administrator sees and edits only the roles of their own institution; a role of
another institution answers 404, as if it did not exist.
"""
from sqlalchemy import func, select

from . import audit
from .database import db
from .errors import ApiError, bad_request, not_found
from .models import Institution, Role, UserRole
from .common import matches_search, paginate, parse_uuid, text


ENTITY = 'ROLE'
RESOURCE = 'role'
# RF-AD-06: the only codes of v1, in display order. Creating others or deleting these is rejected.
PRODUCT_ROLES = ('station_operator', 'spd_supervisor', 'it_admin', 'or_charge')


def _rank(code):
    return PRODUCT_ROLES.index(code) if code in PRODUCT_ROLES else len(PRODUCT_ROLES)


def _assigned_counts(role_ids):
    if not role_ids:
        return {}
    return dict(db.execute(
        select(UserRole.role_id, func.count(UserRole.user_id))
        .where(UserRole.role_id.in_(role_ids)).group_by(UserRole.role_id)
    ).all())


def _serialize(role, institution, assigned_users):
    return {
        'id': str(role.id),
        'code': role.code,
        'description': role.description,
        'institution': {'id': str(institution.id), 'name': institution.name} if institution else None,
        'assigned_users': assigned_users,
        'system': role.code in PRODUCT_ROLES,
    }


def _own_roles(actor):
    rows = db.execute(select(Role).where(Role.institution_id == actor.institution_id)).scalars().all()
    return sorted(rows, key=lambda role: (_rank(role.code), role.code))


def list_roles(actor, *, search='', page=1, per_page=50):
    institution = db.get(Institution, actor.institution_id)
    roles = [role for role in _own_roles(actor) if matches_search(search, role.code, role.description)]
    counts = _assigned_counts([role.id for role in roles])
    result = paginate([_serialize(role, institution, counts.get(role.id, 0)) for role in roles], page, per_page)
    result['filters'] = {'search': search or ''}
    return result


def catalog_roles(actor):
    """The `roles` entry of /catalogs (user form and filters): own institution only."""
    return [{'id': str(role.id), 'code': role.code, 'description': role.description,
             'institution_id': str(role.institution_id)} for role in _own_roles(actor)]


def _owned(actor, role_id):
    role = db.get(Role, parse_uuid(role_id, 'role_not_found', 'Role not found.'))
    if role is None or role.institution_id != actor.institution_id:
        raise not_found('role_not_found', 'Role not found.')
    return role


def get_role(actor, role_id):
    role = _owned(actor, role_id)
    return _serialize(role, db.get(Institution, role.institution_id), _assigned_counts([role.id]).get(role.id, 0))


def update_role(actor, role_id, payload):
    role = _owned(actor, role_id)
    if 'code' in payload and str(payload.get('code') or '').strip() != role.code:
        raise bad_request('role_code_immutable', 'The role code cannot be changed.', 'code')
    description = text(payload, 'description', required=True, max_length=255, label='Description')
    if description != role.description:
        role.description = description
        audit.record(f'UPDATE_{ENTITY}', RESOURCE, role.id)
        db.commit()
    return get_role(actor, role.id)


def reject_code_change(actor, action, role_id=None):
    """Creating or deleting a role code is not part of v1: audited as denied, then refused."""
    target = None
    if role_id is not None:
        target = _owned(actor, role_id).id
    audit.record(f'DENIED_{action}_{ENTITY}', RESOURCE, target, outcome='denied')
    db.commit()
    raise ApiError(405, 'role_codes_fixed', 'Role codes are fixed: roles cannot be created or deleted.')
