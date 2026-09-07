---
id: PEF-DS06
title: Mongo Checkpoints and Telemetry
version: "0.1.0"
status: draft
owner: PEF Core / Visión / Worker
applies_to: [Worker, BE, WP]
stores: [MongoDB]
related: [PEF-DS04, PEF-DS05, PEF-DS03]
last_updated: 2026-09-06
---

# PEF-DS06 · Mongo Checkpoints and Telemetry

## INTRODUCCION

Este estándar define **documentos MongoDB** de evidencia visual ocasional y telemetría ligera del worker.

Colecciones dueñas: `checkpoint_frame`, `session_telemetry`.

Vocabulario de motivo: códigos alineados a `cat_checkpoint_reason` en PostgreSQL (este DS documenta el uso en Mongo; la fila catálogo vive en PG sin FK hacia Mongo).

No define hechos de conteo SQL (`count_event`, PEF-DS04). No define bytes del archivo (PEF-DS05): solo `media_asset_id` + URI redundante.

---

## 1. Explicación del estándar

### 1.1 Definiciones

| Término | Significado |
| :--- | :--- |
| **Checkpoint** | Documento que congela un momento visual/detecciones de una sesión. |
| **Razón (`reason`)** | Motivo tipado (`start`, `close`, `hourly`, `state_change`, `discrepancy`, `manual_pin`). |
| **Ráfaga GIF** | Varios frames del ring buffer → un GIF → **un** checkpoint (no N documentos). |
| **Telemetría** | Métricas de runtime (FPS, latencia); no es evidencia legal. |
| **Tope por sesión** | Máximo 30–50 `checkpoint_frame` por `session_id`. |

### 1.2 Uso

Worker escribe checkpoints fuera del hot path cuando la política lo pide. Backend/portal listan por `session_id`. Fan-out: detección → WSS UI + `count_event` (DS04) + checkpoint Mongo **si** aplica.

---

### 1.3 Convenciones

Enlaces a PG son **lógicos** (UUID string), sin FK Mongo.

Relación lógica: `work_session` 1 → 0..N `checkpoint_frame`; `media_asset` 1 → 0..N checkpoints.

---

#### 1.3.1 Vocabulario `cat_checkpoint_reason` (referencia PG)

Tabla PG de códigos (sin FK a Mongo). Campos estándar de catálogo:

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(32) | NO | — | Código usado en Mongo `reason`. |
| `name` | VARCHAR(80) | NO | — | Etiqueta. |

**Ejemplo de fila completa**

```json
{
  "id": "d1000001-0000-4000-8000-000000000001",
  "code": "discrepancy",
  "name": "Discrepancia registrada"
}
```

Códigos esperados: `start`, `close`, `hourly`, `state_change`, `discrepancy`, `manual_pin`.

---

#### 1.3.2 Colección `checkpoint_frame`

**Para qué:** evidencia semiestructurada por sesión.

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `_id` | ObjectId | NO | auto | PK Mongo. |
| `session_id` | string (UUID) | NO | — | = `work_session.id` (DS04). |
| `operation_id` | string (UUID) | SÍ | — | Denormalizado para paneles. |
| `media_asset_id` | string (UUID) | SÍ | — | = `media_asset.id` (DS05). |
| `gcs_uri` | string | SÍ | — | URI redundante para lectura rápida. |
| `model_version` | string | SÍ | — | Tag `yolo_model.version_tag`. |
| `reason` | string | NO | — | Código de motivo (ver catálogo). |
| `phase_id` | string (UUID) | SÍ | — | Copia de fase al capturar. |
| `phase_code` | string | SÍ | — | Código de fase denormalizado. |
| `burst_frame_count` | int | SÍ | — | Frames usados en ráfaga GIF. |
| `burst_interval_ms` | int | SÍ | — | Espaciado aproximado entre frames. |
| `captured_at` | ISODate | NO | — | Momento UTC. |
| `detections` | array | NO | `[]` | Detecciones del frame clave. |
| `detections[].family_code` | string | NO* | — | Código de familia. |
| `detections[].confidence` | number | SÍ | — | Score del modelo. |
| `detections[].bbox` | array/object | SÍ | — | Caja `[x,y,w,h]` o equivalente. |
| `retention_until` | ISODate | NO | — | TTL Mongo + alineación con media. |
| `session_checkpoint_seq` | int | NO | — | Secuencia ascendente por sesión. |

\*En cada elemento del array, `family_code` es obligatorio si hay detección.

**Hacia dónde apunta (lógico)**

| Campo | Apunta a | Relación | Por qué | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `session_id` | `work_session.id` (DS04) | N:1 | Dueño temporal del checkpoint. | N/A (lógico); purga por job/TTL |
| `operation_id` | `operation.id` (DS02) | N:0..1 | Filtro de panel. | N/A |
| `media_asset_id` | `media_asset.id` (DS05) | N:0..1 | Bytes JPEG/GIF. | N/A; job borra doc + marca purga media |

**Índices:** `(session_id, captured_at)`; TTL en `retention_until`; sparse `operation_id`, `phase_code`.

**Reglas**

1. Máximo 30–50 docs por sesión; al exceder, borrar primero `interval`/`hourly` viejos; conservar `start`, `close`, `state_change`, `discrepancy`, `manual_pin`.
2. GIF = un documento.
3. Debounce ráfagas ricas: 10–30 s por sesión.
4. Fallback: si falla GIF → JPEG único.
5. Retención: +90 días; +180 si `work_session.extended_retention`.

**Ejemplo de documento completo**

```json
{
  "_id": "66fa12ab34cd56ef78901234",
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "operation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "media_asset_id": "m1000003-0000-4000-8000-000000000003",
  "gcs_uri": "s3://pef-minio-evidence/11111111-1111-1111-1111-111111111111/cccccccc-cccc-cccc-cccc-cccccccccccc/2026/09/burst-disc.gif",
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

#### 1.3.3 Colección `session_telemetry`

**Para qué:** métricas de runtime; no evidencia legal.

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `_id` | ObjectId | NO | auto | PK. |
| `session_id` | string (UUID) | NO | — | Sesión. |
| `ts` | ISODate | NO | — | Marca de tiempo de la muestra. |
| `fps_avg` | number | SÍ | — | FPS promedio de la ventana. |
| `latency_ms` | number | SÍ | — | Latencia de inferencia/pipeline. |
| `drops` | int | SÍ | — | Frames descartados. |
| `roi_ok` | boolean | SÍ | — | ROI válida/estable. |

**Hacia dónde apunta (lógico)**

| Campo | Apunta a | Relación | Por qué | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `session_id` | `work_session.id` | N:1 | Agrupa telemetría. | N/A; TTL 7 días o capped |

No cuenta dentro del tope de checkpoints.

**Ejemplo de documento completo**

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

| Aspecto | Responsable |
| :--- | :--- |
| Escritura checkpoint/telemetría | Worker (+ adaptador) |
| Política de tope/TTL | PEF Core / Ops |
| Lectura panel | Backend Web |

---

### 1.5 Justificación del estándar

**Por qué Mongo aquí**

Los checkpoints son documentos variables (detections array, burst metadata) de alta frecuencia relativa. Meter boxes en cada `count_event` inflaría PostgreSQL.

**Decisiones tomadas**

1. **PG = hechos de negocio; Mongo = detalle visual/telemetría.**
2. **Tope 30–50** y prioridad de borrado por `reason`.
3. **GIF off hot path**; ring buffer en RAM.
4. **`reason` string** alineado a catálogo PG sin FK cruzada de motores.
5. **URI redundante** en el doc para no forzar join PG en cada thumbnail.

**Alternativas no elegidas**

| Alternativa | Por qué no |
| :--- | :--- |
| Todo en PostgreSQL JSONB | Posible, pero índices TTL y volumen de telemetría encajan mejor en Mongo. |
| Un documento por frame de ráfaga | Rompe el tope y multiplica I/O. |
| Worker escribe `count_event` directo | Bifurca fuente de verdad; el diseño manda Backend/adaptador para hechos SQL (DS04). |
| Sin `media_asset_id`, solo URI | Pierde ciclo de purga unificado en PG. |

---

## 2. Anexo A – Información general

| Campo | Valor |
| :--- | :--- |
| Código | PEF-DS06 |
| Título | Mongo Checkpoints and Telemetry |
| Versión | 0.1.0 |
| Estado | draft |
| Almacén | MongoDB (`checkpoint_frame`, `session_telemetry`); vocab `cat_checkpoint_reason` en PG |
| Fuera de alcance | `count_event`; bytes storage; mensajes WSS frame-a-frame (DS10) |
| Referencia | `notas_mongo_checkpoints_gcs.txt`, `diagrama_er_completo.md` §Mongo |
