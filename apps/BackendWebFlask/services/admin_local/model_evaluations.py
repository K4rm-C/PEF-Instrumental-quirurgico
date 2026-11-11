"""MS-11 (R17): evaluation records of vision models (datasets, metrics, thresholds) and model history.

PEF never computes, estimates or completes these numbers. An IT administrator RECORDS the results of an evaluation
made outside PEF (e.g. Ultralytics `val` on a versioned dataset); a metric that was not measured stays empty.
Records are immutable (yolo_model_evaluation, migration 018): a correction is a new dataset version or split.

Regression check: for a dataset (name + version + split), the record of this model is compared with the records
of the OTHER models on exactly the same dataset. Different datasets are never compared (that would be meaningless).

History: the activation / deactivation / publication audit of the model (access_audit, D-5 actions) and the
number of counting sessions pinned to it (work_session.yolo_model_id, MS-02).

Audit: RECORD_VISION_MODEL_EVALUATION. Models are shared by the deployment (catalog steward rule applies).
"""
import json
import re
from datetime import datetime, timezone

from sqlalchemy import text as sql

from . import audit, model_regressions
from .database import db
from .errors import bad_request, conflict, not_found
from .common import parse_uuid

SPLITS = ('validation', 'test', 'field')
SOURCES = ('ultralytics_val', 'external', 'manual')
METRICS = ('precision_value', 'recall_value', 'map50', 'map50_95')
SHA_RE = re.compile(r'^[0-9a-f]{64}$')
NAME_RE = re.compile(r'^[\w .:/()+-]{1,120}$', re.UNICODE)
PER_CLASS_MAX = 200
HISTORY_ACTIONS = ('CREATE_VISION_MODEL', 'PUBLISH_VISION_MODEL', 'ACTIVATE_VISION_MODEL', 'DEACTIVATE_VISION_MODEL',
                   'UPDATE_VISION_MODEL', 'RECORD_VISION_MODEL_EVALUATION', 'RECORD_VISION_MODEL_REGRESSION')


def _model(model_id):
    parsed = parse_uuid(model_id)
    row = db.execute(sql('SELECT id, version_tag, active, checksum FROM yolo_model WHERE id = :id'),
                     {'id': parsed}).mappings().first()
    if row is None:
        raise not_found('vision_model_not_found', 'Vision model not found.')
    return row


def _metric(payload, key):
    raw = payload.get(key)
    if raw in (None, ''):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise bad_request('invalid_metric', 'Metrics are decimal numbers between 0 and 1.', key) from None
    if not 0.0 <= value <= 1.0:
        raise bad_request('invalid_metric', 'Metrics are decimal numbers between 0 and 1.', key)
    return round(value, 4)


def _per_class(raw):
    if raw in (None, '', {}):
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise bad_request('invalid_per_class', 'Per-class metrics must be a JSON object.', 'per_class') from None
    if not isinstance(raw, dict) or len(raw) > PER_CLASS_MAX:
        raise bad_request('invalid_per_class', 'Per-class metrics must be a JSON object of at most 200 classes.', 'per_class')
    clean = {}
    for name, values in raw.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or not isinstance(values, dict):
            raise bad_request('invalid_per_class', 'Each class needs a name and an object of metrics.', 'per_class')
        row = {}
        for key, value in values.items():
            if key not in ('precision', 'recall', 'map50', 'map50_95', 'instances'):
                raise bad_request('invalid_per_class', f'Unknown per-class metric: {key[:30]}.', 'per_class')
            if key == 'instances':
                if not isinstance(value, int) or value < 0:
                    raise bad_request('invalid_per_class', 'Per-class instances must be whole numbers.', 'per_class')
                row[key] = value
            else:
                if not isinstance(value, (int, float)) or not 0 <= value <= 1:
                    raise bad_request('invalid_per_class', 'Per-class metrics must be between 0 and 1.', 'per_class')
                row[key] = round(float(value), 4)
        clean[name.strip()] = row
    return clean


def _int(payload, key, *, minimum, required):
    raw = payload.get(key)
    if raw in (None, ''):
        if required:
            raise bad_request('required', 'This value is required.', key)
        return None
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise bad_request('invalid_integer', 'Use a whole number.', key) from None
    if value < minimum:
        raise bad_request('invalid_integer', f'The value must be at least {minimum}.', key)
    return value


def _validate(payload):
    name = str(payload.get('dataset_name') or '').strip()
    version = str(payload.get('dataset_version') or '').strip()
    if not NAME_RE.match(name):
        raise bad_request('invalid_dataset_name', 'Give the dataset a name (letters, digits, spaces, . : / ( ) + -).',
                          'dataset_name')
    if not version or len(version) > 40:
        raise bad_request('invalid_dataset_version', 'Give the dataset version (at most 40 characters).', 'dataset_version')
    split = str(payload.get('split') or '').strip()
    if split not in SPLITS:
        raise bad_request('invalid_split', 'Choose the split: validation, test or field.', 'split')
    source = str(payload.get('source') or '').strip()
    if source not in SOURCES:
        raise bad_request('invalid_source', 'Choose how the evaluation was made.', 'source')
    sha = str(payload.get('dataset_sha256') or '').strip().lower() or None
    if sha is not None and not SHA_RE.match(sha):
        raise bad_request('invalid_dataset_sha256', 'The dataset hash must be 64 hexadecimal characters (SHA-256).',
                          'dataset_sha256')
    raw_date = str(payload.get('evaluated_at') or '').strip()
    try:
        evaluated_at = datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
    except ValueError:
        raise bad_request('invalid_date', 'Give the date of the evaluation (YYYY-MM-DD).', 'evaluated_at') from None
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
    if evaluated_at > datetime.now(timezone.utc):
        raise bad_request('future_date', 'An evaluation cannot be dated in the future.', 'evaluated_at')
    values = {key: _metric(payload, key) for key in METRICS}
    per_class = _per_class(payload.get('per_class'))
    if all(values[key] is None for key in METRICS) and not per_class:
        # Recording a dataset without any measured result would look like an evaluation that did not happen.
        raise bad_request('metrics_required', 'Record at least one measured metric (precision, recall, mAP50 or '
                          'mAP50-95, or per-class metrics).', 'map50')
    notes = str(payload.get('notes') or '').strip()
    if len(notes) > 500:
        raise bad_request('notes_too_long', 'Notes are limited to 500 characters.', 'notes')
    return {
        'dataset_name': name, 'dataset_version': version, 'dataset_sha256': sha, 'split': split, 'source': source,
        'images': _int(payload, 'images', minimum=1, required=True),
        'instances': _int(payload, 'instances', minimum=0, required=False),
        'conf_threshold': _metric(payload, 'conf_threshold'), 'iou_threshold': _metric(payload, 'iou_threshold'),
        'evaluated_at': evaluated_at, 'notes': notes or None, 'per_class': per_class, **values,
    }


def _serialize(row):
    out = {key: row[key] for key in ('dataset_name', 'dataset_version', 'dataset_sha256', 'split', 'source', 'images',
                                     'instances', 'notes', 'per_class')}
    for key in METRICS + ('conf_threshold', 'iou_threshold'):
        out[key] = float(row[key]) if row[key] is not None else None
    out['id'] = str(row['id'])
    out['evaluated_at'] = row['evaluated_at'].isoformat()
    out['created_at'] = row['created_at'].isoformat()
    out['recorded_by'] = row.get('recorded_by')
    return out


def record_evaluation(actor, model_id, payload):
    model = _model(model_id)
    values = _validate(payload or {})
    exists = db.execute(sql("""SELECT 1 FROM yolo_model_evaluation WHERE model_id = :m AND dataset_name = :n
                               AND dataset_version = :v AND split = :s"""),
                        {'m': model['id'], 'n': values['dataset_name'], 'v': values['dataset_version'],
                         's': values['split']}).first()
    if exists:
        raise conflict('evaluation_exists', 'This model already has an evaluation for that dataset version and split '
                       '(records are immutable: use a new dataset version).', 'dataset_version')
    row = db.execute(sql("""
        INSERT INTO yolo_model_evaluation (model_id, dataset_name, dataset_version, dataset_sha256, split, images,
            instances, precision_value, recall_value, map50, map50_95, per_class, conf_threshold, iou_threshold, source,
            evaluated_at, notes, recorded_by_user_id)
        VALUES (:model_id, :dataset_name, :dataset_version, :dataset_sha256, :split, :images, :instances,
            :precision_value, :recall_value, :map50, :map50_95, CAST(:per_class AS jsonb), :conf_threshold, :iou_threshold,
            :source, :evaluated_at, :notes, :actor)
        RETURNING id"""), {**values, 'model_id': model['id'], 'actor': actor.id,
                           'per_class': json.dumps(values['per_class']) if values['per_class'] is not None else None}
                     ).scalar()
    audit.record('RECORD_VISION_MODEL_EVALUATION', 'vision_model', model['id'])
    db.commit()
    return {'id': str(row), 'model_id': str(model['id'])}


def model_overview(model_id):
    """Evaluations of a model with the comparison against other models on the SAME dataset, plus its history."""
    model = _model(model_id)
    rows = db.execute(sql("""
        SELECT e.*, u.name AS recorded_by FROM yolo_model_evaluation e
        LEFT JOIN "user" u ON u.id = e.recorded_by_user_id
        WHERE e.model_id = :m ORDER BY e.evaluated_at DESC, e.created_at DESC"""), {'m': model['id']}).mappings().all()
    evaluations = []
    for row in rows:
        item = _serialize(row)
        others = db.execute(sql("""
            SELECT m.version_tag, e.precision_value, e.recall_value, e.map50, e.map50_95, e.evaluated_at
            FROM yolo_model_evaluation e JOIN yolo_model m ON m.id = e.model_id
            WHERE e.dataset_name = :n AND e.dataset_version = :v AND e.split = :s AND e.model_id <> :m
            ORDER BY e.evaluated_at DESC"""), {'n': row['dataset_name'], 'v': row['dataset_version'], 's': row['split'],
                                              'm': model['id']}).mappings().all()
        comparisons = []
        for other in others:
            deltas = {}
            for key in METRICS:
                if row[key] is not None and other[key] is not None:
                    deltas[key] = round(float(row[key]) - float(other[key]), 4)
            comparisons.append({'version_tag': other['version_tag'], 'deltas': deltas,
                                'regression': any(value < 0 for value in deltas.values())})
        item['comparisons'] = comparisons
        evaluations.append(item)
    history = [dict(action=r['action'], at=r['occurred_at'].isoformat(), by=r['name'])
               for r in db.execute(sql("""
                   SELECT a.action, a.occurred_at, u.name FROM access_audit a LEFT JOIN "user" u ON u.id = a.actor_user_id
                   WHERE a.resource_type = 'vision_model' AND a.resource_id = :m AND a.action = ANY(:actions)
                   ORDER BY a.occurred_at DESC LIMIT 100"""), {'m': model['id'], 'actions': list(HISTORY_ACTIONS)}).mappings()]
    sessions = db.execute(sql('SELECT count(*) FROM work_session WHERE yolo_model_id = :m'), {'m': model['id']}).scalar()
    return {'model': {'id': str(model['id']), 'version_tag': model['version_tag'], 'active': bool(model['active']),
                      'sha256': (model['checksum'] or '').strip().lower() or None},
            'evaluations': evaluations, 'history': history, 'pinned_sessions': int(sessions or 0),
            # MS-12: regression runs against a baseline (agreement, not accuracy).
            'regressions': model_regressions.list_regressions(model['id'])}
