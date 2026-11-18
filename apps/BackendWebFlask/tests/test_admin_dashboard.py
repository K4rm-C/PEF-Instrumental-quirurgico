"""RF-AD-01 Administration dashboard (ADMIN-01): session KPIs by status, thresholds, health, resilience."""
import os
import uuid

import pytest
from sqlalchemy.exc import OperationalError

from services.admin_local import dashboard, facade, health

from .admin_fixtures import as_user, login_as, no_remote_http, pg, tenants  # noqa: F401


@pytest.fixture
def sessions(tenants):
    def add(key, status, count=1):
        status_id = tenants.q('SELECT id FROM cat_session_status WHERE code = :c', c=status).scalar_one()
        op_status = tenants.q("SELECT id FROM cat_operation_status WHERE code = 'scheduled'").scalar_one()
        for _ in range(count):
            operation, session = uuid.uuid4(), uuid.uuid4()
            tenants.q('INSERT INTO operation (id, status_id, institution_id) VALUES (:o, :s, :i)',
                      o=operation, s=op_status, i=tenants.inst[key])
            tenants.q("INSERT INTO work_session (id, status_id, user_id, operation_id, capture_mode) "
                      "VALUES (:w, :s, :u, :o, 'vision')", w=session, s=status_id, u=tenants.users['operator'][key],
                      o=operation)
    return add


@pytest.fixture
def auth_up(monkeypatch):
    monkeypatch.setattr(health, '_auth', lambda: ('ok', 'HTTP 200'))


def by_code(data):
    return {row['code']: (row['count'], row['variant']) for row in data['sessions_by_status']}


def test_sessions_by_status_are_scoped_and_coloured(as_user, tenants, sessions, auth_up):
    sessions('A', 'or_hold', 3)
    sessions('A', 'correction_required', 1)
    sessions('A', 'in_progress', 2)
    sessions('B', 'or_hold', 5)
    with as_user('admin', 'A'):
        a = by_code(facade.get('/dashboard'))
    with as_user('admin', 'B'):
        b = by_code(facade.get('/dashboard'))
    assert a == {'in_progress': (2, 'info'), 'or_hold': (3, 'danger'), 'correction_required': (1, 'warning'),
                 'awaiting_spd_review': (0, 'success')}
    assert b['or_hold'] == (5, 'danger') and b['in_progress'] == (0, 'info')


def test_thresholds_come_from_settings(as_user, tenants, sessions, auth_up):
    sessions('A', 'or_hold', 3)
    tenants.q("INSERT INTO app_setting (institution_id, key, value) VALUES (:i, 'kpi_or_hold_warning', '5'), "
              "(:i, 'kpi_or_hold_danger', '10')", i=tenants.inst['A'])
    with as_user('admin', 'A'):
        assert by_code(facade.get('/dashboard'))['or_hold'] == (3, 'success')


def test_users_are_counted_for_the_own_institution(as_user, tenants, auth_up):
    tenants.q('UPDATE "user" SET active = FALSE WHERE id = :u', u=tenants.users['floor']['B'])
    with as_user('admin', 'A'):
        a = facade.get('/dashboard')['stats']
    with as_user('admin', 'B'):
        b = facade.get('/dashboard')['stats']
    assert (a['active_users'], a['inactive_users']) == (4, 0)
    assert (b['active_users'], b['inactive_users']) == (3, 1)
    assert a['roles'] == 4


def test_health_checks_real_dependencies(as_user, tenants, auth_up, monkeypatch):
    if os.environ.get('PEF_TEST_MONGO_URL'):
        monkeypatch.setenv('MONGO_URL', os.environ['PEF_TEST_MONGO_URL'])
    with as_user('admin', 'A'):
        result = facade.get('/dashboard')['health']
    assert result['postgresql']['status'] == 'ok' and result['auth']['status'] == 'ok', result
    assert result['redis']['status'] == ('ok' if os.environ.get('REDIS_URL') else 'not_configured')
    assert result['mongodb']['status'] == ('ok' if os.environ.get('PEF_TEST_MONGO_URL') else 'not_configured')
    assert result['storage']['status'] == 'ok'


def test_a_failing_dependency_is_reported_without_blocking(client, login_as, tenants, monkeypatch, no_remote_http):
    # AuthService unreachable (HTTP blocked), Redis with a wrong password, MongoDB on a closed port.
    redis = os.environ.get('REDIS_URL', 'redis://:x@127.0.0.1:56379/0')
    monkeypatch.setenv('REDIS_URL', redis.replace('://:', '://:wrong-password-', 1))
    monkeypatch.setenv('MONGO_URL', 'mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=300')
    login_as('admin', 'A')
    page = client.get('/admin/dashboard')
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and 'System Health' in html and 'Unreachable' in html
    with client.application.test_request_context():
        results = health.check_all()
    assert results['auth']['status'] == 'error' and results['redis']['status'] == 'error'
    assert results['mongodb']['status'] == 'error' and results['postgresql']['status'] == 'ok'


def test_a_failing_section_does_not_block_the_dashboard(client, login_as, tenants, monkeypatch, auth_up):
    def broken(inst):
        raise OperationalError('SELECT', {}, Exception('connection lost'))
    monkeypatch.setattr(dashboard, '_sessions_by_status', broken)
    login_as('admin', 'A')
    page = client.get('/admin/dashboard')
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and 'Some indicators are not available right now' in html
    assert 'Active Users' in html and 'System Health' in html


def test_dashboard_page_shows_session_queues(client, login_as, tenants, sessions, auth_up):
    sessions('A', 'awaiting_spd_review', 1)
    login_as('admin', 'A')
    html = client.get('/admin/dashboard').get_data(as_text=True)
    for label in ('In Progress', 'OR Hold', 'Released to SPD Review', 'Awaiting SPD Review', 'Inactive Families'):
        assert label in html
    assert 'stat-card--success' in html


def test_only_it_admin(client, login_as, tenants):
    login_as('operator', 'A')
    assert client.get('/admin/dashboard').status_code == 403
