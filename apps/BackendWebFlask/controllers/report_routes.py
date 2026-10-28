"""Supervisor Reports and Indicators (RF), built from SessionService's read-only report endpoints.

Every figure on these screens comes from GET /api/v1/sessions/reports/* (PostgreSQL aggregates of
the Supervisor's institution, computed by the service that owns sessions, discrepancies and counting
facts). The portal only translates labels and formats numbers; it computes nothing, so the reports,
the indicators and the session details cannot disagree. A null metric is shown as "no data", never
as 0. FRONTEND_DEMO_MODE keeps the centralized presentation fixtures.

Without SESSION_SERVICE_URL (or when it does not answer) the screens show a controlled notice:
there is no second, local implementation of the report rules.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app, flash, redirect, request, url_for
from flask_babel import gettext

from controllers.routes import _v3_supervisor_page, require_role, use_v3_fixture, web_bp
from controllers.view_data import (
    SESSION_STATUS_UI,
    supervisor_indicators_from_rf,
    supervisor_v3_filter_options,
    supervisor_v3_reports,
)
from i18n import N_, localize_db_label, localize_db_text
from services import session_remote
from services.rf_session import RfSessionError

DATE_RANGES = (('today', N_('Today')), ('7d', N_('Last 7 Days')), ('30d', N_('Last 30 Days')), ('90d', N_('Last 90 Days')))
_RANGE_DAYS = {'today': 0, '7d': 6, '30d': 29, '90d': 89}
UNAVAILABLE_REPORTS = N_('Reports need the session service, which is not available right now. Try again in a moment.')
NOT_ENABLED_REPORTS = N_('Reports need the session service, which is not configured in this installation.')
LOCAL_INDICATORS_NOTICE = N_('Development mode: partial indicators computed locally (not the SessionService reports).')
NO_REASON, NO_FAMILY, NO_KIT = N_('No reason recorded'), N_('Outside the kit / no family'), N_('No kit')
# Chart series colors: fixed categorical order (validated light / dark; see static/css/pages/reports.css).
REPORT_CARDS = (
    ('session-summary', 'document', N_('Session Summary'), N_('Sessions of the period by status, duration and discrepancies.')),
    ('discrepancy-summary', 'alert_triangle', N_('Discrepancy Summary'),
     N_('Pending and resolved discrepancies by reason and instrument family.')),
    ('kit-activity', 'box', N_('Kit Activity'), N_('Sessions, expected and reported quantities grouped by kit.')),
    ('operator-activity', 'user', N_('Operator Activity'), N_('Counts, human corrections and AI–human agreement by operator.')),
)
STATUS_ORDER = ('scheduled', 'in_progress', 'awaiting_spd_review', 'correction_required', 'closed', 'aborted')


def _zone():
    try:
        return ZoneInfo(current_app.config.get('ADMIN_DISPLAY_TIMEZONE') or 'UTC')
    except Exception:  # noqa: BLE001 - unknown zone name
        return ZoneInfo('UTC')


def _today(zone):
    return datetime.now(zone).date()


def _clean_filters():
    values = {name: (request.args.get(name) or '').strip() for name in ('date_range', 'kit', 'operator', 'session_status')}
    if values['date_range'] not in _RANGE_DAYS:
        values['date_range'] = '30d'
    if values['session_status'] not in STATUS_ORDER:
        values['session_status'] = ''
    return values


def _service_params(filters, zone):
    today = _today(zone)
    params = {'from': (today - timedelta(days=_RANGE_DAYS[filters['date_range']])).isoformat(), 'to': today.isoformat(),
              'tz': str(zone)}
    if filters['kit']:
        params['kit_id'] = filters['kit']
    if filters['operator']:
        params['operator_id'] = filters['operator']
    if filters['session_status']:
        params['status'] = filters['session_status']
    return params


def _reason_label(item):
    return localize_db_label('discrepancy_reason', item['code'], item['name']) if item['code'] else gettext(NO_REASON)


def _family_label(item):
    if not item.get('family_id'):
        return gettext(NO_FAMILY)
    return localize_db_text('instrument_family', item['family_id'], item['name'])


def _kit_label(item):
    return localize_db_text('kit', item['kit_id'], item['name']) if item.get('kit_id') else gettext(NO_KIT)


def _status_label(code):
    return gettext(SESSION_STATUS_UI.get(code, (code,))[0])


def _number(value, suffix=''):
    return None if value is None else f'{value:g}{suffix}'


def _charts(summary, kits, operators):
    """Series for Highcharts, straight from the report (nothing invented, nulls kept out of bars)."""
    statuses = {item['code']: item['count'] for item in summary['sessions']['by_status']}
    return {
        'status': [{'name': _status_label(code), 'y': statuses.get(code, 0), 'slot': index + 1}
                   for index, code in enumerate(STATUS_ORDER) if statuses.get(code)],
        'activity': {'categories': [day['date'] for day in summary['activity']],
                     'sessions': [day['sessions'] for day in summary['activity']],
                     'with_discrepancies': [day['with_discrepancies'] for day in summary['activity']]},
        'reasons': {'categories': [_reason_label(r) for r in summary['discrepancies']['by_reason']],
                    'values': [r['total'] for r in summary['discrepancies']['by_reason']]},
        'kits': {'categories': [_kit_label(k) for k in kits['kits']], 'values': [k['sessions'] for k in kits['kits']]},
        'operators': {'categories': [o['name'] for o in operators['operators']],
                      'sessions': [o['sessions'] for o in operators['operators']],
                      'discrepancies': [o['discrepancies'] for o in operators['operators']]},
    }


def _report_view(summary, kits, operators):
    s, d, a = summary['sessions'], summary['discrepancies'], summary['ai_agreement']
    return {
        'sessions': {**s, 'avg_duration': _number(s['avg_duration_minutes'], ' min'),
                     'with_discrepancies': _number(s['with_discrepancies_pct'], ' %'),
                     'by_status': [{'label': _status_label(code), 'code': code, 'variant': SESSION_STATUS_UI[code][1],
                                    'count': next((i['count'] for i in s['by_status'] if i['code'] == code), 0)}
                                   for code in STATUS_ORDER]},
        'discrepancies': {**d, 'avg_resolution': _number(d['avg_resolution_minutes'], ' min'),
                          'by_reason': [{**r, 'label': _reason_label(r)} for r in d['by_reason']],
                          'by_family': [{**f, 'label': _family_label(f)} for f in d['by_family']]},
        'agreement': {**a, 'value': _number(a['agreement_pct'], ' %')},
        'kits': [{**k, 'label': _kit_label(k)} for k in kits['kits']],
        'kits_page': {key: kits[key] for key in ('page', 'total', 'total_pages')},
        'operators': [{**o, 'agreement': _number(o['agreement_pct'], ' %')} for o in operators['operators']],
        'operators_page': {key: operators[key] for key in ('page', 'total', 'total_pages')},
        'min_pairs': operators['min_pairs'],
        'charts': _charts(summary, kits, operators),
        'generated_at': datetime.now(_zone()).strftime('%Y-%m-%d %H:%M'),
        'window': summary['filters'],
    }


def _page_number(name):
    try:
        return max(1, int(request.args.get(name, 1)))
    except (TypeError, ValueError):
        return 1


@web_bp.route('/supervisor/reports')
@require_role('spd_supervisor')
def supervisor_reports():
    if use_v3_fixture():
        # FRONTEND_DEMO_MODE only: the centralized presentation cards (no database, no service).
        return _v3_supervisor_page('supervisor/reports.html', demo=True, reports=supervisor_v3_reports(),
                                   filter_options=supervisor_v3_filter_options())
    zone, filters = _zone(), _clean_filters()
    report, options, notice, status = None, {'kits': [], 'operators': []}, None, 200
    if not session_remote.enabled():
        notice = gettext(NOT_ENABLED_REPORTS)
    else:
        params = _service_params(filters, zone)
        try:
            options = session_remote.report_options()
            summary = session_remote.report_summary(params)
            kits = session_remote.report_kits({**params, 'page': _page_number('kits_page'), 'per_page': 10})
            operators = session_remote.report_operators({**params, 'page': _page_number('operators_page'), 'per_page': 10})
            report = _report_view(summary, kits, operators)
        except session_remote.SessionServiceUnavailable:
            notice, status = gettext(UNAVAILABLE_REPORTS), 503
        except RfSessionError as exc:
            if exc.status_code == 401:
                return redirect(url_for('web.sign_in', next=request.path))
            # Invalid filter (e.g. a kit that is not of this institution): say so, show nothing.
            flash(gettext(exc.message) if exc.message else gettext('The report could not be generated.'), 'warning')
            status = 400
    query = {key: value for key, value in filters.items() if value}

    def page_url(name, number):
        return url_for('web.supervisor_reports', **{**query, **{k: request.args[k] for k in ('kits_page', 'operators_page')
                                                                  if k in request.args}, name: number}) + (
            '#kit-activity' if name == 'kits_page' else '#operator-activity')
    return _v3_supervisor_page(
        'supervisor/reports.html', demo=False, report=report, report_notice=notice, report_filters=filters,
        date_ranges=DATE_RANGES, report_cards=REPORT_CARDS,
        kit_options=[{'value': k['id'], 'label': localize_db_text('kit', k['id'], k['name']),
                      'inactive': not k['active']} for k in options.get('kits', [])],
        operator_options=[{'value': o['id'], 'label': o['name'], 'inactive': not o['active']}
                          for o in options.get('operators', [])],
        status_options=[{'value': code, 'label': SESSION_STATUS_UI[code][0]} for code in STATUS_ORDER],
        page_url=page_url,
    ), status


# --- Indicators ---------------------------------------------------------------------------------


def _months(zone, count=3):
    first = _today(zone).replace(day=1)
    months = []
    for _ in range(count):
        months.append(first)
        first = (first - timedelta(days=1)).replace(day=1)
    return months


def _month_end(first: date) -> date:
    return ((first + timedelta(days=32)).replace(day=1)) - timedelta(days=1)


def _with_fill(items):
    top = max((item['value'] for item in items), default=0)
    for item in items:
        item['fill_percent'] = round(100 * item['value'] / top) if top else 0
    return items


@web_bp.route('/supervisor/indicators')
@require_role('spd_supervisor')
def supervisor_indicators():
    if use_v3_fixture():
        from controllers.view_data import supervisor_v3_indicators
        return _v3_supervisor_page('supervisor/indicators.html', demo=True, indicators=supervisor_v3_indicators())
    zone = _zone()
    months = _months(zone)
    keys = [m.strftime('%Y-%m') for m in months]
    period = request.args.get('period') if request.args.get('period') in keys else keys[0]
    first = months[keys.index(period)]
    periods = [{'value': key, 'label': m.strftime('%Y-%m')} for key, m in zip(keys, months)]
    def unavailable(message, status):
        return _v3_supervisor_page('supervisor/indicators.html', demo=False, indicators=None,
                                   indicators_notice=gettext(message), periods=periods, period=period), status

    if not session_remote.enabled():
        if not current_app.config.get('SUPERVISOR_INDICATORS_LOCAL_FALLBACK'):
            return unavailable(NOT_ENABLED_REPORTS, 503)
        # Explicit development configuration only: the old partial local aggregates, labeled as such.
        from controllers.routes import _tenant_id, discrepancies_data, sessions_data
        indicators = supervisor_indicators_from_rf(sessions_data(_tenant_id()), discrepancies_data(_tenant_id()))
        return _v3_supervisor_page('supervisor/indicators.html', demo=False, indicators=indicators,
                                   indicators_dev_notice=gettext(LOCAL_INDICATORS_NOTICE))
    # SessionService is configured: it is the only source. If it fails, say so (never another source).
    try:
        summary = session_remote.report_summary({'from': first.isoformat(), 'to': _month_end(first).isoformat(), 'tz': str(zone)})
    except session_remote.SessionServiceUnavailable:
        return unavailable(UNAVAILABLE_REPORTS, 503)
    except RfSessionError as exc:
        if exc.status_code == 401:
            return redirect(url_for('web.sign_in', next=request.path))
        return unavailable(UNAVAILABLE_REPORTS, 503)
    s, d, a = summary['sessions'], summary['discrepancies'], summary['ai_agreement']
    indicators = {
        'period': period,
        'periods': periods,
        'kpis': {
            'total_sessions': s['total'],
            'total_discrepancies': d['total'],
            'ai_human_agreement': _number(a['agreement_pct'], ' %'),
            'ai_pairs': a['pairs'], 'ai_min_pairs': a['min_pairs'],
            'reviewed_unresolved': d['open'],
            'average_resolution_time': _number(d['avg_resolution_minutes'], ' min'),
        },
        'sessions_by_day': _with_fill([{'label': SESSION_STATUS_UI[i['code']][0], 'value': i['count'], 'count': i['count']}
                                       for i in s['by_status'] if i['count']]),
        'discrepancies_by_family': _with_fill([{'label': _family_label(f), 'value': f['total'], 'variant': 'warning'}
                                               for f in d['by_family']]),
        'discrepancies_by_type': _with_fill([{'label': _reason_label(r), 'value': r['total'], 'variant': 'neutral'}
                                             for r in d['by_reason']]),
        'discrepancies_by_status': _with_fill([
            {'label': N_('Open'), 'value': d['open'], 'variant': 'warning'},
            {'label': N_('Resolved'), 'value': d['resolved'], 'variant': 'success'}]),
    }
    return _v3_supervisor_page('supervisor/indicators.html', demo=False, indicators=indicators, live=True)
