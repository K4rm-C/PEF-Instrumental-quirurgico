---
id: PEF-DS10
title: Vision Realtime Contracts
version: "0.1.0"
status: draft
owner: PEF Core / Visión / Estación
applies_to: [CS, Worker]
stores: []
related: [PEF-DS03, PEF-DS04, PEF-DS06]
last_updated: 2026-11-19
---

# PEF-DS10 · Vision Realtime Contracts

## INTRODUCCION

Este estándar define los **mensajes WebSocket seguro (WSS)** entre la **Estación de Conteo** y el **VisionWorker** (YOLO).

Es el canal de percepción en tiempo real: la estación envía cuadros (o metadatos de frame) y recibe detecciones sugeridas. **No** es la fuente de verdad del negocio: abrir/cerrar sesión, discrepancias y `count_event` pasan por Backend Web (DS04/DS09). Los checkpoints viven en Mongo (DS06) vía EvidenceWorker.

Runtime lab: pipeline `matrix_v1` (assign), holds sticky, soft-name dwell, live WS. Proceso host suele usarse por GPU; Docker Vision es CPU-only. Path típico `/ws`; en LAN el browser usa `wss://<LAN>:5443/ws` (portal-lan) hacia el worker en host. Auth de portal en ese mismo origen puede ser `/auth` relativo (ver DS01/DS09); no exige URL absoluta de auth.

---

## 1. Explicación del estándar

### 1.1 Definiciones

| Término | Significado |
| :--- | :--- |
| **WSS** | WebSocket sobre TLS. |
| **Hot path** | Inferencia + push UI; sin armar MP4 ni escribir PG. |
| **Detección sugerida** | Propuesta del modelo; el humano/reglas validan. |
| **client_event_id** | UUID de idempotencia cuando el backend materializa un hecho. |

### 1.2 Uso

1. Estación autentica el bind WSS (lab: ticket HMAC `VISION_WS_SECRET`; el campo `auth_token` del mensaje sigue siendo el vehículo del secreto de bind, no el JWT de login de portal) y abre WSS con `session_id`.
2. Envía frames muestreados (o chunks según implementación).
3. Worker responde `detections.result` (hot path vivo; el payload público de sesión puede incluir holds / soft_named). `count.suggestion` no está implementado en el runtime actual.
4. La estación puede enviar `session.cancel` para detener captura live.
5. Si hay que persistir hechos: Backend crea `count_event`. Checkpoints/clips: EvidenceWorker (DS05/DS06).

**Qué no hace el worker:** crear/cerrar sesión, aceptar discrepancias, escribir PostgreSQL como dueño, ni ser dueño de Garage/Mongo evidencia.

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
| `model_version_pin` | string | SÍ | Tag del modelo **pineado** en `work_session.yolo_model_id` (`yolo_model.version_tag`). El worker no sustituye por el `active` global en sesiones ya abiertas. Mongo `checkpoint_frame.model_version` debe coincidir con ese tag. |
| `auth_token` | string | NO | Secreto de bind (lab: ticket HMAC; no confundir con JWT de AuthService). Puede venir ya validado en handshake. |
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

#### 1.3.4 Mensaje `count.suggestion` (no implementado)

Tipo histórico del contrato. No emitido en el runtime actual (`matrix_v1`): el hot path vive en `detections.result` (+ holds / soft_named en el estado público de sesión). No usar como camino de persistencia; los hechos los escribe Backend/SessionService (DS04).

---

#### 1.3.5 Mensaje `session.cancel` (estación → worker)

Detiene la captura live de la sesión ya bindeada. Presente en `VisionWorker` (`live_ws`).

| Campo | Tipo | Nulo | Significado |
| :--- | :--- | :--- | :--- |
| `type` | string | NO | `session.cancel` |
| `session_id` | uuid | SÍ | Debe coincidir con la sesión bindeada si se envía. |

```json
{
  "type": "session.cancel",
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc"
}
```

Tras cancel, el worker responde `worker.status` con `state: stopping` y cierra el flujo live.

---

#### 1.3.6 Mensaje `worker.status` / `error`

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

#### 1.3.7 Reglas de frontera

1. Si el modelo cae, la estación sigue en modo manual vía Backend.
2. Clip/checkpoint: off hot path (EvidenceWorker → DS05/DS06); el WSS no espera el MP4.
3. No enviar PII de paciente en el bus de frames.
4. Rate: muestreo de frames; no flood sin límite.
5. Demo `matrix_v1`: assign + hold sticky + soft_named dwell viven en el estado de sesión del worker; no redefinir tablas aquí.

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
3. **`client_event_id`** para idempotencia al materializar conteo (cuando aplique).
4. **Sugerencia ≠ hecho** hasta que Backend escribe `count_event`. Al materializar `auto_count`, copia `detections[].confidence` (o el score asignado de esa caja) a `payload.confidences` / `board.families[].confidences`. No avg/best/worst.
5. **Bind WSS con ticket HMAC** en lab (no JWT de usuario); GPU preferida en proceso host.

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
| Referencias | `apps/VisionWorker/`; DS05/DS06 (EvidenceWorker); runbook raíz `README.md` |
