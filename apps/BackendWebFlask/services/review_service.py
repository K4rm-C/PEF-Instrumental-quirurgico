"""Human validation, supervisor review and session closing (no schema changes).

History is append-only:
    auto_count (AI, user NULL, never updated)
      -> manual_count #1 (operator; payload.reviewed_event_id = auto_count)
      -> manual_count #2 (payload.reviewed_event_id = manual_count #1) -> ...
A HumanCorrection (count_event_id UNIQUE) is written only when a value changes and always
points to the event being corrected (the previous chain head), so chains never violate UNIQUE.
Supervisor decisions REQUEST_CORRECTION / REJECT are CountEvent `correction_requested`
(payload.discrepancy_id, decision, note); APPROVE sets discrepancy.resolved/resolved_at.
Discrepancy workflow states are always derived (get_discrepancy_workflow_state), never stored.
"""
import uuid
from datetime import datetime, timezone

from flask_babel import gettext as _
from sqlalchemy import select

from extensions import db
from localization.labels import label
from models.AccessAudit import AccessAudit
from models.CatDiscrepancyReason import CatDiscrepancyReason
from models.CatEventType import CatEventType
from models.CatSessionStatus import CatSessionStatus
from models.CountEvent import CountEvent
from models.Discrepancy import Discrepancy
from models.ExpectedInventory import ExpectedInventory
from models.HumanCorrection import HumanCorrection
from models.InstrumentFamily import InstrumentFamily
from models.User import User
from services.admin_service import ConflictError, FormError, parse_uuid
from services.audit import record_audit
from services.vision_service import latest_run_events

OPEN, UNDER_REVIEW, CORRECTION_REQUIRED, APPROVED = 'OPEN', 'UNDER_REVIEW', 'CORRECTION_REQUIRED', 'APPROVED'
# badge variant per workflow state; the label is localized by state code (localization.labels 'review_status')
STATE_VARIANTS = {OPEN: 'danger', UNDER_REVIEW: 'warning', CORRECTION_REQUIRED: 'danger', APPROVED: 'success'}
SMALLINT_MAX = 32767


def now():
    return datetime.now(timezone.utc)


def _audit(ctx):
    return {'actor': ctx['user'], 'institution_id': ctx['institution_id']}


def event_type(code):
    row = db.session.execute(select(CatEventType).where(CatEventType.code == code)).scalar_one_or_none()
    if row is None:
        raise ConflictError(_('Event type "%(code)s" is not configured. The catalogs must be updated '
                              '(data/seeds/002_seed_catalogs.sql).', code=code))
    return row


def _event_type_id(code):
    row = db.session.execute(select(CatEventType.id).where(CatEventType.code == code)).first()
    return row[0] if row else None


def status_code(work_session):
    status = db.session.get(CatSessionStatus, work_session.status_id)
    return status.code if status else ''


def _require_status(work_session, *codes):
    if status_code(work_session) not in codes:
        raise ConflictError(_('This action is not available in the current session state.'))


# --------------------------------------------------------------------------- human result chains

class SessionChains:
    """In-memory view of the session's auto_count run and every manual_count chain."""

    def __init__(self, work_session):
        self.session = work_session
        self.run = latest_run_events(work_session)
        manual_id = _event_type_id('manual_count')
        manual = db.session.execute(
            select(CountEvent).where(CountEvent.session_id == work_session.id, CountEvent.event_type_id == manual_id)
            .order_by(CountEvent.occurred_at)).scalars().all() if manual_id else []
        self.by_id = {event.id: event for event in self.run + manual}
        self.next = {}
        for event in manual:
            reviewed = parse_uuid((event.payload or {}).get('reviewed_event_id'))
            if reviewed is not None:
                self.next[reviewed] = event
        self.run_ids = {event.id for event in self.run}

    def chain(self, root):
        """[auto_count, manual_count, manual_count, ...] in order."""
        events, current = [root], root
        while current.id in self.next:
            current = self.next[current.id]
            events.append(current)
        return events

    def head(self, root):
        return self.chain(root)[-1]

    def latest_human(self, root):
        head = self.head(root)
        return head if head.id != root.id else None

    def root_of(self, event):
        if event.id in self.run_ids:
            return event
        root_id = parse_uuid((event.payload or {}).get('root_event_id'))
        return self.by_id.get(root_id) if root_id in self.run_ids else None


def get_latest_human_count(work_session, root_event, chains=None):
    """Latest manual_count of the chain started by root_event (None if no human result yet)."""
    chains = chains or SessionChains(work_session)
    return chains.latest_human(root_event)


def validation_complete(work_session, chains=None):
    chains = chains or SessionChains(work_session)
    return bool(chains.run) and all(chains.latest_human(root) is not None for root in chains.run)


def _record_human_result(chains, root, final_quantity, justification, batch_id, validation_kind, ctx, manual_type):
    """Append one manual_count to root's chain (+ HumanCorrection when the value changes)."""
    head = chains.head(root)
    previous = head.detected_quantity or 0
    changed = final_quantity != previous
    if changed and not justification:
        raise FormError(_('A justification is required for every count that differs from the value under review.'))
    recorded_at = now()
    event = CountEvent(
        session_id=chains.session.id, event_type_id=manual_type.id, user_id=parse_uuid(ctx['user'].get('id')),
        family_id=root.family_id, expected_quantity=root.expected_quantity, detected_quantity=final_quantity,
        occurred_at=recorded_at, client_event_id=uuid.uuid5(batch_id, f'row:{root.id}'),
        payload={
            'validation_batch_id': str(batch_id), 'reviewed_event_id': str(head.id), 'root_event_id': str(root.id),
            'validation_type': 'correction' if changed else 'confirmation', 'validation_kind': validation_kind,
            'previous_quantity': previous, 'final_quantity': final_quantity,
            'inference_run_id': (root.payload or {}).get('inference_run_id'), 'recorded_at': recorded_at.isoformat(),
        })
    db.session.add(event)
    db.session.flush()
    chains.next[head.id] = event
    chains.by_id[event.id] = event
    if changed:
        correction = HumanCorrection(count_event_id=head.id, user_id=event.user_id, justification=justification,
                                     recorded_at=recorded_at)
        db.session.add(correction)
        db.session.flush()
        record_audit('HUMAN_CORRECTION', 'human_correction', correction.id, **_audit(ctx))
    else:
        record_audit('HUMAN_CONFIRMATION', 'count_event', event.id, **_audit(ctx))
    return event


def _reasons():
    return {row.code: row for row in db.session.execute(select(CatDiscrepancyReason)).scalars()}


def _raise_human_discrepancy(chains, event, reasons, ctx):
    """Human final differs from expected and no equivalent unresolved discrepancy exists -> create one."""
    if event.family_id is None or event.expected_quantity is None:
        return None
    difference = event.detected_quantity - event.expected_quantity
    if difference == 0:
        return None
    code = 'shortage' if difference < 0 else 'surplus'
    if code not in reasons:
        raise ConflictError(_('Discrepancy reason "%(code)s" is not configured.', code=code))
    exists = db.session.execute(select(Discrepancy.id).where(
        Discrepancy.session_id == chains.session.id, Discrepancy.family_id == event.family_id,
        Discrepancy.reason_id == reasons[code].id, Discrepancy.resolved.is_(False)).limit(1)).first()
    if exists:
        return None
    name = db.session.get(InstrumentFamily, event.family_id).name
    discrepancy = Discrepancy(
        session_id=chains.session.id, origin_event_id=event.id, family_id=event.family_id,
        expected_quantity=event.expected_quantity, detected_quantity=event.detected_quantity,
        reason_id=reasons[code].id, resolved=False,
        description=f'{code.capitalize()} reported by operator: {name} expected {event.expected_quantity}, '
                    f'counted {event.detected_quantity} ({difference:+d}).')
    db.session.add(discrepancy)
    db.session.flush()
    record_audit('CREATE_DISCREPANCY', 'discrepancy', discrepancy.id, **_audit(ctx))
    return discrepancy


def _parse_rows(form, allowed_roots, require_all):
    batch_id = parse_uuid(form.get('validation_batch_id'))
    if batch_id is None:
        raise FormError(_('Missing validation batch identifier; reload the page and try again.'))
    rows = {}
    for raw_event, raw_final, raw_note in zip(form.getlist('event_id[]'), form.getlist('final_quantity[]'),
                                             form.getlist('justification[]')):
        root = allowed_roots.get(parse_uuid(raw_event))
        if root is None:
            raise FormError(_('One of the submitted rows does not belong to this session.'))
        if root.id in rows:
            raise FormError(_('A count was submitted twice for the same instrument.'))
        try:
            final = int(str(raw_final).strip())
        except (TypeError, ValueError):
            raise FormError(_('Every Human Final Count must be a whole number.')) from None
        if final < 0 or final > SMALLINT_MAX:
            raise FormError(_('Human Final Count must be zero or greater.'))
        rows[root.id] = (root, final, (raw_note or '').strip())
    if not rows:
        raise FormError(_('Submit at least one validated count.'))
    if require_all and set(rows) != set(allowed_roots):
        raise FormError(_('Every instrument of the AI result must be validated before continuing.'))
    return batch_id, list(rows.values())


def _batch_done(chains, batch_id, roots):
    keys = [uuid.uuid5(batch_id, f'row:{root.id}') for root in roots]
    return db.session.execute(select(CountEvent.id).where(
        CountEvent.session_id == chains.session.id, CountEvent.client_event_id.in_(keys)).limit(1)).first() is not None


def submit_validation(work_session, form, ctx):
    """Human Validation: one manual_count per AI row (all rows required). Returns False if already stored."""
    _require_status(work_session, 'validating')
    chains = SessionChains(work_session)
    if not chains.run:
        raise ConflictError(_('There is no AI result to validate for this session.'))
    batch_id, rows = _parse_rows(form, {root.id: root for root in chains.run}, require_all=True)
    if _batch_done(chains, batch_id, [row[0] for row in rows]):
        return False
    manual_type, reasons = event_type('manual_count'), _reasons()
    for root, final, note in rows:
        event = _record_human_result(chains, root, final, note, batch_id, 'validation', ctx, manual_type)
        _raise_human_discrepancy(chains, event, reasons, ctx)
    _after_validation(work_session, chains, batch_id, ctx)
    return True


def _after_validation(work_session, chains, batch_id, ctx):
    unresolved = unresolved_count(work_session)
    if validation_complete(work_session, chains) and unresolved == 0:
        passed = event_type('validation_passed')
        db.session.add(CountEvent(session_id=work_session.id, event_type_id=passed.id, user_id=parse_uuid(ctx['user'].get('id')),
                                  family_id=None, occurred_at=now(), client_event_id=uuid.uuid5(batch_id, 'validation_passed'),
                                  payload={'validation_batch_id': str(batch_id), 'unresolved_discrepancies': 0}))
    record_audit('SUBMIT_VALIDATION', 'work_session', work_session.id, **_audit(ctx))
    db.session.flush()


def submit_corrections(work_session, form, ctx):
    """Correction Requested: new manual_count for discrepancies whose state is CORRECTION_REQUIRED."""
    _require_status(work_session, 'validating')
    chains = SessionChains(work_session)
    allowed = {}
    for discrepancy in _session_discrepancies(work_session):
        if get_discrepancy_workflow_state(discrepancy, chains) == CORRECTION_REQUIRED:
            root = _root_for_discrepancy(discrepancy, chains)
            if root is not None:
                allowed[root.id] = root
    if not allowed:
        raise ConflictError(_('There are no corrections requested by the supervisor for this session.'))
    batch_id, rows = _parse_rows(form, allowed, require_all=False)
    if _batch_done(chains, batch_id, [row[0] for row in rows]):
        return False
    manual_type, reasons = event_type('manual_count'), _reasons()
    for root, final, note in rows:
        event = _record_human_result(chains, root, final, note, batch_id, 'supervisor_correction', ctx, manual_type)
        _raise_human_discrepancy(chains, event, reasons, ctx)
    _after_validation(work_session, chains, batch_id, ctx)
    return True


# --------------------------------------------------------------------------- discrepancy workflow

def _session_discrepancies(work_session):
    return db.session.execute(select(Discrepancy).where(Discrepancy.session_id == work_session.id)
                              .order_by(Discrepancy.id)).scalars().all()


def unresolved_count(work_session):
    return len([d for d in _session_discrepancies(work_session) if not d.resolved])


def _root_for_discrepancy(discrepancy, chains):
    origin = chains.by_id.get(discrepancy.origin_event_id) if discrepancy.origin_event_id else None
    if origin is None and discrepancy.origin_event_id:
        origin = db.session.get(CountEvent, discrepancy.origin_event_id)
    if origin is not None:
        root = chains.root_of(origin)
        if root is not None:
            return root
    return next((root for root in chains.run if root.family_id == discrepancy.family_id and discrepancy.family_id), None)


def supervisor_decisions(work_session, discrepancy_id=None):
    type_id = _event_type_id('correction_requested')
    if type_id is None:
        return []
    events = db.session.execute(select(CountEvent).where(
        CountEvent.session_id == work_session.id, CountEvent.event_type_id == type_id)
        .order_by(CountEvent.occurred_at)).scalars().all()
    if discrepancy_id is not None:
        events = [e for e in events if (e.payload or {}).get('discrepancy_id') == str(discrepancy_id)]
    return events


def get_discrepancy_workflow_state(discrepancy, chains=None):
    """OPEN / UNDER_REVIEW / CORRECTION_REQUIRED / APPROVED derived from persisted facts only."""
    if discrepancy.resolved:
        return APPROVED
    session = discrepancy.session
    chains = chains or SessionChains(session)
    root = _root_for_discrepancy(discrepancy, chains)
    human = chains.latest_human(root) if root is not None else None
    decisions = supervisor_decisions(session, discrepancy.id)
    last_decision = decisions[-1] if decisions else None
    if last_decision is not None and (human is None or last_decision.occurred_at >= human.occurred_at):
        return CORRECTION_REQUIRED
    if human is not None:
        return UNDER_REVIEW
    return OPEN


def _discrepancy_for_action(discrepancy):
    session = discrepancy.session
    _require_status(session, 'validating')
    if discrepancy.resolved:
        raise ConflictError(_('This discrepancy is already approved.'))
    chains = SessionChains(session)
    state = get_discrepancy_workflow_state(discrepancy, chains)
    if state != UNDER_REVIEW:
        raise ConflictError(_('The discrepancy has no new human result to review yet.'))
    root = _root_for_discrepancy(discrepancy, chains)
    return session, chains, (chains.latest_human(root) if root is not None else None)


def approve_discrepancy(discrepancy, form, ctx):
    session, __, __ = _discrepancy_for_action(discrepancy)
    discrepancy.resolved = True
    discrepancy.resolved_at = now()
    db.session.flush()
    record_audit('APPROVE_DISCREPANCY', 'discrepancy', discrepancy.id, **_audit(ctx))
    record_audit('SUPERVISOR_REVIEW', 'work_session', session.id, **_audit(ctx))


def request_discrepancy_correction(discrepancy, form, ctx, decision='request_correction'):
    session, __, human = _discrepancy_for_action(discrepancy)
    note = (form.get('note') or form.get('supervisor_notes') or form.get('reason') or '').strip()
    if not note:
        raise FormError(_('A note explaining the decision is required.'))
    if len(note) > 2000:
        raise FormError(_('The note must be at most 2000 characters.'))
    requested_at = now()
    event = CountEvent(
        session_id=session.id, event_type_id=event_type('correction_requested').id,
        user_id=parse_uuid(ctx['user'].get('id')), family_id=discrepancy.family_id,
        expected_quantity=discrepancy.expected_quantity,
        detected_quantity=human.detected_quantity if human is not None else None, occurred_at=requested_at,
        payload={'discrepancy_id': str(discrepancy.id), 'decision': decision, 'note': note,
                 'reviewed_human_event_id': str(human.id) if human is not None else None,
                 'requested_at': requested_at.isoformat()})
    db.session.add(event)
    db.session.flush()
    record_audit('REJECT_DISCREPANCY' if decision == 'reject' else 'REQUEST_CORRECTION', 'discrepancy',
                 discrepancy.id, **_audit(ctx))
    record_audit('SUPERVISOR_REVIEW', 'work_session', session.id, **_audit(ctx))


def reject_discrepancy(discrepancy, form, ctx):
    request_discrepancy_correction(discrepancy, form, ctx, decision='reject')


# --------------------------------------------------------------------------- closing

def readiness(work_session, chains=None):
    chains = chains or SessionChains(work_session)
    unresolved = unresolved_count(work_session)
    complete = validation_complete(work_session, chains)
    return {'status_ok': status_code(work_session) == 'validating', 'validation_complete': complete,
            'unresolved_discrepancies': unresolved,
            'ready': status_code(work_session) == 'validating' and complete and unresolved == 0}


def close_session(work_session, ctx):
    """Returns 'closed', 'already_closed' or raises ConflictError after persisting close_blocked."""
    if status_code(work_session) == 'closed':
        return 'already_closed'
    chains = SessionChains(work_session)
    state = readiness(work_session, chains)
    if not state['ready']:
        blocked = event_type('close_blocked')
        reasons = []
        if not state['status_ok']:
            reasons.append('session_not_validating')
        if not state['validation_complete']:
            reasons.append('human_validation_incomplete')
        if state['unresolved_discrepancies']:
            reasons.append('unresolved_discrepancies')
        db.session.add(CountEvent(session_id=work_session.id, event_type_id=blocked.id, family_id=None,
                                  user_id=parse_uuid(ctx['user'].get('id')), occurred_at=now(),
                                  payload={'reasons': reasons, 'unresolved_discrepancies': state['unresolved_discrepancies'],
                                           'attempted_at': now().isoformat()}))
        record_audit('CLOSE_SESSION', 'work_session', work_session.id, outcome='denied', **_audit(ctx))
        if state['validation_complete']:
            message = _('The session cannot be closed: %(count)s unresolved discrepancy(ies).',
                        count=state['unresolved_discrepancies'])
        else:
            message = _('The session cannot be closed: %(count)s unresolved discrepancy(ies); human validation is incomplete.',
                        count=state['unresolved_discrepancies'])
        raise SessionCloseBlocked(message)
    closed = db.session.execute(select(CatSessionStatus).where(CatSessionStatus.code == 'closed')).scalar_one_or_none()
    if closed is None:
        raise ConflictError(_('Session status "closed" is not configured. The catalogs must be updated.'))
    close_type = event_type('session_close')
    closed_at = now()
    corrections = sum(1 for root in chains.run for e in chains.chain(root)[:-1]
                      if db.session.execute(select(HumanCorrection.id).where(HumanCorrection.count_event_id == e.id)).first())
    discrepancies = _session_discrepancies(work_session)
    finals = {}
    for root in chains.run:
        head = chains.head(root)
        key = str(root.family_id) if root.family_id else f"class:{(root.payload or {}).get('yolo_class_id')}"
        finals[key] = {'expected': root.expected_quantity, 'final': head.detected_quantity, 'final_event_id': str(head.id)}
    work_session.status_id = closed.id
    work_session.ended_at = closed_at
    work_session.closed_by_user_id = parse_uuid(ctx['user'].get('id'))
    db.session.add(CountEvent(
        session_id=work_session.id, event_type_id=close_type.id, family_id=None,
        user_id=work_session.closed_by_user_id, occurred_at=closed_at,
        client_event_id=uuid.uuid5(work_session.id, 'session_close'),
        payload={'final_result_summary': finals, 'human_corrections_count': corrections,
                 'resolved_discrepancies_count': len([d for d in discrepancies if d.resolved]),
                 'closed_at': closed_at.isoformat()}))
    db.session.flush()
    record_audit('CLOSE_SESSION', 'work_session', work_session.id, **_audit(ctx))
    return 'closed'


class SessionCloseBlocked(ConflictError):
    """Close refused by the backend rule; the close_blocked event/audit must still be committed."""


# --------------------------------------------------------------------------- read model for screens

def _fmt(value):
    return value.strftime('%Y-%m-%d %H:%M:%S') if value else ''


def session_report(work_session):
    """Everything the validation / supervisor / closed screens show, straight from the database."""
    chains = SessionChains(work_session)
    families = {row.id: label('instrument_family', row.code, row.name) for row in db.session.execute(select(InstrumentFamily)).scalars()}
    users = {row.id: row.name for row in db.session.execute(select(User)).scalars()}
    reasons = {row.id: row for row in db.session.execute(select(CatDiscrepancyReason)).scalars()}
    corrections = {row.count_event_id: row for row in db.session.execute(
        select(HumanCorrection).where(HumanCorrection.count_event_id.in_(list(chains.by_id) or [uuid.uuid4()]))).scalars()}

    def instrument(root):
        return families.get(root.family_id) or _('Unidentified (YOLO class %(class_id)s)',
                                                 class_id=(root.payload or {}).get('yolo_class_id'))

    def decision_view(event):
        payload = event.payload or {}
        return {'decision': payload.get('decision'), 'decision_label': label('supervisor_decision', payload.get('decision')),
                'note': payload.get('note'), 'by': users.get(event.user_id, ''), 'at': _fmt(event.occurred_at)}

    discrepancies = _session_discrepancies(work_session)
    disc_by_root = {}
    disc_rows = []
    for d in discrepancies:
        root = _root_for_discrepancy(d, chains)
        state = get_discrepancy_workflow_state(d, chains)
        human = chains.latest_human(root) if root is not None else None
        decisions = supervisor_decisions(work_session, d.id)
        last = decisions[-1] if decisions else None
        reason = reasons.get(d.reason_id)
        row = {
            'id': str(d.id), 'root_event_id': str(root.id) if root else '', 'instrument_name': instrument(root) if root else families.get(d.family_id, ''),
            'reason_code': reason.code if reason else '',
            'reason_label': label('discrepancy_reason', reason.code, reason.name) if reason else '',
            'description': d.description, 'expected_quantity': d.expected_quantity if d.expected_quantity is not None else '—',
            'ai_detected': root.detected_quantity if root else d.detected_quantity,
            'human_final': human.detected_quantity if human else None, 'state': state,
            'state_label': label('review_status', state), 'state_variant': STATE_VARIANTS[state], 'resolved': d.resolved,
            'resolved_at': _fmt(d.resolved_at),
            'last_decision': decision_view(last) if last else None,
            'decisions': [decision_view(e) for e in decisions],
        }
        disc_rows.append(row)
        if root is not None:
            disc_by_root.setdefault(root.id, []).append(row)

    rows, correction_log = [], []
    for root in sorted(chains.run, key=lambda r: (r.family_id is None, instrument(r).lower())):
        chain = chains.chain(root)
        head = chain[-1]
        human = head if head.id != root.id else None
        for previous, following in zip(chain, chain[1:]):
            hc = corrections.get(previous.id)
            if hc is not None:
                kind = (following.payload or {}).get('validation_kind', '')
                correction_log.append({'instrument_name': instrument(root), 'from_quantity': previous.detected_quantity,
                                       'to_quantity': following.detected_quantity, 'justification': hc.justification,
                                       'corrected_by': users.get(hc.user_id, ''), 'timestamp': _fmt(hc.recorded_at),
                                       'kind': kind, 'kind_label': label('validation_kind', kind)})
        latest_hc = next((corrections[e.id] for e in reversed(chain[:-1]) if e.id in corrections), None)
        confidence = (root.payload or {}).get('confidence_mean')
        expected = root.expected_quantity
        row_discrepancies = disc_by_root.get(root.id, [])
        rows.append({
            'event_id': str(root.id), 'instrument_name': instrument(root),
            'expected_quantity': expected if expected is not None else 0, 'expected_label': expected if expected is not None else '—',
            'ai_detected': root.detected_quantity or 0,
            'confidence': round(confidence * 100, 1) if confidence is not None else None,
            'ai_difference': (root.detected_quantity or 0) - expected if expected is not None else None,
            'human_final': human.detected_quantity if human else None,
            'final_difference': human.detected_quantity - expected if human and expected is not None else None,
            'corrected': bool(human) and any(e.id in corrections for e in chain[:-1]),
            'justification': latest_hc.justification if latest_hc else '',
            'reason_codes': sorted({r['reason_code'] for r in row_discrepancies}),
            'reasons': ', '.join(sorted({r['reason_label'] for r in row_discrepancies})),
            'discrepancy_states': [r['state'] for r in row_discrepancies],
            'needs_attention': any(not r['resolved'] for r in row_discrepancies),
            'initial_value': human.detected_quantity if human else (root.detected_quantity or 0),
        })
    first = (chains.run[0].payload or {}) if chains.run else {}
    audits = db.session.execute(select(AccessAudit).where(AccessAudit.resource_id.in_(
        [work_session.id] + list(chains.by_id) + [d.id for d in discrepancies]
        + [hc.id for hc in corrections.values()])).order_by(AccessAudit.occurred_at)).scalars().all()
    state = readiness(work_session, chains)
    return {
        'rows': rows,
        'expected_total': sum(r['expected_quantity'] for r in rows),
        'ai_total': sum(r['ai_detected'] for r in rows),
        'human_total': sum(r['human_final'] or 0 for r in rows),
        'discrepancies': disc_rows,
        'open_discrepancies': [r for r in disc_rows if not r['resolved']],
        'correction_required': [r for r in disc_rows if r['state'] == CORRECTION_REQUIRED],
        'corrections': correction_log,
        'run': {'model_version': first.get('model_version_tag', ''), 'provider': first.get('inference_provider', ''),
                'provider_label': label('inference_provider', first.get('inference_provider'), first.get('inference_provider', '')),
                'threshold': first.get('confidence_threshold', ''), 'inference_run_id': first.get('inference_run_id', ''),
                'media_asset_id': first.get('media_asset_id'),
                'timestamp': _fmt(chains.run[0].occurred_at) if chains.run else ''},
        'readiness': state,
        'validation_complete': state['validation_complete'],
        'operator_name': users.get(work_session.user_id, ''),
        'closed_by': users.get(work_session.closed_by_user_id, ''),
        'started_at': _fmt(work_session.started_at), 'ended_at': _fmt(work_session.ended_at),
        'timeline': [{'timestamp': _fmt(a.occurred_at), 'action': a.action, 'user_name': users.get(a.actor_user_id, ''),
                      'outcome': a.outcome, 'outcome_label': label('audit_outcome', a.outcome, (a.outcome or '').upper())}
                     for a in audits],
    }
