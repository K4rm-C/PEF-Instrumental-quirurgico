from datetime import date, datetime, timezone
from functools import wraps
from uuid import UUID

import requests
from flask import Blueprint, abort, current_app, g, redirect, render_template, request, session, url_for
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import generate_password_hash

from extensions import db
from models.CatInstrumentCycleStatus import CatInstrumentCycleStatus
from models.CatOperationPhase import CatOperationPhase
from models.CatProcedureType import CatProcedureType
from models.Institution import Institution
from models.Instrument import Instrument
from models.Kit import Kit
from models.KitItem import KitItem
from models.InstrumentFamily import InstrumentFamily
from models.ProcedureKit import ProcedureKit
from models.ProcedurePhase import ProcedurePhase
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
    operator_my_sessions,
    operator_session_detail,
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
    user = _current_user()
    sessions = operator_my_sessions(user['id'])
    return _page(
        'operator/sessions/list.html',
        sessions=sessions,
        sessions_shown_count=len(sessions),
        sessions_total_count=len(sessions),
    )


@web_bp.route('/operator/sessions/new', methods=['GET', 'POST'])
@require_role('station_operator')
def operator_session_new():
    # RF-SP-02: only SPD schedules sessions. Operator create UI → legacy.
    return redirect(url_for('web.legacy_operator_session_new'))


@web_bp.route('/operator/sessions/history')
@require_role('station_operator')
def operator_session_history():
    user = _current_user()
    sessions = operator_my_sessions(user['id'])
    return _page(
        'operator/sessions/history.html',
        session_history=sessions,
        sessions_shown_count=len(sessions),
        sessions_total_count=len(sessions),
    )


@web_bp.route('/operator/sessions/<session_id>')
@require_role('station_operator')
def operator_session_details(session_id):
    user = _current_user()
    detail = operator_session_detail(session_id, user['id'])
    if detail is None:
        abort(404)
    return _page('operator/sessions/details.html', session=detail, session_id=session_id)


@web_bp.route('/operator/sessions/<session_id>/begin', methods=['GET', 'POST'])
@require_role('station_operator')
def operator_session_begin(session_id):
    """RF-OP-03 Start Session."""
    from services.rf_session import RfSessionError, start_session

    user = _current_user()
    detail = operator_session_detail(session_id, user['id'])
    if detail is None:
        abort(404)
    if detail.get('status_code') != 'scheduled':
        return redirect(url_for('web.operator_session_details', session_id=session_id))
    if request.method == 'POST':
        if request.form.get('instruments_ready') != '1':
            return _page(
                'operator/sessions/begin.html',
                session=detail,
                session_id=session_id,
                form_error='Confirm the physical instrument set before starting.',
            ), 400
        try:
            result = start_session(
                session_id=UUID(session_id),
                operator_user_id=UUID(user['id']),
                institution_id=UUID(user['institution_id']) if user.get('institution_id') else None,
                ip=request.remote_addr,
            )
        except RfSessionError as exc:
            return _page(
                'operator/sessions/begin.html',
                session=detail,
                session_id=session_id,
                form_error=exc.message,
            ), exc.status_code
        if result['capture_mode'] == 'manual_no_privacy':
            return redirect(url_for('web.operator_session_manual', session_id=session_id))
        return redirect(url_for('web.operator_session_capture', session_id=session_id))
    return _page('operator/sessions/begin.html', session=detail, session_id=session_id)


@web_bp.route('/operator/sessions/<session_id>/manual')
@require_role('station_operator')
def operator_session_manual(session_id):
    """RF-OP-04M progress without live capture."""
    user = _current_user()
    detail = operator_session_detail(session_id, user['id'])
    if detail is None:
        abort(404)
    if detail.get('status_code') != 'in_progress' or detail.get('capture_mode') != 'manual_no_privacy':
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
    if detail.get('status_code') != 'in_progress' or detail.get('capture_mode') != 'manual_no_privacy':
        return redirect(url_for('web.operator_session_details', session_id=session_id))

    if request.method == 'POST':
        reports = []
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
    return _page('operator/sessions/capture.html', session_id=session_id)


@web_bp.route('/operator/sessions/<session_id>/ai-detection')
@require_role('station_operator')
def operator_session_ai_detection(session_id):
    return _page('operator/sessions/ai_detection.html', session_id=session_id)


@web_bp.route('/operator/sessions/<session_id>/validation')
@require_role('station_operator')
def operator_session_validation(session_id):
    return _page('operator/sessions/validation.html', session_id=session_id)


@web_bp.route('/operator/sessions/<session_id>/validation-summary')
@require_role('station_operator')
def operator_session_validation_summary(session_id):
    return _page('operator/sessions/validation_summary.html', session_id=session_id)


@web_bp.route('/operator/sessions/<session_id>/discrepancy')
@require_role('station_operator')
def operator_session_discrepancy(session_id):
    return _page('operator/sessions/discrepancy.html', session_id=session_id)


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
    return _page('operator/sessions/ready_to_close.html', session_id=session_id)


@web_bp.route('/operator/sessions/closed/<session_id>')
@require_role('station_operator')
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
@require_role('spd_supervisor')
def supervisor_profile():
    return _page('shared/profile.html', profile_role='supervisor')


@web_bp.route('/supervisor/dashboard')
@require_role('spd_supervisor')
def supervisor_dashboard():
    stats, sessions, discrepancies = dashboard_data('supervisor')
    return _page(
        'supervisor/dashboard.html',
        dashboard_stats=stats,
        sessions_requiring_attention=discrepancies,
        recent_sessions=sessions[:5],
    )


@web_bp.route('/supervisor/sessions')
@require_role('spd_supervisor')
def supervisor_sessions():
    sessions = sessions_data()
    return _page(
        'supervisor/sessions/list.html',
        sessions=sessions,
        sessions_shown_count=len(sessions),
        sessions_total_count=len(sessions),
    )


@web_bp.route('/supervisor/sessions/new', methods=['GET', 'POST'])
@require_role('spd_supervisor')
def supervisor_session_new():
    """RF-SP-02 schedule session + optional Via A privacy confirm."""
    from datetime import datetime as dt

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
    selected_kit = request.values.get('kit_id') or (options['kits'][0]['id'] if options['kits'] else '')
    expected_lines = kit_expected_lines(UUID(selected_kit)) if selected_kit else []

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

            result = schedule_session(
                supervisor_user_id=UUID(user['id']),
                institution_id=institution_id,
                operator_user_id=UUID(request.form.get('operator_user_id')),
                procedure_type_id=UUID(request.form.get('procedure_type_id')),
                room_id=UUID(request.form.get('room_id')),
                station_id=UUID(request.form.get('station_id')),
                kit_id=UUID(request.form.get('kit_id')),
                patient_id=UUID(request.form.get('patient_id')) if request.form.get('patient_id') else None,
                physician_id=UUID(request.form.get('physician_id')) if request.form.get('physician_id') else None,
                scheduled_at=scheduled_at,
                phase_code=request.form.get('phase_code') or 'setup',
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
            return _page(
                'supervisor/sessions/schedule.html',
                **options,
                expected_lines=expected_lines,
                selected_kit=selected_kit,
                form_error=message,
            ), status

    return _page(
        'supervisor/sessions/schedule.html',
        **options,
        expected_lines=expected_lines,
        selected_kit=selected_kit,
    )


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
    sessions = sessions_data()
    return _page(
        'supervisor/sessions/history.html',
        session_history=sessions,
        sessions_shown_count=len(sessions),
        sessions_total_count=len(sessions),
    )


@web_bp.route('/supervisor/sessions/<session_id>')
@require_role('spd_supervisor')
def supervisor_session_details(session_id):
    detail = supervisor_session_detail(session_id)
    if detail is None:
        # Keep legacy WS-026 click-through for non-UUID demo ids.
        session_data = next((item for item in sessions_data() if item['id'] == session_id), None)
        return _page('supervisor/sessions/details.html', session=session_data, session_id=session_id)
    return _page('supervisor/sessions/rf_details.html', session=detail, session_id=session_id)


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
    discrepancies = discrepancies_data()
    return _page(
        'supervisor/discrepancies/list.html',
        discrepancies=discrepancies,
        discrepancies_summary=discrepancies_summary_data(discrepancies),
    )


@web_bp.route('/supervisor/discrepancies/<session_id>/review', methods=['GET', 'POST'])
@require_role('spd_supervisor')
def supervisor_discrepancy_review(session_id):
    from services.rf_session import RfSessionError, resolve_discrepancy

    detail = supervisor_session_detail(session_id)
    if detail is None:
        session_data = next((item for item in sessions_data() if item['id'] == session_id), None)
        return _page('supervisor/discrepancies/review.html', session=session_data, session_id=session_id)

    if request.method == 'POST':
        user = _current_user()
        try:
            resolve_discrepancy(
                discrepancy_id=UUID(request.form.get('discrepancy_id')),
                supervisor_user_id=UUID(user['id']),
                institution_id=UUID(user['institution_id']) if user.get('institution_id') else None,
                notes=request.form.get('notes') or '',
                mark_lost=request.form.get('mark_lost') == '1',
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
    return _page('supervisor/reports.html')


@web_bp.route('/supervisor/indicators')
@require_role('spd_supervisor')
def supervisor_indicators():
    # indicator_stats/sessions_by_day/discrepancies_by_* have no backing analytics query yet
    # (no AI-vs-human agreement, resolution-time, or per-instrument/family/type discrepancy
    # aggregation exists on any model) — the template supplies the approved WS-026-consistent
    # presentation fallback. See V2_IMPLEMENTATION_NOTES.md.
    return _page('supervisor/indicators.html')


@web_bp.route('/supervisor/audit-log')
@require_role('spd_supervisor')
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
@require_role('it_admin')
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

        instrument.family_id = family.id
        instrument.cycle_status_id = cycle_status.id
        instrument.active = values['active_status'] == 'active'
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
@require_role('it_admin')
def admin_kits():
    kits = kits_data()
    return _page('admin/kits/list.html', kits=kits, kits_shown_count=len(kits), kits_total_count=len(kits))


@web_bp.route('/admin/kits/new', methods=['GET', 'POST'])
@require_role('it_admin')
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
@require_role('it_admin')
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

        kit.name = values['kit']['name']
        kit.active = values['kit']['status'] == 'active'
        _sync_kit_composition(kit, values['kit_composition'])
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
        context.update({
            'kit': kit_values,
            'kit_composition': values.get('kit_composition', context.get('kit_composition', [])),
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