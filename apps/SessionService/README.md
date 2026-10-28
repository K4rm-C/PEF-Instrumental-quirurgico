# PEF Session / Counting Service — Session API v1 (Phase A/B/C/D/E)

> **Stage 8 (DS09 §1.3.4 complement v0.2.0):** this service now owns the Supervisor's session
> operations of the RF portal (schedule, full edit, privacy agreement, expected inventory with
> confirmed `instruments_added`, discrepancy queue and resolution, safe SPD close) and serves the
> session snapshot reads (`expand=detail`) the Supervisor screens are built from. The Operator's
> counting flow stays in the portal: `start`, `vision-counts` and `count-report` answer **410**
> unless `SESSION_OPERATOR_WRITES_ENABLED=true`. Supervisor writes lock the work_session row the
> portal's Operator transactions lock. Denied attempts are audited (`DENIED_*`). Contract:
> `openapi.yaml` (`/api/v1/sessions/docs`), `schemas/session-requests.xsd`; integration: `GUIA_SESSION.md`.
> The rest of this README describes the v0.1.7 phases; where it says the portal does not call this
> service or that resolve requires `instrument_id` / `correction_required`, the complement above wins.

Independent REST microservice for the counting-session domain (PEF-DS04): sessions, expected
inventory, count facts, discrepancies and human corrections. Clients: Android (JSON) and Desktop
(XML) through Bearer tokens issued by the Auth Service.

- **Contract:** [PEF-DS09 §1.3.4](../../data/DataStandards/PEF-DS09-API-Exchange-Contracts.md) (v0.1.7).
- **Data model and semantics:** [PEF-DS04](../../data/DataStandards/PEF-DS04-Work-Session-and-Count-Facts.md) (v0.2.2).
- **OpenAPI (what is implemented now):** [openapi.yaml](openapi.yaml).

It does not import BackendWebFlask, BackendAuthService, CatalogService or VisionWorker. It reads the
existing PostgreSQL schema (never creates/alters tables) and uses Redis only to validate Auth's JWT
sessions (no cache). BackendWebFlask keeps using PostgreSQL directly; nothing in the portal calls this
service. It never calls VisionWorker either (no HTTP/WebSocket client at all).

## Implemented endpoints

| Method | Path | Notes |
| :--- | :--- | :--- |
| GET | `/api/v1/sessions` | Role-scoped list, `scheduled_at` desc (nulls last), then `id` |
| GET | `/api/v1/sessions/{id}` | Detail (incl. `privacy_agreement`, read-only boolean derived from DS07) |
| GET | `/api/v1/sessions/{id}/expected-inventory` | `family_code`, `family_id` order; no stock/kit data |
| GET | `/api/v1/sessions/{id}/counts` | `occurred_at`, then type rank (`session_open` → facts → `manual_close` → `session_close`), then `id`; raw `payload` never exposed |
| GET | `/api/v1/sessions/{id}/discrepancies` | Open first; stored description untranslated |
| GET | `/health/live`, `/health/database`, `/health/redis`, `/health/ready` | PostgreSQL + Redis only |

| POST | `/api/v1/sessions` | Phase C — `spd_supervisor`; schedules a session on an existing operation |
| PUT | `/api/v1/sessions/{id}/expected-inventory` | Phase C — `spd_supervisor`; total replacement while `scheduled`/`in_progress` |
| POST | `/api/v1/sessions/{id}/start` | Phase C — assigned `station_operator`; `scheduled` → `in_progress` |
| POST | `/api/v1/sessions/{id}/vision-counts` | Phase D — assigned `station_operator`; `in_progress` + `vision`; 201 new / 200 replay |
| POST | `/api/v1/sessions/{id}/count-report` | Phase D — assigned `station_operator`; `in_progress` → `awaiting_spd_review` / `correction_required` |

| POST | `/api/v1/sessions/{id}/discrepancies/{discrepancy_id}/resolve` | Phase E — `spd_supervisor`; `correction_required`; last open → `awaiting_spd_review` |
| POST | `/api/v1/sessions/{id}/close` | Phase E — `spd_supervisor`; `awaiting_spd_review` → `closed` |

| GET | `/api/v1/sessions/reports/summary` | Reports v0.3.0 — `spd_supervisor`; sessions, discrepancies, AI–human agreement, daily activity |
| GET | `/api/v1/sessions/reports/kits` | Reports v0.3.0 — `spd_supervisor`; activity per kit (paginated) |
| GET | `/api/v1/sessions/reports/operators` | Reports v0.3.0 — `spd_supervisor`; activity per Operator (paginated) |
| GET | `/api/v1/sessions/reports/options` | Reports v0.3.0 — `spd_supervisor`; kits, operators and statuses for the filters |

Reports are read-only aggregates in PostgreSQL READ ONLY transactions, scoped to the caller's institution
in every query (`repositories/report_repository.py`, `services/session_reports.py`; XML: `schemas/session-reports.xsd`).
Definitions and the AI–human rule: `../../GUIA_REPORTES.md`.

**Session / Counting API v1: implemented** — every endpoint of the PEF-DS09 §1.3.4 (v0.1.7) table,
including resolve with `resolved` / `lost` / `recovered`. Camera, WebSocket, Evidence, a real Audit
Service, Notifications and the API Gateway are not part of this service.

## Writes (Phase C)

- **One transaction per write.** Domain rows and the matching `access_audit` row commit together; if
  anything fails (including the audit insert) everything rolls back. PostgreSQL outage → 503; any
  other failure → 500; no SQL, constraint names or stack traces in responses.
- **Locking.** The decisive row is locked first with `SELECT ... FOR UPDATE` (the operation on create;
  the work_session on inventory, start, vision-counts and count-report) and state is evaluated after the lock, so a concurrent
  writer gets 409. The row is locked alone and joined data is read afterwards (a join inside
  FOR UPDATE would make a concurrently updated row disappear under PostgreSQL's re-check).
- **Transitional audit (D5).** `adapters/audit_adapter.py` is the only writer of `access_audit`:
  actions `session.scheduled`, `session.inventory_updated`, `session.start`; actor user, work_session id,
  institution, outcome `success`, `correlation_id` (exactly the X-Correlation-ID answered to the client: DS09 v0.1.5 caps it at 64
  characters = the VARCHAR(64) column, and a longer header is replaced by a UUID, never truncated). No IP, token or request body.
- **POST /sessions** validates everything against the caller's institution (422 `invalid_reference`
  without saying why): existing `scheduled` operation (other status → 409), active operator with an
  effective `station_operator` role of the tenant, active station in a tenant room (the operation's
  room when it has one), active kit of the tenant, active phase, active families. It creates only
  `work_session` + `expected_inventory` (+ audit): never operation, patient, physicians, agreement,
  reservations, and it does not change `operation.status`.
- **Stock check ≠ reservation.** Each line must be ≤ the available pieces (`instrument` of the tenant,
  active, cycle status `available`). This is a point-in-time validation: no `instrument_reservation`
  is created and availability can change after commit.
- **Line provenance (DS04 §1.3.5 / DS09).** Create: `kit_snapshot` when the family is in the kit with the
  same quantity, otherwise `schedule_additional`. Replace: unchanged lines keep their source; new or
  changed lines follow the kit rule while `scheduled` and become `live_add` while `in_progress`.
- **Start** decides `capture_mode` once: `vision` if the session has a DS07 agreement with
  `purpose_quality_ops` and a privacy notice already in force (`effective_at` ≤ now), otherwise
  `manual_no_privacy`; start never fails for lack of agreement. It sets `started_at`, sets phase `start`
  only when none, copies the kit lines only when the session has no expected inventory (legacy rows),
  writes one `session_open` (`user_id` = operator, payload `{phase_code, capture_mode}`) and the
  `session.start` audit. It does **not** bind or call VisionWorker even when `capture_mode = vision`;
  the capture pipeline starts on its own.
- **No create idempotency.** DS04/DS09 define neither one-session-per-operation nor an idempotency key:
  a double submit of `POST /sessions` creates two sessions.

## Count facts and human validation (Phase D)

Expected (server snapshot) vs AI suggestion (stored vision result) vs Human (reported quantity):

| Comparison | Result |
| :--- | :--- |
| AI suggestion ≠ human | `human_correction` on that family's `manual_count` (justification = `notes`, required) |
| Human ≠ expected | `discrepancy` (`shortage` if lower, `surplus` if higher; `reason_code` required) |

Example: expected 2, AI 1, human 2 → correction yes, discrepancy no. Expected 2, AI 2, human 1 →
correction yes, `shortage`. Manual session, expected 2, human 1 → no correction, `shortage`.

- **Vision facts only.** `POST vision-counts` receives normalized results
  `{client_event_id, occurred_at, model_version, trigger, counts[]}`: never video, camera, frames,
  boxes, JPEG/base64, `video_t`, `run_id` or transport metadata (unknown fields → 400). One call =
  one `auto_count` (`family_id` NULL, `user_id` NULL — the model produced it, not the operator;
  `expected_quantity`/`detected_quantity` = batch totals). Families must belong to the session's
  expected inventory (422 otherwise), but need not all appear; `instrument_family.active` is not
  re-checked (the session snapshot is authoritative). No VisionWorker client, no new dependency.
- **Idempotency** lives only in PostgreSQL: `count_event.client_event_id` UNIQUE with
  `INSERT ... ON CONFLICT DO NOTHING`. Replay with the same session and logical content
  (`occurred_at` instant, `model_version`, `trigger`, counts in any order) → 200 with the same
  `count_event_id` (also after the session moved on); different content or session → 409.
  Concurrent duplicates: 201 + 200 (identical) or 201 + 409 (different), one row.
- **Final vision result is authoritative.** The AI suggestion of a family is its quantity in the
  last `auto_count` with `trigger = final` (`occurred_at` DESC, `id` DESC); 0 if the family is not
  in that batch. `stable_window` batches are intermediate and never used. The report's
  `suggested_quantity` must equal it, otherwise 409 (the browser cannot forge the suggestion).
- **Human fallback.** A `vision` session without any final result reports with
  `suggested_quantity = null` (no correction is possible); `capture_mode` stays `vision`.
  `manual_no_privacy` sessions must send null (400 otherwise).
- **Count report** covers every expected family exactly once (missing/duplicate → 400, foreign →
  422); expected quantities come only from `expected_inventory`. It writes one `manual_count` per
  family (`detected_quantity` = reported; payload `{reason_code}` when given), the corrections and
  discrepancies (canonical untranslated description, e.g. `Shortfall on <family name>: expected 2,
  reported 1.`; `origin_event_id` = the manual_count), one `manual_close` (payload: capture_mode,
  open_discrepancies, human_corrections, next_status), sets `ended_at` and the status
  (`correction_required` with open discrepancies, else `awaiting_spd_review`) and the
  `session.manual_close` audit row — one transaction, full rollback on any failure. Afterwards the
  expected inventory is frozen by the state machine (PUT → 409).
- **Where the AI suggestion lives.** It is not copied into `manual_count`: it is the stored final
  `auto_count`, which cannot change after the report (no auto_count is accepted once the session
  leaves `in_progress`), so the decision is reconstructable as final auto_count + `manual_count` +
  `human_correction`.
- **`reason_code`** (`cat_discrepancy_reason`; `other` is not a code → 422) is the operator's
  declared cause; the discrepancy reason is the objective `shortage`/`surplus`.
- **`notes`** exist only as `human_correction.justification`: required (non-blank) when there is a
  correction (AI suggestion not null and ≠ reported); null/absent otherwise — AI = human, vision
  without final, manual sessions (400 if sent: never accepted to be discarded, never stored in
  `manual_count.payload`).
- **No audit row per vision batch** (DS04 v0.2.2, DS09 v0.1.6 rule 5 exception): the `auto_count`
  is the authoritative fact (`user_id` NULL), `stable_window` batches may be frequent, and the
  technical logs keep the X-Correlation-ID.
- **Order of `GET /counts`:** `occurred_at`; on equal instants `session_open` first, then the session
  facts (`auto_count`, `manual_count`, any other type), then `manual_close`, then `session_close`;
  finally `id`. A report's `manual_count` rows (one instant) therefore always precede its `manual_close`.

Request XML roots: `session_request` (with `expected_lines/line`), `expected_inventory_update`
(with `lines/line`), `start_request` (empty), `vision_counts` (with `counts/count`), `count_report`
(with `reports/report`). Response roots `vision_count_result`, `count_report_result`. Unknown
fields (e.g. `institution_id`) → 400.
Out of v1 altogether: abort, phase change, any video/camera/frame/WebSocket endpoint.

## SPD review (Phase E)

- **Lock order** (PEF-DS03 §1.3.9.1, shared by every writer, so they serialize without deadlocks):
  `work_session` → `discrepancy` → `instrument` (by id) → `instrument_reservation` (by id). Each row is
  locked alone and its state is re-read after the lock. A real race resolve(lost) vs close on the same
  piece and reservation is tested in both orders.
- **Resolve** (`{notes, outcome}`; XML `resolve_request` → `resolve_result`): same tenant (404),
  session `correction_required` (409), discrepancy of that session (404) and still open (409).
  Sets `resolved`/`resolved_at`, keeps family, quantities, reason and origin event, and appends
  `\nSPD resolution (<outcome>): <notes>` (untranslated, trimmed) to `description` — DS04 §1.3.7 has no
  resolution-notes column, so this is temporary compatibility. Last open → `awaiting_spd_review`
  (never closed automatically). Audit `discrepancy.resolve`; no `correction_applied` event.
- **Physical piece — `lost` / `recovered` (DS09 v0.1.7, DS03 §1.3.9.1).** `instrument_id`
  (`instrument.id`) is required for them and forbidden for `resolved` (400); the server never picks a
  piece from the family. The piece must exist, belong to the tenant and to the discrepancy's family
  (else 422 `invalid_reference`, one message). `lost`: active piece in `available`/`reserved`/`in_use`
  → `lost`, `active = false`, releasing the active reservation of that piece only (operation of the same
  institution). `recovered`: `lost` → `available`, `active = true` (stock counts only active pieces), no
  reservation created. Other origins → 409 (`details.cycle_status_code`, `details.active`). Exactly one
  `instrument_cycle_event` per transition (resulting status, session, operation, canonical note).
- **Close** (`{material_recovered: true}`; XML `close_request` → `session`): same tenant, status
  `awaiting_spd_review`, 0 open discrepancies (409 `details.open_discrepancies`). Releases the
  active reservations of the session's operation whose piece belongs to the tenant; pieces that are
  active and `available` / `reserved` / `in_use` move to `sterilization` with one
  `instrument_cycle_event` each; lost, inactive, retired, maintenance or already sterilizing pieces
  keep their state (PEF-DS03 §1.3.9.1). No reservation is
  created, other operations/tenants are never touched and `operation.status` is unchanged. Sets
  `closed`, `closed_by_user_id`, `ended_at` only if missing, one `session_close` event (payload:
  material_recovered and the two counters) and audit `session.spd_close`.
- Every step of resolve and close is one transaction: any failure (including the audit row) rolls
  back the discrepancy, the session, reservations, instruments and cycle events.

## Access rules

Per action: reads need `station_operator` or `spd_supervisor`; create, inventory, resolve and
close need `spd_supervisor`; start, vision-counts and count-report need `station_operator` and being the
assigned operator (404 otherwise).


1. **Auth** (same contract as Catalog): `Authorization: Bearer <access_token>`; signature with exactly
   `JWT_ALGORITHM` (HS256 by default; any other algorithm or `none` → 401); `exp`, `type=access`,
   `sub`, `jti`; Redis `auth:jwt:{jti}` present, not revoked, `token_type=access`, same `user_id`;
   user and institution active in PostgreSQL, Redis institution = `user.institution_id`.
2. **Roles:** effective roles from `user_role` → `role` of the user's institution (never only the JWT
   `role` claim). Only `station_operator` and `spd_supervisor` get in; `it_admin` and any other role →
   **403** on every endpoint, before any session lookup.
3. **Tenant:** tenant = `user.institution_id`; tenant(session) = `operation.institution_id`. Nothing
   from query, body or headers can change it. Sessions without `operation_id` are never returned
   (404 on detail) — there is no fallback through `user_id`.
4. **Scope:** `spd_supervisor` → every session of the tenant. `station_operator` → only sessions with
   `work_session.user_id` = the user. A user holding both roles gets the **supervisor** scope
   (DS09 does not define multi-role; decision of this phase).
5. **Not found:** unknown id, malformed UUID, other tenant, not assigned (operator) or operation-less
   session all answer the same **404** `not_found`. Sub-resources are loaded only after the session
   itself is authorized, so no side channel reveals their existence.

## Read-only compatibility with legacy `count_event` rows

The legacy portal wrote some rows that predate DS04 v0.2.1. `GET /counts` reads them without
modifying anything (no data migration):

| Legacy situation | API behaviour |
| :--- | :--- |
| `manual_count.detected_quantity` NULL, quantity in `payload.reported_quantity` | `detected_quantity` = that value, only if it is a JSON integer 0..32767; otherwise `null` |
| `manual_count.payload.reason_code = "other"` (not in `cat_discrepancy_reason`) | `reason_code: null` (only catalog codes are exposed) |
| `auto_count` payload with boxes, top-k, scores, `run_id`, `video_t`, `frames_processed`, `board.families[]` | Only `family_id` + `detected_quantity` pairs are extracted into `counts`; everything else is dropped |
| `auto_count` with a video `reason` (`video_window`, …) and no `trigger` | `trigger: null` |

Future Session writes (Phase C+) store the human quantity in `count_event.detected_quantity` and the
normalized batch `{model_version, trigger, counts[]}` as DS04 v0.2.1 specifies.

## Configuration

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | built from `DB_*` | SQLAlchemy URL (`postgresql+psycopg://...`) |
| `REDIS_URL` | `redis://localhost:6379/0` | Same Redis as Auth |
| `JWT_SECRET_KEY` | — (required) | Same secret Auth uses |
| `JWT_ALGORITHM` | `HS256` | `HS256`, `HS384` or `HS512`; decoding accepts exactly this one |
| `SESSION_HOST` / `SESSION_PORT` | `0.0.0.0` / `5004` | Bind address/port (5004 is a local deployment choice, not part of the contract) |
| `SESSION_LOG_LEVEL` | `INFO` | |
| `SESSION_DEPENDENCY_TIMEOUT_SECONDS` | `3` | PostgreSQL/Redis connect timeout |

Outside Docker, `.env` is searched upward (`apps/SessionService/.env`, then the repository root `.env`).

## Run

```bash
# Docker (recommended), from the repository root
docker compose up -d --build session
curl http://127.0.0.1:5004/health/ready

# Without Docker (PostgreSQL + Redis running)
cd apps/SessionService
python -m venv .venv && . .venv/Scripts/activate   # Linux/macOS: . .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests
```

Integration tests use the real PostgreSQL/Redis (skipped with a message if unreachable). Each test
runs inside a transaction that is rolled back; institutions A/B, users, roles, operations, sessions,
counts and discrepancies are created with random codes, and Redis keys are deleted afterwards.
`SESSION_TEST_DATABASE_URL` / `SESSION_TEST_REDIS_URL` override the URLs.

## Examples

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:5001/api/v1/auth/login -H "Content-Type: application/json" \
  -d '{"email":"supervisor@instrumed.com","password":"DemopwdSPD78!"}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['access_token'])")

curl -s http://127.0.0.1:5004/api/v1/sessions -H "Authorization: Bearer $TOKEN"
curl -s http://127.0.0.1:5004/api/v1/sessions/<id>/counts -H "Authorization: Bearer $TOKEN"
curl -s http://127.0.0.1:5004/api/v1/sessions/<id> -H "Authorization: Bearer $TOKEN" -H "Accept: application/xml"
```

XML roots: `sessions/session`, `session`, `expected_inventory/line`, `counts/count` (each count has a
nested `counts/count` list for vision batches), `discrepancies/discrepancy`, `error_response`, `health`.
