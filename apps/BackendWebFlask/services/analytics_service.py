"""Dashboards / reports / indicators computed from persisted facts only (no demo values).

Scoping: WorkSession-derived facts (CountEvent, Discrepancy, HumanCorrection) are restricted to
sessions whose operator (WorkSession.user) belongs to the institution; AccessAudit by its
institution_id. Catalogs without institution_id (InstrumentFamily, CatProcedureType, YoloModel,
ModelClass) are intentionally global.

Definitions:
- AI-Human agreement: for every auto_count row that has a human final result (latest manual_count
  of its chain), AI detected == human final. N/A when nothing was reviewed.
- Average resolution time: resolved_at - creation of the discrepancy. Discrepancy has no
  created_at column; it is created in the same transaction as its origin CountEvent, so
  origin_event.occurred_at is used as the creation time. N/A when nothing is resolved.
- Human corrections: HumanCorrection rows (confirmations never create one).

Labels are localized for the current UI language by code (reason / family / role codes);
numbers, percentages and weekday names use the locale's formatting.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone

from flask_babel import gettext as _
from sqlalchemy import distinct, func, or_, select

from extensions import db
from localization.labels import format_day, format_number, format_percentage, label, role_name
from models.AccessAudit import AccessAudit
from models.CatDiscrepancyReason import CatDiscrepancyReason
from models.CatEventType import CatEventType
from models.CatSessionStatus import CatSessionStatus
from models.CountEvent import CountEvent
from models.Discrepancy import Discrepancy
from models.HumanCorrection import HumanCorrection
from models.InstrumentFamily import InstrumentFamily
from models.Kit import Kit
from models.Role import Role
from models.User import User
from models.UserRole import UserRole
from models.WorkSession import WorkSession
from services.admin_service import parse_uuid

def not_available():
    return _('N/A')
CLINICAL_ACTIONS = ('CREATE_SESSION', 'START_COUNT', 'CAPTURE_EVIDENCE', 'VISION_RESULT', 'SUBMIT_VALIDATION',
                    'HUMAN_CONFIRMATION', 'HUMAN_CORRECTION', 'CREATE_DISCREPANCY', 'REQUEST_CORRECTION',
                    'REJECT_DISCREPANCY', 'APPROVE_DISCREPANCY', 'SUPERVISOR_REVIEW', 'CLOSE_SESSION')
VARIANTS = ('danger', 'warning', 'neutral', 'info', 'success')


# --------------------------------------------------------------------------- time helpers

def utc_today_start():
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def period_start(period):
    """'today' / '7d' / '30d' -> UTC start; anything else -> None (all history)."""
    today = utc_today_start()
    return {'today': today, '7d': today - timedelta(days=6), '30d': today - timedelta(days=29)}.get(period)


def format_duration(seconds):
    if seconds is None:
        return not_available()
    minutes = seconds / 60
    if minutes < 60:
        return f"{format_number(minutes, '#,##0')} min" if minutes >= 1 else f"{format_number(seconds, '#,##0')} s"
    hours = minutes / 60
    return f'{format_number(hours)} h' if hours < 48 else f'{format_number(hours / 24)} d'


def _aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- scope

def session_scope(institution_id, start=None, kit_id=None, operator_id=None):
    """Subquery of WorkSession ids visible to the institution (+ optional filters)."""
    statement = select(WorkSession.id).join(User, User.id == WorkSession.user_id).where(User.institution_id == institution_id)
    if start is not None:
        statement = statement.where(WorkSession.started_at >= start)
    if kit_id is not None:
        statement = statement.where(WorkSession.kit_id == kit_id)
    if operator_id is not None:
        statement = statement.where(WorkSession.user_id == operator_id)
    return statement


def _type_id(code):
    row = db.session.execute(select(CatEventType.id).where(CatEventType.code == code)).first()
    return row[0] if row else None


# --------------------------------------------------------------------------- metrics

def session_metrics(scope, user_id=None):
    by_status = dict(db.session.execute(
        select(CatSessionStatus.code, func.count(WorkSession.id))
        .join(CatSessionStatus, CatSessionStatus.id == WorkSession.status_id)
        .where(WorkSession.id.in_(scope)).group_by(CatSessionStatus.code)).all())
    total = sum(by_status.values())
    today = db.session.scalar(select(func.count(WorkSession.id)).where(
        WorkSession.id.in_(scope), WorkSession.started_at >= utc_today_start()))
    with_discrepancies = db.session.scalar(select(func.count(distinct(Discrepancy.session_id))).where(Discrepancy.session_id.in_(scope)))
    with_corrections = db.session.scalar(
        select(func.count(distinct(CountEvent.session_id))).join(HumanCorrection, HumanCorrection.count_event_id == CountEvent.id)
        .where(CountEvent.session_id.in_(scope)))
    metrics = {
        'total': total, 'today': today or 0,
        'open': by_status.get('open', 0), 'counting': by_status.get('counting', 0),
        'validating': by_status.get('validating', 0), 'closed': by_status.get('closed', 0),
        'cancelled': by_status.get('cancelled', 0),
        'with_discrepancies': with_discrepancies or 0, 'without_discrepancies': total - (with_discrepancies or 0),
        'with_corrections': with_corrections or 0,
        'pending_review': db.session.scalar(select(func.count(distinct(Discrepancy.session_id))).where(
            Discrepancy.session_id.in_(scope), Discrepancy.resolved.is_(False))) or 0,
        'by_status': by_status,
    }
    if user_id is not None:
        metrics['mine'] = db.session.scalar(select(func.count(WorkSession.id)).where(
            WorkSession.id.in_(scope), WorkSession.user_id == user_id)) or 0
    return metrics


def discrepancy_metrics(scope):
    base = select(func.count(Discrepancy.id)).where(Discrepancy.session_id.in_(scope))
    total = db.session.scalar(base) or 0
    resolved = db.session.scalar(base.where(Discrepancy.resolved.is_(True))) or 0
    by_reason = [
        {'code': code, 'label': label('discrepancy_reason', code, name), 'value': value}
        for code, name, value in db.session.execute(
            select(CatDiscrepancyReason.code, CatDiscrepancyReason.name, func.count(Discrepancy.id))
            .join(CatDiscrepancyReason, CatDiscrepancyReason.id == Discrepancy.reason_id)
            .where(Discrepancy.session_id.in_(scope))
            .group_by(CatDiscrepancyReason.code, CatDiscrepancyReason.name)
            .order_by(func.count(Discrepancy.id).desc())).all()]
    by_family = [
        {'code': code, 'label': _family_label(code, name), 'value': value}
        for code, name, value in db.session.execute(
            select(InstrumentFamily.code, InstrumentFamily.name, func.count(Discrepancy.id))
            .outerjoin(InstrumentFamily, InstrumentFamily.id == Discrepancy.family_id)
            .where(Discrepancy.session_id.in_(scope))
            .group_by(InstrumentFamily.code, InstrumentFamily.name).order_by(func.count(Discrepancy.id).desc())).all()]
    reason_counts = {row['code']: row['value'] for row in by_reason}
    return {'total': total, 'resolved': resolved, 'unresolved': total - resolved,
            'shortage': reason_counts.get('shortage', 0), 'surplus': reason_counts.get('surplus', 0),
            'unidentified': reason_counts.get('unidentified', 0), 'low_confidence': reason_counts.get('low_confidence', 0),
            'by_reason': by_reason, 'by_family': by_family, 'top_families': by_family[:5]}


def _family_label(code, name):
    return label('instrument_family', code, name) if code else _('Unidentified')


def correction_metrics(scope):
    rows = db.session.execute(
        select(InstrumentFamily.code, InstrumentFamily.name, func.count(HumanCorrection.id))
        .join(CountEvent, CountEvent.id == HumanCorrection.count_event_id)
        .outerjoin(InstrumentFamily, InstrumentFamily.id == CountEvent.family_id)
        .where(CountEvent.session_id.in_(scope))
        .group_by(InstrumentFamily.code, InstrumentFamily.name).order_by(func.count(HumanCorrection.id).desc())).all()
    return {'total': sum(value for __, __, value in rows),
            'by_family': [{'code': code, 'label': _family_label(code, name), 'value': value} for code, name, value in rows]}


def agreement_metrics(scope):
    """AI detected vs latest human final per auto_count row (not vs ExpectedInventory)."""
    auto_id, manual_id = _type_id('auto_count'), _type_id('manual_count')
    if auto_id is None or manual_id is None:
        return {'reviewed': 0, 'matched': 0, 'percentage': None, 'label': not_available()}
    manual = db.session.execute(
        select(CountEvent.payload, CountEvent.detected_quantity, CountEvent.occurred_at)
        .where(CountEvent.session_id.in_(scope), CountEvent.event_type_id == manual_id)).all()
    latest = {}
    for payload, quantity, occurred_at in manual:
        root = parse_uuid((payload or {}).get('root_event_id'))
        if root is not None and (root not in latest or _aware(occurred_at) >= latest[root][1]):
            latest[root] = (quantity, _aware(occurred_at))
    if not latest:
        return {'reviewed': 0, 'matched': 0, 'percentage': None, 'label': not_available()}
    ai = dict(db.session.execute(select(CountEvent.id, CountEvent.detected_quantity).where(
        CountEvent.id.in_(list(latest)), CountEvent.event_type_id == auto_id)).all())
    reviewed = [root for root in latest if root in ai]
    matched = sum(1 for root in reviewed if (ai[root] or 0) == (latest[root][0] or 0))
    if not reviewed:
        return {'reviewed': 0, 'matched': 0, 'percentage': None, 'label': not_available()}
    percentage = matched / len(reviewed) * 100
    return {'reviewed': len(reviewed), 'matched': matched, 'percentage': percentage, 'label': format_percentage(percentage)}


def resolution_metrics(scope):
    rows = db.session.execute(
        select(Discrepancy.resolved_at, CountEvent.occurred_at)
        .join(CountEvent, CountEvent.id == Discrepancy.origin_event_id)
        .where(Discrepancy.session_id.in_(scope), Discrepancy.resolved.is_(True), Discrepancy.resolved_at.is_not(None))).all()
    durations = [(_aware(resolved) - _aware(created)).total_seconds() for resolved, created in rows if resolved and created]
    average = sum(durations) / len(durations) if durations else None
    return {'resolved_count': len(durations), 'average_seconds': average, 'label': format_duration(average)}


def sessions_by_day(scope, days=7):
    start = utc_today_start() - timedelta(days=days - 1)
    counts = Counter(_aware(value).date() for (value,) in db.session.execute(
        select(WorkSession.started_at).where(WorkSession.id.in_(scope), WorkSession.started_at >= start)).all() if value)
    return [{'label': format_day((start + timedelta(days=i)).date()), 'count': counts.get((start + timedelta(days=i)).date(), 0)}
            for i in range(days)]


def with_variants(rows):
    return [{**row, 'variant': VARIANTS[i % len(VARIANTS)]} for i, row in enumerate(rows)]


def indicators(institution_id, start=None, kit_id=None, operator_id=None):
    scope = session_scope(institution_id, start, kit_id, operator_id)
    return {'sessions': session_metrics(scope), 'discrepancies': discrepancy_metrics(scope),
            'corrections': correction_metrics(scope), 'agreement': agreement_metrics(scope),
            'resolution': resolution_metrics(scope)}


# --------------------------------------------------------------------------- audit log

def audit_entries(institution_id, filters=None, actions=None, limit=300):
    """AccessAudit of one institution, newest first; filters: search, user, event_type, outcome, date/period."""
    filters = filters or {}
    statement = select(AccessAudit).where(AccessAudit.institution_id == institution_id)
    if actions:
        statement = statement.where(AccessAudit.action.in_(actions))
    if filters.get('event_type'):
        statement = statement.where(AccessAudit.action == filters['event_type'])
    if filters.get('outcome') in ('success', 'denied'):
        statement = statement.where(AccessAudit.outcome == filters['outcome'])
    user_id = parse_uuid(filters.get('user'))
    if user_id:
        statement = statement.where(AccessAudit.actor_user_id == user_id)
    if filters.get('entity'):
        statement = statement.where(AccessAudit.resource_type == filters['entity'])
    search = (filters.get('search') or '').strip()
    if search:
        pattern = f'%{search.upper()}%'
        statement = statement.where(or_(func.upper(AccessAudit.action).like(pattern), func.upper(AccessAudit.resource_type).like(pattern)))
    start = period_start(filters.get('period'))
    if filters.get('date'):
        try:
            day = datetime.strptime(filters['date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
            statement = statement.where(AccessAudit.occurred_at >= day, AccessAudit.occurred_at < day + timedelta(days=1))
        except ValueError:
            pass
    elif start is not None:
        statement = statement.where(AccessAudit.occurred_at >= start)
    rows = db.session.execute(statement.order_by(AccessAudit.occurred_at.desc()).limit(limit)).scalars().all()
    user_ids = {row.actor_user_id for row in rows if row.actor_user_id}
    names = dict(db.session.execute(select(User.id, User.name).where(User.id.in_(user_ids))).all()) if user_ids else {}
    roles = {}
    if user_ids:
        for uid, code, description in db.session.execute(select(UserRole.user_id, Role.code, Role.description)
                                                         .join(Role, Role.id == UserRole.role_id).where(UserRole.user_id.in_(user_ids))).all():
            roles.setdefault(uid, []).append(role_name(code, description))
    return [{
        'timestamp': row.occurred_at.strftime('%Y-%m-%d %H:%M:%S') if row.occurred_at else '',
        'user_name': names.get(row.actor_user_id, _('Integration client') if row.actor_client_id else _('Unknown user')),
        'role_label': ' / '.join(roles.get(row.actor_user_id, [])),
        'event_label': row.action,  # audit event codes are shown as codes (not translated)
        'event_variant': 'success' if row.outcome == 'success' else 'danger',
        'entity_label': row.resource_type,
        'record_id': str(row.resource_id)[:8].upper(),
        'outcome': row.outcome,
        'result_label': label('audit_outcome', row.outcome, row.outcome.upper()),
        'details': ' · '.join(filter(None, [row.correlation_id, row.ip and str(row.ip)])),
    } for row in rows]


def audit_filter_options(institution_id, actions=None):
    action_statement = select(distinct(AccessAudit.action)).where(AccessAudit.institution_id == institution_id)
    if actions:
        action_statement = action_statement.where(AccessAudit.action.in_(actions))
    return {
        'users': [{'value': str(uid), 'label': name} for uid, name in db.session.execute(
            select(User.id, User.name).where(User.institution_id == institution_id).order_by(User.name)).all()],
        'event_types': sorted(db.session.execute(action_statement).scalars()),
        'entities': sorted(db.session.execute(select(distinct(AccessAudit.resource_type)).where(
            AccessAudit.institution_id == institution_id)).scalars()),
    }


def report_filter_options(institution_id):
    return {
        'kits': [{'value': str(kid), 'label': f'{name} v{version}'} for kid, name, version in db.session.execute(
            select(Kit.id, Kit.name, Kit.version).where(Kit.institution_id == institution_id).order_by(Kit.name)).all()],
        'operators': [{'value': str(uid), 'label': name} for uid, name in db.session.execute(
            select(User.id, User.name).join(WorkSession, WorkSession.user_id == User.id)
            .where(User.institution_id == institution_id).distinct().order_by(User.name)).all()],
    }
