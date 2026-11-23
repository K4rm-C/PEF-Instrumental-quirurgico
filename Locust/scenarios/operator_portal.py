"""
Type: operator_portal
Target: Operator RF portal (read-heavy: list / history / session pages).
Host: PEF_WEB_URL. Auth via AuthService cookie mirrored to the portal.
"""
from __future__ import annotations

import itertools
import re

from locust import HttpUser, between, task

from common.auth import attach_portal_cookies, login_legacy
from common.config import AUTH_URL, OPERATORS, pick

_SESSION_RE = re.compile(
    r"/operator/sessions/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
_counter = itertools.count()


class OperatorPortalUser(HttpUser):
    wait_time = between(1.0, 3.0)

    def on_start(self) -> None:
        self._idx = next(_counter)
        email, password = pick(OPERATORS, self._idx)
        token = login_legacy(self.client, email=email, password=password, auth_base=AUTH_URL)
        if token:
            attach_portal_cookies(self.client, token, web_base=self.host.rstrip("/"))
        self.session_ids: list[str] = []
        self._refresh_session_ids()

    def _refresh_session_ids(self) -> None:
        with self.client.get("/operator/sessions", name="GET /operator/sessions", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"operator list HTTP {response.status_code}")
                return
            response.success()
            found = _SESSION_RE.findall(response.text or "")
            seen: set[str] = set()
            ids: list[str] = []
            for sid in found:
                key = sid.lower()
                if key not in seen:
                    seen.add(key)
                    ids.append(key)
            self.session_ids = ids[:12]

    @task(5)
    def list_sessions(self) -> None:
        self._refresh_session_ids()

    @task(2)
    def history(self) -> None:
        self.client.get("/operator/sessions/history", name="GET /operator/sessions/history")

    @task(4)
    def open_session(self) -> None:
        if not self.session_ids:
            self._refresh_session_ids()
        if not self.session_ids:
            return
        sid = self.session_ids[self._idx % len(self.session_ids)]
        url = f"/operator/sessions/{sid}"
        # One soft retry: portal sometimes drops keep-alive under concurrent HTML renders.
        with self.client.get(url, name="GET /operator/sessions/{id}", catch_response=True) as response:
            if response.status_code == 0 or response.error:
                with self.client.get(url, name="GET /operator/sessions/{id}", catch_response=True) as retry:
                    if retry.status_code == 200:
                        retry.success()
                    elif retry.status_code == 0 or retry.error:
                        retry.failure(f"connection: {retry.error or 'empty'}")
                    else:
                        retry.failure(f"session detail HTTP {retry.status_code}")
                response.success()
                return
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"session detail HTTP {response.status_code}")

    @task(1)
    def sign_in_page(self) -> None:
        self.client.get("/sign-in", name="GET /sign-in")
