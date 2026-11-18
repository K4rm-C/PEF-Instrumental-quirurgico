import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import audit
from database import db
from errors import bad_request, conflict, not_found
from models import Institution, Role, UserRole
from security import ADMIN, OPERATOR, SUPERVISOR
from services.common import matches_search, paginate, parse_uuid, text


ENTITY = 'ROLE'
RESOURCE = 'role'
SYSTEM_ROLES = {ADMIN, SUPERVISOR, OPERATOR}
CODE_RE = re.compile(r'^[a-z][a-z0-9_]*$')


def _counts():
    return dict(db.execute(
        select(UserRole.role_id, func.count(UserRole.user_id)).group_by(UserRole.role_id)
    ).all())


def _serialize(role, institutions, counts):
    institution = institutions.get(role.institution_id)
    return {
        'id': str(role.id),
        'code': role.code,
        'description': role.description,
        'institution': {'id': str(institution.id), 'name': institution.name} if institution else None,
        'assigned_users': counts.get(role.id, 0),
        'system': role.code in SYSTEM_ROLES,
    }


def list_roles(*, search='', page=1, per_page=50):
    institutions = {row.id: row for row in db.execute(select(Institution)).scalars()}
    counts = _counts()
    rows = [
        _serialize(role, institutions, counts)
        for role in db.execute(select(Role).order_by(Role.code)).scalars()
        if matches_search(search, role.code, role.description)
    ]
    result = paginate(rows, page, per_page)
    result['filters'] = {'search': search or ''}
    return result


def _get(role_id):
    role = db.get(Role, parse_uuid(role_id))
    if role is None:
        raise not_found('role_not_found', 'Role not found.')
    return role


def get_role(role_id):
    role = _get(role_id)
    institutions = {row.id: row for row in db.execute(select(Institution)).scalars()}
    return _serialize(role, institutions, _counts())


def create_role(actor, payload):
    code = text(payload, 'code', required=True, max_length=64, label='Role code').lower()
    if not CODE_RE.match(code):
        raise bad_request('invalid_role_code',
                          'Role code may only contain lowercase letters, numbers and underscores.', 'code')
    description = text(payload, 'description', required=True, max_length=255, label='Description')
    role = Role(code=code, description=description, institution_id=actor.institution_id)
    db.add(role)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict('role_code_exists', 'A role with this code already exists for the institution.', 'code')
    audit.record(f'CREATE_{ENTITY}', RESOURCE, role.id)
    db.commit()
    return get_role(role.id)


def update_role(actor, role_id, payload):
    role = _get(role_id)
    description = text(payload, 'description', required=True, max_length=255, label='Description')
    if description != role.description:
        role.description = description
        audit.record(f'UPDATE_{ENTITY}', RESOURCE, role.id)
        db.commit()
    return get_role(role.id)


def delete_role(actor, role_id):
    role = _get(role_id)
    if role.code in SYSTEM_ROLES:
        raise bad_request('system_role', 'System roles cannot be deleted.')
    if _counts().get(role.id, 0):
        raise bad_request('role_in_use', 'Remove this role from every user before deleting it.')
    audit.record(f'DELETE_{ENTITY}', RESOURCE, role.id)
    db.delete(role)
    db.commit()
    return {'deleted': True, 'id': str(role.id)}
