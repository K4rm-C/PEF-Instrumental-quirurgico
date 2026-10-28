"""Administration RF screens of users and roles served by BackendAuthService (Auth / Identity, HTTP).

facade.call() always sends /users and /roles (and the `roles` entry of /catalogs) here: every read
and write of "user", user_role and role goes through /api/v1/auth with the access token that
AuthService already verified for this request (AuthService re-validates it: JWT, Redis session,
active user and institution, effective it_admin). The portal runs no SQL on those tables for these
flows and has no local fallback: if AuthService is down the screen shows "temporarily unavailable"
and nothing is written (sign-in needs AuthService anyway).

This module only adapts representations: it returns the structures the former admin_local users and
roles modules returned, so the RF templates and presenters are unchanged. Lists keep their in-portal
filters, search and pagination (presentation); business rules live in AuthService.
"""

from __future__ import annotations

import uuid

import requests
from flask import current_app, g

from .common import matches_search, matches_status, optional_uuid, paginate, status_filter, status_flag
from .messages import AdminDomainError

BASE = "/api/v1/auth"
# Conflicts that keep 409 on the screen; the other guards were 400 in the former local module.
_CONFLICT_STATUS = {"email_exists": 409, "role_code_exists": 409}


def handles(path: str) -> bool:
    return path.split("/")[1] in {"users", "roles"}


# --- HTTP ----------------------------------------------------------------------------------


def _correlation_id() -> str:
    if not getattr(g, "identity_correlation_id", None):
        g.identity_correlation_id = uuid.uuid4().hex
    return g.identity_correlation_id


def _error(response) -> AdminDomainError:
    try:
        body = response.json() or {}
    except ValueError:
        body = {}
    details = body.get("details") or {}
    message = body.get("message") or ""
    reason, field = details.get("reason"), details.get("field")
    status = response.status_code
    if status == 401:
        return AdminDomainError(401, "token_required", message)
    if status == 403:
        return AdminDomainError(403, "forbidden", message)
    if status == 404:
        return AdminDomainError(404, reason or "not_found", message)
    if status == 409:
        return AdminDomainError(_CONFLICT_STATUS.get(reason, 400), reason or "conflict", message, field)
    if status in (400, 422):
        if not reason and details.get("missing_fields"):
            reason = f"{details['missing_fields'][0]}_required"
        return AdminDomainError(400, reason or "invalid_request", message, field)
    return AdminDomainError(503, "identity_unavailable", message)


def _request(method: str, path: str, *, json=None):
    token = getattr(g, "auth_access_token", None)
    if not token:
        raise AdminDomainError(401, "token_required")
    try:
        response = requests.request(
            method, f"{current_app.config['AUTH_SERVICE_URL']}{BASE}{path}", json=json,
            timeout=current_app.config.get("AUTH_SERVICE_TIMEOUT", 5),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                     "X-Correlation-ID": _correlation_id()},
        )
    except requests.RequestException as exc:
        raise AdminDomainError(503, "identity_unavailable", type(exc).__name__) from exc
    if response.status_code >= 400:
        raise _error(response)
    return response.json() if response.content else None


# --- Users -----------------------------------------------------------------------------------


def list_users(params: dict) -> dict:
    search, role = params.get("search", ""), params.get("role", "")
    institution = params.get("institution", "")
    wanted_status = status_filter(params.get("status", "active"))
    institution_id = optional_uuid(institution)
    rows = []
    for user in _request("GET", "/users"):
        if role and role not in {item["code"] for item in user["roles"]}:
            continue
        if institution_id and user["institution"]["id"] != str(institution_id):
            continue
        if not matches_status(user["active"], wanted_status):
            continue
        if not matches_search(search, user["name"], user["email"]):
            continue
        rows.append(user)
    result = paginate(rows, params.get("page", 1), params.get("per_page", 20))
    result["filters"] = {"search": search or "", "role": role or "", "institution": institution or "",
                         "status": wanted_status}
    return result


def _user_body(payload: dict, *, with_password: bool) -> dict:
    body = {
        "name": payload.get("name") or "",
        "email": payload.get("email") or "",
        "institution_id": payload.get("institution_id") or "",
        "role_codes": [str(code) for code in (payload.get("role_codes") or []) if code],
        "active": status_flag(payload),
    }
    if with_password:
        body["password"] = payload.get("password") or ""
        if "confirm_password" in payload:
            body["confirm_password"] = payload.get("confirm_password") or ""
    return body


def _password_body(payload: dict) -> dict:
    body = {"new_password": payload.get("new_password") or ""}
    if "confirm_new_password" in payload:
        body["confirm_new_password"] = payload.get("confirm_new_password") or ""
    return body


# --- Roles -----------------------------------------------------------------------------------


def list_roles(params: dict) -> dict:
    search = params.get("search", "")
    rows = [role for role in _request("GET", "/roles") if matches_search(search, role["code"], role["description"])]
    result = paginate(rows, params.get("page", 1), params.get("per_page", 50))
    result["filters"] = {"search": search or ""}
    return result


def catalog_roles() -> list[dict]:
    """The `roles` entry of /catalogs (user form and filters), same shape as before."""
    return [{"id": role["id"], "code": role["code"], "description": role["description"],
             "institution_id": role["institution"]["id"]} for role in _request("GET", "/roles")]


# --- Dispatch (same paths the RF screens already use) ------------------------------------------


def dispatch(method: str, path: str, params: dict, body: dict):
    parts = path.strip("/").split("/")
    resource, ident = parts[0], (parts[1] if len(parts) > 1 else None)
    action = parts[2] if len(parts) > 2 else None
    if resource == "users":
        if method == "GET":
            return _request("GET", f"/users/{ident}") if ident else list_users(params)
        if method == "POST" and not ident:
            return _request("POST", "/users", json=_user_body(body, with_password=True))
        if method == "PUT" and ident and not action:
            return _request("PUT", f"/users/{ident}", json=_user_body(body, with_password=False))
        if method == "POST" and action in ("activate", "deactivate"):
            return _request("POST", f"/users/{ident}/{action}")
        if method == "POST" and action == "password":
            return _request("POST", f"/users/{ident}/password", json=_password_body(body))
    if resource == "roles":
        if method == "GET":
            return _request("GET", f"/roles/{ident}") if ident else list_roles(params)
        if method == "POST" and not ident:
            return _request("POST", "/roles", json={"code": body.get("code") or "",
                                                    "description": body.get("description") or ""})
        if method == "PUT" and ident:
            return _request("PUT", f"/roles/{ident}", json={"description": body.get("description") or ""})
        if method == "DELETE" and ident:
            return _request("DELETE", f"/roles/{ident}")
    raise AdminDomainError(404, "not_found")
