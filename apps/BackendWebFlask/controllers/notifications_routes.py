"""Notifications page and header bell (all roles), backed by NotificationsService.

Only these routes talk to the service; the bell's count is fetched by the browser after the page has
rendered (static/js/notifications.js), so no page render — and no Operator action — waits on it.
"""

from flask import flash, jsonify, redirect, request, url_for
from flask_babel import gettext

from controllers.routes import _current_user, _page, require_role, web_bp
from services import notifications_remote as remote

ROLES = ('station_operator', 'spd_supervisor', 'it_admin')
PROFILE = {'station_operator': 'operator', 'spd_supervisor': 'supervisor', 'it_admin': 'admin'}


def _session_url_for(role_code):
    """Session detail the recipient is allowed to open (the target route checks ownership/institution again)."""
    if role_code == 'station_operator':
        return lambda session_id: url_for('web.operator_session_details', session_id=session_id)
    if role_code == 'spd_supervisor':
        return lambda session_id: url_for('web.supervisor_session_details', session_id=session_id)
    return lambda session_id: None


def _back_to_list():
    query = {key: request.values.get(key) for key in ('status', 'type', 'page') if request.values.get(key)}
    return redirect(url_for('web.notifications', **query))


@web_bp.route('/notifications')
@require_role(*ROLES)
def notifications():
    user = _current_user()
    filters = {'status': (request.args.get('status') or '').strip(), 'type': (request.args.get('type') or '').strip()}
    data, notice, status = None, None, 200
    if not remote.enabled():
        notice = gettext(remote.DISABLED)
    else:
        try:
            data = remote.page(status=filters['status'], kind=filters['type'], page_number=request.args.get('page', 1),
                               session_url=_session_url_for(user['role_code']))
        except remote.NotificationsServiceError as exc:
            if exc.status == 401:
                return redirect(url_for('web.sign_in', next=request.path))
            notice, status = gettext(remote.UNAVAILABLE), 503
    data = data or {'rows': [], 'page': 1, 'pages': 1, 'total': 0, 'unread': 0}
    query = {key: value for key, value in filters.items() if value}
    return _page(
        'shared/notifications.html',
        profile_role=PROFILE[user['role_code']],
        active_nav_item='notifications',
        notifications=data,
        notification_filters=filters,
        notification_types=[(kind, remote.TITLES[kind]) for kind in remote.TYPES],
        notifications_notice=notice,
        previous_url=url_for('web.notifications', page=data['page'] - 1, **query) if data['page'] > 1 else None,
        next_url=url_for('web.notifications', page=data['page'] + 1, **query) if data['page'] < data['pages'] else None,
    ), status


@web_bp.route('/notifications/<notification_id>/read', methods=['POST'])
@require_role(*ROLES)
def notification_read(notification_id):
    try:
        remote.mark_read(notification_id)
    except remote.NotificationsServiceError as exc:
        if exc.status == 401:
            return redirect(url_for('web.sign_in', next=url_for('web.notifications')))
        flash(gettext('Notification not found.') if exc.status == 404 else gettext(remote.UNAVAILABLE),
              'warning' if exc.status == 404 else 'danger')
        return _back_to_list()
    target = request.form.get('open')
    # Only a path of this portal (the session link the page rendered), never an external URL.
    if target and target.startswith('/') and not target.startswith('//'):
        return redirect(target)
    return _back_to_list()


@web_bp.route('/notifications/read-all', methods=['POST'])
@require_role(*ROLES)
def notifications_read_all():
    try:
        result = remote.mark_all_read()
        flash(gettext('%(count)s notification(s) marked as read.', count=result.get('updated', 0)), 'success')
    except remote.NotificationsServiceError as exc:
        if exc.status == 401:
            return redirect(url_for('web.sign_in', next=url_for('web.notifications')))
        flash(gettext(remote.UNAVAILABLE), 'danger')
    return _back_to_list()


@web_bp.route('/notifications/unread-count')
def notifications_unread_count():
    """JSON for the header bell. Never an error page: {available: false} when it cannot answer."""
    if _current_user() is None:
        return jsonify({'available': False, 'unread': 0}), 401
    if not remote.enabled():
        return jsonify({'available': False, 'enabled': False, 'unread': 0})
    try:
        return jsonify({'available': True, 'enabled': True, 'unread': remote.unread_count()})
    except remote.NotificationsServiceError:
        return jsonify({'available': False, 'enabled': True, 'unread': 0})
