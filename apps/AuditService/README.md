# PEF Audit / Event Service — Audit API v1

Read API over the existing PEF audit and event trail (PEF-DS09 §1.3.6 complement v0.1.0):
`access_audit` (PEF-DS07, written by Auth, Catalog, Session, Evidence and the portal) and
`count_event` (PEF-DS04, the counting facts). No second log, no insert endpoint; queries run in
PostgreSQL READ ONLY transactions. Integration guide: `../../GUIA_AUDIT.md`.

| Method | Path | Notes |
| :--- | :--- | :--- |
| GET | `/api/v1/audit/events` | Filters, pagination, sort; scope applied by role |
| GET | `/api/v1/audit/events/{id}` | One event (404 outside the scope) |
| GET | `/api/v1/audit/events/summary` | Aggregates with the same filters |
| GET | `/health`, `/health/live`, `/health/ready` | PostgreSQL + Redis |
| GET | `/api/v1/audit/docs`, `/openapi.yaml`, `/schemas/audit.xsd` | Swagger UI, contract, XML schema |

Access: Bearer access token of Auth Service (JWT + Redis session + active user). `it_admin` → its
institution; `spd_supervisor` → the operational trail of its institution; others → 403 (audited).

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements-dev.txt
python main.py                      # :5006
python -m pytest -q                 # needs PostgreSQL/Redis (AUDIT_TEST_DATABASE_URL / AUDIT_TEST_REDIS_URL)
```
