"""RF-AD-09 Access audit log (read only, append-only table `access_audit`).

Tenant scope is mandatory and applied in SQL: an administrator only reads rows whose
`institution_id` is their own institution. Rows without an institution (global or anonymous
events) are never shown (restrictive policy). This is not the clinical timeline (count_event).
Filters: user, action, entity, outcome, free text and an inclusive date range in the display
time zone. An empty range (from after to) is not queried (RF-AD-09 "Rango de fechas vacío").
Pagination runs in SQL (LIMIT/OFFSET), no UPDATE/DELETE anywhere.
"""
import uuid
from datetime import date

from sqlalchemy import text as sql

from .config import Config
from .database import db
from .errors import bad_request
from .common import PER_PAGE_VALUES, optional_uuid


# resource_type -> SQL returning (id, readable label) for a set of ids.
RECORD_LABEL_SQL = {
    'instrument_family': "SELECT id, code FROM instrument_family WHERE id = ANY(:ids)",
    'instrument': "SELECT id, COALESCE(internal_code, id::text) FROM instrument WHERE id = ANY(:ids)",
    'kit': "SELECT id, name || ' v' || version FROM kit WHERE id = ANY(:ids)",
    'procedure': "SELECT id, code FROM cat_procedure_type WHERE id = ANY(:ids)",
    'user': 'SELECT id, email FROM "user" WHERE id = ANY(:ids)',
    'role': "SELECT id, code FROM role WHERE id = ANY(:ids)",
    'vision_model': "SELECT id, version_tag FROM yolo_model WHERE id = ANY(:ids)",
    'operating_room': "SELECT id, code FROM operating_room WHERE id = ANY(:ids)",
    'capture_station': "SELECT id, name FROM capture_station WHERE id = ANY(:ids)",
    'institution': "SELECT id, name FROM institution WHERE id = ANY(:ids)",
    'privacy_notice': "SELECT id, version FROM privacy_notice_version WHERE id = ANY(:ids)",
    'operation_phase': "SELECT id, code FROM cat_operation_phase WHERE id = ANY(:ids)",
    'instrument_category': "SELECT id, code FROM cat_instrument_category WHERE id = ANY(:ids)",
    'discrepancy_reason': "SELECT id, code FROM cat_discrepancy_reason WHERE id = ANY(:ids)",
    'work_session': "SELECT id, 'WS-' || upper(left(replace(id::text, '-', ''), 8)) FROM work_session WHERE id = ANY(:ids)",
}


def _labels(rows):
    wanted = {}
    for row in rows:
        wanted.setdefault(row['resource_type'], set()).add(row['resource_id'])
    labels = {}
    for resource_type, ids in wanted.items():
        query = RECORD_LABEL_SQL.get(resource_type)
        if not query:
            continue
        for record_id, label in db.execute(sql(query), {'ids': list(ids)}).all():
            labels[(resource_type, record_id)] = label
    return labels


def _parse_date(value, field):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise bad_request('invalid_date', 'Use the YYYY-MM-DD date format.', field)


def _page_numbers(page, per_page):
    try:
        per_page = int(per_page)
    except (TypeError, ValueError):
        per_page = 50
    if per_page not in PER_PAGE_VALUES:
        per_page = 50
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    return page, per_page


def _options(inst):
    scope = {'inst': inst}
    return {
        'users': [{'id': str(r[0]), 'name': r[1], 'email': r[2]} for r in db.execute(sql(
            'SELECT DISTINCT u.id, u.name, u.email FROM access_audit a JOIN "user" u ON u.id = a.actor_user_id '
            'WHERE a.institution_id = :inst ORDER BY u.name'), scope).all()],
        'actions': [r[0] for r in db.execute(sql(
            'SELECT DISTINCT action FROM access_audit WHERE institution_id = :inst ORDER BY 1'), scope).all()],
        'entities': [r[0] for r in db.execute(sql(
            'SELECT DISTINCT resource_type FROM access_audit WHERE institution_id = :inst ORDER BY 1'), scope).all()],
    }


def list_events(actor, *, search='', user='', action='', entity='', outcome='', day='', date_from='', date_to='',
                page=1, per_page=50):
    inst = actor.institution_id
    page, per_page = _page_numbers(page, per_page)
    # `date` (one day) is kept for existing links; it is the range day..day.
    start = _parse_date(date_from or day, 'date_from')
    end = _parse_date(date_to or day, 'date_to')
    user_id = optional_uuid(user)
    filters = {'search': search or '', 'user': str(user_id) if user_id else '', 'action': action or '',
               'entity': entity or '', 'outcome': outcome if outcome in {'success', 'denied'} else '',
               'date_from': start.isoformat() if start else '', 'date_to': end.isoformat() if end else ''}
    result = {'items': [], 'page': 1, 'per_page': per_page, 'total': 0, 'total_pages': 1, 'shown': 0,
              'filters': filters, 'options': _options(inst), 'empty_range': False}
    if start and end and start > end:
        result['empty_range'] = True  # RF-AD-09: an empty date range is not queried
        return result

    conditions = ['a.institution_id = :inst']
    params = {'tz': Config.DISPLAY_TIMEZONE, 'inst': inst}
    if user_id:
        conditions.append('a.actor_user_id = :user_id')
        params['user_id'] = user_id
    if action:
        conditions.append('a.action = :action')
        params['action'] = action
    if entity:
        conditions.append('a.resource_type = :entity')
        params['entity'] = entity
    if filters['outcome']:
        conditions.append('a.outcome = :outcome')
        params['outcome'] = filters['outcome']
    if start:
        conditions.append('(a.occurred_at AT TIME ZONE :tz)::date >= :start')
        params['start'] = start
    if end:
        conditions.append('(a.occurred_at AT TIME ZONE :tz)::date <= :end')
        params['end'] = end
    if search:
        conditions.append(
            "(a.action ILIKE :search OR a.resource_type ILIKE :search OR u.name ILIKE :search "
            "OR u.email ILIKE :search OR a.resource_id::text ILIKE :search)"
        )
        params['search'] = f'%{search.strip()}%'
    where = 'WHERE ' + ' AND '.join(conditions)

    total = db.execute(sql(f'SELECT count(*) FROM access_audit a LEFT JOIN "user" u ON u.id = a.actor_user_id {where}'),
                       params).scalar_one()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    rows = db.execute(sql(f"""
        SELECT a.id, a.occurred_at,
               to_char(a.occurred_at AT TIME ZONE :tz, 'YYYY-MM-DD HH24:MI:SS') AS occurred_local,
               a.actor_type, a.actor_user_id, u.name AS user_name, u.email AS user_email,
               a.action, a.resource_type, a.resource_id, a.outcome, host(a.ip) AS ip,
               a.correlation_id,
               (SELECT string_agg(r.code, ',' ORDER BY r.code)
                  FROM user_role ur JOIN role r ON r.id = ur.role_id
                 WHERE ur.user_id = a.actor_user_id) AS role_codes
        FROM access_audit a
        LEFT JOIN "user" u ON u.id = a.actor_user_id
        {where}
        ORDER BY a.occurred_at DESC, a.id
        LIMIT :limit OFFSET :offset
    """), {**params, 'limit': per_page, 'offset': (page - 1) * per_page}).mappings().all()

    labels = _labels(rows)
    result.update({
        'page': page, 'total': total, 'total_pages': total_pages, 'shown': len(rows),
        'items': [{
            'id': str(row['id']),
            'occurred_at': row['occurred_local'],
            'actor_type': row['actor_type'],
            'user': {'id': str(row['actor_user_id']), 'name': row['user_name'], 'email': row['user_email']}
            if row['actor_user_id'] else None,
            'role_codes': (row['role_codes'] or '').split(',') if row['role_codes'] else [],
            'action': row['action'],
            'entity': row['resource_type'],
            'record_id': str(row['resource_id']),
            'record_label': labels.get((row['resource_type'], row['resource_id']))
            or ('—' if row['resource_id'] == uuid.UUID(int=0) else str(row['resource_id'])[:8].upper()),
            'outcome': row['outcome'],
            'ip': row['ip'],
            'correlation_id': row['correlation_id'],
        } for row in rows],
    })
    return result
