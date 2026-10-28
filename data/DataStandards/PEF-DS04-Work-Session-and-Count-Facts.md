---

## id: PEF-DS04
title: Work Session and Count Facts
version: "0.4.0"
status: draft
owner: PEF Core / Estación / Worker
applies_to: [CS, BE, Worker, WP]
stores: [PostgreSQL]
related: [PEF-DS01, PEF-DS02, PEF-DS03, PEF-DS06, PEF-DS07]
last_updated: 2026-10-22

# PEF-DS04 · Work Session and Count Facts

## INTRODUCCION

Este estándar define **el acto de contar en charola** y lo que queda como hecho auditable en PostgreSQL.

Tablas dueñas: `work_session`, `expected_inventory`, `count_event`, `discrepancy`, `human_correction`.

Catálogos dueños: `cat_session_status`, `cat_event_type`, `cat_discrepancy_reason`.

`cat_operation_phase` se define en PEF-DS03; aquí la sesión solo guarda `current_phase_id`.

No define checkpoints Mongo (PEF-DS06), ni bytes JPEG/GIF (PEF-DS05), ni el acuerdo de privacidad de la sesión (PEF-DS07) — aunque la sesión puede existir antes de ese acuerdo.

Abajo: **todos** los campos, FKs con motivo, y **fila completa** por tabla.

---



## 1. Explicación del estándar



### 1.1 Definiciones


| Término                 | Significado                                                                                                |
| ----------------------- | ---------------------------------------------------------------------------------------------------------- |
| **Sesión de trabajo**   | Periodo en que un usuario cuenta o prepara conteo, ligado opcionalmente a operación, estación, kit y fase. |
| **Inventario esperado** | Cantidades por familia para esa sesión. Snapshot independiente del kit; editable solo mientras la sesión está `scheduled` o `in_progress` (ver §1.3.5). |
| **Evento de conteo**    | Hecho auditable (auto/manual/cambio de fase, etc.).                                                        |
| **Cantidad esperada**   | `expected_inventory.expected_quantity` de la familia.                                                      |
| **Sugerencia de IA**    | Cantidad por familia del último resultado de visión `final` persistido (`auto_count`). Nula en sesiones `manual_no_privacy`. |
| **Cantidad reportada**  | Cantidad final que declara el humano en el reporte de conteo (`manual_count`).                             |
| **Discrepancia**        | Registro de descuadre entre **cantidad reportada** y **cantidad esperada**; puede resolverse después.       |
| **Corrección humana**   | Justificación obligatoria (1:1 con un `count_event`) cuando la **cantidad reportada** difiere de la **sugerencia de IA**. |




### 1.2 Uso

Estación abre/cierra sesión y muestra esperado vs detectado. Worker/backend **persisten** `count_event` además del push WSS a UI. Portal consulta historial y discrepancias.


| Pregunta                                                      | Dónde                   |
| ------------------------------------------------------------- | ----------------------- |
| ¿Cuántos se dieron por detectados a las 10:32 en la sesión X? | `count_event` (este DS) |
| ¿Boxes, score, traza fina?                                    | Mongo (DS06)            |
| ¿URI del JPEG?                                                | `media_asset` (DS05)    |


Fan-out del proyecto: detección → UI (WSS) **y** hecho en PG (+ checkpoint si la política lo pide).

---



### 1.3 Convenciones

En “Hacia dónde apunta”, columna **Relación**: `N:1` = muchas filas de esta tabla a una del destino; `N:0..1` = destino opcional; `1:1` = a lo sumo un par (UNIQUE); `N:N` vía tabla puente.

---



#### 1.3.1 `cat_session_status`


| Campo  | Tipo        | Nulo | Default             | Significado                      |
| ------ | ----------- | ---- | ------------------- | -------------------------------- |
| `id`   | UUID        | NO   | `gen_random_uuid()` | PK.                              |
| `code` | VARCHAR(32) | NO   | —                   | Ver códigos reales abajo.        |
| `name` | VARCHAR(80) | NO   | —                   | Etiqueta.                        |


FKs: ninguna. UNIQUE `code`.

```json
{
  "id": "c1000001-0000-4000-8000-000000000001",
  "code": "in_progress",
  "name": "In progress"
}
```

**Códigos reales (seed `002_seed_catalogs.sql`) y transiciones (v0.2.1)**

| Código | Significado | Entra desde | Acción |
| :--- | :--- | :--- | :--- |
| `scheduled` | Programada; aún no inicia. `started_at` y `capture_mode` nulos. | — (alta) | SPD programa |
| `in_progress` | Conteo en curso. | `scheduled` | Operador asignado inicia (start) |
| `awaiting_spd_review` | Conteo enviado sin discrepancias abiertas. | `in_progress`, `correction_required` | Reporte sin diferencias vs esperado; o SPD resuelve la última discrepancia |
| `correction_required` | Conteo enviado con discrepancias abiertas. | `in_progress` | Reporte con alguna diferencia vs esperado |
| `closed` | Cierre administrativo. | `awaiting_spd_review` | SPD cierra (material recuperado, 0 discrepancias abiertas) |
| `aborted` | Definido en catálogo. **Sin transición funcional** en el sistema actual ni en Session API v1. | — | — |

No existe un estado `open`; los ejemplos anteriores que lo usaban eran inválidos.

---



#### 1.3.2 `cat_event_type`


| Campo  | Tipo         | Nulo | Default             | Significado                                       |
| ------ | ------------ | ---- | ------------------- | ------------------------------------------------- |
| `id`   | UUID         | NO   | `gen_random_uuid()` | PK.                                               |
| `code` | VARCHAR(64)  | NO   | —                   | Ej. `auto_count`, `manual_count`, `phase_change`. |
| `name` | VARCHAR(160) | NO   | —                   | Etiqueta.                                         |


FKs: ninguna. UNIQUE `code`.

```json
{
  "id": "c1000002-0000-4000-8000-000000000002",
  "code": "auto_count",
  "name": "Model-suggested count"
}
```

**Códigos reales y uso**

| Código | Quién lo genera | Cuándo | Uso |
| :--- | :--- | :--- | :--- |
| `session_open` | Operador asignado | `scheduled` → `in_progress` | Implementado |
| `auto_count` | Pipeline de visión (resultado normalizado) | Durante `in_progress` (sesión `vision`); `trigger` ∈ (`initial`, `stable_window`, `final`) | Implementado (ver payload §1.3.6 y §1.3.6.1) |
| `close_blocked` | Operador asignado | Conteo final de visión rechazado: total ≠ referencia (§1.3.6.1) | Implementado (v0.3.0, portal RF) |
| `instruments_added` | SPD | Incorporación física confirmada durante `in_progress` (§1.3.6.1) | Implementado (v0.3.0; migración `005_count_reference_events.sql`) |
| `manual_count` | Operador asignado | Reporte de conteo, una fila por familia | Implementado |
| `manual_close` | Operador asignado | Fin de la participación del Operador (→ `awaiting_spd_review` / `correction_required`); **no** es el cierre seguro | Implementado |
| `phase_change` | Operador asignado | Durante `in_progress` | **Incompleto/stub** (fuera de Session API v1) |
| `session_close` | SPD | `awaiting_spd_review` → `closed`: el **cierre seguro** | Implementado |
| `discrepancy_raised`, `correction_applied`, `validation_passed` | — | — | En catálogo; **no escritos** por el sistema actual |

---



#### 1.3.3 `cat_discrepancy_reason`


| Campo  | Tipo         | Nulo | Default             | Significado                                               |
| ------ | ------------ | ---- | ------------------- | --------------------------------------------------------- |
| `id`   | UUID         | NO   | `gen_random_uuid()` | PK.                                                       |
| `code` | VARCHAR(64)  | NO   | —                   | Ver códigos reales abajo.                                 |
| `name` | VARCHAR(160) | NO   | —                   | Etiqueta.                                                 |


FKs: ninguna. UNIQUE `code`.

```json
{
  "id": "c1000003-0000-4000-8000-000000000003",
  "code": "shortage",
  "name": "Shortage versus expected inventory"
}
```

**Códigos reales:** `shortage`, `surplus`, `unidentified`, `occluded`, `misclassified`, `operator_error`. Son los únicos `reason_code` válidos en Session API v1.

> **KNOWN UI/CATALOG GAP:** el portal web ofrece además `other` en el reporte manual (se guarda solo en `count_event.payload`), pero `other` **no existe** en `cat_discrepancy_reason`. No es un código válido de Session API v1. Pendiente de una tarea posterior (sin cambio de seed en v0.2.1).

---



#### 1.3.4 Tabla `work_session`

**Para qué:** cabecera del periodo de conteo.


| Campo                | Tipo        | Nulo | Default             | Significado                                         |
| -------------------- | ----------- | ---- | ------------------- | --------------------------------------------------- |
| `id`                 | UUID        | NO   | `gen_random_uuid()` | PK de la sesión.                                    |
| `started_at`         | TIMESTAMPTZ | SÍ   | NULL                | Inicio real del conteo. NULL mientras `scheduled`; se fija en start. |
| `ended_at`           | TIMESTAMPTZ | SÍ   | NULL                | Cierre.                                             |
| `status_id`          | UUID        | NO   | —                   | Estado de sesión.                                   |
| `user_id`            | UUID        | NO   | —                   | **Operador asignado** (`station_operator`): el único que puede iniciar, reportar y enviar conteos. No es quien programa. |
| `closed_by_user_id`  | UUID        | SÍ   | NULL                | Quien cerró.                                        |
| `operation_id`       | UUID        | SÍ   | NULL                | Caso clínico (política de producto puede exigirlo). |
| `station_id`         | UUID        | SÍ   | NULL                | Estación de captura.                                |
| `kit_id`             | UUID        | SÍ   | NULL                | Kit de referencia al abrir.                         |
| `current_phase_id`   | UUID        | SÍ   | NULL                | Fase clínica actual.                                |
| `phase_changed_at`   | TIMESTAMPTZ | SÍ   | NULL                | Cuándo cambió la fase.                              |
| `capture_mode`       | VARCHAR(32) | SÍ   | NULL                | `vision` \| `manual_no_privacy`. NULL mientras `scheduled`; se decide **una sola vez** en start e inmutable después. |
| `atypical_session`   | BOOLEAN     | NO   | `FALSE`             | Sesión fuera de proceso estándar.                   |
| `extended_retention` | BOOLEAN     | NO   | `FALSE`             | Señal de retención extendida de evidencia.          |
| `retention_until`    | TIMESTAMPTZ | SÍ   | NULL                | Hasta cuándo retener (si aplica).                   |
| `updated_at`         | TIMESTAMPTZ | NO   | `now()`             | Última actualización de la cabecera.                |


**Hacia dónde apunta**


| Campo               | Apunta a                        | Relación | Por qué                                                                 | ON DELETE  |
| ------------------- | ------------------------------- | -------- | ----------------------------------------------------------------------- | ---------- |
| `status_id`         | `cat_session_status.id`         | N:1      | Estado controlado.                                                      | `RESTRICT` |
| `user_id`           | `user.id` (DS01)                | N:1      | Responsable de la sesión.                                               | `RESTRICT` |
| `closed_by_user_id` | `user.id` (DS01)                | N:0..1   | Quién cerró; puede diferir del abridor.                                 | `SET NULL` |
| `operation_id`      | `operation.id` (DS02)           | N:0..1   | Contexto clínico.                                                       | `RESTRICT` |
| `station_id`        | `capture_station.id` (DS02)     | N:0..1   | Dónde se capturó.                                                       | `SET NULL` |
| `kit_id`            | `kit.id` (DS03)                 | N:0..1   | Receta de referencia; el detalle esperado está en `expected_inventory`. | `SET NULL` |
| `current_phase_id`  | `cat_operation_phase.id` (DS03) | N:0..1   | Fase actual.                                                            | `SET NULL` |


CHECK: `ended_at` ≥ `started_at` si ambas existen (`ended_at` exige `started_at`). CHECK `capture_mode` ∈ (`vision`, `manual_no_privacy`) o NULL.

**Nota:** `session_processing_agreement` (0..1) es PEF-DS07. Una sesión puede existir sin acuerdo (p. ej. reserva previa).

**Semántica de start y `capture_mode`:** al iniciar, Session **lee** (no escribe) el acuerdo DS07 de la sesión: con acuerdo → `vision`; sin acuerdo → `manual_no_privacy`. Después del start el acuerdo ya no puede cambiar el modo. Session no es dueño de `session_processing_agreement`.

**Tenant:** `work_session` no tiene `institution_id`; la institución de la sesión es `operation.institution_id` (DS02). Todas las referencias (operador, estación/sala, kit, paciente, médicos, piezas de stock) deben pertenecer a esa misma institución.

**`ended_at`:** se fija al enviar el reporte de conteo (fin del conteo); el cierre administrativo solo lo fija si seguía nulo.

```json
{
  "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "started_at": "2026-09-05T16:15:00Z",
  "ended_at": null,
  "status_id": "c1000001-0000-4000-8000-000000000001",
  "capture_mode": "vision",
  "user_id": "22222222-2222-2222-2222-222222222222",
  "closed_by_user_id": null,
  "operation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "station_id": "f0f0f0f0-f0f0-f0f0-f0f0-f0f0f0f0f0f0",
  "kit_id": "k1000001-0000-4000-8000-000000000001",
  "current_phase_id": "b1000004-0000-4000-8000-000000000004",
  "phase_changed_at": "2026-09-05T16:15:30Z",
  "atypical_session": false,
  "extended_retention": false,
  "retention_until": null,
  "updated_at": "2026-09-05T18:20:00Z"
}
```

---



#### 1.3.5 Tabla `expected_inventory`

**Para qué:** snapshot de “cuántos deberían verse” por familia en esta sesión (independiente del kit).


| Campo               | Tipo        | Nulo | Default             | Significado                |
| ------------------- | ----------- | ---- | ------------------- | -------------------------- |
| `id`                | UUID        | NO   | `gen_random_uuid()` | PK.                        |
| `family_id`         | UUID        | NO   | —                   | Familia esperada.          |
| `expected_quantity` | SMALLINT    | NO   | —                   | Cantidad esperada (≥ 0).   |
| `session_id`        | UUID        | NO   | —                   | Sesión dueña.              |
| `source`            | VARCHAR(32) | NO   | `'kit_snapshot'`    | Origen de la línea (ver abajo). |


**Hacia dónde apunta**


| Campo        | Apunta a                      | Relación | Por qué                      | ON DELETE  |
| ------------ | ----------------------------- | -------- | ---------------------------- | ---------- |
| `family_id`  | `instrument_family.id` (DS03) | N:1      | Qué tipo se espera.          | `RESTRICT` |
| `session_id` | `work_session.id`             | N:1      | Congela esperado por sesión. | `CASCADE`  |


UNIQUE (`session_id`, `family_id`). CHECK cantidad ≥ 0; CHECK `source` ∈ (`kit_snapshot`, `schedule_additional`, `live_add`, `manual`).

| `source` | Significado |
| :--- | :--- |
| `kit_snapshot` | Línea tomada del kit (al programar con la cantidad del kit, o copiada del kit en start si la sesión no tenía líneas). |
| `schedule_additional` | Línea agregada o con cantidad distinta a la del kit durante la programación (`scheduled`). |
| `live_add` | Línea agregada o modificada por SPD con la sesión `in_progress`. |
| `manual` | Origen manual permitido por el esquema (no lo escribe el sistema actual). |

Editar el kit **después** no reescribe solos estos snapshots.

**Editabilidad (v0.2.1):** el inventario esperado **no** queda inmutable en start.

- `scheduled`: SPD puede reemplazarlo.
- `in_progress`: SPD puede reemplazarlo (`live_add`), en una transacción que bloquea la sesión (incompatible con un reporte de conteo concurrente) y queda auditada.
- Después del reporte de conteo final (`awaiting_spd_review`, `correction_required`, `closed`): **no editable**.

```json
{
  "id": "e2000001-0000-4000-8000-000000000001",
  "family_id": "f1000001-0000-4000-8000-000000000001",
  "expected_quantity": 2,
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "source": "kit_snapshot"
}
```

---



#### 1.3.6 Tabla `count_event`

**Para qué:** hecho auditable de conteo u evento relacionado.


| Campo               | Tipo        | Nulo | Default             | Significado                                             |
| ------------------- | ----------- | ---- | ------------------- | ------------------------------------------------------- |
| `id`                | UUID        | NO   | `gen_random_uuid()` | PK del hecho.                                           |
| `event_type_id`     | UUID        | NO   | —                   | Tipo de evento.                                         |
| `client_event_id`   | UUID        | SÍ   | NULL                | Id del cliente/pipeline para idempotencia (único si viene). Obligatorio en `auto_count` recibidos por API. |
| `family_id`         | UUID        | SÍ   | NULL                | Familia afectada (nulo si el evento no es por familia). |
| `expected_quantity` | SMALLINT    | SÍ   | NULL                | Esperado en ese momento (≥ 0 si informado).             |
| `detected_quantity` | SMALLINT    | SÍ   | NULL                | Detectado/contabilizado (≥ 0 si informado). En `manual_count` = **cantidad reportada** por el humano. |
| `payload`           | JSONB       | NO   | `'{}'`              | Datos extra controlados (sin PII innecesaria).          |
| `occurred_at`       | TIMESTAMPTZ | NO   | `now()`             | Momento del hecho.                                      |
| `session_id`        | UUID        | NO   | —                   | Sesión.                                                 |
| `user_id`           | UUID        | SÍ   | NULL                | Usuario si el evento lo inició una persona; nulo si lo produjo el sistema (p. ej. `auto_count`). |


**Hacia dónde apunta**


| Campo           | Apunta a                      | Relación | Por qué                | ON DELETE  |
| --------------- | ----------------------------- | -------- | ---------------------- | ---------- |
| `event_type_id` | `cat_event_type.id`           | N:1      | Clasifica el hecho.    | `RESTRICT` |
| `family_id`     | `instrument_family.id` (DS03) | N:0..1   | Tipo contado.          | `SET NULL` |
| `session_id`    | `work_session.id`             | N:1      | Dueño temporal.        | `CASCADE`  |
| `user_id`       | `user.id` (DS01)              | N:0..1   | Actor humano opcional. | `SET NULL` |


UNIQUE `client_event_id` (cuando no es nulo, vía UNIQUE en columna).

**Idempotencia (`client_event_id`):** es el mecanismo de idempotencia para hechos que llegan de clientes o del pipeline de visión. La restricción UNIQUE existente es autoritativa.

- Replay con el mismo `client_event_id`, la misma sesión y el mismo contenido lógico → operación idempotente (se devuelve el evento existente, no se crea otro).
- Mismo `client_event_id` con otra sesión o contenido incompatible → conflicto.

**`user_id`:** eventos iniciados por una persona (`session_open`, `manual_count`, `manual_close`, `phase_change`, `session_close`) llevan el usuario. `auto_count` es un hecho del modelo: `user_id` es NULL y el hecho no se atribuye al operador que retransmitió el resultado. **No** se escribe una fila `access_audit` por lote de visión (v0.2.2): el `auto_count` es el hecho autoritativo (los `stable_window` pueden ser frecuentes y duplicarlos en auditoría solo añadiría ruido) y los logs técnicos conservan el `X-Correlation-ID` de la petición.

**`manual_count`:** una fila por familia; `detected_quantity` = cantidad reportada por el humano, `expected_quantity` = esperado en ese momento. La cantidad reportada **no** debe quedar únicamente en `payload`.

**Frontera de `payload`:** solo metadata corta y estructurada propia del hecho de negocio. **No** almacena: JPEG ni imágenes base64, bounding boxes, top-k completos, secuencias de frames, `video_t`, `frames_processed`, detalles de transporte (upload, cámara, WebSocket) ni telemetría de inferencia de alta granularidad. Eso pertenece a PEF-DS06 (Mongo) y PEF-DS05 (Evidence).

**Excepción documentada — `auto_count` por lote:** un resultado de visión es **un** `count_event` por snapshot (no uno por familia): `family_id` NULL, `detected_quantity` = total, y `payload` con la representación normalizada y limitada del lote:

```json
{
  "id": "ce000001-0000-4000-8000-000000000001",
  "event_type_id": "c1000002-0000-4000-8000-000000000002",
  "client_event_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
  "family_id": null,
  "expected_quantity": 3,
  "detected_quantity": 2,
  "payload": {
    "model_version": "yolo26l-demo",
    "trigger": "final",
    "counts": [
      { "family_id": "f1000001-0000-4000-8000-000000000001", "detected_quantity": 1 },
      { "family_id": "f1000002-0000-4000-8000-000000000002", "detected_quantity": 1 }
    ]
  },
  "occurred_at": "2026-09-05T18:20:00Z",
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "user_id": null
}
```

`trigger` ∈ (`initial`, `stable_window`, `final`). La **sugerencia de IA** de una familia es su `detected_quantity` en el último `auto_count` con `trigger = final` de la sesión (0 si la familia no aparece en ese lote). Los lotes `initial` y `stable_window` nunca son sugerencia de IA.

#### 1.3.6.1 Conteo de referencia de visión (v0.3.0, portal RF)

Regla de negocio de sesiones `vision` (v0.4.0: **un solo video**): el conteo inicial proviene del **primer cuadro válido** del video y el conteo final del **último cuadro válido** del **mismo** video (mismo SHA-256). Cada conteo es la inferencia de ese cuadro únicamente (sin acumulación, suma, promedio ni máximo del video); los cuadros intermedios solo se muestran y muestrean (`stable_window`). El Operador confirma cada conteo explícitamente. Un total final distinto de la **referencia** se rechaza (`close_blocked`) salvo que el Operador confirme explícitamente la diferencia; entonces se registra y se documenta como discrepancia en la validación humana. La identificación por familia se revisa y corrige en la validación humana. La coincidencia cuantitativa no sustituye la revisión humana ni los protocolos de seguridad quirúrgica.

**Hechos y datos**

| Hecho | `count_event` | `family_id` | `expected_quantity` | `detected_quantity` | `user_id` | `payload` (claves) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Conteo inicial | `auto_count`, `trigger = initial` | NULL | Σ esperado del lote | **Total físico** del primer cuadro válido | Operador (ver brechas) | `trigger`, `reason = detection_initial`, `source = first_frame`, `video{video_id, sha256, filename, size_bytes, frame_count, fps}`, `frame_index`, `video_t`, `frame_quality`, `confirmed_by_user_id`, `confirmed_at`, `expected_total`, `matches_expected`, `mismatch_acknowledged`, `model_version`, `capture_total`, `board.families[]`, `out_of_kit[]` |
| Muestra en curso | `auto_count`, `trigger = stable_window` | NULL | Σ esperado | Σ por familia (limitado a cupos) | Operador | `reason ∈ (video_window, video_ready)`, `run_id`, `board.families[]` |
| Conteo final confirmado | `auto_count`, `trigger = final` | NULL | Σ esperado del lote | **Total físico** del último cuadro válido | Operador | `trigger`, `reason = detection_close`, `source = last_frame`, `run_id`, `video{…}` (mismo `sha256` que el inicial), `frame_index`, `video_t`, `frame_quality`, `initial_count_event_id`, `reference{total, initial_total, added_total}`, `matches_reference`, `mismatch_acknowledged`, `frames_processed`, `confirmed_by_user_id`, `confirmed_at`, `capture_total`, `board.families[]`, `out_of_kit[]` |
| Conteo final rechazado | `close_blocked` | NULL | Referencia vigente | Total físico de la captura | Operador | `reason = final_count_mismatch`, `initial_count_event_id`, `initial_total`, `added_total`, `addition_event_ids[]`, `run_id`, `model_version` |
| Incorporación confirmada | `instruments_added` | NULL | Σ esperado antes del cambio | Instrumentos añadidos | SPD | `added[] = {family_id, previous, new, quantity}`, `reference_before`, `confirmed_by_role` |

**Total físico de un cuadro:** número de cajas de la inferencia de ese cuadro (cajas asignadas a una familia del kit, cajas solo con tipo y cajas `out_of_kit`).

**Cuadro válido:** decodificable y no negro, sobreexpuesto ni uniforme (brillo medio entre `VISION_FRAME_MIN_BRIGHTNESS` y `VISION_FRAME_MAX_BRIGHTNESS`, contraste ≥ `VISION_FRAME_MIN_CONTRAST`). Se revisan hasta `VISION_EDGE_FRAME_SCAN` (15) cuadros en cada extremo; si ninguno es válido, VisionWorker informa el error y no se genera conteo. Los cuadros descartados quedan en `skipped_frames`. No es Σ `board.families[].detected_quantity`, porque esa suma está limitada por los cupos del kit. VisionWorker:

- conserva como `out_of_kit` (sin familia asignada, para revisión humana) las detecciones de familias registradas fuera del kit con confianza ≥ `VISION_OUT_OF_KIT_MIN_CONF` (0.50 por defecto);
- descarta las clases del modelo no registradas como familia (no se sabe si son instrumentos) y las detecciones `out_of_kit` de baja confianza; las cuenta en `assignment_metrics`;
- no vuelve a añadir una caja retenida (*hold*) que se superpone (IoU ≥ `VISION_HOLD_DEDUPE_IOU`, 0.50) con una caja viva: es el mismo objeto reclasificado;
- el NMS es independiente de la clase, así que un objeto no produce dos cajas de clases distintas.

**Reglas**

1. Al cargar el video, VisionWorker analiza solo su primer cuadro válido (no reproduce ni procesa el video). **Register Initial Count** registra ese análisis como conteo **inicial** (al menos un instrumento; si el total difiere del inventario esperado, exige confirmación explícita). Es inmutable: repetirlo con el mismo video devuelve el registrado; otro video → 409.
2. **Referencia vigente** = total inicial + Σ `instruments_added.detected_quantity` posteriores al conteo inicial. Las incorporaciones anteriores al conteo inicial ya están en la captura inicial (se registran para trazabilidad, no suman). Las ediciones de inventario sin confirmación física son administrativas y no cambian la referencia; las disminuciones nunca suman. Reenviar las mismas cantidades no crea un evento (solo cuentan aumentos sobre la cantidad guardada; las ediciones de una sesión se serializan con `SELECT … FOR UPDATE`).
3. **Play & Run YOLO** solo se permite con el conteo inicial registrado y con el mismo video cargado (mismo SHA-256). El conteo **final** exige que el procesamiento del video haya terminado (`ready`) y el análisis propio de su último cuadro válido (`final_frame` de esa corrida). Error, procesamiento en curso o worker no disponible bloquean antes de leer conteos: nunca se usan resultados de corridas anteriores.
4. Total ≠ referencia sin confirmación → `close_blocked`, HTTP 409 y la sesión sigue `in_progress`. Cada intento rechazado queda registrado. Con la confirmación explícita del Operador → `auto_count` `final` con `matches_reference = false`: es una diferencia conocida de YOLO que la validación humana convierte en discrepancia (tabla siguiente).
5. Total = referencia → `auto_count` `final`; se abre la validación humana. Una incorporación confirmada posterior invalida ese final: hace falta un nuevo conteo final contra la referencia vigente.
6. Validación humana: `manual_count` por familia (sugerencia de IA = lote `final`, §1.3.8) más los instrumentos fuera del kit confirmados.

**Discrepancias de sesión (`family_id` NULL)**, además de las de familia (§1.3.7):

| Condición | Motivo | `expected` / `detected` |
| :--- | :--- | :--- |
| Total confirmado (familias + fuera del kit) < referencia y las faltas por familia no lo explican | `shortage` | referencia / total confirmado |
| Contingencia tras un `close_blocked`, o validación humana tras un final confirmado con diferencia, con final < referencia, y las faltas por familia no explican la diferencia | `shortage` (texto: "YOLO final capture X of Y") | referencia / total confirmado |
| Ídem con final > referencia, no explicado por excedentes ni piezas fuera del kit | `surplus` | referencia / final |
| Instrumentos fuera del kit confirmados | `surplus` | 0 / cantidad |

Cualquiera de ellas lleva la sesión a `correction_required` (conciliación SPD).

**Conteo de contingencia** (reporte manual en una sesión `vision`): solo cuando YOLO no puede dar un conteo confiable. `contingency.reason_code` ∈ (`worker_unavailable`, `processing_error`, `unreliable_detection`, `other`) es obligatorio; `notes` (≥ 10 caracteres) es obligatorio para `unreliable_detection` y `other`. Se guarda en `manual_close.payload.contingency` con el estado del worker en ese momento; usuario y fecha son `manual_close.user_id` y `occurred_at`; las cantidades verificadas, los `manual_count` y `verified_total`. La auditoría es `session.contingency_close`. No usa sugerencia de IA y no puede ocultar un faltante conocido (tabla anterior). La validación humana (`vision_validation`) solo está disponible con un `final` válido.

**Fin del Operador frente a cierre seguro:** `manual_close` (`operator_finished = true`, `safe_close = false`) termina el conteo del Operador. El **cierre** es `session_close`:

- **por el Operador** (v0.4.0, `closed_by = station_operator`, `safe_close_confirmed = true`): solo sesiones `vision` en `awaiting_spd_review` validadas por `vision_validation`, sin ninguna discrepancia (abierta o resuelta), sin `close_blocked`, sin contingencia, sin instrumentos fuera del kit (confirmados o detectados), con inicial (primer cuadro) y final (último cuadro) del mismo video, final = referencia = inventario esperado y cantidad validada = esperada en cada familia; el Operador confirma la integridad física (`physical_integrity_confirmed`) y el material recuperado. Payload: `operator_close`, `physical_integrity_confirmed`, `material_recovered`, `reconciliation{expected_total, initial_total, added_total, reference_total, final_total, validated_total, discrepancies}`. Auditoría `session.operator_close`;
- **por SPD** (`closed_by = spd_supervisor`) en los demás casos: sin discrepancias abiertas y con material recuperado; tras una contingencia exige además `contingency_reviewed = true`; con faltante físico, `safe_close_confirmed = false` (§1.3.7).

**Endpoints del portal (BackendWebFlask; HTML, no forman parte de Session API v1)**

| Endpoint | Rol | Hecho |
| :--- | :--- | :--- |
| `POST /operator/sessions/<id>/capture/upload` | Operador | Ninguno (VisionWorker guarda el video y analiza su primer cuadro) |
| `POST /operator/sessions/<id>/capture/initial` (`acknowledge_mismatch=1` si aplica) | Operador | `auto_count` `initial` (primer cuadro) |
| `POST /operator/sessions/<id>/capture/play` | Operador | Ninguno (inicia el procesamiento del mismo video; muestras `stable_window`) |
| `POST /operator/sessions/<id>/capture/final` (`acknowledge_mismatch=1` si aplica) | Operador | `auto_count` `final` (último cuadro) o `close_blocked` |
| `POST /operator/sessions/<id>/close-session` | Operador | `session_close` (vía SessionService `POST /sessions/{id}/operator-close`) |
| `POST /operator/sessions/<id>/capture/finish` | — | Retirado: ya no congela conteos (redirige a captura) |
| `POST /operator/sessions/<id>/vision-validation` | Operador | `manual_count` + `manual_close` (`operator_close_path = vision_validation`) |
| `POST /operator/sessions/<id>/manual-report` | Operador | Contingencia (`vision`) o reporte manual (`manual_no_privacy`) |
| `POST /supervisor/sessions/<id>/inventory` (`physically_added=1`) | SPD | `instruments_added` |
| `POST /supervisor/sessions/<id>/close` (`contingency_reviewed=1` si aplica) | SPD | `session_close` |

---



#### 1.3.7 Tabla `discrepancy`

**Para qué:** descuadre abierto o resuelto, trazable a sesión y opcionalmente a un evento origen.


| Campo               | Tipo        | Nulo | Default             | Significado                                     |
| ------------------- | ----------- | ---- | ------------------- | ----------------------------------------------- |
| `id`                | UUID        | NO   | `gen_random_uuid()` | PK.                                             |
| `description`       | TEXT        | NO   | —                   | Descripción legible del descuadre.              |
| `resolved`          | BOOLEAN     | NO   | `FALSE`             | Si ya se cerró.                                 |
| `resolved_at`       | TIMESTAMPTZ | SÍ   | NULL                | Cuándo se resolvió (obligatorio si `resolved`). |
| `reason_id`         | UUID        | SÍ   | NULL                | Motivo de catálogo al clasificar/resolver.      |
| `family_id`         | UUID        | SÍ   | NULL                | Familia involucrada.                            |
| `expected_quantity` | SMALLINT    | SÍ   | NULL                | Esperado (≥ 0 si informado).                    |
| `detected_quantity` | SMALLINT    | SÍ   | NULL                | Detectado (≥ 0 si informado).                   |
| `session_id`        | UUID        | NO   | —                   | Sesión.                                         |
| `origin_event_id`   | UUID        | SÍ   | NULL                | `count_event` que originó el aviso.             |
| `updated_at`        | TIMESTAMPTZ | NO   | `now()`             | Última actualización.                           |


**Hacia dónde apunta**


| Campo             | Apunta a                      | Relación | Por qué                   | ON DELETE  |
| ----------------- | ----------------------------- | -------- | ------------------------- | ---------- |
| `reason_id`       | `cat_discrepancy_reason.id`   | N:0..1   | Clasificación controlada. | `SET NULL` |
| `family_id`       | `instrument_family.id` (DS03) | N:0..1   | Tipo en descuadre.        | `SET NULL` |
| `session_id`      | `work_session.id`             | N:1      | Dueño.                    | `CASCADE`  |
| `origin_event_id` | `count_event.id`              | N:0..1   | Hecho disparador.         | `SET NULL` |


CHECK: si `resolved = TRUE` entonces `resolved_at` NOT NULL.

**Cuándo nace:** al enviar el reporte de conteo, por cada familia cuya **cantidad reportada ≠ esperada** (`shortage` si es menor, `surplus` si es mayor); `origin_event_id` = el `manual_count` de esa familia. Si cantidad reportada = esperada no hay discrepancia, aunque difiera de la IA. En sesiones `vision` también nacen discrepancias de sesión (`family_id` NULL) contra la referencia de conteo (§1.3.6.1); `origin_event_id` = el `auto_count` inicial o el `close_blocked` que las motiva, o el `final` para instrumentos fuera del kit.

**Resolución:** solo por SPD de la misma institución y solo con la sesión en `correction_required`; al resolver la última abierta la sesión pasa a `awaiting_spd_review`. No hay columna de notas de resolución: el texto libre se agrega a `description` sin traducir.

```json
{
  "id": "di000001-0000-4000-8000-000000000001",
  "description": "Falta 1 portaagujas Mayo-Hegar respecto al esperado",
  "resolved": false,
  "resolved_at": null,
  "reason_id": null,
  "family_id": "f1000001-0000-4000-8000-000000000001",
  "expected_quantity": 2,
  "detected_quantity": 1,
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "origin_event_id": "ce000001-0000-4000-8000-000000000001",
  "updated_at": "2026-09-05T18:20:05Z"
}
```

---



#### 1.3.8 Tabla `human_correction`

**Para qué:** corrección humana de una sugerencia de IA. **Una** corrección por `count_event` (UNIQUE).

**Semántica (v0.2.1):**

| Comparación | Resultado |
| :--- | :--- |
| Sugerencia de IA ≠ cantidad reportada | `human_correction` (ligada al `manual_count` de esa familia) |
| Cantidad reportada ≠ esperada | `discrepancy` |

**Justificación (v0.2.2):** el texto libre del operador (`notes` del reporte) existe solo como `justification` de una corrección: es obligatorio (no vacío ni solo espacios) cuando hay corrección y debe ser nulo/ausente cuando no la hay (IA = humano, sin sugerencia de IA o sesión `manual_no_privacy`); no se guarda en `manual_count.payload`. El motivo estructurado de una discrepancia es `reason_code`.

Son conceptos independientes. Ejemplo: esperado 2, IA 1, humano 2 → **sí** `human_correction`, **no** discrepancia. En sesiones `manual_no_privacy` no hay sugerencia de IA, así que no se crea `human_correction` por comparación con IA (las diferencias contra el esperado son discrepancias).

**Uso heredado del portal web (no es la semántica objetivo):** `rf_session.submit_manual_close` crea `human_correction` cuando la cantidad reportada difiere del **esperado** (justificación = motivo + notas). Se conserva hasta migrar el portal; Session Service aplica la semántica de esta versión.


| Campo            | Tipo        | Nulo | Default             | Significado                                   |
| ---------------- | ----------- | ---- | ------------------- | --------------------------------------------- |
| `id`             | UUID        | NO   | `gen_random_uuid()` | PK.                                           |
| `justification`  | TEXT        | NO   | —                   | Texto obligatorio: por qué el humano corrige a la IA (el `notes` del reporte de conteo). |
| `recorded_at`    | TIMESTAMPTZ | NO   | `now()`             | Momento del registro.                         |
| `count_event_id` | UUID        | NO   | —                   | Evento corregido (único).                     |
| `user_id`        | UUID        | SÍ   | NULL                | Quien registró la corrección.                 |


**Hacia dónde apunta**


| Campo            | Apunta a         | Relación                                          | Por qué                                                   | ON DELETE  |
| ---------------- | ---------------- | ------------------------------------------------- | --------------------------------------------------------- | ---------- |
| `count_event_id` | `count_event.id` | 1:1 (UNIQUE: a lo sumo una corrección por evento) | Hecho que se corrige; no se borra la historia del evento. | `CASCADE`  |
| `user_id`        | `user.id` (DS01) | N:0..1                                            | Autor de la justificación.                                | `SET NULL` |


UNIQUE `count_event_id`.

```json
{
  "id": "hc000001-0000-4000-8000-000000000001",
  "justification": "Confirmado en charola: el segundo portaagujas estaba bajo una gasa; conteo manual = 2.",
  "recorded_at": "2026-09-05T18:22:10Z",
  "count_event_id": "ce000001-0000-4000-8000-000000000001",
  "user_id": "22222222-2222-2222-2222-222222222222"
}
```

---



### 1.4 Governance and Ownership


| Aspecto                       | Responsable                                        |
| ----------------------------- | -------------------------------------------------- |
| Apertura/cierre de sesión     | Estación + Backend                                 |
| Persistencia de `count_event` | Backend / contrato con Worker (no solo UI efímera) |
| Resolución de discrepancias   | SPD de la institución de la sesión                 |
| Retención extendida           | Coordinar con PEF-DS07 / política de privacidad    |


---



### 1.5 Justificación del estándar

**Por qué este bloque**

La UI en tiempo real se olvida. Mongo guarda detalle de visión. Hace falta un lugar en PostgreSQL donde se pueda responder: “en esta sesión, a esta hora, se registró este conteo y esta discrepancia”.

**Decisiones tomadas**

1. **Snapshot** `expected_inventory` **por sesión.** Editar el kit mañana no reescribe lo que se esperaba ayer. Se puede ajustar mientras la sesión está `scheduled`/`in_progress` y queda fijo al enviar el reporte de conteo.
2. `count_event` **como hecho de negocio**, no solo log de aplicación. Reportes y auditoría SQL.
3. `client_event_id` **para idempotencia.** Reintentos de red no duplican el mismo hecho.
4. **Discrepancia como entidad propia.** Puede vivir abierta; no todo descuadre es un “update” del último evento.
5. **Corrección humana 1:1 con justificación obligatoria.** No hay “corregí el número y ya”. Corrección = humano vs IA; discrepancia = humano vs esperado.
6. **Sesión puede existir sin acuerdo de privacidad.** El acuerdo es 0..1 en DS07; p. ej. reserva previa.
7. **Fan-out UI + PG (+ Mongo opcional).** La pantalla no es la única verdad.

**Alternativas no elegidas**


| Alternativa                                         | Por qué no                                                                                                                                                      |
| --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Guardar solo el último conteo en la sesión          | Pierde línea de tiempo; no hay auditoría de cambios.                                                                                                            |
| Esperado = join vivo al kit                         | El pasado se mueve cuando editan el kit.                                                                                                                        |
| Solo Mongo para conteos                             | Reportes legales/hospitalarios suelen querer SQL relacional y retención distinta al TTL de checkpoints.                                                         |
| Meter boxes dentro de `count_event.payload`         | Infla PG; el detalle fino es DS06. El payload queda para metadatos cortos.                                                                                      |
| Varias correcciones humanas por evento sin regla    | Ambiguo cuál mandó; el UNIQUE fuerza una corrección vigente por evento (si hace falta historial largo, se versiona el evento, no se apilan correcciones mudas). |
| Discrepancia solo como flag en el evento            | Complica listar abiertas, asignar motivo y resolver sin tocar el hecho original.                                                                                |


---



### 1.6 Known implementation gaps / pending design

No son comportamiento aprobado; documentan el estado real del portal web (v0.2.1):

| Gap | Estado |
| :--- | :--- |
| UI ofrece `other` como motivo; no existe en `cat_discrepancy_reason` | Known UI/catalog gap |
| `aborted` en catálogo sin transición | State exists, behavior not implemented |
| `phase_change` | Stub (marcado `stub: true`; sin reglas de `procedure_phase` ni checkpoint) |
| Reservas de piezas: ningún flujo las crea al programar (solo existen por seed); el cierre solo las libera | Pending design |
| `operation.status` no se sincroniza con el ciclo de la sesión | Pending design |
| Payloads históricos de visión (`auto_count`) con boxes/top-k, `video_t`, `frames_processed`, `run_id` | Legacy data; no repetir |
| `manual_count` histórico con `detected_quantity` NULL (cantidad en `payload.reported_quantity`) | Legacy data; las lecturas deben contemplarlo |
| Portal: `auto_count` (`initial`, `stable_window`, `final`) se escribe con `user_id` del Operador (este DS pide NULL) | Known gap (portal) |
| Portal: los lotes `initial`/`final` aún guardan cajas compactas en `payload` (`board.families[].boxes`) | Known gap; pasan a `checkpoint_frame` (DS06) |
| Portal: `manual_count.payload.notes` se sigue guardando (este DS lo limita a `human_correction.justification`) | Known gap (portal) |
| Una pieza retirada físicamente durante la sesión no reduce la referencia | Pending design |
| Instrumentos fuera del kit: solo detecciones vivas en la última captura (sin *hold*) | Known limitation (worker) |

---

## 2. Anexo A – Información general


| Campo             | Valor                                                                               |
| ----------------- | ----------------------------------------------------------------------------------- |
| Código            | PEF-DS04                                                                            |
| Título            | Work Session and Count Facts                                                        |
| Versión           | 0.4.0 (0.4.0: un solo video — inicial del primer cuadro y final del último cuadro, confirmación explícita de diferencias, cierre por el Operador sin discrepancias; 0.3.0: referencia de conteo de visión — `trigger` `initial`, `close_blocked`, `instruments_added`, total físico y `out_of_kit`, contingencia y cierre seguro, §1.3.6.1; 0.2.2: sin `access_audit` por lote `auto_count`; `notes` solo como justificación de `human_correction`) |
| Estado            | draft                                                                               |
| Almacén           | PostgreSQL (§5.7)                                                                   |
| Fuera de alcance  | Checkpoints Mongo; object storage; WSS frame-a-frame (DS10); acuerdo privacy (DS07) |
| DDL de referencia | `esquema_base_datos_v2.md`                                                          |


