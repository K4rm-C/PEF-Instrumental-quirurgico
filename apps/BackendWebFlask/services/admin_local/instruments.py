from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import audit
from .database import db
from .errors import bad_request, conflict, not_found
from .models import (
    CatInstrumentCategory, CatInstrumentCycleStatus, Instrument, InstrumentCycleEvent,
    InstrumentFamily,
)
from .catalogs import category_rank
from .common import (
    iso, matches_search, matches_status, paginate, parse_uuid, status_filter, status_flag, text,
    utc_now,
)


ENTITY = 'INSTRUMENT'
RESOURCE = 'instrument'


def _lookups():
    families = {row.id: row for row in db.execute(select(InstrumentFamily)).scalars()}
    categories = {row.id: row for row in db.execute(select(CatInstrumentCategory)).scalars()}
    statuses = {row.id: row for row in db.execute(select(CatInstrumentCycleStatus)).scalars()}
    return families, categories, statuses


def _serialize(row, families, categories, statuses):
    family = families.get(row.family_id)
    category = categories.get(family.category_id) if family else None
    cycle = statuses.get(row.cycle_status_id)
    return {
        'id': str(row.id),
        'internal_code': row.internal_code or str(row.id),
        'active': row.active,
        'family': {'id': str(family.id), 'code': family.code, 'name': family.name} if family else None,
        'category': {'id': str(category.id), 'code': category.code, 'name': category.name} if category else None,
        'cycle_status': {'id': str(cycle.id), 'code': cycle.code, 'name': cycle.name} if cycle else None,
    }


def list_instruments(actor, *, search='', type_code='', cycle_status='', status='active',
                     page=1, per_page=20, matches=()):
    families, categories, statuses = _lookups()
    wanted_status = status_filter(status)
    type_code = (type_code or '').strip().lower()
    cycle_status = (cycle_status or '').strip().lower()
    rows = []
    query = (select(Instrument).where(Instrument.institution_id == actor.institution_id)
             .order_by(Instrument.internal_code))
    for row in db.execute(query).scalars():
        item = _serialize(row, families, categories, statuses)
        if type_code and (item['category'] or {}).get('code') != type_code:
            continue
        if cycle_status and (item['cycle_status'] or {}).get('code') != cycle_status:
            continue
        if not matches_status(row.active, wanted_status):
            continue
        family = item['family'] or {}
        if not matches_search(search, row.internal_code, family.get('name'), family.get('code'),
                              matches=matches, keys=(family.get('code'), family.get('name'))):
            continue
        rows.append(item)
    result = paginate(rows, page, per_page)
    result['filters'] = {'search': search or '', 'type': type_code, 'cycle_status': cycle_status,
                         'status': wanted_status}
    result['type_options'] = sorted(
        ({'code': row.code, 'name': row.name, 'rank': category_rank(row.code)} for row in categories.values()),
        key=lambda option: (option['rank'], option['name']),
    )
    result['cycle_status_options'] = sorted(
        ({'code': row.code, 'name': row.name} for row in statuses.values()),
        key=lambda option: option['name'],
    )
    return result


def _owned(actor, instrument_id):
    row = db.get(Instrument, parse_uuid(instrument_id))
    if row is None or row.institution_id != actor.institution_id:
        raise not_found('instrument_not_found', 'Instrument not found.')
    return row


def get_instrument(actor, instrument_id):
    row = _owned(actor, instrument_id)
    families, categories, statuses = _lookups()
    data = _serialize(row, families, categories, statuses)
    events = db.execute(
        select(InstrumentCycleEvent).where(InstrumentCycleEvent.instrument_id == row.id)
        .order_by(InstrumentCycleEvent.occurred_at.desc()).limit(10)
    ).scalars()
    data['cycle_events'] = [{
        'occurred_at': iso(event.occurred_at),
        'cycle_status': ({'code': statuses[event.cycle_status_id].code,
                          'name': statuses[event.cycle_status_id].name}
                         if event.cycle_status_id in statuses else None),
        'notes': event.notes or '',
    } for event in events]
    return data


def _catalog_values(payload):
    family_raw = text(payload, 'family_id', required=True, label='Instrument family',
                      code='family_required')
    cycle_raw = text(payload, 'cycle_status', required=True, label='Cycle status',
                     code='cycle_status_required')
    family = db.get(InstrumentFamily, parse_uuid(
        family_raw, 'invalid_family', 'Choose a valid instrument family and cycle status.', 'family_id'))
    cycle = db.scalar(select(CatInstrumentCycleStatus).where(CatInstrumentCycleStatus.code == cycle_raw))
    if family is None or cycle is None:
        raise bad_request('invalid_family_or_cycle',
                          'Choose a valid instrument family and cycle status.', 'family_id')
    return family, cycle


def create_instrument(actor, payload):
    internal_code = text(payload, 'internal_code', required=True, max_length=64, label='Internal code')
    family, cycle = _catalog_values(payload)
    now = utc_now()
    instrument = Instrument(
        internal_code=internal_code,
        family_id=family.id,
        cycle_status_id=cycle.id,
        institution_id=actor.institution_id,
        active=status_flag(payload),
        created_at=now,
        updated_at=now,
    )
    db.add(instrument)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict('instrument_code_exists',
                       'An instrument with this internal code already exists for the institution.',
                       'internal_code')
    db.add(InstrumentCycleEvent(
        instrument_id=instrument.id,
        cycle_status_id=cycle.id,
        occurred_at=now,
        notes='registered',
    ))
    audit.record(f'CREATE_{ENTITY}', RESOURCE, instrument.id)
    db.commit()
    return get_instrument(actor, instrument.id)


def update_instrument(actor, instrument_id, payload):
    instrument = _owned(actor, instrument_id)
    family, cycle = _catalog_values(payload)
    new_active = status_flag(payload)
    reason = text(payload, 'change_reason', max_length=500, label='Change reason')

    prior_cycle = db.get(CatInstrumentCycleStatus, instrument.cycle_status_id)
    cycle_changed = instrument.cycle_status_id != cycle.id
    active_changed = instrument.active != new_active
    family_changed = instrument.family_id != family.id
    if (cycle_changed or active_changed) and not reason:
        raise bad_request('change_reason_required',
                          'A change reason is required when cycle status or active status changes.',
                          'change_reason')

    was_active = instrument.active
    now = utc_now()
    instrument.family_id = family.id
    instrument.cycle_status_id = cycle.id
    instrument.active = new_active
    instrument.updated_at = now

    if cycle_changed or active_changed:
        notes = []
        if cycle_changed:
            notes.append(f'cycle: {prior_cycle.code if prior_cycle else "unknown"}→{cycle.code}')
        if active_changed:
            notes.append(f'active: {"active" if was_active else "inactive"}→'
                         f'{"active" if new_active else "inactive"}')
        notes.append(f'reason: {reason}')
        db.add(InstrumentCycleEvent(
            instrument_id=instrument.id,
            cycle_status_id=cycle.id,
            occurred_at=now,
            notes='; '.join(notes),
        ))
    audit.record_update(ENTITY, RESOURCE, instrument.id, was_active=was_active,
                        is_active=new_active, changed=family_changed or cycle_changed)
    db.commit()
    return get_instrument(actor, instrument.id)
