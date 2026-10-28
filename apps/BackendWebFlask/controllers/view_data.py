import os
from collections import Counter
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID as _UUID

from flask import current_app, url_for
from flask_babel import gettext, ngettext
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from controllers import demo_data
from extensions import db
from services import session_snapshot
from services.session_snapshot import parse_iso
from i18n import (
    N_,
    discrepancy_source,
    localize_db_label,
    localize_db_text,
    localize_discrepancy_description,
    localize_known_text,
    search_text,
)
from models.AccessAudit import AccessAudit
from models.CaptureStation import CaptureStation
from models.CatDiscrepancyReason import CatDiscrepancyReason
from models.CatEventType import CatEventType
from models.CatInstrumentCategory import CatInstrumentCategory
from models.CatInstrumentCycleStatus import CatInstrumentCycleStatus
from models.CatOperationPhase import CatOperationPhase
from models.CatOperationStatus import CatOperationStatus
from models.CatProcedureType import CatProcedureType
from models.CatSessionStatus import CatSessionStatus
from models.CatSurgicalRole import CatSurgicalRole
from models.CountEvent import CountEvent
from models.Discrepancy import Discrepancy
from models.HumanCorrection import HumanCorrection
from models.Instrument import Instrument
from models.InstrumentCycleEvent import InstrumentCycleEvent
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


# Labels in this module are English msgids marked with N_(): they stay English in Python
# (some are compared or used as lookup keys) and templates translate them with _().
SESSION_STATUS_UI = {
    'scheduled': (N_('Scheduled'), 'neutral'),
    'in_progress': (N_('In Progress'), 'info'),
    'awaiting_spd_review': (N_('Awaiting Review'), 'warning'),
    'correction_required': (N_('Correction Required'), 'danger'),
    'closed': (N_('Closed'), 'success'),
    'aborted': (N_('Aborted'), 'neutral'),
}

EXPECTED_SOURCE_LABELS = {
    'kit_snapshot': N_('Kit'),
    'schedule_additional': N_('Additional (schedule)'),
    'live_add': N_('Added during session'),
    'manual': N_('Manual'),
}

PAST_OPERATOR_STATUS_CODES = {
    'closed', 'aborted', 'awaiting_spd_review', 'correction_required',
}


def display_session_id(session_uuid):
    return f"WS-{str(session_uuid).replace('-', '')[:8].upper()}"


def paginate_list(rows, page=1, per_page=20):
    """Simple list pagination helper for OP/SPD session lists."""
    per_page_value = _normalize_per_page(per_page, allowed=(10, 20, 50), default=20)
    total = len(rows)
    total_pages = max(1, (total + per_page_value - 1) // per_page_value) if total else 1
    current_page = _clamp_page(page, total_pages)
    start = (current_page - 1) * per_page_value
    page_rows = rows[start:start + per_page_value]
    return {
        'rows': page_rows,
        'page': current_page,
        'per_page': per_page_value,
        'total': total,
        'total_pages': total_pages,
        'shown': len(page_rows),
        'has_previous': current_page > 1,
        'has_next': current_page < total_pages,
    }


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
    return value or N_('Not specified')


def _variant(status):
    code = (status or '').lower()
    if code in {'closed', 'available', 'active', 'resolved'}:
        return 'success'
    if code in {'pending', 'pending_review', 'in_progress'}:
        return 'warning'
    if code in {'cancelled', 'denied', 'missing', 'discrepancy'}:
        return 'danger'
    return 'neutral'


# --- Display labels for system-controlled DB values (see i18n.py). Stored values, ids and
# codes are untouched; only the text shown to the user is localized. -----------------------

def _category_label(category, fallback=N_('Other')):
    if category is None:
        return gettext(fallback)
    return localize_db_label('instrument_category', category.code, category.name)


def _cycle_status_label(status):
    return localize_db_label('cycle_status', status.code, status.name) if status else None


def _family_label(family):
    return localize_db_text('instrument_family', family.code, family.name) if family else None


def _procedure_label(procedure):
    return localize_db_label('procedure', procedure.code, procedure.name) if procedure else None


def _phase_label(phase):
    return localize_db_label('operation_phase', phase.code, phase.name) if phase else None


def _kit_label(kit):
    return localize_db_text('kit', kit.id, kit.name) if kit else None


def _station_label(station):
    return localize_db_text('capture_station', station.id, station.name) if station else None


def _room_label(room):
    return localize_db_text('operating_room', room.code, room.name) if room else None


def _role_label(code, description):
    return localize_db_text('role', code, description)


def _discrepancy_view(disc, family, reason_code, origin_event_code):
    """Discrepancy row for display; stored fields are passed through untouched."""
    family_name = _family_label(family) if family else None
    source = discrepancy_source(origin_event_code)
    return {
        'id': str(disc.id),
        'description': disc.description,
        'display_description': localize_discrepancy_description(
            reason_code=reason_code, source=source, instrument=family_name,
            expected=disc.expected_quantity, actual=disc.detected_quantity,
            stored_description=disc.description,
        ),
        # detected_quantity holds a reported count when the origin is a manual report.
        'quantity_label': N_('Reported') if source == 'reported' else N_('Detected'),
        'resolved': disc.resolved,
        # A confirmed missing instrument: never resolved by notes alone, never a safe close.
        'physical_shortage': reason_code == 'shortage',
        'family_name': family_name or '—',
        'expected_quantity': disc.expected_quantity,
        'detected_quantity': disc.detected_quantity,
        'resolved_at': _fmt_dt(disc.resolved_at),
    }


def _discrepancy_view_from_snapshot(item):
    """_discrepancy_view of a discrepancy snapshot (services/session_snapshot.py)."""
    disc = SimpleNamespace(
        id=item['id'], description=item['description'], expected_quantity=item.get('expected_quantity'),
        detected_quantity=item.get('detected_quantity'), resolved=item['resolved'],
        resolved_at=parse_iso(item.get('resolved_at')),
    )
    family = _ns(item.get('family'))
    return _discrepancy_view(disc, family, item.get('reason_code'), item.get('origin_event_code'))


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
        roles_by_user.setdefault(user_id, []).append({'code': code, 'label': _role_label(code, description)})
    values = []
    for user in rows:
        assigned_roles = roles_by_user.get(user.id, [])
        values.append({
            'id': str(user.id),
            'name': user.name,
            'email': user.email,
            'institution': institutions.get(user.institution_id, N_('Unknown institution')),
            'roles': assigned_roles,
            'role_codes': [item['code'] for item in assigned_roles],
            'status_label': N_('Active') if user.active else N_('Inactive'),
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
            'description': _role_label(role.code, role.description),
            'institution': institutions.get(role.institution_id, N_('Unknown institution')),
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
            'category': _category_label(categories.get(item.category_id), N_('Uncategorized')),
            'function': localize_db_text('instrument_family.function', item.code, item.function_text) or '',
            'status_label': N_('Active') if item.active else N_('Inactive'),
            'status_variant': 'success' if item.active else 'neutral',
            'edit_url': f'/admin/instrument-families/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


CATEGORY_PURPOSE_ORDER = (
    'cutting',
    'dissection',
    'grasping',
    'hemostasis',
    'retraction',
    'suction',
    'suturing',
)


def _clamp_page(page, total_pages):
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    if total_pages < 1:
        return 1
    return max(1, min(page, total_pages))


def _normalize_per_page(per_page, allowed=(10, 20, 50), default=20):
    try:
        value = int(per_page)
    except (TypeError, ValueError):
        return default
    return value if value in allowed else default


def instruments_data():
    def query():
        families = {
            item.id: item
            for item in db.session.execute(select(InstrumentFamily)).scalars()
        }
        statuses = {
            item.id: item
            for item in db.session.execute(select(CatInstrumentCycleStatus)).scalars()
        }
        rows = db.session.execute(select(Instrument).order_by(Instrument.internal_code)).scalars().all()
        return [{
            'id': str(item.id),
            'internal_code': item.internal_code or str(item.id),
            'instrument_family_name': _label(_family_label(families.get(item.family_id))),
            'cycle_status_label': _label(_cycle_status_label(statuses.get(item.cycle_status_id))),
            'cycle_status_variant': _variant(getattr(statuses.get(item.cycle_status_id), 'name', None)),
            'active_status_label': N_('Active') if item.active else N_('Inactive'),
            'active_status_variant': 'success' if item.active else 'neutral',
            'edit_url': f'/admin/instruments/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


def instruments_query_page(
    search='',
    type_code='',
    cycle_status='',
    status='active',
    page=1,
    per_page=20,
):
    """Filtered/paginated instruments list for admin/SPD catalogs."""

    def query():
        per_page_value = _normalize_per_page(per_page)
        families = {
            item.id: item
            for item in db.session.execute(select(InstrumentFamily)).scalars()
        }
        categories = {
            item.id: item
            for item in db.session.execute(select(CatInstrumentCategory)).scalars()
        }
        statuses = {
            item.id: item
            for item in db.session.execute(select(CatInstrumentCycleStatus)).scalars()
        }
        status_by_code = {item.code: item for item in statuses.values()}

        rows = db.session.execute(select(Instrument).order_by(Instrument.internal_code)).scalars().all()
        search_term = (search or '').strip().lower()
        type_filter = (type_code or '').strip().lower()
        cycle_filter = (cycle_status or '').strip().lower()
        status_filter = (status or '').strip().lower()
        if status_filter not in {'active', 'inactive', 'all', ''}:
            status_filter = 'active'

        filtered = []
        for item in rows:
            family = families.get(item.family_id)
            category = categories.get(family.category_id) if family else None
            cycle = statuses.get(item.cycle_status_id)
            family_name = getattr(family, 'name', '') or ''
            family_label = _family_label(family) or ''
            internal_code = item.internal_code or ''

            if search_term:
                haystack = search_text(internal_code, family_label, family_name, getattr(family, 'code', ''))
                if search_term not in haystack:
                    continue
            if type_filter and (not category or category.code.lower() != type_filter):
                continue
            if cycle_filter and (not cycle or cycle.code.lower() != cycle_filter):
                continue
            if status_filter == 'active' and not item.active:
                continue
            if status_filter == 'inactive' and item.active:
                continue

            filtered.append({
                'id': str(item.id),
                'internal_code': internal_code or str(item.id),
                'instrument_type_label': _label(_category_label(category, N_('Uncategorized'))),
                'instrument_type_code': getattr(category, 'code', '') or '',
                'instrument_family_name': _label(family_label),
                'cycle_status_label': _label(_cycle_status_label(cycle)),
                'cycle_status_variant': _variant(getattr(cycle, 'name', None)),
                'active_status_label': N_('Active') if item.active else N_('Inactive'),
                'active_status_variant': 'success' if item.active else 'neutral',
                'edit_url': f'/admin/instruments/{item.id}/edit',
            })

        total_count = len(filtered)
        total_pages = max(1, (total_count + per_page_value - 1) // per_page_value) if total_count else 1
        current_page = _clamp_page(page, total_pages)
        start = (current_page - 1) * per_page_value
        page_rows = filtered[start:start + per_page_value]

        type_options = [
            {'value': item.code, 'label': _category_label(item)}
            for item in sorted(
                categories.values(),
                key=lambda row: (
                    CATEGORY_PURPOSE_ORDER.index(row.code)
                    if row.code in CATEGORY_PURPOSE_ORDER
                    else 999,
                    row.name,
                ),
            )
        ]
        cycle_options = sorted(
            ({'value': item.code, 'label': _cycle_status_label(item)} for item in statuses.values()),
            key=lambda option: option['label'],
        )

        return {
            'instruments': page_rows,
            'instruments_shown_count': len(page_rows),
            'instruments_total_count': total_count,
            'current_page': current_page,
            'total_pages': total_pages,
            'per_page': per_page_value,
            'filters': {
                'search': search or '',
                'type': type_filter,
                'cycle_status': cycle_filter,
                'status': status_filter if status_filter else 'all',
            },
            'instrument_type_filter': type_options,
            'cycle_status_filter': cycle_options,
            'available_cycle_codes': set(status_by_code),
        }

    return _read(query, {
        'instruments': [],
        'instruments_shown_count': 0,
        'instruments_total_count': 0,
        'current_page': 1,
        'total_pages': 1,
        'per_page': _normalize_per_page(per_page),
        'filters': {
            'search': search or '',
            'type': type_code or '',
            'cycle_status': cycle_status or '',
            'status': status or 'active',
        },
        'instrument_type_filter': [],
        'cycle_status_filter': [],
        'available_cycle_codes': set(),
    })


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
            'name': _kit_label(item),
            'version': item.version,
            'instrument_family_count': families[item.id],
            'total_expected_instruments': totals[item.id],
            'status_label': N_('Active') if item.active else N_('Inactive'),
            'status_variant': 'success' if item.active else 'neutral',
            'edit_url': f'/admin/kits/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


def kits_query_page(
    search='',
    procedure_type='',
    status='active',
    page=1,
    per_page=20,
    active_only=False,
):
    """Filtered/paginated kits list; SPD can force active_only=True."""

    def query():
        per_page_value = _normalize_per_page(per_page)
        rows = db.session.execute(select(Kit).order_by(Kit.name, Kit.version.desc())).scalars().all()
        item_rows = db.session.execute(select(KitItem)).scalars().all()
        totals = Counter()
        families = Counter()
        for item in item_rows:
            totals[item.kit_id] += item.quantity
            families[item.kit_id] += 1

        procedure_types = {
            item.id: item
            for item in db.session.execute(select(CatProcedureType)).scalars()
        }
        kit_procedures = {}
        for pk in db.session.execute(select(ProcedureKit)).scalars():
            procedure = procedure_types.get(pk.procedure_type_id)
            if procedure is None:
                continue
            kit_procedures.setdefault(pk.kit_id, []).append(procedure)

        search_term = (search or '').strip().lower()
        procedure_filter = (procedure_type or '').strip().lower()
        status_filter = 'active' if active_only else (status or '').strip().lower()
        if status_filter not in {'active', 'inactive', 'all', ''}:
            status_filter = 'active'

        filtered = []
        for item in rows:
            linked = kit_procedures.get(item.id, [])
            procedure_labels = [_procedure_label(proc) for proc in linked]
            procedure_codes = {proc.code.lower() for proc in linked}
            procedure_ids = {str(proc.id) for proc in linked}
            kit_label = _kit_label(item)

            if search_term:
                haystack = search_text(kit_label, item.name, *procedure_labels,
                                       *(proc.name for proc in linked), *procedure_codes)
                if search_term not in haystack:
                    continue
            if procedure_filter:
                if procedure_filter not in procedure_codes and procedure_filter not in procedure_ids:
                    continue
            if status_filter == 'active' and not item.active:
                continue
            if status_filter == 'inactive' and item.active:
                continue

            filtered.append({
                'id': str(item.id),
                'name': kit_label,
                'version': item.version,
                'instrument_family_count': families[item.id],
                'total_expected_instruments': totals[item.id],
                'procedure_types_label': ', '.join(procedure_labels) or '—',
                'status_label': N_('Active') if item.active else N_('Inactive'),
                'status_variant': 'success' if item.active else 'neutral',
                'edit_url': f'/admin/kits/{item.id}/edit',
            })

        total_count = len(filtered)
        total_pages = max(1, (total_count + per_page_value - 1) // per_page_value) if total_count else 1
        current_page = _clamp_page(page, total_pages)
        start = (current_page - 1) * per_page_value
        page_rows = filtered[start:start + per_page_value]

        procedure_options = sorted(
            ({'value': item.code, 'label': _procedure_label(item)} for item in procedure_types.values()),
            key=lambda option: option['label'],
        )

        return {
            'kits': page_rows,
            'kits_shown_count': len(page_rows),
            'kits_total_count': total_count,
            'current_page': current_page,
            'total_pages': total_pages,
            'per_page': per_page_value,
            'filters': {
                'search': search or '',
                'procedure_type': procedure_filter,
                'status': status_filter if status_filter else 'all',
            },
            'procedure_type_filter': procedure_options,
            'active_only': active_only,
        }

    return _read(query, {
        'kits': [],
        'kits_shown_count': 0,
        'kits_total_count': 0,
        'current_page': 1,
        'total_pages': 1,
        'per_page': _normalize_per_page(per_page),
        'filters': {
            'search': search or '',
            'procedure_type': procedure_type or '',
            'status': 'active' if active_only else (status or 'active'),
        },
        'procedure_type_filter': [],
        'active_only': active_only,
    })


def stations_data():
    def query():
        rooms = {item.id: item.code for item in db.session.execute(select(OperatingRoom)).scalars()}
        rows = db.session.execute(select(CaptureStation).order_by(CaptureStation.name)).scalars().all()
        return [{
            'id': str(item.id),
            'name': _station_label(item),
            'operating_room': rooms.get(item.room_id, N_('Unassigned')),
            'status_label': N_('Active') if item.active else N_('Inactive'),
            'status_variant': 'success' if item.active else 'neutral',
            'edit_url': f'/admin/configuration/capture-stations/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


def operating_rooms_data():
    return _read(lambda: [{
        'id': str(item.id),
        'code': item.code,
        'name': _room_label(item),
        'status_label': N_('Active') if item.active else N_('Inactive'),
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
            'categories': [
                {'value': str(item.id), 'label': _category_label(item)}
                for item in db.session.execute(select(CatInstrumentCategory).order_by(CatInstrumentCategory.name)).scalars()
            ],
        }
    return _read(query, {'instrument_family': {}, 'categories': []})


def _family_active_available_counts():
    """Count active instruments currently in Available cycle status, per family."""
    available = db.session.scalar(
        select(CatInstrumentCycleStatus).where(CatInstrumentCycleStatus.code == 'available')
    )
    if available is None:
        return {}
    rows = db.session.execute(
        select(Instrument.family_id, func.count(Instrument.id))
        .where(Instrument.active.is_(True), Instrument.cycle_status_id == available.id)
        .group_by(Instrument.family_id)
    ).all()
    return {family_id: count for family_id, count in rows}


def _category_purpose_rank(code):
    try:
        return CATEGORY_PURPOSE_ORDER.index(code)
    except ValueError:
        return len(CATEGORY_PURPOSE_ORDER)


def instrument_form_data(instrument_id=None):
    def query():
        instrument = db.session.get(Instrument, instrument_id) if instrument_id else None
        families = db.session.execute(select(InstrumentFamily).order_by(InstrumentFamily.name)).scalars().all()
        statuses = db.session.execute(select(CatInstrumentCycleStatus).order_by(CatInstrumentCycleStatus.name)).scalars().all()
        status_by_id = {item.id: item for item in statuses}
        status_codes = {item.id: item.code for item in statuses}
        cycle_events = []
        if instrument:
            events = db.session.execute(
                select(InstrumentCycleEvent)
                .where(InstrumentCycleEvent.instrument_id == instrument.id)
                .order_by(InstrumentCycleEvent.occurred_at.desc())
                .limit(10)
            ).scalars().all()
            for event in events:
                status = status_by_id.get(event.cycle_status_id)
                cycle_events.append({
                    'occurred_at': event.occurred_at.strftime('%Y-%m-%d %H:%M') if event.occurred_at else '',
                    'cycle_status_label': _cycle_status_label(status) if status else gettext('Unknown'),
                    'notes': event.notes or '',
                })
        return {
            'instrument': {
                'internal_code': instrument.internal_code if instrument else '',
                'instrument_family': str(instrument.family_id) if instrument else '',
                'cycle_status': status_codes.get(instrument.cycle_status_id, '') if instrument else 'available',
                'active_status': 'active' if not instrument or instrument.active else 'inactive',
            },
            'instrument_families': [{'value': str(item.id), 'label': _family_label(item)} for item in families],
            'cycle_statuses': [{'value': item.code, 'label': _cycle_status_label(item)} for item in statuses],
            'cycle_events': cycle_events,
        }
    return _read(query, {
        'instrument': {},
        'instrument_families': [],
        'cycle_statuses': [],
        'cycle_events': [],
    })


def kit_form_data(kit_id=None):
    def query():
        kit = db.session.get(Kit, kit_id) if kit_id else None
        families = db.session.execute(select(InstrumentFamily).order_by(InstrumentFamily.name)).scalars().all()
        categories = {
            item.id: item
            for item in db.session.execute(select(CatInstrumentCategory)).scalars()
        }
        active_available = _family_active_available_counts()
        family_options = []
        for family in families:
            category = categories.get(family.category_id)
            category_code = category.code if category else ''
            family_options.append({
                'value': str(family.id),
                'label': _family_label(family),
                'category_code': category_code,
                'category_label': _category_label(category),
                'category_rank': _category_purpose_rank(category_code),
                'active_available_count': active_available.get(family.id, 0),
            })
        family_options.sort(key=lambda item: (item['category_rank'], item['label']))

        composition = []
        if kit:
            items = db.session.execute(select(KitItem).where(KitItem.kit_id == kit.id)).scalars().all()
            for item in items:
                family = db.session.get(InstrumentFamily, item.family_id)
                category = categories.get(family.category_id) if family else None
                category_code = category.code if category else ''
                composition.append({
                    'instrument_family_value': str(item.family_id),
                    'instrument_family_label': _family_label(family) if family else gettext('Unknown family'),
                    'expected_quantity': item.quantity,
                    'category_code': category_code,
                    'category_label': _category_label(category),
                    'category_rank': _category_purpose_rank(category_code),
                    'active_available_count': active_available.get(item.family_id, 0),
                })
            composition.sort(key=lambda row: (row['category_rank'], row['instrument_family_label']))

        return {
            'kit': {
                'name': kit.name if kit else '',
                'version': kit.version if kit else 1,
                'status': 'active' if not kit or kit.active else 'inactive',
            },
            'instrument_families': family_options,
            'kit_composition': composition,
            'category_purpose_order': list(CATEGORY_PURPOSE_ORDER),
        }
    return _read(query, {
        'kit': {},
        'instrument_families': [],
        'kit_composition': [],
        'category_purpose_order': list(CATEGORY_PURPOSE_ORDER),
    })


def procedures_data():
    def query():
        rows = db.session.execute(select(CatProcedureType).order_by(CatProcedureType.code)).scalars().all()
        kits_by_procedure = {}
        default_kit_by_procedure = {}
        for pk in db.session.execute(select(ProcedureKit)).scalars():
            kits_by_procedure.setdefault(pk.procedure_type_id, []).append(pk)
            if pk.is_default:
                kit = db.session.get(Kit, pk.kit_id)
                default_kit_by_procedure[pk.procedure_type_id] = _kit_label(kit) if kit else ''
        phases_by_procedure = {}
        phase_names = {item.id: _phase_label(item) for item in db.session.execute(select(CatOperationPhase)).scalars()}
        for pp in db.session.execute(select(ProcedurePhase).order_by(ProcedurePhase.sort_order)).scalars():
            phases_by_procedure.setdefault(pp.procedure_type_id, []).append(phase_names.get(pp.phase_id, ''))
        return [{
            'id': str(item.id),
            'code': item.code,
            'name': _procedure_label(item),
            'associated_kits_count': len(kits_by_procedure.get(item.id, [])),
            'default_kit_name': default_kit_by_procedure.get(item.id, N_('Not set')),
            'counting_phases_label': ', '.join(phases_by_procedure.get(item.id, [])) or N_('Not configured'),
            'edit_url': f'/admin/procedures/{item.id}/edit',
        } for item in rows]
    return _read(query, [])


def procedure_form_data(procedure_id=None):
    def query():
        procedure = db.session.get(CatProcedureType, procedure_id) if procedure_id else None
        kits = db.session.execute(
            select(Kit).where(Kit.active.is_(True)).order_by(Kit.name)
        ).scalars().all()
        phases = db.session.execute(select(CatOperationPhase).order_by(CatOperationPhase.name)).scalars().all()
        associated_kits = []
        counting_phases = []
        if procedure:
            for pk in db.session.execute(select(ProcedureKit).where(ProcedureKit.procedure_type_id == procedure.id)).scalars():
                kit = db.session.get(Kit, pk.kit_id)
                associated_kits.append({
                    'kit_value': str(pk.kit_id),
                    'kit_label': _kit_label(kit) if kit else gettext('Unknown kit'),
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
                    'phase_label': _phase_label(phase) if phase else gettext('Unknown phase'),
                    'is_count_required': pp.is_count_required,
                    'sort_order': pp.sort_order,
                    'active': pp.active,
                })
        return {
            'procedure': {
                'code': procedure.code if procedure else '',
                'name': procedure.name if procedure else '',
            },
            'kits': [{'value': str(item.id), 'label': _kit_label(item)} for item in kits],
            'phases': [{'value': str(item.id), 'label': _phase_label(item)} for item in phases],
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
            'classes_label': ngettext('%(num)d Class', '%(num)d Classes', class_counts.get(item.id, 0)),
            'status_label': N_('Active Model') if item.active else N_('Inactive'),
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
                    'instrument_family_label': _family_label(family) if family else gettext('Unknown family'),
                })
        return {
            'vision_model': {
                'version_tag': model.version_tag if model else '',
                'model_asset_reference': media_asset.object_key if media_asset else '',
                'active': model.active if model else False,
                'checksum': model.checksum if model else '',
                'published_at': model.published_at.strftime('%Y-%m-%d') if model and model.published_at else '',
            },
            'instrument_families': [{'value': str(item.id), 'label': _family_label(item)} for item in families],
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
            role_label = ' / '.join(_role_label(code, description) for code, description in assigned)
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
            'roles': [{'value': role.code, 'label': _role_label(role.code, role.description)} for role in roles],
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
    if isinstance(value, str):
        value = parse_iso(value)
    if value.tzinfo is None:
        return value.strftime('%Y-%m-%d %H:%M')
    return value.strftime('%Y-%m-%d %H:%M')


def _ns(data):
    """Snapshot reference ({id, code, name...}) as an object for the label helpers above."""
    return SimpleNamespace(**data) if data else None


def _build_session_row(snap, for_role='supervisor'):
    """RF list row of one session snapshot (services/session_snapshot.py; local or SessionService)."""
    status = snap.get('status') or {}
    status_code = status.get('code') or 'unknown'
    status_label, status_variant = SESSION_STATUS_UI.get(
        status_code,
        (localize_db_label('session_status', status_code, status.get('name')) if status else N_('Unknown'), 'neutral'),
    )
    open_discrepancies = snap.get('open_discrepancies') or 0
    has_agreement = bool(snap.get('has_privacy_agreement'))
    capture_mode = snap.get('capture_mode')
    if capture_mode == 'vision' or (capture_mode is None and has_agreement):
        privacy_label = N_('Notice OK · Vision')
        privacy_variant = 'success'
    else:
        privacy_label = N_('No notice · Manual')
        privacy_variant = 'warning'

    operation = snap.get('operation') or {}
    room = operation.get('room')
    room_code = room['code'] if room else '—'

    scheduled_at = _fmt_dt(operation.get('scheduled_at')) if operation else ''
    created_at = scheduled_at or _fmt_dt(snap.get('started_at')) or _fmt_dt(snap.get('updated_at'))

    procedure = _ns(operation.get('procedure_type'))
    kit = _ns(snap.get('kit'))
    station = _ns(snap.get('station'))
    procedure_name = _procedure_label(procedure) or gettext('Unspecified procedure')
    kit_name = _kit_label(kit) or gettext('Unspecified kit')
    station_name = _station_label(station) or gettext('Unspecified station')
    session_id = snap['id']

    if for_role == 'operator':
        if status_code == 'scheduled':
            action_label, action_url = N_('Begin'), f'/operator/sessions/{session_id}/begin'
        elif status_code == 'in_progress':
            if capture_mode == 'manual_no_privacy':
                action_label, action_url = N_('Continue'), f'/operator/sessions/{session_id}/manual'
            else:
                action_label, action_url = N_('Continue'), f'/operator/sessions/{session_id}/capture'
        else:
            action_label, action_url = N_('View details'), f'/operator/sessions/{session_id}'
        details_url = f'/operator/sessions/{session_id}'
    else:
        if status_code == 'correction_required' or open_discrepancies > 0:
            action_label, action_url = N_('Review'), f'/supervisor/discrepancies/{session_id}/review'
        elif status_code == 'awaiting_spd_review':
            action_label, action_url = N_('Confirm close'), f'/supervisor/sessions/{session_id}'
        else:
            action_label, action_url = N_('View'), f'/supervisor/sessions/{session_id}'
        details_url = f'/supervisor/sessions/{session_id}'

    operator = snap.get('operator') or {}
    return {
        'id': session_id,
        'session_id': display_session_id(session_id),
        'status_code': status_code,
        'capture_mode': capture_mode,
        'has_privacy_agreement': has_agreement,
        'privacy_label': privacy_label,
        'privacy_variant': privacy_variant,
        'procedure_name': procedure_name,
        'procedure_label': procedure_name,
        'operating_room': room_code,
        'kit_name': kit_name,
        'operator_name': operator.get('name') or N_('Unknown operator'),
        'capture_station_name': station_name,
        # Stable filter values (display names change with the UI language).
        'procedure_type_id': procedure.id if procedure else '',
        'kit_id': kit.id if kit else '',
        'station_id': station.id if station else '',
        # Search matches what is shown, what is stored and the catalog code.
        'search_text': search_text(
            display_session_id(session_id), procedure_name, getattr(procedure, 'name', ''),
            getattr(procedure, 'code', ''), kit_name, getattr(kit, 'name', ''),
            station_name, getattr(station, 'name', ''),
        ),
        'created_at': created_at,
        'scheduled_at': scheduled_at,
        'started_at': _fmt_dt(snap.get('started_at')),
        'closed_at': _fmt_dt(snap.get('ended_at')),
        'submitted_at': _fmt_dt(snap.get('updated_at')),
        'status_label': status_label,
        'status_variant': status_variant if open_discrepancies == 0 else 'warning',
        'discrepancy_count': open_discrepancies,
        'action_label': action_label,
        'action_url': action_url,
        'details_url': details_url,
    }


def _tenant_uuid(institution_id):
    """Normalize the authenticated user's institution id; None means "no tenant" (deny)."""
    if institution_id is None:
        return None
    try:
        return _UUID(str(institution_id))
    except (TypeError, ValueError):
        return None


def _session_in_tenant(item, institution_id):
    """tenant(session) = operation.institution_id (sessions without operation have no tenant)."""
    if item is None or item.operation_id is None or institution_id is None:
        return False
    operation = db.session.get(Operation, item.operation_id)
    return operation is not None and operation.institution_id == institution_id


def _tenant_session_ids(institution_id):
    return (
        select(WorkSession.id)
        .join(Operation, Operation.id == WorkSession.operation_id)
        .where(Operation.institution_id == institution_id)
    )


def _session_rows(for_role='supervisor', user_id=None, *, institution_id):
    tenant = _tenant_uuid(institution_id)
    if tenant is None:
        return []
    return session_rows_from_snapshots(session_snapshot.list_sessions(tenant, user_id), for_role=for_role)


def session_rows_from_snapshots(snapshots, for_role='supervisor'):
    return [_build_session_row(snap, for_role=for_role) for snap in snapshots]


def sessions_data(institution_id):
    return _read(lambda: _session_rows(for_role='supervisor', institution_id=institution_id), [])


def operator_my_sessions(user_id, institution_id):
    from uuid import UUID
    try:
        parsed = UUID(str(user_id))
    except (TypeError, ValueError):
        return []
    return _read(
        lambda: _session_rows(for_role='operator', user_id=parsed, institution_id=institution_id),
        [],
    )


def session_detail_from_snapshot(snap, *, for_role):
    """RF detail payload of a session snapshot (same output for the local and remote sources)."""
    row = _build_session_row(snap, for_role=for_role)
    phase = _ns(snap.get('phase'))
    row['phase_code'] = phase.code if phase else None
    row['phase_label'] = _phase_label(phase)
    capture_mode = snap.get('capture_mode')
    # Three separate facts per family: expected (expected_inventory), AI (auto_count) and
    # human (manual_count). "reported" keeps its previous meaning: the human count, or the
    # latest AI tally while no human count exists (legacy sessions closed without a report).
    human_by_family = {}
    ai_by_family = {}
    final_ai_by_family = None
    events = snap.get('events') or []
    for event in events:
        payload = event.get('payload') or {}
        code = event['event_code']
        if code == 'manual_count' and event.get('family_id'):
            reported = payload.get('reported_quantity')
            if reported is None:
                reported = event.get('detected_quantity')
            human_by_family[event['family_id']] = reported
        elif code == 'auto_count' and event.get('family_id'):
            # Legacy per-family AI rows.
            ai_by_family[event['family_id']] = event.get('detected_quantity')
        elif code == 'auto_count' and payload.get('scope') == 'board':
            # Board-scoped vision snapshot: last wins per family from payload.board.families.
            lot = {}
            for fam in (payload.get('board') or {}).get('families') or []:
                if fam.get('family_id'):
                    lot[str(fam['family_id'])] = fam.get('detected_quantity')
            ai_by_family.update(lot)
            if payload.get('trigger') == 'final' or payload.get('reason') == 'detection_close':
                final_ai_by_family = lot
    reported_by_family = {**ai_by_family, **human_by_family}
    if final_ai_by_family is not None:
        # DS04: the AI suggestion is the final lot; families missing from it count 0.
        ai_by_family = final_ai_by_family
    row['expected_items'] = []
    for inv in snap.get('expected_inventory') or []:
        family = SimpleNamespace(code=inv['family_code'], name=inv['family_name'])
        family_id = inv['family_id']
        expected_quantity = inv['expected_quantity']
        reported = reported_by_family.get(family_id)
        diff = None if reported is None else int(reported) - expected_quantity
        if reported is None:
            status_label, status_variant = N_('Pending'), 'neutral'
        elif diff == 0:
            status_label, status_variant = N_('Match'), 'success'
        elif diff < 0:
            status_label, status_variant = N_('Shortfall'), 'danger'
        else:
            status_label, status_variant = N_('Extra'), 'warning'
        category = _ns(inv.get('category'))
        category_code = category.code if category else ''
        source = inv.get('source') or 'manual'
        row['expected_items'].append({
            'family_id': family_id,
            'family_name': _family_label(family),
            'family_code': family.code,
            'expected_quantity': expected_quantity,
            'reported_quantity': reported,
            'ai_detected_quantity': (
                None if capture_mode == 'manual_no_privacy'
                else ai_by_family.get(family_id, 0 if final_ai_by_family is not None else None)
            ),
            'human_quantity': human_by_family.get(family_id),
            'difference': diff,
            'status_label': status_label,
            'status_variant': status_variant,
            'source': source,
            'source_label': EXPECTED_SOURCE_LABELS.get(source, source),
            'category_code': category_code,
            'category_label': _category_label(category),
            # Stored name: VisionWorker keys category_summary by it (see vision_bridge).
            'category_name': category.name if category else '',
            'category_rank': _category_purpose_rank(category_code),
            'is_additional': source != 'kit_snapshot',
        })
    row['expected_items'].sort(
        key=lambda entry: (entry['category_rank'], entry['family_name'])
    )
    inventory_groups = {}
    for entry in row['expected_items']:
        if entry['is_additional']:
            group_key = 'additional'
            group_label = N_('Additional')
            group_rank = 999
        else:
            group_key = entry['category_code'] or 'other'
            group_label = entry['category_label']
            group_rank = entry['category_rank']
        # Use key "lines" (not "items") — Jinja dict.items is the builtin method.
        bucket = inventory_groups.setdefault(group_key, {
            'key': group_key,
            'label': group_label,
            'rank': group_rank,
            'lines': [],
        })
        bucket['lines'].append(entry)
    row['expected_groups'] = sorted(inventory_groups.values(), key=lambda g: (g['rank'], g['label']))
    family_name_by_id = {
        inv['family_id']: _family_label(SimpleNamespace(code=inv['family_code'], name=inv['family_name']))
        for inv in snap.get('expected_inventory') or []
    }
    row['timeline'] = []
    for event in events:
        payload = event.get('payload') or {}
        board = payload.get('board') if isinstance(payload.get('board'), dict) else {}
        totals = board.get('totals') if isinstance(board.get('totals'), dict) else {}
        reason = payload.get('reason')
        scope = payload.get('scope')
        event_name = localize_db_label('event_type', event['event_code'], event.get('event_name'))
        label_bits = [event_name]
        if reason:
            label_bits.append(str(reason))
        if scope == 'board':
            label_bits.append('board')
        fid = event.get('family_id')
        row['timeline'].append({
            'occurred_at': _fmt_dt(event.get('occurred_at')),
            'event_code': event['event_code'],
            'event_name': event_name,
            'event_label': ' · '.join(label_bits),
            'family_id': fid,
            'family_name': family_name_by_id.get(fid) if fid else None,
            'scope': scope,
            'reason': reason,
            'expected_quantity': event.get('expected_quantity'),
            'detected_quantity': event.get('detected_quantity'),
            'board_family_count': totals.get('family_count'),
            'frames_processed': payload.get('frames_processed'),
            'video_t': payload.get('video_t'),
            'pipeline': payload.get('pipeline'),
            'solver': (payload.get('pipeline_config') or {}).get('solver') or payload.get('solver'),
            'model_version': payload.get('model_version'),
            'payload': payload,
        })
    row['discrepancies'] = [_discrepancy_view_from_snapshot(item) for item in snap.get('discrepancies') or []]
    privacy = snap.get('privacy')
    if privacy:
        row['privacy'] = {
            'has_agreement': True,
            'version': privacy.get('version') or '—',
            'document_uri': privacy.get('document_uri'),
            'agreed_at': _fmt_dt(privacy.get('agreed_at')),
            'purpose_quality_ops': privacy.get('purpose_quality_ops'),
            'purpose_model_improvement': privacy.get('purpose_model_improvement'),
            'agreed_by': (privacy.get('agreed_by') or {}).get('name') or '—',
        }
    else:
        row['privacy'] = {
            'has_agreement': False,
            'message': N_('No privacy notice linked. Start Session will freeze capture_mode = manual_no_privacy.'),
        }

    operation = snap.get('operation') or {}
    patient = snap.get('patient')
    surgical_team = [
        {
            'physician_id': member['physician_id'],
            'name': member['name'],
            'role': localize_db_label('surgical_role', member['role_code'], member['role_name']),
            'role_code': member['role_code'],
        }
        for member in snap.get('surgical_team') or []
    ]
    row['patient_name'] = patient.get('display_name') if patient else None
    row['patient_record'] = (patient['id'].replace('-', '')[:10].upper()
                             if patient and patient.get('display_name') is not None else None)
    row['patient_id'] = patient['id'] if patient else None
    row['surgical_team'] = surgical_team
    row['physician_name'] = surgical_team[0]['name'] if surgical_team else None
    row['operator_user_id'] = (snap.get('operator') or {}).get('id') or ''
    row['procedure_type_id'] = (operation.get('procedure_type') or {}).get('id') or ''
    row['room_id'] = (operation.get('room') or {}).get('id') or ''
    row['station_id'] = (snap.get('station') or {}).get('id') or ''
    row['kit_id'] = (snap.get('kit') or {}).get('id') or ''
    scheduled = parse_iso(operation.get('scheduled_at'))
    if scheduled is not None:
        row['scheduled_at_local'] = scheduled.astimezone().replace(tzinfo=None).strftime('%Y-%m-%dT%H:%M')
    else:
        row['scheduled_at_local'] = ''
    row['can_edit_inventory'] = row.get('status_code') in {'scheduled', 'in_progress'}
    row['can_edit_session_fields'] = row.get('status_code') == 'scheduled'
    return row


def operator_session_detail(session_id, user_id, institution_id):
    """Operator screens: always the local snapshot (PostgreSQL), never SessionService."""
    from uuid import UUID

    def query():
        try:
            parsed_session = UUID(str(session_id))
            parsed_user = UUID(str(user_id))
        except (TypeError, ValueError):
            return None
        tenant = _tenant_uuid(institution_id)
        if tenant is None:
            return None
        snap = session_snapshot.session_detail(parsed_session, tenant)
        # Assigned to this operator AND inside the operator's tenant; otherwise "not found".
        if snap is None or (snap.get('operator') or {}).get('id') != str(parsed_user):
            return None
        return session_detail_from_snapshot(snap, for_role='operator')

    return _read(query, None)


def supervisor_session_detail(session_id, institution_id):
    """Local snapshot of a session (Supervisor screens use controllers/supervisor_view.py)."""
    from uuid import UUID

    def query():
        try:
            parsed_session = UUID(str(session_id))
        except (TypeError, ValueError):
            return None
        tenant = _tenant_uuid(institution_id)
        if tenant is None:
            return None
        snap = session_snapshot.session_detail(parsed_session, tenant)
        return session_detail_from_snapshot(snap, for_role='supervisor') if snap else None

    return _read(query, None)


def discrepancy_rows_from_snapshots(items, session_rows, search='', instrument='', status=''):
    """Supervisor discrepancy queue rows from discrepancy snapshots + the session list rows."""
    sessions = {row['id']: row for row in session_rows}
    search_term = (search or '').strip().lower()
    instrument_term = (instrument or '').strip().lower()
    status_filter = (status or '').strip().lower()
    values = []
    for item in items:
        session_row = sessions.get(item['session_id'], {})
        display_id = session_row.get('session_id', display_session_id(item['session_id']))
        family = _ns(item.get('family'))
        instrument_name = _family_label(family) or gettext('Unspecified instrument')
        reason_code = item.get('reason_code')
        reason_label = (localize_db_label('discrepancy_reason', reason_code, item.get('reason_name')) if reason_code
                        else gettext('Recorded discrepancy'))
        status_label = N_('Resolved') if item['resolved'] else N_('Open')
        status_code = 'resolved' if item['resolved'] else 'open'
        if status_filter in {'open', 'resolved'} and status_code != status_filter:
            continue
        if instrument_term and instrument_term not in (instrument_name or '').lower():
            continue
        if search_term:
            haystack = search_text(
                display_id, item['session_id'], session_row.get('search_text', ''),
                instrument_name, getattr(family, 'name', ''), getattr(family, 'code', ''),
                status_label, gettext(status_label),
            )
            if search_term not in haystack:
                continue
        values.append({
            'id': item['id'],
            'session_uuid': item['session_id'],
            'session_id': display_id,
            'procedure_name': session_row.get('procedure_name', N_('Unspecified procedure')),
            'operating_room': session_row.get('operating_room', N_('Unspecified room')),
            'kit_name': session_row.get('kit_name', N_('Unspecified kit')),
            'operator_name': session_row.get('operator_name', N_('Unknown operator')),
            'instrument_name': instrument_name,
            'expected_quantity': item.get('expected_quantity') or 0,
            'counted_quantity': item.get('detected_quantity') or 0,
            'difference': (item.get('detected_quantity') or 0) - (item.get('expected_quantity') or 0),
            'reason': reason_label,
            'status_code': status_code,
            'status_label': status_label,
            'status_variant': 'success' if item['resolved'] else 'warning',
            'action_label': N_('Review'),
            'action_url': f'/supervisor/discrepancies/{item["session_id"]}/review',
        })
    return values


def discrepancies_data(institution_id, search='', instrument='', status=''):
    def query():
        tenant = _tenant_uuid(institution_id)
        if tenant is None:
            return []
        return discrepancy_rows_from_snapshots(
            session_snapshot.list_discrepancies(tenant), _session_rows(institution_id=tenant),
            search=search, instrument=instrument, status=status,
        )
    return _read(query, [])


def session_discrepancies(session_id, institution_id):
    return _read(
        lambda: [
            item for item in discrepancies_data(institution_id)
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


def dashboard_data(role, institution_id, *, sessions=None, discrepancies=None):
    """Dashboard stats; the Supervisor passes the rows it got from SessionService."""
    from datetime import date as date_cls

    sessions = sessions_data(institution_id) if sessions is None else sessions
    discrepancies = discrepancies_data(institution_id) if discrepancies is None else discrepancies
    today = date_cls.today().isoformat()

    def _is_today(row):
        for key in ('scheduled_at', 'started_at', 'created_at', 'closed_at'):
            value = str(row.get(key) or '')
            if value.startswith(today):
                return True
        return False

    sessions_today = [row for row in sessions if _is_today(row)]
    in_progress = sum(1 for row in sessions if row.get('status_code') == 'in_progress')
    active_non_closed = sum(
        1 for row in sessions if row.get('status_code') not in {'closed', 'aborted'}
    )
    if _demo_mode():
        # An explicit zero-value stats dict is still "defined" to Jinja and would defeat the
        # template's `dashboard_stats|default({...}, true)` fallback, so hand back {} instead.
        return {}, sessions, discrepancies
    if role == 'operator':
        stats = {
            'sessions_today': len(sessions_today),
            'active_sessions': in_progress or active_non_closed,
            'closed_sessions': sum(1 for row in sessions if row.get('status_code') == 'closed'),
            'open_discrepancies': sum(item['status_label'] == 'Open' for item in discrepancies),
        }
    elif role == 'supervisor':
        stats = {
            'sessions_today': len(sessions_today) or len(sessions),
            'active_sessions': in_progress or active_non_closed,
            'pending_reviews': sum(
                1 for row in sessions if row.get('status_code') in {
                    'awaiting_spd_review', 'correction_required',
                }
            ) or sum(item['status_label'] == 'Open' for item in discrepancies),
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


def admin_dashboard_overview(institution_id):
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
        # Session-derived metrics are limited to the administrator's own institution.
        tenant = _tenant_uuid(institution_id)
        human_corrections_count = db.session.scalar(
            select(func.count(HumanCorrection.id))
            .join(CountEvent, CountEvent.id == HumanCorrection.count_event_id)
            .where(CountEvent.session_id.in_(_tenant_session_ids(tenant)))
        ) if tenant else 0
        sessions = sessions_data(tenant)
        discrepancies = discrepancies_data(tenant)

        return {
            'secondary_stats': {
                'procedures': procedures_count or 0,
                'capture_stations': stations_count or 0,
                'vision_models': vision_models_count or 0,
                'active_vision_model': active_vision_models_count,
            },
            'catalog_overview': [
                {'label': N_('Instrument Families'), 'value': gettext('%(count)s Active', count=len(families_data()))},
                {'label': N_('Instruments'), 'value': gettext('%(count)s Registered', count=len(instruments_data()))},
                {'label': N_('Kits'), 'value': gettext('%(count)s Active', count=len([item for item in kits_data() if item['status_label'] == 'Active']))},
                {'label': N_('Procedures'), 'value': gettext('%(count)s Active', count=procedures_count or 0)},
                {'label': N_('Capture Stations'), 'value': gettext('%(count)s Configured', count=stations_count or 0)},
            ],
            'system_overview': [
                {'label': N_('Inactive Users'), 'value': inactive_users_count or 0},
                {'label': N_('Configured Roles'), 'value': roles_count or 0},
                {'label': N_('Operating Rooms'), 'value': operating_rooms_count or 0},
                {'label': N_('Capture Stations'), 'value': stations_count or 0},
                {'label': N_('Active Institution'), 'value': active_institutions_count or 0},
                {'label': N_('Active Vision Model Version'), 'value': active_vision_model.version_tag if active_vision_model else gettext('None')},
                {'label': N_('Model Classes'), 'value': model_classes_count or 0},
            ],
            'operational_metrics': [
                {'label': N_('Total Sessions'), 'value': len(sessions)},
                {'label': N_('Total Discrepancies'), 'value': len(discrepancies)},
                {'label': N_('AI-Human Agreement'), 'value': '94.2%'},
                {'label': N_('Human Corrections'), 'value': human_corrections_count or 0},
                {'label': N_('Average Resolution Time'), 'value': '18 min'},
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
        action = {'label': N_('Review'), 'url': review_url, 'style': 'primary'}
    elif code == 'reviewed_unresolved':
        action = {'label': N_('Continue Review'), 'url': review_url, 'style': 'primary'}
    else:
        action = {'label': N_('View Details'), 'url': details_url, 'style': 'secondary'}
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
            'user': event['actor'] or N_('System'),
            'role': event['actor_role'],
            'event': event['code'],
            'entity': event['entity'],
            'record': demo_data.WS026_ID,
            'result': 'SUCCESS',
            'details': event['detail'],
        })
    return rows


# --- Supervisor Audit Log (RF, access_audit) ----------------------------------------------
# Operational traceability only: what an SPD Supervisor works on (counting sessions and the
# instruments / kits SPD may edit). Security and administration events (users, roles,
# vision models, configuration) stay in the Administrator audit log.
SUPERVISOR_AUDIT_RESOURCES = ('work_session', 'instrument', 'kit')
SUPERVISOR_AUDIT_ENTITY_LABELS = {
    'work_session': N_('Counting Session'),
    'instrument': N_('Instrument'),
    'kit': N_('Kit'),
}
SUPERVISOR_AUDIT_ROLE_LABELS = {
    'it_admin': N_('Administrator'),
    'spd_supervisor': N_('SPD Supervisor'),
    'station_operator': N_('Station Operator'),
}
SUPERVISOR_AUDIT_DATES = (
    ('', N_('All Dates')),
    ('today', N_('Today')),
    ('7d', N_('Last 7 Days')),
    ('30d', N_('Last 30 Days')),
)
_SUPERVISOR_AUDIT_DAYS = {'today': 0, '7d': 6, '30d': 29}


def supervisor_audit_log_data(institution_id, *, search='', user='', event='', session='', date='',
                              page=1, per_page=20):
    """Real access_audit rows of the caller's institution for the Supervisor Audit Log."""
    from sqlalchemy import text as sql

    per_page = _normalize_per_page(per_page, allowed=(10, 20, 50), default=20)
    empty = {'rows': [], 'total': 0, 'page': 1, 'pages': 1, 'per_page': per_page,
             'options': {'users': [], 'events': [], 'sessions': []}}
    if institution_id is None:
        return empty
    tz = current_app.config.get('ADMIN_DISPLAY_TIMEZONE') or 'UTC'
    scope = 'a.institution_id = :inst AND a.resource_type IN :resources'
    params = {'inst': institution_id, 'resources': SUPERVISOR_AUDIT_RESOURCES, 'tz': tz}
    conditions = [scope]
    user_id = _uuid_or_none(user)
    if user_id:
        conditions.append('a.actor_user_id = :user_id')
        params['user_id'] = user_id
    if event:
        conditions.append('a.action = :event')
        params['event'] = event
    session_id = _uuid_or_none(session)
    if session_id:
        conditions.append("a.resource_type = 'work_session' AND a.resource_id = :session_id")
        params['session_id'] = session_id
    if date in _SUPERVISOR_AUDIT_DAYS:
        conditions.append("(a.occurred_at AT TIME ZONE :tz)::date >= (now() AT TIME ZONE :tz)::date - :days")
        params['days'] = _SUPERVISOR_AUDIT_DAYS[date]
    if search:
        conditions.append(
            "(a.action ILIKE :search OR u.name ILIKE :search OR u.email ILIKE :search "
            "OR a.resource_id::text ILIKE :search "
            "OR ('WS-' || upper(left(replace(a.resource_id::text, '-', ''), 8))) ILIKE :search)"
        )
        params['search'] = f'%{search.strip()}%'
    where = ' AND '.join(conditions)

    def statement(query):
        from sqlalchemy import bindparam
        return sql(query).bindparams(bindparam('resources', expanding=True))

    def query():
        total = db.session.execute(statement(
            f'SELECT count(*) FROM access_audit a LEFT JOIN "user" u ON u.id = a.actor_user_id WHERE {where}'
        ), params).scalar() or 0
        pages = max(1, (total + per_page - 1) // per_page)
        current = _clamp_page(page, pages)
        rows = db.session.execute(statement(f"""
            SELECT a.occurred_at, to_char(a.occurred_at AT TIME ZONE :tz, 'YYYY-MM-DD HH24:MI:SS') AS occurred_local,
                   a.action, a.resource_type, a.resource_id, a.outcome, host(a.ip) AS ip,
                   u.name AS user_name,
                   (SELECT string_agg(r.code, ',' ORDER BY r.code) FROM user_role ur JOIN role r ON r.id = ur.role_id
                     WHERE ur.user_id = a.actor_user_id) AS role_codes,
                   CASE a.resource_type
                       WHEN 'work_session' THEN 'WS-' || upper(left(replace(a.resource_id::text, '-', ''), 8))
                       WHEN 'instrument' THEN (SELECT COALESCE(i.internal_code, i.id::text) FROM instrument i WHERE i.id = a.resource_id)
                       WHEN 'kit' THEN (SELECT k.name || ' v' || k.version FROM kit k WHERE k.id = a.resource_id)
                   END AS record_label
            FROM access_audit a LEFT JOIN "user" u ON u.id = a.actor_user_id
            WHERE {where}
            ORDER BY a.occurred_at DESC, a.id
            LIMIT :limit OFFSET :offset
        """), {**params, 'limit': per_page, 'offset': (current - 1) * per_page}).mappings().all()
        scope_params = {key: params[key] for key in ('inst', 'resources')}
        options = {
            'users': [{'value': str(r[0]), 'label': r[1] or r[2]} for r in db.session.execute(statement(
                'SELECT DISTINCT u.id, u.name, u.email FROM access_audit a JOIN "user" u ON u.id = a.actor_user_id '
                f'WHERE {scope} ORDER BY u.name'), scope_params).all()],
            'events': [r[0] for r in db.session.execute(statement(
                f'SELECT DISTINCT a.action FROM access_audit a WHERE {scope} ORDER BY 1'), scope_params).all()],
            'sessions': [{'value': str(r[0]), 'label': display_session_id(r[0])} for r in db.session.execute(statement(
                f"SELECT a.resource_id, max(a.occurred_at) FROM access_audit a WHERE {scope} "
                "AND a.resource_type = 'work_session' GROUP BY a.resource_id ORDER BY 2 DESC LIMIT 200"),
                scope_params).all()],
        }
        values = []
        for row in rows:
            roles = [code for code in (row['role_codes'] or '').split(',') if code]
            denied = row['outcome'] == 'denied'
            values.append({
                'time': row['occurred_local'],
                'user': row['user_name'] or N_('System'),
                'role': ', '.join(gettext(SUPERVISOR_AUDIT_ROLE_LABELS.get(code, code)) for code in roles) or '—',
                'event': row['action'],
                'entity': SUPERVISOR_AUDIT_ENTITY_LABELS.get(row['resource_type'], row['resource_type']),
                'record': row['record_label'] or str(row['resource_id'])[:8].upper(),
                'result': 'DENIED' if denied else 'SUCCESS',
                'result_variant': 'danger' if denied else 'success',
                'details': row['ip'] or '',
            })
        return {'rows': values, 'total': total, 'page': current, 'pages': pages, 'per_page': per_page,
                'options': options}

    return _read(query, empty)


def _uuid_or_none(value):
    try:
        return _UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


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
            'privacy_label': item.get('privacy_label') or '—',
            'privacy_variant': item.get('privacy_variant') or 'neutral',
            'capture_mode': item.get('capture_mode') or '—',
            'issue_label': ngettext('%(num)d open discrepancy', '%(num)d open discrepancies', open_count)
                           if open_count else '—',
            'issue_detail': '',
            'session_status': {'code': item['status_code'], 'label': item['status_label'],
                               'variant': item['status_variant']},
            'kit_id': item.get('kit_id', ''),
            'search_text': item.get('search_text', ''),
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
            'action': {'label': N_('View Details') if resolved else N_('Review'), 'url': item['action_url'],
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


def supervisor_rf_filter_options(sessions):
    """Dropdown options derived from live RF session rows (not V3 fixture names)."""
    # Kit filter: value = kit id (stable across languages), label = localized kit name.
    kits = sorted(
        {(row['kit_id'], row.get('kit_name')) for row in sessions if row.get('kit_id')},
        key=lambda kit: kit[1] or '',
    )
    operators = sorted({row.get('operator_name') for row in sessions if row.get('operator_name')})
    status_codes = []
    seen = set()
    for row in sessions:
        code = row.get('status_code')
        if code and code not in seen:
            seen.add(code)
            status_codes.append(code)
    return {
        'session_statuses': [
            {'code': code, 'label': SESSION_STATUS_UI.get(code, (code, 'neutral'))[0],
             'variant': SESSION_STATUS_UI.get(code, (code, 'neutral'))[1]}
            for code in status_codes
        ],
        'review_statuses': [demo_data.review_status(code) for code in (
            'no_review_needed', 'review_required', 'resolved',
        )],
        'kits': [{'value': kit_id, 'label': label} for kit_id, label in kits],
        'operators': operators,
        # Values double as filter codes in routes.supervisor_session_history (English).
        'dates': [N_('Today'), N_('Last 7 Days'), N_('Last 30 Days')],
        'audit_users': [],
        'audit_sessions': [],
    }


def supervisor_indicators_from_rf(sessions, discrepancies):
    """
    Minimal indicators from PG session/discrepancy rows.
    AI–Human agreement and average resolution time have no analytics query yet → n/a.
    """
    total_sessions = len(sessions)
    total_disc = len(discrepancies)
    open_disc = sum(1 for item in discrepancies if item.get('status_label') == 'Open')
    resolved_disc = sum(1 for item in discrepancies if item.get('status_label') == 'Resolved')

    by_instrument = {}
    by_type = {}
    for item in discrepancies:
        name = item.get('instrument_name') or N_('Other')
        by_instrument[name] = by_instrument.get(name, 0) + 1
        reason = item.get('reason') or N_('Other')
        by_type[reason] = by_type.get(reason, 0) + 1

    disc_by_instrument = [
        {'label': label, 'value': value, 'variant': 'warning'}
        for label, value in sorted(by_instrument.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    ]
    disc_by_type = [
        {'label': label, 'value': value, 'variant': 'neutral'}
        for label, value in sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    ]

    status_counts = {}
    for row in sessions:
        label = row.get('status_label') or row.get('status_code') or N_('Unknown')
        status_counts[label] = status_counts.get(label, 0) + 1
    sessions_by_status = [
        {'label': label, 'count': count, 'variant': 'info'}
        for label, count in sorted(status_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    return {
        'period': N_('Current seed / live data'),
        'periods': [N_('Current seed / live data')],
        'kpis': {
            'total_sessions': total_sessions,
            'total_discrepancies': total_disc,
            'ai_human_agreement': 'n/a',
            'ai_human_agreement_target': '—',
            'reviewed_unresolved': open_disc,
            'average_resolution_time': 'n/a',
        },
        'sessions_by_day': _with_fill(sessions_by_status, key='count'),
        'discrepancies_by_instrument': _with_fill(disc_by_instrument),
        'discrepancies_by_family': _with_fill([
            {'label': N_('Resolved'), 'value': resolved_disc, 'variant': 'success'},
            {'label': N_('Open'), 'value': open_disc, 'variant': 'warning'},
        ]),
        'discrepancies_by_type': _with_fill(disc_by_type),
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
            row['action'] = {'label': N_('Continue Session'),
                             'url': url_for('web.operator_session_capture', session_id=row['session_id'])}
        else:
            row['action'] = {'label': N_('Review & Start'),
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
    # Translate the msgid before filling {session}; the filled text is no longer a msgid.
    title, description = banner['title'], banner['description']
    banner['title'] = gettext(title).replace('{session}', session)
    banner['description'] = gettext(description).replace('{session}', session)
    return banner


RF_OPERATOR_ACTION_LABELS = {'Begin': N_('Review & Start'), 'Continue': N_('Continue Session'),
                             'View details': N_('View Details')}


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
            'patient_name': item.get('patient_name'),
            'operating_room': item['operating_room'],
            'kit_name': item.get('kit_name') or '—',
            'privacy_label': item.get('privacy_label') or '—',
            'privacy_variant': item.get('privacy_variant') or 'neutral',
            'scheduled_time': item.get('scheduled_at') or '—',
            'search_text': item.get('search_text', ''),
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
    ('missing', N_('Missing Instrument')),
    ('extra', N_('Extra Instrument')),
    ('misclassification', N_('AI Misclassification')),
    ('occlusion', N_('Occlusion / Poor Visibility')),
    ('manual', N_('Manual Count Correction')),
    ('other', N_('Other')),
)
_OPERATOR_V3_REASON_LABELS = dict(OPERATOR_V3_CORRECTION_REASONS)
_OPERATOR_V3_REASON_CODES = {label: code for code, label in OPERATOR_V3_CORRECTION_REASONS}
OPERATOR_V3_HV_STATUS_UI = {
    'matched': (N_('Matched'), 'success'),
    'unresolved': (N_('Unresolved'), 'danger'),
    'evidence_verification_required': (N_('Evidence Verification Required'), 'warning'),
    'evidence_verified': (N_('Evidence Verified'), 'success'),
}
OPERATOR_V3_REVIEW_CODES = ('unresolved', 'evidence_verification_required')
OPERATOR_V3_EVIDENCE_REASON = N_('Detection evidence requires verification')
OPERATOR_V3_EVIDENCE_VERIFIED_REASON = N_('Evidence verified')
OPERATOR_V3_UNSPECIFIED_REASON = N_('Not specified')


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
                errors.append(gettext('Enter a whole number from 0 to %(max)s for %(name)s.',
                                      max=OPERATOR_V3_MAX_COUNT, name=name))
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
            errors.append(gettext('Select a correction reason for %(name)s.', name=name))
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
            code, reason = 'matched', N_('None')
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
        type_label = row['correction_reason'] if difference else N_('Evidence Verification')
        if difference:
            reason_label = row['correction_reason']
            description = gettext(
                'Final validated count of %(validated)s does not match expected %(expected)s (%(reason)s).',
                validated=row['validated'], expected=row['expected_quantity'], reason=gettext(reason_label),
            )
            if row['evidence_required'] and not row['evidence_verified']:
                description += ' ' + gettext('Detection evidence also requires verification.')
            title = N_('Count discrepancy detected')
        else:
            description = (base['summary_description'] if base else
                           gettext('%(name)s counts match, but the detection evidence requires verification.',
                                   name=name))
            title = N_('Evidence verification required')
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
