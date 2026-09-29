"""Vision inference for a counting session (provider-agnostic) + automatic comparison.

Flow (one transaction, staged here, committed by the route):
    latest capture (evidence_service) + single active YoloModel + ModelClass mapping
    -> provider.infer() -> InferenceResult (normalized, provider independent)
    -> CountEvent auto_count per family in ExpectedInventory UNION detected families
       (+ one per unmapped yolo_class_id) with user_id = NULL (system fact)
    -> Discrepancy shortage / surplus / low_confidence / unidentified (resolved = FALSE)
    -> WorkSession counting -> validating
    -> AccessAudit VISION_RESULT (+ CREATE_DISCREPANCY per discrepancy)

Expected quantities always come from the frozen ExpectedInventory, never from KitItem.
Idempotency: every CountEvent gets client_event_id = uuid5(inference_run_id, key), so a
resubmitted run is detected and never duplicates events or discrepancies.
Bounding boxes are not stored in PostgreSQL (they belong to the worker/Mongo, DS06).
Discrepancy.description is persisted canonical English (never translated); screens show labels
localized by code instead (reason / result codes, family codes).
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from flask import current_app
from flask_babel import gettext as _
from sqlalchemy import select

from extensions import db
from localization.labels import label
from models.CatDiscrepancyReason import CatDiscrepancyReason
from models.CatEventType import CatEventType
from models.CatSessionStatus import CatSessionStatus
from models.CountEvent import CountEvent
from models.Discrepancy import Discrepancy
from models.ExpectedInventory import ExpectedInventory
from models.InstrumentFamily import InstrumentFamily
from models.ModelClass import ModelClass
from models.YoloModel import YoloModel
from services.admin_service import ConflictError, FormError, parse_uuid
from services.audit import record_audit
from services.evidence_service import get_latest_capture_for_session

AUTO_COUNT = 'auto_count'
REASONS = ('shortage', 'surplus', 'unidentified', 'low_confidence')
SMALLINT_MAX = 32767


# --------------------------------------------------------------------------- normalized result

@dataclass
class ClassResult:
    yolo_class_id: int
    detected_quantity: int
    confidence: float
    family_id: uuid.UUID | None = None  # None -> no ModelClass for this class id (unidentified)


@dataclass
class InferenceResult:
    inference_run_id: uuid.UUID
    media_asset_id: uuid.UUID
    model_id: uuid.UUID
    model_version_tag: str
    inferred_at: datetime
    provider: str
    confidence_threshold: float
    results: list = field(default_factory=list)


# --------------------------------------------------------------------------- providers

class ControlledInferenceProvider:
    """Development provider: the operator enters the model output explicitly (never hardcoded)."""
    name = 'controlled'  # human-readable label: localization.labels 'inference_provider'
    needs_form = True

    def infer(self, capture, model, form):
        rows = zip(form.getlist('yolo_class_id[]'), form.getlist('detected_quantity[]'), form.getlist('confidence[]'))
        detections, seen = [], set()
        for raw_class, raw_quantity, raw_confidence in rows:
            raw_class, raw_quantity, raw_confidence = (str(v or '').strip() for v in (raw_class, raw_quantity, raw_confidence))
            if not raw_class and not raw_quantity:
                continue  # blank row
            class_id = _int(raw_class, _('YOLO class ID'), 0, SMALLINT_MAX)
            if class_id in seen:
                raise FormError(_('YOLO class ID %(class_id)s is listed more than once.', class_id=class_id))
            seen.add(class_id)
            quantity = _int(raw_quantity, _('Detected quantity for class %(class_id)s', class_id=class_id), 0, SMALLINT_MAX)
            try:
                confidence = float(raw_confidence)
            except ValueError:
                raise FormError(_('Confidence for class %(class_id)s must be a number between 0 and 1.', class_id=class_id)) from None
            if not 0.0 <= confidence <= 1.0:
                raise FormError(_('Confidence for class %(class_id)s must be between 0 and 1.', class_id=class_id))
            detections.append((class_id, quantity, confidence))
        if not detections:
            raise FormError(_('Enter at least one model output row (class ID, detected quantity, confidence).'))
        return detections


PROVIDERS = {ControlledInferenceProvider.name: ControlledInferenceProvider}


def get_provider():
    name = (current_app.config.get('VISION_INFERENCE_PROVIDER') or 'controlled').strip().lower()
    provider = PROVIDERS.get(name)
    if provider is None:
        raise ConflictError(_('Vision inference provider "%(name)s" is not available in this deployment.', name=name))
    return provider()


def confidence_threshold():
    return float(current_app.config.get('VISION_CONFIDENCE_THRESHOLD', 0.70))


def _int(value, field, minimum, maximum):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise FormError(_('%(field)s must be a whole number.', field=field)) from None
    if number < minimum or number > maximum:
        raise FormError(_('%(field)s must be between %(minimum)s and %(maximum)s.', field=field, minimum=minimum, maximum=maximum))
    return number


# --------------------------------------------------------------------------- model registry

def active_model():
    """Exactly one active YoloModel with a class mapping -> (model, {yolo_class_id: ModelClass})."""
    models = db.session.execute(select(YoloModel).where(YoloModel.active.is_(True))).scalars().all()
    if not models:
        raise ConflictError(_('There is no active vision model. An administrator must activate one first.'))
    if len(models) > 1:
        raise ConflictError(_('More than one vision model is marked active; the model registry is inconsistent.'))
    model = models[0]
    if not model.version_tag:
        raise ConflictError(_('The active vision model has no version tag.'))
    classes = db.session.execute(select(ModelClass).where(ModelClass.model_id == model.id)).scalars().all()
    if not classes:
        raise ConflictError(_('The active vision model %(version)s has no class mappings configured.', version=model.version_tag))
    mapping = {}
    for mc in classes:
        if mc.yolo_class_id in mapping or mc.yolo_class_id < 0 or mc.family_id is None:
            raise ConflictError(_('The class mapping of model %(version)s is inconsistent.', version=model.version_tag))
        mapping[mc.yolo_class_id] = mc
    return model, mapping


# --------------------------------------------------------------------------- run

def _status_code(work_session):
    status = db.session.get(CatSessionStatus, work_session.status_id)
    return status.code if status else ''


def _expected(work_session):
    return {row.family_id: row.expected_quantity for row in db.session.execute(
        select(ExpectedInventory).where(ExpectedInventory.session_id == work_session.id)).scalars()}


def event_client_id(run_id, key):
    return uuid.uuid5(run_id, key)


def run_already_processed(work_session, run_id):
    """True if this inference_run_id already produced events for this session (idempotency)."""
    keys = [event_client_id(run_id, f'family:{fid}') for fid in _expected(work_session)]
    if not keys:
        return False
    return db.session.execute(select(CountEvent.id).where(
        CountEvent.session_id == work_session.id, CountEvent.client_event_id.in_(keys)).limit(1)).first() is not None


def _lookup_catalogs():
    auto_count = db.session.execute(select(CatEventType).where(CatEventType.code == AUTO_COUNT)).scalar_one_or_none()
    if auto_count is None:
        raise ConflictError(_('Event type "%(code)s" is not configured. The catalogs must be updated.', code=AUTO_COUNT))
    reasons = {row.code: row for row in db.session.execute(
        select(CatDiscrepancyReason).where(CatDiscrepancyReason.code.in_(REASONS))).scalars()}
    missing = [code for code in REASONS if code not in reasons]
    if missing:
        raise ConflictError(_('Discrepancy reason(s) %(codes)s missing; the catalogs must be updated '
                              '(data/seeds/002_seed_catalogs.sql).', codes=', '.join(missing)))
    validating = db.session.execute(select(CatSessionStatus).where(CatSessionStatus.code == 'validating')).scalar_one_or_none()
    if validating is None:
        raise ConflictError(_('Session status "%(code)s" is not configured. The catalogs must be updated.', code='validating'))
    return auto_count, reasons, validating


def run_inference(work_session, form, ctx):
    """Validate, infer and stage everything. Returns InferenceResult, or None if the run was already stored."""
    run_id = parse_uuid(form.get('inference_run_id'))
    if run_id is None:
        raise FormError(_('Missing inference run identifier; reload the page and try again.'))
    if run_already_processed(work_session, run_id):
        return None
    code = _status_code(work_session)
    if code == 'open':
        raise ConflictError(_('Take a capture of the instrument tray before running the analysis.'))
    if code != 'counting':
        raise ConflictError(_('The analysis can only run once, while the session is counting.'))
    capture = get_latest_capture_for_session(work_session)
    if capture is None:
        raise ConflictError(_('This session has no capture to analyse.'))
    model, mapping = active_model()
    catalogs = _lookup_catalogs()
    provider = get_provider()
    detections = provider.infer(capture, model, form)
    result = InferenceResult(
        inference_run_id=run_id, media_asset_id=capture.media_asset_id, model_id=model.id,
        model_version_tag=model.version_tag, inferred_at=datetime.now(timezone.utc), provider=provider.name,
        confidence_threshold=confidence_threshold(),
        results=[ClassResult(class_id, quantity, confidence, mapping[class_id].family_id if class_id in mapping else None)
                 for class_id, quantity, confidence in detections],
    )
    persist_inference(work_session, result, catalogs, ctx)
    return result


def _payload(result, class_result=None):
    return {
        'inference_run_id': str(result.inference_run_id),
        'media_asset_id': str(result.media_asset_id),
        'model_id': str(result.model_id),
        'model_version_tag': result.model_version_tag,
        'inference_provider': result.provider,
        'confidence_threshold': result.confidence_threshold,
        'confidence_mean': class_result.confidence if class_result else None,
        'yolo_class_id': class_result.yolo_class_id if class_result else None,
        'inferred_at': result.inferred_at.isoformat(),
    }


def persist_inference(work_session, result, catalogs, ctx):
    auto_count, reasons, validating = catalogs
    expected = _expected(work_session)
    detected = {item.family_id: item for item in result.results if item.family_id is not None}
    names = {row.id: row.name for row in db.session.execute(
        select(InstrumentFamily).where(InstrumentFamily.id.in_(set(expected) | set(detected)))).scalars()}
    audit = {'actor': ctx['user'], 'institution_id': ctx['institution_id']}
    discrepancies = []

    def add_discrepancy(event, reason_code, description):
        discrepancies.append(Discrepancy(
            session_id=work_session.id, origin_event_id=event.id, family_id=event.family_id,
            expected_quantity=event.expected_quantity, detected_quantity=event.detected_quantity,
            reason_id=reasons[reason_code].id, description=description, resolved=False, resolved_at=None))

    for family_id in sorted(set(expected) | set(detected), key=lambda fid: names.get(fid, '')):
        class_result = detected.get(family_id)
        exp = expected.get(family_id, 0)
        det = class_result.detected_quantity if class_result else 0
        if class_result is not None and det == 0 and family_id not in expected:
            continue  # model reported the class with zero pieces and nothing was expected
        event = CountEvent(
            session_id=work_session.id, event_type_id=auto_count.id, user_id=None, family_id=family_id,
            expected_quantity=exp, detected_quantity=det, occurred_at=result.inferred_at,
            client_event_id=event_client_id(result.inference_run_id, f'family:{family_id}'),
            payload=_payload(result, class_result))
        db.session.add(event)
        db.session.flush()
        name, difference = names.get(family_id, 'Instrument'), det - exp
        if difference < 0:
            add_discrepancy(event, 'shortage', f'Shortage: {name} expected {exp}, detected {det} ({difference}).')
        elif difference > 0:
            add_discrepancy(event, 'surplus', f'Surplus: {name} expected {exp}, detected {det} (+{difference}).')
        if class_result is not None and class_result.confidence < result.confidence_threshold:
            add_discrepancy(event, 'low_confidence',
                            f'Low confidence: {name} detected with {class_result.confidence:.2f} '
                            f'(threshold {result.confidence_threshold:.2f}).')

    for class_result in (item for item in result.results if item.family_id is None and item.detected_quantity > 0):
        event = CountEvent(
            session_id=work_session.id, event_type_id=auto_count.id, user_id=None, family_id=None,
            expected_quantity=None, detected_quantity=class_result.detected_quantity, occurred_at=result.inferred_at,
            client_event_id=event_client_id(result.inference_run_id, f'class:{class_result.yolo_class_id}'),
            payload=_payload(result, class_result))
        db.session.add(event)
        db.session.flush()
        add_discrepancy(event, 'unidentified',
                        f'Unidentified: YOLO class {class_result.yolo_class_id} has no instrument family mapping '
                        f'({class_result.detected_quantity} detected).')

    for discrepancy in discrepancies:
        db.session.add(discrepancy)
    db.session.flush()
    for discrepancy in discrepancies:
        record_audit('CREATE_DISCREPANCY', 'discrepancy', discrepancy.id, **audit)
    work_session.status_id = validating.id
    record_audit('VISION_RESULT', 'work_session', work_session.id, **audit)
    db.session.flush()


# --------------------------------------------------------------------------- reading (AI Suggested Count)

def _reason_labels(discrepancy_rows):
    codes = {row.id: row.code for row in db.session.execute(select(CatDiscrepancyReason)).scalars()}
    by_event = {}
    for row in discrepancy_rows:
        by_event.setdefault(row.origin_event_id, []).append(codes.get(row.reason_id, ''))
    return by_event


# badge variant per result code (label localized by code: localization.labels 'vision_result')
RESULT_VARIANTS = {'unidentified': 'danger', 'shortage': 'danger', 'surplus': 'warning', 'low_confidence': 'warning'}


def latest_run_events(work_session):
    """auto_count CountEvents of the session's latest inference run ([] when none)."""
    auto_count = db.session.execute(select(CatEventType).where(CatEventType.code == AUTO_COUNT)).scalar_one_or_none()
    if auto_count is None:
        return []
    events = db.session.execute(
        select(CountEvent).where(CountEvent.session_id == work_session.id, CountEvent.event_type_id == auto_count.id)
        .order_by(CountEvent.occurred_at.desc())).scalars().all()
    if not events:
        return []
    run_id = (events[0].payload or {}).get('inference_run_id')
    return [event for event in events if (event.payload or {}).get('inference_run_id') == run_id]


def latest_run_view(work_session):
    """Rows of the latest auto_count run of the session (None when no inference has run yet)."""
    events = latest_run_events(work_session)
    if not events:
        return None
    run_id = (events[0].payload or {}).get('inference_run_id')
    discrepancy_rows = db.session.execute(
        select(Discrepancy).where(Discrepancy.origin_event_id.in_([event.id for event in events]))).scalars().all()
    reasons = _reason_labels(discrepancy_rows)
    names = {row.id: label('instrument_family', row.code, row.name) for row in db.session.execute(
        select(InstrumentFamily).where(InstrumentFamily.id.in_({e.family_id for e in events if e.family_id}))).scalars()}
    rows = []
    for event in sorted(events, key=lambda e: (e.family_id is None, names.get(e.family_id, '').lower())):
        payload = event.payload or {}
        codes = reasons.get(event.id, [])
        result_codes = [code for code in ('unidentified', 'shortage', 'surplus', 'low_confidence') if code in codes]
        confidence = payload.get('confidence_mean')
        expected = event.expected_quantity
        detected = event.detected_quantity or 0
        rows.append({
            'instrument_name': names.get(event.family_id) or _('Unidentified (YOLO class %(class_id)s)',
                                                                class_id=payload.get('yolo_class_id')),
            'expected_quantity': expected if expected is not None else 0,
            'expected_label': expected if expected is not None else '—',
            'ai_detected': detected,
            'difference': detected - (expected or 0) if expected is not None else None,
            'confidence': round(confidence * 100, 1) if confidence is not None else None,
            'needs_attention': bool(codes),
            'result_codes': result_codes or ['match'],
            'result_label': ' · '.join(label('vision_result', code) for code in result_codes or ['match']),
            'result_variant': RESULT_VARIANTS[result_codes[0]] if result_codes else 'success',
        })
    first = events[0].payload or {}
    return {
        'rows': rows,
        'expected_total': sum(row['expected_quantity'] for row in rows),
        'detected_total': sum(row['ai_detected'] for row in rows),
        'discrepancy_count': len(discrepancy_rows),
        'media_asset_id': first.get('media_asset_id'),
        'details': {
            'provider_code': first.get('inference_provider', ''),
            'vision_model': label('inference_provider', first.get('inference_provider'), first.get('inference_provider', '')),
            'model_version': first.get('model_version_tag', ''),
            'confidence_threshold': first.get('confidence_threshold', ''),
            'analysis_timestamp': events[0].occurred_at.strftime('%Y-%m-%d %H:%M:%S') if events[0].occurred_at else '',
            'inference_run_id': run_id,
        },
    }


def controlled_form_rows(mapping):
    """Pre-filled rows (one per mapped class) + two blank rows for arbitrary class ids."""
    names = {row.id: label('instrument_family', row.code, row.name) for row in db.session.execute(
        select(InstrumentFamily).where(InstrumentFamily.id.in_({mc.family_id for mc in mapping.values()}))).scalars()}
    rows = [{'yolo_class_id': class_id, 'family_name': names.get(mc.family_id, '')}
            for class_id, mc in sorted(mapping.items())]
    return rows + [{'yolo_class_id': '', 'family_name': ''}, {'yolo_class_id': '', 'family_name': ''}]
