from datetime import datetime, timezone

from sqlalchemy import String, func, select
import audit
from database import db
from errors import ApiError, bad_request, not_found
from models import (
    CaptureStation,
    CatDiscrepancyReason,
    CatEventType,
    CatInstrumentCycleStatus,
    CatSessionStatus,
    CatProcedureType,
    CountEvent,
    Discrepancy,
    Instrument,
    InstrumentCycleEvent,
    InstrumentReservation,
    InstrumentFamily,
    Kit,
    Operation,
    OperatingRoom,
    User,
    WorkSession,
)
from services.sessions import _commit, _owned_session


def list_discrepancies(actor, *, status='', search='', page=1, per_page=20):
    try:
        page = max(1, int(page))
        per_page = min(100, max(1, int(per_page)))
    except (TypeError, ValueError) as exc:
        raise bad_request('invalid_pagination', 'page and per_page must be integers.') from exc

    query = (
        select(
            Discrepancy, WorkSession, InstrumentFamily, CatProcedureType,
            OperatingRoom, User, Kit, CaptureStation, CatDiscrepancyReason, CatEventType.code,
        )
        .join(WorkSession, WorkSession.id == Discrepancy.session_id)
        .join(Operation, Operation.id == WorkSession.operation_id)
        .outerjoin(InstrumentFamily, InstrumentFamily.id == Discrepancy.family_id)
        .outerjoin(CatProcedureType, CatProcedureType.id == Operation.procedure_type_id)
        .outerjoin(OperatingRoom, OperatingRoom.id == Operation.room_id)
        .outerjoin(User, User.id == WorkSession.user_id)
        .outerjoin(Kit, Kit.id == WorkSession.kit_id)
        .outerjoin(CaptureStation, CaptureStation.id == WorkSession.station_id)
        .outerjoin(CatDiscrepancyReason, CatDiscrepancyReason.id == Discrepancy.reason_id)
        .outerjoin(CountEvent, CountEvent.id == Discrepancy.origin_event_id)
        .outerjoin(CatEventType, CatEventType.id == CountEvent.event_type_id)
        .where(Operation.institution_id == actor.institution_id)
    )
    if status == 'open':
        query = query.where(Discrepancy.resolved.is_(False))
    elif status == 'resolved':
        query = query.where(Discrepancy.resolved.is_(True))
    elif status not in ('', 'all'):
        raise bad_request('invalid_status', 'status must be open, resolved, or all.', 'status')
    if search:
        term = f'%{search.strip()}%'
        query = query.where(
            Discrepancy.description.ilike(term)
            | InstrumentFamily.name.ilike(term)
            | WorkSession.id.cast(String).ilike(term)
        )
    rows = db.execute(
        query.order_by(Discrepancy.updated_at.desc(), Discrepancy.id)
        .offset((page - 1) * per_page).limit(per_page)
    ).all()
    total_query = (
        select(func.count(Discrepancy.id))
        .join(WorkSession, WorkSession.id == Discrepancy.session_id)
        .join(Operation, Operation.id == WorkSession.operation_id)
        .outerjoin(InstrumentFamily, InstrumentFamily.id == Discrepancy.family_id)
        .where(Operation.institution_id == actor.institution_id)
    )
    if status == 'open':
        total_query = total_query.where(Discrepancy.resolved.is_(False))
    elif status == 'resolved':
        total_query = total_query.where(Discrepancy.resolved.is_(True))
    if search:
        total_query = total_query.where(
            Discrepancy.description.ilike(term)
            | InstrumentFamily.name.ilike(term)
            | WorkSession.id.cast(String).ilike(term)
        )
    total = int(db.scalar(total_query) or 0)
    items = []
    for (
        discrepancy, work, family, procedure, room, operator, kit, station, reason,
        origin_event_type,
    ) in rows:
        items.append({
            'id': str(discrepancy.id),
            'session_id': str(work.id),
            'resolved': discrepancy.resolved,
            'description': discrepancy.description,
            'expected_quantity': discrepancy.expected_quantity,
            'detected_quantity': discrepancy.detected_quantity,
            'family': {'id': str(family.id), 'code': family.code, 'name': family.name}
            if family else None,
            'reason': {'id': str(reason.id), 'code': reason.code, 'name': reason.name} if reason else None,
            'origin_event_type': origin_event_type,
            'operator': {'id': str(operator.id), 'name': operator.name} if operator else None,
            'procedure': {'id': str(procedure.id), 'code': procedure.code, 'name': procedure.name}
            if procedure else None,
            'room': {'id': str(room.id), 'code': room.code, 'name': room.name} if room else None,
            'kit': {'id': str(kit.id), 'name': kit.name, 'version': kit.version} if kit else None,
            'station': {'id': str(station.id), 'name': station.name} if station else None,
            'updated_at': discrepancy.updated_at.isoformat() if discrepancy.updated_at else None,
        })
    return {
        'items': items,
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': (total + per_page - 1) // per_page,
    }


def _owned_discrepancy(actor, discrepancy_id):
    row = db.execute(
        select(Discrepancy)
        .join(WorkSession, WorkSession.id == Discrepancy.session_id)
        .join(Operation, Operation.id == WorkSession.operation_id)
        .where(
            Discrepancy.id == discrepancy_id,
            Operation.institution_id == actor.institution_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found('discrepancy_not_found', 'Discrepancy not found.')
    return row


def get_discrepancy(actor, discrepancy_id):
    row = _owned_discrepancy(actor, discrepancy_id)
    family = db.get(InstrumentFamily, row.family_id) if row.family_id else None
    work = _owned_session(actor, row.session_id)
    return {
        'id': str(row.id),
        'session_id': str(row.session_id),
        'description': row.description,
        'resolved': row.resolved,
        'resolved_at': row.resolved_at.isoformat() if row.resolved_at else None,
        'expected_quantity': row.expected_quantity,
        'detected_quantity': row.detected_quantity,
        'family': {'id': str(family.id), 'code': family.code, 'name': family.name}
        if family else None,
        'session_status': db.get(CatSessionStatus, work.status_id).code,
    }


def resolve(actor, discrepancy_id, payload):
    notes = str(payload.get('notes') or '').strip()
    if not notes:
        raise bad_request('notes_required', 'Resolution notes are required.', 'notes')
    mark_lost = payload.get('mark_lost') is True
    mark_recovered = payload.get('mark_recovered') is True
    if mark_lost and mark_recovered:
        raise bad_request('invalid_resolution', 'Choose lost or recovered, not both.')

    discrepancy = _owned_discrepancy(actor, discrepancy_id)
    if discrepancy.resolved:
        raise ApiError(409, 'already_resolved', 'Discrepancy has already been resolved.')
    work = _owned_session(actor, discrepancy.session_id)
    now = datetime.now(timezone.utc)
    outcome = 'marked lost' if mark_lost else ('marked recovered' if mark_recovered else 'resolved')
    discrepancy.resolved = True
    discrepancy.resolved_at = now
    discrepancy.updated_at = now
    discrepancy.description = f'{discrepancy.description}\nSPD resolution ({outcome}): {notes}'

    if discrepancy.family_id and (mark_lost or mark_recovered):
        target_code = 'lost' if mark_lost else 'available'
        target_status = db.scalar(
            select(CatInstrumentCycleStatus).where(CatInstrumentCycleStatus.code == target_code)
        )
        if target_status is None:
            raise ApiError(503, 'catalog_unavailable', f'Required cycle status "{target_code}" is missing.')
        source_status_id = None
        if mark_recovered:
            source_status_id = db.scalar(
                select(CatInstrumentCycleStatus.id).where(CatInstrumentCycleStatus.code == 'lost')
            )
            if source_status_id is None:
                raise ApiError(503, 'catalog_unavailable', 'Required cycle status "lost" is missing.')
        instrument_query = select(Instrument).where(
            Instrument.family_id == discrepancy.family_id,
            Instrument.institution_id == actor.institution_id,
        )
        if mark_recovered:
            instrument_query = instrument_query.where(Instrument.cycle_status_id == source_status_id)
        else:
            instrument_query = instrument_query.where(Instrument.active.is_(True))
        instrument = db.execute(instrument_query.limit(1)).scalar_one_or_none()
        if instrument:
            instrument.cycle_status_id = target_status.id
            instrument.active = not mark_lost
            instrument.updated_at = now
            db.add(InstrumentCycleEvent(
                instrument_id=instrument.id,
                cycle_status_id=target_status.id,
                occurred_at=now,
                session_id=work.id,
                operation_id=work.operation_id,
                notes=notes,
            ))
            if mark_lost:
                reservations = db.execute(
                    select(InstrumentReservation).where(
                        InstrumentReservation.instrument_id == instrument.id,
                        InstrumentReservation.active.is_(True),
                    )
                ).scalars()
                for reservation in reservations:
                    reservation.active = False
                    reservation.released_at = now
        elif mark_recovered:
            discrepancy.description = (
                f'{discrepancy.description}\n'
                'Note: no lost instrument of this family was found to reactivate.'
            )

    remaining = int(db.scalar(
        select(func.count(Discrepancy.id)).where(
            Discrepancy.session_id == work.id,
            Discrepancy.resolved.is_(False),
            Discrepancy.id != discrepancy.id,
        )
    ) or 0)
    current_status = db.get(CatSessionStatus, work.status_id)
    if remaining == 0 and current_status and current_status.code == 'correction_required':
        next_status_id = db.scalar(
            select(CatSessionStatus.id).where(CatSessionStatus.code == 'awaiting_spd_review')
        )
        if next_status_id is None:
            raise ApiError(503, 'catalog_unavailable', 'Required session status "awaiting_spd_review" is missing.')
        work.status_id = next_status_id
        work.updated_at = now
    audit.record(actor, 'RESOLVE_SUPERVISOR_DISCREPANCY', 'discrepancy', discrepancy.id)
    _commit()
    return {
        'discrepancy_id': str(discrepancy.id),
        'session_id': str(work.id),
        'open_remaining': remaining,
        'session_status': db.get(CatSessionStatus, work.status_id).code,
    }
