# PEF Evidence / File Service — v1 (Phase A/B/C/D)

Independent REST microservice for clinical **evidence** (JPEG/GIF produced during a counting session):
metadata in PostgreSQL (`media_asset`) and bytes in S3-compatible object storage (Garage today).

- **Contract:** [PEF-DS09 §1.3.5](../../data/DataStandards/PEF-DS09-API-Exchange-Contracts.md) (v0.1.8).
- **Data and storage rules:** [PEF-DS05](../../data/DataStandards/PEF-DS05-Evidence-and-Object-Storage.md) (v0.2.1, §1.3.2).
- **OpenAPI (what is implemented now):** [openapi.yaml](openapi.yaml).

**Phase A** = infrastructure (configuration, JWT auth context, PostgreSQL, Redis, S3/Garage adapter,
X-Correlation-ID, DS09 error envelope, health). **Phase B** = read API (metadata list + detail with an
audited signed URL). **Phase C** = upload (`POST /api/v1/evidence`, multipart). **Phase D** = internal
jobs: retention/purge and storage↔DB reconciliation (CLI, no HTTP API). There is no DELETE/block/purge
endpoint. Outside Evidence v1: block/ARCO workflow, Vision/portal integration, Mongo checkpoints, DS08
machine identity.

## Ownership

| Concept | Owner |
| :--- | :--- |
| `media_asset` (clinical rows, `session_id` not null) | **Evidence** (PEF-DS05) |
| Objects in the evidence bucket | **Evidence** (only Evidence writes; clients never get S3 credentials) |
| `work_session`, `operation` | Session (read-only here: tenant, state, `capture_mode`, retention flag) |
| `user`, `role`, `user_role`, `institution` | Auth / DS01 (read-only here: authentication context) |
| Vision boxes / checkpoints / telemetry | DS06 (Mongo) — not a dependency of Evidence |

```
Android / Desktop / producer ──Bearer JWT──▶ Evidence (:5005)
                                               ├── PostgreSQL  (auth context; media_asset from Phase B)
                                               ├── Redis       (auth:jwt:{jti} sessions issued by Auth)
                                               └── Garage S3   (evidence bucket, internal endpoint)
```

It does not import any other service, and it does not depend on Mongo, VisionWorker, or Session/Catalog
over HTTP: it reads the shared PostgreSQL.

## Endpoints

| Method | Path | Phase |
| :--- | :--- | :--- |
| GET | `/health/live`, `/health/database`, `/health/redis`, `/health/storage`, `/health/ready` | **A (implemented)** |
| POST | `/api/v1/evidence` (multipart upload, assigned operator) | **C (implemented)** |
| GET | `/api/v1/evidence/{id}` (metadata + 300 s signed URL, audited) | **B (implemented)** |
| GET | `/api/v1/sessions/{id}/evidence` (metadata list, no URLs, not audited) | **B (implemented)** |

Health is public (no token) and only reports `ok` / `error` per dependency — never credentials,
endpoints, bucket internals or raw errors. `ready` = PostgreSQL **and** Redis **and** storage.
`/health/storage` is one read-only `HeadBucket` on the evidence bucket: 503 if the endpoint is
unreachable, the credentials are rejected or the bucket does not exist. The service never creates
buckets.

## Read API (Phase B)

- **Tenant:** `media_asset.session_id` → `work_session.operation_id` → `operation.institution_id` must
  equal the user's institution. `bucket`, `object_key`, `gcs_uri` and the key prefix are never used to
  authorize. An asset with `session_id` NULL (DS03/MLOps) or a session without operation has no clinical
  tenant → 404 (no fallback by `user_id`).
- **Roles:** `station_operator` → only sessions assigned to them (`work_session.user_id`);
  `spd_supervisor` → every session of the tenant; both roles → supervisor scope; `it_admin` and any
  other role → 403 before any lookup. Out of scope, unknown or malformed id → one indistinguishable 404.
- **List** `GET /api/v1/sessions/{id}/evidence`: the session is authorized first, then its assets are
  read. Metadata only (DS09 fields), `created_at` ASC, `id` ASC, blocked/purged included with their
  flags, `signed_url` / `signed_url_expires_at` always null, no audit, no storage call.
- **Detail** `GET /api/v1/evidence/{id}`: blocked or purged → 200 metadata with null URL (no signing,
  no audit). Otherwise a SigV4 presigned GET signed against `S3_PUBLIC_ENDPOINT`, valid
  `EVIDENCE_SIGNED_URL_TTL_SECONDS` (300 by default), `signed_url_expires_at` = generation time + TTL.
  Expired `retention_until` alone does not stop serving (the future purge job handles it).
- **Audit:** every emitted URL writes one `access_audit` row (`evidence.download_url`,
  `resource_type = media_asset`, actor, tenant, exact correlation id; never the URL, bucket or key) via
  `adapters/audit_adapter.py`. The URL is returned only after that row is committed; if the audit fails
  the response is 500 without URL. Two calls → two URLs → two audit rows (no caching).
- **No HeadObject per request:** `media_asset` is the authoritative metadata and presigning is local;
  a missing blob is a storage↔DB reconciliation concern. Consequence: a detail can still emit a URL
  while `/health/storage` is 503 (signing failure itself → 503 `dependency: storage`, no audit).
- Responses never expose `bucket`, `object_key`, `gcs_uri`, endpoints or credentials as fields (the
  signed URL path naturally contains the bucket and key). JSON or XML (`evidence`,
  `evidence_list/evidence`, `xsi:nil` for nulls).

## Upload (Phase C)

- **Request:** `multipart/form-data` with exactly `session_id` (text UUID), `kind` (`jpeg` | `gif`) and
  `file`. Missing, repeated or extra parts (e.g. `object_key`, `sha256`, `retention_until`, `reason`)
  and non-multipart bodies (JSON/XML/octet-stream/base64) → 400. Filename and declared part type are ignored.
- **Who / which session:** `station_operator` assigned to the session (`work_session.user_id`), same
  tenant; a supervisor — even multi-role — has no upload scope (SPD-only → 403). Unknown, other tenant,
  not assigned or without operation → 404. `capture_mode` must be `vision` (else 409); state must be
  `in_progress`, `correction_required` or `awaiting_spd_review` (else 409, `details.status_code`).
- **Streaming:** Werkzeug writes the file part to an on-disk `tempfile.TemporaryFile` (custom request
  class; never the in-memory spool); the service reads it in 64 KiB chunks counting bytes (stop at the
  limit → 413), updating SHA-256 and keeping only the first bytes for the signature check, then rewinds
  and PUTs the same file. Every temporary file is closed (= deleted) in a `finally`.
- **Limits:** per file JPEG 2 MiB (`EVIDENCE_JPEG_MAX_BYTES`), GIF 5 MiB (`EVIDENCE_GIF_MAX_BYTES`),
  counted on real bytes. Separately, the whole multipart envelope is capped at the largest limit + 64 KiB
  (Flask `MAX_CONTENT_LENGTH`). The 64 KiB JSON/XML body limit of other services does not apply.
- **Type:** decided by the bytes — JPEG `FF D8 FF`, GIF `GIF87a`/`GIF89a` — and must equal `kind` (else
  422); stored `content_type` is the canonical `image/jpeg` / `image/gif`. Signature check only (no
  Pillow/OpenCV: DS05 asks for nothing deeper). Empty file → 400.
- **Identity and key:** the server generates the asset UUID and the key
  `{institution_id}/{session_id}/{yyyy}/{uuid}.{jpg|gif}` (DS05 §1.3.2 rule 2; `yyyy` from `created_at`
  UTC). Client filenames never reach the key (no path traversal).
- **Storage:** PUT through `S3_INTERNAL_ENDPOINT`, bucket `S3_BUCKET_EVIDENCE`, only `ContentType` set
  (no user metadata: no filename, correlation id, token or PII), then HeadObject: stored size must equal
  the counted size. `storage_provider = other` (Garage), `gcs_uri = s3://{bucket}/{key}`.
- **SHA-256 semantics:** Garage exposes no verifiable SHA-256 (the ETag is not one and is not used), so
  `sha256` (lowercase hex) is the digest of the exact temporary file handed to the PUT; HeadObject
  verifies the size; the integration test re-downloads and re-hashes.
- **Retention:** `retention_until` = `created_at` + 90 days, or + 180 when `work_session.extended_retention`.
- **Storage-first transaction:** no PostgreSQL lock is held during storage I/O. First read (no lock) →
  stream/validate → PUT → HEAD → ONE transaction: `work_session` locked alone (FOR UPDATE), tenant/owner/
  capture_mode/state re-validated, INSERT `media_asset` + `access_audit` `evidence.upload`, COMMIT. A
  session closed while the object was being stored is caught here (409).
- **Compensation:** any failure after the PUT (HEAD, size mismatch → 503; re-validation → 404/409;
  INSERT/audit/commit → 500) deletes the object. If that delete fails too, the original error is kept and
  an `evidence orphan candidate: bucket=… object_key=… asset=…` line is logged for the future
  reconciliation job (no DB row is invented, nothing is returned to the client).
- **Response:** 201 with the Evidence metadata; `signed_url` / `signed_url_expires_at` are null (use the
  detail endpoint, which audits `evidence.download_url`). No idempotency key: two identical uploads create
  two assets, two objects and two audits (no SHA dedupe).

## Internal jobs (Phase D)

No HTTP endpoint and no scheduler: run them explicitly (from `apps/EvidenceService`, or in the container).

```bash
python -m jobs.purge [--dry-run]
python -m jobs.reconcile [--dry-run] [--delete-orphans]
docker compose exec evidence python -m jobs.purge --dry-run
docker compose exec evidence python -m jobs.reconcile
```

Both check PostgreSQL and the bucket first, tag every log line with a run id (`run=<uuid>`, the job's
correlation id — there is no HTTP request) and print one JSON summary line. **Exit codes:** `0` completed
without failures (also with nothing to do); `1` completed but some items failed (they stay for the next
run); `2` configuration or dependency error (nothing done).

**Purge / retention** (`python -m jobs.purge`; DS05 §1.3.2 rule 12)
- Candidates: clinical rows (`session_id` not null) of the evidence bucket with `purged_at` NULL and
  `purge_requested_at` set or `retention_until` ≤ now. `blocked_at` alone never purges (blocked ≠ purged);
  DS03/MLOps rows and other buckets are out of scope.
- Purge = DeleteObject (internal endpoint, no GET/HEAD/signing) + `purged_at = now`. The row is never
  deleted and keeps every other column; the read API then shows `purged: true` and no URL.
- Locking: candidates are read in keyset batches of `EVIDENCE_PURGE_BATCH_SIZE` without locks; each asset
  is then purged in its own short transaction — `SELECT … FOR UPDATE SKIP LOCKED` re-checking that it is
  still a candidate, DeleteObject, `UPDATE … WHERE purged_at IS NULL`, COMMIT. The row lock covers one
  bounded DELETE (dependency timeout, one attempt) and blocks no reader or upload; it guarantees the
  irreversible delete happens only while the row is verifiably a candidate. A concurrent worker skips the
  locked row instead of waiting; `purged_at` is set exactly once.
- Idempotent: deleting a missing object succeeds, so an asset whose object is gone is still marked. If the
  delete succeeded but the update/commit failed, the row stays unpurged and the next run deletes again
  (no-op) and marks it — eventual consistency; the blob is never restored or copied. A storage failure
  leaves `purged_at` NULL (still a candidate).
- `--dry-run` only logs `asset=<id> reason=retention|purge_requested` (no delete, no update).
- Audit: `access_audit` accepts only a `user` or `client` actor; the job is neither, so no
  `evidence.purge` row is written (no fake actor). The technical log is the record (DS05 rule 13 gap).

**Reconciliation** (`python -m jobs.reconcile`; DS05 §1.3.2 rule 14)
- One streaming merge of the paginated ListObjectsV2 listing (ascending key order) and the bucket's
  `media_asset` rows (keyset batches ordered by `object_key COLLATE "C"`, the same byte order): bounded
  memory, one pass, no blob read. A listing out of order aborts the run (exit 2).
- **Orphan object** (no row): only keys with the exact clinical format
  `{institution_uuid}/{session_uuid}/{yyyy}/{asset_uuid}.{jpg|gif}` count; anything else (`models/…`,
  unknown or malformed keys) is reported as non-clinical and never deleted. Objects younger than
  `EVIDENCE_ORPHAN_GRACE_SECONDS` (age from `LastModified`; protects uploads between PUT and INSERT) are
  ignored. Older ones are reported; they are deleted only with `--delete-orphans` (never with
  `--dry-run`), after re-querying PostgreSQL for (bucket, key) right before the delete.
- **Missing blob** (non-purged row, blocked included, without object): confirmed with HeadObject and a
  re-read of the row, then only reported (`missing blob candidate asset=<id>`): no metadata change, no
  `purged_at`, no row delete, no re-upload. A purged row without object is correct; a purged row whose
  object still exists is reported (`purged_with_blob`).
- No `access_audit` (an orphan has no clinical resource); deletions are logged.

## Auth

Same contract as Session/Catalog: `Authorization: Bearer <access_token>` (cookies are ignored); JWT
decoded with exactly `JWT_ALGORITHM` (any other HS*, RS*, or `none` → 401); claims `exp`, `sub`,
`jti`, `type=access`; Redis `auth:jwt:{jti}` present, not revoked, `token_type=access`, same user;
user and institution active in PostgreSQL and the institution recorded by Auth still the user's.
Roles are the effective `user_role` → `role` rows of the user's institution (never the JWT `role`
claim).

## Configuration

| Variable | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` (or `DB_HOST`/`DB_NAME`/`DB_USER`/`DB_PASSWORD`/`DB_PORT`) | yes | — | PostgreSQL (`postgresql+psycopg://...`) |
| `REDIS_URL` | yes | — | Same Redis as Auth |
| `JWT_SECRET_KEY` | yes | — | Same secret Auth uses |
| `JWT_ALGORITHM` | no | `HS256` | `HS256`, `HS384` or `HS512`; decoding accepts exactly this one |
| `S3_INTERNAL_ENDPOINT` | yes | — | Endpoint the service uses for storage operations (compose: `http://garage:3900`) |
| `S3_PUBLIC_ENDPOINT` | yes | — | Endpoint reachable by browsers/clients; signed download URLs are signed against it (Phase B). Local: `http://localhost:3900` |
| `S3_REGION` | yes | — | Garage `s3_region` (`garage`) |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | yes | — | Garage key (compose maps them from `S3_ACCESS_KEY` / `S3_SECRET_KEY`) |
| `S3_BUCKET_EVIDENCE` | yes | — | Evidence bucket (`evidence`) |
| `EVIDENCE_SIGNED_URL_TTL_SECONDS` | no | `300` | Signed URL lifetime |
| `EVIDENCE_JPEG_MAX_BYTES` / `EVIDENCE_GIF_MAX_BYTES` | no | `2097152` / `5242880` | API v1 upload limits (Phase C); not PostgreSQL constraints |
| `EVIDENCE_PURGE_BATCH_SIZE` | no | `100` | Rows per purge/reconcile batch (operational, not clinical policy) |
| `EVIDENCE_ORPHAN_GRACE_SECONDS` | no | `3600` | Minimum age before reconciliation may delete an unreferenced clinical object (operational safeguard, not retention) |
| `EVIDENCE_HOST` / `EVIDENCE_PORT` | no | `0.0.0.0` / `5005` | Bind address/port (5005 is a local deployment choice, not part of the contract) |
| `EVIDENCE_LOG_LEVEL` | no | `INFO` | |
| `EVIDENCE_DEPENDENCY_TIMEOUT_SECONDS` | no | `3` | PostgreSQL / Redis / S3 connect and read timeout |

Missing or invalid values stop the service at startup with a message that names the variables only;
the settings `repr` redacts every secret, and no secret, credential-bearing URL or raw storage error
is ever logged or returned.

**Internal vs public endpoint.** Inside Docker the service talks to Garage by its compose name
(`http://garage:3900`); a URL signed against that host would be useless to a browser, so download
URLs (Phase B) are signed directly against `S3_PUBLIC_ENDPOINT` (never by rewriting the host of an
already signed URL).

**Garage specifics.** boto3/botocore with `endpoint_url` = internal endpoint, SigV4 with
`S3_REGION`, path-style addressing, botocore's optional checksums only when required, 1 attempt and
the dependency timeout. The bucket `evidence` is created by Garage itself (`--default-bucket` in
`docker-compose.yml`), not by this service. Garage's admin (3903) and RPC (3901) ports are still
published to the host — a known hardening gap, outside this service.

## Migration 004 (required)

Evidence relies on `media_asset.session_id` (FK `work_session`, `ON DELETE RESTRICT`, index
`idx_media_asset_session_created`) from `data/migrations/004_media_asset_session.sql`. A **new**
PostgreSQL volume runs it automatically (mounted as `04_…` in `docker-entrypoint-initdb.d`, after 001,
002 and 003). An **existing** database must apply it once by hand (idempotent):

```powershell
Get-Content data/migrations/004_media_asset_session.sql | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U <POSTGRES_USER> -d InstruMed
```

## Run

```bash
# Docker (recommended), from the repository root
docker compose up -d --build evidence
curl http://127.0.0.1:5005/health/ready

# Without Docker (PostgreSQL, Redis and Garage running): copy .env.example to .env and fill it
cd apps/EvidenceService
python -m venv .venv && . .venv/Scripts/activate   # Linux/macOS: . .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests
```

PostgreSQL/Redis integration tests run inside a rolled-back transaction with random identities and
delete their Redis keys; they are skipped with a message if the stores are unreachable. Storage is a
fake by default; the optional real-Garage tests (credentials from the repository `.env`, skipped if
Garage is not usable) only perform `HeadBucket` and verify that the bucket list and object count do not
change; the Phase B integration tests write a temporary non-clinical object, fetch it through the signed
URL returned by the API (exact bytes) and delete it. Session and `media_asset` fixtures are inserted
directly in the rolled-back transaction (there is no write endpoint).
`EVIDENCE_TEST_DATABASE_URL` / `EVIDENCE_TEST_REDIS_URL` override the URLs.
