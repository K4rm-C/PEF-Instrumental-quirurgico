---
id: PEF-DS04
title: Work Session and Count Facts
version: "0.2.1"
status: draft
owner: PEF Core / Station / Worker
applies_to: [CS, BE, Worker, WP]
stores: [PostgreSQL]
related: [PEF-DS01, PEF-DS02, PEF-DS03, PEF-DS06, PEF-DS07]
last_updated: 2026-11-19
---

# PEF-DS04 · Work Session and Count Facts

## INTRODUCTION

This standard defines **the act of counting on a tray** and what is persisted as an auditable fact in PostgreSQL.

Owned tables: `work_session`, `expected_inventory`, `count_event`, `discrepancy`, `human_correction`.

Owned catalogs: `cat_session_status`, `cat_event_type`, `cat_discrepancy_reason`.

`cat_operation_phase` is defined in PEF-DS03; here the session only stores `current_phase_id`.

It does not define MongoDB checkpoints (PEF-DS06), JPEG/MP4 bytes (PEF-DS05), or the session privacy agreement (PEF-DS07) — although a session may exist prior to that agreement.

Below: **all** fields, FKs with rationales, and one **complete example row** per table.

---

## 1. Standard Explanation

### 1.1 Definitions

| Term | Meaning |
| :--- | :--- |
| **Work session** | Period during which a user counts or prepares a count, optionally linked to an operation, station, kit, and phase. |
| **Expected inventory** | Per-family quantities **frozen** for that session. |
| **Count event** | Auditable fact (auto/manual/phase change, etc.). |
| **Discrepancy** | Mismatch record; may be resolved later. |
| **Human correction** | Mandatory justification linked to a `count_event` (1:1). |

### 1.2 Usage

The station opens/closes the session and displays expected vs. detected counts. Backend/portal (SessionService) **persists** `count_event`. VisionWorker pushes UI updates over WSS and feeds the Redis evidence ring; it does not own SQL facts. The portal queries history and discrepancies.

| Question | Where |
| :--- | :--- |
| How many items were recorded as detected at 10:32 in session X? | `count_event` (this DS) |
| Boxes, score, fine-grained trace? | MongoDB (DS06) |
| JPEG URI? | `media_asset` (DS05) |

Project fan-out: detection → UI (WSS) **and** fact in PG (+ checkpoint if policy requires it).

---

### 1.3 Conventions

Under "References", the **Relationship** column means: `N:1` = many rows in this table to one in the target; `N:0..1` = optional target; `1:1` = at most one pair (UNIQUE); `N:N` via junction table.

---

#### 1.3.1 `cat_session_status`

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(32) | NO | — | `scheduled`, `in_progress`, `or_hold`, `awaiting_spd_review`, `correction_required`, `closed`, `aborted`. |
| `name` | VARCHAR(80) | NO | — | Label. |

FKs: none. UNIQUE `code`.

```json
{
  "id": "c1000001-0000-4000-8000-000000000001",
  "code": "or_hold",
  "name": "OR hold"
}
```

---

#### 1.3.2 `cat_event_type`

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(64) | NO | — | Includes `auto_count`, `manual_count`, `phase_change`, `or_hold_opened`, `or_hold_comment`, `or_hold_released`. |
| `name` | VARCHAR(160) | NO | — | Label. |

FKs: none. UNIQUE `code`.

```json
{
  "id": "c1000002-0000-4000-8000-000000000002",
  "code": "auto_count",
  "name": "Automatic count"
}
```

---

#### 1.3.3 `cat_discrepancy_reason`

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(64) | NO | — | E.g. `left_in_patient`, `counting_error`, `extra_on_tray`. |
| `name` | VARCHAR(160) | NO | — | Label. |

FKs: none. UNIQUE `code`.

```json
{
  "id": "c1000003-0000-4000-8000-000000000003",
  "code": "counting_error",
  "name": "Counting / detection error"
}
```

---

#### 1.3.4 Table `work_session`

**Purpose:** header record for a counting period.

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | Session PK. |
| `started_at` | TIMESTAMPTZ | NO | `now()` | Start time. |
| `ended_at` | TIMESTAMPTZ | YES | NULL | Close time. |
| `status_id` | UUID | NO | — | Session status. |
| `user_id` | UUID | NO | — | Owning user / who opened it. |
| `closed_by_user_id` | UUID | YES | NULL | Who closed it. |
| `operation_id` | UUID | YES | NULL | Clinical case (product policy may require it). |
| `station_id` | UUID | YES | NULL | Capture station. |
| `kit_id` | UUID | YES | NULL | Reference kit at open time. |
| `current_phase_id` | UUID | YES | NULL | Current clinical phase. |
| `phase_changed_at` | TIMESTAMPTZ | YES | NULL | When the phase last changed. |
| `yolo_model_id` | UUID | YES | NULL | Detector pinned at Start in `vision` mode. NULL in `scheduled` and in `manual_no_privacy`. Worker/UI for an open session read this FK, not the global `yolo_model.active`. |
| `atypical_session` | BOOLEAN | NO | `FALSE` | Session outside standard procedure. |
| `extended_retention` | BOOLEAN | NO | `FALSE` | Extended evidence retention flag. |
| `retention_until` | TIMESTAMPTZ | YES | NULL | Retain evidence until this timestamp (if applicable). |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Last header update. |

**References**

| Field | Points to | Relationship | Rationale | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `status_id` | `cat_session_status.id` | N:1 | Controlled status. | `RESTRICT` |
| `user_id` | `user.id` (DS01) | N:1 | Session owner. | `RESTRICT` |
| `closed_by_user_id` | `user.id` (DS01) | N:0..1 | Closer; may differ from opener. | `SET NULL` |
| `operation_id` | `operation.id` (DS02) | N:0..1 | Clinical context. | `RESTRICT` |
| `station_id` | `capture_station.id` (DS02) | N:0..1 | Where capture occurred. | `SET NULL` |
| `kit_id` | `kit.id` (DS03) | N:0..1 | Reference recipe; expected breakdown is stored in `expected_inventory`. | `SET NULL` |
| `current_phase_id` | `cat_operation_phase.id` (DS03) | N:0..1 | Current phase. | `SET NULL` |
| `yolo_model_id` | `yolo_model.id` (DS03) | N:0..1 | Frozen detector for this session. | `SET NULL` |

CHECK: `ended_at` ≥ `started_at` when both are present.

**Note:** `session_processing_agreement` (0..1) belongs to PEF-DS07. A session may exist without an agreement (e.g., pre-reservation).

```json
{
  "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "started_at": "2026-09-05T16:15:00Z",
  "ended_at": null,
  "status_id": "c1000001-0000-4000-8000-000000000001",
  "user_id": "22222222-2222-2222-2222-222222222222",
  "closed_by_user_id": null,
  "operation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "station_id": "f0f0f0f0-f0f0-f0f0-f0f0-f0f0f0f0f0f0",
  "kit_id": "k1000001-0000-4000-8000-000000000001",
  "current_phase_id": "b1000004-0000-4000-8000-000000000004",
  "phase_changed_at": "2026-09-05T16:15:30Z",
  "yolo_model_id": "y1000001-0000-4000-8000-000000000001",
  "atypical_session": false,
  "extended_retention": false,
  "retention_until": null,
  "updated_at": "2026-09-05T18:20:00Z"
}
```

---

#### 1.3.5 Table `expected_inventory`

**Purpose:** snapshot of "how many should be present" per family in this session.

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `family_id` | UUID | NO | — | Expected family. |
| `expected_quantity` | SMALLINT | NO | — | Expected quantity (≥ 0). |
| `session_id` | UUID | NO | — | Owning session. |
| `source` | VARCHAR(32) | NO | `'kit_snapshot'` | Traceability of how the line was set (see CHECK below). |

**References**

| Field | Points to | Relationship | Rationale | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `family_id` | `instrument_family.id` (DS03) | N:1 | Which type is expected. | `RESTRICT` |
| `session_id` | `work_session.id` | N:1 | Freezes expected counts per session. | `CASCADE` |

UNIQUE (`session_id`, `family_id`). CHECK quantity ≥ 0; CHECK `source` ∈ (`kit_snapshot`, `schedule_additional`, `live_add`, `manual`, `operator_adjustment`).

Editing the kit **later** does not automatically rewrite these snapshots.

```json
{
  "id": "e2000001-0000-4000-8000-000000000001",
  "family_id": "f1000001-0000-4000-8000-000000000001",
  "expected_quantity": 2,
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "source": "kit_snapshot"
}
```

---

#### 1.3.6 Table `count_event`

**Purpose:** auditable counting fact or related event.

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | Fact PK. |
| `event_type_id` | UUID | NO | — | Event type. |
| `client_event_id` | UUID | YES | NULL | Client ID for idempotency (unique when provided). |
| `family_id` | UUID | YES | NULL | Affected family (null if event is not per-family). |
| `expected_quantity` | SMALLINT | YES | NULL | Expected count at that moment (≥ 0 if provided). |
| `detected_quantity` | SMALLINT | YES | NULL | Detected/counted quantity (≥ 0 if provided). |
| `payload` | JSONB | NO | `'{}'` | Controlled extra data (no unnecessary PII). In `auto_count`: `confidences` list (0..1, one per box). |
| `occurred_at` | TIMESTAMPTZ | NO | `now()` | Timestamp of the fact. |
| `session_id` | UUID | NO | — | Session. |
| `user_id` | UUID | YES | NULL | User if triggered by a human; null if system-only. |

**References**

| Field | Points to | Relationship | Rationale | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `event_type_id` | `cat_event_type.id` | N:1 | Classifies the fact. | `RESTRICT` |
| `family_id` | `instrument_family.id` (DS03) | N:0..1 | Counted type. | `SET NULL` |
| `session_id` | `work_session.id` | N:1 | Temporal owner. | `CASCADE` |
| `user_id` | `user.id` (DS01) | N:0..1 | Optional human actor. | `SET NULL` |

UNIQUE `client_event_id` (when non-null, via column UNIQUE constraint).

In `auto_count`, the payload **must** include the per-box confidences (0..1). Per-family row: `payload.confidences`. Board snapshot (`scope = board`): `payload.board.families[].confidences`. Do not persist `avg` / `best` / `worst`.

```json
{
  "id": "ce000001-0000-4000-8000-000000000001",
  "event_type_id": "c1000002-0000-4000-8000-000000000002",
  "client_event_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
  "family_id": "f1000001-0000-4000-8000-000000000001",
  "expected_quantity": 2,
  "detected_quantity": 1,
  "payload": {
    "model_version": "yolo26l-demo",
    "confidences": [0.93, 0.88],
    "ui_source": "station"
  },
  "occurred_at": "2026-09-05T18:20:00Z",
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "user_id": null
}
```

---

#### 1.3.7 Table `discrepancy`

**Purpose:** open or resolved mismatch, traceable to a session and optionally to an originating event.

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `description` | TEXT | NO | — | Human-readable mismatch description. |
| `resolved` | BOOLEAN | NO | `FALSE` | Whether it has been closed. |
| `resolved_at` | TIMESTAMPTZ | YES | NULL | When it was resolved (mandatory if `resolved`). |
| `reason_id` | UUID | YES | NULL | Catalog reason when classifying/resolving. |
| `family_id` | UUID | YES | NULL | Involved family. |
| `expected_quantity` | SMALLINT | YES | NULL | Expected count (≥ 0 if provided). |
| `detected_quantity` | SMALLINT | YES | NULL | Detected count (≥ 0 if provided). |
| `session_id` | UUID | NO | — | Session. |
| `origin_event_id` | UUID | YES | NULL | `count_event` that originated the alert. |
| `suggested_resolution` | TEXT | YES | NULL | OR narrative when releasing the hold (found / not found / search). |
| `or_released_at` | TIMESTAMPTZ | YES | NULL | When OP sent the case to SPD. |
| `or_released_by_user_id` | UUID | YES | NULL | Who released the hold. |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Last update. |

**References**

| Field | Points to | Relationship | Rationale | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `reason_id` | `cat_discrepancy_reason.id` | N:0..1 | Controlled classification. | `SET NULL` |
| `family_id` | `instrument_family.id` (DS03) | N:0..1 | Mismatched type. | `SET NULL` |
| `session_id` | `work_session.id` | N:1 | Owner. | `CASCADE` |
| `origin_event_id` | `count_event.id` | N:0..1 | Triggering fact. | `SET NULL` |
| `or_released_by_user_id` | `user.id` (DS01) | N:0..1 | Operator who released to SPD. | `SET NULL` |

CHECK: if `resolved = TRUE` then `resolved_at` NOT NULL.

```json
{
  "id": "di000001-0000-4000-8000-000000000001",
  "description": "Missing 1 Mayo-Hegar needle holder relative to expected count",
  "resolved": false,
  "resolved_at": null,
  "reason_id": null,
  "family_id": "f1000001-0000-4000-8000-000000000001",
  "expected_quantity": 2,
  "detected_quantity": 1,
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "origin_event_id": "ce000001-0000-4000-8000-000000000001",
  "suggested_resolution": null,
  "or_released_at": null,
  "or_released_by_user_id": null,
  "updated_at": "2026-09-05T18:20:05Z"
}
```

---

#### 1.3.8 Table `human_correction`

**Purpose:** human correction/validation of an event. **One** correction per `count_event` (UNIQUE).

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `justification` | TEXT | NO | — | Mandatory text explaining why it is corrected/validated. |
| `recorded_at` | TIMESTAMPTZ | NO | `now()` | Recording timestamp. |
| `count_event_id` | UUID | NO | — | Corrected event (unique). |
| `user_id` | UUID | YES | NULL | Who recorded the correction. |

**References**

| Field | Points to | Relationship | Rationale | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `count_event_id` | `count_event.id` | 1:1 (UNIQUE: at most one correction per event) | Fact being corrected; event history is not erased. | `CASCADE` |
| `user_id` | `user.id` (DS01) | N:0..1 | Author of the justification. | `SET NULL` |

UNIQUE `count_event_id`.

```json
{
  "id": "hc000001-0000-4000-8000-000000000001",
  "justification": "Confirmed on tray: second needle holder was under gauze; manual count = 2.",
  "recorded_at": "2026-09-05T18:22:10Z",
  "count_event_id": "ce000001-0000-4000-8000-000000000001",
  "user_id": "22222222-2222-2222-2222-222222222222"
}
```

---

### 1.4 Governance and Ownership

| Aspect | Owner |
| :--- | :--- |
| Session open/close | Station + Backend |
| `count_event` persistence | Backend / SessionService (portal); VisionWorker does not write PG |
| Discrepancy resolution | Authorized clinical staff |
| Extended retention | Coordinate with PEF-DS07 / privacy policy |

---

### 1.5 Standard Rationale

**Why this block exists**

The real-time UI is ephemeral. MongoDB stores vision detail. PostgreSQL needs a durable record that answers: "in this session, at this time, this count and this discrepancy were recorded."

**Design decisions taken**

1. **Per-session `expected_inventory` snapshot.** Editing a kit tomorrow does not rewrite what was expected yesterday.
2. **`count_event` as a business fact**, not just an application log. Supports SQL reporting and auditing.
3. **`client_event_id` for idempotency.** Network retries do not duplicate the same fact.
4. **Discrepancy as a first-class entity.** Can remain open; not every mismatch is simply an update to the last event.
5. **1:1 human correction with mandatory justification.** Prevents silent number overrides.
6. **Session can exist without a privacy agreement.** Agreement is 0..1 in DS07 (e.g., pre-reservation).
7. **Fan-out UI + PG (+ optional MongoDB).** The screen is not the sole source of truth.

**Rejected alternatives**

| Alternative | Why not |
| :--- | :--- |
| Store only the latest count on the session | Loses timeline; no change audit trail. |
| Expected = live join to kit | Past sessions shift whenever the kit is edited. |
| MongoDB-only for counts | Legal/hospital reporting typically requires relational SQL and different retention than checkpoint TTLs. |
| Always put boxes (xyxy, top-k, matrix) inside `count_event.payload` | Bloats PG; geometric detail belongs to DS06. Each `auto_count` does include per-box **`confidences`** (short list), not avg/best/worst. |
| Multiple unconstrained human corrections per event | Ambiguous which one prevailed; UNIQUE enforces one active correction per event (if long history is needed, version the event rather than stacking silent corrections). |
| Discrepancy only as a flag on the event | Complicates listing open items, assigning reasons, and resolving without altering the original fact. |

---

## 2. Annex A – General Information

| Field | Value |
| :--- | :--- |
| Code | PEF-DS04 |
| Title | Work Session and Count Facts |
| Version | 0.2.1 |
| Status | draft |
| Store | PostgreSQL (§5.7) |
| Out of scope | MongoDB checkpoints; object storage; frame-by-frame WSS (DS10); privacy agreement (DS07) |
| Reference DDL | `esquema_base_datos_v2.md` |
