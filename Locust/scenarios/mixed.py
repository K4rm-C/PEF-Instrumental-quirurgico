"""
Type: mixed
Target: Concurrent Operator portal + SPD portal + catalog API traffic.
Host: overridden per user class via on_start absolute URLs where needed.

Locust host should be the portal (PEF_WEB_URL). Catalog/API calls use Gateway absolute URLs.
"""
from __future__ import annotations

import itertools

from locust import HttpUser, between, task

from common.auth import attach_portal_cookies, bearer_headers, login_auth_api, login_legacy
from common.config import GATEWAY_URL, OPERATORS, SUPERVISORS, pick

_counter = itertools.count()


class MixedOperatorUser(HttpUser):
    weight = 3
    wait_time = between(1.0, 3.0)

    def on_start(self) -> None:
        self._idx = next(_counter)
        email, password = pick(OPERATORS, self._idx)
        token = login_legacy(self.client, email=email, password=password)
        if token:
            attach_portal_cookies(self.client, token, web_base=self.host.rstrip("/"))

    @task(4)
    def operator_list(self) -> None:
        self.client.get("/operator/sessions", name="GET /operator/sessions")

    @task(2)
    def operator_history(self) -> None:
        self.client.get("/operator/sessions/history", name="GET /operator/sessions/history")


class MixedSpdUser(HttpUser):
    weight = 2
    wait_time = between(1.0, 3.0)

    def on_start(self) -> None:
        self._idx = next(_counter)
        email, password = pick(SUPERVISORS, self._idx)
        token = login_legacy(self.client, email=email, password=password)
        if token:
            attach_portal_cookies(self.client, token, web_base=self.host.rstrip("/"))

    @task(4)
    def spd_list(self) -> None:
        self.client.get("/supervisor/sessions", name="GET /supervisor/sessions")

    @task(1)
    def spd_history(self) -> None:
        self.client.get("/supervisor/sessions/history", name="GET /supervisor/sessions/history")


class MixedCatalogUser(HttpUser):
    weight = 2
    wait_time = between(1.0, 2.5)

    def on_start(self) -> None:
        self._idx = next(_counter)
        email, password = pick(OPERATORS, self._idx)
        # Login against Gateway absolute URL so Bearer works for catalog.
        result = login_auth_api(
            self.client,
            email=email,
            password=password,
            auth_base=GATEWAY_URL,
            name="POST Gateway /api/v1/auth/login",
        )
        self.access_token = result.get("access_token")
        if not self.access_token and result.get("cookies"):
            self.access_token = result["cookies"].get("access_token")

    @task(3)
    def families(self) -> None:
        self.client.get(
            f"{GATEWAY_URL}/api/v1/catalog/families",
            headers=bearer_headers(self.access_token),
            name="GET Gateway /api/v1/catalog/families",
        )

    @task(2)
    def kits(self) -> None:
        self.client.get(
            f"{GATEWAY_URL}/api/v1/catalog/kits",
            headers=bearer_headers(self.access_token),
            name="GET Gateway /api/v1/catalog/kits",
        )
