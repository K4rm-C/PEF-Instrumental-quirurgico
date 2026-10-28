"""Catalog use cases and validation rules (PEF-DS03 fields, PEF-DS09 contracts).

Input is the logical representation produced by serializers (same for JSON and XML);
output is plain dicts with JSON-compatible values. Tenant scoping always uses the
institution of the authenticated principal, never a value supplied by the client.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError

from adapters import audit_adapter as audit
from database import Database
from errors import ApiError
from repositories import catalog_repository as repo

logger = logging.getLogger("catalog.service")

FAMILY_REQUIRED_FIELDS = ("code", "name", "category_code")
FAMILY_OPTIONAL_TEXT_FIELDS = ("identify_text", "classify_text", "function_text")
FAMILY_ALLOWED_FIELDS = frozenset(FAMILY_REQUIRED_FIELDS + FAMILY_OPTIONAL_TEXT_FIELDS + ("active",))
# Column lengths from PEF-DS03 / 001_init.sql.
FAMILY_MAX_LENGTHS = {"code": 64, "name": 160, "category_code": 64}
FAMILY_CODE_UNIQUE_CONSTRAINT = "uk_instrument_family_code"


def _family_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "code": row["code"],
        "name": row["name"],
        "category_code": row["category_code"],
        "active": bool(row["active"]),
    }


def _instrument_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "internal_code": row["internal_code"],
        "family_id": str(row["family_id"]),
        "family_code": row["family_code"],
        "cycle_status_code": row["cycle_status_code"],
        "institution_id": str(row["institution_id"]),
        "active": bool(row["active"]),
    }


def _kit_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "version": int(row["version"]),
        "active": bool(row["active"]),
        "institution_id": str(row["institution_id"]),
    }


def validate_family_create(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a POST /families body. 400 = malformed/typed wrong, 422 = invalid values."""
    unknown = sorted(set(payload) - FAMILY_ALLOWED_FIELDS)
    if unknown:
        raise ApiError.invalid_request("Unknown fields in request body", details={"unknown_fields": unknown})

    missing = [name for name in FAMILY_REQUIRED_FIELDS if payload.get(name) is None]
    if missing:
        raise ApiError.invalid_request("Required fields are missing", details={"missing_fields": missing})

    values: dict[str, Any] = {}
    for name in FAMILY_REQUIRED_FIELDS:
        raw = payload[name]
        if not isinstance(raw, str):
            raise ApiError.invalid_request("Field must be a string", details={"field": name})
        value = raw.strip()
        if not value:
            raise ApiError.validation_error("Field must not be empty", details={"field": name})
        if len(value) > FAMILY_MAX_LENGTHS[name]:
            raise ApiError.validation_error(
                "Field is too long",
                details={"field": name, "max_length": FAMILY_MAX_LENGTHS[name]},
            )
        values[name] = value

    for name in FAMILY_OPTIONAL_TEXT_FIELDS:
        raw = payload.get(name)
        if raw is not None and not isinstance(raw, str):
            raise ApiError.invalid_request("Field must be a string or null", details={"field": name})
        # Blank optional text is stored as NULL, same as omitting it.
        values[name] = (raw.strip() or None) if isinstance(raw, str) else None

    if "active" in payload:
        if not isinstance(payload["active"], bool):
            raise ApiError.invalid_request("Field must be a boolean", details={"field": "active"})
        values["active"] = payload["active"]
    else:
        values["active"] = True

    return values


class CatalogService:
    def __init__(self, database: Database):
        self.database = database

    # --- Families -------------------------------------------------------------------

    def list_families(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            return [_family_item(row) for row in repo.list_families(connection)]

    def create_family(self, payload: dict[str, Any], principal=None) -> dict[str, Any]:
        values = validate_family_create(payload)
        try:
            with self.database.begin() as connection:
                category_id = repo.get_category_id(connection, values["category_code"])
                if category_id is None:
                    raise ApiError.invalid_reference(
                        "category_code does not exist",
                        details={"field": "category_code", "value": values["category_code"]},
                    )
                if repo.family_code_exists(connection, values["code"]):
                    raise ApiError.conflict(
                        "An instrument family with this code already exists",
                        details={"field": "code", "value": values["code"]},
                    )
                family_id = repo.insert_family(
                    connection,
                    {
                        "id": uuid.uuid4(),
                        "code": values["code"],
                        "name": values["name"],
                        "category_id": category_id,
                        "identify_text": values["identify_text"],
                        "classify_text": values["classify_text"],
                        "function_text": values["function_text"],
                        "active": values["active"],
                    },
                )
                created = repo.get_family(connection, family_id)
                if principal is not None:
                    # Same transaction as the INSERT (access_audit, DS07).
                    audit.record(connection, action="CREATE_INSTRUMENT_FAMILY", resource_type="instrument_family",
                                 resource_id=family_id, principal=principal)
        except IntegrityError as exc:
            # Concurrent insert of the same code between the check and the INSERT.
            if FAMILY_CODE_UNIQUE_CONSTRAINT in str(exc.orig):
                raise ApiError.conflict(
                    "An instrument family with this code already exists",
                    details={"field": "code", "value": values["code"]},
                ) from None
            raise
        logger.info("instrument family created id=%s code=%s", family_id, values["code"])
        return _family_item(created)

    # --- Instruments ----------------------------------------------------------------

    def list_instruments(self, institution_id: uuid.UUID) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            return [_instrument_item(row) for row in repo.list_instruments(connection, institution_id)]

    # --- Kits -----------------------------------------------------------------------

    def list_kits(self, institution_id: uuid.UUID) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            return [_kit_item(row) for row in repo.list_kits(connection, institution_id)]

    def get_kit(self, raw_kit_id: str, institution_id: uuid.UUID) -> dict[str, Any]:
        try:
            kit_id = uuid.UUID(raw_kit_id)
        except (TypeError, ValueError):
            # A malformed id can never exist; same answer as an unknown id (documented in DS09).
            raise ApiError.not_found("Kit not found") from None
        with self.database.connect() as connection:
            kit = repo.get_kit(connection, kit_id, institution_id)
            if kit is None:
                raise ApiError.not_found("Kit not found")
            items = repo.list_kit_items(connection, kit_id)
        detail = _kit_item(kit)
        detail["items"] = [
            {
                "family_id": str(item["family_id"]),
                "family_code": item["family_code"],
                "quantity": int(item["quantity"]),
            }
            for item in items
        ]
        return detail
