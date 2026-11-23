---
id: PEF-DS05
title: Evidence and Object Storage
version: "0.2.0"
status: draft
owner: PEF Core / Evidence / Worker
applies_to: [BE, Worker, MS, WP]
stores: [PostgreSQL, ObjectStorage]
related: [PEF-DS03, PEF-DS04, PEF-DS06, PEF-DS07]
last_updated: 2026-11-19
---

# PEF-DS05 · Evidence and Object Storage

## INTRODUCTION

This standard defines **where binary bytes live** (JPEG, MP4 burst clip, model weights) and **how they are registered in PostgreSQL** without storing files inside the relational database.

Owned table: `media_asset`. Object storage (S3-compatible API) stores the binary object; PG stores metadata, URI, and purge lifecycle. Lab and on-prem production use Garage (S3-compatible); `storage_provider` continues to allow `gcs` / `minio` / `other` (in lab, Garage is typically tagged `minio` as an S3-compatible tag, without implying the MinIO product).

It does not define the MongoDB checkpoint document (PEF-DS06): that document **points** to `media_asset.id`. It does not define families or the YOLO registry (PEF-DS03): only the pointer from those tables. It does not define the full ARCO workflow (PEF-DS07), although it reuses `blocked_at` / `purge_*`.

---

## 1. Standard Explanation

### 1.1 Definitions

| Term | Meaning |
| :--- | :--- |
| **media_asset** | Metadata row for an object stored in object storage. |
| **Object storage** | Bucket of objects addressable by key (`bucket` + `object_key`). |
| **Canonical URI** | String stored in `gcs_uri` (historical column name; also valid for Garage/S3 or other S3-compatible stores). |
| **Purge** | Delete the object from the bucket and set `purged_at` in PG. |
| **Block** | `blocked_at` is set (e.g., ARCO); the object is not served even if it still exists. |

### 1.2 Usage

**Who writes:** EvidenceWorker (MP4 clips from the Redis ring `pef:evidence:frames:*` + triggers), VisionWorker (JPEGs to the ring; does not own the bucket), MLOps jobs (weights), Backend/Evidence Service (authorized uploads).  
**Who reads:** Portal (signed URL), MS Evidence, supervision panel / App.

**Typical flow**

1. VisionWorker publishes JPEG frames to the Redis ring; EvidenceWorker assembles clips off the hot path.
2. Upload bytes to the Garage (S3) bucket with a prefixed `object_key` (`institution_id/session_id/...`).
3. INSERT `media_asset` with URI, kind, hash, and retention.
4. Any consumer that needs the file (MongoDB checkpoint, `family_example`, `yolo_model`) stores only the UUID. Upload retries use the Redis queue `pef:evidence:upload_retry`.

---

### 1.3 Conventions

Under "References": this table has **no outgoing FKs**. Other tables point **to** it (N:0..1 from DS03).

Suggested key naming: `{institution_id}/{session_id|models}/{yyyy}/{uuid}.{ext}`.

---

#### 1.3.1 Table `media_asset`

**Purpose:** single registry entry for a binary object accessible by the system.

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK; referenced by other tables and MongoDB. |
| `gcs_uri` | TEXT | NO | — | Full logical access URI (`gs://`, `s3://`, or internal canonical URL). |
| `bucket` | VARCHAR(128) | NO | — | Bucket name. |
| `object_key` | TEXT | NO | — | Key inside the bucket. |
| `content_type` | VARCHAR(128) | YES | NULL | MIME type (`image/jpeg`, `video/mp4`, `video/webm`, `video/x-matroska`, `application/octet-stream`, …). |
| `kind` | VARCHAR(32) | NO | `'jpeg'` | Asset class: `jpeg`, `mp4`, `mkv`, `webm`, `gif` (legacy), `weights`, `other`. |
| `storage_provider` | VARCHAR(32) | NO | `'gcs'` | `gcs`, `minio`, `other`. |
| `sha256` | CHAR(64) | YES | NULL | Content hash (hex). |
| `size_bytes` | BIGINT | YES | NULL | Size in bytes (≥ 0 if provided). |
| `retention_until` | TIMESTAMPTZ | YES | NULL | Retention deadline (TTL job). |
| `purge_requested_at` | TIMESTAMPTZ | YES | NULL | When deletion was requested (TTL or ARCO). |
| `purged_at` | TIMESTAMPTZ | YES | NULL | When the object was actually deleted from storage. |
| `blocked_at` | TIMESTAMPTZ | YES | NULL | Serving block (do not issue signed URLs). |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Metadata creation timestamp. |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Last metadata update timestamp. |

**References**

| Field | Points to | Relationship | Rationale | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| — | — | — | No outgoing FKs; metadata leaf table. | — |

**Incoming references (cross-reference, not owned by this DS)**

| Source | Relationship | Rationale |
| :--- | :--- | :--- |
| `family_example.media_asset_id` (DS03) | N:0..1 | Family reference photo. |
| `yolo_model.media_asset_id` (DS03) | N:0..1 | Model weights file. |
| `checkpoint_frame.media_asset_id` (DS06, logical) | N:0..1 | Checkpoint evidence. |

**Constraints:** PK `id`; UNIQUE (`bucket`, `object_key`); CHECK `kind`; CHECK `storage_provider`; CHECK `size_bytes >= 0`; CHECK purge ordering (`purged_at` ≥ `purge_requested_at` when both are present).

**Lifecycle rules**

1. After uploading an object → INSERT with `retention_until` (90 days baseline, or 180 if the session has extended retention; see DS04/DS07).
2. Idempotent job: if `purge_requested_at` is set and `purged_at` is still null → delete from storage → set `purged_at = now()`.
3. If `blocked_at` is non-null → do not issue a signed URL even if the object remains in the bucket.
4. Weights (`kind = weights`) may have a separate retention policy (MLOps policy); they are not purged on the clinical TTL without review.
5. **Burst clip v1:** EvidenceWorker reads the ring (Redis → RAM, ~5–8 s of JPEGs) and encodes an **H.264 MP4**, `yuv420p`, **no audio**, one file / one checkpoint. It is lighter than GIF and plays natively in browsers. `kind = 'mp4'`, `content_type = 'video/mp4'`. `mkv` and `webm` are allowed in the CHECK; web UI prioritizes MP4. If encoding fails → fallback to a single JPEG (`kind = 'jpeg'`). `gif` is kept for legacy rows only.

**Complete example row (checkpoint JPEG)**

```json
{
  "id": "m1000001-0000-4000-8000-000000000001",
  "gcs_uri": "gs://pef-demo-evidence/11111111-1111-1111-1111-111111111111/cccccccc-cccc-cccc-cccc-cccccccccccc/2026/09/frame-001.jpg",
  "bucket": "pef-demo-evidence",
  "object_key": "11111111-1111-1111-1111-111111111111/cccccccc-cccc-cccc-cccc-cccccccccccc/2026/09/frame-001.jpg",
  "content_type": "image/jpeg",
  "kind": "jpeg",
  "storage_provider": "gcs",
  "sha256": "a3f5c8e2b91d0476f0aa1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcd",
  "size_bytes": 245760,
  "retention_until": "2026-12-04T18:20:00Z",
  "purge_requested_at": null,
  "purged_at": null,
  "blocked_at": null,
  "created_at": "2026-09-05T18:20:01Z",
  "updated_at": "2026-09-05T18:20:01Z"
}
```

**Complete example row (burst MP4)**

```json
{
  "id": "m1000003-0000-4000-8000-000000000003",
  "gcs_uri": "s3://evidence/11111111-1111-1111-1111-111111111111/cccccccc-cccc-cccc-cccc-cccccccccccc/2026/09/burst-disc.mp4",
  "bucket": "evidence",
  "object_key": "11111111-1111-1111-1111-111111111111/cccccccc-cccc-cccc-cccc-cccccccccccc/2026/09/burst-disc.mp4",
  "content_type": "video/mp4",
  "kind": "mp4",
  "storage_provider": "minio",
  "sha256": "b1c2d3e4f5061728394a5b6c7d8e9f00112233445566778899aabbccddeeff00",
  "size_bytes": 420000,
  "retention_until": "2027-03-04T18:25:00Z",
  "purge_requested_at": null,
  "purged_at": null,
  "blocked_at": null,
  "created_at": "2026-09-05T18:25:10Z",
  "updated_at": "2026-09-05T18:25:10Z"
}
```

**Complete example row (YOLO weights)**

```json
{
  "id": "m1000002-0000-4000-8000-000000000002",
  "gcs_uri": "gs://pef-demo-models/yolo11n-pef-2026-03.pt",
  "bucket": "pef-demo-models",
  "object_key": "yolo11n-pef-2026-03.pt",
  "content_type": "application/octet-stream",
  "kind": "weights",
  "storage_provider": "gcs",
  "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "size_bytes": 5600000,
  "retention_until": null,
  "purge_requested_at": null,
  "purged_at": null,
  "blocked_at": null,
  "created_at": "2026-03-20T17:55:00Z",
  "updated_at": "2026-03-20T17:55:00Z"
}
```

---

### 1.4 Governance and Ownership

| Aspect | Owner |
| :--- | :--- |
| Clinical evidence ingestion | EvidenceWorker + Backend / Evidence Service |
| Weights publication | Vision / MLOps |
| Purge job | Ops / Backend (idempotent) |
| ARCO blocking | Compliance via DS07 |

---

### 1.5 Standard Rationale

**Why it exists**

Binary bytes do not belong inside PostgreSQL or MongoDB as massive production BLOBs. PG needs to know *which* file exists and *until when*; the bucket stores the content.

**Design decisions taken**

1. **Single `media_asset` table** with a `kind` discriminator (JPEG, burst MP4, weights; `gif` legacy).
2. **Clip v1 = audio-free H.264 MP4**, not GIF: smaller size, better fidelity, native `<video>` playback.
3. **`storage_provider`** to support portability across GCS ↔ Garage (S3) or other S3-compatible providers without changing the schema.
4. **Purge/block fields on the same row** so the ARCO/TTL purge job has a single control plane.
5. **Column name `gcs_uri` retained** even when the provider is Garage (design legacy; value holds the canonical object URI).

**Rejected alternatives**

| Alternative | Why not |
| :--- | :--- |
| Store bytes in PG (`BYTEA`) | Bloats backups, slows replicas, complicates CDN/signed URLs. |
| Path only in MongoDB with no PG row | Portal and FHIR Attachment require SQL-queryable metadata; multi-store ARCO becomes harder. |
| Separate table per type (`jpeg_asset`, `mp4_asset`) | Duplicates purge lifecycle and FKs. |
| Animated GIF as v1 clip format | Heavier and lower fidelity than H.264 for the same burst. |
| MKV as default clip format | Poor browser playback; allowed in CHECK, not prioritized in UI. |
| Delete only the object and leave an orphan row without `purged_at` | Breaks inventory truth; the job must close the lifecycle. |

---

## 2. Annex A – General Information

| Field | Value |
| :--- | :--- |
| Code | PEF-DS05 |
| Title | Evidence and Object Storage |
| Version | 0.2.0 |
| Status | draft |
| Stores | PostgreSQL (`media_asset`); Object storage (on-prem Garage S3 today; other S3-compat / GCS by contract) |
| Out of scope | MongoDB document; ARCO legal policy (orchestrated in DS07); HTTP contract (DS09) |
| Reference DDL | `esquema_base_datos_v2.md` §5.5 |
