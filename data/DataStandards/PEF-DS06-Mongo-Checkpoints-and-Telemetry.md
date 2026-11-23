---
id: PEF-DS06
title: Mongo Checkpoints and Telemetry
version: "0.2.0"
status: draft
owner: PEF Core / Vision / Worker
applies_to: [Worker, BE, WP]
stores: [MongoDB]
related: [PEF-DS04, PEF-DS05, PEF-DS03]
last_updated: 2026-11-19
---

# PEF-DS06 · Mongo Checkpoints and Telemetry

## INTRODUCTION

This standard defines **MongoDB documents** for occasional visual evidence and lightweight worker telemetry.

Owned collections: `checkpoint_frame`, `session_telemetry`. Evidence runtime: typical DB `pef_evidence` (env `EVIDENCE_MONGO_DB`).

Reason vocabulary: codes aligned to `cat_checkpoint_reason` in PostgreSQL (this DS documents MongoDB usage; the catalog row lives in PG with no FK toward MongoDB).

It does not define SQL count facts (`count_event`, PEF-DS04). It does not define file bytes (PEF-DS05): only `media_asset_id` + redundant URI.

---

## 1. Standard Explanation

### 1.1 Definitions

| Term | Meaning |
| :--- | :--- |
| **Checkpoint** | Document freezing a visual moment and detections for a session. |
| **Reason (`reason`)** | Typed cause (`start`, `close`, `hourly`, `state_change`, `discrepancy`, `manual_pin`). |
| **Burst / clip** | Multiple frames from the Redis/RAM ring → one MP4 (H.264) via EvidenceWorker → **one** checkpoint (not N documents). |
| **Telemetry** | Runtime metrics (FPS, latency); not legal evidence. |
| **Per-session cap** | Maximum 30–50 `checkpoint_frame` documents per `session_id`. |

### 1.2 Usage

EvidenceWorker writes checkpoints and telemetry to MongoDB (`pef_evidence`) off the hot path (encode + Garage + `media_asset` + MongoDB) when policy requires it. VisionWorker feeds the Redis ring and live WSS; it does not write checkpoints or own evidence MongoDB. Backend/portal query by `session_id`. Fan-out: detection → WSS UI; `count_event` (DS04) is materialized by Backend/portal; MongoDB checkpoint when applicable.

---

### 1.3 Conventions

Links to PG are **logical** (UUID strings), with no MongoDB FK.

Logical relationships: `work_session` 1 → 0..N `checkpoint_frame`; `media_asset` 1 → 0..N checkpoints.

---

#### 1.3.1 Vocabulary `cat_checkpoint_reason` (PG Reference)

PostgreSQL code table (no FK to MongoDB). Standard catalog fields:

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(32) | NO | — | Code used in MongoDB `reason`. |
| `name` | VARCHAR(80) | NO | — | Label. |

**Complete example row**

```json
{
  "id": "d1000001-0000-4000-8000-000000000001",
  "code": "discrepancy",
  "name": "Discrepancy recorded"
}
```

Expected codes: `start`, `close`, `hourly`, `state_change`, `discrepancy`, `manual_pin`.

---

#### 1.3.2 Collection `checkpoint_frame`

**Purpose:** semi-structured evidence per session.

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `_id` | ObjectId | NO | auto | MongoDB PK. |
| `session_id` | string (UUID) | NO | — | = `work_session.id` (DS04). |
| `operation_id` | string (UUID) | YES | — | Denormalized for dashboards. |
| `media_asset_id` | string (UUID) | YES | — | = `media_asset.id` (DS05). |
| `gcs_uri` | string | YES | — | Redundant URI for fast reads. |
| `model_version` | string | YES | — | `yolo_model.version_tag` **of the model pinned on the session** (`work_session.yolo_model_id` → `yolo_model.version_tag`, DS04/DS03). Does not redefine the MongoDB contract. |
| `reason` | string | NO | — | Reason code (see catalog). |
| `phase_id` | string (UUID) | YES | — | Copy of phase at capture time. |
| `phase_code` | string | YES | — | Denormalized phase code. |
| `burst_frame_count` | int | YES | — | Number of ring-buffer frames used in the clip. |
| `burst_interval_ms` | int | YES | — | Approximate spacing between frames. |
| `captured_at` | ISODate | NO | — | UTC timestamp. |
| `detections` | array | NO | `[]` | Detections from the key frame. |
| `detections[].family_code` | string | NO* | — | Family code. |
| `detections[].confidence` | number | YES | — | Model score. |
| `detections[].bbox` | array/object | YES | — | Box `[x,y,w,h]` or equivalent. |
| `retention_until` | ISODate | NO | — | MongoDB TTL + media alignment. |
| `session_checkpoint_seq` | int | NO | — | Ascending sequence number per session. |

\*In each array element, `family_code` is required when a detection is present.

**References (logical)**

| Field | Points to | Relationship | Rationale | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `session_id` | `work_session.id` (DS04) | N:1 | Temporal owner of the checkpoint. | N/A (logical); purged by job/TTL |
| `operation_id` | `operation.id` (DS02) | N:0..1 | Dashboard filter. | N/A |
| `media_asset_id` | `media_asset.id` (DS05) | N:0..1 | Clip (MP4) or fallback JPEG bytes. | N/A; job deletes doc + marks media for purge |

**Indexes:** `(session_id, captured_at)`; TTL on `retention_until`; sparse `operation_id`, `phase_code`.

**Rules**

1. Maximum 30–50 docs per session; when exceeded, delete oldest `interval`/`hourly` docs first; preserve `start`, `close`, `state_change`, `discrepancy`, `manual_pin`.
2. One clip (MP4) = one document.
3. Debounce rich bursts: 10–30 s per session.
4. Fallback: if MP4 encode fails → single JPEG. `gif` is not the v1 format.
5. Retention: +90 days; +180 if `work_session.extended_retention`.

**Complete example document**

```json
{
  "_id": "66fa12ab34cd56ef78901234",
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "operation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "media_asset_id": "m1000003-0000-4000-8000-000000000003",
  "gcs_uri": "s3://evidence/11111111-1111-1111-1111-111111111111/cccccccc-cccc-cccc-cccc-cccccccccccc/2026/09/burst-disc.mp4",
  "model_version": "yolo11n-pef-2026-03",
  "reason": "discrepancy",
  "phase_id": "b1000004-0000-4000-8000-000000000004",
  "phase_code": "pre_count",
  "burst_frame_count": 12,
  "burst_interval_ms": 250,
  "captured_at": "2026-09-05T18:25:00.000Z",
  "detections": [
    {
      "family_code": "mayo_hegar_needle_holder",
      "confidence": 0.93,
      "bbox": [0.12, 0.20, 0.18, 0.25]
    },
    {
      "family_code": "metzenbaum_scissors",
      "confidence": 0.88,
      "bbox": [0.40, 0.35, 0.15, 0.22]
    }
  ],
  "retention_until": "2027-03-04T18:25:00.000Z",
  "session_checkpoint_seq": 4
}
```

---

#### 1.3.3 Collection `session_telemetry`

**Purpose:** runtime metrics; not legal evidence.

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `_id` | ObjectId | NO | auto | PK. |
| `session_id` | string (UUID) | NO | — | Session. |
| `ts` | ISODate | NO | — | Sample timestamp. |
| `fps_avg` | number | YES | — | Window average FPS. |
| `latency_ms` | number | YES | — | Inference/pipeline latency. |
| `drops` | int | YES | — | Dropped frames. |
| `roi_ok` | boolean | YES | — | Whether the ROI is valid/stable. |

**References (logical)**

| Field | Points to | Relationship | Rationale | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `session_id` | `work_session.id` | N:1 | Groups telemetry. | N/A; 7-day TTL or capped collection |

Does not count toward the checkpoint cap.

**Complete example document**

```json
{
  "_id": "66fa12ab34cd56ef78909999",
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "ts": "2026-09-05T18:21:00.000Z",
  "fps_avg": 12.4,
  "latency_ms": 48.2,
  "drops": 1,
  "roi_ok": true
}
```

---

### 1.4 Governance and Ownership

| Aspect | Owner |
| :--- | :--- |
| Checkpoint/telemetry writes | EvidenceWorker (MongoDB `pef_evidence`); VisionWorker does not write |
| Cap/TTL policy | PEF Core / Ops |
| Dashboard reads | Web Backend |

---

### 1.5 Standard Rationale

**Why MongoDB here**

Checkpoints are variable-shape documents (detections array, burst metadata) generated at relatively high frequency. Putting bounding boxes in every `count_event` would bloat PostgreSQL.

**Design decisions taken**

1. **PG = business facts; MongoDB = visual detail/telemetry.**
2. **30–50 cap** with deletion priority by `reason`.
3. **MP4 encoding off the hot path** (EvidenceWorker); Redis ring `pef:evidence:frames:*` + RAM buffer.
4. **String `reason`** aligned with PG catalog without cross-engine FKs.
5. **Redundant URI** on the document to avoid forcing a PG join for every thumbnail.

**Rejected alternatives**

| Alternative | Why not |
| :--- | :--- |
| Everything in PostgreSQL JSONB | Possible, but TTL indexes and telemetry volume fit MongoDB better. |
| One document per burst frame | Violates the cap and multiplies I/O. |
| GIF as the v1 container | Worse size/fidelity than MP4 for the same frame queue. |
| Worker writes `count_event` directly | Splits the source of truth; the design routes SQL facts through Backend/adapter (DS04). |
| No `media_asset_id`, URI only | Loses the unified purge lifecycle in PG. |

---

## 2. Annex A – General Information

| Field | Value |
| :--- | :--- |
| Code | PEF-DS06 |
| Title | Mongo Checkpoints and Telemetry |
| Version | 0.2.0 |
| Status | draft |
| Store | MongoDB (`checkpoint_frame`, `session_telemetry`); vocabulary `cat_checkpoint_reason` in PG |
| Out of scope | `count_event`; storage bytes; frame-by-frame WSS messages (DS10) |
| References | DS05 (`media_asset` / Garage); ER [`../ER PostgreSQL-MongoDB-v3.jpg`](../ER%20PostgreSQL-MongoDB-v3.jpg); root runbook `README.md` |
