"""MS-08: Supervisor screens for physical trays (list, detail with history and sessions, create / edit).

CatalogService owns the trays; SessionService answers which sessions used one. The kit of a tray is a
historical reference: when the kit is superseded the screen says so and the Supervisor decides (nothing is
moved automatically). Expected quantities of sessions never change with a tray.
"""
from flask import abort, flash, redirect, request, url_for
from flask_babel import gettext

from controllers.routes import _v3_supervisor_page, require_role, web_bp
from services import session_remote, trays_remote
from services.rf_session import RfSessionError

FIELDS = ('code', 'name', 'kit_id', 'status', 'notes')


def _view(item):
    return {**item, 'status_label': gettext(trays_remote.STATUS_LABELS.get(item['status'], item['status'])),
            'status_variant': trays_remote.STATUS_VARIANTS.get(item['status'], 'neutral'),
            'url': url_for('web.supervisor_tray_detail', tray_id=item['id'])}


def _unavailable(exc):
    if exc.status == 401:
        return redirect(url_for('web.sign_in', next=request.path))
    if exc.status == 404:
        abort(404)
    return None


@web_bp.route('/supervisor/trays')
@require_role('spd_supervisor')
def supervisor_trays():
    status = request.args.get('status') or None
    if status not in (None, *trays_remote.STATUS_LABELS):
        status = None
    items, notice, code = [], None, 200
    if not trays_remote.enabled():
        notice, code = gettext('The catalog service is not configured in this installation.'), 503
    else:
        try:
            items = [_view(t) for t in trays_remote.trays(status)]
        except trays_remote.TrayError as exc:
            response = _unavailable(exc)
            if response is not None:
                return response
            notice, code = gettext(exc.message), exc.status
    return _v3_supervisor_page('supervisor/trays/list.html', demo=False, trays=items, tray_notice=notice,
                               status_filter=status or '', statuses=trays_remote.STATUS_LABELS,
                               new_tray_url=url_for('web.supervisor_tray_new')), code


def _kit_options(selected=None):
    try:
        kits = trays_remote.kits()
    except trays_remote.TrayError:
        kits = []
    return [{'value': k['id'], 'label': f"{k['name']} · v{k['version']}"} for k in kits]


def _payload():
    data = {name: (request.form.get(name) or '').strip() for name in FIELDS}
    data['kit_id'] = data['kit_id'] or None
    data['name'] = data['name'] or None
    data['notes'] = data['notes'] or None
    return data


@web_bp.route('/supervisor/trays/new', methods=['GET', 'POST'])
@require_role('spd_supervisor')
def supervisor_tray_new():
    values, error, code = {'status': 'active'}, None, 200
    if request.method == 'POST':
        values = _payload()
        try:
            created = trays_remote.create(values)
        except trays_remote.TrayError as exc:
            response = _unavailable(exc)
            if response is not None:
                return response
            error, code = gettext(exc.message), exc.status
        else:
            flash(gettext('Tray saved.'), 'success')
            return redirect(url_for('web.supervisor_tray_detail', tray_id=created['id']))
    return _v3_supervisor_page('supervisor/trays/form.html', demo=False, tray=values, is_edit=False, form_error=error,
                               kit_options=_kit_options(), statuses=trays_remote.STATUS_LABELS,
                               save_url=url_for('web.supervisor_tray_new'),
                               cancel_url=url_for('web.supervisor_trays')), code


@web_bp.route('/supervisor/trays/<tray_id>', methods=['GET', 'POST'])
@require_role('spd_supervisor')
def supervisor_tray_detail(tray_id):
    error, code = None, 200
    try:
        item = trays_remote.tray(tray_id)
    except trays_remote.TrayError as exc:
        response = _unavailable(exc)
        if response is not None:
            return response
        flash(gettext(exc.message), 'warning')
        return redirect(url_for('web.supervisor_trays'))
    if request.method == 'POST':
        payload = _payload()
        try:
            payload['version'] = int(request.form.get('version') or 0)
            item = trays_remote.update(tray_id, payload)
        except ValueError:
            error, code = gettext('The tray could not be saved.'), 400
        except trays_remote.TrayError as exc:
            response = _unavailable(exc)
            if response is not None:
                return response
            error, code = gettext(exc.message), exc.status
            item = trays_remote.tray(tray_id) if exc.status == 409 else item
        else:
            flash(gettext('Tray saved.'), 'success')
            return redirect(url_for('web.supervisor_tray_detail', tray_id=tray_id))
    sessions, sessions_notice = [], None
    try:
        sessions = session_remote.tray_sessions(tray_id)["sessions"] if session_remote.enabled() else []
    except RfSessionError as exc:
        sessions_notice = gettext(exc.message)
    return _v3_supervisor_page('supervisor/trays/detail.html', demo=False, tray=_view(item), form_error=error,
                               sessions=[{**s, 'url': url_for('web.supervisor_session_details', session_id=s['id'])}
                                         for s in sessions],
                               sessions_notice=sessions_notice, kit_options=_kit_options(item.get('kit_id')),
                               statuses=trays_remote.STATUS_LABELS,
                               save_url=url_for('web.supervisor_tray_detail', tray_id=tray_id),
                               back_url=url_for('web.supervisor_trays')), code
