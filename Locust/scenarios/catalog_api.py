"""
Type: catalog_api
Target: CatalogService reads through Gateway (families / kits / categories).
Host: PEF_GATEWAY_URL. Authenticated as operator (station_operator).
"""
from __future__ import annotations

import itertools

from locust import HttpUser, between, task

from common.auth import bearer_headers, login_auth_api
from common.config import OPERATORS, pick

_counter = itertools.count()


class CatalogApiUser(HttpUser):
    wait_time = between(1.2, 3.0)

    def on_start(self) -> None:
        self._idx = next(_counter)
        self.email, self.password = pick(OPERATORS, self._idx)
        self.access_token: str | None = None
        self._login()

    def _login(self) -> None:
        result = login_auth_api(self.client, email=self.email, password=self.password)
        self.access_token = result.get("access_token")

    def _ensure_token(self) -> bool:
        if self.access_token:
            return True
        self._login()
        return bool(self.access_token)

    def _get(self, path: str, name: str) -> None:
        if not self._ensure_token():
            return
        with self.client.get(
            path,
            headers=bearer_headers(self.access_token),
            name=name,
            catch_response=True,
        ) as response:
            if response.status_code == 401:
                self.access_token = None
                response.failure(f"{name} HTTP 401")
            elif response.status_code == 200:
                response.success()
            else:
                response.failure(f"{name} HTTP {response.status_code}")

    @task(5)
    def families(self) -> None:
        self._get("/api/v1/catalog/families", "GET /api/v1/catalog/families")

    @task(4)
    def kits(self) -> None:
        self._get("/api/v1/catalog/kits", "GET /api/v1/catalog/kits")

    @task(2)
    def categories(self) -> None:
        self._get("/api/v1/catalog/categories", "GET /api/v1/catalog/categories")

    @task(2)
    def instruments(self) -> None:
        self._get("/api/v1/catalog/instruments", "GET /api/v1/catalog/instruments")
