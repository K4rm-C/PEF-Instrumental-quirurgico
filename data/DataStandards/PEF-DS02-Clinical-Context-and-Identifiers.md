---
id: PEF-DS02
title: Clinical Context and Identifiers
version: "0.2.0"
status: draft
owner: PEF Core / Integraciones
applies_to: [WP, BE, MS, AP]
stores: [PostgreSQL]
related: [PEF-DS01, PEF-DS03, PEF-DS04, PEF-DS08]
last_updated: 2026-09-06
---

# PEF-DS02 · Clinical Context and Identifiers

## INTRODUCCION

Este estándar define el **contexto clínico-operativo** alrededor del conteo: paciente, médico, quirófano, estación de captura, operación, y los **identificadores externos** estilo FHIR Identifier (`resource_identifier`).

También es dueño de estos catálogos: `cat_gender`, `cat_specialty`, `cat_procedure_type`, `cat_surgical_role`, `cat_operation_status`.

No define kits ni piezas (PEF-DS03), ni la sesión/eventos de conteo (PEF-DS04), ni el Bundle de export FHIR (PEF-DS08).

Abajo: **todos** los campos de cada tabla dueña, FKs con motivo, y una **fila completa** de ejemplo por tabla.

---



## 1. Explicación del estándar



### 1.1 Definiciones


| Término                      | Significado en PEF                                                                                                                      |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| **Paciente**                 | Persona asociada a la operación. PEF guarda datos mínimos; el expediente “oficial” suele vivir en el HIS y se enlaza por identificador. |
| **Médico**                   | Profesional de la institución. En una operación lleva un **rol quirúrgico** (cirujano, ayudante, …), distinto del rol de login (DS01).  |
| **Quirófano**                | Sala física (`operating_room`) dentro de la institución.                                                                                |
| **Estación de captura**      | Cámara/ROI sobre charola, anclada a un quirófano.                                                                                       |
| **Operación**                | Caso clínico-operativo (programado o en curso) con estado, fechas y vínculos.                                                           |
| **Identificador de recurso** | Par `system` + `value` que apunta a un UUID PEF (`resource_type` + `resource_id`).                                                      |




### 1.2 Uso

**Quién:** portal; apps Android/Desktop vía MS; estación al elegir operación/estación.

**Para qué**

1. Saber en qué caso y sala se cuenta.
2. Relacionar conteo con pacientes/equipo sin meter el expediente completo en tablas de visión.
3. Traducir IDs del hospital (MRN, código de quirófano) al UUID interno.

**Flujo típico:** crear/obtener operación → asociar pacientes y médicos → asignar sala → estación ya registrada en esa sala → si llega ID externo, resolver vía `resource_identifier`.

---



### 1.3 Convenciones

- Catálogos: `code` estable en inglés; `name` legible (puede ir en español en datos).
- Identificadores externos activos: únicos por (`system`, `value`).
- No hay FK polimórfica física de `resource_identifier.resource_id` a seis tablas; la app garantiza coherencia.
- En “Hacia dónde apunta”, **Relación**: `N:1` = muchas filas aquí a una allá; `N:0..1` = destino opcional; `N:N` vía tabla puente (cada FK del puente es `N:1`).

---



#### 1.3.1 Catálogo `cat_gender`

**Para qué:** valor controlado de sexo/género administrativo del paciente.


| Campo  | Tipo        | Nulo | Default             | Significado                                            |
| ------ | ----------- | ---- | ------------------- | ------------------------------------------------------ |
| `id`   | UUID        | NO   | `gen_random_uuid()` | PK del catálogo.                                       |
| `code` | VARCHAR(32) | NO   | —                   | Código estable (`female`, `male`, `other`, `unknown`). |
| `name` | VARCHAR(80) | NO   | —                   | Etiqueta legible.                                      |


**FKs:** ninguna (tabla raíz de catálogo). UNIQUE `code`.

```json
{
  "id": "a1000001-0000-4000-8000-000000000001",
  "code": "female",
  "name": "Femenino"
}
```

---



#### 1.3.2 Catálogo `cat_specialty`

**Para qué:** especialidad médica del profesional.


| Campo  | Tipo         | Nulo | Default             | Significado                            |
| ------ | ------------ | ---- | ------------------- | -------------------------------------- |
| `id`   | UUID         | NO   | `gen_random_uuid()` | PK.                                    |
| `code` | VARCHAR(64)  | NO   | —                   | Código estable (`general_surgery`, …). |
| `name` | VARCHAR(160) | NO   | —                   | Nombre legible.                        |


**FKs:** ninguna. UNIQUE `code`.

```json
{
  "id": "a1000002-0000-4000-8000-000000000002",
  "code": "general_surgery",
  "name": "Cirugía general"
}
```

---



#### 1.3.3 Catálogo `cat_procedure_type`

**Para qué:** tipo de procedimiento clínico. Lo usan operación (este DS) y kits/fases/usos (DS03). **Definición canónica aquí.**


| Campo  | Tipo         | Nulo | Default             | Significado     |
| ------ | ------------ | ---- | ------------------- | --------------- |
| `id`   | UUID         | NO   | `gen_random_uuid()` | PK.             |
| `code` | VARCHAR(64)  | NO   | —                   | Código estable. |
| `name` | VARCHAR(200) | NO   | —                   | Nombre legible. |


**FKs:** ninguna. UNIQUE `code`.

```json
{
  "id": "a1000003-0000-4000-8000-000000000003",
  "code": "inguinal_hernia_open",
  "name": "Hernioplastia inguinal abierta"
}
```

---



#### 1.3.4 Catálogo `cat_surgical_role`

**Para qué:** rol del médico **dentro de una operación** (no es rol de login).


| Campo  | Tipo         | Nulo | Default             | Significado                         |
| ------ | ------------ | ---- | ------------------- | ----------------------------------- |
| `id`   | UUID         | NO   | `gen_random_uuid()` | PK.                                 |
| `code` | VARCHAR(64)  | NO   | —                   | Ej. `primary_surgeon`, `assistant`. |
| `name` | VARCHAR(120) | NO   | —                   | Etiqueta.                           |


**FKs:** ninguna. UNIQUE `code`.

```json
{
  "id": "a1000004-0000-4000-8000-000000000004",
  "code": "primary_surgeon",
  "name": "Cirujano principal"
}
```

---



#### 1.3.5 Catálogo `cat_operation_status`

**Para qué:** estado del caso operativo.


| Campo  | Tipo        | Nulo | Default             | Significado                                               |
| ------ | ----------- | ---- | ------------------- | --------------------------------------------------------- |
| `id`   | UUID        | NO   | `gen_random_uuid()` | PK.                                                       |
| `code` | VARCHAR(32) | NO   | —                   | Ej. `scheduled`, `in_progress`, `completed`, `cancelled`. |
| `name` | VARCHAR(80) | NO   | —                   | Etiqueta.                                                 |


**FKs:** ninguna. UNIQUE `code`.

```json
{
  "id": "a1000005-0000-4000-8000-000000000005",
  "code": "in_progress",
  "name": "En curso"
}
```

---



#### 1.3.6 Tabla `patient`

**Para qué:** registro mínimo de paciente en PEF.


| Campo            | Tipo         | Nulo | Default             | Significado                                                     |
| ---------------- | ------------ | ---- | ------------------- | --------------------------------------------------------------- |
| `id`             | UUID         | NO   | `gen_random_uuid()` | PK interno.                                                     |
| `display_name`   | VARCHAR(160) | NO   | —                   | Nombre para pantallas (puede ser seudonimizado según política). |
| `birth_date`     | DATE         | SÍ   | NULL                | Fecha de nacimiento si se captura.                              |
| `active`         | BOOLEAN      | NO   | `TRUE`              | Paciente usable en nuevas operaciones.                          |
| `gender_id`      | UUID         | SÍ   | NULL                | Sexo/género administrativo.                                     |
| `institution_id` | UUID         | NO   | —                   | Tenant dueño del registro.                                      |


**Hacia dónde apunta**


| Campo            | Apunta a                | Relación | Por qué                     | ON DELETE  |
| ---------------- | ----------------------- | -------- | --------------------------- | ---------- |
| `gender_id`      | `cat_gender.id`         | N:0..1   | Valor controlado; opcional. | `SET NULL` |
| `institution_id` | `institution.id` (DS01) | N:1      | Aislamiento multi-tenant.   | `RESTRICT` |


```json
{
  "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  "display_name": "Paciente Demo",
  "birth_date": "1988-04-12",
  "active": true,
  "gender_id": "a1000001-0000-4000-8000-000000000001",
  "institution_id": "11111111-1111-1111-1111-111111111111"
}
```

---



#### 1.3.7 Tabla `physician`

**Para qué:** profesional clínico de la institución.


| Campo            | Tipo         | Nulo | Default             | Significado                              |
| ---------------- | ------------ | ---- | ------------------- | ---------------------------------------- |
| `id`             | UUID         | NO   | `gen_random_uuid()` | PK.                                      |
| `name`           | VARCHAR(160) | NO   | —                   | Nombre del médico.                       |
| `active`         | BOOLEAN      | NO   | `TRUE`              | Si puede asignarse a operaciones nuevas. |
| `institution_id` | UUID         | NO   | —                   | Institución.                             |


**Hacia dónde apunta**


| Campo            | Apunta a         | Relación | Por qué | ON DELETE  |
| ---------------- | ---------------- | -------- | ------- | ---------- |
| `institution_id` | `institution.id` | N:1      | Tenant. | `RESTRICT` |


```json
{
  "id": "c0c0c0c0-c0c0-c0c0-c0c0-c0c0c0c0c0c0",
  "name": "Dr. Demo Ruiz",
  "active": true,
  "institution_id": "11111111-1111-1111-1111-111111111111"
}
```

---



#### 1.3.8 Tabla `physician_specialty`

**Para qué:** N:M médico ↔ especialidad.


| Campo          | Tipo | Nulo | Default             | Significado        |
| -------------- | ---- | ---- | ------------------- | ------------------ |
| `id`           | UUID | NO   | `gen_random_uuid()` | PK de la relación. |
| `physician_id` | UUID | NO   | —                   | Médico.            |
| `specialty_id` | UUID | NO   | —                   | Especialidad.      |


**Hacia dónde apunta**


| Campo          | Apunta a           | Relación                                        | Por qué                            | ON DELETE                                   |
| -------------- | ------------------ | ----------------------------------------------- | ---------------------------------- | ------------------------------------------- |
| `physician_id` | `physician.id`     | N:1 (physician↔specialty es N:N vía esta tabla) | Dueño de la especialidad asignada. | `CASCADE`                                   |
| `specialty_id` | `cat_specialty.id` | N:1 (physician↔specialty es N:N vía esta tabla) | Catálogo.                          | `RESTRICT` — no borrar especialidad en uso. |


UNIQUE (`physician_id`, `specialty_id`).

```json
{
  "id": "d0d0d0d0-d0d0-d0d0-d0d0-d0d0d0d0d0d0",
  "physician_id": "c0c0c0c0-c0c0-c0c0-c0c0-c0c0c0c0c0c0",
  "specialty_id": "a1000002-0000-4000-8000-000000000002"
}
```

---



#### 1.3.9 Tabla `operating_room`

**Para qué:** sala física.


| Campo            | Tipo         | Nulo | Default             | Significado                                  |
| ---------------- | ------------ | ---- | ------------------- | -------------------------------------------- |
| `id`             | UUID         | NO   | `gen_random_uuid()` | PK.                                          |
| `code`           | VARCHAR(32)  | NO   | —                   | Código corto único por institución (`OR-3`). |
| `name`           | VARCHAR(120) | NO   | —                   | Nombre legible.                              |
| `active`         | BOOLEAN      | NO   | `TRUE`              | Sala usable.                                 |
| `institution_id` | UUID         | NO   | —                   | Institución.                                 |


**Hacia dónde apunta**


| Campo            | Apunta a         | Relación | Por qué | ON DELETE |
| ---------------- | ---------------- | -------- | ------- | --------- |
| `institution_id` | `institution.id` | N:1      | Tenant. | `CASCADE` |


UNIQUE (`institution_id`, `code`).

```json
{
  "id": "e0e0e0e0-e0e0-e0e0-e0e0-e0e0e0e0e0e0",
  "code": "OR-3",
  "name": "Quirófano 3",
  "active": true,
  "institution_id": "11111111-1111-1111-1111-111111111111"
}
```

---



#### 1.3.10 Tabla `capture_station`

**Para qué:** punto de captura (cámara) sobre charola.


| Campo     | Tipo         | Nulo | Default             | Significado                                                   |
| --------- | ------------ | ---- | ------------------- | ------------------------------------------------------------- |
| `id`      | UUID         | NO   | `gen_random_uuid()` | PK.                                                           |
| `name`    | VARCHAR(120) | NO   | —                   | Nombre de la estación.                                        |
| `roi`     | JSONB        | SÍ   | NULL                | Región de interés normalizada o en píxeles (contrato de app). |
| `active`  | BOOLEAN      | NO   | `TRUE`              | Estación usable.                                              |
| `room_id` | UUID         | NO   | —                   | Quirófano donde está instalada.                               |


**Hacia dónde apunta**


| Campo     | Apunta a            | Relación | Por qué                                     | ON DELETE |
| --------- | ------------------- | -------- | ------------------------------------------- | --------- |
| `room_id` | `operating_room.id` | N:1      | La estación no flota: pertenece a una sala. | `CASCADE` |


```json
{
  "id": "f0f0f0f0-f0f0-f0f0-f0f0-f0f0f0f0f0f0",
  "name": "Charola OR-3 A",
  "roi": { "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.7, "coord_space": "normalized" },
  "active": true,
  "room_id": "e0e0e0e0-e0e0-e0e0-e0e0-e0e0e0e0e0e0"
}
```

---



#### 1.3.11 Tabla `operation`

**Para qué:** caso clínico-operativo al que se cuelga el conteo.


| Campo               | Tipo        | Nulo | Default             | Significado            |
| ------------------- | ----------- | ---- | ------------------- | ---------------------- |
| `id`                | UUID        | NO   | `gen_random_uuid()` | PK.                    |
| `scheduled_at`      | TIMESTAMPTZ | SÍ   | NULL                | Hora programada.       |
| `started_at`        | TIMESTAMPTZ | SÍ   | NULL                | Inicio real.           |
| `ended_at`          | TIMESTAMPTZ | SÍ   | NULL                | Fin real.              |
| `status_id`         | UUID        | NO   | —                   | Estado actual.         |
| `procedure_type_id` | UUID        | SÍ   | NULL                | Tipo de procedimiento. |
| `room_id`           | UUID        | SÍ   | NULL                | Sala asignada.         |
| `institution_id`    | UUID        | NO   | —                   | Tenant.                |


**Hacia dónde apunta**


| Campo               | Apunta a                  | Relación | Por qué                               | ON DELETE  |
| ------------------- | ------------------------- | -------- | ------------------------------------- | ---------- |
| `status_id`         | `cat_operation_status.id` | N:1      | Estado controlado.                    | `RESTRICT` |
| `procedure_type_id` | `cat_procedure_type.id`   | N:0..1   | Clasifica el caso; opcional al crear. | `SET NULL` |
| `room_id`           | `operating_room.id`       | N:0..1   | Dónde ocurre.                         | `SET NULL` |
| `institution_id`    | `institution.id`          | N:1      | Tenant.                               | `RESTRICT` |


**Check:** `ended_at` ≥ `started_at` cuando ambas existen.

```json
{
  "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "scheduled_at": "2026-09-05T16:00:00Z",
  "started_at": "2026-09-05T16:12:00Z",
  "ended_at": null,
  "status_id": "a1000005-0000-4000-8000-000000000005",
  "procedure_type_id": "a1000003-0000-4000-8000-000000000003",
  "room_id": "e0e0e0e0-e0e0-e0e0-e0e0-e0e0e0e0e0e0",
  "institution_id": "11111111-1111-1111-1111-111111111111"
}
```

---



#### 1.3.12 Tabla `operation_patient`

**Para qué:** N:M operación ↔ paciente (una operación puede involucrar más de un paciente en escenarios especiales; lo habitual es uno).


| Campo          | Tipo | Nulo | Default             | Significado |
| -------------- | ---- | ---- | ------------------- | ----------- |
| `id`           | UUID | NO   | `gen_random_uuid()` | PK.         |
| `operation_id` | UUID | NO   | —                   | Operación.  |
| `patient_id`   | UUID | NO   | —                   | Paciente.   |


**Hacia dónde apunta**


| Campo          | Apunta a       | Relación                                      | Por qué  | ON DELETE                                   |
| -------------- | -------------- | --------------------------------------------- | -------- | ------------------------------------------- |
| `operation_id` | `operation.id` | N:1 (operation↔patient es N:N vía esta tabla) | Caso.    | `CASCADE`                                   |
| `patient_id`   | `patient.id`   | N:1 (operation↔patient es N:N vía esta tabla) | Persona. | `RESTRICT` — no borrar paciente aún ligado. |


UNIQUE (`operation_id`, `patient_id`).

```json
{
  "id": "aa11aa11-aa11-aa11-aa11-aa11aa11aa11",
  "operation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "patient_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
}
```

---



#### 1.3.13 Tabla `operation_physician`

**Para qué:** médicos en la operación **con** rol quirúrgico.


| Campo              | Tipo | Nulo | Default             | Significado      |
| ------------------ | ---- | ---- | ------------------- | ---------------- |
| `id`               | UUID | NO   | `gen_random_uuid()` | PK.              |
| `operation_id`     | UUID | NO   | —                   | Operación.       |
| `physician_id`     | UUID | NO   | —                   | Médico.          |
| `surgical_role_id` | UUID | NO   | —                   | Rol en ese caso. |


**Hacia dónde apunta**


| Campo              | Apunta a               | Relación                                        | Por qué         | ON DELETE  |
| ------------------ | ---------------------- | ----------------------------------------------- | --------------- | ---------- |
| `operation_id`     | `operation.id`         | N:1 (operation↔physician es N:N vía esta tabla) | Caso.           | `CASCADE`  |
| `physician_id`     | `physician.id`         | N:1 (operation↔physician es N:N vía esta tabla) | Profesional.    | `RESTRICT` |
| `surgical_role_id` | `cat_surgical_role.id` | N:1                                             | Rol quirúrgico. | `RESTRICT` |


UNIQUE (`operation_id`, `physician_id`, `surgical_role_id`) — la misma persona puede aparecer con roles distintos si el proceso lo permite.

```json
{
  "id": "bb22bb22-bb22-bb22-bb22-bb22bb22bb22",
  "operation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "physician_id": "c0c0c0c0-c0c0-c0c0-c0c0-c0c0c0c0c0c0",
  "surgical_role_id": "a1000004-0000-4000-8000-000000000004"
}
```

---



#### 1.3.14 Tabla `resource_identifier`

**Para qué:** mapa Identifier FHIR (`system`|`value`) → recurso PEF UUID. Sirve para HIS, códigos de sala, instrumentos serializados externos, etc.


| Campo           | Tipo         | Nulo | Default             | Significado                                                                                   |
| --------------- | ------------ | ---- | ------------------- | --------------------------------------------------------------------------------------------- |
| `id`            | UUID         | NO   | `gen_random_uuid()` | PK de la fila identificador.                                                                  |
| `resource_type` | VARCHAR(32)  | NO   | —                   | Tipo PEF: `institution`, `patient`, `physician`, `operation`, `operating_room`, `instrument`. |
| `resource_id`   | UUID         | NO   | —                   | UUID de la fila PEF (sin FK física polimórfica).                                              |
| `system`        | URI          | NO   | —                   | Namespace del emisor (URI).                                                                   |
| `value`         | VARCHAR(255) | NO   | —                   | Valor en ese sistema.                                                                         |
| `use_code`      | VARCHAR(16)  | NO   | `'official'`        | `usual`, `official`, `temp`, `secondary`, `old`.                                              |
| `period_start`  | TIMESTAMPTZ  | SÍ   | NULL                | Inicio de vigencia.                                                                           |
| `period_end`    | TIMESTAMPTZ  | SÍ   | NULL                | Fin de vigencia.                                                                              |
| `active`        | BOOLEAN      | NO   | `TRUE`              | Solo activos entran al índice único.                                                          |
| `first_seen_at` | TIMESTAMPTZ  | NO   | `now()`             | Primera vez que PEF vio este identificador.                                                   |


**Hacia dónde apunta**


| Campo         | Apunta a                            | Relación                                                                                      | Por qué                                      | ON DELETE           |
| ------------- | ----------------------------------- | --------------------------------------------------------------------------------------------- | -------------------------------------------- | ------------------- |
| `resource_id` | Lógico: tabla según `resource_type` | N:1 lógico (muchos identificadores → un recurso; un recurso puede tener 0..N identificadores) | Evita FK polimórfica ilegal en SQL estricto. | N/A (sin FK física) |


**Checks:** `resource_type` ∈ lista; `use_code` ∈ lista; `period_end` ≥ `period_start` si ambos existen.  
**Índice único parcial:** (`system`, `value`) WHERE `active = TRUE`.

```json
{
  "id": "cc33cc33-cc33-cc33-cc33-cc33cc33cc33",
  "resource_type": "patient",
  "resource_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  "system": "https://demo.hospital/fhir/sid/mrn",
  "value": "MRN-0001",
  "use_code": "official",
  "period_start": "2026-01-01T00:00:00Z",
  "period_end": null,
  "active": true,
  "first_seen_at": "2026-03-15T10:00:00Z"
}
```

---



### 1.4 Governance and Ownership


| Aspecto                      | Responsable                             |
| ---------------------------- | --------------------------------------- |
| Dueño                        | PEF Core + Integraciones (IDs externos) |
| Alta pacientes/médicos/salas | Portal / MS según canal                 |
| Catálogos clínicos           | Gobierno de datos PEF                   |


**Límites:** `instrument` puede tener Identifier aquí, pero la pieza es dueña de DS03. La sesión de conteo referencia `operation_id`/`station_id` pero es DS04.

---



### 1.5 Justificación del estándar

**Por qué este bloque**

El conteo solo tiene sentido clínico si se sabe **en qué operación y sala** ocurrió. Separarlo del instrumental y de los eventos evita que el HIS o el catálogo de kits reescriban el modelo del otro.

**Decisiones tomadas**

1. **Paciente mínimo en PEF** (`display_name`, no expediente completo): PEF no reemplaza al HIS; reduce PII local.
2. **N:M operación–paciente y operación–médico:** soporta casos reales sin asumir “siempre uno”.
3. **Rol quirúrgico en la relación, no en** `physician`**:** la misma persona puede ser cirujano en un caso y ayudante en otro.
4. **Estación cuelga de sala, no de institución directo:** el ROI es físico; el tenant se hereda por la sala.
5. `resource_identifier` **estilo FHIR sin FK polimórfica:** UUID interno estable; IDs externos versionables con `active`/`period_`*.
6. `cat_procedure_type` **canónico en este DS:** el procedimiento es concepto clínico primero; kits (DS03) lo reutilizan.

**Alternativas no elegidas**


| Alternativa                                         | Por qué no                                                                                       |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Meter MRN como columna en `patient`                 | Un paciente puede tener varios sistemas emisores; Identifier es más fiel a FHIR y a la realidad. |
| FK polimórfica o tabla por tipo de ID               | Explota el número de tablas; el patrón Identifier unifica consulta por `system`+`value`.         |
| Unificar `user` y `physician`                       | Quien opera la estación no es necesariamente el cirujano listado.                                |
| Guardar ROI solo en config de archivo en el edge    | Pierde trazabilidad central; la estación es entidad de negocio (mantenimiento, sesión).          |
| Operación embebida solo como JSON en la sesión      | Impide listar/programar operaciones sin abrir sesión de conteo.                                  |
| Copiar especialidad como texto libre en `physician` | Rompe reportes; el catálogo mantiene códigos estables.                                           |


---



## 2. Anexo A – Información general


| Campo             | Valor                                                          |
| ----------------- | -------------------------------------------------------------- |
| Código            | PEF-DS02                                                       |
| Título            | Clinical Context and Identifiers                               |
| Versión           | 0.2.0                                                          |
| Estado            | draft                                                          |
| Almacén           | PostgreSQL (catálogos clínicos + §5.4 + `resource_identifier`) |
| Fuera de alcance  | Kits, YOLO, `work_session`, `count_event`, Bundle FHIR export  |
| DDL de referencia | `esquema_base_datos_v2.md`                                     |


