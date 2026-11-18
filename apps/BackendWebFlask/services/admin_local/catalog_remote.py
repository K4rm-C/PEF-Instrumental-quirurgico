"""Administration RF screens of families, instruments and kits served by CatalogService (HTTP).

When CATALOG_SERVICE_URL is configured, facade.call() sends these operations here instead of the
in-process admin_local modules: every read and write of instrument_family, instrument, kit (and its
items, versions and procedure links) goes through CatalogService /api/v1/catalog, with the access
token that BackendAuthService already verified for this request (Catalog re-validates it: JWT, Redis
session, active user, effective roles). There is no local fallback and no double write: if
CatalogService is down the screen shows "temporarily unavailable" and nothing is written.

This module only adapts representations: it returns the same structures the admin_local domain
returned, so the RF templates and presenters are unchanged. Lists keep their in-portal filters,
localized search and pagination (presentation); business rules live in CatalogService.
"""

from __future__ import annotations

import os
import uuid

import requests
from flask import g

from .catalogs import category_rank
from .common import matches_search, matches_status, paginate, status_filter
from .messages import AdminDomainError

CATALOG_SERVICE_URL = os.getenv("CATALOG_SERVICE_URL", "").strip().rstrip("/")
CATALOG_TIMEOUT = float(os.getenv("CATALOG_SERVICE_TIMEOUT", "5"))
BASE = "/api/v1/catalog"
REMOTE_CATALOG_NAMES = {"categories", "cycle_statuses", "families", "active_families", "kits"}
ADMIN = "it_admin"


def enabled() -> bool:
    return bool(CATALOG_SERVICE_URL)


def handles(path: str) -> bool:
    return enabled() and path.split("/")[1] in {"families", "instruments", "kits"}


# --- HTTP ----------------------------------------------------------------------------------

# Catalog 409 per resource -> admin message code.
_CONFLICTS = {"families": "family_code_exists", "instruments": "instrument_code_exists", "kits": "kit_exists"}


def _correlation_id() -> str:
    if not getattr(g, "catalog_correlation_id", None):
        g.catalog_correlation_id = uuid.uuid4().hex
    return g.catalog_correlation_id


def _error(response, resource: str) -> AdminDomainError:
    try:
        body = response.json() or {}
    except ValueError:
        body = {}
    details = body.get("details") or {}
    message = body.get("message") or ""
    status = response.status_code
    field = details.get("field")
    if status in (401, 403, 404):
        return AdminDomainError(status, {401: "token_required", 403: "forbidden", 404: "not_found"}[status], message)
    if status == 409:
        return AdminDomainError(409, details.get("reason") or _CONFLICTS.get(resource, "conflict"), message, field)
    if status in (400, 422):
        code = details.get("reason")
        if not code and details.get("missing_fields"):
            code = f"{details['missing_fields'][0]}_required"
        if not code and field:
            code = f"{field}_too_long" if "too long" in message else (
                "invalid_category" if field == "category_code" else f"{field}_required")
        return AdminDomainError(400, code or "invalid_request", message, field)
    return AdminDomainError(503, "catalog_unavailable", message)


def _request(method: str, path: str, *, json=None, params=None):
    token = getattr(g, "auth_access_token", None)
    if not token:
        raise AdminDomainError(401, "token_required")
    try:
        response = requests.request(
            method, f"{CATALOG_SERVICE_URL}{BASE}{path}", json=json, params=params, timeout=CATALOG_TIMEOUT,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                     "X-Correlation-ID": _correlation_id()},
        )
    except requests.RequestException as exc:
        raise AdminDomainError(503, "catalog_unavailable", type(exc).__name__) from exc
    if response.status_code >= 400:
        raise _error(response, path.split("/")[1])
    return response.json()


# --- Reference data --------------------------------------------------------------------------


def _categories() -> list[dict]:
    rows = [{**row, "rank": category_rank(row["code"])} for row in _request("GET", "/categories")]
    return sorted(rows, key=lambda row: (row["rank"], row["name"]))


def _category_by_id(category_id: str) -> dict:
    if not (category_id or "").strip():
        raise AdminDomainError(400, "category_required", field="category_id")
    for row in _request("GET", "/categories"):
        if row["id"] == str(category_id).strip():
            return row
    raise AdminDomainError(400, "invalid_category", field="category_id")


def _category(item: dict, categories_by_code: dict | None = None) -> dict | None:
    code = item.get("category_code")
    if not code:
        return None
    category = {"id": item.get("category_id"), "code": code, "name": item.get("category_name")}
    if categories_by_code and code in categories_by_code:
        category["id"] = categories_by_code[code]["id"]
    category["rank"] = category_rank(code)
    return category


def _family_option(item: dict) -> dict:
    return {"id": item["id"], "code": item["code"], "name": item["name"], "active": item["active"],
            "category": _category(item), "available_count": item.get("available_count", 0)}


def catalogs(names: list[str]) -> dict:
    """The /catalogs entries owned by CatalogService (the rest stay in admin_local)."""
    result = {}
    if "categories" in names:
        result["categories"] = _categories()
    if "cycle_statuses" in names:
        result["cycle_statuses"] = sorted(_request("GET", "/cycle-statuses"), key=lambda row: row["name"])
    if "families" in names or "active_families" in names:
        families = sorted((_family_option(item) for item in _request("GET", "/families", params={"expand": "detail"})),
                          key=lambda row: row["name"])
        if "families" in names:
            result["families"] = families
        if "active_families" in names:
            result["active_families"] = [row for row in families if row["active"]]
    if "kits" in names:
        kits = [row for row in _request("GET", "/kits") if row["active"]]
        result["kits"] = [{"id": row["id"], "name": row["name"], "version": row["version"], "active": row["active"]}
                          for row in sorted(kits, key=lambda row: (row["name"], row["version"]))]
    return result


# --- Families --------------------------------------------------------------------------------


def _family_payload(payload: dict) -> dict:
    category = _category_by_id(payload.get("category_id"))
    return {
        "name": payload.get("name", ""), "category_code": category["code"],
        "identify_text": payload.get("identify_text") or None, "classify_text": payload.get("classify_text") or None,
        "function_text": payload.get("function_text") or None,
        "active": str(payload.get("status") or "active").lower() == "active",
    }


def list_families(params: dict) -> dict:
    search, category = params.get("search", ""), (params.get("category") or "").strip()
    wanted = status_filter(params.get("status", "active"))
    rows = []
    for item in _request("GET", "/families", params={"expand": "detail"}):
        if category and item["category_code"] != category:
            continue
        if not matches_status(item["active"], wanted):
            continue
        if not matches_search(search, item["code"], item["name"], item["function_text"], item["category_name"],
                              matches=params.get("match", []), keys=(item["code"], item["name"], item["category_code"])):
            continue
        rows.append({"id": item["id"], "code": item["code"], "name": item["name"], "active": item["active"],
                     "function_text": item["function_text"] or "", "category": _category(item)})
    result = paginate(rows, params.get("page", 1), params.get("per_page", 20))
    result["filters"] = {"search": search or "", "category": category, "status": wanted}
    return result


def get_family(family_id: str) -> dict:
    item = _request("GET", f"/families/{family_id}")
    return {"id": item["id"], "code": item["code"], "name": item["name"], "active": item["active"],
            "function_text": item["function_text"] or "", "category": _category(item),
            "identify_text": item["identify_text"] or "", "classify_text": item["classify_text"] or "",
            "kits_using": item["kits_using"]}


def create_family(payload: dict) -> dict:
    body = {"code": payload.get("code", ""), **_family_payload(payload)}
    return _request("POST", "/families", json=body)


def update_family(family_id: str, payload: dict) -> dict:
    return _request("PUT", f"/families/{family_id}", json=_family_payload(payload))


# --- Instruments -----------------------------------------------------------------------------


def _instrument(item: dict) -> dict:
    data = {
        "id": item["id"], "internal_code": item["internal_code"] or item["id"], "active": item["active"],
        "family": {"id": item["family_id"], "code": item["family_code"], "name": item["family_name"]},
        "category": {"id": None, "code": item["category_code"], "name": item["category_name"]},
        "cycle_status": {"id": None, "code": item["cycle_status_code"], "name": item["cycle_status_name"]},
    }
    if "cycle_events" in item:
        data["cycle_events"] = [{"occurred_at": event["occurred_at"],
                                 "cycle_status": {"code": event["cycle_status_code"], "name": event["cycle_status_name"]},
                                 "notes": event["notes"] or ""} for event in item["cycle_events"]]
    return data


def list_instruments(params: dict) -> dict:
    search = params.get("search", "")
    type_code = (params.get("type") or "").strip().lower()
    cycle_status = (params.get("cycle_status") or "").strip().lower()
    wanted = status_filter(params.get("status", "active"))
    rows = []
    for item in _request("GET", "/instruments", params={"expand": "detail"}):
        if type_code and item["category_code"] != type_code:
            continue
        if cycle_status and item["cycle_status_code"] != cycle_status:
            continue
        if not matches_status(item["active"], wanted):
            continue
        if not matches_search(search, item["internal_code"], item["family_name"], item["family_code"],
                              matches=params.get("match", []), keys=(item["family_code"], item["family_name"])):
            continue
        rows.append(_instrument(item))
    # Same order as the RF list always had: by internal code (pieces without one last).
    rows.sort(key=lambda row: (row["internal_code"] == row["id"], row["internal_code"]))
    result = paginate(rows, params.get("page", 1), params.get("per_page", 20))
    result["filters"] = {"search": search or "", "type": type_code, "cycle_status": cycle_status, "status": wanted}
    result["type_options"] = [{"code": row["code"], "name": row["name"], "rank": row["rank"]} for row in _categories()]
    result["cycle_status_options"] = sorted(({"code": row["code"], "name": row["name"]}
                                             for row in _request("GET", "/cycle-statuses")), key=lambda row: row["name"])
    return result


def get_instrument(instrument_id: str) -> dict:
    return _instrument(_request("GET", f"/instruments/{instrument_id}"))


def _instrument_refs(payload: dict) -> dict:
    if not (payload.get("family_id") or "").strip():
        raise AdminDomainError(400, "family_required", field="family_id")
    if not (payload.get("cycle_status") or "").strip():
        raise AdminDomainError(400, "cycle_status_required", field="cycle_status")
    return {"family_id": payload["family_id"].strip(), "cycle_status_code": payload["cycle_status"].strip(),
            "active": str(payload.get("status") or "active").lower() == "active"}


def create_instrument(payload: dict) -> dict:
    body = {"internal_code": payload.get("internal_code", ""), **_instrument_refs(payload)}
    return _instrument(_request("POST", "/instruments", json=body))


def update_instrument(instrument_id: str, payload: dict) -> dict:
    body = {**_instrument_refs(payload), "change_reason": payload.get("change_reason") or None}
    return _instrument(_request("PUT", f"/instruments/{instrument_id}", json=body))


# --- Kits ------------------------------------------------------------------------------------


def _kit_body(payload: dict) -> dict:
    items = []
    for row in payload.get("items") or []:
        quantity = str(row.get("quantity", "")).strip()
        items.append({"family_id": row.get("family_id"),
                      "quantity": int(quantity) if quantity.lstrip("-").isdigit() else quantity})
    return {"name": payload.get("name", ""), "active": str(payload.get("status") or "active").lower() == "active",
            "items": items}


def _kit(item: dict) -> dict:
    return {
        "id": item["id"], "name": item["name"], "version": item["version"], "active": item["active"],
        "created_at": item.get("created_at"), "procedures": item.get("procedures", []),
        "items": [{
            "family": {"id": row["family_id"], "code": row["family_code"], "name": row["family_name"]},
            "category": {"code": row["category_code"], "name": row["category_name"],
                         "rank": category_rank(row["category_code"])},
            "quantity": row["quantity"], "available_count": row["available_count"],
        } for row in item.get("items", [])],
    }


def list_kits(params: dict, *, active_only: bool, procedure_types: list[dict]) -> dict:
    search = params.get("search", "")
    procedure_type = (params.get("procedure_type") or "").strip().lower()
    wanted = "active" if active_only else status_filter(params.get("status", "active"))
    rows = []
    for item in _request("GET", "/kits", params={"expand": "detail"}):
        procedures = item["procedures"]
        if procedure_type and procedure_type not in {p["code"].lower() for p in procedures} \
                and procedure_type not in {p["id"] for p in procedures}:
            continue
        if not matches_status(item["active"], wanted):
            continue
        if not matches_search(search, item["name"], *(p["name"] for p in procedures), *(p["code"] for p in procedures),
                              matches=params.get("match", []),
                              keys=(item["id"], item["name"], *(p["code"] for p in procedures))):
            continue
        rows.append({"id": item["id"], "name": item["name"], "version": item["version"], "active": item["active"],
                     "family_count": item["family_count"], "total_expected": item["total_expected"],
                     "procedures": procedures})
    # Catalog orders by name then version ascending; the RF list shows the newest version first.
    rows.sort(key=lambda row: (row["name"], -row["version"]))
    result = paginate(rows, params.get("page", 1), params.get("per_page", 20))
    result["filters"] = {"search": search or "", "procedure_type": procedure_type, "status": wanted}
    result["active_only"] = active_only
    result["procedure_type_options"] = sorted(({"code": p["code"], "name": p["name"]} for p in procedure_types),
                                              key=lambda option: option["name"])
    return result


def get_kit(kit_id: str) -> dict:
    return _kit(_request("GET", f"/kits/{kit_id}", params={"expand": "detail"}))


def create_kit(payload: dict) -> dict:
    return _kit(_request("POST", "/kits", json=_kit_body(payload)))


def update_kit(kit_id: str, payload: dict) -> dict:
    return _kit(_request("PUT", f"/kits/{kit_id}", json=_kit_body(payload)))


# --- Dispatcher (same operations and paths as admin_local.api) --------------------------------


def dispatch(method: str, path: str, params: dict, body: dict, *, is_admin: bool, procedure_types=None) -> dict:
    parts = path.strip("/").split("/")
    resource, ident = parts[0], (parts[1] if len(parts) > 1 else None)
    if resource == "families":
        if method == "GET":
            return get_family(ident) if ident else list_families(params)
        return create_family(body) if method == "POST" and not ident else update_family(ident, body)
    if resource == "instruments":
        if method == "GET":
            return get_instrument(ident) if ident else list_instruments(params)
        return create_instrument(body) if method == "POST" and not ident else update_instrument(ident, body)
    if resource == "kits":
        if method == "GET":
            return get_kit(ident) if ident else list_kits(params, active_only=not is_admin,
                                                          procedure_types=procedure_types or [])
        return create_kit(body) if method == "POST" and not ident else update_kit(ident, body)
    raise AdminDomainError(404, "not_found")
