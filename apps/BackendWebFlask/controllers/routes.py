from datetime import date, datetime, timedelta, timezone
from functools import wraps
from uuid import UUID

import requests
from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import generate_password_hash

from extensions import db
from models.AccessAudit import AccessAudit
from models.CatInstrumentCycleStatus import CatInstrumentCycleStatus
from models.CatOperationPhase import CatOperationPhase
from models.CatProcedureType import CatProcedureType
from models.Institution import Institution
from models.Instrument import Instrument
from models.InstrumentCycleEvent import InstrumentCycleEvent
from models.Kit import Kit
from models.KitItem import KitItem
from models.InstrumentFamily import InstrumentFamily
from models.ProcedureKit import ProcedureKit
from models.ProcedurePhase import ProcedurePhase
from models.Role import Role
from models.User import User
from models.UserRole import UserRole

from controllers.view_data import (
    PAST_OPERATOR_STATUS_CODES,
    admin_dashboard_overview,
    dashboard_data,
    discrepancies_data,
    discrepancies_summary_data,
    family_form_data,
    families_data,
    institution_form_data,
    instrument_form_data,
    instruments_query_page,
    kit_form_data,
    kits_query_page,
    operating_room_form_data,
    operating_rooms_data,
    operator_my_sessions,
    operator_session_detail,
    paginate_list,
    supervisor_session_detail,
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
from controllers.view_data import (
    operator_v3_assigned_session_rows,
    operator_v3_confirmation,
    operator_v3_demo_date,
    operator_v3_escalation_notes,
    operator_v3_is_assigned_demo_id,
    operator_v3_list_banner,
    operator_v3_parse_validation,
    operator_v3_rows_from_rf,
    operator_v3_scenario,
    operator_v3_validation_query,
    operator_v3_view,
    v3_operator_display_user,
    v3_session_status,
    supervisor_v3_audit_log,
    supervisor_v3_created_banner,
    supervisor_v3_dashboard,
    supervisor_indicators_from_rf,
    supervisor_rf_filter_options,
    supervisor_v3_dashboard_from_rf,
    supervisor_v3_filter_options,
    supervisor_v3_indicators,
    supervisor_v3_list_meta,
    supervisor_v3_new_session,
    supervisor_v3_queue_from_rf,
    supervisor_v3_reports,
    supervisor_v3_review_case_keys,
    supervisor_v3_review_queue,
    supervisor_v3_review_summary,
    supervisor_v3_rows_from_rf,
    supervisor_v3_session_history,
    supervisor_v3_sessions,
    supervisor_v3_validated_banner,
    supervisor_v3_view,
    use_v3_fixture,
    v3_supervisor_display_user,
)


web_bp = Blueprint('web', __name__)

ROLE_LABELS = {
    'station_operator': 'Station Operator',
    'spd_supervisor': 'SPD Supervisor',
    'it_admin': 'Administrator',
}

EMPTY_STATS = {
    'total': 0,
    'active': 0,
    'pending': 0,
    'completed': 0,
}


def _normalize_locale(value):
    locale = (value or '').strip()
    if locale == 'es':
        locale = 'es-MX'
    if locale not in {'en', 'es-MX'}:
        return 'en'
    return locale


def _resolve_locale(user):
    prefs = (user or {}).get('ui_preferences') or {}
    from_prefs = prefs.get('locale') or prefs.get('language')
    if from_prefs:
        return _normalize_locale(from_prefs)
    return _normalize_locale(request.cookies.get('pef_locale') or 'en')


def _context(**values):
    current_user = _current_user()
    context = {
        'current_locale': _resolve_locale(current_user),
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
    refresh_token = request.cookies.get('refresh_token')

    # Transparent renewal: refresh even when the access cookie already expired/missing
    # (OP/SPD must not be kicked mid-flow solely because access max-age elapsed).
    response = None
    if access_token:
        response = _auth_request('get', '/verify', token=access_token)
    need_refresh = (
        not access_token
        or (response is not None and response.status_code == 401)
    )
    if need_refresh and refresh_token:
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
        'ui_preferences': user_data.get('ui_preferences') or {},
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
    cookies = []
    headers = getattr(getattr(upstream_response, 'raw', None), 'headers', None)
    if headers is not None and hasattr(headers, 'getlist'):
        cookies.extend(headers.getlist('Set-Cookie'))
    if cookies:
        return cookies

    # Fallback when urllib3 collapses Set-Cookie: rebuild from cookie jar.
    for cookie in upstream_response.cookies:
        parts = [f'{cookie.name}={cookie.value}', 'Path=/', 'HttpOnly']
        if cookie.secure:
            parts.append('Secure')
        if cookie.get_nonstandard_attr('samesite'):
            parts.append(f"SameSite={cookie.get_nonstandard_attr('samesite')}")
        cookies.append('; '.join(parts))
    return cookies


def _clear_auth_cookies(response):
    for name in ('access_token', 'refresh_token'):
        response.delete_cookie(name, path='/')
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


def _nav_urls(user):
    if not user:
        return {'sign_in': url_for('web.sign_in')}

    profile_endpoints = {
        'station_operator': 'web.operator_profile',
        'spd_supervisor': 'web.supervisor_profile',
        'it_admin': 'web.admin_profile',
    }
    common = {
        'profile': url_for(profile_endpoints[user['role_code']]),
        'sign_out': url_for('web.sign_out'),
    }
    if user['role_code'] == 'station_operator':
        return {
            **common,
            # RF-OP-01: home is session list (no metrics dashboard).
            'dashboard': url_for('web.operator_sessions'),
            'counting_sessions': url_for('web.operator_sessions'),
            'session_history': url_for('web.operator_session_history'),
            # Pre-RF UI kept under /legacy (see apps/BackendWebFlask/legacy/README.md).
            'legacy_dashboard': url_for('web.legacy_operator_dashboard'),
            'legacy_new_session': url_for('web.legacy_operator_session_new'),
        }
    if user['role_code'] == 'spd_supervisor':
        return {
            **common,
            'dashboard': url_for('web.supervisor_dashboard'),
            'sessions': url_for('web.supervisor_sessions'),
            'new_session': url_for('web.supervisor_session_new'),
            'discrepancies': url_for('web.supervisor_discrepancies'),
            'session_history': url_for('web.supervisor_session_history'),
            'kits': url_for('web.admin_kits'),
            'instruments': url_for('web.admin_instruments'),
            'reports': url_for('web.supervisor_reports'),
            'indicators': url_for('web.supervisor_indicators'),
            'audit_log': url_for('web.supervisor_audit_log'),
            'services_health': url_for('web.supervisor_services_health'),
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


@web_bp.get('/sign-in')
def sign_in():
    redirect_target = request.args.get('next')
    return _page(
        'auth/sign_in.html',
        redirect_target=redirect_target,
    )


@web_bp.route('/sign-out', methods=['POST', 'GET'])
def sign_out():
    access_token = request.cookies.get('access_token')
    refresh_token = request.cookies.get('refresh_token')
    token = access_token or refresh_token
    if token:
        _auth_request('post', '/logout', token=token)
    session.clear()
    response = redirect(url_for('web.sign_in'))
    return _clear_auth_cookies(response)


# ----------------------------------------------------------------------------------------
# Operator V3 (ui_reference_v3/operator). V3 branches run only when use_v3_fixture() allows
# (demo mode for the list; explicit WS-0xx demo ids for session pages). UUID sessions keep
# the RF/V2 templates and RF POST behavior unchanged. The `scenario` query parameter
# (escalated | clean) is presentation-only and is carried in URLs, never persisted.
# A submitted Human Validation (hv=1 + vc_/vr_/vn_/ev_ parameters, see
# view_data.operator_v3_parse_validation) is carried the same way and, when present,
# decides the clean / escalated outcome from what the Operator entered.
# ----------------------------------------------------------------------------------------

V3_TRAY_STATES = ('verifying', 'failed', 'success')


def _v3_operator_page(template, *, demo, **values):
    """Render an Operator V3 template; demo pages show the presentation identity only."""
    if demo:
        values.setdefault('current_user', v3_operator_display_user(_current_user()))
    return _page(template, v3_demo=demo, **values)


def _v3_op_url(endpoint, session_id, scenario, validation=None, **params):
    if operator_v3_scenario(scenario) == 'clean':
        params['scenario'] = 'clean'
    for key, value in operator_v3_validation_query(validation).items():
        params.setdefault(key, value)
    return url_for(endpoint, session_id=session_id, **params)


def _v3_operator_flow(session_id, scenario, validation=None):
    def link(endpoint, **params):
        return _v3_op_url(endpoint, session_id, scenario, validation, **params)
    return {
        'sessions': url_for('web.operator_sessions'),
        'begin': link('web.operator_session_begin'),
        'capture': link('web.operator_session_capture'),
        'tray_start': link('web.operator_session_tray_verification', state='verifying', attempt=1),
        'tray_retry': link('web.operator_session_tray_verification', state='verifying', attempt=2),
        'tray_failed': link('web.operator_session_tray_verification', state='failed'),
        'tray_success': link('web.operator_session_tray_verification', state='success'),
        'ai_detection': link('web.operator_session_ai_detection'),
        'validation': link('web.operator_session_validation'),
        'validation_summary': link('web.operator_session_validation_summary'),
        'escalation': link('web.operator_session_discrepancy', state='prepared'),
        'escalation_action': link('web.operator_session_discrepancy'),
        'escalated': link('web.operator_session_discrepancy', state='escalated'),
        'ready_to_close': link('web.operator_session_ready_to_close'),
        'close_action': link('web.operator_session_v3_close'),
    }


def _v3_flow_for(view):
    """Flow links for a V3 view, carrying its base scenario and submitted validation state."""
    return _v3_operator_flow(view['session']['session_id'], view['base_scenario'], view['validation_state'])


def _v3_request_validation():
    """Submitted Human Validation demo state carried in the query string (or None)."""
    state, _errors = operator_v3_parse_validation(request.args, request.values.get('scenario'))
    return state


def _v3_operator_view_or_redirect(session_id, validation=None):
    """WS-026 walkthrough view, or a redirect for other assigned demo sessions, or 404."""
    if validation is None:
        validation = _v3_request_validation()
    view = operator_v3_view(session_id, request.values.get('scenario'), validation)
    if view is not None:
        return view, None
    if operator_v3_is_assigned_demo_id(session_id):
        return None, redirect(url_for('web.operator_sessions', notice='walkthrough', session=session_id.upper()))
    abort(404)


def _v3_operator_flow_page(template, session_id, status_code, validation=None, **values):
    view, response = _v3_operator_view_or_redirect(session_id, validation)
    if response is not None:
        return response
    sid = view['session']['session_id']
    return _v3_operator_page(
        template, demo=True, v3=view, session_id=sid,
        flow=_v3_flow_for(view),
        context_status=v3_session_status(status_code), **values,
    )


def _v3_operator_begin(session_id):
    """V3 Assigned Session Confirmation (read-only). Demo only: no DB write on Start."""
    confirmation = operator_v3_confirmation(session_id)
    if confirmation is None:
        abort(404)
    sid = confirmation['session']['session_id']
    scenario = operator_v3_scenario(request.values.get('scenario'))
    values = {'confirmation': confirmation, 'session_id': sid,
              'begin_action': _v3_op_url('web.operator_session_begin', sid, scenario)}
    if request.method == 'POST':
        if request.form.get('instruments_ready') != '1':
            return _v3_operator_page(
                'operator/sessions/begin.html', demo=True,
                form_error='Confirm the physical instrument set before starting.', **values,
            ), 400
        if not confirmation['is_full_case']:
            return redirect(url_for('web.operator_sessions', notice='walkthrough', session=sid))
        return redirect(_v3_op_url('web.operator_session_capture', sid, scenario))
    return _v3_operator_page('operator/sessions/begin.html', demo=True, **values)


@web_bp.route('/operator/profile')
@require_role('station_operator')
def operator_profile():
    return _page('shared/profile.html', profile_role='operator')


@web_bp.route('/operator/dashboard')
@require_role('station_operator')
def operator_dashboard():
    # Pre-RF metrics dashboard kept reachable via redirect to legacy.
    return redirect(url_for('web.legacy_operator_dashboard'))


@web_bp.route('/operator/sessions')
@require_role('station_operator')
def operator_sessions():
    """Operator landing page — RF sessions only (assigned / in progress)."""
    from controllers.rf_ui import active_session_rows

    user = _current_user()
    rf_rows = active_session_rows(operator_my_sessions(user['id']))
    rows = operator_v3_rows_from_rf(rf_rows)

    search = (request.args.get('search') or '').strip().lower()
    status = (request.args.get('status') or '').strip()
    room = (request.args.get('room') or '').strip()
    date_filter = (request.args.get('date') or '').strip()
    today = date.today().isoformat()

    filtered = []
    for row in rows:
        if search:
            hay = ' '.join([
                str(row.get('session_id') or ''),
                str(row.get('procedure_name') or ''),
                str(row.get('kit_name') or ''),
            ]).lower()
            if search not in hay:
                continue
        code = (row.get('session_status') or {}).get('code') or ''
        if status and code != status:
            continue
        if room and row.get('operating_room') != room:
            continue
        scheduled = str(row.get('scheduled_time') or '')
        if date_filter == 'today' and not scheduled.startswith(today):
            continue
        if date_filter in {'next-7-days', 'upcoming'} and scheduled:
            # Keep rows whose scheduled date is today or later (best-effort on display string).
            day = scheduled[:10]
            if len(day) == 10 and day < today:
                continue
        filtered.append(row)

    per_page = 20
    page = max(1, int(request.args.get('page') or 1))
    total = len(filtered)
    pages = max(1, (total + per_page - 1) // per_page)
    if page > pages:
        page = pages
    start = (page - 1) * per_page
    page_rows = filtered[start:start + per_page]
    list_filters = {
        'search': request.args.get('search', ''),
        'status': status,
        'room': room,
        'date': date_filter,
    }
    previous_url = (
        url_for('web.operator_sessions', page=page - 1, **list_filters)
        if page > 1 else None
    )
    next_url = (
        url_for('web.operator_sessions', page=page + 1, **list_filters)
        if page < pages else None
    )
    return _v3_operator_page(
        'operator/sessions/list.html',
        demo=False,
        session_rows=page_rows,
        feedback_banner=None,
        list_filters=list_filters,
        filter_source_rows=rows,
        list_meta={'total': total, 'pages': pages, 'page': page},
        current_page=page,
        total_pages=pages,
        previous_url=previous_url,
        next_url=next_url,
        go_to_action=url_for('web.operator_sessions'),
        pagination_query=list_filters,
    )


@web_bp.route('/operator/sessions/new', methods=['GET', 'POST'])
@require_role('station_operator')
def operator_session_new():
    # RF-SP-02: only SPD schedules sessions. Operator create UI → legacy.
    return redirect(url_for('web.legacy_operator_session_new'))


@web_bp.route('/operator/sessions/history')
@require_role('station_operator')
def operator_session_history():
    from controllers.rf_ui import (
        apply_history_filters,
        history_filter_options,
        past_session_rows,
    )

    user = _current_user()
    all_past = past_session_rows(operator_my_sessions(user['id']))
    filters = {
        'search': request.args.get('search', ''),
        'session_id': request.args.get('session_id', ''),
        'procedure': request.args.get('procedure', ''),
        'kit': request.args.get('kit', ''),
        'operating_room': request.args.get('operating_room', ''),
        'station': request.args.get('station', ''),
        'privacy': request.args.get('privacy', ''),
        'closed_date': request.args.get('closed_date', ''),
        'status': request.args.get('status', ''),
    }
    sessions = apply_history_filters(all_past, filters)
    per_page = 20
    page = max(1, int(request.args.get('page') or 1))
    total = len(sessions)
    pages = max(1, (total + per_page - 1) // per_page)
    if page > pages:
        page = pages
    start = (page - 1) * per_page
    page_sessions = sessions[start:start + per_page]
    previous_url = (
        url_for('web.operator_session_history', page=page - 1, **filters)
        if page > 1 else None
    )
    next_url = (
        url_for('web.operator_session_history', page=page + 1, **filters)
        if page < pages else None
    )
    return _page(
        'operator/sessions/history.html',
        session_history=page_sessions,
        sessions_shown_count=len(page_sessions),
        sessions_total_count=total,
        history_filters=filters,
        history_filter_options=history_filter_options(all_past),
        current_page=page,
        total_pages=pages,
        has_previous=page > 1,
        has_next=page < pages,
        previous_url=previous_url,
        next_url=next_url,
        go_to_action=url_for('web.operator_session_history'),
        pagination_query=filters,
    )


@web_bp.route('/operator/sessions/<session_id>')
@require_role('station_operator')
def operator_session_details(session_id):
    from controllers.rf_ui import past_session_rows

    user = _current_user()
    detail = operator_session_detail(session_id, user['id'])
    if detail is None:
        abort(404)
    nav = 'session_history' if detail.get('status_code') in {
        'closed', 'aborted', 'awaiting_spd_review', 'correction_required',
    } else 'counting_sessions'
    return _page(
        'operator/sessions/details.html',
        session=detail,
        session_id=session_id,
        active_nav_item=nav,
    )


@web_bp.route('/operator/sessions/<session_id>/begin', methods=['GET', 'POST'])
@require_role('station_operator')
def operator_session_begin(session_id):
    """RF-OP-03 Start Session — V3 confirmation UI wired to start_session."""
    from controllers.rf_ui import confirmation_from_rf_detail
    from services.rf_session import RfSessionError, start_session

    user = _current_user()
    detail = operator_session_detail(session_id, user['id'])
    if detail is None:
        abort(404)
    if detail.get('status_code') != 'scheduled':
        return redirect(url_for('web.operator_session_details', session_id=session_id))

    confirmation = confirmation_from_rf_detail(detail)
    begin_action = url_for('web.operator_session_begin', session_id=session_id)
    values = {
        'confirmation': confirmation,
        'session_id': session_id,
        'begin_action': begin_action,
        'session': detail,
    }

    if request.method == 'POST':
        if request.form.get('instruments_ready') != '1':
            return _v3_operator_page(
                'operator/sessions/begin.html', demo=False,
                form_error='Confirm the physical instrument set before starting.',
                **values,
            ), 400
        try:
            result = start_session(
                session_id=UUID(session_id),
                operator_user_id=UUID(user['id']),
                institution_id=UUID(user['institution_id']) if user.get('institution_id') else None,
                ip=request.remote_addr,
            )
        except RfSessionError as exc:
            return _v3_operator_page(
                'operator/sessions/begin.html', demo=False,
                form_error=exc.message, **values,
            ), exc.status_code
        if result['capture_mode'] == 'manual_no_privacy':
            return redirect(url_for('web.operator_session_manual', session_id=session_id))
        return redirect(url_for('web.operator_session_capture', session_id=session_id))
    return _v3_operator_page('operator/sessions/begin.html', demo=False, **values)


@web_bp.route('/operator/sessions/<session_id>/manual')
@require_role('station_operator')
def operator_session_manual(session_id):
    """RF-OP-04M progress without live capture."""
    user = _current_user()
    detail = operator_session_detail(session_id, user['id'])
    if detail is None:
        abort(404)
    if detail.get('status_code') != 'in_progress':
        return redirect(url_for('web.operator_session_details', session_id=session_id))
    if detail.get('capture_mode') == 'vision':
        return redirect(url_for('web.operator_session_capture', session_id=session_id))
    if detail.get('capture_mode') != 'manual_no_privacy':
        return redirect(url_for('web.operator_session_details', session_id=session_id))
    return _page(
        'operator/sessions/manual_progress.html',
        session=detail,
        session_id=session_id,
    )


@web_bp.route('/operator/sessions/<session_id>/manual-report', methods=['GET', 'POST'])
@require_role('station_operator')
def operator_session_manual_report(session_id):
    """RF-OP-06M quantity report and close."""
    from services.rf_session import RfSessionError, submit_manual_close

    user = _current_user()
    detail = operator_session_detail(session_id, user['id'])
    if detail is None:
        abort(404)
    if detail.get('status_code') != 'in_progress':
        return redirect(url_for('web.operator_session_details', session_id=session_id))
    if detail.get('capture_mode') not in {'manual_no_privacy', 'vision'}:
        return redirect(url_for('web.operator_session_details', session_id=session_id))

    def _report_values_from_form():
        values = {}
        for item in detail.get('expected_items') or []:
            family_id = item['family_id']
            raw = request.form.get(f"reported_{family_id}", "")
            try:
                reported = int(raw)
            except ValueError:
                reported = raw
            values[family_id] = {
                'reported': reported,
                'reason': request.form.get(f"reason_{family_id}", "").strip(),
                'notes': request.form.get(f"notes_{family_id}", "").strip(),
            }
        return values

    if request.method == 'POST':
        reports = []
        report_values = _report_values_from_form()
        for item in detail.get('expected_items') or []:
            family_id = item['family_id']
            try:
                reported = int(request.form.get(f"reported_{family_id}", ""))
            except ValueError:
                return _page(
                    'operator/sessions/manual_report.html',
                    session=detail,
                    session_id=session_id,
                    form_error='Enter a whole number for every reported quantity.',
                    reason_options=_manual_reason_options(),
                    report_values=report_values,
                ), 400
            reports.append({
                'family_id': family_id,
                'reported_quantity': reported,
                'reason_code': request.form.get(f"reason_{family_id}", "").strip(),
                'notes': request.form.get(f"notes_{family_id}", "").strip(),
            })
        try:
            result = submit_manual_close(
                session_id=UUID(session_id),
                operator_user_id=UUID(user['id']),
                institution_id=UUID(user['institution_id']) if user.get('institution_id') else None,
                reports=reports,
                ip=request.remote_addr,
            )
        except RfSessionError as exc:
            return _page(
                'operator/sessions/manual_report.html',
                session=detail,
                session_id=session_id,
                form_error=exc.message,
                reason_options=_manual_reason_options(),
                report_values=report_values,
            ), exc.status_code
        return redirect(url_for(
            'web.operator_session_manual_report_done',
            session_id=session_id,
            status=result['status_code'],
        ))

    return _page(
        'operator/sessions/manual_report.html',
        session=detail,
        session_id=session_id,
        reason_options=_manual_reason_options(),
    )


@web_bp.route('/operator/sessions/<session_id>/manual-report/done')
@require_role('station_operator')
def operator_session_manual_report_done(session_id):
    user = _current_user()
    detail = operator_session_detail(session_id, user['id'])
    if detail is None:
        abort(404)
    return _page(
        'operator/sessions/manual_report_done.html',
        session=detail,
        session_id=session_id,
        result_status=request.args.get('status') or detail.get('status_code'),
    )


def _manual_reason_options():
    return [
        {'code': 'shortage', 'label': 'Shortage / missing piece'},
        {'code': 'surplus', 'label': 'Extra / surplus'},
        {'code': 'operator_error', 'label': 'Operator counting error'},
        {'code': 'other', 'label': 'Other'},
    ]


# ----------------------------------------------------------------------------------------
# Legacy operator UI (pre-RF). Reachable under /legacy/... so it does not collide with
# RF-OP-01 / RF-SP-02. Templates stay in operator/; see legacy/README.md.
# ----------------------------------------------------------------------------------------

@web_bp.route('/legacy/operator/dashboard')
@require_role('station_operator')
def legacy_operator_dashboard():
    stats, sessions, _ = dashboard_data('operator')
    return _page('operator/dashboard.html', dashboard_stats=stats, recent_sessions=sessions[:5])


@web_bp.route('/legacy/operator/sessions/new', methods=['GET', 'POST'])
@require_role('station_operator')
def legacy_operator_session_new():
    if request.method == 'POST':
        return redirect(url_for('web.operator_session_capture', session_id='WS-026'))
    return _page('operator/sessions/new.html')


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
@require_role('station_operator')
def operator_session_capture(session_id):
    """RF-OP-04 vision capture: upload video → YOLO worker → boxes + count logs."""
    from services.rf_session import schedule_form_options
    from services.vision_bridge import VisionBridgeError, bind_session, worker_health

    user = _current_user()
    detail = operator_session_detail(session_id, user['id'])
    if detail is None:
        abort(404)
    if detail.get('status_code') != 'in_progress' or detail.get('capture_mode') != 'vision':
        return redirect(url_for('web.operator_session_details', session_id=session_id))
    health = worker_health()
    bind_error = None
    if health.get('ok') or health.get('weights_present') is not None:
        try:
            bind_session(UUID(session_id))
        except VisionBridgeError as exc:
            bind_error = exc.message
        except Exception as exc:  # noqa: BLE001
            bind_error = str(exc)
    phases = []
    try:
        opts = schedule_form_options(UUID(user['institution_id'])) if user.get('institution_id') else None
        phases = (opts or {}).get('phases') or []
    except Exception:
        phases = []
    return _page(
        'operator/sessions/capture_rf.html',
        session=detail,
        session_id=session_id,
        worker_health=health,
        bind_error=bind_error,
        phases=phases,
        state_url=url_for('web.operator_session_capture_state', session_id=session_id),
        upload_url=url_for('web.operator_session_capture_upload', session_id=session_id),
        finish_url=url_for('web.operator_session_capture_finish', session_id=session_id),
        phase_url=url_for('web.operator_session_capture_phase', session_id=session_id),
        report_url=url_for('web.operator_session_manual_report', session_id=session_id),
    )


@web_bp.route('/operator/sessions/<session_id>/capture/state')
@require_role('station_operator')
def operator_session_capture_state(session_id):
    from services.rf_session import (
        persist_board_auto_count,
        persist_vision_timeline_windows,
    )
    from services.vision_bridge import VisionBridgeError, fetch_state, fetch_timeline

    user = _current_user()
    detail = operator_session_detail(session_id, user['id'])
    if detail is None:
        abort(404)
    if detail.get('status_code') != 'in_progress' or detail.get('capture_mode') != 'vision':
        return jsonify({'ok': False, 'error': 'Session not in vision capture'}), 409
    try:
        data = fetch_state(UUID(session_id))
    except VisionBridgeError as exc:
        return jsonify({'ok': False, 'error': exc.message}), exc.status_code
    session_state = data.get('session') or {}
    status = session_state.get('status')
    run_id = session_state.get('run_id')
    frames_processed = session_state.get('frames_processed')
    sample_every = max(1, int(session_state.get('count_sample_every') or 10))
    sample_meta_base = {
        'run_id': run_id,
        'frame_index': session_state.get('frame_index'),
        'frames_processed': frames_processed,
        'pipeline': session_state.get('pipeline'),
        'assign_solver': session_state.get('assign_solver'),
        'model_version': session_state.get('model_version'),
        'assignment_metrics': session_state.get('assignment_metrics') or {},
        'count_sample_every': sample_every,
        'conf_threshold': session_state.get('conf_threshold'),
    }

    # Live window: one board event if UI polls on a sample boundary (idempotent once_key).
    if (
        status == 'processing'
        and session_state.get('counts')
        and run_id
        and frames_processed
        and int(frames_processed) % sample_every == 0
    ):
        try:
            persist_board_auto_count(
                session_id=UUID(session_id),
                operator_user_id=UUID(user['id']),
                counts=session_state.get('counts') or {},
                model_version=session_state.get('model_version'),
                reason='video_window',
                once_key=f"{session_id}:video_window:{run_id}:{frames_processed}",
                include_boxes=False,
                sample_meta=sample_meta_base,
            )
        except Exception:
            pass

    # On ready: backfill board windows from worker NDJSON, then final video_ready (with boxes).
    if status == 'ready' and session_state.get('counts'):
        try:
            timeline = fetch_timeline(UUID(session_id), run_id=run_id)
            persist_vision_timeline_windows(
                session_id=UUID(session_id),
                operator_user_id=UUID(user['id']),
                samples=timeline.get('samples') or [],
                model_version=session_state.get('model_version'),
            )
        except Exception:
            pass
        try:
            persist_board_auto_count(
                session_id=UUID(session_id),
                operator_user_id=UUID(user['id']),
                counts=session_state.get('counts') or {},
                model_version=session_state.get('model_version'),
                reason='video_ready',
                once_key=f"{session_id}:video_ready:{run_id}:{frames_processed}",
                include_boxes=True,
                sample_meta=sample_meta_base,
            )
        except Exception:
            pass
    return jsonify(data)


@web_bp.route('/operator/sessions/<session_id>/capture/upload', methods=['POST'])
@require_role('station_operator')
def operator_session_capture_upload(session_id):
    from services.vision_bridge import VisionBridgeError, bind_session, upload_video

    user = _current_user()
    detail = operator_session_detail(session_id, user['id'])
    if detail is None:
        abort(404)
    if detail.get('status_code') != 'in_progress' or detail.get('capture_mode') != 'vision':
        return jsonify({'ok': False, 'error': 'Session not in vision capture'}), 409
    video = request.files.get('video')
    if video is None or not video.filename:
        return jsonify({'ok': False, 'error': 'Choose a video file (.mp4, .mkv, …).'}), 400
    try:
        bind_session(UUID(session_id))
        result = upload_video(UUID(session_id), video)
    except VisionBridgeError as exc:
        return jsonify({'ok': False, 'error': exc.message}), exc.status_code
    return jsonify(result), 202


@web_bp.route('/operator/sessions/<session_id>/capture/finish', methods=['POST'])
@require_role('station_operator')
def operator_session_capture_finish(session_id):
    from services.rf_session import RfSessionError, finish_vision_counting
    from services.vision_bridge import VisionBridgeError, fetch_state

    user = _current_user()
    detail = operator_session_detail(session_id, user['id'])
    if detail is None:
        abort(404)
    if detail.get('status_code') != 'in_progress' or detail.get('capture_mode') != 'vision':
        return redirect(url_for('web.operator_session_details', session_id=session_id))
    try:
        data = fetch_state(UUID(session_id))
        finish_vision_counting(
            session_id=UUID(session_id),
            operator_user_id=UUID(user['id']),
            worker_session=data.get('session') or {},
        )
    except (VisionBridgeError, RfSessionError) as exc:
        flash(getattr(exc, 'message', str(exc)), 'warning')
        return redirect(url_for('web.operator_session_capture', session_id=session_id))
    return redirect(url_for('web.operator_session_detection_summary', session_id=session_id))


@web_bp.route('/operator/sessions/<session_id>/capture/phase', methods=['POST'])
@require_role('station_operator')
def operator_session_capture_phase(session_id):
    from services.rf_session import RfSessionError, change_phase_stub

    user = _current_user()
    detail = operator_session_detail(session_id, user['id'])
    if detail is None:
        abort(404)
    if detail.get('status_code') != 'in_progress':
        return jsonify({'ok': False, 'error': 'Session not in progress'}), 409
    to_phase = (request.form.get('phase_code') or (request.get_json(silent=True) or {}).get('phase_code') or '').strip()
    if not to_phase:
        return jsonify({'ok': False, 'error': 'phase_code required'}), 400
    try:
        result = change_phase_stub(
            session_id=UUID(session_id),
            operator_user_id=UUID(user['id']),
            institution_id=UUID(user['institution_id']) if user.get('institution_id') else None,
            to_phase_code=to_phase,
            ip=request.remote_addr,
        )
    except RfSessionError as exc:
        return jsonify({'ok': False, 'error': exc.message}), exc.status_code
    return jsonify({'ok': True, **result})


@web_bp.route('/operator/sessions/<session_id>/detection-summary')
@require_role('station_operator')
def operator_session_detection_summary(session_id):
    """RF-OP-05 AI detection summary (demo: last annotated frame from worker, no Mongo)."""
    from services.rf_session import get_vision_snapshot

    user = _current_user()
    detail = operator_session_detail(session_id, user['id'])
    if detail is None:
        abort(404)
    if detail.get('status_code') != 'in_progress' or detail.get('capture_mode') != 'vision':
        return redirect(url_for('web.operator_session_details', session_id=session_id))
    snapshot = get_vision_snapshot(session_id)
    if snapshot is None:
        return redirect(url_for('web.operator_session_capture', session_id=session_id))
    counts_by_family = {}
    for code, meta in (snapshot.get('counts') or {}).items():
        fid = str(meta.get('family_id') or '')
        if fid:
            counts_by_family[fid] = meta
    rows = []
    for item in detail.get('expected_items') or []:
        meta = counts_by_family.get(item['family_id'], {})
        detected = int(meta.get('detected_quantity') or 0)
        expected = int(item['expected_quantity'] or 0)
        diff = detected - expected
        if diff == 0:
            result_label, result_variant = 'Match', 'success'
        elif diff < 0:
            result_label, result_variant = 'Missing', 'danger'
        else:
            result_label, result_variant = 'Extra', 'warning'
        rows.append({
            **item,
            'ai_detected_quantity': detected,
            'difference': diff,
            'avg_confidence': meta.get('avg_confidence'),
            'best_confidence': meta.get('best_confidence'),
            'weakest_confidence': meta.get('weakest_confidence'),
            'raw_detected_quantity': meta.get('raw_detected_quantity'),
            'result_label': result_label,
            'result_variant': result_variant,
        })
    return _page(
        'operator/sessions/detection_summary_rf.html',
        session=detail,
        session_id=session_id,
        snapshot=snapshot,
        detection_rows=rows,
        validation_url=url_for('web.operator_session_vision_validation', session_id=session_id),
        capture_url=url_for('web.operator_session_capture', session_id=session_id),
    )


@web_bp.route('/operator/sessions/<session_id>/vision-validation', methods=['GET', 'POST'])
@require_role('station_operator')
def operator_session_vision_validation(session_id):
    """RF-OP-06 validation UI fed by YOLO snapshot; close path = manual_count (no auto disc from video)."""
    from services.rf_session import RfSessionError, get_vision_snapshot, submit_manual_close

    user = _current_user()
    detail = operator_session_detail(session_id, user['id'])
    if detail is None:
        abort(404)
    if detail.get('status_code') != 'in_progress' or detail.get('capture_mode') != 'vision':
        return redirect(url_for('web.operator_session_details', session_id=session_id))
    snapshot = get_vision_snapshot(session_id)
    if snapshot is None:
        return redirect(url_for('web.operator_session_capture', session_id=session_id))

    counts_by_family = {}
    for _code, meta in (snapshot.get('counts') or {}).items():
        fid = str(meta.get('family_id') or '')
        if fid:
            counts_by_family[fid] = meta

    def _default_values():
        values = {}
        for item in detail.get('expected_items') or []:
            meta = counts_by_family.get(item['family_id'], {})
            ai_detected = int(meta.get('detected_quantity') or 0)
            values[item['family_id']] = {
                'ai_detected': ai_detected,
                'validated': ai_detected,
                'reason': '',
                'notes': '',
            }
        return values

    if request.method == 'POST':
        reports = []
        report_values = {}
        for item in detail.get('expected_items') or []:
            family_id = item['family_id']
            meta = counts_by_family.get(family_id, {})
            ai_detected = int(meta.get('detected_quantity') or 0)
            try:
                validated = int(request.form.get(f'validated_{family_id}', ''))
            except ValueError:
                return _page(
                    'operator/sessions/vision_validation_rf.html',
                    session=detail,
                    session_id=session_id,
                    snapshot=snapshot,
                    form_error='Enter a whole number for every validated count.',
                    reason_options=_manual_reason_options(),
                    report_values=_default_values(),
                    summary_url=url_for('web.operator_session_detection_summary', session_id=session_id),
                ), 400
            reason = request.form.get(f'reason_{family_id}', '').strip()
            notes = request.form.get(f'notes_{family_id}', '').strip()
            report_values[family_id] = {
                'ai_detected': ai_detected,
                'validated': validated,
                'reason': reason,
                'notes': notes,
            }
            reports.append({
                'family_id': family_id,
                'reported_quantity': validated,
                'reason_code': reason,
                'notes': notes,
            })
        try:
            result = submit_manual_close(
                session_id=UUID(session_id),
                operator_user_id=UUID(user['id']),
                institution_id=UUID(user['institution_id']) if user.get('institution_id') else None,
                reports=reports,
                ip=request.remote_addr,
            )
        except RfSessionError as exc:
            return _page(
                'operator/sessions/vision_validation_rf.html',
                session=detail,
                session_id=session_id,
                snapshot=snapshot,
                form_error=exc.message,
                reason_options=_manual_reason_options(),
                report_values=report_values,
                summary_url=url_for('web.operator_session_detection_summary', session_id=session_id),
            ), exc.status_code
        return redirect(url_for(
            'web.operator_session_manual_report_done',
            session_id=session_id,
            status=result['status_code'],
        ))

    return _page(
        'operator/sessions/vision_validation_rf.html',
        session=detail,
        session_id=session_id,
        snapshot=snapshot,
        reason_options=_manual_reason_options(),
        report_values=_default_values(),
        summary_url=url_for('web.operator_session_detection_summary', session_id=session_id),
    )


@web_bp.route('/operator/sessions/<session_id>/tray-verification')
@require_role('station_operator')
def operator_session_tray_verification(session_id):
    """Removed from RF path — tray×2 was V3-only. Send vision sessions to capture."""
    return redirect(url_for('web.operator_session_capture', session_id=session_id))


@web_bp.route('/operator/sessions/<session_id>/ai-detection')
@require_role('station_operator')
def operator_session_ai_detection(session_id):
    """Legacy V3 path → RF detection summary when a vision snapshot exists."""
    from services.rf_session import get_vision_snapshot
    if get_vision_snapshot(session_id):
        return redirect(url_for('web.operator_session_detection_summary', session_id=session_id))
    return redirect(url_for('web.operator_session_capture', session_id=session_id))


@web_bp.route('/operator/sessions/<session_id>/validation', methods=['GET', 'POST'])
@require_role('station_operator')
def operator_session_validation(session_id):
    """
    V3 demo Human Validation (editable counts). POST validates the Operator's values and
    redirects to the Validation Summary with them in the query string; nothing is written
    (no DB, cookie or session). RF/UUID vision sessions with a snapshot use vision-validation.
    """
    from services.rf_session import get_vision_snapshot
    if not use_v3_fixture(session_id):
        if get_vision_snapshot(session_id):
            return redirect(url_for('web.operator_session_vision_validation', session_id=session_id))
        return redirect(url_for('web.operator_session_capture', session_id=session_id))
    if request.method == 'POST':
        scenario = request.values.get('scenario')
        state, errors = operator_v3_parse_validation(request.form, scenario, strict=True)
        if state is None:
            state, errors = None, ['Submit the Human Validation form to continue.']
        if errors:
            response = _v3_operator_flow_page(
                'operator/sessions/validation.html', session_id, 'in_progress',
                validation=state, form_errors=errors,
            )
            return response if not isinstance(response, str) else (response, 400)
        view, response = _v3_operator_view_or_redirect(session_id, state)
        if response is not None:
            return response
        return redirect(_v3_flow_for(view)['validation_summary'])
    return _v3_operator_flow_page('operator/sessions/validation.html', session_id, 'in_progress')


@web_bp.route('/operator/sessions/<session_id>/validation-summary')
@require_role('station_operator')
def operator_session_validation_summary(session_id):
    if use_v3_fixture(session_id):
        return _v3_operator_flow_page('operator/sessions/validation_summary.html', session_id, 'in_progress')
    return redirect(url_for('web.operator_session_details', session_id=session_id))


@web_bp.route('/operator/sessions/<session_id>/discrepancy', methods=['GET', 'POST'])
@require_role('station_operator')
def operator_session_discrepancy(session_id):
    """V3 demo Discrepancy Escalation (?state=prepared|escalated); RF → session details."""
    if not use_v3_fixture(session_id):
        return redirect(url_for('web.operator_session_details', session_id=session_id))
    view, response = _v3_operator_view_or_redirect(session_id)
    if response is not None:
        return response
    flow = _v3_flow_for(view)
    if view['is_clean']:
        # The clean projection has nothing to escalate.
        return redirect(flow['validation_summary'])
    if request.method == 'POST':
        # Demo escalation: the Operator note is only carried to the success page in the URL;
        # it is not persisted and nothing is written to the DB.
        notes = operator_v3_escalation_notes(request.form.get('operator_notes'))
        return redirect(_v3_op_url(
            'web.operator_session_discrepancy', view['session']['session_id'], view['base_scenario'],
            view['validation_state'], state='escalated', **({'on': notes} if notes else {}),
        ))
    state = 'escalated' if request.args.get('state') == 'escalated' else 'prepared'
    return _v3_operator_flow_page(
        'operator/sessions/discrepancy.html', session_id,
        'escalated' if state == 'escalated' else 'escalation_prepared',
        validation=view['validation_state'], escalation_state=state,
        escalation_notes=operator_v3_escalation_notes(request.args.get('on')) if state == 'escalated' else '',
    )


@web_bp.route('/operator/sessions/<session_id>/awaiting-review')
@require_role('station_operator')
def operator_session_awaiting_review(session_id):
    return _page('operator/sessions/awaiting_review.html', session_id=session_id)


@web_bp.route('/operator/sessions/<session_id>/correction')
@require_role('station_operator')
def operator_session_correction(session_id):
    return _page('operator/sessions/correction_requested.html', session_id=session_id)


@web_bp.route('/operator/sessions/<session_id>/ready-to-close')
@require_role('station_operator')
def operator_session_ready_to_close(session_id):
    # RF: Operator does not close — SPD confirms. V2 ready_to_close lives under Legacy/.
    if not use_v3_fixture(session_id):
        return redirect(url_for('web.operator_session_details', session_id=session_id))
    view, response = _v3_operator_view_or_redirect(session_id)
    if response is not None:
        return response
    if not view['is_clean']:
        # Ready to Close is the clean path only; escalated sessions close from Escalation.
        return redirect(_v3_flow_for(view)['validation_summary'])
    return _v3_operator_flow_page('operator/sessions/ready_to_close.html', session_id, 'ready_to_close',
                                  validation=view['validation_state'])


@web_bp.route('/operator/sessions/<session_id>/close', methods=['POST'])
@require_role('station_operator')
def operator_session_v3_close(session_id):
    """
    V3 demo Operator close only. RF sessions never close from Operator UI
    (confirm_spd_close on the Supervisor RF path). Non-fixture ids → 404.
    """
    if not use_v3_fixture(session_id):
        abort(404)
    view, response = _v3_operator_view_or_redirect(session_id)
    if response is not None:
        return response
    return redirect(url_for(
        'web.operator_sessions', closed=view['session']['session_id'], outcome=view['scenario'],
    ))


@web_bp.route('/operator/sessions/closed/<session_id>')
@require_role('station_operator')
def operator_session_closed(session_id):
    user = _current_user()
    detail = operator_session_detail(session_id, user['id'])
    if detail is not None:
        return _page(
            'operator/sessions/details.html',
            session=detail,
            session_id=session_id,
            active_nav_item='session_history',
        )
    session_data = next((item for item in sessions_data() if item['id'] == session_id), None)
    if session_data is None:
        return _page(
            'operator/sessions/closed_details.html',
            session_id=session_id,
            active_nav_item='session_history',
        )
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
        active_nav_item='session_history',
    )


@web_bp.route('/supervisor/profile')
@require_role('spd_supervisor')
def supervisor_profile():
    return _page('shared/profile.html', profile_role='supervisor')


# ----------------------------------------------------------------------------------------
# Supervisor V3 (ui_reference_v3/supervisor). Each route below takes the V3 fixture branch
# only when use_v3_fixture() allows it (demo mode for lists; explicit WS-0xx demo ids for
# details). UUID-backed sessions with demo mode off keep the RF branch unchanged
# (rf_details.html / rf_review.html / RF POSTs / services.rf_session). V3 demo actions are
# navigation-only: they never write RF state.
# ----------------------------------------------------------------------------------------

V3_SESSION_DETAIL_TABS = ('overview', 'counts', 'activity')
V3_REVIEW_TABS = ('overview', 'evidence', 'resolution', 'final')


def _v3_supervisor_page(template, *, demo, **values):
    """Render a Supervisor V3 template; demo pages show the presentation identity only."""
    if demo:
        values.setdefault('current_user', v3_supervisor_display_user(_current_user()))
    return _page(template, v3_demo=demo, **values)


def _v3_tab(value, tabs):
    return value if value in tabs else tabs[0]


def _v3_case_keys(source, name):
    allowed = set(supervisor_v3_review_case_keys())
    return [key for key in (source.get(name) or '').split(',') if key in allowed]


def _v3_review_url(session_id, tab, states, case=None, **extra):
    resolved = [key for key, code in states.items() if code == 'resolved']
    unresolved = [key for key, code in states.items() if code == 'reviewed_unresolved']
    params = {'tab': tab}
    if case:
        params['case'] = case
    if resolved:
        params['resolved'] = ','.join(resolved)
    if unresolved:
        params['unresolved'] = ','.join(unresolved)
    params.update(extra)
    return url_for('web.supervisor_discrepancy_review', session_id=session_id, **params)


@web_bp.route('/supervisor/dashboard')
@require_role('spd_supervisor')
def supervisor_dashboard():
    stats, sessions, _ = dashboard_data('supervisor')
    return _v3_supervisor_page(
        'supervisor/dashboard.html', demo=False,
        dashboard=supervisor_v3_dashboard_from_rf(stats, sessions),
    )


@web_bp.route('/supervisor/sessions')
@require_role('spd_supervisor')
def supervisor_sessions():
    from controllers.rf_ui import active_session_rows

    rf_rows = active_session_rows(sessions_data())
    rows = supervisor_v3_rows_from_rf(rf_rows)
    page_data = paginate_list(rows, page=request.args.get('page', 1), per_page=request.args.get('per_page', 20))
    return _v3_supervisor_page(
        'supervisor/sessions/list.html', demo=False,
        session_rows=page_data['rows'],
        list_meta={
            'total': page_data['total'],
            'pages': page_data['total_pages'],
            'page': page_data['page'],
            'has_previous': page_data['has_previous'],
            'has_next': page_data['has_next'],
            'previous_url': url_for('web.supervisor_sessions', page=page_data['page'] - 1) if page_data['has_previous'] else None,
            'next_url': url_for('web.supervisor_sessions', page=page_data['page'] + 1) if page_data['has_next'] else None,
        },
        filter_options=supervisor_rf_filter_options(sessions_data()),
        current_page=page_data['page'],
        total_pages=page_data['total_pages'],
        previous_url=url_for('web.supervisor_sessions', page=page_data['page'] - 1) if page_data['has_previous'] else None,
        next_url=url_for('web.supervisor_sessions', page=page_data['page'] + 1) if page_data['has_next'] else None,
        go_to_action=url_for('web.supervisor_sessions'),
        pagination_query={},
    )


def _vision_health_row():
    from services.vision_bridge import worker_health
    health = worker_health()
    if health.get('ok'):
        weights = 'weights OK' if health.get('weights_present') else 'weights MISSING'
        return {
            'name': 'Vision worker',
            'status': 'up',
            'detail': f"Local :5002 · {weights} · bound={health.get('bound_sessions', 0)}",
        }
    return {
        'name': 'Vision worker',
        'status': 'down',
        'detail': health.get('error') or 'Unreachable — start apps/VisionWorker',
    }


@web_bp.route('/supervisor/services-health')
@require_role('spd_supervisor')
def supervisor_services_health():
    """Future microservices health — stub for sidebar placement only."""
    return _page(
        'supervisor/services_health.html',
        services=[
            {'name': 'Auth', 'status': 'direct', 'detail': 'Local Auth service (no MS mesh yet)'},
            {'name': 'Web / Session', 'status': 'direct', 'detail': 'Monolith BackendWebFlask'},
            _vision_health_row(),
            {'name': 'Evidence worker', 'status': 'pending', 'detail': 'Mongo/Garage evidence cycle — deferred'},
        ],
    )


@web_bp.route('/supervisor/sessions/new', methods=['GET', 'POST'])
@require_role('spd_supervisor')
def supervisor_session_new():
    """RF-SP-02 schedule session + optional Via A privacy confirm (no V3 demo wizard)."""
    from datetime import datetime as dt

    from controllers.rf_ui import initial_phase_code_for_procedure
    from services.rf_session import (
        RfSessionError,
        confirm_privacy_via_a,
        kit_expected_lines,
        schedule_form_options,
        schedule_session,
    )

    user = _current_user()
    try:
        institution_id = UUID(user['institution_id'])
    except (TypeError, ValueError, KeyError):
        abort(400)
    options = schedule_form_options(institution_id)
    selected_kit = (request.values.get('kit_id') or '').strip()
    expected_lines = []
    if selected_kit:
        try:
            expected_lines = kit_expected_lines(UUID(selected_kit))
        except (TypeError, ValueError):
            selected_kit = ''
            expected_lines = []

    def _schedule_form_values():
        return {
            'operator_user_id': request.form.get('operator_user_id', ''),
            'procedure_type_id': request.form.get('procedure_type_id', ''),
            'room_id': request.form.get('room_id', ''),
            'station_id': request.form.get('station_id', ''),
            'patient_id': request.form.get('patient_id', ''),
            'scheduled_at': request.form.get('scheduled_at', ''),
        }

    if request.method == 'POST':
        action = request.form.get('action') or 'schedule'
        try:
            if action == 'confirm_privacy':
                session_id = request.form.get('session_id', '').strip()
                confirm_privacy_via_a(
                    session_id=UUID(session_id),
                    supervisor_user_id=UUID(user['id']),
                    institution_id=institution_id,
                    privacy_notice_version_id=UUID(request.form.get('privacy_notice_version_id')),
                    purpose_model_improvement=request.form.get('purpose_model_improvement') == '1',
                    ip=request.remote_addr,
                )
                return redirect(url_for('web.supervisor_session_details', session_id=session_id))

            scheduled_raw = request.form.get('scheduled_at', '').strip()
            scheduled_at = dt.fromisoformat(scheduled_raw)
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

            family_ids = request.form.getlist('family_id[]')
            quantities = request.form.getlist('expected_quantity[]')
            lines = []
            for family_id, qty in zip(family_ids, quantities):
                lines.append({'family_id': family_id, 'expected_quantity': int(qty)})

            physician_ids = request.form.getlist('physician_id[]')
            role_codes = request.form.getlist('surgical_role_code[]')
            physicians = []
            for pid, role_code in zip(physician_ids, role_codes):
                if not pid:
                    continue
                physicians.append({
                    'physician_id': pid,
                    'surgical_role_code': role_code or 'surgeon',
                })
            legacy_physician = request.form.get('physician_id')
            if not physicians and legacy_physician:
                physicians = [{
                    'physician_id': legacy_physician,
                    'surgical_role_code': 'surgeon',
                }]

            procedure_type_id = UUID(request.form.get('procedure_type_id'))
            result = schedule_session(
                supervisor_user_id=UUID(user['id']),
                institution_id=institution_id,
                operator_user_id=UUID(request.form.get('operator_user_id')),
                procedure_type_id=procedure_type_id,
                room_id=UUID(request.form.get('room_id')),
                station_id=UUID(request.form.get('station_id')),
                kit_id=UUID(request.form.get('kit_id')),
                patient_id=UUID(request.form.get('patient_id')) if request.form.get('patient_id') else None,
                physicians=physicians,
                scheduled_at=scheduled_at,
                phase_code=initial_phase_code_for_procedure(procedure_type_id),
                expected_lines=lines,
                ip=request.remote_addr,
            )
            if request.form.get('confirm_privacy') == '1' and request.form.get('privacy_notice_version_id'):
                if request.form.get('privacy_confirm_checkbox') == '1':
                    confirm_privacy_via_a(
                        session_id=UUID(result['session_id']),
                        supervisor_user_id=UUID(user['id']),
                        institution_id=institution_id,
                        privacy_notice_version_id=UUID(request.form.get('privacy_notice_version_id')),
                        purpose_model_improvement=request.form.get('purpose_model_improvement') == '1',
                        ip=request.remote_addr,
                    )
            return redirect(url_for('web.supervisor_session_details', session_id=result['session_id']))
        except (RfSessionError, ValueError, TypeError) as exc:
            message = exc.message if isinstance(exc, RfSessionError) else str(exc)
            status = exc.status_code if isinstance(exc, RfSessionError) else 400
            post_kit = (request.form.get('kit_id') or '').strip()
            post_lines = expected_lines
            if post_kit:
                try:
                    post_lines = kit_expected_lines(UUID(post_kit))
                except (TypeError, ValueError):
                    post_kit = selected_kit
                    post_lines = expected_lines
            return _page(
                'supervisor/sessions/schedule.html',
                **options,
                expected_lines=post_lines,
                selected_kit=post_kit,
                form_values=_schedule_form_values(),
                form_error=message,
            ), status

    return _page(
        'supervisor/sessions/schedule.html',
        **options,
        expected_lines=expected_lines,
        selected_kit=selected_kit,
        form_values={},
    )


@web_bp.route('/supervisor/sessions/stock-recheck', methods=['POST'])
@require_role('spd_supervisor')
def supervisor_session_stock_recheck():
    """Stock semaphore recheck for schedule qty edits (JSON or form-urlencoded)."""
    from services.rf_session import RfSessionError, stock_recheck_lines

    lines = []
    try:
        payload = request.get_json(silent=True) or {}
        raw_lines = payload.get('lines') or []
        if raw_lines:
            for item in raw_lines:
                lines.append({
                    'family_id': item['family_id'],
                    'expected_quantity': int(item.get('expected_quantity') or 0),
                })
        else:
            family_ids = request.form.getlist('family_id[]')
            quantities = request.form.getlist('expected_quantity[]')
            for family_id, qty in zip(family_ids, quantities):
                if not family_id:
                    continue
                lines.append({
                    'family_id': family_id,
                    'expected_quantity': int(qty or 0),
                })
        return jsonify({'lines': stock_recheck_lines(lines)})
    except (RfSessionError, KeyError, TypeError, ValueError) as exc:
        message = exc.message if isinstance(exc, RfSessionError) else 'Invalid stock recheck payload.'
        return jsonify({'error': message}), 400


@web_bp.route('/supervisor/sessions/<session_id>/privacy', methods=['POST'])
@require_role('spd_supervisor')
def supervisor_session_privacy(session_id):
    from services.rf_session import RfSessionError, confirm_privacy_via_a

    user = _current_user()
    if request.form.get('privacy_confirm_checkbox') != '1':
        return redirect(url_for('web.supervisor_session_details', session_id=session_id))
    try:
        confirm_privacy_via_a(
            session_id=UUID(session_id),
            supervisor_user_id=UUID(user['id']),
            institution_id=UUID(user['institution_id']) if user.get('institution_id') else None,
            privacy_notice_version_id=UUID(request.form.get('privacy_notice_version_id')),
            purpose_model_improvement=request.form.get('purpose_model_improvement') == '1',
            ip=request.remote_addr,
        )
    except RfSessionError:
        pass
    return redirect(url_for('web.supervisor_session_details', session_id=session_id))


@web_bp.route('/supervisor/sessions/history')
@require_role('spd_supervisor')
def supervisor_session_history():
    from controllers.rf_ui import past_session_rows

    source = past_session_rows(sessions_data(), for_role='supervisor')
    all_rows = supervisor_v3_rows_from_rf(source)
    search = (request.args.get('search') or '').strip().lower()
    review_status = (request.args.get('review_status') or '').strip()
    kit = (request.args.get('kit') or '').strip()
    operator = (request.args.get('operator') or '').strip()
    date_filter = (request.args.get('date') or '').strip()
    today = date.today().isoformat()

    filtered = []
    for row in all_rows:
        if search:
            hay = ' '.join([
                str(row.get('session_id') or ''),
                str(row.get('procedure_label') or ''),
                str(row.get('kit_name') or ''),
                str(row.get('operator_name') or ''),
            ]).lower()
            if search not in hay:
                continue
        review_code = (row.get('review_status') or {}).get('code') or ''
        if review_status and review_code != review_status:
            continue
        if kit and row.get('kit_name') != kit:
            continue
        if operator and row.get('operator_name') != operator:
            continue
        closed = str(row.get('closed_time') or '')
        started = str(row.get('started_time') or '')
        day = (closed or started)[:10]
        if date_filter == 'Today' and day != today:
            continue
        if date_filter == 'Last 7 Days' and day and len(day) == 10:
            cutoff = (date.today() - timedelta(days=7)).isoformat()
            if day < cutoff:
                continue
        if date_filter == 'Last 30 Days' and day and len(day) == 10:
            cutoff = (date.today() - timedelta(days=30)).isoformat()
            if day < cutoff:
                continue
        filtered.append(row)

    per_page = 20
    page = max(1, int(request.args.get('page') or 1))
    total = len(filtered)
    pages = max(1, (total + per_page - 1) // per_page)
    if page > pages:
        page = pages
    start = (page - 1) * per_page
    page_rows = filtered[start:start + per_page]
    query_args = {
        'search': request.args.get('search', ''),
        'review_status': review_status,
        'kit': kit,
        'operator': operator,
        'date': date_filter,
    }
    previous_url = (
        url_for('web.supervisor_session_history', page=page - 1, **query_args)
        if page > 1 else None
    )
    next_url = (
        url_for('web.supervisor_session_history', page=page + 1, **query_args)
        if page < pages else None
    )
    return _v3_supervisor_page(
        'supervisor/sessions/history.html', demo=False,
        session_rows=page_rows,
        list_meta={'total': total, 'pages': pages, 'page': page},
        filter_options=supervisor_rf_filter_options(source),
        list_filters=query_args,
        previous_url=previous_url,
        next_url=next_url,
        current_page=page,
        total_pages=pages,
        go_to_action=url_for('web.supervisor_session_history'),
        pagination_query=query_args,
    )


@web_bp.route('/supervisor/sessions/<session_id>')
@require_role('spd_supervisor')
def supervisor_session_details(session_id):
    if use_v3_fixture(session_id):
        view = supervisor_v3_view(session_id)
        if view is None:
            abort(404)
        tab = _v3_tab(request.args.get('tab'), V3_SESSION_DETAIL_TABS)
        return _v3_supervisor_page(
            'supervisor/sessions/details.html', demo=True,
            v3=view, tab=tab, session_id=view['session']['session_id'],
            tab_urls={name: url_for('web.supervisor_session_details', session_id=view['session']['session_id'], tab=name)
                      for name in V3_SESSION_DETAIL_TABS},
        )
    detail = supervisor_session_detail(session_id)
    if detail is None:
        abort(404)
    from services.rf_session import schedule_form_options

    user = _current_user()
    families = []
    try:
        families = schedule_form_options(UUID(user['institution_id'])).get('families', [])
    except (TypeError, ValueError, KeyError):
        families = []
    return _page(
        'supervisor/sessions/rf_details.html',
        session=detail,
        session_id=session_id,
        inventory_families=families,
        inventory_view=request.args.get('inventory_view', 'table'),
        form_error=request.args.get('error'),
    )


@web_bp.route('/supervisor/sessions/<session_id>/inventory', methods=['GET', 'POST'])
@require_role('spd_supervisor')
def supervisor_session_inventory(session_id):
    from datetime import datetime as dt

    from services.rf_session import (
        RfSessionError,
        schedule_form_options,
        update_scheduled_session,
        update_session_inventory,
    )

    user = _current_user()
    detail = supervisor_session_detail(session_id)
    if detail is None:
        abort(404)
    if not detail.get('can_edit_inventory'):
        return redirect(url_for('web.supervisor_session_details', session_id=session_id))

    try:
        institution_id = UUID(user['institution_id']) if user.get('institution_id') else None
        options = schedule_form_options(institution_id) if institution_id else {}
    except (TypeError, ValueError, KeyError):
        options = {}

    def _render_edit(form_error=None, status=200):
        return _page(
            'supervisor/sessions/inventory_edit.html',
            session=detail,
            session_id=session_id,
            families=options.get('families', []),
            operators=options.get('operators', []),
            procedures=options.get('procedures', []),
            rooms=options.get('rooms', []),
            stations=options.get('stations', []),
            kits=options.get('kits', []),
            patients=options.get('patients', []),
            physicians=options.get('physicians', []),
            surgical_roles=options.get('surgical_roles', []),
            form_error=form_error,
        ), status

    if request.method == 'POST':
        family_ids = request.form.getlist('family_id[]')
        quantities = request.form.getlist('expected_quantity[]')
        lines = []
        for family_id, qty in zip(family_ids, quantities):
            if not family_id:
                continue
            lines.append({'family_id': family_id, 'expected_quantity': int(qty)})
        try:
            if detail.get('can_edit_session_fields') and request.form.get('edit_mode') == 'full':
                physician_ids = request.form.getlist('physician_id[]')
                role_codes = request.form.getlist('surgical_role_code[]')
                physicians = []
                for pid, role_code in zip(physician_ids, role_codes):
                    if not pid:
                        continue
                    physicians.append({
                        'physician_id': pid,
                        'surgical_role_code': role_code or 'surgeon',
                    })
                scheduled_raw = request.form.get('scheduled_at', '').strip()
                scheduled_at = dt.fromisoformat(scheduled_raw)
                if scheduled_at.tzinfo is None:
                    scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
                update_scheduled_session(
                    session_id=UUID(session_id),
                    supervisor_user_id=UUID(user['id']),
                    institution_id=institution_id,
                    operator_user_id=UUID(request.form.get('operator_user_id')),
                    procedure_type_id=UUID(request.form.get('procedure_type_id')),
                    room_id=UUID(request.form.get('room_id')),
                    station_id=UUID(request.form.get('station_id')),
                    kit_id=UUID(request.form.get('kit_id')),
                    patient_id=UUID(request.form.get('patient_id')) if request.form.get('patient_id') else None,
                    physicians=physicians,
                    scheduled_at=scheduled_at,
                    expected_lines=lines,
                    ip=request.remote_addr,
                )
            else:
                update_session_inventory(
                    session_id=UUID(session_id),
                    supervisor_user_id=UUID(user['id']),
                    institution_id=institution_id,
                    lines=lines,
                    ip=request.remote_addr,
                )
        except (RfSessionError, ValueError, TypeError) as exc:
            message = exc.message if isinstance(exc, RfSessionError) else str(exc)
            return _render_edit(form_error=message, status=400)
        return redirect(url_for('web.supervisor_session_details', session_id=session_id))

    return _render_edit()[0]


@web_bp.route('/supervisor/sessions/<session_id>/close', methods=['POST'])
@require_role('spd_supervisor')
def supervisor_session_close(session_id):
    from services.rf_session import RfSessionError, confirm_spd_close

    user = _current_user()
    try:
        confirm_spd_close(
            session_id=UUID(session_id),
            supervisor_user_id=UUID(user['id']),
            institution_id=UUID(user['institution_id']) if user.get('institution_id') else None,
            material_recovered=request.form.get('material_recovered') == '1',
            ip=request.remote_addr,
        )
    except RfSessionError as exc:
        detail = supervisor_session_detail(session_id)
        return _page(
            'supervisor/sessions/rf_details.html',
            session=detail,
            session_id=session_id,
            form_error=exc.message,
        ), exc.status_code
    return redirect(url_for('web.supervisor_session_details', session_id=session_id))


@web_bp.route('/supervisor/discrepancies')
@require_role('spd_supervisor')
def supervisor_discrepancies():
    if use_v3_fixture():
        return _v3_supervisor_page(
            'supervisor/discrepancies/list.html', demo=True,
            queue_rows=supervisor_v3_review_queue(),
            queue_summary=supervisor_v3_review_summary(),
            filter_options=supervisor_v3_filter_options(),
            feedback_banner=supervisor_v3_validated_banner(request.args.get('validated')),
            discrepancy_filters={'search': '', 'instrument': '', 'status': ''},
        )
    filters = {
        'search': request.args.get('search', ''),
        'instrument': request.args.get('instrument', ''),
        'status': request.args.get('status', ''),
    }
    discrepancies = discrepancies_data(
        search=filters['search'],
        instrument=filters['instrument'],
        status=filters['status'],
    )
    summary = discrepancies_summary_data(discrepancies_data())
    return _v3_supervisor_page(
        'supervisor/discrepancies/list.html', demo=False,
        queue_rows=supervisor_v3_queue_from_rf(discrepancies),
        queue_summary={
            'review_required': summary.get('pending_reviews', 0),
            'reviewed_unresolved': 0,
            'resolved_today': summary.get('reviewed_today', 0),
        },
        filter_options=supervisor_v3_filter_options(),
        discrepancy_filters=filters,
    )


V3_REVIEW_BANNERS = {
    'draft': {'variant': 'info', 'title': 'Draft saved.',
              'description': 'Demo only: the resolution draft is not persisted.'},
    'notes_required': {'variant': 'warning',
                       'title': 'Review notes are required to mark a case as Reviewed – Unresolved.',
                       'description': ''},
}


def _v3_supervisor_review(session_id):
    """V3 Review Detail (demo). POST actions only change the projection carried in the URL."""
    source = request.form if request.method == 'POST' else request.args
    outcome = request.args.get('outcome') if request.method == 'GET' else None
    view = supervisor_v3_view(
        session_id, outcome,
        resolved=_v3_case_keys(source, 'resolved'),
        unresolved=_v3_case_keys(source, 'unresolved'),
    )
    if view is None:
        abort(404)
    sid = view['session']['session_id']
    states = dict(view['case_states'])
    case_keys = [case['case_key'] for case in view['review_cases'] if case['case_key']]

    if request.method == 'POST':
        if not view['is_full_case']:
            return redirect(url_for('web.supervisor_discrepancy_review', session_id=sid))
        action = request.form.get('v3_action')
        case = request.form.get('case') if request.form.get('case') in case_keys else None
        if action in ('resolve', 'mark_unresolved') and case:
            if action == 'mark_unresolved' and not (request.form.get('review_notes') or '').strip():
                return redirect(_v3_review_url(sid, 'resolution', states, case=case, notice='notes_required'))
            states[case] = 'resolved' if action == 'resolve' else 'reviewed_unresolved'
            pending = [key for key in case_keys if key not in states]
            if pending:
                return redirect(_v3_review_url(sid, 'resolution', states, case=pending[0]))
            return redirect(_v3_review_url(sid, 'final', states))
        if action == 'save_draft':
            return redirect(_v3_review_url(sid, 'resolution', states, case=case, notice='draft'))
        if action == 'confirm_resolution':
            acknowledged = all(request.form.get(f'ack_{index}') == '1' for index in (1, 2, 3))
            if view['final_validation']['ready'] and acknowledged:
                return redirect(url_for('web.supervisor_discrepancies', validated=sid))
            return redirect(_v3_review_url(sid, 'final', states))
        return redirect(_v3_review_url(sid, 'overview', states))

    tab = _v3_tab(request.args.get('tab'), V3_REVIEW_TABS)
    selected_key = request.args.get('case')
    if selected_key not in case_keys:
        pending = [key for key in case_keys if key not in states]
        selected_key = pending[0] if pending else (case_keys[0] if case_keys else None)
    selected_case = next((case for case in view['review_cases'] if case['case_key'] == selected_key),
                         view['review_cases'][0] if view['review_cases'] else None)
    return _v3_supervisor_page(
        'supervisor/discrepancies/review.html', demo=True,
        v3=view, tab=tab, session_id=sid,
        selected_case=selected_case,
        tab_urls={name: _v3_review_url(sid, name, states, case=selected_key) for name in V3_REVIEW_TABS},
        case_urls={key: _v3_review_url(sid, 'resolution', states, case=key) for key in case_keys},
        final_url=_v3_review_url(sid, 'final', states),
        form_action=url_for('web.supervisor_discrepancy_review', session_id=sid),
        state_fields={
            'resolved': ','.join(key for key, code in states.items() if code == 'resolved'),
            'unresolved': ','.join(key for key, code in states.items() if code == 'reviewed_unresolved'),
        },
        feedback_banner=V3_REVIEW_BANNERS.get(request.args.get('notice')),
    )


@web_bp.route('/supervisor/discrepancies/<session_id>/review', methods=['GET', 'POST'])
@require_role('spd_supervisor')
def supervisor_discrepancy_review(session_id):
    from services.rf_session import RfSessionError, resolve_discrepancy

    if use_v3_fixture(session_id):
        return _v3_supervisor_review(session_id)

    detail = supervisor_session_detail(session_id)
    if detail is None:
        abort(404)

    if request.method == 'POST':
        user = _current_user()
        resolution = (request.form.get('resolution_outcome') or '').strip().lower()
        mark_lost = resolution == 'lost' or request.form.get('mark_lost') == '1'
        mark_recovered = resolution == 'recovered' or request.form.get('mark_recovered') == '1'
        try:
            resolve_discrepancy(
                discrepancy_id=UUID(request.form.get('discrepancy_id')),
                supervisor_user_id=UUID(user['id']),
                institution_id=UUID(user['institution_id']) if user.get('institution_id') else None,
                notes=request.form.get('notes') or '',
                mark_lost=mark_lost,
                mark_recovered=mark_recovered,
                ip=request.remote_addr,
            )
        except (RfSessionError, ValueError, TypeError) as exc:
            message = exc.message if isinstance(exc, RfSessionError) else str(exc)
            detail = supervisor_session_detail(session_id)
            return _page(
                'supervisor/sessions/rf_review.html',
                session=detail,
                session_id=session_id,
                form_error=message,
            ), 400
        return redirect(url_for('web.supervisor_discrepancy_review', session_id=session_id))

    return _page('supervisor/sessions/rf_review.html', session=detail, session_id=session_id)


@web_bp.route('/supervisor/reports')
@require_role('spd_supervisor')
def supervisor_reports():
    # No report generation backend exists yet: both modes render the centralized V3
    # presentation data (previously the same role was played by template-local fallbacks).
    return _v3_supervisor_page(
        'supervisor/reports.html', demo=use_v3_fixture(),
        reports=supervisor_v3_reports(), filter_options=supervisor_v3_filter_options(),
    )


@web_bp.route('/supervisor/indicators')
@require_role('spd_supervisor')
def supervisor_indicators():
    # Minimal PG aggregates for MVP; AI-agreement / resolution-time remain n/a stubs.
    return _v3_supervisor_page(
        'supervisor/indicators.html', demo=False,
        indicators=supervisor_indicators_from_rf(sessions_data(), discrepancies_data()),
    )


@web_bp.route('/supervisor/audit-log')
@require_role('spd_supervisor')
def supervisor_audit_log():
    # AccessAudit has no query in the V3 shape yet: both modes render rows built from the
    # centralized WS-026 timeline. See V2_IMPLEMENTATION_NOTES.md.
    return _v3_supervisor_page(
        'supervisor/audit_log.html', demo=use_v3_fixture(),
        audit_rows=supervisor_v3_audit_log(),
        list_meta=supervisor_v3_list_meta('audit_log'),
        filter_options=supervisor_v3_filter_options(),
    )


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
    from models.CatInstrumentCategory import CatInstrumentCategory

    if request.method == 'GET':
        ctx = family_form_data()
        ctx.update({
            'form_mode': 'create',
            'is_edit': False,
            'page_title': 'New Instrument Family',
            'save_url': url_for('web.admin_instrument_family_new'),
            'cancel_url': url_for('web.admin_instrument_families'),
        })
        return _page('admin/instrument_families/form.html', **ctx)

    values = {
        'code': request.form.get('code', '').strip(),
        'name': request.form.get('name', '').strip(),
        'category': request.form.get('category', '').strip(),
        'how_to_identify': request.form.get('how_to_identify', '').strip(),
        'classification_characteristics': request.form.get('classification_characteristics', '').strip(),
        'function': request.form.get('function', '').strip(),
        'status': request.form.get('status', 'active'),
    }
    if not values['code'] or not values['name'] or not values['category']:
        ctx = family_form_data()
        ctx['instrument_family'] = values
        ctx.update({
            'form_mode': 'create', 'is_edit': False,
            'save_url': url_for('web.admin_instrument_family_new'),
            'cancel_url': url_for('web.admin_instrument_families'),
            'form_error': 'Code, name and category are required.',
        })
        return _page('admin/instrument_families/form.html', **ctx), 400
    try:
        category_id = UUID(values['category'])
    except ValueError:
        ctx = family_form_data()
        ctx['instrument_family'] = values
        ctx.update({
            'form_mode': 'create', 'is_edit': False,
            'save_url': url_for('web.admin_instrument_family_new'),
            'cancel_url': url_for('web.admin_instrument_families'),
            'form_error': 'Choose a valid category.',
        })
        return _page('admin/instrument_families/form.html', **ctx), 400
    if db.session.get(CatInstrumentCategory, category_id) is None:
        abort(400)
    try:
        family = InstrumentFamily(
            code=values['code'],
            name=values['name'],
            category_id=category_id,
            identify_text=values['how_to_identify'] or None,
            classify_text=values['classification_characteristics'] or None,
            function_text=values['function'] or None,
            active=values['status'] == 'active',
        )
        db.session.add(family)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        ctx = family_form_data()
        ctx['instrument_family'] = values
        ctx.update({
            'form_mode': 'create', 'is_edit': False,
            'save_url': url_for('web.admin_instrument_family_new'),
            'cancel_url': url_for('web.admin_instrument_families'),
            'form_error': 'That family code already exists.',
        })
        return _page('admin/instrument_families/form.html', **ctx), 409
    except SQLAlchemyError:
        db.session.rollback()
        abort(503)
    return redirect(url_for('web.admin_instrument_families'))


@web_bp.route('/admin/instrument-families/<family_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_instrument_family_edit(family_id):
    from models.CatInstrumentCategory import CatInstrumentCategory

    try:
        parsed_id = UUID(family_id)
    except ValueError:
        abort(404)
    family = db.session.get(InstrumentFamily, parsed_id)
    if family is None:
        abort(404)

    if request.method == 'GET':
        ctx = family_form_data(parsed_id)
        ctx.update({
            'form_mode': 'edit',
            'is_edit': True,
            'page_title': 'Edit Instrument Family',
            'family_id': parsed_id,
            'save_url': url_for('web.admin_instrument_family_edit', family_id=parsed_id),
            'cancel_url': url_for('web.admin_instrument_families'),
        })
        return _page('admin/instrument_families/form.html', **ctx)

    values = {
        'code': family.code,
        'name': request.form.get('name', '').strip(),
        'category': request.form.get('category', '').strip(),
        'how_to_identify': request.form.get('how_to_identify', '').strip(),
        'classification_characteristics': request.form.get('classification_characteristics', '').strip(),
        'function': request.form.get('function', '').strip(),
        'status': request.form.get('status', 'active'),
    }
    if not values['name'] or not values['category']:
        ctx = family_form_data(parsed_id)
        ctx['instrument_family'] = values
        ctx.update({
            'form_mode': 'edit', 'is_edit': True, 'family_id': parsed_id,
            'save_url': url_for('web.admin_instrument_family_edit', family_id=parsed_id),
            'cancel_url': url_for('web.admin_instrument_families'),
            'form_error': 'Name and category are required.',
        })
        return _page('admin/instrument_families/form.html', **ctx), 400
    try:
        category_id = UUID(values['category'])
    except ValueError:
        abort(400)
    if db.session.get(CatInstrumentCategory, category_id) is None:
        abort(400)
    try:
        family.name = values['name']
        family.category_id = category_id
        family.identify_text = values['how_to_identify'] or None
        family.classify_text = values['classification_characteristics'] or None
        family.function_text = values['function'] or None
        family.active = values['status'] == 'active'
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        abort(503)
    return redirect(url_for('web.admin_instrument_families'))


def _list_query_args(filters, *, page=None, per_page=None):
    args = {}
    for key, value in (filters or {}).items():
        if value is None:
            continue
        text = str(value).strip()
        if text == '':
            continue
        args[key] = text
    if page is not None:
        args['page'] = page
    if per_page is not None:
        args['per_page'] = per_page
    return args


def _pagination_urls(endpoint, filters, current_page, total_pages, per_page):
    previous_url = None
    next_url = None
    if current_page > 1:
        previous_url = url_for(
            endpoint,
            **_list_query_args(filters, page=current_page - 1, per_page=per_page),
        )
    if current_page < total_pages:
        next_url = url_for(
            endpoint,
            **_list_query_args(filters, page=current_page + 1, per_page=per_page),
        )
    return previous_url, next_url


def _access_audit(*, user_id, action, resource_type, resource_id, institution_id=None):
    try:
        actor_user_id = UUID(str(user_id)) if user_id else None
    except (TypeError, ValueError):
        actor_user_id = None
    if actor_user_id is None:
        return
    try:
        institution_uuid = UUID(str(institution_id)) if institution_id else None
    except (TypeError, ValueError):
        institution_uuid = None
    db.session.add(AccessAudit(
        occurred_at=datetime.now(timezone.utc),
        actor_type='user',
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        institution_id=institution_uuid,
        outcome='success',
        ip=(request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip() or None,
    ))


@web_bp.route('/admin/instruments')
@require_role('spd_supervisor', 'it_admin')
def admin_instruments():
    payload = instruments_query_page(
        search=request.args.get('search', ''),
        type_code=request.args.get('type', ''),
        cycle_status=request.args.get('cycle_status', ''),
        status=request.args.get('status', 'active'),
        page=request.args.get('page', 1),
        per_page=request.args.get('per_page', 20),
    )
    filters = payload.get('filters', {})
    previous_url, next_url = _pagination_urls(
        'web.admin_instruments',
        filters,
        payload.get('current_page', 1),
        payload.get('total_pages', 1),
        payload.get('per_page', 20),
    )
    return _page(
        'admin/instruments/list.html',
        instruments=payload.get('instruments', []),
        instruments_shown_count=payload.get('instruments_shown_count', 0),
        instruments_total_count=payload.get('instruments_total_count', 0),
        current_page=payload.get('current_page', 1),
        total_pages=payload.get('total_pages', 1),
        per_page=payload.get('per_page', 20),
        filters=filters,
        instrument_type_filter=payload.get('instrument_type_filter', []),
        cycle_status_filter=payload.get('cycle_status_filter', []),
        previous_url=previous_url,
        next_url=next_url,
        go_to_action=url_for('web.admin_instruments'),
        pagination_query=_list_query_args(filters, per_page=payload.get('per_page', 20)),
        new_instrument_url=url_for('web.admin_instrument_new'),
    )


@web_bp.route('/admin/instruments/new', methods=['GET', 'POST'])
@require_role('spd_supervisor', 'it_admin')
def admin_instrument_new():
    if request.method == 'GET':
        return _render_instrument_form('create')

    values = _submitted_instrument_values()
    error = _validate_instrument_values(values, include_code=True)
    if error:
        return _render_instrument_form('create', values=values, form_error=error, status=400)

    user = _current_user()
    try:
        institution_id = UUID(user.get('institution_id') or '')
    except (AttributeError, TypeError, ValueError):
        return _render_instrument_form(
            'create', values=values,
            form_error='Your account must be assigned to an institution before creating an instrument.', status=400,
        )

    try:
        institution = db.session.get(Institution, institution_id)
        if institution is None:
            return _render_instrument_form(
                'create', values=values,
                form_error='Your assigned institution could not be found.', status=400,
            )
        family, cycle_status = _validated_instrument_catalog_values(values)
        if family is None or cycle_status is None:
            return _render_instrument_form(
                'create', values=values,
                form_error='Choose a valid instrument family and cycle status.', status=400,
            )

        instrument = Instrument(
            internal_code=values['internal_code'],
            family_id=family.id,
            cycle_status_id=cycle_status.id,
            institution_id=institution.id,
            active=values['active_status'] == 'active',
        )
        db.session.add(instrument)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _render_instrument_form(
            'create', values=values,
            form_error='An instrument with this internal code already exists for the institution.', status=409,
        )
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Failed to create admin instrument')
        return _render_instrument_form(
            'create', values=values,
            form_error='Unable to save this instrument right now.', status=503,
        )

    return redirect(url_for('web.admin_instruments'))


@web_bp.route('/admin/instruments/<instrument_id>/edit', methods=['GET', 'POST'])
@require_role('spd_supervisor', 'it_admin')
def admin_instrument_edit(instrument_id):
    try:
        parsed_instrument_id = UUID(instrument_id)
    except ValueError:
        abort(404)

    instrument = db.session.get(Instrument, parsed_instrument_id)
    if instrument is None:
        abort(404)
    if request.method == 'GET':
        return _render_instrument_form('edit', instrument_id=parsed_instrument_id)

    values = _submitted_instrument_values()
    error = _validate_instrument_values(values)
    if error:
        return _render_instrument_form(
            'edit', instrument_id=parsed_instrument_id, values=values,
            form_error=error, status=400,
        )

    try:
        family, cycle_status = _validated_instrument_catalog_values(values)
        if family is None or cycle_status is None:
            return _render_instrument_form(
                'edit', instrument_id=parsed_instrument_id, values=values,
                form_error='Choose a valid instrument family and cycle status.', status=400,
            )

        prior_cycle = db.session.get(CatInstrumentCycleStatus, instrument.cycle_status_id)
        prior_cycle_code = prior_cycle.code if prior_cycle else ''
        prior_active = bool(instrument.active)
        new_active = values['active_status'] == 'active'
        cycle_changed = instrument.cycle_status_id != cycle_status.id
        active_changed = prior_active != new_active

        if (cycle_changed or active_changed) and not values['change_reason']:
            return _render_instrument_form(
                'edit', instrument_id=parsed_instrument_id, values=values,
                form_error='A change reason is required when cycle status or active status changes.',
                status=400,
            )

        instrument.family_id = family.id
        instrument.cycle_status_id = cycle_status.id
        instrument.active = new_active

        if cycle_changed or active_changed:
            note_parts = []
            if cycle_changed:
                note_parts.append(f'cycle: {prior_cycle_code or "unknown"}→{cycle_status.code}')
            if active_changed:
                prior_label = 'active' if prior_active else 'inactive'
                new_label = 'active' if new_active else 'inactive'
                note_parts.append(f'active: {prior_label}→{new_label}')
            note_parts.append(f'reason: {values["change_reason"]}')
            db.session.add(InstrumentCycleEvent(
                instrument_id=instrument.id,
                cycle_status_id=cycle_status.id,
                occurred_at=datetime.now(timezone.utc),
                notes='; '.join(note_parts),
            ))
            user = _current_user() or {}
            if cycle_changed:
                _access_audit(
                    user_id=user.get('id'),
                    action='instrument.cycle_change',
                    resource_type='instrument',
                    resource_id=instrument.id,
                    institution_id=user.get('institution_id') or instrument.institution_id,
                )
            if active_changed:
                _access_audit(
                    user_id=user.get('id'),
                    action='instrument.active_change',
                    resource_type='instrument',
                    resource_id=instrument.id,
                    institution_id=user.get('institution_id') or instrument.institution_id,
                )

        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _render_instrument_form(
            'edit', instrument_id=parsed_instrument_id, values=values,
            form_error='Unable to save this instrument because it conflicts with existing data.', status=409,
        )
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Failed to update admin instrument %s', parsed_instrument_id)
        return _render_instrument_form(
            'edit', instrument_id=parsed_instrument_id, values=values,
            form_error='Unable to save this instrument right now.', status=503,
        )

    return redirect(url_for('web.admin_instruments'))


def _submitted_instrument_values():
    return {
        'internal_code': request.form.get('internal_code', '').strip(),
        'instrument_family': request.form.get('instrument_family', '').strip(),
        'cycle_status': request.form.get('cycle_status', '').strip(),
        'active_status': request.form.get('active_status', 'active'),
        'change_reason': request.form.get('change_reason', '').strip(),
    }


def _validate_instrument_values(values, include_code=False):
    if include_code and not values['internal_code']:
        return 'Internal code is required.'
    if include_code and len(values['internal_code']) > 64:
        return 'Internal code must contain at most 64 characters.'
    if not values['instrument_family'] or not values['cycle_status']:
        return 'Instrument family and cycle status are required.'
    if values['active_status'] not in {'active', 'inactive'}:
        return 'Choose a valid active status.'
    return None


def _validated_instrument_catalog_values(values):
    try:
        family_id = UUID(values['instrument_family'])
    except (TypeError, ValueError):
        return None, None
    family = db.session.get(InstrumentFamily, family_id)
    cycle_status = db.session.scalar(
        select(CatInstrumentCycleStatus).where(
            CatInstrumentCycleStatus.code == values['cycle_status']
        )
    )
    return family, cycle_status


def _render_instrument_form(form_mode, instrument_id=None, values=None, form_error=None, status=200):
    is_edit = form_mode == 'edit'
    context = instrument_form_data(instrument_id)
    if values:
        instrument_values = dict(context.get('instrument', {}))
        instrument_values.update(values)
        if is_edit:
            instrument_values['internal_code'] = context.get('instrument', {}).get('internal_code', '')
        context['instrument'] = instrument_values
    context.update({
        'form_mode': form_mode,
        'is_edit': is_edit,
        'page_title': 'Edit Instrument' if is_edit else 'New Instrument',
        'save_url': url_for('web.admin_instrument_edit', instrument_id=instrument_id) if is_edit else url_for('web.admin_instrument_new'),
        'cancel_url': url_for('web.admin_instruments'),
        'form_error': form_error,
    })
    return _page('admin/instruments/form.html', **context), status


@web_bp.route('/admin/kits')
@require_role('spd_supervisor', 'it_admin')
def admin_kits():
    user = _current_user() or {}
    active_only = user.get('role_code') == 'spd_supervisor'
    payload = kits_query_page(
        search=request.args.get('search', ''),
        procedure_type=request.args.get('procedure_type', ''),
        status=request.args.get('status', 'active'),
        page=request.args.get('page', 1),
        per_page=request.args.get('per_page', 20),
        active_only=active_only,
    )
    filters = payload.get('filters', {})
    previous_url, next_url = _pagination_urls(
        'web.admin_kits',
        filters,
        payload.get('current_page', 1),
        payload.get('total_pages', 1),
        payload.get('per_page', 20),
    )
    return _page(
        'admin/kits/list.html',
        kits=payload.get('kits', []),
        kits_shown_count=payload.get('kits_shown_count', 0),
        kits_total_count=payload.get('kits_total_count', 0),
        current_page=payload.get('current_page', 1),
        total_pages=payload.get('total_pages', 1),
        per_page=payload.get('per_page', 20),
        filters=filters,
        procedure_type_filter=payload.get('procedure_type_filter', []),
        active_only=active_only,
        previous_url=previous_url,
        next_url=next_url,
        go_to_action=url_for('web.admin_kits'),
        pagination_query=_list_query_args(filters, per_page=payload.get('per_page', 20)),
        new_kit_url=url_for('web.admin_kit_new'),
    )


@web_bp.route('/admin/kits/new', methods=['GET', 'POST'])
@require_role('spd_supervisor', 'it_admin')
def admin_kit_new():
    if request.method == 'GET':
        return _render_kit_form('create')

    values, error = _submitted_kit_values()
    if error:
        return _render_kit_form('create', values=values, form_error=error, status=400)

    user = _current_user()
    try:
        institution_id = UUID(user.get('institution_id') or '')
    except (AttributeError, TypeError, ValueError):
        return _render_kit_form(
            'create', values=values,
            form_error='Your account must be assigned to an institution before creating a kit.', status=400,
        )

    try:
        institution = db.session.get(Institution, institution_id)
        if institution is None:
            return _render_kit_form(
                'create', values=values,
                form_error='Your assigned institution could not be found.', status=400,
            )
        error = _validate_kit_families(values)
        if error:
            return _render_kit_form('create', values=values, form_error=error, status=400)

        kit = Kit(
            name=values['kit']['name'],
            version=1,
            active=values['kit']['status'] == 'active',
            institution_id=institution.id,
        )
        db.session.add(kit)
        db.session.flush()
        _sync_kit_composition(kit, values['kit_composition'])
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _render_kit_form(
            'create', values=values,
            form_error='A kit with this name and version already exists for the institution.', status=409,
        )
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Failed to create admin kit')
        return _render_kit_form(
            'create', values=values,
            form_error='Unable to save this kit right now.', status=503,
        )

    return redirect(url_for('web.admin_kits'))


@web_bp.route('/admin/kits/<kit_id>/edit', methods=['GET', 'POST'])
@require_role('spd_supervisor', 'it_admin')
def admin_kit_edit(kit_id):
    try:
        parsed_kit_id = UUID(kit_id)
    except ValueError:
        abort(404)

    kit = db.session.get(Kit, parsed_kit_id)
    if kit is None:
        abort(404)
    if request.method == 'GET':
        return _render_kit_form('edit', kit_id=parsed_kit_id)

    values, error = _submitted_kit_values()
    if error:
        return _render_kit_form(
            'edit', kit_id=parsed_kit_id, values=values, form_error=error, status=400,
        )

    try:
        error = _validate_kit_families(values)
        if error:
            return _render_kit_form(
                'edit', kit_id=parsed_kit_id, values=values, form_error=error, status=400,
            )

        now = datetime.now(timezone.utc)
        new_kit = Kit(
            name=values['kit']['name'],
            version=int(kit.version or 1) + 1,
            active=values['kit']['status'] == 'active',
            institution_id=kit.institution_id,
            created_at=now,
        )
        db.session.add(new_kit)
        db.session.flush()
        _sync_kit_composition(new_kit, values['kit_composition'])
        kit.active = False

        linked_procedures = db.session.execute(
            select(ProcedureKit).where(ProcedureKit.kit_id == kit.id)
        ).scalars().all()
        for procedure_kit in linked_procedures:
            procedure_kit.kit_id = new_kit.id
            procedure_kit.updated_at = now

        user = _current_user() or {}
        _access_audit(
            user_id=user.get('id'),
            action='kit.version_bump',
            resource_type='kit',
            resource_id=new_kit.id,
            institution_id=user.get('institution_id') or kit.institution_id,
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _render_kit_form(
            'edit', kit_id=parsed_kit_id, values=values,
            form_error='A kit with this name and version already exists for the institution.', status=409,
        )
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Failed to update admin kit %s', parsed_kit_id)
        return _render_kit_form(
            'edit', kit_id=parsed_kit_id, values=values,
            form_error='Unable to save this kit right now.', status=503,
        )

    return redirect(url_for('web.admin_kits'))


def _submitted_kit_values():
    kit_values = {
        'name': request.form.get('name', '').strip(),
        'status': request.form.get('status', 'active'),
    }
    values = {'kit': kit_values, 'kit_composition': []}
    error = None
    if not kit_values['name']:
        error = 'Kit name is required.'
    elif len(kit_values['name']) > 160:
        error = 'Kit name must contain at most 160 characters.'
    elif kit_values['status'] not in {'active', 'inactive'}:
        error = 'Choose a valid kit status.'

    family_ids = request.form.getlist('instrument_family[]')
    quantities = request.form.getlist('expected_quantity[]')
    if len(family_ids) != len(quantities):
        return values, error or 'Kit composition rows are incomplete.'

    for family_id, quantity_value in zip(family_ids, quantities):
        try:
            UUID(family_id)
        except (TypeError, ValueError):
            error = error or 'Choose a valid instrument family and quantity for every row.'
        try:
            quantity = int(quantity_value)
        except (TypeError, ValueError):
            quantity = quantity_value
            error = error or 'Choose a valid instrument family and quantity for every row.'
        if isinstance(quantity, int) and (quantity < 1 or quantity > 32767):
            error = error or 'Expected quantity must be between 1 and 32767.'
        values['kit_composition'].append({
            'instrument_family_value': family_id,
            'expected_quantity': quantity,
        })

    if len({item['instrument_family_value'] for item in values['kit_composition']}) != len(values['kit_composition']):
        error = error or 'An instrument family can only appear once in the composition.'
    return values, error


def _validate_kit_families(values):
    family_ids = {UUID(item['instrument_family_value']) for item in values['kit_composition']}
    if not family_ids:
        return None
    existing_ids = {
        item.id
        for item in db.session.execute(
            select(InstrumentFamily).where(InstrumentFamily.id.in_(family_ids))
        ).scalars()
    }
    if existing_ids != family_ids:
        return 'Choose instrument families from the available catalog.'
    return None


def _sync_kit_composition(kit, composition):
    existing_items = db.session.execute(
        select(KitItem).where(KitItem.kit_id == kit.id)
    ).scalars().all()
    for item in existing_items:
        db.session.delete(item)
    db.session.flush()
    for item in composition:
        db.session.add(KitItem(
            kit_id=kit.id,
            family_id=UUID(item['instrument_family_value']),
            quantity=item['expected_quantity'],
        ))


def _render_kit_form(form_mode, kit_id=None, values=None, form_error=None, status=200):
    is_edit = form_mode == 'edit'
    context = kit_form_data(kit_id)
    if values:
        kit_values = dict(context.get('kit', {}))
        kit_values.update(values.get('kit', {}))
        family_lookup = {
            item['value']: item for item in context.get('instrument_families', [])
        }
        composition = []
        for row in values.get('kit_composition', []):
            family = family_lookup.get(str(row.get('instrument_family_value')))
            composition.append({
                'instrument_family_value': str(row.get('instrument_family_value')),
                'instrument_family_label': family['label'] if family else '',
                'expected_quantity': row.get('expected_quantity'),
                'category_code': family.get('category_code', '') if family else '',
                'category_label': family.get('category_label', '') if family else '',
                'category_rank': family.get('category_rank', 999) if family else 999,
                'active_available_count': family.get('active_available_count', 0) if family else 0,
            })
        composition.sort(key=lambda row: (row.get('category_rank', 999), row.get('instrument_family_label', '')))
        context.update({
            'kit': kit_values,
            'kit_composition': composition,
        })
    context.update({
        'form_mode': form_mode,
        'is_edit': is_edit,
        'page_title': 'Edit Kit' if is_edit else 'New Kit',
        'save_url': url_for('web.admin_kit_edit', kit_id=kit_id) if is_edit else url_for('web.admin_kit_new'),
        'cancel_url': url_for('web.admin_kits'),
        'form_error': form_error,
    })
    return _page('admin/kits/form.html', **context), status


@web_bp.route('/admin/procedures')
@require_role('it_admin')
def admin_procedures():
    procedures = procedures_data()
    return _page('admin/procedures/list.html', procedures=procedures, procedures_shown_count=len(procedures), procedures_total_count=len(procedures))


@web_bp.route('/admin/procedures/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_procedure_new():
    if request.method == 'GET':
        return _render_procedure_form('create')

    values, error = _submitted_procedure_values()
    if error:
        return _render_procedure_form('create', values=values, form_error=error, status=400)

    try:
        error = _validate_procedure_catalog_rows(values)
        if error:
            return _render_procedure_form('create', values=values, form_error=error, status=400)

        procedure = CatProcedureType(
            code=values['procedure']['code'],
            name=values['procedure']['name'],
        )
        db.session.add(procedure)
        db.session.flush()
        _sync_procedure_associations(procedure, values)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _render_procedure_form(
            'create', values=values,
            form_error='A procedure with this code already exists.', status=409,
        )
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Failed to create admin procedure')
        return _render_procedure_form(
            'create', values=values,
            form_error='Unable to save this procedure right now.', status=503,
        )

    return redirect(url_for('web.admin_procedures'))


@web_bp.route('/admin/procedures/<procedure_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_procedure_edit(procedure_id):
    try:
        parsed_procedure_id = UUID(procedure_id)
    except ValueError:
        abort(404)

    procedure = db.session.get(CatProcedureType, parsed_procedure_id)
    if procedure is None:
        abort(404)
    if request.method == 'GET':
        return _render_procedure_form('edit', procedure_id=parsed_procedure_id)

    values, error = _submitted_procedure_values()
    if error:
        return _render_procedure_form(
            'edit', procedure_id=parsed_procedure_id, values=values,
            form_error=error, status=400,
        )

    try:
        error = _validate_procedure_catalog_rows(values)
        if error:
            return _render_procedure_form(
                'edit', procedure_id=parsed_procedure_id, values=values,
                form_error=error, status=400,
            )

        procedure.code = values['procedure']['code']
        procedure.name = values['procedure']['name']
        _sync_procedure_associations(procedure, values)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _render_procedure_form(
            'edit', procedure_id=parsed_procedure_id, values=values,
            form_error='A procedure with this code already exists.', status=409,
        )
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Failed to update admin procedure %s', parsed_procedure_id)
        return _render_procedure_form(
            'edit', procedure_id=parsed_procedure_id, values=values,
            form_error='Unable to save this procedure right now.', status=503,
        )

    return redirect(url_for('web.admin_procedures'))


def _submitted_procedure_values():
    procedure = {
        'code': request.form.get('code', '').strip(),
        'name': request.form.get('name', '').strip(),
    }
    values = {'procedure': procedure, 'associated_kits': [], 'counting_phases': []}
    error = _validate_procedure_values(procedure)

    kit_ids = request.form.getlist('kit[]')
    kit_indices = request.form.getlist('kit_row_index[]')
    technique_labels = request.form.getlist('technique_label[]')
    default_indices = request.form.getlist('is_default[]')
    kit_active_indices = request.form.getlist('kit_active[]')
    if not (len(kit_ids) == len(kit_indices) == len(technique_labels)):
        return values, error or 'Associated kit rows are incomplete.'

    phase_ids = request.form.getlist('phase[]')
    phase_indices = request.form.getlist('phase_row_index[]')
    sort_orders = request.form.getlist('sort_order[]')
    count_required_indices = request.form.getlist('count_required[]')
    phase_active_indices = request.form.getlist('phase_active[]')
    if not (len(phase_ids) == len(phase_indices) == len(sort_orders)):
        return values, error or 'Counting phase rows are incomplete.'

    try:
        kit_row_indices = [int(value) for value in kit_indices]
        phase_row_indices = [int(value) for value in phase_indices]
        checked_default_indices = {int(value) for value in default_indices}
        checked_kit_active_indices = {int(value) for value in kit_active_indices}
        checked_count_required_indices = {int(value) for value in count_required_indices}
        checked_phase_active_indices = {int(value) for value in phase_active_indices}
        parsed_phase_orders = [int(value) for value in sort_orders]
    except ValueError:
        return values, error or 'Procedure row data is invalid.'

    if (len(set(kit_row_indices)) != len(kit_row_indices)
            or len(set(phase_row_indices)) != len(phase_row_indices)
            or not checked_default_indices.issubset(kit_row_indices)
            or not checked_kit_active_indices.issubset(kit_row_indices)
            or not checked_count_required_indices.issubset(phase_row_indices)
            or not checked_phase_active_indices.issubset(phase_row_indices)):
        return values, error or 'Procedure row data is invalid.'

    for index, (kit_id, technique_label) in enumerate(zip(kit_ids, technique_labels)):
        try:
            UUID(kit_id)
        except ValueError:
            return values, error or 'Choose a valid kit for every associated kit row.'
        if len(technique_label.strip()) > 160:
            return values, error or 'Technique labels must contain at most 160 characters.'
        row_index = kit_row_indices[index]
        values['associated_kits'].append({
            'kit_value': kit_id,
            'technique_label': technique_label.strip(),
            'is_default': row_index in checked_default_indices,
            'active': row_index in checked_kit_active_indices,
            'row_index': row_index,
        })

    if sum(item['is_default'] for item in values['associated_kits']) > 1:
        return values, error or 'Choose at most one default kit.'

    for index, (phase_id, sort_order) in enumerate(zip(phase_ids, parsed_phase_orders)):
        try:
            UUID(phase_id)
        except ValueError:
            return values, error or 'Choose a valid phase for every counting phase row.'
        if sort_order < 1 or sort_order > 32767:
            return values, error or 'Phase order must be between 1 and 32767.'
        row_index = phase_row_indices[index]
        values['counting_phases'].append({
            'phase_value': phase_id,
            'is_count_required': row_index in checked_count_required_indices,
            'sort_order': sort_order,
            'active': row_index in checked_phase_active_indices,
            'row_index': row_index,
        })

    if len({item['kit_value'] for item in values['associated_kits']}) != len(values['associated_kits']):
        return values, error or 'A kit can only be associated once.'
    if len({item['phase_value'] for item in values['counting_phases']}) != len(values['counting_phases']):
        return values, error or 'A phase can only be added once.'
    if len({item['sort_order'] for item in values['counting_phases']}) != len(values['counting_phases']):
        return values, error or 'Each counting phase must have a unique order.'
    return values, error


def _validate_procedure_values(values):
    if not values['code'] or not values['name']:
        return 'Procedure code and name are required.'
    if len(values['code']) > 64 or len(values['name']) > 200:
        return 'Procedure code must be at most 64 characters and name at most 200 characters.'
    return None


def _validate_procedure_catalog_rows(values):
    kit_ids = {UUID(item['kit_value']) for item in values['associated_kits']}
    phase_ids = {UUID(item['phase_value']) for item in values['counting_phases']}
    kits = {
        item.id
        for item in db.session.execute(select(Kit).where(Kit.id.in_(kit_ids))).scalars()
    } if kit_ids else set()
    phases = {
        item.id
        for item in db.session.execute(select(CatOperationPhase).where(CatOperationPhase.id.in_(phase_ids))).scalars()
    } if phase_ids else set()
    if kits != kit_ids or phases != phase_ids:
        return 'Choose kits and phases from the available catalogs.'
    return None


def _sync_procedure_associations(procedure, values):
    existing_kits = db.session.execute(
        select(ProcedureKit).where(ProcedureKit.procedure_type_id == procedure.id)
    ).scalars().all()
    existing_phases = db.session.execute(
        select(ProcedurePhase).where(ProcedurePhase.procedure_type_id == procedure.id)
    ).scalars().all()
    for association in existing_kits + existing_phases:
        db.session.delete(association)
    db.session.flush()

    now = datetime.now(timezone.utc)
    for item in values['associated_kits']:
        db.session.add(ProcedureKit(
            procedure_type_id=procedure.id,
            kit_id=UUID(item['kit_value']),
            technique_label=item['technique_label'],
            is_default=item['is_default'],
            active=item['active'],
            created_at=now,
            updated_at=now,
        ))
    for item in values['counting_phases']:
        db.session.add(ProcedurePhase(
            procedure_type_id=procedure.id,
            phase_id=UUID(item['phase_value']),
            sort_order=item['sort_order'],
            is_count_required=item['is_count_required'],
            active=item['active'],
        ))


def _render_procedure_form(form_mode, procedure_id=None, values=None, form_error=None, status=200):
    is_edit = form_mode == 'edit'
    context = procedure_form_data(procedure_id)
    if values:
        procedure_values = dict(context.get('procedure', {}))
        procedure_values.update(values.get('procedure', {}))
        context.update({
            'procedure': procedure_values,
            'associated_kits': values.get('associated_kits', context.get('associated_kits', [])),
            'counting_phases': values.get('counting_phases', context.get('counting_phases', [])),
        })
    context.update({
        'form_mode': form_mode,
        'is_edit': is_edit,
        'page_title': 'Edit Procedure' if is_edit else 'New Procedure',
        'save_url': url_for('web.admin_procedure_edit', procedure_id=procedure_id) if is_edit else url_for('web.admin_procedure_new'),
        'cancel_url': url_for('web.admin_procedures'),
        'form_error': form_error,
    })
    return _page('admin/procedures/form.html', **context), status


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