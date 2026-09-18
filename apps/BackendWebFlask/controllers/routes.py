from functools import wraps

from flask import Blueprint, abort, redirect, render_template, request, session, url_for

from controllers.view_data import (
    dashboard_data,
    discrepancies_data,
    family_form_data,
    families_data,
    institution_form_data,
    instrument_form_data,
    instruments_data,
    kit_form_data,
    kits_data,
    role_form_data,
    roles_data,
    sessions_data,
    session_discrepancies,
    station_form_data,
    stations_data,
    user_form_data,
    users_data,
)


web_bp = Blueprint('web', __name__)


DEMO_USERS = {
    'ana.perez@demo.local': {
        'name': 'Ana Perez',
        'role_code': 'operator_cde',
        'role_label': 'Operator',
    },
    'luis.ramirez@demo.local': {
        'name': 'Luis Ramirez',
        'role_code': 'supervisor_quality',
        'role_label': 'Supervisor',
    },
    'sofia.duarte@demo.local': {
        'name': 'Sofia Duarte',
        'role_code': 'it_admin',
        'role_label': 'Administrator',
    },
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
        'session': {
            'session_id': 'DEMO-001',
            'status_label': 'Draft',
            'status_variant': 'neutral',
            'procedure_name': 'Demo procedure',
            'operating_room': 'OR-01',
        },
    }
    context.update(values)
    return context


def _page(template, **values):
    return render_template(template, **_context(**values))


def _current_user():
    email = session.get('signed_user_email')
    user = DEMO_USERS.get(email)
    if user is None:
        return None
    return {**user, 'email': email}


def require_role(*role_codes):
    """MVP authorization based on the seeded demo email-to-role mapping."""
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
        'users': url_for('web.admin_users'),
        'roles': url_for('web.admin_roles'),
        'configuration': url_for('web.admin_configuration'),
    }


@web_bp.context_processor
def template_helpers():
    def translate(value, **variables):
        return value % variables if variables else value

    return {'_': translate}


@web_bp.route('/')
def index():
    return redirect(url_for('web.sign_in'))


@web_bp.route('/sign-in', methods=['GET', 'POST'])
def sign_in():
    if request.method == 'POST':
        email = request.form.get('institutional_email', '').strip().lower()
        user = DEMO_USERS.get(email)
        if user is None:
            return _page('auth/sign_in.html', sign_in_error='Unknown development user.')
        session.clear()
        session['signed_user_email'] = email
        session['signed_role_code'] = user['role_code']
        return redirect(url_for(_role_home(user['role_code'])))
    return _page('auth/sign_in.html', sign_in_error=None)


@web_bp.route('/sign-out', methods=['POST', 'GET'])
def sign_out():
    session.clear()
    return redirect(url_for('web.sign_in'))


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


@web_bp.route('/operator/sessions/active')
@require_role('operator_cde')
def operator_session_active():
    sessions = sessions_data()
    return _page('operator/sessions/active.html', session=sessions[0] if sessions else None)


@web_bp.route('/operator/sessions/validation')
@require_role('operator_cde')
def operator_session_validation():
    sessions = sessions_data()
    return _page('operator/sessions/validation.html', session=sessions[0] if sessions else None)


@web_bp.route('/operator/sessions/closed/<session_id>')
@require_role('operator_cde')
def operator_session_closed(session_id):
    session_data = next((item for item in sessions_data() if item['id'] == session_id), None)
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
        discrepancies_summary={
            'pending_reviews': sum(item['status_label'] == 'Open' for item in discrepancies),
            'open_discrepancies': sum(item['status_label'] == 'Open' for item in discrepancies),
            'reviewed_today': sum(item['status_label'] == 'Resolved' for item in discrepancies),
        },
    )


@web_bp.route('/supervisor/discrepancies/<session_id>/review')
@require_role('supervisor_quality')
def supervisor_discrepancy_review(session_id):
    session_data = next((item for item in sessions_data() if item['id'] == session_id), None)
    return _page('supervisor/discrepancies/review.html', session=session_data, session_id=session_id)


@web_bp.route('/supervisor/reports')
@require_role('supervisor_quality')
def supervisor_reports():
    return _page('supervisor/reports.html', reports=[])


@web_bp.route('/supervisor/indicators')
@require_role('supervisor_quality')
def supervisor_indicators():
    stats, sessions, discrepancies = dashboard_data('supervisor')
    return _page('supervisor/indicators.html', indicator_stats=stats, sessions_by_day=[], discrepancies_by_reason=[], discrepancies_by_kit=[])


@web_bp.route('/supervisor/audit-log')
@require_role('supervisor_quality')
def supervisor_audit_log():
    return _page('supervisor/audit_log.html', audit_log_entries=[], events_shown_count=0, events_total_count=0)


@web_bp.route('/admin/profile')
@require_role('it_admin')
def admin_profile():
    return _page('shared/profile.html', profile_role='admin')


@web_bp.route('/admin/dashboard')
@require_role('it_admin')
def admin_dashboard():
    stats, _, _ = dashboard_data('admin')
    return _page('admin/dashboard.html', dashboard_stats=stats, catalog_overview=[], system_overview=[])


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


@web_bp.route('/admin/users')
@require_role('it_admin')
def admin_users():
    users = users_data()
    return _page('admin/users/list.html', users=users, users_shown_count=len(users), users_total_count=len(users))


@web_bp.route('/admin/users/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_user_new():
    return _page('admin/users/form.html', **user_form_data(), page_title='New User', is_edit=False, form_mode='create')


@web_bp.route('/admin/users/<user_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_user_edit(user_id):
    return _page('admin/users/form.html', **user_form_data(user_id), page_title='Edit User', is_edit=True, form_mode='edit', user_id=user_id)


@web_bp.route('/admin/roles')
@require_role('it_admin')
def admin_roles():
    roles = roles_data()
    return _page('admin/roles/list.html', roles=roles, roles_shown_count=len(roles), roles_total_count=len(roles))


@web_bp.route('/admin/roles/<role_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_role_edit(role_id):
    return _page('admin/roles/edit.html', **role_form_data(role_id), role_id=role_id)


@web_bp.route('/admin/configuration')
@require_role('it_admin')
def admin_configuration():
    stations = stations_data()
    return _page('admin/configuration/index.html', capture_stations=stations)


@web_bp.route('/admin/configuration/institution/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_institution_edit():
    return _page('admin/configuration/institution_form.html', **institution_form_data(), page_title='Edit Institution Information', form_mode='edit')


@web_bp.route('/admin/configuration/capture-stations/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_capture_station_new():
    return _page('admin/configuration/capture_station_form.html', **station_form_data(), page_title='New Capture Station', is_edit=False, form_mode='create')


@web_bp.route('/admin/configuration/capture-stations/<station_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_capture_station_edit(station_id):
    return _page('admin/configuration/capture_station_form.html', **station_form_data(station_id), page_title='Edit Capture Station', is_edit=True, form_mode='edit', station_id=station_id)