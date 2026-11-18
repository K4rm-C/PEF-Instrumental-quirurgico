# PEF Integration Service (MS-07)

Sync API for desktop / Android / machine clients, idempotent operations forwarded to the owner services, read-only
FHIR R4 projection (PEF-DS08) and FHIR deliveries. Guides: `GUIA_SINCRONIZACION.md`, `GUIA_INTEGRACION_FHIR.md`.

    python main.py      # API (INTEGRATION_PORT, default 5009); applies migration 014 (idempotent)
    python worker.py    # FHIR deliveries with retries + compaction of the change log
    python -m pytest -q # real PostgreSQL / Redis (INTEGRATION_TEST_DATABASE_URL / _REDIS_URL or DATABASE_URL / REDIS_URL)

| Variable | Default | Use |
| :--- | :--- | :--- |
| `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET_KEY`, `JWT_ALGORITHM` | — | Same as the other services |
| `INTEGRATION_SESSION_URL`, `INTEGRATION_CATALOG_URL`, `INTEGRATION_NOTIFICATIONS_URL` | empty | Owner services of the forwarded operations |
| `SYNC_CURSOR_SECRET` | derived from the JWT secret | HMAC key of the cursors |
| `SYNC_CURSOR_MAX_AGE_DAYS`, `SYNC_RETENTION_DAYS`, `SYNC_PAGE_MAX` | 30, 30, 500 | Cursor expiry, compaction, page size |
| `INTEGRATION_ALLOWED_HOSTS` | empty (nothing allowed) | FHIR destinations (host[:port]) |
| `INTEGRATION_ALLOW_PLAIN_HTTP` | false | Only for the isolated test stack |
| `INTEGRATION_DELIVERY_TIMEOUT_SECONDS`, `_MAX_ATTEMPTS`, `_BACKOFF_SECONDS` | 15, 5, 5 | Deliveries |
