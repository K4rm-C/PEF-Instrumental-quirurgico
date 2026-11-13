"""MS-05 stations and cameras (base /api/v1/monitoring), scoped to the caller's institution.

| Endpoint                                   | station_operator | spd_supervisor | it_admin |
| POST /stations/{id}/heartbeat              | yes              | 403            | 403      |
| GET  /stations                             | 403              | yes            | yes      |
| GET  /stations/{id}                        | 403              | yes            | yes      |
| GET  /alerts?status=open|all&limit=        | 403              | yes            | yes      |

The heartbeat body is JSON: {"camera_id": uuid|null, "camera_status": ok|error|unavailable|unknown,
"camera_error": short code|null, "client": short name|null}. Responses JSON or XML (Accept).
"""

from __future__ import annotations

from flask import Blueprint, current_app, request

from auth import ADMIN, OPERATOR, SUPERVISOR, current_principal, require_roles
from errors import ApiError
from serializers import render
from services.stations import StationService

stations_bp = Blueprint("stations", __name__, url_prefix="/api/v1/monitoring")


def _service() -> StationService:
    return current_app.extensions["monitoring"]["stations"]


@stations_bp.post("/stations/<station_id>/heartbeat")
@require_roles(OPERATOR)
def heartbeat(station_id: str):
    if not request.is_json:
        raise ApiError(415, "Content-Type must be application/json", error="unsupported_media_type")
    payload = request.get_json(silent=True)
    if payload is None:
        raise ApiError.invalid_request("Malformed JSON body")
    return render(_service().heartbeat(current_principal(), station_id, payload), root="station")


@stations_bp.get("/stations")
@require_roles(ADMIN, SUPERVISOR)
def stations():
    return render(_service().stations(current_principal(), request.args), root="station_list")


@stations_bp.get("/stations/<station_id>")
@require_roles(ADMIN, SUPERVISOR)
def station(station_id: str):
    return render(_service().station(current_principal(), station_id), root="station")


@stations_bp.get("/alerts")
@require_roles(ADMIN, SUPERVISOR)
def alerts():
    return render(_service().alerts(current_principal(), request.args), root="alert_list")
