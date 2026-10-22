from datetime import date, datetime, timedelta, timezone
from functools import wraps
from uuid import UUID

import requests
from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask_babel import gettext, ngettext

from clients import supervisor_service as supervisor_api
from clients.supervisor_service import SupervisorServiceError
from i18n import (
    LOCALE_COOKIE,
    LOCALE_COOKIE_MAX_AGE,
    N_,
    discrepancy_source,
    is_supported_locale,
    localize_discrepancy_description,
    localize_db_label,
    localize_db_text,
    resolve_locale,
)

from controllers.view_data import (
    PAST_OPERATOR_STATUS_CODES,
    SESSION_STATUS_UI,
    dashboard_data,
    display_session_id,
    discrepancies_summary_data,
    operator_my_sessions,
    operator_session_detail,
    paginate_list,
    sessions_data,
    session_discrepancies,
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

# Display labels (English msgids); templates translate them with _().
ROLE_LABELS = {
    'station_operator': N_('Station Operator'),
    'spd_supervisor': N_('SPD Supervisor'),
    'it_admin': N_('Administrator'),
}

EMPTY_STATS = {
    'total': 0,
    'active': 0,
    'pending': 0,
    'completed': 0,
}


def _error_message(exc):
    """User-facing text of an RfSessionError / VisionBridgeError (services mark it with N_)."""
    message = getattr(exc, 'message', None) or str(exc)
    return gettext(message) if message else message


def _context(**values):
    current_user = _current_user()
    context = {
        'current_locale': resolve_locale(current_user),
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


def _supervisor_request(method, path, *, params=None, json=None):
    try:
        return supervisor_api.call(method, path, params=params, json=json)
    except SupervisorServiceError as exc:
        flash(exc.user_message, 'danger')
        abort(exc.status if 400 <= exc.status < 600 else 503)


def _supervisor_all(path, **params):
    page = 1
    items = []
    while True:
        result = _supervisor_request(
            'GET', path, params={**params, 'page': page, 'per_page': 100},
        )
        items.extend(result.get('items') or [])
        if page >= int(result.get('total_pages') or 1):
            return items
        page += 1


def _supervisor_catalogs():
    return _supervisor_request('GET', '/catalogs')


def _supervisor_schedule_options():
    catalogs = _supervisor_catalogs()
    return {
        'operators': catalogs.get('operators', []),
        'procedures': catalogs.get('procedures', []),
        'rooms': catalogs.get('rooms', []),
        'stations': catalogs.get('stations', []),
        'kits': catalogs.get('kits', []),
        'patients': catalogs.get('patients', []),
        'physicians': catalogs.get('physicians', []),
        'surgical_roles': catalogs.get('surgical_roles', []),
        'families': catalogs.get('families', []),
        'privacy_notices': catalogs.get('privacy_notices', []),
    }


def _supervisor_kit_expected_lines(kit_id):
    result = _supervisor_request('GET', f'/kits/{kit_id}/expected-inventory')
    return result.get('items') or []


def _display_service_datetime(raw):
    if not raw:
        return ''
    try:
        return datetime.fromisoformat(str(raw).replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
    except ValueError:
        return str(raw)


def _supervisor_session_rows():
    rows = []
    for item in _supervisor_all('/sessions'):
        status_code = item.get('status') or 'unknown'
        status_label, status_variant = SESSION_STATUS_UI.get(
            status_code, (N_('Unknown'), 'neutral'),
        )
        has_agreement = bool(item.get('has_privacy_agreement'))
        if item.get('capture_mode') == 'vision' or (
            item.get('capture_mode') is None and has_agreement
        ):
            privacy_label, privacy_variant = N_('Notice OK · Vision'), 'success'
        else:
            privacy_label, privacy_variant = N_('No notice · Manual'), 'warning'
        session_id = item.get('id') or item.get('session_id')
        open_count = int(item.get('discrepancy_count') or 0)
        if status_code == 'correction_required' or open_count:
            action_label, action_url = N_('Review'), f'/supervisor/discrepancies/{session_id}/review'
        elif status_code == 'awaiting_spd_review':
            action_label, action_url = N_('Confirm close'), f'/supervisor/sessions/{session_id}'
        else:
            action_label, action_url = N_('View'), f'/supervisor/sessions/{session_id}'
        procedure = item.get('procedure') or {}
        room = item.get('room') or {}
        kit = item.get('kit') or {}
        station = item.get('station') or {}
        operator = item.get('operator') or {}
        procedure_name = localize_db_label(
            'procedure', procedure.get('code'), procedure.get('name'),
        ) or N_('Unspecified procedure')
        room_name = localize_db_text(
            'operating_room', room.get('code'), room.get('name'),
        ) or N_('Unspecified room')
        kit_name = localize_db_text('kit', kit.get('id'), kit.get('name')) or N_('Unspecified kit')
        station_name = localize_db_text(
            'capture_station', station.get('id'), station.get('name'),
        ) or N_('Unspecified station')
        scheduled = _display_service_datetime(item.get('scheduled_at'))
        started = _display_service_datetime(item.get('started_at'))
        ended = _display_service_datetime(item.get('ended_at'))
        updated = _display_service_datetime(item.get('updated_at'))
        rows.append({
            'id': session_id,
            'session_id': display_session_id(session_id),
            'status_code': status_code,
            'status_label': status_label,
            'status_variant': status_variant if not open_count else 'warning',
            'capture_mode': item.get('capture_mode'),
            'has_privacy_agreement': has_agreement,
            'privacy_label': privacy_label,
            'privacy_variant': privacy_variant,
            'procedure_name': procedure_name,
            'procedure_label': procedure_name,
            'operating_room': room.get('code') or room_name,
            'kit_name': kit_name,
            'operator_name': operator.get('name') or N_('Unknown operator'),
            'capture_station_name': station_name,
            'procedure_type_id': procedure.get('id') or '',
            'kit_id': kit.get('id') or '',
            'station_id': station.get('id') or '',
            'search_text': ' '.join(str(value or '') for value in (
                session_id, procedure_name, procedure.get('code'), room_name,
                kit_name, station_name, operator.get('name'),
            )).lower(),
            'created_at': scheduled or started or updated,
            'scheduled_at': scheduled,
            'started_at': started,
            'closed_at': ended,
            'submitted_at': updated,
            'discrepancy_count': open_count,
            'action_label': action_label,
            'action_url': action_url,
            'details_url': f'/supervisor/sessions/{session_id}',
        })
    return rows


def _supervisor_session_detail(session_id):
    from controllers.view_data import EXPECTED_SOURCE_LABELS

    value = _supervisor_request('GET', f'/sessions/{session_id}')
    if not value:
        return None

    status_code = value.get('status') or 'unknown'
    status_label, status_variant = SESSION_STATUS_UI.get(status_code, (N_('Unknown'), 'neutral'))
    operator = value.get('operator') or {}
    procedure = value.get('procedure') or {}
    room = value.get('room') or {}
    kit = value.get('kit') or {}
    station = value.get('station') or {}
    patient = value.get('patient') or {}
    reported_by_family = {}
    for event in value.get('events') or []:
        if event.get('type') not in {'manual_count', 'auto_count'}:
            continue
        payload = event.get('payload') if isinstance(event.get('payload'), dict) else {}
        family_id = event.get('family_id')
        if family_id:
            reported_by_family[family_id] = (
                payload.get('reported_quantity')
                if payload.get('reported_quantity') is not None
                else event.get('detected_quantity')
            )
        elif payload.get('scope') == 'board':
            board = payload.get('board') if isinstance(payload.get('board'), dict) else {}
            for family_row in board.get('families') or []:
                family_id = family_row.get('family_id')
                if family_id:
                    reported_by_family[family_id] = (
                        family_row.get('reported_quantity')
                        if family_row.get('reported_quantity') is not None
                        else family_row.get('detected_quantity')
                    )
    expected_items = []
    for entry in value.get('expected_inventory') or []:
        family = entry.get('family') or {}
        category = family.get('category') or {}
        source = entry.get('source') or 'manual'
        expected_quantity = entry.get('expected_quantity')
        reported_quantity = None
        if family.get('id') in reported_by_family:
            reported_quantity = reported_by_family[family['id']]
        difference = (
            int(reported_quantity) - int(expected_quantity)
            if reported_quantity is not None and expected_quantity is not None else None
        )
        if reported_quantity is None:
            count_label, count_variant = N_('Pending'), 'neutral'
        elif difference == 0:
            count_label, count_variant = N_('Match'), 'success'
        elif difference < 0:
            count_label, count_variant = N_('Shortfall'), 'danger'
        else:
            count_label, count_variant = N_('Extra'), 'warning'
        expected_items.append({
            'family_id': family.get('id'),
            'family_name': localize_db_text(
                'instrument_family', family.get('code'), family.get('name'),
            ) or N_('Unspecified instrument'),
            'family_code': family.get('code') or '',
            'expected_quantity': expected_quantity,
            'reported_quantity': reported_quantity,
            'ai_detected_quantity': (
                None if value.get('capture_mode') == 'manual_no_privacy' else reported_quantity
            ),
            'difference': difference,
            'status_label': count_label,
            'status_variant': count_variant,
            'source': source,
            'source_label': EXPECTED_SOURCE_LABELS.get(source, source),
            'category_code': category.get('code') or '',
            'category_label': localize_db_label(
                'instrument_category', category.get('code'), category.get('name'),
            ) if category.get('code') else N_('Other'),
            'category_name': category.get('name') or N_('Other'),
            'category_rank': (
                ('cutting', 'dissection', 'grasping', 'hemostasis', 'retraction',
                 'suction', 'suturing').index(category.get('code'))
                if category.get('code') in {
                    'cutting', 'dissection', 'grasping', 'hemostasis', 'retraction', 'suction', 'suturing',
                } else 7
            ),
            'is_additional': source != 'kit_snapshot',
        })
    groups_by_key = {}
    for item in expected_items:
        key = 'additional' if item['is_additional'] else (item['category_code'] or 'other')
        group = groups_by_key.setdefault(key, {
            'key': key, 'label': item['category_label'],
            'rank': 999 if item['is_additional'] else item['category_rank'], 'lines': [],
        })
        group['lines'].append(item)
    discrepancies = []
    for entry in value.get('discrepancies') or []:
        family = entry.get('family') or {}
        reason = entry.get('reason') or {}
        family_name = localize_db_text(
            'instrument_family', family.get('code'), family.get('name'),
        ) or N_('Unspecified instrument')
        source = discrepancy_source(entry.get('origin_event_type'))
        expected_quantity = entry.get('expected_quantity')
        detected_quantity = entry.get('detected_quantity')
        discrepancies.append({
            'id': entry.get('id'),
            'instrument_name': family_name,
            'family_name': family_name,
            'expected_quantity': expected_quantity,
            'counted_quantity': detected_quantity,
            'detected_quantity': detected_quantity,
            'quantity_label': N_('Reported') if source == 'reported' else N_('Detected'),
            'difference': (detected_quantity or 0) - (expected_quantity or 0),
            'description': entry.get('description'),
            'display_description': localize_discrepancy_description(
                reason_code=reason.get('code'),
                source=source,
                instrument=family_name,
                expected=expected_quantity,
                actual=detected_quantity,
                stored_description=entry.get('description'),
            ),
            'reason': localize_db_label(
                'discrepancy_reason', reason.get('code'), reason.get('name'),
            ) if reason.get('code') else (entry.get('description') or N_('Recorded discrepancy')),
            'reason_code': reason.get('code'),
            'resolved': entry.get('resolved'),
            'resolved_at': entry.get('resolved_at'),
        })
    open_count = sum(not item.get('resolved') for item in value.get('discrepancies') or [])
    agreement = value.get('privacy_agreement')
    privacy = ({
        'has_agreement': True,
        'version': agreement.get('version'),
        'agreed_at': _display_service_datetime(agreement.get('agreed_at')),
        'document_uri': agreement.get('document_uri'),
        'purpose_model_improvement': agreement.get('purpose_model_improvement'),
        'agreed_by': agreement.get('agreed_by') or '—',
    } if agreement else {
        'has_agreement': False,
        'message': N_('No privacy notice linked. Start Session will freeze capture_mode = manual_no_privacy.'),
    })
    team = [{
        'physician_id': member.get('id'),
        'name': member.get('name'),
        'role': localize_db_label(
            'surgical_role',
            (member.get('surgical_role') or {}).get('code'),
            (member.get('surgical_role') or {}).get('name'),
        ) or N_('Surgeon'),
        'role_code': (member.get('surgical_role') or {}).get('code') or 'surgeon',
    } for member in value.get('physicians') or []]
    scheduled_at = _display_service_datetime(value.get('scheduled_at'))
    procedure_name = localize_db_label(
        'procedure', procedure.get('code'), procedure.get('name'),
    ) or N_('Unspecified procedure')
    room_name = localize_db_text(
        'operating_room', room.get('code'), room.get('name'),
    ) or N_('Unspecified room')
    kit_name = localize_db_text('kit', kit.get('id'), kit.get('name')) or N_('Unspecified kit')
    station_name = localize_db_text(
        'capture_station', station.get('id'), station.get('name'),
    ) or N_('Unspecified station')
    scheduled_raw = value.get('scheduled_at') or ''
    scheduled_local = scheduled_raw
    if scheduled_raw:
        try:
            scheduled_parsed = datetime.fromisoformat(scheduled_raw.replace('Z', '+00:00'))
            if scheduled_parsed.tzinfo is not None:
                scheduled_parsed = scheduled_parsed.astimezone().replace(tzinfo=None)
            scheduled_local = scheduled_parsed.strftime('%Y-%m-%dT%H:%M')
        except ValueError:
            pass
    family_names = {item['family_id']: item['family_name'] for item in expected_items}
    timeline = []
    for event in reversed(value.get('events') or []):
        payload = event.get('payload') if isinstance(event.get('payload'), dict) else {}
        board = payload.get('board') if isinstance(payload.get('board'), dict) else {}
        totals = board.get('totals') if isinstance(board.get('totals'), dict) else {}
        event_code = event.get('type')
        event_name = localize_db_label('event_type', event_code, event_code) if event_code else ''
        label_bits = [event_name] if event_name else []
        if payload.get('reason'):
            label_bits.append(str(payload['reason']))
        if payload.get('scope') == 'board':
            label_bits.append('board')
        family_id = event.get('family_id')
        timeline.append({
            'occurred_at': _display_service_datetime(event.get('occurred_at')),
            'event_code': event_code,
            'event_name': event_name,
            'event_label': ' · '.join(label_bits),
            'family_id': family_id,
            'family_name': family_names.get(family_id),
            'scope': payload.get('scope'),
            'reason': payload.get('reason'),
            'expected_quantity': event.get('expected_quantity'),
            'detected_quantity': event.get('detected_quantity'),
            'board_family_count': totals.get('family_count'),
            'frames_processed': payload.get('frames_processed'),
            'video_t': payload.get('video_t'),
            'pipeline': payload.get('pipeline'),
            'solver': (payload.get('pipeline_config') or {}).get('solver') or payload.get('solver'),
            'model_version': payload.get('model_version'),
            'payload': payload,
        })
    return {
        'id': value.get('id'),
        'session_id': display_session_id(value.get('id')),
        'status_code': status_code,
        'status_label': status_label,
        'status_variant': status_variant,
        'capture_mode': value.get('capture_mode'),
        'privacy_label': N_('Notice OK · Vision') if agreement else N_('No notice · Manual'),
        'privacy_variant': 'success' if agreement else 'warning',
        'has_privacy_agreement': bool(agreement),
        'discrepancy_count': open_count,
        'procedure_name': procedure_name,
        'procedure_type_id': procedure.get('id') or '',
        'operating_room': room.get('code') or room_name,
        'room_id': room.get('id') or '',
        'kit_name': kit_name,
        'kit_id': kit.get('id') or '',
        'operator_name': operator.get('name') or N_('Unknown operator'),
        'operator_user_id': operator.get('id') or '',
        'capture_station_name': station_name,
        'station_id': station.get('id') or '',
        'patient_name': patient.get('name'),
        'patient_record': str(patient.get('id') or '').replace('-', '')[:10].upper() or None,
        'patient_id': patient.get('id'),
        'surgical_team': team,
        'physician_name': team[0]['name'] if team else None,
        'scheduled_at': scheduled_at,
        'scheduled_at_local': scheduled_local[:16] if scheduled_local else '',
        'started_at': _display_service_datetime(value.get('started_at')),
        'closed_at': _display_service_datetime(value.get('ended_at')),
        'expected_items': expected_items,
        'expected_groups': sorted(groups_by_key.values(), key=lambda group: group['rank']),
        'timeline': timeline,
        'discrepancies': discrepancies,
        'privacy': privacy,
        'can_edit_inventory': status_code in {'scheduled', 'in_progress'},
        'can_edit_session_fields': status_code == 'scheduled',
        'scheduled_at_raw': scheduled_raw,
        'procedure': procedure,
    }


def _supervisor_discrepancies_data(search='', instrument='', status=''):
    rows = _supervisor_all(
        '/discrepancies', status=status or 'all',
    )
    search_term = (search or '').strip().lower()
    instrument_term = (instrument or '').strip().lower()
    values = []
    for item in rows:
        family = item.get('family') or {}
        instrument_name = family.get('name') or N_('Unspecified instrument')
        if instrument_term and instrument_term not in instrument_name.lower():
            continue
        resolved = bool(item.get('resolved'))
        expected = item.get('expected_quantity') or 0
        counted = item.get('detected_quantity') or 0
        session_id = item.get('session_id')
        procedure = item.get('procedure') or {}
        room = item.get('room') or {}
        kit = item.get('kit') or {}
        operator = item.get('operator') or {}
        reason = item.get('reason') or {}
        haystack = ' '.join(str(value or '') for value in (
            session_id, procedure.get('name'), procedure.get('code'), room.get('name'),
            room.get('code'), kit.get('name'), operator.get('name'), family.get('code'),
            family.get('name'), item.get('description'), reason.get('name'),
        )).lower()
        if search_term and search_term not in haystack:
            continue
        values.append({
            'id': item.get('id'),
            'session_uuid': session_id,
            'session_id': display_session_id(session_id),
            'procedure_name': localize_db_label(
                'procedure', procedure.get('code'), procedure.get('name'),
            ) or N_('Unspecified procedure'),
            'operating_room': room.get('code') or N_('Unspecified room'),
            'kit_name': kit.get('name') or N_('Unspecified kit'),
            'operator_name': operator.get('name') or N_('Unknown operator'),
            'instrument_name': localize_db_text(
                'instrument_family', family.get('code'), family.get('name'),
            ) or instrument_name,
            'expected_quantity': expected,
            'counted_quantity': counted,
            'difference': counted - expected,
            'reason': localize_db_label(
                'discrepancy_reason', reason.get('code'), reason.get('name'),
            ) if reason.get('code') else (item.get('description') or N_('Recorded discrepancy')),
            'status_code': 'resolved' if resolved else 'open',
            'status_label': N_('Resolved') if resolved else N_('Open'),
            'status_variant': 'success' if resolved else 'warning',
            'action_label': N_('View Details') if resolved else N_('Review'),
            'action_url': f'/supervisor/discrepancies/{session_id}/review',
        })
    return values


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
    # Token that verified this request (possibly just refreshed), for server-to-server
    # calls made on the user's behalf (e.g. saving ui_preferences).
    g.auth_access_token = access_token

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


@web_bp.post('/locale')
def set_locale():
    """
    Header language selector. Guests: pef_locale cookie only. Authenticated users: merge
    the locale into user.ui_preferences through the auth service (owner of identity and
    preferences), then mirror it into the cookie (DS01).
    """
    payload = request.get_json(silent=True) or {}
    locale = payload.get('locale') or request.form.get('locale')
    if not isinstance(locale, str) or not is_supported_locale(locale):
        return jsonify({'ok': False, 'error': gettext('Unsupported locale')}), 400

    user = _current_user()
    if user is not None:
        upstream = _auth_request(
            'post', '/preferences',
            token=getattr(g, 'auth_access_token', None),
            json={'locale': locale},
        )
        if upstream is None or not upstream.ok:
            status = 503 if upstream is None else upstream.status_code
            return jsonify({'ok': False, 'error': gettext('Could not save the language preference')}), status
        preferences = (upstream.json() or {}).get('ui_preferences') or {'locale': locale}
        user['ui_preferences'] = preferences
    else:
        preferences = {'locale': locale}

    response = jsonify({'ok': True, 'locale': locale, 'ui_preferences': preferences})
    response.set_cookie(LOCALE_COOKIE, locale, max_age=LOCALE_COOKIE_MAX_AGE, path='/', samesite='Lax')
    g.locale_cookie_set = True
    return response


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
                form_error=gettext('Confirm the physical instrument set before starting.'), **values,
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
            # Shown (localized) names, stored DB names and codes (see view_data search_text).
            hay = row.get('search_text') or str(row.get('session_id') or '').lower()
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
                form_error=gettext('Confirm the physical instrument set before starting.'),
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
                form_error=_error_message(exc), **values,
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
                    form_error=gettext('Enter a whole number for every reported quantity.'),
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
                form_error=_error_message(exc),
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
        {'code': 'shortage', 'label': gettext('Shortage / missing piece')},
        {'code': 'surplus', 'label': gettext('Extra / surplus')},
        {'code': 'operator_error', 'label': gettext('Operator counting error')},
        {'code': 'other', 'label': gettext('Other')},
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
            bind_error = _error_message(exc)
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
        return jsonify({'ok': False, 'error': gettext('Session not in vision capture')}), 409
    try:
        data = fetch_state(UUID(session_id))
    except VisionBridgeError as exc:
        return jsonify({'ok': False, 'error': _error_message(exc)}), exc.status_code
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
        return jsonify({'ok': False, 'error': gettext('Session not in vision capture')}), 409
    video = request.files.get('video')
    if video is None or not video.filename:
        return jsonify({'ok': False, 'error': gettext('Choose a video file (.mp4, .mkv, …).')}), 400
    try:
        bind_session(UUID(session_id))
        result = upload_video(UUID(session_id), video)
    except VisionBridgeError as exc:
        return jsonify({'ok': False, 'error': _error_message(exc)}), exc.status_code
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
        flash(_error_message(exc), 'warning')
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
        return jsonify({'ok': False, 'error': gettext('Session not in progress')}), 409
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
        return jsonify({'ok': False, 'error': _error_message(exc)}), exc.status_code
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
            result_label, result_variant = gettext('Match'), 'success'
        elif diff < 0:
            result_label, result_variant = gettext('Missing'), 'danger'
        else:
            result_label, result_variant = gettext('Extra'), 'warning'
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
                    form_error=gettext('Enter a whole number for every validated count.'),
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
                form_error=_error_message(exc),
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
            state, errors = None, [gettext('Submit the Human Validation form to continue.')]
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
        'message': ngettext(
            'Closed with %(num)d documented discrepancy.',
            'Closed with %(num)d documented discrepancies.',
            len(discrepancies),
        ),
    } if discrepancies else {
        'variant': 'success',
        'message': gettext('All Instruments Accounted For'),
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
# (rf_details.html / rf_review.html / RF POSTs / SupervisorService). V3 demo actions are
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
    service_dashboard = _supervisor_request('GET', '/dashboard')
    sessions = _supervisor_session_rows()
    discrepancies = _supervisor_discrepancies_data()
    today = date.today().isoformat()
    session_statuses = service_dashboard.get('sessions_by_status') or {}
    stats = {
        'sessions_today': sum(
            1 for row in sessions
            if any(str(row.get(key) or '').startswith(today)
                   for key in ('scheduled_at', 'started_at', 'created_at', 'closed_at'))
        ) or len(sessions),
        'active_sessions': session_statuses.get('in_progress', 0)
        or sum(row.get('status_code') not in {'closed', 'aborted'} for row in sessions),
        'pending_reviews': sum(
            row.get('status_code') in {'awaiting_spd_review', 'correction_required'}
            for row in sessions
        ) or sum(row.get('status_label') == 'Open' for row in discrepancies),
        'open_discrepancies': service_dashboard.get('open_discrepancies', 0),
    }
    return _v3_supervisor_page(
        'supervisor/dashboard.html', demo=False,
        dashboard=supervisor_v3_dashboard_from_rf(stats, sessions),
    )


@web_bp.route('/supervisor/sessions')
@require_role('spd_supervisor')
def supervisor_sessions():
    from controllers.rf_ui import active_session_rows

    source = _supervisor_session_rows()
    rf_rows = active_session_rows(source)
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
        filter_options=supervisor_rf_filter_options(source),
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
        weights = gettext('weights OK') if health.get('weights_present') else gettext('weights MISSING')
        return {
            'name': gettext('Vision worker'),
            'status': 'up',
            'detail': f"Local :5002 · {weights} · bound={health.get('bound_sessions', 0)}",
        }
    return {
        'name': gettext('Vision worker'),
        'status': 'down',
        'detail': health.get('error') or gettext('Unreachable — start apps/VisionWorker'),
    }


def _supervisor_health_row():
    try:
        response = requests.get(
            f"{current_app.config['SUPERVISOR_SERVICE_URL']}/health",
            timeout=current_app.config['SUPERVISOR_SERVICE_TIMEOUT'],
        )
        payload = response.json()
        healthy = response.ok and isinstance(payload, dict) and bool(payload.get('ok'))
        return {
            'name': gettext('Supervisor service'),
            'status': 'up' if healthy else 'down',
            'detail': gettext('Local :5004') if healthy else gettext('Health check failed'),
        }
    except (requests.RequestException, ValueError) as exc:
        current_app.logger.warning('SupervisorService health check failed: %s', exc)
        return {
            'name': gettext('Supervisor service'),
            'status': 'down',
            'detail': gettext('Unreachable — start apps/SupervisorService'),
        }


@web_bp.route('/supervisor/services-health')
@require_role('spd_supervisor')
def supervisor_services_health():
    return _page(
        'supervisor/services_health.html',
        services=[
            {'name': 'Auth', 'status': 'direct', 'detail': gettext('Local Auth service (no MS mesh yet)')},
            {'name': gettext('Web portal'), 'status': 'up', 'detail': gettext('BackendWebFlask')},
            _supervisor_health_row(),
            _vision_health_row(),
            {'name': gettext('Evidence worker'), 'status': 'pending', 'detail': gettext('Mongo/Garage evidence cycle — deferred')},
        ],
    )


@web_bp.route('/supervisor/sessions/new', methods=['GET', 'POST'])
@require_role('spd_supervisor')
def supervisor_session_new():
    """RF-SP-02 schedule session + optional Via A privacy confirm (no V3 demo wizard)."""
    from datetime import datetime as dt

    user = _current_user()
    try:
        institution_id = UUID(user['institution_id'])
    except (TypeError, ValueError, KeyError):
        abort(400)
    options = _supervisor_schedule_options()
    selected_kit = (request.values.get('kit_id') or '').strip()
    expected_lines = []
    if selected_kit:
        try:
            expected_lines = _supervisor_kit_expected_lines(selected_kit)
        except SupervisorServiceError:
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
                supervisor_api.post(
                    f'/sessions/{session_id}/privacy-agreement',
                    {
                        'privacy_notice_version_id': request.form.get('privacy_notice_version_id'),
                        'purpose_model_improvement': request.form.get('purpose_model_improvement') == '1',
                    },
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

            result = supervisor_api.post(
                '/sessions',
                {
                    'operator_user_id': request.form.get('operator_user_id'),
                    'procedure_type_id': request.form.get('procedure_type_id'),
                    'room_id': request.form.get('room_id'),
                    'station_id': request.form.get('station_id'),
                    'kit_id': request.form.get('kit_id'),
                    'patient_id': request.form.get('patient_id') or None,
                    'physicians': physicians,
                    'scheduled_at': scheduled_at.isoformat(),
                    'expected_lines': lines,
                },
            )
            if request.form.get('confirm_privacy') == '1' and request.form.get('privacy_notice_version_id'):
                if request.form.get('privacy_confirm_checkbox') == '1':
                    supervisor_api.post(
                        f"/sessions/{result['session_id']}/privacy-agreement",
                        {
                            'privacy_notice_version_id': request.form.get('privacy_notice_version_id'),
                            'purpose_model_improvement': request.form.get('purpose_model_improvement') == '1',
                        },
                    )
            return redirect(url_for('web.supervisor_session_details', session_id=result['session_id']))
        except (SupervisorServiceError, ValueError, TypeError) as exc:
            message = exc.user_message if isinstance(exc, SupervisorServiceError) else str(exc)
            status = exc.status if isinstance(exc, SupervisorServiceError) else 400
            post_kit = (request.form.get('kit_id') or '').strip()
            post_lines = expected_lines
            if post_kit:
                try:
                    post_lines = _supervisor_kit_expected_lines(post_kit)
                except SupervisorServiceError:
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
        result = supervisor_api.call(
            'POST', '/inventory/stock-recheck', json={'lines': lines},
        )
        return jsonify({'lines': result.get('lines') or []})
    except SupervisorServiceError as exc:
        return jsonify({'error': exc.user_message}), exc.status
    except (KeyError, TypeError, ValueError):
        return jsonify({'error': gettext('Invalid stock recheck payload.')}), 400


@web_bp.route('/supervisor/sessions/<session_id>/privacy', methods=['POST'])
@require_role('spd_supervisor')
def supervisor_session_privacy(session_id):
    if request.form.get('privacy_confirm_checkbox') != '1':
        return redirect(url_for('web.supervisor_session_details', session_id=session_id))
    try:
        supervisor_api.post(
            f'/sessions/{session_id}/privacy-agreement',
            {
                'privacy_notice_version_id': request.form.get('privacy_notice_version_id'),
                'purpose_model_improvement': request.form.get('purpose_model_improvement') == '1',
            },
        )
    except SupervisorServiceError as exc:
        flash(exc.user_message, 'danger')
    return redirect(url_for('web.supervisor_session_details', session_id=session_id))


@web_bp.route('/supervisor/sessions/history')
@require_role('spd_supervisor')
def supervisor_session_history():
    from controllers.rf_ui import past_session_rows

    source = past_session_rows(_supervisor_session_rows(), for_role='supervisor')
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
                row.get('search_text') or str(row.get('session_id') or '').lower(),
                str(row.get('operator_name') or '').lower(),
            ])
            if search not in hay:
                continue
        review_code = (row.get('review_status') or {}).get('code') or ''
        if review_status and review_code != review_status:
            continue
        # Kit filter value is the kit id, so it survives a language switch.
        if kit and row.get('kit_id') != kit:
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
    detail = _supervisor_session_detail(session_id)
    if detail is None:
        abort(404)
    families = _supervisor_schedule_options().get('families', [])
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

    detail = _supervisor_session_detail(session_id)
    if detail is None:
        abort(404)
    if not detail.get('can_edit_inventory'):
        return redirect(url_for('web.supervisor_session_details', session_id=session_id))

    options = _supervisor_schedule_options()

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
                supervisor_api.put(
                    f'/sessions/{session_id}',
                    {
                        'operator_user_id': request.form.get('operator_user_id'),
                        'procedure_type_id': request.form.get('procedure_type_id'),
                        'room_id': request.form.get('room_id'),
                        'station_id': request.form.get('station_id'),
                        'kit_id': request.form.get('kit_id'),
                        'patient_id': request.form.get('patient_id') or None,
                        'physicians': physicians,
                        'scheduled_at': scheduled_at.isoformat(),
                        'expected_lines': lines,
                    },
                )
            else:
                supervisor_api.put(
                    f'/sessions/{session_id}/inventory',
                    {'expected_lines': lines},
                )
        except (SupervisorServiceError, ValueError, TypeError) as exc:
            message = exc.user_message if isinstance(exc, SupervisorServiceError) else str(exc)
            status = exc.status if isinstance(exc, SupervisorServiceError) else 400
            return _render_edit(form_error=message, status=status)
        return redirect(url_for('web.supervisor_session_details', session_id=session_id))

    return _render_edit()[0]


@web_bp.route('/supervisor/sessions/<session_id>/close', methods=['POST'])
@require_role('spd_supervisor')
def supervisor_session_close(session_id):
    try:
        supervisor_api.post(
            f'/sessions/{session_id}/close',
            {'material_recovered': request.form.get('material_recovered') == '1'},
        )
    except SupervisorServiceError as exc:
        detail = _supervisor_session_detail(session_id)
        return _page(
            'supervisor/sessions/rf_details.html',
            session=detail,
            session_id=session_id,
            form_error=exc.user_message,
        ), exc.status
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
    discrepancies = _supervisor_discrepancies_data(
        search=filters['search'],
        instrument=filters['instrument'],
        status=filters['status'],
    )
    summary = discrepancies_summary_data(_supervisor_discrepancies_data())
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
    'draft': {'variant': 'info', 'title': N_('Draft saved.'),
              'description': N_('Demo only: the resolution draft is not persisted.')},
    'notes_required': {'variant': 'warning',
                       'title': N_('Review notes are required to mark a case as Reviewed – Unresolved.'),
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
    if use_v3_fixture(session_id):
        return _v3_supervisor_review(session_id)

    detail = _supervisor_session_detail(session_id)
    if detail is None:
        abort(404)

    if request.method == 'POST':
        resolution = (request.form.get('resolution_outcome') or '').strip().lower()
        mark_lost = resolution == 'lost' or request.form.get('mark_lost') == '1'
        mark_recovered = resolution == 'recovered' or request.form.get('mark_recovered') == '1'
        try:
            supervisor_api.post(
                f"/discrepancies/{request.form.get('discrepancy_id')}/resolve",
                {
                    'notes': request.form.get('notes') or '',
                    'mark_lost': mark_lost,
                    'mark_recovered': mark_recovered,
                },
            )
        except (SupervisorServiceError, ValueError, TypeError) as exc:
            message = exc.user_message if isinstance(exc, SupervisorServiceError) else str(exc)
            detail = _supervisor_session_detail(session_id)
            return _page(
                'supervisor/sessions/rf_review.html',
                session=detail,
                session_id=session_id,
                form_error=message,
            ), exc.status if isinstance(exc, SupervisorServiceError) else 400
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
    # Minimal service-backed aggregates; AI-agreement / resolution-time remain n/a stubs.
    return _v3_supervisor_page(
        'supervisor/indicators.html', demo=False,
        indicators=supervisor_indicators_from_rf(
            _supervisor_session_rows(), _supervisor_discrepancies_data(),
        ),
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


# Administrator screens live in controllers/admin_routes.py (backed by AdminService). Imported
# here, after every shared helper above is defined, so they register on this same blueprint.
from controllers import admin_routes  # noqa: E402,F401
