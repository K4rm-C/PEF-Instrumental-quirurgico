"""RF-AD-03 selection catalogs that OP and SPD only *choose* from: operation phases, instrument
categories and discrepancy reasons (procedures themselves live in procedures.py).

- `code` is unique and immutable once created (operation logic and other services refer to codes);
  only the display name (and, for phases, `active`) can change.
- No DELETE: these rows are referenced with ON DELETE RESTRICT. Phases can be deactivated (the only
  table of the three with an `active` column); categories and reasons cannot be hidden in v1 without
  changing OP/SPD queries, so they are never removed.
- Shared catalogs (no institution column in the schema), audited in access_audit.
"""
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import audit
from .common import matches_search, parse_uuid, status_flag, text
from .database import db
from .errors import bad_request, conflict, not_found
from .models import CatDiscrepancyReason, CatInstrumentCategory, CatOperationPhase

CODE_RE = re.compile(r'^[a-z][a-z0-9_]*$')
KINDS = {
    'phases': {'model': CatOperationPhase, 'entity': 'OPERATION_PHASE', 'resource': 'operation_phase',
               'code_max': 32, 'name_max': 80, 'has_active': True},
    'categories': {'model': CatInstrumentCategory, 'entity': 'INSTRUMENT_CATEGORY', 'resource': 'instrument_category',
                   'code_max': 64, 'name_max': 120, 'has_active': False},
    'reasons': {'model': CatDiscrepancyReason, 'entity': 'DISCREPANCY_REASON', 'resource': 'discrepancy_reason',
                'code_max': 64, 'name_max': 160, 'has_active': False},
}


def _kind(kind):
    if kind not in KINDS:
        raise not_found('catalog_not_found', 'Catalog not found.')
    return KINDS[kind]


def _row(spec, row):
    data = {'id': str(row.id), 'code': row.code, 'name': row.name}
    if spec['has_active']:
        data['active'] = row.active
    return data


def list_items(kind, *, search=''):
    spec = _kind(kind)
    model = spec['model']
    rows = [_row(spec, row) for row in db.execute(select(model).order_by(model.code)).scalars()
            if matches_search(search, row.code, row.name)]
    return {'kind': kind, 'has_active': spec['has_active'], 'items': rows, 'filters': {'search': search or ''}}


def create_item(kind, payload):
    spec = _kind(kind)
    code = text(payload, 'code', required=True, max_length=spec['code_max'], label='Code').lower()
    if not CODE_RE.match(code):
        raise bad_request('invalid_catalog_code', 'Code may only contain lowercase letters, numbers and underscores.', 'code')
    name = text(payload, 'name', required=True, max_length=spec['name_max'], label='Name')
    values = {'code': code, 'name': name}
    if spec['has_active']:
        values['active'] = status_flag(payload)
    row = spec['model'](**values)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict('catalog_code_exists', 'That code already exists in this catalog.', 'code')
    audit.record(f"CREATE_{spec['entity']}", spec['resource'], row.id)
    db.commit()
    return _row(spec, row)


def update_item(kind, item_id, payload):
    spec = _kind(kind)
    row = db.get(spec['model'], parse_uuid(item_id))
    if row is None:
        raise not_found('catalog_item_not_found', 'Record not found.')
    if 'code' in payload and str(payload.get('code') or '').strip().lower() != row.code:
        raise bad_request('catalog_code_immutable', 'The code cannot be changed once created.', 'code')
    name = text(payload, 'name', required=True, max_length=spec['name_max'], label='Name')
    changed = name != row.name
    row.name = name
    was_active = getattr(row, 'active', None)
    if spec['has_active']:
        row.active = status_flag(payload)
    audit.record_update(spec['entity'], spec['resource'], row.id, was_active=was_active,
                        is_active=getattr(row, 'active', None), changed=changed)
    db.commit()
    return _row(spec, row)
