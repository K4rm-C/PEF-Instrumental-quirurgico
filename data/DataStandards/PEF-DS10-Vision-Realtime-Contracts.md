---
id: PEF-DS10
title: Vision Realtime Contracts
version: "0.1.0"
status: draft
owner: PEF Core / Vision / Station
applies_to: [CS, Worker]
stores: []
related: [PEF-DS03, PEF-DS04, PEF-DS06]
last_updated: 2026-11-19
---

# PEF-DS10 · Vision Realtime Contracts

## INTRODUCTION

This standard defines the **Secure WebSocket (WSS) messages** exchanged between the **Counting Station** and the **VisionWorker** (YOLO).

It is the real-time perception channel: the station submits frames (or frame metadata) and receives suggested detections. It is **not** the business source of truth: opening/closing sessions, discrepancies, and `count_event` records flow through the Web Backend (DS04/DS09). Checkpoints live in MongoDB (DS06) via EvidenceWorker.

Lab runtime: `matrix_v1` pipeline (assign), sticky holds, soft-name dwell, live WS. Host process execution is typically used for GPU acceleration; Docker Vision is CPU-only. Typical path `/ws`; on LAN the browser connects to `wss://<LAN>:5443/ws` (portal-lan) forwarding to the host worker. Portal auth on that same origin may use relative `/auth` (see DS01/DS09); an absolute auth URL is not required.

---

## 1. Standard Explanation

### 1.1 Definitions

| Term | Meaning |
| :--- | :--- |
| **WSS** | WebSocket over TLS. |
| **Hot path** | Inference + UI push; no MP4 encoding or PG writes. |
| **Suggested detection** | Model proposal; validated by human/business rules. |
| **client_event_id** | Idempotency UUID used when the backend materializes a fact. |

### 1.2 Usage

1. The station authenticates the WSS bind (lab: `VISION_WS_SECRET` HMAC ticket; the `auth_token` field carries this bind secret, not the portal login JWT) and opens WSS with `session_id`.
2. It sends sampled frames (or chunks depending on implementation).
3. The worker replies with `detections.result` (live hot path; public session payload may include holds / `soft_named`). `count.suggestion` is not implemented in the current runtime.
4. The station may send `session.cancel` to stop live capture.
5. When facts must be persisted: Backend writes `count_event`. Checkpoints/clips: EvidenceWorker (DS05/DS06).

**What the worker does not do:** open/close sessions, accept discrepancies, write to PostgreSQL as an owner, or own Garage/MongoDB evidence storage.

---

### 1.3 Conventions

Transport: WSS. Payload: JSON (UTF-8). Every message includes a `type` discriminator.

Logical relationship: 1 station ↔ 1 worker connection per typical active session (N technical connections possible; one business session).

---

#### 1.3.1 Message `session.bind` (station → worker)

| Field | Type | Nullable | Meaning |
| :--- | :--- | :--- | :--- |
| `type` | string | NO | `session.bind` |
| `session_id` | uuid | NO | Counting session (DS04). |
| `station_id` | uuid | YES | Station (DS02). |
| `model_version_pin` | string | YES | Tag of the model **pinned** on `work_session.yolo_model_id` (`yolo_model.version_tag`). The worker must not substitute the global `active` model on already-open sessions. MongoDB `checkpoint_frame.model_version` must match this tag. |
| `auth_token` | string | NO | Bind secret (lab: HMAC ticket; do not confuse with AuthService JWT). May already be validated during the handshake. |
| `correlation_id` | string | YES | Tracing ID. |

```json
{
  "type": "session.bind",
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "station_id": "f0f0f0f0-f0f0-f0f0-f0f0-f0f0f0f0f0f0",
  "model_version_pin": null,
  "auth_token": "[JWT_REDACTED]",
  "correlation_id": "corr-ws-001"
}
```

---

#### 1.3.2 Message `frame.submit` (station → worker)

| Field | Type | Nullable | Meaning |
| :--- | :--- | :--- | :--- |
| `type` | string | NO | `frame.submit` |
| `session_id` | uuid | NO | |
| `frame_id` | uuid | NO | Client-assigned frame ID. |
| `captured_at` | string ISO | NO | Capture timestamp. |
| `image_base64` | string | YES | Base64-encoded JPEG (when no binary channel is used). |
| `image_content_type` | string | YES | `image/jpeg` |
| `roi` | object | YES | Per-frame ROI override. |
| `roi.x` | number | YES | |
| `roi.y` | number | YES | |
| `roi.w` | number | YES | |
| `roi.h` | number | YES | |
| `correlation_id` | string | YES | |

```json
{
  "type": "frame.submit",
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "frame_id": "ff000001-0000-4000-8000-000000000001",
  "captured_at": "2026-09-05T18:20:00.120Z",
  "image_base64": "[BASE64_JPEG_REDACTED]",
  "image_content_type": "image/jpeg",
  "roi": { "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.7 },
  "correlation_id": "corr-ws-001"
}
```

---

#### 1.3.3 Message `detections.result` (worker → station)

| Field | Type | Nullable | Meaning |
| :--- | :--- | :--- | :--- |
| `type` | string | NO | `detections.result` |
| `session_id` | uuid | NO | |
| `frame_id` | uuid | NO | Responded frame ID. |
| `model_version` | string | NO | Model tag used. |
| `inferred_at` | string ISO | NO | |
| `latency_ms` | number | YES | |
| `detections` | array | NO | List (may be empty). |
| `detections[].family_code` | string | NO | Mapped class (DS03). |
| `detections[].yolo_class_id` | int | YES | Raw integer class ID. |
| `detections[].confidence` | number | NO | 0..1 |
| `detections[].bbox` | array | NO | `[x,y,w,h]` normalized or pixels (declared via `bbox_space`). |
| `bbox_space` | string | NO | `normalized` \| `pixels` |
| `counts_by_family` | array | YES | Suggested aggregate. |
| `counts_by_family[].family_code` | string | NO | |
| `counts_by_family[].detected_quantity` | int | NO | |
| `correlation_id` | string | YES | |

```json
{
  "type": "detections.result",
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "frame_id": "ff000001-0000-4000-8000-000000000001",
  "model_version": "yolo11n-pef-2026-03",
  "inferred_at": "2026-09-05T18:20:00.168Z",
  "latency_ms": 48.0,
  "bbox_space": "normalized",
  "detections": [
    {
      "family_code": "mayo_hegar_needle_holder",
      "yolo_class_id": 0,
      "confidence": 0.93,
      "bbox": [0.12, 0.20, 0.18, 0.25]
    }
  ],
  "counts_by_family": [
    {
      "family_code": "mayo_hegar_needle_holder",
      "detected_quantity": 1
    }
  ],
  "correlation_id": "corr-ws-001"
}
```

---

#### 1.3.4 Message `count.suggestion` (Not Implemented)

Historical contract message type. Not emitted in the current runtime (`matrix_v1`): the live hot path runs on `detections.result` (+ holds / `soft_named` in public session state). Do not use as a persistence path; facts are written by Backend/SessionService (DS04).

---

#### 1.3.5 Message `session.cancel` (station → worker)

Stops live capture for the currently bound session. Implemented in `VisionWorker` (`live_ws`).

| Field | Type | Nullable | Meaning |
| :--- | :--- | :--- | :--- |
| `type` | string | NO | `session.cancel` |
| `session_id` | uuid | YES | Must match the bound session if sent. |

```json
{
  "type": "session.cancel",
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc"
}
```

After cancellation, the worker responds with `worker.status` (`state: stopping`) and closes the live stream.

---

#### 1.3.6 Message `worker.status` / `error`

**`worker.status`**

| Field | Type | Meaning |
| :--- | :--- | :--- |
| `type` | string | `worker.status` |
| `session_id` | uuid\|null | |
| `state` | string | `ready` \| `busy` \| `degraded` \| `stopping` |
| `fps_avg` | number\|null | |
| `model_version` | string\|null | |
| `message` | string\|null | |

```json
{
  "type": "worker.status",
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "state": "ready",
  "fps_avg": 12.4,
  "model_version": "yolo11n-pef-2026-03",
  "message": null
}
```

**`error`**

| Field | Type | Meaning |
| :--- | :--- | :--- |
| `type` | string | `error` |
| `code` | string | `unauthorized` \| `invalid_frame` \| `model_unavailable` \| … |
| `message` | string | |
| `frame_id` | uuid\|null | |
| `correlation_id` | string\|null | |

```json
{
  "type": "error",
  "code": "invalid_frame",
  "message": "Empty or corrupted JPEG",
  "frame_id": "ff000001-0000-4000-8000-000000000001",
  "correlation_id": "corr-ws-001"
}
```

---

#### 1.3.7 Boundary Rules

1. If the model goes down, the station continues operating in manual mode via the Backend.
2. Clips/checkpoints run off the hot path (EvidenceWorker → DS05/DS06); WSS does not wait for MP4 encoding.
3. Never transmit patient PII over the frame bus.
4. Rate control: sample frames; do not flood unboundedly.
5. `matrix_v1` demo: assign + sticky hold + `soft_named` dwell live inside worker session state; do not redefine database tables here.

---

### 1.4 Governance and Ownership

| Aspect | Owner |
| :--- | :--- |
| WSS contract | Vision + Station |
| Fact persistence | Backend (worker does not own PG) |
| Model version | DS03 (`yolo_model`) |

---

### 1.5 Standard Rationale

**Design decisions taken**

1. **WSS for perception only**; business logic over HTTPS/Backend.
2. **Messages typed by `type`** with explicit fields.
3. **`client_event_id`** for idempotency when materializing counts (where applicable).
4. **Suggestion ≠ fact** until Backend writes `count_event`. When materializing `auto_count`, copy `detections[].confidence` (or that box's assigned score) into `payload.confidences` / `board.families[].confidences`. Do not store avg/best/worst.
5. **HMAC-ticket WSS bind** in lab (not user JWT); host process preferred for GPU.

**Rejected alternatives**

| Alternative | Why not |
| :--- | :--- |
| Worker writes directly to PG | Splits source of truth and complicates RBAC/auditing. |
| REST polling for frames | Unacceptable latency for operating room UI. |
| Single raw binary blob without JSON control envelope | Harder to version and debug message types. |
| Send accepted discrepancies over WSS | That is a clinical decision; belongs to Backend/station HTTP. |

---

## 2. Annex A – General Information

| Field | Value |
| :--- | :--- |
| Code | PEF-DS10 |
| Title | Vision Realtime Contracts |
| Version | 0.1.0 |
| Status | draft |
| Store | None of its own (WSS transport); effects in DS04/DS06 |
| Out of scope | REST (DS09); bucket bytes (DS05) |
| References | `apps/VisionWorker/`; DS05/DS06 (EvidenceWorker); root runbook `README.md` |
