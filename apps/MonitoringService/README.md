# PEF Monitoring Service — Monitoring API v1

Technical health of the PEF components (PEF-DS09 §1.3.6 complement v0.1.0). One image, two processes:
- `main.py` (gunicorn, port 5008): the read API below;
- `collector.py`: checks every component once per interval and stores the results in PostgreSQL.

Integration guide: `../../GUIA_MONITORING.md`.

| Method | Path | Access |
| :--- | :--- | :--- |
| GET | `/api/v1/monitoring/services` | it_admin (detail), spd_supervisor (status only) |
| GET | `/api/v1/monitoring/services/{name}` | it_admin |
| GET | `/api/v1/monitoring/history` | it_admin |
| GET | `/api/v1/monitoring/summary` | it_admin (availability + latency), spd_supervisor (availability) |
| GET | `/health` (header `X-Last-Run-Age-Seconds`), `/health/live`, `/health/ready` | public |
| GET | `/api/v1/monitoring/docs`, `/openapi.yaml`, `/schemas/monitoring.xsd` | public |

What each component's check uses:

| Component | Check |
| :--- | :--- |
| web, auth, catalog, session, evidence, audit, notifications | `GET <target>/health/ready` (DS09 JSON with `checks`) |
| vision | `GET <target>/health` (`weights_present`) |
| garage | admin API `GET <target>/health` |
| postgres | `SELECT 1` |
| redis | `PING` |
| mongo | `ping` |
| notifications-worker | its connection in `pg_stat_activity` (`application_name = pef-notifications-worker`) and the age of the oldest pending outbox event |

Each check measures its own latency, runs in parallel with the others, and has its own timeout.

The schema is `data/migrations/007_monitoring.sql`. It is idempotent; a copy lives in `migrations/` for the image. Both processes apply it at start-up under an advisory lock. Retention: rows older than `MONITORING_RETENTION_DAYS` are purged hourly.

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements-dev.txt
python main.py                      # API :5008
python collector.py                 # collector (logs one line per cycle)
python -m pytest -q                 # needs PostgreSQL/Redis (MONITORING_TEST_DATABASE_URL / MONITORING_TEST_REDIS_URL)
```

| Variable | Default |
| :--- | :--- |
| `MONITORING_INTERVAL_SECONDS` | 30 (minimum 5) |
| `MONITORING_CHECK_TIMEOUT_SECONDS` | 3 |
| `MONITORING_DEGRADED_LATENCY_MS` | 1500 |
| `MONITORING_WORKER_BACKLOG_SECONDS` | 120 |
| `MONITORING_STALE_AFTER_SECONDS` | 3 × interval |
| `MONITORING_RETENTION_DAYS` | 7 |
| `MONITORING_TARGET_<NAME>_URL` | empty = not configured (`unknown`) |
| `MONITORING_MONGO_URL` | — |
