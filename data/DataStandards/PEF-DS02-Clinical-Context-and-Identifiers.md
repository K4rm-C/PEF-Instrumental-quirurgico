---
id: PEF-DS02
title: Clinical Context and Identifiers
version: "0.2.0"
status: draft
owner: PEF Core / Integrations
applies_to: [WP, BE, MS, AP]
stores: [PostgreSQL]
related: [PEF-DS01, PEF-DS03, PEF-DS04, PEF-DS08]
last_updated: 2026-09-06
---

# PEF-DS02 · Clinical Context and Identifiers

## INTRODUCTION

This standard defines the **clinical-operational context** surrounding counting: patient, physician, operating room, capture station, operation, and **external FHIR Identifier-style mappings** (`resource_identifier`).

It also owns these catalogs: `cat_gender`, `cat_specialty`, `cat_procedure_type`, `cat_surgical_role`, `cat_operation_status`.

It does not define kits or pieces (PEF-DS03), counting sessions/events (PEF-DS04), or the FHIR export Bundle (PEF-DS08).

Below: **all** fields of each owned table, FKs with rationales, and one **complete example row** per table.

---



## 1. Standard Explanation



### 1.1 Definitions


| Term | Meaning in PEF |
| --- | --- |
| **Patient** | Person associated with the operation. PEF stores minimal data; the "official" medical record typically lives in the HIS and is linked via identifier. |
| **Physician** | Institution clinical professional. In an operation they hold a **surgical role** (surgeon, assistant, …), distinct from login roles (DS01). |
| **Operating room** | Physical room (`operating_room`) within the institution. |
| **Capture station** | Camera/ROI over a tray, anchored to an operating room. |
| **Operation** | Clinical-operational case (scheduled or in progress) with status, timestamps, and links. |
| **Resource identifier** | Pair `system` + `value` pointing to a PEF UUID (`resource_type` + `resource_id`). |




### 1.2 Usage

**Who:** web portal; Android/Desktop apps via MS; capture station when selecting an operation/station.

**Purpose**

1. Know which case and room the count takes place in.
2. Link counting to patients/surgical team without placing the full EHR inside vision tables.
3. Translate hospital IDs (MRN, operating room code) to internal UUIDs.

**Typical flow:** create/fetch operation → link patients and physicians → assign room → station already registered in that room → if an external ID arrives, resolve via `resource_identifier`.

---



### 1.3 Conventions

- Catalogs: stable English `code`; human-readable `name` (may be localized in data).
- Active external identifiers: unique by (`system`, `value`).
- No physical polymorphic FK from `resource_identifier.resource_id` to six tables; the application enforces integrity.
- Under "References", **Relationship**: `N:1` = many rows here to one there; `N:0..1` = optional target; `N:N` via junction table (each junction FK is `N:1`).

---



#### 1.3.1 Catalog `cat_gender`

**Purpose:** controlled administrative sex/gender value for a patient.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | Catalog PK. |
| `code` | VARCHAR(32) | NO | — | Stable code (`female`, `male`, `other`, `unknown`). |
| `name` | VARCHAR(80) | NO | — | Human-readable label. |


**FKs:** none (root catalog table). UNIQUE `code`.

```json
{
  "id": "a1000001-0000-4000-8000-000000000001",
  "code": "female",
  "name": "Female"
}
```

---



#### 1.3.2 Catalog `cat_specialty`

**Purpose:** medical specialty of the practitioner.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(64) | NO | — | Stable code (`general_surgery`, …). |
| `name` | VARCHAR(160) | NO | — | Human-readable name. |


**FKs:** none. UNIQUE `code`.

```json
{
  "id": "a1000002-0000-4000-8000-000000000002",
  "code": "general_surgery",
  "name": "General Surgery"
}
```

---



#### 1.3.3 Catalog `cat_procedure_type`

**Purpose:** clinical procedure type. Used by `operation` (this DS) and kits/phases/usages (DS03). **Canonical definition here.**


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(64) | NO | — | Stable code. |
| `name` | VARCHAR(200) | NO | — | Human-readable name. |


**FKs:** none. UNIQUE `code`.

```json
{
  "id": "a1000003-0000-4000-8000-000000000003",
  "code": "inguinal_hernia_open",
  "name": "Open inguinal hernia repair"
}
```

---



#### 1.3.4 Catalog `cat_surgical_role`

**Purpose:** physician role **within an operation** (not a login role).


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(64) | NO | — | E.g. `primary_surgeon`, `assistant`. |
| `name` | VARCHAR(120) | NO | — | Label. |


**FKs:** none. UNIQUE `code`.

```json
{
  "id": "a1000004-0000-4000-8000-000000000004",
  "code": "primary_surgeon",
  "name": "Primary surgeon"
}
```

---



#### 1.3.5 Catalog `cat_operation_status`

**Purpose:** operational case status.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(32) | NO | — | E.g. `scheduled`, `in_progress`, `completed`, `cancelled`. |
| `name` | VARCHAR(80) | NO | — | Label. |


**FKs:** none. UNIQUE `code`.

```json
{
  "id": "a1000005-0000-4000-8000-000000000005",
  "code": "in_progress",
  "name": "In progress"
}
```

---



#### 1.3.6 Table `patient`

**Purpose:** minimal patient record in PEF.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | Internal PK. |
| `display_name` | VARCHAR(160) | NO | — | Name for screens (may be pseudonymized per policy). |
| `birth_date` | DATE | YES | NULL | Birth date if captured. |
| `active` | BOOLEAN | NO | `TRUE` | Patient usable in new operations. |
| `gender_id` | UUID | YES | NULL | Administrative sex/gender. |
| `institution_id` | UUID | NO | — | Owning tenant. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `gender_id` | `cat_gender.id` | N:0..1 | Controlled value; optional. | `SET NULL` |
| `institution_id` | `institution.id` (DS01) | N:1 | Multi-tenant isolation. | `RESTRICT` |


```json
{
  "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  "display_name": "Demo Patient",
  "birth_date": "1988-04-12",
  "active": true,
  "gender_id": "a1000001-0000-4000-8000-000000000001",
  "institution_id": "11111111-1111-1111-1111-111111111111"
}
```

---



#### 1.3.7 Table `physician`

**Purpose:** clinical professional belonging to the institution.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `name` | VARCHAR(160) | NO | — | Physician name. |
| `active` | BOOLEAN | NO | `TRUE` | Whether assignable to new operations. |
| `institution_id` | UUID | NO | — | Institution. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `institution_id` | `institution.id` | N:1 | Tenant. | `RESTRICT` |


```json
{
  "id": "c0c0c0c0-c0c0-c0c0-c0c0-c0c0c0c0c0c0",
  "name": "Dr. Demo Ruiz",
  "active": true,
  "institution_id": "11111111-1111-1111-1111-111111111111"
}
```

---



#### 1.3.8 Table `physician_specialty`

**Purpose:** N:M physician ↔ specialty.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | Relationship PK. |
| `physician_id` | UUID | NO | — | Physician. |
| `specialty_id` | UUID | NO | — | Specialty. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `physician_id` | `physician.id` | N:1 (physician↔specialty is N:N via this table) | Owner of the assigned specialty. | `CASCADE` |
| `specialty_id` | `cat_specialty.id` | N:1 (physician↔specialty is N:N via this table) | Catalog. | `RESTRICT` — do not delete a specialty in use. |


UNIQUE (`physician_id`, `specialty_id`).

```json
{
  "id": "d0d0d0d0-d0d0-d0d0-d0d0-d0d0d0d0d0d0",
  "physician_id": "c0c0c0c0-c0c0-c0c0-c0c0-c0c0c0c0c0c0",
  "specialty_id": "a1000002-0000-4000-8000-000000000002"
}
```

---



#### 1.3.9 Table `operating_room`

**Purpose:** physical room.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(32) | NO | — | Short code unique per institution (`OR-3`). |
| `name` | VARCHAR(120) | NO | — | Human-readable name. |
| `active` | BOOLEAN | NO | `TRUE` | Usable room. |
| `institution_id` | UUID | NO | — | Institution. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `institution_id` | `institution.id` | N:1 | Tenant. | `CASCADE` |


UNIQUE (`institution_id`, `code`).

```json
{
  "id": "e0e0e0e0-e0e0-e0e0-e0e0-e0e0e0e0e0e0",
  "code": "OR-3",
  "name": "Operating Room 3",
  "active": true,
  "institution_id": "11111111-1111-1111-1111-111111111111"
}
```

---



#### 1.3.10 Table `capture_station`

**Purpose:** capture point (camera) positioned over a tray.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `name` | VARCHAR(120) | NO | — | Station name. |
| `roi` | JSONB | YES | NULL | Normalized or pixel region of interest (app contract). |
| `active` | BOOLEAN | NO | `TRUE` | Usable station. |
| `room_id` | UUID | NO | — | Operating room where it is installed. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `room_id` | `operating_room.id` | N:1 | A station does not float: it belongs to a room. | `CASCADE` |


```json
{
  "id": "f0f0f0f0-f0f0-f0f0-f0f0-f0f0f0f0f0f0",
  "name": "OR-3 Tray A",
  "roi": { "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.7, "coord_space": "normalized" },
  "active": true,
  "room_id": "e0e0e0e0-e0e0-e0e0-e0e0-e0e0e0e0e0e0"
}
```

---



#### 1.3.11 Table `operation`

**Purpose:** clinical-operational case to which counting attaches.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `scheduled_at` | TIMESTAMPTZ | YES | NULL | Scheduled time. |
| `started_at` | TIMESTAMPTZ | YES | NULL | Actual start time. |
| `ended_at` | TIMESTAMPTZ | YES | NULL | Actual end time. |
| `status_id` | UUID | NO | — | Current status. |
| `procedure_type_id` | UUID | YES | NULL | Procedure type. |
| `room_id` | UUID | YES | NULL | Assigned room. |
| `institution_id` | UUID | NO | — | Tenant. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `status_id` | `cat_operation_status.id` | N:1 | Controlled status. | `RESTRICT` |
| `procedure_type_id` | `cat_procedure_type.id` | N:0..1 | Classifies the case; optional on creation. | `SET NULL` |
| `room_id` | `operating_room.id` | N:0..1 | Where it takes place. | `SET NULL` |
| `institution_id` | `institution.id` | N:1 | Tenant. | `RESTRICT` |


**Check:** `ended_at` ≥ `started_at` when both are present.

```json
{
  "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "scheduled_at": "2026-09-05T16:00:00Z",
  "started_at": "2026-09-05T16:12:00Z",
  "ended_at": null,
  "status_id": "a1000005-0000-4000-8000-000000000005",
  "procedure_type_id": "a1000003-0000-4000-8000-000000000003",
  "room_id": "e0e0e0e0-e0e0-e0e0-e0e0-e0e0e0e0e0e0",
  "institution_id": "11111111-1111-1111-1111-111111111111"
}
```

---



#### 1.3.12 Table `operation_patient`

**Purpose:** N:M operation ↔ patient (an operation may involve more than one patient in special scenarios; typically one).


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `operation_id` | UUID | NO | — | Operation. |
| `patient_id` | UUID | NO | — | Patient. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `operation_id` | `operation.id` | N:1 (operation↔patient is N:N via this table) | Case. | `CASCADE` |
| `patient_id` | `patient.id` | N:1 (operation↔patient is N:N via this table) | Person. | `RESTRICT` — do not delete a still-linked patient. |


UNIQUE (`operation_id`, `patient_id`).

```json
{
  "id": "aa11aa11-aa11-aa11-aa11-aa11aa11aa11",
  "operation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "patient_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
}
```

---



#### 1.3.13 Table `operation_physician`

**Purpose:** physicians participating in an operation **with** a surgical role.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `operation_id` | UUID | NO | — | Operation. |
| `physician_id` | UUID | NO | — | Physician. |
| `surgical_role_id` | UUID | NO | — | Role in that case. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `operation_id` | `operation.id` | N:1 (operation↔physician is N:N via this table) | Case. | `CASCADE` |
| `physician_id` | `physician.id` | N:1 (operation↔physician is N:N via this table) | Practitioner. | `RESTRICT` |
| `surgical_role_id` | `cat_surgical_role.id` | N:1 | Surgical role. | `RESTRICT` |


UNIQUE (`operation_id`, `physician_id`, `surgical_role_id`) — the same person may appear under different roles if the workflow permits.

```json
{
  "id": "bb22bb22-bb22-bb22-bb22-bb22bb22bb22",
  "operation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "physician_id": "c0c0c0c0-c0c0-c0c0-c0c0-c0c0c0c0c0c0",
  "surgical_role_id": "a1000004-0000-4000-8000-000000000004"
}
```

---



#### 1.3.14 Table `resource_identifier`

**Purpose:** FHIR Identifier map (`system`|`value`) → PEF resource UUID. Used for HIS IDs, room codes, externally serialized instruments, etc.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | Identifier row PK. |
| `resource_type` | VARCHAR(32) | NO | — | PEF type: `institution`, `patient`, `physician`, `operation`, `operating_room`, `instrument`. |
| `resource_id` | UUID | NO | — | UUID of the PEF row (no physical polymorphic FK). |
| `system` | URI | NO | — | Issuer namespace (URI). |
| `value` | VARCHAR(255) | NO | — | Value in that system. |
| `use_code` | VARCHAR(16) | NO | `'official'` | `usual`, `official`, `temp`, `secondary`, `old`. |
| `period_start` | TIMESTAMPTZ | YES | NULL | Validity start. |
| `period_end` | TIMESTAMPTZ | YES | NULL | Validity end. |
| `active` | BOOLEAN | NO | `TRUE` | Only active rows enter the unique index. |
| `first_seen_at` | TIMESTAMPTZ | NO | `now()` | First time PEF observed this identifier. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `resource_id` | Logical: table matching `resource_type` | Logical N:1 (many identifiers → one resource; a resource may have 0..N identifiers) | Avoids illegal polymorphic FK in strict SQL. | N/A (no physical FK) |


**Checks:** `resource_type` ∈ allowed list; `use_code` ∈ allowed list; `period_end` ≥ `period_start` when both are present.  
**Partial unique index:** (`system`, `value`) WHERE `active = TRUE`.

```json
{
  "id": "cc33cc33-cc33-cc33-cc33-cc33cc33cc33",
  "resource_type": "patient",
  "resource_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  "system": "https://demo.hospital/fhir/sid/mrn",
  "value": "MRN-0001",
  "use_code": "official",
  "period_start": "2026-01-01T00:00:00Z",
  "period_end": null,
  "active": true,
  "first_seen_at": "2026-03-15T10:00:00Z"
}
```

---



### 1.4 Governance and Ownership


| Aspect | Owner |
| --- | --- |
| Owner | PEF Core + Integrations (external IDs) |
| Patient/physician/room provisioning | Portal / MS depending on channel |
| Clinical catalogs | PEF Data Governance |


**Boundaries:** `instrument` may hold an Identifier here, but the piece itself is owned by DS03. The counting session references `operation_id`/`station_id` but is owned by DS04.

---



### 1.5 Standard Rationale

**Why this block exists**

Counting only has clinical meaning if we know **which operation and room** it occurred in. Separating it from instruments and events prevents the HIS or the kit catalog from rewriting the other's model.

**Design decisions taken**

1. **Minimal patient in PEF** (`display_name`, not a full medical record): PEF does not replace the HIS; reduces local PII.
2. **N:M operation–patient and operation–physician:** supports real cases without assuming "always one."
3. **Surgical role on the relationship, not on** `physician`**:** the same person can be primary surgeon in one case and assistant in another.
4. **Station hangs from room, not directly from institution:** the ROI is physical; the tenant is inherited through the room.
5. **FHIR-style** `resource_identifier` **without polymorphic FK:** stable internal UUID; versionable external IDs via `active`/`period_*`.
6. **Canonical** `cat_procedure_type` **in this DS:** procedure is a clinical concept first; kits (DS03) reuse it.

**Rejected alternatives**


| Alternative | Why not |
| --- | --- |
| Put MRN as a column on `patient` | A patient can have multiple issuing systems; Identifier is truer to FHIR and reality. |
| Polymorphic FK or one table per ID type | Causes table proliferation; the Identifier pattern unifies lookup by `system`+`value`. |
| Unify `user` and `physician` | The person operating the station is not necessarily the listed surgeon. |
| Store ROI only in an edge config file | Loses central traceability; the station is a business entity (maintenance, sessions). |
| Embed operation only as JSON inside the session | Prevents listing/scheduling operations before opening a counting session. |
| Store specialty as free text on `physician` | Breaks reporting; the catalog preserves stable codes. |


---



## 2. Annex A – General Information


| Field | Value |
| --- | --- |
| Code | PEF-DS02 |
| Title | Clinical Context and Identifiers |
| Version | 0.2.0 |
| Status | draft |
| Store | PostgreSQL (clinical catalogs + §5.4 + `resource_identifier`) |
| Out of scope | Kits, YOLO, `work_session`, `count_event`, FHIR export Bundle |
| Reference DDL | `esquema_base_datos_v2.md` |
