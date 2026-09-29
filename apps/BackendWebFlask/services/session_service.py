"""Operator counting-session creation (WorkSession + ExpectedInventory snapshot).

create_work_session() only stages rows in the caller's transaction (WorkSession, one
ExpectedInventory per KitItem and the CREATE_SESSION audit row); the route commits once, so a
failure anywhere rolls everything back and never leaves a session with a partial snapshot.
After creation, session screens read ExpectedInventory only — never KitItem again.
"""
from flask_babel import gettext as _
from sqlalchemy import select

from extensions import db
from localization.labels import label
from models.CaptureStation import CaptureStation
from models.CatOperationPhase import CatOperationPhase
from models.CatOperationStatus import CatOperationStatus
from models.CatProcedureType import CatProcedureType
from models.CatSessionStatus import CatSessionStatus
from models.ExpectedInventory import ExpectedInventory
from models.InstrumentFamily import InstrumentFamily
from models.Kit import Kit
from models.KitItem import KitItem
from models.OperatingRoom import OperatingRoom
from models.Operation import Operation
from models.OperationPatient import OperationPatient
from models.OperationPhysician import OperationPhysician
from models.Patient import Patient
from models.Physician import Physician
from models.ProcedureKit import ProcedureKit
from models.ProcedurePhase import ProcedurePhase
from models.WorkSession import WorkSession
from services.admin_service import ConflictError, FormError, parse_uuid
from services.audit import record_audit

# 002_seed_catalogs.sql: session state machine open -> counting -> validating -> closed.
# A freshly created session is "open": the snapshot is frozen but counting starts at capture.
INITIAL_SESSION_STATUS = 'open'
# Operations that can still receive a counting session (cat_operation_status codes).
SESSION_OPERATION_STATUSES = ('scheduled', 'in_progress')


# --------------------------------------------------------------------------- allowed options

def available_operations(institution_id):
    return db.session.execute(
        select(Operation)
        .join(CatOperationStatus, CatOperationStatus.id == Operation.status_id)
        .where(Operation.institution_id == institution_id, CatOperationStatus.code.in_(SESSION_OPERATION_STATUSES))
        .order_by(Operation.scheduled_at.desc().nulls_last())
    ).scalars().all()


def allowed_kits(operation, institution_id):
    """Only kits linked to the operation's procedure through an active ProcedureKit (no fallback)."""
    if operation is None or not operation.procedure_type_id:
        return []
    return db.session.execute(
        select(Kit).join(ProcedureKit, ProcedureKit.kit_id == Kit.id)
        .where(Kit.institution_id == institution_id, Kit.active.is_(True),
               ProcedureKit.procedure_type_id == operation.procedure_type_id, ProcedureKit.active.is_(True))
        .order_by(ProcedureKit.is_default.desc(), Kit.name, Kit.version)
    ).scalars().all()


def setup_notice(operation, kits, phases=None):
    """Why a session cannot start for this operation (None when it can)."""
    if operation is None:
        return _('There are no scheduled or in-progress operations available for your institution.')
    if not operation.procedure_type_id:
        return _('This operation has no procedure type; a counting session cannot be started for it.')
    if not kits:
        return _('No active kit is associated with this procedure. An administrator must configure '
                 'the procedure kits (Procedures > Associated Kits) before starting a session.')
    if phases is not None and not phases:
        return _('No active counting phase is configured for this procedure. An administrator must configure '
                 'the counting phases (Procedures > Counting Phases) before starting a session.')
    return None


def allowed_stations(operation, institution_id):
    """Active stations in active rooms of the institution; same room as the operation when it has one."""
    statement = (
        select(CaptureStation).join(OperatingRoom, OperatingRoom.id == CaptureStation.room_id)
        .where(OperatingRoom.institution_id == institution_id, OperatingRoom.active.is_(True),
               CaptureStation.active.is_(True))
        .order_by(CaptureStation.name)
    )
    if operation is not None and operation.room_id:
        statement = statement.where(CaptureStation.room_id == operation.room_id)
    return db.session.execute(statement).scalars().all()


def allowed_phases(operation):
    """Only the procedure's active ProcedurePhase rows (by sort_order); no fallback to global phases."""
    if operation is None or not operation.procedure_type_id:
        return []
    return db.session.execute(
        select(CatOperationPhase).join(ProcedurePhase, ProcedurePhase.phase_id == CatOperationPhase.id)
        .where(ProcedurePhase.procedure_type_id == operation.procedure_type_id, ProcedurePhase.active.is_(True),
               CatOperationPhase.active.is_(True))
        .order_by(ProcedurePhase.sort_order)
    ).scalars().all()


def _pick(options, raw_id):
    wanted = parse_uuid(raw_id)
    return next((item for item in options if item.id == wanted), None)


# --------------------------------------------------------------------------- display helpers

def procedure_label(procedure):
    return label('procedure_type', procedure.code, procedure.name) if procedure else _('Unspecified procedure')


def _inventory(statement):
    """[(family code, stored name, quantity)] -> rows with the family name localized by code."""
    rows = [{'instrument_family_code': code, 'instrument_family_name': label('instrument_family', code, name),
             'expected_quantity': quantity} for code, name, quantity in db.session.execute(statement).all()]
    return sorted(rows, key=lambda row: row['instrument_family_name'].lower())


def _operation_people(operation_ids):
    patients, physicians = {}, {}
    if operation_ids:
        for op_id, name in db.session.execute(
            select(OperationPatient.operation_id, Patient.display_name)
            .join(Patient, Patient.id == OperationPatient.patient_id)
            .where(OperationPatient.operation_id.in_(operation_ids))
        ).all():
            patients.setdefault(op_id, name)
        for op_id, name in db.session.execute(
            select(OperationPhysician.operation_id, Physician.name)
            .join(Physician, Physician.id == OperationPhysician.physician_id)
            .where(OperationPhysician.operation_id.in_(operation_ids))
        ).all():
            physicians.setdefault(op_id, name)
    return patients, physicians


def _operation_option(operation, patients, physicians):
    procedure = db.session.get(CatProcedureType, operation.procedure_type_id) if operation.procedure_type_id else None
    room = db.session.get(OperatingRoom, operation.room_id) if operation.room_id else None
    scheduled = operation.scheduled_at.strftime('%Y-%m-%d %H:%M') if operation.scheduled_at else ''
    return {
        'operation_id': str(operation.id),
        'procedure_code': procedure.code if procedure else '',
        'procedure_name': procedure_label(procedure) + (f' ({scheduled})' if scheduled else ''),
        'operating_room_name': room.code if room else _('Unassigned room'),
        'patient_name': patients.get(operation.id, ''),
        'physician_name': physicians.get(operation.id, ''),
    }


def _kit_inventory(kit_id):
    return _inventory(
        select(InstrumentFamily.code, InstrumentFamily.name, KitItem.quantity)
        .join(InstrumentFamily, InstrumentFamily.id == KitItem.family_id)
        .where(KitItem.kit_id == kit_id))


def new_session_context(institution_id, selection):
    """Options + preview for the New Counting Session form. selection: request args/form."""
    operations = available_operations(institution_id)
    operation = _pick(operations, selection.get('operation_id')) or (operations[0] if operations else None)
    kits = allowed_kits(operation, institution_id) if operation else []
    kit = _pick(kits, selection.get('kit_id')) or (kits[0] if kits else None)
    stations = allowed_stations(operation, institution_id) if operation else []
    station = _pick(stations, selection.get('capture_station_id')) or (stations[0] if stations else None)
    phases = allowed_phases(operation) if operation else []
    phase = _pick(phases, selection.get('counting_phase')) or (phases[0] if phases else None)
    patients, physicians = _operation_people([item.id for item in operations])
    operation_options = [_operation_option(item, patients, physicians) for item in operations]
    return {
        'operations': operation_options,
        'selected_operation': next((item for item in operation_options if operation and item['operation_id'] == str(operation.id)), None),
        'available_kits': [{'kit_id': str(item.id), 'kit_name': f'{item.name} v{item.version}'} for item in kits],
        'selected_kit': {'kit_id': str(kit.id), 'kit_name': kit.name} if kit else None,
        'available_capture_stations': [{'station_id': str(item.id), 'station_name': item.name} for item in stations],
        'selected_capture_station': {'station_id': str(station.id), 'station_name': station.name} if station else None,
        'counting_phases': [{'value': str(item.id), 'code': item.code, 'label': label('operation_phase', item.code, item.name)}
                            for item in phases],
        'selected_counting_phase': str(phase.id) if phase else None,
        'expected_inventory': _kit_inventory(kit.id) if kit else [],
        'session_setup_notice': setup_notice(operation, kits, phases),
    }


# --------------------------------------------------------------------------- creation

def create_work_session(form, ctx):
    """Validate the New Counting Session POST and stage WorkSession + snapshot + audit."""
    institution_id = ctx['institution_id']
    if not form.get('instrument_readiness_confirmed'):
        raise FormError(_('Confirm that the instrument set is physically present before starting.'))
    operation = _pick(available_operations(institution_id), form.get('operation_id'))
    if operation is None:
        raise FormError(_('Choose a valid scheduled or in-progress operation of your institution.'))
    if not operation.procedure_type_id:
        raise ConflictError(_('This operation has no procedure type; a counting session cannot be started for it.'))
    kits = allowed_kits(operation, institution_id)
    if not kits:
        raise ConflictError(_('No active kit is associated with this procedure. Ask an administrator to configure it.'))
    kit = _pick(kits, form.get('kit_id'))
    if kit is None:
        raise FormError(_('Choose an active kit of your institution that is allowed for this procedure.'))
    station = _pick(allowed_stations(operation, institution_id), form.get('capture_station_id'))
    if station is None:
        raise FormError(_("Choose an active capture station located in the operation's operating room."))
    phases = allowed_phases(operation)
    if not phases:
        raise ConflictError(_('No active counting phase is configured for this procedure. Ask an administrator to configure it.'))
    phase = _pick(phases, form.get('counting_phase'))
    if phase is None:
        raise FormError(_('Choose a counting phase configured for this procedure.'))
    status = db.session.execute(
        select(CatSessionStatus).where(CatSessionStatus.code == INITIAL_SESSION_STATUS)).scalar_one_or_none()
    if status is None:
        raise ConflictError(_('Session status "%(code)s" is not configured. Contact the administrator.', code=INITIAL_SESSION_STATUS))
    items = db.session.execute(select(KitItem).where(KitItem.kit_id == kit.id)).scalars().all()
    if not items:
        raise ConflictError(_('Kit "%(kit)s" has no instruments configured; a counting session cannot start with an empty kit.',
                              kit=kit.name))

    work_session = WorkSession(
        user_id=parse_uuid(ctx['user'].get('id')),  # always the authenticated operator, never a form field
        status_id=status.id,
        operation_id=operation.id,
        station_id=station.id,
        kit_id=kit.id,
        current_phase_id=phase.id if phase else None,
        phase_changed_at=db.func.now() if phase else None,
    )
    db.session.add(work_session)
    db.session.flush()
    snapshot_expected_inventory(work_session, items)
    record_audit('CREATE_SESSION', 'work_session', work_session.id, actor=ctx['user'], institution_id=institution_id)
    return work_session


def snapshot_expected_inventory(work_session, items):
    """Freeze the kit composition for this session (source = kit_snapshot)."""
    for item in items:
        db.session.add(ExpectedInventory(
            session_id=work_session.id, family_id=item.family_id,
            expected_quantity=item.quantity, source='kit_snapshot'))
    db.session.flush()


# --------------------------------------------------------------------------- reading a session

def session_view(work_session):
    """Header context + frozen expected inventory for the session stage screens."""
    operation = db.session.get(Operation, work_session.operation_id) if work_session.operation_id else None
    station = db.session.get(CaptureStation, work_session.station_id) if work_session.station_id else None
    room_id = (station.room_id if station else None) or (operation.room_id if operation else None)
    room = db.session.get(OperatingRoom, room_id) if room_id else None
    kit = db.session.get(Kit, work_session.kit_id) if work_session.kit_id else None
    phase = db.session.get(CatOperationPhase, work_session.current_phase_id) if work_session.current_phase_id else None
    status = db.session.get(CatSessionStatus, work_session.status_id)
    procedure = db.session.get(CatProcedureType, operation.procedure_type_id) if operation and operation.procedure_type_id else None
    patients, physicians = _operation_people([operation.id] if operation else [])
    inventory = _inventory(
        select(InstrumentFamily.code, InstrumentFamily.name, ExpectedInventory.expected_quantity)
        .join(InstrumentFamily, InstrumentFamily.id == ExpectedInventory.family_id)
        .where(ExpectedInventory.session_id == work_session.id))
    return {
        'session': {
            'session_id': str(work_session.id),
            'procedure_code': procedure.code if procedure else '',
            'procedure_name': procedure_label(procedure),
            'operating_room': room.code if room else _('Unspecified room'),
            'patient_name': patients.get(operation.id, '') if operation else '',
            'physician_name': physicians.get(operation.id, '') if operation else '',
            'kit_name': kit.name if kit else '',
            'capture_station_name': station.name if station else '',
            'phase_code': phase.code if phase else '',
            'phase_label': label('operation_phase', phase.code, phase.name) if phase else '',
            'status_code': status.code if status else '',
            'status_label': label('session_status', status.code, status.name) if status else '',
            'status_variant': 'info' if status and status.code == 'open' else 'neutral',
            'started_at': work_session.started_at.strftime('%Y-%m-%d %H:%M') if work_session.started_at else '',
        },
        'expected_inventory': inventory,
        'expected_inventory_total': sum(item['expected_quantity'] for item in inventory),
    }
