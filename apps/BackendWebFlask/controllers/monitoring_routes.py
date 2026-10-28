"""Administration "System status" and the Supervisor's "Services health", backed by MonitoringService.

Only these two screens talk to the service. A MonitoringService outage only changes them (controlled
notice); no counting, validation, discrepancy or close operation calls it.
"""

from flask import redirect, request, url_for
from flask_babel import gettext

from controllers.routes import _page, require_role, web_bp
from services import monitoring_remote as remote


def _failure(exc):
    """Message for a failed call: (notice, HTTP status of the page) or a redirect to sign in."""
    if exc.status == 401:
        return None, None
    if exc.code == "monitoring_disabled":
        return gettext(remote.DISABLED), 200
    return gettext(remote.UNAVAILABLE), 503


@web_bp.route('/admin/system-status')
@require_role('it_admin')
def admin_system_status():
    window = request.args.get('window') if request.args.get('window') in remote.WINDOWS else '24h'
    filters = {'component': (request.args.get('component') or '').strip(), 'status': (request.args.get('status') or '').strip()}
    overview, history, notice, status = None, None, None, 200
    try:
        overview = remote.admin_overview(window)
        history = remote.admin_history(page=request.args.get('page', 1), **filters)
    except remote.MonitoringServiceError as exc:
        notice, status = _failure(exc)
        if notice is None:
            return redirect(url_for('web.sign_in', next=request.path))
    history = history or {'rows': [], 'page': 1, 'pages': 1, 'total': 0}
    query = {key: value for key, value in {**filters, 'window': window}.items() if value}
    return _page(
        'admin/system_status.html',
        active_nav_item='system_status',
        overview=overview,
        history=history,
        history_filters=filters,
        window=window,
        windows=list(remote.WINDOWS),
        component_options=[(name, label) for name, label in remote.COMPONENT_LABELS.items()],
        status_options=list(remote.STATUS_LABELS.items()),
        monitoring_notice=notice,
        previous_url=url_for('web.admin_system_status', page=history['page'] - 1, **query) if history['page'] > 1 else None,
        next_url=url_for('web.admin_system_status', page=history['page'] + 1, **query) if history['page'] < history['pages'] else None,
    ), status


def supervisor_health_page(extra_status=None):
    """The Supervisor's services health screen (also the page shown when SessionService is unreachable)."""
    overview, notice, status = None, None, 200
    try:
        overview = remote.supervisor_overview()
    except remote.MonitoringServiceError as exc:
        notice, status = _failure(exc)
        if notice is None:
            return redirect(url_for('web.sign_in', next=request.path))
    return _page('supervisor/services_health.html', overview=overview, monitoring_notice=notice), extra_status or status


@web_bp.route('/supervisor/services-health')
@require_role('spd_supervisor')
def supervisor_services_health():
    return supervisor_health_page()
