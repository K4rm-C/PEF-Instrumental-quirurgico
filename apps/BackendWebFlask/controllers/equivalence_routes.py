"""MS-12: Supervisor screen of equivalent instrument families authorized for a kit (CatalogService owns them).

The screen lists the active kits; for the chosen kit it shows its families, the authorizations (active and revoked,
with who / when) and lets the SPD Supervisor authorize or revoke. An equivalence is information for the Operator and
the Supervisor: it never changes expected quantities, the reference count or the final-count rule.
"""
from flask import abort, flash, redirect, request, url_for
from flask_babel import gettext

from controllers.routes import _v3_supervisor_page, require_role, web_bp
from services import equivalences_remote as remote


def _redirect_on(exc):
    if exc.status == 401:
        return redirect(url_for('web.sign_in', next=request.path))
    if exc.status == 404:
        abort(404)
    return None


@web_bp.route('/supervisor/kit-equivalences', methods=['GET', 'POST'])
@require_role('spd_supervisor')
def supervisor_kit_equivalences():
    kit_id = (request.values.get('kit_id') or '').strip() or None
    notice, form_error, code = None, None, 200
    kits, kit, items, families = [], None, [], []
    if not remote.enabled():
        notice, code = gettext('The catalog service is not configured in this installation.'), 503
        return _v3_supervisor_page('supervisor/kit_equivalences.html', demo=False, kits=kits, kit=kit,
                                   equivalences=items, families=families, notice=notice, form_error=form_error,
                                   page_url=url_for('web.supervisor_kit_equivalences')), code
    if request.method == 'POST' and kit_id:
        action = request.form.get('action')
        try:
            if action == 'revoke':
                remote.revoke(kit_id, (request.form.get('equivalence_id') or '').strip())
                flash(gettext('Equivalence revoked. It stays in the history.'), 'success')
            else:
                remote.authorize(kit_id, {'family_id': request.form.get('family_id') or None,
                                          'equivalent_family_id': request.form.get('equivalent_family_id') or None,
                                          'notes': (request.form.get('notes') or '').strip() or None})
                flash(gettext('Equivalence authorized.'), 'success')
        except remote.EquivalenceError as exc:
            response = _redirect_on(exc)
            if response is not None:
                return response
            form_error, code = gettext(exc.message), exc.status
        else:
            return redirect(url_for('web.supervisor_kit_equivalences', kit_id=kit_id))
    try:
        kits = remote.active_kits()
        if kit_id:
            kit = remote.kit(kit_id)
            items = remote.equivalences(kit_id)
            in_kit = {row['family_id'] for row in kit.get('items', [])}
            families = [f for f in remote.active_families() if f['id'] not in in_kit]
    except remote.EquivalenceError as exc:
        response = _redirect_on(exc)
        if response is not None:
            return response
        notice, code = gettext(exc.message), exc.status
    return _v3_supervisor_page('supervisor/kit_equivalences.html', demo=False, kits=kits, kit=kit, equivalences=items,
                               families=families, notice=notice, form_error=form_error,
                               page_url=url_for('web.supervisor_kit_equivalences')), code
