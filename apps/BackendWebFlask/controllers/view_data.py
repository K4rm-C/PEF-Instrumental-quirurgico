from collections import Counter
from datetime import datetime

from flask import current_app
from flask_babel import gettext as _, ngettext
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from localization.labels import active_label, is_localized, label, role_name
from models.AccessAudit import AccessAudit
from models.CaptureStation import CaptureStation
from models.CatDiscrepancyReason import CatDiscrepancyReason
from models.CatInstrumentCategory import CatInstrumentCategory
from models.CatInstrumentCycleStatus import CatInstrumentCycleStatus
from models.CatOperationPhase import CatOperationPhase
from models.CatOperationStatus import CatOperationStatus
from models.CatProcedureType import CatProcedureType
from models.CatSessionStatus import CatSessionStatus
from models.CountEvent import CountEvent
from models.Discrepancy import Discrepancy
from models.HumanCorrection import HumanCorrection
from models.Instrument import Instrument
from models.InstrumentFamily import InstrumentFamily
from models.Institution import Institution
from models.Kit import Kit
from models.KitItem import KitItem
from models.MediaAsset import MediaAsset
from models.ModelClass import ModelClass
from models.Operation import Operation
from models.OperationPatient import OperationPatient
from models.OperationPhysician import OperationPhysician
from models.OperatingRoom import OperatingRoom
from models.Patient import Patient
from models.Physician import Physician
from models.ProcedureKit import ProcedureKit
from models.ProcedurePhase import ProcedurePhase
from models.Role import Role
from models.User import User
from models.UserRole import UserRole
from models.WorkSession import WorkSession
from models.YoloModel import YoloModel
from services import analytics_service as analytics


def _demo_mode():
    """FRONTEND_DEMO_MODE: skip PostgreSQL and use fallback/demo values (see config.py)."""
    return bool(current_app.config.get('FRONTEND_DEMO_MODE'))


def _read(operation, fallback):
    if _demo_mode():
        return fallback
    try:
        return operation()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Database-backed view data query failed; using fallback data')
        return fallback


def _label(value):
    return value or _('Not specified')


def _variant(status):
    code = (status or '').lower()
    if code in {'closed', 'available', 'active', 'resolved'}:
        return 'success'
    if code in {'pending', 'pending_review', 'in_progress'}:
        return 'warning'
    if code in {'cancelled', 'denied', 'missing', 'discrepancy'}:
        return 'danger'
    return 'neutral'


def _status(active):
    return active_label(active), ('success' if active else 'neutral')


def _family_label(family):
    """Instrument family name, localized by code for unmodified seeded families."""
    return label('instrument_family', family.code, family.name) if family is not None else ''


def _catalog_options(catalog, rows):
    """Select options for catalog rows: value = id, label localized by code, sorted by label."""
    options = [{'value': str(row.id), 'code': row.code, 'label': label(catalog, row.code, row.name)} for row in rows]
    return sorted(options, key=lambda option: option['label'].lower())


def _family_options(families):
    return sorted(({'value': str(item.id), 'code': item.code, 'label': _family_label(item)} for item in families),
                  key=lambda option: option['label'].lower())


def _scoped(statement, column, institution_id):
    """Restrict a query to one institution when institution_id is given (None = no filter)."""
    return statement.where(column == institution_id) if institution_id else statement


def _users(institution_id=None):
    rows = db.session.execute(_scoped(select(User), User.institution_id, institution_id).order_by(User.name)).scalars().all()
    institutions = {
        item.id: item.name
        for item in db.session.execute(select(Institution)).scalars()
    }
    role_rows = db.session.execute(
        select(UserRole.user_id, Role.code, Role.description)
        .join(Role, Role.id == UserRole.role_id)
    ).all()
    roles_by_user = {}
    for user_id, code, description in role_rows:
        roles_by_user.setdefault(user_id, []).append({'code': code, 'label': role_name(code, description)})
    values = []
    for user in rows:
        assigned_roles = roles_by_user.get(user.id, [])
        status_label, status_variant = _status(user.active)
        values.append({
            'id': str(user.id),
            'name': user.name,
            'email': user.email,
            'institution': institutions.get(user.institution_id, _('Unknown institution')),
            'roles': assigned_roles,
            'role_codes': [item['code'] for item in assigned_roles],
            'active': user.active,
            'status_label': status_label,
            'status_variant': status_variant,
            'edit_url': f'/admin/users/{user.id}/edit',
        })
    return values


def users_data(institution_id=None):
    return _read(lambda: _users(institution_id), [])


def roles_data(institution_id=None):
    def query():
        rows = db.session.execute(_scoped(select(Role), Role.institution_id, institution_id).order_by(Role.code)).scalars().all()
        counts = dict(db.session.execute(
            select(UserRole.role_id, func.count(UserRole.user_id)).group_by(UserRole.role_id)
        ).all())
        institutions = {
            item.id: item.name
            for item in db.session.execute(select(Institution)).scalars()
        }
        return [{
            'id': str(role.id),
            'code': role.code,
            'name': role_name(role.code, role.description),
            'description': label('role_description', role.code, role.description),
            'institution': institutions.get(role.institution_id, _('Unknown institution')),
            'assigned_users': counts.get(role.id, 0),
            'edit_url': f'/admin/roles/{role.id}/edit',
        } for role in rows]
    return _read(query, [])


def families_data():
    def query():
        rows = db.session.execute(
            select(InstrumentFamily).order_by(InstrumentFamily.code)
        ).scalars().all()
        categories = {
            item.id: item
            for item in db.session.execute(select(CatInstrumentCategory)).scalars()
        }
        return [{
            'id': str(item.id),
            'code': item.code,
            'name': _family_label(item),
            'category_code': getattr(categories.get(item.category_id), 'code', ''),
            'category': (label('instrument_category', categories[item.category_id].code, categories[item.category_id].name)
                         if item.category_id in categories else _('Uncategorized')),
            'function': label('instrument_family_function', item.code, item.function_text or ''),
            'active': item.active,
            'status_label': _status(item.active)[0],
            'status_variant': _status(item.active)[1],
            'edit_url': f'/admin/instrument-families/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


def instruments_data(institution_id=None):
    def query():
        families = {
            item.id: item
            for item in db.session.execute(select(InstrumentFamily)).scalars()
        }
        statuses = {
            item.id: item
            for item in db.session.execute(select(CatInstrumentCycleStatus)).scalars()
        }
        rows = db.session.execute(
            _scoped(select(Instrument), Instrument.institution_id, institution_id).order_by(Instrument.internal_code)
        ).scalars().all()
        return [{
            'id': str(item.id),
            'internal_code': item.internal_code or str(item.id)[:8].upper(),
            'instrument_family_name': _label(_family_label(families.get(item.family_id))),
            'cycle_status_code': getattr(statuses.get(item.cycle_status_id), 'code', ''),
            'cycle_status_label': _label(label('instrument_cycle_status', statuses[item.cycle_status_id].code,
                                               statuses[item.cycle_status_id].name) if item.cycle_status_id in statuses else None),
            'cycle_status_variant': _variant(getattr(statuses.get(item.cycle_status_id), 'code', '')),
            'active': item.active,
            'active_status_label': _status(item.active)[0],
            'active_status_variant': _status(item.active)[1],
            'edit_url': f'/admin/instruments/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


def kits_data(institution_id=None):
    def query():
        rows = db.session.execute(
            _scoped(select(Kit), Kit.institution_id, institution_id).order_by(Kit.name, Kit.version)
        ).scalars().all()
        item_rows = db.session.execute(select(KitItem)).scalars().all()
        totals = Counter()
        families = Counter()
        for item in item_rows:
            totals[item.kit_id] += item.quantity
            families[item.kit_id] += 1
        return [{
            'id': str(item.id),
            'name': item.name,
            'version': item.version,
            'instrument_family_count': families[item.id],
            'total_expected_instruments': totals[item.id],
            'active': item.active,
            'status_label': _status(item.active)[0],
            'status_variant': _status(item.active)[1],
            'edit_url': f'/admin/kits/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


def stations_data(institution_id=None):
    def query():
        rooms = {
            item.id: item.code
            for item in db.session.execute(_scoped(select(OperatingRoom), OperatingRoom.institution_id, institution_id)).scalars()
        }
        rows = db.session.execute(
            _scoped(select(CaptureStation).join(OperatingRoom, OperatingRoom.id == CaptureStation.room_id),
                    OperatingRoom.institution_id, institution_id).order_by(CaptureStation.name)
        ).scalars().all()
        return [{
            'id': str(item.id),
            'name': item.name,
            'operating_room': rooms.get(item.room_id, _('Unassigned')),
            'active': item.active,
            'status_label': _status(item.active)[0],
            'status_variant': _status(item.active)[1],
            'edit_url': f'/admin/configuration/capture-stations/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


def operating_rooms_data(institution_id=None):
    return _read(lambda: [{
        'id': str(item.id),
        'code': item.code,
        'name': item.name,
        'active': item.active,
        'status_label': _status(item.active)[0],
        'status_variant': _status(item.active)[1],
        'edit_url': f'/admin/configuration/operating-rooms/{item.id}/edit',
    } for item in db.session.execute(
        _scoped(select(OperatingRoom), OperatingRoom.institution_id, institution_id).order_by(OperatingRoom.code)
    ).scalars()], [])


def _options(rows, value='id', label='name'):
    return [{'value': str(getattr(row, value)), 'label': getattr(row, label)} for row in rows]


def _selectable(model, current_ids=(), order_by=None, *filters):
    """Active rows plus the ones already referenced (so editing never drops a selection)."""
    condition = model.active.is_(True)
    ids = [item for item in current_ids if item]
    if ids:
        condition = condition | model.id.in_(ids)
    return db.session.execute(select(model).where(condition, *filters).order_by(order_by if order_by is not None else model.name)).scalars().all()


def family_form_data(family_id=None):
    def query():
        family = db.session.get(InstrumentFamily, family_id) if family_id else None
        return {
            'instrument_family': {
                'code': family.code if family else '',
                'name': family.name if family else '',
                # how a seeded family is shown in the current UI language (the input keeps the stored value)
                'display_name': _family_label(family) if family and is_localized('instrument_family', family.code, family.name) else '',
                'category': str(family.category_id) if family else '',
                'how_to_identify': (family.identify_text or '') if family else '',
                'classification_characteristics': (family.classify_text or '') if family else '',
                'function': (family.function_text or '') if family else '',
                'status': 'active' if not family or family.active else 'inactive',
            },
            'categories': _catalog_options('instrument_category', db.session.execute(select(CatInstrumentCategory)).scalars().all()),
        }
    return _read(query, {'instrument_family': {}, 'categories': []})


def instrument_form_data(institution_id=None, instrument_id=None):
    def query():
        instrument = db.session.get(Instrument, instrument_id) if instrument_id else None
        families = _selectable(InstrumentFamily, [instrument.family_id] if instrument else [])
        statuses = db.session.execute(select(CatInstrumentCycleStatus).order_by(CatInstrumentCycleStatus.name)).scalars().all()
        default_status = next((item for item in statuses if item.code == 'available'), statuses[0] if statuses else None)
        return {
            'instrument': {
                'internal_code': (instrument.internal_code or '') if instrument else '',
                'instrument_family': str(instrument.family_id) if instrument else '',
                'cycle_status': str(instrument.cycle_status_id) if instrument else (str(default_status.id) if default_status else ''),
                'active_status': 'active' if not instrument or instrument.active else 'inactive',
            },
            'instrument_families': _family_options(families),
            'cycle_statuses': _catalog_options('instrument_cycle_status', statuses),
        }
    return _read(query, {'instrument': {}, 'instrument_families': [], 'cycle_statuses': []})


def kit_form_data(institution_id=None, kit_id=None):
    def query():
        kit = db.session.get(Kit, kit_id) if kit_id else None
        items = db.session.execute(select(KitItem).where(KitItem.kit_id == kit.id)).scalars().all() if kit else []
        families = _selectable(InstrumentFamily, [item.family_id for item in items])
        names = {family.id: _family_label(family) for family in families}
        return {
            'kit': {
                'name': kit.name if kit else '',
                'version': kit.version if kit else 1,
                'status': 'active' if not kit or kit.active else 'inactive',
            },
            'instrument_families': _family_options(families),
            'kit_composition': [{
                'instrument_family_value': str(item.family_id),
                'instrument_family_label': names.get(item.family_id, ''),
                'expected_quantity': item.quantity,
            } for item in items],
        }
    return _read(query, {'kit': {}, 'instrument_families': [], 'kit_composition': []})


def _institution_kit_ids(institution_id):
    return {item for item in db.session.execute(_scoped(select(Kit.id), Kit.institution_id, institution_id)).scalars()}


def procedures_data(institution_id=None):
    def query():
        rows = db.session.execute(select(CatProcedureType).order_by(CatProcedureType.code)).scalars().all()
        kit_ids = _institution_kit_ids(institution_id)
        kit_names = {item.id: item.name for item in db.session.execute(select(Kit)).scalars()}
        kits_by_procedure = {}
        default_kit_by_procedure = {}
        for pk in db.session.execute(select(ProcedureKit).where(ProcedureKit.active.is_(True))).scalars():
            if pk.kit_id not in kit_ids:
                continue
            kits_by_procedure.setdefault(pk.procedure_type_id, []).append(pk)
            if pk.is_default:
                default_kit_by_procedure[pk.procedure_type_id] = kit_names.get(pk.kit_id, '')
        phases_by_procedure = {}
        phase_names = {item.id: label('operation_phase', item.code, item.name)
                       for item in db.session.execute(select(CatOperationPhase)).scalars()}
        for pp in db.session.execute(
            select(ProcedurePhase).where(ProcedurePhase.active.is_(True)).order_by(ProcedurePhase.sort_order)
        ).scalars():
            phases_by_procedure.setdefault(pp.procedure_type_id, []).append(phase_names.get(pp.phase_id, ''))
        return [{
            'id': str(item.id),
            'code': item.code,
            'name': label('procedure_type', item.code, item.name),
            'associated_kits_count': len(kits_by_procedure.get(item.id, [])),
            'default_kit_name': default_kit_by_procedure.get(item.id, _('Not set')),
            'counting_phases_label': ', '.join(phases_by_procedure.get(item.id, [])) or _('Not configured'),
            'edit_url': f'/admin/procedures/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


def procedure_form_data(institution_id=None, procedure_id=None):
    def query():
        procedure = db.session.get(CatProcedureType, procedure_id) if procedure_id else None
        kit_links = db.session.execute(
            _scoped(select(ProcedureKit).join(Kit, Kit.id == ProcedureKit.kit_id), Kit.institution_id, institution_id)
            .where(ProcedureKit.procedure_type_id == procedure.id)
        ).scalars().all() if procedure else []
        phase_links = db.session.execute(
            select(ProcedurePhase).where(ProcedurePhase.procedure_type_id == procedure.id).order_by(ProcedurePhase.sort_order)
        ).scalars().all() if procedure else []
        kits = _selectable(Kit, [pk.kit_id for pk in kit_links], None,
                           *([Kit.institution_id == institution_id] if institution_id else []))
        phases = _selectable(CatOperationPhase, [pp.phase_id for pp in phase_links])
        kit_names = {kit.id: kit.name for kit in kits}
        phase_names = {phase.id: label('operation_phase', phase.code, phase.name) for phase in phases}
        return {
            'procedure': {
                'code': procedure.code if procedure else '',
                'name': procedure.name if procedure else '',
                'display_name': (label('procedure_type', procedure.code, procedure.name)
                                 if procedure and is_localized('procedure_type', procedure.code, procedure.name) else ''),
            },
            'kits': _options(kits),
            'phases': _catalog_options('operation_phase', phases),
            'associated_kits': [{
                'kit_value': str(pk.kit_id),
                'kit_label': kit_names.get(pk.kit_id, _('Unknown kit')),
                'technique_label': pk.technique_label or '',
                'is_default': pk.is_default,
                'active': pk.active,
            } for pk in kit_links],
            'counting_phases': [{
                'phase_value': str(pp.phase_id),
                'phase_label': phase_names.get(pp.phase_id, _('Unknown phase')),
                'is_count_required': pp.is_count_required,
                'sort_order': pp.sort_order,
                'active': pp.active,
            } for pp in phase_links],
        }
    return _read(query, {'procedure': {}, 'kits': [], 'phases': [], 'associated_kits': [], 'counting_phases': []})


def _checksum_short(checksum):
    if not checksum:
        return ''
    return f'{checksum[:4]}...{checksum[-3:]}' if len(checksum) > 8 else checksum


def vision_models_data():
    def query():
        rows = db.session.execute(select(YoloModel).order_by(YoloModel.published_at.desc())).scalars().all()
        class_counts = dict(db.session.execute(
            select(ModelClass.model_id, func.count(ModelClass.id)).group_by(ModelClass.model_id)
        ).all())
        return [{
            'id': str(item.id),
            'version_tag': item.version_tag,
            'active': item.active,
            'checksum': item.checksum or '',
            'classes_label': ngettext('%(num)d Class', '%(num)d Classes', class_counts.get(item.id, 0)),
            'status_label': _('Active Model') if item.active else _('Inactive'),
            'status_variant': 'info' if item.active else 'danger',
            'published_at': item.published_at.strftime('%Y-%m-%d') if item.published_at else '',
            'checksum_short': _checksum_short(item.checksum),
            'edit_url': f'/admin/vision-models/{item.id}/edit',
            'activate_url': f'/admin/vision-models/{item.id}/activate',
        } for item in rows]
    return _read(query, [])


def vision_model_form_data(model_id=None):
    def query():
        model = db.session.get(YoloModel, model_id) if model_id else None
        mappings = db.session.execute(
            select(ModelClass).where(ModelClass.model_id == model.id).order_by(ModelClass.yolo_class_id)
        ).scalars().all() if model else []
        families = _selectable(InstrumentFamily, [mc.family_id for mc in mappings])
        names = {family.id: _family_label(family) for family in families}
        media_asset = db.session.get(MediaAsset, model.media_asset_id) if model and model.media_asset_id else None
        return {
            'vision_model': {
                'version_tag': model.version_tag if model else '',
                'model_asset_reference': media_asset.gcs_uri if media_asset else '',
                'active': model.active if model else False,
                'checksum': (model.checksum or '') if model else '',
                'published_at': model.published_at.strftime('%Y-%m-%d') if model and model.published_at else '',
            },
            'instrument_families': _family_options(families),
            'model_classes': [{
                'yolo_class_id': mc.yolo_class_id,
                'instrument_family_value': str(mc.family_id),
                'instrument_family_label': names.get(mc.family_id, _('Unknown family')),
            } for mc in mappings],
        }
    return _read(query, {'vision_model': {}, 'instrument_families': [], 'model_classes': []})


def user_form_data(institution_id=None, user_id=None):
    def query():
        user = db.session.get(User, user_id) if user_id else None
        institutions = db.session.execute(
            _scoped(select(Institution), Institution.id, institution_id).order_by(Institution.name)).scalars().all()
        roles = db.session.execute(
            _scoped(select(Role), Role.institution_id, institution_id).order_by(Role.code)).scalars().all()
        assigned_role_codes = []
        role_label = ''
        if user:
            assigned = db.session.execute(
                select(Role.code, Role.description)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == user.id)
            ).all()
            assigned_role_codes = [code for code, __ in assigned]
            role_label = ' / '.join(role_name(code, description) for code, description in assigned)
        return {
            'user': {
                'name': user.name if user else '',
                'email': user.email if user else '',
                'institution': str(user.institution_id) if user else (str(institution_id) if institution_id else ''),
                'assigned_role_codes': assigned_role_codes,
                'role_label': role_label,
                'status': 'active' if not user or user.active else 'inactive',
            },
            'institutions': _options(institutions),
            'roles': [{'value': role.code, 'label': role_name(role.code, role.description)} for role in roles],
        }
    return _read(query, {'user': {}, 'institutions': [], 'roles': []})


def role_form_data(institution_id=None, role_id=None):
    def query():
        role = db.session.get(Role, role_id) if role_id else None
        assigned = db.session.scalar(select(func.count(UserRole.user_id)).where(UserRole.role_id == role.id)) if role else 0
        institution = db.session.get(Institution, role.institution_id if role else institution_id) if (role or institution_id) else None
        current_codes = [] if role else list(db.session.execute(
            _scoped(select(Role.code), Role.institution_id, institution_id).order_by(Role.code)).scalars())
        return {'role': {
            'code': role.code if role else '',
            'description': role.description if role else '',
            'institution': institution.name if institution else '',
            'assigned_users': assigned,
        }, 'current_role_codes': current_codes}
    return _read(query, {'role': {}, 'current_role_codes': []})


def institution_form_data(institution_id=None):
    def query():
        institution = db.session.get(Institution, institution_id) if institution_id else None
        status_label, status_variant = _status(institution.active if institution else False)
        return {'institution': {
            'name': institution.name if institution else '',
            'code': str(institution.id)[:8].upper() if institution else '',
            'status': 'active' if institution and institution.active else 'inactive',
            'status_label': status_label,
            'status_variant': status_variant,
        }}
    return _read(query, {'institution': {}})


def station_form_data(institution_id=None, station_id=None):
    def query():
        station = db.session.get(CaptureStation, station_id) if station_id else None
        rooms = _selectable(OperatingRoom, [station.room_id] if station else [], OperatingRoom.code,
                            *([OperatingRoom.institution_id == institution_id] if institution_id else []))
        return {
            'capture_station': {
                'name': station.name if station else '',
                'operating_room': str(station.room_id) if station else '',
                'status': 'active' if not station or station.active else 'inactive',
                'roi_configured': bool(station and station.roi),
            },
            'operating_rooms': _options(rooms, value='id', label='code'),
        }
    return _read(query, {'capture_station': {}, 'operating_rooms': []})


def operating_room_form_data(room_id=None):
    def query():
        room = db.session.get(OperatingRoom, room_id) if room_id else None
        return {'operating_room': {
            'code': room.code if room else '',
            'name': room.name if room else '',
            'status': 'active' if not room or room.active else 'inactive',
        }}
    return _read(query, {'operating_room': {}})


def _scoped_sessions(institution_id=None):
    """WorkSession rows whose operator belongs to institution_id (None = no filter)."""
    statement = select(WorkSession).join(User, User.id == WorkSession.user_id)
    return _scoped(statement, User.institution_id, institution_id)


def _session_rows(institution_id=None, operator_links=False):
    rows = db.session.execute(_scoped_sessions(institution_id).order_by(WorkSession.started_at.desc())).scalars().all()
    discrepancy_counts = dict(db.session.execute(
        select(Discrepancy.session_id, func.count(Discrepancy.id))
        .group_by(Discrepancy.session_id)
    ).all())
    statuses = {item.id: item for item in db.session.execute(select(CatSessionStatus)).scalars()}
    operations = {item.id: item for item in db.session.execute(select(Operation)).scalars()}
    users = {item.id: item.name for item in db.session.execute(select(User)).scalars()}
    kits = {item.id: item.name for item in db.session.execute(select(Kit)).scalars()}
    stations = {item.id: item for item in db.session.execute(select(CaptureStation)).scalars()}
    procedures = {item.id: item for item in db.session.execute(select(CatProcedureType)).scalars()}
    rooms = {item.id: item.code for item in db.session.execute(select(OperatingRoom)).scalars()}
    values = []
    for item in rows:
        operation = operations.get(item.operation_id)
        station = stations.get(item.station_id)
        status_row = statuses.get(item.status_id)
        status_code = getattr(status_row, 'code', '')
        status = label('session_status', status_code, getattr(status_row, 'name', None)) if status_row else _('Unknown')
        discrepancy_count = discrepancy_counts.get(item.id, 0)
        if discrepancy_count:
            status = _('Closed with Discrepancy') if item.ended_at else _('With Discrepancy')
        procedure = procedures.get(getattr(operation, 'procedure_type_id', None))
        procedure_label = label('procedure_type', procedure.code, procedure.name) if procedure else _('Unspecified procedure')
        room_id = getattr(station, 'room_id', None) or getattr(operation, 'room_id', None)
        if operator_links:
            action_url = (f'/operator/sessions/closed/{item.id}' if item.ended_at or status_code == 'closed'
                          else f'/operator/sessions/{item.id}/awaiting-review' if status_code == 'validating'
                          else f'/operator/sessions/{item.id}/capture')
        else:
            action_url = f'/supervisor/sessions/{item.id}'
        values.append({
            'id': str(item.id),
            'session_id': str(item.id)[:8].upper(),
            'procedure_code': getattr(procedure, 'code', ''),
            'procedure_name': procedure_label,
            'procedure_label': procedure_label,
            'operating_room': rooms.get(room_id, _('Unspecified room')),
            'kit_name': kits.get(item.kit_id, _('Unspecified kit')),
            'operator_name': users.get(item.user_id, _('Unknown operator')),
            'capture_station_name': getattr(station, 'name', None) or _('Unspecified station'),
            'started_at': item.started_at.strftime('%Y-%m-%d %H:%M') if item.started_at else '',
            'closed_at': item.ended_at.strftime('%Y-%m-%d %H:%M') if item.ended_at else '',
            'submitted_at': item.updated_at.strftime('%Y-%m-%d %H:%M') if getattr(item, 'updated_at', None) else '',
            'status_code': status_code,
            'status_label': status,
            'status_variant': 'warning' if discrepancy_count else _variant(status_code),
            'discrepancy_count': discrepancy_count,
            'action_label': _('View'),
            'action_url': action_url,
        })
    return values


def sessions_data(institution_id=None, operator_links=False):
    return _read(lambda: _session_rows(institution_id, operator_links), [])


def discrepancies_data(institution_id=None):
    def query():
        query = select(Discrepancy).where(Discrepancy.session_id.in_(
            _scoped_sessions(institution_id).with_only_columns(WorkSession.id)))
        if hasattr(Discrepancy, 'updated_at'):
            query = query.order_by(Discrepancy.updated_at.desc())
        rows = db.session.execute(query).scalars().all()
        families = {item.id: _family_label(item) for item in db.session.execute(select(InstrumentFamily)).scalars()}
        sessions = {item['id']: item for item in _session_rows(institution_id)}
        reasons = {item.id: item for item in db.session.execute(select(CatDiscrepancyReason)).scalars()}
        values = []
        for item in rows:
            session_row = sessions.get(str(item.session_id), {})
            values.append({
                'session_id': session_row.get('session_id', str(item.session_id)[:8].upper()),
                'procedure_name': session_row.get('procedure_name', _('Unspecified procedure')),
                'operating_room': session_row.get('operating_room', _('Unspecified room')),
                'kit_name': session_row.get('kit_name', _('Unspecified kit')),
                'operator_name': session_row.get('operator_name', _('Unknown operator')),
                'instrument_name': families.get(item.family_id, _('Unspecified instrument')),
                'expected_quantity': item.expected_quantity or 0,
                'counted_quantity': item.detected_quantity or 0,
                'difference': (item.detected_quantity or 0) - (item.expected_quantity or 0),
                'reason_code': getattr(reasons.get(item.reason_id), 'code', ''),
                'reason': (label('discrepancy_reason', reasons[item.reason_id].code, reasons[item.reason_id].name)
                           if item.reason_id in reasons else _('Recorded discrepancy')),
                'resolved': item.resolved,
                'status_label': _('Resolved') if item.resolved else _('Open'),
                'status_variant': 'success' if item.resolved else 'warning',
                'action_label': _('Review'),
                'action_url': f'/supervisor/discrepancies/{item.session_id}/review',
            })
        return values
    return _read(query, [])


def session_discrepancies(session_id):
    return _read(
        lambda: [
            item for item in discrepancies_data()
            if item['session_id'] == str(session_id)[:8].upper()
            or item.get('session_id') == str(session_id)
        ],
        [],
    )


def discrepancies_summary_data(discrepancies):
    if _demo_mode():
        # See dashboard_data(): an explicit zero-value dict would defeat the template's
        # `discrepancies_summary|default({...}, true)` fallback.
        return {}
    return {
        'pending_reviews': sum(not item['resolved'] for item in discrepancies),
        'open_discrepancies': sum(not item['resolved'] for item in discrepancies),
        'reviewed_today': sum(item['resolved'] for item in discrepancies),
    }


def dashboard_data(role, institution_id=None):
    sessions = sessions_data(institution_id, operator_links=role == 'operator')
    discrepancies = discrepancies_data(institution_id)
    if _demo_mode():
        # An explicit zero-value stats dict is still "defined" to Jinja and would defeat the
        # template's `dashboard_stats|default({...}, true)` fallback, so hand back {} instead.
        return {}, sessions, discrepancies
    if role == 'operator':
        stats = {
            'sessions_today': len(sessions),
            'active_sessions': sum(item.get('status_code') != 'closed' for item in sessions),
            'closed_sessions': sum(item.get('status_code') == 'closed' for item in sessions),
            'open_discrepancies': sum(not item['resolved'] for item in discrepancies),
        }
    elif role == 'supervisor':
        stats = {
            'sessions_today': len(sessions),
            'active_sessions': sum(item.get('status_code') != 'closed' for item in sessions),
            'pending_reviews': sum(not item['resolved'] for item in discrepancies),
            'open_discrepancies': sum(not item['resolved'] for item in discrepancies),
        }
    else:
        stats = {
            'active_users': len([item for item in users_data(institution_id) if item['active']]),
            'instrument_families': len(families_data()),  # global catalog (no institution_id)
            'registered_instruments': len(instruments_data(institution_id)),
            'active_kits': len([item for item in kits_data(institution_id) if item['active']]),
        }
    return stats, sessions, discrepancies


def admin_dashboard_overview(institution_id=None):
    """
    Secondary KPI cards, Catalog/System Overview panels, and Operational Metrics for the
    Administrator Dashboard, all from real rows; AI-Human agreement / resolution time come from
    services/analytics_service (see its definitions). No presentation fallback values are used.
    """
    def query():
        procedures_count = db.session.scalar(select(func.count(CatProcedureType.id)))
        stations_count = db.session.scalar(_scoped(
            select(func.count(CaptureStation.id)).join(OperatingRoom, OperatingRoom.id == CaptureStation.room_id),
            OperatingRoom.institution_id, institution_id))
        vision_models_count = db.session.scalar(select(func.count(YoloModel.id)))
        active_vision_model = db.session.scalar(select(YoloModel).where(YoloModel.active.is_(True)))
        active_vision_models_count = 1 if active_vision_model else 0
        inactive_users_count = db.session.scalar(
            _scoped(select(func.count(User.id)), User.institution_id, institution_id).where(User.active.is_(False)))
        roles_count = db.session.scalar(_scoped(select(func.count(Role.id)), Role.institution_id, institution_id))
        operating_rooms_count = db.session.scalar(
            _scoped(select(func.count(OperatingRoom.id)), OperatingRoom.institution_id, institution_id))
        active_institutions_count = db.session.scalar(
            _scoped(select(func.count(Institution.id)), Institution.id, institution_id).where(Institution.active.is_(True)))
        model_classes_count = (
            db.session.scalar(select(func.count(ModelClass.id)).where(ModelClass.model_id == active_vision_model.id))
            if active_vision_model else 0
        )
        human_corrections_count = db.session.scalar(
            select(func.count(HumanCorrection.id)).join(CountEvent, CountEvent.id == HumanCorrection.count_event_id)
            .where(CountEvent.session_id.in_(_scoped_sessions(institution_id).with_only_columns(WorkSession.id))))
        facts = analytics.indicators(institution_id) if institution_id else None
        total_users = db.session.scalar(_scoped(select(func.count(User.id)), User.institution_id, institution_id))

        return {
            'secondary_stats': {
                'procedures': procedures_count or 0,
                'capture_stations': stations_count or 0,
                'vision_models': vision_models_count or 0,
                'active_vision_model': active_vision_models_count,
            },
            'catalog_overview': [
                # instrument families, procedures and vision models are global catalogs (no institution_id)
                {'label': _('Instrument Families (global)'), 'value': _('%(count)s Active', count=len([f for f in families_data() if f['active']]))},
                {'label': _('Instruments'), 'value': _('%(count)s Registered', count=len(instruments_data(institution_id)))},
                {'label': _('Kits'), 'value': _('%(count)s Active', count=len([item for item in kits_data(institution_id) if item['active']]))},
                {'label': _('Procedures (global)'), 'value': _('%(count)s Configured', count=procedures_count or 0)},
                {'label': _('Capture Stations'), 'value': _('%(count)s Configured', count=stations_count or 0)},
            ],
            'system_overview': [
                {'label': _('Users'), 'value': total_users or 0},
                {'label': _('Inactive Users'), 'value': inactive_users_count or 0},
                {'label': _('Configured Roles'), 'value': roles_count or 0},
                {'label': _('Operating Rooms'), 'value': operating_rooms_count or 0},
                {'label': _('Capture Stations'), 'value': stations_count or 0},
                {'label': _('Active Institution'), 'value': active_institutions_count or 0},
                {'label': _('Active Vision Model Version (global)'), 'value': active_vision_model.version_tag if active_vision_model else _('None')},
                {'label': _('Model Classes'), 'value': model_classes_count or 0},
            ],
            'operational_metrics': [
                {'label': _('Total Sessions'), 'value': facts['sessions']['total'] if facts else 0},
                {'label': _('Sessions Open / Counting / Validating / Closed'), 'value': (
                    f"{facts['sessions']['open']} / {facts['sessions']['counting']} / "
                    f"{facts['sessions']['validating']} / {facts['sessions']['closed']}") if facts else '0 / 0 / 0 / 0'},
                {'label': _('Total Discrepancies'), 'value': facts['discrepancies']['total'] if facts else 0},
                {'label': _('Unresolved / Resolved Discrepancies'), 'value': (
                    f"{facts['discrepancies']['unresolved']} / {facts['discrepancies']['resolved']}") if facts else '0 / 0'},
                {'label': _('AI-Human Agreement'), 'value': facts['agreement']['label'] if facts else _('N/A')},
                {'label': _('Human Corrections'), 'value': human_corrections_count or 0},
                {'label': _('Average Resolution Time'), 'value': facts['resolution']['label'] if facts else _('N/A')},
            ],
        }
    return _read(query, {
        'secondary_stats': {}, 'catalog_overview': [], 'system_overview': [], 'operational_metrics': [],
    })
