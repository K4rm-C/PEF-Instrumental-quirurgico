"""Incremental change feed (MS-07) over `sync_change` (migration 014).

Ordering and consistency
------------------------
Triggers write one `sync_change` row per created / updated / deactivated / deleted resource, in the same
transaction as the change (nothing is published if it rolls back). Each row carries the xid8 of that
transaction. A page only contains rows of transactions that have already finished
(`txid < pg_snapshot_xmin(pg_current_snapshot())`), ordered by (txid, seq), and the cursor is the
(txid, seq) of the last row delivered. A transaction that started earlier but commits later always has
txid >= the watermark used before, so it can never fall "behind" a cursor: no change is skipped and none is
delivered twice, whatever the concurrency, without trusting any client clock.

Cursor
------
Opaque: `v1.<base64url(json {t, s, i})>.<hmac>` — position (txid, seq) and issue time, signed with a server
key (a forged or edited cursor is 400 invalid_cursor). A cursor older than SYNC_CURSOR_MAX_AGE_DAYS answers
410 cursor_expired: the client restarts without a cursor (full snapshot). Compaction keeps the LATEST change
of every resource (deletions and deactivations included), so a restart always rebuilds the full state.

Visibility (never more than the owner services would show)
----------------------------------------------------------
Each change carries `data`: a read-only projection of the CURRENT state of the resource (public fields only;
no patient data, no ROI, no secrets) and `href` to the owner API. `data_version` is the resource's current
version: a client applies `data` when it is newer than what it has.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from datetime import timezone
from typing import Any

from sqlalchemy import text

from auth import ADMIN, OPERATOR, SUPERVISOR, Principal
from errors import ApiError


@dataclass(frozen=True)
class Resource:
    name: str
    roles: tuple[str, ...]           # user roles that may read it
    scope: str | None                # machine-client scope that may read it (None: no client)
    projection: str                  # SELECT ... WHERE x.id = ANY(:ids)
    href: str


ALL_ROLES = (ADMIN, SUPERVISOR, OPERATOR)
RESOURCES: dict[str, Resource] = {r.name: r for r in (
    Resource("instrument_family", ALL_ROLES, "sync.catalog.read",
             "SELECT f.id, f.code, f.name, f.active, c.code AS category_code FROM instrument_family f "
             "LEFT JOIN cat_instrument_category c ON c.id = f.category_id WHERE f.id = ANY(:ids)",
             "/api/v1/catalog/families/{id}"),
    Resource("kit", ALL_ROLES, "sync.catalog.read",
             "SELECT k.id, k.name, k.version, k.active, COALESCE((SELECT jsonb_agg(jsonb_build_object("
             "'family_id', ki.family_id, 'quantity', ki.quantity) ORDER BY ki.family_id) FROM kit_item ki "
             "WHERE ki.kit_id = k.id), '[]'::jsonb) AS items FROM kit k WHERE k.id = ANY(:ids)",
             "/api/v1/catalog/kits/{id}"),
    Resource("instrument", (ADMIN, SUPERVISOR), "sync.catalog.read",
             "SELECT i.id, i.internal_code, i.family_id, i.active, s.code AS cycle_status_code FROM instrument i "
             "LEFT JOIN cat_instrument_cycle_status s ON s.id = i.cycle_status_id WHERE i.id = ANY(:ids)",
             "/api/v1/catalog/instruments/{id}"),
    Resource("surgical_tray", ALL_ROLES, "sync.catalog.read",
             "SELECT t.id, t.code, t.name, t.kit_id, t.status, t.version FROM surgical_tray t WHERE t.id = ANY(:ids)",
             "/api/v1/catalog/trays/{id}"),
    Resource("operating_room", ALL_ROLES, "sync.locations.read",
             "SELECT r.id, r.code, r.name, r.active FROM operating_room r WHERE r.id = ANY(:ids)", ""),
    Resource("capture_station", ALL_ROLES, "sync.locations.read",
             "SELECT s.id, s.name, s.room_id, s.active FROM capture_station s WHERE s.id = ANY(:ids)", ""),
    Resource("capture_camera", ALL_ROLES, "sync.locations.read",
             "SELECT c.id, c.station_id, c.name, c.device_type, c.active FROM capture_camera c WHERE c.id = ANY(:ids)", ""),
    Resource("physician", (ADMIN, SUPERVISOR), "sync.practitioners.read",
             "SELECT p.id, p.name, p.active FROM physician p WHERE p.id = ANY(:ids)", ""),
    Resource("work_session", (SUPERVISOR, OPERATOR), "sync.sessions.read",
             "SELECT w.id, st.code AS status, w.kit_id, w.station_id, (to_jsonb(w) ->> 'tray_id') AS tray_id, "
             "w.user_id AS operator_user_id, w.capture_mode, w.started_at, w.ended_at FROM work_session w "
             "JOIN cat_session_status st ON st.id = w.status_id WHERE w.id = ANY(:ids)",
             "/api/v1/sessions/{id}"),
)}

_FEED_SQL = """
SELECT c.seq, c.txid::text AS txid, c.resource_type, c.resource_id, c.operation, c.version, c.changed_at,
       v.version AS data_version
FROM sync_change c
LEFT JOIN sync_resource_version v ON v.resource_type = c.resource_type AND v.resource_id = c.resource_id
WHERE c.txid < pg_snapshot_xmin(pg_current_snapshot())
  AND (c.txid, c.seq) > (CAST(:t AS xid8), :s)
  AND c.resource_type = ANY(:types)
  AND (c.institution_id = :inst OR (c.institution_id IS NULL AND c.resource_type = 'instrument_family'))
  AND (c.resource_type <> 'work_session' OR :all_sessions OR c.owner_user_id = :uid)
ORDER BY c.txid, c.seq
LIMIT :limit
"""


def visible_types(principal: Principal) -> list[str]:
    if principal.is_user:
        return [name for name, r in RESOURCES.items() if any(principal.has_role(code) for code in r.roles)]
    return [name for name, r in RESOURCES.items() if r.scope and principal.has_scope(r.scope)]


class CursorCodec:
    def __init__(self, key: bytes, max_age_days: int):
        self.key, self.max_age = key, max_age_days * 86400

    def encode(self, txid: int, seq: int) -> str:
        payload = base64.urlsafe_b64encode(json.dumps({"t": str(txid), "s": seq, "i": int(time.time())},
                                                      separators=(",", ":")).encode()).decode().rstrip("=")
        return f"v1.{payload}.{self._sign(payload)}"

    def _sign(self, payload: str) -> str:
        return hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()[:40]

    def decode(self, cursor: str | None) -> tuple[int, int]:
        if not cursor:
            return 0, 0
        try:
            version, payload, signature = cursor.split(".")
            if version != "v1" or not hmac.compare_digest(signature, self._sign(payload)):
                raise ValueError
            data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            txid, seq, issued = int(data["t"]), int(data["s"]), int(data["i"])
        except (ValueError, KeyError, TypeError):
            raise ApiError.invalid_request("Invalid cursor", details={"field": "cursor", "reason": "invalid_cursor"}) from None
        if time.time() - issued > self.max_age:
            raise ApiError(410, "The cursor has expired: restart the synchronization without a cursor",
                           error="cursor_expired", details={"field": "cursor"})
        return txid, seq


class SyncFeed:
    def __init__(self, database, codec: CursorCodec, page_max: int):
        self.database, self.codec, self.page_max = database, codec, page_max

    def changes(self, principal: Principal, args) -> dict[str, Any]:
        unknown = sorted(set(args) - {"cursor", "types", "limit"})
        if unknown:
            raise ApiError.invalid_request("Unknown query parameter(s)", details={"unknown_parameters": unknown})
        allowed = visible_types(principal)
        requested = [t.strip() for t in (args.get("types") or "").split(",") if t.strip()] or allowed
        bad = sorted(set(requested) - set(RESOURCES))
        if bad:
            raise ApiError.invalid_request("Unknown resource type(s)", details={"field": "types", "allowed": sorted(RESOURCES)})
        forbidden = sorted(set(requested) - set(allowed))
        if forbidden:
            raise ApiError.forbidden(f"Not allowed to synchronize: {', '.join(forbidden)}")
        try:
            limit = int(args.get("limit") or 100)
        except ValueError:
            raise ApiError.invalid_request("limit must be an integer", details={"field": "limit"}) from None
        if not 1 <= limit <= self.page_max:
            raise ApiError.invalid_request(f"limit must be between 1 and {self.page_max}", details={"field": "limit"})
        txid, seq = self.codec.decode(args.get("cursor"))
        all_sessions = not principal.is_user or principal.has_role(SUPERVISOR)
        with self.database.connect() as connection:
            rows = connection.execute(text(_FEED_SQL), {
                "t": str(txid), "s": seq, "types": requested, "inst": principal.institution_id,
                "all_sessions": all_sessions, "uid": principal.id if principal.is_user else uuid.UUID(int=0),
                "limit": limit + 1}).mappings().all()
            has_more = len(rows) > limit
            rows = rows[:limit]
            data = self._projections(connection, rows, principal)
        changes = [{
            "resource_type": r["resource_type"], "resource_id": str(r["resource_id"]), "operation": r["operation"],
            "version": int(r["version"]), "data_version": int(r["data_version"] or r["version"]),
            "changed_at": r["changed_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "href": RESOURCES[r["resource_type"]].href.format(id=r["resource_id"]) or None,
            "data": data.get((r["resource_type"], r["resource_id"])) if r["operation"] != "delete" else None,
        } for r in rows]
        next_cursor = self.codec.encode(int(rows[-1]["txid"]), int(rows[-1]["seq"])) if rows else \
            self.codec.encode(txid, seq)
        return {"changes": changes, "next_cursor": next_cursor, "has_more": has_more, "types": requested}

    @staticmethod
    def _projections(connection, rows, principal: Principal) -> dict[tuple[str, uuid.UUID], dict[str, Any]]:
        by_type: dict[str, set[uuid.UUID]] = {}
        for r in rows:
            if r["operation"] != "delete":
                by_type.setdefault(r["resource_type"], set()).add(r["resource_id"])
        result = {}
        for name, ids in by_type.items():
            for row in connection.execute(text(RESOURCES[name].projection), {"ids": list(ids)}).mappings():
                item = {key: _plain(value) for key, value in row.items()}
                if name == "kit" and not item.get("active") and not principal.has_role(ADMIN):
                    item = {"id": item["id"], "active": False}       # superseded kits: only "it is gone"
                result[(name, row["id"])] = item
        return result


def _plain(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return value
