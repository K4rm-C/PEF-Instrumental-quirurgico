"""Session API v1 endpoints of PEF-DS09 §1.3.4 (v0.1.7 + complement v0.2.0 for the Supervisor).

Responsibilities (see GUIA_SESSION.md):
- Supervisor (spd_supervisor), through this service: schedule, full edit while scheduled, privacy
  agreement, expected inventory (and confirmed instruments_added), discrepancy queue and resolution,
  safe SPD close, and the session reads with `expand=detail` the RF screens are built from.
- Operator: the counting flow (start, YOLO counts, human validation, contingency report) lives in
  the portal (BackendWebFlask). The legacy Operator writes of this service (start, vision-counts,
  count-report) answer 410 unless SESSION_OPERATOR_WRITES_ENABLED=true (compatibility only).
  The Operator close of a reconciled vision session (POST /{id}/operator-close) is authorised and
  executed here (services/session_operator_close.py).
- Reads: station_operator or spd_supervisor (an operator sees only their own sessions).
- Reports (spd_supervisor only; complement v0.3.0): read-only aggregates of the institution's
  sessions, discrepancies, kits and operators under /api/v1/sessions/reports/*.
it_admin and any other role → 403, recorded in access_audit as DENIED_<METHOD>_WORK_SESSION.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, current_app, g, request

from auth import OPERATOR_ROLE_CODE, SESSION_ROLE_CODES, SUPERVISOR_ROLE_CODE, current_principal, require_auth
from errors import ApiError
from serializers import parse_body, render
from services.session_counts import SessionCountService
from services.session_planning import SessionPlanningService
from services.session_reports import SessionReportService
from services.session_review import SessionReviewService
from services.session_service import SessionService
from services.session_snapshot import SessionSnapshotService
from services.session_writes import SessionWriteService

sessions_bp = Blueprint("sessions", __name__, url_prefix="/api/v1/sessions")
SERVICE_DIR = Path(__file__).resolve().parents[1]

LINE_INT_FIELDS = frozenset({"expected_quantity"})
VISION_INT_FIELDS = frozenset({"detected_quantity"})
REPORT_INT_FIELDS = frozenset({"suggested_quantity", "reported_quantity"})
PLAN_LISTS = {"expected_lines": "line", "physicians": "physician"}
MAX_PER_PAGE = 100


def _service() -> SessionService:
    return current_app.extensions["session_service"]["service"]


def _writes() -> SessionWriteService:
    return current_app.extensions["session_service"]["writes"]


def _counts() -> SessionCountService:
    return current_app.extensions["session_service"]["counts"]


def _review() -> SessionReviewService:
    return current_app.extensions["session_service"]["review"]


def _snapshots() -> SessionSnapshotService:
    return current_app.extensions["session_service"]["snapshots"]


def _planning() -> SessionPlanningService:
    return current_app.extensions["session_service"]["planning"]


def _expanded() -> bool:
    value = request.args.get("expand")
    if value is None:
        return False
    if value != "detail":
        raise ApiError.invalid_request("expand must be 'detail'", details={"field": "expand"})
    return True


def _paginated(items: list, root: str):
    """Optional ?page=&per_page= (1..100): the body stays a list; totals go in X-Total-* headers."""
    if "page" not in request.args and "per_page" not in request.args:
        return render(items, root=root)
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
    except ValueError:
        raise ApiError.invalid_request("page and per_page must be integers", details={"field": "page"}) from None
    if page < 1 or not 1 <= per_page <= MAX_PER_PAGE:
        raise ApiError.invalid_request("page >= 1 and 1 <= per_page <= 100", details={"field": "per_page"})
    total = len(items)
    response = render(items[(page - 1) * per_page: page * per_page], root=root)
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Page"] = str(page)
    response.headers["X-Per-Page"] = str(per_page)
    response.headers["X-Total-Pages"] = str(max(1, (total + per_page - 1) // per_page))
    return response


def _operator_writes_enabled() -> None:
    if not current_app.config["SESSION_SETTINGS"].operator_writes_enabled:
        raise ApiError(410, "The Operator counting flow runs in the portal (BackendWebFlask); "
                            "this legacy endpoint is disabled",
                       details={"reason": "operator_flow_in_portal"})


# --- Reads --------------------------------------------------------------------------------


@sessions_bp.get("")
@require_auth(*SESSION_ROLE_CODES)
def list_sessions():
    if _expanded():
        return _paginated(_snapshots().list_summaries(current_principal(), request.args.get("status")), "sessions")
    # DS09 v0.1.7 list: query parameters never change its scope or content (they are ignored).
    return render(_service().list_sessions(current_principal()), root="sessions")


@sessions_bp.get("/discrepancies")
@require_auth(SUPERVISOR_ROLE_CODE)
def list_tenant_discrepancies():
    return _paginated(_snapshots().discrepancies(current_principal(), request.args.get("status")), "discrepancies")


def _reports() -> SessionReportService:
    return current_app.extensions["session_service"]["reports"]


# --- Supervisor reports (read-only) ---------------------------------------------------------


@sessions_bp.get("/reports/summary")
@require_auth(SUPERVISOR_ROLE_CODE)
def report_summary():
    return render(_reports().summary(current_principal(), request.args), root="report_summary")


@sessions_bp.get("/reports/kits")
@require_auth(SUPERVISOR_ROLE_CODE)
def report_kits():
    return render(_reports().kits(current_principal(), request.args), root="kit_report")


@sessions_bp.get("/reports/operators")
@require_auth(SUPERVISOR_ROLE_CODE)
def report_operators():
    return render(_reports().operators(current_principal(), request.args), root="operator_report")


@sessions_bp.get("/reports/options")
@require_auth(SUPERVISOR_ROLE_CODE)
def report_options():
    return render(_reports().options(current_principal(), request.args), root="report_options")


@sessions_bp.get("/<session_id>")
@require_auth(*SESSION_ROLE_CODES)
def get_session(session_id: str):
    if _expanded():
        return render(_snapshots().detail(current_principal(), session_id), root="session")
    return render(_service().get_session(current_principal(), session_id), root="session")


@sessions_bp.get("/<session_id>/expected-inventory")
@require_auth(*SESSION_ROLE_CODES)
def get_expected_inventory(session_id: str):
    return render(_service().get_expected_inventory(current_principal(), session_id), root="expected_inventory")


@sessions_bp.get("/<session_id>/counts")
@require_auth(*SESSION_ROLE_CODES)
def get_counts(session_id: str):
    return render(_service().get_counts(current_principal(), session_id), root="counts")


@sessions_bp.get("/<session_id>/discrepancies")
@require_auth(*SESSION_ROLE_CODES)
def get_discrepancies(session_id: str):
    return render(_service().get_discrepancies(current_principal(), session_id), root="discrepancies")


# --- Supervisor: planning -----------------------------------------------------------------


@sessions_bp.post("/schedule")
@require_auth(SUPERVISOR_ROLE_CODE)
def schedule_session():
    payload = parse_body("schedule_request", list_fields=PLAN_LISTS, int_fields=LINE_INT_FIELDS,
                         bool_fields=frozenset({"purpose_model_improvement"}))
    return render(_planning().schedule(current_principal(), payload, g.correlation_id), root="session", status=201)


@sessions_bp.put("/<session_id>")
@require_auth(SUPERVISOR_ROLE_CODE)
def update_scheduled_session(session_id: str):
    payload = parse_body("session_update_request", list_fields=PLAN_LISTS, int_fields=LINE_INT_FIELDS)
    return render(_planning().update_scheduled(current_principal(), session_id, payload, g.correlation_id),
                  root="session")


@sessions_bp.put("/<session_id>/privacy-agreement")
@require_auth(SUPERVISOR_ROLE_CODE)
def confirm_privacy(session_id: str):
    payload = parse_body("privacy_agreement_request", bool_fields=frozenset({"purpose_model_improvement"}))
    return render(_planning().confirm_privacy(current_principal(), session_id, payload, g.correlation_id),
                  root="session")


@sessions_bp.post("")
@require_auth(SUPERVISOR_ROLE_CODE)
def create_session():
    payload = parse_body("session_request", list_fields={"expected_lines": "line"}, int_fields=LINE_INT_FIELDS)
    detail = _writes().create_session(current_principal(), payload, g.correlation_id)
    return render(detail, root="session", status=201)


@sessions_bp.put("/<session_id>/expected-inventory")
@require_auth(SUPERVISOR_ROLE_CODE)
def replace_expected_inventory(session_id: str):
    payload = parse_body("expected_inventory_update", list_fields={"lines": "line"}, int_fields=LINE_INT_FIELDS,
                         bool_fields=frozenset({"physically_added"}))
    lines = _writes().replace_expected_inventory(current_principal(), session_id, payload, g.correlation_id)
    return render(lines, root="expected_inventory")


# --- Legacy Operator writes (disabled by default) -------------------------------------------


@sessions_bp.post("/<session_id>/start")
@require_auth(OPERATOR_ROLE_CODE)
def start_session(session_id: str):
    _operator_writes_enabled()
    payload = parse_body("start_request", allow_empty=True)
    return render(_writes().start_session(current_principal(), session_id, payload, g.correlation_id), root="session")


@sessions_bp.post("/<session_id>/vision-counts")
@require_auth(OPERATOR_ROLE_CODE)
def record_vision_counts(session_id: str):
    _operator_writes_enabled()
    payload = parse_body("vision_counts", list_fields={"counts": "count"}, int_fields=VISION_INT_FIELDS)
    result, created = _counts().record_vision_counts(current_principal(), session_id, payload)
    return render(result, root="vision_count_result", status=201 if created else 200)


@sessions_bp.post("/<session_id>/count-report")
@require_auth(OPERATOR_ROLE_CODE)
def submit_count_report(session_id: str):
    _operator_writes_enabled()
    payload = parse_body("count_report", list_fields={"reports": "report"}, int_fields=REPORT_INT_FIELDS)
    result = _counts().submit_count_report(current_principal(), session_id, payload, g.correlation_id)
    return render(result, root="count_report_result")


# --- Supervisor: SPD review ------------------------------------------------------------------


@sessions_bp.post("/<session_id>/discrepancies/<discrepancy_id>/resolve")
@require_auth(SUPERVISOR_ROLE_CODE)
def resolve_discrepancy(session_id: str, discrepancy_id: str):
    payload = parse_body("resolve_request")
    result = _review().resolve_discrepancy(current_principal(), session_id, discrepancy_id, payload, g.correlation_id)
    return render(result, root="resolve_result")


@sessions_bp.post("/<session_id>/close")
@require_auth(SUPERVISOR_ROLE_CODE)
def close_session(session_id: str):
    payload = parse_body("close_request", bool_fields=frozenset({"material_recovered", "contingency_reviewed",
                                                                 "unreconciled_shortage_acknowledged"}))
    return render(_review().close_session(current_principal(), session_id, payload, g.correlation_id), root="session")


# --- Operator: close of a reconciled session ---------------------------------------------------------


@sessions_bp.post("/<session_id>/operator-close")
@require_auth(OPERATOR_ROLE_CODE)
def operator_close_session(session_id: str):
    payload = parse_body("operator_close_request",
                         bool_fields=frozenset({"physical_integrity_confirmed", "material_recovered"}))
    service = current_app.extensions["session_service"]["operator_close"]
    return render(service.close(current_principal(), session_id, payload, g.correlation_id), root="session")


# --- Contract documents (public: the contract is public, the data is not) ---------------------


docs_bp = Blueprint("session_docs", __name__, url_prefix="/api/v1/sessions")


@docs_bp.get("/openapi.yaml")
def openapi_document():
    return Response((SERVICE_DIR / "openapi.yaml").read_bytes(), mimetype="application/yaml")


@docs_bp.get("/schemas/session-reports.xsd")
def report_schema():
    return Response((SERVICE_DIR / "schemas" / "session-reports.xsd").read_bytes(), mimetype="application/xml")


@docs_bp.get("/schemas/session-requests.xsd")
def request_schema():
    return Response((SERVICE_DIR / "schemas" / "session-requests.xsd").read_bytes(), mimetype="application/xml")


@docs_bp.get("/docs")
def swagger_ui():
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>PEF Session Service API</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css"></head>
<body><div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
<script>window.ui = SwaggerUIBundle({url: "openapi.yaml", dom_id: "#swagger-ui", persistAuthorization: true});</script>
</body></html>"""
    return Response(html, mimetype="text/html")
