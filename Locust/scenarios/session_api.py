"""
Type: session_api
Target: SessionService list/detail through Gateway.
Host: PEF_GATEWAY_URL.

Keep user count low: Gateway login is rate-limited (~10/min/IP). A single 429 without
retry used to leave a user tokenless and flood 401s on every list call.
"""
from __future__ import annotations

import itertools

from locust import HttpUser, between, task

from common.auth import bearer_headers, login_auth_api
from common.config import OPERATORS, SUPERVISORS, pick

_counter = itertools.count()


class SessionApiUser(HttpUser):
    wait_time = between(1.5, 3.5)

    def on_start(self) -> None:
        self._idx = next(_counter)
        # Alternate OP / SPD for role-scoped lists
        pool = OPERATORS if self._idx % 2 == 0 else SUPERVISORS
        self.email, self.password = pick(pool, self._idx)
        self.access_token: str | None = None
        self.session_ids: list[str] = []
        self._login()

    def _login(self) -> None:
        result = login_auth_api(self.client, email=self.email, password=self.password)
        self.access_token = result.get("access_token")

    def _ensure_token(self) -> bool:
        if self.access_token:
            return True
        self._login()
        return bool(self.access_token)

    def _headers(self) -> dict[str, str]:
        return bearer_headers(self.access_token)

    def _parse_list(self, response) -> None:
        try:
            body = response.json()
        except Exception:
            return
        items = body if isinstance(body, list) else body.get("sessions") or body.get("items") or []
        ids: list[str] = []
        if isinstance(items, list):
            for row in items:
                if isinstance(row, dict) and row.get("id"):
                    ids.append(str(row["id"]))
        self.session_ids = ids[:20]

    @task(6)
    def list_sessions(self) -> None:
        if not self._ensure_token():
            return
        with self.client.get(
            "/api/v1/sessions",
            headers=self._headers(),
            name="GET /api/v1/sessions",
            catch_response=True,
        ) as response:
            if response.status_code == 401:
                self.access_token = None
                response.failure("list HTTP 401 (will re-auth)")
                if self._ensure_token():
                    with self.client.get(
                        "/api/v1/sessions",
                        headers=self._headers(),
                        name="GET /api/v1/sessions",
                        catch_response=True,
                    ) as retry:
                        if retry.status_code == 200:
                            retry.success()
                            self._parse_list(retry)
                        else:
                            retry.failure(f"list retry HTTP {retry.status_code}")
                return
            if response.status_code != 200:
                response.failure(f"list HTTP {response.status_code}")
                return
            response.success()
            self._parse_list(response)

    @task(3)
    def session_detail(self) -> None:
        if not self._ensure_token():
            return
        if not self.session_ids:
            self.list_sessions()
        if not self.session_ids:
            return
        sid = self.session_ids[self._idx % len(self.session_ids)]
        with self.client.get(
            f"/api/v1/sessions/{sid}",
            headers=self._headers(),
            name="GET /api/v1/sessions/{id}",
            catch_response=True,
        ) as response:
            if response.status_code == 401:
                self.access_token = None
                response.failure("detail HTTP 401")
            elif response.status_code == 200:
                response.success()
            else:
                response.failure(f"detail HTTP {response.status_code}")

    @task(1)
    def list_paginated(self) -> None:
        if not self._ensure_token():
            return
        with self.client.get(
            "/api/v1/sessions?page=1&per_page=20",
            headers=self._headers(),
            name="GET /api/v1/sessions?page",
            catch_response=True,
        ) as response:
            if response.status_code == 401:
                self.access_token = None
                response.failure("page HTTP 401")
            elif response.status_code == 200:
                response.success()
            else:
                response.failure(f"page HTTP {response.status_code}")
