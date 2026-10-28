"""Catalog management use cases (PEF-DS09 §1.3.3 complement v0.1.2; fields and rules of PEF-DS03).

Business rules moved from the portal's Administration (apps/BackendWebFlask/services/admin_local):
families (code immutable, category from the catalog), instruments (unique internal code per
institution, cycle history, change reason when the cycle or the active status changes), kits
(validated composition, one new version per change, the previous version deactivated and its
procedure links moved) and the access_audit entries of every change.

Every write is one transaction; instruments and kits are always scoped to the caller's institution
(derived from the JWT/user, never from the body). Validation errors carry `details.reason`, a stable
machine code (the portal maps it to its localized messages).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError

from adapters import audit_adapter as audit
from auth import ADMIN_ROLE_CODE
from database import Database
from errors import ApiError
from repositories import management_repository as repo

logger = logging.getLogger("catalog.management")

INSTRUMENT_CODE_UNIQUE_INDEX = "uk_instrument_institution_internal_code"
KIT_UNIQUE_CONSTRAINT = "uk_kit_institution_name_version"
MAX_QUANTITY = 32767


def sees_inactive_kits(principal) -> bool:
    """Only it_admin works with inactive (superseded / retired) kits; any other role, the SPD
    supervisor included, sees just the active kits of its institution, also over direct HTTP."""
    return principal.has_role(ADMIN_ROLE_CODE)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _uuid(value, *, field: str, reason: str, message: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise ApiError.validation_error(message, details={"field": field, "reason": reason}) from None


def _not_found_id(raw: str, message: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        raise ApiError.not_found(message) from None


# --- Body validation (400 = malformed / wrong type, 422 = invalid value) ----------------------


def _check_fields(payload: dict[str, Any], allowed: set[str], required: tuple[str, ...]) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ApiError.invalid_request("Unknown fields in request body", details={"unknown_fields": unknown})
    missing = [name for name in required if name not in payload]
    if missing:
        raise ApiError.invalid_request("Required fields are missing", details={"missing_fields": missing})


def _text(payload: dict[str, Any], name: str, *, required: bool, max_length: int | None = None,
          reason: str | None = None) -> str | None:
    raw = payload.get(name)
    if raw is not None and not isinstance(raw, str):
        raise ApiError.invalid_request("Field must be a string or null", details={"field": name})
    value = (raw or "").strip()
    if required and not value:
        raise ApiError.validation_error("Field must not be empty",
                                        details={"field": name, "reason": reason or f"{name}_required"})
    if max_length and len(value) > max_length:
        raise ApiError.validation_error("Field is too long", details={
            "field": name, "max_length": max_length, "reason": f"{name}_too_long"})
    return value or None


def _bool(payload: dict[str, Any], name: str, default: bool = True) -> bool:
    if name not in payload or payload[name] is None:
        return default
    if not isinstance(payload[name], bool):
        raise ApiError.invalid_request("Field must be a boolean", details={"field": name})
    return payload[name]


# --- Representations --------------------------------------------------------------------------


def category_item(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(row["id"]), "code": row["code"], "name": row["name"]}


def family_detail(row: dict[str, Any], *, available_count: int, kits_using: int | None = None) -> dict[str, Any]:
    item = {
        "id": str(row["id"]), "code": row["code"], "name": row["name"], "category_code": row["category_code"],
        "active": bool(row["active"]), "category_id": str(row["category_id"]), "category_name": row["category_name"],
        "identify_text": row["identify_text"], "classify_text": row["classify_text"],
        "function_text": row["function_text"], "available_count": available_count,
    }
    if kits_using is not None:
        item["kits_using"] = kits_using
    return item


def instrument_detail(row: dict[str, Any], events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    item = {
        "id": str(row["id"]), "internal_code": row["internal_code"], "family_id": str(row["family_id"]),
        "family_code": row["family_code"], "cycle_status_code": row["cycle_status_code"],
        "institution_id": str(row["institution_id"]), "active": bool(row["active"]),
        "family_name": row["family_name"], "category_code": row["category_code"],
        "category_name": row["category_name"], "cycle_status_name": row["cycle_status_name"],
    }
    if events is not None:
        item["cycle_events"] = [{
            "occurred_at": _iso(event["occurred_at"]), "cycle_status_code": event["cycle_status_code"],
            "cycle_status_name": event["cycle_status_name"], "notes": event["notes"],
        } for event in events]
    return item


def kit_summary(row: dict[str, Any], totals: tuple[int, int], procedures: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "name": row["name"], "version": int(row["version"]), "active": bool(row["active"]),
        "institution_id": str(row["institution_id"]), "created_at": _iso(row["created_at"]),
        "family_count": totals[0], "total_expected": totals[1], "procedures": procedures,
    }


class ManagementService:
    def __init__(self, database: Database):
        self.database = database

    # --- Reference catalogs -----------------------------------------------------------------

    def list_categories(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            return [category_item(row) for row in repo.list_categories(connection)]

    def list_cycle_statuses(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            return [category_item(row) for row in repo.list_cycle_statuses(connection)]

    # --- Families ---------------------------------------------------------------------------

    def list_families_detail(self, principal) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            counts = repo.available_counts(connection, principal.institution_id)
            return [family_detail(row, available_count=counts.get(row["id"], 0))
                    for row in repo.list_families_detail(connection)]

    def get_family(self, principal, raw_id: str) -> dict[str, Any]:
        family_id = _not_found_id(raw_id, "Instrument family not found")
        with self.database.connect() as connection:
            return self._family(connection, principal, family_id)

    def _family(self, connection, principal, family_id: uuid.UUID) -> dict[str, Any]:
        row = repo.get_family_detail(connection, family_id)
        if row is None:
            raise ApiError.not_found("Instrument family not found")
        return family_detail(row, available_count=repo.available_counts(connection, principal.institution_id)
                             .get(family_id, 0),
                             kits_using=repo.kits_using_family(connection, family_id, principal.institution_id))

    def update_family(self, principal, raw_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        family_id = _not_found_id(raw_id, "Instrument family not found")
        _check_fields(payload, {"name", "category_code", "identify_text", "classify_text", "function_text", "active"},
                      ("name", "category_code"))
        name = _text(payload, "name", required=True, max_length=160)
        category_code = _text(payload, "category_code", required=True, max_length=64, reason="category_required")
        texts = {name_: _text(payload, name_, required=False)
                 for name_ in ("identify_text", "classify_text", "function_text")}
        active = _bool(payload, "active")
        with self.database.begin() as connection:
            current = repo.get_family_detail(connection, family_id, for_update=True)
            if current is None:
                raise ApiError.not_found("Instrument family not found")
            category = repo.get_category_by_code(connection, category_code)
            if category is None:
                raise ApiError.invalid_reference("category_code does not exist", details={
                    "field": "category_code", "value": category_code, "reason": "invalid_category"})
            values = {"name": name, "category_id": category["id"], **texts, "active": active}
            before = (current["name"], current["category_id"], current["identify_text"], current["classify_text"],
                      current["function_text"])
            after = (name, category["id"], texts["identify_text"], texts["classify_text"], texts["function_text"])
            repo.update_family(connection, family_id, values)
            audit.record_update(connection, entity="INSTRUMENT_FAMILY", resource_type="instrument_family",
                                resource_id=family_id, principal=principal, was_active=bool(current["active"]),
                                is_active=active, changed=before != after)
            result = self._family(connection, principal, family_id)
        logger.info("instrument family updated id=%s", family_id)
        return result

    # --- Instruments ------------------------------------------------------------------------

    def list_instruments_detail(self, principal) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            return [instrument_detail(row) for row in repo.list_instruments_detail(connection, principal.institution_id)]

    def get_instrument(self, principal, raw_id: str) -> dict[str, Any]:
        instrument_id = _not_found_id(raw_id, "Instrument not found")
        with self.database.connect() as connection:
            return self._instrument(connection, principal, instrument_id)

    def _instrument(self, connection, principal, instrument_id: uuid.UUID) -> dict[str, Any]:
        row = repo.get_instrument_detail(connection, instrument_id, principal.institution_id)
        if row is None:
            raise ApiError.not_found("Instrument not found")
        return instrument_detail(row, repo.list_cycle_events(connection, instrument_id))

    def _family_and_cycle(self, connection, payload: dict[str, Any]):
        message = "Choose a valid instrument family and cycle status"
        family_id = _uuid(payload.get("family_id"), field="family_id", reason="invalid_family_or_cycle", message=message)
        cycle_code = _text(payload, "cycle_status_code", required=True, max_length=32, reason="cycle_status_required")
        if not repo.existing_family_ids(connection, {family_id}):
            raise ApiError.invalid_reference(message, details={"field": "family_id", "reason": "invalid_family_or_cycle"})
        cycle = repo.get_cycle_status_by_code(connection, cycle_code)
        if cycle is None:
            raise ApiError.invalid_reference(message, details={"field": "cycle_status_code",
                                                               "reason": "invalid_family_or_cycle"})
        return family_id, cycle

    def create_instrument(self, principal, payload: dict[str, Any]) -> dict[str, Any]:
        _check_fields(payload, {"internal_code", "family_id", "cycle_status_code", "active"},
                      ("internal_code", "family_id", "cycle_status_code"))
        internal_code = _text(payload, "internal_code", required=True, max_length=64)
        active = _bool(payload, "active")
        instrument_id = uuid.uuid4()
        now = _utc_now()
        try:
            with self.database.begin() as connection:
                family_id, cycle = self._family_and_cycle(connection, payload)
                repo.insert_instrument(connection, {
                    "id": instrument_id, "internal_code": internal_code, "family_id": family_id,
                    "cycle_status_id": cycle["id"], "institution_id": principal.institution_id, "active": active,
                    "created_at": now, "updated_at": now})
                repo.insert_cycle_event(connection, instrument_id=instrument_id, cycle_status_id=cycle["id"],
                                        occurred_at=now, notes="registered")
                audit.record(connection, action="CREATE_INSTRUMENT", resource_type="instrument",
                             resource_id=instrument_id, principal=principal)
                result = self._instrument(connection, principal, instrument_id)
        except IntegrityError as exc:
            if INSTRUMENT_CODE_UNIQUE_INDEX in str(exc.orig):
                raise ApiError.conflict("An instrument with this internal code already exists for the institution",
                                        details={"field": "internal_code", "reason": "instrument_code_exists"}) from None
            raise
        logger.info("instrument created id=%s", instrument_id)
        return result

    def update_instrument(self, principal, raw_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        instrument_id = _not_found_id(raw_id, "Instrument not found")
        _check_fields(payload, {"family_id", "cycle_status_code", "active", "change_reason"},
                      ("family_id", "cycle_status_code"))
        active = _bool(payload, "active")
        reason = _text(payload, "change_reason", required=False, max_length=500)
        now = _utc_now()
        with self.database.begin() as connection:
            current = repo.get_instrument_detail(connection, instrument_id, principal.institution_id, for_update=True)
            if current is None:
                raise ApiError.not_found("Instrument not found")
            family_id, cycle = self._family_and_cycle(connection, payload)
            cycle_changed = current["cycle_status_id"] != cycle["id"]
            active_changed = bool(current["active"]) != active
            family_changed = current["family_id"] != family_id
            if (cycle_changed or active_changed) and not reason:
                raise ApiError.validation_error(
                    "A change reason is required when cycle status or active status changes",
                    details={"field": "change_reason", "reason": "change_reason_required"})
            repo.update_instrument(connection, instrument_id, {
                "family_id": family_id, "cycle_status_id": cycle["id"], "active": active, "updated_at": now})
            if cycle_changed or active_changed:
                notes = []
                if cycle_changed:
                    notes.append(f"cycle: {current['cycle_status_code']}→{cycle['code']}")
                if active_changed:
                    notes.append(f"active: {'active' if current['active'] else 'inactive'}→"
                                 f"{'active' if active else 'inactive'}")
                notes.append(f"reason: {reason}")
                repo.insert_cycle_event(connection, instrument_id=instrument_id, cycle_status_id=cycle["id"],
                                        occurred_at=now, notes="; ".join(notes))
            audit.record_update(connection, entity="INSTRUMENT", resource_type="instrument", resource_id=instrument_id,
                                principal=principal, was_active=bool(current["active"]), is_active=active,
                                changed=family_changed or cycle_changed)
            result = self._instrument(connection, principal, instrument_id)
        logger.info("instrument updated id=%s", instrument_id)
        return result

    # --- Kits ---------------------------------------------------------------------------------

    def list_kits_detail(self, principal) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = repo.kit_rows(connection, principal.institution_id)
            if not sees_inactive_kits(principal):
                rows = [row for row in rows if row["active"]]
            ids = [row["id"] for row in rows]
            totals = repo.kit_item_totals(connection, ids)
            procedures = repo.procedures_by_kit(connection, ids)
            return [kit_summary(row, totals.get(row["id"], (0, 0)), procedures.get(row["id"], [])) for row in rows]

    def get_kit_detail(self, principal, raw_id: str) -> dict[str, Any]:
        kit_id = _not_found_id(raw_id, "Kit not found")
        with self.database.connect() as connection:
            kit = self._kit(connection, principal, kit_id)
        if not kit["active"] and not sees_inactive_kits(principal):
            raise ApiError.not_found("Kit not found")
        return kit

    def _kit(self, connection, principal, kit_id: uuid.UUID) -> dict[str, Any]:
        row = repo.get_kit_row(connection, kit_id, principal.institution_id)
        if row is None:
            raise ApiError.not_found("Kit not found")
        items = repo.kit_items_detail(connection, kit_id)
        counts = repo.available_counts(connection, principal.institution_id)
        detail = kit_summary(row, (len(items), sum(int(i["quantity"]) for i in items)),
                             repo.procedures_by_kit(connection, [kit_id]).get(kit_id, []))
        detail["items"] = [{
            "family_id": str(item["family_id"]), "family_code": item["family_code"], "quantity": int(item["quantity"]),
            "family_name": item["family_name"], "family_active": bool(item["family_active"]),
            "category_code": item["category_code"], "category_name": item["category_name"],
            "available_count": counts.get(item["family_id"], 0),
        } for item in items]
        return detail

    @staticmethod
    def _kit_values(payload: dict[str, Any]) -> tuple[str, bool, dict[uuid.UUID, int]]:
        _check_fields(payload, {"name", "active", "items"}, ("name", "items"))
        name = _text(payload, "name", required=True, max_length=160)
        active = _bool(payload, "active")
        raw_items = payload["items"]
        if not isinstance(raw_items, list):
            raise ApiError.invalid_request("items must be a list", details={"field": "items"})
        composition: dict[uuid.UUID, int] = {}
        for raw in raw_items:
            if not isinstance(raw, dict) or set(raw) - {"family_id", "quantity"}:
                raise ApiError.invalid_request("Each item needs family_id and quantity only", details={"field": "items"})
            family_id = _uuid(raw.get("family_id"), field="items", reason="invalid_composition",
                              message="Choose a valid instrument family and quantity for every row")
            quantity = raw.get("quantity")
            if isinstance(quantity, str) and quantity.strip().lstrip("-").isdigit():
                quantity = int(quantity.strip())
            if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= MAX_QUANTITY:
                raise ApiError.validation_error("Expected quantity must be between 1 and 32767",
                                                details={"field": "items", "reason": "invalid_quantity"})
            if family_id in composition:
                raise ApiError.validation_error("An instrument family can only appear once in the composition",
                                                details={"field": "items", "reason": "duplicate_family"})
            composition[family_id] = quantity
        if not composition:
            raise ApiError.validation_error("Add at least one instrument family to the kit composition",
                                            details={"field": "items", "reason": "empty_composition"})
        return name, active, composition

    def _check_families(self, connection, composition) -> None:
        if repo.existing_family_ids(connection, set(composition)) != set(composition):
            raise ApiError.invalid_reference("Choose instrument families from the available catalog",
                                             details={"field": "items", "reason": "invalid_family"})

    def create_kit(self, principal, payload: dict[str, Any]) -> dict[str, Any]:
        name, active, composition = self._kit_values(payload)
        kit_id, now = uuid.uuid4(), _utc_now()
        try:
            with self.database.begin() as connection:
                self._check_families(connection, composition)
                repo.insert_kit(connection, {
                    "id": kit_id, "name": name, "version": repo.next_kit_version(connection, principal.institution_id, name),
                    "active": active, "institution_id": principal.institution_id, "created_at": now, "updated_at": now,
                }, composition)
                audit.record(connection, action="CREATE_KIT", resource_type="kit", resource_id=kit_id, principal=principal)
                result = self._kit(connection, principal, kit_id)
        except IntegrityError as exc:
            if KIT_UNIQUE_CONSTRAINT in str(exc.orig):
                raise ApiError.conflict("A kit with this name and version already exists for the institution",
                                        details={"field": "name", "reason": "kit_exists"}) from None
            raise
        logger.info("kit created id=%s version=%s", kit_id, result["version"])
        return result

    def update_kit(self, principal, raw_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """A changed kit becomes a NEW version (the old one is kept, inactive, for history).

        Returns (kit, created): created is False when nothing changed (no new version)."""
        kit_id = _not_found_id(raw_id, "Kit not found")
        name, active, composition = self._kit_values(payload)
        now = _utc_now()
        try:
            with self.database.begin() as connection:
                current = repo.get_kit_row(connection, kit_id, principal.institution_id, for_update=True)
                if current is None or (not current["active"] and not sees_inactive_kits(principal)):
                    raise ApiError.not_found("Kit not found")
                self._check_families(connection, composition)
                if (name == current["name"] and composition == repo.kit_composition(connection, kit_id)
                        and active == bool(current["active"])):
                    return self._kit(connection, principal, kit_id), False
                new_id = uuid.uuid4()
                repo.insert_kit(connection, {
                    "id": new_id, "name": name, "version": repo.next_kit_version(connection, principal.institution_id, name),
                    "active": active, "institution_id": principal.institution_id, "created_at": now, "updated_at": now,
                }, composition)
                moved = repo.supersede_kit(connection, kit_id, new_id, now)
                audit.record(connection, action="UPDATE_KIT", resource_type="kit", resource_id=new_id, principal=principal)
                if current["active"]:
                    audit.record(connection, action="SUPERSEDE_KIT", resource_type="kit", resource_id=kit_id,
                                 principal=principal)
                result = self._kit(connection, principal, new_id)
        except IntegrityError as exc:
            if KIT_UNIQUE_CONSTRAINT in str(exc.orig):
                raise ApiError.conflict("A kit with this name and version already exists for the institution",
                                        details={"field": "name", "reason": "kit_exists"}) from None
            raise
        result["superseded_kit_id"] = str(kit_id)
        logger.info("kit %s superseded by %s (version %s, %s procedure links moved)", kit_id, new_id,
                    result["version"], moved)
        return result, True

