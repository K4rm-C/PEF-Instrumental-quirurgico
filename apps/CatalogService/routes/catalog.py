"""Catalog endpoints of PEF-DS09 §1.3.3 (base /api/v1/catalog).

The five original contracts (GET/POST /families, GET /instruments, GET /kits, GET /kits/{id}) are
unchanged. Complement v0.1.2 adds the management operations the portal's Administration used to
run in-process: family detail/update, instrument detail/create/update, kit create/update (new
version), the reference catalogs and an opt-in `expand=detail` representation for the lists.

Roles (effective, institution of the user): families are written by it_admin; instruments and kits
by it_admin or spd_supervisor; any authenticated user reads. Inactive kits exist only for it_admin:
any other role (the SPD supervisor included) lists and reads the active kits of its institution
and gets 404 for an inactive one, whatever client it uses. A management request from a role
without permission is answered 403 and recorded in access_audit (DENIED_*).
"""

from __future__ import annotations

from functools import wraps
from pathlib import Path

from flask import Blueprint, Response, current_app, g, request

from adapters import audit_adapter
from auth import ADMIN_ROLE_CODE, authenticate, current_principal, require_auth
from errors import ApiError
from serializers import parse_family_create_body, parse_resource_body, render
from services.catalog_service import CatalogService
from services.management_service import ManagementService, sees_inactive_kits

catalog_bp = Blueprint("catalog", __name__, url_prefix="/api/v1/catalog")

SUPERVISOR_ROLE_CODE = "spd_supervisor"
FAMILY_WRITERS = (ADMIN_ROLE_CODE,)
STOCK_WRITERS = (ADMIN_ROLE_CODE, SUPERVISOR_ROLE_CODE)   # instruments and kits
SERVICE_DIR = Path(__file__).resolve().parents[1]


def _service() -> CatalogService:
    return current_app.extensions["catalog"]["service"]


def _management() -> ManagementService:
    return current_app.extensions["catalog"]["management"]


def _expanded() -> bool:
    """`expand=detail` selects the richer representation (documented in DS09 v0.1.2)."""
    value = request.args.get("expand")
    if value is None:
        return False
    if value != "detail":
        raise ApiError.invalid_request("expand must be 'detail'", details={"field": "expand"})
    return True


def require_writer(roles: tuple[str, ...], resource_type: str):
    """require_auth for management writes, plus an access_audit entry for denied attempts."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            principal = authenticate()
            g.principal = principal
            if not any(principal.has_role(code) for code in roles):
                target = next(iter(kwargs.values()), None)
                audit_adapter.record_denied(current_app.extensions["catalog"]["database"], principal,
                                            resource_type, target)
                raise ApiError.forbidden()
            return view(*args, **kwargs)

        return wrapped

    return decorator


# --- Reference catalogs (DS03) ------------------------------------------------------------


@catalog_bp.get("/categories")
@require_auth()
def list_categories():
    return render(_management().list_categories(), root="categories")


@catalog_bp.get("/cycle-statuses")
@require_auth()
def list_cycle_statuses():
    return render(_management().list_cycle_statuses(), root="cycle_statuses")


# --- Families -----------------------------------------------------------------------------


@catalog_bp.get("/families")
@require_auth()
def list_families():
    if _expanded():
        return render(_management().list_families_detail(current_principal()), root="families")
    return render(_service().list_families(), root="families")


@catalog_bp.post("/families")
@require_writer(FAMILY_WRITERS, "instrument_family")
def create_family():
    family = _service().create_family(parse_family_create_body(), current_principal())
    return render(family, root="family", status=201)


@catalog_bp.get("/families/<family_id>")
@require_auth()
def get_family(family_id: str):
    return render(_management().get_family(current_principal(), family_id), root="family")


@catalog_bp.put("/families/<family_id>")
@require_writer(FAMILY_WRITERS, "instrument_family")
def update_family(family_id: str):
    family = _management().update_family(current_principal(), family_id, parse_resource_body("family"))
    return render(family, root="family")


# --- Instruments --------------------------------------------------------------------------


@catalog_bp.get("/instruments")
@require_auth()
def list_instruments():
    if _expanded():
        return render(_management().list_instruments_detail(current_principal()), root="instruments")
    instruments = _service().list_instruments(current_principal().institution_id)
    return render(instruments, root="instruments")


@catalog_bp.post("/instruments")
@require_writer(STOCK_WRITERS, "instrument")
def create_instrument():
    item = _management().create_instrument(current_principal(), parse_resource_body("instrument"))
    return render(item, root="instrument", status=201)


@catalog_bp.get("/instruments/<instrument_id>")
@require_auth()
def get_instrument(instrument_id: str):
    return render(_management().get_instrument(current_principal(), instrument_id), root="instrument")


@catalog_bp.put("/instruments/<instrument_id>")
@require_writer(STOCK_WRITERS, "instrument")
def update_instrument(instrument_id: str):
    item = _management().update_instrument(current_principal(), instrument_id, parse_resource_body("instrument"))
    return render(item, root="instrument")


# --- Kits ---------------------------------------------------------------------------------


@catalog_bp.get("/kits")
@require_auth()
def list_kits():
    if _expanded():
        return render(_management().list_kits_detail(current_principal()), root="kits")
    principal = current_principal()
    kits = _service().list_kits(principal.institution_id)
    if not sees_inactive_kits(principal):
        kits = [kit for kit in kits if kit["active"]]
    return render(kits, root="kits")


@catalog_bp.post("/kits")
@require_writer(STOCK_WRITERS, "kit")
def create_kit():
    item = _management().create_kit(current_principal(), parse_resource_body("kit"))
    return render(item, root="kit", status=201)


@catalog_bp.get("/kits/<kit_id>")
@require_auth()
def get_kit(kit_id: str):
    if _expanded():
        return render(_management().get_kit_detail(current_principal(), kit_id), root="kit")
    principal = current_principal()
    kit = _service().get_kit(kit_id, principal.institution_id)
    if not kit["active"] and not sees_inactive_kits(principal):
        raise ApiError.not_found("Kit not found")
    return render(kit, root="kit")


@catalog_bp.put("/kits/<kit_id>")
@require_writer(STOCK_WRITERS, "kit")
def update_kit(kit_id: str):
    item, created = _management().update_kit(current_principal(), kit_id, parse_resource_body("kit"))
    return render(item, root="kit", status=201 if created else 200)


# --- Contract documentation (no auth: the contract is public, the data is not) ------------


@catalog_bp.get("/openapi.yaml")
def openapi_document():
    return Response((SERVICE_DIR / "openapi.yaml").read_bytes(), mimetype="application/yaml")


@catalog_bp.get("/schemas/catalog.xsd")
def xml_schema():
    return Response((SERVICE_DIR / "schemas" / "catalog.xsd").read_bytes(), mimetype="application/xml")


@catalog_bp.get("/schemas/catalog-requests.xsd")
def request_xml_schema():
    return Response((SERVICE_DIR / "schemas" / "catalog-requests.xsd").read_bytes(), mimetype="application/xml")


@catalog_bp.get("/docs")
def swagger_ui():
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>PEF Catalog Service API</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css"></head>
<body><div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
<script>window.ui = SwaggerUIBundle({url: "openapi.yaml", dom_id: "#swagger-ui", persistAuthorization: true});</script>
</body></html>"""
    return Response(html, mimetype="text/html")
