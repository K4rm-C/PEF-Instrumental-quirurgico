"""RF-AD-09 Access audit log (ADMIN-01, block 1): local, read only, institution scope in SQL."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from services.admin_local import facade
from services.admin_local.messages import AdminDomainError

from .admin_fixtures import as_user, login_as, no_remote_http, pg, tenants  # noqa: F401

BASE = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)  # 12:00 in America/Mexico_City


@pytest.fixture
def events(tenants):
    def add(key, action, *, user_kind='admin', days=0, outcome='success', institution=True, resource=None):
        tenants.q('INSERT INTO access_audit (id, occurred_at, actor_type, actor_user_id, action, resource_type, '
                  "resource_id, institution_id, outcome) VALUES (:id, :at, 'user', :u, :a, 'kit', :r, :i, :o)",
                  id=uuid.uuid4(), at=BASE + timedelta(days=days), u=tenants.users[user_kind][key], a=action,
                  r=resource or uuid.uuid4(), i=tenants.inst[key] if institution else None, o=outcome)
    tag = tenants.tag.upper()
    for day in range(3):
        add('A', f'A_EVENT_{tag}', days=day)
    add('A', f'A_DENIED_{tag}', user_kind='supervisor', outcome='denied', days=1)
    add('B', f'B_SECRET_{tag}', days=1)
    add('A', f'GLOBAL_{tag}', institution=False, days=1)  # no institution: never shown
    return tag


def test_page_shows_only_own_institution_rows(client, login_as, tenants, events, no_remote_http):
    login_as('admin', 'A')
    html = client.get(f'/admin/audit-log?search={events}').get_data(as_text=True)
    assert f'A_EVENT_{events}' in html and f'A_DENIED_{events}' in html
    assert f'B_SECRET_{events}' not in html and f'GLOBAL_{events}' not in html
    login_as('admin', 'B')
    html = client.get(f'/admin/audit-log?search={events}').get_data(as_text=True)
    assert f'B_SECRET_{events}' in html and f'A_EVENT_{events}' not in html


def test_direct_domain_call_cannot_cross_institutions(as_user, tenants, events, no_remote_http):
    with as_user('admin', 'A'):
        # Filtering by a user of B or by B's action yields nothing; scope is not a client filter.
        by_foreign_user = facade.get('/audit-events', user=str(tenants.admin['B']))
        by_foreign_action = facade.get('/audit-events', action=f'B_SECRET_{events}')
        everything = facade.get('/audit-events', search=events, per_page=50)
    assert by_foreign_user['total'] == 0 and by_foreign_action['total'] == 0
    assert {item['action'] for item in everything['items']} == {f'A_EVENT_{events}', f'A_DENIED_{events}'}
    assert str(tenants.admin['B']) not in {u['id'] for u in everything['options']['users']}
    assert f'B_SECRET_{events}' not in everything['options']['actions']
    assert f'GLOBAL_{events}' not in everything['options']['actions']


def test_filters_user_action_outcome_and_date_range(as_user, tenants, events):
    with as_user('admin', 'A'):
        denied = facade.get('/audit-events', search=events, outcome='denied')
        by_user = facade.get('/audit-events', search=events, user=str(tenants.users['supervisor']['A']))
        by_action = facade.get('/audit-events', action=f'A_EVENT_{events}')
        day_two = facade.get('/audit-events', search=events, date_from='2026-10-11', date_to='2026-10-11')
        from_day_two = facade.get('/audit-events', search=events, date_from='2026-10-11')
        legacy_day = facade.get('/audit-events', search=events, date='2026-10-12')
    assert [i['action'] for i in denied['items']] == [f'A_DENIED_{events}']
    assert [i['action'] for i in by_user['items']] == [f'A_DENIED_{events}']
    assert by_action['total'] == 3
    assert day_two['total'] == 2  # A_EVENT day 1 + A_DENIED day 1
    assert from_day_two['total'] == 3
    assert legacy_day['total'] == 1
    assert day_two['filters']['date_from'] == '2026-10-11' == day_two['filters']['date_to']


def test_empty_date_range_is_not_queried(client, login_as, as_user, tenants, events):
    with as_user('admin', 'A'):
        result = facade.get('/audit-events', search=events, date_from='2026-10-12', date_to='2026-10-10')
    assert result['empty_range'] is True and result['total'] == 0 and result['items'] == []
    login_as('admin', 'A')
    page = client.get('/admin/audit-log?date_from=2026-10-12&date_to=2026-10-10').get_data(as_text=True)
    assert 'The start date is after the end date' in page


def test_invalid_date_is_rejected(as_user, tenants):
    with as_user('admin', 'A'), pytest.raises(AdminDomainError) as exc:
        facade.get('/audit-events', date_from='12/10/2026')
    assert (exc.value.status, exc.value.code) == (400, 'invalid_date')


def test_pagination_runs_in_sql(as_user, tenants, events):
    with as_user('admin', 'A'):
        first = facade.get('/audit-events', search=events, per_page=10, page=1)
        beyond = facade.get('/audit-events', search=events, per_page=10, page=99)
    assert first['total'] == 4 and first['total_pages'] == 1 and first['shown'] == 4
    assert beyond['page'] == 1  # clamped to the last page
    assert [i['occurred_at'] for i in first['items']] == sorted((i['occurred_at'] for i in first['items']), reverse=True)


def test_only_it_admin_reads_the_log(client, login_as, as_user, tenants):
    login_as('supervisor', 'A')
    assert client.get('/admin/audit-log').status_code == 403
    with as_user('supervisor', 'A'), pytest.raises(AdminDomainError) as exc:
        facade.get('/audit-events')
    assert exc.value.status == 403
