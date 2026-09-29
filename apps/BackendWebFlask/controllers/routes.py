import uuid
from datetime import date, datetime
from functools import wraps

import requests
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, session, url_for
from flask_babel import gettext as _
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.urls import urlsplit

import localization
from extensions import db
from localization.labels import label, role_name
from models.CaptureStation import CaptureStation
from models.CatProcedureType import CatProcedureType
from models.CatSessionStatus import CatSessionStatus
from models.Discrepancy import Discrepancy
from models.Institution import Institution
from models.Instrument import Instrument
from models.InstrumentFamily import InstrumentFamily
from models.Kit import Kit
from models.OperatingRoom import OperatingRoom
from models.Role import Role
from models.User import User
from models.WorkSession import WorkSession
from models.YoloModel import YoloModel
from services.admin_service import (
    FormError,
    activate_model,
    change_user_password,
    kit_rows,
    parse_uuid,
    procedure_kit_rows,
    procedure_phase_rows,
    save_capture_station,
    save_family,
    save_institution,
    save_instrument,
    save_kit,
    save_operating_room,
    save_procedure,
    save_role,
    save_user,
    save_vision_model,
    set_user_active,
)
from services import analytics_service as analytics
from services.audit import record_denied
from services.evidence_service import (
    PayloadTooLarge,
    capture_file,
    discard_file,
    get_capture_for_session,
    session_captures,
    store_capture,
)
from services.review_service import (
    SessionCloseBlocked,
    approve_discrepancy,
    close_session,
    reject_discrepancy,
    request_discrepancy_correction,
    session_report,
    status_code,
    submit_corrections,
    submit_validation,
    validation_complete,
)
from services.session_service import create_work_session, new_session_context, session_view
from services.vision_service import (
    active_model,
    confidence_threshold,
    controlled_form_rows,
    get_provider,
    latest_run_view,
    run_already_processed,
    run_inference,
)

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

# application roles (role.code); their labels are localized by code (localization.labels)
APP_ROLES = ('operator_cde', 'supervisor_quality', 'it_admin')

EMPTY_STATS = {
    'total': 0,
    'active': 0,
    'pending': 0,
    'completed': 0,
}


def _context(**values):
    current_user = _current_user()
    context = {
        'current_user': _display_user(current_user),
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


def _display_user(user):
    """Header/profile labels by role.code in the current UI language (never role.description)."""
    if not user:
        return user
    return {**user, 'role_label': label('role_short', user['role_code']), 'profile_role_label': role_name(user['role_code'])}


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
        _clear_web_session()
        g.verified_user = None
        return None

    payload = response.json()
    user_data = payload.get('user', {})
    roles = user_data.get('roles', [])
    role = next((item for item in roles if item.get('code') in APP_ROLES), None)
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
        'avatar_url': None,
    }
    return g.verified_user


def _clear_web_session():
    """Drop the web session but keep the visitor's UI language for the public pages."""
    locale = session.get(localization.SESSION_KEY)
    session.clear()
    localization.remember_for_signed_out_session(locale)


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
            return _page('auth/sign_in.html', sign_in_error=_('Authentication service unavailable.')), 503
        if not auth_response.ok:
            return _page('auth/sign_in.html', sign_in_error=_('Invalid email or password.')), 401

        user_data = auth_response.json().get('user', {})
        roles = user_data.get('roles', [])
        role = next((item for item in roles if item.get('code') in APP_ROLES), None)
        if role is None:
            return _page('auth/sign_in.html', sign_in_error=_('Your account has no application role.')), 403

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
    locale = localization.resolve_locale()  # the signed-in user's language stays on the public pages
    auth_response = None
    if access_token:
        auth_response = _auth_request('post', '/logout', token=access_token)
    session.clear()
    localization.remember_for_signed_out_session(locale)
    response = redirect(url_for('web.sign_in'))
    return _forward_set_cookies(response, auth_response)


@web_bp.route('/preferences/locale', methods=['POST'])
def set_locale_preference():
    """Language switch (EN / ES) for every page. Signed in: user.ui_preferences.locale; always: web session."""
    user = _current_user()
    target = localization.safe_local_path(request.form.get('next'))
    if target is None:
        target = url_for(_role_home(user['role_code'])) if user else url_for('web.index')
    if localization.normalize_locale(request.form.get('locale')) is None:
        abort(400)  # unsupported value: nothing is stored
    try:
        localization.set_locale(request.form.get('locale'), user)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Saving the UI locale preference failed')
        flash(_('Your language preference could not be saved. Please try again.'), 'danger')
    return redirect(target, code=303)


@web_bp.route('/operator/profile')
@require_role('operator_cde')
def operator_profile():
    return _page('shared/profile.html', profile_role='operator')


@web_bp.route('/operator/dashboard')
@require_role('operator_cde')
def operator_dashboard():
    ctx = _admin_ctx()

    def build():
        scope = analytics.session_scope(ctx['institution_id'])
        metrics = analytics.session_metrics(scope, user_id=parse_uuid(ctx['user'].get('id')))
        unresolved = analytics.discrepancy_metrics(scope)['unresolved']
        stats = {'sessions_today': metrics['today'], 'open_sessions': metrics['open'], 'counting_sessions': metrics['counting'],
                 'validating_sessions': metrics['validating'], 'closed_sessions': metrics['closed'],
                 'open_discrepancies': unresolved, 'my_sessions': metrics['mine']}
        breakdown = [{'code': key, 'label': label('session_status', key), 'value': metrics[key], 'variant': variant}
                     for key, variant in (('open', 'info'), ('counting', 'warning'), ('validating', 'warning'),
                                          ('closed', 'success'))]
        return stats, breakdown, analytics.sessions_by_day(scope)

    stats, breakdown, by_day = _read_or(build, ({}, [], []))
    sessions = _read_or(lambda: sessions_data(ctx['institution_id'], operator_links=True), [])
    return _page('operator/dashboard.html', dashboard_stats=stats, session_status_breakdown=breakdown,
                 sessions_by_day=by_day, recent_sessions=sessions[:5])


@web_bp.route('/operator/sessions')
@require_role('operator_cde')
def operator_sessions():
    sessions = sessions_data(_admin_ctx()['institution_id'], operator_links=True)
    statuses = _read_or(_session_status_options, [])
    return _page(
        'operator/sessions/list.html',
        sessions=sessions,
        sessions_shown_count=len(sessions),
        sessions_total_count=len(sessions),
        filter_statuses=statuses,
    )


@web_bp.route('/operator/sessions/new', methods=['GET', 'POST'])
@require_role('operator_cde')
def operator_session_new():
    status = 200
    if request.method == 'POST':
        ok, status, work_session = _persist(create_work_session)
        if ok:
            flash(_('Counting session started. Expected inventory frozen from the selected kit.'), 'success')
            return redirect(url_for('web.operator_session_capture', session_id=str(work_session.id)))
    ctx = _admin_ctx()
    selection = request.form if request.method == 'POST' else request.args
    context = _read_or(lambda: new_session_context(ctx['institution_id'], selection), {})
    return _page(
        'operator/sessions/new.html',
        start_session_url=url_for('web.operator_session_new'),
        session_datetime=datetime.now().strftime('%Y-%m-%d %H:%M'),
        **context,
    ), status


@web_bp.route('/operator/sessions/history')
@require_role('operator_cde')
def operator_session_history():
    sessions = sessions_data(_admin_ctx()['institution_id'], operator_links=True)
    statuses = _read_or(_session_status_options, [])
    return _page(
        'operator/sessions/history.html',
        session_history=sessions,
        sessions_shown_count=len(sessions),
        sessions_total_count=len(sessions),
        filter_statuses=statuses,
    )


def _session_status_options():
    """Session status filter options: value = stable code, label localized by code."""
    options = [{'value': code, 'label': label('session_status', code, name)} for code, name in db.session.execute(
        select(CatSessionStatus.code, CatSessionStatus.name)).all()]
    return sorted(options, key=lambda option: option['label'].lower())


# ----------------------------------------------------------------------------------------
# Operator V2 counting-session workflow (prompts/09_Operator_V2.md).
#
# Each stage below is a distinct page keyed by the real WorkSession UUID. Every route loads
# the WorkSession (404 when missing or outside the operator's institution) and renders the
# real session header + frozen ExpectedInventory. AI detection / validation / discrepancy /
# closing content is still the templates' reference fallback until those blocks are built
# (CountEvent, Discrepancy, HumanCorrection). See V2_IMPLEMENTATION_NOTES.md.
# ----------------------------------------------------------------------------------------

def _read_or(operation, fallback):
    """Read-only DB helper for operator pages: demo mode or DB errors fall back without a 500."""
    if current_app.config.get('FRONTEND_DEMO_MODE'):
        return fallback
    try:
        return operation()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Operator view query failed')
        return fallback


def _operator_session(session_id):
    """WorkSession by URL UUID, visible only inside the operator's institution (else 404)."""
    return _owned(WorkSession, session_id, owner=lambda item: item.user.institution_id)


def _operator_stage(template, session_id, **extra):
    """Stage screens get the real session header; AI/validation content is still pending."""
    if current_app.config.get('FRONTEND_DEMO_MODE'):
        return _page(template, session_id=session_id)
    context = session_view(_operator_session(session_id))
    return _page(template, session_id=context['session']['session_id'], **context, **extra)


def _capture_context(work_session):
    captures = [{
        'id': str(asset.id),
        'url': url_for('web.operator_session_media', session_id=work_session.id, asset_id=asset.id),
        'captured_at': asset.created_at.strftime('%Y-%m-%d %H:%M:%S') if asset.created_at else '',
        'size_kb': round((asset.size_bytes or 0) / 1024, 1),
    } for asset in session_captures(work_session)]
    return {
        'captures': captures,
        'latest_capture': captures[0] if captures else None,
        'captured_at': captures[0]['captured_at'] if captures else '',
        'upload_capture_url': url_for('web.operator_session_capture', session_id=work_session.id),
    }


def _store_uploaded_capture(work_session):
    """POST handler body: validate + store + commit, compensating the file on any failure."""
    path = None
    try:
        try:
            upload = request.files.get('capture_image')
        except RequestEntityTooLarge:
            raise PayloadTooLarge(_('The image exceeds the maximum allowed size.')) from None
        __, path = store_capture(work_session, upload, _admin_ctx())
        db.session.commit()
    except FormError as exc:
        db.session.rollback()
        discard_file(path)
        flash(str(exc), 'danger')
        return exc.status
    except (SQLAlchemyError, OSError, ValueError):
        db.session.rollback()
        discard_file(path)
        current_app.logger.exception('Capture upload failed for session %s', work_session.id)
        flash(_('The capture could not be saved. Please try again.'), 'danger')
        return 503
    flash(_('Capture saved. The session is now counting.'), 'success')
    return None


@web_bp.route('/operator/sessions/<session_id>/capture', methods=['GET', 'POST'])
@require_role('operator_cde')
def operator_session_capture(session_id):
    if current_app.config.get('FRONTEND_DEMO_MODE'):
        return _page('operator/sessions/capture.html', session_id=session_id)
    work_session = _operator_session(session_id)
    status = 200
    if request.method == 'POST':
        status = _store_uploaded_capture(work_session)
        if status is None:
            return redirect(url_for('web.operator_session_capture', session_id=work_session.id))
    context = session_view(work_session)
    return _page('operator/sessions/capture.html', session_id=context['session']['session_id'],
                 **context, **_capture_context(work_session)), status


@web_bp.route('/operator/sessions/<session_id>/media/<asset_id>')
@require_role('operator_cde')
def operator_session_media(session_id, asset_id):
    return _serve_capture(_operator_session(session_id), asset_id)


def _serve_capture(work_session, asset_id):
    """Private evidence: only assets inside this session's namespace, never a user-given path."""
    asset = get_capture_for_session(work_session, asset_id)
    if asset is None:
        abort(404)
    try:
        path = capture_file(asset).path
    except ValueError:
        abort(404)
    if not path.is_file():
        abort(404)
    response = send_file(path, mimetype='image/jpeg', max_age=0, conditional=True)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def _ai_context(work_session):
    """AI Suggested Count data: latest persisted run, or the inference form when none exists yet."""
    run = latest_run_view(work_session)
    context = {'ai_run': run, 'ai_form': None, 'ai_notice': None, 'capture_url': None}
    asset_id = run['media_asset_id'] if run else None
    if asset_id is None:
        latest = session_captures(work_session)
        asset_id = str(latest[0].id) if latest else None
    if asset_id:
        context['capture_url'] = url_for('web.operator_session_media', session_id=work_session.id, asset_id=asset_id)
    if run is None:
        status = session_view(work_session)['session']
        try:
            provider = get_provider()
            model, mapping = active_model()
        except FormError as exc:
            context['ai_notice'] = str(exc)
            return context
        if asset_id is None:
            context['ai_notice'] = _('Take a capture of the instrument tray before running the analysis.')
        context['ai_form'] = {
            'provider_code': provider.name,
            'provider_label': label('inference_provider', provider.name),
            'needs_form': provider.needs_form,
            'model_version': model.version_tag,
            'threshold': confidence_threshold(),
            'inference_run_id': str(uuid.uuid4()),
            'rows': controlled_form_rows(mapping),
            'can_run': asset_id is not None,
            'status_code': status['status_code'],
            'status_label': status['status_label'],
        }
    return context


@web_bp.route('/operator/sessions/<session_id>/ai-detection', methods=['GET', 'POST'])
@require_role('operator_cde')
def operator_session_ai_detection(session_id):
    if current_app.config.get('FRONTEND_DEMO_MODE'):
        return _page('operator/sessions/ai_detection.html', session_id=session_id)
    work_session = _operator_session(session_id)
    status = 200
    if request.method == 'POST':
        run_id = parse_uuid(request.form.get('inference_run_id'))
        try:
            result = run_inference(work_session, request.form, _admin_ctx())
            db.session.commit()
            flash(_('This analysis was already stored.') if result is None
                  else _('Analysis stored. Review the suggested count before human validation.'), 'success')
            return redirect(url_for('web.operator_session_ai_detection', session_id=work_session.id))
        except FormError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
            status = exc.status
        except IntegrityError:
            db.session.rollback()
            if run_id and run_already_processed(work_session, run_id):  # concurrent double submit
                return redirect(url_for('web.operator_session_ai_detection', session_id=work_session.id))
            flash(_('The analysis conflicts with existing records and was not stored.'), 'danger')
            status = 409
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception('Inference persistence failed for session %s', work_session.id)
            flash(_('The analysis could not be stored. Nothing was saved; please try again.'), 'danger')
            status = 503
    context = session_view(work_session)
    context.update(_ai_context(work_session))
    return _page('operator/sessions/ai_detection.html', session_id=context['session']['session_id'], **context), status


def _media_url(work_session, asset_id):
    if not asset_id:
        return None
    user = _current_user() or {}
    endpoint = 'web.supervisor_session_media' if user.get('role_code') == 'supervisor_quality' else 'web.operator_session_media'
    return url_for(endpoint, session_id=work_session.id, asset_id=asset_id)


def _review_context(work_session, **extra):
    """Session header + persisted validation/review/closing data for the V2 stage screens."""
    context = session_view(work_session)
    report = session_report(work_session)
    context.update(report=report, capture_url=_media_url(work_session, report['run']['media_asset_id']),
                   captures=[{'url': _media_url(work_session, asset.id), 'captured_at': asset.created_at.strftime('%Y-%m-%d %H:%M:%S') if asset.created_at else ''}
                             for asset in session_captures(work_session)],
                   **extra)
    return context


def _review_page(template, work_session, status=200, **extra):
    context = _review_context(work_session, **extra)
    return _page(template, session_id=context['session']['session_id'], **context), status


def _run_review_action(operation):
    """Commit operation(...) or roll back + flash; returns (ok, status, result)."""
    try:
        result = operation()
        db.session.commit()
        return True, 200, result
    except FormError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
        return False, exc.status, None
    except IntegrityError:
        db.session.rollback()
        flash(_('This action conflicts with existing records and was not stored.'), 'danger')
        return False, 409, None
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Review action failed on %s', request.path)
        flash(_('The action could not be stored. Nothing was saved; please try again.'), 'danger')
        return False, 503, None


def _operator_flow_redirect(work_session, endpoint):
    return redirect(url_for(endpoint, session_id=work_session.id))


@web_bp.route('/operator/sessions/<session_id>/validation', methods=['GET', 'POST'])
@require_role('operator_cde')
def operator_session_validation(session_id):
    if current_app.config.get('FRONTEND_DEMO_MODE'):
        return _page('operator/sessions/validation.html', session_id=session_id)
    work_session = _operator_session(session_id)
    status = 200
    if request.method == 'POST':
        ok, status, stored = _run_review_action(lambda: submit_validation(work_session, request.form, _admin_ctx()))
        if ok:
            flash(_('Validation stored.') if stored else _('This validation was already stored.'), 'success')
            return _operator_flow_redirect(work_session, 'web.operator_session_validation_summary')
    return _review_page('operator/sessions/validation.html', work_session, status,
                        validation_batch_id=str(uuid.uuid4()), session_status=status_code(work_session))


@web_bp.route('/operator/sessions/<session_id>/validation-summary')
@require_role('operator_cde')
def operator_session_validation_summary(session_id):
    if current_app.config.get('FRONTEND_DEMO_MODE'):
        return _page('operator/sessions/validation_summary.html', session_id=session_id)
    work_session = _operator_session(session_id)
    if not validation_complete(work_session):
        flash(_('Validate every instrument before continuing.'), 'warning')
        return _operator_flow_redirect(work_session, 'web.operator_session_validation')
    return _review_page('operator/sessions/validation_summary.html', work_session)


@web_bp.route('/operator/sessions/<session_id>/discrepancy')
@require_role('operator_cde')
def operator_session_discrepancy(session_id):
    return operator_session_awaiting_review(session_id)


@web_bp.route('/operator/sessions/<session_id>/awaiting-review')
@require_role('operator_cde')
def operator_session_awaiting_review(session_id):
    if current_app.config.get('FRONTEND_DEMO_MODE'):
        return _page('operator/sessions/awaiting_review.html', session_id=session_id)
    return _review_page('operator/sessions/awaiting_review.html', _operator_session(session_id))


@web_bp.route('/operator/sessions/<session_id>/correction', methods=['GET', 'POST'])
@require_role('operator_cde')
def operator_session_correction(session_id):
    if current_app.config.get('FRONTEND_DEMO_MODE'):
        return _page('operator/sessions/correction_requested.html', session_id=session_id)
    work_session = _operator_session(session_id)
    status = 200
    if request.method == 'POST':
        ok, status, stored = _run_review_action(lambda: submit_corrections(work_session, request.form, _admin_ctx()))
        if ok:
            flash(_('Correction sent back to the supervisor.') if stored else _('This correction was already stored.'), 'success')
            return _operator_flow_redirect(work_session, 'web.operator_session_awaiting_review')
    return _review_page('operator/sessions/correction_requested.html', work_session, status,
                        validation_batch_id=str(uuid.uuid4()))


@web_bp.route('/operator/sessions/<session_id>/ready-to-close')
@require_role('operator_cde')
def operator_session_ready_to_close(session_id):
    if current_app.config.get('FRONTEND_DEMO_MODE'):
        return _page('operator/sessions/ready_to_close.html', session_id=session_id)
    work_session = _operator_session(session_id)
    return _review_page('operator/sessions/ready_to_close.html', work_session,
                        close_session_url=url_for('web.operator_session_close', session_id=work_session.id))


@web_bp.route('/operator/sessions/<session_id>/close', methods=['POST'])
@require_role('operator_cde')
def operator_session_close(session_id):
    work_session = _operator_session(session_id)
    try:
        outcome = close_session(work_session, _admin_ctx())
        db.session.commit()
    except SessionCloseBlocked as exc:
        db.session.commit()  # persist close_blocked + denied CLOSE_SESSION audit, session unchanged
        flash(str(exc), 'danger')
        return _review_page('operator/sessions/ready_to_close.html', work_session, 409,
                            close_session_url=url_for('web.operator_session_close', session_id=work_session.id))
    except FormError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
        return _review_page('operator/sessions/ready_to_close.html', work_session, exc.status,
                            close_session_url=url_for('web.operator_session_close', session_id=work_session.id))
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Close failed for session %s', work_session.id)
        flash(_('The session could not be closed. Nothing was changed; please try again.'), 'danger')
        return _review_page('operator/sessions/ready_to_close.html', work_session, 503,
                            close_session_url=url_for('web.operator_session_close', session_id=work_session.id))
    flash(_('Session closed.') if outcome == 'closed' else _('This session was already closed.'), 'success')
    return redirect(url_for('web.operator_session_closed', session_id=work_session.id))


@web_bp.route('/operator/sessions/closed/<session_id>')
@require_role('operator_cde')
def operator_session_closed(session_id):
    if current_app.config.get('FRONTEND_DEMO_MODE'):
        return _page('operator/sessions/closed_details.html', session_id=session_id)
    return _review_page('operator/sessions/closed_details.html', _operator_session(session_id))


# --------------------------------------------------------------------------- supervisor

@web_bp.route('/supervisor/profile')
@require_role('supervisor_quality')
def supervisor_profile():
    return _page('shared/profile.html', profile_role='supervisor')


def _supervisor_discrepancy_rows(institution_id, include_resolved=True):
    rows = []
    sessions = {row['id']: row for row in sessions_data(institution_id)}
    for work_session in db.session.execute(
        select(WorkSession).join(User, User.id == WorkSession.user_id)
        .where(User.institution_id == institution_id).order_by(WorkSession.started_at.desc())
    ).scalars():
        report = session_report(work_session)
        for item in report['discrepancies']:
            if item['resolved'] and not include_resolved:
                continue
            meta = sessions.get(str(work_session.id), {})
            rows.append({**item, 'session_uuid': str(work_session.id), 'session_id': meta.get('session_id', ''),
                         'procedure_name': meta.get('procedure_name', ''), 'operating_room': meta.get('operating_room', ''),
                         'kit_name': meta.get('kit_name', ''), 'operator_name': meta.get('operator_name', ''),
                         'timestamp': report['run']['timestamp'],
                         'action_url': url_for('web.supervisor_discrepancy_review', session_id=work_session.id)})
    return rows


@web_bp.route('/supervisor/dashboard')
@require_role('supervisor_quality')
def supervisor_dashboard():
    ctx = _admin_ctx()
    sessions = _read_or(lambda: sessions_data(ctx['institution_id']), [])
    discrepancies = _read_or(lambda: _supervisor_discrepancy_rows(ctx['institution_id'], include_resolved=False), [])
    facts = _read_or(lambda: analytics.indicators(ctx['institution_id']), None)
    stats = {
        'sessions_today': facts['sessions']['today'] if facts else 0,
        'sessions_in_validation': facts['sessions']['validating'] if facts else 0,
        'sessions_pending_review': facts['sessions']['pending_review'] if facts else 0,
        'pending_reviews': sum(1 for d in discrepancies if d['state'] == 'UNDER_REVIEW'),
        'open_discrepancies': facts['discrepancies']['unresolved'] if facts else 0,
        'resolved_discrepancies': facts['discrepancies']['resolved'] if facts else 0,
        'human_corrections': facts['corrections']['total'] if facts else 0,
    }
    return _page('supervisor/dashboard.html', dashboard_stats=stats, sessions_requiring_attention=discrepancies,
                 recent_sessions=sessions[:5],
                 top_families=analytics.with_variants(facts['discrepancies']['top_families']) if facts else [])


@web_bp.route('/supervisor/sessions')
@require_role('supervisor_quality')
def supervisor_sessions():
    sessions = sessions_data(_admin_ctx()['institution_id'])
    return _page('supervisor/sessions/list.html', sessions=sessions, sessions_shown_count=len(sessions),
                 sessions_total_count=len(sessions))


@web_bp.route('/supervisor/sessions/history')
@require_role('supervisor_quality')
def supervisor_session_history():
    sessions = [s for s in sessions_data(_admin_ctx()['institution_id']) if s.get('status_code') == 'closed']
    return _page('supervisor/sessions/history.html', session_history=sessions, sessions_shown_count=len(sessions),
                 sessions_total_count=len(sessions))


@web_bp.route('/supervisor/sessions/<session_id>')
@require_role('supervisor_quality')
def supervisor_session_details(session_id):
    work_session = _operator_session(session_id)
    return _review_page('supervisor/sessions/details.html', work_session,
                        review_url=url_for('web.supervisor_discrepancy_review', session_id=work_session.id))


@web_bp.route('/supervisor/discrepancies')
@require_role('supervisor_quality')
def supervisor_discrepancies():
    rows = _supervisor_discrepancy_rows(_admin_ctx()['institution_id'])
    summary = {
        'pending_reviews': sum(1 for d in rows if d['state'] == 'UNDER_REVIEW'),
        'open_discrepancies': sum(1 for d in rows if not d['resolved']),
        'reviewed_today': sum(1 for d in rows if d['resolved']),
    }
    return _page('supervisor/discrepancies/list.html', discrepancies=rows, discrepancies_summary=summary)


@web_bp.route('/supervisor/discrepancies/<session_id>/review')
@require_role('supervisor_quality')
def supervisor_discrepancy_review(session_id):
    return _review_page('supervisor/discrepancies/review.html', _operator_session(session_id))


def _supervisor_decision(discrepancy_id, operation, success):
    discrepancy = _owned(Discrepancy, discrepancy_id, owner=lambda item: item.session.user.institution_id)
    ok, status, __ = _run_review_action(lambda: operation(discrepancy, request.form, _admin_ctx()))
    if ok:
        flash(success, 'success')
        return redirect(url_for('web.supervisor_discrepancy_review', session_id=discrepancy.session_id))
    return _review_page('supervisor/discrepancies/review.html', discrepancy.session, status)


@web_bp.route('/supervisor/discrepancies/<discrepancy_id>/approve', methods=['POST'])
@require_role('supervisor_quality')
def supervisor_discrepancy_approve(discrepancy_id):
    return _supervisor_decision(discrepancy_id, approve_discrepancy, _('Discrepancy approved and resolved.'))


@web_bp.route('/supervisor/discrepancies/<discrepancy_id>/request-correction', methods=['POST'])
@require_role('supervisor_quality')
def supervisor_discrepancy_request_correction(discrepancy_id):
    return _supervisor_decision(discrepancy_id, request_discrepancy_correction, _('Correction requested from the operator.'))


@web_bp.route('/supervisor/discrepancies/<discrepancy_id>/reject', methods=['POST'])
@require_role('supervisor_quality')
def supervisor_discrepancy_reject(discrepancy_id):
    return _supervisor_decision(discrepancy_id, reject_discrepancy, _('Human result rejected; the operator must recount.'))


@web_bp.route('/supervisor/sessions/<session_id>/media/<asset_id>')
@require_role('supervisor_quality')
def supervisor_session_media(session_id, asset_id):
    return _serve_capture(_operator_session(session_id), asset_id)


def _report_periods():
    return (('', _('All History')), ('today', _('Today')), ('7d', _('Last 7 Days')), ('30d', _('Last 30 Days')))


def _report_statuses():
    return (('', _('All Statuses')), *((code, label('session_status', code)) for code in ('open', 'counting', 'validating', 'closed')),
            ('with_discrepancies', _('With Discrepancies')))


@web_bp.route('/supervisor/reports')
@require_role('supervisor_quality')
def supervisor_reports():
    ctx = _admin_ctx()
    filters = {key: request.args.get(key, '') for key in ('period', 'kit', 'operator', 'session_status')}
    start, kit_id, operator_id = analytics.period_start(filters['period']), parse_uuid(filters['kit']), parse_uuid(filters['operator'])

    def build():
        facts = analytics.indicators(ctx['institution_id'], start, kit_id, operator_id)
        scope_ids = {str(i) for i in db.session.execute(
            analytics.session_scope(ctx['institution_id'], start, kit_id, operator_id)).scalars()}
        with_discrepancies = {str(i) for i in db.session.execute(
            select(Discrepancy.session_id).where(Discrepancy.session_id.in_(
                analytics.session_scope(ctx['institution_id'], start, kit_id, operator_id)))).scalars()}
        rows = [row for row in sessions_data(ctx['institution_id']) if row['id'] in scope_ids]
        wanted = filters['session_status']
        if wanted == 'with_discrepancies':
            rows = [row for row in rows if row['id'] in with_discrepancies]
        elif wanted:
            rows = [row for row in rows if row.get('status_code') == wanted]
        return facts, rows, analytics.report_filter_options(ctx['institution_id'])

    facts, rows, options = _read_or(build, (None, [], {'kits': [], 'operators': []}))
    return _page('supervisor/reports.html', facts=facts, report_sessions=rows[:20],
                 closed_sessions=[row for row in rows if row.get('status_code') == 'closed'][:20],
                 filters=filters, periods=_report_periods(), statuses=_report_statuses(), filter_options=options)


@web_bp.route('/supervisor/indicators')
@require_role('supervisor_quality')
def supervisor_indicators():
    ctx = _admin_ctx()

    def build():
        facts = analytics.indicators(ctx['institution_id'])
        return facts, analytics.sessions_by_day(analytics.session_scope(ctx['institution_id']))

    facts, by_day = _read_or(build, (None, []))
    return _page('supervisor/indicators.html', facts=facts, sessions_by_day=by_day,
                 discrepancies_by_family=analytics.with_variants(facts['discrepancies']['by_family']) if facts else [],
                 top_families=analytics.with_variants(facts['discrepancies']['top_families']) if facts else [],
                 discrepancies_by_type=analytics.with_variants(facts['discrepancies']['by_reason']) if facts else [],
                 corrections_by_family=analytics.with_variants(facts['corrections']['by_family']) if facts else [])


AUDIT_FILTER_KEYS = ('search', 'user', 'event_type', 'entity', 'outcome', 'date', 'period')


def _audit_page(template, actions=None):
    ctx = _admin_ctx()
    filters = {key: request.args.get(key, '') for key in AUDIT_FILTER_KEYS}
    entries = _read_or(lambda: analytics.audit_entries(ctx['institution_id'], filters, actions), [])
    options = _read_or(lambda: analytics.audit_filter_options(ctx['institution_id'], actions),
                       {'users': [], 'event_types': [], 'entities': []})
    return _page(template, audit_log_entries=entries, events_shown_count=len(entries), events_total_count=len(entries),
                 filters=filters, filter_options=options, periods=_report_periods(), audit_url=request.path)


@web_bp.route('/supervisor/audit-log')
@require_role('supervisor_quality')
def supervisor_audit_log():
    # clinical/operational events only (administrative CRUD events stay in the admin audit log)
    return _audit_page('supervisor/audit_log.html', actions=analytics.CLINICAL_ACTIONS)


@web_bp.route('/admin/profile')
@require_role('it_admin')
def admin_profile():
    return _page('shared/profile.html', profile_role='admin')


@web_bp.route('/admin/dashboard')
@require_role('it_admin')
def admin_dashboard():
    ctx = _admin_ctx()
    stats, __, __ = dashboard_data('admin', ctx['institution_id'])
    overview = admin_dashboard_overview(ctx['institution_id'])
    return _page(
        'admin/dashboard.html',
        dashboard_stats=stats,
        secondary_stats=overview['secondary_stats'],
        catalog_overview=overview['catalog_overview'],
        system_overview=overview['system_overview'],
        operational_metrics=overview['operational_metrics'],
    )


# --------------------------------------------------------------------------- admin helpers

def _admin_ctx():
    """Verified administrator + institution taken from the auth token (never from the form)."""
    user = _current_user()
    institution_id = parse_uuid((user or {}).get('institution_id'))
    if institution_id is None:
        abort(403)
    return {'user': user, 'institution_id': institution_id}


def _owned(model, raw_id, owner=None):
    """Load an entity by URL id; institution-owned rows of another institution behave as 404."""
    entity_id = parse_uuid(raw_id)
    entity = db.session.get(model, entity_id) if entity_id else None
    if entity is None:
        abort(404)
    if owner is None and hasattr(entity, 'institution_id'):
        owner = lambda item: item.institution_id  # noqa: E731
    if owner is not None:
        ctx = _admin_ctx()
        if owner(entity) != ctx['institution_id']:
            record_denied(f'ACCESS_{entity.__tablename__.upper()}', entity.__tablename__, entity.id,
                          actor=ctx['user'], institution_id=ctx['institution_id'])
            abort(404)
    return entity


def _persist(operation):
    """Run operation(form, ctx) in one transaction. Returns (ok, http_status, result); flashes errors."""
    try:
        result = operation(request.form, _admin_ctx())
        db.session.commit()
        return True, 200, result
    except FormError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
        return False, exc.status, None
    except IntegrityError:
        db.session.rollback()
        flash(_('This change conflicts with an existing record (duplicate code/name or another default/active record).'), 'danger')
        return False, 409, None
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Save failed on %s', request.path)
        flash(_('Unable to save right now. Please try again.'), 'danger')
        return False, 503, None


def _admin_form(template, build_context, save, success, list_endpoint, save_url, overlay=None, **page):
    """GET renders the form; POST persists, then redirects to the list or re-renders with errors."""
    status = 200
    if request.method == 'POST':
        ok, status, __ = _persist(save)
        if ok:
            flash(success, 'success')
            return redirect(url_for(list_endpoint))
    context = build_context()
    if status != 200 and overlay:
        overlay(context, request.form)
    return _page(template, **context, save_url=save_url, cancel_url=url_for(list_endpoint), **page), status


def _keep(target, form, *fields):
    target.update({field: form.get(field, '') for field in fields if field in form})


def _labels(options):
    return {option['value']: option['label'] for option in options}


def _list_page(template, endpoint, key, rows, count_prefix, **extra):
    url = url_for(endpoint)
    return _page(template, **{key: rows, f'{count_prefix}_shown_count': len(rows), f'{count_prefix}_total_count': len(rows)},
                 current_page=1, total_pages=1, previous_url=url, next_url=url, **extra)


# --------------------------------------------------------------------------- instrument families

@web_bp.route('/admin/instrument-families')
@require_role('it_admin')
def admin_instrument_families():
    return _list_page('admin/instrument_families/list.html', 'web.admin_instrument_families', 'instrument_families',
                      families_data(), 'families', new_instrument_family_url=url_for('web.admin_instrument_family_new'),
                      filter_categories=family_form_data()['categories'])


def _family_form(family=None):
    def overlay(context, form):
        _keep(context['instrument_family'], form, 'code', 'name', 'category', 'how_to_identify',
              'classification_characteristics', 'function', 'status')
    is_edit = family is not None
    return _admin_form(
        'admin/instrument_families/form.html', lambda: family_form_data(family.id if family else None),
        lambda form, ctx: save_family(form, family, ctx),
        _('Instrument family saved.'), 'web.admin_instrument_families',
        url_for('web.admin_instrument_family_edit', family_id=family.id) if is_edit else url_for('web.admin_instrument_family_new'),
        overlay, page_title=_('Edit Instrument Family') if is_edit else _('New Instrument Family'),
        is_edit=is_edit, form_mode='edit' if is_edit else 'create')


@web_bp.route('/admin/instrument-families/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_instrument_family_new():
    return _family_form()


@web_bp.route('/admin/instrument-families/<family_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_instrument_family_edit(family_id):
    return _family_form(_owned(InstrumentFamily, family_id))


# --------------------------------------------------------------------------- instruments

@web_bp.route('/admin/instruments')
@require_role('it_admin')
def admin_instruments():
    ctx = _admin_ctx()
    return _list_page('admin/instruments/list.html', 'web.admin_instruments', 'instruments',
                      instruments_data(ctx['institution_id']), 'instruments',
                      new_instrument_url=url_for('web.admin_instrument_new'),
                      instrument_families_filter=[{'value': item['id'], 'label': item['name']} for item in families_data()],
                      filter_cycle_statuses=instrument_form_data(ctx['institution_id'])['cycle_statuses'])


def _instrument_form(instrument=None):
    ctx = _admin_ctx()
    is_edit = instrument is not None
    return _admin_form(
        'admin/instruments/form.html',
        lambda: instrument_form_data(ctx['institution_id'], instrument.id if instrument else None),
        lambda form, c: save_instrument(form, instrument, c),
        _('Instrument saved.'), 'web.admin_instruments',
        url_for('web.admin_instrument_edit', instrument_id=instrument.id) if is_edit else url_for('web.admin_instrument_new'),
        lambda context, form: _keep(context['instrument'], form, 'internal_code', 'instrument_family', 'cycle_status', 'active_status'),
        page_title=_('Edit Instrument') if is_edit else _('New Instrument'), form_mode='edit' if is_edit else 'create')


@web_bp.route('/admin/instruments/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_instrument_new():
    return _instrument_form()


@web_bp.route('/admin/instruments/<instrument_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_instrument_edit(instrument_id):
    return _instrument_form(_owned(Instrument, instrument_id))


# --------------------------------------------------------------------------- kits

@web_bp.route('/admin/kits')
@require_role('it_admin')
def admin_kits():
    ctx = _admin_ctx()
    return _list_page('admin/kits/list.html', 'web.admin_kits', 'kits', kits_data(ctx['institution_id']), 'kits',
                      new_kit_url=url_for('web.admin_kit_new'))


def _kit_overlay(context, form):
    _keep(context['kit'], form, 'name', 'status')
    labels = _labels(context['instrument_families'])
    rows = []
    for family, quantity in kit_rows(form):
        try:
            quantity = int(quantity)
        except ValueError:
            quantity = 0
        rows.append({'instrument_family_value': family, 'instrument_family_label': labels.get(family, ''), 'expected_quantity': quantity})
    context['kit_composition'] = rows


def _kit_form(kit=None):
    ctx = _admin_ctx()
    is_edit = kit is not None
    return _admin_form(
        'admin/kits/form.html', lambda: kit_form_data(ctx['institution_id'], kit.id if kit else None),
        lambda form, c: save_kit(form, kit, c),
        _('Kit saved.'), 'web.admin_kits',
        url_for('web.admin_kit_edit', kit_id=kit.id) if is_edit else url_for('web.admin_kit_new'),
        _kit_overlay, page_title=_('Edit Kit') if is_edit else _('New Kit'), form_mode='edit' if is_edit else 'create')


@web_bp.route('/admin/kits/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_kit_new():
    return _kit_form()


@web_bp.route('/admin/kits/<kit_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_kit_edit(kit_id):
    return _kit_form(_owned(Kit, kit_id))


# --------------------------------------------------------------------------- procedures

@web_bp.route('/admin/procedures')
@require_role('it_admin')
def admin_procedures():
    ctx = _admin_ctx()
    return _list_page('admin/procedures/list.html', 'web.admin_procedures', 'procedures',
                      procedures_data(ctx['institution_id']), 'procedures', new_procedure_url=url_for('web.admin_procedure_new'))


def _procedure_overlay(context, form):
    _keep(context['procedure'], form, 'code', 'name')
    kit_labels, phase_labels = _labels(context['kits']), _labels(context['phases'])
    context['associated_kits'] = [
        {'kit_value': row['kit'], 'kit_label': kit_labels.get(row['kit'], ''), 'technique_label': row['technique_label'],
         'is_default': row['is_default'], 'active': row['active']}
        for row in procedure_kit_rows(form)]
    context['counting_phases'] = [
        {'phase_value': row['phase'], 'phase_label': phase_labels.get(row['phase'], ''), 'sort_order': row['sort_order'],
         'is_count_required': row['is_count_required'], 'active': row['active']}
        for row in procedure_phase_rows(form)]


def _procedure_form(procedure=None):
    ctx = _admin_ctx()
    is_edit = procedure is not None
    return _admin_form(
        'admin/procedures/form.html', lambda: procedure_form_data(ctx['institution_id'], procedure.id if procedure else None),
        lambda form, c: save_procedure(form, procedure, c),
        _('Procedure saved.'), 'web.admin_procedures',
        url_for('web.admin_procedure_edit', procedure_id=procedure.id) if is_edit else url_for('web.admin_procedure_new'),
        _procedure_overlay, page_title=_('Edit Procedure') if is_edit else _('New Procedure'), form_mode='edit' if is_edit else 'create')


@web_bp.route('/admin/procedures/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_procedure_new():
    return _procedure_form()


@web_bp.route('/admin/procedures/<procedure_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_procedure_edit(procedure_id):
    return _procedure_form(_owned(CatProcedureType, procedure_id))


# --------------------------------------------------------------------------- users

@web_bp.route('/admin/users')
@require_role('it_admin')
def admin_users():
    ctx = _admin_ctx()
    options = user_form_data(ctx['institution_id'])
    return _list_page('admin/users/list.html', 'web.admin_users', 'users', users_data(ctx['institution_id']), 'users',
                      new_user_url=url_for('web.admin_user_new'), filter_roles=options['roles'],
                      filter_institutions=options['institutions'])


def _user_form(user=None):
    ctx = _admin_ctx()
    is_edit = user is not None

    def overlay(context, form):
        _keep(context['user'], form, 'name', 'email', 'status')
        context['user']['assigned_role_codes'] = list(dict.fromkeys(form.getlist('roles[]')))

    page = {}
    if is_edit:
        page = {
            'change_password_url': url_for('web.admin_user_change_password', user_id=user.id),
            'deactivate_user_url': url_for('web.admin_user_deactivate', user_id=user.id),
        }
    return _admin_form(
        'admin/users/form.html', lambda: user_form_data(ctx['institution_id'], user.id if user else None),
        lambda form, c: save_user(form, user, c),
        _('User saved.'), 'web.admin_users',
        url_for('web.admin_user_edit', user_id=user.id) if is_edit else url_for('web.admin_user_new'),
        overlay, page_title=_('Edit User') if is_edit else _('New User'), is_edit=is_edit,
        form_mode='edit' if is_edit else 'create', **page)


@web_bp.route('/admin/users/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_user_new():
    return _user_form()


@web_bp.route('/admin/users/<user_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_user_edit(user_id):
    return _user_form(_owned(User, user_id))


def _user_action(user_id, operation, success):
    user = _owned(User, user_id)
    ok, __, __ = _persist(lambda form, ctx: operation(user, form, ctx))
    if ok:
        flash(success, 'success')
    return redirect(url_for('web.admin_user_edit', user_id=user.id))


@web_bp.route('/admin/users/<user_id>/password', methods=['POST'])
@require_role('it_admin')
def admin_user_change_password(user_id):
    return _user_action(user_id, change_user_password, _('Password changed.'))


@web_bp.route('/admin/users/<user_id>/deactivate', methods=['POST'])
@require_role('it_admin')
def admin_user_deactivate(user_id):
    return _user_action(user_id, lambda user, form, ctx: set_user_active(user, False, ctx), _('User deactivated.'))


# --------------------------------------------------------------------------- roles

@web_bp.route('/admin/roles')
@require_role('it_admin')
def admin_roles():
    ctx = _admin_ctx()
    return _list_page('admin/roles/list.html', 'web.admin_roles', 'roles', roles_data(ctx['institution_id']), 'roles',
                      new_role_url=url_for('web.admin_role_new'))


def _role_form(role=None):
    ctx = _admin_ctx()
    is_edit = role is not None
    return _admin_form(
        'admin/roles/form.html', lambda: role_form_data(ctx['institution_id'], role.id if role else None),
        lambda form, c: save_role(form, role, c),
        _('Role saved.'), 'web.admin_roles',
        url_for('web.admin_role_edit', role_id=role.id) if is_edit else url_for('web.admin_role_new'),
        lambda context, form: _keep(context['role'], form, 'code', 'description'),
        page_title=_('Edit Role') if is_edit else _('New Role'), form_mode='edit' if is_edit else 'create')


@web_bp.route('/admin/roles/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_role_new():
    return _role_form()


@web_bp.route('/admin/roles/<role_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_role_edit(role_id):
    return _role_form(_owned(Role, role_id))


# --------------------------------------------------------------------------- vision models

@web_bp.route('/admin/vision-models')
@require_role('it_admin')
def admin_vision_models():
    return _list_page('admin/vision_models/list.html', 'web.admin_vision_models', 'vision_models',
                      vision_models_data(), 'vision_models', new_vision_model_url=url_for('web.admin_vision_model_new'))


def _vision_overlay(context, form):
    _keep(context['vision_model'], form, 'version_tag', 'model_asset_reference', 'checksum')
    if 'version_tag' in form:
        context['vision_model']['active'] = bool(form.get('active'))
    labels = _labels(context['instrument_families'])
    context['model_classes'] = [
        {'yolo_class_id': class_id, 'instrument_family_value': family, 'instrument_family_label': labels.get(family, '')}
        for class_id, family in zip(form.getlist('yolo_class_id[]'), form.getlist('instrument_family[]'))]


def _vision_form(model=None):
    is_edit = model is not None
    return _admin_form(
        'admin/vision_models/form.html', lambda: vision_model_form_data(model.id if model else None),
        lambda form, ctx: save_vision_model(form, model, ctx),
        _('Vision model saved.'), 'web.admin_vision_models',
        url_for('web.admin_vision_model_edit', model_id=model.id) if is_edit else url_for('web.admin_vision_model_new'),
        _vision_overlay, page_title=_('Edit Vision Model') if is_edit else _('New Vision Model'), form_mode='edit' if is_edit else 'create')


@web_bp.route('/admin/vision-models/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_vision_model_new():
    return _vision_form()


@web_bp.route('/admin/vision-models/<model_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_vision_model_edit(model_id):
    return _vision_form(_owned(YoloModel, model_id))


@web_bp.route('/admin/vision-models/<model_id>/activate', methods=['POST'])
@require_role('it_admin')
def admin_vision_model_activate(model_id):
    model = _owned(YoloModel, model_id)
    ok, __, __ = _persist(lambda form, ctx: activate_model(model, ctx))
    if ok:
        flash(_('Vision model %(version)s is now active.', version=model.version_tag), 'success')
    return redirect(url_for('web.admin_vision_models'))


@web_bp.route('/admin/audit-log')
@require_role('it_admin')
def admin_audit_log():
    return _audit_page('admin/audit_log.html')


# --------------------------------------------------------------------------- configuration

@web_bp.route('/admin/configuration')
@require_role('it_admin')
def admin_configuration():
    ctx = _admin_ctx()
    return _page(
        'admin/configuration/index.html',
        capture_stations=stations_data(ctx['institution_id']),
        operating_rooms=operating_rooms_data(ctx['institution_id']),
        institution=institution_form_data(ctx['institution_id'])['institution'],
        edit_institution_url=url_for('web.admin_institution_edit'),
        new_operating_room_url=url_for('web.admin_operating_room_new'),
        new_capture_station_url=url_for('web.admin_capture_station_new'),
    )


@web_bp.route('/admin/configuration/institution/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_institution_edit():
    ctx = _admin_ctx()
    institution = _owned(Institution, ctx['institution_id'], owner=lambda item: item.id)
    return _admin_form(
        'admin/configuration/institution_form.html', lambda: institution_form_data(institution.id),
        lambda form, c: save_institution(form, institution, c),
        _('Institution information saved.'), 'web.admin_configuration', url_for('web.admin_institution_edit'),
        lambda context, form: _keep(context['institution'], form, 'name', 'status'),
        page_title=_('Edit Institution Information'), form_mode='edit')


def _room_form(room=None):
    is_edit = room is not None
    return _admin_form(
        'admin/configuration/operating_room_form.html', lambda: operating_room_form_data(room.id if room else None),
        lambda form, ctx: save_operating_room(form, room, ctx),
        _('Operating room saved.'), 'web.admin_configuration',
        url_for('web.admin_operating_room_edit', room_id=room.id) if is_edit else url_for('web.admin_operating_room_new'),
        lambda context, form: _keep(context['operating_room'], form, 'code', 'name', 'status'),
        page_title=_('Edit Operating Room') if is_edit else _('New Operating Room'), is_edit=is_edit,
        form_mode='edit' if is_edit else 'create')


@web_bp.route('/admin/configuration/operating-rooms/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_operating_room_new():
    return _room_form()


@web_bp.route('/admin/configuration/operating-rooms/<room_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_operating_room_edit(room_id):
    return _room_form(_owned(OperatingRoom, room_id))


def _station_form(station=None):
    ctx = _admin_ctx()
    is_edit = station is not None
    return _admin_form(
        'admin/configuration/capture_station_form.html',
        lambda: station_form_data(ctx['institution_id'], station.id if station else None),
        lambda form, c: save_capture_station(form, station, c),
        _('Capture station saved.'), 'web.admin_configuration',
        url_for('web.admin_capture_station_edit', station_id=station.id) if is_edit else url_for('web.admin_capture_station_new'),
        lambda context, form: _keep(context['capture_station'], form, 'name', 'operating_room', 'status'),
        page_title=_('Edit Capture Station') if is_edit else _('New Capture Station'), is_edit=is_edit,
        form_mode='edit' if is_edit else 'create')


@web_bp.route('/admin/configuration/capture-stations/new', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_capture_station_new():
    return _station_form()


@web_bp.route('/admin/configuration/capture-stations/<station_id>/edit', methods=['GET', 'POST'])
@require_role('it_admin')
def admin_capture_station_edit(station_id):
    return _station_form(_owned(CaptureStation, station_id, owner=lambda item: item.room.institution_id))
