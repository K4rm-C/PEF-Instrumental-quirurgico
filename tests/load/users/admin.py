"""IT admin: low-frequency administration pages."""
from locust import between, task

import common
from users.base import PortalUser


class AdminUser(PortalUser):
    weight = 1
    pool = 'admin'
    wait_time = between(4, 10)

    @task(3)
    def dashboard(self):
        common.page(self.client, '/admin/dashboard')

    @task(2)
    def users(self):
        common.page(self.client, '/admin/users')

    @task(1)
    def families(self):
        common.page(self.client, '/admin/instrument-families')

    @task(1)
    def procedures(self):
        common.page(self.client, '/admin/procedures')

    @task(1)
    def audit_log(self):
        common.page(self.client, '/admin/audit-log')

    @task(1)
    def system_status(self):
        common.page(self.client, '/admin/system-status')
