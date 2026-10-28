from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from . import audit
from .database import db
from .errors import bad_request, conflict, not_found
from .models import CatInstrumentCategory, InstrumentFamily, KitItem, Kit
from .catalogs import category_rank
from .common import (
    matches_search, matches_status, paginate, parse_uuid, status_filter, status_flag, text,
)


ENTITY = 'INSTRUMENT_FAMILY'
RESOURCE = 'instrument_family'


def _serialize(row, category, *, detail=False):
    data = {
        'id': str(row.id),
        'code': row.code,
        'name': row.name,
        'active': row.active,
        'function_text': row.function_text or '',
        'category': {'id': str(category.id), 'code': category.code, 'name': category.name,
                     'rank': category_rank(category.code)} if category else None,
    }
    if detail:
        data.update({
            'identify_text': row.identify_text or '',
            'classify_text': row.classify_text or '',
        })
    return data


def _categories():
    return {row.id: row for row in db.execute(select(CatInstrumentCategory)).scalars()}


def list_families(*, search='', category='', status='active', page=1, per_page=20, matches=()):
    categories = _categories()
    wanted_status = status_filter(status)
    rows = []
    for row in db.execute(select(InstrumentFamily).order_by(InstrumentFamily.code)).scalars():
        cat = categories.get(row.category_id)
        if category and (cat is None or cat.code != category):
            continue
        if not matches_status(row.active, wanted_status):
            continue
        if not matches_search(search, row.code, row.name, row.function_text, cat.name if cat else '',
                              matches=matches, keys=(row.code, row.name, cat.code if cat else None)):
            continue
        rows.append(_serialize(row, cat))
    result = paginate(rows, page, per_page)
    result['filters'] = {'search': search or '', 'category': category or '', 'status': wanted_status}
    return result


def get_family(family_id):
    row = db.get(InstrumentFamily, parse_uuid(family_id))
    if row is None:
        raise not_found('family_not_found', 'Instrument family not found.')
    data = _serialize(row, db.get(CatInstrumentCategory, row.category_id), detail=True)
    data['kits_using'] = db.scalar(
        select(func.count()).select_from(KitItem).join(Kit, Kit.id == KitItem.kit_id)
        .where(KitItem.family_id == row.id, Kit.active.is_(True))
    ) or 0
    return data


def _category(payload):
    raw = text(payload, 'category_id', required=True, label='Category', code='category_required')
    category = db.get(CatInstrumentCategory, parse_uuid(raw, 'invalid_category', 'Choose a valid category.', 'category_id'))
    if category is None:
        raise bad_request('invalid_category', 'Choose a valid category.', 'category_id')
    return category


def create_family(payload):
    code = text(payload, 'code', required=True, max_length=64, label='Code')
    name = text(payload, 'name', required=True, max_length=160, label='Name')
    category = _category(payload)
    family = InstrumentFamily(
        code=code,
        name=name,
        category_id=category.id,
        identify_text=text(payload, 'identify_text') or None,
        classify_text=text(payload, 'classify_text') or None,
        function_text=text(payload, 'function_text') or None,
        active=status_flag(payload),
    )
    db.add(family)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict('family_code_exists', 'That family code already exists.', 'code')
    audit.record(f'CREATE_{ENTITY}', RESOURCE, family.id)
    db.commit()
    return _serialize(family, category, detail=True)


def update_family(family_id, payload):
    family = db.get(InstrumentFamily, parse_uuid(family_id))
    if family is None:
        raise not_found('family_not_found', 'Instrument family not found.')
    name = text(payload, 'name', required=True, max_length=160, label='Name')
    category = _category(payload)
    was_active = family.active
    before = (family.name, family.category_id, family.identify_text, family.classify_text, family.function_text)
    family.name = name
    family.category_id = category.id
    family.identify_text = text(payload, 'identify_text') or None
    family.classify_text = text(payload, 'classify_text') or None
    family.function_text = text(payload, 'function_text') or None
    family.active = status_flag(payload)
    after = (family.name, family.category_id, family.identify_text, family.classify_text, family.function_text)
    audit.record_update(ENTITY, RESOURCE, family.id, was_active=was_active,
                        is_active=family.active, changed=before != after)
    db.commit()
    return _serialize(family, category, detail=True)
