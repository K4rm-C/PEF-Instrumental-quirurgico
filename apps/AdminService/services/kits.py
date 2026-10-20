from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import audit
from database import db
from errors import bad_request, conflict, not_found
from models import CatInstrumentCategory, CatProcedureType, InstrumentFamily, Kit, KitItem, ProcedureKit
from services.catalogs import available_counts, category_rank
from services.common import (
    integer, iso, matches_search, matches_status, paginate, parse_uuid, status_filter, status_flag,
    text, utc_now,
)


ENTITY = 'KIT'
RESOURCE = 'kit'


def _procedures_by_kit():
    procedures = {row.id: row for row in db.execute(select(CatProcedureType)).scalars()}
    linked = {}
    for link in db.execute(select(ProcedureKit)).scalars():
        procedure = procedures.get(link.procedure_type_id)
        if procedure is not None:
            linked.setdefault(link.kit_id, []).append(procedure)
    return procedures, linked


def list_kits(actor, *, search='', procedure_type='', status='active', page=1, per_page=20,
              active_only=False, matches=()):
    procedures, linked = _procedures_by_kit()
    totals, family_counts = Counter(), Counter()
    for item in db.execute(select(KitItem)).scalars():
        totals[item.kit_id] += item.quantity
        family_counts[item.kit_id] += 1

    wanted_status = 'active' if active_only else status_filter(status)
    procedure_type = (procedure_type or '').strip().lower()
    rows = []
    query = (select(Kit).where(Kit.institution_id == actor.institution_id)
             .order_by(Kit.name, Kit.version.desc()))
    for kit in db.execute(query).scalars():
        kit_procedures = linked.get(kit.id, [])
        if procedure_type and procedure_type not in {p.code.lower() for p in kit_procedures} \
                and procedure_type not in {str(p.id) for p in kit_procedures}:
            continue
        if not matches_status(kit.active, wanted_status):
            continue
        if not matches_search(search, kit.name, *(p.name for p in kit_procedures),
                              *(p.code for p in kit_procedures), matches=matches,
                              keys=(kit.id, kit.name, *(p.code for p in kit_procedures))):
            continue
        rows.append({
            'id': str(kit.id),
            'name': kit.name,
            'version': kit.version,
            'active': kit.active,
            'family_count': family_counts[kit.id],
            'total_expected': totals[kit.id],
            'procedures': [{'id': str(p.id), 'code': p.code, 'name': p.name} for p in kit_procedures],
        })
    result = paginate(rows, page, per_page)
    result['filters'] = {'search': search or '', 'procedure_type': procedure_type,
                         'status': wanted_status}
    result['active_only'] = active_only
    result['procedure_type_options'] = sorted(
        ({'code': p.code, 'name': p.name} for p in procedures.values()), key=lambda o: o['name'],
    )
    return result


def _owned(actor, kit_id):
    kit = db.get(Kit, parse_uuid(kit_id))
    if kit is None or kit.institution_id != actor.institution_id:
        raise not_found('kit_not_found', 'Kit not found.')
    return kit


def get_kit(actor, kit_id):
    kit = _owned(actor, kit_id)
    families = {row.id: row for row in db.execute(select(InstrumentFamily)).scalars()}
    categories = {row.id: row for row in db.execute(select(CatInstrumentCategory)).scalars()}
    counts = available_counts()
    items = []
    for item in db.execute(select(KitItem).where(KitItem.kit_id == kit.id)).scalars():
        family = families.get(item.family_id)
        category = categories.get(family.category_id) if family else None
        items.append({
            'family': {'id': str(family.id), 'code': family.code, 'name': family.name} if family else None,
            'category': {'code': category.code, 'name': category.name,
                         'rank': category_rank(category.code)} if category else None,
            'quantity': item.quantity,
            'available_count': counts.get(item.family_id, 0),
        })
    _, linked = _procedures_by_kit()
    return {
        'id': str(kit.id),
        'name': kit.name,
        'version': kit.version,
        'active': kit.active,
        'created_at': iso(kit.created_at),
        'items': items,
        'procedures': [{'id': str(p.id), 'code': p.code, 'name': p.name} for p in linked.get(kit.id, [])],
    }


def _composition(payload):
    raw_items = payload.get('items')
    if not isinstance(raw_items, list):
        raise bad_request('invalid_composition', 'Kit composition rows are incomplete.', 'items')
    composition = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise bad_request('invalid_composition', 'Kit composition rows are incomplete.', 'items')
        family_id = parse_uuid(raw.get('family_id'), 'invalid_composition',
                               'Choose a valid instrument family and quantity for every row.', 'items')
        quantity = integer(raw.get('quantity'), name='quantity', minimum=1, maximum=32767,
                           message='Expected quantity must be between 1 and 32767.')
        if family_id in composition:
            raise bad_request('duplicate_family',
                              'An instrument family can only appear once in the composition.', 'items')
        composition[family_id] = quantity
    if not composition:
        raise bad_request('empty_composition',
                          'Add at least one instrument family to the kit composition.', 'items')
    existing = set(db.execute(
        select(InstrumentFamily.id).where(InstrumentFamily.id.in_(composition))
    ).scalars())
    if existing != set(composition):
        raise bad_request('invalid_family', 'Choose instrument families from the available catalog.', 'items')
    return composition


def _write_items(kit, composition):
    for family_id, quantity in composition.items():
        db.add(KitItem(kit_id=kit.id, family_id=family_id, quantity=quantity))


def _next_version(actor, name):
    current = db.scalar(
        select(func.max(Kit.version)).where(Kit.institution_id == actor.institution_id, Kit.name == name)
    )
    return (current or 0) + 1


def create_kit(actor, payload):
    name = text(payload, 'name', required=True, max_length=160, label='Kit name')
    composition = _composition(payload)
    now = utc_now()
    kit = Kit(
        name=name,
        version=_next_version(actor, name),
        active=status_flag(payload),
        institution_id=actor.institution_id,
        created_at=now,
        updated_at=now,
    )
    db.add(kit)
    try:
        db.flush()
        _write_items(kit, composition)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict('kit_exists', 'A kit with this name and version already exists for the institution.', 'name')
    audit.record(f'CREATE_{ENTITY}', RESOURCE, kit.id)
    db.commit()
    return get_kit(actor, kit.id)


def update_kit(actor, kit_id, payload):
    kit = _owned(actor, kit_id)
    name = text(payload, 'name', required=True, max_length=160, label='Kit name')
    composition = _composition(payload)
    new_active = status_flag(payload)
    current = {item.family_id: item.quantity
               for item in db.execute(select(KitItem).where(KitItem.kit_id == kit.id)).scalars()}
    now = utc_now()

    if name == kit.name and composition == current and new_active == kit.active:
        # Nothing changed: no new version.
        return get_kit(actor, kit.id)

    new_kit = Kit(
        name=name,
        version=_next_version(actor, name),
        active=new_active,
        institution_id=kit.institution_id,
        created_at=now,
        updated_at=now,
    )
    db.add(new_kit)
    try:
        db.flush()
        _write_items(new_kit, composition)
        was_active = kit.active
        kit.active = False
        kit.updated_at = now
        for link in db.execute(select(ProcedureKit).where(ProcedureKit.kit_id == kit.id)).scalars():
            link.kit_id = new_kit.id
            link.updated_at = now
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict('kit_exists', 'A kit with this name and version already exists for the institution.', 'name')
    audit.record(f'UPDATE_{ENTITY}', RESOURCE, new_kit.id)
    if was_active:
        audit.record(f'SUPERSEDE_{ENTITY}', RESOURCE, kit.id)
    db.commit()
    return get_kit(actor, new_kit.id)
