import uuid
from datetime import datetime, timezone

from errors import bad_request


STATUS_VALUES = {'active', 'inactive'}
PER_PAGE_VALUES = (10, 20, 50)


def utc_now():
    return datetime.now(timezone.utc)


def iso(value):
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def parse_uuid(value, code='invalid_id', message='Invalid identifier.', field=None):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise bad_request(code, message, field)


def optional_uuid(value):
    try:
        return uuid.UUID(str(value)) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def text(payload, name, *, required=False, max_length=None, label=None, code=None):
    """Trimmed string field. Raises 400 when required and missing or when too long."""
    raw = payload.get(name)
    value = '' if raw is None else str(raw).strip()
    label = label or name.replace('_', ' ').capitalize()
    if required and not value:
        raise bad_request(code or f'{name}_required', f'{label} is required.', name)
    if max_length and len(value) > max_length:
        raise bad_request(
            f'{name}_too_long', f'{label} must contain at most {max_length} characters.', name,
        )
    return value


def status_flag(payload, name='status', default='active'):
    """'active' / 'inactive' (or a boolean `active`) to bool."""
    if 'active' in payload and name not in payload:
        value = payload.get('active')
        if isinstance(value, bool):
            return value
        value = 'active' if str(value).lower() in {'1', 'true', 'yes', 'on'} else 'inactive'
    else:
        value = str(payload.get(name) or default).strip().lower()
    if value not in STATUS_VALUES:
        raise bad_request('invalid_status', 'Choose a valid status.', name)
    return value == 'active'


def integer(value, *, name, minimum=None, maximum=None, message=None):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise bad_request(f'invalid_{name}', message or f'{name} must be a whole number.', name)
    if (minimum is not None and number < minimum) or (maximum is not None and number > maximum):
        raise bad_request(f'invalid_{name}', message or f'{name} is out of range.', name)
    return number


def status_filter(value, default='active'):
    value = (value or default).strip().lower()
    return value if value in {'active', 'inactive', 'all'} else default


def matches_status(active, wanted):
    return wanted == 'all' or (wanted == 'active') == bool(active)


def matches_search(term, *values, matches=(), keys=()):
    """True when `term` appears in `values`, or when one of `keys` (codes, ids, stored names of
    the row and its related catalog rows) is in `matches`: values the portal found by searching
    their localized label (e.g. a Spanish label for an English stored name)."""
    if not term:
        return True
    haystack = ' '.join(str(value) for value in values if value).lower()
    if term.lower() in haystack:
        return True
    wanted = {str(item) for item in matches or ()}
    return bool(wanted) and any(str(key) in wanted for key in keys if key)


def paginate(rows, page=1, per_page=20):
    try:
        per_page = int(per_page)
    except (TypeError, ValueError):
        per_page = 20
    if per_page not in PER_PAGE_VALUES:
        per_page = 20
    total = len(rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    items = rows[start:start + per_page]
    return {
        'items': items,
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': total_pages,
        'shown': len(items),
    }


def ref(row, *fields):
    """Small {id, code/name...} dict for a related catalog row (None-safe)."""
    if row is None:
        return None
    data = {'id': str(row.id)}
    for name in fields:
        data[name] = getattr(row, name)
    return data
