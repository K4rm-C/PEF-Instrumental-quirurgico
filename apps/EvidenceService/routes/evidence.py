"""Evidence endpoints of PEF-DS09 §1.3.5 (Phase B reads, Phase C upload).

Role gate first (effective roles): station_operator or spd_supervisor; it_admin and any other
role → 403 before any lookup, so the existence of a resource is never revealed to them. The
service then applies tenant and operator scope (404 when out of scope). Upload requires
station_operator: a supervisor (even multi-role) uploads only to a session assigned to them.
"""

from __future__ import annotations

from flask import Blueprint, current_app, g, request

from auth import EVIDENCE_ROLE_CODES, OPERATOR_ROLE_CODE, current_principal, require_auth
from serializers import render
from services.evidence_service import EvidenceService
from services.evidence_upload import EvidenceUploadService
from uploads import parse_upload_form

evidence_bp = Blueprint("evidence", __name__, url_prefix="/api/v1")


def _service() -> EvidenceService:
    return current_app.extensions["evidence_service"]["evidence"]


@evidence_bp.get("/evidence/<asset_id>")
@require_auth(*EVIDENCE_ROLE_CODES)
def get_evidence(asset_id: str):
    return render(_service().get_evidence(current_principal(), asset_id, g.correlation_id), root="evidence")


@evidence_bp.get("/sessions/<session_id>/evidence")
@require_auth(*EVIDENCE_ROLE_CODES)
def list_session_evidence(session_id: str):
    return render(_service().list_session_evidence(current_principal(), session_id), root="evidence_list")


@evidence_bp.post("/evidence")
@require_auth(OPERATOR_ROLE_CODE)
def upload_evidence():
    uploads: EvidenceUploadService = current_app.extensions["evidence_service"]["uploads"]
    try:
        result = uploads.upload(current_principal(), parse_upload_form(), g.correlation_id)
    finally:
        _close_uploaded_parts()
    return render(result, root="evidence", status=201)


def _close_uploaded_parts() -> None:
    """Close (= delete) every on-disk temporary file of the request, whatever happened."""
    if "files" not in request.__dict__:   # body never parsed (e.g. 413 envelope, wrong role)
        return
    for _, part in request.files.items(multi=True):
        part.close()
