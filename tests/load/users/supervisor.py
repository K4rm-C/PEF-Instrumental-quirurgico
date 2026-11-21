"""SPD supervisor: portal pages plus the SessionService / Catalog APIs behind them."""
from locust import task

import common
from users.base import PortalUser


class SupervisorUser(PortalUser):
    weight = 3
    pool = 'supervisor'

    @task(4)
    def dashboard(self):
        common.page(self.client, '/supervisor/dashboard')

    @task(4)
    def sessions(self):
        common.page(self.client, '/supervisor/sessions')

    @task(2)
    def history(self):
        common.page(self.client, '/supervisor/sessions/history')

    @task(2)
    def session_detail(self):
        row = self.pick(f'{common.SESSION}/api/v1/sessions', 'session GET /api/v1/sessions')
        if row and row.get('id'):
            common.page(self.client, f'/supervisor/sessions/{row["id"]}', name='/supervisor/sessions/[id]')
            self.api(f'{common.SESSION}/api/v1/sessions/{row["id"]}/counts',
                     'session GET /api/v1/sessions/[id]/counts')

    @task(2)
    def discrepancies(self):
        common.page(self.client, '/supervisor/discrepancies')
        self.api(f'{common.SESSION}/api/v1/sessions/discrepancies?status=open',
                 'session GET /api/v1/sessions/discrepancies')

    @task(2)
    def reports(self):
        common.page(self.client, '/supervisor/reports')
        self.api(f'{common.SESSION}/api/v1/sessions/reports/summary',
                 'session GET /api/v1/sessions/reports/summary')

    @task(1)
    def indicators(self):
        common.page(self.client, '/supervisor/indicators')

    @task(2)
    def catalog(self):
        self.api(f'{common.CATALOG}/api/v1/catalog/kits', 'catalog GET /api/v1/catalog/kits')
        self.api(f'{common.CATALOG}/api/v1/catalog/families', 'catalog GET /api/v1/catalog/families')

    @task(1)
    def instruments(self):
        common.page(self.client, '/admin/instruments')

    @task(1)
    def services_health(self):
        common.page(self.client, '/supervisor/services-health')

    @task(2)
    def notifications(self):
        common.page(self.client, '/notifications/unread-count')
