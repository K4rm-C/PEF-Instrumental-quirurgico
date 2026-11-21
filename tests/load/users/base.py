"""Base classes: a portal user (cookies + Bearer taken from the cookie) and an App user (JSON tokens)."""
from __future__ import annotations

import random

from locust import HttpUser, between

import common


def items(body):
    """SessionService/Catalog lists come as a JSON list or wrapped in an object."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for value in body.values():
            if isinstance(value, list):
                return value
    return []


def rows_of(response):
    if response is None or response.status_code != 200:
        return []
    try:
        return items(response.json())
    except ValueError:
        return []


class PortalUser(HttpUser):
    abstract = True
    host = common.WEB
    wait_time = between(2, 6)   # think time of a person reading a page
    pool = 'operator'

    def on_start(self):
        self.email = common.next_account(self.pool)
        common.portal_login(self.client, self.email)

    def on_stop(self):
        self.client.post(f'{common.AUTH}/logout', name='auth POST /logout')

    def api(self, url, name, **kwargs):
        """Bearer call to a microservice, reusing the portal access cookie."""
        return common.api_get(self.client, url, name, lambda: common.cookie_bearer(self.client),
                              lambda: common.portal_refresh(self.client), **kwargs)

    def pick(self, url, name):
        rows = rows_of(self.api(url, name))
        return random.choice(rows) if rows else None


class AppUser(HttpUser):
    """or_charge on the Android App: tokens in the JSON body, Bearer only (PefApis.kt)."""
    abstract = True
    host = common.AUTH_API
    wait_time = between(3, 8)
    pool = 'app'

    def on_start(self):
        self.email = common.next_account(self.pool)
        self.tokens = {}
        with self.client.post(f'{common.AUTH_API}/api/v1/auth/login', name='app POST /api/v1/auth/login',
                              json={'email': self.email, 'password': common.password_for(self.email)},
                              catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f'login {self.email}: {r.status_code}')
                raise RuntimeError(f'App login failed for {self.email}: {r.status_code}')
            self.tokens = r.json()

    def _refresh(self) -> bool:
        with self.client.post(f'{common.AUTH_API}/api/v1/auth/refresh', name='app POST /api/v1/auth/refresh',
                              json={'refresh_token': self.tokens.get('refresh_token')}, catch_response=True) as r:
            if r.status_code == 200:
                self.tokens = r.json()
                return True
            r.failure(f'HTTP {r.status_code}')
        return False

    def bearer(self):
        return {'Authorization': f'Bearer {self.tokens.get("access_token")}'}

    def api(self, url, name, **kwargs):
        return common.api_get(self.client, url, name, self.bearer, self._refresh, **kwargs)

    def on_stop(self):
        self.client.post(f'{common.AUTH_API}/api/v1/auth/logout', headers=self.bearer(),
                         json={'refresh_token': self.tokens.get('refresh_token')}, name='app POST /api/v1/auth/logout')
