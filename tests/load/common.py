"""Shared helpers for the PEF Locust suite: URLs, demo credentials, login/refresh, CSRF and checks.

Every user talks to the services directly (integral stack, 127.0.0.1:5000-5008) using absolute URLs,
so one Locust user can call the portal, AuthService and the microservices with the same session.
Set PEF_API_URL to send the Bearer/API traffic through the gateway (e.g. http://127.0.0.1:8080).
"""
from __future__ import annotations

import itertools
import os
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _url(name: str, default: str) -> str:
    return os.environ.get(name, default).rstrip('/')


_GATEWAY = os.environ.get('PEF_API_URL', '').rstrip('/')
WEB = _url('PEF_WEB_URL', 'http://127.0.0.1:5000')
AUTH = _url('PEF_AUTH_URL', 'http://127.0.0.1:5001')          # portal cookie login (/login, /refresh)
AUTH_API = _GATEWAY or AUTH                                     # /api/v1/auth/* (App)
CATALOG = _GATEWAY or _url('PEF_CATALOG_URL', 'http://127.0.0.1:5003')
SESSION = _GATEWAY or _url('PEF_SESSION_URL', 'http://127.0.0.1:5004')
EVIDENCE = _GATEWAY or _url('PEF_EVIDENCE_URL', 'http://127.0.0.1:5005')
NOTIFICATIONS = _GATEWAY or _url('PEF_NOTIFICATIONS_URL', 'http://127.0.0.1:5007')
MONITORING = _url('PEF_MONITORING_URL', 'http://127.0.0.1:5008')  # reads are not published on the gateway

# Seed ids (data/seeds/003_seed_demo.sql and 004_seed_demo_locust.sql).
INSTITUTION = '11111111-1111-1111-1111-111111111111'
KIT = os.environ.get('PEF_LOCUST_KIT', '51515151-0000-4000-8000-000000000001')        # Video Demo Kit 1
ROOM = os.environ.get('PEF_LOCUST_ROOM', '12121212-0000-4000-8000-000000000001')      # OR-01
STATION = os.environ.get('PEF_LOCUST_STATION', '13131313-0000-4000-8000-000000000001')  # CS-01
PATIENT = '31313131-0000-4000-8000-000000000001'
PHYSICIAN = '32323232-0000-4000-8000-000000000001'
USER_IDS = {
    'operator@instrumed.com': '22222222-2222-2222-2222-222222222221',
    'operador2@instrumed.com': '22222222-2222-2222-2222-222222222224',
    'ana.ruiz@instrumed.com': '22222222-2222-2222-2222-222222222231',
    'luis.pena@instrumed.com': '22222222-2222-2222-2222-222222222232',
}


def _pool(env: str, default: str) -> list[str]:
    return [e.strip() for e in os.environ.get(env, default).split(',') if e.strip()]


POOLS = {
    'operator': _pool('PEF_LOCUST_OPERATORS',
                      'operator@instrumed.com,operador2@instrumed.com,ana.ruiz@instrumed.com,luis.pena@instrumed.com'),
    'supervisor': _pool('PEF_LOCUST_SUPERVISORS',
                        'supervisor@instrumed.com,supervisor2@instrumed.com,carla.soto@instrumed.com'),
    'admin': _pool('PEF_LOCUST_ADMINS', 'admin@instrumed.com,ti.extra@instrumed.com'),
    'app': _pool('PEF_LOCUST_APP', 'floor@instrumed.com,nora.diaz@instrumed.com'),
    # luis.pena already has an in_progress session in 004; the flow uses the others.
    'flow_operator': _pool('PEF_LOCUST_FLOW_OPERATORS', 'operador2@instrumed.com,ana.ruiz@instrumed.com'),
    'flow_supervisor': _pool('PEF_LOCUST_FLOW_SUPERVISORS', 'supervisor@instrumed.com,carla.soto@instrumed.com'),
}

# --- Credentials (never committed: data/seeds/DEMO_CREDENTIALS.local.md is gitignored) ---------------
CREDENTIALS_FILE = Path(os.environ.get('PEF_LOCUST_CREDENTIALS', ROOT / 'data/seeds/DEMO_CREDENTIALS.local.md'))
_ROW = re.compile(r'^\|\s*`([^`]+@[^`]+)`\s*\|\s*`([^`]+)`\s*\|', re.MULTILINE)
_passwords: dict[str, str] | None = None


def password_for(email: str) -> str:
    global _passwords
    if _passwords is None:
        if not CREDENTIALS_FILE.is_file():
            raise RuntimeError(f'Demo credentials not found: {CREDENTIALS_FILE} (set PEF_LOCUST_CREDENTIALS)')
        _passwords = dict(_ROW.findall(CREDENTIALS_FILE.read_text(encoding='utf-8')))
    try:
        return _passwords[email]
    except KeyError:
        raise RuntimeError(f'No password for {email} in {CREDENTIALS_FILE}') from None


_cycles: dict[str, itertools.cycle] = {}
_lock = threading.Lock()


def next_account(pool: str) -> str:
    """Round-robin over a pool so concurrent Locust users spread across the demo accounts."""
    with _lock:
        if pool not in _cycles:
            _cycles[pool] = itertools.cycle(POOLS[pool])
        return next(_cycles[pool])


# --- Portal (cookie) users -------------------------------------------------------------------------
def portal_login(client, email: str) -> None:
    """POST AuthService /login: access/refresh tokens arrive as host-only cookies, also sent to :5000."""
    with client.post(f'{AUTH}/login', json={'email': email, 'password': password_for(email)},
                     name='auth POST /login', catch_response=True) as r:
        if r.status_code != 200 or not client.cookies.get('access_token'):
            r.failure(f'login {email}: {r.status_code}')
            raise RuntimeError(f'login failed for {email}: {r.status_code}')


def portal_refresh(client) -> bool:
    with client.post(f'{AUTH}/refresh', name='auth POST /refresh', catch_response=True) as r:
        if r.status_code != 200:
            r.failure(f'refresh: {r.status_code}')
            return False
    return True


def cookie_bearer(client) -> dict:
    return {'Authorization': f'Bearer {client.cookies.get("access_token")}'}


def page(client, path: str, name: str | None = None, expect: str | None = None):
    """GET a portal page without following redirects: a 302 to /sign-in is a failure, not a success."""
    with client.get(f'{WEB}{path}', name=name or path, allow_redirects=False, catch_response=True) as r:
        if r.status_code in (301, 302, 303) and '/sign-in' in r.headers.get('Location', ''):
            r.failure('redirected to sign-in (session expired?)')
        elif r.status_code != 200:
            r.failure(f'HTTP {r.status_code}')
        elif expect and expect not in r.text:
            r.failure(f'missing marker {expect!r}')
        return r


def csrf_headers(client) -> dict:
    """The portal requires X-CSRF-Token on every unsafe request (services/csrf.py)."""
    with client.get(f'{WEB}/csrf-token', name='portal GET /csrf-token', catch_response=True) as r:
        if r.status_code != 200:
            r.failure(f'HTTP {r.status_code}')
            return {}
        body = r.json()
    return {body.get('header', 'X-CSRF-Token'): body['csrf_token']}


def portal_post(client, path: str, data: dict, name: str, ok_location: str | None = None):
    """Form POST to the portal with CSRF; the portal answers 302 on success."""
    headers = {**csrf_headers(client), 'Referer': f'{WEB}{path}'}
    with client.post(f'{WEB}{path}', data=data, headers=headers, name=name,
                     allow_redirects=False, catch_response=True) as r:
        location = r.headers.get('Location', '')
        if r.status_code != 302 or '/sign-in' in location:
            r.failure(f'HTTP {r.status_code} {location}')
        elif ok_location and ok_location not in location:
            r.failure(f'unexpected redirect {location}')
        return r


def api_get(client, url: str, name: str, headers_fn, refresh_fn, **kwargs):
    """GET a Bearer API; on 401 refresh once and retry (access tokens live 15 minutes)."""
    with client.get(url, headers=headers_fn(), name=name, catch_response=True, **kwargs) as r:
        if r.status_code == 401:
            r.success()   # expected expiry: not counted as an error, the retry below is measured
            retry = True
        else:
            retry = False
            if r.status_code != 200:
                r.failure(f'HTTP {r.status_code}')
            return r
    if retry and refresh_fn():
        with client.get(url, headers=headers_fn(), name=name, catch_response=True, **kwargs) as r:
            if r.status_code != 200:
                r.failure(f'HTTP {r.status_code} after refresh')
            return r
    return None
