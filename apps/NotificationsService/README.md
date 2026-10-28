# PEF Notifications Service — Notifications API v1

Internal notifications of the authenticated user (PEF-DS09 §1.3.6 complement v0.1.0). Two processes
from the same image:

- `main.py` (gunicorn, port 5007): the read/mark API below;
- `worker.py`: the outbox consumer that turns business events into notifications.

Integration guide: `../../GUIA_NOTIFICATIONS.md`.

| Method | Path | Notes |
| :--- | :--- | :--- |
| GET | `/api/v1/notifications` | `status`, `type`, `from`, `to`, `page`, `per_page` ≤ 100, `sort`; includes `unread` |
| GET | `/api/v1/notifications/{id}` | Own notification only (otherwise 404) |
| GET | `/api/v1/notifications/unread-count` | `{unread}` |
| PATCH | `/api/v1/notifications/{id}/read` | Idempotent |
| PATCH | `/api/v1/notifications/read-all` | Optional body `{type?, before?}` (JSON or XML, XSD-validated) |
| GET | `/health` (header `X-Outbox-Pending`), `/health/live`, `/health/ready` | PostgreSQL + Redis |
| GET | `/api/v1/notifications/docs`, `/openapi.yaml`, `/schemas/notifications.xsd` | Swagger UI, contract, XML schema |

There is no creation endpoint. Events are captured by database triggers in `notification_outbox`
(`data/migrations/006_notifications.sql`, idempotent; a copy lives in `migrations/` for the image).
Both processes apply the migration at start-up under an advisory lock.

Delivery guarantees:
- **Durable capture:** the producer's own transaction writes the outbox row.
- **Safety net:** a periodic reconciliation sweep of the source tables.
- **No duplicates:** `UNIQUE (source_table, source_id)` on the outbox and `UNIQUE (source_table,
  source_id, recipient_user_id, type)` on notifications.
- **Concurrent workers:** rows are claimed with `FOR UPDATE SKIP LOCKED`.

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements-dev.txt
python main.py                      # API :5007
python worker.py                    # consumer (logs every processed event)
python -m pytest -q                 # needs PostgreSQL/Redis (NOTIFICATIONS_TEST_DATABASE_URL / NOTIFICATIONS_TEST_REDIS_URL)
```

Worker settings:

| Variable | Default |
| :--- | :--- |
| `NOTIFICATIONS_WORKER_POLL_SECONDS` | 2 |
| `NOTIFICATIONS_WORKER_BATCH_SIZE` | 50 |
| `NOTIFICATIONS_WORKER_MAX_ATTEMPTS` | 10 |
| `NOTIFICATIONS_RECONCILE_EVERY_SECONDS` | 60 |
| `NOTIFICATIONS_RECONCILE_WINDOW_DAYS` | 7 |
| `NOTIFICATIONS_APPLY_MIGRATIONS` | true |
