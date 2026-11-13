"""Audit / event queries (PEF-DS09 §1.3.6): validation, scope and the public representation.

Never exposed: passwords or hashes (no audit table holds them), tokens, request bodies, patient data
(no audit row carries it) or free text typed by the Operator (count_event notes / justification).
The actor's e-mail is shown only to it_admin; the IP to it_admin and the SPD Supervisor (the RF
audit screens already showed it).
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from auth import Principal, parse_uuid
from database import Database
from errors import ApiError
from repositories import event_repository as repo

CATEGORIES = ("security", "business", "operation")
OUTCOMES = ("success", "denied", "blocked")
SOURCES = ("auth", "catalog", "session", "evidence", "web", "audit")
RECORD_TYPES = ("access_audit", "count_event")
SORTS = ("-occurred_at", "occurred_at")
LIST_PARAMS = frozenset({"institution_id", "user_id", "action", "category", "outcome", "source_service",
                         "record_type", "resource_type", "session_id", "correlation_id", "from", "to", "q",
                         "page", "per_page", "sort"})
SUMMARY_PARAMS = LIST_PARAMS - {"page", "per_page", "sort"}
MAX_PER_PAGE = 100
_TOKEN = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")
# count_event payload keys that describe the fact (never boxes, frames, notes or free text).
PAYLOAD_KEYS = ("trigger", "reason", "scope", "capture_mode", "next_status", "open_discrepancies",
                "operator_close_path", "close_path", "reference_before", "confirmed_by_role", "model_version")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _invalid(field: str, message: str, **details) -> ApiError:
    return ApiError.invalid_request(message, details={"field": field, **details})


def _parse_instant(raw: str, field: str, *, end: bool) -> datetime:
    """ISO-8601 date-time (offset or Z; naive = UTC) or a date (from → its start, to → next day)."""
    try:
        if len(raw) == 10:
            day = date.fromisoformat(raw)
            moment = datetime.combine(day + timedelta(days=1) if end else day, time.min, tzinfo=timezone.utc)
        else:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
    except ValueError:
        raise _invalid(field, f"{field} must be an ISO-8601 date or date-time") from None
    return moment


def parse_filters(principal: Principal, args, allowed: frozenset[str]) -> dict[str, Any]:
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise ApiError.invalid_request("Unknown query parameters", details={"unknown_parameters": unknown})
    filters: dict[str, Any] = {}
    if args.get("institution_id"):
        institution = parse_uuid(args["institution_id"])
        if institution is None:
            raise _invalid("institution_id", "institution_id must be a UUID")
        if institution != principal.institution_id:
            # The scope is the caller's institution; another one is never readable.
            raise ApiError(403, "Events of another institution are outside your scope",
                           details={"field": "institution_id", "reason": "out_of_scope"})
    for name in ("user_id", "session_id"):
        if args.get(name):
            value = parse_uuid(args[name])
            if value is None:
                raise _invalid(name, f"{name} must be a UUID")
            filters[name] = value
    for name, allowed_values in (("category", CATEGORIES), ("outcome", OUTCOMES), ("source_service", SOURCES),
                                 ("record_type", RECORD_TYPES)):
        if args.get(name):
            if args[name] not in allowed_values:
                raise _invalid(name, f"{name} must be one of {', '.join(allowed_values)}", allowed=list(allowed_values))
            filters[name] = args[name]
    for name in ("action", "resource_type", "correlation_id"):
        if args.get(name):
            if not _TOKEN.match(args[name]):
                raise _invalid(name, f"{name} must be 1-64 characters of [A-Za-z0-9._:-]")
            filters[name] = args[name]
    if args.get("from"):
        filters["date_from"] = _parse_instant(args["from"], "from", end=False)
    if args.get("to"):
        filters["date_to"] = _parse_instant(args["to"], "to", end=True)
    if "date_from" in filters and "date_to" in filters and filters["date_from"] >= filters["date_to"]:
        raise _invalid("to", "to must be later than from")
    if args.get("q"):
        q = args["q"].strip()
        if len(q) > 100:
            raise _invalid("q", "q must contain at most 100 characters")
        filters["q"] = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return filters


def _page_args(args) -> tuple[int, int, str]:
    try:
        page = int(args.get("page", 1))
        per_page = int(args.get("per_page", 20))
    except ValueError:
        raise _invalid("page", "page and per_page must be integers") from None
    if page < 1:
        raise _invalid("page", "page must be >= 1")
    if not 1 <= per_page <= MAX_PER_PAGE:
        raise _invalid("per_page", f"per_page must be between 1 and {MAX_PER_PAGE}")
    sort = args.get("sort", "-occurred_at")
    if sort not in SORTS:
        raise _invalid("sort", "sort must be -occurred_at or occurred_at", allowed=list(SORTS))
    return page, per_page, sort


def event_item(row: dict[str, Any], scope: str) -> dict[str, Any]:
    is_count = row["record_type"] == "count_event"
    payload = row["payload"] if isinstance(row["payload"], dict) else {}
    actor = None
    if row["user_id"]:
        actor = {"type": row["actor_type"], "user_id": str(row["user_id"]), "name": row["user_name"],
                 "email": row["user_email"] if scope == "admin" else None,
                 "role_codes": [code for code in (row["role_codes"] or "").split(",") if code]}
    resource_id = row["resource_id"]
    item = {
        "id": str(row["id"]),
        "record_type": row["record_type"],
        "occurred_at": _iso(row["occurred_at"]),
        "category": row["category"],
        "source_service": row["source_service"],
        "action": row["action"],
        "outcome": row["outcome"],
        "actor": actor,
        "institution_id": str(row["institution_id"]) if row["institution_id"] else None,
        "resource": {"type": row["resource_type"], "id": str(resource_id), "label": row["resource_label"]},
        "session_id": str(row["session_id"]) if row["session_id"] else None,
        "correlation_id": row["correlation_id"],
        "ip": row["ip"],
        "details": None,
    }
    if is_count:
        item["details"] = {
            "expected_quantity": row["expected_quantity"],
            "detected_quantity": row["detected_quantity"],
            "family_id": str(row["family_id"]) if row["family_id"] else None,
            "family_code": row["family_code"],
            **{key: payload[key] for key in PAYLOAD_KEYS if key in payload and not isinstance(payload[key], (dict, list))},
        }
    return item


class EventService:
    def __init__(self, database: Database):
        self.database = database

    def list_events(self, principal: Principal, args) -> dict[str, Any]:
        filters = parse_filters(principal, args, LIST_PARAMS)
        page, per_page, sort = _page_args(args)
        with self.database.read() as connection:
            total, rows = repo.search(connection, principal.scope, principal.institution_id, filters,
                                      descending=sort.startswith("-"), limit=per_page, offset=(page - 1) * per_page)
        return {
            "items": [event_item(row, principal.scope) for row in rows],
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": max(1, (total + per_page - 1) // per_page),
            "sort": sort,
        }

    def get_event(self, principal: Principal, raw_id: str) -> dict[str, Any]:
        event_id = parse_uuid(raw_id)
        if event_id is None:
            raise ApiError.not_found("Event not found")
        with self.database.read() as connection:
            row = repo.get(connection, principal.scope, principal.institution_id, event_id)
        if row is None:   # unknown, of another institution or outside the Supervisor's scope: same answer
            raise ApiError.not_found("Event not found")
        return event_item(row, principal.scope)

    def summary(self, principal: Principal, args) -> dict[str, Any]:
        filters = parse_filters(principal, args, SUMMARY_PARAMS)
        with self.database.read() as connection:
            data = repo.summary(connection, principal.scope, principal.institution_id, filters)
        return {
            "total": data["total"],
            "first_at": _iso(data["first_at"]),
            "last_at": _iso(data["last_at"]),
            "by_category": data["by_category"],
            "by_source_service": data["by_source_service"],
            "by_outcome": data["by_outcome"],
            "by_record_type": data["by_record_type"],
            "by_action": data["by_action"],
            "by_resource_type": data["by_resource_type"],
            "by_user": [{"user_id": str(row["user_id"]), "name": row["name"],
                         "email": row["email"] if principal.scope == "admin" else None, "count": row["count"]}
                        for row in data["by_user"]],
            "by_session": [{"session_id": str(row["session_id"]),
                            "label": "WS-" + str(row["session_id"]).replace("-", "")[:8].upper(),
                            "last_at": _iso(row["last_at"]), "count": row["count"]} for row in data["by_session"]],
        }
