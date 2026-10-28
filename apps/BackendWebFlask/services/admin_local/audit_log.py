import uuid
from datetime import date

from sqlalchemy import text as sql

from .config import Config
from .database import db
from .errors import bad_request
from .common import optional_uuid, paginate


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


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise bad_request('invalid_date', 'Use the YYYY-MM-DD date format.', 'date')


def list_events(*, search='', user='', action='', entity='', outcome='', day='', page=1, per_page=50):
    conditions = []
    params = {'tz': Config.DISPLAY_TIMEZONE}
    user_id = optional_uuid(user)
    if user_id:
        conditions.append('a.actor_user_id = :user_id')
        params['user_id'] = user_id
    if action:
        conditions.append('a.action = :action')
        params['action'] = action
    if entity:
        conditions.append('a.resource_type = :entity')
        params['entity'] = entity
    if outcome in {'success', 'denied'}:
        conditions.append('a.outcome = :outcome')
        params['outcome'] = outcome
    wanted_day = _parse_date(day)
    if wanted_day:
        conditions.append('(a.occurred_at AT TIME ZONE :tz)::date = :day')
        params['day'] = wanted_day
    if search:
        conditions.append(
            "(a.action ILIKE :search OR a.resource_type ILIKE :search OR u.name ILIKE :search "
            "OR u.email ILIKE :search OR a.resource_id::text ILIKE :search)"
        )
        params['search'] = f'%{search.strip()}%'
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ''

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
        LIMIT 5000
    """), params).mappings().all()

    result = paginate(list(rows), page, per_page)
    labels = _labels(result['items'])
    result['items'] = [{
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
    } for row in result['items']]

    result['filters'] = {'search': search or '', 'user': str(user_id) if user_id else '', 'action': action or '',
                         'entity': entity or '', 'outcome': outcome or '',
                         'date': wanted_day.isoformat() if wanted_day else ''}
    result['options'] = {
        'users': [{'id': str(r[0]), 'name': r[1], 'email': r[2]} for r in db.execute(sql(
            'SELECT DISTINCT u.id, u.name, u.email FROM access_audit a JOIN "user" u ON u.id = a.actor_user_id '
            'ORDER BY u.name'
        )).all()],
        'actions': [r[0] for r in db.execute(sql('SELECT DISTINCT action FROM access_audit ORDER BY 1')).all()],
        'entities': [r[0] for r in db.execute(sql('SELECT DISTINCT resource_type FROM access_audit ORDER BY 1')).all()],
    }
    return result
