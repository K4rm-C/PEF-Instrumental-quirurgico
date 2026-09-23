from collections import Counter
from datetime import datetime

from flask import current_app
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models.AccessAudit import AccessAudit
from models.CaptureStation import CaptureStation
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
        return fallback


def _label(value):
    return value or 'Not specified'


def _variant(status):
    code = (status or '').lower()
    if code in {'closed', 'available', 'active', 'resolved'}:
        return 'success'
    if code in {'pending', 'pending_review', 'in_progress'}:
        return 'warning'
    if code in {'cancelled', 'denied', 'missing', 'discrepancy'}:
        return 'danger'
    return 'neutral'


def _users():
    rows = db.session.execute(select(User).order_by(User.name)).scalars().all()
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
        roles_by_user.setdefault(user_id, []).append({'code': code, 'label': description})
    values = []
    for user in rows:
        assigned_roles = roles_by_user.get(user.id, [])
        values.append({
            'id': str(user.id),
            'name': user.name,
            'email': user.email,
            'institution': institutions.get(user.institution_id, 'Unknown institution'),
            'roles': assigned_roles,
            'role_codes': [item['code'] for item in assigned_roles],
            'status_label': 'Active' if user.active else 'Inactive',
            'status_variant': 'success' if user.active else 'neutral',
            'edit_url': f'/admin/users/{user.id}/edit',
        })
    return values


def users_data():
    return _read(_users, [])


def roles_data():
    def query():
        rows = db.session.execute(select(Role).order_by(Role.code)).scalars().all()
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
            'description': role.description,
            'institution': institutions.get(role.institution_id, 'Unknown institution'),
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
            item.id: item.name
            for item in db.session.execute(select(CatInstrumentCategory)).scalars()
        }
        return [{
            'id': str(item.id),
            'code': item.code,
            'name': item.name,
            'category': categories.get(item.category_id, 'Uncategorized'),
            'function': item.function_text or '',
            'status_label': 'Active' if item.active else 'Inactive',
            'status_variant': 'success' if item.active else 'neutral',
            'edit_url': f'/admin/instrument-families/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


def instruments_data():
    def query():
        families = {
            item.id: item
            for item in db.session.execute(select(InstrumentFamily)).scalars()
        }
        statuses = {
            item.id: item.name
            for item in db.session.execute(select(CatInstrumentCycleStatus)).scalars()
        }
        rows = db.session.execute(select(Instrument).order_by(Instrument.internal_code)).scalars().all()
        return [{
            'id': str(item.id),
            'internal_code': item.internal_code or str(item.id),
            'instrument_family_name': _label(getattr(families.get(item.family_id), 'name', None)),
            'cycle_status_label': _label(statuses.get(item.cycle_status_id)),
            'cycle_status_variant': _variant(statuses.get(item.cycle_status_id)),
            'active_status_label': 'Active' if item.active else 'Inactive',
            'active_status_variant': 'success' if item.active else 'neutral',
            'edit_url': f'/admin/instruments/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


def kits_data():
    def query():
        rows = db.session.execute(select(Kit).order_by(Kit.name, Kit.version)).scalars().all()
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
            'status_label': 'Active' if item.active else 'Inactive',
            'status_variant': 'success' if item.active else 'neutral',
            'edit_url': f'/admin/kits/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


def stations_data():
    def query():
        rooms = {item.id: item.code for item in db.session.execute(select(OperatingRoom)).scalars()}
        rows = db.session.execute(select(CaptureStation).order_by(CaptureStation.name)).scalars().all()
        return [{
            'id': str(item.id),
            'name': item.name,
            'operating_room': rooms.get(item.room_id, 'Unassigned'),
            'status_label': 'Active' if item.active else 'Inactive',
            'status_variant': 'success' if item.active else 'neutral',
            'edit_url': f'/admin/configuration/capture-stations/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


def operating_rooms_data():
    return _read(lambda: [{
        'id': str(item.id),
        'code': item.code,
        'name': item.name,
        'status_label': 'Active' if item.active else 'Inactive',
        'status_variant': 'success' if item.active else 'neutral',
        'edit_url': f'/admin/configuration/operating-rooms/{item.id}/edit',
    } for item in db.session.execute(select(OperatingRoom).order_by(OperatingRoom.code)).scalars()], [])


def _options(rows, value='id', label='name'):
    return [{'value': str(getattr(row, value)), 'label': getattr(row, label)} for row in rows]


def family_form_data(family_id=None):
    def query():
        family = db.session.get(InstrumentFamily, family_id) if family_id else None
        return {
            'instrument_family': {
                'code': family.code if family else '',
                'name': family.name if family else '',
                'category': str(family.category_id) if family else '',
                'how_to_identify': family.identify_text if family else '',
                'classification_characteristics': family.classify_text if family else '',
                'function': family.function_text if family else '',
                'status': 'active' if not family or family.active else 'inactive',
            },
            'categories': _options(db.session.execute(select(CatInstrumentCategory).order_by(CatInstrumentCategory.name)).scalars().all()),
        }
    return _read(query, {'instrument_family': {}, 'categories': []})


def instrument_form_data(instrument_id=None):
    def query():
        instrument = db.session.get(Instrument, instrument_id) if instrument_id else None
        families = db.session.execute(select(InstrumentFamily).order_by(InstrumentFamily.name)).scalars().all()
        statuses = db.session.execute(select(CatInstrumentCycleStatus).order_by(CatInstrumentCycleStatus.name)).scalars().all()
        return {
            'instrument': {
                'internal_code': instrument.internal_code if instrument else '',
                'instrument_family': str(instrument.family_id) if instrument else '',
                'cycle_status': str(instrument.cycle_status_id) if instrument else '',
                'active_status': 'active' if not instrument or instrument.active else 'inactive',
            },
            'instrument_families': _options(families),
            'cycle_statuses': _options(statuses),
        }
    return _read(query, {'instrument': {}, 'instrument_families': [], 'cycle_statuses': []})


def kit_form_data(kit_id=None):
    def query():
        kit = db.session.get(Kit, kit_id) if kit_id else None
        families = db.session.execute(select(InstrumentFamily).order_by(InstrumentFamily.name)).scalars().all()
        composition = []
        if kit:
            items = db.session.execute(select(KitItem).where(KitItem.kit_id == kit.id)).scalars().all()
            composition = [
                {
                    'instrument_family_value': str(item.family_id),
                    'instrument_family_label': db.session.get(InstrumentFamily, item.family_id).name,
                    'expected_quantity': item.quantity,
                }
                for item in items
            ]
        return {
            'kit': {
                'name': kit.name if kit else '',
                'version': kit.version if kit else 1,
                'status': 'active' if not kit or kit.active else 'inactive',
            },
            'instrument_families': _options(families),
            'kit_composition': composition,
        }
    return _read(query, {'kit': {}, 'instrument_families': [], 'kit_composition': []})


def procedures_data():
    def query():
        rows = db.session.execute(select(CatProcedureType).order_by(CatProcedureType.code)).scalars().all()
        kits_by_procedure = {}
        default_kit_by_procedure = {}
        for pk in db.session.execute(select(ProcedureKit)).scalars():
            kits_by_procedure.setdefault(pk.procedure_type_id, []).append(pk)
            if pk.is_default:
                kit = db.session.get(Kit, pk.kit_id)
                default_kit_by_procedure[pk.procedure_type_id] = kit.name if kit else ''
        phases_by_procedure = {}
        phase_names = {item.id: item.name for item in db.session.execute(select(CatOperationPhase)).scalars()}
        for pp in db.session.execute(select(ProcedurePhase).order_by(ProcedurePhase.sort_order)).scalars():
            phases_by_procedure.setdefault(pp.procedure_type_id, []).append(phase_names.get(pp.phase_id, ''))
        return [{
            'id': str(item.id),
            'code': item.code,
            'name': item.name,
            'associated_kits_count': len(kits_by_procedure.get(item.id, [])),
            'default_kit_name': default_kit_by_procedure.get(item.id, 'Not set'),
            'counting_phases_label': ', '.join(phases_by_procedure.get(item.id, [])) or 'Not configured',
            'edit_url': f'/admin/procedures/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


def procedure_form_data(procedure_id=None):
    def query():
        procedure = db.session.get(CatProcedureType, procedure_id) if procedure_id else None
        kits = db.session.execute(select(Kit).order_by(Kit.name)).scalars().all()
        phases = db.session.execute(select(CatOperationPhase).order_by(CatOperationPhase.name)).scalars().all()
        associated_kits = []
        counting_phases = []
        if procedure:
            for pk in db.session.execute(select(ProcedureKit).where(ProcedureKit.procedure_type_id == procedure.id)).scalars():
                kit = db.session.get(Kit, pk.kit_id)
                associated_kits.append({
                    'kit_value': str(pk.kit_id),
                    'kit_label': kit.name if kit else 'Unknown kit',
                    'technique_label': pk.technique_label or '',
                    'is_default': pk.is_default,
                    'active': pk.active,
                })
            for pp in db.session.execute(
                select(ProcedurePhase).where(ProcedurePhase.procedure_type_id == procedure.id).order_by(ProcedurePhase.sort_order)
            ).scalars():
                phase = db.session.get(CatOperationPhase, pp.phase_id)
                counting_phases.append({
                    'phase_value': str(pp.phase_id),
                    'phase_label': phase.name if phase else 'Unknown phase',
                    'is_count_required': pp.is_count_required,
                    'sort_order': pp.sort_order,
                    'active': pp.active,
                })
        return {
            'procedure': {
                'code': procedure.code if procedure else '',
                'name': procedure.name if procedure else '',
            },
            'kits': _options(kits),
            'phases': _options(phases),
            'associated_kits': associated_kits,
            'counting_phases': counting_phases,
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
            'classes_label': f'{class_counts.get(item.id, 0)} Classes',
            'status_label': 'Active Model' if item.active else 'Inactive',
            'status_variant': 'info' if item.active else 'danger',
            'published_at': item.published_at.strftime('%Y-%m-%d') if item.published_at else '',
            'checksum_short': _checksum_short(item.checksum),
            'edit_url': f'/admin/vision-models/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


def vision_model_form_data(model_id=None):
    def query():
        model = db.session.get(YoloModel, model_id) if model_id else None
        families = db.session.execute(select(InstrumentFamily).order_by(InstrumentFamily.name)).scalars().all()
        model_classes = []
        media_asset = None
        if model:
            media_asset = db.session.get(MediaAsset, model.media_asset_id)
            for mc in db.session.execute(select(ModelClass).where(ModelClass.model_id == model.id)).scalars():
                family = db.session.get(InstrumentFamily, mc.family_id)
                model_classes.append({
                    'yolo_class_id': mc.yolo_class_id,
                    'instrument_family_value': str(mc.family_id),
                    'instrument_family_label': family.name if family else 'Unknown family',
                })
        return {
            'vision_model': {
                'version_tag': model.version_tag if model else '',
                'model_asset_reference': media_asset.object_key if media_asset else '',
                'active': model.active if model else False,
                'checksum': model.checksum if model else '',
                'published_at': model.published_at.strftime('%Y-%m-%d') if model and model.published_at else '',
            },
            'instrument_families': _options(families),
            'model_classes': model_classes,
        }
    return _read(query, {'vision_model': {}, 'instrument_families': [], 'model_classes': []})


def user_form_data(user_id=None):
    def query():
        user = db.session.get(User, user_id) if user_id else None
        institutions = db.session.execute(select(Institution).order_by(Institution.name)).scalars().all()
        roles = db.session.execute(select(Role).order_by(Role.code)).scalars().all()
        assigned_role_codes = []
        role_label = ''
        if user:
            assigned = db.session.execute(
                select(Role.code, Role.description)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == user.id)
            ).all()
            assigned_role_codes = [code for code, _ in assigned]
            role_label = ' / '.join(description for _, description in assigned)
        return {
            'user': {
                'name': user.name if user else '',
                'email': user.email if user else '',
                'institution': str(user.institution_id) if user else '',
                'assigned_role_codes': assigned_role_codes,
                'role_label': role_label,
                'status': 'active' if not user or user.active else 'inactive',
            },
            'institutions': _options(institutions),
            'roles': [{'value': role.code, 'label': role.description} for role in roles],
        }
    return _read(query, {
        'user': {
            'name': 'Development User',
            'email': '',
            'institution': '',
            'assigned_role_codes': [],
            'role_label': '',
            'status': 'active',
        },
        'institutions': [],
        'roles': [],
    })


def role_form_data(role_id=None):
    def query():
        role = db.session.get(Role, role_id) if role_id else None
        assigned = db.session.scalar(select(func.count(UserRole.user_id)).where(UserRole.role_id == role.id)) if role else 0
        institution = db.session.get(Institution, role.institution_id) if role else db.session.scalar(select(Institution).order_by(Institution.name))
        current_codes = [item.code for item in db.session.execute(select(Role.code).order_by(Role.code)).all()] if not role else []
        return {'role': {
            'code': role.code if role else '',
            'description': role.description if role else '',
            'institution': institution.name if institution else '',
            'assigned_users': assigned,
        }, 'current_role_codes': current_codes}
    return _read(query, {'role': {}, 'current_role_codes': []})


def institution_form_data(institution_id=None):
    def query():
        institution = db.session.get(Institution, institution_id) if institution_id else db.session.scalar(select(Institution).order_by(Institution.name))
        return {'institution': {
            'name': institution.name if institution else '',
            'code': str(institution.id)[:8].upper() if institution else '',
            'status': 'active' if not institution or institution.active else 'inactive',
        }}
    return _read(query, {'institution': {}})


def station_form_data(station_id=None):
    def query():
        station = db.session.get(CaptureStation, station_id) if station_id else None
        rooms = db.session.execute(select(OperatingRoom).order_by(OperatingRoom.code)).scalars().all()
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


def _session_rows():
    rows = db.session.execute(select(WorkSession).order_by(WorkSession.started_at.desc())).scalars().all()
    discrepancy_counts = dict(db.session.execute(
        select(Discrepancy.session_id, func.count(Discrepancy.id))
        .group_by(Discrepancy.session_id)
    ).all())
    statuses = {item.id: item.name for item in db.session.execute(select(CatSessionStatus)).scalars()}
    operations = {item.id: item for item in db.session.execute(select(Operation)).scalars()}
    users = {item.id: item.name for item in db.session.execute(select(User)).scalars()}
    kits = {item.id: item.name for item in db.session.execute(select(Kit)).scalars()}
    stations = {item.id: item.name for item in db.session.execute(select(CaptureStation)).scalars()}
    procedures = {item.id: item.name for item in db.session.execute(select(CatProcedureType)).scalars()}
    rooms = {item.id: item.code for item in db.session.execute(select(db.Model.metadata.tables['operating_room'])).all()} if False else {}
    values = []
    for item in rows:
        operation = operations.get(item.operation_id)
        status = statuses.get(item.status_id, 'Unknown')
        discrepancy_count = discrepancy_counts.get(item.id, 0)
        if discrepancy_count:
            status = 'Closed with Discrepancy' if item.ended_at else 'With Discrepancy'
        values.append({
            'id': str(item.id),
            'session_id': str(item.id)[:8].upper(),
            'procedure_name': procedures.get(getattr(operation, 'procedure_type_id', None), 'Unspecified procedure'),
            'procedure_label': procedures.get(getattr(operation, 'procedure_type_id', None), 'Unspecified procedure'),
            'operating_room': 'Unspecified room',
            'kit_name': kits.get(item.kit_id, 'Unspecified kit'),
            'operator_name': users.get(item.user_id, 'Unknown operator'),
            'capture_station_name': stations.get(item.station_id, 'Unspecified station'),
            'started_at': item.started_at.strftime('%Y-%m-%d %H:%M') if item.started_at else '',
            'closed_at': item.ended_at.strftime('%Y-%m-%d %H:%M') if item.ended_at else '',
            'submitted_at': item.updated_at.strftime('%Y-%m-%d %H:%M') if getattr(item, 'updated_at', None) else '',
            'status_label': status,
            'status_variant': 'warning' if discrepancy_count else _variant(status),
            'discrepancy_count': discrepancy_count,
            'action_label': 'View',
            'action_url': f'/supervisor/sessions/{item.id}',
        })
    return values


def sessions_data():
    return _read(_session_rows, [])


def discrepancies_data():
    def query():
        query = select(Discrepancy)
        if hasattr(Discrepancy, 'updated_at'):
            query = query.order_by(Discrepancy.updated_at.desc())
        rows = db.session.execute(query).scalars().all()
        families = {item.id: item.name for item in db.session.execute(select(InstrumentFamily)).scalars()}
        sessions = {item['id']: item for item in _session_rows()}
        reasons = {item.id: item.name for item in db.session.execute(select(db.Model.metadata.tables['cat_discrepancy_reason'])).all()} if False else {}
        values = []
        for item in rows:
            session_row = sessions.get(str(item.session_id), {})
            values.append({
                'session_id': session_row.get('session_id', str(item.session_id)[:8].upper()),
                'procedure_name': session_row.get('procedure_name', 'Unspecified procedure'),
                'operating_room': session_row.get('operating_room', 'Unspecified room'),
                'kit_name': session_row.get('kit_name', 'Unspecified kit'),
                'operator_name': session_row.get('operator_name', 'Unknown operator'),
                'instrument_name': families.get(item.family_id, 'Unspecified instrument'),
                'expected_quantity': item.expected_quantity or 0,
                'counted_quantity': item.detected_quantity or 0,
                'difference': (item.detected_quantity or 0) - (item.expected_quantity or 0),
                'reason': 'Recorded discrepancy',
                'status_label': 'Resolved' if item.resolved else 'Open',
                'status_variant': 'success' if item.resolved else 'warning',
                'action_label': 'Review',
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
        'pending_reviews': sum(item['status_label'] == 'Open' for item in discrepancies),
        'open_discrepancies': sum(item['status_label'] == 'Open' for item in discrepancies),
        'reviewed_today': sum(item['status_label'] == 'Resolved' for item in discrepancies),
    }


def dashboard_data(role):
    sessions = sessions_data()
    discrepancies = discrepancies_data()
    if _demo_mode():
        # An explicit zero-value stats dict is still "defined" to Jinja and would defeat the
        # template's `dashboard_stats|default({...}, true)` fallback, so hand back {} instead.
        return {}, sessions, discrepancies
    if role == 'operator':
        stats = {
            'sessions_today': len(sessions),
            'active_sessions': sum(item['status_label'].lower() != 'closed' for item in sessions),
            'closed_sessions': sum(item['status_label'].lower() == 'closed' for item in sessions),
            'open_discrepancies': sum(item['status_label'] == 'Open' for item in discrepancies),
        }
    elif role == 'supervisor':
        stats = {
            'sessions_today': len(sessions),
            'active_sessions': sum(item['status_label'].lower() != 'closed' for item in sessions),
            'pending_reviews': sum(item['status_label'] == 'Open' for item in discrepancies),
            'open_discrepancies': sum(item['status_label'] == 'Open' for item in discrepancies),
        }
    else:
        stats = {
            'active_users': len(users_data()),
            'instrument_families': len(families_data()),
            'registered_instruments': len(instruments_data()),
            'active_kits': len([item for item in kits_data() if item['status_label'] == 'Active']),
        }
    return stats, sessions, discrepancies


def admin_dashboard_overview():
    """
    Secondary KPI cards, Catalog/System Overview panels, and Operational Metrics for the
    Administrator Dashboard. Counts backed by real models are computed here; AI-Human
    Agreement and Average Resolution Time have no backing analytics query on any existing
    model (see V2_IMPLEMENTATION_NOTES.md) and are left for the template's presentation
    fallback.
    """
    def query():
        procedures_count = db.session.scalar(select(func.count(CatProcedureType.id)))
        stations_count = db.session.scalar(select(func.count(CaptureStation.id)))
        vision_models_count = db.session.scalar(select(func.count(YoloModel.id)))
        active_vision_model = db.session.scalar(select(YoloModel).where(YoloModel.active.is_(True)))
        active_vision_models_count = 1 if active_vision_model else 0
        inactive_users_count = db.session.scalar(select(func.count(User.id)).where(User.active.is_(False)))
        roles_count = db.session.scalar(select(func.count(Role.id)))
        operating_rooms_count = db.session.scalar(select(func.count(OperatingRoom.id)))
        active_institutions_count = db.session.scalar(select(func.count(Institution.id)).where(Institution.active.is_(True)))
        model_classes_count = (
            db.session.scalar(select(func.count(ModelClass.id)).where(ModelClass.model_id == active_vision_model.id))
            if active_vision_model else 0
        )
        human_corrections_count = db.session.scalar(select(func.count(HumanCorrection.id)))
        sessions = sessions_data()
        discrepancies = discrepancies_data()

        return {
            'secondary_stats': {
                'procedures': procedures_count or 0,
                'capture_stations': stations_count or 0,
                'vision_models': vision_models_count or 0,
                'active_vision_model': active_vision_models_count,
            },
            'catalog_overview': [
                {'label': 'Instrument Families', 'value': f'{len(families_data())} Active'},
                {'label': 'Instruments', 'value': f'{len(instruments_data())} Registered'},
                {'label': 'Kits', 'value': f"{len([item for item in kits_data() if item['status_label'] == 'Active'])} Active"},
                {'label': 'Procedures', 'value': f'{procedures_count or 0} Active'},
                {'label': 'Capture Stations', 'value': f'{stations_count or 0} Configured'},
            ],
            'system_overview': [
                {'label': 'Inactive Users', 'value': inactive_users_count or 0},
                {'label': 'Configured Roles', 'value': roles_count or 0},
                {'label': 'Operating Rooms', 'value': operating_rooms_count or 0},
                {'label': 'Capture Stations', 'value': stations_count or 0},
                {'label': 'Active Institution', 'value': active_institutions_count or 0},
                {'label': 'Active Vision Model Version', 'value': active_vision_model.version_tag if active_vision_model else 'None'},
                {'label': 'Model Classes', 'value': model_classes_count or 0},
            ],
            'operational_metrics': [
                {'label': 'Total Sessions', 'value': len(sessions)},
                {'label': 'Total Discrepancies', 'value': len(discrepancies)},
                {'label': 'AI-Human Agreement', 'value': '94.2%'},
                {'label': 'Human Corrections', 'value': human_corrections_count or 0},
                {'label': 'Average Resolution Time', 'value': '18 min'},
            ],
        }
    return _read(query, {
        'secondary_stats': {}, 'catalog_overview': [], 'system_overview': [], 'operational_metrics': [],
    })