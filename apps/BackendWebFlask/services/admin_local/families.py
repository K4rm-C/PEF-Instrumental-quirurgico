"""RF-AD-02 Instrument families (the *type* that is counted and detected) and their teaching photos.

Families are a shared catalog (no institution column in the schema): every administrator maintains
the same list that OP/SPD/App use. No physical delete (FKs from instrument, kit_item, model_class):
deactivation hides it from new registrations. Deactivating a family mapped by the active vision model
needs an explicit confirmation (`confirm_active_model`), otherwise 409 `family_in_active_model`.
Examples: image uploaded to object storage (MIME and size from RF-AD-10 settings, default image 5 MB),
media_asset row with server SHA-256, family_example row (text, order). Audit names keep the
platform convention (D-5): CREATE_/UPDATE_/DEACTIVATE_INSTRUMENT_FAMILY = catalog.family.upsert /
catalog.family.deactivate of the RF; example changes are recorded as UPDATE_INSTRUMENT_FAMILY.
"""
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from . import audit, settings, storage
from .database import db
from .errors import ApiError, bad_request, conflict, not_found
from .models import (
    CatInstrumentCategory, FamilyExample, InstrumentFamily, KitItem, Kit, MediaAsset, ModelClass, YoloModel,
)
from .catalogs import category_rank
from .common import (
    integer, matches_search, matches_status, paginate, parse_uuid, status_filter, status_flag, text, utc_now,
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


def _example_counts():
    return dict(db.execute(select(FamilyExample.family_id, func.count(FamilyExample.id))
                           .group_by(FamilyExample.family_id)).all())


def _active_model_families():
    return set(db.execute(select(ModelClass.family_id).join(YoloModel, YoloModel.id == ModelClass.model_id)
                          .where(YoloModel.active.is_(True))).scalars())


def list_families(*, search='', category='', status='active', page=1, per_page=20, matches=()):
    categories = _categories()
    examples = _example_counts()
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
        rows.append({**_serialize(row, cat), 'example_count': examples.get(row.id, 0)})
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
    data['in_active_model'] = row.id in _active_model_families()
    data['examples'] = [{
        'id': str(example.id), 'example_text': example.example_text or '', 'sort_order': example.sort_order,
        'media_asset_id': str(example.media_asset_id) if example.media_asset_id else None,
        'content_type': asset.content_type if asset else None,
    } for example, asset in db.execute(
        select(FamilyExample, MediaAsset).outerjoin(MediaAsset, MediaAsset.id == FamilyExample.media_asset_id)
        .where(FamilyExample.family_id == row.id).order_by(FamilyExample.sort_order, FamilyExample.id)).all()]
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
    if was_active and not family.active and family.id in _active_model_families() \
            and str(payload.get('confirm_active_model') or '').lower() not in {'1', 'true', 'on', 'yes'}:
        # RF-AD-02: deactivating a family of the active model needs an explicit confirmation.
        raise conflict('family_in_active_model',
                       'This family is detected by the active vision model. Confirm to deactivate it.', 'status')
    after = (family.name, family.category_id, family.identify_text, family.classify_text, family.function_text)
    audit.record_update(ENTITY, RESOURCE, family.id, was_active=was_active,
                        is_active=family.active, changed=before != after)
    db.commit()
    return _serialize(family, category, detail=True)


# --- Examples (family_example + media_asset + object storage) --------------------------------

def _family(family_id):
    family = db.get(InstrumentFamily, parse_uuid(family_id))
    if family is None:
        raise not_found('family_not_found', 'Instrument family not found.')
    return family


def _order(payload):
    return integer(payload.get('sort_order') or 0, name='sort_order', minimum=0, maximum=32767,
                   message='Order must be between 0 and 32767.')


def add_example(actor, family_id, payload):
    family = _family(family_id)
    example_text = text(payload, 'example_text', max_length=2000, label='Example text') or None
    order = _order(payload)
    max_mb = settings.value(actor.institution_id, 'family_example_max_mb')
    upload = storage.receive(payload.get('file'), max_bytes=max_mb * 1024 * 1024, field='file',
                             allowed=settings.value(actor.institution_id, 'family_example_allowed_mime'))
    try:
        try:
            stored = storage.put(upload, 'family-examples/')
        except storage.StorageUnavailable as exc:
            raise ApiError(503, 'storage_unavailable', 'Object storage is not available right now.') from exc
        now = utc_now()
        asset = MediaAsset(gcs_uri=stored.uri, bucket=stored.bucket, object_key=stored.key,
                           content_type=upload.content_type,
                           kind='jpeg' if upload.content_type == 'image/jpeg' else 'other',
                           storage_provider=stored.provider, sha256=upload.sha256, size_bytes=upload.size,
                           created_at=now, updated_at=now)
        db.add(asset)
        db.flush()
        db.add(FamilyExample(family_id=family.id, media_asset_id=asset.id, example_text=example_text,
                             sort_order=order))
        audit.record('UPDATE_INSTRUMENT_FAMILY', 'instrument_family', family.id)
        db.commit()
    finally:
        upload.discard()
    return get_family(family.id)


def _example(family_id, example_id):
    family = _family(family_id)
    example = db.get(FamilyExample, parse_uuid(example_id))
    if example is None or example.family_id != family.id:
        raise not_found('example_not_found', 'Example not found.')
    return family, example


def update_example(actor, family_id, example_id, payload):
    family, example = _example(family_id, example_id)
    example.example_text = text(payload, 'example_text', max_length=2000, label='Example text') or None
    example.sort_order = _order(payload)
    audit.record('UPDATE_INSTRUMENT_FAMILY', 'instrument_family', family.id)
    db.commit()
    return get_family(family.id)


def delete_example(actor, family_id, example_id):
    """The example row goes away; the stored image and its media_asset are kept (traceability)."""
    family, example = _example(family_id, example_id)
    db.delete(example)
    audit.record('UPDATE_INSTRUMENT_FAMILY', 'instrument_family', family.id)
    db.commit()
    return get_family(family.id)
