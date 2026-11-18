"""MS-12 (ms-validacion-humana): integrity seal of the Operator's human validation.

The seal is the SHA-256 of a canonical JSON document built ONLY from data stored with the close: who, when, the path,
each reported line (family, expected, reported, reason, notes, AI suggestion), the final count event and the SHA-256
of its evidence frame. SessionService recomputes it from the stored rows (GET /sessions/{id}/validation-seal) and
reports whether anything changed afterwards.

It is an integrity check (tamper evidence), NOT an electronic signature: a re-authentication / signature policy for
the validation is a pending institutional decision (docs/DECISIONES_PENDIENTES.md, D7).

KEEP IDENTICAL to apps/BackendWebFlask/services/validation_seal.py (same test vector in both suites).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

SEAL_VERSION = 1


def _ts(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _text(value: Any) -> str | None:
    value = (str(value).strip() if value is not None else "")
    return value or None


def canonical(*, session_id, operator_user_id, closed_at: datetime, close_path: str | None,
              lines: Iterable[dict[str, Any]], final_count_event_id=None, final_frame_sha256: str | None = None,
              extra_instruments: int | None = None) -> bytes:
    doc = {
        "v": SEAL_VERSION,
        "session_id": str(session_id),
        "operator_user_id": str(operator_user_id),
        "closed_at": _ts(closed_at),
        "close_path": close_path or None,
        "lines": sorted(([str(line["family_id"]), int(line["expected_quantity"]), int(line["reported_quantity"]),
                          _text(line.get("reason_code")), _text(line.get("notes")),
                          None if line.get("ai_suggested_quantity") is None else int(line["ai_suggested_quantity"])]
                         for line in lines), key=lambda row: row[0]),
        "final_count_event_id": str(final_count_event_id) if final_count_event_id else None,
        "final_frame_sha256": final_frame_sha256 or None,
        "extra_instruments": int(extra_instruments or 0),
    }
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def seal(**fields) -> dict[str, Any]:
    return {"algorithm": "sha256", "version": SEAL_VERSION,
            "sha256": hashlib.sha256(canonical(**fields)).hexdigest(),
            "final_count_event_id": str(fields["final_count_event_id"]) if fields.get("final_count_event_id") else None,
            "final_frame_sha256": fields.get("final_frame_sha256") or None}
