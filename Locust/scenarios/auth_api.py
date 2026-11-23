"""
Type: auth_api
Target: Auth identity API through the Gateway (login / me / refresh).
Note: Gateway rate-limits login (~10/min/IP). Prefer few users; reuse sessions.
DS09 /api/v1/auth/refresh requires JSON body {"refresh_token": "..."}; cookie-only → 400.
"""
from __future__ import annotations

from locust import HttpUser, between, task

from common.auth import bearer_headers, login_auth_api
from common.config import OPERATORS, pick


class AuthApiUser(HttpUser):
    wait_time = between(2.0, 5.0)

    def on_start(self) -> None:
        email, password = pick(OPERATORS, self.environment.runner.user_count if self.environment.runner else 0)
        self.email = email
        self.password = password
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self._login()

    def _login(self) -> None:
        result = login_auth_api(self.client, email=self.email, password=self.password)
        self.access_token = result.get("access_token")
        self.refresh_token = result.get("refresh_token")

    @task(8)
    def me(self) -> None:
        if not self.access_token:
            self._login()
            return
        self.client.get(
            "/api/v1/auth/me",
            headers=bearer_headers(self.access_token),
            name="GET /api/v1/auth/me",
        )

    @task(2)
    def refresh(self) -> None:
        if not self.refresh_token:
            self._login()
            return
        with self.client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": self.refresh_token},
            name="POST /api/v1/auth/refresh",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                try:
                    body = response.json()
                except Exception:
                    body = {}
                self.access_token = body.get("access_token") or self.access_token
                # Rotation: old refresh is revoked; keep the new one.
                self.refresh_token = body.get("refresh_token") or self.refresh_token
                response.success()
            elif response.status_code == 401:
                # Expired / revoked under concurrency — re-login next cycle.
                self.access_token = None
                self.refresh_token = None
                response.success()
            else:
                response.failure(f"refresh HTTP {response.status_code}")

    @task(1)
    def re_login(self) -> None:
        self._login()
