"""MS-08: physical surgical trays (migration 015_charolas.sql).

A tray is an individual physical object with its own label (`code`, unique per institution), status and, when
known, the kit VERSION it is configured with. It is not the kit: two trays with the same kit are two rows.

Rules (only what is defined; everything else is a documented pending decision):
- Owned by the SPD like kits (MS-01 / P3): spd_supervisor writes; any authenticated user of the institution
  reads. Always scoped to the caller's institution (never from the body).
- `kit_id` must be an ACTIVE kit of the same institution when it is set or changed; a tray keeps the kit
  version it was configured with when that kit is later superseded (the reference is historical, never moved
  automatically). The response says `kit_superseded: true` so the Supervisor can decide.
- Status: active | out_of_service | retired (administrative). The operational cycle (in use, sterilizing...)
  is not defined by the RF: not inferred.
- Optimistic concurrency: PUT requires the `version` the client read; a different current version answers
  409 version_conflict with the current one. Every change: surgical_tray_history (trigger) + access_audit.
- Changing a tray never changes any session: expected quantities stay frozen in each session.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from adapters import audit_adapter as audit
from errors import ApiError

logger = logging.getLogger("catalog.trays")
STATUSES = ("active", "out_of_service", "retired")
CODE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._\-/]{0,63}$")

_TRAY_SQL = """
SELECT t.id, t.code, t.name, t.kit_id, t.status, t.version, t.notes, t.created_at, t.updated_at,
       k.name AS kit_name, k.version AS kit_version, k.active AS kit_active
FROM surgical_tray t LEFT JOIN kit k ON k.id = t.kit_id
WHERE t.institution_id = :inst
"""


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _tray(row) -> dict[str, Any]:
    return {"id": str(row["id"]), "code": row["code"], "name": row["name"], "status": row["status"],
            "version": row["version"], "notes": row["notes"],
            "kit_id": str(row["kit_id"]) if row["kit_id"] else None, "kit_name": row["kit_name"],
            "kit_version": row["kit_version"], "kit_superseded": bool(row["kit_id"]) and not row["kit_active"],
            "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"])}


class TrayService:
    def __init__(self, database):
        self.database = database

    # --- reads ---------------------------------------------------------------------------------------------
    def list(self, principal, args) -> list[dict[str, Any]]:
        unknown = sorted(set(args) - {"status", "kit_id"})
        if unknown:
            raise ApiError.invalid_request("Unknown query parameter(s)", details={"unknown_parameters": unknown})
        sql, params = _TRAY_SQL, {"inst": principal.institution_id}
        if args.get("status"):
            if args["status"] not in STATUSES:
                raise ApiError.invalid_request("Unknown status", details={"field": "status"})
            sql += " AND t.status = :status"
            params["status"] = args["status"]
        if args.get("kit_id"):
            sql += " AND t.kit_id = :kit"
            params["kit"] = self._uuid(args["kit_id"], "kit_id")
        with self.database.connect() as connection:
            return [_tray(r) for r in connection.execute(text(sql + " ORDER BY t.code"), params).mappings()]

    def get(self, principal, raw_id: str, *, with_history: bool = True) -> dict[str, Any]:
        tray_id = self._found_id(raw_id)
        with self.database.connect() as connection:
            row = connection.execute(text(_TRAY_SQL + " AND t.id = :id"),
                                     {"inst": principal.institution_id, "id": tray_id}).mappings().first()
            if not row:
                raise ApiError.not_found("Tray not found")
            tray = _tray(row)
            if with_history:
                tray["history"] = [{
                    "version": h["version"], "changed_at": _iso(h["changed_at"]), "change_type": h["change_type"],
                    "changed_by_user_id": str(h["changed_by_user_id"]) if h["changed_by_user_id"] else None,
                    "changed_by_name": h["changed_by_name"], "before": h["before"], "after": h["after"],
                } for h in connection.execute(text(
                    'SELECT h.*, u.name AS changed_by_name FROM surgical_tray_history h LEFT JOIN "user" u '
                    "ON u.id = h.changed_by_user_id WHERE h.tray_id = :id ORDER BY h.id"), {"id": tray_id}).mappings()]
        return tray

    # --- writes --------------------------------------------------------------------------------------------
    def create(self, principal, payload: dict[str, Any]) -> dict[str, Any]:
        values = self._values(payload, creating=True)
        tray_id = uuid.uuid4()
        try:
            with self.database.begin() as connection:
                self._check_kit(connection, principal, values.get("kit_id"))
                connection.execute(text("""
                    INSERT INTO surgical_tray (id, institution_id, code, name, kit_id, status, notes, created_by_user_id,
                                               updated_by_user_id)
                    VALUES (:id, :inst, :code, :name, :kit, :status, :notes, :by, :by)"""),
                                   {"id": tray_id, "inst": principal.institution_id, "code": values["code"],
                                    "name": values.get("name"), "kit": values.get("kit_id"),
                                    "status": values.get("status") or "active", "notes": values.get("notes"),
                                    "by": principal.user_id})
                audit.record(connection, action="CREATE_TRAY", resource_type="surgical_tray", resource_id=tray_id,
                             principal=principal)
        except IntegrityError as exc:
            if "uk_surgical_tray_code" in str(exc.orig):
                raise ApiError.conflict("A tray with this code already exists in the institution",
                                        details={"field": "code", "reason": "tray_code_taken"}) from None
            raise
        logger.info("tray created id=%s", tray_id)
        return self.get(principal, str(tray_id))

    def update(self, principal, raw_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        tray_id = self._found_id(raw_id)
        values = self._values(payload, creating=False)
        try:
            with self.database.begin() as connection:
                current = connection.execute(text("SELECT * FROM surgical_tray WHERE id = :id AND institution_id = :inst "
                                                  "FOR UPDATE"), {"id": tray_id, "inst": principal.institution_id}).mappings().first()
                if not current:
                    raise ApiError.not_found("Tray not found")
                if values["version"] != current["version"]:
                    raise ApiError(409, "The tray was changed by someone else; reload it", error="version_conflict",
                                   details={"field": "version", "current_version": current["version"]})
                merged = {key: values[key] if key in values else current[key]
                          for key in ("code", "name", "kit_id", "status", "notes")}
                if "kit_id" in values and values["kit_id"] != current["kit_id"]:
                    self._check_kit(connection, principal, values["kit_id"])
                if all(merged[k] == current[k] for k in merged):
                    return self.get(principal, str(tray_id))
                connection.execute(text("""
                    UPDATE surgical_tray SET code = :code, name = :name, kit_id = :kit_id, status = :status, notes = :notes,
                           version = version + 1, updated_at = now(), updated_by_user_id = :by
                    WHERE id = :id"""), {**merged, "by": principal.user_id, "id": tray_id})
                action = "UPDATE_TRAY" if merged["status"] == current["status"] else f"TRAY_STATUS_{merged['status'].upper()}"
                audit.record(connection, action="UPDATE_TRAY", resource_type="surgical_tray", resource_id=tray_id,
                             principal=principal)
                if action != "UPDATE_TRAY":
                    audit.record(connection, action=action, resource_type="surgical_tray", resource_id=tray_id,
                                 principal=principal)
        except IntegrityError as exc:
            if "uk_surgical_tray_code" in str(exc.orig):
                raise ApiError.conflict("A tray with this code already exists in the institution",
                                        details={"field": "code", "reason": "tray_code_taken"}) from None
            raise
        return self.get(principal, str(tray_id))

    # --- helpers -------------------------------------------------------------------------------------------
    @staticmethod
    def _uuid(raw, field: str) -> uuid.UUID:
        try:
            return uuid.UUID(str(raw))
        except (TypeError, ValueError):
            raise ApiError.validation_error(f"{field} must be a UUID", details={"field": field}) from None

    @staticmethod
    def _found_id(raw) -> uuid.UUID:
        try:
            return uuid.UUID(str(raw))
        except (TypeError, ValueError):
            raise ApiError.not_found("Tray not found") from None

    def _values(self, payload: dict[str, Any], *, creating: bool) -> dict[str, Any]:
        allowed = {"code", "name", "kit_id", "status", "notes"} | (set() if creating else {"version"})
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ApiError.invalid_request("Unknown fields", details={"unknown_fields": unknown})
        values: dict[str, Any] = {}
        if creating or "code" in payload:
            code = str(payload.get("code") or "").strip()
            if not CODE_PATTERN.fullmatch(code):
                raise ApiError.validation_error("The tray code is required (max 64 characters: letters, digits, . _ - /)",
                                                details={"field": "code", "reason": "invalid_code"})
            values["code"] = code
        if "name" in payload:
            name = (str(payload["name"]).strip() if payload["name"] is not None else "") or None
            if name and len(name) > 160:
                raise ApiError.validation_error("name is too long", details={"field": "name"})
            values["name"] = name
        if "notes" in payload:
            notes = (str(payload["notes"]).strip() if payload["notes"] is not None else "") or None
            if notes and len(notes) > 500:
                raise ApiError.validation_error("notes is too long", details={"field": "notes"})
            values["notes"] = notes
        if "kit_id" in payload:
            values["kit_id"] = self._uuid(payload["kit_id"], "kit_id") if payload["kit_id"] else None
        if "status" in payload:
            if payload["status"] not in STATUSES:
                raise ApiError.validation_error("status must be active, out_of_service or retired",
                                                details={"field": "status", "reason": "invalid_status"})
            values["status"] = payload["status"]
        if not creating:
            version = payload.get("version")
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ApiError.invalid_request("version (the version you read) is required",
                                               details={"field": "version"})
            values["version"] = version
        return values

    @staticmethod
    def _check_kit(connection, principal, kit_id) -> None:
        if kit_id is None:
            return
        row = connection.execute(text("SELECT active FROM kit WHERE id = :id AND institution_id = :inst"),
                                 {"id": kit_id, "inst": principal.institution_id}).first()
        if not row:
            raise ApiError.invalid_reference("Unknown kit", details={"field": "kit_id", "reason": "invalid_kit"})
        if not row[0]:
            raise ApiError.invalid_reference("Only an active kit version can be assigned to a tray",
                                             details={"field": "kit_id", "reason": "inactive_kit"})
