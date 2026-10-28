"""Smoke: Auth API v1 → Bearer token → Catalog → refresh → Catalog → logout → Catalog 401.

Needs Auth on :5001 and Catalog (docker compose) on :5003 with the demo seed.
Run from the repo root:  python scripts/smoke_auth_catalog_api.py
Tokens and passwords are never printed in full.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

AUTH = os.getenv("AUTH_URL", "http://127.0.0.1:5001")
CATALOG = os.getenv("CATALOG_URL", "http://127.0.0.1:5003")
EMAIL = os.getenv("SMOKE_EMAIL", "operator@instrumed.com")
PASSWORD = os.getenv("SMOKE_PASSWORD", "DemopwdOP78!")
OTHER_EMAIL = os.getenv("SMOKE_OTHER_EMAIL", "supervisor@instrumed.com")
OTHER_PASSWORD = os.getenv("SMOKE_OTHER_PASSWORD", "DemopwdSPD78!")

failures = 0


def call(method, url, *, headers=None, body=None):
    request = urllib.request.Request(url, method=method, headers=headers or {}, data=body)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def mask(token: str) -> str:
    return f"{token[:8]}...({len(token)} chars)"


def check(label: str, status: int, expected: int, headers: dict, extra: str = "") -> None:
    global failures
    ok = status == expected
    failures += 0 if ok else 1
    print(f"[{'OK ' if ok else 'FAIL'}] {label}: {status} (expected {expected}) corr={headers.get('X-Correlation-ID')} {extra}")


def json_post(url, payload, headers=None):
    return call("POST", url, headers={"Content-Type": "application/json", **(headers or {})},
                body=json.dumps(payload).encode())


# A. API login (tokens in the body)
status, headers, body = json_post(f"{AUTH}/api/v1/auth/login", {"email": EMAIL, "password": PASSWORD},
                                  {"X-Correlation-ID": "smoke-login-1"})
login = json.loads(body)
check("A. POST /api/v1/auth/login (JSON)", status, 200, headers,
      f"fields={list(login)} access={mask(login.get('access_token', ''))} refresh={mask(login.get('refresh_token', ''))}")
access, refresh = login["access_token"], login["refresh_token"]
bearer = {"Authorization": f"Bearer {access}"}

# B. Catalog with the Auth-issued access token
status, headers, body = call("GET", f"{CATALOG}/api/v1/catalog/families", headers=bearer)
check("B. Catalog GET /families", status, 200, headers, f"families={len(json.loads(body))}")

# C. /me
status, headers, body = call("GET", f"{AUTH}/api/v1/auth/me", headers=bearer)
me = json.loads(body)
check("C. GET /api/v1/auth/me", status, 200, headers, f"email={me.get('email')} roles={me.get('roles')}")

# D. /permissions
status, headers, body = call("GET", f"{AUTH}/api/v1/auth/permissions", headers=bearer)
check("D. GET /api/v1/auth/permissions", status, 200, headers, f"body={json.loads(body)}")

# E. refresh → new access token works in Catalog
status, headers, body = json_post(f"{AUTH}/api/v1/auth/refresh", {"refresh_token": refresh})
refreshed = json.loads(body)
check("E. POST /api/v1/auth/refresh", status, 200, headers, f"new access={mask(refreshed.get('access_token', ''))}")
new_access, new_refresh = refreshed["access_token"], refreshed["refresh_token"]
status, headers, _ = call("GET", f"{CATALOG}/api/v1/catalog/families", headers={"Authorization": f"Bearer {new_access}"})
check("E2. Catalog with refreshed access token", status, 200, headers)
status, headers, _ = json_post(f"{AUTH}/api/v1/auth/refresh", {"refresh_token": refresh})
check("E3. replay of the rotated refresh token", status, 401, headers)

# F0. logout without refresh_token → 400, nothing revoked
new_bearer = {"Authorization": f"Bearer {new_access}"}
status, headers, body = call("POST", f"{AUTH}/api/v1/auth/logout", headers=new_bearer)
check("F0. logout without refresh_token", status, 400, headers, json.loads(body).get("error", ""))
status, headers, _ = call("GET", f"{CATALOG}/api/v1/catalog/families", headers=new_bearer)
check("F0b. access token still valid in Catalog", status, 200, headers)

# F1. logout with another user's refresh token → 403, that token is not revoked
other = json.loads(json_post(f"{AUTH}/api/v1/auth/login", {"email": OTHER_EMAIL, "password": OTHER_PASSWORD})[2])
status, headers, body = json_post(f"{AUTH}/api/v1/auth/logout", {"refresh_token": other["refresh_token"]}, new_bearer)
check("F1. logout with another user's refresh_token", status, 403, headers, json.loads(body).get("error", ""))
status, headers, body = json_post(f"{AUTH}/api/v1/auth/refresh", {"refresh_token": other["refresh_token"]})
check("F1b. other user's refresh_token still valid", status, 200, headers)
other = json.loads(body)
json_post(f"{AUTH}/api/v1/auth/logout", {"refresh_token": other["refresh_token"]},
          {"Authorization": f"Bearer {other['access_token']}"})

# F. logout (access + paired refresh) → Catalog rejects the token
status, headers, _ = json_post(f"{AUTH}/api/v1/auth/logout", {"refresh_token": new_refresh},
                               {"Authorization": f"Bearer {new_access}"})
check("F. POST /api/v1/auth/logout", status, 204, headers)
status, headers, body = call("GET", f"{CATALOG}/api/v1/catalog/families", headers={"Authorization": f"Bearer {new_access}"})
check("F2. Catalog with logged-out token", status, 401, headers, json.loads(body).get("error", ""))
status, headers, _ = json_post(f"{AUTH}/api/v1/auth/refresh", {"refresh_token": new_refresh})
check("F3. refresh with logged-out refresh token", status, 401, headers)
# The first access token was never logged out explicitly: still valid until it expires.
status, headers, _ = call("GET", f"{CATALOG}/api/v1/catalog/families", headers=bearer)
print(f"[info] first access token (not part of the logout) on Catalog: {status}")

# G. XML login + me, same semantics as JSON
xml_login = (f"<login_request><email>{EMAIL}</email><password>{PASSWORD}</password></login_request>").encode()
status, headers, body = call("POST", f"{AUTH}/api/v1/auth/login", body=xml_login,
                             headers={"Content-Type": "application/xml", "Accept": "application/xml"})
root = ET.fromstring(body)
check("G. POST /api/v1/auth/login (XML)", status, 200, headers, f"root=<{root.tag}> children={[c.tag for c in root]}")
xml_access = root.findtext("access_token")
xml_user = root.find("user")
same = (xml_user.findtext("id") == login["user"]["id"]
        and [r.text for r in xml_user.find("roles")] == login["user"]["roles"]
        and xml_user.find("ui_preferences").findtext("locale") == login["user"]["ui_preferences"]["locale"])
print(f"[{'OK ' if same else 'FAIL'}] G2. XML login user == JSON login user")
failures += 0 if same else 1
status, headers, body = call("GET", f"{AUTH}/api/v1/auth/me",
                             headers={"Authorization": f"Bearer {xml_access}", "Accept": "application/xml"})
me_xml = ET.fromstring(body)
check("G3. GET /me (XML)", status, 200, headers, f"root=<{me_xml.tag}> email={me_xml.findtext('email')}")
same_me = me_xml.findtext("email") == me["email"] and [r.text for r in me_xml.find("roles")] == me["roles"]
print(f"[{'OK ' if same_me else 'FAIL'}] G4. XML /me == JSON /me")
failures += 0 if same_me else 1
status, headers, _ = call(
    "POST", f"{AUTH}/api/v1/auth/logout",
    headers={"Authorization": f"Bearer {xml_access}", "Content-Type": "application/xml", "Accept": "application/xml"},
    body=f"<logout_request><refresh_token>{root.findtext('refresh_token')}</refresh_token></logout_request>".encode(),
)
check("G5. POST /logout (XML)", status, 204, headers)
status, headers, _ = call("GET", f"{CATALOG}/api/v1/catalog/families", headers={"Authorization": f"Bearer {xml_access}"})
check("G6. Catalog with XML-logged-out token", status, 401, headers)

# Health
for path in ("/health/live", "/health/database", "/health/redis", "/health/ready"):
    status, headers, body = call("GET", f"{AUTH}{path}")
    check(f"H. Auth {path}", status, 200, headers, json.loads(body)["status"])

print("SMOKE OK" if failures == 0 else f"SMOKE FAILED ({failures})")
sys.exit(1 if failures else 0)
