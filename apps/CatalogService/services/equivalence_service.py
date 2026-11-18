"""MS-12 (ms-kits-charolas): instrument families AUTHORIZED as equivalent to a family of a kit (migration 019).

Rules (only what is technically defined; the clinical use is a pending functional decision):
- Owned by the SPD like kits (MS-01 / P3): spd_supervisor authorizes / revokes; any authenticated user of the
  institution reads. Always scoped to the caller's institution (never from the body).
- The kit must be an ACTIVE kit of the institution; `family_id` must be an item of that kit; the equivalent must be an
  ACTIVE family, different from it, and NOT already an item of the kit (it would already count as itself).
- One active authorization per (kit, family, equivalent). Revoking keeps the row (who / when): history, never deleted.
- An equivalence NEVER changes expected quantities, the reference count or the final-count rule (MS-03.1). It is
  information shown to the Operator (validation) and the Supervisor. Whether a validated equivalent must close a
  classification discrepancy is a functional decision (docs/DECISIONES_PENDIENTES.md, D6).
- Audit: AUTHORIZE_KIT_EQUIVALENCE / REVOKE_KIT_EQUIVALENCE (resource kit_equivalence).
"""

from __future__ import annotations

import uuid
from datetime import timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from adapters import audit_adapter as audit
from errors import ApiError

_SQL = """
SELECT e.id, e.kit_id, e.family_id, f.code AS family_code, f.name AS family_name,
       e.equivalent_family_id, q.code AS equivalent_code, q.name AS equivalent_name, e.notes, e.active,
       e.authorized_by_user_id, ua.name AS authorized_by_name, e.authorized_at,
       e.revoked_by_user_id, ur.name AS revoked_by_name, e.revoked_at
FROM kit_item_equivalence e
JOIN instrument_family f ON f.id = e.family_id
JOIN instrument_family q ON q.id = e.equivalent_family_id
LEFT JOIN "user" ua ON ua.id = e.authorized_by_user_id
LEFT JOIN "user" ur ON ur.id = e.revoked_by_user_id
WHERE e.kit_id = :kit AND e.institution_id = :inst
"""


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _item(row) -> dict[str, Any]:
    return {"id": str(row["id"]), "kit_id": str(row["kit_id"]),
            "family_id": str(row["family_id"]), "family_code": row["family_code"], "family_name": row["family_name"],
            "equivalent_family_id": str(row["equivalent_family_id"]), "equivalent_code": row["equivalent_code"],
            "equivalent_name": row["equivalent_name"], "notes": row["notes"], "active": bool(row["active"]),
            "authorized_by_user_id": str(row["authorized_by_user_id"]), "authorized_by_name": row["authorized_by_name"],
            "authorized_at": _iso(row["authorized_at"]),
            "revoked_by_user_id": str(row["revoked_by_user_id"]) if row["revoked_by_user_id"] else None,
            "revoked_by_name": row["revoked_by_name"], "revoked_at": _iso(row["revoked_at"])}


class EquivalenceService:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def _uuid(raw, field: str, *, not_found: str | None = None) -> uuid.UUID:
        try:
            return uuid.UUID(str(raw))
        except (TypeError, ValueError):
            if not_found:
                raise ApiError.not_found(not_found) from None
            raise ApiError.validation_error(f"{field} must be a UUID", details={"field": field}) from None

    def _kit(self, connection, principal, kit_id: uuid.UUID, *, for_write: bool):
        row = connection.execute(text("SELECT id, active FROM kit WHERE id = :id AND institution_id = :inst"),
                                 {"id": kit_id, "inst": principal.institution_id}).mappings().first()
        if not row:
            raise ApiError.not_found("Kit not found")
        if for_write and not row["active"]:
            raise ApiError(422, "Equivalences are authorized on the ACTIVE version of a kit", error="kit_inactive",
                           details={"field": "kit_id"})
        return row

    def list(self, principal, raw_kit: str, args) -> list[dict[str, Any]]:
        unknown = sorted(set(args) - {"status"})
        if unknown:
            raise ApiError.invalid_request("Unknown query parameter(s)", details={"unknown_parameters": unknown})
        status = args.get("status", "active")
        if status not in ("active", "all"):
            raise ApiError.invalid_request("status must be active or all", details={"field": "status"})
        kit_id = self._uuid(raw_kit, "kit_id", not_found="Kit not found")
        with self.database.connect() as connection:
            self._kit(connection, principal, kit_id, for_write=False)
            sql = _SQL + (" AND e.active" if status == "active" else "") + " ORDER BY f.code, q.code, e.authorized_at"
            return [_item(r) for r in connection.execute(text(sql), {"kit": kit_id, "inst": principal.institution_id}).mappings()]

    def authorize(self, principal, raw_kit: str, payload: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(payload) - {"family_id", "equivalent_family_id", "notes"})
        if unknown:
            raise ApiError.invalid_request("Unknown fields", details={"unknown_fields": unknown})
        kit_id = self._uuid(raw_kit, "kit_id", not_found="Kit not found")
        family_id = self._uuid(payload.get("family_id"), "family_id")
        equivalent_id = self._uuid(payload.get("equivalent_family_id"), "equivalent_family_id")
        notes = (str(payload.get("notes") or "").strip() or None)
        if notes and len(notes) > 300:
            raise ApiError.validation_error("notes is too long (max 300)", details={"field": "notes"})
        if family_id == equivalent_id:
            raise ApiError.validation_error("A family cannot be equivalent to itself",
                                            details={"field": "equivalent_family_id", "reason": "same_family"})
        new_id = uuid.uuid4()
        try:
            with self.database.begin() as connection:
                self._kit(connection, principal, kit_id, for_write=True)
                items = {r[0] for r in connection.execute(text("SELECT family_id FROM kit_item WHERE kit_id = :k"), {"k": kit_id})}
                if family_id not in items:
                    raise ApiError(422, "family_id is not an instrument family of this kit", error="family_not_in_kit",
                                   details={"field": "family_id"})
                if equivalent_id in items:
                    raise ApiError(422, "The equivalent family is already part of the kit", error="equivalent_in_kit",
                                   details={"field": "equivalent_family_id"})
                active = connection.execute(text("SELECT active FROM instrument_family WHERE id = :f"), {"f": equivalent_id}).scalar()
                if not active:
                    raise ApiError(422, "The equivalent family does not exist or is inactive", error="equivalent_unavailable",
                                   details={"field": "equivalent_family_id"})
                connection.execute(text("""
                    INSERT INTO kit_item_equivalence (id, institution_id, kit_id, family_id, equivalent_family_id, notes,
                                                      authorized_by_user_id)
                    VALUES (:id, :inst, :kit, :fam, :eq, :notes, :by)"""),
                                   {"id": new_id, "inst": principal.institution_id, "kit": kit_id, "fam": family_id,
                                    "eq": equivalent_id, "notes": notes, "by": principal.user_id})
                audit.record(connection, action="AUTHORIZE_KIT_EQUIVALENCE", resource_type="kit_equivalence",
                             resource_id=new_id, principal=principal)
        except IntegrityError as exc:
            if "uk_kit_item_equivalence_active" in str(exc.orig):
                raise ApiError.conflict("This equivalence is already authorized for the kit",
                                        details={"reason": "equivalence_exists"}) from None
            raise
        return self._one(principal, kit_id, new_id)

    def revoke(self, principal, raw_kit: str, raw_id: str) -> dict[str, Any]:
        kit_id = self._uuid(raw_kit, "kit_id", not_found="Kit not found")
        eq_id = self._uuid(raw_id, "equivalence_id", not_found="Equivalence not found")
        with self.database.begin() as connection:
            self._kit(connection, principal, kit_id, for_write=False)
            updated = connection.execute(text("""
                UPDATE kit_item_equivalence SET active = FALSE, revoked_at = now(), revoked_by_user_id = :by
                WHERE id = :id AND kit_id = :kit AND institution_id = :inst AND active"""),
                                         {"id": eq_id, "kit": kit_id, "inst": principal.institution_id,
                                          "by": principal.user_id}).rowcount
            if not updated:
                exists = connection.execute(text("SELECT 1 FROM kit_item_equivalence WHERE id = :id AND kit_id = :kit "
                                                 "AND institution_id = :inst"),
                                            {"id": eq_id, "kit": kit_id, "inst": principal.institution_id}).first()
                if not exists:
                    raise ApiError.not_found("Equivalence not found")
                return self._one(principal, kit_id, eq_id)          # already revoked: idempotent
            audit.record(connection, action="REVOKE_KIT_EQUIVALENCE", resource_type="kit_equivalence",
                         resource_id=eq_id, principal=principal)
        return self._one(principal, kit_id, eq_id)

    def _one(self, principal, kit_id, eq_id) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(text(_SQL + " AND e.id = :id"),
                                     {"kit": kit_id, "inst": principal.institution_id, "id": eq_id}).mappings().first()
        return _item(row)
