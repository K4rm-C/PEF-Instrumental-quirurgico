from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

import audit
from database import db
from errors import ApiError, bad_request, conflict, not_found
from models import CatInstrumentCategory, CatProcedureType, InstrumentFamily, Kit, KitItem, ProcedureKit
from services.catalogs import available_counts
from services.sessions import _commit, _uuid


def _status(payload):
    value = payload.get('status', 'active')
    if value not in {'active', 'inactive'}:
        raise bad_request('invalid_status', 'status must be active or inactive.', 'status')
    return value == 'active'


def _owned(actor, kit_id):
    kit = db.get(Kit, kit_id)
    if kit is None or kit.institution_id != actor.institution_id:
        raise not_found('kit_not_found', 'Kit not found.')
    return kit


def _composition(payload):
    values = payload.get('items')
    if not isinstance(values, list) or not values:
        raise bad_request('empty_composition', 'A kit must contain at least one family.', 'items')
    result = {}
    for index, row in enumerate(values):
        field = f'items[{index}]'
        if not isinstance(row, dict):
            raise bad_request('invalid_composition', 'Each kit row must be an object.', field)
        family_id = _uuid(row.get('family_id'), f'{field}.family_id')
        try:
            quantity = int(row.get('quantity'))
        except (TypeError, ValueError) as exc:
            raise bad_request('invalid_quantity', 'Quantity must be a positive integer.', field) from exc
        if quantity < 1 or quantity > 32767:
            raise bad_request('invalid_quantity', 'Quantity must be between 1 and 32767.', field)
        if family_id in result:
            raise bad_request('duplicate_family', 'A family can only appear once in a kit.', field)
        if db.get(InstrumentFamily, family_id) is None:
            raise bad_request('invalid_family', 'Choose a valid instrument family.', field)
        result[family_id] = quantity
    return result


def _kit_name(payload):
    name = str(payload.get('name') or '').strip()
    if not name:
        raise bad_request('name_required', 'Kit name is required.', 'name')
    if len(name) > 160:
        raise bad_request('name_too_long', 'Kit name must be at most 160 characters.', 'name')
    return name


def _next_version(actor, name):
    version = db.scalar(
        select(func.max(Kit.version)).where(
            Kit.institution_id == actor.institution_id,
            Kit.name == name,
        )
    )
    return int(version or 0) + 1


def _write_items(kit, composition):
    for family_id, quantity in composition.items():
        db.add(KitItem(kit_id=kit.id, family_id=family_id, quantity=quantity))


def _serialize(actor, kit):
    items = db.execute(
        select(KitItem, InstrumentFamily)
        .join(InstrumentFamily, InstrumentFamily.id == KitItem.family_id)
        .where(KitItem.kit_id == kit.id)
        .order_by(InstrumentFamily.name)
    ).all()
    categories = {
        row.id: row for row in db.execute(select(CatInstrumentCategory)).scalars()
    }
    counts = available_counts(actor.institution_id)
    procedures = db.execute(
        select(CatProcedureType)
        .join(ProcedureKit, ProcedureKit.procedure_type_id == CatProcedureType.id)
        .where(ProcedureKit.kit_id == kit.id)
        .order_by(CatProcedureType.name)
    ).scalars()
    return {
        'id': str(kit.id),
        'name': kit.name,
        'version': kit.version,
        'active': kit.active,
        'created_at': kit.created_at.isoformat() if kit.created_at else None,
        'items': [{
            'family': {'id': str(family.id), 'code': family.code, 'name': family.name},
            'category': {
                'code': categories[family.category_id].code,
                'name': categories[family.category_id].name,
            } if family.category_id in categories else None,
            'quantity': item.quantity,
            'available_count': counts.get(family.id, 0),
        } for item, family in items],
        'procedures': [{
            'id': str(row.id),
            'code': row.code,
            'name': row.name,
        } for row in procedures],
    }


def list_kits(actor, *, search='', procedure_type='', status='active', page=1, per_page=20,
              matches=()):
    try:
        page = max(1, int(page))
        per_page = min(100, max(1, int(per_page)))
    except (TypeError, ValueError) as exc:
        raise bad_request('invalid_pagination', 'page and per_page must be integers.') from exc
    if status not in {'active', 'inactive', 'all'}:
        raise bad_request('invalid_status', 'status must be active, inactive, or all.', 'status')
    filters = [Kit.active.is_(True)]
    query = select(Kit).where(Kit.institution_id == actor.institution_id)
    search_terms = [value.strip() for value in (search, *matches) if value and value.strip()]
    if search_terms:
        filters.append(or_(*(Kit.name.ilike(f'%{term}%') for term in search_terms)))
    if procedure_type:
        try:
            procedure_uuid = _uuid(procedure_type, 'procedure_type')
        except ApiError:
            procedure_uuid = None
        procedure_query = select(ProcedureKit.kit_id).join(
            CatProcedureType, CatProcedureType.id == ProcedureKit.procedure_type_id
        )
        if procedure_uuid:
            procedure_query = procedure_query.where(
                (ProcedureKit.procedure_type_id == procedure_uuid)
                | (CatProcedureType.code == procedure_type)
            )
        else:
            procedure_query = procedure_query.where(CatProcedureType.code == procedure_type)
        filters.append(Kit.id.in_(procedure_query))
    if matches:
        procedure_matches = [
            CatProcedureType.name.ilike(f'%{term.strip()}%')
            | CatProcedureType.code.ilike(f'%{term.strip()}%')
            for term in matches if term and term.strip()
        ]
        if procedure_matches:
            filters.append(Kit.id.in_(
                select(ProcedureKit.kit_id)
                .join(CatProcedureType, CatProcedureType.id == ProcedureKit.procedure_type_id)
                .where(or_(*procedure_matches))
            ) | or_(*(Kit.name.ilike(f'%{term.strip()}%') for term in matches
                      if term and term.strip())))
    if filters:
        query = query.where(*filters)
    rows = db.execute(
        query.order_by(Kit.name, Kit.version.desc())
        .offset((page - 1) * per_page).limit(per_page)
    ).scalars().all()
    total = int(db.scalar(
        select(func.count(Kit.id)).where(Kit.institution_id == actor.institution_id, *filters)
    ) or 0)
    procedure_rows = db.execute(select(CatProcedureType).order_by(CatProcedureType.name)).scalars()
    kit_ids = [row.id for row in rows]
    item_totals = {}
    procedure_options = defaultdict(list)
    if kit_ids:
        item_totals = {
            kit_id: (int(count), int(quantity or 0))
            for kit_id, count, quantity in db.execute(
                select(KitItem.kit_id, func.count(KitItem.id), func.sum(KitItem.quantity))
                .where(KitItem.kit_id.in_(kit_ids))
                .group_by(KitItem.kit_id)
            ).all()
        }
        for kit_id, procedure in db.execute(
            select(ProcedureKit.kit_id, CatProcedureType)
            .join(CatProcedureType, CatProcedureType.id == ProcedureKit.procedure_type_id)
            .where(ProcedureKit.kit_id.in_(kit_ids))
            .order_by(CatProcedureType.name)
        ).all():
            procedure_options[kit_id].append({
                'id': str(procedure.id),
                'code': procedure.code,
                'name': procedure.name,
            })
    result = {
        'items': [{
            'id': str(kit.id),
            'name': kit.name,
            'version': kit.version,
            'active': kit.active,
            'family_count': item_totals.get(kit.id, (0, 0))[0],
            'total_expected': item_totals.get(kit.id, (0, 0))[1],
            'procedures': procedure_options[kit.id],
        } for kit in rows],
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': (total + per_page - 1) // per_page,
        'active_only': True,
        'filters': {'search': search, 'procedure_type': procedure_type, 'status': 'active'},
        'procedure_type_options': [{'code': row.code, 'name': row.name} for row in procedure_rows],
    }
    return result


def get_kit(actor, kit_id):
    return _serialize(actor, _owned(actor, kit_id))


def create_kit(actor, payload):
    name = _kit_name(payload)
    composition = _composition(payload)
    now = datetime.now(timezone.utc)
    kit = Kit(
        name=name,
        version=_next_version(actor, name),
        active=_status(payload),
        institution_id=actor.institution_id,
        created_at=now,
        updated_at=now,
    )
    db.add(kit)
    try:
        db.flush()
        _write_items(kit, composition)
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise conflict('kit_exists', 'A kit with this name and version already exists.', 'name') from exc
    audit.record(actor, 'CREATE_KIT', 'kit', kit.id)
    _commit()
    return get_kit(actor, kit.id)


def update_kit(actor, kit_id, payload):
    kit = _owned(actor, kit_id)
    name = _kit_name(payload)
    composition = _composition(payload)
    active = _status(payload)
    current = {
        row.family_id: row.quantity
        for row in db.execute(select(KitItem).where(KitItem.kit_id == kit.id)).scalars()
    }
    if name == kit.name and composition == current and active == kit.active:
        return get_kit(actor, kit.id)
    now = datetime.now(timezone.utc)
    successor = Kit(
        name=name,
        version=_next_version(actor, name),
        active=active,
        institution_id=kit.institution_id,
        created_at=now,
        updated_at=now,
    )
    db.add(successor)
    try:
        db.flush()
        _write_items(successor, composition)
        was_active = kit.active
        kit.active = False
        kit.updated_at = now
        for link in db.execute(select(ProcedureKit).where(ProcedureKit.kit_id == kit.id)).scalars():
            link.kit_id = successor.id
            link.updated_at = now
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise conflict('kit_exists', 'A kit with this name and version already exists.', 'name') from exc
    audit.record(actor, 'UPDATE_KIT', 'kit', successor.id)
    if was_active:
        audit.record(actor, 'SUPERSEDE_KIT', 'kit', kit.id)
    _commit()
    return get_kit(actor, successor.id)
