"""RF-AD-08 Vision models: upload weights, map classes to families, publish, activate.

Step 1 (upload_model): a `.pt` (size from RF-AD-10 settings, PyTorch zip magic bytes) is spooled,
hashed on the server (SHA-256) and stored in object storage under `models/` with a media_asset
(kind `weights`). A checksum already registered is rejected. Class names are read WITHOUT executing
the file (yolo_weights); if that fails the administrator must send names.yaml / JSON, otherwise the
upload is refused (no step 2 without names). The model is created inactive with its class names.
Step 2 (publish_mapping): every class is mapped to a family or explicitly rejected; families of the
active catalog the model does not cover need confirmation (overlay); model_class rows are written.
Activate: transaction that deactivates the previous model; sessions already started keep
work_session.yolo_model_id (never touched here) and the vision worker is not notified (RF-AD-08 §4:
the worker loads its weights file; deploying the file to it is an operations step).
Deactivate: allowed unless it would leave zero active models while active capture stations exist.
Legacy models registered by reference (before ADMIN-01, no class names) keep the manual mapping
editor (update_model). Audit (D-5): CREATE_ / PUBLISH_ (= vision.model.publish) / ACTIVATE_
(= vision.model.activate) / DEACTIVATE_ / UPDATE_VISION_MODEL. Models are shared by the deployment.
"""
import re
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from . import audit, settings, storage, yolo_weights
from .config import Config
from .database import db
from .errors import ApiError, bad_request, conflict, not_found
from .models import CaptureStation, InstrumentFamily, MediaAsset, ModelClass, YoloModel
from .common import integer, iso, matches_search, paginate, parse_uuid, text, utc_now


ENTITY = 'VISION_MODEL'
RESOURCE = 'vision_model'
CHECKSUM_RE = re.compile(r'^[0-9a-fA-F]{64}$')
NAMES_FILE_MAX = 1024 * 1024


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
        'architecture': model.architecture or '',
        'model_size': model.model_size or '',
        'uploaded': bool(model.class_names),
        'size_bytes': asset.size_bytes if asset else None,
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
        if not matches_search(search, model.version_tag, model.checksum, model.architecture):
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


def _normalize(value):
    value = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '', value.lower())


def _active_families():
    return db.execute(select(InstrumentFamily).where(InstrumentFamily.active.is_(True))
                      .order_by(InstrumentFamily.name)).scalars().all()


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
    if model.class_names:
        mapped = {item.yolo_class_id: str(item.family_id) for item in classes}
        by_key = {}
        for family in families.values():
            if family.active:
                by_key.setdefault(_normalize(family.code), family)
                by_key.setdefault(_normalize(family.name), family)
        rows = []
        for class_id, name in sorted(((int(k), v) for k, v in model.class_names.items())):
            suggestion = by_key.get(_normalize(name))
            rows.append({'yolo_class_id': class_id, 'name': name, 'family_id': mapped.get(class_id),
                         'suggested_family_id': str(suggestion.id) if suggestion else None})
        data['class_names'] = rows
        covered = set(mapped.values()) or {row['suggested_family_id'] for row in rows if row['suggested_family_id']}
        data['missing_active_families'] = [{'id': str(f.id), 'code': f.code, 'name': f.name}
                                           for f in _active_families() if str(f.id) not in covered]
    return data


def _assert_unique_checksum(checksum, except_id=None):
    query = select(YoloModel.version_tag).where(func.lower(func.trim(YoloModel.checksum)) == checksum.lower())
    if except_id is not None:
        query = query.where(YoloModel.id != except_id)
    existing = db.execute(query).scalar_one_or_none()
    if existing:
        raise conflict('checksum_exists', f'These weights are already registered as version {existing}.', 'file')


# --- Step 1: upload -----------------------------------------------------------------------------

def upload_model(actor, payload):
    version_tag = text(payload, 'version_tag', required=True, max_length=32, label='Version tag')
    architecture = text(payload, 'architecture', max_length=64, label='Architecture') or None
    model_size = text(payload, 'model_size', max_length=8, label='Model size') or None
    if db.execute(select(YoloModel.id).where(YoloModel.version_tag == version_tag)).first():
        raise conflict('version_tag_exists', 'A vision model with this version tag already exists.', 'version_tag')
    max_mb = settings.value(actor.institution_id, 'model_weights_max_mb')
    upload = storage.receive(payload.get('file'), max_bytes=max_mb * 1024 * 1024, field='file',
                             allowed={'application/x-pytorch'}, allowed_extensions=set(settings.MODEL_EXTENSIONS))
    try:
        _assert_unique_checksum(upload.sha256)
        try:
            parsed = yolo_weights.read_checkpoint(upload.path)
        except yolo_weights.WeightsParseError:
            parsed = None
        names_file = payload.get('names_file')
        if names_file is not None and getattr(names_file, 'filename', ''):
            content = names_file.stream.read(NAMES_FILE_MAX + 1)
            if len(content) > NAMES_FILE_MAX:
                raise bad_request('file_too_large', 'The names file exceeds 1 MB.', 'names_file')
            try:
                names = yolo_weights.parse_names_file(content)
            except yolo_weights.WeightsParseError as exc:
                raise bad_request('invalid_names_file', 'The names file must be names.yaml or JSON {id: name}.',
                                  'names_file') from exc
            if parsed and parsed['names'] != names:
                raise bad_request('names_mismatch', 'The names file does not match the classes inside the weights.',
                                  'names_file')
            parsed = {**(parsed or {'architecture': None, 'model_size': None}), 'names': names}
        if parsed is None:
            raise bad_request('names_required', 'The class names could not be read from the weights. '
                                                'Upload names.yaml or a JSON {id: name} file.', 'names_file')
        try:
            stored = storage.put(upload, 'models/')
        except storage.StorageUnavailable as exc:
            raise ApiError(503, 'storage_unavailable', 'Object storage is not available right now.') from exc
        now = utc_now()
        asset = MediaAsset(gcs_uri=stored.uri, bucket=stored.bucket, object_key=stored.key,
                           content_type='application/octet-stream', kind='weights', storage_provider=stored.provider,
                           sha256=upload.sha256, size_bytes=upload.size, created_at=now, updated_at=now)
        db.add(asset)
        db.flush()
        model = YoloModel(version_tag=version_tag, checksum=upload.sha256, active=False, published_at=now,
                          media_asset_id=asset.id, architecture=architecture or parsed.get('architecture'),
                          model_size=model_size or parsed.get('model_size'),
                          class_names={str(k): v for k, v in sorted(parsed['names'].items())})
        db.add(model)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise conflict('version_tag_exists', 'A vision model with this version tag already exists.', 'version_tag')
        audit.record(f'CREATE_{ENTITY}', RESOURCE, model.id)
        db.commit()
    finally:
        upload.discard()
    return get_model(model.id)


# --- Step 2: mapping and publication --------------------------------------------------------------

def publish_mapping(actor, model_id, payload):
    model = _get(model_id)
    if not model.class_names:
        raise bad_request('model_without_class_names', 'This model has no class names; use the manual mapping.', 'classes')
    if model.active:
        raise bad_request('active_model_classes_locked',
                          'The class mapping of the active model cannot be changed. Register a new version instead.',
                          'classes')
    class_ids = {int(key) for key in model.class_names}
    decisions = payload.get('mapping') or {}
    mapping, rejected, unmapped = {}, [], []
    for class_id in sorted(class_ids):
        choice = str(decisions.get(str(class_id)) or decisions.get(class_id) or '').strip()
        if choice == 'reject':
            rejected.append(class_id)
        elif choice:
            mapping[class_id] = parse_uuid(choice, 'invalid_family', 'Choose a valid instrument family for every class.',
                                           'classes')
        else:
            unmapped.append(class_id)
    if unmapped:
        raise bad_request('unmapped_classes', 'Map every class of the model to a family or reject it.', 'classes')
    if not mapping:
        raise bad_request('classes_required', 'Map at least one YOLO class before activating a model.', 'classes')
    if len(set(mapping.values())) != len(mapping):
        raise bad_request('duplicate_class_family', 'Each instrument family can only be mapped once per model.',
                          'classes')
    found = set(db.execute(select(InstrumentFamily.id).where(InstrumentFamily.id.in_(set(mapping.values())))).scalars())
    if found != set(mapping.values()):
        raise bad_request('invalid_family', 'Choose a valid instrument family for every class.', 'classes')
    missing = [family for family in _active_families() if family.id not in found]
    if missing and str(payload.get('confirm_missing_families') or '').lower() not in {'1', 'true', 'on', 'yes'}:
        raise conflict('missing_families', settings.value(actor.institution_id, 'missing_families_overlay_text')
                       + ' ' + ', '.join(family.code for family in missing), 'classes')
    _write_classes(model, mapping)
    model.published_at = utc_now()
    audit.record(f'PUBLISH_{ENTITY}', RESOURCE, model.id)
    db.commit()
    result = get_model(model.id)
    result['rejected_classes'] = rejected
    return result


# --- Legacy manual mapping (models registered by reference, without class names) ------------------

def _checksum(payload, except_id=None):
    value = text(payload, 'checksum', max_length=64, label='Checksum')
    if value and not CHECKSUM_RE.match(value):
        raise bad_request('invalid_checksum', 'Checksum must be a 64-character SHA-256 hex value.', 'checksum')
    if value:
        _assert_unique_checksum(value, except_id)
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
    """media_asset row describing where legacy weights live (registered by reference)."""
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
        asset = MediaAsset(gcs_uri=f's3://{bucket}/{reference}', bucket=bucket, object_key=reference,
                           content_type='application/octet-stream', kind='weights', storage_provider='other',
                           sha256=checksum, created_at=now, updated_at=now)
        db.add(asset)
        db.flush()
    return asset


def _write_classes(model, mapping):
    for item in db.execute(select(ModelClass).where(ModelClass.model_id == model.id)).scalars():
        db.delete(item)
    db.flush()
    for class_id, family_id in sorted(mapping.items()):
        db.add(ModelClass(model_id=model.id, family_id=family_id, yolo_class_id=class_id))


def update_model(actor, model_id, payload):
    """Manual editor of legacy models; uploaded models use publish_mapping (their weights are immutable)."""
    model = _get(model_id)
    if model.class_names:
        return publish_mapping(actor, model_id, payload)
    reference = text(payload, 'asset_reference', max_length=500, label='Model asset reference')
    checksum = _checksum(payload, except_id=model.id)
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


# --- Activation -------------------------------------------------------------------------------

def _deactivate_others(model_id):
    for other in db.execute(select(YoloModel).where(YoloModel.active.is_(True), YoloModel.id != model_id)).scalars():
        other.active = False
        audit.record(f'DEACTIVATE_{ENTITY}', RESOURCE, other.id)
    db.flush()


def activate_model(actor, model_id):
    """New sessions use this model; sessions already started keep work_session.yolo_model_id."""
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


def deactivate_model(actor, model_id):
    model = _get(model_id)
    if not model.active:
        return get_model(model.id)
    stations = db.scalar(select(func.count()).select_from(CaptureStation).where(CaptureStation.active.is_(True)))
    if stations:
        raise conflict('last_active_model', 'Activate another model first: active capture stations need one.', 'active')
    model.active = False
    audit.record(f'DEACTIVATE_{ENTITY}', RESOURCE, model.id)
    db.commit()
    return get_model(model.id)
