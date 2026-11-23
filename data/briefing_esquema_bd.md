# Briefing · Database Schema (PEF)

> Historical snapshot / introductory narrative. For canonical contracts, see `data/DataStandards/`; to run the stack, see the root README. `docs/` is only a stub. There is no `Legacy/` or `porting_sources` at the root. The "51 tables" count describes the baseline snapshot; the deployed schema is `data/migrations/` (001 + 004…021). Current on-prem object store: Garage (S3-compatible).

---

## 1. Summary

The schema models who counts, in which surgical procedure, with which kit and instruments, what the system detected, and what the human decided—without attempting to become a full HIS/EHR. PostgreSQL is the source of truth for business data and auditing; MongoDB stores occasional visual evidence; S3-compatible object storage (on-prem Garage today) stores binary files; Redis holds ephemeral tokens and the evidence ring (`pef:evidence:*`).

**Total (baseline snapshot):** 51 tables in PostgreSQL + 2 MongoDB collections. Includes `user_operating_room`, `work_session.yolo_model_id` pinning, and the `or_hold` status (human shortage hold before SPD).

---

## 2. Coverage


| Area | Depth | What It Includes |
| --- | --- | --- |
| **Counting session & auditing** | High | Work session, expected inventory, events, discrepancies, human corrections, session statuses |
| **Instruments & stock** | High | YOLO family, individual physical piece, usages, per-operation reservations, versionable kits, lifecycle |
| **Vision / model** | Medium–high (metadata) | Weights version, YOLO class-to-family mapping, per-station ROI; **not** model tensors |
| **Identity & access** | High | Institution, users, N:M roles |
| **Clinical-operational context** | Medium (minimum viable) | Patient, physician, operation, room, station; only what is needed to link the count |
| **FHIR interoperability** | Medium (contract-ready) | `system` + `value` identifiers; no live HIS connected yet |
| **Visual evidence** | Medium | MongoDB checkpoints + media anchor in PostgreSQL; cap of 30–50 frames per session; 90/180-day retention |


---

## 3. Intentional Limitations


| Limitation | Rationale |
| --- | --- |
| **Not an EHR** | Patients and physicians have no clinical history, notes, or diagnoses. PEF is a counting assistant, not a medical record system. |
| **No aggregate stock per family** | Stock = individual `instrument` rows. Available total = query (status + reservations). Avoids desynchronizing an aggregate counter against real pieces. |
| **Frame-by-frame detections do not go into PostgreSQL** | High frequency → MongoDB and UI. PostgreSQL records **business events**, not every bounding box at 10 FPS. |
| `resource_identifier` **without physical FK** | Polymorphic (FHIR Identifier pattern). PostgreSQL does not cleanly support "FK to N tables based on type." Integrity is enforced by queries and application logic. |
| `cat_checkpoint_reason` **without FK in PG** | Reason lives as a string in MongoDB. The PostgreSQL catalog serves as reference vocabulary. |
| **Redis is omitted from the ER diagram** | Holds JWT/TTL only; not persistent business data. |
| **Live TTS / STT / HIS** | Outside core scope or deferred; the schema already provides FHIR anchors. |


---

## 4. Storage Distribution


| Store | Stores | Does Not Store |
| --- | --- | --- |
| **PostgreSQL** | Business data, auditing, metadata, canonical file URIs | Model weights or JPEG bytes |
| **MongoDB** | Frame checkpoints, session telemetry | Closing rules, users |
| **Object storage (Garage/S3)** | YOLO weights, evidence JPEG/MP4 files, and family examples | Business logic |
| **Redis** | Tokens, ephemeral counters, evidence ring | Permanent audit trail |


---

## 5. Key Tables Rundown

### Identity and Portal

- `institution` — multi-campus root
- `user` **/** `role` **/** `user_role` — portal login and RBAC (patients are **not** users)

### External Exchange (Integrations)

- `resource_identifier` — `system|value` ↔ local UUID dictionary (patient, physician, operation, room, instrument). Lookup before duplicating. No polymorphic FK in PostgreSQL.

### Clinical Context (Lightweight)

- `operation` — surgical act where counting takes place
- `patient` **/** `physician` + N:M junction tables
- `operating_room` **→** `capture_station` — room + camera/ROI
- `cat_procedure_type` — procedure type (also feeds instrument usages)

### Instruments and Vision

- `instrument_family` — counting vocabulary (stable code + educational texts)
- `instrument` — physical piece (real stock)
- `instrument_usage` — piece × procedure × context
- `kit` **/** `kit_item` — template; copied into `expected_inventory` when opening a session
- `instrument_reservation` — specific piece reserved for an operation
- `yolo_model` **/** `model_class` — which weights are active and which `class_id` maps to which family
- `media_asset` — object storage anchor (weights, examples, frames/clips)

### Counting Session (Operational Core)

- `work_session` — who, when, station, kit, retention flags
- `expected_inventory` — snapshot of the kit when opening the session
- `count_event` — auditable facts (`event_type` + JSON `payload`)
- `discrepancy` **/** `human_correction` — shortages and human justification

### MongoDB

- `checkpoint_frame` — occasional evidence (detections + URI)
- `session_telemetry` — FPS/latency (short TTL)

---

## 6. General Design Decisions

1. **4NF / `cat_*` catalogs** — states and closed lists in dedicated tables; avoids free text and repeated columns.
2. **Per-piece stock, not per-family** — real reservations; kit assignment fails if no free units exist.
3. **Kit vs. expected inventory** — mutable template; each session retains a historical snapshot.
4. **Model outside the browser** — PostgreSQL stores metadata only; weights live in object storage and are loaded in VisionWorker (host GPU preferred).
5. **Post-inference fan-out** — same filtered result → WebSocket (UI); `count_event` in PostgreSQL via Backend/portal; checkpoint/clip via EvidenceWorker + Garage/MongoDB when policy requires it.
6. **Centralized FHIR Identifier** — HIS-ready; counting uses local UUIDs rather than querying by MRN everywhere.

---

## 7. The 4 Major Modules and Their DB Footprint

### Module 1 — Portal / Auth / UI

**Tables:** `institution`, `user`, `role`, `user_role`, Redis (tokens).  
**DB role:** authenticate, scope by institution, open/close work sessions.  
**Does not touch:** YOLO weights, checkpoints, `resource_identifier` (except supervision screens displaying external IDs).

### Module 2 — Inference / Vision / Station

**Tables:** `capture_station` (ROI), `yolo_model`, `model_class`, `instrument_family`, `media_asset`; MongoDB checkpoint and telemetry collections.  
**DB role:** load active model, map `class_id` → family, filter by ROI, emit detections.  
**Does not touch:** creating HIS patients; never writes every frame to PostgreSQL.

### Module 3 — Business Rules / Counting Session

**Tables:** `work_session`, `expected_inventory`, `count_event`, `discrepancy`, `human_correction`, `cat_session_status`, `kit`/`kit_item`, reservations.  
**DB role:** compare counted vs. expected, raise alerts, block closure, audit corrections.  
**Key link:** `work_session` connects user + operation + station + kit.

### Module 4 — Integrations / Clinical-Operational / FHIR

**Tables:** `resource_identifier`, `patient`, `physician`, `operation`, rooms, specialties.  
**DB role:** ingest external IDs (`system|value`), resolve to local UUID, link procedure ↔ patient/physician.  
**Bounded scope:** no EHR; no MongoDB; counting never talks to the HIS directly.

```text
UI ──WS──► Inference ──► Rules ──► PostgreSQL (events)
               │           │
               │           └──► (optional) EvidenceWorker → Garage + Mongo
               └── reads: ROI, yolo_model, model_class, families

HIS Integrations ──► resource_identifier ──► patient / physician / operation
                             (does not touch the frame pipeline)
```

---

## 8. Diagram

- **Solid line** = real foreign key in PostgreSQL.
- **Dashed line or note** = logical relationship (FHIR, MongoDB, polymorphic).
- `resource_identifier` has no solid arrows: this is intentional; joins occur via query (`resource_type` + `resource_id`).
- `cat_checkpoint_reason` has no incoming FK in PG; it feeds the `reason` field in MongoDB as a vocabulary catalog.
- Visual canvas layout does **not** need to match the internal Mermaid diagram; what matters are the documented tables and FKs.
- Draw.io v3 export (canonical image): [ER PostgreSQL-MongoDB-v3.jpg](ER%20PostgreSQL-MongoDB-v3.jpg)

---

## 9. Brief Technical Conventions

- PostgreSQL 15+, `UUID` PKs, UTC timestamps.
- FHIR Identifiers: `system` (URI) + `value` (string).
- Internal PEF URI: `https://pef.udem/conteo-cenital` + record UUID.
- Table names in English in the DDL (`work_session`, `instrument_family`, etc.) (because PEF is developed jointly with Linnaeus University).
- Total (baseline snapshot): 51 PostgreSQL tables + 2 MongoDB collections; live deployment via migrations 001 + 004…021.
