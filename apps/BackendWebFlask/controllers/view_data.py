import os
from collections import Counter
from copy import deepcopy
from datetime import datetime
from uuid import UUID as _UUID

from flask import current_app, url_for
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from controllers import demo_data
from extensions import db
from models.AccessAudit import AccessAudit
from models.CaptureStation import CaptureStation
from models.CatEventType import CatEventType
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
from models.PrivacyNoticeVersion import PrivacyNoticeVersion
from models.Role import Role
from models.SessionProcessingAgreement import SessionProcessingAgreement
from models.ExpectedInventory import ExpectedInventory
from models.User import User
from models.UserRole import UserRole
from models.WorkSession import WorkSession
from models.YoloModel import YoloModel


SESSION_STATUS_UI = {
    'scheduled': ('Scheduled', 'neutral'),
    'in_progress': ('In Progress', 'info'),
    'awaiting_spd_review': ('Awaiting Review', 'warning'),
    'correction_required': ('Correction Required', 'danger'),
    'closed': ('Closed', 'success'),
    'aborted': ('Aborted', 'neutral'),
}


def display_session_id(session_uuid):
    return f"WS-{str(session_uuid).replace('-', '')[:8].upper()}"


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
        status_codes = {item.id: item.code for item in statuses}
        return {
            'instrument': {
                'internal_code': instrument.internal_code if instrument else '',
                'instrument_family': str(instrument.family_id) if instrument else '',
                'cycle_status': status_codes.get(instrument.cycle_status_id, '') if instrument else 'available',
                'active_status': 'active' if not instrument or instrument.active else 'inactive',
            },
            'instrument_families': _options(families),
            'cycle_statuses': _options(statuses, value='code'),
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


def _fmt_dt(value):
    if not value:
        return ''
    if value.tzinfo is None:
        return value.strftime('%Y-%m-%d %H:%M')
    return value.strftime('%Y-%m-%d %H:%M')


def _build_session_row(item, *, status_by_id, operations, users, kits, stations, procedures, rooms,
                       open_discrepancy_counts, agreement_session_ids, for_role='supervisor'):
    operation = operations.get(item.operation_id)
    status = status_by_id.get(item.status_id)
    status_code = status.code if status else 'unknown'
    status_label, status_variant = SESSION_STATUS_UI.get(
        status_code, (status.name if status else 'Unknown', 'neutral')
    )
    open_discrepancies = open_discrepancy_counts.get(item.id, 0)
    has_agreement = item.id in agreement_session_ids
    if item.capture_mode == 'vision' or (item.capture_mode is None and has_agreement):
        privacy_label = 'Notice OK · Vision'
        privacy_variant = 'success'
    else:
        privacy_label = 'No notice · Manual'
        privacy_variant = 'warning'

    room_code = '—'
    if operation and operation.room_id:
        room = rooms.get(operation.room_id)
        room_code = room.code if room else '—'

    scheduled_at = _fmt_dt(getattr(operation, 'scheduled_at', None)) if operation else ''
    created_at = scheduled_at or _fmt_dt(item.started_at) or _fmt_dt(item.updated_at)

    if for_role == 'operator':
        if status_code == 'scheduled':
            action_label, action_url = 'Begin', f'/operator/sessions/{item.id}/begin'
        elif status_code == 'in_progress':
            if item.capture_mode == 'manual_no_privacy':
                action_label, action_url = 'Continue', f'/operator/sessions/{item.id}/manual'
            else:
                action_label, action_url = 'Continue', f'/operator/sessions/{item.id}/capture'
        else:
            action_label, action_url = 'View details', f'/operator/sessions/{item.id}'
        details_url = f'/operator/sessions/{item.id}'
    else:
        if status_code == 'correction_required' or open_discrepancies > 0:
            action_label, action_url = 'Review', f'/supervisor/discrepancies/{item.id}/review'
        elif status_code == 'awaiting_spd_review':
            action_label, action_url = 'Confirm close', f'/supervisor/sessions/{item.id}'
        else:
            action_label, action_url = 'View', f'/supervisor/sessions/{item.id}'
        details_url = f'/supervisor/sessions/{item.id}'

    return {
        'id': str(item.id),
        'session_id': display_session_id(item.id),
        'status_code': status_code,
        'capture_mode': item.capture_mode,
        'has_privacy_agreement': has_agreement,
        'privacy_label': privacy_label,
        'privacy_variant': privacy_variant,
        'procedure_name': procedures.get(getattr(operation, 'procedure_type_id', None), 'Unspecified procedure'),
        'procedure_label': procedures.get(getattr(operation, 'procedure_type_id', None), 'Unspecified procedure'),
        'operating_room': room_code,
        'kit_name': kits.get(item.kit_id, 'Unspecified kit'),
        'operator_name': users.get(item.user_id, 'Unknown operator'),
        'capture_station_name': stations.get(item.station_id, 'Unspecified station'),
        'created_at': created_at,
        'scheduled_at': scheduled_at,
        'started_at': _fmt_dt(item.started_at),
        'closed_at': _fmt_dt(item.ended_at),
        'submitted_at': _fmt_dt(getattr(item, 'updated_at', None)),
        'status_label': status_label,
        'status_variant': status_variant if open_discrepancies == 0 else 'warning',
        'discrepancy_count': open_discrepancies,
        'action_label': action_label,
        'action_url': action_url,
        'details_url': details_url,
    }


def _session_lookup_maps():
    statuses = {item.id: item for item in db.session.execute(select(CatSessionStatus)).scalars()}
    operations = {item.id: item for item in db.session.execute(select(Operation)).scalars()}
    users = {item.id: item.name for item in db.session.execute(select(User)).scalars()}
    kits = {item.id: item.name for item in db.session.execute(select(Kit)).scalars()}
    stations = {item.id: item.name for item in db.session.execute(select(CaptureStation)).scalars()}
    procedures = {item.id: item.name for item in db.session.execute(select(CatProcedureType)).scalars()}
    rooms = {item.id: item for item in db.session.execute(select(OperatingRoom)).scalars()}
    open_discrepancy_counts = dict(db.session.execute(
        select(Discrepancy.session_id, func.count(Discrepancy.id))
        .where(Discrepancy.resolved.is_(False))
        .group_by(Discrepancy.session_id)
    ).all())
    agreement_session_ids = {
        row[0] for row in db.session.execute(select(SessionProcessingAgreement.session_id)).all()
    }
    return statuses, operations, users, kits, stations, procedures, rooms, open_discrepancy_counts, agreement_session_ids


def _session_rows(for_role='supervisor', user_id=None):
    query = select(WorkSession)
    if user_id is not None:
        query = query.where(WorkSession.user_id == user_id)
    query = query.order_by(WorkSession.updated_at.desc())
    rows = db.session.execute(query).scalars().all()
    (
        statuses, operations, users, kits, stations, procedures, rooms,
        open_discrepancy_counts, agreement_session_ids,
    ) = _session_lookup_maps()
    return [
        _build_session_row(
            item,
            status_by_id=statuses,
            operations=operations,
            users=users,
            kits=kits,
            stations=stations,
            procedures=procedures,
            rooms=rooms,
            open_discrepancy_counts=open_discrepancy_counts,
            agreement_session_ids=agreement_session_ids,
            for_role=for_role,
        )
        for item in rows
    ]


def sessions_data():
    return _read(lambda: _session_rows(for_role='supervisor'), [])


def operator_my_sessions(user_id):
    from uuid import UUID
    try:
        parsed = UUID(str(user_id))
    except (TypeError, ValueError):
        return []
    return _read(lambda: _session_rows(for_role='operator', user_id=parsed), [])


def _session_detail_payload(item, *, for_role, users):
    (
        statuses, operations, _users, kits, stations, procedures, rooms,
        open_discrepancy_counts, agreement_session_ids,
    ) = _session_lookup_maps()
    row = _build_session_row(
        item,
        status_by_id=statuses,
        operations=operations,
        users=users or _users,
        kits=kits,
        stations=stations,
        procedures=procedures,
        rooms=rooms,
        open_discrepancy_counts=open_discrepancy_counts,
        agreement_session_ids=agreement_session_ids,
        for_role=for_role,
    )
    expected = db.session.execute(
        select(ExpectedInventory, InstrumentFamily)
        .join(InstrumentFamily, InstrumentFamily.id == ExpectedInventory.family_id)
        .where(ExpectedInventory.session_id == item.id)
        .order_by(InstrumentFamily.name)
    ).all()
    reported_by_family = {}
    events = db.session.execute(
        select(CountEvent, CatEventType)
        .join(CatEventType, CatEventType.id == CountEvent.event_type_id)
        .where(CountEvent.session_id == item.id)
        .order_by(CountEvent.occurred_at)
    ).all()
    for event, event_type in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.family_id and event_type.code in {'manual_count', 'auto_count'}:
            reported = payload.get('reported_quantity')
            if reported is None:
                reported = event.detected_quantity
            reported_by_family[str(event.family_id)] = reported
    row['expected_items'] = []
    for inv, family in expected:
        reported = reported_by_family.get(str(inv.family_id))
        diff = None if reported is None else int(reported) - inv.expected_quantity
        if reported is None:
            status_label, status_variant = 'Pending', 'neutral'
        elif diff == 0:
            status_label, status_variant = 'Match', 'success'
        elif diff < 0:
            status_label, status_variant = 'Shortfall', 'danger'
        else:
            status_label, status_variant = 'Extra', 'warning'
        row['expected_items'].append({
            'family_id': str(inv.family_id),
            'family_name': family.name,
            'family_code': family.code,
            'expected_quantity': inv.expected_quantity,
            'reported_quantity': reported,
            'ai_detected_quantity': None if item.capture_mode == 'manual_no_privacy' else reported,
            'difference': diff,
            'status_label': status_label,
            'status_variant': status_variant,
            'source': inv.source,
        })
    row['timeline'] = [
        {
            'occurred_at': _fmt_dt(event.occurred_at),
            'event_code': event_type.code,
            'event_name': event_type.name,
            'family_id': str(event.family_id) if event.family_id else None,
            'expected_quantity': event.expected_quantity,
            'detected_quantity': event.detected_quantity,
            'payload': event.payload if isinstance(event.payload, dict) else {},
        }
        for event, event_type in events
    ]
    open_discs = db.session.execute(
        select(Discrepancy, InstrumentFamily)
        .outerjoin(InstrumentFamily, InstrumentFamily.id == Discrepancy.family_id)
        .where(Discrepancy.session_id == item.id)
        .order_by(Discrepancy.resolved, InstrumentFamily.name)
    ).all()
    row['discrepancies'] = [
        {
            'id': str(disc.id),
            'description': disc.description,
            'resolved': disc.resolved,
            'family_name': family.name if family else '—',
            'expected_quantity': disc.expected_quantity,
            'detected_quantity': disc.detected_quantity,
            'resolved_at': _fmt_dt(disc.resolved_at),
        }
        for disc, family in open_discs
    ]
    agreement = db.session.execute(
        select(SessionProcessingAgreement)
        .where(SessionProcessingAgreement.session_id == item.id)
    ).scalar_one_or_none()
    name_users = users or _users
    if agreement:
        notice = db.session.get(PrivacyNoticeVersion, agreement.privacy_notice_version_id)
        row['privacy'] = {
            'has_agreement': True,
            'version': notice.version if notice else '—',
            'document_uri': notice.document_uri if notice else None,
            'agreed_at': _fmt_dt(agreement.agreed_at),
            'purpose_quality_ops': agreement.purpose_quality_ops,
            'purpose_model_improvement': agreement.purpose_model_improvement,
            'agreed_by': name_users.get(agreement.agreed_by_user_id, '—'),
        }
    else:
        row['privacy'] = {
            'has_agreement': False,
            'message': 'No privacy notice linked. Start Session will freeze capture_mode = manual_no_privacy.',
        }
    return row


def operator_session_detail(session_id, user_id):
    from uuid import UUID

    def query():
        try:
            parsed_session = UUID(str(session_id))
            parsed_user = UUID(str(user_id))
        except (TypeError, ValueError):
            return None
        item = db.session.get(WorkSession, parsed_session)
        if item is None or item.user_id != parsed_user:
            return None
        users = {u.id: u.name for u in db.session.execute(select(User)).scalars()}
        return _session_detail_payload(item, for_role='operator', users=users)

    return _read(query, None)


def supervisor_session_detail(session_id):
    from uuid import UUID

    def query():
        try:
            parsed_session = UUID(str(session_id))
        except (TypeError, ValueError):
            return None
        item = db.session.get(WorkSession, parsed_session)
        if item is None:
            return None
        users = {u.id: u.name for u in db.session.execute(select(User)).scalars()}
        return _session_detail_payload(item, for_role='supervisor', users=users)

    return _read(query, None)


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


# ----------------------------------------------------------------------------------------
# V3 demo accessors (ui_reference_v3). ADDITIVE ONLY: nothing above this line calls them,
# and they never touch the database or the RF data functions (sessions_data(),
# discrepancies_data(), operator_session_detail(), supervisor_session_detail()), which the
# RF workflow and the Administrator dashboard depend on.
#
# Demo vs RF boundary (see use_v3_fixture()):
#   - FRONTEND_DEMO_MODE on, or the id is an explicit V3 demo fixture id (WS-0xx)
#       -> V3 fixture data may be used.
#   - A UUID session id with demo mode off -> keep the current RF data path.
# No active route uses these yet; page phases opt in screen by screen.
# ----------------------------------------------------------------------------------------

def _is_uuid(value):
    try:
        _UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return False
    return True


def is_v3_demo_session_id(session_id):
    return demo_data.is_demo_session_id(session_id)


def use_v3_fixture(session_id=None):
    """
    Whether a V3 screen may render fixture data instead of the RF path.

    session_id=None asks about list screens (Dashboard, Sessions, Assigned Sessions): only
    demo mode enables fixtures there. For a detail id, an explicit demo id always qualifies;
    any other non-UUID id qualifies only in demo mode; a UUID with demo mode off never does.
    """
    if session_id is None:
        return _demo_mode()
    if demo_data.is_demo_session_id(session_id):
        return True
    return _demo_mode() and not _is_uuid(session_id)


def v3_presentation_user(role):
    """Optional display identity for V3 demo pages ('operator' | 'supervisor'). Never replaces current_user."""
    user = demo_data.PRESENTATION_USERS.get(role)
    return deepcopy(user) if user else None


def v3_evidence():
    """Shared WS-026 final evidence; image_url is set only if the asset exists in static/."""
    evidence = deepcopy(demo_data.WS026_EVIDENCE)
    evidence['image_url'] = None
    evidence['image_available'] = False
    static_folder = current_app.static_folder or ''
    for extension in demo_data.EVIDENCE_IMAGE_EXTENSIONS:
        filename = f"{evidence['image_basename']}.{extension}"
        if os.path.isfile(os.path.join(static_folder, *filename.split('/'))):
            evidence['image_url'] = url_for('static', filename=filename)
            evidence['image_available'] = True
            break
    return evidence


def _v3_inventory():
    kit = deepcopy(demo_data.DELIVERY_KIT)
    return {'kit_name': kit['name'], 'items': kit['items'], 'total_expected': kit['total_expected']}


def _v3_ws026_header(session_status_code, review_status_code):
    header = deepcopy(demo_data.WS026_SESSION)
    header['surgical_team'] = deepcopy(demo_data.SURGICAL_TEAM)
    header['session_status'] = demo_data.session_status(session_status_code)
    header['review_status'] = demo_data.review_status(review_status_code)
    return header


def operator_v3_view(session_id, scenario=None, validation=None):
    """
    Operator V3 view-model for a demo session (currently WS-026), or None when the id is
    not a V3 detail fixture. `scenario` (escalated | clean, default escalated) is a
    presentation-only projection and is never persisted.

    `validation` is the submitted Human Validation demo state (operator_v3_parse_validation).
    When given, counts, review cases and the clean/escalated outcome are derived from it
    instead of the scenario; `scenario` then only selects the AI fixture shown (base_scenario).
    """
    if demo_data.normalize_session_id(session_id) != demo_data.WS026_ID:
        return None
    base_scenario = demo_data.normalize_scenario(scenario)
    if validation is None:
        scenario = base_scenario
        is_clean = scenario == demo_data.SCENARIO_CLEAN
        counts = _operator_v3_decorate_rows(demo_data.ws026_count_rows(scenario), scenario)
        review_cases = [] if is_clean else demo_data.ws026_review_cases()
    else:
        counts = _operator_v3_hv_rows(validation, base_scenario)
        review_cases = _operator_v3_hv_review_cases(
            counts, validation, operator_v3_validation_defaults(base_scenario))
        is_clean = not review_cases
        scenario = demo_data.SCENARIO_CLEAN if is_clean else demo_data.SCENARIO_ESCALATED
    review_code = 'no_review_needed' if is_clean else 'review_required'
    return {
        'scenario': scenario,
        'base_scenario': base_scenario,
        'validation_state': validation,
        'is_interactive': validation is not None,
        'correction_reasons': operator_v3_correction_reasons(),
        'is_clean': is_clean,
        'has_discrepancy': not is_clean,
        'session': _v3_ws026_header('in_progress', review_code),
        'inventory': _v3_inventory(),
        'counts': counts,
        'totals': demo_data.count_totals(counts),
        'review_cases': review_cases,
        'unresolved_review_cases': len(review_cases),
        'review_status': demo_data.review_status(review_code),
        'tray_verification': deepcopy(demo_data.TRAY_VERIFICATION),
        'ai_analysis': deepcopy(demo_data.AI_ANALYSIS),
        'evidence': v3_evidence(),
        'timeline': demo_data.ws026_timeline(scenario),
        'instrument_events': deepcopy(demo_data.WS026_INSTRUMENT_EVENTS),
        'latest_live_capture': deepcopy(demo_data.LATEST_LIVE_CAPTURE),
        'date_display': demo_data.DEMO_DATE_DISPLAY,
        'presentation_user': v3_presentation_user('operator'),
    }


def operator_v3_assigned_sessions():
    """Operator Assigned Sessions rows (fixture projection)."""
    return [
        demo_data.session_row(item['session_id'], session_status=item['session_status'])
        for item in demo_data.OPERATOR_ASSIGNED_ROWS
    ]


def _v3_supervisor_links(row):
    """Action button for a Supervisor V3 row, derived from its review status."""
    session_id = row['session_id']
    review_url = url_for('web.supervisor_discrepancy_review', session_id=session_id, tab='overview')
    details_url = url_for('web.supervisor_session_details', session_id=session_id)
    code = row['review_status']['code']
    if code == 'review_required':
        action = {'label': 'Review', 'url': review_url, 'style': 'primary'}
    elif code == 'reviewed_unresolved':
        action = {'label': 'Continue Review', 'url': review_url, 'style': 'primary'}
    else:
        action = {'label': 'View Details', 'url': details_url, 'style': 'secondary'}
    row.update({'action': action, 'details_url': details_url, 'review_url': review_url})
    return row


def _v3_review_case_detail_rows(session_id):
    """Review cases for supporting demo sessions (summary rows only, no evidence/history)."""
    cases = []
    for item in deepcopy(demo_data.OTHER_REVIEW_QUEUE_ROWS):
        if item['session_id'] != session_id:
            continue
        item.update({
            'case_key': None,
            'type_label': item['type_short'],
            'review_status': demo_data.review_status(item['review_status']),
            'severity': demo_data.severity('critical' if item['difference'] else 'info'),
            'escalated_time': None,
            'applied_resolution': None,
        })
        cases.append(item)
    return cases


def supervisor_v3_view(session_id, review_outcome=None, resolved=None, unresolved=None):
    """
    Supervisor V3 view-model for a demo session id, or None when the id is not a V3 demo id.

    WS-026 is the full canonical case (escalated projection only). Other demo ids
    (WS-021..WS-028, including the wizard-created WS-027) return a summary-only view
    (`is_full_case` false) built from their list row. `review_outcome` (resolved |
    unresolved) and the `resolved` / `unresolved` case-key lists select the Review demo
    state; nothing is persisted and the canonical fixture is never mutated.
    """
    session_id = demo_data.normalize_session_id(session_id)
    if session_id == demo_data.WS026_ID:
        states = demo_data.review_case_states(review_outcome, resolved or (), unresolved or ())
        counts = demo_data.ws026_count_rows(demo_data.SCENARIO_ESCALATED)
        review_cases = demo_data.ws026_review_cases(states=states)
        timeline = demo_data.ws026_timeline(demo_data.SCENARIO_ESCALATED)
        return {
            'is_full_case': True,
            'review_outcome': demo_data.normalize_review_outcome(review_outcome),
            'case_states': states,
            'session': _v3_ws026_header('closed', 'review_required'),
            'inventory': _v3_inventory(),
            'counts': counts,
            'totals': demo_data.count_totals(counts),
            'review_cases': review_cases,
            'open_review_cases': sum(c['review_status']['code'] != 'resolved' for c in review_cases),
            'final_validation': demo_data.final_validation_summary(review_cases),
            'validation_history_event': deepcopy(demo_data.WS026_VALIDATION_HISTORY_EVENT),
            'resolution_types': list(demo_data.RESOLUTION_TYPES),
            'ai_analysis': deepcopy(demo_data.AI_ANALYSIS),
            'evidence': v3_evidence(),
            'timeline': timeline,
            'traceability': [e for e in timeline if e['code'] in demo_data.TRACEABILITY_CODES],
            'date_display': demo_data.DEMO_DATE_DISPLAY,
            'presentation_user': v3_presentation_user('supervisor'),
        }
    if session_id not in demo_data.DEMO_SESSION_IDS:
        return None
    row = demo_data.session_row(session_id)
    if session_id == demo_data.WS027_ID:
        wizard = demo_data.WS027_NEW_SESSION
        row.update({'patient_record': wizard['patient_record'],
                    'surgical_team': deepcopy(wizard['surgical_team']),
                    'scheduled_at_display': wizard['scheduled_at_display']})
    review_cases = _v3_review_case_detail_rows(session_id)
    return {
        'is_full_case': False,
        'review_outcome': None,
        'case_states': {},
        'session': row,
        'inventory': _v3_inventory() if row['kit_name'] == demo_data.DELIVERY_KIT['name'] else None,
        'counts': [],
        'totals': None,
        'review_cases': review_cases,
        'open_review_cases': sum(c['review_status']['code'] != 'resolved' for c in review_cases),
        'final_validation': None,
        'validation_history_event': None,
        'resolution_types': list(demo_data.RESOLUTION_TYPES),
        'ai_analysis': None,
        'evidence': None,
        'timeline': [],
        'traceability': [],
        'date_display': demo_data.DEMO_DATE_DISPLAY,
        'presentation_user': v3_presentation_user('supervisor'),
    }


def supervisor_v3_new_session():
    """Supervisor New Session wizard demo (WS-027, a different session from WS-026)."""
    wizard = deepcopy(demo_data.WS027_NEW_SESSION)
    wizard['inventory'] = _v3_inventory()
    wizard['session_status'] = demo_data.session_status(wizard['session_status'])
    wizard['review_status'] = demo_data.review_status(wizard['review_status'])
    return wizard


def supervisor_v3_created_banner(created):
    """Success banner for /supervisor/sessions?created=WS-027 (only the wizard demo id)."""
    if demo_data.normalize_session_id(created) != demo_data.WS027_ID:
        return None
    return {'variant': 'success', 'title': demo_data.WS027_NEW_SESSION['success_message'], 'description': ''}


def supervisor_v3_sessions(created=None):
    """Supervisor Sessions rows; the wizard-created WS-027 is listed first after creation."""
    order = list(demo_data.SUPERVISOR_SESSIONS_ORDER)
    if demo_data.normalize_session_id(created) == demo_data.WS027_ID:
        order.insert(0, demo_data.WS027_ID)
    return [_v3_supervisor_links(demo_data.session_row(item)) for item in order]


def supervisor_v3_session_history():
    return [_v3_supervisor_links(demo_data.session_row(item)) for item in demo_data.SUPERVISOR_HISTORY_ORDER]


def supervisor_v3_review_queue():
    """Supervisor Review Queue: one row per review case (WS-026's two cases first)."""
    ws026 = demo_data.session_row(demo_data.WS026_ID)
    rows = []
    for case in demo_data.ws026_review_cases():
        rows.append({
            'session_id': ws026['session_id'],
            'procedure_label': ws026['procedure_label'],
            'operator_name': ws026['operator_name'],
            'case_key': case['case_key'],
            'instrument_name': case['instrument_name'],
            'kit_label': case['kit_label'],
            'expected_quantity': case['expected_quantity'],
            'ai_detected': case['ai_detected'],
            'validated': case['validated'],
            'difference': case['difference'],
            'type_short': case['type_short'],
            'review_status': case['review_status'],
        })
    for item in deepcopy(demo_data.OTHER_REVIEW_QUEUE_ROWS):
        session = demo_data.session_row(item['session_id'])
        item.update({
            'procedure_label': session['procedure_label'],
            'operator_name': session['operator_name'],
            'case_key': None,
            'review_status': demo_data.review_status(item['review_status']),
        })
        rows.append(item)
    for row in rows:
        _v3_supervisor_links(row)
        if row['case_key'] and row['review_status']['code'] != 'resolved':
            row['action']['url'] = url_for('web.supervisor_discrepancy_review', session_id=row['session_id'],
                                           tab='overview', case=row['case_key'])
    return rows


def supervisor_v3_review_summary():
    return deepcopy(demo_data.SUPERVISOR_DASHBOARD['review_queue_summary'])


def _with_fill(items, key='value'):
    peak = max((item[key] for item in items), default=0) or 1
    for item in items:
        item['fill_percent'] = int(item[key] * 100 // peak)
    return items


def supervisor_v3_dashboard():
    dashboard = deepcopy(demo_data.SUPERVISOR_DASHBOARD)
    for item in dashboard['review_status_counts']:
        item.update(demo_data.review_status(item['code']))
    _with_fill(dashboard['review_status_counts'])
    _with_fill(dashboard['sessions_by_day'], key='count')
    dashboard['review_rows'] = [
        row for row in supervisor_v3_sessions()
        if row['review_status']['code'] in {'review_required', 'reviewed_unresolved'}
    ]
    return dashboard


def supervisor_v3_reports():
    return deepcopy(demo_data.SUPERVISOR_REPORTS)


def supervisor_v3_indicators():
    indicators = deepcopy(demo_data.SUPERVISOR_INDICATORS)
    _with_fill(indicators['sessions_by_day'], key='count')
    for key in ('discrepancies_by_instrument', 'discrepancies_by_family', 'discrepancies_by_type'):
        _with_fill(indicators[key])
    return indicators


def supervisor_v3_audit_log():
    """Audit Log rows built from the centralized WS-026 timeline (newest last, as recorded)."""
    rows = []
    for event in demo_data.ws026_timeline(demo_data.SCENARIO_ESCALATED):
        rows.append({
            'time': event['time'],
            'user': event['actor'] or 'System',
            'role': event['actor_role'],
            'event': event['code'],
            'entity': event['entity'],
            'record': demo_data.WS026_ID,
            'result': 'SUCCESS',
            'details': event['detail'],
        })
    return rows


def supervisor_v3_list_meta(list_name):
    return deepcopy(demo_data.SUPERVISOR_LIST_TOTALS[list_name])


def supervisor_v3_filter_options():
    options = deepcopy(demo_data.SUPERVISOR_FILTER_OPTIONS)
    options['session_statuses'] = [demo_data.session_status(code) for code in options['session_statuses']]
    options['review_statuses'] = [demo_data.review_status(code) for code in options['review_statuses']]
    return options


def v3_supervisor_display_user(user):
    """
    Header display identity for V3 Supervisor demo pages: a shallow copy of the
    authenticated user with only the presentation name/role label swapped. The
    authenticated g.verified_user and auth service identity are never modified.
    """
    presentation = v3_presentation_user('supervisor')
    if not user or not presentation:
        return user
    display = dict(user)
    display['name'] = presentation['name']
    display['role_label'] = presentation['role_label']
    return display


# --- RF -> V3 presentation adapters (used only when demo mode is off) --------------------

def supervisor_v3_rows_from_rf(rows):
    """
    Map RF session rows (sessions_data()) onto the V3 list shape. RF status labels and
    action URLs are kept as-is; review status is derived from open discrepancies only.
    """
    values = []
    for item in rows:
        open_count = item.get('discrepancy_count') or 0
        values.append({
            'session_id': item['session_id'],
            'procedure_label': f"{item['procedure_name']} - {item['operating_room']}",
            'kit_name': item['kit_name'],
            'operator_name': item['operator_name'],
            'capture_station': item['capture_station_name'],
            'scheduled_time': item.get('scheduled_at') or '—',
            'started_time': item.get('started_at') or '—',
            'closed_time': item.get('closed_at') or '—',
            'issue_label': f'{open_count} open discrepancies' if open_count else '—',
            'issue_detail': '',
            'session_status': {'code': item['status_code'], 'label': item['status_label'],
                               'variant': item['status_variant']},
            'review_status': demo_data.review_status('review_required' if open_count else 'no_review_needed'),
            'action': {'label': item['action_label'], 'url': item['action_url'],
                       'style': 'primary' if open_count else 'secondary'},
            'details_url': item['details_url'],
        })
    return values


def supervisor_v3_queue_from_rf(rows):
    """Map RF discrepancy rows (discrepancies_data()) onto the V3 review-queue shape."""
    values = []
    for item in rows:
        resolved = item['status_label'] == 'Resolved'
        values.append({
            'session_id': item['session_id'],
            'procedure_label': f"{item['procedure_name']} - {item['operating_room']}",
            'operator_name': item['operator_name'],
            'case_key': None,
            'instrument_name': item['instrument_name'],
            'kit_label': item['kit_name'],
            'expected_quantity': item['expected_quantity'],
            'ai_detected': item['counted_quantity'],
            'validated': item['counted_quantity'],
            'difference': item['difference'],
            'type_short': item['reason'],
            'review_status': demo_data.review_status('resolved' if resolved else 'review_required'),
            'action': {'label': 'View Details' if resolved else 'Review', 'url': item['action_url'],
                       'style': 'secondary' if resolved else 'primary'},
        })
    return values


def supervisor_v3_dashboard_from_rf(stats, sessions):
    """RF-mode Dashboard in the V3 shape (no sessions-by-day analytics exist yet)."""
    rows = supervisor_v3_rows_from_rf(sessions)
    review_rows = [row for row in rows if row['review_status']['code'] != 'no_review_needed']
    review_counts = [
        {'code': 'review_required', 'value': len(review_rows)},
        {'code': 'reviewed_unresolved', 'value': 0},
        {'code': 'resolved', 'value': 0},
    ]
    for item in review_counts:
        item.update(demo_data.review_status(item['code']))
    return {
        'kpis': {
            'sessions_today': stats.get('sessions_today', 0),
            'in_progress_sessions': stats.get('active_sessions', 0),
            'reviews_required': stats.get('pending_reviews', 0),
            'unresolved_discrepancies': stats.get('open_discrepancies', 0),
        },
        'sessions_by_day': [],
        'review_status_counts': _with_fill(review_counts),
        'review_rows': review_rows,
    }


def supervisor_v3_validated_banner(validated):
    """Banner after the demo Confirm Resolution action (nothing is persisted)."""
    if demo_data.normalize_session_id(validated) != demo_data.WS026_ID:
        return None
    return deepcopy(demo_data.RESOLUTION_VALIDATED_BANNER)


def supervisor_v3_review_case_keys():
    return list(demo_data.REVIEW_CASE_KEYS)


# --- Operator V3 (ui_reference_v3/operator). Additive; Supervisor accessors are untouched. -

OPERATOR_V3_ACTIVE_CODES = ('assigned', 'ready_to_start', 'in_progress')


def v3_session_status(code):
    """V3 presentation status {code, label, variant} (e.g. for the Operator context bar)."""
    return demo_data.session_status(code)


def v3_operator_display_user(user):
    """
    Header display identity for V3 Operator demo pages: a shallow copy of the authenticated
    user with only the presentation name/role label swapped (same technique as
    v3_supervisor_display_user). g.verified_user and the auth identity are never modified.
    """
    presentation = v3_presentation_user('operator')
    if not user or not presentation:
        return user
    display = dict(user)
    display['name'] = presentation['name']
    display['role_label'] = presentation['role_label']
    return display


def operator_v3_is_assigned_demo_id(session_id):
    normalized = demo_data.normalize_session_id(session_id)
    return any(item['session_id'] == normalized for item in demo_data.OPERATOR_ASSIGNED_ROWS)


def operator_v3_assigned_session_rows(exclude_session_id=None):
    """
    Assigned Sessions rows (operator_v3_assigned_sessions) with their V3 action buttons.
    exclude_session_id drops one session from this response only (used right after a demo
    close); nothing is persisted, so a plain reload shows the static fixture again.
    """
    excluded = demo_data.normalize_session_id(exclude_session_id) if exclude_session_id else None
    rows = []
    for row in operator_v3_assigned_sessions():
        if row['session_id'] == excluded:
            continue
        # Date used by the client-side demo date filter: an explicit date in the fixture's
        # scheduled time (WS-027), otherwise the demo day.
        scheduled = str(row.get('scheduled_time') or '')
        row['scheduled_date'] = scheduled[:10] if len(scheduled) > 10 and scheduled[4] == '-' else demo_data.DEMO_DATE
        if row['session_status']['code'] == 'in_progress':
            row['action'] = {'label': 'Continue Session',
                             'url': url_for('web.operator_session_capture', session_id=row['session_id'])}
        else:
            row['action'] = {'label': 'Review & Start',
                             'url': url_for('web.operator_session_begin', session_id=row['session_id'])}
        rows.append(row)
    return rows


def operator_v3_demo_date():
    """Demo "today" (ISO date) for the Assigned Sessions client-side date filter."""
    return demo_data.DEMO_DATE


def operator_v3_confirmation(session_id):
    """
    Assigned Session Confirmation view-model (read-only), or None for ids not assigned to
    the Operator in the demo. WS-026 is the full walkthrough case; WS-027 comes from the
    Supervisor wizard fixture; other assigned rows are summary-only.
    """
    normalized = demo_data.normalize_session_id(session_id)
    if not operator_v3_is_assigned_demo_id(normalized):
        return None
    assigned = next(item for item in demo_data.OPERATOR_ASSIGNED_ROWS if item['session_id'] == normalized)
    status = demo_data.session_status(assigned['session_status'])
    if normalized == demo_data.WS026_ID:
        session = _v3_ws026_header('assigned', 'no_review_needed')
        session['session_status'] = status
        return {'is_full_case': True, 'session': session, 'inventory': _v3_inventory()}
    row = demo_data.session_row(normalized, session_status=assigned['session_status'])
    session = {
        'session_id': row['session_id'], 'procedure_name': row['procedure_name'],
        'operating_room': row['operating_room'], 'kit_name': row['kit_name'],
        'capture_station': row['capture_station'], 'operator_name': row['operator_name'],
        'assigned_by': demo_data.WS026_SESSION['supervisor_name'],
        'patient_name': row['patient_name'], 'patient_record': None,
        'scheduled_at_display': row['scheduled_time'], 'surgical_team': [],
        'session_status': status,
    }
    inventory = None
    if normalized == demo_data.WS027_ID:
        wizard = demo_data.WS027_NEW_SESSION
        session.update({'patient_record': wizard['patient_record'],
                        'scheduled_at_display': wizard['scheduled_at'],
                        'surgical_team': deepcopy(wizard['surgical_team'])})
        inventory = _v3_inventory()
    return {'is_full_case': False, 'session': session, 'inventory': inventory}


def operator_v3_list_banner(closed=None, outcome=None, notice=None, session_id=None):
    """Assigned Sessions banner after a demo close or a non-walkthrough start (no persistence)."""
    if closed is not None:
        if demo_data.normalize_session_id(closed) != demo_data.WS026_ID:
            return None
        key = 'closed_clean' if demo_data.normalize_scenario(outcome) == demo_data.SCENARIO_CLEAN else 'closed_escalated'
        session = demo_data.WS026_ID
    elif notice == 'walkthrough' and operator_v3_is_assigned_demo_id(session_id):
        key, session = 'walkthrough', demo_data.normalize_session_id(session_id)
    else:
        return None
    banner = deepcopy(demo_data.OPERATOR_BANNERS[key])
    banner['title'] = banner['title'].replace('{session}', session)
    banner['description'] = banner['description'].replace('{session}', session)
    return banner


RF_OPERATOR_ACTION_LABELS = {'Begin': 'Review & Start', 'Continue': 'Continue Session', 'View details': 'View Details'}


def operator_v3_rows_from_rf(rows):
    """
    Map RF operator rows (operator_my_sessions()) onto the V3 Assigned Sessions layout.

    Inclusion matches the pre-V3 list exactly: EVERY RF row is listed, whatever its RF
    lifecycle status (scheduled, in progress, awaiting review, correction required, closed,
    aborted...). Only labels change; the RF action URL (begin / manual / capture / details)
    and the RF details URL are kept unchanged.
    """
    values = []
    for item in rows:
        values.append({
            'session_id': item['session_id'],
            'procedure_name': item['procedure_name'],
            'patient_name': None,
            'operating_room': item['operating_room'],
            'scheduled_time': item.get('scheduled_at') or '—',
            'session_status': {'code': item['status_code'], 'label': item['status_label'],
                               'variant': item['status_variant']},
            'action': {'label': RF_OPERATOR_ACTION_LABELS.get(item['action_label'], item['action_label']),
                       'url': item['action_url']},
            'details_url': item.get('details_url'),
        })
    return values


def operator_v3_scenario(value):
    """Normalize the presentation-only Operator demo scenario (escalated | clean)."""
    return demo_data.normalize_scenario(value)


# --- Operator V3 interactive Human Validation (demo form state only) ---------------------
#
# The Human Validation form POSTs the Operator's values; the route validates them and
# redirects with them as query parameters (hv=1, vc_<key> count, vr_<key> reason code,
# vn_<key> notes, ev_<key>=1 evidence verified). Every following V3 page (Summary, Edit
# Count, Escalation, Ready to Close, close) re-derives the same projection from its URL.
# Nothing is stored: no DB, cookie or Flask session. The canonical WS-026 fixture in
# demo_data is never mutated, so the Supervisor V3 pages keep their approved example.

OPERATOR_V3_HV_MARKER = 'hv'
OPERATOR_V3_MAX_COUNT = 99
OPERATOR_V3_MAX_NOTES = 200
OPERATOR_V3_MAX_ESCALATION_NOTES = 500
OPERATOR_V3_CORRECTION_REASONS = (
    ('missing', 'Missing Instrument'),
    ('extra', 'Extra Instrument'),
    ('misclassification', 'AI Misclassification'),
    ('occlusion', 'Occlusion / Poor Visibility'),
    ('manual', 'Manual Count Correction'),
    ('other', 'Other'),
)
_OPERATOR_V3_REASON_LABELS = dict(OPERATOR_V3_CORRECTION_REASONS)
_OPERATOR_V3_REASON_CODES = {label: code for code, label in OPERATOR_V3_CORRECTION_REASONS}
OPERATOR_V3_HV_STATUS_UI = {
    'matched': ('Matched', 'success'),
    'unresolved': ('Unresolved', 'danger'),
    'evidence_verification_required': ('Evidence Verification Required', 'warning'),
    'evidence_verified': ('Evidence Verified', 'success'),
}
OPERATOR_V3_REVIEW_CODES = ('unresolved', 'evidence_verification_required')
OPERATOR_V3_EVIDENCE_REASON = 'Detection evidence requires verification'
OPERATOR_V3_EVIDENCE_VERIFIED_REASON = 'Evidence verified'
OPERATOR_V3_UNSPECIFIED_REASON = 'Not specified'


def _operator_v3_evidence_keys():
    """Instruments whose canonical review case is evidence verification (Suture Needle Set)."""
    return {key for key, counts in demo_data.WS026_COUNTS_ESCALATED.items()
            if counts['validation_status'] == 'evidence_verification_required'}


def operator_v3_correction_reasons():
    return [{'code': code, 'label': label} for code, label in OPERATOR_V3_CORRECTION_REASONS]


def _operator_v3_decorate_rows(rows, scenario):
    """Add the form fields Human Validation needs to fixture rows (values unchanged)."""
    evidence_keys = _operator_v3_evidence_keys()
    verified = demo_data.normalize_scenario(scenario) == demo_data.SCENARIO_CLEAN
    for row in rows:
        row['evidence_required'] = row['key'] in evidence_keys
        row['evidence_verified'] = row['evidence_required'] and verified
        row['reason_code'] = _OPERATOR_V3_REASON_CODES.get(row['correction_reason'], '')
        row['validated_input'] = row['validated']
    return rows


def operator_v3_validation_defaults(scenario=None):
    """Initial Human Validation form state, taken from the WS-026 fixture of the scenario."""
    state = {}
    for row in _operator_v3_decorate_rows(demo_data.ws026_count_rows(scenario), scenario):
        state[row['key']] = {
            'validated': row['validated'],
            'reason': row['reason_code'],
            'notes': row['correction_notes'],
            'evidence_verified': row['evidence_verified'],
        }
    return state


def operator_v3_parse_validation(values, scenario=None, strict=False):
    """
    Read a submitted Human Validation demo state from request values.

    Returns (state, errors). state is None when the values carry no validation (no hv=1),
    i.e. the canonical fixture projection applies. Counts must be whole numbers 0..99;
    with strict=True (the form POST) a count discrepancy also requires a correction reason.
    Invalid input falls back to the fixture value and is reported in errors.
    """
    if values is None or values.get(OPERATOR_V3_HV_MARKER) != '1':
        return None, []
    state = operator_v3_validation_defaults(scenario)
    evidence_keys = _operator_v3_evidence_keys()
    items = {item['key']: item for item in demo_data.DELIVERY_KIT['items']}
    errors = []
    for key, entry in state.items():
        name = items[key]['instrument_name']
        raw = (values.get(f'vc_{key}') or '').strip()
        count = None
        if raw:
            try:
                count = int(raw)
            except ValueError:
                count = None
        if count is None or not 0 <= count <= OPERATOR_V3_MAX_COUNT:
            if raw or strict:
                errors.append(f'Enter a whole number from 0 to {OPERATOR_V3_MAX_COUNT} for {name}.')
                entry['input'] = raw
        else:
            entry['validated'] = count
        reason = (values.get(f'vr_{key}') or '').strip()
        entry['reason'] = reason if reason in _OPERATOR_V3_REASON_LABELS else ''
        if f'vn_{key}' in values:
            entry['notes'] = (values.get(f'vn_{key}') or '').strip()[:OPERATOR_V3_MAX_NOTES]
        entry['evidence_verified'] = key in evidence_keys and values.get(f'ev_{key}') == '1'
        if (strict and 'input' not in entry and entry['validated'] != items[key]['expected_quantity']
                and not entry['reason']):
            errors.append(f'Select a correction reason for {name}.')
    return state, errors


def operator_v3_validation_query(state):
    """Query parameters that carry a submitted validation state to the next V3 page."""
    if not state:
        return {}
    params = {OPERATOR_V3_HV_MARKER: '1'}
    for key, entry in state.items():
        params[f'vc_{key}'] = entry['validated']
        if entry['reason']:
            params[f'vr_{key}'] = entry['reason']
        params[f'vn_{key}'] = entry['notes']
        if entry.get('evidence_verified'):
            params[f'ev_{key}'] = '1'
    return params


def operator_v3_escalation_notes(value):
    """Operator escalation note carried to the success page (trimmed, length-capped)."""
    return (value or '').strip()[:OPERATOR_V3_MAX_ESCALATION_NOTES]


def _operator_v3_hv_rows(state, scenario):
    """WS-026 count rows recomputed from a submitted validation state."""
    rows = _operator_v3_decorate_rows(demo_data.ws026_count_rows(scenario), scenario)
    for row in rows:
        entry = state[row['key']]
        validated = entry['validated']
        difference = validated - row['expected_quantity']
        verified = row['evidence_required'] and bool(entry.get('evidence_verified'))
        if difference:
            code, reason = 'unresolved', _OPERATOR_V3_REASON_LABELS.get(entry['reason'], OPERATOR_V3_UNSPECIFIED_REASON)
        elif row['evidence_required'] and not verified:
            code, reason = 'evidence_verification_required', OPERATOR_V3_EVIDENCE_REASON
        elif row['evidence_required']:
            code, reason = 'evidence_verified', OPERATOR_V3_EVIDENCE_VERIFIED_REASON
        else:
            code, reason = 'matched', 'None'
        label, variant = OPERATOR_V3_HV_STATUS_UI[code]
        row.update({
            'validated': validated,
            'validated_input': entry.get('input', validated),
            'difference': difference,
            'validation_status': {'code': code, 'label': label, 'variant': variant},
            'needs_review': code in OPERATOR_V3_REVIEW_CODES,
            'correction_reason': reason,
            'correction_notes': entry['notes'],
            'reason_code': entry['reason'],
            'evidence_verified': verified,
        })
    return rows


def _operator_v3_hv_review_cases(rows, state, defaults):
    """
    Operator-side review cases for a submitted validation: one per row still needing review.
    Rows left exactly at their canonical values reuse the canonical WS-026 case text; any
    other discrepancy gets a case built from what the Operator entered.
    """
    canonical = {case['instrument_key']: case for case in demo_data.ws026_review_cases()}
    template = demo_data.WS026_REVIEW_CASES[0]
    cases = []
    for row in rows:
        if not row['needs_review']:
            continue
        key = row['key']
        entry = state[key]
        base = canonical.get(key)
        if base is not None and all(entry.get(f) == defaults[key].get(f)
                                    for f in ('validated', 'reason', 'notes', 'evidence_verified')):
            cases.append(base)
            continue
        difference = row['difference']
        name = row['instrument_name']
        type_label = row['correction_reason'] if difference else 'Evidence Verification'
        if difference:
            description = (f'Final validated count of {row["validated"]} does not match expected '
                           f'{row["expected_quantity"]} ({row["correction_reason"]}).')
            if row['evidence_required'] and not row['evidence_verified']:
                description += ' Detection evidence also requires verification.'
            title = 'Count discrepancy detected'
        else:
            description = (base['summary_description'] if base else
                           f'{name} counts match, but the detection evidence requires verification.')
            title = 'Evidence verification required'
        cases.append({
            'case_key': key.replace('_', '-'),
            'instrument_key': key,
            'instrument_name': name,
            'kit_label': template['kit_label'],
            'expected_quantity': row['expected_quantity'],
            'ai_detected': row['ai_detected'],
            'validated': row['validated'],
            'difference': difference,
            'type_label': type_label,
            'type_short': type_label,
            'severity': demo_data.severity('critical' if difference else 'info'),
            'escalated_time': template['escalated_time'],
            'review_status': demo_data.review_status('review_required'),
            'ai_evidence': {'detected_count': row['ai_detected'],
                            'avg_confidence_label': row['avg_confidence_label'],
                            'timestamp': template['ai_evidence']['timestamp']},
            'operator_validation': {
                'validated_count': row['validated'],
                'operator_name': demo_data.WS026_SESSION['operator_name'],
                'reason': row['correction_reason'],
                'timestamp': template['operator_validation']['timestamp'],
                'notes': entry['notes'],
            },
            'summary_title': title,
            'summary_description': description,
            'applied_resolution': None,
        })
    return cases
