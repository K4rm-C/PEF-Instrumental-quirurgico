from flask import Response, abort, flash, make_response, redirect, request, url_for
from flask_babel import gettext

from services.admin_local import facade as api
from services.admin_local.messages import AdminDomainError
from controllers import admin_presenters as present
from controllers.routes import _current_user, _page, require_role, web_bp
from i18n import N_, localized_search_matches


ADMIN = 'it_admin'
SUPERVISOR = 'spd_supervisor'


# --- Helpers ----------------------------------------------------------------------------------

# 403s the user can act on: shown as a message on the page (the answer keeps its 403 status) instead of
# the generic Forbidden page. Role/session denials stay a plain 403.
EXPLAINED_DENIALS = {'shared_catalog_read_only'}


def _is_explained_denial(exc):
    return exc.status == 403 and exc.code in EXPLAINED_DENIALS


def _handle_read_error(exc):
    """Navigation-level errors (session, permission, missing record) end the request."""
    if exc.status == 401:
        return redirect(url_for('web.sign_in', next=request.path))
    if exc.status == 403 and not _is_explained_denial(exc):
        abort(403)
    if exc.status == 404:
        abort(404)
    flash(exc.user_message, 'danger')
    return None


def _fetch(path, default=None, **params):
    """GET from Administración local; on a service error flash it and return `default`."""
    try:
        return api.get(path, **params), None
    except AdminDomainError as exc:
        response = _handle_read_error(exc)
        if response is not None:
            return None, response
        return default, None


def _catalogs(*names):
    data, response = _fetch('/catalogs', {}, names=','.join(names))
    if response is not None:
        abort(response)
    return {name: (data or {}).get(name, []) for name in names}


def _form_error(exc):
    """Return the message to show on a form, or a navigation response for session errors."""
    if exc.status == 401:
        return None, redirect(url_for('web.sign_in', next=request.path))
    if exc.status == 403 and not _is_explained_denial(exc):
        abort(403)
    if exc.status == 404:
        abort(404)
    return exc.user_message, None


def _status_value(name='status'):
    value = request.form.get(name, 'active')
    return value if value in {'active', 'inactive'} else 'active'


def _list_args(*names, status_default='active'):
    values = {name: (request.args.get(name) or '').strip() for name in names}
    if 'status' in values and not values['status']:
        values['status'] = status_default
    values['page'] = request.args.get('page', 1)
    values['per_page'] = request.args.get('per_page', 20)
    return values


def _is_admin():
    user = _current_user() or {}
    return user.get('role_code') == ADMIN


# --- Dashboard ------------------------------------------------------------------------------

@web_bp.route('/admin/dashboard')
@require_role(ADMIN)
def admin_dashboard():
    data, response = _fetch('/dashboard', None)
    if response is not None:
        return response
    context = present.dashboard(data) if data else {
        'dashboard_stats': {}, 'secondary_stats': {}, 'catalog_overview': [], 'system_overview': [],
        'operational_metrics': [], 'session_stats': [], 'health_rows': [], 'section_errors': [],
    }
    return _page('admin/dashboard.html', **context)


# --- Instrument families --------------------------------------------------------------------

@web_bp.route('/admin/instrument-families')
@require_role(ADMIN)
def admin_instrument_families():
    args = _list_args('search', 'category', 'status')
    data, response = _fetch('/families', {}, match=localized_search_matches(
        args['search'], 'instrument_family', 'instrument_category'), **args)
    if response is not None:
        return response
    data = data or {}
    filters = data.get('filters') or {key: args[key] for key in ('search', 'category', 'status')}
    categories = _catalogs('categories')['categories']
    return _page(
        'admin/instrument_families/list.html',
        instrument_families=present.family_rows(data.get('items', [])),
        filters=filters,
        category_filter=[{'value': item['code'], 'label': present.category_label(item)} for item in categories],
        new_instrument_family_url=url_for('web.admin_instrument_family_new'),
        **present.pagination_context(data, 'web.admin_instrument_families', filters),
    )


def _family_payload():
    return {
        'code': request.form.get('code', ''),
        'name': request.form.get('name', ''),
        'category_id': request.form.get('category', ''),
        'identify_text': request.form.get('how_to_identify', ''),
        'classify_text': request.form.get('classification_characteristics', ''),
        'function_text': request.form.get('function', ''),
        'status': _status_value(),
        'confirm_active_model': request.form.get('confirm_active_model', ''),
    }


def _render_family_form(form_mode, family=None, values=None, form_error=None, status=200, family_id=None):
    categories = _catalogs('categories')['categories']
    is_edit = form_mode == 'edit'
    return _page(
        'admin/instrument_families/form.html',
        form_mode=form_mode,
        is_edit=is_edit,
        instrument_family=values or present.family_form_values(family),
        in_active_model=bool((family or {}).get('in_active_model')),
        examples=present.family_examples(family_id, (family or {}).get('examples', [])) if is_edit else [],
        add_example_url=url_for('web.admin_family_example_add', family_id=family_id) if is_edit else None,
        categories=present.options(categories, present.category_label),
        save_url=(url_for('web.admin_instrument_family_edit', family_id=family_id) if is_edit
                  else url_for('web.admin_instrument_family_new')),
        cancel_url=url_for('web.admin_instrument_families'),
        form_error=form_error,
    ), status


def _submitted_family_values(code=None):
    return {
        'code': code if code is not None else request.form.get('code', '').strip(),
        'name': request.form.get('name', '').strip(),
        'category': request.form.get('category', ''),
        'how_to_identify': request.form.get('how_to_identify', ''),
        'classification_characteristics': request.form.get('classification_characteristics', ''),
        'function': request.form.get('function', ''),
        'status': _status_value(),
    }


@web_bp.route('/admin/instrument-families/new', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_instrument_family_new():
    if request.method == 'GET':
        return _render_family_form('create')
    try:
        api.post('/families', _family_payload())
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        return response or _render_family_form('create', values=_submitted_family_values(),
                                                form_error=message, status=exc.status)
    flash(gettext('Instrument family saved.'), 'success')
    return redirect(url_for('web.admin_instrument_families'))


@web_bp.route('/admin/instrument-families/<family_id>/edit', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_instrument_family_edit(family_id):
    family, response = _fetch(f'/families/{family_id}')
    if response is not None:
        return response
    if family is None:
        return redirect(url_for('web.admin_instrument_families'))
    if request.method == 'GET':
        return _render_family_form('edit', family=family, family_id=family_id)
    try:
        api.put(f'/families/{family_id}', _family_payload())
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        return response or _render_family_form('edit', values=_submitted_family_values(family['code']),
                                                form_error=message, status=exc.status, family_id=family_id)
    flash(gettext('Instrument family saved.'), 'success')
    return redirect(url_for('web.admin_instrument_families'))


def _file_response(document):
    """Serve a stored Administration file: images inline, documents as sandboxed attachments."""
    response = Response(document['data'], mimetype=document['content_type'])
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'private, no-store'
    if document.get('attachment'):
        response.headers['Content-Disposition'] = f"attachment; filename=\"{document['filename']}\""
        response.headers['Content-Security-Policy'] = "sandbox; default-src 'none'"
    return response


def _family_example_action(family_id, call, success_message):
    try:
        call()
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        if response is not None:
            return response
        flash(message, 'danger')
    else:
        flash(success_message, 'success')
    return redirect(url_for('web.admin_instrument_family_edit', family_id=family_id) + '#family-examples')


@web_bp.post('/admin/instrument-families/<family_id>/examples')
@require_role(ADMIN)
def admin_family_example_add(family_id):
    payload = {'file': request.files.get('image'), 'example_text': request.form.get('example_text', ''),
               'sort_order': request.form.get('sort_order', '0')}
    return _family_example_action(family_id, lambda: api.post(f'/families/{family_id}/examples', payload),
                                  gettext('Example photo added.'))


@web_bp.post('/admin/instrument-families/<family_id>/examples/<example_id>/edit')
@require_role(ADMIN)
def admin_family_example_edit(family_id, example_id):
    payload = {'example_text': request.form.get('example_text', ''), 'sort_order': request.form.get('sort_order', '0')}
    return _family_example_action(family_id, lambda: api.put(f'/families/{family_id}/examples/{example_id}', payload),
                                  gettext('Example saved.'))


@web_bp.post('/admin/instrument-families/<family_id>/examples/<example_id>/delete')
@require_role(ADMIN)
def admin_family_example_delete(family_id, example_id):
    return _family_example_action(family_id, lambda: api.delete(f'/families/{family_id}/examples/{example_id}'),
                                  gettext('Example removed.'))


@web_bp.get('/admin/instrument-families/<family_id>/examples/<example_id>/image')
@require_role(ADMIN)
def admin_family_example_image(family_id, example_id):
    document, response = _fetch(f'/families/{family_id}/examples/{example_id}/image')
    if response is not None:
        return response
    if document is None:
        abort(404)
    return _file_response(document)


# --- Instruments (administrator and SPD supervisor) ----------------------------------------

@web_bp.route('/admin/instruments')
@require_role(SUPERVISOR)  # RACI: pieces and kits belong to the SPD supervisor, not Administration
def admin_instruments():
    args = _list_args('search', 'type', 'cycle_status', 'status')
    data, response = _fetch('/instruments', {}, match=localized_search_matches(
        args['search'], 'instrument_family'), **args)
    if response is not None:
        return response
    data = data or {}
    filters = data.get('filters') or {key: args[key] for key in ('search', 'type', 'cycle_status', 'status')}
    pagination = present.pagination_context(data, 'web.admin_instruments', filters)
    return _page(
        'admin/instruments/list.html',
        instruments=present.instrument_rows(data.get('items', [])),
        instruments_shown_count=pagination['shown_count'],
        instruments_total_count=pagination['total_count'],
        filters=filters,
        instrument_type_filter=[{'value': option['code'], 'label': present.category_label(option)}
                                for option in data.get('type_options', [])],
        cycle_status_filter=[{'value': option['code'], 'label': present.cycle_label(option)}
                             for option in data.get('cycle_status_options', [])],
        new_instrument_url=url_for('web.admin_instrument_new'),
        **pagination,
    )


def _instrument_payload():
    return {
        'internal_code': request.form.get('internal_code', ''),
        'family_id': request.form.get('instrument_family', ''),
        'cycle_status': request.form.get('cycle_status', ''),
        'status': _status_value('active_status'),
        'change_reason': request.form.get('change_reason', ''),
    }


def _render_instrument_form(form_mode, instrument=None, values=None, form_error=None, status=200,
                            instrument_id=None):
    catalogs = _catalogs('active_families', 'cycle_statuses')
    families = catalogs['active_families']
    current_family = (instrument or {}).get('family')
    if current_family and current_family['id'] not in {item['id'] for item in families}:
        families = families + [current_family]  # keep an inactive family selectable on edit
    is_edit = form_mode == 'edit'
    form_values = present.instrument_form_values(instrument)
    if values:
        form_values.update(values)
        if is_edit and instrument:
            form_values['internal_code'] = instrument['internal_code']
    return _page(
        'admin/instruments/form.html',
        form_mode=form_mode,
        is_edit=is_edit,
        instrument=form_values,
        instrument_families=present.options(families, present.family_label),
        cycle_statuses=[{'value': item['code'], 'label': present.cycle_label(item)}
                        for item in catalogs['cycle_statuses']],
        cycle_events=present.cycle_events(instrument),
        save_url=(url_for('web.admin_instrument_edit', instrument_id=instrument_id) if is_edit
                  else url_for('web.admin_instrument_new')),
        cancel_url=url_for('web.admin_instruments'),
        form_error=form_error,
    ), status


def _submitted_instrument_values():
    return {
        'internal_code': request.form.get('internal_code', '').strip(),
        'instrument_family': request.form.get('instrument_family', ''),
        'cycle_status': request.form.get('cycle_status', ''),
        'active_status': _status_value('active_status'),
        'change_reason': request.form.get('change_reason', ''),
    }


@web_bp.route('/admin/instruments/new', methods=['GET', 'POST'])
@require_role(SUPERVISOR)  # RACI: pieces and kits belong to the SPD supervisor, not Administration
def admin_instrument_new():
    if request.method == 'GET':
        return _render_instrument_form('create')
    try:
        api.post('/instruments', _instrument_payload())
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        return response or _render_instrument_form('create', values=_submitted_instrument_values(),
                                                    form_error=message, status=exc.status)
    flash(gettext('Instrument saved.'), 'success')
    return redirect(url_for('web.admin_instruments'))


@web_bp.route('/admin/instruments/<instrument_id>/edit', methods=['GET', 'POST'])
@require_role(SUPERVISOR)  # RACI: pieces and kits belong to the SPD supervisor, not Administration
def admin_instrument_edit(instrument_id):
    instrument, response = _fetch(f'/instruments/{instrument_id}')
    if response is not None:
        return response
    if instrument is None:
        return redirect(url_for('web.admin_instruments'))
    if request.method == 'GET':
        return _render_instrument_form('edit', instrument=instrument, instrument_id=instrument_id)
    try:
        api.put(f'/instruments/{instrument_id}', _instrument_payload())
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        return response or _render_instrument_form('edit', instrument=instrument,
                                                    values=_submitted_instrument_values(), form_error=message,
                                                    status=exc.status, instrument_id=instrument_id)
    flash(gettext('Instrument saved.'), 'success')
    return redirect(url_for('web.admin_instruments'))


# --- Kits (administrator and SPD supervisor) ------------------------------------------------

@web_bp.route('/admin/kits')
@require_role(SUPERVISOR)  # RACI: pieces and kits belong to the SPD supervisor, not Administration
def admin_kits():
    args = _list_args('search', 'procedure_type', 'status')
    data, response = _fetch('/kits', {}, match=localized_search_matches(
        args['search'], 'kit', 'procedure'), **args)
    if response is not None:
        return response
    data = data or {}
    active_only = data.get('active_only', not _is_admin())
    filters = data.get('filters') or {key: args[key] for key in ('search', 'procedure_type', 'status')}
    pagination = present.pagination_context(data, 'web.admin_kits', filters)
    return _page(
        'admin/kits/list.html',
        kits=present.kit_rows(data.get('items', [])),
        kits_shown_count=pagination['shown_count'],
        kits_total_count=pagination['total_count'],
        filters=filters,
        procedure_type_filter=[{'value': option['code'], 'label': present.procedure_label(option)}
                               for option in data.get('procedure_type_options', [])],
        active_only=active_only,
        new_kit_url=url_for('web.admin_kit_new'),
        **pagination,
    )


def _submitted_kit_rows():
    family_ids = request.form.getlist('instrument_family[]')
    quantities = request.form.getlist('expected_quantity[]')
    rows = []
    for index, family_id in enumerate(family_ids):
        quantity = quantities[index] if index < len(quantities) else ''
        rows.append({'family_id': family_id, 'quantity': quantity})
    return rows


def _kit_payload():
    return {
        'name': request.form.get('name', ''),
        'status': _status_value(),
        'items': _submitted_kit_rows(),
    }


def _render_kit_form(form_mode, kit=None, values=None, form_error=None, status=200, kit_id=None):
    families = _catalogs('families')['families']
    if form_mode == 'create' or not kit:
        families = [family for family in families if family.get('active')]
    else:
        used = {item['family']['id'] for item in kit.get('items', []) if item.get('family')}
        families = [family for family in families if family.get('active') or family['id'] in used]
    family_options = present.kit_family_options(families)
    is_edit = form_mode == 'edit'
    if values is not None:
        kit_values = {'name': values['name'], 'version': (kit or {}).get('version', 1), 'status': values['status']}
        composition_rows = [
            {'family_id': row['family_id'], 'quantity': row['quantity']} for row in values['items']
        ]
    else:
        kit_values = {
            'name': (kit or {}).get('name', ''),
            'version': (kit or {}).get('version', 1),
            'status': 'active' if not kit or kit.get('active') else 'inactive',
        }
        composition_rows = [
            {'family_id': item['family']['id'], 'quantity': item['quantity']}
            for item in (kit or {}).get('items', []) if item.get('family')
        ]
    return _page(
        'admin/kits/form.html',
        form_mode=form_mode,
        is_edit=is_edit,
        kit=kit_values,
        instrument_families=family_options,
        kit_composition=present.kit_composition(composition_rows, family_options),
        save_url=url_for('web.admin_kit_edit', kit_id=kit_id) if is_edit else url_for('web.admin_kit_new'),
        cancel_url=url_for('web.admin_kits'),
        form_error=form_error,
    ), status


@web_bp.route('/admin/kits/new', methods=['GET', 'POST'])
@require_role(SUPERVISOR)  # RACI: pieces and kits belong to the SPD supervisor, not Administration
def admin_kit_new():
    if request.method == 'GET':
        return _render_kit_form('create')
    payload = _kit_payload()
    try:
        api.post('/kits', payload)
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        return response or _render_kit_form('create', values=payload, form_error=message, status=exc.status)
    flash(gettext('Kit saved.'), 'success')
    return redirect(url_for('web.admin_kits'))


@web_bp.route('/admin/kits/<kit_id>/edit', methods=['GET', 'POST'])
@require_role(SUPERVISOR)  # RACI: pieces and kits belong to the SPD supervisor, not Administration
def admin_kit_edit(kit_id):
    kit, response = _fetch(f'/kits/{kit_id}')
    if response is not None:
        return response
    if kit is None:
        return redirect(url_for('web.admin_kits'))
    if request.method == 'GET':
        return _render_kit_form('edit', kit=kit, kit_id=kit_id)
    payload = _kit_payload()
    try:
        saved = api.put(f'/kits/{kit_id}', payload)
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        return response or _render_kit_form('edit', kit=kit, values=payload, form_error=message,
                                            status=exc.status, kit_id=kit_id)
    if saved.get('id') != kit['id']:
        flash(gettext('Kit saved as version %(version)s. The previous version was kept for history.',
                      version=saved.get('version')), 'success')
    else:
        flash(gettext('No changes were made to the kit.'), 'info')
    return redirect(url_for('web.admin_kits'))


# --- Procedures -----------------------------------------------------------------------------

@web_bp.route('/admin/procedures')
@require_role(ADMIN)
def admin_procedures():
    args = _list_args('search')
    args['per_page'] = request.args.get('per_page', 50)
    data, response = _fetch('/procedures', {}, match=localized_search_matches(
        args['search'], 'procedure'), **args)
    if response is not None:
        return response
    data = data or {}
    filters = data.get('filters') or {'search': args['search']}
    pagination = present.pagination_context(data, 'web.admin_procedures', filters)
    return _page(
        'admin/procedures/list.html',
        procedures=present.procedure_rows(data.get('items', [])),
        procedures_shown_count=pagination['shown_count'],
        procedures_total_count=pagination['total_count'],
        filters=filters,
        new_procedure_url=url_for('web.admin_procedure_new'),
        **pagination,
    )


def _int_list(name):
    values = []
    for value in request.form.getlist(name):
        try:
            values.append(int(value))
        except (TypeError, ValueError):
            continue
    return values


def _submitted_procedure():
    """(payload for Administración local, values to re-render the form). Kit links are not sent (D-4)."""
    phase_ids = request.form.getlist('phase[]')
    phase_indices = _int_list('phase_row_index[]')
    sort_orders = request.form.getlist('sort_order[]')
    count_required = set(_int_list('count_required[]'))
    phase_active = set(_int_list('phase_active[]'))

    phases, form_phases = [], []
    for position, phase_id in enumerate(phase_ids):
        row_index = phase_indices[position] if position < len(phase_indices) else position
        order = sort_orders[position] if position < len(sort_orders) else ''
        row = {'phase_id': phase_id, 'sort_order': order,
               'is_count_required': row_index in count_required, 'active': row_index in phase_active}
        phases.append(row)
        form_phases.append({'phase_value': phase_id, 'sort_order': order,
                            'is_count_required': row['is_count_required'], 'active': row['active'],
                            'row_index': row_index})

    procedure = {'code': request.form.get('code', '').strip(), 'name': request.form.get('name', '').strip()}
    return {**procedure, 'phases': phases}, (procedure, form_phases)


def _render_procedure_form(form_mode, procedure=None, values=None, form_error=None, status=200,
                           procedure_id=None):
    catalogs = _catalogs('phases')
    procedure_values, associated_kits, counting_phases = present.procedure_form_values(procedure)
    if values is not None:
        procedure_values, counting_phases = values
    is_edit = form_mode == 'edit'
    return _page(
        'admin/procedures/form.html',
        form_mode=form_mode,
        is_edit=is_edit,
        procedure=procedure_values,
        phases=present.options(catalogs['phases'], present.phase_label),
        associated_kits=associated_kits,
        counting_phases=counting_phases,
        save_url=(url_for('web.admin_procedure_edit', procedure_id=procedure_id) if is_edit
                  else url_for('web.admin_procedure_new')),
        cancel_url=url_for('web.admin_procedures'),
        form_error=form_error,
    ), status


@web_bp.route('/admin/procedures/new', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_procedure_new():
    if request.method == 'GET':
        return _render_procedure_form('create')
    payload, values = _submitted_procedure()
    try:
        api.post('/procedures', payload)
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        return response or _render_procedure_form('create', values=values, form_error=message, status=exc.status)
    flash(gettext('Procedure saved.'), 'success')
    return redirect(url_for('web.admin_procedures'))


@web_bp.route('/admin/procedures/<procedure_id>/edit', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_procedure_edit(procedure_id):
    procedure, response = _fetch(f'/procedures/{procedure_id}')
    if response is not None:
        return response
    if procedure is None:
        return redirect(url_for('web.admin_procedures'))
    if request.method == 'GET':
        return _render_procedure_form('edit', procedure=procedure, procedure_id=procedure_id)
    payload, values = _submitted_procedure()
    try:
        api.put(f'/procedures/{procedure_id}', payload)
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        return response or _render_procedure_form('edit', procedure=procedure, values=values, form_error=message,
                                                  status=exc.status, procedure_id=procedure_id)
    flash(gettext('Procedure saved.'), 'success')
    return redirect(url_for('web.admin_procedures'))


# --- Selection catalogs (RF-AD-03) ----------------------------------------------------------

SELECTION_KINDS = {'phases': N_('Operation Phases'), 'categories': N_('Instrument Categories'),
                   'reasons': N_('Discrepancy Reasons')}


@web_bp.route('/admin/selection-catalogs')
@require_role(ADMIN)
def admin_selection_catalogs():
    return redirect(url_for('web.admin_selection_catalog', kind='phases'))


@web_bp.route('/admin/selection-catalogs/<kind>', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_selection_catalog(kind):
    if kind not in SELECTION_KINDS:
        abort(404)
    form_error, status, values = None, 200, {'code': '', 'name': '', 'status': 'active'}
    if request.method == 'POST':
        values = {'code': request.form.get('code', ''), 'name': request.form.get('name', ''), 'status': _status_value()}
        try:
            api.post(f'/selection-catalogs/{kind}', values)
        except AdminDomainError as exc:
            form_error, response = _form_error(exc)
            if response is not None:
                return response
            status = exc.status
        else:
            flash(gettext('Catalog entry saved.'), 'success')
            return redirect(url_for('web.admin_selection_catalog', kind=kind))
    search = (request.args.get('search') or '').strip()
    data, response = _fetch(f'/selection-catalogs/{kind}', {'items': [], 'has_active': False}, search=search)
    if response is not None:
        return response
    return _page(
        'admin/selection_catalogs/list.html',
        kind=kind,
        tabs=[{'kind': key, 'label': label, 'url': url_for('web.admin_selection_catalog', kind=key)}
              for key, label in SELECTION_KINDS.items()],
        title=SELECTION_KINDS[kind],
        items=[{**item, 'edit_url': url_for('web.admin_selection_catalog_edit', kind=kind, item_id=item['id'])}
               for item in data.get('items', [])],
        has_active=data.get('has_active', False),
        search=search,
        values=values,
        create_url=url_for('web.admin_selection_catalog', kind=kind),
        form_error=form_error,
    ), status


@web_bp.post('/admin/selection-catalogs/<kind>/<item_id>/edit')
@require_role(ADMIN)
def admin_selection_catalog_edit(kind, item_id):
    if kind not in SELECTION_KINDS:
        abort(404)
    try:
        api.put(f'/selection-catalogs/{kind}/{item_id}', {'name': request.form.get('name', ''),
                                                          'status': _status_value()})
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        if response is not None:
            return response
        flash(message, 'danger')
    else:
        flash(gettext('Catalog entry saved.'), 'success')
    return redirect(url_for('web.admin_selection_catalog', kind=kind))


# --- Privacy notices (RF-AD-04) -------------------------------------------------------------

@web_bp.route('/admin/privacy-notices')
@require_role(ADMIN)
def admin_privacy_notices():
    args = _list_args('search', 'status', status_default='all')
    data, response = _fetch('/privacy-notices', {}, **args)
    if response is not None:
        return response
    data = data or {}
    filters = data.get('filters') or {'search': args['search'], 'status': args['status']}
    pagination = present.pagination_context(data, 'web.admin_privacy_notices', filters)
    return _page(
        'admin/privacy_notices/list.html',
        notices=present.privacy_notice_rows(data.get('items', [])),
        filters=filters,
        new_notice_url=url_for('web.admin_privacy_notice_new'),
        **pagination,
    )


@web_bp.route('/admin/privacy-notices/new', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_privacy_notice_new():
    values = {'version': '', 'effective_at': '', 'status': 'active'}
    form_error, status, duplicate = None, 200, False
    if request.method == 'POST':
        values = {'version': request.form.get('version', ''), 'effective_at': request.form.get('effective_at', ''),
                  'status': _status_value()}
        try:
            api.post('/privacy-notices', {**values, 'file': request.files.get('document'),
                                          'confirm_duplicate': request.form.get('confirm_duplicate', '')})
        except AdminDomainError as exc:
            form_error, response = _form_error(exc)
            if response is not None:
                return response
            status, duplicate = exc.status, exc.code == 'privacy_notice_duplicate_hash'
        else:
            flash(gettext('Privacy notice published.'), 'success')
            return redirect(url_for('web.admin_privacy_notices'))
    parameters, _ = _fetch('/settings', {})
    limits = {item['key']: item['value'] for item in (parameters or {}).get('items', [])}
    return _page(
        'admin/privacy_notices/form.html',
        notice=values,
        duplicate_warning=duplicate,
        max_mb=limits.get('privacy_notice_max_mb', 10),
        allowed_mime=limits.get('privacy_notice_allowed_mime', ['application/pdf', 'text/html']),
        save_url=url_for('web.admin_privacy_notice_new'),
        cancel_url=url_for('web.admin_privacy_notices'),
        form_error=form_error,
    ), status


@web_bp.post('/admin/privacy-notices/<notice_id>/<action>')
@require_role(ADMIN)
def admin_privacy_notice_status(notice_id, action):
    if action not in ('activate', 'deactivate'):
        abort(404)
    try:
        api.post(f'/privacy-notices/{notice_id}/{action}')
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        if response is not None:
            return response
        flash(message, 'danger')
    else:
        flash(gettext('Privacy notice activated.') if action == 'activate' else gettext('Privacy notice deactivated.'),
              'success')
    return redirect(url_for('web.admin_privacy_notices'))


@web_bp.get('/admin/privacy-notices/<notice_id>/document')
@require_role(ADMIN)
def admin_privacy_notice_document(notice_id):
    document, response = _fetch(f'/privacy-notices/{notice_id}/document')
    if response is not None:
        return response
    if document is None:
        return redirect(url_for('web.admin_privacy_notices'))
    return _file_response(document)


# --- Users ----------------------------------------------------------------------------------

@web_bp.route('/admin/users')
@require_role(ADMIN)
def admin_users():
    args = _list_args('search', 'role', 'status')
    data, response = _fetch('/users', {}, **args)
    if response is not None:
        return response
    data = data or {}
    catalogs = _catalogs('roles')
    filters = data.get('filters') or {key: args[key] for key in ('search', 'role', 'status')}
    pagination = present.pagination_context(data, 'web.admin_users', filters)
    roles = {}
    for role in catalogs['roles']:
        roles.setdefault(role['code'], present.role_label(role['code'], role['description']))
    return _page(
        'admin/users/list.html',
        users=present.user_rows(data.get('items', [])),
        users_shown_count=pagination['shown_count'],
        users_total_count=pagination['total_count'],
        filters=filters,
        role_filter=[{'value': code, 'label': label} for code, label in roles.items()],
        new_user_url=url_for('web.admin_user_new'),
        **pagination,
    )


def _user_payload(include_password=False):
    payload = {
        'name': request.form.get('name', ''),
        'email': request.form.get('email', ''),
        'role_codes': list(dict.fromkeys(request.form.getlist('roles[]'))),
        'room_ids': list(dict.fromkeys(request.form.getlist('rooms[]'))),
        'status': _status_value(),
    }
    if include_password:
        payload['password'] = request.form.get('password', '')
        payload['confirm_password'] = request.form.get('confirm_password', '')
    return payload


def _render_user_form(form_mode, user=None, values=None, form_error=None, status=200, user_id=None):
    catalogs = _catalogs('roles', 'operating_rooms')
    is_edit = form_mode == 'edit'
    form_values = present.user_form_values(user)
    if values:
        form_values.update({
            'name': values['name'], 'email': values['email'], 'assigned_role_codes': values['role_codes'],
            'assigned_room_ids': values['room_ids'], 'status': values['status'],
        })
    institution, _ = _fetch('/institution', {})
    parameters, _ = _fetch('/settings', {})
    password_min_length = next((item['value'] for item in (parameters or {}).get('items', [])
                                if item['key'] == 'password_min_length'), 8)
    roles = {}
    for role in catalogs['roles']:
        roles.setdefault(role['code'], present.role_label(role['code'], role['description']))
    return _page(
        'admin/users/form.html',
        form_mode=form_mode,
        is_edit=is_edit,
        user=form_values,
        institution_name=(institution or {}).get('name', ''),
        password_min_length=password_min_length,
        roles=[{'value': code, 'label': label} for code, label in roles.items()],
        operating_rooms=[{'value': room['id'], 'label': f"{room['code']} · {present.room_label(room)}"}
                         for room in catalogs['operating_rooms']
                         if room['active'] or room['id'] in form_values.get('assigned_room_ids', [])],
        save_url=url_for('web.admin_user_edit', user_id=user_id) if is_edit else url_for('web.admin_user_new'),
        cancel_url=url_for('web.admin_users'),
        change_password_url=url_for('web.admin_user_password', user_id=user_id) if is_edit else None,
        deactivate_user_url=url_for('web.admin_user_deactivate', user_id=user_id) if is_edit else None,
        activate_user_url=url_for('web.admin_user_activate', user_id=user_id) if is_edit else None,
        is_self=is_edit and str((_current_user() or {}).get('id')) == str(user_id),
        form_error=form_error,
    ), status


@web_bp.route('/admin/users/new', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_user_new():
    if request.method == 'GET':
        return _render_user_form('create')
    payload = _user_payload(include_password=True)
    try:
        api.post('/users', payload)
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        return response or _render_user_form('create', values=payload, form_error=message, status=exc.status)
    flash(gettext('User saved.'), 'success')
    return redirect(url_for('web.admin_users'))


@web_bp.route('/admin/users/<user_id>/edit', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_user_edit(user_id):
    user, response = _fetch(f'/users/{user_id}')
    if response is not None:
        return response
    if user is None:
        return redirect(url_for('web.admin_users'))
    if request.method == 'GET':
        return _render_user_form('edit', user=user, user_id=user_id)
    payload = _user_payload()
    try:
        result = api.put(f'/users/{user_id}', payload)
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        return response or _render_user_form('edit', user=user, values=payload, form_error=message,
                                             status=exc.status, user_id=user_id)
    flash(gettext('User saved.'), 'success')
    _flash_revocation(result)
    return redirect(url_for('web.admin_users'))


def _flash_revocation(result):
    """D-1: sessions are revoked by AuthService after the change; say so when it could not be done."""
    if (result or {}).get('session_revocation') == 'failed':
        flash(gettext('The change was saved, but the active sessions of this user could not be closed. '
                      'They end when their tokens expire; try again later to close them now.'), 'warning')


def _user_action(user_id, path, payload, success_message):
    try:
        result = api.post(f'/users/{user_id}{path}', payload)
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        if response is not None:
            return response
        flash(message, 'danger')
        return redirect(url_for('web.admin_user_edit', user_id=user_id))
    flash(success_message, 'success')
    _flash_revocation(result)
    return redirect(url_for('web.admin_user_edit', user_id=user_id))


@web_bp.post('/admin/users/<user_id>/password')
@require_role(ADMIN)
def admin_user_password(user_id):
    return _user_action(user_id, '/password', {
        'new_password': request.form.get('new_password', ''),
        'confirm_new_password': request.form.get('confirm_new_password', ''),
    }, gettext('Password updated. The user must sign in with the new password.'))


@web_bp.post('/admin/users/<user_id>/deactivate')
@require_role(ADMIN)
def admin_user_deactivate(user_id):
    return _user_action(user_id, '/deactivate', {}, gettext('User deactivated. Their history is kept.'))


@web_bp.post('/admin/users/<user_id>/activate')
@require_role(ADMIN)
def admin_user_activate(user_id):
    return _user_action(user_id, '/activate', {}, gettext('User reactivated.'))


# --- Roles ----------------------------------------------------------------------------------

@web_bp.route('/admin/roles')
@require_role(ADMIN)
def admin_roles():
    args = _list_args('search')
    args['per_page'] = request.args.get('per_page', 50)
    data, response = _fetch('/roles', {}, **args)
    if response is not None:
        return response
    data = data or {}
    filters = data.get('filters') or {'search': args['search']}
    pagination = present.pagination_context(data, 'web.admin_roles', filters)
    return _page(
        'admin/roles/list.html',
        roles=present.role_rows(data.get('items', [])),
        roles_shown_count=pagination['shown_count'],
        roles_total_count=pagination['total_count'],
        filters=filters,
        **pagination,
    )


# RF-AD-06: role codes are fixed, so there is no New Role or Delete Role screen; the domain also
# refuses POST /roles and DELETE /roles/<id> (admin_local.roles.reject_code_change).
def _render_role_form(role, role_id, values=None, form_error=None, status=200):
    role_values = present.role_form_values(role)
    role_values.update(values or {})
    return _page(
        'admin/roles/form.html',
        role=role_values,
        save_url=url_for('web.admin_role_edit', role_id=role_id),
        cancel_url=url_for('web.admin_roles'),
        form_error=form_error,
    ), status


@web_bp.route('/admin/roles/<role_id>/edit', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_role_edit(role_id):
    role, response = _fetch(f'/roles/{role_id}')
    if response is not None:
        return response
    if role is None:
        return redirect(url_for('web.admin_roles'))
    if request.method == 'GET':
        return _render_role_form(role, role_id)
    # Only the description is sent: the code is never editable (the domain rejects a changed code).
    payload = {'description': request.form.get('description', '')}
    try:
        api.put(f'/roles/{role_id}', payload)
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        return response or _render_role_form(role, role_id, values=payload, form_error=message, status=exc.status)
    flash(gettext('Role saved.'), 'success')
    return redirect(url_for('web.admin_roles'))


# --- Vision models --------------------------------------------------------------------------

@web_bp.route('/admin/vision-models')
@require_role(ADMIN)
def admin_vision_models():
    args = _list_args('search', 'status', status_default='all')
    args['per_page'] = request.args.get('per_page', 50)
    data, response = _fetch('/vision-models', {}, **args)
    if response is not None:
        return response
    data = data or {}
    filters = data.get('filters') or {'search': args['search'], 'status': args['status']}
    pagination = present.pagination_context(data, 'web.admin_vision_models', filters)
    return _page(
        'admin/vision_models/list.html',
        vision_models=present.vision_model_rows(data.get('items', [])),
        vision_models_shown_count=pagination['shown_count'],
        vision_models_total_count=pagination['total_count'],
        filters=filters,
        new_vision_model_url=url_for('web.admin_vision_model_new'),
        **pagination,
    )


def _vision_payload():
    """Legacy manual editor (models registered by reference, without class names)."""
    class_ids = request.form.getlist('yolo_class_id[]')
    families = request.form.getlist('instrument_family[]')
    return {
        'asset_reference': request.form.get('model_asset_reference', ''),
        'checksum': request.form.get('checksum', ''),
        'classes': [{'yolo_class_id': class_id, 'family_id': families[index] if index < len(families) else ''}
                    for index, class_id in enumerate(class_ids)],
    }


def _render_vision_form(model, values=None, form_error=None, status=200, model_id=None):
    families = _catalogs('active_families')['active_families']
    form_values = present.vision_model_form_values(model)
    classes = present.vision_model_classes(model)
    if values:
        form_values.update({'model_asset_reference': values.get('asset_reference', ''),
                            'checksum': values.get('checksum', '')})
        labels = {family['id']: present.family_label(family) for family in families}
        classes = [{'yolo_class_id': row['yolo_class_id'], 'instrument_family_value': row['family_id'],
                    'instrument_family_label': labels.get(row['family_id'], '')} for row in values['classes']]
    family_options = present.options(families, present.family_label)
    known = {option['value'] for option in family_options}
    for row in classes:  # keep mapped families selectable even if they were deactivated
        if row['instrument_family_value'] not in known:
            family_options.append({'value': row['instrument_family_value'], 'label': row['instrument_family_label']})
            known.add(row['instrument_family_value'])
    return _page(
        'admin/vision_models/form.html',
        form_mode='edit',
        is_edit=True,
        vision_model=form_values,
        instrument_families=family_options,
        model_classes=classes,
        save_url=url_for('web.admin_vision_model_edit', model_id=model_id),
        cancel_url=url_for('web.admin_vision_models'),
        activate_url=url_for('web.admin_vision_model_activate', model_id=model_id),
        form_error=form_error,
    ), status


def _render_vision_mapping(model, form_error=None, status=200, decisions=None, missing_warning=False):
    families = _catalogs('families')['families']
    decisions = decisions or {}
    rows = []
    for row in model.get('class_names', []):
        key = str(row['yolo_class_id'])
        rows.append({**row, 'selected': decisions.get(key) or row['family_id'] or row['suggested_family_id'] or ''})
    return _page(
        'admin/vision_models/mapping.html',
        vision_model=present.vision_model_form_values(model),
        details=model,
        class_rows=rows,
        families=[{'value': family['id'], 'label': present.family_label(family)
                   + ('' if family['active'] else ' (' + gettext('Inactive') + ')')} for family in families],
        missing_families=model.get('missing_active_families', []),
        missing_warning=missing_warning,
        save_url=url_for('web.admin_vision_model_edit', model_id=model['id']),
        cancel_url=url_for('web.admin_vision_models'),
        activate_url=url_for('web.admin_vision_model_activate', model_id=model['id']),
        deactivate_url=url_for('web.admin_vision_model_deactivate', model_id=model['id']),
        form_error=form_error,
    ), status


@web_bp.route('/admin/vision-models/new', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_vision_model_new():
    """RF-AD-08 step 1: upload the weights (class names read without executing the file)."""
    values = {'version_tag': '', 'architecture': '', 'model_size': ''}
    form_error, status = None, 200
    if request.method == 'POST':
        values = {key: request.form.get(key, '') for key in values}
        try:
            model = api.post('/vision-models', {**values, 'file': request.files.get('weights'),
                                                'names_file': request.files.get('names_file')})
        except AdminDomainError as exc:
            form_error, response = _form_error(exc)
            if response is not None:
                return response
            status = exc.status
        else:
            flash(gettext('Weights uploaded. Map every class to an instrument family to publish the model.'), 'success')
            return redirect(url_for('web.admin_vision_model_edit', model_id=model['id']))
    parameters, _ = _fetch('/settings', {})
    limits = {item['key']: item['value'] for item in (parameters or {}).get('items', [])}
    return _page(
        'admin/vision_models/upload.html',
        vision_model=values,
        max_mb=limits.get('model_weights_max_mb', 500),
        save_url=url_for('web.admin_vision_model_new'),
        cancel_url=url_for('web.admin_vision_models'),
        form_error=form_error,
    ), status


@web_bp.route('/admin/vision-models/<model_id>/edit', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_vision_model_edit(model_id):
    model, response = _fetch(f'/vision-models/{model_id}')
    if response is not None:
        return response
    if model is None:
        return redirect(url_for('web.admin_vision_models'))
    if model.get('class_names'):
        if request.method == 'GET':
            return _render_vision_mapping(model)
        decisions = {key[len('class_'):]: value for key, value in request.form.items() if key.startswith('class_')}
        try:
            api.post(f'/vision-models/{model_id}/publish', {
                'mapping': decisions, 'confirm_missing_families': request.form.get('confirm_missing_families', '')})
        except AdminDomainError as exc:
            message, response = _form_error(exc)
            return response or _render_vision_mapping(model, form_error=message, status=exc.status,
                                                      decisions=decisions,
                                                      missing_warning=exc.code == 'missing_families')
        flash(gettext('Class mapping published.'), 'success')
        return redirect(url_for('web.admin_vision_models'))
    if request.method == 'GET':
        return _render_vision_form(model, model_id=model_id)
    payload = _vision_payload()
    try:
        api.put(f'/vision-models/{model_id}', payload)
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        return response or _render_vision_form(model, values=payload, form_error=message, status=exc.status,
                                               model_id=model_id)
    flash(gettext('Vision model saved.'), 'success')
    return redirect(url_for('web.admin_vision_models'))


@web_bp.route('/admin/vision-models/<model_id>/evaluations', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_vision_model_evaluations(model_id):
    """MS-11 (R17): evaluation records (datasets, metrics, thresholds), regression against other models on the
    same dataset, and the model's history. The values are declared by the administrator, never computed here."""
    form_error, status, values = None, 200, {}
    if request.method == 'POST' and request.form.get('kind') == 'regression':
        # MS-12: JSON report of scripts/model_regression.py (pasted or uploaded); PEF verifies and recomputes it.
        upload = request.files.get('report_file')
        report = (upload.read(2_000_000).decode('utf-8', 'replace') if upload and upload.filename
                  else request.form.get('report', ''))
        values = {'report': report, 'regression_notes': request.form.get('notes', '')}
        try:
            api.post(f'/vision-models/{model_id}/regressions', {'report': report, 'notes': values['regression_notes']})
        except AdminDomainError as exc:
            form_error, response = _form_error(exc)
            if response is not None:
                return response
            status = exc.status
        else:
            flash(gettext('Regression run recorded.'), 'success')
            return redirect(url_for('web.admin_vision_model_evaluations', model_id=model_id))
    elif request.method == 'POST':
        values = {key: request.form.get(key, '') for key in (
            'dataset_name', 'dataset_version', 'dataset_sha256', 'split', 'source', 'images', 'instances',
            'precision_value', 'recall_value', 'map50', 'map50_95', 'conf_threshold', 'iou_threshold', 'evaluated_at',
            'notes', 'per_class')}
        try:
            api.post(f'/vision-models/{model_id}/evaluations', values)
        except AdminDomainError as exc:
            form_error, response = _form_error(exc)
            if response is not None:
                return response
            status = exc.status
        else:
            flash(gettext('Evaluation recorded.'), 'success')
            return redirect(url_for('web.admin_vision_model_evaluations', model_id=model_id))
    overview, response = _fetch(f'/vision-models/{model_id}/evaluations')
    if response is not None:
        return response
    if overview is None:
        return redirect(url_for('web.admin_vision_models'))
    return _page('admin/vision_models/evaluations.html', overview=overview, values=values, form_error=form_error,
                 splits=('validation', 'test', 'field'), sources=('ultralytics_val', 'external', 'manual'),
                 list_url=url_for('web.admin_vision_models')), status


@web_bp.post('/admin/vision-models/<model_id>/activate')
@require_role(ADMIN)
def admin_vision_model_activate(model_id):
    try:
        model = api.post(f'/vision-models/{model_id}/activate')
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        if response is not None:
            return response
        flash(message, 'danger')
        return redirect(url_for('web.admin_vision_models'))
    flash(gettext('Vision model %(version)s is now the active model.', version=model.get('version_tag')), 'success')
    return redirect(url_for('web.admin_vision_models'))


@web_bp.post('/admin/vision-models/<model_id>/deactivate')
@require_role(ADMIN)
def admin_vision_model_deactivate(model_id):
    try:
        api.post(f'/vision-models/{model_id}/deactivate')
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        if response is not None:
            return response
        flash(message, 'danger')
    else:
        flash(gettext('Vision model deactivated.'), 'success')
    return redirect(url_for('web.admin_vision_models'))


# --- Audit log ------------------------------------------------------------------------------

@web_bp.route('/admin/audit-log')
@require_role(ADMIN)
def admin_audit_log():
    args = _list_args('search', 'user', 'action', 'entity', 'outcome', 'date', 'date_from', 'date_to')
    args['per_page'] = request.args.get('per_page', 50)
    data, response = _fetch('/audit-events', {}, **args)
    if response is not None:
        return response
    data = data or {}
    filters = data.get('filters') or {key: args[key] for key in ('search', 'user', 'action', 'entity', 'outcome',
                                                                 'date_from', 'date_to')}
    if data.get('empty_range'):
        flash(gettext('The start date is after the end date: choose a valid date range.'), 'warning')
    pagination = present.pagination_context(data, 'web.admin_audit_log', filters)
    return _page(
        'admin/audit_log.html',
        audit_log_entries=present.audit_rows(data.get('items', [])),
        events_shown_count=pagination['shown_count'],
        events_total_count=pagination['total_count'],
        filters=filters,
        filter_options=present.audit_filter_options(data.get('options', {})),
        **pagination,
    )


# --- Configuration --------------------------------------------------------------------------

@web_bp.route('/admin/configuration')
@require_role(ADMIN)
def admin_configuration():
    institution, response = _fetch('/institution', None)
    if response is not None:
        return response
    rooms, _ = _fetch('/operating-rooms', {})
    stations, _ = _fetch('/capture-stations', {})
    cameras, _ = _fetch('/cameras', {})
    clients, _ = _fetch('/integration-clients', {})
    parameters, _ = _fetch('/settings', {})
    institution_view = None
    if institution:
        label, variant = present.status(institution['active'])
        institution_view = {'name': institution['name'], 'code': institution['code'],
                            'status_label': label, 'status_variant': variant}
    return _page(
        'admin/configuration/index.html',
        institution=institution_view,
        operating_rooms=present.room_rows((rooms or {}).get('items', [])),
        capture_stations=present.station_rows((stations or {}).get('items', [])),
        edit_institution_url=url_for('web.admin_institution_edit'),
        new_operating_room_url=url_for('web.admin_operating_room_new'),
        new_capture_station_url=url_for('web.admin_capture_station_new'),
        cameras=present.camera_rows((cameras or {}).get('items', [])),
        new_camera_url=url_for('web.admin_camera_new'),
        integration_clients=[{**c, 'scopes_text': ', '.join(c['scopes']),
                              'status_label': present.status(c['active'])[0], 'status_variant': present.status(c['active'])[1],
                              'edit_url': url_for('web.admin_integration_client_edit', client_id=c['id'])}
                             for c in (clients or {}).get('items', [])],
        new_integration_client_url=url_for('web.admin_integration_client_new'),
        parameters=present.setting_groups((parameters or {}).get('items', [])),
        model_extensions=(parameters or {}).get('model_extensions', []),
        save_settings_url=url_for('web.admin_settings_save'),
    )


# --- MS-07: machine clients (PEF-DS08) ------------------------------------------------------------


def _integration_client_form(form_mode, client=None, client_id=None):
    is_edit = form_mode == 'edit'
    catalog, response = _fetch('/integration-clients', {})
    if response is not None:
        return response
    values = {'name': (client or {}).get('name', ''), 'scopes': list((client or {}).get('scopes', [])),
              'status': 'active' if not client or client.get('active') else 'inactive'}
    form_error, status = None, 200
    if request.method == 'POST':
        payload = {'name': request.form.get('name', ''), 'scopes': request.form.getlist('scopes'), 'status': _status_value()}
        try:
            result = (api.put(f'/integration-clients/{client_id}', payload) if is_edit
                      else api.post('/integration-clients', payload))
        except AdminDomainError as exc:
            form_error, response = _form_error(exc)
            if response is not None:
                return response
            values.update(payload)
            status = exc.status
        else:
            if result.get('revocation_warning'):
                flash(gettext('The client was deactivated, but its open tokens could not be revoked now; they expire '
                              'within 15 minutes and are refused anyway because the client is inactive.'), 'warning')
            if not is_edit:
                return _integration_secret_page(result)
            flash(gettext('Integration client saved.'), 'success')
            return redirect(url_for('web.admin_configuration'))
    return _page('admin/configuration/integration_client_form.html', is_edit=is_edit, client=values,
                 client_meta=client or {}, scope_options=(catalog or {}).get('scopes', []), form_error=form_error,
                 save_url=request.path, cancel_url=url_for('web.admin_configuration'),
                 rotate_url=url_for('web.admin_integration_client_rotate', client_id=client_id) if is_edit else None), status


def _integration_secret_page(result):
    response = make_response(_page('admin/configuration/integration_client_secret.html', client=result,
                                   back_url=url_for('web.admin_configuration')))
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'
    return response


@web_bp.route('/admin/configuration/integration-clients/new', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_integration_client_new():
    return _integration_client_form('create')


@web_bp.route('/admin/configuration/integration-clients/<client_id>/edit', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_integration_client_edit(client_id):
    client, response = _fetch(f'/integration-clients/{client_id}')
    if response is not None:
        return response
    if client is None:
        return redirect(url_for('web.admin_configuration'))
    return _integration_client_form('edit', client=client, client_id=client_id)


@web_bp.post('/admin/configuration/integration-clients/<client_id>/rotate')
@require_role(ADMIN)
def admin_integration_client_rotate(client_id):
    try:
        result = api.post(f'/integration-clients/{client_id}/rotate-secret', {})
    except AdminDomainError as exc:
        form_error, response = _form_error(exc)
        if response is not None:
            return response
        flash(form_error or gettext('The secret could not be rotated.'), 'warning')
        return redirect(url_for('web.admin_configuration'))
    if result.get('revocation_warning'):
        flash(gettext('The new secret is active, but the tokens issued with the old one could not be revoked now; '
                      'they expire within 15 minutes.'), 'warning')
    return _integration_secret_page(result)


@web_bp.post('/admin/configuration/settings')
@require_role(ADMIN)
def admin_settings_save():
    """RF-AD-10 application parameters of the administrator's institution."""
    parameters, response = _fetch('/settings', {})
    if response is not None:
        return response
    payload = {}
    for item in (parameters or {}).get('items', []):
        key = item['key']
        if item['kind'] == 'mime_list':
            payload[key] = request.form.getlist(f'{key}[]')
        elif item['kind'] == 'bool':
            payload[key] = request.form.get(key) == 'true'
        elif key in request.form:
            payload[key] = request.form.get(key, '')
    try:
        api.put('/settings', payload)
    except AdminDomainError as exc:
        message, response = _form_error(exc)
        if response is not None:
            return response
        flash(message, 'danger')
    else:
        flash(gettext('Application parameters saved.'), 'success')
    return redirect(url_for('web.admin_configuration') + '#application-parameters')


@web_bp.route('/admin/configuration/institution/edit', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_institution_edit():
    institution, response = _fetch('/institution')
    if response is not None:
        return response
    if institution is None:
        return redirect(url_for('web.admin_configuration'))
    values = {'name': institution['name'], 'code': institution['code'],
              'status': 'active' if institution['active'] else 'inactive'}
    form_error, status = None, 200
    if request.method == 'POST':
        payload = {'name': request.form.get('name', ''), 'status': _status_value()}
        try:
            api.put('/institution', payload)
        except AdminDomainError as exc:
            form_error, response = _form_error(exc)
            if response is not None:
                return response
            values.update(payload)
            status = exc.status
        else:
            flash(gettext('Institution information saved.'), 'success')
            return redirect(url_for('web.admin_configuration'))
    return _page(
        'admin/configuration/institution_form.html',
        institution=values,
        save_url=url_for('web.admin_institution_edit'),
        cancel_url=url_for('web.admin_configuration'),
        form_error=form_error,
    ), status


def _room_form(form_mode, room=None, room_id=None):
    is_edit = form_mode == 'edit'
    values = {'code': (room or {}).get('code', ''), 'name': (room or {}).get('name', ''),
              'status': 'active' if not room or room.get('active') else 'inactive'}
    form_error, status = None, 200
    if request.method == 'POST':
        payload = {'code': request.form.get('code', ''), 'name': request.form.get('name', ''),
                   'status': _status_value()}
        try:
            if is_edit:
                api.put(f'/operating-rooms/{room_id}', payload)
            else:
                api.post('/operating-rooms', payload)
        except AdminDomainError as exc:
            form_error, response = _form_error(exc)
            if response is not None:
                return response
            values.update(payload)
            status = exc.status
        else:
            flash(gettext('Operating room saved.'), 'success')
            return redirect(url_for('web.admin_configuration'))
    return _page(
        'admin/configuration/operating_room_form.html',
        form_mode=form_mode,
        is_edit=is_edit,
        operating_room=values,
        save_url=(url_for('web.admin_operating_room_edit', room_id=room_id) if is_edit
                  else url_for('web.admin_operating_room_new')),
        cancel_url=url_for('web.admin_configuration'),
        form_error=form_error,
    ), status


@web_bp.route('/admin/configuration/operating-rooms/new', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_operating_room_new():
    return _room_form('create')


@web_bp.route('/admin/configuration/operating-rooms/<room_id>/edit', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_operating_room_edit(room_id):
    room, response = _fetch(f'/operating-rooms/{room_id}')
    if response is not None:
        return response
    if room is None:
        return redirect(url_for('web.admin_configuration'))
    return _room_form('edit', room=room, room_id=room_id)


ROI_FIELDS = ('x', 'y', 'w', 'h', 'frame_w', 'frame_h')


def _station_form(form_mode, station=None, station_id=None):
    is_edit = form_mode == 'edit'
    rooms, response = _fetch('/operating-rooms', {})
    if response is not None:
        return response
    room_items = (rooms or {}).get('items', [])
    values = {
        'name': (station or {}).get('name', ''),
        'operating_room': ((station or {}).get('room') or {}).get('id', ''),
        'status': 'active' if not station or station.get('active') else 'inactive',
        'roi_configured': (station or {}).get('roi_configured', False),
        'roi': {key: '' if value is None else value
                for key, value in (((station or {}).get('roi')) or dict.fromkeys(ROI_FIELDS)).items()},
    }
    form_error, status = None, 200
    if request.method == 'POST':
        payload = {'name': request.form.get('name', ''), 'room_id': request.form.get('operating_room', ''),
                   'status': _status_value(), 'clear_roi': request.form.get('clear_roi', ''),
                   'roi': {key: request.form.get(f'roi_{key}', '') for key in ROI_FIELDS}}
        try:
            if is_edit:
                api.put(f'/capture-stations/{station_id}', payload)
            else:
                api.post('/capture-stations', payload)
        except AdminDomainError as exc:
            form_error, response = _form_error(exc)
            if response is not None:
                return response
            values.update({'name': payload['name'], 'operating_room': payload['room_id'],
                           'status': payload['status'], 'roi': payload['roi']})
            status = exc.status
        else:
            flash(gettext('Capture station saved.'), 'success')
            return redirect(url_for('web.admin_configuration'))
    room_options = [{'value': room['id'], 'label': f"{room['code']} · {present.room_label(room)}"}
                    for room in room_items if room['active'] or room['id'] == values['operating_room']]
    return _page(
        'admin/configuration/capture_station_form.html',
        form_mode=form_mode,
        is_edit=is_edit,
        capture_station=values,
        operating_rooms=room_options,
        save_url=(url_for('web.admin_capture_station_edit', station_id=station_id) if is_edit
                  else url_for('web.admin_capture_station_new')),
        cancel_url=url_for('web.admin_configuration'),
        form_error=form_error,
    ), status


@web_bp.route('/admin/configuration/capture-stations/new', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_capture_station_new():
    return _station_form('create')


@web_bp.route('/admin/configuration/capture-stations/<station_id>/edit', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_capture_station_edit(station_id):
    station, response = _fetch(f'/capture-stations/{station_id}')
    if response is not None:
        return response
    if station is None:
        return redirect(url_for('web.admin_configuration'))
    return _station_form('edit', station=station, station_id=station_id)


# --- MS-05 cameras ------------------------------------------------------------------------------

CAMERA_FIELDS = ('name', 'station_id', 'device_type', 'resolution_width', 'resolution_height', 'fps')


def _camera_form(form_mode, camera=None, camera_id=None):
    is_edit = form_mode == 'edit'
    stations, response = _fetch('/capture-stations', {})
    if response is not None:
        return response
    station_items = (stations or {}).get('items', [])
    values = {
        'name': (camera or {}).get('name', ''),
        'station_id': ((camera or {}).get('station') or {}).get('id', ''),
        'device_type': (camera or {}).get('device_type', 'usb'),
        'resolution_width': (camera or {}).get('resolution_width') or '',
        'resolution_height': (camera or {}).get('resolution_height') or '',
        'fps': (camera or {}).get('fps') or '',
        'status': 'active' if not camera or camera.get('active') else 'inactive',
        'roi_configured': (camera or {}).get('roi_configured', False),
        'roi': {key: '' if value is None else value
                for key, value in (((camera or {}).get('roi')) or dict.fromkeys(ROI_FIELDS)).items()},
    }
    form_error, status = None, 200
    if request.method == 'POST':
        payload = {key: request.form.get(key, '') for key in CAMERA_FIELDS}
        payload.update({'status': _status_value(), 'clear_roi': request.form.get('clear_roi', ''),
                        'roi': {key: request.form.get(f'roi_{key}', '') for key in ROI_FIELDS}})
        try:
            if is_edit:
                api.put(f'/cameras/{camera_id}', payload)
            else:
                api.post('/cameras', payload)
        except AdminDomainError as exc:
            form_error, response = _form_error(exc)
            if response is not None:
                return response
            values.update({key: payload[key] for key in CAMERA_FIELDS})
            values.update({'status': payload['status'], 'roi': payload['roi']})
            status = exc.status
        else:
            flash(gettext('Camera saved.'), 'success')
            return redirect(url_for('web.admin_configuration'))
    station_options = [{'value': item['id'], 'label': f"{((item.get('room') or {}).get('code') or '—')} · {item['name']}"}
                       for item in station_items if item['active'] or item['id'] == values['station_id']]
    return _page(
        'admin/configuration/camera_form.html',
        form_mode=form_mode,
        is_edit=is_edit,
        camera=values,
        capture_stations=station_options,
        device_types=[{'value': key, 'label': label} for key, label in present.CAMERA_DEVICE_LABELS.items()],
        save_url=(url_for('web.admin_camera_edit', camera_id=camera_id) if is_edit else url_for('web.admin_camera_new')),
        cancel_url=url_for('web.admin_configuration'),
        form_error=form_error,
    ), status


@web_bp.route('/admin/configuration/cameras/new', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_camera_new():
    return _camera_form('create')


@web_bp.route('/admin/configuration/cameras/<camera_id>/edit', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_camera_edit(camera_id):
    camera, response = _fetch(f'/cameras/{camera_id}')
    if response is not None:
        return response
    if camera is None:
        return redirect(url_for('web.admin_configuration'))
    return _camera_form('edit', camera=camera, camera_id=camera_id)


CALIBRATION_FIELDS = ('reference_length_mm', 'measured_pixels', 'frame_width', 'frame_height', 'notes')


@web_bp.route('/admin/configuration/cameras/<camera_id>/calibration', methods=['GET', 'POST'])
@require_role(ADMIN)
def admin_camera_calibration(camera_id):
    """MS-12 (ms-camaras): record a calibration MEASURED by a technician and show the immutable history."""
    camera, response = _fetch(f'/cameras/{camera_id}')
    if response is not None:
        return response
    if camera is None:
        return redirect(url_for('web.admin_configuration'))
    values = {'reference_length_mm': '', 'measured_pixels': '', 'notes': '',
              'frame_width': camera.get('resolution_width') or '', 'frame_height': camera.get('resolution_height') or ''}
    form_error, status = None, 200
    if request.method == 'POST':
        payload = {key: (request.form.get(key) or '').strip() for key in CALIBRATION_FIELDS}
        payload['method'] = 'reference_length'
        try:
            api.post(f'/cameras/{camera_id}/calibrations', payload)
        except AdminDomainError as exc:
            form_error, response = _form_error(exc)
            if response is not None:
                return response
            values.update({key: payload[key] for key in CALIBRATION_FIELDS})
            status = exc.status
        else:
            flash(gettext('Calibration recorded.'), 'success')
            return redirect(url_for('web.admin_camera_calibration', camera_id=camera_id))
    history, response = _fetch(f'/cameras/{camera_id}/calibrations')
    if response is not None:
        return response
    return _page('admin/configuration/camera_calibration.html', camera=camera, values=values,
                 calibrations=(history or {}).get('items', []), form_error=form_error,
                 save_url=url_for('web.admin_camera_calibration', camera_id=camera_id),
                 cancel_url=url_for('web.admin_configuration')), status

