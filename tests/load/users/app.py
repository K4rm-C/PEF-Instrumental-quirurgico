"""or_charge (floor supervisor) on the Android App: the calls in PefApis.kt."""
from locust import task

import common
from users.base import AppUser as _AppBase, rows_of


class AppUser(_AppBase):
    weight = 3

    @task(4)
    def sessions(self):
        rows = rows_of(self.api(f'{common.SESSION}/api/v1/sessions', 'app GET /api/v1/sessions'))
        if rows and rows[0].get('id'):
            sid = rows[0]['id']
            self.api(f'{common.SESSION}/api/v1/sessions/{sid}', 'app GET /api/v1/sessions/[id]')
            self.api(f'{common.EVIDENCE}/api/v1/sessions/{sid}/evidence', 'app GET /api/v1/sessions/[id]/evidence')

    @task(2)
    def summary(self):
        self.api(f'{common.SESSION}/api/v1/sessions/reports/summary', 'app GET /api/v1/sessions/reports/summary')

    @task(4)
    def unread(self):
        self.api(f'{common.NOTIFICATIONS}/api/v1/notifications/unread-count',
                 'app GET /api/v1/notifications/unread-count')

    @task(2)
    def notifications(self):
        self.api(f'{common.NOTIFICATIONS}/api/v1/notifications', 'app GET /api/v1/notifications')

    @task(1)
    def catalog(self):
        self.api(f'{common.CATALOG}/api/v1/catalog/families', 'app GET /api/v1/catalog/families')
        self.api(f'{common.CATALOG}/api/v1/catalog/categories', 'app GET /api/v1/catalog/categories')

    @task(1)
    def me(self):
        self.api(f'{common.AUTH_API}/api/v1/auth/me', 'app GET /api/v1/auth/me')
