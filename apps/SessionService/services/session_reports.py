"""Supervisor reports (PEF-DS09 §1.3.4 complement v0.3.0): read-only aggregates of the counting data.

Scope: the caller's institution (spd_supervisor only; the route enforces the role). Filters:
`from` / `to` (ISO date or date-time; a date is a whole day in `tz`; default the last 30 days,
at most 366), `tz` (IANA name known to PostgreSQL, default UTC), `kit_id` and `operator_id` (must
belong to the caller's institution), `status` (session status code). Every query runs in a
PostgreSQL READ ONLY transaction: a report can never change a session, a discrepancy or an
inventory.

A metric without a denominator is null, not 0 — e.g. the average duration of a period without
timed sessions, or the AI-human agreement with fewer than AGREEMENT_MIN_PAIRS validated rows.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from database import Database
from errors import ApiError
from repositories import report_repository as repo

STATUS_CODES = ("scheduled", "in_progress", "awaiting_spd_review", "correction_required", "closed", "aborted")
FILTERS = {"from", "to", "tz", "kit_id", "operator_id", "status"}
PAGED = FILTERS | {"page", "per_page"}
DEFAULT_DAYS = 30
MAX_DAYS = 366
AGREEMENT_MIN_PAIRS = 5
MAX_PER_PAGE = 100


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _num(value, digits: int = 1) -> float | None:
    return None if value is None else round(float(value), digits)


def _pct(part, whole) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


def _uuid(args, name: str) -> uuid.UUID | None:
    raw = (args.get(name) or "").strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ApiError.invalid_request(f"{name} must be a UUID", details={"field": name}) from None


def _int(args, name: str, default: int, low: int, high: int) -> int:
    raw = args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = low - 1
    if not low <= value <= high:
        raise ApiError.invalid_request(f"{name} must be an integer between {low} and {high}",
                                       details={"field": name, "min": low, "max": high})
    return value


class SessionReportService:
    def __init__(self, database: Database):
        self.database = database

    # --- filters ------------------------------------------------------------------------------

    def _instant(self, connection, raw: str, name: str, tz: str, *, end: bool) -> datetime:
        raw = raw.strip()
        try:
            if len(raw) == 10:
                day = datetime.strptime(raw, "%Y-%m-%d").date()
                if end:
                    day += timedelta(days=1)          # a date "to" includes that whole day
                # The day boundary is local to `tz`; PostgreSQL converts it (no tzdata needed here).
                return connection.execute(text("SELECT CAST(:d AS timestamp) AT TIME ZONE :tz"),
                                          {"d": day.isoformat(), "tz": tz}).scalar_one()
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise ApiError.invalid_request(f"{name} must be an ISO-8601 date or date-time", details={"field": name}) from None
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)

    def _filters(self, connection, principal, args, allowed: set[str]) -> dict[str, Any]:
        unknown = sorted(set(args) - allowed)
        if unknown:
            raise ApiError.invalid_request("Unknown query parameter(s)", details={"unknown_parameters": unknown})
        tz = (args.get("tz") or "UTC").strip()
        if not repo.valid_time_zone(connection, tz):
            raise ApiError.invalid_request("tz must be an IANA time zone name", details={"field": "tz"})
        date_to = self._instant(connection, args["to"], "to", tz, end=True) if args.get("to") else datetime.now(timezone.utc)
        date_from = self._instant(connection, args["from"], "from", tz, end=False) if args.get("from") \
            else date_to - timedelta(days=DEFAULT_DAYS)
        if date_from >= date_to:
            raise ApiError.invalid_request("to must be later than from", details={"field": "to"})
        if date_to - date_from > timedelta(days=MAX_DAYS):
            raise ApiError.invalid_request(f"The period cannot exceed {MAX_DAYS} days",
                                           details={"field": "from", "max_days": MAX_DAYS})
        kit_id, operator_id = _uuid(args, "kit_id"), _uuid(args, "operator_id")
        # A kit or user of another institution is "unknown" (nothing about it is revealed).
        if kit_id and not repo.kit_in_institution(connection, kit_id, principal.institution_id):
            raise ApiError.invalid_request("Unknown kit", details={"field": "kit_id"})
        if operator_id and not repo.user_in_institution(connection, operator_id, principal.institution_id):
            raise ApiError.invalid_request("Unknown operator", details={"field": "operator_id"})
        status = (args.get("status") or "").strip() or None
        if status and status not in STATUS_CODES:
            raise ApiError.invalid_request("Unknown session status", details={"field": "status", "allowed": list(STATUS_CODES)})
        return {"institution_id": principal.institution_id, "date_from": date_from, "date_to": date_to, "tz": tz,
                "kit_id": kit_id, "operator_id": operator_id, "status": status}

    @staticmethod
    def _echo(params: dict[str, Any]) -> dict[str, Any]:
        return {"from": _iso(params["date_from"]), "to": _iso(params["date_to"]), "tz": params["tz"],
                "kit_id": str(params["kit_id"]) if params["kit_id"] else None,
                "operator_id": str(params["operator_id"]) if params["operator_id"] else None,
                "status": params["status"]}

    # --- reports ------------------------------------------------------------------------------

    def summary(self, principal, args) -> dict[str, Any]:
        with self.database.read() as connection:
            params = self._filters(connection, principal, args, FILTERS)
            sessions = repo.session_summary(connection, params)
            discrepancies = repo.discrepancy_summary(connection, params)
            by_reason = repo.discrepancies_by_reason(connection, params)
            by_family = repo.discrepancies_by_family(connection, params)
            agreement = repo.ai_agreement(connection, params)
            days = repo.activity(connection, params)
        pairs = agreement["pairs"]
        return {
            "filters": self._echo(params),
            "sessions": {
                "total": sessions["total"],
                "by_status": [{"code": code, "count": sessions[code]} for code in STATUS_CODES],
                "scheduled": sessions["scheduled"],
                "in_progress": sessions["in_progress"],
                "pending_review": sessions["awaiting_spd_review"] + sessions["correction_required"],
                "closed": sessions["closed"],
                "reported": sessions["reported"],
                "timed": sessions["timed"],
                "avg_duration_minutes": _num(sessions["avg_duration_minutes"]),
                "reported_with_discrepancies": sessions["reported_with_discrepancies"],
                "with_discrepancies_pct": _pct(sessions["reported_with_discrepancies"], sessions["reported"]),
            },
            "discrepancies": {
                "total": discrepancies["total"],
                "open": discrepancies["open"],
                "resolved": discrepancies["resolved"],
                "timed": discrepancies["timed"],
                "avg_resolution_minutes": _num(discrepancies["avg_resolution_minutes"]),
                "by_reason": [{"code": r["code"], "name": r["name"], "total": r["total"], "open": r["open"]} for r in by_reason],
                "by_family": [{"family_id": str(r["family_id"]) if r["family_id"] else None, "code": r["code"],
                               "name": r["name"], "total": r["total"], "open": r["open"]} for r in by_family],
            },
            "ai_agreement": {
                "pairs": pairs, "agreed": agreement["agreed"], "corrections": pairs - agreement["agreed"],
                "min_pairs": AGREEMENT_MIN_PAIRS, "sufficient": pairs >= AGREEMENT_MIN_PAIRS,
                "agreement_pct": _pct(agreement["agreed"], pairs) if pairs >= AGREEMENT_MIN_PAIRS else None,
            },
            "activity": [{"date": d["day"].isoformat(), "sessions": d["sessions"], "reported": d["reported"],
                          "with_discrepancies": d["with_discrepancies"]} for d in days],
        }

    def _page(self, args) -> tuple[int, int]:
        return _int(args, "page", 1, 1, 100000), _int(args, "per_page", 20, 1, MAX_PER_PAGE)

    def kits(self, principal, args) -> dict[str, Any]:
        page, per_page = self._page(args)
        with self.database.read() as connection:
            params = self._filters(connection, principal, args, PAGED)
            total, rows = repo.kit_activity(connection, params, limit=per_page, offset=(page - 1) * per_page)
        return {"filters": self._echo(params), "page": page, "per_page": per_page, "total": total,
                "total_pages": max(1, math.ceil(total / per_page)), "kits": [{
                    "kit_id": str(r["kit_id"]) if r["kit_id"] else None, "name": r["kit_name"], "version": r["kit_version"],
                    "active": r["kit_active"], "sessions": r["sessions"], "reported_sessions": r["reported_sessions"],
                    "sessions_with_discrepancies": r["sessions_with_discrepancies"], "discrepancies": int(r["discrepancies"]),
                    "expected_quantity": int(r["expected_quantity"]) if r["expected_quantity"] is not None else None,
                    "expected_quantity_counted": int(r["expected_quantity_counted"])
                    if r["expected_quantity_counted"] is not None else None,
                    "reported_quantity": int(r["reported_quantity"]) if r["reported_quantity"] is not None else None,
                } for r in rows]}

    def operators(self, principal, args) -> dict[str, Any]:
        page, per_page = self._page(args)
        with self.database.read() as connection:
            params = self._filters(connection, principal, args, PAGED)
            total, rows = repo.operator_activity(connection, params, limit=per_page, offset=(page - 1) * per_page)
        items = []
        for r in rows:
            pairs, agreed = int(r["ai_pairs"]), int(r["ai_agreed"])
            items.append({"operator_id": str(r["user_id"]), "name": r["operator_name"], "sessions": r["sessions"],
                          "started_sessions": r["started_sessions"], "counts_completed": r["counts_completed"],
                          "family_counts": int(r["family_counts"]), "ai_pairs": pairs, "ai_agreed": agreed,
                          "human_corrections": pairs - agreed, "discrepancies": int(r["discrepancies"]),
                          "agreement_pct": _pct(agreed, pairs) if pairs >= AGREEMENT_MIN_PAIRS else None})
        return {"filters": self._echo(params), "page": page, "per_page": per_page, "total": total,
                "total_pages": max(1, math.ceil(total / per_page)), "min_pairs": AGREEMENT_MIN_PAIRS, "operators": items}

    def options(self, principal, args) -> dict[str, Any]:
        unknown = sorted(set(args))
        if unknown:
            raise ApiError.invalid_request("Unknown query parameter(s)", details={"unknown_parameters": unknown})
        with self.database.read() as connection:
            options = repo.filter_options(connection, principal.institution_id)
        return {"kits": [{"id": str(k["id"]), "name": k["name"], "version": k["version"], "active": k["active"]}
                         for k in options["kits"]],
                "operators": [{"id": str(o["id"]), "name": o["name"], "active": o["active"]} for o in options["operators"]],
                "statuses": list(STATUS_CODES)}
