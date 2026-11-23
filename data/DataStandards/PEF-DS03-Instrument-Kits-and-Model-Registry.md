---
id: PEF-DS03
title: Instrument Kits and Model Registry
version: "0.2.0"
status: draft
owner: PEF Core / Vision
applies_to: [WP, BE, Worker, MS]
stores: [PostgreSQL]
related: [PEF-DS02, PEF-DS04, PEF-DS05]
last_updated: 2026-09-06
---

# PEF-DS03 · Instrument Kits and Model Registry

## INTRODUCTION

This standard defines **what is counted** and **how the vision model names its classes**.

Owned tables: `instrument_family`, `family_example`, `instrument`, `instrument_usage`, `instrument_cycle_event`, `kit`, `kit_item`, `procedure_kit`, `procedure_phase`, `instrument_reservation`, `yolo_model`, `model_class`.

Owned catalogs: `cat_instrument_category`, `cat_usage_context`, `cat_instrument_cycle_status`, `cat_operation_phase`.

`cat_procedure_type` is defined in PEF-DS02; it is only referenced here.

It does not define counting sessions/events (PEF-DS04). It does not define `.pt`/photo bytes (only the `media_asset_id` pointer; the asset itself is PEF-DS05).

Below: **all** fields, FKs with rationales, and one **complete example row** per table.

---

## 1. Standard Explanation

### 1.1 Definitions


| Term | Meaning |
| --- | --- |
| **Family** | Catalog-level instrument type (what the model typically detects as a class). |
| **Piece** | Physical unit in an institution's stock. |
| **Kit** | Versioned recipe: quantities per family (does not list individual piece UUIDs). |
| **Procedure kit** | Procedure ↔ kit link (with optional default). |
| **Procedure phase** | Ordered clinical phases and whether counting is required. |
| **Reservation** | Physical piece assigned to an operation while `active`. |
| **YOLO model** | Detector version; at most one row has `active = true`. |
| **Model class** | Integer `yolo_class_id` ↔ `family_id` mapping. |


### 1.2 Usage

Admin creates **families** (with examples) and publishes **models**. SPD registers **pieces** and **kits**. Worker loads weights and translates class → family. Opening a session (DS04) typically copies the kit into `expected_inventory`.

**Stock concept:** stock consists of `instrument` rows. The kit speaks in families. Specific pieces can be reserved for an operation.

---

### 1.3 Conventions

Under "References", the **Relationship** column means: `N:1` = many rows in this table to one in the target; `N:0..1` = optional target (nullable FK); `1:1` = at most one pair (e.g., UNIQUE); `N:N` is built via a junction table (each junction FK remains `N:1`).

---

#### 1.3.1 `cat_instrument_category`


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(64) | NO | — | E.g. `suturing`, `cutting`. |
| `name` | VARCHAR(120) | NO | — | Label. |


FKs: none. UNIQUE `code`.

```json
{
  "id": "b1000001-0000-4000-8000-000000000001",
  "code": "suturing",
  "name": "Suturing and closure"
}
```

---

#### 1.3.2 `cat_usage_context`


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(64) | NO | — | Documented usage context. |
| `name` | VARCHAR(200) | NO | — | Label. |


FKs: none. UNIQUE `code`.

```json
{
  "id": "b1000002-0000-4000-8000-000000000002",
  "code": "primary_tray",
  "name": "Primary tray"
}
```

---

#### 1.3.3 `cat_instrument_cycle_status`


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(32) | NO | — | E.g. `clean`, `in_use`, `dirty`, `sterilizing`. |
| `name` | VARCHAR(80) | NO | — | Label. |


FKs: none. UNIQUE `code`.

```json
{
  "id": "b1000003-0000-4000-8000-000000000003",
  "code": "clean",
  "name": "Clean / ready"
}
```

---

#### 1.3.4 `cat_operation_phase`

**Canonical owner here** (also used by `work_session` in DS04).


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(32) | NO | — | E.g. `pre_count`, `intra`, `closing`. |
| `name` | VARCHAR(80) | NO | — | Label. |
| `active` | BOOLEAN | NO | `TRUE` | Phase usable in new procedures. |

FKs: none. UNIQUE `code`.

```json
{
  "id": "b1000004-0000-4000-8000-000000000004",
  "code": "pre_count",
  "name": "Pre-count",
  "active": true
}
```

---

#### 1.3.5 Table `instrument_family`

**Purpose:** instrument type (detection and kit vocabulary).


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(64) | NO | — | Globally unique family code. |
| `name` | VARCHAR(160) | NO | — | Human-readable name. |
| `category_id` | UUID | NO | — | Category. |
| `identify_text` | TEXT | YES | NULL | Identification text (training/UI). |
| `classify_text` | TEXT | YES | NULL | Classification text. |
| `function_text` | TEXT | YES | NULL | Clinical function of this type. |
| `active` | BOOLEAN | NO | `TRUE` | Usable family. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `category_id` | `cat_instrument_category.id` | N:1 | Groups families. | `RESTRICT` |


UNIQUE `code`.

```json
{
  "id": "f1000001-0000-4000-8000-000000000001",
  "code": "mayo_hegar_needle_holder",
  "name": "Mayo-Hegar Needle Holder",
  "category_id": "b1000001-0000-4000-8000-000000000001",
  "identify_text": "Ratcheted needle holder with ring handles and blunt tip.",
  "classify_text": "Mayo-Hegar needle holder vs. Olsen-Hegar.",
  "function_text": "Hold the suture needle during suturing.",
  "active": true
}
```

---

#### 1.3.6 Table `family_example`

**Purpose:** examples (text and/or image) linked to a family, used by this system in lab/training contexts.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `family_id` | UUID | NO | — | Family. |
| `media_asset_id` | UUID | YES | NULL | Example image (bytes in DS05). |
| `example_text` | TEXT | YES | NULL | Note or caption. |
| `sort_order` | SMALLINT | NO | `0` | Display order. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `family_id` | `instrument_family.id` | N:1 | Example owner. | `CASCADE` |
| `media_asset_id` | `media_asset.id` (DS05) | N:0..1 | Reference photo; no bytes stored here. | `SET NULL` |


```json
{
  "id": "f1000002-0000-4000-8000-000000000002",
  "family_id": "f1000001-0000-4000-8000-000000000001",
  "media_asset_id": "m1000001-0000-4000-8000-000000000001",
  "example_text": "Clean overhead view, handle pointing downward.",
  "sort_order": 1
}
```

---

#### 1.3.7 Table `instrument`

**Purpose:** physical piece = unit of stock.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `internal_code` | VARCHAR(64) | YES | NULL | Institution internal code (tag/inventory). |
| `family_id` | UUID | NO | — | Instrument type it belongs to. |
| `cycle_status_id` | UUID | NO | — | **Current** cycle status. |
| `institution_id` | UUID | NO | — | Stock owner. |
| `active` | BOOLEAN | NO | `TRUE` | Piece in usable inventory. |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Created at. |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Last updated at. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `family_id` | `instrument_family.id` | N:1 | Classifies the piece. | `RESTRICT` |
| `cycle_status_id` | `cat_instrument_cycle_status.id` | N:1 | Denormalized current state for fast lookup. | `RESTRICT` |
| `institution_id` | `institution.id` (DS01) | N:1 | Stock per tenant. | `RESTRICT` |


Partial UNIQUE (`institution_id`, `internal_code`) WHERE `internal_code IS NOT NULL`.

```json
{
  "id": "i1000001-0000-4000-8000-000000000001",
  "internal_code": "NH-MH-014",
  "family_id": "f1000001-0000-4000-8000-000000000001",
  "cycle_status_id": "b1000003-0000-4000-8000-000000000003",
  "institution_id": "11111111-1111-1111-1111-111111111111",
  "active": true,
  "created_at": "2026-02-01T09:00:00Z",
  "updated_at": "2026-09-05T08:00:00Z"
}
```

---

#### 1.3.8 Table `instrument_usage`

**Purpose:** documents in which procedure/context a piece is used (operational catalog, not live counting).


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `instrument_id` | UUID | NO | — | Piece. |
| `procedure_type_id` | UUID | NO | — | Procedure type (DS02). |
| `context_id` | UUID | NO | — | Usage context. |
| `notes` | TEXT | YES | NULL | Notes. |
| `active` | BOOLEAN | NO | `TRUE` | Active usage. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `instrument_id` | `instrument.id` | N:1 | Documented piece. | `CASCADE` |
| `procedure_type_id` | `cat_procedure_type.id` | N:1 | Procedure. | `RESTRICT` |
| `context_id` | `cat_usage_context.id` | N:1 | Context. | `RESTRICT` |


UNIQUE (`instrument_id`, `procedure_type_id`, `context_id`).

```json
{
  "id": "i1000002-0000-4000-8000-000000000002",
  "instrument_id": "i1000001-0000-4000-8000-000000000001",
  "procedure_type_id": "a1000003-0000-4000-8000-000000000003",
  "context_id": "b1000002-0000-4000-8000-000000000002",
  "notes": "Preferred in primary tray for inguinal hernia.",
  "active": true
}
```

---

#### 1.3.9 Table `instrument_cycle_event`

**Purpose:** history of cycle status transitions (temporal facts). The current state remains on `instrument.cycle_status_id`.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `instrument_id` | UUID | NO | — | Piece. |
| `cycle_status_id` | UUID | NO | — | Status **at that moment**. |
| `occurred_at` | TIMESTAMPTZ | NO | `now()` | When the transition occurred. |
| `session_id` | UUID | YES | NULL | Related counting session (if applicable). |
| `operation_id` | UUID | YES | NULL | Related operation (if applicable). |
| `notes` | TEXT | YES | NULL | Free-form note. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `instrument_id` | `instrument.id` | N:1 | Piece. | `CASCADE` |
| `cycle_status_id` | `cat_instrument_cycle_status.id` | N:1 | Recorded status. | `RESTRICT` |
| `operation_id` | `operation.id` (DS02) | N:0..1 | Optional clinical context. | `SET NULL` |
| `session_id` | `work_session.id` (DS04) | N:0..1 | Optional counting context; FK added at the end of the DDL. | `SET NULL` (per schema ALTER) |


```json
{
  "id": "i1000003-0000-4000-8000-000000000003",
  "instrument_id": "i1000001-0000-4000-8000-000000000001",
  "cycle_status_id": "b1000003-0000-4000-8000-000000000003",
  "occurred_at": "2026-09-05T08:00:00Z",
  "session_id": null,
  "operation_id": null,
  "notes": "Released from sterilization; marked clean."
}
```

---

#### 1.3.10 Table `kit`

**Purpose:** versioned recipe per institution.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `name` | VARCHAR(160) | NO | — | Kit name. |
| `version` | SMALLINT | NO | `1` | Version (> 0). |
| `active` | BOOLEAN | NO | `TRUE` | Usable kit. |
| `institution_id` | UUID | NO | — | Tenant. |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Created at. |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Last edited at. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `institution_id` | `institution.id` | N:1 | Kits per hospital. | `CASCADE` |


UNIQUE (`institution_id`, `name`, `version`). CHECK `version > 0`.

```json
{
  "id": "k1000001-0000-4000-8000-000000000001",
  "name": "Open inguinal hernia kit",
  "version": 1,
  "active": true,
  "institution_id": "11111111-1111-1111-1111-111111111111",
  "created_at": "2026-04-01T10:00:00Z",
  "updated_at": "2026-04-01T10:00:00Z"
}
```

---

#### 1.3.11 Table `kit_item`

**Purpose:** kit line item = family + quantity.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `kit_id` | UUID | NO | — | Kit. |
| `family_id` | UUID | NO | — | Requested family. |
| `quantity` | SMALLINT | NO | — | Expected quantity (> 0). |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `kit_id` | `kit.id` | N:1 | Line item owner. | `CASCADE` |
| `family_id` | `instrument_family.id` | N:1 | Required instrument type. | `RESTRICT` |


UNIQUE (`kit_id`, `family_id`). CHECK `quantity > 0`.

```json
{
  "id": "k1000002-0000-4000-8000-000000000002",
  "kit_id": "k1000001-0000-4000-8000-000000000001",
  "family_id": "f1000001-0000-4000-8000-000000000001",
  "quantity": 2
}
```

---

#### 1.3.12 Table `procedure_kit`

**Purpose:** defines which kits apply to a procedure type.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `procedure_type_id` | UUID | NO | — | Procedure (DS02). |
| `kit_id` | UUID | NO | — | Kit. |
| `technique_label` | VARCHAR(160) | YES | NULL | Technique/variant label. |
| `is_default` | BOOLEAN | NO | `FALSE` | Whether this is the default kit for that procedure. |
| `active` | BOOLEAN | NO | `TRUE` | Active relationship. |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Created at. |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Updated at. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `procedure_type_id` | `cat_procedure_type.id` | N:1 (procedure↔kit is N:N via this table) | Procedure. | `CASCADE` |
| `kit_id` | `kit.id` | N:1 (procedure↔kit is N:N via this table) | Recipe. | `RESTRICT` |


UNIQUE (`procedure_type_id`, `kit_id`).  
Partial UNIQUE: at most one active default per `procedure_type_id`.

```json
{
  "id": "k1000003-0000-4000-8000-000000000003",
  "procedure_type_id": "a1000003-0000-4000-8000-000000000003",
  "kit_id": "k1000001-0000-4000-8000-000000000001",
  "technique_label": "Standard open technique",
  "is_default": true,
  "active": true,
  "created_at": "2026-04-01T10:05:00Z",
  "updated_at": "2026-04-01T10:05:00Z"
}
```

---

#### 1.3.13 Table `procedure_phase`

**Purpose:** ordered phases for a procedure and whether each requires counting.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `procedure_type_id` | UUID | NO | — | Procedure. |
| `phase_id` | UUID | NO | — | Catalog phase. |
| `sort_order` | SMALLINT | NO | — | Order (> 0). |
| `is_count_required` | BOOLEAN | NO | `TRUE` | Whether counting is required in this phase. |
| `active` | BOOLEAN | NO | `TRUE` | Active. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `procedure_type_id` | `cat_procedure_type.id` | N:1 | Workflow owner. | `CASCADE` |
| `phase_id` | `cat_operation_phase.id` | N:1 | Clinical phase. | `RESTRICT` |


UNIQUE (`procedure_type_id`, `phase_id`); UNIQUE (`procedure_type_id`, `sort_order`).

```json
{
  "id": "k1000004-0000-4000-8000-000000000004",
  "procedure_type_id": "a1000003-0000-4000-8000-000000000003",
  "phase_id": "b1000004-0000-4000-8000-000000000004",
  "sort_order": 1,
  "is_count_required": true,
  "active": true
}
```

---

#### 1.3.14 Table `instrument_reservation`

**Purpose:** physical piece reserved for an operation (at most one active reservation per piece).


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `operation_id` | UUID | NO | — | Operation. |
| `instrument_id` | UUID | NO | — | Piece. |
| `reserved_at` | TIMESTAMPTZ | NO | `now()` | Reservation start. |
| `released_at` | TIMESTAMPTZ | YES | NULL | Release time. |
| `active` | BOOLEAN | NO | `TRUE` | Active reservation. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `operation_id` | `operation.id` (DS02) | N:1 | Case holding the piece. | `CASCADE` |
| `instrument_id` | `instrument.id` | N:1 (with active partial UNIQUE: at most 1 active reservation per piece ≈ active 0..1:1) | Held piece. | `RESTRICT` |


CHECK: `released_at` ≥ `reserved_at` when both are present.  
Partial UNIQUE: `instrument_id` WHERE `active = TRUE`.

```json
{
  "id": "r1000001-0000-4000-8000-000000000001",
  "operation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "instrument_id": "i1000001-0000-4000-8000-000000000001",
  "reserved_at": "2026-09-05T15:50:00Z",
  "released_at": null,
  "active": true
}
```

---

#### 1.3.15 Table `yolo_model`

**Purpose:** detector version registry.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `version_tag` | VARCHAR(32) | NO | — | Unique version tag. |
| `media_asset_id` | UUID | YES | NULL | Weights file (DS05). |
| `checksum` | CHAR(64) | YES | NULL | Artifact hash (e.g., SHA-256). |
| `architecture` | VARCHAR(64) | YES | NULL | Detector family extracted on Admin upload (e.g., `yolo26`). |
| `model_size` | VARCHAR(8) | YES | NULL | Weight size (e.g., `n`, `s`, `m`, `l`, `x`). |
| `active` | BOOLEAN | NO | `FALSE` | Whether this is the production model **for new Starts**. |
| `published_at` | TIMESTAMPTZ | NO | `now()` | Publication timestamp. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `media_asset_id` | `media_asset.id` (DS05) | N:0..1 | `.pt`/weights bytes. | `SET NULL` |


UNIQUE `version_tag`. Partial UNIQUE: at most one `active = TRUE`.

**Session pin:** `work_session.yolo_model_id` (PEF-DS04). The worker for an already-open session reads that FK, not this global `active` flag.

```json
{
  "id": "y1000001-0000-4000-8000-000000000001",
  "version_tag": "yolo26n-pef-2026-03",
  "media_asset_id": "m1000002-0000-4000-8000-000000000002",
  "checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "architecture": "yolo26",
  "model_size": "n",
  "active": true,
  "published_at": "2026-03-20T18:00:00Z"
}
```

---

#### 1.3.16 Table `model_class`

**Purpose:** maps YOLO class → family for a specific model version.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `model_id` | UUID | NO | — | Model. |
| `family_id` | UUID | NO | — | Detected family. |
| `yolo_class_id` | SMALLINT | NO | — | Model class integer (≥ 0). |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `model_id` | `yolo_model.id` | N:1 | Detector version. | `CASCADE` |
| `family_id` | `instrument_family.id` | N:1 | Instrument type. | `RESTRICT` |


UNIQUE (`model_id`, `yolo_class_id`); UNIQUE (`model_id`, `family_id`).

```json
{
  "id": "y1000002-0000-4000-8000-000000000002",
  "model_id": "y1000001-0000-4000-8000-000000000001",
  "family_id": "f1000001-0000-4000-8000-000000000001",
  "yolo_class_id": 0
}
```

---

### 1.4 Governance and Ownership


| Aspect | Owner |
| --- | --- |
| Families and kits | PEF Core / instrument standardization |
| Model publishing | Vision / MLOps |
| Reservations | Operating room workflow (portal/station) |


Changing the classes of a model already in use = publish **another** version, never rewrite history.

---

### 1.5 Standard Rationale

**Why this block exists**

Three concepts that are often conflated must stay separate: (1) instrument type, (2) physical piece with lifecycle state, and (3) integer class ID emitted by YOLO. Without this separation, it is impossible to audit whether "a type was missing" versus "piece NH-MH-014 was missing," or which model produced the count.

**Design decisions taken**

1. **Stock =** `instrument` **rows**, not a `family_stock` table. Expected counts come from the kit (families); real inventory consists of pieces.
2. **Kit = family-level recipe**, not a list of piece UUIDs. Reusable and versionable; the session freezes expected quantities in DS04.
3. **Current cycle status on the piece + history in** `instrument_cycle_event`**.** Fast queries without losing audit trail.
4. **Single active** `yolo_model` + per-version `model_class`. Prevents ambiguity in the worker.
5. **Single active reservation per piece.** Prevents double-allocation.
6. **Weights and photos referenced only via** `media_asset_id`**.** This DS does not manage buckets.

**Rejected alternatives**


| Alternative | Why not |
| --- | --- |
| Track stock only as an integer per family | Loses serialization, lifecycle history, and per-piece reservations. |
| Kit listing specific physical pieces | Every surgery would need a different kit; no stable template. |
| Hardcode YOLO classes in the worker | Changing the model breaks production with no audit trail. |
| Multiple `active` models simultaneously | The station would not know which one to use without an extra routing layer. |
| Store bounding boxes here | That belongs to vision telemetry (MongoDB / DS06), not catalog data. |
| `family_stock` + `instrument` | Duplicates the source of truth; totals are computed by counting pieces. |


---

## 2. Annex A – General Information


| Field | Value |
| --- | --- |
| Code | PEF-DS03 |
| Title | Instrument Kits and Model Registry |
| Version | 0.2.0 |
| Status | draft |
| Store | PostgreSQL (§5.6 + instrument/phase catalogs) |
| Out of scope | `expected_inventory`, `count_event`, object storage bytes |
| Reference DDL | `esquema_base_datos_v2.md` |
