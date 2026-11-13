"""MS-12 (ms-modelos-ia): regression runs of a vision model against a baseline (report of scripts/model_regression.py).
Real PostgreSQL (rolled back): weights verified by SHA-256, agreement and reference-set hash recomputed by PEF,
immutability, audit, permissions and the Administration screen."""
import hashlib
import io
import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from services.admin_local import facade
from services.admin_local.messages import AdminDomainError

from .admin_fixtures import as_user, login_as, no_remote_http, pg, tenants  # noqa: F401


def _model(tenants, tag, sha):
    model_id = uuid.uuid4()
    tenants.q("INSERT INTO yolo_model (id, version_tag, checksum, active) VALUES (:i, :v, :c, FALSE)",
              i=model_id, v=f'{tag}-{tenants.tag}'[:32], c=sha)
    return model_id


@pytest.fixture
def models(pg, tenants):
    shas = {'base': uuid.uuid4().hex + uuid.uuid4().hex, 'cand': uuid.uuid4().hex + uuid.uuid4().hex}
    return {'shas': shas, 'base': _model(tenants, 'rg-base', shas['base']), 'cand': _model(tenants, 'rg-cand', shas['cand'])}


def frames(first, last, label='Type'):
    return {'first': {'total': first, 'counts': {label: first}}, 'last': {'total': last, 'counts': {label: last}}}


def report(models, items=None, **overrides):
    data = {'format': 'pef-model-regression/1', 'reference_set': 'ms031-ref-v1',
            'baseline_sha256': models['shas']['base'], 'candidate_sha256': models['shas']['cand'],
            'runner': 'scripts/model_regression.py (VisionWorker image, real inference)',
            'executed_at': '2026-11-10T10:00:00+00:00',
            'items': items if items is not None else [
                {'video': 'ms031_full.avi', 'video_sha256': 'a' * 64, 'baseline': frames(8, 9), 'candidate': frames(8, 9)},
                {'video': 'tray_full.mp4', 'video_sha256': 'b' * 64, 'baseline': frames(8, 9), 'candidate': frames(8, 8)}]}
    data.update(overrides)
    return json.dumps(data)


def test_a_run_is_verified_recomputed_and_audited(as_user, tenants, models):
    tampered = json.loads(report(models))
    tampered['items_identical'] = 2                                         # given summaries are ignored
    with as_user('admin', 'A', 'POST'):
        created = facade.post(f"/vision-models/{models['cand']}/regressions",
                              {'report': json.dumps(tampered), 'notes': 'before deploying'})
    with as_user('admin', 'A'):
        [run] = facade.get(f"/vision-models/{models['cand']}/evaluations")['regressions']
    assert (created['items_total'], created['items_identical']) == (2, 1)
    assert created['reference_set_sha256'] == hashlib.sha256(('a' * 64 + '\n' + 'b' * 64).encode()).hexdigest()
    assert run['all_identical'] is False and run['baseline_version'].startswith('rg-base')
    assert [i['identical'] for i in run['items']] == [True, False]
    assert [r for _, r, _ in tenants.audit('RECORD_VISION_MODEL_REGRESSION')] == [models['cand']]


@pytest.mark.parametrize('mutate, code', [
    (lambda m: report(m, format='other'), 'invalid_regression_report'),
    (lambda m: 'not json', 'invalid_regression_report'),
    (lambda m: report(m, candidate_sha256=m['shas']['base']), 'regression_model_mismatch'),
    (lambda m: report(m, baseline_sha256='c' * 64), 'regression_baseline_unknown'),
    (lambda m: report(m, items=[]), 'invalid_regression_report'),
    (lambda m: report(m, reference_set=''), 'invalid_reference_set'),
    (lambda m: report(m, executed_at='2099-01-01T00:00:00+00:00'), 'future_date'),
    (lambda m: report(m, items=[{'video': 'x', 'video_sha256': 'a' * 64, 'baseline': frames(8, 9),
                                 'candidate': {'first': {'total': 3, 'counts': {'Type': 2}}, 'last': frames(1, 1)['last']}}]),
     'invalid_regression_report'),                                          # counts that do not add up
    (lambda m: report(m, items=[{'video': 'x', 'video_sha256': 'a' * 64, 'baseline': frames(1, 1), 'candidate': frames(1, 1)}] * 2),
     'invalid_regression_report'),                                          # the same video twice
])
def test_invalid_reports_are_refused(as_user, tenants, models, mutate, code):
    with as_user('admin', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        facade.post(f"/vision-models/{models['cand']}/regressions", {'report': mutate(models)})
    assert exc.value.status == 400 and exc.value.code == code
    assert tenants.q('SELECT count(*) FROM yolo_model_regression_run WHERE model_id = :m', m=models['cand']).scalar_one() == 0


def test_runs_are_immutable_and_only_it_admin_records(as_user, pg, tenants, models):
    with as_user('admin', 'A', 'POST'):
        facade.post(f"/vision-models/{models['cand']}/regressions", {'report': report(models)})
    savepoint = pg.begin_nested()
    with pytest.raises(DBAPIError, match='immutable'):
        pg.execute(text('UPDATE yolo_model_regression_run SET items_identical = 2 WHERE model_id = :m'), {'m': models['cand']})
    savepoint.rollback()
    with as_user('operator', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        facade.post(f"/vision-models/{models['cand']}/regressions", {'report': report(models)})
    assert exc.value.status == 403


def test_screen_records_an_uploaded_report(client, login_as, tenants, models, no_remote_http):
    login_as('admin', 'A')
    url = f"/admin/vision-models/{models['cand']}/evaluations"
    assert b'No regression run recorded.' in client.get(url).data
    posted = client.post(url, data={'kind': 'regression', 'notes': '',
                                    'report_file': (io.BytesIO(report(models).encode()), 'report.json')},
                         content_type='multipart/form-data', follow_redirects=True)
    assert posted.status_code == 200 and b'data-all-identical="false"' in posted.data
    refused = client.post(url, data={'kind': 'regression', 'report': '{}'})
    assert refused.status_code == 400 and b'data-form-error' in refused.data
