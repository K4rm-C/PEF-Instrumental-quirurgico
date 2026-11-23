"""
Type: admin_portal
Target: IT Admin + SPD kit surfaces (dashboard, kits, users, families).
Host: PEF_WEB_URL.

Portal require_role() checks the JWT *primary* role only. Dual it_admin+spd still
presents primary it_admin → 403 on /admin/kits. Kits traffic must use spd_supervisor.
"""
from __future__ import annotations

import itertools

from locust import HttpUser, between, task

from common.auth import attach_portal_cookies, login_legacy
from common.config import ADMINS, AUTH_URL, SUPERVISORS, pick

_counter = itertools.count()


class AdminItUser(HttpUser):
    """it_admin: dashboard / users / families / procedures / profile (no kits)."""

    weight = 2
    wait_time = between(1.5, 4.0)

    def on_start(self) -> None:
        self._idx = next(_counter)
        email, password = pick(ADMINS, self._idx)
        token = login_legacy(self.client, email=email, password=password, auth_base=AUTH_URL)
        if token:
            attach_portal_cookies(self.client, token, web_base=self.host.rstrip("/"))

    @task(4)
    def dashboard(self) -> None:
        self.client.get("/admin/dashboard", name="GET /admin/dashboard")

    @task(3)
    def users(self) -> None:
        self.client.get("/admin/users", name="GET /admin/users")

    @task(2)
    def families(self) -> None:
        self.client.get("/admin/instrument-families", name="GET /admin/instrument-families")

    @task(2)
    def procedures(self) -> None:
        self.client.get("/admin/procedures", name="GET /admin/procedures")

    @task(1)
    def profile(self) -> None:
        self.client.get("/admin/profile", name="GET /admin/profile")


class AdminKitsUser(HttpUser):
    """spd_supervisor: /admin/kits (SPD RACI) + shared catalog admin reads."""

    weight = 2
    wait_time = between(1.5, 4.0)

    def on_start(self) -> None:
        self._idx = next(_counter)
        email, password = pick(SUPERVISORS, self._idx)
        token = login_legacy(self.client, email=email, password=password, auth_base=AUTH_URL)
        if token:
            attach_portal_cookies(self.client, token, web_base=self.host.rstrip("/"))

    @task(4)
    def kits(self) -> None:
        self.client.get("/admin/kits", name="GET /admin/kits")

    @task(2)
    def instruments(self) -> None:
        # Same RACI surface as kits (SPD owns pieces).
        self.client.get("/admin/instruments", name="GET /admin/instruments")
