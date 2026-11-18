# SupervisorService

SupervisorService owns the SPD-supervisor operational API. It reads identity and clinical context from the shared PostgreSQL database, verifies Auth-issued bearer access tokens against their Redis session records, and derives authorization from the user's current PostgreSQL role assignment. Every protected route requires `spd_supervisor`; `GET /health` is the only unauthenticated route.

The service models the existing migration contracts rather than creating service-specific tables. Operational writes and their `access_audit` rows share a PostgreSQL transaction. Clinical records are scoped to the authenticated user's institution. Sessions and discrepancies are lifecycle-managed; they are not hard-deleted.

## Run

Start the shared PostgreSQL and Redis containers first. In the service folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

`GET http://127.0.0.1:5004/health` reports PostgreSQL and Redis availability. Configure `DATABASE_URL` (or `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, and `DB_NAME`), `JWT_SECRET_KEY`, `JWT_ALGORITHM`, and `REDIS_URL` with the same values used by BackendAuthService. The service listens on `SUPERVISOR_SERVICE_PORT` (default `5004`).

## API

All routes except `/health` require `Authorization: Bearer <access-token>`. An access token must have a valid signature and expiration, `type=access`, a `jti` whose `auth:jwt:{jti}` Redis record is present and unrevoked, and a subject matching the Redis `user_id`. The user and institution must be active, and `spd_supervisor` must be assigned to that user in PostgreSQL.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/me` | Authenticated supervisor identity and current roles |
| GET | `/api/v1/catalogs?names=operators,procedures,rooms,stations,kits,patients,physicians,surgical_roles,phases,privacy_notices,families` | Institution-scoped scheduling and management catalogs; `names` is optional. Also supports `categories`, `cycle_statuses`, `institutions`, `roles`, `active_families`, `procedure_types`, and `operating_rooms` |
| GET | `/api/v1/kits/{kit_id}/expected-inventory` | Load kit contents and current stock for scheduling |
| POST | `/api/v1/inventory/stock-recheck` | Recalculate available stock and semaphore for submitted family/quantity lines |
| GET/POST | `/api/v1/instruments` | List/search/paginate instruments; create an instrument |
| GET/PUT | `/api/v1/instruments/{instrument_id}` | Read/update instrument family, cycle state, or active state |
| GET/POST | `/api/v1/kits` | List active institution kits; create a kit |
| GET/PUT | `/api/v1/kits/{kit_id}` | Read/update a kit. Updates create a new version and preserve history |
| GET | `/api/v1/dashboard` | Session status totals and open discrepancy count |
| GET | `/api/v1/sessions?status=&search=&date=YYYY-MM-DD&page=&per_page=` | Paginated session list |
| POST | `/api/v1/sessions` | Schedule a session with operation, assigned operator, team, kit, and expected inventory |
| GET | `/api/v1/sessions/{session_id}` | Session details, expected inventory, events, discrepancies, and privacy agreement |
| PUT | `/api/v1/sessions/{session_id}` | Replace the metadata/team/inventory for a scheduled session |
| PUT | `/api/v1/sessions/{session_id}/inventory` | Update expected inventory for scheduled or in-progress sessions |
| POST | `/api/v1/sessions/{session_id}/privacy-agreement` | Record/update the pre-start privacy agreement |
| POST | `/api/v1/sessions/{session_id}/close` | Close an SPD-reviewed session after confirming material recovery |
| GET | `/api/v1/discrepancies?status=open\|resolved\|all&search=&page=&per_page=` | Paginated institution-scoped discrepancy queue |
| GET | `/api/v1/discrepancies/{discrepancy_id}` | Discrepancy details |
| POST | `/api/v1/discrepancies/{discrepancy_id}/resolve` | Resolve with required notes; optionally mark lost or recovered |

Schedule requests use JSON fields `operator_user_id`, `procedure_type_id`, `room_id`, `station_id`, `kit_id`, `patient_id` (optional), `scheduled_at` (ISO 8601), `physicians` (`[{ "physician_id", "surgical_role_code" }]`), and `expected_lines` (`[{ "family_id", "expected_quantity" }]`). Inventory updates accept `expected_lines`; discrepancy resolution accepts `notes` and optional boolean `mark_lost` / `mark_recovered`; closing accepts `material_recovered: true`.

Errors use the AdminService-compatible shape `{"error":{"code":"...","message":"...","field":"..."}}`. No route deletes session history, inventory snapshots, or discrepancies.

## Validation

```powershell
python -m pytest tests
```
