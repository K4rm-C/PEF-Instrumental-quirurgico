"""RF-AD-05 Users, in-process on the identity tables AuthService reads (ADMIN-01.2).

Rules ported from BackendAuthService/identity_admin.py (same codes, messages and audit actions):
email unique (case-insensitive), roles of the user's institution, password policy, "at least one
active it_admin per institution" under the same transaction-level advisory lock AuthService uses,
no self-deactivation / self-demotion, werkzeug password hashes (AuthService verifies them).

Scope (D-2): an administrator lists, reads and changes only users of their own institution; a user
of another institution answers 404 and a different institution in the payload is rejected. Users
are never moved between institutions here.

Sessions (D-1): after a deactivation or password reset is committed, the user's Redis sessions are
revoked through AuthService (auth_sessions). The result reports `session_revocation` so the screen
can warn if AuthService could not do it (the account is still closed by PostgreSQL on deactivation).
Floor supervisors (or_charge) get operating rooms (user_operating_room) of the same institution.
"""
import re

from flask import current_app
from sqlalchemy import func, select, text as sql
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from . import audit, auth_sessions, settings
from .common import iso, matches_search, matches_status, paginate, parse_uuid, status_filter, status_flag, text, utc_now
from .database import db
from .errors import bad_request, conflict, not_found
from .models import Institution, OperatingRoom, Role, User, UserOperatingRoom, UserRole

ADMIN = 'it_admin'
FLOOR_SUPERVISOR = 'or_charge'
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
ADMIN_LOCK_KEY = 'pef:identity:administrators'  # same key as AuthService: serializes both writers


# --- Reads ----------------------------------------------------------------------------------

def _roles_by_user(user_ids):
    rows = db.execute(
        select(UserRole.user_id, Role.code, Role.description).join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id.in_(user_ids)).order_by(Role.code)
    ).all() if user_ids else []
    result = {}
    for user_id, code, description in rows:
        result.setdefault(user_id, []).append({'code': code, 'description': description})
    return result


def _rooms_by_user(user_ids):
    rows = db.execute(
        select(UserOperatingRoom.user_id, OperatingRoom).join(OperatingRoom, OperatingRoom.id == UserOperatingRoom.room_id)
        .where(UserOperatingRoom.user_id.in_(user_ids)).order_by(OperatingRoom.code)
    ).all() if user_ids else []
    result = {}
    for user_id, room in rows:
        result.setdefault(user_id, []).append({'id': str(room.id), 'code': room.code, 'name': room.name})
    return result


def _document(user, institution, roles, rooms):
    return {
        'id': str(user.id), 'name': user.name, 'email': user.email, 'active': user.active,
        'institution': {'id': str(institution.id), 'name': institution.name},
        'roles': roles, 'operating_rooms': rooms,
        'last_login_at': iso(user.last_login_at), 'password_updated_at': iso(user.password_updated_at),
        'created_at': iso(user.created_at), 'updated_at': iso(user.updated_at),
    }


def list_users(actor, *, search='', role='', status='active', page=1, per_page=20):
    institution = db.get(Institution, actor.institution_id)
    users = db.execute(select(User).where(User.institution_id == actor.institution_id)
                       .order_by(User.name, User.email)).scalars().all()
    roles = _roles_by_user([user.id for user in users])
    rooms = _rooms_by_user([user.id for user in users])
    wanted = status_filter(status)
    rows = []
    for user in users:
        user_roles = roles.get(user.id, [])
        if role and role not in {item['code'] for item in user_roles}:
            continue
        if not matches_status(user.active, wanted) or not matches_search(search, user.name, user.email):
            continue
        rows.append(_document(user, institution, user_roles, rooms.get(user.id, [])))
    result = paginate(rows, page, per_page)
    result['filters'] = {'search': search or '', 'role': role or '', 'status': wanted}
    return result


def _owned(actor, user_id, *, for_update=False):
    query = select(User).where(User.id == parse_uuid(user_id, 'user_not_found', 'User not found.'))
    user = db.execute(query.with_for_update() if for_update else query).scalar_one_or_none()
    if user is None or user.institution_id != actor.institution_id:
        raise not_found('user_not_found', 'User not found.')
    return user


def _detail(user):
    return _document(user, db.get(Institution, user.institution_id), _roles_by_user([user.id]).get(user.id, []),
                     _rooms_by_user([user.id]).get(user.id, []))


def get_user(actor, user_id):
    return _detail(_owned(actor, user_id))


# --- Validation (same order and codes as AuthService identity_admin) -------------------------

def _identity_values(actor, payload):
    name = text(payload, 'name', required=True, max_length=160, label='Name')
    email = text(payload, 'email', required=True, max_length=255, label='Email').lower()
    if not EMAIL_RE.match(email):
        raise bad_request('invalid_email', 'Enter a valid email address.', 'email')
    institution = str(payload.get('institution_id') or '').strip()
    if institution and institution != str(actor.institution_id):
        # D-2: users are created and kept in the administrator's own institution.
        raise bad_request('foreign_institution', 'Users can only belong to your institution.', 'institution_id')
    codes = list(dict.fromkeys(str(code).strip() for code in (payload.get('role_codes') or []) if str(code).strip()))
    if not codes:
        raise bad_request('roles_required', 'Assign at least one role.', 'role_codes')
    roles = db.execute(select(Role).where(Role.institution_id == actor.institution_id, Role.code.in_(codes))).scalars().all()
    if {role.code for role in roles} != set(codes):
        raise bad_request('invalid_roles', "Choose roles that belong to the user's institution.", 'role_codes')
    return name, email, roles


def _room_ids(actor, payload, role_codes):
    raw = [str(value).strip() for value in (payload.get('room_ids') or []) if str(value).strip()]
    if not raw:
        return []
    if FLOOR_SUPERVISOR not in role_codes:
        raise bad_request('rooms_need_floor_supervisor', 'Only floor supervisors (or_charge) are assigned to operating rooms.',
                          'room_ids')
    ids = list(dict.fromkeys(parse_uuid(value, 'invalid_room', 'Choose a valid operating room.', 'room_ids') for value in raw))
    found = set(db.execute(select(OperatingRoom.id).where(OperatingRoom.id.in_(ids),
                                                          OperatingRoom.institution_id == actor.institution_id)).scalars())
    if found != set(ids):
        raise bad_request('invalid_room', 'Choose a valid operating room.', 'room_ids')
    return ids


def _password(institution_id, payload, field, confirm_field):
    password = str(payload.get(field) or '')
    minimum = settings.value(institution_id, 'password_min_length')
    if len(password) < minimum:
        raise bad_request('password_too_short', f'Password must contain at least {minimum} characters.', field)
    if confirm_field in payload and str(payload.get(confirm_field) or '') != password:
        raise bad_request('password_mismatch', 'Passwords do not match.', confirm_field)
    return password


def _email_taken(email, except_id=None):
    query = select(User.id).where(func.lower(User.email) == email)
    if except_id is not None:
        query = query.where(User.id != except_id)
    return db.execute(query).first() is not None


def _lock_administrators():
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(sql('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': ADMIN_LOCK_KEY})


def _is_effective_admin(user_id):
    return db.execute(sql(
        'SELECT 1 FROM "user" u JOIN user_role ur ON ur.user_id = u.id '
        'JOIN role r ON r.id = ur.role_id AND r.institution_id = u.institution_id '
        'WHERE u.id = :id AND u.active AND r.code = :admin'), {'id': user_id, 'admin': ADMIN}).first() is not None


def _other_active_admins(institution_id, user_id):
    return db.execute(sql(
        'SELECT count(DISTINCT u.id) FROM "user" u JOIN user_role ur ON ur.user_id = u.id '
        'JOIN role r ON r.id = ur.role_id AND r.institution_id = u.institution_id '
        'WHERE u.institution_id = :inst AND u.active AND r.code = :admin AND u.id <> :id'),
        {'inst': institution_id, 'admin': ADMIN, 'id': user_id}).scalar_one()


def _guard_administrators(actor, user, *, will_be_active, keeps_admin):
    if user.id == actor.id and not will_be_active:
        raise conflict('cannot_deactivate_self', 'You cannot deactivate your own account.', 'status')
    if user.id == actor.id and not keeps_admin:
        raise conflict('cannot_remove_own_admin', 'You cannot remove the administrator role from your own account.',
                       'role_codes')
    if will_be_active and keeps_admin:
        return
    # One administrator change at a time: two concurrent requests cannot remove the last two together.
    _lock_administrators()
    if _is_effective_admin(user.id) and _other_active_admins(user.institution_id, user.id) == 0:
        raise conflict('last_admin', 'At least one active administrator must remain.', 'status')


def _write_roles(user, roles, now):
    current = {row.role_id: row for row in db.execute(select(UserRole).where(UserRole.user_id == user.id)).scalars()}
    wanted = {role.id for role in roles}
    for role_id, row in current.items():
        if role_id not in wanted:
            db.delete(row)
    for role_id in wanted - set(current):
        db.add(UserRole(user_id=user.id, role_id=role_id, assigned_at=now))
    return set(current) != wanted


def _write_rooms(user, room_ids, now):
    current = {row.room_id: row for row in db.execute(
        select(UserOperatingRoom).where(UserOperatingRoom.user_id == user.id)).scalars()}
    wanted = set(room_ids)
    for room_id, row in current.items():
        if room_id not in wanted:
            db.delete(row)
    for room_id in wanted - set(current):
        db.add(UserOperatingRoom(user_id=user.id, room_id=room_id, assigned_at=now))
    return set(current) != wanted


def _revoke(user_id, result):
    """After commit: end the user's sessions through AuthService; report the outcome to the screen."""
    try:
        auth_sessions.revoke_user_sessions(user_id)
        result['session_revocation'] = 'revoked'
    except auth_sessions.RevocationFailed as exc:
        current_app.logger.warning('Session revocation failed for user %s: %s', user_id, exc)
        result['session_revocation'] = 'failed'
    return result


# --- Writes -----------------------------------------------------------------------------------

def create_user(actor, payload):
    name, email, roles = _identity_values(actor, payload)
    password = _password(actor.institution_id, payload, 'password', 'confirm_password')
    room_ids = _room_ids(actor, payload, {role.code for role in roles})
    if _email_taken(email):
        raise conflict('email_exists', 'A user with this email already exists.', 'email')
    now = utc_now()
    user = User(name=name, email=email, password_hash=generate_password_hash(password), password_updated_at=now,
                institution_id=actor.institution_id, active=status_flag(payload), created_at=now, updated_at=now)
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict('email_exists', 'A user with this email already exists.', 'email')
    _write_roles(user, roles, now)
    _write_rooms(user, room_ids, now)
    audit.record('CREATE_USER', 'user', user.id)
    if room_ids:
        audit.record('CHANGE_USER_ROOMS', 'user', user.id)
    db.commit()
    return _detail(user)


def update_user(actor, user_id, payload):
    user = _owned(actor, user_id, for_update=True)
    name, email, roles = _identity_values(actor, payload)
    codes = {role.code for role in roles}
    room_ids = _room_ids(actor, payload, codes)
    active = user.active if payload.get('status') is None and payload.get('active') is None else status_flag(payload)
    _guard_administrators(actor, user, will_be_active=active, keeps_admin=ADMIN in codes)
    if _email_taken(email, except_id=user.id):
        raise conflict('email_exists', 'A user with this email already exists.', 'email')
    was_active = user.active
    changed = (user.name, user.email) != (name, email)
    now = utc_now()
    user.name, user.email, user.active, user.updated_at = name, email, active, now
    roles_changed = _write_roles(user, roles, now)
    rooms_changed = _write_rooms(user, room_ids if FLOOR_SUPERVISOR in codes else [], now)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict('email_exists', 'A user with this email already exists.', 'email')
    if changed:
        audit.record('UPDATE_USER', 'user', user.id)
    if was_active != active:
        audit.record(f"{'ACTIVATE' if active else 'DEACTIVATE'}_USER", 'user', user.id)
    if roles_changed:
        audit.record('CHANGE_USER_ROLES', 'user', user.id)
    if rooms_changed:
        audit.record('CHANGE_USER_ROOMS', 'user', user.id)
    db.commit()
    result = _detail(user)
    return _revoke(user.id, result) if was_active and not active else result


def set_status(actor, user_id, active):
    user = _owned(actor, user_id, for_update=True)
    _guard_administrators(actor, user, will_be_active=active, keeps_admin=True)  # a status change never touches roles
    result = None
    if user.active != active:
        user.active, user.updated_at = active, utc_now()
        audit.record(f"{'ACTIVATE' if active else 'DEACTIVATE'}_USER", 'user', user.id)
        db.commit()
        result = _detail(user)
        if not active:
            return _revoke(user.id, result)
    return result or _detail(user)


def set_password(actor, user_id, payload):
    """Administrator reset: new hash, then every session of that user ends (AuthService)."""
    user = _owned(actor, user_id, for_update=True)
    password = _password(actor.institution_id, payload, 'new_password', 'confirm_new_password')
    now = utc_now()
    user.password_hash, user.password_updated_at, user.updated_at = generate_password_hash(password), now, now
    audit.record('RESET_USER_PASSWORD', 'user', user.id)
    db.commit()
    return _revoke(user.id, _detail(user))
