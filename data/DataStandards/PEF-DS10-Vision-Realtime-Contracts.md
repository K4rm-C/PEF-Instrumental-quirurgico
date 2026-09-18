---
id: PEF-DS10
title: Vision Realtime Contracts
version: "0.1.0"
status: draft
owner: PEF Core / Visión / Estación
applies_to: [CS, Worker]
stores: []
related: [PEF-DS03, PEF-DS04, PEF-DS06]
last_updated: 2026-09-06
---

# PEF-DS10 · Vision Realtime Contracts

## INTRODUCCION

Este estándar define los **mensajes WebSocket seguro (WSS)** entre la **Estación de Conteo** y el **Worker YOLO**.

Es el canal de percepción en tiempo real: la estación envía cuadros (o metadatos de frame) y recibe detecciones sugeridas. **No** es la fuente de verdad del negocio: abrir/cerrar sesión, discrepancias y `count_event` pasan por Backend Web (DS04/DS09). Los checkpoints viven en Mongo (DS06).

---

## 1. Explicación del estándar

### 1.1 Definiciones

| Término | Significado |
| :--- | :--- |
| **WSS** | WebSocket sobre TLS. |
| **Hot path** | Inferencia + push UI; sin armar GIF ni escribir PG. |
| **Detección sugerida** | Propuesta del modelo; el humano/reglas validan. |
| **client_event_id** | UUID de idempotencia cuando el backend materializa un hecho. |

### 1.2 Uso

1. Estación autentica (JWT de usuario) y abre WSS con `session_id`.
2. Envía frames muestreados (o chunks según implementación).
3. Worker responde `detections` / `count_suggestion`.
4. Si hay que persistir: Backend crea `count_event` (+ checkpoint si política).

**Qué no hace el worker:** crear/cerrar sesión, aceptar discrepancias, escribir PostgreSQL como dueño.

---

### 1.3 Convenciones

Transporte: WSS. Payload: JSON (UTF-8). Cada mensaje lleva `type` discriminador.

Relación lógica: 1 estación ↔ 1 conexión worker por sesión activa típica (N conexiones técnicas posibles; una sesión de negocio).

---

#### 1.3.1 Mensaje `session.bind` (estación → worker)

| Campo | Tipo | Nulo | Significado |
| :--- | :--- | :--- | :--- |
| `type` | string | NO | `session.bind` |
| `session_id` | uuid | NO | Sesión de conteo (DS04). |
| `station_id` | uuid | SÍ | Estación (DS02). |
| `model_version_pin` | string | SÍ | Si null, worker usa modelo `active`. |
| `auth_token` | string | NO | JWT (o ya validado en handshake). |
| `correlation_id` | string | SÍ | Trazas. |

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

#### 1.3.2 Mensaje `frame.submit` (estación → worker)

| Campo | Tipo | Nulo | Significado |
| :--- | :--- | :--- | :--- |
| `type` | string | NO | `frame.submit` |
| `session_id` | uuid | NO | |
| `frame_id` | uuid | NO | Id del frame en el cliente. |
| `captured_at` | string ISO | NO | Momento de captura. |
| `image_base64` | string | SÍ | JPEG en base64 (si no hay canal binario). |
| `image_content_type` | string | SÍ | `image/jpeg` |
| `roi` | object | SÍ | Override puntual de ROI. |
| `roi.x` | number | SÍ | |
| `roi.y` | number | SÍ | |
| `roi.w` | number | SÍ | |
| `roi.h` | number | SÍ | |
| `correlation_id` | string | SÍ | |

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

#### 1.3.3 Mensaje `detections.result` (worker → estación)

| Campo | Tipo | Nulo | Significado |
| :--- | :--- | :--- | :--- |
| `type` | string | NO | `detections.result` |
| `session_id` | uuid | NO | |
| `frame_id` | uuid | NO | Frame respondido. |
| `model_version` | string | NO | Tag usado. |
| `inferred_at` | string ISO | NO | |
| `latency_ms` | number | SÍ | |
| `detections` | array | NO | Lista (puede vacía). |
| `detections[].family_code` | string | NO | Clase mapeada (DS03). |
| `detections[].yolo_class_id` | int | SÍ | Entero crudo. |
| `detections[].confidence` | number | NO | 0..1 |
| `detections[].bbox` | array | NO | `[x,y,w,h]` normalizado o px (declarar en `bbox_space`). |
| `bbox_space` | string | NO | `normalized` \| `pixels` |
| `counts_by_family` | array | SÍ | Agregado sugerido. |
| `counts_by_family[].family_code` | string | NO | |
| `counts_by_family[].detected_quantity` | int | NO | |
| `correlation_id` | string | SÍ | |

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

#### 1.3.4 Mensaje `count.suggestion` (worker → estación / backend hook)

Sugerencia de hecho de conteo (la persistencia la hace el Backend).

| Campo | Tipo | Nulo | Significado |
| :--- | :--- | :--- | :--- |
| `type` | string | NO | `count.suggestion` |
| `session_id` | uuid | NO | |
| `client_event_id` | uuid | NO | Idempotencia hacia `count_event`. |
| `family_code` | string | SÍ | |
| `expected_quantity` | int | SÍ | Si el worker la conoce. |
| `detected_quantity` | int | NO | |
| `occurred_at` | string ISO | NO | |
| `trigger` | string | NO | `auto` \| `stable_window` \| … |
| `correlation_id` | string | SÍ | |

```json
{
  "type": "count.suggestion",
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "client_event_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
  "family_code": "mayo_hegar_needle_holder",
  "expected_quantity": 2,
  "detected_quantity": 1,
  "occurred_at": "2026-09-05T18:20:00Z",
  "trigger": "stable_window",
  "correlation_id": "corr-ws-001"
}
```

---

#### 1.3.5 Mensaje `worker.status` / `error`

**`worker.status`**

| Campo | Tipo | Significado |
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

| Campo | Tipo | Significado |
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
  "message": "JPEG vacío o corrupto",
  "frame_id": "ff000001-0000-4000-8000-000000000001",
  "correlation_id": "corr-ws-001"
}
```

---

#### 1.3.6 Reglas de frontera

1. Si el modelo cae, la estación sigue en modo manual vía Backend.
2. GIF/checkpoint: off hot path (DS06); el WSS no espera el GIF.
3. No enviar PII de paciente en el bus de frames.
4. Rate: muestreo de frames; no flood sin límite.

---

### 1.4 Governance and Ownership

| Aspecto | Responsable |
| :--- | :--- |
| Contrato WSS | Visión + estación |
| Persistencia de hechos | Backend (no worker dueño de PG) |
| Versión de modelo | DS03 (`yolo_model`) |

---

### 1.5 Justificación del estándar

**Decisiones tomadas**

1. **WSS solo percepción**; negocio por HTTPS/Backend.
2. **Mensajes tipados por `type`** con campos explícitos.
3. **`client_event_id`** para idempotencia al materializar conteo.
4. **Sugerencia ≠ hecho** hasta que Backend escribe `count_event`.

**Alternativas no elegidas**

| Alternativa | Por qué no |
| :--- | :--- |
| Worker escribe PG directo | Bifurca verdad y complica RBAC/auditoría. |
| REST polling de frames | Latencia inútil para UI de quirófano. |
| Un solo blob binario sin JSON de control | Dificulta versionar y depurar tipos de mensaje. |
| Meter discrepancia aceptada en WSS | Es decisión clínica; pertenece a Backend/estación HTTP. |

---

## 2. Anexo A – Información general

| Campo | Valor |
| :--- | :--- |
| Código | PEF-DS10 |
| Título | Vision Realtime Contracts |
| Versión | 0.1.0 |
| Estado | draft |
| Almacén | Ninguno propio (transporte WSS); efectos en DS04/DS06 |
| Fuera de alcance | REST (DS09); bytes en bucket (DS05) |
| Referencias | `arquitectura_servicios_y_cobertura.md`, notas worker |
