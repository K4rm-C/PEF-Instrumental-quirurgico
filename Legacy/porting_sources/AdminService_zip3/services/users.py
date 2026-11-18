import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

import audit
from database import db
from errors import bad_request, conflict, not_found
from models import Institution, Role, User, UserRole
from security import ADMIN
from services.common import (
    iso, matches_search, matches_status, optional_uuid, paginate, parse_uuid, status_filter,
    status_flag, text, utc_now,
)


ENTITY = 'USER'
RESOURCE = 'user'
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
MIN_PASSWORD = 8


def _roles_by_user():
    rows = db.execute(
        select(UserRole.user_id, Role.code, Role.description).join(Role, Role.id == UserRole.role_id)
        .order_by(Role.code)
    ).all()
    result = {}
    for user_id, code, description in rows:
        result.setdefault(user_id, []).append({'code': code, 'description': description})
    return result


def _serialize(user, institutions, roles):
    institution = institutions.get(user.institution_id)
    return {
        'id': str(user.id),
        'name': user.name,
        'email': user.email,
        'active': user.active,
        'institution': {'id': str(institution.id), 'name': institution.name} if institution else None,
        'roles': roles,
        'last_login_at': iso(user.last_login_at),
        'password_updated_at': iso(user.password_updated_at),
        'created_at': iso(user.created_at),
    }


def list_users(*, search='', role='', institution='', status='active', page=1, per_page=20):
    institutions = {row.id: row for row in db.execute(select(Institution)).scalars()}
    roles = _roles_by_user()
    wanted_status = status_filter(status)
    institution_id = optional_uuid(institution)
    rows = []
    for user in db.execute(select(User).order_by(User.name)).scalars():
        user_roles = roles.get(user.id, [])
        if role and role not in {item['code'] for item in user_roles}:
            continue
        if institution_id and user.institution_id != institution_id:
            continue
        if not matches_status(user.active, wanted_status):
            continue
        if not matches_search(search, user.name, user.email):
            continue
        rows.append(_serialize(user, institutions, user_roles))
    result = paginate(rows, page, per_page)
    result['filters'] = {'search': search or '', 'role': role or '', 'institution': institution or '',
                         'status': wanted_status}
    return result


def _get(user_id):
    user = db.get(User, parse_uuid(user_id))
    if user is None:
        raise not_found('user_not_found', 'User not found.')
    return user


def get_user(user_id):
    user = _get(user_id)
    institutions = {row.id: row for row in db.execute(select(Institution)).scalars()}
    return _serialize(user, institutions, _roles_by_user().get(user.id, []))


def _identity_values(payload):
    name = text(payload, 'name', required=True, max_length=160, label='Name')
    email = text(payload, 'email', required=True, max_length=255, label='Email').lower()
    if not EMAIL_RE.match(email):
        raise bad_request('invalid_email', 'Enter a valid email address.', 'email')
    institution_raw = text(payload, 'institution_id', required=True, label='Institution',
                           code='institution_required')
    institution = db.get(Institution, parse_uuid(
        institution_raw, 'invalid_institution', 'Choose a valid institution.', 'institution_id'))
    if institution is None:
        raise bad_request('invalid_institution', 'Choose a valid institution.', 'institution_id')
    role_codes = list(dict.fromkeys(str(code).strip() for code in (payload.get('role_codes') or []) if code))
    if not role_codes:
        raise bad_request('roles_required', 'Assign at least one role.', 'role_codes')
    roles = db.execute(
        select(Role).where(Role.institution_id == institution.id, Role.code.in_(role_codes))
    ).scalars().all()
    if {role.code for role in roles} != set(role_codes):
        raise bad_request('invalid_roles', 'Choose a valid institution and at least one role assigned to it.',
                          'role_codes')
    return name, email, institution, roles


def _password(payload, field='password', confirm_field='confirm_password'):
    password = str(payload.get(field) or '')
    if len(password) < MIN_PASSWORD:
        raise bad_request('password_too_short', 'Password must contain at least 8 characters.', field)
    if confirm_field in payload and str(payload.get(confirm_field) or '') != password:
        raise bad_request('password_mismatch', 'Passwords do not match.', confirm_field)
    return password


def _active_admins_excluding(user_id):
    return db.scalar(
        select(func.count(func.distinct(User.id)))
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.code == ADMIN, User.active.is_(True), User.id != user_id)
    ) or 0


def _guard_admin_access(actor, user, *, will_be_active, keeps_admin_role):
    if user.id == actor.id and not will_be_active:
        raise bad_request('cannot_deactivate_self', 'You cannot deactivate your own account.', 'status')
    if user.id == actor.id and not keeps_admin_role:
        raise bad_request('cannot_remove_own_admin', 'You cannot remove the administrator role from your own account.',
                          'role_codes')
    currently_admin = db.scalar(
        select(func.count()).select_from(UserRole).join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id == user.id, Role.code == ADMIN)
    )
    if currently_admin and user.active and (not will_be_active or not keeps_admin_role):
        if _active_admins_excluding(user.id) == 0:
            raise bad_request('last_admin', 'At least one active administrator must remain.', 'status')


def create_user(actor, payload):
    name, email, institution, roles = _identity_values(payload)
    password = _password(payload)
    now = utc_now()
    user = User(
        name=name,
        email=email,
        password_hash=generate_password_hash(password),
        password_updated_at=now,
        institution_id=institution.id,
        active=status_flag(payload),
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict('email_exists', 'A user with this email already exists.', 'email')
    for role in roles:
        db.add(UserRole(user_id=user.id, role_id=role.id, assigned_at=now))
    audit.record(f'CREATE_{ENTITY}', RESOURCE, user.id)
    db.commit()
    return get_user(user.id)


def update_user(actor, user_id, payload):
    user = _get(user_id)
    name, email, institution, roles = _identity_values(payload)
    new_active = status_flag(payload)
    _guard_admin_access(actor, user, will_be_active=new_active,
                        keeps_admin_role=ADMIN in {role.code for role in roles})

    was_active = user.active
    changed = (user.name, user.email, user.institution_id) != (name, email, institution.id)
    now = utc_now()
    user.name, user.email, user.institution_id = name, email, institution.id
    user.active = new_active
    user.updated_at = now

    assignments = db.execute(select(UserRole).where(UserRole.user_id == user.id)).scalars().all()
    wanted = {role.id: role for role in roles}
    roles_changed = False
    for assignment in assignments:
        if assignment.role_id in wanted:
            wanted.pop(assignment.role_id)
        else:
            db.delete(assignment)
            roles_changed = True
    for role in wanted.values():
        db.add(UserRole(user_id=user.id, role_id=role.id, assigned_at=now))
        roles_changed = True
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict('email_exists', 'A user with this email already exists.', 'email')
    audit.record_update(ENTITY, RESOURCE, user.id, was_active=was_active, is_active=new_active,
                        changed=changed)
    if roles_changed:
        audit.record('CHANGE_USER_ROLES', RESOURCE, user.id)
    db.commit()
    return get_user(user.id)


def set_status(actor, user_id, active):
    user = _get(user_id)
    # A status-only change never touches roles.
    _guard_admin_access(actor, user, will_be_active=active, keeps_admin_role=True)
    if user.active == active:
        return get_user(user.id)
    user.active = active
    user.updated_at = utc_now()
    audit.record(f'{"ACTIVATE" if active else "DEACTIVATE"}_{ENTITY}', RESOURCE, user.id)
    db.commit()
    return get_user(user.id)


def set_password(actor, user_id, payload):
    user = _get(user_id)
    password = _password(payload, 'new_password', 'confirm_new_password')
    now = utc_now()
    user.password_hash = generate_password_hash(password)
    user.password_updated_at = now
    user.updated_at = now
    audit.record('RESET_USER_PASSWORD', RESOURCE, user.id)
    db.commit()
    return get_user(user.id)

