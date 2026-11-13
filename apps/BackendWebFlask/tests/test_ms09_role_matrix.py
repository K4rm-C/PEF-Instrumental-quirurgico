"""MS-09: navigation and permission matrix of the four product roles in the portal.

Each role reaches its own area and gets 403 in the others; `or_charge` (floor supervisor) keeps exactly what it
had: no new permission was granted without a functional definition. The same matrix is enforced by every
microservice (their own suites) and through the gateway (scripts/e2e_gateway.py).
"""

import pytest

from controllers import report_routes
from services import monitoring_remote, notifications_remote, session_remote, trays_remote

AREAS = {
    'admin': ['/admin/dashboard', '/admin/configuration', '/admin/users', '/admin/audit-log'],
    'supervisor': ['/supervisor/dashboard', '/supervisor/sessions', '/supervisor/reports', '/supervisor/trays',
                   '/supervisor/services-health', '/supervisor/discrepancies'],
    'operator': ['/operator/sessions'],
}
OWN = {'it_admin': 'admin', 'spd_supervisor': 'supervisor', 'station_operator': 'operator', 'or_charge': None}


@pytest.fixture
def offline(monkeypatch):
    """No microservice configured: pages render their controlled notices; permissions are decided before."""
    for module, name in ((session_remote, 'SESSION_SERVICE_URL'), (monitoring_remote, 'MONITORING_SERVICE_URL'),
                         (notifications_remote, 'NOTIFICATIONS_SERVICE_URL'), (trays_remote, 'CATALOG_SERVICE_URL')):
        monkeypatch.setattr(module, name, '')
    monkeypatch.setattr(report_routes, 'use_v3_fixture', lambda *a: True)


@pytest.mark.parametrize('role', list(OWN))
def test_each_role_reaches_only_its_area(client, login, offline, role):
    login(role)
    for area, urls in AREAS.items():
        for url in urls:
            status = client.get(url).status_code
            if OWN[role] == area:
                assert status not in (401, 403), (role, url, status)
            else:
                assert status in (403, 302), (role, url, status)


def test_or_charge_is_not_granted_any_area(client, login, offline):
    login('or_charge')
    allowed = [url for urls in AREAS.values() for url in urls if client.get(url).status_code not in (403, 302)]
    assert allowed == []


def test_without_a_session_every_area_redirects_to_sign_in(client, offline):
    for urls in AREAS.values():
        for url in urls:
            response = client.get(url)
            assert response.status_code == 302 and '/sign-in' in response.headers['Location'], url
