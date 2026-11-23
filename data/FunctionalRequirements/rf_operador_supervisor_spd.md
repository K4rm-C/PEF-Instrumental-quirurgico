# Formal Functional Contract · Station Operator and SPD Supervisor

## How to read this document

Each functional requirement (RF) follows the same template:

1. **Identifier and name**
2. **Actor** (who can execute it)
3. **How the user gets there** (navigation / entry condition)
4. **What the interface shows** (fields, buttons, exact expected messages)
5. **What the user does**
6. **What happens in the system** (order of steps: Backend, SQL on tables, vision-worker, evidence-worker, Mongo, storage)
7. **When each store is written or read**
8. **Configuration** (parameters that someone should be able to adjust without redeploying business logic, if possible)
9. **Output / next screen**
10. **Errors and blocks** that the UI must handle

**Database write convention**


| Who writes                 | What it writes                                                                                     | How                                                                                                                                                                      |
| -------------------------- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Web Backend**            | Business facts in PostgreSQL                                                                       | Parameterized SQL through the application's repository/ORM. There are **no** mandatory stored procedures in v1; if the team adds SPs later, the RF does not change meaning. |
| **Vision-worker**          | Only perception WSS messages; it does **not** own PostgreSQL                                       | Inference + DS10 JSON sent to the station                                                                                                                                |
| **Evidence-worker**        | Object storage + `media_asset` row (via Backend or adapter) + Mongo `checkpoint_frame` document    | Asynchronous queue; does not block the camera                                                                                                                            |
| **Client (web portal)**    | Never opens a JDBC/SQL connection or Mongo/Garage credentials                                      | HTTPS to the Backend and WSS to the vision-worker only                                                                                                                   |


**Outside this contract:** user/role/password administration, ARCO, FHIR clients, bulk imports, master catalogs, and the Floor App (those live in [rf_admin_app.md](rf_admin_app.md)).

---

## Operational glossary (screen language)


| UI term                     | Meaning                                                         | Where it lives in data                                                                                     |
| --------------------------- | --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Session / Counting session  | Counting period tied to an operation                            | Table `work_session`                                                                                       |
| Operation / Procedure case  | Scheduled surgical act (room, patient, physicians, type)        | Table `operation` (+ `operation_patient`, `operation_physician`)                                           |
| Kit                         | Recipe of families and instruments with quantities              | `kit` + `kit_item`                                                                                         |
| Expected inventory          | Frozen list of what **must** be present in this session         | `expected_inventory`                                                                                       |
| Detected / AI detected      | Quantity suggested by the model                                 | Comes from the vision-worker; reflected in `count_event.detected_quantity` when the Backend persists the fact |
| Validated count             | Quantity accepted by a human                                    | Corrections in `human_correction` and/or manual `count_event`                                              |
| Discrepancy                 | Difference between expected and validated/detected that requires attention | `discrepancy`                                                                           |
| Caution / temporal marker   | Prolonged absence that later recovers on its own                | `discrepancy` with automatic resolution + evidence                                                         |
| Phase / Counting phase      | Clinical stage of the count (pre, intra, closing, …)            | `work_session.current_phase_id` → `cat_operation_phase`                                                    |
| Station / Capture station   | Camera post                                                     | `capture_station`                                                                                          |
| Evidence clip               | Visual burst of the moment (MP4)                                | File in Garage (S3) + `media_asset` + Mongo `checkpoint_frame`                                             |
| Privacy notice              | Versioned legal text the hospital uses for data processing      | Table `privacy_notice_version` (`document_uri`, `content_sha256`, `version`, `active`)                     |
| Processing agreement        | Confirmation that **this** session has an accepted notice       | Table `session_processing_agreement` (max. 1 row per `session_id`)                                         |
| Capture mode                | With vision (camera + AI) or manual count only                  | **Frozen at Start Session**: `vision` if there is an agreement; `manual_no_privacy` if not                 |


**Formal privacy ↔ capture rule (v1)**

1. Scheduling a session does **not** yet require the agreement; the session can remain `scheduled` with no row in `session_processing_agreement`.
2. The supervisor **must be able to** upload a new PDF/HTML or **confirm** an already active version of the notice and link it to the session (see RF-SP-02). Without that, the session is still valid.
3. When **Start Session** is pressed, the Backend checks whether a `session_processing_agreement` exists for that session:
  - **Yes** → `vision` mode → live screen with camera (RF-OP-04).
  - **No** → `manual_no_privacy` mode → **no** WSS is opened, there is **no** capture or count evidence clip; the operator enters the manual progress view (RF-OP-04M) until closing with manually reported quantities.
4. The mode is written to `work_session` (application field or key in JSON `payload`: `capture_mode`) and **does not change** even if SPD adds the agreement after Start. Reason: avoid mixing half a session without camera with half using AI.
5. `purpose_quality_ops` in the agreement is always `TRUE` (DB CHECK). `purpose_model_improvement` is an opt-in by the supervisor when confirming.

---

## Roles and access


| Role code          | Name                      | Client                                         | Can                                                                                                                               |
| ------------------ | ------------------------- | ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `station_operator` | Station Operator          | Web portal / station on the room PC            | Only assigned sessions; begin/continue; live or manual progress without camera; validation/closing; document hold and release to SPD |
| `spd_supervisor`   | SPD Supervisor            | Web portal                                     | Schedule sessions **including privacy notice**; view all; dashboard; **post-release** review; extras; kits; metrics               |
| `or_charge`        | Floor Supervisor (SdP)    | Floor App                                      | `or_hold` queue, comments, and evidence reading. Does **not** close the SPD cycle. Contract: [rf_admin_app.md](rf_admin_app.md)   |
| `it_admin`         | IT Administrator          | Admin Portal                                   | Catalogs, IAM, models. Contract: [rf_admin_app.md](rf_admin_app.md)                                                               |


**Fixed access rules**

1. After login, the Backend issues a session/JWT (Redis per DS01).
2. Every business request carries identity; the Backend applies the role before touching PostgreSQL.
3. The operator has **no** global metrics screen or kit editing.
4. The vision-worker does **not** close sessions or accept discrepancies.

---

## Official session states (`cat_session_status.code`)

These codes are the state contract. The UI shows the Spanish/English label from the mockup; the Backend stores the `code`.


| code                  | UI label               | Who triggers it                                                                                           |
| --------------------- | ---------------------- | --------------------------------------------------------------------------------------------------------- |
| `scheduled`           | Scheduled / Programada | Supervisor creates/saves the session                                                                      |
| `in_progress`         | In Progress            | Operator presses **Start Session**                                                                        |
| `or_hold`             | OR hold                | Operator confirms a human-verified **shortfall** (validated/reported < expected). **Extras do not** trigger this state |
| `awaiting_spd_review` | Awaiting Review        | Operator finishes validation **without** shortfalls (match or only documented extras)                     |
| `correction_required` | Correction Required    | Operator **releases** the hold to SPD (RF-OP-07) with a suggested resolution                              |
| `closed`              | Closed                 | SPD Supervisor confirms recovery / closes the case                                                        |
| `aborted`             | Aborted                | Controlled cancellation (SPD or operator depending on permission)                                         |


---

## Technical channels (always the same)


| Channel                                     | Use                                                                                                                 |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| HTTPS → Web Backend                         | Login, lists, start, phase change, validation, closing, SPD scheduling, **privacy notice**, kits, metrics           |
| WSS → vision-worker                         | Only frames and detections (DS10) while the session is `in_progress` **and** `capture_mode = vision`                |
| Queue `pef-evidence-queue` → evidence-worker | Assemble burst MP4, upload to storage, write Mongo (**not** used in `manual_no_privacy`)                           |


---

# PART A — STATION OPERATOR

The operator opens the station app, logs in with their `station_operator` user, and **goes directly** to the session list. There is no metrics dashboard for this role.

---

## RF-OP-01 · My sessions list

### Actor

`station_operator`

### How the user gets there

1. Successful login in the station app.
2. The app automatically navigates to **Sessions** (the operator's only home).

### What the interface shows

Table with one row per session assigned to the operator (`work_session.user_id` = current user).


| UI column              | Data source                                                                                                                                     |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Session ID             | Visual prefix `WS-` + short identifier derived from `work_session.id` (or business code if added later; v1 may show a short UUID)               |
| Procedure              | Procedure type name + room (from `operation` / `cat_procedure_type` / `operating_room`)                                                         |
| Kit type               | `kit.name` via `work_session.kit_id`                                                                                                            |
| OR                     | Code/name of `operating_room`                                                                                                                   |
| Created / Scheduled at | Scheduling date-time (session creation timestamp or `operation`, depending on what is stored when scheduling)                                   |
| Status                 | Badge according to `cat_session_status.code`                                                                                                    |
| Privacy / Capture      | If there is a `session_processing_agreement` (or `capture_mode=vision` after Start): "Notice OK · Vision"; if not: "No notice · Manual" (different badge) |
| Discrepancies          | Number of `discrepancy` rows with `resolved = false` for that session                                                                           |
| Action                 | Only the allowed buttons (below)                                                                                                                |

**Operator filters (optional and local to "my sessions"):** text search; filter by status. There is **no** "all hospital rooms" selector.

**Row action buttons (the only ones allowed)**


| Button            | Visible when status is      | Action                                                                                                                                                                    |
| ----------------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **View details**  | any in the list             | Opens a read-only panel/page with the case summary                                                                                                                        |
| **Begin**         | `scheduled`                 | Goes to RF-OP-03 (pre-start confirmation)                                                                                                                                 |
| **Continue**      | `in_progress`               | According to frozen `capture_mode`: `vision` → RF-OP-04 (or RF-OP-05/06 if already in closing); `manual_no_privacy` → RF-OP-04M (or RF-OP-06M if already in quantity reporting) |
| **Continue hold** | `or_hold`                   | RF-OP-07 (document and release to SPD)                                                                                                                                    |


Do not show in this list: Close Session, Review Count, or supervisor actions.

### What the user does

Reviews the list; chooses View details, Begin, or Continue.

### What happens in the system

1. App → `GET /api/.../my-sessions` (HTTPS) with JWT.
2. Backend verifies role `station_operator`.
3. Backend runs a **SELECT** (SQL) joining:
  - `work_session`
  - `cat_session_status`
  - `operation`, `operating_room`, `cat_procedure_type`
  - `kit`
  - `COUNT(discrepancy)` where `session_id` and `resolved = false`
  - filter `work_session.user_id = :current_user_id`
4. JSON response → renders the table.
5. **No** WSS. **No** table writes.

### Configuration

- Date/time format of the time column (hospital time zone).  
- Length of the displayed short ID.

### Output

Same screen, or detail (RF-OP-02), or Begin (RF-OP-03), or Continue (RF-OP-04/05).

### UI errors

- No sessions: message "You have no assigned sessions".  
- Network/auth error: message to retry / return to login.

---

## RF-OP-02 · View brief detail (read-only)

### Actor

`station_operator`

### How the user gets there

**View details** button in RF-OP-01.

### What the interface shows

**Read-only** page or modal panel with:

- Session ID, status  
- Procedure, OR, planned station  
- Listed patient(s) and physician(s)  
- Kit and **planned** expected inventory table (if still `scheduled`: from the scheduling draft; if already started: from `expected_inventory`)  
- Privacy notice status:
  - If a `session_processing_agreement` exists: notice version (`privacy_notice_version.version`), `agreed_at` date, read-only link to `document_uri`, and purposes (`quality_ops` always yes; `model_improvement` yes/no).
  - If it does not exist: clear message *"No privacy notice agreement. On start, this session will enter manual count mode (no camera or AI)."*
- Number of open discrepancies  
- No buttons to edit kit, staff, or stock

Buttons: **Close/Back** to the list; if applicable, **Begin** / **Continue** same as RF-OP-01.

### What happens in the system

`GET` detail → Backend **SELECT** from the same tables + `expected_inventory` or scheduling snapshot + `operation_patient` / `operation_physician` / `physician` / `patient` + **LEFT JOIN** `session_processing_agreement` → `privacy_notice_version`. No writes.

### Output

Returns to RF-OP-01 or advances to Begin/Continue.

---

## RF-OP-03 · Pre-start confirmation (Begin > Start Session)

### Actor

`station_operator`  
(The supervisor uses the same visual template when **scheduling**; see RF-SP-02. Here the operator does **not edit** the scheduling fields.)

### How the user gets there

**Begin** from a session in `scheduled` state.

### What the interface shows (section by section)

**Section 1 — Session setup (all read-only for the operator)**  

- Operator (name of the assigned user)  
- Procedure  
- Patient  
- Physician / Surgeon  
- Operating room  
- Kit  
- Initial counting phase (first active phase of the procedure)  
- Scheduled date & time  
- Capture station

**Section 2 — Expected inventory preview**  
Table: Instrument (family name) | Expected quantity.  
Total row: sum of quantities.  
Fixed banner: *"Expected inventory will be frozen when the session starts."*

**Section 3 — Instrument readiness**  
Mandatory checkbox: *"I confirm the instrument set is physically present and matches the expected inventory."*  
Must be checked to enable Start.

**Section 4 — Privacy notice (read-only for the operator)**  


| Condition when opening Begin          | What the operator sees                                                                                                                                                                                                                                                               |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `session_processing_agreement` exists | Green badge "Notice confirmed · vision capture enabled"; notice version; "View document" link; purposes text.                                                                                                                                                                        |
| No agreement                          | Amber badge "No privacy notice · NO camera or AI on start"; fixed text: *"You will be able to confirm the instruments and start. Afterwards you will report the quantities by hand and close the session. If any quantity is below the expected amount, a discrepancy will be opened."* |


The operator does **not** upload or accept the notice on this screen (that belongs to the supervisor in RF-SP-02). The operator is only informed of the mode the session will have at Start.

**Footer**  

- Cancel → returns to RF-OP-01 with no changes.  
- **Start Session** (enabled only with the instrument checkbox).

### What the user does

Reads the information scheduled by SPD, verifies the physical tray, checks the checkbox, presses **Start Session**.

### What happens in the system when Start Session is pressed

Everything in one Backend **PostgreSQL transaction**, except the evidence steps (only in vision mode):

1. Backend revalidates: session exists, status = `scheduled`, `user_id` = current operator.
2. Backend determines `capture_mode`:
  - **SELECT** `session_processing_agreement` WHERE `session_id = :id`
  - If there is a row → `capture_mode = 'vision'`
  - If there is no row → `capture_mode = 'manual_no_privacy'`
3. **UPDATE** `work_session`:
  - `status_id` → code `in_progress`  
  - `started_at` = now()  
  - `station_id` = displayed station  
  - `current_phase_id` = initial phase  
  - `phase_changed_at` = now()  
  - `updated_at` = now()  
  - Persists `capture_mode` (application column or `payload->>'capture_mode'`; v1 contract: it is **mandatory** that it remains queryable after Start)
  - **If** `capture_mode = vision`: **UPDATE** `work_session.yolo_model_id` = `yolo_model.id` of the record with `active = true`. This pin does **not** change if Admin activates another weight later. The worker and the UI of already-open sessions read this FK, **not** the global active model. `scheduled` sessions leave `yolo_model_id` NULL until Start.  
  - **If** `manual_no_privacy`: `yolo_model_id` remains NULL.
4. **Freeze expected inventory** — **INSERT** into `expected_inventory` one row per family:
  - `session_id`, `family_id`, `expected_quantity`  
  - `source = 'kit_snapshot'` for kit lines  
  - `source = 'manual'` for lines that SPD added/removed when scheduling (the set already arrives resolved from RF-SP-02)  
  - If earlier drafts of the same session already existed, they are replaced in a controlled way (DELETE+INSERT or UPSERT) **only at this Start**; afterwards they become immutable except for RF-SP-04.
5. If there are serialized pieces pre-assigned to the case cart: **INSERT** `instrument_reservation` (`active = true`, `operation_id`, `instrument_id`) and, if applicable, **UPDATE** `instrument.cycle_status_id` to a reserved/in-use state according to the cycle catalog.
6. **INSERT** `count_event`: opening event type (`cat_event_type.code` = e.g. `session_start`), `session_id`, `user_id`, `occurred_at`, JSON `payload` with `phase_id`, `phase_code`, `capture_mode`, and `model_version` **only if** `capture_mode = vision`.
7. **INSERT** `access_audit`: actor operator, action `session.start`, outcome success, resource `work_session`.

**Only if** `capture_mode = vision` **(steps 8–10):**

1. Backend **enqueues** a job in `pef-evidence-queue`: `{ session_id, reason: "start", phase_id, frame_snapshot_ref }`.
2. Evidence-worker (separate process):
  - Freezes N frames from the buffer or from the start capture  
  - Encodes H.264 MP4 (no audio); if it fails, a JPEG  
  - Uploads to Garage (S3-compatible)  
  - INSERT `media_asset` (`kind = 'mp4'` or `'jpeg'`; SQL via Backend/adapter)  
  - INSERT Mongo document `checkpoint_frame` (`session_id`, `reason`, `phase_id`/`phase_code`, `media_asset_id`, detections if any, timestamp)
3. Station opens a **WSS** `session.bind` to the vision-worker with `session_id`, `station_id`, and the `yolo_model_id` **pinned** in `work_session` (not the global active model).

**If** `capture_mode = manual_no_privacy`**:**

1. **Do not** enqueue camera evidence.
2. **Do not** open WSS.
3. **Do not** call the vision-worker.
4. Reason: without a `session_processing_agreement` there is no documented legal basis for processing frames/evidence of the tray; counting continues through human reporting.

**Reason for freezing expected:** what is compared later in live mode or in manual closing must be exactly what was confirmed at the start; editing the master kit tomorrow must not alter this session.

### Configuration

- Text of the checkbox, the expected banner, and the privacy badges (i18n).  
- Number of frames / duration of the `start` clip (evidence-worker) — vision mode only.  
- Timeout to fail the WSS bind (vision mode only; the session may already be `in_progress`).

### Output


| `capture_mode`      | Next screen                           |
| ------------------- | ------------------------------------- |
| `vision`            | RF-OP-04 Live count                   |
| `manual_no_privacy` | RF-OP-04M Manual progress without capture |


### UI errors

- Checkbox not checked: Start disabled.  
- Session is no longer `scheduled`: message and return to the list.  
- SQL failure: full rollback; message "Could not start; please retry".  
- Evidence failure (vision only): Start **can** still complete; a pending-evidence warning is logged (the camera does not depend on the clip).

---

## RF-OP-04 · Live count (tray + expected)

### Actor

`station_operator`  
Session in `in_progress` **and** `capture_mode = vision`.

### How the user gets there

After Start Session with a privacy agreement, or **Continue** from the list when `capture_mode = vision`.

### What the interface shows

**Context header (always visible)**  
Session ID, Procedure, Patient, Physician, OR, Kit, Station, **In Progress** badge, current phase, **Vision / Notice OK** badge.

**Left panel — Overhead capture**  

- Video/stream or current frame of the tray.  
- Boxes drawn with the family name (do not invent names outside the `model_class` mapping).  
- Timestamp of the last frame shown.  
- AI status: e.g. "Detecting…" / "AI Detection active".

**Right panel — Expected vs detected (recent window)**  
Table for each row of `expected_inventory`:


| Column     | Content                                                                                                                                             |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Instrument | Name of `instrument_family`                                                                                                                         |
| Expected   | Frozen `expected_quantity`                                                                                                                          |
| Detected   | Quantity aggregated over the **sliding window of the last N seconds** (default **5 s**)                                                             |
| Difference | Detected − Expected (or Expected − Detected; the UI must fix one convention and display it the same on all screens: **v1 = Detected − Expected**)  |
| Confidence | Aggregated confidence of that family's boxes in the window (average)                                                                                |
| Result     | Match / Missing / Extra / Possible miss according to thresholds                                                                                     |


Totals: Total Expected | Total Detected (window).

**Controls**  

- **Change phase** (opens selector for the next valid phase).  
- **Finish counting / Proceed to closing summary** (stops live and goes to RF-OP-05).  
- Fixed text: AI results are suggestions.

### What the user does

Observes the tray and the panel; changes stage when the procedure requires it; when the assisted count is done, moves on to detection closing.

### What happens in the system (continuous loop)

**Every frame (or every configured stride)**  

1. Station sends WSS `frame.submit` (image or reference + `frame_id`, `session_id`).
2. Vision-worker:
  - Runs the model (active `yolo_model` / session pin)  
  - Translates class → `family_code` with `model_class`  
  - **Refines** against the session's `expected_inventory` (unexpected families are downgraded or marked Extra; they do not invent expected lines)  
  - Replies WSS `detections.result` with boxes, `family_code`, confidence, and `counts_by_family`
3. Station updates the right panel with the aggregation of the **last 5 s**.
4. Station keeps a **ring buffer** in RAM (last M seconds of frames) for evidence.

**Persistence of count facts (not every frame)**  
The Backend receives from the station (HTTPS) or from an authorized hook a summary when:

- the aggregate changes in a stable way, or  
- there is a periodic heartbeat (e.g. every 10–30 s), or  
- there is a phase / caution event

Then the Backend does an **INSERT** `count_event` (`event_type` = `auto_count` or equivalent), with `family_id` (or `NULL` if the snapshot is a board snapshot), `expected_quantity`, `detected_quantity`, `occurred_at`, `session_id`, `payload`:

- phase, `window_seconds` / sampling, `model_version`
- **`confidences`:** array of 0..1 scores, **one entry per reported box** (in a board snapshot: `payload.board.families[].confidences`). It is the transparent measure of how the model performed in that fact.
- `avg` / `best` / `worst` are **not** stored: if the UI shows them, it derives them from that list.
- Full bboxes / top-k are **not** mandatory in every `auto_count` (they remain optional at milestones or in Mongo DS06).

**Reason for not storing every frame in PostgreSQL:** avoid bloating PG; visual detail goes to Mongo only at milestones/alerts.

### Phase change (sub-flow of RF-OP-04)

1. User chooses the next allowed phase (order `procedure_phase.sort_order`).
2. Backend **UPDATE** `work_session.current_phase_id`, `phase_changed_at`.
3. Backend **INSERT** `count_event` with `event_type` = `phase_change` and `payload` `{ from_phase, to_phase, phase_id, phase_code }`.
4. If there is a rule of "families only for the phase that ends": Backend evaluates missing items for that phase and may **INSERT** an open `discrepancy` with the phase in the description/payload of the originating event.
5. Backend enqueues evidence `reason = state_change` → evidence-worker (MP4 → storage → `media_asset` → Mongo).

### Temporal cautions (automatic sub-flow)

**Configuration**


| Parameter                        | v1 default | Meaning                                                                          |
| -------------------------------- | ---------- | -------------------------------------------------------------------------------- |
| `absence_warn_seconds`           | 1200       | Seconds without stable detection of an expected family before opening a caution  |
| `absence_resolve_stable_seconds` | 10         | Seconds of stable re-detection to auto-resolve                                   |
| `detection_window_seconds`       | 5          | Live panel window                                                                |
| `conf_display_threshold`         | 0.70       | Visual threshold (the training val value may differ)                             |

**When the absence condition is met**  

1. Backend **INSERT** `discrepancy` (`resolved = false`, description like "Absence > Ns", `family_id`, expected/detected, `origin_event_id` if any).
2. Enqueues clip `reason = discrepancy`.
3. UI may show a chip "Caution: [instrument] absent".

**When stable detection returns**  

1. Backend **UPDATE** `discrepancy` → `resolved = true`, `resolved_at = now()`, auto-resolve reason/note "re-detected".
2. **INSERT** `count_event` recording the resolution.
3. The caution does **not** escalate to SPD on its own if it was resolved before closing.

**Reason:** leave a trace of "was out for X time" without forcing SPD to act if the model already recovered the detection during surgery.

### Additional live configuration

- `frame_stride` (1 of every N frames).  
- `max_det` of the model.  
- Active model (`yolo_model.active = true`).

### Output

**Finish counting** → RF-OP-05. WSS is closed or left idle without `frame.submit`.

### UI errors

- WSS loss: banner "Vision disconnected — you can continue in manual mode when validating"; do not delete expected.  
- Model down: same banner; operator continues to human validation.  
- If, due to a data error, `capture_mode ≠ vision`: the UI must **not** open this screen; redirect to RF-OP-04M.

---

## RF-OP-04M · Manual progress without capture (no notice agreement)

### Actor

`station_operator`  
Session in `in_progress` **and** `capture_mode = manual_no_privacy`.

### How the user gets there

1. After **Start Session** when no `session_processing_agreement` existed at the time of Start.
2. **Continue** from RF-OP-01 while the session remains in this mode and the quantity closing has not yet been submitted.

### What the interface shows

**Header**  
Session ID, Procedure, Patient, Physician, OR, Kit, Station, **In Progress** badge, current phase (if applicable), fixed amber badge **No notice · No camera**.

**Permanent banner**  
*"This session has no confirmed privacy notice. There is no video capture or AI detection. You confirmed the instruments at the start; when closing you will report the quantities present. If you report less than expected for any family, a discrepancy will be opened."*

**Body — Expected inventory (reference only)**  
Read-only table:


| Column     | Content                          |
| ---------- | -------------------------------- |
| Instrument | Name of `instrument_family`      |
| Expected   | Frozen `expected_quantity`       |
| Reported   | Empty or "—" until closing       |
| Status     | "Pending report at closing"      |


Totals: Total Expected. There is **no** Detected/AI column.

**What does NOT appear**

- Video panel / overhead capture  
- AI buttons, confidence, Match/Missing per detection  
- Controls that trigger WSS  
- "Finish counting" toward the AI summary (RF-OP-05)

**Allowed controls**

- **Change phase** (optional; same phase SQL UPDATE as RF-OP-04 but **without** enqueueing camera evidence or a clip).  
- **Proceed to quantity report / Close counting** → RF-OP-06M.

### What the user does

Keeps the session "in progress" as long as the clinical case requires (without camera). When it is time to close the count, moves on to the quantity report.

### What happens in the system while the user remains in this view

1. Backend only serves HTTPS (phase change, optional heartbeat, expected reading).
2. **Prohibited:** `frame.submit`, WSS bind, vision-based evidence-worker jobs, Mongo INSERT of tray `checkpoint_frame`.
3. Phase change (if used): **UPDATE** `work_session.current_phase_id` + **INSERT** `count_event` `phase_change` with `payload.capture_mode = manual_no_privacy`. No visual evidence queue.
4. The session remains in status `in_progress` until RF-OP-06M.

**Reason:** the operator already confirmed the physical set (checkbox from RF-OP-03); the human numeric report is still missing because there is no model suggesting quantities.

### Configuration

- i18n texts of the banner and the badge.  
- `change_phase_allowed_without_privacy` = true|false (default **true**).

### Output

**Proceed to quantity report** → RF-OP-06M.

### UI errors

- Attempt to open the camera: block on the client and reject in the Backend if a frame arrives.  
- If SPD added the agreement **after** Start: this screen is **still** the correct one (mode already frozen); show an informational note "The notice was registered late; this session is already running in manual mode".

---

## RF-OP-05 · Final detection summary (model no longer feeds live)

### Actor

`station_operator`  
Only sessions with `capture_mode = vision`. `manual_no_privacy` sessions do **not** go through this screen.

### How the user gets there

On leaving live (Finish counting). At this point the frame loop has **ended**.

### What the interface shows

- Session header (same as live) + status In Progress.  
- Left: **last capture** of the tray (version without boxes and/or with boxes from the last result).  
- Right: table per instrument of the expected set:


| Column         | Content                                                                                |
| -------------- | -------------------------------------------------------------------------------------- |
| Instrument     | Family                                                                                 |
| Expected       | Frozen                                                                                 |
| AI Detected    | From the closing snapshot (not the 5 s window)                                         |
| Difference     | Detected − Expected                                                                    |
| Avg confidence | Average of the confidences of **all boxes** of that family in the closing snapshot     |
| Result         | Match / Missing / Extra                                                                |


- Totals Expected vs AI Detected.  
- "AI Analysis Details" block: Vision model name, `yolo_model.version_tag`, confidence threshold used, analysis timestamp.  
- Disclaimer: suggestion; requires human validation.  
- **Proceed to Validation** button.

### What happens in the system

1. On entering this screen (or on Finish): Backend **INSERT** detection-closing `count_event`(s) (`event_type` e.g. `detection_close`) per family with expected/detected from the snapshot.
2. Enqueues evidence `reason` aligned with vision closing (`close` or the equivalent from the checkpoint catalog).
3. Evidence-worker saves the final MP4 (JPEG fallback).
4. Reads `yolo_model` for the metadata block.
5. The status is **not** yet changed to `awaiting_spd_review` (human validation is still missing).

### Output

RF-OP-06 Validation.

---

## RF-OP-06 · Human validation, corrections, and operator exit

### Actor

`station_operator`  
Only after RF-OP-05 (`capture_mode = vision`).

### How the user gets there

**Proceed to Validation** from RF-OP-05.

### What the interface shows

Table:

| Column          | Content                                                 |
| --------------- | ------------------------------------------------------- |
| Instrument      | Family                                                  |
| Expected        | Frozen                                                  |
| AI Detected     | AI value **locked** (visual padlock; not editable)      |
| Validated count | − / + control; starts at AI Detected                    |
| Difference      | Validated − Expected                                    |
| Status          | Confirmed (match) / Pending / Corrected                 |

When selecting a row with difference ≠ 0 or when correcting:

- Original AI quantity (locked)  
- Corrected quantity  
- **Reason** (catalog dropdown + **Other** option)  
- Notes (text; mandatory if Reason = Other)  
- Footer: "Corrected by: [name] | Timestamp: [now]"

Buttons:  

- Back to AI Results → RF-OP-05  
- **Submit Validation**

### What the user does

Adjusts validated quantities, documents reasons, submits.

### What happens in the system on Submit Validation

1. Backend validates session `in_progress` and owning user.
2. For each family:
  - If Validated == AI Detected and == Expected: match is confirmed (optional event).  
  - If Validated ≠ AI Detected: **INSERT** `count_event` type `manual_count` with detected/validated in columns or payload; **INSERT** `human_correction` (`justification` = reason + notes, `user_id`, `count_event_id`).  
  - If Validated ≠ Expected: ensures an open `discrepancy` or updates quantities; if the user "closed" the difference in the room, **UPDATE** `discrepancy.resolved = true` with the appropriate `reason_id`.
3. Final session status decision:
  - **Shortfall** (any family with Validated < Expected, `resolved = false`) → **UPDATE** `work_session.status` = `or_hold` (not `correction_required`). **INSERT** `count_event` type `or_hold_opened`. In-app notification to `or_charge` users. The operator is **not done**: moves to RF-OP-07. `ended_at` is **not yet** filled.  
  - **No shortfalls** (match or only documented extras) → **UPDATE** status = `awaiting_spd_review`, `ended_at` = now() (end of station work; SPD confirms closing). Extra: documented (`discrepancy` and/or event); does **not** trigger hold or the SPD correction queue.
4. The operator's `closed_by_user_id` does **not** imply a final `closed`; administrative closing is SPD's (RF-SP-05) **after** `awaiting_spd_review` or, if there was a hold, **after** RF-OP-07 → `correction_required`.
5. INSERT `access_audit` for submit validation.

**Reason for the hold:** a human-verified shortfall is investigated **in the room** (evidence, comments, suggested resolution) before SPD receives the case. SPD does not enter `or_hold`.

### Configuration

- Catalog of correction reasons (UI) mapped to `cat_discrepancy_reason` or a parallel list + `other`.  
- If `other`, minimum N characters in notes (default 10).

### Output

| Result | Next screen |
| --- | --- |
| There is a shortfall → `or_hold` | **RF-OP-07** hold view (the operator is **not** done) |
| No shortfall → `awaiting_spd_review` | **Final report** (read-only): totals, extras if any, evidence, **Back to sessions** → RF-OP-01 |

If there is no hold, the operator **finishes their flow here**.

### UI errors

- Submit with reason Other without notes: block.  
- Partial submit: not allowed; or define "save draft" (v1: there is no draft; Submit is atomic).

---

## RF-OP-06M · Manual quantity report and closing (no AI)

### Actor

`station_operator`  
Session `in_progress` with `capture_mode = manual_no_privacy`.

### How the user gets there

**Proceed to quantity report / Close counting** from RF-OP-04M.

### What the interface shows

Session header + **No notice · No camera** badge.

Editable table (one row per `expected_inventory`):


| Column            | Content                                                                           |
| ----------------- | --------------------------------------------------------------------------------- |
| Instrument        | Family                                                                            |
| Expected          | Frozen (read-only)                                                                |
| Reported quantity | − / + control; initial value = Expected (the operator adjusts to the actual count) |
| Difference        | Reported − Expected (**v1** same numeric convention: reported − expected)         |
| Status            | Match if Diff = 0; Shortfall if Reported < Expected; Extra if Reported > Expected |

When marking Shortfall or Extra (or when changing the quantity):

- **Reason** (dropdown + **Other**) — mandatory if Diff ≠ 0  
- Notes — mandatory if Reason = Other  
- Footer: "Reported by: [name] | Timestamp: [now]"

Buttons:

- Back → RF-OP-04M (without saving the closing)  
- **Submit report and close counting**

Fixed text below the table:  
*"If you report a quantity lower than expected, the session enters OR hold: you document evidence and a suggested resolution before sending it to SPD. Extras are documented and do not open a hold. If everything matches (or there are only extras), it will go to Awaiting Review."*

### What the user does

Enters the physical quantities present, documents differences, submits, and closes their part of the process.

### What happens in the system on Submit report (strict order, SQL transaction)

1. Backend validates: session `in_progress`, owning `user_id`, `capture_mode = manual_no_privacy`, and that **all** families of the expected set have a reported quantity (integer ≥ 0).
2. For each family:
  - **INSERT** `count_event` type `manual_count` (or `manual_close`):  
    - `expected_quantity` = frozen  
    - `detected_quantity` = **NULL** or 0 with a note in `payload` that there was no AI (`"ai": false`, `"capture_mode": "manual_no_privacy"`)  
    - reported quantity in the validated column or in `payload.reported_quantity` / `validated_quantity` according to the schema's typed columns  
    - `user_id`, `occurred_at`, `session_id`
  - If Reported ≠ Expected: **INSERT** `human_correction` with `justification` = reason + notes.
  - **Discrepancy rule (v1 contract):**  
    - If **Reported < Expected** → **INSERT** `discrepancy` with `resolved = false`, `family_id`, expected/detected_or_reported quantities, reason linked to the operator's reason. It is **not** auto-resolved.  
    - If Reported = Expected → no discrepancy is opened for that family.  
    - If Reported > Expected → **INSERT** a **documented** Extra `discrepancy` (or event); it does **not** trigger `or_hold`. If `extra_auto_ack_on_manual_close = true`, it is marked resolved; default **false** = stays open as data, with no hold or SPD correction queue.
3. Session status decision (same spirit as RF-OP-06):
  - If **any** Reported < Expected → **UPDATE** `work_session.status` = `or_hold`. **INSERT** `count_event` `or_hold_opened`. In-app notification to `or_charge`. `ended_at` is **not** filled. The operator moves to RF-OP-07.  
  - If **zero** shortfalls → **UPDATE** status = `awaiting_spd_review`, `ended_at` = now().
4. **INSERT** `access_audit` action `session.manual_close`.
5. **No** WSS, **No** evidence-worker, **No** frame Mongo.

**Reason:** without a notice there is no visual camera evidence; the auditable fact is the human report. The shortfall hold still occurs (RF-OP-07) so the room documents the search before SPD.

### Configuration

- Initial spinner value: `expected` (default) or `0`.  
- `extra_auto_ack_on_manual_close` (default false).  
- Shortfall/extra reasons (same catalog as corrections).  
- Minimum notes if Other (default 10).

### Output

| Result | Next screen |
| --- | --- |
| There is a shortfall → `or_hold` | **RF-OP-07** (the operator is **not** done) |
| No shortfall → `awaiting_spd_review` | **Final report** (read-only): expected vs reported, extras, notice that there was no capture/AI, **Back to sessions** → RF-OP-01 |

If there is no hold, the operator **finishes here**. The final administrative closing is still SPD's (RF-SP-05 / RF-SP-06) **after** `awaiting_spd_review` or after the OP-07 release.

### UI errors

- Missing quantity in any row: block Submit.  
- Shortfall/Extra without reason: block.  
- Other without notes: block.  
- Atomic submit: all or nothing.

---

## RF-OP-07 · Room hold view (evidence, comments, release to SPD)

### Actor

`station_operator` (session owner). `or_charge` can **read and comment** on the same record from the App (RF-AP-03); this does **not** replace the operator's submission.

### How the user gets there

Automatic after RF-OP-06 / RF-OP-06M when the status ends up `or_hold`. Also **Continue** from RF-OP-01 if the session is already on hold.

### What the interface shows

- Banner **OR hold · shortfall confirmed**. SPD does **not** yet appear as a queue.
- Missing families (expected vs validated/reported).
- Notable evidence: session clips/checkpoints (Mongo + signed URL). In `manual_no_privacy` it is indicated that there is no camera; the account is textual.
- `count_event` timeline (including `or_hold_opened` and comments).
- **Comment** field (OP + SdP thread).
- **Suggested resolution** field (mandatory text to release): found / not found / still searching / other.
- Buttons: **Save comment**, **Send to SPD** (enabled only with a suggested resolution). There is no cycle "Confirm and close".

### What the user does

Reviews evidence, writes comments, drafts the suggested resolution, presses **Send to SPD**.

### What happens in the system

**Comment:** INSERT `count_event` type `or_hold_comment` (`user_id`, `payload` `{text, discrepancy_id?}`).

**Send to SPD** (transaction):

1. Revalidates status = `or_hold` and owning user.
2. **UPDATE** each open shortfall `discrepancy`: `suggested_resolution` = text, `or_released_at` = now(), `or_released_by_user_id` = operator.
3. **INSERT** `count_event` type `or_hold_released`.
4. **UPDATE** `work_session.status` = `correction_required`, `ended_at` = now() (end of station work; enters the SPD queue).
5. `access_audit` `session.or_hold.release`.
6. Does **not** reopen WSS. Does **not** close the instrument cycle.

### Stores

PostgreSQL write. Mongo/storage read-only for already existing evidence.

### Configuration

Minimum length of the suggested resolution (default 10). Free-text catalog in v1 (there is no `cat_or_resolution`).

### Output

Final station report: status `correction_required`, text sent, **Back to sessions**. The operator **finishes** here. SPD takes the case in RF-SP-06.

### UI errors

- Send without suggested resolution: block.
- Status is no longer `or_hold`: message and return to the list.
- SdP cannot press Send to SPD in v1 (only the owning OP).

---

# PART B — SPD SUPERVISOR

The supervisor logs in to the **web portal** with the `spd_supervisor` role. Their home is the **dashboard**. From the menu they access sessions, kits, metrics, and the configuration audit.

---

## RF-SP-01 · SPD Dashboard

### Actor

`spd_supervisor`

### How the user gets there

Portal login → Dashboard home.

### What the interface shows

Cards / widgets with current numbers, for example:

- `in_progress` sessions  
- `in_progress` sessions in `manual_no_privacy` mode (optional, separate counter)  
- `scheduled` sessions **without** `session_processing_agreement` (notice pending)  
- `awaiting_spd_review` sessions (clean closing / only extras)  
- `correction_required` sessions (**post-release only** from OR; do **not** include `or_hold`)  
- Open discrepancies **already released** to SPD (`or_released_at` NOT NULL)  
- (Optional) Instruments in dirty / sterilizing states

Short "Pending review" list with **only** `awaiting_spd_review` and `correction_required`, with shortcuts to RF-SP-05 / RF-SP-06. `or_hold` sessions do **not** appear here (they live in the App / RF-OP-07).

### What happens in the system

`GET` dashboard → Backend **SELECT** SQL aggregates over `work_session` + `discrepancy` (+ `instrument` by cycle). Read-only.

### Configuration

Color thresholds (e.g. >N corrections = red).

### Output

Navigation to other RF-SP-*.

---

## RF-SP-02 · Schedule session (source of what the operator sees), kit ± and privacy notice

### Actor

`spd_supervisor`

### How the user gets there

**Sessions** menu → **New session** / edit a `scheduled` session (also from the detail view if it has not started yet).

### What the interface shows

Editable form (unlike the operator's):

- Assigned operator (select of `station_operator` users)  
- Procedure / type  
- Patient(s), physician(s), and surgical roles  
- OR room  
- Planned capture station  
- Kit (procedure default or chosen)  
- Scheduled date/time  
- Initial phase

**Expected inventory editor (kit ±)**  

1. When a kit is chosen, `kit_item` lines (family + quantity) are loaded.
2. The supervisor can:
  - **Add** a family from the `instrument_family` catalog (search) with quantity ≥ 1  
  - **Remove** a line  
  - **Change the quantity** of a line
3. After each change, the Backend recalculates the stock traffic light (see configuration below) and the UI shows OK / WARN / BLOCK with clear text.
4. Save the schedule only if there is no BLOCK.

**Mandatory UI section · Privacy notice for system use**

This section appears on the same screen for scheduling/editing a `scheduled` session. The supervisor chooses **one** of these two paths (both end in the same tables):

**Path A — Confirm an existing version**

1. Selector of `privacy_notice_version` with `active = true` (shows `version`, `effective_at`, "View document" link).
2. Mandatory checkbox on confirming: *"I confirm that privacy notice version [X] applies to this session and to the checked purposes."*
3. Opt-in toggle: **Allow use of evidence for model improvement** → `purpose_model_improvement` (default OFF).
4. `purpose_quality_ops` is not editable: always ON (the DB requires it).

**Path B — Upload a new notice file**

1. **Upload** control (PDF or HTML; allowed MIME types configurable).
2. `version` field (unique label, e.g. `2026.09.2`) and `effective_at`.
3. On upload: the Backend saves the file in Garage object storage (S3-compatible), computes SHA-256, and prepares an INSERT `privacy_notice_version` (`document_uri`, `content_sha256`, `version`, `effective_at`, `active = true`).
4. After creation, the UI automatically selects that version and asks for the same confirmation checkbox + model improvement toggle (same as Path A).

**Visual states of the section**


| State                               | What the UI shows                                                             | Can the session be saved?                                                      |
| ----------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| No file/version and not confirmed   | Amber badge "Notice pending — the session will start in manual mode without camera" | **Yes** (full scheduling; capture not enabled until an agreement exists) |
| Version chosen + checkbox checked   | Green badge "Notice confirmed for this session"                               | Yes                                                                            |
| Upload in progress / hash failed    | Spinner / error "Could not register the notice"                               | Saving the session is allowed **without** an agreement; do not create a partial agreement row |


Section buttons (in addition to the global Save):

- **Confirm privacy for this session** — creates/updates the agreement (see SQL below). Available only with a valid version + checkbox.  
- **Clear / Remove pending confirmation** — only if no agreement is saved yet; clears the local selection.  
- If an agreement already exists: shows version, `agreed_at`, who confirmed (`agreed_by_user_id`), link to the document; **Replace agreement** button only while status = `scheduled` (deletes/replaces the single row per session).

**Note:** the agreement cannot "activate capture" mid-way through a session that has already started in `manual_no_privacy`. If the session is still `scheduled`, the agreement can be added or replaced before the operator's Start.

### What happens in the system on Save (clinical scheduling)

1. Backend validates the role.
2. **INSERT/UPDATE** `operation` (+ patients, physicians).
3. **INSERT/UPDATE** `work_session`: status `scheduled`, `user_id` = chosen operator, `kit_id`, `station_id`, `operation_id`, scheduling timestamps.
4. Saves the **expected draft** (formal v1 option):
  - Working table: it is already materialized in provisional `expected_inventory` **or** in a draft structure in `payload`/auxiliary table.  
  - **Chosen v1 contract:** when saving the schedule, the resolved set is saved in prepared rows linked to the session; at **Start Session (RF-OP-03)** it is **re-frozen** with the same content (idempotent) and marked as final. Kit lines carry traceability `source = kit_snapshot`; those altered by SPD `source = 'manual'`.
5. Optional soft reservations at family level (app logic); hard `instrument_reservation` if the supervisor assigns specific serial numbers on this screen (if the serial UI exists; if not, only families in v1).
6. **INSERT** `access_audit`: "session.scheduled" / "session.updated".
7. Saving the schedule does **not** by itself create `session_processing_agreement` if the supervisor did not press Confirm privacy (or did not check the checkbox in the same composite submit).

### What happens on Confirm notice (same submit or dedicated action)

Order in the PostgreSQL transaction (+ storage if there was an upload):

**If Path B (new file) and the version does not exist yet:**

1. Upload bytes to the legal documents bucket (path e.g. `privacy-notices/{version}/{sha256}.pdf`).
2. **INSERT** `privacy_notice_version` (`version`, `effective_at`, `document_uri`, `content_sha256`, `active = true`).
3. If `version` already exists (UK): UI error "Duplicate version"; do not overwrite.

**Always (Path A or B once** `privacy_notice_version_id` **is available):**

1. Validate that the session is `scheduled` (or that no `capture_mode` has been frozen yet).
2. **UPSERT** `session_processing_agreement` (UNIQUE `session_id`):
  - `session_id`  
  - `privacy_notice_version_id`  
  - `purpose_quality_ops = TRUE`  
  - `purpose_model_improvement` = toggle value  
  - `agreed_at = now()`  
  - `agreed_by_user_id` = current supervisor
3. **INSERT** `access_audit`: action `privacy.session_agreement`, resource_type `work_session`, resource_id = session.
4. UI response: green badge; the session list and the operator's Begin will show "Notice OK · Vision".

**If the supervisor saves the session without confirming the notice:** there is no row in `session_processing_agreement`. At the operator's Start → `manual_no_privacy` (RF-OP-04M → RF-OP-06M).

### Stock traffic light (how it is calculated)

For each family of the scheduled expected set:

- `stock_total` = COUNT of active `instrument` of that family  
- `stock_available` = COUNT with usable `cycle_status` (`available`) and **without** an active `instrument_reservation`  
- `requested` = expected quantity

| Condition                                            | UI                                       | Allows saving?                 |
| ---------------------------------------------------- | ---------------------------------------- | ------------------------------ |
| `requested` ≤ `stock_available`                      | OK                                       | Yes                            |
| `requested` ≤ `stock_total` but > `stock_available`  | WARN (turnaround / in use / sterilizing) | Yes, with explicit confirmation |
| `requested` > `stock_total`                          | **BLOCK**                                | No                             |


Additional concurrency warning: if two `scheduled`/`in_progress` sessions request kits whose per-family sum > stock, show a WARN "no slack for two simultaneous procedures".

### Configuration

- List of cycle states that count as "available".  
- Policy: WARN with confirmation vs BLOCK on overlaps.  
- Reprocessing SLA minutes (WARN text only, does not invent availability).  
- Allowed MIME types of the notice (`application/pdf`, `text/html`).  
- Maximum size of the notice file (default 10 MB).  
- Storage bucket/prefix for notices (`privacy_notice_storage_prefix`).  
- If `require_privacy_before_schedule = true`: session Save **fails** without an agreement (v1 default = **false**, to allow the manual flow).

### Output

The session appears in the SPD global list and in the assigned operator's RF-OP-01, with a notice OK or pending indicator.

---

## RF-SP-03 · Global session list

### Actor

`spd_supervisor`

### How the user gets there

Sessions menu.

### What the interface shows

Same column structure as the sessions mockup, but **without** the "mine only" filter.

Filters: Search, Procedures, Statuses, Date range, Privacy (With notice / Without notice).  
Pagination: "Showing X of Y".

Additional columns compared to the operator: **Privacy** (OK / Pending according to EXISTS `session_processing_agreement`) and, if already started, **Capture mode** (`vision` / `manual_no_privacy`).

Actions per row according to status, including:


| Button                      | Typical status                               | Goes to                   |
| --------------------------- | -------------------------------------------- | ------------------------- |
| View details                | any                                          | Detail                    |
| Edit / Confirm privacy      | `scheduled` without agreement                | RF-SP-02 notice section   |
| Review count / Review       | `awaiting_spd_review`, `correction_required` (never `or_hold`) | RF-SP-05 / RF-SP-06 |
| Open live / View progress   | `in_progress` + `vision`                     | Detail + extras RF-SP-04  |
| View manual progress        | `in_progress` + `manual_no_privacy`          | Detail (no live camera)   |
| View OR hold (read-only)    | `or_hold`                                    | Record; **no** SPD review |
| Confirm close               | clean review                                 | RF-SP-05                  |


### What happens in the system

`GET` with filters → global SQL **SELECT** (not filtered by the supervisor's `user_id`). Read-only.

---

## RF-SP-04 · Extra instruments in an in-progress session

### Actor

`spd_supervisor`

### How the user gets there

Detail of an `in_progress` session → **Add instruments** action.

### What the interface shows

Family selector + quantity; stock traffic light result; Confirm / Cancel.  
Current expected list with the new items highlighted.

### What happens in the system on confirm

1. Revalidates the traffic light (BLOCK prevents).
2. **UPSERT** `expected_inventory`: if the family exists, adds the quantity; if not, INSERT with `source = 'manual'`.
3. Optional **INSERT** `instrument_reservation` if a specific piece is chosen.
4. **INSERT** `count_event` type expected adjustment (`payload`: before/after qty, SPD actor).
5. **INSERT** `access_audit`.
6. The station, on the next UI cycle or via HTTPS/SSE notification, refreshes expected; the vision-worker refines with the new set in the following frames.

**Reason:** real mid-case complications; expected must grow in an auditable way.

### Output

Updated detail; the operator sees the new rows in the expected panel.

---

## RF-SP-05 · Recovery review and administrative closing

### Actor

`spd_supervisor`

### How the user gets there

"Awaiting Review" queue or Review button on a session in `awaiting_spd_review` (with no open discrepancies, or after resolving them in RF-SP-06).

### What the interface shows

Session summary, validated totals, list of evidence (thumbnails/links to MP4), cycle status of the associated pieces, checkbox *"Material recovered / reprocessing started"* or equivalent flow, **Confirm and close** button.

### What happens in the system on confirming closing

1. **UPDATE** pieces: `instrument_cycle_event` + `cycle_status_id` → `dirty` or `sterilizing` according to the SPD process captured in the UI.
2. **UPDATE** `instrument_reservation`: `active = false`, `released_at = now()` when the policy releases the piece (if release happens only upon returning to `available`, it is documented in the same flow or in a later "Mark sterilized/available" step).
3. **v1 availability contract:** the instrument does **not** become `available` on the operator's Submit; it becomes `available` when SPD records the end of sterilization (this can be this same screen in two steps or a cycle screen).
4. **UPDATE** `work_session.status` = `closed`, `closed_by_user_id` = supervisor, `ended_at` was already set by the operator or is adjusted.
5. `access_audit` of SPD closing.

### Output

Session Closed; disappears from pending queues.

---

## RF-SP-06 · Discrepancy resolution (SPD) + evidence + timeline

### Actor

`spd_supervisor`

### How the user gets there

Session `correction_required` (**after** RF-OP-07) or discrepancy with `or_released_at` NOT NULL from the dashboard/list. `or_hold` sessions are **not** entered.

### What the interface shows

Same spirit as the operator's validation, **plus**:

- **OR account (read-only):** `discrepancy.suggested_resolution`, `or_released_at`, `or_released_by_user_id`, and `or_hold_comment` / `or_hold_released` events. SPD **confirms** that account; it does **not** reopen the room thread or write `or_hold_comment`.  
- **Clips/checkpoints** viewer for the session (list by reason + timestamp + phase).  
- **History of** `count_event`: timestamp, type, family, expected, detected, phase (from `payload`).  
- Actions per discrepancy:  
  - Mark as **resolved** (reason + notes; implies confirming the OR account)  
  - Mark **instrument loss**

### What happens if "resolved"

1. **UPDATE** `discrepancy` resolved.
2. `human_correction` or closing manual `count_event`.
3. If none remain open → status may move to `awaiting_spd_review` or go directly to the RF-SP-05 flow.

### What happens if "loss"

1. **UPDATE** `instrument.cycle_status_id` → code `lost` or `retired` (catalog). **No DELETE.**
2. **INSERT** `instrument_cycle_event` with loss notes, `operation_id`/`session_id`.
3. Closes `discrepancy` with a loss reason.
4. Releases/cancels the reservation if one existed.
5. Effective stock decreases → future RF-SP-02/10 show WARN/BLOCK with the new numbers.
6. `access_audit`.

### How clips are read

1. Backend **SELECT**/query Mongo `checkpoint_frame` by `session_id`.
2. Resolves `media_asset` → generates a temporary signed URL to Garage (S3).
3. The browser only uses that URL; no bucket credentials.

### Output

Discrepancy closed; the session advances depending on whether any remain open.

---

## RF-SP-07 · Session configuration audit log

### Actor

`spd_supervisor`

### How the user gets there

**Audit** menu / Session configuration log.

### What the interface shows

Table of **scheduling and editing** events (create session, change kit, change operator, cancel, add extra), with: timestamp, actor, action, session id, summary.

It does **not** list `count_event` (those are seen inside the session detail / RF-SP-06).

### What happens in the system

`GET` → **SELECT** `access_audit` filtered by configuration action types. Read-only.

---

## RF-SP-08 · Metrics

### Actor

`spd_supervisor`

### How the user gets there

Metrics menu.

### What the interface shows

Configurable charts/tables, at minimum in v1:

- Average session time by procedure type  
- Count of sessions by status in a date range  
- Rate of sessions with discrepancies  
- (Optional) average time between phases

### What happens in the system

`GET` with date range → SQL aggregations over `work_session`, `operation`, `discrepancy`, `count_event`. Read-only. Mongo only if counting the number of evidence items.

### Configuration

Default range (7/30 days); included procedures.

---

## RF-SP-09 · Operation kit configuration

### Actor

`spd_supervisor` (SPD catalog role)

### How the user gets there

Kits menu.

### What the interface shows

Kit CRUD: name, version, family+qty lines, link to procedure types (`procedure_kit`, default).

When saving a line/kit:

### Validation rules (contract)


| Rule                                                                                                                  | UI behavior                                                                               | Backend                      |
| --------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ---------------------------- |
| `quantity` of a family > number of active physical pieces of that family                                              | **Hard block** + message "Not enough units in catalog"                                    | Rejects SAVE                 |
| After losses, the sum of qty of default kits used in typical concurrent procedures leaves slack < 2 processes         | Non-blocking **WARN** when editing the kit; **WARN/BLOCK** when scheduling the second case (RF-SP-02) | Stock calculation |
| Removing a kit family referenced by `scheduled`/`in_progress` sessions                                                | WARN + confirmation or block                                                              | Backend decides by logical FK |

Write: **INSERT/UPDATE/DELETE** SQL on `kit`, `kit_item`, `procedure_kit`. `access_audit` on changes.

---

# PART C — MASTER WRITE TABLE BY EVENT


| Business event                | PostgreSQL                                                                                       | Mongo                      | Storage            | Who                       |
| ----------------------------- | ------------------------------------------------------------------------------------------------ | -------------------------- | ------------------ | ------------------------- |
| Login                         | — (Redis session)                                                                                | —                          | —                  | Backend                   |
| Schedule session              | `operation*`, `work_session`, expected draft, `access_audit`                                     | —                          | —                  | Backend                   |
| Upload/confirm notice         | `privacy_notice_version` (±), `session_processing_agreement`, `access_audit`                     | —                          | Notice PDF/HTML    | Backend                   |
| Start Session (vision)        | `work_session` (+ `capture_mode`, **pin** `yolo_model_id`), `expected_inventory`, `count_event`, reservations | `checkpoint_frame` (async) | Start MP4  | Backend + evidence-worker |
| Start Session (no notice)     | `work_session` (`capture_mode=manual_no_privacy`), `expected_inventory`, `count_event`, reservations | —                      | —                  | Backend                   |
| Live frame                    | —                                                                                                | —                          | —                  | WSS vision-worker         |
| Persisted auto count          | `count_event` (`payload.confidences` per box)                                                    | —                          | —                  | Backend                   |
| Phase change (vision)         | `work_session`, `count_event`, ± `discrepancy`                                                   | async checkpoint           | MP4                | Backend + evidence-worker |
| Phase change (manual)         | `work_session`, `count_event`                                                                    | —                          | —                  | Backend                   |
| Absence caution               | `discrepancy`, `count_event`                                                                     | async checkpoint           | MP4                | Backend + evidence-worker |
| Caution auto-resolve          | `discrepancy`, `count_event`                                                                     | —                          | —                  | Backend                   |
| Detection closing (vision)    | `count_event`                                                                                    | async checkpoint           | Closing MP4        | Backend + evidence-worker |
| Validation / correction       | `count_event`, `human_correction`, `discrepancy`; status `or_hold` if shortfall, otherwise `awaiting_spd_review` | — | — | Backend |
| Manual closing without AI (OP-06M) | Same shortfall rule → `or_hold`; documented extra without hold | — | — | Backend |
| OR hold (OP-07)               | `count_event` (`or_hold_comment` / `or_hold_released`), `discrepancy` (suggested resolution, `or_released_*`), status `correction_required` | checkpoint reads | — | Backend |
| App view release (SdP)        | Only `or_hold_comment` (RF-AP-03); does **not** change status | read | — | Backend |
| SPD mid-case extra            | `expected_inventory`, `count_event`, ± reservation                                               | —                          | —                  | Backend                   |
| SPD review / close            | `work_session`, instrument cycle, reservations                                                   | —                          | —                  | Backend                   |
| Loss                          | `instrument`, `instrument_cycle_event`, `discrepancy`                                            | —                          | —                  | Backend                   |
| Kit CRUD                      | `kit`, `kit_item`, `procedure_kit`                                                               | —                          | —                  | Backend                   |


---

# PART D — CENTRALIZED CONFIGURATION PARAMETERS (v1)


| Key                                    | Default                   | Used in                         |
| -------------------------------------- | ------------------------- | ------------------------------- |
| `detection_window_seconds`             | 5                         | Live panel                      |
| `absence_warn_seconds`                 | 60                        | Cautions                        |
| `absence_resolve_stable_seconds`       | 10                        | Auto-resolve                    |
| `conf_display_threshold`               | 0.70                      | Results UI                      |
| `frame_stride`                         | 1 or 2                    | Station → WSS                   |
| `evidence_clip_frames`                 | 8–16                      | Evidence-worker (MP4)           |
| `evidence_queue_name`                  | `pef-evidence-queue`      | Queue                           |
| `diff_convention`                      | `detected_minus_expected` | All Diff tables                 |
| `session_id_display_prefix`            | `WS-`                     | Lists                           |
| `require_privacy_before_schedule`      | false                     | RF-SP-02 session Save           |
| `privacy_notice_max_mb`                | 10                        | Notice upload                   |
| `privacy_notice_allowed_mime`          | pdf, html                 | Notice upload                   |
| `privacy_notice_storage_prefix`        | `privacy-notices/`        | Object storage                  |
| `change_phase_allowed_without_privacy` | true                      | RF-OP-04M                       |
| `manual_report_qty_default`            | `expected`                | RF-OP-06M initial spinner value |
| `extra_auto_ack_on_manual_close`       | false                     | RF-OP-06M extras                |


These values live in application configuration (file/env/`configuration` table); changing them does not require altering the meaning of the RFs.

---

# PART E — OUT OF SCOPE (explicit)

- User creation, roles, password resets, families, selection catalogs, models, and the Floor App: [rf_admin_app.md](rf_admin_app.md) (RF-AD-02, AD-05, AD-06, AD-08, RF-AP-*).
- Executed ARCO processes: RF-AD-11 is a list stub. The **session agreement** and the notice at scheduling remain in RF-SP-02; the notice **library** is RF-AD-04.
- FHIR machine clients / `integration_client`: considered in the plan, **not designed**.
- ML training (only the `yolo_model` pinned in the session is **consumed**; Admin publishes weights in RF-AD-08). The `purpose_model_improvement` opt-in only marks permission for future use of evidence.
- Advanced multi-hospital preferences. App: sees all rooms (the SdP's own first); **notifications only** for `user_operating_room`.

---

*End of v1 contract (includes the no-notice / manual capture branch). Any change in screen behavior or DB writes must update the corresponding RF in this file before being implemented as "done".*
