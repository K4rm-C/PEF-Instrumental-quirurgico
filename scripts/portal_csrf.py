"""MS-04 (P7): CSRF token for scripts that drive the portal with `requests`.

The portal refuses state-changing requests without its CSRF token (apps/BackendWebFlask/services/csrf.py).
A browser gets it from the page; a script asks GET /csrf-token with the SAME session (cookies) right before
each POST/PUT/PATCH/DELETE to the portal, so the token always matches the user signed in at that moment.
Requests to AuthService, the microservices or the API Gateway are left untouched (Bearer, no cookies).

    import portal_csrf; portal_csrf.enable(WEB)      # once, at import time of the script
"""
import os
from urllib.parse import urlsplit

import requests

_PORTALS: set[str] = set()
_UNSAFE = {'POST', 'PUT', 'PATCH', 'DELETE'}
_native_request = requests.Session.request


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f'{parts.scheme}://{parts.netloc}'.lower()


def _request(self, method, url, *args, **kwargs):
    if method.upper() in _UNSAFE and _origin(str(url)) in _PORTALS:
        headers = dict(kwargs.get('headers') or {})
        if 'X-CSRF-Token' not in headers:
            # Same cookies as the request itself (module-level requests.post(..., cookies=...) uses a
            # throw-away session): the token is bound to the user those cookies authenticate.
            issued = _native_request(self, 'GET', _origin(str(url)) + '/csrf-token', timeout=30,
                                     cookies=kwargs.get('cookies'))
            if issued.ok:
                headers['X-CSRF-Token'] = issued.json()['csrf_token']
                kwargs['headers'] = headers
    return _native_request(self, method, url, *args, **kwargs)


def enable(*bases: str) -> None:
    for base in (*bases, os.environ.get('PEF_WEB_URL', '')):
        if base:
            _PORTALS.add(_origin(base))
    requests.Session.request = _request
