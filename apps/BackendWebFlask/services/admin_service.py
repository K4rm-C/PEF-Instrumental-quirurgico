"""Persistence for the IT Administrator catalog/configuration screens.

Every save_* function validates a submitted form, stages ORM changes plus their audit rows in
the current session and raises FormError on invalid input. The caller owns the transaction
(single commit on success, rollback on any error), so a kit and its items, a procedure and its
kit/phase associations, etc. are always saved all-or-nothing.
"""
import re
import uuid

from flask_babel import gettext as _
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from extensions import db
from localization.labels import label
from models.CatInstrumentCategory import CatInstrumentCategory
from models.CatInstrumentCycleStatus import CatInstrumentCycleStatus
from models.CatOperationPhase import CatOperationPhase
from models.CatProcedureType import CatProcedureType
from models.CaptureStation import CaptureStation
from models.Instrument import Instrument
from models.InstrumentFamily import InstrumentFamily
from models.Kit import Kit
from models.KitItem import KitItem
from models.MediaAsset import MediaAsset
from models.ModelClass import ModelClass
from models.OperatingRoom import OperatingRoom
from models.ProcedureKit import ProcedureKit
from models.ProcedurePhase import ProcedurePhase
from models.Role import Role
from models.User import User
from models.UserRole import UserRole
from models.YoloModel import YoloModel
from services.audit import record_audit

SMALLINT_MAX = 32767
MIN_PASSWORD_LENGTH = 8


class FormError(ValueError):
    """Invalid submitted data; the message is safe to show to the user (HTTP 400)."""
    status = 400


class ConflictError(FormError):
    """Valid input that conflicts with the current business state (HTTP 409)."""
    status = 409


# --------------------------------------------------------------------------- parsing helpers

def parse_uuid(value):
    try:
        return uuid.UUID(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _text(form, name, label, max_length, required=True):
    value = (form.get(name) or '').strip()
    if required and not value:
        raise FormError(_('%(field)s is required.', field=label))
    if len(value) > max_length:
        raise FormError(_('%(field)s must be at most %(max_length)s characters.', field=label, max_length=max_length))
    return value or None


def _active(form, name='status'):
    value = form.get(name, 'active')
    if value not in {'active', 'inactive'}:
        raise FormError(_('Choose a valid status.'))
    return value == 'active'


def _small_int(value, label, minimum):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise FormError(_('%(field)s must be a whole number.', field=label)) from None
    if number < minimum or number > SMALLINT_MAX:
        raise FormError(_('%(field)s must be between %(minimum)s and %(maximum)s.', field=label, minimum=minimum, maximum=SMALLINT_MAX))
    return number


def _family_label(family):
    return label('instrument_family', family.code, family.name)


def _checked_indexes(form, name):
    return {int(value) for value in form.getlist(name) if str(value).isdigit()}


def _lookup(model, raw_id, message, **filters):
    """Fetch model by submitted id, enforcing optional column filters (e.g. institution)."""
    entity_id = parse_uuid(raw_id)
    entity = db.session.get(model, entity_id) if entity_id else None
    if entity is None or any(getattr(entity, key) != value for key, value in filters.items()):
        raise FormError(message)
    return entity


def _exists(statement):
    return db.session.execute(statement.limit(1)).first() is not None


def audit_save(ctx, entity, name, created, was_active=None):
    """Flush (to get the id) and stage CREATE_/UPDATE_ plus ACTIVATE_/DEACTIVATE_ events."""
    db.session.flush()
    resource_type = entity.__tablename__
    record_audit(f"{'CREATE' if created else 'UPDATE'}_{name}", resource_type, entity.id,
                 actor=ctx['user'], institution_id=ctx['institution_id'])
    if not created and was_active is not None and hasattr(entity, 'active') and entity.active != was_active:
        record_audit(f"{'ACTIVATE' if entity.active else 'DEACTIVATE'}_{name}", resource_type, entity.id,
                     actor=ctx['user'], institution_id=ctx['institution_id'])
    return entity


# --------------------------------------------------------------------------- instrument families

def save_family(form, family, ctx):
    created = family is None
    was_active = None if created else family.active
    name = _text(form, 'name', _('Name'), 160)
    category = _lookup(CatInstrumentCategory, form.get('category'), _('Choose a valid category.'))
    active = _active(form)
    if created:
        code = _text(form, 'code', _('Code'), 64)
        if _exists(select(InstrumentFamily.id).where(InstrumentFamily.code == code)):
            raise FormError(_('An instrument family with this code already exists.'))
        family = InstrumentFamily(code=code)
        db.session.add(family)
    family.name = name
    family.category_id = category.id
    family.identify_text = _text(form, 'how_to_identify', _('How to Identify'), 10000, required=False)
    family.classify_text = _text(form, 'classification_characteristics', _('Classification Characteristics'), 10000, required=False)
    family.function_text = _text(form, 'function', _('Function'), 10000, required=False)
    family.active = active
    return audit_save(ctx, family, 'INSTRUMENT_FAMILY', created, was_active)


# --------------------------------------------------------------------------- instruments

def save_instrument(form, instrument, ctx):
    created = instrument is None
    was_active = None if created else instrument.active
    family = _lookup(InstrumentFamily, form.get('instrument_family'), _('Choose a valid instrument family.'))
    if not family.active and (created or family.id != instrument.family_id):
        raise FormError(_('The selected instrument family is inactive.'))
    status = _lookup(CatInstrumentCycleStatus, form.get('cycle_status'), _('Choose a valid cycle status.'))
    active = _active(form, 'active_status')
    if created:
        code = _text(form, 'internal_code', _('Internal Code'), 64, required=False)
        if code and _exists(select(Instrument.id).where(
                Instrument.institution_id == ctx['institution_id'], Instrument.internal_code == code)):
            raise FormError(_('An instrument with this internal code already exists in your institution.'))
        instrument = Instrument(internal_code=code, institution_id=ctx['institution_id'])
        db.session.add(instrument)
    instrument.family_id = family.id
    instrument.cycle_status_id = status.id
    instrument.active = active
    return audit_save(ctx, instrument, 'INSTRUMENT', created, was_active)


# --------------------------------------------------------------------------- kits

def kit_rows(form):
    return list(zip(form.getlist('instrument_family[]'), form.getlist('expected_quantity[]')))


def sync_kit_items(kit, rows):
    """Upsert KitItem per family, delete families removed from the form."""
    wanted = {}
    for raw_family, raw_quantity in rows:
        family = _lookup(InstrumentFamily, raw_family, _('Choose a valid instrument family for every kit row.'))
        if family.id in wanted:
            raise FormError(_('"%(family)s" appears more than once in the kit composition.', family=_family_label(family)))
        wanted[family.id] = _small_int(raw_quantity, _('Quantity for "%(family)s"', family=_family_label(family)), 1)
    if not wanted:
        raise FormError(_('Add at least one instrument family to the kit.'))
    existing = {item.family_id: item for item in kit.items} if kit.id else {}
    for family_id, item in existing.items():
        if family_id not in wanted:
            db.session.delete(item)
    for family_id, quantity in wanted.items():
        item = existing.get(family_id)
        if item is None:
            db.session.add(KitItem(kit=kit, family_id=family_id, quantity=quantity))
        else:
            item.quantity = quantity


def save_kit(form, kit, ctx):
    created = kit is None
    was_active = None if created else kit.active
    name = _text(form, 'name', _('Kit Name'), 160)
    active = _active(form)
    version = 1 if created else kit.version
    duplicate = select(Kit.id).where(
        Kit.institution_id == ctx['institution_id'], Kit.name == name, Kit.version == version)
    if not created:
        duplicate = duplicate.where(Kit.id != kit.id)
    if _exists(duplicate):
        raise FormError(_('A kit with this name and version already exists in your institution.'))
    if created:
        kit = Kit(institution_id=ctx['institution_id'], version=version)
        db.session.add(kit)
    kit.name = name
    kit.active = active
    sync_kit_items(kit, kit_rows(form))
    return audit_save(ctx, kit, 'KIT', created, was_active)


# --------------------------------------------------------------------------- procedures

def procedure_kit_rows(form):
    defaults, actives = _checked_indexes(form, 'is_default[]'), _checked_indexes(form, 'kit_active[]')
    return [
        {'kit': kit_id, 'technique_label': (label or '').strip(), 'is_default': index in defaults, 'active': index in actives}
        for index, (kit_id, label) in enumerate(zip(form.getlist('kit[]'), form.getlist('technique_label[]')))
    ]


def procedure_phase_rows(form):
    required, actives = _checked_indexes(form, 'count_required[]'), _checked_indexes(form, 'phase_active[]')
    return [
        {'phase': phase_id, 'sort_order': order, 'is_count_required': index in required, 'active': index in actives}
        for index, (phase_id, order) in enumerate(zip(form.getlist('phase[]'), form.getlist('sort_order[]')))
    ]


def sync_procedure_kits(procedure, rows, institution_id):
    """Only this institution's kits are touched; removed associations are deactivated."""
    wanted = {}
    for row in rows:
        kit = _lookup(Kit, row['kit'], _('Choose a valid kit for every associated kit row.'), institution_id=institution_id)
        if kit.id in wanted:
            raise FormError(_('Kit "%(kit)s" is associated more than once.', kit=kit.name))
        if len(row['technique_label']) > 160:
            raise FormError(_('Technique Label must be at most 160 characters.'))
        if row['is_default'] and not row['active']:
            raise FormError(_('The default kit must be active.'))
        wanted[kit.id] = row
    if sum(row['is_default'] for row in wanted.values()) > 1:
        raise FormError(_('Only one associated kit can be the Default Kit.'))

    existing = {}
    if procedure.id:
        existing = {pk.kit_id: pk for pk in db.session.execute(
            select(ProcedureKit).join(Kit, Kit.id == ProcedureKit.kit_id).where(
                ProcedureKit.procedure_type_id == procedure.id, Kit.institution_id == institution_id)
        ).scalars()}
        # clear defaults first so the partial unique index never sees two defaults mid-flush
        for pk in existing.values():
            pk.is_default = False
        db.session.flush()
    for kit_id, pk in existing.items():
        if kit_id not in wanted:
            pk.active = False
    for kit_id, row in wanted.items():
        pk = existing.get(kit_id)
        if pk is None:
            pk = ProcedureKit(procedure_type=procedure, kit_id=kit_id)
            db.session.add(pk)
        pk.technique_label = row['technique_label'] or None
        pk.is_default = row['is_default']
        pk.active = row['active']


def sync_procedure_phases(procedure, rows):
    """Upsert ProcedurePhase keeping sort_order unique; removed phases are deactivated."""
    wanted = {}
    for row in rows:
        phase = _lookup(CatOperationPhase, row['phase'], _('Choose a valid phase for every counting phase row.'))
        if phase.id in wanted:
            raise FormError(_('Phase "%(phase)s" is listed more than once.', phase=label('operation_phase', phase.code, phase.name)))
        row['sort_order'] = _small_int(row['sort_order'], _('Order for "%(phase)s"', phase=label('operation_phase', phase.code, phase.name)), 1)
        wanted[phase.id] = row
    orders = [row['sort_order'] for row in wanted.values()]
    if len(orders) != len(set(orders)):
        raise FormError(_('Each counting phase needs a different order.'))

    existing = {}
    if procedure.id:
        existing = {pp.phase_id: pp for pp in db.session.execute(
            select(ProcedurePhase).where(ProcedurePhase.procedure_type_id == procedure.id)).scalars()}
    # move existing rows out of the way so reordering never trips uk_procedure_phase_type_sort
    # temporary slots sit above every final value (final max <= max(orders) + len(existing))
    offset = max([pp.sort_order for pp in existing.values()] + orders + [0]) + len(existing)
    if offset + len(existing) > SMALLINT_MAX:
        raise FormError(_('Counting phase order values are too large.'))
    for index, pp in enumerate(existing.values(), start=1):
        pp.sort_order = offset + index
    db.session.flush()
    next_free = max(orders + [0])
    for phase_id, pp in existing.items():
        if phase_id not in wanted:
            next_free += 1
            pp.sort_order = next_free
            pp.active = False
    for phase_id, row in wanted.items():
        pp = existing.get(phase_id)
        if pp is None:
            pp = ProcedurePhase(procedure_type=procedure, phase_id=phase_id)
            db.session.add(pp)
        pp.sort_order = row['sort_order']
        pp.is_count_required = row['is_count_required']
        pp.active = row['active']


def save_procedure(form, procedure, ctx):
    created = procedure is None
    code = _text(form, 'code', _('Procedure Code'), 64)
    name = _text(form, 'name', _('Procedure Name'), 200)
    duplicate = select(CatProcedureType.id).where(CatProcedureType.code == code)
    if not created:
        duplicate = duplicate.where(CatProcedureType.id != procedure.id)
    if _exists(duplicate):
        raise FormError(_('A procedure with this code already exists.'))
    if created:
        procedure = CatProcedureType()
        db.session.add(procedure)
    procedure.code = code
    procedure.name = name
    db.session.flush()
    sync_procedure_kits(procedure, procedure_kit_rows(form), ctx['institution_id'])
    sync_procedure_phases(procedure, procedure_phase_rows(form))
    return audit_save(ctx, procedure, 'PROCEDURE', created)


# --------------------------------------------------------------------------- operating rooms / stations

def save_operating_room(form, room, ctx):
    created = room is None
    was_active = None if created else room.active
    code = _text(form, 'code', _('Room Code'), 32)
    name = _text(form, 'name', _('Room Name'), 120)
    active = _active(form)
    duplicate = select(OperatingRoom.id).where(
        OperatingRoom.institution_id == ctx['institution_id'], OperatingRoom.code == code)
    if not created:
        duplicate = duplicate.where(OperatingRoom.id != room.id)
    if _exists(duplicate):
        raise FormError(_('An operating room with this code already exists in your institution.'))
    if created:
        room = OperatingRoom(institution_id=ctx['institution_id'])
        db.session.add(room)
    room.code, room.name, room.active = code, name, active
    return audit_save(ctx, room, 'OPERATING_ROOM', created, was_active)


def save_capture_station(form, station, ctx):
    created = station is None
    was_active = None if created else station.active
    name = _text(form, 'name', _('Station Name'), 120)
    room = _lookup(OperatingRoom, form.get('operating_room'), _('Choose a valid operating room.'),
                   institution_id=ctx['institution_id'])
    active = _active(form)
    if created:
        station = CaptureStation()  # ROI stays NULL until configured
        db.session.add(station)
    station.name, station.room_id, station.active = name, room.id, active
    return audit_save(ctx, station, 'CAPTURE_STATION', created, was_active)


# --------------------------------------------------------------------------- users / roles

def sync_user_roles(user, roles):
    """Make user's UserRole rows exactly `roles` (removed roles are deleted)."""
    wanted = {role.id: role for role in roles}
    for assignment in (list(user.user_roles) if user.id else []):
        if assignment.role_id in wanted:
            wanted.pop(assignment.role_id)
        else:
            db.session.delete(assignment)
    for role in wanted.values():
        db.session.add(UserRole(user=user, role=role))


def validate_password(password, confirmation):
    if len(password or '') < MIN_PASSWORD_LENGTH:
        raise FormError(_('Password must contain at least %(count)s characters.', count=MIN_PASSWORD_LENGTH))
    if password != confirmation:
        raise FormError(_('Passwords do not match.'))
    return generate_password_hash(password)


def change_user_password(user, form, ctx):
    user.password_hash = validate_password(form.get('new_password', ''), form.get('confirm_new_password', ''))
    user.password_updated_at = db.func.now()
    db.session.flush()
    record_audit('CHANGE_USER_PASSWORD', 'user', user.id, actor=ctx['user'], institution_id=ctx['institution_id'])


def set_user_active(user, active, ctx):
    if not active and str(user.id) == str(ctx['user'].get('id')):
        raise FormError(_('You cannot deactivate your own account.'))
    if user.active != active:
        user.active = active
        record_audit(f"{'ACTIVATE' if active else 'DEACTIVATE'}_USER", 'user', user.id,
                     actor=ctx['user'], institution_id=ctx['institution_id'])


def save_role(form, role, ctx):
    created = role is None
    description = _text(form, 'description', _('Description'), 255)
    if created:
        code = _text(form, 'code', _('Role Code'), 64)
        if _exists(select(Role.id).where(Role.institution_id == ctx['institution_id'], Role.code == code)):
            raise FormError(_('A role with this code already exists in your institution.'))
        role = Role(code=code, institution_id=ctx['institution_id'])
        db.session.add(role)
    role.description = description
    return audit_save(ctx, role, 'ROLE', created)


# --------------------------------------------------------------------------- vision models

_ASSET_URI = re.compile(r'^(gs|s3|minio)://([^/\s]+)/(\S+)$')
_SHA256 = re.compile(r'^[0-9a-fA-F]{64}$')


def _media_asset(reference):
    match = _ASSET_URI.match(reference or '')
    if not match:
        raise FormError(_('Model Asset Reference must be a storage URI like gs://bucket/path/model.pt or s3://bucket/path/model.pt.'))
    scheme, bucket, key = match.groups()
    if len(bucket) > 128:
        raise FormError(_('The bucket name is too long.'))
    asset = db.session.execute(
        select(MediaAsset).where(MediaAsset.bucket == bucket, MediaAsset.object_key == key)).scalar_one_or_none()
    if asset is None:
        asset = MediaAsset(gcs_uri=reference, bucket=bucket, object_key=key, kind='weights',
                           storage_provider='gcs' if scheme == 'gs' else 'minio')
        db.session.add(asset)
    return asset


def sync_model_classes(model, form):
    wanted = {}
    families = set()
    for raw_class, raw_family in zip(form.getlist('yolo_class_id[]'), form.getlist('instrument_family[]')):
        class_id = _small_int(raw_class, _('YOLO Class ID'), 0)
        family = _lookup(InstrumentFamily, raw_family, _('Choose a valid instrument family for every class mapping.'))
        if class_id in wanted or family.id in families:
            raise FormError(_('Each YOLO class ID and each instrument family can be mapped only once.'))
        wanted[class_id] = family.id
        families.add(family.id)
    existing = list(model.model_classes) if model.id else []
    for mc in existing:  # mapping rows are configuration (no history); replace them
        db.session.delete(mc)
    db.session.flush()
    for class_id, family_id in wanted.items():
        db.session.add(ModelClass(model=model, yolo_class_id=class_id, family_id=family_id))


def activate_model(model, ctx):
    """Single active model: deactivate the previous one before activating (uk_yolo_model_active)."""
    for other in db.session.execute(select(YoloModel).where(YoloModel.active.is_(True), YoloModel.id != model.id)).scalars():
        other.active = False
        record_audit('DEACTIVATE_YOLO_MODEL', 'yolo_model', other.id, actor=ctx['user'], institution_id=ctx['institution_id'])
    db.session.flush()
    if not model.active:
        model.active = True
        db.session.flush()
        record_audit('ACTIVATE_YOLO_MODEL', 'yolo_model', model.id, actor=ctx['user'], institution_id=ctx['institution_id'])


def save_vision_model(form, model, ctx):
    created = model is None
    checksum = (form.get('checksum') or '').strip() or None
    if checksum and not _SHA256.match(checksum):
        raise FormError(_('Checksum must be a 64-character SHA-256 hex digest.'))
    asset = _media_asset((form.get('model_asset_reference') or '').strip())
    if created:
        version_tag = _text(form, 'version_tag', _('Version Tag'), 32)
        if _exists(select(YoloModel.id).where(YoloModel.version_tag == version_tag)):
            raise FormError(_('A vision model with this version tag already exists.'))
        model = YoloModel(version_tag=version_tag, active=False)
        db.session.add(model)
    model.media_asset = asset
    model.checksum = checksum.lower() if checksum else None
    sync_model_classes(model, form)
    audit_save(ctx, model, 'YOLO_MODEL', created)
    if created and form.get('active'):
        activate_model(model, ctx)
    return model


def save_user(form, user, ctx):
    """Create/update a user inside the administrator's institution, syncing UserRole rows."""
    created = user is None
    was_active = None if created else user.active
    name = _text(form, 'name', _('Name'), 160)
    email = _text(form, 'email', _('Email'), 255).lower()
    local, __, domain = email.partition('@')
    if not local or '.' not in domain or '@' in domain or any(char.isspace() for char in email):
        raise FormError(_('Enter a valid email address.'))
    active = _active(form)
    codes = list(dict.fromkeys(form.getlist('roles[]')))
    if not codes:
        raise FormError(_('Assign at least one role.'))
    roles = db.session.execute(select(Role).where(
        Role.institution_id == ctx['institution_id'], Role.code.in_(codes))).scalars().all()
    if len(roles) != len(codes):
        raise FormError(_('Choose roles that belong to your institution.'))
    duplicate = select(User.id).where(User.email == email)
    if not created:
        duplicate = duplicate.where(User.id != user.id)
    if _exists(duplicate):
        raise FormError(_('A user with this email already exists.'))
    if created:
        user = User(institution_id=ctx['institution_id'],
                    password_hash=validate_password(form.get('password', ''), form.get('confirm_password', '')))
        db.session.add(user)
    elif not active and str(user.id) == str(ctx['user'].get('id')):
        raise FormError(_('You cannot deactivate your own account.'))
    user.name, user.email, user.active = name, email, active
    sync_user_roles(user, roles)
    return audit_save(ctx, user, 'USER', created, was_active)


def save_institution(form, institution, ctx):
    name = _text(form, 'name', _('Institution Name'), 200)
    if not _active(form):
        raise FormError(_('Your own institution cannot be deactivated from here.'))
    institution.name = name
    db.session.flush()
    record_audit('UPDATE_INSTITUTION', 'institution', institution.id, actor=ctx['user'], institution_id=institution.id)
    return institution
