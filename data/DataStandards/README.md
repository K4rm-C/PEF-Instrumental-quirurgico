# PEF Data Standards · Index

Documents explaining what data the system handles, what it is used for, how it is written and read, and under which rules. They do not replace architecture diagrams or DDL: they make them usable for any team member (or successor) who needs to understand the meaning of each element without guessing.

Language: file names, IDs, and body text are in English.

Technical table source: [`../esquema_base_datos_v2.md`](../esquema_base_datos_v2.md) (historical baseline DDL snapshot; the "51 tables" count describes that snapshot, not the live inventory). The schema deployed in the integrated stack is `data/migrations/` (001 + 004…021) via `docker-compose.integral.yml`. ER export: [`../ER PostgreSQL-MongoDB-v3.jpg`](../ER%20PostgreSQL-MongoDB-v3.jpg).

Operational runbook (compose, LAN HTTPS :5443, Gateway :8080, Vision/Evidence, S3-compatible Garage object store): README at the repository root. `docs/` contains only a stub pointing here and to that README; there are no longer MS/admin packs or `Legacy/` / `porting_sources` at the root.

Screen contracts (FR) do not live here; they are in [`../FunctionalRequirements/`](../FunctionalRequirements/).

---

## Catalog

| ID | File | Scope (canonical owner) |
| :--- | :--- | :--- |
| PEF-DS01 | [PEF-DS01-Identity-and-Credentials.md](PEF-DS01-Identity-and-Credentials.md) | Institution, user, role, `user_operating_room`, hashed password, Redis session/login keys |
| PEF-DS02 | [PEF-DS02-Clinical-Context-and-Identifiers.md](PEF-DS02-Clinical-Context-and-Identifiers.md) | Patient, physician, operating room, capture station, operation, FHIR identifiers |
| PEF-DS03 | [PEF-DS03-Instrument-Kits-and-Model-Registry.md](PEF-DS03-Instrument-Kits-and-Model-Registry.md) | Families, pieces, kits, procedure phases, YOLO model registry |
| PEF-DS04 | [PEF-DS04-Work-Session-and-Count-Facts.md](PEF-DS04-Work-Session-and-Count-Facts.md) | Work session, expected inventory, count events, discrepancies |
| PEF-DS05 | [PEF-DS05-Evidence-and-Object-Storage.md](PEF-DS05-Evidence-and-Object-Storage.md) | Evidence in object storage (`media_asset`) |
| PEF-DS06 | [PEF-DS06-Mongo-Checkpoints-and-Telemetry.md](PEF-DS06-Mongo-Checkpoints-and-Telemetry.md) | Checkpoints and telemetry in MongoDB |
| PEF-DS07 | [PEF-DS07-Privacy-Purposes-and-Subject-Rights.md](PEF-DS07-Privacy-Purposes-and-Subject-Rights.md) | Privacy notice, processing agreements, ARCO, access audit |
| PEF-DS08 | [PEF-DS08-Machine-Clients-and-FHIR-Export.md](PEF-DS08-Machine-Clients-and-FHIR-Export.md) | Machine client and FHIR export |
| PEF-DS09 | [PEF-DS09-API-Exchange-Contracts.md](PEF-DS09-API-Exchange-Contracts.md) | REST APIs, endpoints, JSON/XML |
| PEF-DS10 | [PEF-DS10-Vision-Realtime-Contracts.md](PEF-DS10-Vision-Realtime-Contracts.md) | Station–worker WSS messages |

Useful tags in each file's front matter:

- `applies_to`: WP (web portal), BE (web backend), CS (capture station), AP (mobile/desktop apps), MS (microservices), Worker
- `stores`: PostgreSQL, Redis, MongoDB, ObjectStorage

---

## Anti-Overlap Rules

1. Each table or collection has a single canonical owner (one DS). Others only say "see PEF-DSxx".
2. Each endpoint is defined in detail in DS09 (REST) or DS10 (WSS). Table DS documents may mention the endpoint that uses them without redescribing the HTTP contract.
3. Image/MP4/weights bytes: DS05. Detections in MongoDB: DS06. Count facts in PostgreSQL: DS04.
4. Passwords and secrets: hashes only in DS01 / DS08. Never plaintext values in a standard or in mocks.

### Anti-Overlap Matrix (Verification)

| Artifact | Canonical owner | Do not redefine in |
| :--- | :--- | :--- |
| `institution`, `user`, `role`, `user_role`, `user_operating_room`, Redis auth | DS01 | DS08–10 |
| `patient`…`operation*`, `resource_identifier`, clinical catalogs | DS02 | DS03–04 (FK only) |
| Families, kits, YOLO, `cat_operation_phase` | DS03 | DS04 (phase reference only) |
| `work_session`, `count_event`, discrepancy | DS04 | DS06 (no SQL facts) |
| `media_asset` + bucket bytes | DS05 | DS06 (UUID/URI only) |
| `checkpoint_frame`, `session_telemetry` | DS06 | DS04/DS05 |
| Privacy notice, agreement, ARCO, `access_audit`, purposes | DS07 | DS04 (session without agreement allowed) |
| `integration_client`, FHIR projection | DS08 | DS01 (do not mix with user) |
| REST endpoints `/api/v1/*` | DS09 | DS01–08 (mention only) |
| Station–worker WSS messages | DS10 | DS09 |

---

## Structure of Each Standard

Aligned with the project's data governance template:

0. INTRODUCTION  
1. Standard Explanation  
   - 1.1 Definitions  
   - 1.2 Usage  
   - 1.3 Conventions: all owned tables, all fields (type/nullable/default/meaning), FKs (target, relationship/cardinality, rationale / ON DELETE), and one complete example row per table  
   - 1.4 Governance and Ownership  
   - 1.5 Rationale: design decisions and rejected alternatives with reasons  
2. Annex A – General Information  

---

## Data Conventions (All DS)

- Catalog codes and table/column names are in English in the database (`code`, `work_session`, etc.).
- UI texts may be localized (e.g., Spanish); stored values always follow the catalog `code`.
- Internal identifiers: UUID.
- Timestamps: UTC (`TIMESTAMPTZ`).
- Demo mocks: fabricated data, with no real patient records.
- Relationship / cardinality on FKs (from the current table → target):
  - `N:1`: many rows here point to one row there (typical mandatory FK)
  - `N:0..1`: the target is optional (nullable FK)
  - `1:1`: at most one pair (usually paired with UNIQUE)
  - `N:N`: conceptual between two entities; implemented in SQL with a junction table (each FK in the junction table is `N:1`)
