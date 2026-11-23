---
id: PEF-DS07
title: Privacy Purposes and Subject Rights
version: "0.1.0"
status: draft
owner: PEF Core / Compliance / Web Backend
applies_to: [WP, BE]
stores: [PostgreSQL]
related: [PEF-DS01, PEF-DS04, PEF-DS05, PEF-DS08]
last_updated: 2026-09-06
---

# PEF-DS07 · Privacy Purposes and Subject Rights

## INTRODUCTION

This standard defines **processing purposes**, **versioned privacy notices**, **per-session counting agreements**, **access auditing** for sensitive resources, and **ARCO requests** (access, rectification, cancellation, opposition).

Owned tables: `privacy_notice_version`, `session_processing_agreement`, `access_audit`, `privacy_request`.

Owned catalog: `cat_processing_purpose` (vocabulary; the agreement uses booleans rather than a per-purpose FK).

It does not define `media_asset` (PEF-DS05) or checkpoints (PEF-DS06): it orchestrates them via purge/block flags. It does not define human login (PEF-DS01) or machine clients (PEF-DS08), though `access_audit` can record both actor types.

---

## 1. Standard Explanation

### 1.1 Definitions

| Term | Meaning |
| :--- | :--- |
| **Versioned privacy notice** | Notice text/URI with content hash and effective date. |
| **Session agreement** | 0..1 per `work_session`: which processing purposes apply when using counting. |
| **quality_ops** | Purpose that is always true when an agreement exists: operating and auditing counting. |
| **model_improvement** | Opt-in: allows listing evidence from that session for offline model training. |
| **access_audit** | Append-oriented log of who accessed which resource (distinct from `count_event`). |
| **privacy_request** | ARCO case concerning a data-subject patient. |

### 1.2 Usage

When opening/activating capture-based counting: materialize an agreement. When reading a patient/session/clip: insert `access_audit`. When receiving an ARCO request: create `privacy_request` and, for cancellation, block/purge across stores.

**Key cardinality:** `work_session` 0..1 `session_processing_agreement` (a session may exist without an agreement, e.g., pre-reservation).

---

### 1.3 Conventions

---

#### 1.3.1 Catalog `cat_processing_purpose`

Processing purpose vocabulary. The agreement does **not** hold row-by-row FKs: it uses booleans (`purpose_quality_ops`, `purpose_model_improvement`), following the same pattern as checkpoint reasons.

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(64) | NO | — | Allowed only: `quality_ops`, `model_improvement`, `external_sharing`. |
| `name` | VARCHAR(160) | NO | — | Label. |
| `active` | BOOLEAN | NO | `TRUE` | Whether the purpose is enabled in the product. |

**References:** no FKs.

**Complete example row**

```json
{
  "id": "p1000001-0000-4000-8000-000000000001",
  "code": "quality_ops",
  "name": "Operational quality and count auditing",
  "active": true
}
```

`external_sharing` exists for future use; it is not part of the demo workflow.

---

#### 1.3.2 Table `privacy_notice_version`

**Purpose:** freeze which privacy notice text was displayed/accepted.

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `version` | VARCHAR(32) | NO | — | Unique version label (`2026.09.1`). |
| `effective_at` | TIMESTAMPTZ | NO | — | Effective start timestamp. |
| `document_uri` | TEXT | NO | — | URI of the notice PDF/HTML. |
| `content_sha256` | CHAR(64) | YES | NULL | Hash of the published content. |
| `active` | BOOLEAN | NO | `TRUE` | Version available for new agreements. |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Created at. |

**References:** no outgoing FKs.

**Complete example row**

```json
{
  "id": "p2000001-0000-4000-8000-000000000001",
  "version": "2026.09.1",
  "effective_at": "2026-09-01T00:00:00Z",
  "document_uri": "https://demo.pef.local/legal/aviso-2026-09-1.pdf",
  "content_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "active": true,
  "created_at": "2026-08-28T12:00:00Z"
}
```

---

#### 1.3.3 Table `session_processing_agreement`

**Purpose:** record accepted processing purposes for **one** session.

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `session_id` | UUID | NO | — | Session (unique: 0..1 agreement). |
| `privacy_notice_version_id` | UUID | NO | — | Notice version active at agreement time. |
| `purpose_quality_ops` | BOOLEAN | NO | `TRUE` | Must always be true (CHECK). |
| `purpose_model_improvement` | BOOLEAN | NO | `FALSE` | Model improvement opt-in. |
| `agreed_at` | TIMESTAMPTZ | NO | `now()` | Agreement timestamp. |
| `agreed_by_user_id` | UUID | YES | NULL | Who accepted. |

**References**

| Field | Points to | Relationship | Rationale | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `session_id` | `work_session.id` (DS04) | 1:1 (UNIQUE session_id; session has 0..1 agreement) | Processing anchor. | `CASCADE` |
| `privacy_notice_version_id` | `privacy_notice_version.id` | N:1 | Which text was accepted. | `RESTRICT` |
| `agreed_by_user_id` | `user.id` (DS01) | N:0..1 | Human actor. | `SET NULL` |

**Complete example row**

```json
{
  "id": "p3000001-0000-4000-8000-000000000001",
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "privacy_notice_version_id": "p2000001-0000-4000-8000-000000000001",
  "purpose_quality_ops": true,
  "purpose_model_improvement": false,
  "agreed_at": "2026-09-05T16:16:00Z",
  "agreed_by_user_id": "22222222-2222-2222-2222-222222222222"
}
```

---

#### 1.3.4 Table `access_audit`

**Purpose:** access trace for sensitive resources (PHI, evidence, exports). Not a counting log.

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `occurred_at` | TIMESTAMPTZ | NO | `now()` | Timestamp. |
| `actor_type` | VARCHAR(16) | NO | — | `user` or `client`. |
| `actor_user_id` | UUID | YES | NULL | User when `actor_type=user`. |
| `actor_client_id` | UUID | YES | NULL | Machine client when `actor_type=client`. |
| `action` | VARCHAR(64) | NO | — | E.g. `read`, `export`, `download`. |
| `resource_type` | VARCHAR(64) | NO | — | E.g. `patient`, `work_session`, `media_asset`. |
| `resource_id` | UUID | NO | — | Resource UUID. |
| `institution_id` | UUID | YES | NULL | Access tenant. |
| `outcome` | VARCHAR(16) | NO | `'success'` | `success` or `denied`. |
| `correlation_id` | VARCHAR(64) | YES | NULL | HTTP/WSS correlation ID. |
| `ip` | INET | YES | NULL | Source IP. |

**References**

| Field | Points to | Relationship | Rationale | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `actor_user_id` | `user.id` (DS01) | N:0..1 | Human actor. | `SET NULL` |
| `actor_client_id` | `integration_client.id` (DS08) | N:0..1 | Machine actor. | `SET NULL` |
| `institution_id` | `institution.id` (DS01) | N:0..1 | Tenant context. | `SET NULL` |
| `resource_id` | Logical per `resource_type` | Logical N:1 | No polymorphic FK. | N/A |

CHECK: actor columns consistent with `actor_type`; `outcome` ∈ (`success`, `denied`).

**Complete example row**

```json
{
  "id": "p4000001-0000-4000-8000-000000000001",
  "occurred_at": "2026-09-05T18:30:00Z",
  "actor_type": "user",
  "actor_user_id": "22222222-2222-2222-2222-222222222222",
  "actor_client_id": null,
  "action": "download",
  "resource_type": "media_asset",
  "resource_id": "m1000003-0000-4000-8000-000000000003",
  "institution_id": "11111111-1111-1111-1111-111111111111",
  "outcome": "success",
  "correlation_id": "corr-demo-7788",
  "ip": "10.0.0.15"
}
```

---

#### 1.3.5 Table `privacy_request`

**Purpose:** ARCO rights case file for a data subject (patient).

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `request_type` | VARCHAR(16) | NO | — | `access`, `rectify`, `cancel`, `oppose`. |
| `status` | VARCHAR(32) | NO | `'received'` | `received`, `in_progress`, `completed`, `denied`. |
| `channel` | VARCHAR(32) | YES | NULL | Intake channel (`portal`, `email`, …). |
| `subject_patient_id` | UUID | NO | — | Data-subject patient. |
| `requested_at` | TIMESTAMPTZ | NO | `now()` | Received timestamp. |
| `due_at` | TIMESTAMPTZ | YES | NULL | Internal response deadline. |
| `handled_by_user_id` | UUID | YES | NULL | Compliance operator. |
| `resolution_notes` | TEXT | YES | NULL | Resolution notes. |
| `completed_at` | TIMESTAMPTZ | YES | NULL | Closure timestamp (required if completed/denied). |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Created at. |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Updated at. |

**References**

| Field | Points to | Relationship | Rationale | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `subject_patient_id` | `patient.id` (DS02) | N:1 | Data subject. | `RESTRICT` |
| `handled_by_user_id` | `user.id` (DS01) | N:0..1 | Handler. | `SET NULL` |

**Cancellation (operational guidance):** set `media_asset.blocked_at` / `purge_requested_at` on linked evidence; delete/anonymize per policy; reuse the DS05 purge job; not "just a checkbox."

**Complete example row**

```json
{
  "id": "p5000001-0000-4000-8000-000000000001",
  "request_type": "cancel",
  "status": "in_progress",
  "channel": "portal",
  "subject_patient_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  "requested_at": "2026-09-06T10:00:00Z",
  "due_at": "2026-09-26T10:00:00Z",
  "handled_by_user_id": "22222222-2222-2222-2222-222222222222",
  "resolution_notes": "Inventory of linked sessions and media in progress.",
  "completed_at": null,
  "created_at": "2026-09-06T10:00:00Z",
  "updated_at": "2026-09-06T11:00:00Z"
}
```

---

### 1.4 Governance and Ownership

| Aspect | Owner |
| :--- | :--- |
| Privacy notice text | Legal / institutional compliance |
| Session agreements | Station / Backend upon capture activation |
| `access_audit` | All endpoints/screens reading PHI |
| ARCO | Compliance role |

---

### 1.5 Standard Rationale

**Why it exists**

Without explicit purposes and an access trail, counting with identifiable evidence cannot be explained to an auditor or fulfill ARCO obligations.

**Design decisions taken**

1. **0..1 agreement per session**, not mandatory on session creation (supports pre-reservation).
2. **`quality_ops` always true** in the agreement; **`model_improvement` opt-in**.
3. **Booleans on the agreement** + purpose catalog (same pattern as checkpoint reasons).
4. **`access_audit` separate from `count_event`.**
5. **Actor user XOR client** in the same audit log.
6. **ARCO as an entity** orchestrating purges already modeled in `media_asset`. In v1, the Admin portal only **lists** `privacy_request` (stub RF-AD-11); it does not execute access, rectification, cancellation, or opposition workflows.

**Rejected alternatives**

| Alternative | Why not |
| :--- | :--- |
| Global per-user consent without session scope | Evidence is per clinical act/session; training opt-in must be scoped. |
| N:M FK agreement ↔ purpose | More flexible, more complexity; two flags + catalog suffice today. |
| Mix ARCO into generic support tickets | Loses statutory deadlines, request types, and patient linkage. |
| Audit every YOLO frame | Useless noise; audit access to identifiable resources/downloads instead. |

---

## 2. Annex A – General Information

| Field | Value |
| :--- | :--- |
| Code | PEF-DS07 |
| Title | Privacy Purposes and Subject Rights |
| Version | 0.1.0 |
| Status | draft |
| Store | PostgreSQL (§5.7b + `cat_processing_purpose`) |
| Out of scope | Storage bytes; FHIR export (DS08); detailed HTTP contracts (DS09) |
| Reference DDL | `esquema_base_datos_v2.md` |
