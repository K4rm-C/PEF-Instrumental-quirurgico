"""MS-12 (ms-tracking-instrumental): tracking event log of the ACCEPTED final run of a session (Supervisor read).

The portal persists the tracker's events in `tracking_event` (migration 019) when the final count is accepted,
linked to that count event. This read gives the Supervisor the summary stored in the count payload plus the
persisted events (paginated) and states whether the persisted log is complete (persisted == events_recorded).
Traceability only: tracks are never a count; the accepted total is the last valid frame (MS-03.1).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from errors import ApiError

MAX_LIMIT = 500


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


def tracking_events(database, principal, raw_session: str, args) -> dict[str, Any]:
    unknown = sorted(set(args) - {"offset", "limit"})
    if unknown:
        raise ApiError.invalid_request("Unknown query parameter(s)", details={"unknown_parameters": unknown})
    try:
        offset = int(args.get("offset", 0))
        limit = int(args.get("limit", 200))
    except (TypeError, ValueError):
        raise ApiError.invalid_request("offset and limit must be integers", details={"field": "offset"}) from None
    if offset < 0 or not 1 <= limit <= MAX_LIMIT:
        raise ApiError.invalid_request(f"offset >= 0 and 1 <= limit <= {MAX_LIMIT}", details={"field": "limit"})
    try:
        session_id = uuid.UUID(str(raw_session))
    except (TypeError, ValueError):
        raise ApiError.not_found("Session not found") from None
    with database.connect() as connection:
        found = connection.execute(text("""
            SELECT w.id FROM work_session w JOIN operation o ON o.id = w.operation_id
            WHERE w.id = :s AND o.institution_id = :inst"""), {"s": session_id, "inst": principal.institution_id}).first()
        if not found:
            raise ApiError.not_found("Session not found")
        final = connection.execute(text("""
            SELECT id, occurred_at, payload->>'run_id' AS run_id, payload->'tracking' AS tracking
            FROM count_event WHERE session_id = :s AND payload->>'source' = 'last_frame'
            ORDER BY occurred_at DESC LIMIT 1"""), {"s": session_id}).mappings().first()
        if final is None:
            return {"session_id": str(session_id), "count_event_id": None, "run_id": None, "accepted_at": None,
                    "summary": None, "events_recorded": None, "events_persisted": 0, "complete": None,
                    "offset": offset, "limit": limit, "events": []}
        persisted = connection.execute(text("SELECT count(*) FROM tracking_event WHERE count_event_id = :c"),
                                       {"c": final["id"]}).scalar_one()
        rows = connection.execute(text("""
            SELECT seq, video_t, frame_index, track_id, event, family_code, confidence, detail
            FROM tracking_event WHERE count_event_id = :c ORDER BY seq OFFSET :o LIMIT :l"""),
                                  {"c": final["id"], "o": offset, "l": limit}).mappings().all()
    summary = dict(final["tracking"]) if isinstance(final["tracking"], dict) else None
    recorded = summary.get("events_recorded") if summary else None
    return {
        "session_id": str(session_id), "count_event_id": str(final["id"]), "run_id": final["run_id"],
        "accepted_at": _iso(final["occurred_at"]),
        "summary": None if summary is None else {
            key: summary.get(key) for key in ("version", "frames", "unique_confirmed_tracks", "active_confirmed_tracks",
                                              "final_frame_total", "active_tracks_match_final_frame",
                                              "events_recorded", "events_dropped", "note")},
        "events_recorded": recorded, "events_persisted": persisted,
        "complete": (persisted == recorded) if isinstance(recorded, int) else None,
        "offset": offset, "limit": limit,
        "events": [{"seq": r["seq"], "video_t": float(r["video_t"]), "frame_index": r["frame_index"],
                    "track_id": r["track_id"], "event": r["event"], "family_code": r["family_code"],
                    "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
                    "detail": r["detail"] or {}} for r in rows],
    }
