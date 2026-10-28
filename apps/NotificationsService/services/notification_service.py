"""Notifications of the authenticated user (PEF-DS09 §1.3.6): list, detail, unread count, mark read.

Every query is scoped to recipient_user_id = token user AND institution_id = the user's current
institution: another user's id, or a notification of another institution, is simply not found (404),
so its existence is never revealed. Marking read is idempotent (read_at is set once).
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import text

from auth import Principal, parse_uuid
from database import Database
from errors import ApiError

TYPES = ("session_scheduled", "discrepancy_pending", "correction_required", "discrepancy_resolved", "session_closed")
STATUSES = ("unread", "read")
LIST_PARAMS = frozenset({"status", "type", "from", "to", "page", "per_page", "sort"})
MAX_PER_PAGE = 100
# Neutral English text for API clients; the portal shows its own localized text for each type.
TITLES = {
    "session_scheduled": "Session scheduled",
    "discrepancy_pending": "Discrepancy pending review",
    "correction_required": "Correction required",
    "discrepancy_resolved": "Discrepancy resolved",
    "session_closed": "Safe close confirmed",
}
MESSAGES = {
    "session_scheduled": "Counting session {session_label} has been scheduled for you.",
    "discrepancy_pending": "Counting session {session_label} has open discrepancies waiting for SPD review.",
    "correction_required": "Counting session {session_label} requires correction: its discrepancies are under SPD review.",
    "discrepancy_resolved": "A discrepancy of counting session {session_label} was resolved by SPD.",
    "session_closed": "SPD confirmed the safe close of counting session {session_label}.",
}
COLUMNS = "id, type, status, session_id, data, created_at, read_at"


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def item(row: dict[str, Any]) -> dict[str, Any]:
    data = row["data"] if isinstance(row["data"], dict) else {}
    return {
        "id": str(row["id"]),
        "type": row["type"],
        "status": row["status"],
        "title": TITLES[row["type"]],
        "message": MESSAGES[row["type"]].format(session_label=data.get("session_label", "")),
        "session_id": str(row["session_id"]) if row["session_id"] else None,
        "data": {key: data[key] for key in ("session_label", "scheduled_at", "open_discrepancies") if key in data},
        "created_at": _iso(row["created_at"]),
        "read_at": _iso(row["read_at"]),
    }


def _invalid(field: str, message: str, **details) -> ApiError:
    return ApiError.invalid_request(message, details={"field": field, **details})


def _instant(raw: str, field: str, *, end: bool) -> datetime:
    try:
        if len(raw) == 10:
            day = date.fromisoformat(raw)
            return datetime.combine(day + timedelta(days=1) if end else day, time.min, tzinfo=timezone.utc)
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    except ValueError:
        raise _invalid(field, f"{field} must be an ISO-8601 date or date-time") from None


def _filters(args) -> tuple[list[str], dict[str, Any]]:
    unknown = sorted(set(args) - LIST_PARAMS)
    if unknown:
        raise ApiError.invalid_request("Unknown query parameters", details={"unknown_parameters": unknown})
    conditions, params = [], {}
    if args.get("status"):
        if args["status"] not in STATUSES:
            raise _invalid("status", "status must be unread or read", allowed=list(STATUSES))
        conditions.append("status = :status")
        params["status"] = args["status"]
    if args.get("type"):
        if args["type"] not in TYPES:
            raise _invalid("type", "type is not a notification type", allowed=list(TYPES))
        conditions.append("type = :type")
        params["type"] = args["type"]
    if args.get("from"):
        params["from_at"] = _instant(args["from"], "from", end=False)
        conditions.append("created_at >= :from_at")
    if args.get("to"):
        params["to_at"] = _instant(args["to"], "to", end=True)
        conditions.append("created_at < :to_at")
    if "from_at" in params and "to_at" in params and params["from_at"] >= params["to_at"]:
        raise _invalid("to", "to must be later than from")
    return conditions, params


class NotificationService:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _scope(principal: Principal) -> tuple[str, dict[str, Any]]:
        return ("recipient_user_id = :me AND institution_id = :inst",
                {"me": principal.user_id, "inst": principal.institution_id})

    def list(self, principal: Principal, args) -> dict[str, Any]:
        conditions, params = _filters(args)
        try:
            page = int(args.get("page", 1))
            per_page = int(args.get("per_page", 20))
        except ValueError:
            raise _invalid("page", "page and per_page must be integers") from None
        if page < 1:
            raise _invalid("page", "page must be >= 1")
        if not 1 <= per_page <= MAX_PER_PAGE:
            raise _invalid("per_page", f"per_page must be between 1 and {MAX_PER_PAGE}")
        sort = args.get("sort", "-created_at")
        if sort not in ("-created_at", "created_at"):
            raise _invalid("sort", "sort must be -created_at or created_at")
        scope, scope_params = self._scope(principal)
        where = " AND ".join([scope, *conditions])
        order = "DESC" if sort.startswith("-") else "ASC"
        with self.database.connect() as connection:
            total = connection.execute(text(f"SELECT count(*) FROM notification WHERE {where}"),
                                       {**scope_params, **params}).scalar_one()
            unread = connection.execute(text(f"SELECT count(*) FROM notification WHERE {scope} AND status = 'unread'"),
                                        scope_params).scalar_one()
            rows = connection.execute(text(
                f"SELECT {COLUMNS} FROM notification WHERE {where} ORDER BY created_at {order}, id {order} "
                "LIMIT :limit OFFSET :offset"),
                {**scope_params, **params, "limit": per_page, "offset": (page - 1) * per_page}).mappings().all()
        return {"items": [item(dict(row)) for row in rows], "page": page, "per_page": per_page, "total": int(total),
                "total_pages": max(1, (int(total) + per_page - 1) // per_page), "unread": int(unread), "sort": sort}

    def get(self, principal: Principal, raw_id: str) -> dict[str, Any]:
        notification_id = parse_uuid(raw_id)
        if notification_id is None:
            raise ApiError.not_found("Notification not found")
        scope, params = self._scope(principal)
        with self.database.connect() as connection:
            row = connection.execute(text(f"SELECT {COLUMNS} FROM notification WHERE {scope} AND id = :id"),
                                     {**params, "id": notification_id}).mappings().first()
        if row is None:   # unknown, another user's or another institution's: same answer
            raise ApiError.not_found("Notification not found")
        return item(dict(row))

    def unread_count(self, principal: Principal) -> dict[str, Any]:
        scope, params = self._scope(principal)
        with self.database.connect() as connection:
            count = connection.execute(text(f"SELECT count(*) FROM notification WHERE {scope} AND status = 'unread'"),
                                       params).scalar_one()
        return {"unread": int(count)}

    def mark_read(self, principal: Principal, raw_id: str) -> dict[str, Any]:
        notification_id = parse_uuid(raw_id)
        if notification_id is None:
            raise ApiError.not_found("Notification not found")
        scope, params = self._scope(principal)
        with self.database.begin() as connection:
            row = connection.execute(text(
                f"UPDATE notification SET status = 'read', read_at = COALESCE(read_at, now()) "
                f"WHERE {scope} AND id = :id RETURNING {COLUMNS}"), {**params, "id": notification_id}).mappings().first()
        if row is None:
            raise ApiError.not_found("Notification not found")
        return item(dict(row))

    def mark_all_read(self, principal: Principal, body: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(body) - {"type", "before"})
        if unknown:
            raise ApiError.invalid_request("Unknown fields in request body", details={"unknown_fields": unknown})
        scope, params = self._scope(principal)
        conditions = [scope, "status = 'unread'"]
        if body.get("type") is not None:
            if body["type"] not in TYPES:
                raise _invalid("type", "type is not a notification type", allowed=list(TYPES))
            conditions.append("type = :type")
            params["type"] = body["type"]
        if body.get("before") is not None:
            if not isinstance(body["before"], str):
                raise _invalid("before", "before must be an ISO-8601 date-time")
            params["before"] = _instant(body["before"], "before", end=False)
            conditions.append("created_at < :before")
        with self.database.begin() as connection:
            updated = connection.execute(text(
                f"UPDATE notification SET status = 'read', read_at = now() WHERE {' AND '.join(conditions)}"),
                params).rowcount
            remaining = connection.execute(text(f"SELECT count(*) FROM notification WHERE {scope} AND status = 'unread'"),
                                           {"me": principal.user_id, "inst": principal.institution_id}).scalar_one()
        return {"updated": int(updated), "unread": int(remaining)}
