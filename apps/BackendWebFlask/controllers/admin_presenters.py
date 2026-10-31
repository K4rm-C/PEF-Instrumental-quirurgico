from datetime import datetime

import pytz
from flask import current_app, url_for
from flask_babel import gettext, ngettext

from i18n import N_, localize_db_label, localize_db_text


# --- Label helpers (same rules as controllers/view_data.py) ---------------------------------

def category_label(category, fallback=N_('Uncategorized')):
    if not category:
        return gettext(fallback)
    return localize_db_label('instrument_category', category.get('code'), category.get('name'))


def cycle_label(cycle):
    if not cycle:
        return N_('Not specified')
    return localize_db_label('cycle_status', cycle.get('code'), cycle.get('name'))


def family_label(family):
    if not family:
        return gettext('Unknown family')
    return localize_db_text('instrument_family', family.get('code'), family.get('name')) or family.get('code')


def procedure_label(procedure):
    return localize_db_label('procedure', procedure.get('code'), procedure.get('name')) if procedure else ''


def phase_label(phase):
    if not phase or not phase.get('code'):
        return gettext('Unknown phase')
    return localize_db_label('operation_phase', phase.get('code'), phase.get('name'))


def kit_label(kit):
    if not kit or not kit.get('name'):
        return gettext('Unknown kit')
    return localize_db_text('kit', kit.get('id'), kit.get('name'))


def room_label(room):
    return localize_db_text('operating_room', room.get('code'), room.get('name')) if room else ''


def station_label(station):
    return localize_db_text('capture_station', station.get('id'), station.get('name')) if station else ''


def role_label(code, description):
    return localize_db_text('role', code, description)


def status(active, active_label=N_('Active')):
    return (active_label if active else N_('Inactive')), ('success' if active else 'neutral')


def cycle_variant(cycle):
    code = (cycle or {}).get('code', '')
    if code == 'available':
        return 'success'
    if code in {'reserved', 'in_use'}:
        return 'warning'
    if code in {'retired'}:
        return 'danger'
    return 'neutral'


def format_dt(value, with_time=True):
    """ISO-8601 UTC string from AdminService -> local 'YYYY-MM-DD HH:MM' (or date only)."""
    if not value:
        return ''
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return str(value)
    if parsed.tzinfo is not None:
        try:
            parsed = parsed.astimezone(pytz.timezone(current_app.config.get('ADMIN_DISPLAY_TIMEZONE', 'UTC')))
        except pytz.UnknownTimeZoneError:
            pass
    return parsed.strftime('%Y-%m-%d %H:%M' if with_time else '%Y-%m-%d')


def options(rows, label_fn, value_key='id'):
    return [{'value': row[value_key], 'label': label_fn(row)} for row in rows]


def pagination_context(payload, endpoint, filters, **extra_args):
    """Context consumed by components/pagination.html plus shown/total counters."""
    page, total_pages, per_page = payload.get('page', 1), payload.get('total_pages', 1), payload.get('per_page', 20)
    query = {key: value for key, value in {**filters, **extra_args}.items() if value not in (None, '')}
    query['per_page'] = per_page
    return {
        'current_page': page,
        'total_pages': total_pages,
        'per_page': per_page,
        'previous_url': url_for(endpoint, **{**query, 'page': page - 1}) if page > 1 else None,
        'next_url': url_for(endpoint, **{**query, 'page': page + 1}) if page < total_pages else None,
        'go_to_action': url_for(endpoint),
        'pagination_query': query,
        'shown_count': payload.get('shown', 0),
        'total_count': payload.get('total', 0),
    }


# --- Dashboard ------------------------------------------------------------------------------

def dashboard(payload):
    stats = payload.get('stats', {})
    operations = payload.get('operations', {})
    model = payload.get('active_model')
    agreement = operations.get('ai_human_agreement')
    resolution = operations.get('avg_resolution_minutes')
    return {
        'dashboard_stats': {
            'active_users': stats.get('active_users', 0),
            'instrument_families': stats.get('active_families', 0),
            'registered_instruments': stats.get('registered_instruments', 0),
            'active_kits': stats.get('active_kits', 0),
        },
        'secondary_stats': {
            'procedures': stats.get('procedures', 0),
            'capture_stations': stats.get('capture_stations', 0),
            'vision_models': stats.get('vision_models', 0),
            'active_vision_model': stats.get('active_vision_model', 0),
        },
        'catalog_overview': [
            {'label': N_('Instrument Families'), 'value': gettext('%(count)s Active', count=stats.get('active_families', 0))},
            {'label': N_('Instruments'), 'value': gettext('%(count)s Registered', count=stats.get('registered_instruments', 0))},
            {'label': N_('Kits'), 'value': gettext('%(count)s Active', count=stats.get('active_kits', 0))},
            {'label': N_('Procedures'), 'value': gettext('%(count)s Registered', count=stats.get('procedures', 0))},
            {'label': N_('Capture Stations'), 'value': gettext('%(count)s Configured', count=stats.get('capture_stations', 0))},
        ],
        'system_overview': [
            {'label': N_('Inactive Users'), 'value': stats.get('inactive_users', 0)},
            {'label': N_('Configured Roles'), 'value': stats.get('roles', 0)},
            {'label': N_('Operating Rooms'), 'value': stats.get('operating_rooms', 0)},
            {'label': N_('Capture Stations'), 'value': stats.get('capture_stations', 0)},
            {'label': N_('Active Institution'), 'value': stats.get('active_institutions', 0)},
            {'label': N_('Active Vision Model Version'), 'value': model['version_tag'] if model else gettext('None')},
            {'label': N_('Model Classes'), 'value': model['class_count'] if model else 0},
        ],
        'operational_metrics': [
            {'label': N_('Total Sessions'), 'value': operations.get('total_sessions', 0)},
            {'label': N_('Total Discrepancies'), 'value': operations.get('total_discrepancies', 0)},
            {'label': N_('Open Discrepancies'), 'value': operations.get('open_discrepancies', 0)},
            {'label': N_('AI-Human Agreement'),
             'value': f'{agreement:.1f}%' if agreement is not None else gettext('No data yet')},
            {'label': N_('Human Corrections'), 'value': operations.get('human_corrections', 0)},
            {'label': N_('Average Resolution Time'),
             'value': gettext('%(minutes)s min', minutes=f'{resolution:.1f}') if resolution is not None
             else gettext('No data yet')},
        ],
    }


# --- Instrument families --------------------------------------------------------------------

def family_rows(items):
    rows = []
    for item in items:
        label, variant = status(item['active'])
        rows.append({
            'id': item['id'],
            'code': item['code'],
            'name': family_label(item),
            'category': category_label(item.get('category')),
            'function': localize_db_text('instrument_family.function', item['code'], item.get('function_text')) or '',
            'status_label': label,
            'status_variant': variant,
            'edit_url': url_for('web.admin_instrument_family_edit', family_id=item['id']),
        })
    return rows


def family_form_values(item):
    if not item:
        return {'code': '', 'name': '', 'category': '', 'how_to_identify': '',
                'classification_characteristics': '', 'function': '', 'status': 'active'}
    return {
        'code': item['code'],
        'name': item['name'],
        'category': (item.get('category') or {}).get('id', ''),
        'how_to_identify': item.get('identify_text', ''),
        'classification_characteristics': item.get('classify_text', ''),
        'function': item.get('function_text', ''),
        'status': 'active' if item['active'] else 'inactive',
    }


# --- Instruments ----------------------------------------------------------------------------

def instrument_rows(items):
    rows = []
    for item in items:
        label, variant = status(item['active'])
        rows.append({
            'id': item['id'],
            'internal_code': item['internal_code'],
            'instrument_type_label': category_label(item.get('category')),
            'instrument_type_code': (item.get('category') or {}).get('code', ''),
            'instrument_family_name': family_label(item.get('family')) if item.get('family') else N_('Not specified'),
            'cycle_status_label': cycle_label(item.get('cycle_status')),
            'cycle_status_variant': cycle_variant(item.get('cycle_status')),
            'active_status_label': label,
            'active_status_variant': variant,
            'edit_url': url_for('web.admin_instrument_edit', instrument_id=item['id']),
        })
    return rows


def instrument_form_values(item):
    if not item:
        return {'internal_code': '', 'instrument_family': '', 'cycle_status': 'available', 'active_status': 'active'}
    return {
        'internal_code': item['internal_code'],
        'instrument_family': (item.get('family') or {}).get('id', ''),
        'cycle_status': (item.get('cycle_status') or {}).get('code', ''),
        'active_status': 'active' if item['active'] else 'inactive',
    }


def cycle_events(item):
    return [{
        'occurred_at': format_dt(event.get('occurred_at')),
        'cycle_status_label': cycle_label(event.get('cycle_status')) if event.get('cycle_status') else gettext('Unknown'),
        'notes': event.get('notes', ''),
    } for event in (item or {}).get('cycle_events', [])]


# --- Kits -----------------------------------------------------------------------------------

def kit_rows(items):
    rows = []
    for item in items:
        label, variant = status(item['active'])
        rows.append({
            'id': item['id'],
            'name': kit_label(item),
            'version': item['version'],
            'instrument_family_count': item['family_count'],
            'total_expected_instruments': item['total_expected'],
            'procedure_types_label': ', '.join(procedure_label(p) for p in item.get('procedures', [])) or '—',
            'status_label': label,
            'status_variant': variant,
            'edit_url': url_for('web.admin_kit_edit', kit_id=item['id']),
        })
    return rows


def kit_family_options(families):
    values = []
    for family in families:
        category = family.get('category') or {}
        values.append({
            'value': family['id'],
            'label': family_label(family),
            'category_code': category.get('code', ''),
            'category_label': category_label(category, N_('Other')),
            'category_rank': category.get('rank', 999),
            'active_available_count': family.get('available_count', 0),
        })
    values.sort(key=lambda item: (item['category_rank'], item['label']))
    return values


def kit_composition(items, family_options):
    lookup = {option['value']: option for option in family_options}
    rows = []
    for item in items:
        family_id = item['family_id']
        option = lookup.get(family_id, {})
        rows.append({
            'instrument_family_value': family_id,
            'instrument_family_label': option.get('label', gettext('Unknown family')),
            'expected_quantity': item['quantity'],
            'category_code': option.get('category_code', ''),
            'category_label': option.get('category_label', ''),
            'category_rank': option.get('category_rank', 999),
            'active_available_count': option.get('active_available_count', 0),
        })
    rows.sort(key=lambda row: (row['category_rank'], row['instrument_family_label']))
    return rows


# --- Procedures -----------------------------------------------------------------------------

def procedure_rows(items):
    return [{
        'id': item['id'],
        'code': item['code'],
        'name': procedure_label(item),
        'associated_kits_count': item['kit_count'],
        'default_kit_name': kit_label(item['default_kit']) if item.get('default_kit') else N_('Not set'),
        'counting_phases_label': ', '.join(phase_label(p) for p in item.get('phases', [])) or N_('Not configured'),
        'edit_url': url_for('web.admin_procedure_edit', procedure_id=item['id']),
    } for item in items]


def procedure_form_values(item):
    if not item:
        return {'code': '', 'name': ''}, [], []
    kits = [{
        'kit_value': link['kit']['id'],
        'kit_label': kit_label(link['kit']),
        # Editable inputs always show the stored value (see README, i18n).
        'technique_label': link.get('technique_label') or '',
        'is_default': link['is_default'],
        'active': link['active'],
    } for link in item.get('kits', [])]
    phases = [{
        'phase_value': link['phase']['id'],
        'phase_label': phase_label(link['phase']),
        'is_count_required': link['is_count_required'],
        'sort_order': link['sort_order'],
        'active': link['active'],
    } for link in item.get('phases', [])]
    return {'code': item['code'], 'name': item['name']}, kits, phases


# --- Users and roles ------------------------------------------------------------------------

def user_rows(items):
    rows = []
    for item in items:
        label, variant = status(item['active'])
        roles = [{'code': role['code'], 'label': role_label(role['code'], role['description'])}
                 for role in item.get('roles', [])]
        rows.append({
            'id': item['id'],
            'name': item['name'],
            'email': item['email'],
            'institution': (item.get('institution') or {}).get('name', N_('Unknown institution')),
            'roles': roles,
            'role_codes': [role['code'] for role in roles],
            'last_login': format_dt(item.get('last_login_at')) or N_('Never'),
            'status_label': label,
            'status_variant': variant,
            'edit_url': url_for('web.admin_user_edit', user_id=item['id']),
        })
    return rows


def user_form_values(item):
    if not item:
        return {'name': '', 'email': '', 'institution': '', 'assigned_role_codes': [], 'role_label': '',
                'status': 'active', 'active': True}
    return {
        'id': item['id'],
        'name': item['name'],
        'email': item['email'],
        'institution': (item.get('institution') or {}).get('id', ''),
        'assigned_role_codes': [role['code'] for role in item.get('roles', [])],
        'role_label': ' / '.join(role_label(role['code'], role['description']) for role in item.get('roles', [])),
        'status': 'active' if item['active'] else 'inactive',
        'active': item['active'],
        'last_login': format_dt(item.get('last_login_at')),
        'password_updated_at': format_dt(item.get('password_updated_at')),
    }


def role_rows(items):
    return [{
        'id': item['id'],
        'code': item['code'],
        'description': role_label(item['code'], item['description']),
        'institution': (item.get('institution') or {}).get('name', N_('Unknown institution')),
        'assigned_users': item['assigned_users'],
        'system': item.get('system', False),
        'edit_url': url_for('web.admin_role_edit', role_id=item['id']),
        'delete_url': url_for('web.admin_role_delete', role_id=item['id']),
    } for item in items]


def role_form_values(item, institution_name=''):
    if not item:
        return {'code': '', 'description': '', 'institution': institution_name, 'assigned_users': 0, 'system': False}
    return {
        'id': item['id'],
        'code': item['code'],
        'description': item['description'],
        'institution': (item.get('institution') or {}).get('name', ''),
        'assigned_users': item['assigned_users'],
        'system': item.get('system', False),
    }


# --- Vision models --------------------------------------------------------------------------

def checksum_short(checksum):
    if not checksum:
        return '—'
    return f'{checksum[:4]}...{checksum[-3:]}' if len(checksum) > 8 else checksum


def vision_model_rows(items):
    rows = []
    for item in items:
        rows.append({
            'id': item['id'],
            'version_tag': item['version_tag'],
            'classes_label': ngettext('%(num)d Class', '%(num)d Classes', item.get('class_count', 0)),
            'class_count': item.get('class_count', 0),
            'status_label': N_('Active Model') if item['active'] else N_('Inactive'),
            'status_variant': 'info' if item['active'] else 'neutral',
            'published_at': format_dt(item.get('published_at'), with_time=False),
            'checksum': item.get('checksum', ''),
            'checksum_short': checksum_short(item.get('checksum')),
            'asset_label': item.get('asset_reference') or N_('Not registered'),
            'active': item['active'],
            'edit_url': url_for('web.admin_vision_model_edit', model_id=item['id']),
            'activate_url': url_for('web.admin_vision_model_activate', model_id=item['id']),
        })
    return rows


def vision_model_form_values(item):
    if not item:
        return {'version_tag': '', 'model_asset_reference': '', 'active': False, 'checksum': '', 'published_at': ''}
    return {
        'id': item['id'],
        'version_tag': item['version_tag'],
        'model_asset_reference': item.get('asset_reference', ''),
        'active': item['active'],
        'checksum': item.get('checksum', ''),
        'published_at': format_dt(item.get('published_at')),
    }


def vision_model_classes(item):
    return [{
        'yolo_class_id': entry['yolo_class_id'],
        'instrument_family_value': entry['family']['id'],
        'instrument_family_label': family_label(entry['family']),
    } for entry in (item or {}).get('classes', [])]


# --- Configuration --------------------------------------------------------------------------

def room_rows(items):
    rows = []
    for item in items:
        label, variant = status(item['active'])
        rows.append({
            'id': item['id'],
            'code': item['code'],
            'name': room_label(item),
            'status_label': label,
            'status_variant': variant,
            'edit_url': url_for('web.admin_operating_room_edit', room_id=item['id']),
        })
    return rows


def station_rows(items):
    rows = []
    for item in items:
        label, variant = status(item['active'])
        rows.append({
            'id': item['id'],
            'name': station_label(item),
            'operating_room': (item.get('room') or {}).get('code', N_('Unassigned')),
            'roi_configured': item.get('roi_configured', False),
            'status_label': label,
            'status_variant': variant,
            'edit_url': url_for('web.admin_capture_station_edit', station_id=item['id']),
        })
    return rows


# --- Audit log ------------------------------------------------------------------------------

ROLE_SHORT_LABELS = {
    'it_admin': N_('Administrator'),
    'spd_supervisor': N_('SPD Supervisor'),
    'station_operator': N_('Station Operator'),
}

ENTITY_LABELS = {
    'instrument_family': N_('Instrument Family'),
    'instrument': N_('Instrument'),
    'kit': N_('Kit'),
    'procedure': N_('Procedure'),
    'user': N_('User'),
    'role': N_('Role'),
    'vision_model': N_('Vision Model'),
    'institution': N_('Institution'),
    'operating_room': N_('Operating Room'),
    'capture_station': N_('Capture Station'),
    'work_session': N_('Counting Session'),
    'discrepancy': N_('Discrepancy'),
    'patient': N_('Patient'),
    'privacy_notice': N_('Privacy Notice'),
}


def audit_rows(items):
    rows = []
    for item in items:
        user = item.get('user') or {}
        roles = item.get('role_codes') or []
        denied = item.get('outcome') == 'denied'
        rows.append({
            'timestamp': item.get('occurred_at', ''),
            'user_name': user.get('name') or N_('System client'),
            'user_email': user.get('email', ''),
            'role_label': ', '.join(gettext(ROLE_SHORT_LABELS.get(code, code)) for code in roles) or '—',
            'event_label': item.get('action', ''),
            'event_variant': 'danger' if denied else 'default',
            'entity_label': ENTITY_LABELS.get(item.get('entity'), item.get('entity', '')),
            'record_id': item.get('record_label', ''),
            'result_label': N_('DENIED') if denied else N_('SUCCESS'),
            'result_variant': 'danger' if denied else 'success',
            'details': item.get('ip') or '',
        })
    return rows


def audit_filter_options(options):
    return {
        'users': [{'value': user['id'], 'label': user['name']} for user in options.get('users', [])],
        'actions': [{'value': action, 'label': action} for action in options.get('actions', [])],
        'entities': [{'value': entity, 'label': ENTITY_LABELS.get(entity, entity)} for entity in options.get('entities', [])],
    }
