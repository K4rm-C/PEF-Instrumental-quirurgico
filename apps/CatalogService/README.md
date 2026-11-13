# PEF Catalog Service

Independent REST microservice that exposes the instrument catalog (families, instruments, kits)
to the Android (JSON) and Desktop (XML) clients.

- **Contracts:** [PEF-DS09 §1.3.3](../../data/DataStandards/PEF-DS09-API-Exchange-Contracts.md) (owner of the HTTP contract).
- **Fields and constraints:** [PEF-DS03](../../data/DataStandards/PEF-DS03-Instrument-Kits-and-Model-Registry.md) (`instrument_family`, `instrument`, `kit`, `kit_item`, catalogs).
- **Identity / JWT / Redis:** [PEF-DS01](../../data/DataStandards/PEF-DS01-Identity-and-Credentials.md), compatible with `apps/BackendAuthService`.
- **OpenAPI:** [openapi.yaml](openapi.yaml).

It is not a Blueprint of BackendWebFlask and does not import BackendWebFlask or BackendAuthService.
It reads the existing PostgreSQL schema (no migrations, no schema changes) and only uses Redis to
validate Auth's JWT sessions (no caching). The web portal keeps using BackendWebFlask directly.

## Endpoints

| Method | Path | Access | Notes |
| :--- | :--- | :--- | :--- |
| GET | `/api/v1/catalog/families` | JWT | Global catalog, ordered by `code`. |
| POST | `/api/v1/catalog/families` | JWT + `it_admin` | JSON or XML body; 201 with the family item. |
| GET | `/api/v1/catalog/instruments` | JWT | Only the caller's institution. |
| GET | `/api/v1/catalog/kits` | JWT | Only the caller's institution, without items. |
| GET | `/api/v1/catalog/kits/{id}` | JWT | Detail + items; 404 if unknown, malformed or another institution's. |
| GET | `/health/live` | — | Process alive (no dependency checks). |
| GET | `/health/database` | — | `SELECT 1` on PostgreSQL. |
| GET | `/health/redis` | — | `PING` on Redis. |
| GET | `/health/ready` | — | 200 only if PostgreSQL and Redis are OK. |

Errors use the DS09 envelope `{"error", "message", "correlation_id", "details"}`
(`invalid_request` 400, `unauthorized` 401, `forbidden` 403, `not_found` 404, `conflict` 409,
`validation_error` / `invalid_reference` 422, `dependency_unavailable` 503).

### Authentication

`Authorization: Bearer <access_token>` (cookies are not read). For each request the service:

1. verifies signature, algorithm (`JWT_ALGORITHM`), expiry and the `type=access`, `sub`, `jti` claims;
2. reads `auth:jwt:{jti}` in Redis: it must exist, not be revoked, have `token_type=access` and the same `user_id`;
3. checks in PostgreSQL that the user and its institution are still active;
4. resolves the tenant from `user.institution_id` and the effective roles from `user_role` → `role`
   (roles of the user's institution). `it_admin` is checked there, never only in the JWT `role` claim.

### JSON / XML

`Accept: application/xml` → XML; anything else (`application/json`, `*/*`, missing) → JSON.
`POST /families` accepts `Content-Type: application/json` or `application/xml`. Both are parsed into the
same internal representation, validated once, and serialized from the same result.
XML element names equal JSON field names; lists are wrappers (`families/family`, `instruments/instrument`,
`kits/kit`, `items/item`); `null` is `xsi:nil="true"`. DTD / entity declarations are rejected.

### X-Correlation-ID

Echoed when present and matching `^[A-Za-z0-9._:-]{1,64}$`; otherwise (missing, invalid or longer than 64 characters) a
UUID is generated; it is never truncated (PEF-DS09 v0.1.5). It is
returned in the response header, in every error body and in the service logs (`[corr=...]`).

## Configuration

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | built from `DB_*` | SQLAlchemy URL (`postgresql+psycopg://...`). |
| `REDIS_URL` | `redis://localhost:6379/0` | Same Redis as Auth. |
| `JWT_SECRET_KEY` | — (required) | Must be the same secret Auth uses. |
| `JWT_ALGORITHM` | `HS256` | `HS256`, `HS384` or `HS512`. |
| `CATALOG_HOST` | `0.0.0.0` | Bind address for `python main.py`. |
| `CATALOG_PORT` | `5003` | Listen port (local deployment choice, not part of the contract). |
| `CATALOG_LOG_LEVEL` | `INFO` | Python log level. |
| `CATALOG_MAX_CONTENT_LENGTH` | `65536` | Max request body in bytes (413 above). |
| `CATALOG_DEPENDENCY_TIMEOUT_SECONDS` | `3` | Connect/read timeout for PostgreSQL and Redis. |

Outside Docker, `.env` is searched upward: `apps/CatalogService/.env` first, otherwise the repository root `.env`.
See [.env.example](.env.example).

## Run with Docker (recommended)

The service is part of the root `docker-compose.yml` (service `catalog`; Compose names the container `<project>-catalog-1`).
Inside the network it uses `postgres:5432` and `redis:6379`.

```bash
# from the repository root, with .env created from .env.example
docker compose up -d --build catalog
docker compose ps catalog          # should become "healthy"
curl http://127.0.0.1:5003/health/ready
```

## Run without Docker

PostgreSQL and Redis must be running (`docker compose up -d postgres redis`).

```powershell
cd apps\CatalogService
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py                     # http://127.0.0.1:5003
```

(Linux/macOS: `source .venv/bin/activate`; production-like: `gunicorn --bind 0.0.0.0:5003 "main:create_app()"`.)

## Tests

```powershell
cd apps\CatalogService
pip install -r requirements-dev.txt
python -m pytest tests
```

Integration tests need PostgreSQL and Redis (they are skipped with a message if unreachable). They never
leave data behind: each test runs inside a PostgreSQL transaction that is rolled back (the service's own
transactions become savepoints), creates its own institutions/users/roles/families/kits with random codes,
and deletes the Redis keys it creates. Tokens are minted with a test-only secret.
`CATALOG_TEST_DATABASE_URL` / `CATALOG_TEST_REDIS_URL` override the URLs from `.env`.

## Examples

Get a token from the Auth API v1 (returned in the response body):

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:5001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@instrumed.com","password":"DemopwdADM78!"}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
```

JSON:

```bash
curl -s http://127.0.0.1:5003/api/v1/catalog/families -H "Authorization: Bearer $TOKEN"
curl -s http://127.0.0.1:5003/api/v1/catalog/instruments -H "Authorization: Bearer $TOKEN"
curl -s http://127.0.0.1:5003/api/v1/catalog/kits -H "Authorization: Bearer $TOKEN"
curl -s http://127.0.0.1:5003/api/v1/catalog/kits/51515151-0000-4000-8000-000000000001 \
  -H "Authorization: Bearer $TOKEN" -H "X-Correlation-ID: demo-123"

curl -s -X POST http://127.0.0.1:5003/api/v1/catalog/families \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"code":"mayo_hegar_needle_holder","name":"Portaagujas Mayo-Hegar","category_code":"suturing"}'
```

XML:

```bash
curl -s http://127.0.0.1:5003/api/v1/catalog/kits/51515151-0000-4000-8000-000000000001 \
  -H "Authorization: Bearer $TOKEN" -H "Accept: application/xml"

curl -s -X POST http://127.0.0.1:5003/api/v1/catalog/families \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/xml" -H "Accept: application/xml" \
  -d '<family><code>mayo_hegar_needle_holder</code><name>Portaagujas Mayo-Hegar</name><category_code>suturing</category_code></family>'
```

Valid `category_code` values come from `cat_instrument_category` (seed: `cutting`, `dissection`, `grasping`,
`hemostasis`, `retraction`, `suction`, `suturing`). There is no DELETE endpoint: families created while
experimenting must be removed manually from the database.

## Structure

```
main.py                 app factory, correlation id, request logging
config.py               settings from environment
database.py             PostgreSQL engine / Redis client
auth.py                 JWT + Redis session + PostgreSQL identity, require_auth()
errors.py               ApiError and DS09 error envelope handlers
serializers.py          content negotiation, JSON/XML rendering and parsing
repositories/           SQLAlchemy Core tables (subset) and queries
services/               validation and use cases
routes/                 catalog and health blueprints
tests/                  pytest suite
openapi.yaml            OpenAPI 3.0 contract of this service
```

## Future shared library (proposal only)

Auth validation (`auth.py`), the error envelope (`errors.py`) and JSON/XML handling (`serializers.py`)
will be needed by the next microservices. Once a second service exists, consider extracting them into a
small versioned package (e.g. `libs/pef_service_common`) installed by each service. It was intentionally
not done here: each service must stay installable on its own and duplication is cheaper than premature
coupling at this stage.

## Management endpoints (DS09 §1.3.3 complement v0.1.2)

Family detail/update, instrument detail/create/update (cycle history, change reason), kit create/update
(versioned, procedure links moved), reference catalogs and `expand=detail`. Writes are audited in
`access_audit` in the same transaction; denied management attempts are recorded as `DENIED_*`. Contract:
`openapi.yaml` (served at `/api/v1/catalog/openapi.yaml`, Swagger UI at `/api/v1/catalog/docs`) and
`schemas/catalog.xsd` (`/api/v1/catalog/schemas/catalog.xsd`). Portal integration: `GUIA_CATALOG.md`.
