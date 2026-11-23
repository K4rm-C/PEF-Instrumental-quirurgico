# Formal Functional Contract · IT Administrator and Floor Supervisor (App)

## How to read this document

Each functional requirement (RF) follows the same ten-point template as [rf_operador_supervisor_spd.md](rf_operador_supervisor_spd.md): identifier, actor, entry point, UI, user actions, system effects, stores, configuration, output, errors.

**Database write convention:** same as the OP/SPD contract (Web Backend writes PostgreSQL; vision-worker does not own PG; evidence-worker writes storage + Mongo; the client never opens JDBC).

This contract does **not replace** the operator/SPD one. It complements it: Admin owns families, selection catalogs, identity, topology, and models; the App is floor monitoring. Room counting, **kits**, **individual pieces**, and the material cycle closing remain in RF-OP-* and RF-SP-*.

---

## Glossary of this contract


| UI term | Meaning | Where it lives |
| --- | --- | --- |
| Family | Instrument type that is counted / detected | `instrument_family` + `family_example` |
| Piece | Physical stock unit (**SPD**, not Admin) | `instrument` |
| Kit (recipe) | Quantities per family, versioned (**SPD**, not Admin) | `kit` + `kit_item` + `procedure_kit` |
| Active model | Detector that **new** sessions will take at Start | `yolo_model.active = true` (only one TRUE) |
| Model pin | Detector **frozen** in an already started session | `work_session.yolo_model_id` |
| Room hold | Human-confirmed shortfall; SPD does not enter yet | `cat_session_status.code = or_hold` |
| Suggested resolution | OR account (found / not found / searching) | `discrepancy.suggested_resolution` |
| Release to SPD | The **OP** sends the case to SPD review | status `correction_required` + `or_released_at` |
| Room assignment | SdP ↔ operating room | `user_operating_room` |
| Notice (library) | Hospital's versioned PDF/HTML | `privacy_notice_version` |
| Session agreement | Linking a notice to **this** session (still SPD's) | `session_processing_agreement` |

**RACI catalog vs operation (v1)**

1. **Admin** creates, edits, and **deactivates** families (with examples), procedures/selection catalogs, notices, users, roles, rooms, stations, and models. Deactivated items are **hidden** from OP/SPD/App; Admin does list them.
2. **SPD** registers **pieces** (`instrument`), moves the cycle (`instrument_cycle_event`), and **creates/manages kits** (RF-SP-09). Uses Admin's families and procedures when scheduling (RF-SP-02). Does not publish weights.
3. There is no physical DELETE if there are FKs; only `active = false`.
4. FHIR / `integration_client`: **considered in the plan, not designed**.

---

## Roles and access


| Code | Name | Client | Can |
| --- | --- | --- | --- |
| `it_admin` | IT Administrator | Admin web portal | Families, catalogs, IAM, topology, models, access audit, ARCO stub |
| `or_charge` | Floor Supervisor (SdP) | App | Sees all rooms (their own first); **notifications only** for linked rooms; comments on holds; does **not** close the SPD cycle or edit the catalog |
| `station_operator` | Operator | Station | Counting; documents hold and **releases to SPD** (RF-OP-07) |
| `spd_supervisor` | SPD Supervisor | SPD Portal | Scheduling, kits, pieces, post-release review, material cycle |

**Fixed rules**

1. JWT/Redis per DS01. The Backend applies the role before writing to PostgreSQL.
2. Admin does not validate counts or close business `work_session`s.
3. The App does not open WSS to the vision-worker. Session live view = PG reads (poll or WS to the Backend) + already stored video evidence.
4. App lists: **all** rooms, ordered with the SdP's `user_operating_room` ones **first**. **In-app notifications only** if the hold/session belongs to a room linked to that user.

---

## Session states (cross-reference with OP/SPD)

Codes in `cat_session_status`. Admin/App do **not** invent others.


| code | Who triggers it | Who sees it as a queue |
| --- | --- | --- |
| `scheduled` | SPD schedules | App (upcoming) |
| `in_progress` | OP Start | App home / sessions |
| `or_hold` | OP confirms human-verified **shortfall** | App alerts + OP-07; **SPD does not enter** |
| `correction_required` | OP **releases** the hold to SPD (RF-OP-07) | SPD review queue |
| `awaiting_spd_review` | OP closes **without** shortfall | SPD clean closing queue |
| `closed` / `aborted` | SPD or controlled abort | History |

---

## Technical channels


| Channel | Use in this contract |
| --- | --- |
| HTTPS → Web Backend | All of Admin and all of the App |
| WSS → vision-worker | Not used by Admin or App |
| Object storage | `.pt` weights, `family_example` images, PDF/HTML notices, session evidence (read-only for App) |
| Mongo `checkpoint_frame` | App reads **already stored** evidence/video; does not write |

---

# PART A — IT ADMINISTRATOR

The administrator logs in with `it_admin`. Home = metrics dashboard. Deactivated items remain visible **only** here.

---

## RF-AD-01 · Metrics dashboard

### Actor

`it_admin`

### How the user gets there

Admin portal login → Dashboard (sidebar HOME).

### What the interface shows

Read-only KPIs, for example:

- **Catalog:** active/inactive families, procedures, models (which one is `active`).
- **System:** Auth/PG/Redis/Mongo/storage reachable (health; without faking microservices).
- **Operation:** sessions by status (`in_progress`, `or_hold`, `correction_required`, `awaiting_spd_review`), users with `active = false`.

No buttons that close clinical cases. It does **not** own piece counts or kits (those KPIs, if they appear, are read-only).

### What the user does

Reviews; navigates to a detailed RF-AD-*.

### What happens in the system

`GET` → SELECT aggregates. No writes.

### Stores

PostgreSQL read. Health: ping of app services.

### Configuration

KPI color thresholds.

### Output

Navigation to families / IAM / models / audit.

### Errors

If a store does not respond, the system KPI shows an error; the rest of the dashboard is not blocked.

---

## RF-AD-02 · Instrument families and examples

### Actor

`it_admin`

### How the user gets there

Sidebar CATALOGS → Instrument families. Create/edit from the list.

### What the interface shows

List (includes inactive ones, with badge). Form: `code` (the one SPD uses when registering a **piece**), `name`, `category_id`, `identify_text`, `classify_text`, `function_text`, `active`. **`family_example` table:** image via `media_asset`, `example_text`, `sort_order`.

Buttons: Save, Deactivate / Reactivate. There is no Delete if `instrument`, `kit_item`, or `model_class` exist.

**Admin does not register individual instruments.** That is SPD (`instrument` + cycle).

### What the user does

Creates or corrects the master record of the **type** (family) and its educational photos.

### What happens in the system

1. INSERT/UPDATE `instrument_family`.
2. For each example: upload file to storage, INSERT `media_asset`, INSERT/UPDATE `family_example`.
3. Deactivate: `active = false` (OP/SPD/App stop offering it for new registrations; already frozen sessions do not change).
4. `access_audit` action `catalog.family.upsert` / `catalog.family.deactivate`.

### Stores

PostgreSQL + object storage (images).

### Configuration

MIME type and max size of an example (default image, 5 MB).

### Output

Families list.

### Errors

Duplicate code (`uk_instrument_family_code`). Deactivating the family of the active model: warn and require confirmation.

---

## RF-AD-03 · Procedures and selection catalogs

### Actor

`it_admin`

### How the user gets there

CATALOGS → Procedures (and sub-screens: phases / categories / reasons).

### What the interface shows

CRUD of `cat_procedure_type`. Where the mockup allows: `cat_operation_phase` (order, whether the count is mandatory), `cat_instrument_category`, `cat_discrepancy_reason`. OP and SPD only **choose** these values.

### What the user does

Maintains the library that feeds RF-SP-02 and the correction dropdowns.

### What happens in the system

INSERT/UPDATE catalogs (unique `code`). Do not delete referenced codes (`ON DELETE RESTRICT`); hide or use `active` if it exists. `access_audit`.

### Stores

PostgreSQL.

### Configuration

None.

### Output

List of the edited catalog.

### Errors

Duplicate code. Removing a procedure with linked `operation`s: block or only hide.

---

## RF-AD-04 · Privacy notice library

### Actor

`it_admin`

### How the user gets there

Administration / Privacy notices.

### What the interface shows

List of `privacy_notice_version` (`version`, `effective_at`, `active`, link to the document). Creation: upload PDF/HTML, version, effective date, mark as active.

**SPD does not own this library.** In RF-SP-02 it chooses an active version and **links the notice to ONE session** (`session_processing_agreement`).

### What the user does

Publishes or deactivates versions.

### What happens in the system

1. Upload to object storage; SHA-256; INSERT `privacy_notice_version`.
2. Multiple active versions allowed by default (`privacy_notice_single_active = false`); SPD chooses.
3. `access_audit` `privacy.notice.publish`.
4. **No** INSERT `session_processing_agreement`.

### Stores

PostgreSQL + object storage (`privacy-notices/`).

### Configuration

`privacy_notice_max_mb`, MIME pdf/html (same defaults as RF-SP-02).

### Output

List of versions.

### Errors

Duplicate hash: warn. Sessions already agreed do not change version.

---

## RF-AD-05 · User management

### Actor

`it_admin`

### How the user gets there

ADMINISTRATION → Users.

### What the interface shows

List (active and inactive): name, email, roles, status, last login. Create/edit: `name`, `email`, role(s) via `user_role` (`station_operator` / `spd_supervisor` / `it_admin` / `or_charge`), initial password or reset, `active`. Deactivate modal.

Room assignment (`user_operating_room`) on the `or_charge` user: **is used**. The App lists all rooms (the user's own first) and **notifies only** for linked ones.

### What the user does

Creation, resets, deactivation, linking SdP ↔ operating room. Never sees the password in clear text after saving.

### What happens in the system

INSERT/UPDATE `user` (`password_hash`, `password_updated_at`). INSERT/DELETE `user_role`. INSERT/DELETE `user_operating_room`. Deactivate: `active = false` (login denied). `access_audit`. Invalidate the deactivated user's Redis refresh token if it exists.

### Stores

PostgreSQL + Redis (optional blacklist).

### Configuration

Password length policy.

### Output

List.

### Errors

Duplicate email. Removing the last active `it_admin`: block.

---

## RF-AD-06 · Role management

### Actor

`it_admin`

### How the user gets there

ADMINISTRATION → Roles.

### What the interface shows

`code`, `description`, `institution_id`. The four product codes are fixed in v1: `station_operator`, `spd_supervisor`, `it_admin`, `or_charge`.

### What the user does

Edits the **description**. Creating new codes outside that list is out of scope for v1.

### What happens in the system

UPDATE `role.description`. INSERT of non-product codes: reject in v1. `access_audit`.

### Stores

PostgreSQL.

### Configuration

None.

### Output

List.

### Errors

Changing the `code` of a role in use: blocked.

---

## RF-AD-07 · Operating rooms and capture stations

### Actor

`it_admin`

### How the user gets there

Configuration → Operating rooms / Capture stations.

### What the interface shows

CRUD `operating_room` (`code`, `name`, `active`). CRUD `capture_station` (`name`, `room_id`, `roi` JSON, `active`).

### What the user does

Registers or deactivates spaces and camera posts.

### What happens in the system

INSERT/UPDATE. Deactivating a room does not delete historical sessions. `access_audit`.

### Stores

PostgreSQL.

### Configuration

ROI validation (keys `x,y,w,h,frame_w,frame_h`).

### Output

Configuration overview.

### Errors

Station without a room: block. Duplicate room code within the institution.

---

## RF-AD-08 · Vision models (create, mapping, activate)

### Actor

`it_admin`

### How the user gets there

ADMINISTRATION → Vision models. New / Edit / Activate.

### What the interface shows

List: `version_tag`, `architecture`, `model_size`, checksum, `active`, `published_at`.

**Creation (two steps):**

1. Upload the weights file (`.pt`). The Backend computes SHA-256, saves `media_asset`, tries to extract `names` / `nc` and YOLO metadata. If parsing fails, it asks for `names.yaml` or a JSON `{id: name}`.
2. Mapping table: `yolo_class_id` → `instrument_family`. Autocomplete if the name matches `family.code` or `name`. **Active** catalog families that the model does **not** include: **warning overlay**; Admin must **confirm** to publish. Model classes without a family: map them or reject that class.

Activate: modal "this model will be the active one for **new** sessions". `in_progress` / `or_hold` sessions keep `work_session.yolo_model_id`.

There is no Delete; deactivate = `active = false` (v1: do not leave zero active models if there are vision stations).

### What the user does

Uploads, maps, confirms the missing-families overlay, publishes, activates.

### What happens in the system

1. INSERT `yolo_model` (`version_tag`, `media_asset_id`, `checksum`, `architecture`, `model_size`, `active = false`).
2. INSERT `model_class` for each mapped class.
3. Activate: transaction that UPDATEs the previous one `active = false` and the new one `active = true`.
4. Does **not** notify the vision-worker for open sessions.
5. `access_audit` `vision.model.publish` / `vision.model.activate`.

### Stores

PostgreSQL + object storage.

### Configuration

Max `.pt` size; extensions; text of the missing-families overlay.

### Output

List of models.

### Errors

Checksum already exists: reject. Activate without `model_class`: block. Parse failed without YAML/JSON: block step 2.

---

## RF-AD-09 · Access audit log

### Actor

`it_admin`

### How the user gets there

ADMINISTRATION → Audit log.

### What the interface shows

Table of `access_audit`: `occurred_at`, actor, `action`, `resource_type`, `resource_id`, `outcome`, IP. Filters by date, action, user.

This is **not** the clinical timeline (`count_event`).

### What the user does

Reviews. No row editing (append-only).

### What happens in the system

Filtered SELECT. No UPDATE/DELETE.

### Stores

PostgreSQL.

### Configuration

Pagination.

### Output

Same screen.

### Errors

Empty date range: do not query.

---

## RF-AD-10 · Institution and application configuration

### Actor

`it_admin`

### How the user gets there

Configuration overview / Edit institution.

### What the interface shows

`institution.name`, `active`. Application parameters visible in the UI (KPI thresholds, notice MIME types).

### What the user does

Corrects the name; does not delete the demo institution.

### What happens in the system

UPDATE `institution`. `access_audit`.

### Stores

PostgreSQL.

### Configuration

N/A (this screen *is* the config).

### Output

Overview.

### Errors

Setting `active = false` on the only institution: block in v1.

---

## RF-AD-11 · ARCO requests (demo stub)

### Actor

`it_admin`

### How the user gets there

Legal / ARCO sidebar.

### What the interface shows

List of `privacy_request` if there are rows: type, status, patient, dates. Read-only detail.

Fixed demo text: *record prepared; v1 does not execute the right.* The row types should resemble real hospital ARCO operations (access, rectification, cancellation, opposition) so the screen is understandable in the demo — research that institutional vocabulary; do **not** implement the flow.

No Execute deletion / Export bundle buttons.

### What the user does

Opens the screen for the demo.

### What happens in the system

SELECT. No purge jobs.

### Stores

PostgreSQL read.

### Configuration

None.

### Output

List.

### Errors

Empty table: empty state, not an error.

---

# PART B — FLOOR SUPERVISOR APP

Tabs: **Home** (Inicio), **Discrepancies/alerts**, **Sessions**, **Staff** (Personal). The hold detail (RF-AP-03) is a drill-in, not an extra tab.

The system does **not** raise alerts through AI alone. The alert exists when a human confirmed a shortfall (`or_hold`).

**Rooms:** the SdP **sees all**. List order puts that user's `user_operating_room` rooms **first**, then the rest. **In-app notification (and new-alert badge) only** if `work_session` → `operation.room_id` (or station/`capture_station.room_id`) is in the rooms linked to that `or_charge`.

---

## RF-AP-01 · Home (floor KPIs)

### Actor

`or_charge`

### How the user gets there

App login → Home tab.

### What the interface shows

KPIs: `in_progress` sessions; `or_hold` holds; already released SPD pending items; shortcut to holds. "My rooms" figures may be highlighted; the global listing remains visible.

### What the user does

Reviews; enters a case.

### What happens in the system

SELECT aggregates over all rooms. Order: linked ones first.

### Stores

PostgreSQL.

### Configuration

None.

### Output

AP-02 / AP-03 / AP-04.

### Errors

Empty: zeros, not an error.

---

## RF-AP-02 · Alert queue (`or_hold`)

### Actor

`or_charge`

### How the user gets there

Discrepancies/alerts tab.

### What the interface shows

`or_hold` sessions (human-verified shortfall). Room, procedure, operator, missing families, age. Badge "not released to SPD".

List: all rooms, **their own first**. **Push/in-app notification only** for holds in linked rooms (no SMS).

### What the user does

Opens a case (RF-AP-03). Does not resolve piece loss.

### What happens in the system

SELECT `work_session` status `or_hold` + `discrepancy.resolved = false`. JOIN room. Notification: filter `user_operating_room`.

### Stores

PostgreSQL.

### Configuration

TTL of the notification badge.

### Output

RF-AP-03.

### Errors

Hold already released: remove from the queue; toast "already in SPD review".

---

## RF-AP-03 · Hold detail (for awareness only; does not release to SPD)

### Actor

`or_charge` (read/comment). The **owning OP** releases in RF-OP-07.

### How the user gets there

From AP-02 or from a session on hold.

### What the interface shows

- Record (status, expected vs validated, shortfalls).
- `count_event` timeline.
- Evidence: **already stored** clips/checkpoints (signed URL, MP4). **No** camera stream.
- Comment thread (OP and SdP).
- Suggested resolution if the OP already entered it.
- **Comment** button. There is **no** "Send to SPD", "Mark lost", or "Confirm and close".

The SdP is informed and documents. **Releasing to SPD is done by the OP** (RF-OP-07).

### What the user does

Reads evidence, writes a comment, waits for the OP to release.

### What happens in the system

Comment: INSERT `count_event` `or_hold_comment` (`user_id`, `payload` `{discrepancy_id, text, actor_role: or_charge}`). Does **not** change status. Does **not** fill `or_released_*`.

### Stores

PostgreSQL write (event). Mongo + storage **read**.

### Configuration

Minimum comment length (default 10).

### Output

Stays on the detail or returns to the queue.

### Errors

Commenting on a session that is no longer `or_hold`: block. Vision WSS: do not open.

---

## RF-AP-04 · Sessions (live count table, no camera)

### Actor

`or_charge`

### How the user gets there

Sessions tab.

### What the interface shows

Filters: scheduled, in progress, `or_hold`, procedure, room. Lists **all** rooms, linked ones first.

Record: data, inventory, timeline. **Live count table:** expected vs detected and `confidences` per box as `count_event` is persisted (poll or Backend WS). **No** camera feed. **Yes** to already stored video evidence (signed URL, MP4).

### What the user does

Filters and observes.

### What happens in the system

GET list/detail. No writes except navigation. Notification of change to hold: only if the room is linked.

### Stores

PostgreSQL. Mongo/storage read of evidence. No DS10 WSS.

### Configuration

Poll interval (default 3 s).

### Output

AP-03 if the status is `or_hold`.

### Errors

Aborted session: read-only historical record.

---

## RF-AP-05 · Staff (physicians and users)

### Actor

`or_charge`

### How the user gets there

Staff tab.

### What the interface shows

Directory of **`physician`** and **`user`** (without hashes). For each person:

- record (specialty / role, minimal contact);
- history of sessions they participated in;
- **scheduled** sessions;
- if **currently in a session**: which one and **where** (room).

A surgeon **may not have** a login account. Does not edit IAM (RF-AD-05).

### What the user does

Reviews; shortcut to the related session.

### What happens in the system

SELECT `physician` + `physician_specialty` + `operation_physician` + `user` + `work_session` / `operation` / `operating_room`. No password. PHI: do not list `patient.display_name` in this directory (it is shown in an already authorized session record).

### Stores

PostgreSQL.

### Configuration

Nothing critical.

### Output

Detail → RF-AP-04.

### Errors

Empty: empty state.

---

# PART C — OUT OF SCOPE (explicit)

- Kits (recipe CRUD): RF-SP-09.
- Individual instruments and material cycle: SPD.
- Families and examples: RF-AD-02.
- FHIR machine clients / `integration_client` (considered, **not designed**).
- Executing ARCO rights (only RF-AD-11 list stub).
- ML training or publishing from a dataset.
- Push/SMS. Camera stream in the App.
- An App that schedules sessions, edits kits, activates models, or **releases a hold to SPD**.
- Admin validating counts or closing `work_session`.

---

*End of v1 Admin + App contract. Any screen or DB write change must update the corresponding RF here and, if it affects counting, kits, or review, also [rf_operador_supervisor_spd.md](rf_operador_supervisor_spd.md).*
