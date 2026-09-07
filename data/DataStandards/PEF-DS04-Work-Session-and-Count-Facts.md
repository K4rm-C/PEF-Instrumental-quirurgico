---
id: PEF-DS04
title: Work Session and Count Facts
version: "0.2.0"
status: draft
owner: PEF Core / Estación / Worker
applies_to: [CS, BE, Worker, WP]
stores: [PostgreSQL]
related: [PEF-DS01, PEF-DS02, PEF-DS03, PEF-DS06, PEF-DS07]
last_updated: 2026-09-06
---

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

| Término | Significado |
| :--- | :--- |
| **Sesión de trabajo** | Periodo en que un usuario cuenta o prepara conteo, ligado opcionalmente a operación, estación, kit y fase. |
| **Inventario esperado** | Cantidades por familia **congeladas** para esa sesión. |
| **Evento de conteo** | Hecho auditable (auto/manual/cambio de fase, etc.). |
| **Discrepancia** | Registro de descuadre; puede resolverse después. |
| **Corrección humana** | Justificación obligatoria ligada a un `count_event` (1:1). |

### 1.2 Uso

Estación abre/cierra sesión y muestra esperado vs detectado. Worker/backend **persisten** `count_event` además del push WSS a UI. Portal consulta historial y discrepancias.

| Pregunta | Dónde |
| :--- | :--- |
| ¿Cuántos se dieron por detectados a las 10:32 en la sesión X? | `count_event` (este DS) |
| ¿Boxes, score, traza fina? | Mongo (DS06) |
| ¿URI del JPEG? | `media_asset` (DS05) |

Fan-out del proyecto: detección → UI (WSS) **y** hecho en PG (+ checkpoint si la política lo pide).

---

### 1.3 Convenciones

En “Hacia dónde apunta”, columna **Relación**: `N:1` = muchas filas de esta tabla a una del destino; `N:0..1` = destino opcional; `1:1` = a lo sumo un par (UNIQUE); `N:N` vía tabla puente.

---

#### 1.3.1 `cat_session_status`

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(32) | NO | — | Ej. `open`, `closed`, `aborted`. |
| `name` | VARCHAR(80) | NO | — | Etiqueta. |

FKs: ninguna. UNIQUE `code`.

```json
{
  "id": "c1000001-0000-4000-8000-000000000001",
  "code": "open",
  "name": "Abierta"
}
```

---

#### 1.3.2 `cat_event_type`

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(64) | NO | — | Ej. `auto_count`, `manual_count`, `phase_change`. |
| `name` | VARCHAR(160) | NO | — | Etiqueta. |

FKs: ninguna. UNIQUE `code`.

```json
{
  "id": "c1000002-0000-4000-8000-000000000002",
  "code": "auto_count",
  "name": "Conteo automático"
}
```

---

#### 1.3.3 `cat_discrepancy_reason`

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(64) | NO | — | Ej. `left_in_patient`, `counting_error`, `extra_on_tray`. |
| `name` | VARCHAR(160) | NO | — | Etiqueta. |

FKs: ninguna. UNIQUE `code`.

```json
{
  "id": "c1000003-0000-4000-8000-000000000003",
  "code": "counting_error",
  "name": "Error de conteo / detección"
}
```

---

#### 1.3.4 Tabla `work_session`

**Para qué:** cabecera del periodo de conteo.

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK de la sesión. |
| `started_at` | TIMESTAMPTZ | NO | `now()` | Inicio. |
| `ended_at` | TIMESTAMPTZ | SÍ | NULL | Cierre. |
| `status_id` | UUID | NO | — | Estado de sesión. |
| `user_id` | UUID | NO | — | Usuario dueño / quien abre. |
| `closed_by_user_id` | UUID | SÍ | NULL | Quien cerró. |
| `operation_id` | UUID | SÍ | NULL | Caso clínico (política de producto puede exigirlo). |
| `station_id` | UUID | SÍ | NULL | Estación de captura. |
| `kit_id` | UUID | SÍ | NULL | Kit de referencia al abrir. |
| `current_phase_id` | UUID | SÍ | NULL | Fase clínica actual. |
| `phase_changed_at` | TIMESTAMPTZ | SÍ | NULL | Cuándo cambió la fase. |
| `atypical_session` | BOOLEAN | NO | `FALSE` | Sesión fuera de proceso estándar. |
| `extended_retention` | BOOLEAN | NO | `FALSE` | Señal de retención extendida de evidencia. |
| `retention_until` | TIMESTAMPTZ | SÍ | NULL | Hasta cuándo retener (si aplica). |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Última actualización de la cabecera. |

**Hacia dónde apunta**

| Campo | Apunta a | Relación | Por qué | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `status_id` | `cat_session_status.id` | N:1 | Estado controlado. | `RESTRICT` |
| `user_id` | `user.id` (DS01) | N:1 | Responsable de la sesión. | `RESTRICT` |
| `closed_by_user_id` | `user.id` (DS01) | N:0..1 | Quién cerró; puede diferir del abridor. | `SET NULL` |
| `operation_id` | `operation.id` (DS02) | N:0..1 | Contexto clínico. | `RESTRICT` |
| `station_id` | `capture_station.id` (DS02) | N:0..1 | Dónde se capturó. | `SET NULL` |
| `kit_id` | `kit.id` (DS03) | N:0..1 | Receta de referencia; el detalle esperado está en `expected_inventory`. | `SET NULL` |
| `current_phase_id` | `cat_operation_phase.id` (DS03) | N:0..1 | Fase actual. | `SET NULL` |

CHECK: `ended_at` ≥ `started_at` si ambas existen.

**Nota:** `session_processing_agreement` (0..1) es PEF-DS07. Una sesión puede existir sin acuerdo (p. ej. reserva previa).

```json
{
  "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "started_at": "2026-09-05T16:15:00Z",
  "ended_at": null,
  "status_id": "c1000001-0000-4000-8000-000000000001",
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

**Para qué:** snapshot de “cuántos deberían verse” por familia en esta sesión.

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `family_id` | UUID | NO | — | Familia esperada. |
| `expected_quantity` | SMALLINT | NO | — | Cantidad esperada (≥ 0). |
| `session_id` | UUID | NO | — | Sesión dueña. |
| `source` | VARCHAR(32) | NO | `'kit_snapshot'` | `kit_snapshot` o `manual`. |

**Hacia dónde apunta**

| Campo | Apunta a | Relación | Por qué | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `family_id` | `instrument_family.id` (DS03) | N:1 | Qué tipo se espera. | `RESTRICT` |
| `session_id` | `work_session.id` | N:1 | Congela esperado por sesión. | `CASCADE` |

UNIQUE (`session_id`, `family_id`). CHECK cantidad ≥ 0; CHECK `source` ∈ (`kit_snapshot`, `manual`).

Editar el kit **después** no reescribe solos estos snapshots.

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

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK del hecho. |
| `event_type_id` | UUID | NO | — | Tipo de evento. |
| `client_event_id` | UUID | SÍ | NULL | Id del cliente para idempotencia (único si viene). |
| `family_id` | UUID | SÍ | NULL | Familia afectada (nulo si el evento no es por familia). |
| `expected_quantity` | SMALLINT | SÍ | NULL | Esperado en ese momento (≥ 0 si informado). |
| `detected_quantity` | SMALLINT | SÍ | NULL | Detectado/contabilizado (≥ 0 si informado). |
| `payload` | JSONB | NO | `'{}'` | Datos extra controlados (sin PII innecesaria). |
| `occurred_at` | TIMESTAMPTZ | NO | `now()` | Momento del hecho. |
| `session_id` | UUID | NO | — | Sesión. |
| `user_id` | UUID | SÍ | NULL | Usuario si el evento fue humano; nulo si solo sistema. |

**Hacia dónde apunta**

| Campo | Apunta a | Relación | Por qué | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `event_type_id` | `cat_event_type.id` | N:1 | Clasifica el hecho. | `RESTRICT` |
| `family_id` | `instrument_family.id` (DS03) | N:0..1 | Tipo contado. | `SET NULL` |
| `session_id` | `work_session.id` | N:1 | Dueño temporal. | `CASCADE` |
| `user_id` | `user.id` (DS01) | N:0..1 | Actor humano opcional. | `SET NULL` |

UNIQUE `client_event_id` (cuando no es nulo, vía UNIQUE en columna).

```json
{
  "id": "ce000001-0000-4000-8000-000000000001",
  "event_type_id": "c1000002-0000-4000-8000-000000000002",
  "client_event_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
  "family_id": "f1000001-0000-4000-8000-000000000001",
  "expected_quantity": 2,
  "detected_quantity": 1,
  "payload": {
    "model_version_tag": "yolo11n-pef-2026-03",
    "confidence_mean": 0.91,
    "ui_source": "station"
  },
  "occurred_at": "2026-09-05T18:20:00Z",
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "user_id": null
}
```

---

#### 1.3.7 Tabla `discrepancy`

**Para qué:** descuadre abierto o resuelto, trazable a sesión y opcionalmente a un evento origen.

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `description` | TEXT | NO | — | Descripción legible del descuadre. |
| `resolved` | BOOLEAN | NO | `FALSE` | Si ya se cerró. |
| `resolved_at` | TIMESTAMPTZ | SÍ | NULL | Cuándo se resolvió (obligatorio si `resolved`). |
| `reason_id` | UUID | SÍ | NULL | Motivo de catálogo al clasificar/resolver. |
| `family_id` | UUID | SÍ | NULL | Familia involucrada. |
| `expected_quantity` | SMALLINT | SÍ | NULL | Esperado (≥ 0 si informado). |
| `detected_quantity` | SMALLINT | SÍ | NULL | Detectado (≥ 0 si informado). |
| `session_id` | UUID | NO | — | Sesión. |
| `origin_event_id` | UUID | SÍ | NULL | `count_event` que originó el aviso. |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Última actualización. |

**Hacia dónde apunta**

| Campo | Apunta a | Relación | Por qué | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `reason_id` | `cat_discrepancy_reason.id` | N:0..1 | Clasificación controlada. | `SET NULL` |
| `family_id` | `instrument_family.id` (DS03) | N:0..1 | Tipo en descuadre. | `SET NULL` |
| `session_id` | `work_session.id` | N:1 | Dueño. | `CASCADE` |
| `origin_event_id` | `count_event.id` | N:0..1 | Hecho disparador. | `SET NULL` |

CHECK: si `resolved = TRUE` entonces `resolved_at` NOT NULL.

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

**Para qué:** corrección/validación humana de un evento. **Una** corrección por `count_event` (UNIQUE).

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `justification` | TEXT | NO | — | Texto obligatorio: por qué se corrige/valida. |
| `recorded_at` | TIMESTAMPTZ | NO | `now()` | Momento del registro. |
| `count_event_id` | UUID | NO | — | Evento corregido (único). |
| `user_id` | UUID | SÍ | NULL | Quien registró la corrección. |

**Hacia dónde apunta**

| Campo | Apunta a | Relación | Por qué | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `count_event_id` | `count_event.id` | 1:1 (UNIQUE: a lo sumo una corrección por evento) | Hecho que se corrige; no se borra la historia del evento. | `CASCADE` |
| `user_id` | `user.id` (DS01) | N:0..1 | Autor de la justificación. | `SET NULL` |

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

| Aspecto | Responsable |
| :--- | :--- |
| Apertura/cierre de sesión | Estación + Backend |
| Persistencia de `count_event` | Backend / contrato con Worker (no solo UI efímera) |
| Resolución de discrepancias | Personal clínico autorizado |
| Retención extendida | Coordinar con PEF-DS07 / política de privacidad |

---

### 1.5 Justificación del estándar

**Por qué este bloque**

La UI en tiempo real se olvida. Mongo guarda detalle de visión. Hace falta un lugar en PostgreSQL donde se pueda responder: “en esta sesión, a esta hora, se registró este conteo y esta discrepancia”.

**Decisiones tomadas**

1. **Snapshot `expected_inventory` por sesión.** Editar el kit mañana no reescribe lo que se esperaba ayer.
2. **`count_event` como hecho de negocio**, no solo log de aplicación. Reportes y auditoría SQL.
3. **`client_event_id` para idempotencia.** Reintentos de red no duplican el mismo hecho.
4. **Discrepancia como entidad propia.** Puede vivir abierta; no todo descuadre es un “update” del último evento.
5. **Corrección humana 1:1 con justificación obligatoria.** No hay “corregí el número y ya”.
6. **Sesión puede existir sin acuerdo de privacidad.** El acuerdo es 0..1 en DS07; p. ej. reserva previa.
7. **Fan-out UI + PG (+ Mongo opcional).** La pantalla no es la única verdad.

**Alternativas no elegidas**

| Alternativa | Por qué no |
| :--- | :--- |
| Guardar solo el último conteo en la sesión | Pierde línea de tiempo; no hay auditoría de cambios. |
| Esperado = join vivo al kit | El pasado se mueve cuando editan el kit. |
| Solo Mongo para conteos | Reportes legales/hospitalarios suelen querer SQL relacional y retención distinta al TTL de checkpoints. |
| Meter boxes dentro de `count_event.payload` siempre | Infla PG; el detalle fino es DS06. El payload queda para metadatos cortos. |
| Varias correcciones humanas por evento sin regla | Ambiguo cuál mandó; el UNIQUE fuerza una corrección vigente por evento (si hace falta historial largo, se versiona el evento, no se apilan correcciones mudas). |
| Discrepancia solo como flag en el evento | Complica listar abiertas, asignar motivo y resolver sin tocar el hecho original. |

---

## 2. Anexo A – Información general

| Campo | Valor |
| :--- | :--- |
| Código | PEF-DS04 |
| Título | Work Session and Count Facts |
| Versión | 0.2.0 |
| Estado | draft |
| Almacén | PostgreSQL (§5.7) |
| Fuera de alcance | Checkpoints Mongo; object storage; WSS frame-a-frame (DS10); acuerdo privacy (DS07) |
| DDL de referencia | `esquema_base_datos_v2.md` |
