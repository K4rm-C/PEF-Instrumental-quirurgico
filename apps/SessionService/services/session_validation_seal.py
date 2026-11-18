"""MS-12 (ms-validacion-humana): Supervisor verification of the integrity seal of the Operator's human validation.

The portal stores `validation_seal` in the payload of the Operator's manual_close (services/validation_seal.py). This
read recomputes the seal from the rows stored NOW (manual_count lines of that close, the close itself, the final count
event and its evidence frame hash) and compares:
- intact      the stored rows still produce the stored seal;
- altered     something sealed changed after the close (the Supervisor must investigate; nothing is repaired);
- not_sealed  the close predates MS-12 (or was written by a path without seal, e.g. an escalation);
- no_validation  the Operator has not closed the count yet.
It is tamper evidence, not an electronic signature (pending decision D7).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from errors import ApiError
from services import validation_seal


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


def verify(database, principal, raw_session: str) -> dict[str, Any]:
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
        close = connection.execute(text("""
            SELECT ce.id, ce.user_id, ce.occurred_at, ce.payload FROM count_event ce
            JOIN cat_event_type t ON t.id = ce.event_type_id
            WHERE ce.session_id = :s AND t.code = 'manual_close' AND ce.user_id IS NOT NULL
              AND COALESCE(ce.payload->>'operator_finished', 'false') = 'true'
            ORDER BY ce.occurred_at DESC LIMIT 1"""), {"s": session_id}).mappings().first()
        base = {"session_id": str(session_id), "status": "no_validation", "algorithm": None, "stored_sha256": None,
                "computed_sha256": None, "close_event_id": None, "closed_at": None, "operator_user_id": None,
                "close_path": None, "final_count_event_id": None, "final_frame_sha256": None, "lines": 0,
                "note": "Integrity check of the stored validation; not an electronic signature."}
        if close is None:
            return base
        payload = close["payload"] if isinstance(close["payload"], dict) else {}
        stored = payload.get("validation_seal") if isinstance(payload.get("validation_seal"), dict) else None
        base.update({"close_event_id": str(close["id"]), "closed_at": _iso(close["occurred_at"]),
                     "operator_user_id": str(close["user_id"]), "close_path": payload.get("operator_close_path")})
        if stored is None:
            return {**base, "status": "not_sealed"}
        rows = connection.execute(text("""
            SELECT ce.family_id, ce.expected_quantity, ce.detected_quantity, ce.payload FROM count_event ce
            JOIN cat_event_type t ON t.id = ce.event_type_id
            WHERE ce.session_id = :s AND t.code = 'manual_count' AND ce.user_id = :u AND ce.occurred_at = :at"""),
                                  {"s": session_id, "u": close["user_id"], "at": close["occurred_at"]}).mappings().all()
        final_id = stored.get("final_count_event_id")
        frame_sha = None
        if final_id:
            try:
                final_payload = connection.execute(text("SELECT payload FROM count_event WHERE id = :i AND session_id = :s"),
                                                   {"i": uuid.UUID(final_id), "s": session_id}).scalar()
            except ValueError:
                final_payload = None
            frame_sha = (final_payload or {}).get("frame_sha256") if isinstance(final_payload, dict) else None
    lines = []
    for row in rows:
        p = row["payload"] if isinstance(row["payload"], dict) else {}
        lines.append({"family_id": row["family_id"], "expected_quantity": row["expected_quantity"] or 0,
                      "reported_quantity": row["detected_quantity"] or 0, "reason_code": p.get("reason_code"),
                      "notes": p.get("notes"), "ai_suggested_quantity": p.get("ai_suggested_quantity")})
    computed = validation_seal.seal(session_id=session_id, operator_user_id=close["user_id"],
                                    closed_at=close["occurred_at"], close_path=payload.get("operator_close_path"),
                                    lines=lines, final_count_event_id=final_id, final_frame_sha256=frame_sha,
                                    extra_instruments=payload.get("extra_instruments"))
    return {**base, "status": "intact" if computed["sha256"] == stored.get("sha256") else "altered",
            "algorithm": stored.get("algorithm"), "stored_sha256": stored.get("sha256"),
            "computed_sha256": computed["sha256"], "final_count_event_id": final_id, "final_frame_sha256": frame_sha,
            "lines": len(lines)}
