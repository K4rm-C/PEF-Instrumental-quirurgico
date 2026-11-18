"""MS-04 (P7) — AuthService transversal security.

- Cookie-authenticated state-changing routes (used by the portal's browser code) refuse foreign origins;
  Bearer requests and requests without ambient cookies are not affected.
- Auth cookies: HttpOnly, SameSite=Lax by default, Secure from AUTH_COOKIE_SECURE.
- X-Forwarded-For is only trusted from AUTH_TRUSTED_PROXIES proxies (right-most entries), so a client
  can no longer choose the address the login lockout counts.
"""

from __future__ import annotations

import importlib

import pytest

import main

ALLOWED = "http://portal.test"


@pytest.fixture
def origins(monkeypatch):
    monkeypatch.setattr(main, "allowed_origins", {ALLOWED})
    return ALLOWED


def _cookie_logout(client, token, **headers):
    client.set_cookie("access_token", token, domain="localhost")
    return client.post("/logout", headers=headers)


def test_cookie_logout_from_a_foreign_origin_is_refused(client, world, mint, origins, store):
    token = mint(world.users["operator_a"], world.institution_a)
    response = _cookie_logout(client, token, Origin="https://evil.example")
    assert response.status_code == 403 and response.get_json()["error"] == "Cross-site request refused"
    # Nothing was revoked: the same token still verifies.
    assert client.get("/verify", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_cookie_logout_from_a_foreign_referer_is_refused(client, world, mint, origins):
    token = mint(world.users["operator_a"], world.institution_a)
    response = _cookie_logout(client, token, Referer="https://evil.example/page")
    assert response.status_code == 403


def test_null_origin_is_refused(client, world, mint, origins):
    token = mint(world.users["operator_a"], world.institution_a)
    assert _cookie_logout(client, token, Origin="null").status_code == 403


def test_cookie_logout_from_the_portal_origin_works(client, world, mint, origins):
    token = mint(world.users["operator_a"], world.institution_a)
    response = _cookie_logout(client, token, Origin=ALLOWED)
    assert response.status_code == 200
    assert client.get("/verify", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_cookie_refresh_and_preferences_from_a_foreign_origin_are_refused(client, world, mint, origins):
    refresh = mint(world.users["operator_a"], world.institution_a, token_type="refresh")
    client.set_cookie("refresh_token", refresh, domain="localhost")
    assert client.post("/refresh", headers={"Origin": "https://evil.example"}).status_code == 403
    access = mint(world.users["operator_a"], world.institution_a)
    client.set_cookie("access_token", access, domain="localhost")
    response = client.post("/preferences", json={"locale": "en"}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_bearer_requests_are_not_cookie_requests(client, world, mint, origins):
    """A Bearer call cannot be forged cross-site (no ambient credential): the origin is not checked."""
    token = mint(world.users["operator_a"], world.institution_a)
    response = client.post("/logout", headers={"Authorization": f"Bearer {token}", "Origin": "https://evil.example"})
    assert response.status_code == 200


def test_requests_without_cookies_or_origin_are_not_blocked_by_the_guard(client, world, mint, origins):
    # No cookie: nothing ambient to abuse; the route answers by itself (no token → 401).
    assert client.post("/logout", headers={"Origin": "https://evil.example"}).status_code == 401
    # Cookie but no Origin/Referer: not a cross-site browser request (non-browser client).
    token = mint(world.users["operator_a"], world.institution_a)
    assert _cookie_logout(client, token).status_code == 200


def test_safe_methods_are_not_checked(client, world, mint, origins):
    token = mint(world.users["operator_a"], world.institution_a)
    client.set_cookie("access_token", token, domain="localhost")
    assert client.get("/verify", headers={"Origin": "https://evil.example"}).status_code == 200


def test_auth_cookies_are_httponly_and_lax(client, world, origins, monkeypatch):
    from conftest import PASSWORD
    monkeypatch.setitem(main.app.config, "COOKIE_SAMESITE", "Lax")
    monkeypatch.setitem(main.app.config, "COOKIE_SECURE", True)
    response = client.post("/login", json={"email": world.emails["operator_a"], "password": PASSWORD},
                           headers={"Origin": ALLOWED})
    assert response.status_code == 200
    cookies = [value for key, value in response.headers.items() if key.lower() == "set-cookie"]
    assert {c.split("=", 1)[0] for c in cookies} == {"access_token", "refresh_token"}
    for cookie in cookies:
        assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" in cookie and "Path=/" in cookie


def test_samesite_default_is_lax(monkeypatch):
    """Without AUTH_COOKIE_SAMESITE the code default is Lax (it was None before MS-04)."""
    monkeypatch.delenv("AUTH_COOKIE_SAMESITE", raising=False)
    source = importlib.util.find_spec("main").origin
    text = open(source, encoding="utf-8").read()
    assert 'os.getenv("AUTH_COOKIE_SAMESITE", "Lax")' in text


@pytest.mark.parametrize("hops, header, expected", [
    (0, "203.0.113.7", "10.0.0.5"),                    # not behind a trusted proxy: header ignored
    (0, "203.0.113.7, 198.51.100.1", "10.0.0.5"),
    (1, "203.0.113.7", "203.0.113.7"),                  # behind the gateway: the address it added
    (1, "6.6.6.6, 203.0.113.7", "203.0.113.7"),         # a spoofed first value is ignored
    (2, "6.6.6.6, 203.0.113.7, 10.1.1.1", "203.0.113.7"),
    (1, "", "10.0.0.5"),
])
def test_client_ip_only_from_trusted_proxies(monkeypatch, hops, header, expected):
    monkeypatch.setitem(main.app.config, "TRUSTED_PROXIES", hops)
    environ = {"REMOTE_ADDR": "10.0.0.5"}
    if header:
        environ["HTTP_X_FORWARDED_FOR"] = header
    with main.app.test_request_context("/login", environ_base=environ):
        assert main.client_ip_from_request() == expected
