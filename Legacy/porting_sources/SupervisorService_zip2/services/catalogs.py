from sqlalchemy import func, select

from database import db
from errors import ApiError, not_found
from models import (
    CaptureStation,
    CatInstrumentCategory,
    CatInstrumentCycleStatus,
    CatOperationPhase,
    CatProcedureType,
    CatSurgicalRole,
    Instrument,
    InstrumentFamily,
    Institution,
    Kit,
    KitItem,
    OperatingRoom,
    Patient,
    Physician,
    PrivacyNoticeVersion,
    ProcedurePhase,
    Role,
    User,
    UserRole,
)


CATEGORY_ORDER = (
    'cutting', 'dissection', 'grasping', 'hemostasis', 'retraction', 'suction', 'suturing',
)


def available_counts(institution_id):
    available_id = db.scalar(
        select(CatInstrumentCycleStatus.id).where(CatInstrumentCycleStatus.code == 'available')
    )
    if available_id is None:
        raise ApiError(503, 'catalog_unavailable', 'Required instrument cycle status "available" is missing.')
    rows = db.execute(
        select(Instrument.family_id, func.count(Instrument.id))
        .where(
            Instrument.institution_id == institution_id,
            Instrument.active.is_(True),
            Instrument.cycle_status_id == available_id,
        )
        .group_by(Instrument.family_id)
    ).all()
    return {family_id: int(count) for family_id, count in rows}


def total_counts(institution_id):
    rows = db.execute(
        select(Instrument.family_id, func.count(Instrument.id))
        .where(
            Instrument.institution_id == institution_id,
            Instrument.active.is_(True),
        )
        .group_by(Instrument.family_id)
    ).all()
    return {family_id: int(count) for family_id, count in rows}


def scheduling_catalogs(actor):
    institution_id = actor.institution_id
    operators = db.execute(
        select(User.id, User.name)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            User.active.is_(True),
            User.institution_id == institution_id,
            Role.institution_id == institution_id,
            Role.code == 'station_operator',
        )
        .order_by(User.name)
    ).all()
    procedures = db.execute(
        select(CatProcedureType).order_by(CatProcedureType.name)
    ).scalars().all()
    categories = db.execute(
        select(CatInstrumentCategory).order_by(CatInstrumentCategory.name)
    ).scalars().all()
    cycle_statuses = db.execute(
        select(CatInstrumentCycleStatus).order_by(CatInstrumentCycleStatus.name)
    ).scalars().all()
    rooms = db.execute(
        select(OperatingRoom)
        .where(OperatingRoom.institution_id == institution_id, OperatingRoom.active.is_(True))
        .order_by(OperatingRoom.code)
    ).scalars().all()
    stations = db.execute(
        select(CaptureStation)
        .join(OperatingRoom, OperatingRoom.id == CaptureStation.room_id)
        .where(
            OperatingRoom.institution_id == institution_id,
            OperatingRoom.active.is_(True),
            CaptureStation.active.is_(True),
        )
        .order_by(CaptureStation.name)
    ).scalars().all()
    kits = db.execute(
        select(Kit).where(
            Kit.institution_id == institution_id,
            Kit.active.is_(True),
        ).order_by(Kit.name)
    ).scalars().all()
    patients = db.execute(
        select(Patient).where(
            Patient.institution_id == institution_id,
            Patient.active.is_(True),
        ).order_by(Patient.display_name)
    ).scalars().all()
    physicians = db.execute(
        select(Physician).where(
            Physician.institution_id == institution_id,
            Physician.active.is_(True),
        ).order_by(Physician.name)
    ).scalars().all()
    surgical_roles = db.execute(
        select(CatSurgicalRole).order_by(CatSurgicalRole.name)
    ).scalars().all()
    phases = db.execute(
        select(CatOperationPhase).where(CatOperationPhase.active.is_(True)).order_by(CatOperationPhase.name)
    ).scalars().all()
    notices = db.execute(
        select(PrivacyNoticeVersion)
        .where(PrivacyNoticeVersion.active.is_(True))
        .order_by(PrivacyNoticeVersion.version)
    ).scalars().all()

    categories_by_id = {row.id: row for row in categories}
    counts = available_counts(institution_id)
    family_rows = []
    for family in db.execute(
        select(InstrumentFamily).order_by(InstrumentFamily.name)
    ).scalars():
        category = categories_by_id.get(family.category_id)
        family_rows.append({
            'id': str(family.id),
            'code': family.code,
            'name': family.name,
            'category_code': category.code if category else '',
            'category_label': category.name if category else 'Other',
            'category_rank': CATEGORY_ORDER.index(category.code) if category and category.code in CATEGORY_ORDER
            else len(CATEGORY_ORDER),
            'active': family.active,
            'available_count': counts.get(family.id, 0),
        })
    institution = db.get(Institution, institution_id)
    institution_roles = db.execute(
        select(Role).where(Role.institution_id == institution_id).order_by(Role.code)
    ).scalars().all()
    category_rows = [{
        'id': str(row.id),
        'code': row.code,
        'name': row.name,
        'rank': CATEGORY_ORDER.index(row.code) if row.code in CATEGORY_ORDER else len(CATEGORY_ORDER),
    } for row in categories]
    active_families = [row for row in family_rows if row['active']]
    procedure_rows = [{'id': str(row.id), 'code': row.code, 'name': row.name} for row in procedures]
    room_rows = [{'id': str(row.id), 'code': row.code, 'name': row.name} for row in rooms]

    result = {
        'operators': [{'id': str(row.id), 'name': row.name} for row in operators],
        'procedures': procedure_rows,
        'procedure_types': procedure_rows,
        'rooms': room_rows,
        'operating_rooms': room_rows,
        'stations': [{
            'id': str(row.id),
            'name': row.name,
            'room_id': str(row.room_id),
        } for row in stations],
        'kits': [{'id': str(row.id), 'name': row.name, 'version': row.version} for row in kits],
        'patients': [{'id': str(row.id), 'name': row.display_name} for row in patients],
        'physicians': [{'id': str(row.id), 'name': row.name} for row in physicians],
        'surgical_roles': [{'code': row.code, 'name': row.name} for row in surgical_roles],
        'phases': [{'id': str(row.id), 'code': row.code, 'name': row.name} for row in phases],
        'privacy_notices': [{
            'id': str(row.id),
            'version': row.version,
            'document_uri': row.document_uri,
            'effective_at': row.effective_at.isoformat() if row.effective_at else None,
        } for row in notices],
        'families': family_rows,
        'active_families': active_families,
        'categories': category_rows,
        'cycle_statuses': [{'id': str(row.id), 'code': row.code, 'name': row.name}
                           for row in cycle_statuses],
        'institutions': ([{
            'id': str(institution.id),
            'name': institution.name,
            'active': institution.active,
        }] if institution else []),
        'roles': [{
            'id': str(row.id),
            'code': row.code,
            'description': row.description,
            'institution_id': str(row.institution_id),
        } for row in institution_roles],
    }
    return result


def kit_expected_lines(actor, kit_id):
    kit = db.get(Kit, kit_id)
    if kit is None or kit.institution_id != actor.institution_id:
        raise not_found('kit_not_found', 'Kit not found.')
    counts = available_counts(actor.institution_id)
    totals = total_counts(actor.institution_id)
    families = {row.id: row for row in db.execute(select(InstrumentFamily)).scalars()}
    categories = {row.id: row for row in db.execute(select(CatInstrumentCategory)).scalars()}
    result = []
    for row in db.execute(select(KitItem).where(KitItem.kit_id == kit.id)).scalars():
        family = families.get(row.family_id)
        category = categories.get(family.category_id) if family else None
        available = counts.get(row.family_id, 0)
        requested = int(row.quantity)
        semaphore = 'BLOCK' if requested > available else (
            'WARN' if requested > max(1, int(available * 0.5)) else 'OK'
        )
        category_code = category.code if category else ''
        result.append({
            'family_id': str(row.family_id),
            'family_name': family.name if family else '',
            'family_code': family.code if family else '',
            'expected_quantity': requested,
            'stock_available': available,
            'stock_total': totals.get(row.family_id, 0),
            'semaphore': semaphore,
            'category_code': category_code,
            'category_label': category.name if category else 'Other',
            'category_rank': CATEGORY_ORDER.index(category_code)
            if category_code in CATEGORY_ORDER else len(CATEGORY_ORDER),
        })
    return result


def initial_phase(procedure_type_id):
    row = db.execute(
        select(CatOperationPhase.code)
        .join(ProcedurePhase, ProcedurePhase.phase_id == CatOperationPhase.id)
        .where(
            ProcedurePhase.procedure_type_id == procedure_type_id,
            ProcedurePhase.active.is_(True),
            CatOperationPhase.active.is_(True),
        )
        .order_by(ProcedurePhase.sort_order)
        .limit(1)
    ).first()
    return row[0] if row else 'start'
