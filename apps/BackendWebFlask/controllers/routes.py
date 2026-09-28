from datetime import date
from functools import wraps
from uuid import UUID

import requests
from flask import Blueprint, abort, current_app, g, redirect, render_template, request, session, url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.urls import urlsplit
from werkzeug.security import generate_password_hash

from extensions import db
from models.Institution import Institution
from models.Role import Role
from models.User import User
from models.UserRole import UserRole

from controllers.view_data import (
    admin_dashboard_overview,
    dashboard_data,
    discrepancies_data,
    discrepancies_summary_data,
    family_form_data,
    families_data,
    institution_form_data,
    instrument_form_data,
    instruments_data,
    kit_form_data,
    kits_data,
    operating_room_form_data,
    operating_rooms_data,
    procedure_form_data,
    procedures_data,
    role_form_data,
    roles_data,
    sessions_data,
    session_discrepancies,
    station_form_data,
    stations_data,
    user_form_data,
    users_data,
    vision_model_form_data,
    vision_models_data,
)


web_bp = Blueprint('web', __name__)

ROLE_LABELS = {
    'operator_cde': 'Operator',
    'supervisor_quality': 'Supervisor',
    'it_admin': 'Administrator',
}

EMPTY_STATS = {
    'total': 0,
    'active': 0,
    'pending': 0,
    'completed': 0,
}


def _context(**values):
    current_user = _current_user()
    context = {
        'current_locale': 'en',
        'current_user': current_user,
        'nav_urls': _nav_urls(current_user),
        'dashboard_stats': EMPTY_STATS,
        'sessions_by_day': [],
        'session_status_breakdown': [],
        'recent_sessions': [],
        'sessions': [],
        'discrepancies': [],
        'discrepancies_summary': {
            'pending_reviews': 0,
            'open_discrepancies': 0,
            'reviewed_today': 0,
        },
        'audit_events': [],
        'users': [],
        'roles': [],
        'instruments': [],
        'instrument_families': [],
        'kits': [],
        'capture_stations': [],
        'reports': [],
        'indicators': [],
        'families_shown_count': 0,
        'families_total_count': 0,
        'instruments_shown_count': 0,
        'instruments_total_count': 0,
        'kits_shown_count': 0,
        'kits_total_count': 0,
        'users_shown_count': 0,
        'users_total_count': 0,
        'sessions_shown_count': 0,
        'sessions_total_count': 0,
        'page': 1,
        'total_pages': 1,
        # None (not a placeholder dict) so every page's own `session|default({...}, true)`
        # reliably falls back to that page's real demo data — a plain dict here would be
        # "defined" from Jinja's point of view and silently defeat every page's fallback.
        'session': None,
    }
    context.update(values)
    return context


def _page(template, **values):
    return render_template(template, **_context(**values))


def _current_user():
    if hasattr(g, 'verified_user'):
        return g.verified_user

    access_token = request.cookies.get('access_token')
    if not access_token:
        g.verified_user = None
        return None

    response = _auth_request('get', '/verify', token=access_token)
    if response is not None and response.status_code == 401:
        refresh_token = request.cookies.get('refresh_token')
        if refresh_token:
            refreshed = _auth_request('post', '/refresh', token=refresh_token)
            if refreshed is not None and refreshed.ok:
                g.auth_set_cookies = _upstream_set_cookies(refreshed)
                access_token = refreshed.cookies.get('access_token')
                if access_token:
                    response = _auth_request('get', '/verify', token=access_token)

    if response is None or not response.ok:
        session.clear()
        g.verified_user = None
        return None

    payload = response.json()
    user_data = payload.get('user', {})
    roles = user_data.get('roles', [])
    role = next((item for item in roles if item.get('code') in ROLE_LABELS), None)
    if role is None:
        g.verified_user = None
        return None

    g.verified_user = {
        'id': user_data.get('id'),
        'name': user_data.get('name', ''),
        'email': user_data.get('email', ''),
        'institution_id': user_data.get('institution_id'),
        'roles': roles,
        'role_code': role['code'],
        'role_label': ROLE_LABELS[role['code']],
        'profile_role_label': role.get('description') or ROLE_LABELS[role['code']],
        'avatar_url': None,
    }
    return g.verified_user


def _auth_request(method, path, token=None, **kwargs):
    headers = kwargs.pop('headers', {})
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        return requests.request(
            method,
            f"{current_app.config['AUTH_SERVICE_URL']}{path}",
            headers=headers,
            timeout=current_app.config['AUTH_SERVICE_TIMEOUT'],
            **kwargs,
        )
    except requests.RequestException:
        return None


def _upstream_set_cookies(upstream_response):
    headers = getattr(getattr(upstream_response, 'raw', None), 'headers', None)
    if headers is None:
        return []
    return headers.getlist('Set-Cookie')


def _forward_set_cookies(response, upstream_response):
    if upstream_response is not None:
        for set_cookie in _upstream_set_cookies(upstream_response):
            response.headers.add('Set-Cookie', set_cookie)
    return response


def require_role(*role_codes):
    """Authorize using the role returned by the auth service token verification."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = _current_user()
            if user is None:
                return redirect(url_for('web.sign_in', next=request.path))
            if user['role_code'] not in role_codes:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def _role_home(role_code):
    return {
        'operator_cde': 'web.operator_dashboard',
        'supervisor_quality': 'web.supervisor_dashboard',
        'it_admin': 'web.admin_dashboard',
    }[role_code]


def _safe_next_url(value, user):
    if not value:
        return url_for(_role_home(user['role_code']))
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith('/'):
        return url_for(_role_home(user['role_code']))
    allowed_prefixes = {
        'operator_cde': '/operator/',
        'supervisor_quality': '/supervisor/',
        'it_admin': '/admin/',
    }
    if parsed.path.startswith(allowed_prefixes[user['role_code']]):
        return value
    return url_for(_role_home(user['role_code']))


def _nav_urls(user):
    if not user:
        return {'sign_in': url_for('web.sign_in')}

    profile_endpoints = {
        'operator_cde': 'web.operator_profile',
        'supervisor_quality': 'web.supervisor_profile',
        'it_admin': 'web.admin_profile',
    }
    common = {
        'profile': url_for(profile_endpoints[user['role_code']]),
        'sign_out': url_for('web.sign_out'),
    }
    if user['role_code'] == 'operator_cde':
        return {
            **common,
            'dashboard': url_for('web.operator_dashboard'),
            'counting_sessions': url_for('web.operator_sessions'),
            'new_session': url_for('web.operator_session_new'),
            'session_history': url_for('web.operator_session_history'),
        }
    if user['role_code'] == 'supervisor_quality':
        return {
            **common,
            'dashboard': url_for('web.supervisor_dashboard'),
            'sessions': url_for('web.supervisor_sessions'),
            'discrepancies': url_for('web.supervisor_discrepancies'),
            'session_history': url_for('web.supervisor_session_history'),
            'reports': url_for('web.supervisor_reports'),
            'indicators': url_for('web.supervisor_indicators'),
            'audit_log': url_for('web.supervisor_audit_log'),
        }
    return {
        **common,
        'dashboard': url_for('web.admin_dashboard'),
        'instrument_families': url_for('web.admin_instrument_families'),
        'instruments': url_for('web.admin_instruments'),
        'kits': url_for('web.admin_kits'),
        'procedures': url_for('web.admin_procedures'),
        'users': url_for('web.admin_users'),
        'roles': url_for('web.admin_roles'),
        'vision_models': url_for('web.admin_vision_models'),
        'audit_log': url_for('web.admin_audit_log'),
        'configuration': url_for('web.admin_configuration'),
    }


@web_bp.context_processor
def template_helpers():
    def translate(value, **variables):
        return value % variables if variables else value

    return {'_': translate}


@web_bp.route('/')
def index():
    return _page('shared/landing.html', current_year=date.today().year)


@web_bp.route('/sign-in', methods=['GET', 'POST'])
def sign_in():
    redirect_target = request.form.get('next') or request.args.get('next')
    if request.method == 'POST':
        email = request.form.get('institutional_email', '').strip().lower()
        password = request.form.get('password', '')
        auth_response = _auth_request(
            'post',
            '/login',
            json={'email': email, 'password': password},
        )
        if auth_response is None:
            return _page('auth/sign_in.html', sign_in_error='Authentication service unavailable.'), 503
        if not auth_response.ok:
            return _page('auth/sign_in.html', sign_in_error='Invalid email or password.'), 401

        user_data = auth_response.json().get('user', {})
        roles = user_data.get('roles', [])
        role = next((item for item in roles if item.get('code') in ROLE_LABELS), None)
        if role is None:
            return _page('auth/sign_in.html', sign_in_error='Your account has no application role.'), 403

        response = redirect(_safe_next_url(redirect_target, {'role_code': role['code']}), code=303)
        return _forward_set_cookies(response, auth_response)
    return _page(
        'auth/sign_in.html',
        sign_in_error=None,
        redirect_target=redirect_target,
    )


@web_bp.route('/sign-out', methods=['POST', 'GET'])
def sign_out():
    access_token = request.cookies.get('access_token')
    auth_response = None
    if access_token:
        auth_response = _auth_request('post', '/logout', token=access_token)
    session.clear()
    response = redirect(url_for('web.sign_in'))
    return _forward_set_cookies(response, auth_response)


@web_bp.route('/operator/profile')
@require_role('operator_cde')
def operator_profile():
    return _page('shared/profile.html', profile_role='operator')


@web_bp.route('/operator/dashboard')
@require_role('operator_cde')
def operator_dashboard():
    stats, sessions, _ = dashboard_data('operator')
    return _page('operator/dashboard.html', dashboard_stats=stats, recent_sessions=sessions[:5])


@web_bp.route('/operator/sessions')
@require_role('operator_cde')
def operator_sessions():
    sessions = sessions_data()
    return _page(
        'operator/sessions/list.html',
        sessions=sessions,
        sessions_shown_count=len(sessions),
        sessions_total_count=len(sessions),
    )


@web_bp.route('/operator/sessions/new', methods=['GET', 'POST'])
@require_role('operator_cde')
def operator_session_new():
    if request.method == 'POST':
        # Integration point: no WorkSession is created yet (see V2_IMPLEMENTATION_NOTES.md).
        # Redirects straight into the approved WS-026 reference-case Capture screen so the
        # rest of the V2 Operator workflow can be clicked through end to end.
        return redirect(url_for('web.operator_session_capture', session_id='WS-026'))
    return _page('operator/sessions/new.html')


@web_bp.route('/operator/sessions/history')
@require_role('operator_cde')
def operator_session_history():
    sessions = sessions_data()
    return _page(
        'operator/sessions/history.html',
        session_history=sessions,
        sessions_shown_count=len(sessions),
        sessions_total_count=len(sessions),
    )


# ----------------------------------------------------------------------------------------
# Operator V2 counting-session workflow (prompts/09_Operator_V2.md).
#
# Each stage below is a distinct, session-id-scoped page per the approved V2 references.
# None of these query WorkSession/CountEvent/Discrepancy/HumanCorrection/AccessAudit yet:
# the detailed per-instrument AI/validation/discrepancy/audit data those references show
# (confidence scores, correction reasons, supervisor evidence, etc.) has no backing field on
# any existing model, and inventing one would violate this prompt's "do not invent database
# fields" constraint. Every route below renders its template with only the session_id it was
# given; the template itself supplies the approved WS-026 reference-case data as its
# |default(...) fallback (same convention already used by every other page in this app), so
# the full click-through workflow is reviewable today and swappable for real queries later
# without any template changes. See V2_IMPLEMENTATION_NOTES.md for what remains pending.
# ----------------------------------------------------------------------------------------

@web_bp.route('/operator/sessions/<session_id>/capture')
@require_role('operator_cde')
def operator_session_capture(session_id):
    return _page('operator/sessions/capture.html', session_id=session_id)


@web_bp.route('/operator/sessions/<session_id>/ai-detection')
@require_role('operator_cde')
def operator_session_ai_detection(session_id):
    return _page('operator/sessions/ai_detection.html', session_id=session_id)


@web_bp.route('/operator/sessions/<session_id>/validation')
@require_role('operator_cde')
def operator_session_validation(session_id):
    return _page('operator/sessions/validation.html', session_id=session_id)


@web_bp.route('/operator/sessions/<session_id>/validation-summary')
@require_role('operator_cde')
def operator_session_validation_summary(session_id):
    return _page('operator/sessions/validation_summary.html', session_id=session_id)


@web_bp.route('/operator/sessions/<session_id>/discrepancy')
@require_role('operator_cde')
def operator_session_discrepancy(session_id):
    return _page('operator/sessions/discrepancy.html', session_id=session_id)


@web_bp.route('/operator/sessions/<session_id>/awaiting-review')
@require_role('operator_cde')
def operator_session_awaiting_review(session_id):
    return _page('operator/sessions/awaiting_review.html', session_id=session_id)


@web_bp.route('/operator/sessions/<session_id>/correction')
@require_role('operator_cde')
def operator_session_correction(session_id):
    return _page('operator/sessions/correction_requested.html', session_id=session_id)


@web_bp.route('/operator/sessions/<session_id>/ready-to-close')
@require_role('operator_cde')
def operator_session_ready_to_close(session_id):
    return _page('operator/sessions/ready_to_close.html', session_id=session_id)


@web_bp.route('/operator/sessions/closed/<session_id>')
@require_role('operator_cde')
def operator_session_closed(session_id):
    session_data = next((item for item in sessions_data() if item['id'] == session_id), None)
    if session_data is None:
        return _page('operator/sessions/closed_details.html', session_id=session_id)
    discrepancies = session_discrepancies(session_id)
    closure_summary = {
        'variant': 'warning',
        'message': f"Closed with {len(discrepancies)} documented discrepancy(ies).",
    } if discrepancies else {
        'variant': 'success',
        'message': 'All Instruments Accounted For',
    }
    return _page(
        'operator/sessions/closed_details.html',
        session=session_data,
        session_id=session_id,
        closure_summary=closure_summary,
    )


@web_bp.route('/supervisor/profile')
@require_role('supervisor_quality')
def supervisor_profile():
    return _page('shared/profile.html', profile_role='supervisor')


@web_bp.route('/supervisor/dashboard')
@require_role('supervisor_quality')
def supervisor_dashboard():
    stats, sessions, discrepancies = dashboard_data('supervisor')
    return _page(
        'supervisor/dashboard.html',
        dashboard_stats=stats,
        sessions_requiring_attention=discrepancies,
        recent_sessions=sessions[:5],
    )


@web_bp.route('/supervisor/sessions')
@require_role('supervisor_quality')
def supervisor_sessions():
    sessions = sessions_data()
    return _page(
        'supervisor/sessions/list.html',
        sessions=sessions,
        sessions_shown_count=len(sessions),
        sessions_total_count=len(sessions),
    )


@web_bp.route('/supervisor/sessions/history')
@require_role('supervisor_quality')
def supervisor_session_history():
    sessions = sessions_data()
    return _page(
        'supervisor/sessions/history.html',
        session_history=sessions,
        sessions_shown_count=len(sessions),
        sessions_total_count=len(sessions),
    )


@web_bp.route('/supervisor/sessions/<session_id>')
@require_role('supervisor_quality')
def supervisor_session_details(session_id):
    session_data = next((item for item in sessions_data() if item['id'] == session_id), None)
    return _page('supervisor/sessions/details.html', session=session_data, session_id=session_id)


@web_bp.route('/supervisor/discrepancies')
@require_role('supervisor_quality')
def supervisor_discrepancies():
    discrepancies = discrepancies_data()
    return _page(
        'supervisor/discrepancies/list.html',
        discrepancies=discrepancies,
        discrepancies_summary=discrepancies_summary_data(discrepancies),
    )


@web_bp.route('/supervisor/discrepancies/<session_id>/review')
@require_role('supervisor_quality')
def supervisor_discrepancy_review(session_id):
    session_data = next((item for item in sessions_data() if item['id'] == session_id), None)
    return _page('supervisor/discrepancies/review.html', session=session_data, session_id=session_id)


@web_bp.route('/supervisor/reports')
@require_role('supervisor_quality')
def supervisor_reports():
    return _page('supervisor/reports.html')


@web_bp.route('/supervisor/indicators')
@require_role('supervisor_quality')
def supervisor_indicators():
    # indicator_stats/sessions_by_day/discrepancies_by_* have no backing analytics query yet
    # (no AI-vs-human agreement, resolution-time, or per-instrument/family/type discrepancy
    # aggregation exists on any model) — the template supplies the approved WS-026-consistent
    # presentation fallback. See V2_IMPLEMENTATION_NOTES.md.
    return _page('supervisor/indicators.html')


@web_bp.route('/supervisor/audit-log')
@require_role('supervisor_quality')
def supervisor_audit_log():
    # AccessAudit has no query wired up yet (no code/entity/record/result shape to match the
    # approved V2 reference) — the template supplies the approved WS-026-consistent
    # presentation fallback. See V2_IMPLEMENTATION_NOTES.md.
    return _page('supervisor/audit_log.html')


@web_bp.route('/admin/profile')
@require_role('it_admin')
def admin_profile():
    return _page('shared/profile.html', profile_role='admin')


@web_bp.route('/admin/dashboard')
@require_role('it_admin')
def admin_dashboard():
    stats, _, _ = dashboard_data('admin')
    overview = admin_dashboard_overview()
    return _page(
        'admin/dashboard.html',
        dashboard_stats=stats,
        secondary_stats=overview['secondary_stats'],
        catalog_overview=overview['catalog_overview'],
        system_overview=overview['system_overview'],
        operational_metrics=overview['operational_metrics'],
    )


@web_bp.route('/admin/instrument-families')
@require_role('it_admin')
def admin_instrument_families():
    families = families_data()
    return _page('admin/instrument_families/list.html', instrument_families=families, families_shown_count=len(families), families_total_count=len(families))


@web_bp.route('/admin/instrument-families/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_instrument_family_new():
    return _page('admin/instrument_families/form.html', **family_form_data(), page_title='New Instrument Family', is_edit=False, form_mode='create')


@web_bp.route('/admin/instrument-families/<family_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_instrument_family_edit(family_id):
    return _page('admin/instrument_families/form.html', **family_form_data(family_id), page_title='Edit Instrument Family', is_edit=True, form_mode='edit', family_id=family_id)


@web_bp.route('/admin/instruments')
@require_role('it_admin')
def admin_instruments():
    instruments = instruments_data()
    return _page('admin/instruments/list.html', instruments=instruments, instruments_shown_count=len(instruments), instruments_total_count=len(instruments))


@web_bp.route('/admin/instruments/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_instrument_new():
    return _page('admin/instruments/form.html', **instrument_form_data(), page_title='New Instrument', form_mode='create')


@web_bp.route('/admin/instruments/<instrument_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_instrument_edit(instrument_id):
    return _page('admin/instruments/form.html', **instrument_form_data(instrument_id), page_title='Edit Instrument', form_mode='edit', instrument_id=instrument_id)


@web_bp.route('/admin/kits')
@require_role('it_admin')
def admin_kits():
    kits = kits_data()
    return _page('admin/kits/list.html', kits=kits, kits_shown_count=len(kits), kits_total_count=len(kits))


@web_bp.route('/admin/kits/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_kit_new():
    return _page('admin/kits/form.html', **kit_form_data(), page_title='New Kit', form_mode='create')


@web_bp.route('/admin/kits/<kit_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_kit_edit(kit_id):
    return _page('admin/kits/form.html', **kit_form_data(kit_id), page_title='Edit Kit', form_mode='edit', kit_id=kit_id)


@web_bp.route('/admin/procedures')
@require_role('it_admin')
def admin_procedures():
    procedures = procedures_data()
    return _page('admin/procedures/list.html', procedures=procedures, procedures_shown_count=len(procedures), procedures_total_count=len(procedures))


@web_bp.route('/admin/procedures/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_procedure_new():
    return _page('admin/procedures/form.html', **procedure_form_data(), page_title='New Procedure', form_mode='create')


@web_bp.route('/admin/procedures/<procedure_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_procedure_edit(procedure_id):
    return _page('admin/procedures/form.html', **procedure_form_data(procedure_id), page_title='Edit Procedure', form_mode='edit', procedure_id=procedure_id)


@web_bp.route('/admin/users')
@require_role('it_admin')
def admin_users():
    users = users_data()
    return _page('admin/users/list.html', users=users, users_shown_count=len(users), users_total_count=len(users))


@web_bp.route('/admin/users/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_user_new():
    if request.method == 'GET':
        return _render_user_form('create')

    values = _submitted_user_values()
    name = values['name']
    email = values['email']
    institution_id = values['institution']
    role_codes = values['assigned_role_codes']
    password = request.form.get('password', '')
    confirm_password = request.form.get('confirm_password', '')

    error = _validate_user_values(values)
    if not error and len(password) < 8:
        error = 'Password must contain at least 8 characters.'
    if not error and password != confirm_password:
        error = 'Passwords do not match.'
    if error:
        return _render_user_form('create', values=values, form_error=error, status=400)

    institution, roles = _validated_institution_and_roles(institution_id, role_codes)
    if institution is None or roles is None:
        return _render_user_form(
            'create', values=values,
            form_error='Choose a valid institution and at least one role assigned to it.', status=400,
        )

    try:
        user = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
            institution_id=institution.id,
            active=values['status'] == 'active',
        )
        db.session.add(user)
        for role in roles:
            db.session.add(UserRole(user=user, role=role))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _render_user_form('create', values=values, form_error='A user with this email already exists.', status=409)
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Failed to create admin user')
        return _render_user_form('create', values=values, form_error='Unable to save this user right now.', status=503)

    return redirect(url_for('web.admin_users'))


@web_bp.route('/admin/users/<user_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_user_edit(user_id):
    try:
        parsed_user_id = UUID(user_id)
    except ValueError:
        abort(404)

    user = db.session.get(User, parsed_user_id)
    if user is None:
        abort(404)
    if request.method == 'GET':
        return _render_user_form('edit', user_id=parsed_user_id)

    values = _submitted_user_values()
    error = _validate_user_values(values)
    if error:
        return _render_user_form('edit', user_id=parsed_user_id, values=values, form_error=error, status=400)

    institution, roles = _validated_institution_and_roles(values['institution'], values['assigned_role_codes'])
    if institution is None or roles is None:
        return _render_user_form(
            'edit', user_id=parsed_user_id, values=values,
            form_error='Choose a valid institution and at least one role assigned to it.', status=400,
        )

    try:
        user.name = values['name']
        user.email = values['email']
        user.institution_id = institution.id
        user.active = values['status'] == 'active'
        assignments = db.session.execute(
            select(UserRole).where(UserRole.user_id == user.id)
        ).scalars().all()
        roles_to_add = {role.id: role for role in roles}
        for assignment in assignments:
            if assignment.role_id in roles_to_add:
                roles_to_add.pop(assignment.role_id)
            else:
                db.session.delete(assignment)
        for role in roles_to_add.values():
            db.session.add(UserRole(user=user, role=role))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _render_user_form('edit', user_id=parsed_user_id, values=values, form_error='A user with this email already exists.', status=409)
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Failed to update admin user %s', parsed_user_id)
        return _render_user_form('edit', user_id=parsed_user_id, values=values, form_error='Unable to save this user right now.', status=503)

    return redirect(url_for('web.admin_users'))


def _submitted_user_values():
    return {
        'name': request.form.get('name', '').strip(),
        'email': request.form.get('email', '').strip().lower(),
        'institution': request.form.get('institution', '').strip(),
        'assigned_role_codes': list(dict.fromkeys(request.form.getlist('roles[]'))),
        'status': request.form.get('status', 'active'),
    }


def _validate_user_values(values):
    if not values['name'] or not values['email'] or not values['institution']:
        return 'Name, email, and institution are required.'
    if len(values['name']) > 160 or len(values['email']) > 255:
        return 'Name or email is too long.'
    email_parts = values['email'].split('@')
    if len(email_parts) != 2 or not email_parts[0] or '.' not in email_parts[1] or any(char.isspace() for char in values['email']):
        return 'Enter a valid email address.'
    if values['status'] not in {'active', 'inactive'}:
        return 'Choose a valid user status.'
    if not values['assigned_role_codes']:
        return 'Assign at least one role.'
    return None


def _validated_institution_and_roles(institution_id, role_codes):
    try:
        parsed_institution_id = UUID(institution_id)
    except ValueError:
        return None, None
    institution = db.session.get(Institution, parsed_institution_id)
    if institution is None:
        return None, None
    roles = db.session.execute(
        select(Role).where(
            Role.institution_id == parsed_institution_id,
            Role.code.in_(role_codes),
        )
    ).scalars().all()
    if {role.code for role in roles} != set(role_codes):
        return None, None
    return institution, roles


def _render_user_form(form_mode, user_id=None, values=None, form_error=None, status=200):
    is_edit = form_mode == 'edit'
    context = user_form_data(user_id)
    values = values or context.get('user', {})
    context.update({
        'user': values,
        'form_mode': form_mode,
        'is_edit': is_edit,
        'page_title': 'Edit User' if is_edit else 'New User',
        'save_url': url_for('web.admin_user_edit', user_id=user_id) if is_edit else url_for('web.admin_user_new'),
        'cancel_url': url_for('web.admin_users'),
        'form_error': form_error,
    })
    return _page('admin/users/form.html', **context), status


@web_bp.route('/admin/roles')
@require_role('it_admin')
def admin_roles():
    roles = roles_data()
    return _page('admin/roles/list.html', roles=roles, roles_shown_count=len(roles), roles_total_count=len(roles))


@web_bp.route('/admin/roles/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_role_new():
    if request.method == 'GET':
        return _render_role_form('create')

    values = {
        'code': request.form.get('code', '').strip(),
        'description': request.form.get('description', '').strip(),
    }
    error = _validate_role_values(values, include_code=True)
    if error:
        return _render_role_form('create', values=values, form_error=error, status=400)

    institution = db.session.scalar(select(Institution).order_by(Institution.name))
    if institution is None:
        return _render_role_form(
            'create', values=values,
            form_error='Create an institution before adding a role.', status=400,
        )

    try:
        db.session.add(Role(
            code=values['code'],
            description=values['description'],
            institution_id=institution.id,
        ))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _render_role_form(
            'create', values=values,
            form_error='A role with this code already exists for the institution.', status=409,
        )
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Failed to create admin role')
        return _render_role_form(
            'create', values=values,
            form_error='Unable to save this role right now.', status=503,
        )

    return redirect(url_for('web.admin_roles'))


@web_bp.route('/admin/roles/<role_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_role_edit(role_id):
    try:
        parsed_role_id = UUID(role_id)
    except ValueError:
        abort(404)

    role = db.session.get(Role, parsed_role_id)
    if role is None:
        abort(404)
    if request.method == 'GET':
        return _render_role_form('edit', role_id=parsed_role_id)

    values = {'description': request.form.get('description', '').strip()}
    error = _validate_role_values(values)
    if error:
        return _render_role_form(
            'edit', role_id=parsed_role_id, values=values, form_error=error, status=400,
        )

    try:
        role.description = values['description']
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _render_role_form(
            'edit', role_id=parsed_role_id, values=values,
            form_error='Unable to save this role because it conflicts with existing data.', status=409,
        )
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Failed to update admin role %s', parsed_role_id)
        return _render_role_form(
            'edit', role_id=parsed_role_id, values=values,
            form_error='Unable to save this role right now.', status=503,
        )

    return redirect(url_for('web.admin_roles'))


def _validate_role_values(values, include_code=False):
    if include_code and not values['code']:
        return 'Role code is required.'
    if not values['description']:
        return 'Description is required.'
    if include_code and len(values['code']) > 64:
        return 'Role code must contain at most 64 characters.'
    if len(values['description']) > 255:
        return 'Description must contain at most 255 characters.'
    return None


def _render_role_form(form_mode, role_id=None, values=None, form_error=None, status=200):
    is_edit = form_mode == 'edit'
    context = role_form_data(role_id)
    role_values = dict(context.get('role', {}))
    role_values.update(values or {})
    context.update({
        'role': role_values,
        'form_mode': form_mode,
        'is_edit': is_edit,
        'page_title': 'Edit Role' if is_edit else 'New Role',
        'save_url': url_for('web.admin_role_edit', role_id=role_id) if is_edit else url_for('web.admin_role_new'),
        'cancel_url': url_for('web.admin_roles'),
        'form_error': form_error,
    })
    return _page('admin/roles/form.html', **context), status


@web_bp.route('/admin/vision-models')
@require_role('it_admin')
def admin_vision_models():
    models = vision_models_data()
    return _page('admin/vision_models/list.html', vision_models=models, vision_models_shown_count=len(models), vision_models_total_count=len(models))


@web_bp.route('/admin/vision-models/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_vision_model_new():
    return _page('admin/vision_models/form.html', **vision_model_form_data(), page_title='New Vision Model', form_mode='create')


@web_bp.route('/admin/vision-models/<model_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_vision_model_edit(model_id):
    return _page('admin/vision_models/form.html', **vision_model_form_data(model_id), page_title='Edit Vision Model', form_mode='edit', model_id=model_id)


@web_bp.route('/admin/audit-log')
@require_role('it_admin')
def admin_audit_log():
    # AccessAudit has no code/entity/record/result shape matching the approved V2 reference
    # wired up yet — the template supplies the approved presentation fallback, same convention
    # as Supervisor's Audit Log. See V2_IMPLEMENTATION_NOTES.md.
    return _page('admin/audit_log.html')


@web_bp.route('/admin/configuration')
@require_role('it_admin')
def admin_configuration():
    stations = stations_data()
    rooms = operating_rooms_data()
    return _page('admin/configuration/index.html', capture_stations=stations, operating_rooms=rooms)


@web_bp.route('/admin/configuration/institution/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_institution_edit():
    return _page('admin/configuration/institution_form.html', **institution_form_data(), page_title='Edit Institution Information', form_mode='edit')


@web_bp.route('/admin/configuration/operating-rooms/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_operating_room_new():
    return _page('admin/configuration/operating_room_form.html', **operating_room_form_data(), page_title='New Operating Room', is_edit=False, form_mode='create')


@web_bp.route('/admin/configuration/operating-rooms/<room_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_operating_room_edit(room_id):
    return _page('admin/configuration/operating_room_form.html', **operating_room_form_data(room_id), page_title='Edit Operating Room', is_edit=True, form_mode='edit', room_id=room_id)


@web_bp.route('/admin/configuration/capture-stations/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_capture_station_new():
    return _page('admin/configuration/capture_station_form.html', **station_form_data(), page_title='New Capture Station', is_edit=False, form_mode='create')


@web_bp.route('/admin/configuration/capture-stations/<station_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_capture_station_edit(station_id):
    return _page('admin/configuration/capture_station_form.html', **station_form_data(station_id), page_title='Edit Capture Station', is_edit=True, form_mode='edit', station_id=station_id)