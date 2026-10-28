"""Session count use cases — Phase D (PEF-DS09 §1.3.4, PEF-DS04 v0.2.1 §1.3.6–1.3.8).

- POST /sessions/{id}/vision-counts → record_vision_counts  (assigned station_operator)
- POST /sessions/{id}/count-report  → submit_count_report   (assigned station_operator)

Both take the work_session row lock first (repo.lock_session: row locked alone, joined read after),
so they serialize with each other, with PUT expected-inventory and with start.

Vision counts are facts only: one normalized batch {model_version, trigger, counts[]} per call,
stored as ONE auto_count with user_id NULL (the model produced it, not the operator). No boxes,
frames, video or camera metadata ever reach Session. Idempotency is PostgreSQL's UNIQUE
count_event.client_event_id (INSERT ... ON CONFLICT DO NOTHING), never memory or Redis.

Count report = the operator's final human count (manual_no_privacy and vision):
- AI suggestion = the stored last `final` auto_count (0 if the family is absent); the client value
  must match it (409 otherwise); no final → null (human fallback);
- AI ≠ human → human_correction on that family's manual_count (justification = notes, required);
  no correction (AI = human or no AI suggestion) → notes must be null/absent (400 otherwise);
- human ≠ expected → discrepancy (shortage/surplus by the sign; never the operator's reason_code);
- one manual_count per expected family, one manual_close, ended_at, new status and the
  `session.manual_close` audit row — all in ONE transaction.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from adapters import audit_adapter
from auth import Principal
from database import Database
from errors import ApiError
from repositories import write_repository as repo
from services.session_service import _iso, _vision_counts

logger = logging.getLogger("session.counts")

SMALLINT_MAX = 32767
MODEL_VERSION_MAX = 32          # yolo_model.version_tag VARCHAR(32) (DS03)
VISION_TRIGGERS = ("stable_window", "final")
VISION_FIELDS = ("client_event_id", "occurred_at", "model_version", "trigger", "counts")
VISION_COUNT_FIELDS = ("family_id", "detected_quantity")
REPORT_FIELDS = ("family_id", "suggested_quantity", "reported_quantity", "reason_code", "notes")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_session_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        raise ApiError.not_found("Session not found") from None


# --- Shared field validation -----------------------------------------------------------------


def _reject_unknown(payload: dict[str, Any], allowed: tuple[str, ...], **context: Any) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise ApiError(400, "Unknown fields in request body", details={**context, "unknown_fields": unknown})


def _uuid_value(value: Any, field: str, **context: Any) -> uuid.UUID:
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            pass
    raise ApiError(400, "Field must be a UUID string", details={**context, "field": field})


def _quantity(value: Any, field: str, **context: Any) -> int:
    """Real integer (bool rejected), 0 ≤ value ≤ SMALLINT (DS09: negative quantities → 400)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(400, f"{field} must be an integer", details={**context, "field": field})
    if value < 0 or value > SMALLINT_MAX:
        raise ApiError(400, f"{field} out of range", details={**context, "field": field, "min": 0, "max": SMALLINT_MAX})
    return value


def _required(payload: dict[str, Any], fields: tuple[str, ...], **context: Any) -> None:
    missing = [name for name in fields if payload.get(name) is None]
    if missing:
        raise ApiError(400, "Required fields are missing", details={**context, "missing_fields": missing})


def _parse_occurred_at(value: Any) -> datetime:
    """ISO-8601 date-time WITH timezone, normalized to UTC. No clock-skew window is defined (DS09/DS10)."""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed.astimezone(timezone.utc)
    raise ApiError(400, "occurred_at must be an ISO-8601 date-time with timezone", details={"field": "occurred_at"})


# --- Vision counts: request -----------------------------------------------------------------


def validate_vision_counts(payload: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown(payload, VISION_FIELDS)
    _required(payload, VISION_FIELDS)
    model_version = payload["model_version"]
    if not isinstance(model_version, str) or not model_version.strip() or len(model_version) > MODEL_VERSION_MAX:
        raise ApiError(400, "model_version must be a non-empty string of at most 32 characters",
                       details={"field": "model_version", "max_length": MODEL_VERSION_MAX})
    trigger = payload["trigger"]
    if trigger not in VISION_TRIGGERS:
        raise ApiError(400, "trigger must be stable_window or final",
                       details={"field": "trigger", "allowed": list(VISION_TRIGGERS)})
    raw_counts = payload["counts"]
    if not isinstance(raw_counts, list):
        raise ApiError(400, "counts must be a list", details={"field": "counts"})
    counts: dict[uuid.UUID, int] = {}
    for item in raw_counts:
        if not isinstance(item, dict):
            raise ApiError(400, "Each count must be an object", details={"field": "counts"})
        _reject_unknown(item, VISION_COUNT_FIELDS, field="counts")
        _required(item, VISION_COUNT_FIELDS, field="counts")
        family_id = _uuid_value(item["family_id"], "family_id")
        quantity = _quantity(item["detected_quantity"], "detected_quantity", family_id=str(family_id))
        if family_id in counts:
            raise ApiError(400, "Duplicate family in counts", details={"family_id": str(family_id)})
        counts[family_id] = quantity
    return {
        "client_event_id": _uuid_value(payload["client_event_id"], "client_event_id"),
        "occurred_at": _parse_occurred_at(payload["occurred_at"]),
        "model_version": model_version,
        "trigger": trigger,
        "counts": counts,
    }


def _normalized_counts(counts: dict[uuid.UUID, int]) -> list[dict[str, Any]]:
    """Order-independent form: sorted by family_id (what is stored and what replays compare)."""
    return [{"family_id": str(family_id), "detected_quantity": quantity}
            for family_id, quantity in sorted(counts.items(), key=lambda item: str(item[0]))]


def _same_vision_fact(existing: dict[str, Any], session_id: uuid.UUID, data: dict[str, Any]) -> bool:
    """Same logical content: session, type, occurred_at instant, model_version, trigger, counts (any order)."""
    payload = existing["payload"] if isinstance(existing["payload"], dict) else {}
    return (
        existing["session_id"] == session_id
        and existing["event_type_code"] == "auto_count"
        and existing["occurred_at"] == data["occurred_at"]
        and payload.get("model_version") == data["model_version"]
        and payload.get("trigger") == data["trigger"]
        and _vision_counts(payload) == _normalized_counts(data["counts"])
    )


def _vision_result(event_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "count_event_id": str(event_id),
        "client_event_id": str(data["client_event_id"]),
        "trigger": data["trigger"],
        "occurred_at": _iso(data["occurred_at"]),
    }


# --- Count report: request --------------------------------------------------------------------


def _optional_text(value: Any, field: str, family_id: uuid.UUID) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(400, f"{field} must be a string", details={"family_id": str(family_id), "field": field})
    return value


def validate_count_report(payload: dict[str, Any]) -> list[dict[str, Any]]:
    _reject_unknown(payload, ("reports",))
    raw = payload.get("reports")
    if not isinstance(raw, list) or not raw:
        raise ApiError(400, "At least one report line is required", details={"field": "reports"})
    lines: list[dict[str, Any]] = []
    seen: set[uuid.UUID] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ApiError(400, "Each report must be an object", details={"field": "reports"})
        _reject_unknown(item, REPORT_FIELDS, field="reports")
        _required(item, ("family_id", "reported_quantity"), field="reports")
        family_id = _uuid_value(item["family_id"], "family_id")
        if family_id in seen:
            raise ApiError(400, "Duplicate family in reports", details={"family_id": str(family_id)})
        seen.add(family_id)
        suggested = item.get("suggested_quantity")
        reason_code = _optional_text(item.get("reason_code"), "reason_code", family_id)
        lines.append({
            "family_id": family_id,
            "reported": _quantity(item["reported_quantity"], "reported_quantity", family_id=str(family_id)),
            "suggested": None if suggested is None else _quantity(suggested, "suggested_quantity",
                                                                  family_id=str(family_id)),
            # An empty XML element carries no code.
            "reason_code": reason_code.strip() or None if reason_code is not None else None,
            "notes": _optional_text(item.get("notes"), "notes", family_id),
        })
    return lines


def _description(kind: str, family_name: str, expected: int, reported: int) -> str:
    """Canonical, untranslated, PII-free text (DS09 example); clients localize from structured fields."""
    label = "Shortfall" if kind == "shortage" else "Surplus"
    return f"{label} on {family_name}: expected {expected}, reported {reported}."


class SessionCountService:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _locked_own_session(connection, principal: Principal, session_id: uuid.UUID) -> dict[str, Any]:
        session = repo.lock_session(connection, session_id)
        # Unknown, other tenant, operation-less or assigned to someone else: indistinguishable 404.
        if session is None or session["institution_id"] != principal.institution_id \
                or session["user_id"] != principal.user_id:
            raise ApiError.not_found("Session not found")
        return session

    # --- POST /sessions/{id}/vision-counts ---------------------------------------------------------

    def record_vision_counts(
        self, principal: Principal, raw_session_id: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """Returns (result, created): created → 201, idempotent replay → 200."""
        session_id = _parse_session_id(raw_session_id)
        data = validate_vision_counts(payload)
        with self.database.begin() as connection:
            session = self._locked_own_session(connection, principal, session_id)

            # Replay first: a retried request whose fact already exists is answered 200 even if the
            # session moved on afterwards (the fact was accepted while it was in progress).
            existing = repo.find_count_event_by_client_id(connection, data["client_event_id"])
            if existing is not None:
                return self._replay(existing, session_id, data), False

            if session["status_code"] != "in_progress":
                raise ApiError.conflict("Session is not in progress", details={"status_code": session["status_code"]})
            if session["capture_mode"] != "vision":
                raise ApiError.conflict("Session does not use vision capture",
                                        details={"capture_mode": session["capture_mode"]})

            expected = repo.expected_rows(connection, session_id)
            for family_id in data["counts"]:
                if family_id not in expected:
                    raise ApiError.invalid_reference(
                        "Family is not part of the session's expected inventory",
                        details={"field": "family_id", "family_id": str(family_id)},
                    )

            expected_total = sum(int(row["expected_quantity"]) for row in expected.values())
            detected_total = sum(data["counts"].values())
            event_id = repo.insert_auto_count(connection, {
                "event_type_id": repo.event_type_id(connection, "auto_count"),
                "client_event_id": data["client_event_id"],
                "family_id": None,
                # DS04: batch totals; NULL rather than overflowing the SMALLINT column.
                "expected_quantity": expected_total if expected_total <= SMALLINT_MAX else None,
                "detected_quantity": detected_total if detected_total <= SMALLINT_MAX else None,
                "payload": {
                    "model_version": data["model_version"],
                    "trigger": data["trigger"],
                    "counts": _normalized_counts(data["counts"]),
                },
                "occurred_at": data["occurred_at"],
                "session_id": session_id,
                "user_id": None,   # produced by the model, not by the operator who relayed it
            })
            if event_id is None:
                # Lost the race on the UNIQUE constraint to a transaction that has now committed.
                existing = repo.find_count_event_by_client_id(connection, data["client_event_id"])
                if existing is None:   # pragma: no cover — the conflicting row was deleted meanwhile
                    raise ApiError.conflict("client_event_id is already in use",
                                            details={"client_event_id": str(data["client_event_id"])})
                return self._replay(existing, session_id, data), False
        logger.info("auto_count stored session=%s event=%s trigger=%s families=%d",
                    session_id, event_id, data["trigger"], len(data["counts"]))
        return _vision_result(event_id, data), True

    @staticmethod
    def _replay(existing: dict[str, Any], session_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
        if not _same_vision_fact(existing, session_id, data):
            raise ApiError.conflict("client_event_id was already used with different content or session",
                                    details={"client_event_id": str(data["client_event_id"])})
        logger.info("auto_count replay session=%s event=%s", session_id, existing["id"])
        return _vision_result(existing["id"], data)

    # --- POST /sessions/{id}/count-report ------------------------------------------------------------

    def submit_count_report(
        self, principal: Principal, raw_session_id: str, payload: dict[str, Any], correlation_id: str | None
    ) -> dict[str, Any]:
        session_id = _parse_session_id(raw_session_id)
        lines = validate_count_report(payload)
        now = _utc_now()
        with self.database.begin() as connection:
            session = self._locked_own_session(connection, principal, session_id)
            if session["status_code"] != "in_progress":
                raise ApiError.conflict("Session is not in progress", details={"status_code": session["status_code"]})

            # Expected comes only from the (now locked, hence stable) session inventory.
            expected = {family_id: int(row["expected_quantity"])
                        for family_id, row in repo.expected_rows(connection, session_id).items()}
            if not expected:
                raise ApiError.conflict("Session has no expected inventory to report", details={"expected_lines": 0})
            submitted = {line["family_id"] for line in lines}
            extra = sorted(str(f) for f in submitted - set(expected))
            if extra:
                raise ApiError.invalid_reference("Family is not part of the session's expected inventory",
                                                 details={"field": "family_id", "family_ids": extra})
            missing = sorted(str(f) for f in set(expected) - submitted)
            if missing:
                raise ApiError(400, "Every expected family must be reported exactly once",
                               details={"missing_family_ids": missing})

            server_suggested = self._server_suggestions(connection, session, lines, expected)
            self._validate_justifications(lines, expected, server_suggested)
            reasons = repo.discrepancy_reason_ids(connection)
            for line in lines:
                if line["reason_code"] is not None and line["reason_code"] not in reasons:
                    raise ApiError.invalid_reference("reason_code is not a valid discrepancy reason",
                                                     details={"field": "reason_code",
                                                              "family_id": str(line["family_id"])})

            manual_count = repo.event_type_id(connection, "manual_count")
            names = repo.family_names(connection, list(expected))
            corrections = 0
            for line in sorted(lines, key=lambda item: str(item["family_id"])):
                family_id, reported = line["family_id"], line["reported"]
                expected_qty = expected[family_id]
                event_id = repo.insert_count_event(connection, {
                    "event_type_id": manual_count,
                    "session_id": session_id,
                    "family_id": family_id,
                    "expected_quantity": expected_qty,
                    "detected_quantity": reported,       # the human quantity is a typed column (DS04)
                    "payload": {"reason_code": line["reason_code"]} if line["reason_code"] else {},
                    "occurred_at": now,
                    "user_id": principal.user_id,
                })
                suggested = server_suggested[family_id]
                if suggested is not None and suggested != reported:
                    repo.insert_human_correction(connection, {
                        "justification": line["notes"],
                        "recorded_at": now,
                        "count_event_id": event_id,
                        "user_id": principal.user_id,
                    })
                    corrections += 1
                if reported != expected_qty:
                    kind = "shortage" if reported < expected_qty else "surplus"
                    repo.insert_discrepancy(connection, {
                        "description": _description(kind, names.get(family_id, str(family_id)), expected_qty, reported),
                        "resolved": False,
                        "resolved_at": None,
                        "reason_id": reasons[kind],      # objective result, not the operator's reason_code
                        "family_id": family_id,
                        "expected_quantity": expected_qty,
                        "detected_quantity": reported,
                        "session_id": session_id,
                        "origin_event_id": event_id,
                        "updated_at": now,
                    })

            open_discrepancies = repo.count_open_discrepancies(connection, session_id)
            next_status = "correction_required" if open_discrepancies else "awaiting_spd_review"
            repo.insert_count_event(connection, {
                "event_type_id": repo.event_type_id(connection, "manual_close"),
                "session_id": session_id,
                "occurred_at": now,
                "user_id": principal.user_id,
                "payload": {
                    "capture_mode": session["capture_mode"],
                    "open_discrepancies": open_discrepancies,
                    "human_corrections": corrections,
                    "next_status": next_status,
                },
            })
            repo.update_session(connection, session_id, {
                "status_id": repo.session_status_id(connection, next_status),
                "ended_at": now,
                "updated_at": now,
            })
            audit_adapter.record(
                connection, action=audit_adapter.ACTION_MANUAL_CLOSE, actor_user_id=principal.user_id,
                session_id=session_id, institution_id=principal.institution_id, correlation_id=correlation_id,
                occurred_at=now,
            )
        return {
            "session_id": str(session_id),
            "status_code": next_status,
            "open_discrepancies": open_discrepancies,
            "human_corrections": corrections,
        }

    @staticmethod
    def _server_suggestions(connection, session: dict[str, Any], lines: list[dict[str, Any]],
                            expected: dict[uuid.UUID, int]) -> dict[uuid.UUID, int | None]:
        """The authoritative AI suggestion per expected family, checked against the client's value."""
        if session["capture_mode"] != "vision":
            for line in lines:
                if line["suggested"] is not None:
                    raise ApiError(400, "suggested_quantity must be null in a session without vision",
                                   details={"family_id": str(line["family_id"]), "field": "suggested_quantity"})
            return {family_id: None for family_id in expected}

        final = repo.latest_final_auto_count(connection, session["id"])
        if final is None:
            # Vision session without a final result: human fallback, no AI suggestion to correct.
            suggestions: dict[uuid.UUID, int | None] = {family_id: None for family_id in expected}
        else:
            stored = {uuid.UUID(item["family_id"]): item["detected_quantity"]
                      for item in _vision_counts(final["payload"] if isinstance(final["payload"], dict) else {})}
            suggestions = {family_id: stored.get(family_id, 0) for family_id in expected}
        for line in lines:
            if line["suggested"] != suggestions[line["family_id"]]:
                raise ApiError.conflict("suggested_quantity does not match the stored vision result",
                                        details={"family_id": str(line["family_id"]), "field": "suggested_quantity"})
        return suggestions

    @staticmethod
    def _validate_justifications(lines: list[dict[str, Any]], expected: dict[uuid.UUID, int],
                                 suggested: dict[uuid.UUID, int | None]) -> None:
        for line in lines:
            family_id = line["family_id"]
            ai = suggested[family_id]
            correction = ai is not None and ai != line["reported"]
            if correction and not (line["notes"] or "").strip():
                raise ApiError(400, "notes are required when the reported quantity differs from the AI suggestion",
                               details={"family_id": str(family_id), "field": "notes"})
            if not correction and line["notes"] is not None:
                # notes only exist as human_correction.justification: never accepted to be discarded.
                raise ApiError(400, "notes are only allowed when the reported quantity differs from the AI suggestion",
                               details={"family_id": str(family_id), "field": "notes"})
            if line["reported"] != expected[family_id] and line["reason_code"] is None:
                raise ApiError(400, "reason_code is required when the reported quantity differs from the expected",
                               details={"family_id": str(family_id), "field": "reason_code"})
