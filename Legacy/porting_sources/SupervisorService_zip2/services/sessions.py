from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import String, func, select
from sqlalchemy.exc import SQLAlchemyError

import audit
from database import db
from errors import ApiError, bad_request, not_found
from models import (
    CatEventType,
    CatInstrumentCategory,
    CatInstrumentCycleStatus,
    CatOperationPhase,
    CatOperationStatus,
    CatProcedureType,
    CatSessionStatus,
    CatSurgicalRole,
    CaptureStation,
    CountEvent,
    Discrepancy,
    ExpectedInventory,
    Instrument,
    InstrumentCycleEvent,
    InstrumentFamily,
    InstrumentReservation,
    Kit,
    KitItem,
    Operation,
    OperationPatient,
    OperationPhysician,
    OperatingRoom,
    Patient,
    Physician,
    PrivacyNoticeVersion,
    Role,
    SessionProcessingAgreement,
    User,
    UserRole,
    WorkSession,
)
from services.catalogs import initial_phase


def _uuid(value, field):
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise bad_request('invalid_identifier', f'{field} must be a UUID.', field) from exc


def _required(payload, key):
    value = payload.get(key)
    if value is None or value == '':
        raise bad_request('field_required', f'{key} is required.', key)
    return value


def _status_id(code):
    value = db.scalar(select(CatSessionStatus.id).where(CatSessionStatus.code == code))
    if value is None:
        raise ApiError(503, 'catalog_unavailable', f'Required session status "{code}" is missing.')
    return value


def _operation_status_id(code):
    value = db.scalar(select(CatOperationStatus.id).where(CatOperationStatus.code == code))
    if value is None:
        raise ApiError(503, 'catalog_unavailable', f'Required operation status "{code}" is missing.')
    return value


def _event_type_id(code):
    value = db.scalar(select(CatEventType.id).where(CatEventType.code == code))
    if value is None:
        raise ApiError(503, 'catalog_unavailable', f'Required event type "{code}" is missing.')
    return value


def _commit():
    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise ApiError(503, 'database_unavailable', 'Database operation could not be completed.') from exc


def _status_name(status_id):
    status = db.get(CatSessionStatus, status_id)
    return status.code if status else 'unknown'


def _owned_session(actor, session_id):
    row = db.execute(
        select(WorkSession)
        .join(Operation, Operation.id == WorkSession.operation_id)
        .where(
            WorkSession.id == session_id,
            Operation.institution_id == actor.institution_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found('session_not_found', 'Session not found.')
    return row


def _stock(actor, family_id):
    available_id = db.scalar(
        select(CatInstrumentCycleStatus.id).where(CatInstrumentCycleStatus.code == 'available')
    )
    if available_id is None:
        raise ApiError(503, 'catalog_unavailable', 'Required instrument cycle status "available" is missing.')
    filters = [
        Instrument.family_id == family_id,
        Instrument.institution_id == actor.institution_id,
        Instrument.active.is_(True),
    ]
    total = db.scalar(select(func.count(Instrument.id)).where(*filters)) or 0
    available = 0
    if available_id:
        available = db.scalar(
            select(func.count(Instrument.id)).where(*filters, Instrument.cycle_status_id == available_id)
        ) or 0
    return int(total), int(available)


def stock_recheck(actor, lines):
    if not isinstance(lines, list):
        raise bad_request('invalid_inventory', 'lines must be a list.', 'lines')
    result = []
    seen = set()
    for index, row in enumerate(lines):
        field = f'lines[{index}]'
        if not isinstance(row, dict):
            raise bad_request('invalid_inventory', 'Each stock line must be an object.', field)
        family_id = _uuid(row.get('family_id'), f'{field}.family_id')
        if db.get(InstrumentFamily, family_id) is None:
            raise bad_request('invalid_family', 'Choose a valid instrument family.', field)
        if family_id in seen:
            raise bad_request('duplicate_family', 'Each family may appear only once.', field)
        seen.add(family_id)
        try:
            quantity = int(row.get('expected_quantity') or 0)
        except (TypeError, ValueError) as exc:
            raise bad_request('invalid_quantity', 'Expected quantity must be an integer.', field) from exc
        if quantity < 0 or quantity > 32767:
            raise bad_request('invalid_quantity', 'Expected quantity must be between 0 and 32767.', field)
        total, available = _stock(actor, family_id)
        semaphore = 'BLOCK' if quantity > available else (
            'WARN' if quantity > max(1, int(available * 0.5)) else 'OK'
        )
        result.append({
            'family_id': str(family_id),
            'expected_quantity': quantity,
            'stock_available': available,
            'stock_total': total,
            'semaphore': semaphore,
        })
    return result


def _normalize_lines(actor, lines):
    if not isinstance(lines, list) or not lines:
        raise bad_request('inventory_required', 'At least one expected inventory line is required.', 'expected_lines')
    normalized = {}
    for index, raw in enumerate(lines):
        field = f'expected_lines[{index}]'
        if not isinstance(raw, dict):
            raise bad_request('invalid_inventory', 'Each inventory line must be an object.', field)
        family_id = _uuid(raw.get('family_id'), f'{field}.family_id')
        try:
            quantity = int(raw.get('expected_quantity'))
        except (TypeError, ValueError) as exc:
            raise bad_request('invalid_quantity', 'Expected quantity must be a positive integer.', field) from exc
        if quantity < 1 or quantity > 32767:
            raise bad_request('invalid_quantity', 'Expected quantity must be between 1 and 32767.', field)
        if family_id in normalized:
            raise bad_request('duplicate_family', 'Each family may appear only once.', field)
        family = db.get(InstrumentFamily, family_id)
        if family is None or not family.active:
            raise bad_request('invalid_family', 'Choose an active instrument family.', field)
        _total, available = _stock(actor, family_id)
        if quantity > available:
            raise bad_request(
                'stock_exceeded',
                f'Requested quantity ({quantity}) exceeds available stock ({available}).',
                field,
            )
        normalized[family_id] = quantity
    return normalized


def _validate_schedule(actor, payload):
    operator_id = _uuid(_required(payload, 'operator_user_id'), 'operator_user_id')
    procedure_id = _uuid(_required(payload, 'procedure_type_id'), 'procedure_type_id')
    room_id = _uuid(_required(payload, 'room_id'), 'room_id')
    station_id = _uuid(_required(payload, 'station_id'), 'station_id')
    kit_id = _uuid(_required(payload, 'kit_id'), 'kit_id')
    patient_raw = payload.get('patient_id')
    patient_id = _uuid(patient_raw, 'patient_id') if patient_raw else None
    try:
        scheduled_at = datetime.fromisoformat(str(_required(payload, 'scheduled_at')).replace('Z', '+00:00'))
    except ValueError as exc:
        raise bad_request('invalid_datetime', 'scheduled_at must be an ISO 8601 datetime.', 'scheduled_at') from exc
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

    operator = db.get(User, operator_id)
    is_operator = db.scalar(
        select(UserRole.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            UserRole.user_id == operator_id,
            Role.institution_id == actor.institution_id,
            Role.code == 'station_operator',
        )
    )
    if operator is None or not operator.active or operator.institution_id != actor.institution_id or not is_operator:
        raise bad_request('invalid_operator', 'Choose an active station operator in this institution.', 'operator_user_id')
    if db.get(CatProcedureType, procedure_id) is None:
        raise bad_request('invalid_procedure', 'Choose a valid procedure type.', 'procedure_type_id')
    room = db.get(OperatingRoom, room_id)
    if room is None or room.institution_id != actor.institution_id or not room.active:
        raise bad_request('invalid_room', 'Choose an active operating room in this institution.', 'room_id')
    station = db.get(CaptureStation, station_id)
    if station is None or station.room_id != room.id or not station.active:
        raise bad_request('invalid_station', 'Choose an active capture station in the selected room.', 'station_id')
    kit = db.get(Kit, kit_id)
    if kit is None or kit.institution_id != actor.institution_id or not kit.active:
        raise bad_request('invalid_kit', 'Choose an active kit in this institution.', 'kit_id')
    if patient_id:
        patient = db.get(Patient, patient_id)
        if patient is None or patient.institution_id != actor.institution_id or not patient.active:
            raise bad_request('invalid_patient', 'Choose an active patient in this institution.', 'patient_id')

    team = payload.get('physicians')
    if not isinstance(team, list) or not team:
        legacy_id = payload.get('physician_id')
        team = [{'physician_id': legacy_id, 'surgical_role_code': 'surgeon'}] if legacy_id else []
    if not team:
        raise bad_request('physicians_required', 'At least one physician is required.', 'physicians')
    normalized_team = []
    seen = set()
    for index, member in enumerate(team):
        field = f'physicians[{index}]'
        if not isinstance(member, dict):
            raise bad_request('invalid_physician', 'Each physician entry must be an object.', field)
        physician_id = _uuid(member.get('physician_id'), f'{field}.physician_id')
        role_code = str(member.get('surgical_role_code') or 'surgeon').strip()
        if physician_id in seen:
            raise bad_request('duplicate_physician', 'Each physician may be selected only once.', field)
        seen.add(physician_id)
        physician = db.get(Physician, physician_id)
        role = db.scalar(select(CatSurgicalRole).where(CatSurgicalRole.code == role_code))
        if (
            physician is None
            or not physician.active
            or physician.institution_id != actor.institution_id
            or role is None
        ):
            raise bad_request('invalid_physician', 'Choose a valid physician and surgical role.', field)
        normalized_team.append((physician_id, role.id))

    lines = _normalize_lines(actor, payload.get('expected_lines'))
    return {
        'operator_id': operator_id,
        'procedure_id': procedure_id,
        'room_id': room_id,
        'station_id': station_id,
        'kit_id': kit_id,
        'patient_id': patient_id,
        'scheduled_at': scheduled_at,
        'team': normalized_team,
        'lines': lines,
    }


def _replace_people(operation_id, patient_id, team):
    for row in db.execute(
        select(OperationPatient).where(OperationPatient.operation_id == operation_id)
    ).scalars():
        db.delete(row)
    for row in db.execute(
        select(OperationPhysician).where(OperationPhysician.operation_id == operation_id)
    ).scalars():
        db.delete(row)
    db.flush()
    if patient_id:
        db.add(OperationPatient(operation_id=operation_id, patient_id=patient_id))
    for physician_id, role_id in team:
        db.add(OperationPhysician(
            operation_id=operation_id,
            physician_id=physician_id,
            surgical_role_id=role_id,
        ))


def _save_expected_inventory(session_id, kit_id, lines):
    kit_quantities = {
        row.family_id: int(row.quantity)
        for row in db.execute(select(KitItem).where(KitItem.kit_id == kit_id)).scalars()
    }
    existing = {
        row.family_id: row
        for row in db.execute(
            select(ExpectedInventory).where(ExpectedInventory.session_id == session_id)
        ).scalars()
    }
    for family_id, quantity in lines.items():
        row = existing.pop(family_id, None)
        source = 'kit_snapshot' if kit_quantities.get(family_id) == quantity else 'schedule_additional'
        if row is None:
            db.add(ExpectedInventory(
                session_id=session_id,
                family_id=family_id,
                expected_quantity=quantity,
                source=source,
            ))
        else:
            row.expected_quantity = quantity
            row.source = source
    for row in existing.values():
        db.delete(row)


def schedule(actor, payload):
    data = _validate_schedule(actor, payload)
    phase_code = initial_phase(data['procedure_id'])
    phase = db.scalar(select(CatOperationPhase).where(
        CatOperationPhase.code == phase_code,
        CatOperationPhase.active.is_(True),
    ))
    if phase is None:
        raise ApiError(503, 'catalog_unavailable', 'No active initial operation phase is configured.')
    operation = Operation(
        scheduled_at=data['scheduled_at'],
        status_id=_operation_status_id('scheduled'),
        procedure_type_id=data['procedure_id'],
        room_id=data['room_id'],
        institution_id=actor.institution_id,
    )
    db.add(operation)
    db.flush()
    _replace_people(operation.id, data['patient_id'], data['team'])
    work = WorkSession(
        status_id=_status_id('scheduled'),
        user_id=data['operator_id'],
        operation_id=operation.id,
        station_id=data['station_id'],
        kit_id=data['kit_id'],
        current_phase_id=phase.id,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(work)
    db.flush()
    _save_expected_inventory(work.id, data['kit_id'], data['lines'])
    audit.record(actor, 'SCHEDULE_SUPERVISOR_SESSION', 'work_session', work.id)
    _commit()
    return {'session_id': str(work.id), 'status': 'scheduled'}


def update_scheduled(actor, session_id, payload):
    work = _owned_session(actor, session_id)
    if _status_name(work.status_id) != 'scheduled':
        raise ApiError(409, 'session_not_editable', 'Only scheduled sessions can be edited.')
    operation = db.get(Operation, work.operation_id)
    if operation is None:
        raise ApiError(409, 'operation_missing', 'The session has no associated operation.')
    data = _validate_schedule(actor, payload)
    operation.scheduled_at = data['scheduled_at']
    operation.procedure_type_id = data['procedure_id']
    operation.room_id = data['room_id']
    work.user_id = data['operator_id']
    work.station_id = data['station_id']
    work.kit_id = data['kit_id']
    work.updated_at = datetime.now(timezone.utc)
    _replace_people(operation.id, data['patient_id'], data['team'])
    _save_expected_inventory(work.id, data['kit_id'], data['lines'])
    audit.record(actor, 'UPDATE_SCHEDULED_SUPERVISOR_SESSION', 'work_session', work.id)
    _commit()
    return {'session_id': str(work.id), 'status': 'scheduled'}


def update_inventory(actor, session_id, payload):
    work = _owned_session(actor, session_id)
    status = _status_name(work.status_id)
    if status not in {'scheduled', 'in_progress'}:
        raise ApiError(409, 'inventory_not_editable', 'Inventory can only be changed before session closure.')
    lines = _normalize_lines(actor, payload.get('expected_lines'))
    existing = {
        row.family_id: row
        for row in db.execute(
            select(ExpectedInventory).where(ExpectedInventory.session_id == work.id)
        ).scalars()
    }
    kit_quantities = {}
    if work.kit_id:
        kit_quantities = {
            row.family_id: int(row.quantity)
            for row in db.execute(select(KitItem).where(KitItem.kit_id == work.kit_id)).scalars()
        }
    for family_id, quantity in lines.items():
        row = existing.pop(family_id, None)
        source = 'live_add' if status == 'in_progress' else (
            'kit_snapshot' if kit_quantities.get(family_id) == quantity else 'schedule_additional'
        )
        if row is None:
            db.add(ExpectedInventory(
                session_id=work.id,
                family_id=family_id,
                expected_quantity=quantity,
                source=source,
            ))
        elif row.expected_quantity != quantity or row.source != source:
            row.expected_quantity = quantity
            row.source = source
    for row in existing.values():
        db.delete(row)
    work.updated_at = datetime.now(timezone.utc)
    audit.record(actor, 'UPDATE_SESSION_INVENTORY', 'work_session', work.id)
    _commit()
    return {'session_id': str(work.id), 'expected_lines': len(lines)}


def confirm_privacy(actor, session_id, payload):
    work = _owned_session(actor, session_id)
    if _status_name(work.status_id) != 'scheduled' or work.capture_mode is not None:
        raise ApiError(409, 'privacy_not_editable', 'Privacy agreement can only be set before session start.')
    notice_id = _uuid(_required(payload, 'privacy_notice_version_id'), 'privacy_notice_version_id')
    notice = db.get(PrivacyNoticeVersion, notice_id)
    if notice is None or not notice.active:
        raise bad_request('invalid_privacy_notice', 'Choose an active privacy notice version.')
    agreement = db.scalar(
        select(SessionProcessingAgreement).where(SessionProcessingAgreement.session_id == work.id)
    )
    now = datetime.now(timezone.utc)
    if agreement:
        agreement.privacy_notice_version_id = notice.id
        agreement.purpose_quality_ops = True
        agreement.purpose_model_improvement = bool(payload.get('purpose_model_improvement', False))
        agreement.agreed_by_user_id = actor.id
        agreement.agreed_at = now
    else:
        db.add(SessionProcessingAgreement(
            session_id=work.id,
            privacy_notice_version_id=notice.id,
            purpose_quality_ops=True,
            purpose_model_improvement=bool(payload.get('purpose_model_improvement', False)),
            agreed_by_user_id=actor.id,
            agreed_at=now,
        ))
    audit.record(actor, 'CONFIRM_SESSION_PRIVACY', 'work_session', work.id)
    _commit()
    return {'session_id': str(work.id), 'privacy_notice_version': notice.version}


def close_session(actor, session_id, payload):
    if payload.get('material_recovered') is not True:
        raise bad_request('material_recovery_required', 'Confirm that materials were recovered or reprocessing started.')
    work = _owned_session(actor, session_id)
    if _status_name(work.status_id) != 'awaiting_spd_review':
        raise ApiError(409, 'session_not_ready_to_close', 'Session is not awaiting SPD review.')
    open_count = db.scalar(
        select(func.count(Discrepancy.id)).where(
            Discrepancy.session_id == work.id,
            Discrepancy.resolved.is_(False),
        )
    ) or 0
    if open_count:
        raise ApiError(409, 'open_discrepancies', 'Resolve all open discrepancies before closing.')
    now = datetime.now(timezone.utc)
    work.status_id = _status_id('closed')
    work.closed_by_user_id = actor.id
    work.ended_at = work.ended_at or now
    work.updated_at = now
    sterilizing_id = db.scalar(
        select(CatInstrumentCycleStatus.id).where(CatInstrumentCycleStatus.code == 'sterilization')
    )
    if sterilizing_id is None:
        raise ApiError(503, 'catalog_unavailable', 'Required instrument cycle status "sterilization" is missing.')
    if work.operation_id:
        reservations = db.execute(
            select(InstrumentReservation).where(
                InstrumentReservation.operation_id == work.operation_id,
                InstrumentReservation.active.is_(True),
            )
        ).scalars()
        for reservation in reservations:
            reservation.active = False
            reservation.released_at = now
            instrument = db.get(Instrument, reservation.instrument_id)
            if instrument:
                instrument.cycle_status_id = sterilizing_id
                instrument.updated_at = now
                db.add(InstrumentCycleEvent(
                    instrument_id=instrument.id,
                    cycle_status_id=sterilizing_id,
                    occurred_at=now,
                    session_id=work.id,
                    operation_id=work.operation_id,
                    notes='SPD confirm close — reprocessing started',
                ))
    db.add(CountEvent(
        event_type_id=_event_type_id('session_close'),
        session_id=work.id,
        user_id=actor.id,
        occurred_at=now,
        payload={'closed_by': 'spd_supervisor', 'material_recovered': True},
    ))
    audit.record(actor, 'CLOSE_SUPERVISOR_SESSION', 'work_session', work.id)
    _commit()
    return {'session_id': str(work.id), 'status': 'closed'}


def list_sessions(actor, *, status='', search='', date_value='', page=1, per_page=20):
    try:
        page = max(1, int(page))
        per_page = min(100, max(1, int(per_page)))
    except (TypeError, ValueError) as exc:
        raise bad_request('invalid_pagination', 'page and per_page must be integers.') from exc
    query = (
        select(
            WorkSession, Operation, CatSessionStatus, OperatingRoom, CatProcedureType, User,
            Kit, CaptureStation,
        )
        .join(Operation, Operation.id == WorkSession.operation_id)
        .join(CatSessionStatus, CatSessionStatus.id == WorkSession.status_id)
        .outerjoin(OperatingRoom, OperatingRoom.id == Operation.room_id)
        .outerjoin(CatProcedureType, CatProcedureType.id == Operation.procedure_type_id)
        .outerjoin(Kit, Kit.id == WorkSession.kit_id)
        .outerjoin(CaptureStation, CaptureStation.id == WorkSession.station_id)
        .join(User, User.id == WorkSession.user_id)
        .where(Operation.institution_id == actor.institution_id)
    )
    if status:
        query = query.where(CatSessionStatus.code == status)
    if date_value:
        try:
            parsed_date = datetime.strptime(date_value, '%Y-%m-%d').date()
        except ValueError as exc:
            raise bad_request('invalid_date', 'date must use YYYY-MM-DD format.', 'date') from exc
        query = query.where(func.date(Operation.scheduled_at) == parsed_date)
    if search:
        term = f'%{search.strip()}%'
        query = query.where(
            (WorkSession.id.cast(String).ilike(term))
            | User.name.ilike(term)
            | CatProcedureType.name.ilike(term)
            | OperatingRoom.name.ilike(term)
        )
    rows = db.execute(query.order_by(Operation.scheduled_at.desc().nullslast(), WorkSession.id)
                      .offset((page - 1) * per_page).limit(per_page)).all()
    total_query = select(func.count(WorkSession.id)).join(
        Operation, Operation.id == WorkSession.operation_id
    ).join(CatSessionStatus, CatSessionStatus.id == WorkSession.status_id).where(
        Operation.institution_id == actor.institution_id
    ).join(User, User.id == WorkSession.user_id).outerjoin(
        CatProcedureType, CatProcedureType.id == Operation.procedure_type_id
    ).outerjoin(OperatingRoom, OperatingRoom.id == Operation.room_id)
    if status:
        total_query = total_query.where(CatSessionStatus.code == status)
    if date_value:
        total_query = total_query.where(func.date(Operation.scheduled_at) == parsed_date)
    if search:
        total_query = total_query.where(
            (WorkSession.id.cast(String).ilike(term))
            | User.name.ilike(term)
            | CatProcedureType.name.ilike(term)
            | OperatingRoom.name.ilike(term)
        )
    total = int(db.scalar(total_query) or 0)
    session_ids = [row[0].id for row in rows]
    agreements = set(db.execute(
        select(SessionProcessingAgreement.session_id)
        .where(SessionProcessingAgreement.session_id.in_(session_ids))
    ).scalars()) if session_ids else set()
    discrepancy_counts = dict(db.execute(
        select(Discrepancy.session_id, func.count(Discrepancy.id))
        .where(
            Discrepancy.session_id.in_(session_ids),
            Discrepancy.resolved.is_(False),
        )
        .group_by(Discrepancy.session_id)
    ).all()) if session_ids else {}
    items = []
    for work, operation, state, room, procedure, operator, kit, station in rows:
        has_agreement = work.id in agreements
        discrepancy_count = int(discrepancy_counts.get(work.id, 0))
        items.append({
            'id': str(work.id),
            'session_id': str(work.id),
            'status': state.code,
            'capture_mode': work.capture_mode,
            'has_privacy_agreement': has_agreement,
            'discrepancy_count': discrepancy_count,
            'updated_at': work.updated_at.isoformat() if work.updated_at else None,
            'scheduled_at': operation.scheduled_at.isoformat() if operation.scheduled_at else None,
            'started_at': work.started_at.isoformat() if work.started_at else None,
            'ended_at': work.ended_at.isoformat() if work.ended_at else None,
            'operator': {'id': str(operator.id), 'name': operator.name},
            'procedure': {'id': str(procedure.id), 'code': procedure.code, 'name': procedure.name}
            if procedure else None,
            'room': {'id': str(room.id), 'code': room.code, 'name': room.name} if room else None,
            'station': {'id': str(station.id), 'name': station.name} if station else None,
            'kit': {'id': str(kit.id), 'name': kit.name, 'version': kit.version} if kit else None,
        })
    return {
        'items': items,
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': (total + per_page - 1) // per_page,
    }


def get_session(actor, session_id):
    work = _owned_session(actor, session_id)
    operation = db.get(Operation, work.operation_id)
    state = db.get(CatSessionStatus, work.status_id)
    procedure = db.get(CatProcedureType, operation.procedure_type_id) if operation and operation.procedure_type_id else None
    room = db.get(OperatingRoom, operation.room_id) if operation and operation.room_id else None
    operator = db.get(User, work.user_id)
    station = db.get(CaptureStation, work.station_id) if work.station_id else None
    kit = db.get(Kit, work.kit_id) if work.kit_id else None
    patient_row = db.execute(
        select(Patient).join(OperationPatient, OperationPatient.patient_id == Patient.id)
        .where(OperationPatient.operation_id == operation.id)
    ).scalars().first() if operation else None
    team_rows = db.execute(
        select(Physician, CatSurgicalRole)
        .join(OperationPhysician, OperationPhysician.physician_id == Physician.id)
        .join(CatSurgicalRole, CatSurgicalRole.id == OperationPhysician.surgical_role_id)
        .where(OperationPhysician.operation_id == operation.id)
        .order_by(Physician.name)
    ).all() if operation else []
    expected = db.execute(
        select(ExpectedInventory, InstrumentFamily, CatInstrumentCategory)
        .join(InstrumentFamily, InstrumentFamily.id == ExpectedInventory.family_id)
        .outerjoin(CatInstrumentCategory, CatInstrumentCategory.id == InstrumentFamily.category_id)
        .where(ExpectedInventory.session_id == work.id)
        .order_by(InstrumentFamily.name)
    ).all()
    discrepancies = db.execute(
        select(Discrepancy, InstrumentFamily, CatDiscrepancyReason, CatEventType.code)
        .outerjoin(InstrumentFamily, InstrumentFamily.id == Discrepancy.family_id)
        .outerjoin(CatDiscrepancyReason, CatDiscrepancyReason.id == Discrepancy.reason_id)
        .outerjoin(CountEvent, CountEvent.id == Discrepancy.origin_event_id)
        .outerjoin(CatEventType, CatEventType.id == CountEvent.event_type_id)
        .where(Discrepancy.session_id == work.id)
        .order_by(Discrepancy.updated_at.desc())
    ).all()
    events = db.execute(
        select(CountEvent, CatEventType)
        .join(CatEventType, CatEventType.id == CountEvent.event_type_id)
        .where(CountEvent.session_id == work.id)
        .order_by(CountEvent.occurred_at.desc())
        .limit(100)
    ).all()
    agreement = db.scalar(
        select(SessionProcessingAgreement).where(SessionProcessingAgreement.session_id == work.id)
    )
    notice = db.get(PrivacyNoticeVersion, agreement.privacy_notice_version_id) if agreement else None
    agreed_by = db.get(User, agreement.agreed_by_user_id) if agreement else None
    return {
        'id': str(work.id),
        'session_id': str(work.id),
        'status': state.code if state else 'unknown',
        'started_at': work.started_at.isoformat() if work.started_at else None,
        'ended_at': work.ended_at.isoformat() if work.ended_at else None,
        'updated_at': work.updated_at.isoformat() if work.updated_at else None,
        'capture_mode': work.capture_mode,
        'scheduled_at': operation.scheduled_at.isoformat() if operation and operation.scheduled_at else None,
        'operator': {'id': str(operator.id), 'name': operator.name} if operator else None,
        'procedure': {'id': str(procedure.id), 'code': procedure.code, 'name': procedure.name}
        if procedure else None,
        'room': {'id': str(room.id), 'code': room.code, 'name': room.name} if room else None,
        'station': {'id': str(station.id), 'name': station.name} if station else None,
        'kit': {'id': str(kit.id), 'name': kit.name, 'version': kit.version} if kit else None,
        'patient': {'id': str(patient_row.id), 'name': patient_row.display_name} if patient_row else None,
        'physicians': [{
            'id': str(physician.id),
            'name': physician.name,
            'surgical_role': {'id': str(role.id), 'code': role.code, 'name': role.name},
        } for physician, role in team_rows],
        'expected_inventory': [{
            'family': {
                'id': str(family.id),
                'code': family.code,
                'name': family.name,
                'category': ({
                    'code': category.code,
                    'name': category.name,
                } if category else None),
            },
            'expected_quantity': row.expected_quantity,
            'source': row.source,
        } for row, family, category in expected],
        'discrepancies': [{
            'id': str(row.id),
            'description': row.description,
            'resolved': row.resolved,
            'resolved_at': row.resolved_at.isoformat() if row.resolved_at else None,
            'family': {'id': str(family.id), 'code': family.code, 'name': family.name} if family else None,
            'reason': {'id': str(reason.id), 'code': reason.code, 'name': reason.name} if reason else None,
            'origin_event_type': origin_event_type,
            'expected_quantity': row.expected_quantity,
            'detected_quantity': row.detected_quantity,
        } for row, family, reason, origin_event_type in discrepancies],
        'events': [{
            'id': str(event.id),
            'type': event_type.code,
            'occurred_at': event.occurred_at.isoformat(),
            'family_id': str(event.family_id) if event.family_id else None,
            'expected_quantity': event.expected_quantity,
            'detected_quantity': event.detected_quantity,
            'payload': event.payload,
        } for event, event_type in events],
        'privacy_agreement': {
            'privacy_notice_version_id': str(notice.id),
            'version': notice.version,
            'document_uri': notice.document_uri,
            'purpose_model_improvement': agreement.purpose_model_improvement,
            'agreed_at': agreement.agreed_at.isoformat(),
            'agreed_by': agreed_by.name if agreed_by else None,
        } if agreement and notice else None,
    }
