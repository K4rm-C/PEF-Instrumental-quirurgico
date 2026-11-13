"""MS-11 (R17): evaluation records of vision models (datasets, metrics, thresholds), regression between models on the
same dataset, immutability, audit, permissions and the Administration screen. Real PostgreSQL (rolled back)."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from services.admin_local import facade
from services.admin_local.messages import AdminDomainError

from .admin_fixtures import as_user, login_as, no_remote_http, pg, tenants  # noqa: F401


def _model(tenants, tag):
    model_id = uuid.uuid4()
    tenants.q("INSERT INTO yolo_model (id, version_tag, checksum, active) VALUES (:i, :v, :c, FALSE)",
              i=model_id, v=f'{tag}-{tenants.tag}'[:32], c=uuid.uuid4().hex + uuid.uuid4().hex)
    return model_id


def _evaluation(**overrides):
    values = {'dataset_name': 'PEF instruments', 'dataset_version': '2026.10', 'split': 'test',
              'source': 'ultralytics_val', 'images': '120', 'instances': '840', 'precision_value': '0.91',
              'recall_value': '0.88', 'map50': '0.93', 'map50_95': '0.71', 'conf_threshold': '0.25',
              'iou_threshold': '0.7', 'evaluated_at': '2026-10-20', 'notes': 'offline val'}
    values.update(overrides)
    return values


@pytest.fixture
def models(pg, tenants):
    return {'old': _model(tenants, 'eval-old'), 'new': _model(tenants, 'eval-new')}


def test_recorded_values_are_stored_as_declared_and_compared_on_the_same_dataset(as_user, tenants, models):
    with as_user('admin', 'A', 'POST'):
        facade.post(f"/vision-models/{models['old']}/evaluations", _evaluation())
        facade.post(f"/vision-models/{models['new']}/evaluations", _evaluation(precision_value='0.93', map50='0.90',
                                                                              recall_value='', map50_95=''))
        facade.post(f"/vision-models/{models['new']}/evaluations", _evaluation(dataset_version='2026.11', map50='0.95'))
    with as_user('admin', 'A'):
        overview = facade.get(f"/vision-models/{models['new']}/evaluations")
    by_version = {e['dataset_version']: e for e in overview['evaluations']}
    same = by_version['2026.10']
    assert same['recall_value'] is None and same['map50_95'] is None          # not measured: stays empty
    assert same['precision_value'] == 0.93 and same['map50'] == 0.90
    [comparison] = same['comparisons']
    assert comparison['deltas'] == {'precision_value': 0.02, 'map50': -0.03}  # only metrics both models declared
    assert comparison['regression'] is True                                    # mAP50 lower than the other model
    assert by_version['2026.11']['comparisons'] == []                          # other dataset: never compared
    assert overview['model']['version_tag'].startswith('eval-new')
    assert [h['action'] for h in overview['history']].count('RECORD_VISION_MODEL_EVALUATION') == 2


def test_records_are_immutable_and_unique_per_dataset_version_and_split(as_user, pg, tenants, models):
    with as_user('admin', 'A', 'POST'):
        facade.post(f"/vision-models/{models['old']}/evaluations", _evaluation())
        with pytest.raises(AdminDomainError) as exc:
            facade.post(f"/vision-models/{models['old']}/evaluations", _evaluation(map50='0.99'))
    assert exc.value.status == 409 and exc.value.code == 'evaluation_exists'
    savepoint = pg.begin_nested()
    with pytest.raises(DBAPIError, match='immutable'):
        pg.execute(text("UPDATE yolo_model_evaluation SET map50 = 0.99 WHERE model_id = :m"), {'m': models['old']})
    savepoint.rollback()


@pytest.mark.parametrize('overrides, code', [
    ({'map50': '1.2'}, 'invalid_metric'),
    ({'precision_value': '', 'recall_value': '', 'map50': '', 'map50_95': ''}, 'metrics_required'),
    ({'evaluated_at': '2099-01-01'}, 'future_date'),
    ({'dataset_sha256': 'abc'}, 'invalid_dataset_sha256'),
    ({'images': '0'}, 'invalid_integer'),
    ({'split': 'train'}, 'invalid_split'),
    ({'source': 'guess'}, 'invalid_source'),
    ({'per_class': '{"A": {"precision": 2}}'}, 'invalid_per_class'),
])
def test_invalid_records_are_refused(as_user, tenants, models, overrides, code):
    with as_user('admin', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        facade.post(f"/vision-models/{models['old']}/evaluations", _evaluation(**overrides))
    assert exc.value.status == 400 and exc.value.code == code


def test_per_class_metrics_and_audit(as_user, tenants, models):
    with as_user('admin', 'A', 'POST'):
        facade.post(f"/vision-models/{models['old']}/evaluations",
                    _evaluation(per_class='{"CRILE_WOOD": {"precision": 0.9, "recall": 0.8, "instances": 40}}'))
    with as_user('admin', 'A'):
        [evaluation] = facade.get(f"/vision-models/{models['old']}/evaluations")['evaluations']
    assert evaluation['per_class'] == {'CRILE_WOOD': {'precision': 0.9, 'recall': 0.8, 'instances': 40}}
    audited = tenants.audit('RECORD_VISION_MODEL_EVALUATION')
    assert [(actor, resource, outcome) for actor, resource, outcome in audited] == [
        (tenants.admin['A'], models['old'], 'success')]


def test_only_it_admin_can_read_or_record(client, login_as, as_user, tenants, models):
    login_as('supervisor', 'A')
    assert client.get(f"/admin/vision-models/{models['old']}/evaluations").status_code == 403
    with as_user('operator', 'A', 'POST'), pytest.raises(AdminDomainError) as exc:
        facade.post(f"/vision-models/{models['old']}/evaluations", _evaluation())
    assert exc.value.status == 403


def test_screen_lists_records_and_records_from_the_form(client, login_as, tenants, models, no_remote_http):
    login_as('admin', 'A')
    page = client.get(f"/admin/vision-models/{models['old']}/evaluations")
    assert page.status_code == 200 and b'data-no-evaluations' in page.data
    posted = client.post(f"/admin/vision-models/{models['old']}/evaluations", data=_evaluation(), follow_redirects=True)
    assert posted.status_code == 200 and b'data-evaluation="PEF instruments:2026.10:test"' in posted.data
    refused = client.post(f"/admin/vision-models/{models['old']}/evaluations", data=_evaluation(map50='7'))
    assert refused.status_code == 400 and b'data-form-error' in refused.data
    listing = client.get('/admin/vision-models')
    assert b'data-model-evaluations' in listing.data
