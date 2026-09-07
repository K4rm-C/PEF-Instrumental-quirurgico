from collections import Counter
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models.AccessAudit import AccessAudit
from models.CaptureStation import CaptureStation
from models.CatInstrumentCategory import CatInstrumentCategory
from models.CatInstrumentCycleStatus import CatInstrumentCycleStatus
from models.CatOperationStatus import CatOperationStatus
from models.CatProcedureType import CatProcedureType
from models.CatSessionStatus import CatSessionStatus
from models.CountEvent import CountEvent
from models.Discrepancy import Discrepancy
from models.Instrument import Instrument
from models.InstrumentFamily import InstrumentFamily
from models.Institution import Institution
from models.Kit import Kit
from models.KitItem import KitItem
from models.Operation import Operation
from models.OperationPatient import OperationPatient
from models.OperationPhysician import OperationPhysician
from models.Patient import Patient
from models.Physician import Physician
from models.Role import Role
from models.User import User
from models.UserRole import UserRole
from models.WorkSession import WorkSession


def _read(operation, fallback):
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
    role_map = {}
    for user_id, code, description in role_rows:
        role_map.setdefault(user_id, (code, description))
    values = []
    for user in rows:
        role_code, role_description = role_map.get(user.id, ('', 'No role'))
        values.append({
            'id': str(user.id),
            'name': user.name,
            'email': user.email,
            'institution': institutions.get(user.institution_id, 'Unknown institution'),
            'role': role_code,
            'role_label': role_description,
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
        return [{
            'id': str(role.id),
            'code': role.code,
            'name': role.code.replace('_', ' ').title(),
            'description': role.description,
            'assigned_users': counts.get(role.id, 0),
            'status_label': 'Active',
            'status_variant': 'success',
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
        types = {}
        for item in item_rows:
            totals[item.kit_id] += item.quantity
            family = db.session.get(InstrumentFamily, item.family_id)
            if family:
                types.setdefault(item.kit_id, []).append(family.name)
        return [{
            'id': str(item.id),
            'name': item.name,
            'version': item.version,
            'instrument_types': ', '.join(types.get(item.id, [])),
            'total_expected_instruments': totals[item.id],
            'status_label': 'Active' if item.active else 'Inactive',
            'status_variant': 'success' if item.active else 'neutral',
            'edit_url': f'/admin/kits/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


def stations_data():
    return _read(lambda: [{
        'id': str(item.id),
        'code': str(item.id)[:8].upper(),
        'name': item.name,
        'status_label': 'Active' if item.active else 'Inactive',
        'status_variant': 'success' if item.active else 'neutral',
        'edit_url': f'/admin/configuration/capture-stations/{item.id}/edit',
    } for item in db.session.execute(select(CaptureStation).order_by(CaptureStation.name)).scalars()], [])


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


def user_form_data(user_id=None):
    def query():
        user = db.session.get(User, user_id) if user_id else None
        institutions = db.session.execute(select(Institution).order_by(Institution.name)).scalars().all()
        roles = db.session.execute(select(Role).order_by(Role.code)).scalars().all()
        role_codes = dict(db.session.execute(
            select(UserRole.user_id, Role.code).join(Role, Role.id == UserRole.role_id)
        ).all())
        return {
            'user': {
                'name': user.name if user else '',
                'email': user.email if user else '',
                'institution': str(user.institution_id) if user else '',
                'role': role_codes.get(user.id, '') if user else '',
                'role_label': role_codes.get(user.id, '') if user else '',
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
            'role': '',
            'role_label': '',
            'status': 'active',
        },
        'institutions': [],
        'roles': [],
    })


def role_form_data(role_id=None):
    def query():
        role = db.session.get(Role, role_id)
        assigned = db.session.scalar(select(func.count(UserRole.user_id)).where(UserRole.role_id == role.id)) if role else 0
        return {'role': {
            'code': role.code if role else '',
            'name': role.code.replace('_', ' ').title() if role else '',
            'description': role.description if role else '',
            'status': 'active',
            'assigned_users': assigned,
        }}
    return _read(query, {'role': {}})


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
        return {'capture_station': {
            'code': str(station.id)[:8].upper() if station else '',
            'name': station.name if station else '',
            'status': 'active' if not station or station.active else 'inactive',
        }}
    return _read(query, {'capture_station': {}})


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


def dashboard_data(role):
    sessions = sessions_data()
    discrepancies = discrepancies_data()
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