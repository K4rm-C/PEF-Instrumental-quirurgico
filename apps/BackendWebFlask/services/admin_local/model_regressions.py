"""MS-12 (ms-modelos-ia): regression runs of a vision model against a baseline model (migration 019).

The run is executed OUTSIDE the portal with real inference (scripts/model_regression.py in the VisionWorker image) and
its JSON report is recorded here by an IT administrator. PEF never computes or completes detections; it only:
- verifies the report format and that BOTH weights SHA-256 are registered models, the candidate being this model;
- recomputes the agreement (identical per video = same totals and per-class counts on the first and last frames) and
  the reference-set hash (SHA-256 of the sorted video hashes) from the items; it does not trust given summaries;
- stores the run immutably (yolo_model_regression_run; a new run is a new row) and audits it.
Agreement with a baseline is NOT accuracy (that is a recorded evaluation, MS-11 R17).
"""
import hashlib
import json
import re
from datetime import datetime, timezone

from sqlalchemy import text as sql

from . import audit
from .database import db
from .errors import bad_request, not_found
from .common import parse_uuid

FORMAT = 'pef-model-regression/1'
SHA_RE = re.compile(r'^[0-9a-f]{64}$')
SET_RE = re.compile(r'^[\w .:/()+-]{1,120}$', re.UNICODE)
MAX_ITEMS = 200
POSITIONS = ('first', 'last')


def _result(raw, field):
    if not isinstance(raw, dict):
        raise bad_request('invalid_regression_report', 'Each video needs the results of both models.', field)
    out = {}
    for position in POSITIONS:
        frame = raw.get(position)
        if not isinstance(frame, dict) or not isinstance(frame.get('total'), int) or frame['total'] < 0 \
                or not isinstance(frame.get('counts'), dict):
            raise bad_request('invalid_regression_report', 'Each frame result needs a total and per-class counts.', field)
        counts = {}
        for name, value in frame['counts'].items():
            if not isinstance(name, str) or not name or len(name) > 80 or not isinstance(value, int) or value < 0:
                raise bad_request('invalid_regression_report', 'Per-class counts must be whole numbers.', field)
            counts[name] = value
        if sum(counts.values()) != frame['total']:
            raise bad_request('invalid_regression_report', 'Per-class counts must add up to the total.', field)
        out[position] = {'total': frame['total'], 'counts': dict(sorted(counts.items()))}
    return out


def _parse(report):
    if isinstance(report, str):
        try:
            report = json.loads(report)
        except ValueError:
            raise bad_request('invalid_regression_report', 'The report must be the JSON produced by '
                              'scripts/model_regression.py.', 'report') from None
    if not isinstance(report, dict) or report.get('format') != FORMAT:
        raise bad_request('invalid_regression_report', 'The report must be the JSON produced by '
                          'scripts/model_regression.py.', 'report')
    reference_set = str(report.get('reference_set') or '').strip()
    if not SET_RE.match(reference_set):
        raise bad_request('invalid_reference_set', 'Name the reference set (letters, digits, spaces, . : / ( ) + -).',
                          'reference_set')
    shas = {key: str(report.get(key) or '').lower() for key in ('baseline_sha256', 'candidate_sha256')}
    if not all(SHA_RE.match(value) for value in shas.values()):
        raise bad_request('invalid_regression_report', 'The report must give the SHA-256 of both weights.', 'report')
    items = report.get('items')
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise bad_request('invalid_regression_report', f'The report must contain between 1 and {MAX_ITEMS} videos.',
                          'report')
    clean, seen = [], set()
    for index, item in enumerate(items):
        field = f'items[{index}]'
        if not isinstance(item, dict) or not SHA_RE.match(str(item.get('video_sha256') or '')):
            raise bad_request('invalid_regression_report', 'Each video needs its SHA-256.', field)
        if item['video_sha256'] in seen:
            raise bad_request('invalid_regression_report', 'A video appears twice in the report.', field)
        seen.add(item['video_sha256'])
        baseline, candidate = _result(item.get('baseline'), field), _result(item.get('candidate'), field)
        clean.append({'video': str(item.get('video') or '')[:200], 'video_sha256': item['video_sha256'],
                      'baseline': baseline, 'candidate': candidate, 'identical': baseline == candidate})
    try:
        executed_at = datetime.fromisoformat(str(report.get('executed_at') or '').replace('Z', '+00:00'))
    except ValueError:
        raise bad_request('invalid_regression_report', 'The report must give when it was executed.', 'report') from None
    if executed_at.tzinfo is None:
        executed_at = executed_at.replace(tzinfo=timezone.utc)
    if executed_at > datetime.now(timezone.utc):
        raise bad_request('future_date', 'A regression run cannot be dated in the future.', 'report')
    runner = str(report.get('runner') or 'scripts/model_regression.py').strip()[:80]
    return reference_set, shas, clean, executed_at, runner


def _model_by_sha(sha):
    return db.execute(sql('SELECT id, version_tag FROM yolo_model WHERE lower(checksum) = :c'), {'c': sha}).mappings().first()


def record_regression(actor, model_id, payload):
    model = db.execute(sql('SELECT id, version_tag, checksum FROM yolo_model WHERE id = :id'),
                       {'id': parse_uuid(model_id)}).mappings().first()
    if model is None:
        raise not_found('vision_model_not_found', 'Vision model not found.')
    reference_set, shas, items, executed_at, runner = _parse((payload or {}).get('report'))
    if (model['checksum'] or '').lower() != shas['candidate_sha256']:
        raise bad_request('regression_model_mismatch', 'The candidate weights of the report are not this model.', 'report')
    baseline = _model_by_sha(shas['baseline_sha256'])
    if baseline is None:
        raise bad_request('regression_baseline_unknown', 'The baseline weights of the report are not a registered model.',
                          'report')
    notes = str((payload or {}).get('notes') or '').strip()
    if len(notes) > 300:
        raise bad_request('notes_too_long', 'Notes are limited to 300 characters.', 'notes')
    set_sha = hashlib.sha256('\n'.join(sorted(item['video_sha256'] for item in items)).encode('ascii')).hexdigest()
    identical = sum(1 for item in items if item['identical'])
    run_id = db.execute(sql("""
        INSERT INTO yolo_model_regression_run (model_id, baseline_model_id, reference_set, reference_set_sha256, items,
            items_total, items_identical, runner, notes, recorded_by_user_id, executed_at)
        VALUES (:m, :b, :set, :sha, CAST(:items AS jsonb), :total, :identical, :runner, :notes, :actor, :at)
        RETURNING id"""), {'m': model['id'], 'b': baseline['id'], 'set': reference_set, 'sha': set_sha,
                           'items': json.dumps(items), 'total': len(items), 'identical': identical, 'runner': runner,
                           'notes': notes or None, 'actor': actor.id, 'at': executed_at}).scalar()
    audit.record('RECORD_VISION_MODEL_REGRESSION', 'vision_model', model['id'])
    db.commit()
    return {'id': str(run_id), 'model_id': str(model['id']), 'baseline_model_id': str(baseline['id']),
            'reference_set_sha256': set_sha, 'items_total': len(items), 'items_identical': identical}


def list_regressions(model_id):
    rows = db.execute(sql("""
        SELECT r.id, r.reference_set, r.reference_set_sha256, r.items, r.items_total, r.items_identical, r.runner,
               r.notes, r.executed_at, r.created_at, b.version_tag AS baseline_version, u.name AS recorded_by
        FROM yolo_model_regression_run r JOIN yolo_model b ON b.id = r.baseline_model_id
        LEFT JOIN "user" u ON u.id = r.recorded_by_user_id
        WHERE r.model_id = :m ORDER BY r.executed_at DESC, r.created_at DESC"""),
                      {'m': parse_uuid(model_id)}).mappings().all()
    return [{'id': str(r['id']), 'reference_set': r['reference_set'], 'reference_set_sha256': r['reference_set_sha256'],
             'items': r['items'], 'items_total': r['items_total'], 'items_identical': r['items_identical'],
             'all_identical': r['items_total'] == r['items_identical'], 'runner': r['runner'], 'notes': r['notes'],
             'baseline_version': r['baseline_version'], 'recorded_by': r['recorded_by'],
             'executed_at': r['executed_at'].isoformat(), 'created_at': r['created_at'].isoformat()} for r in rows]
