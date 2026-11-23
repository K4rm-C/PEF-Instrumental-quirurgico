"""Login helpers: AuthService cookies / Bearer for Gateway and portal surfaces."""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

from locust.clients import HttpSession

from common.config import AUTH_URL, WEB_URL


def login_auth_api(
    client: HttpSession,
    *,
    email: str,
    password: str,
    auth_base: str | None = None,
    name: str = "POST /api/v1/auth/login",
    max_retries: int = 4,
) -> dict[str, Any]:
    """Sign in via versioned Auth API.

    When auth_base is None, posts to the Locust host path /api/v1/auth/login
    (typical Gateway host). When auth_base is set, posts to that absolute origin.

    Retries on HTTP 429 (Gateway login rate limit ~10/min/IP) so users are not left
    tokenless — that previously cascaded into mass 401s on API GETs.
    """
    if auth_base:
        url = f"{auth_base.rstrip('/')}/api/v1/auth/login"
    else:
        url = "/api/v1/auth/login"

    last_status = 0
    for attempt in range(max_retries):
        with client.post(
            url,
            json={"email": email, "password": password},
            name=name,
            catch_response=True,
        ) as response:
            last_status = response.status_code
            if response.status_code == 429:
                response.failure("login HTTP 429 (rate limited)")
                # Honor rate window before next attempt (do not leave user without token).
                time.sleep(6.0 + attempt * 3.0)
                continue
            if response.status_code != 200:
                response.failure(f"login HTTP {response.status_code}")
                return {}
            response.success()
            try:
                body = response.json()
            except Exception:
                body = {}
            # Versioned /api/v1/auth/login returns tokens in the JSON body (no Set-Cookie).
            access = body.get("access_token") or response.cookies.get("access_token")
            refresh = body.get("refresh_token") or response.cookies.get("refresh_token")
            return {
                "body": body,
                "access_token": access,
                "refresh_token": refresh,
                "cookies": response.cookies,
            }

    return {"error": f"login exhausted retries (last HTTP {last_status})"}


def login_legacy(
    client: HttpSession,
    *,
    email: str,
    password: str,
    auth_base: str = AUTH_URL,
    name: str = "POST /login (Auth)",
) -> str | None:
    """Legacy Auth /login (sets access_token cookie). Used to seed portal cookies."""
    url = f"{auth_base.rstrip('/')}/login"
    with client.post(
        url,
        json={"email": email, "password": password},
        name=name,
        catch_response=True,
    ) as response:
        if response.status_code != 200:
            response.failure(f"auth login HTTP {response.status_code}")
            return None
        token = response.cookies.get("access_token")
        if not token:
            response.failure("auth login missing access_token cookie")
            return None
        response.success()
        return token


def attach_portal_cookies(client: HttpSession, access_token: str, *, web_base: str = WEB_URL) -> None:
    """Copy Auth access_token onto the portal origin so RF pages authenticate."""
    host = urlsplit(web_base).hostname or "127.0.0.1"
    client.cookies.set("access_token", access_token, domain=host)


def portal_csrf(client: HttpSession, *, web_base: str = WEB_URL) -> str | None:
    """Fetch CSRF token for unsafe portal methods (same session cookies)."""
    url = f"{web_base.rstrip('/')}/csrf-token"
    with client.get(url, name="GET /csrf-token", catch_response=True) as response:
        if not response.ok:
            response.failure(f"csrf HTTP {response.status_code}")
            return None
        try:
            token = response.json().get("csrf_token")
        except Exception:
            token = None
        if not token:
            response.failure("csrf_token missing")
            return None
        response.success()
        return token


def bearer_headers(access_token: str | None) -> dict[str, str]:
    if not access_token:
        return {}
    return {"Authorization": f"Bearer {access_token}"}
