"""Station operator browsing the portal (RF-OP): dashboard, sessions, history, notifications."""
import random

from locust import task

import common
from users.base import PortalUser, rows_of


class OperatorUser(PortalUser):
    weight = 4
    pool = 'operator'

    # /operator/dashboard only redirects to the legacy page; the operator home is the session list.
    @task(8)
    def sessions(self):
        common.page(self.client, '/operator/sessions')

    @task(2)
    def history(self):
        common.page(self.client, '/operator/sessions/history')

    @task(2)
    def session_detail(self):
        # SessionService lists institution sessions; the portal detail only opens the operator's own (else 404).
        own = common.USER_IDS.get(self.email)
        rows = [r for r in rows_of(self.api(f'{common.SESSION}/api/v1/sessions', 'session GET /api/v1/sessions'))
                if r.get('id') and (own is None or r.get('user_id') == own)]
        if rows:
            row = random.choice(rows)
            common.page(self.client, f'/operator/sessions/{row["id"]}', name='/operator/sessions/[id]')

    @task(3)
    def unread_notifications(self):
        common.page(self.client, '/notifications/unread-count')

    @task(1)
    def profile(self):
        common.page(self.client, '/operator/profile')
