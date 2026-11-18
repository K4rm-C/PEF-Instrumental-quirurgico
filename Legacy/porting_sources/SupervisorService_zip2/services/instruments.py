from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

import audit
from database import db
from errors import ApiError, bad_request, conflict, not_found
from models import (
    CatInstrumentCategory,
    CatInstrumentCycleStatus,
    Instrument,
    InstrumentCycleEvent,
    InstrumentFamily,
)
from services.sessions import _commit, _uuid


def _active(payload):
    value = payload.get('status', 'active')
    if value not in {'active', 'inactive'}:
        raise bad_request('invalid_status', 'status must be active or inactive.', 'status')
    return value == 'active'


def _catalog_values(payload):
    family_id = _uuid(payload.get('family_id'), 'family_id')
    family = db.get(InstrumentFamily, family_id)
    cycle_code = str(payload.get('cycle_status') or '').strip()
    cycle = db.scalar(
        select(CatInstrumentCycleStatus).where(CatInstrumentCycleStatus.code == cycle_code)
    )
    if family is None or cycle is None:
        raise bad_request('invalid_instrument_catalog', 'Choose a valid family and cycle status.')
    return family, cycle


def _owned(actor, instrument_id):
    instrument = db.get(Instrument, instrument_id)
    if instrument is None or instrument.institution_id != actor.institution_id:
        raise not_found('instrument_not_found', 'Instrument not found.')
    return instrument


def _serialize(instrument, family=None, category=None, cycle=None):
    family = family or db.get(InstrumentFamily, instrument.family_id)
    category = category or (
        db.get(CatInstrumentCategory, family.category_id) if family else None
    )
    cycle = cycle or db.get(CatInstrumentCycleStatus, instrument.cycle_status_id)
    return {
        'id': str(instrument.id),
        'internal_code': instrument.internal_code or str(instrument.id),
        'active': instrument.active,
        'family': {'id': str(family.id), 'code': family.code, 'name': family.name} if family else None,
        'category': {'id': str(category.id), 'code': category.code, 'name': category.name}
        if category else None,
        'cycle_status': {'id': str(cycle.id), 'code': cycle.code, 'name': cycle.name}
        if cycle else None,
    }


def list_instruments(actor, *, search='', type_code='', cycle_status='', status='active',
                     page=1, per_page=20, matches=()):
    if status not in {'active', 'inactive', 'all'}:
        raise bad_request('invalid_status', 'status must be active, inactive, or all.', 'status')
    try:
        page = max(1, int(page))
        per_page = min(100, max(1, int(per_page)))
    except (TypeError, ValueError) as exc:
        raise bad_request('invalid_pagination', 'page and per_page must be integers.') from exc
    filters = []
    if status != 'all':
        filters.append(Instrument.active.is_(status == 'active'))
    if type_code:
        filters.append(CatInstrumentCategory.code == type_code)
    if cycle_status:
        filters.append(CatInstrumentCycleStatus.code == cycle_status)
    terms = [value.strip() for value in (search, *matches) if value and value.strip()]
    if terms:
        filters.append(or_(*(
            Instrument.internal_code.ilike(f'%{term}%')
            | InstrumentFamily.code.ilike(f'%{term}%')
            | InstrumentFamily.name.ilike(f'%{term}%')
            for term in terms
        )))
    rows = db.execute(
        select(Instrument, InstrumentFamily, CatInstrumentCategory, CatInstrumentCycleStatus)
        .join(InstrumentFamily, InstrumentFamily.id == Instrument.family_id)
        .join(CatInstrumentCategory, CatInstrumentCategory.id == InstrumentFamily.category_id)
        .join(CatInstrumentCycleStatus, CatInstrumentCycleStatus.id == Instrument.cycle_status_id)
        .where(Instrument.institution_id == actor.institution_id, *filters)
        .order_by(Instrument.internal_code)
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    total = int(db.scalar(
        select(func.count(Instrument.id))
        .join(InstrumentFamily, InstrumentFamily.id == Instrument.family_id)
        .join(CatInstrumentCategory, CatInstrumentCategory.id == InstrumentFamily.category_id)
        .join(CatInstrumentCycleStatus, CatInstrumentCycleStatus.id == Instrument.cycle_status_id)
        .where(Instrument.institution_id == actor.institution_id, *filters)
    ) or 0)
    categories = db.execute(select(CatInstrumentCategory).order_by(CatInstrumentCategory.name)).scalars()
    cycles = db.execute(select(CatInstrumentCycleStatus).order_by(CatInstrumentCycleStatus.name)).scalars()
    return {
        'items': [_serialize(*row) for row in rows],
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': (total + per_page - 1) // per_page,
        'filters': {'search': search, 'type': type_code, 'cycle_status': cycle_status, 'status': status},
        'type_options': [{'code': row.code, 'name': row.name} for row in categories],
        'cycle_status_options': [{'code': row.code, 'name': row.name} for row in cycles],
    }


def get_instrument(actor, instrument_id):
    instrument = _owned(actor, instrument_id)
    result = _serialize(instrument)
    events = db.execute(
        select(InstrumentCycleEvent)
        .where(InstrumentCycleEvent.instrument_id == instrument.id)
        .order_by(InstrumentCycleEvent.occurred_at.desc())
        .limit(20)
    ).scalars()
    result['cycle_events'] = [{
        'occurred_at': event.occurred_at.isoformat(),
        'cycle_status': db.get(CatInstrumentCycleStatus, event.cycle_status_id).code,
        'notes': event.notes or '',
    } for event in events]
    return result


def create_instrument(actor, payload):
    internal_code = str(payload.get('internal_code') or '').strip()
    if not internal_code:
        raise bad_request('internal_code_required', 'Internal code is required.', 'internal_code')
    if len(internal_code) > 64:
        raise bad_request('internal_code_too_long', 'Internal code must be at most 64 characters.', 'internal_code')
    family, cycle = _catalog_values(payload)
    instrument = Instrument(
        internal_code=internal_code,
        family_id=family.id,
        cycle_status_id=cycle.id,
        institution_id=actor.institution_id,
        active=_active(payload),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(instrument)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise conflict('instrument_code_exists', 'An instrument with this internal code already exists.') from exc
    db.add(InstrumentCycleEvent(
        instrument_id=instrument.id,
        cycle_status_id=cycle.id,
        occurred_at=datetime.now(timezone.utc),
        notes='registered',
    ))
    audit.record(actor, 'CREATE_INSTRUMENT', 'instrument', instrument.id)
    _commit()
    return get_instrument(actor, instrument.id)


def update_instrument(actor, instrument_id, payload):
    instrument = _owned(actor, instrument_id)
    family, cycle = _catalog_values(payload)
    active = _active(payload)
    reason = str(payload.get('change_reason') or '').strip()
    cycle_changed = cycle.id != instrument.cycle_status_id
    active_changed = active != instrument.active
    family_changed = family.id != instrument.family_id
    if (cycle_changed or active_changed) and not reason:
        raise bad_request(
            'change_reason_required',
            'A reason is required when cycle status or active status changes.',
            'change_reason',
        )
    old_cycle = db.get(CatInstrumentCycleStatus, instrument.cycle_status_id)
    old_active = instrument.active
    now = datetime.now(timezone.utc)
    instrument.family_id = family.id
    instrument.cycle_status_id = cycle.id
    instrument.active = active
    instrument.updated_at = now
    if cycle_changed or active_changed:
        details = []
        if cycle_changed:
            details.append(f'cycle: {old_cycle.code if old_cycle else "unknown"}→{cycle.code}')
        if active_changed:
            details.append(f'active: {old_active}→{active}')
        details.append(f'reason: {reason}')
        db.add(InstrumentCycleEvent(
            instrument_id=instrument.id,
            cycle_status_id=cycle.id,
            occurred_at=now,
            notes='; '.join(details),
        ))
    if family_changed or cycle_changed:
        audit.record(actor, 'UPDATE_INSTRUMENT', 'instrument', instrument.id)
    if active_changed:
        audit.record(
            actor,
            'ACTIVATE_INSTRUMENT' if active else 'DEACTIVATE_INSTRUMENT',
            'instrument',
            instrument.id,
        )
    _commit()
    return get_instrument(actor, instrument.id)
