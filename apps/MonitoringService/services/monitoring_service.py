"""Read side of the monitoring history (every figure is an aggregate of stored checks).

Scope by role (decided here, not by the caller):
- it_admin: technical detail — latency, HTTP code, dependencies, error type, endpoint, history;
- spd_supervisor: name, label, status, last check and availability % only.

Current state: the latest check of each component, unless it is older than `stale_seconds`
(collector stopped, PostgreSQL unreachable...): then the component is `unknown` — a state that
was not verified recently is never presented as current.

Aggregates over a window (default: the last 24 h):
- availability_pct = (up + degraded) / (checks - unknown) * 100 — the component answered;
- up_pct = up / (checks - unknown) * 100 — it answered with every dependency ok;
- latency (avg, p95, min, max) over the checks it answered (up / degraded): a timeout is an
  outage, not a latency sample.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import text

from config import COMPONENTS, STATUSES
from database import Database
from errors import ApiError

BUCKETS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}
MAX_WINDOW = timedelta(days=31)
MAX_POINTS = 1500
DETAIL_KEYS = ("pending_events", "oldest_pending_seconds", "bound_sessions")


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _num(value) -> float | None:
    return None if value is None else round(float(value), 2)


def _known(args, allowed: set[str]) -> None:
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise ApiError.invalid_request("Unknown query parameter(s)", details={"unknown_parameters": unknown})


def _instant(args, name: str, *, end: bool = False) -> datetime | None:
    raw = (args.get(name) or "").strip()
    if not raw:
        return None
    try:
        if len(raw) == 10:
            day = date.fromisoformat(raw)
            moment = datetime.combine(day + timedelta(days=1) if end else day, time.min, tzinfo=timezone.utc)
        else:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
    except ValueError:
        raise ApiError.invalid_request(f"{name} must be an ISO-8601 date or date-time", details={"field": name}) from None
    return moment


def _int(args, name: str, default: int, low: int, high: int) -> int:
    raw = args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = low - 1
    if value < low or value > high:
        raise ApiError.invalid_request(f"{name} must be an integer between {low} and {high}",
                                       details={"field": name, "min": low, "max": high})
    return value


def _component(args, required: bool = False) -> str | None:
    value = (args.get("component") or "").strip()
    if not value:
        if required:
            raise ApiError.invalid_request("component is required", details={"field": "component"})
        return None
    if value not in COMPONENTS:
        raise ApiError.invalid_request("Unknown component", details={"field": "component", "allowed": list(COMPONENTS)})
    return value


def _window(args, default_hours: int = 24) -> tuple[datetime, datetime]:
    end = _instant(args, "to", end=True) or datetime.now(timezone.utc)
    start = _instant(args, "from") or end - timedelta(hours=default_hours)
    if start >= end:
        raise ApiError.invalid_request("to must be later than from", details={"field": "to"})
    if end - start > MAX_WINDOW:
        raise ApiError.invalid_request("The window cannot exceed 31 days", details={"field": "from", "max_days": 31})
    return start, end


class MonitoringService:
    def __init__(self, database: Database, stale_seconds: float, interval_seconds: float):
        self.database = database
        self.stale_seconds = stale_seconds
        self.interval_seconds = interval_seconds

    # --- rows -> representations -------------------------------------------------------------

    @staticmethod
    def _check(row, *, admin: bool) -> dict[str, Any]:
        base = {"component": row["component"], "status": row["status"], "checked_at": _iso(row["checked_at"])}
        if not admin:
            return base
        deps = row["dependencies"] or {}
        details = row["details"] or {}
        return {"id": str(row["id"]), "run_id": str(row["run_id"]), **base, "latency_ms": _num(row["latency_ms"]),
                "http_status": row["http_status"], "error_type": row["error_type"], "endpoint": row["endpoint"],
                "dependencies": [{"name": k, "status": v} for k, v in sorted(deps.items())],
                "details": {k: details[k] for k in DETAIL_KEYS if k in details}}

    def _current(self, row, name: str, *, admin: bool) -> dict[str, Any]:
        label, kind = COMPONENTS[name]
        item: dict[str, Any] = {"name": name, "label": label}
        if row is None:
            item.update({"status": "unknown", "checked_at": None, "freshness": "never_checked"})
            if admin:
                item.update({"kind": kind, "measured_status": None, "check": None})
            return item
        age = (datetime.now(timezone.utc) - row["checked_at"]).total_seconds()
        stale = age > self.stale_seconds
        item.update({"status": "unknown" if stale else row["status"], "checked_at": _iso(row["checked_at"]),
                     "freshness": "stale" if stale else "current"})
        if admin:
            item.update({"kind": kind, "measured_status": row["status"], "check": self._check(row, admin=True)})
        return item

    def _latest(self, connection, component: str | None = None) -> dict[str, Any]:
        rows = connection.execute(text("""
            SELECT DISTINCT ON (component) id, run_id, component, checked_at, status, latency_ms, http_status,
                   dependencies, error_type, endpoint, details
            FROM monitoring_check
            WHERE checked_at > now() - interval '7 days' AND (CAST(:c AS text) IS NULL OR component = :c)
            ORDER BY component, checked_at DESC
        """), {"c": component}).mappings().all()
        return {row["component"]: row for row in rows}

    # --- endpoints ---------------------------------------------------------------------------

    def services(self, args, *, admin: bool) -> dict[str, Any]:
        _known(args, set())
        with self.database.connect() as connection:
            latest = self._latest(connection)
            run = connection.execute(text(
                "SELECT started_at, finished_at FROM monitoring_run ORDER BY started_at DESC LIMIT 1")).mappings().first()
        items = [self._current(latest.get(name), name, admin=admin) for name in COMPONENTS]
        body: dict[str, Any] = {"items": items, "counts": {s: sum(1 for i in items if i["status"] == s) for s in STATUSES}}
        if admin:
            body["collector"] = {"last_run_at": _iso(run["started_at"]) if run else None,
                                 "interval_seconds": self.interval_seconds, "stale_after_seconds": self.stale_seconds}
        return body

    def service(self, name: str, args) -> dict[str, Any]:
        if name not in COMPONENTS:
            raise ApiError.not_found("Unknown component")
        _known(args, {"recent"})
        recent = _int(args, "recent", 20, 1, 100)
        with self.database.connect() as connection:
            latest = self._latest(connection, name).get(name)
            rows = connection.execute(text("""
                SELECT id, run_id, component, checked_at, status, latency_ms, http_status, dependencies, error_type,
                       endpoint, details
                FROM monitoring_check WHERE component = :c ORDER BY checked_at DESC LIMIT :n
            """), {"c": name, "n": recent}).mappings().all()
            end = datetime.now(timezone.utc)
            stats = self._aggregate(connection, end - timedelta(hours=24), end, name).get(name)
        item = self._current(latest, name, admin=True)
        item["recent"] = [self._check(row, admin=True) for row in rows]
        item["last_24h"] = stats or self._empty_stats(name)
        return item

    def history(self, args) -> dict[str, Any]:
        _known(args, {"component", "status", "from", "to", "page", "per_page", "sort"})
        component = _component(args)
        status = (args.get("status") or "").strip() or None
        if status and status not in STATUSES:
            raise ApiError.invalid_request("Unknown status", details={"field": "status", "allowed": list(STATUSES)})
        start, end = _instant(args, "from"), _instant(args, "to", end=True)
        if start and end and start >= end:
            raise ApiError.invalid_request("to must be later than from", details={"field": "to"})
        page, per_page = _int(args, "page", 1, 1, 100000), _int(args, "per_page", 50, 1, 200)
        sort = args.get("sort") or "-checked_at"
        if sort not in ("-checked_at", "checked_at"):
            raise ApiError.invalid_request("sort must be -checked_at or checked_at",
                                           details={"field": "sort", "allowed": ["-checked_at", "checked_at"]})
        where = """(CAST(:component AS text) IS NULL OR component = :component)
                   AND (CAST(:status AS text) IS NULL OR status = :status)
                   AND (CAST(:start AS timestamptz) IS NULL OR checked_at >= :start)
                   AND (CAST(:end AS timestamptz) IS NULL OR checked_at < :end)"""
        params = {"component": component, "status": status, "start": start, "end": end}
        with self.database.connect() as connection:
            total = connection.execute(text(f"SELECT count(*) FROM monitoring_check WHERE {where}"), params).scalar_one()
            rows = connection.execute(text(f"""
                SELECT id, run_id, component, checked_at, status, latency_ms, http_status, dependencies, error_type,
                       endpoint, details
                FROM monitoring_check WHERE {where}
                ORDER BY checked_at {'DESC' if sort.startswith('-') else 'ASC'}, id
                LIMIT :limit OFFSET :offset
            """), {**params, "limit": per_page, "offset": (page - 1) * per_page}).mappings().all()
        return {"items": [self._check(row, admin=True) for row in rows], "page": page, "per_page": per_page,
                "total": total, "total_pages": max(1, math.ceil(total / per_page)), "sort": sort}

    @staticmethod
    def _empty_stats(name: str) -> dict[str, Any]:
        return {"name": name, "label": COMPONENTS[name][0], "checks": 0, "up": 0, "degraded": 0, "down": 0, "unknown": 0,
                "availability_pct": None, "up_pct": None, "avg_latency_ms": None, "p95_latency_ms": None,
                "min_latency_ms": None, "max_latency_ms": None}

    def _aggregate(self, connection, start, end, component=None) -> dict[str, dict[str, Any]]:
        rows = connection.execute(text("""
            SELECT component, count(*) AS checks,
                   count(*) FILTER (WHERE status = 'up') AS up,
                   count(*) FILTER (WHERE status = 'degraded') AS degraded,
                   count(*) FILTER (WHERE status = 'down') AS down,
                   count(*) FILTER (WHERE status = 'unknown') AS unknown,
                   avg(latency_ms) FILTER (WHERE status IN ('up', 'degraded')) AS avg_latency,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                       FILTER (WHERE status IN ('up', 'degraded')) AS p95_latency,
                   min(latency_ms) FILTER (WHERE status IN ('up', 'degraded')) AS min_latency,
                   max(latency_ms) FILTER (WHERE status IN ('up', 'degraded')) AS max_latency
            FROM monitoring_check
            WHERE checked_at >= :start AND checked_at < :end AND (CAST(:c AS text) IS NULL OR component = :c)
            GROUP BY component
        """), {"start": start, "end": end, "c": component}).mappings().all()
        out = {}
        for row in rows:
            known = row["checks"] - row["unknown"]
            out[row["component"]] = {
                "name": row["component"], "label": COMPONENTS.get(row["component"], (row["component"],))[0],
                "checks": row["checks"], "up": row["up"], "degraded": row["degraded"], "down": row["down"],
                "unknown": row["unknown"],
                "availability_pct": _num(100.0 * (row["up"] + row["degraded"]) / known) if known else None,
                "up_pct": _num(100.0 * row["up"] / known) if known else None,
                "avg_latency_ms": _num(row["avg_latency"]), "p95_latency_ms": _num(row["p95_latency"]),
                "min_latency_ms": _num(row["min_latency"]), "max_latency_ms": _num(row["max_latency"])}
        return out

    def summary(self, args, *, admin: bool) -> dict[str, Any]:
        _known(args, {"from", "to", "component", "bucket"})
        start, end = _window(args)
        component = _component(args)
        bucket = (args.get("bucket") or "").strip() or None
        if bucket is not None:
            if bucket not in BUCKETS:
                raise ApiError.invalid_request("Unknown bucket", details={"field": "bucket", "allowed": list(BUCKETS)})
            if (end - start).total_seconds() / BUCKETS[bucket] > MAX_POINTS:
                raise ApiError.invalid_request("Too many points for this window; use a larger bucket",
                                               details={"field": "bucket", "max_points": MAX_POINTS})
        names = [component] if component else list(COMPONENTS)
        with self.database.connect() as connection:
            stats = self._aggregate(connection, start, end, component)
            series_rows = connection.execute(text("""
                SELECT component, date_bin(make_interval(secs => :step), checked_at, TIMESTAMPTZ '2000-01-01 00:00:00+00') AS bucket,
                       count(*) AS checks,
                       count(*) FILTER (WHERE status IN ('up', 'degraded')) AS answered,
                       count(*) FILTER (WHERE status <> 'unknown') AS known,
                       avg(latency_ms) FILTER (WHERE status IN ('up', 'degraded')) AS avg_latency
                FROM monitoring_check
                WHERE checked_at >= :start AND checked_at < :end AND (CAST(:c AS text) IS NULL OR component = :c)
                GROUP BY 1, 2 ORDER BY 1, 2
            """), {"step": BUCKETS[bucket], "start": start, "end": end, "c": component}).mappings().all() if bucket else []
        components = [stats.get(name) or self._empty_stats(name) for name in names]
        if not admin:
            components = [{k: c[k] for k in ("name", "label", "checks", "availability_pct", "up_pct")} for c in components]
        body: dict[str, Any] = {"window": {"from": _iso(start), "to": _iso(end)}, "components": components}
        if bucket:
            grouped: dict[str, list] = {name: [] for name in names}
            for row in series_rows:
                point = {"bucket_start": _iso(row["bucket"]), "checks": row["checks"],
                         "availability_pct": _num(100.0 * row["answered"] / row["known"]) if row["known"] else None}
                if admin:
                    point["avg_latency_ms"] = _num(row["avg_latency"])
                grouped.setdefault(row["component"], []).append(point)
            body["bucket"] = bucket
            body["series"] = [{"name": name, "points": grouped.get(name, [])} for name in names]
        return body

