"""MS-03: real evidence of a session for the Supervisor (Session Details → Timeline, Review Detail).

Sources, each read once per page and each optional (one failing never breaks the page):
* the session detail already loaded by the page (SessionService snapshot, or the local fallback): pinned model
  (vision_model), count events (timeline: AI counts with their model_version, human counts, rejected finals),
  discrepancies;
* MongoDB checkpoint_frame (PEF-DS06, written by the Operator's outbox): when/why each key frame was captured,
  its YOLO detections (family, confidence, bbox in pixels) and the media_asset_id of its JPEG;
* EvidenceService GET /sessions/{id}/evidence with the Supervisor's own token: the stored assets (SHA-256,
  size, created_at, blocked/purged). EvidenceService authorizes the session (institution, role) itself.

Images are never embedded and no storage credential or URL reaches the HTML: each <img> points to the portal
route supervisor_session_evidence_image, which re-authorizes the session AND checks that the asset belongs to
it, then lets EvidenceService sign a short-lived URL (audited as evidence.download_url) and redirects to it.
Nothing is invented: a field that a source does not provide is shown as unavailable.
"""
from __future__ import annotations

from typing import Any

from flask import g, url_for

REASON_LABELS = {
    "start": "Initial count",
    "close": "Final count",
    "state_change": "Rejected final count",
    # Written for a rejected final count (close_blocked); before MS-03.1 also for an acknowledged one.
    "discrepancy": "Rejected final count",
    "hourly": "Periodic",
    "manual_pin": "Pinned by the Operator",
}
AI_EVENTS = ("auto_count", "close_blocked")
HUMAN_EVENTS = ("manual_count",)


def _checkpoints(session_id: str) -> tuple[list[dict], str | None]:
    from services import mongo_checkpoints
    if not mongo_checkpoints.enabled():
        return [], "Visual checkpoints are not configured (MongoDB)."
    try:
        return mongo_checkpoints.list_checkpoints(session_id), None
    except Exception as exc:  # noqa: BLE001 - PyMongoError and network errors: degrade, never break the page
        return [], f"Visual checkpoints are not available right now ({type(exc).__name__})."


def _assets(session_id: str) -> tuple[dict[str, dict], str | None]:
    from services import evidence_client
    token = getattr(g, "auth_access_token", None)
    if not evidence_client.configured():
        return {}, "Evidence storage is not configured (EvidenceService)."
    if not token:
        return {}, "Evidence cannot be read without an authenticated session."
    try:
        items = evidence_client.list_session_evidence(session_id, token)
    except evidence_client.EvidenceError as exc:
        return {}, f"Evidence is not available right now (EvidenceService {exc.status or 'unreachable'})."
    if items is None:
        return {}, "Evidence of this session is not available to your account."
    return {item["id"]: item for item in items if item.get("id")}, None


def _boxes(detections: list[dict]) -> list[dict]:
    boxes = []
    for item in detections or []:
        bbox = item.get("bbox")
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
            continue
        confidence = item.get("confidence")
        boxes.append({
            "label": item.get("family_code") or "—",
            "confidence": confidence,
            "confidence_label": f"{confidence:.2f}" if isinstance(confidence, (int, float)) else "",
            "x": float(bbox[0]), "y": float(bbox[1]), "w": float(bbox[2]), "h": float(bbox[3]),
        })
    return boxes


def _image_state(asset: dict | None, media_asset_id: str | None) -> tuple[bool, str]:
    if not media_asset_id:
        return False, "No image was stored for this checkpoint."
    if asset is None:
        return False, "The image of this checkpoint is not available."
    if asset.get("purged"):
        return False, "The image was removed by the retention policy."
    if asset.get("blocked"):
        return False, "The image is blocked (privacy request)."
    return True, ""


def session_evidence(detail: dict[str, Any], vision_panel: dict[str, Any] | None = None) -> dict[str, Any]:
    """vision_panel: the DS06 panel the page already read (Session Details) — reused, never read twice."""
    session_id = str(detail.get("id"))
    vision = detail.get("capture_mode") == "vision"
    if vision_panel is not None:
        checkpoints = vision_panel.get("checkpoints") or [] if vision_panel.get("available") else []
        checkpoint_note = None if vision_panel.get("available") else "Visual checkpoints are not available right now."
    else:
        checkpoints, checkpoint_note = _checkpoints(session_id) if vision else ([], None)
    assets, asset_note = _assets(session_id)
    used_assets: set[str] = set()
    captures = []
    for checkpoint in checkpoints:
        media_asset_id = checkpoint.get("media_asset_id")
        asset = assets.get(media_asset_id) if media_asset_id else None
        available, missing = _image_state(asset, media_asset_id)
        if media_asset_id:
            used_assets.add(media_asset_id)
        captured_at = checkpoint.get("captured_at")
        captures.append({
            "seq": checkpoint.get("session_checkpoint_seq"),
            "reason": checkpoint.get("reason"),
            "reason_label": REASON_LABELS.get(checkpoint.get("reason"), checkpoint.get("reason") or "—"),
            "captured_at": captured_at.strftime("%Y-%m-%d %H:%M:%S") if hasattr(captured_at, "strftime") else captured_at,
            "phase_code": checkpoint.get("phase_code"),
            "model_version": checkpoint.get("model_version"),
            "detections": len(checkpoint.get("detections") or []),
            "boxes": _boxes(checkpoint.get("detections")),
            "media_asset_id": media_asset_id,
            "sha256": (asset or {}).get("sha256"),
            "size_bytes": (asset or {}).get("size_bytes"),
            "image_available": available,
            "image_missing_reason": missing,
            "image_url": url_for("web.supervisor_session_evidence_image", session_id=session_id,
                                 asset_id=media_asset_id) if available else None,
        })
    # Stored assets that no checkpoint references (e.g. MongoDB unavailable): still shown, without detections.
    for asset_id, asset in assets.items():
        if asset_id in used_assets:
            continue
        available, missing = _image_state(asset, asset_id)
        captures.append({
            "seq": None, "reason": None, "reason_label": "Stored evidence", "captured_at": asset.get("created_at"),
            "phase_code": None, "model_version": None, "detections": None, "boxes": [],
            "media_asset_id": asset_id, "sha256": asset.get("sha256"), "size_bytes": asset.get("size_bytes"),
            "image_available": available and asset.get("kind") == "jpeg", "image_missing_reason": missing,
            "image_url": url_for("web.supervisor_session_evidence_image", session_id=session_id, asset_id=asset_id)
            if available and asset.get("kind") == "jpeg" else None,
        })
    timeline = detail.get("timeline") or []
    return {
        "session_id": session_id,
        "vision": vision,
        "model": detail.get("vision_model"),
        "captures": captures,
        # Tray-level AI facts: auto_count of the whole board (no family) and rejected final counts.
        "ai_counts": [e for e in timeline if e.get("event_code") in AI_EVENTS and not e.get("family_id")],
        "human_counts": [e for e in timeline if e.get("event_code") in HUMAN_EVENTS],
        "discrepancies": detail.get("discrepancies") or [],
        "notes": [note for note in (checkpoint_note, asset_note) if note],
    }
