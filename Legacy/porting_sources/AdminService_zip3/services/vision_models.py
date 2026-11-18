import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import audit
from config import Config
from database import db
from errors import bad_request, conflict, not_found
from models import InstrumentFamily, MediaAsset, ModelClass, YoloModel
from services.common import integer, iso, matches_search, paginate, parse_uuid, text, utc_now


ENTITY = 'VISION_MODEL'
RESOURCE = 'vision_model'
CHECKSUM_RE = re.compile(r'^[0-9a-fA-F]{64}$')


def _class_counts():
    return dict(db.execute(
        select(ModelClass.model_id, func.count(ModelClass.id)).group_by(ModelClass.model_id)
    ).all())


def _serialize(model, class_count, asset=None):
    return {
        'id': str(model.id),
        'version_tag': model.version_tag,
        'active': model.active,
        'checksum': (model.checksum or '').strip(),
        'published_at': iso(model.published_at),
        'class_count': class_count,
        'asset_reference': asset.object_key if asset else '',
    }


def list_models(*, search='', status='all', page=1, per_page=50):
    counts = _class_counts()
    assets = {row.id: row for row in db.execute(select(MediaAsset).where(MediaAsset.kind == 'weights')).scalars()}
    status = (status or 'all').lower()
    rows = []
    for model in db.execute(select(YoloModel).order_by(YoloModel.published_at.desc())).scalars():
        if status == 'active' and not model.active:
            continue
        if status == 'inactive' and model.active:
            continue
        if not matches_search(search, model.version_tag, model.checksum):
            continue
        rows.append(_serialize(model, counts.get(model.id, 0), assets.get(model.media_asset_id)))
    result = paginate(rows, page, per_page)
    result['filters'] = {'search': search or '', 'status': status}
    return result


def _get(model_id):
    model = db.get(YoloModel, parse_uuid(model_id))
    if model is None:
        raise not_found('vision_model_not_found', 'Vision model not found.')
    return model


def get_model(model_id):
    model = _get(model_id)
    asset = db.get(MediaAsset, model.media_asset_id) if model.media_asset_id else None
    families = {row.id: row for row in db.execute(select(InstrumentFamily)).scalars()}
    classes = db.execute(
        select(ModelClass).where(ModelClass.model_id == model.id).order_by(ModelClass.yolo_class_id)
    ).scalars().all()
    data = _serialize(model, len(classes), asset)
    data['classes'] = [{
        'yolo_class_id': item.yolo_class_id,
        'family': ({'id': str(item.family_id), 'code': families[item.family_id].code,
                    'name': families[item.family_id].name}
                   if item.family_id in families else {'id': str(item.family_id), 'code': None, 'name': None}),
    } for item in classes]
    return data


def _checksum(payload):
    value = text(payload, 'checksum', max_length=64, label='Checksum')
    if value and not CHECKSUM_RE.match(value):
        raise bad_request('invalid_checksum', 'Checksum must be a 64-character SHA-256 hex value.', 'checksum')
    return value.lower() or None


def _classes(payload):
    mapping = {}
    for raw in payload.get('classes') or []:
        class_id = integer((raw or {}).get('yolo_class_id'), name='yolo_class_id', minimum=0, maximum=32767,
                           message='YOLO class IDs must be whole numbers from 0.')
        family_id = parse_uuid((raw or {}).get('family_id'), 'invalid_family',
                               'Choose a valid instrument family for every class.', 'classes')
        if class_id in mapping:
            raise bad_request('duplicate_class_id', 'Each YOLO class ID can only be mapped once.', 'classes')
        mapping[class_id] = family_id
    if len(set(mapping.values())) != len(mapping):
        raise bad_request('duplicate_class_family', 'Each instrument family can only be mapped once per model.',
                          'classes')
    if mapping:
        found = set(db.execute(
            select(InstrumentFamily.id).where(InstrumentFamily.id.in_(set(mapping.values())))
        ).scalars())
        if found != set(mapping.values()):
            raise bad_request('invalid_family', 'Choose a valid instrument family for every class.', 'classes')
    return mapping


def _asset_for(reference, checksum, existing=None):
    """media_asset row describing where the weights live (registered by reference)."""
    if not reference:
        return None
    if existing is not None and existing.object_key == reference:
        existing.sha256 = checksum
        existing.updated_at = utc_now()
        return existing
    bucket = Config.MODEL_WEIGHTS_BUCKET
    asset = db.scalar(select(MediaAsset).where(MediaAsset.bucket == bucket, MediaAsset.object_key == reference))
    if asset is None:
        now = utc_now()
        asset = MediaAsset(
            gcs_uri=f's3://{bucket}/{reference}',
            bucket=bucket,
            object_key=reference,
            content_type='application/octet-stream',
            kind='weights',
            storage_provider='other',
            sha256=checksum,
            created_at=now,
            updated_at=now,
        )
        db.add(asset)
        db.flush()
    return asset


def _write_classes(model, mapping):
    for item in db.execute(select(ModelClass).where(ModelClass.model_id == model.id)).scalars():
        db.delete(item)
    db.flush()
    for class_id, family_id in sorted(mapping.items()):
        db.add(ModelClass(model_id=model.id, family_id=family_id, yolo_class_id=class_id))


def _deactivate_others(model_id):
    for other in db.execute(select(YoloModel).where(YoloModel.active.is_(True), YoloModel.id != model_id)).scalars():
        other.active = False
        audit.record(f'DEACTIVATE_{ENTITY}', RESOURCE, other.id)
    db.flush()


def create_model(actor, payload):
    version_tag = text(payload, 'version_tag', required=True, max_length=32, label='Version tag')
    reference = text(payload, 'asset_reference', max_length=500, label='Model asset reference')
    checksum = _checksum(payload)
    mapping = _classes(payload)
    activate = bool(payload.get('active'))
    if activate and not mapping:
        raise bad_request('classes_required', 'Map at least one YOLO class before activating a model.', 'classes')

    model = YoloModel(version_tag=version_tag, checksum=checksum, active=False, published_at=utc_now())
    db.add(model)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict('version_tag_exists', 'A vision model with this version tag already exists.', 'version_tag')
    asset = _asset_for(reference, checksum)
    model.media_asset_id = asset.id if asset else None
    _write_classes(model, mapping)
    db.flush()
    audit.record(f'CREATE_{ENTITY}', RESOURCE, model.id)
    if activate:
        _deactivate_others(model.id)
        model.active = True
        audit.record(f'ACTIVATE_{ENTITY}', RESOURCE, model.id)
    db.commit()
    return get_model(model.id)


def update_model(actor, model_id, payload):
    model = _get(model_id)
    reference = text(payload, 'asset_reference', max_length=500, label='Model asset reference')
    checksum = _checksum(payload)
    mapping = _classes(payload)
    current = {item.yolo_class_id: item.family_id
               for item in db.execute(select(ModelClass).where(ModelClass.model_id == model.id)).scalars()}
    if mapping != current and model.active:
        raise bad_request('active_model_classes_locked',
                          'The class mapping of the active model cannot be changed. Register a new version instead.',
                          'classes')
    existing_asset = db.get(MediaAsset, model.media_asset_id) if model.media_asset_id else None
    before = (existing_asset.object_key if existing_asset else '', (model.checksum or '').strip() or None)
    asset = _asset_for(reference, checksum, existing_asset)
    model.media_asset_id = asset.id if asset else None
    model.checksum = checksum
    if mapping != current:
        _write_classes(model, mapping)
    db.flush()
    if before != (reference, checksum) or mapping != current:
        audit.record(f'UPDATE_{ENTITY}', RESOURCE, model.id)
    db.commit()
    return get_model(model.id)


def activate_model(actor, model_id):
    model = _get(model_id)
    if model.active:
        return get_model(model.id)
    if not db.scalar(select(func.count()).select_from(ModelClass).where(ModelClass.model_id == model.id)):
        raise bad_request('classes_required', 'Map at least one YOLO class before activating a model.', 'classes')
    _deactivate_others(model.id)
    model.active = True
    audit.record(f'ACTIVATE_{ENTITY}', RESOURCE, model.id)
    db.commit()
    return get_model(model.id)
