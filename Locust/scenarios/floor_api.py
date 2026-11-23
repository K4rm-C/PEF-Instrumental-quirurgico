"""
Type: floor_api
Target: OR charge / floor App-style API via Gateway (sessions list + auth me).
Host: PEF_GATEWAY_URL.
"""
from __future__ import annotations

import itertools

from locust import HttpUser, between, task

from common.auth import bearer_headers, login_auth_api
from common.config import FLOOR, pick

_counter = itertools.count()


class FloorApiUser(HttpUser):
    wait_time = between(1.0, 3.0)

    def on_start(self) -> None:
        self._idx = next(_counter)
        email, password = pick(FLOOR, self._idx)
        result = login_auth_api(self.client, email=email, password=password)
        self.access_token = result.get("access_token")
        if not self.access_token and result.get("cookies"):
            self.access_token = result["cookies"].get("access_token")

    @task(6)
    def list_sessions(self) -> None:
        self.client.get(
            "/api/v1/sessions",
            headers=bearer_headers(self.access_token),
            name="GET /api/v1/sessions",
        )

    @task(3)
    def me(self) -> None:
        self.client.get(
            "/api/v1/auth/me",
            headers=bearer_headers(self.access_token),
            name="GET /api/v1/auth/me",
        )

    @task(2)
    def notifications(self) -> None:
        self.client.get(
            "/api/v1/notifications",
            headers=bearer_headers(self.access_token),
            name="GET /api/v1/notifications",
        )
