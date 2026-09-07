---
id: PEF-DS03
title: Instrument Kits and Model Registry
version: "0.2.0"
status: draft
owner: PEF Core / Visión
applies_to: [WP, BE, Worker, MS]
stores: [PostgreSQL]
related: [PEF-DS02, PEF-DS04, PEF-DS05]
last_updated: 2026-09-06
---

# PEF-DS03 · Instrument Kits and Model Registry

## INTRODUCCION

Este estándar define **qué se cuenta** y **cómo el modelo de visión nombra las clases**.

Tablas dueñas: `instrument_family`, `family_example`, `instrument`, `instrument_usage`, `instrument_cycle_event`, `kit`, `kit_item`, `procedure_kit`, `procedure_phase`, `instrument_reservation`, `yolo_model`, `model_class`.

Catálogos dueños: `cat_instrument_category`, `cat_usage_context`, `cat_instrument_cycle_status`, `cat_operation_phase`.

`cat_procedure_type` se define en PEF-DS02; aquí solo se referencia.

No define sesión/eventos de conteo (PEF-DS04). No define bytes de `.pt`/fotos (solo el puntero `media_asset_id`; el asset es PEF-DS05).

Abajo: **todos** los campos, FKs con motivo, y **fila completa** por tabla.

---

## 1. Explicación del estándar

### 1.1 Definiciones


| Término                   | Significado                                                                        |
| ------------------------- | ---------------------------------------------------------------------------------- |
| **Familia**               | Tipo de instrumento a nivel catálogo (lo que el modelo suele detectar como clase). |
| **Pieza**                 | Unidad física en stock de una institución.                                         |
| **Kit**                   | Receta versionada: cantidades por familia (no lista UUIDs de piezas).              |
| **Procedure kit**         | Vínculo procedimiento ↔ kit (con default opcional).                                |
| **Fase de procedimiento** | Orden de fases clínicas y si el conteo es obligatorio.                             |
| **Reserva**               | Pieza asignada a una operación mientras `active`.                                  |
| **Modelo YOLO**           | Versión del detector; como máximo uno `active = true`.                             |
| **Clase de modelo**       | Entero `yolo_class_id` ↔ `family_id`.                                              |


### 1.2 Uso

Portal arma familias/kits y publica modelos. Worker carga pesos y traduce clase→familia. Al abrir sesión (DS04) se suele copiar el kit a `expected_inventory`.

**Idea de stock:** el stock son las filas `instrument`. El kit habla de familias. En operación se pueden reservar piezas concretas.

---

### 1.3 Convenciones

En “Hacia dónde apunta”, columna **Relación**: `N:1` = muchas filas de esta tabla a una del destino; `N:0..1` = destino opcional (FK nullable); `1:1` = a lo sumo un par (p. ej. UNIQUE); `N:N` se arma con tabla puente (cada FK del puente sigue siendo `N:1`).

---

#### 1.3.1 `cat_instrument_category`


| Campo  | Tipo         | Nulo | Default             | Significado                |
| ------ | ------------ | ---- | ------------------- | -------------------------- |
| `id`   | UUID         | NO   | `gen_random_uuid()` | PK.                        |
| `code` | VARCHAR(64)  | NO   | —                   | Ej. `suturing`, `cutting`. |
| `name` | VARCHAR(120) | NO   | —                   | Etiqueta.                  |


FKs: ninguna. UNIQUE `code`.

```json
{
  "id": "b1000001-0000-4000-8000-000000000001",
  "code": "suturing",
  "name": "Sutura y cierre"
}
```

---

#### 1.3.2 `cat_usage_context`


| Campo  | Tipo         | Nulo | Default             | Significado                  |
| ------ | ------------ | ---- | ------------------- | ---------------------------- |
| `id`   | UUID         | NO   | `gen_random_uuid()` | PK.                          |
| `code` | VARCHAR(64)  | NO   | —                   | Contexto de uso documentado. |
| `name` | VARCHAR(200) | NO   | —                   | Etiqueta.                    |


FKs: ninguna. UNIQUE `code`.

```json
{
  "id": "b1000002-0000-4000-8000-000000000002",
  "code": "primary_tray",
  "name": "Charola primaria"
}
```

---

#### 1.3.3 `cat_instrument_cycle_status`


| Campo  | Tipo        | Nulo | Default             | Significado                                    |
| ------ | ----------- | ---- | ------------------- | ---------------------------------------------- |
| `id`   | UUID        | NO   | `gen_random_uuid()` | PK.                                            |
| `code` | VARCHAR(32) | NO   | —                   | Ej. `clean`, `in_use`, `dirty`, `sterilizing`. |
| `name` | VARCHAR(80) | NO   | —                   | Etiqueta.                                      |


FKs: ninguna. UNIQUE `code`.

```json
{
  "id": "b1000003-0000-4000-8000-000000000003",
  "code": "clean",
  "name": "Limpio / listo"
}
```

---

#### 1.3.4 `cat_operation_phase`

**Dueño canónico aquí** (también lo usa `work_session` en DS04).


| Campo    | Tipo        | Nulo | Default             | Significado                           |
| -------- | ----------- | ---- | ------------------- | ------------------------------------- |
| `id`     | UUID        | NO   | `gen_random_uuid()` | PK.                                   |
| `code`   | VARCHAR(32) | NO   | —                   | Ej. `pre_count`, `intra`, `closing`.  |
| `name`   | VARCHAR(80) | NO   | —                   | Etiqueta.                             |
| `active` | BOOLEAN     | NO   | `TRUE`              | Fase usable en nuevos procedimientos. |


FKs: ninguna. UNIQUE `code`.

```json
{
  "id": "b1000004-0000-4000-8000-000000000004",
  "code": "pre_count",
  "name": "Conteo previo",
  "active": true
}
```

---

#### 1.3.5 Tabla `instrument_family`

**Para qué:** tipo de instrumento (vocabulario de detección y de kits).


| Campo           | Tipo         | Nulo | Default             | Significado                                 |
| --------------- | ------------ | ---- | ------------------- | ------------------------------------------- |
| `id`            | UUID         | NO   | `gen_random_uuid()` | PK.                                         |
| `code`          | VARCHAR(64)  | NO   | —                   | Código único global de familia.             |
| `name`          | VARCHAR(160) | NO   | —                   | Nombre legible.                             |
| `category_id`   | UUID         | NO   | —                   | Categoría.                                  |
| `identify_text` | TEXT         | SÍ   | NULL                | Texto de identificación (entrenamiento/UI). |
| `classify_text` | TEXT         | SÍ   | NULL                | Texto de clasificación.                     |
| `function_text` | TEXT         | SÍ   | NULL                | Función clínica del tipo.                   |
| `active`        | BOOLEAN      | NO   | `TRUE`              | Familia usable.                             |


**Hacia dónde apunta**


| Campo         | Apunta a                     | Relación | Por qué          | ON DELETE  |
| ------------- | ---------------------------- | -------- | ---------------- | ---------- |
| `category_id` | `cat_instrument_category.id` | N:1      | Agrupa familias. | `RESTRICT` |


UNIQUE `code`.

```json
{
  "id": "f1000001-0000-4000-8000-000000000001",
  "code": "mayo_hegar_needle_holder",
  "name": "Portaagujas Mayo-Hegar",
  "category_id": "b1000001-0000-4000-8000-000000000001",
  "identify_text": "Portaagujas con cremallera y anillos; punta roma.",
  "classify_text": "Needle holder Mayo-Hegar vs Olsen-Hegar.",
  "function_text": "Sujetar la aguja durante la sutura.",
  "active": true
}
```

---

#### 1.3.6 Tabla `family_example`

**Para qué:** ejemplos (texto y/o imagen) ligados a una familia, para uso de este sistema en laboratorio/educación.


| Campo            | Tipo     | Nulo | Default             | Significado                        |
| ---------------- | -------- | ---- | ------------------- | ---------------------------------- |
| `id`             | UUID     | NO   | `gen_random_uuid()` | PK.                                |
| `family_id`      | UUID     | NO   | —                   | Familia.                           |
| `media_asset_id` | UUID     | SÍ   | NULL                | Imagen de ejemplo (bytes en DS05). |
| `example_text`   | TEXT     | SÍ   | NULL                | Nota o caption.                    |
| `sort_order`     | SMALLINT | NO   | `0`                 | Orden de presentación.             |


**Hacia dónde apunta**


| Campo            | Apunta a                | Relación | Por qué                                   | ON DELETE  |
| ---------------- | ----------------------- | -------- | ----------------------------------------- | ---------- |
| `family_id`      | `instrument_family.id`  | N:1      | Dueño del ejemplo.                        | `CASCADE`  |
| `media_asset_id` | `media_asset.id` (DS05) | N:0..1   | Foto de referencia; no guarda bytes aquí. | `SET NULL` |


```json
{
  "id": "f1000002-0000-4000-8000-000000000002",
  "family_id": "f1000001-0000-4000-8000-000000000001",
  "media_asset_id": "m1000001-0000-4000-8000-000000000001",
  "example_text": "Vista cenital limpia, mango hacia abajo.",
  "sort_order": 1
}
```

---

#### 1.3.7 Tabla `instrument`

**Para qué:** pieza física = unidad de stock.


| Campo             | Tipo        | Nulo | Default             | Significado                                             |
| ----------------- | ----------- | ---- | ------------------- | ------------------------------------------------------- |
| `id`              | UUID        | NO   | `gen_random_uuid()` | PK.                                                     |
| `internal_code`   | VARCHAR(64) | SÍ   | NULL                | Código interno de la institución (etiqueta/inventario). |
| `family_id`       | UUID        | NO   | —                   | Tipo al que pertenece.                                  |
| `cycle_status_id` | UUID        | NO   | —                   | Estado de ciclo **actual**.                             |
| `institution_id`  | UUID        | NO   | —                   | Dueño del stock.                                        |
| `active`          | BOOLEAN     | NO   | `TRUE`              | Pieza en inventario usable.                             |
| `created_at`      | TIMESTAMPTZ | NO   | `now()`             | Alta.                                                   |
| `updated_at`      | TIMESTAMPTZ | NO   | `now()`             | Última actualización.                                   |


**Hacia dónde apunta**


| Campo             | Apunta a                         | Relación | Por qué                                           | ON DELETE  |
| ----------------- | -------------------------------- | -------- | ------------------------------------------------- | ---------- |
| `family_id`       | `instrument_family.id`           | N:1      | Clasifica la pieza.                               | `RESTRICT` |
| `cycle_status_id` | `cat_instrument_cycle_status.id` | N:1      | Estado actual denormalizado para consulta rápida. | `RESTRICT` |
| `institution_id`  | `institution.id` (DS01)          | N:1      | Stock por tenant.                                 | `RESTRICT` |


UNIQUE parcial (`institution_id`, `internal_code`) WHERE `internal_code IS NOT NULL`.

```json
{
  "id": "i1000001-0000-4000-8000-000000000001",
  "internal_code": "NH-MH-014",
  "family_id": "f1000001-0000-4000-8000-000000000001",
  "cycle_status_id": "b1000003-0000-4000-8000-000000000003",
  "institution_id": "11111111-1111-1111-1111-111111111111",
  "active": true,
  "created_at": "2026-02-01T09:00:00Z",
  "updated_at": "2026-09-05T08:00:00Z"
}
```

---

#### 1.3.8 Tabla `instrument_usage`

**Para qué:** documenta en qué procedimiento/contexto se usa una pieza (catálogo operativo, no el conteo en vivo).


| Campo               | Tipo    | Nulo | Default             | Significado                   |
| ------------------- | ------- | ---- | ------------------- | ----------------------------- |
| `id`                | UUID    | NO   | `gen_random_uuid()` | PK.                           |
| `instrument_id`     | UUID    | NO   | —                   | Pieza.                        |
| `procedure_type_id` | UUID    | NO   | —                   | Tipo de procedimiento (DS02). |
| `context_id`        | UUID    | NO   | —                   | Contexto de uso.              |
| `notes`             | TEXT    | SÍ   | NULL                | Notas.                        |
| `active`            | BOOLEAN | NO   | `TRUE`              | Uso vigente.                  |


**Hacia dónde apunta**


| Campo               | Apunta a                | Relación | Por qué            | ON DELETE  |
| ------------------- | ----------------------- | -------- | ------------------ | ---------- |
| `instrument_id`     | `instrument.id`         | N:1      | Pieza documentada. | `CASCADE`  |
| `procedure_type_id` | `cat_procedure_type.id` | N:1      | Procedimiento.     | `RESTRICT` |
| `context_id`        | `cat_usage_context.id`  | N:1      | Contexto.          | `RESTRICT` |


UNIQUE (`instrument_id`, `procedure_type_id`, `context_id`).

```json
{
  "id": "i1000002-0000-4000-8000-000000000002",
  "instrument_id": "i1000001-0000-4000-8000-000000000001",
  "procedure_type_id": "a1000003-0000-4000-8000-000000000003",
  "context_id": "b1000002-0000-4000-8000-000000000002",
  "notes": "Preferido en charola primaria de hernia inguinal.",
  "active": true
}
```

---

#### 1.3.9 Tabla `instrument_cycle_event`

**Para qué:** historial de cambios de ciclo (hechos temporales). El estado actual sigue en `instrument.cycle_status_id`.


| Campo             | Tipo        | Nulo | Default             | Significado                               |
| ----------------- | ----------- | ---- | ------------------- | ----------------------------------------- |
| `id`              | UUID        | NO   | `gen_random_uuid()` | PK.                                       |
| `instrument_id`   | UUID        | NO   | —                   | Pieza.                                    |
| `cycle_status_id` | UUID        | NO   | —                   | Estado **en ese momento**.                |
| `occurred_at`     | TIMESTAMPTZ | NO   | `now()`             | Cuándo ocurrió el cambio.                 |
| `session_id`      | UUID        | SÍ   | NULL                | Sesión de conteo relacionada (si aplica). |
| `operation_id`    | UUID        | SÍ   | NULL                | Operación relacionada (si aplica).        |
| `notes`           | TEXT        | SÍ   | NULL                | Nota libre.                               |


**Hacia dónde apunta**


| Campo             | Apunta a                         | Relación | Por qué                                                   | ON DELETE                            |
| ----------------- | -------------------------------- | -------- | --------------------------------------------------------- | ------------------------------------ |
| `instrument_id`   | `instrument.id`                  | N:1      | Pieza.                                                    | `CASCADE`                            |
| `cycle_status_id` | `cat_instrument_cycle_status.id` | N:1      | Estado registrado.                                        | `RESTRICT`                           |
| `operation_id`    | `operation.id` (DS02)            | N:0..1   | Contexto clínico opcional.                                | `SET NULL`                           |
| `session_id`      | `work_session.id` (DS04)         | N:0..1   | Contexto de conteo opcional; FK añadida al final del DDL. | `SET NULL` (según ALTER del esquema) |


```json
{
  "id": "i1000003-0000-4000-8000-000000000003",
  "instrument_id": "i1000001-0000-4000-8000-000000000001",
  "cycle_status_id": "b1000003-0000-4000-8000-000000000003",
  "occurred_at": "2026-09-05T08:00:00Z",
  "session_id": null,
  "operation_id": null,
  "notes": "Salida de esterilización; marcado limpio."
}
```

---

#### 1.3.10 Tabla `kit`

**Para qué:** receta versionada por institución.


| Campo            | Tipo         | Nulo | Default             | Significado     |
| ---------------- | ------------ | ---- | ------------------- | --------------- |
| `id`             | UUID         | NO   | `gen_random_uuid()` | PK.             |
| `name`           | VARCHAR(160) | NO   | —                   | Nombre del kit. |
| `version`        | SMALLINT     | NO   | `1`                 | Versión (> 0).  |
| `active`         | BOOLEAN      | NO   | `TRUE`              | Kit usable.     |
| `institution_id` | UUID         | NO   | —                   | Tenant.         |
| `created_at`     | TIMESTAMPTZ  | NO   | `now()`             | Alta.           |
| `updated_at`     | TIMESTAMPTZ  | NO   | `now()`             | Última edición. |


**Hacia dónde apunta**


| Campo            | Apunta a         | Relación | Por qué            | ON DELETE |
| ---------------- | ---------------- | -------- | ------------------ | --------- |
| `institution_id` | `institution.id` | N:1      | Kits por hospital. | `CASCADE` |


UNIQUE (`institution_id`, `name`, `version`). CHECK `version > 0`.

```json
{
  "id": "k1000001-0000-4000-8000-000000000001",
  "name": "Kit hernia inguinal abierta",
  "version": 1,
  "active": true,
  "institution_id": "11111111-1111-1111-1111-111111111111",
  "created_at": "2026-04-01T10:00:00Z",
  "updated_at": "2026-04-01T10:00:00Z"
}
```

---

#### 1.3.11 Tabla `kit_item`

**Para qué:** línea del kit = familia + cantidad.


| Campo       | Tipo     | Nulo | Default             | Significado              |
| ----------- | -------- | ---- | ------------------- | ------------------------ |
| `id`        | UUID     | NO   | `gen_random_uuid()` | PK.                      |
| `kit_id`    | UUID     | NO   | —                   | Kit.                     |
| `family_id` | UUID     | NO   | —                   | Familia pedida.          |
| `quantity`  | SMALLINT | NO   | —                   | Cantidad esperada (> 0). |


**Hacia dónde apunta**


| Campo       | Apunta a               | Relación | Por qué            | ON DELETE  |
| ----------- | ---------------------- | -------- | ------------------ | ---------- |
| `kit_id`    | `kit.id`               | N:1      | Dueño de la línea. | `CASCADE`  |
| `family_id` | `instrument_family.id` | N:1      | Qué tipo se pide.  | `RESTRICT` |


UNIQUE (`kit_id`, `family_id`). CHECK `quantity > 0`.

```json
{
  "id": "k1000002-0000-4000-8000-000000000002",
  "kit_id": "k1000001-0000-4000-8000-000000000001",
  "family_id": "f1000001-0000-4000-8000-000000000001",
  "quantity": 2
}
```

---

#### 1.3.12 Tabla `procedure_kit`

**Para qué:** qué kits aplican a un tipo de procedimiento.


| Campo               | Tipo         | Nulo | Default             | Significado                                    |
| ------------------- | ------------ | ---- | ------------------- | ---------------------------------------------- |
| `id`                | UUID         | NO   | `gen_random_uuid()` | PK.                                            |
| `procedure_type_id` | UUID         | NO   | —                   | Procedimiento (DS02).                          |
| `kit_id`            | UUID         | NO   | —                   | Kit.                                           |
| `technique_label`   | VARCHAR(160) | SÍ   | NULL                | Etiqueta de técnica/variante.                  |
| `is_default`        | BOOLEAN      | NO   | `FALSE`             | Si es el kit por defecto de ese procedimiento. |
| `active`            | BOOLEAN      | NO   | `TRUE`              | Relación vigente.                              |
| `created_at`        | TIMESTAMPTZ  | NO   | `now()`             | Alta.                                          |
| `updated_at`        | TIMESTAMPTZ  | NO   | `now()`             | Edición.                                       |


**Hacia dónde apunta**


| Campo               | Apunta a                | Relación                                  | Por qué        | ON DELETE  |
| ------------------- | ----------------------- | ----------------------------------------- | -------------- | ---------- |
| `procedure_type_id` | `cat_procedure_type.id` | N:1 (procedure↔kit es N:N vía esta tabla) | Procedimiento. | `CASCADE`  |
| `kit_id`            | `kit.id`                | N:1 (procedure↔kit es N:N vía esta tabla) | Receta.        | `RESTRICT` |


UNIQUE (`procedure_type_id`, `kit_id`).  
UNIQUE parcial: un solo default activo por `procedure_type_id`.

```json
{
  "id": "k1000003-0000-4000-8000-000000000003",
  "procedure_type_id": "a1000003-0000-4000-8000-000000000003",
  "kit_id": "k1000001-0000-4000-8000-000000000001",
  "technique_label": "Técnica abierta estándar",
  "is_default": true,
  "active": true,
  "created_at": "2026-04-01T10:05:00Z",
  "updated_at": "2026-04-01T10:05:00Z"
}
```

---

#### 1.3.13 Tabla `procedure_phase`

**Para qué:** fases ordenadas de un procedimiento y si exigen conteo.


| Campo               | Tipo     | Nulo | Default             | Significado                   |
| ------------------- | -------- | ---- | ------------------- | ----------------------------- |
| `id`                | UUID     | NO   | `gen_random_uuid()` | PK.                           |
| `procedure_type_id` | UUID     | NO   | —                   | Procedimiento.                |
| `phase_id`          | UUID     | NO   | —                   | Fase del catálogo.            |
| `sort_order`        | SMALLINT | NO   | —                   | Orden (> 0).                  |
| `is_count_required` | BOOLEAN  | NO   | `TRUE`              | Si en esa fase debe contarse. |
| `active`            | BOOLEAN  | NO   | `TRUE`              | Vigente.                      |


**Hacia dónde apunta**


| Campo               | Apunta a                 | Relación | Por qué          | ON DELETE  |
| ------------------- | ------------------------ | -------- | ---------------- | ---------- |
| `procedure_type_id` | `cat_procedure_type.id`  | N:1      | Dueño del flujo. | `CASCADE`  |
| `phase_id`          | `cat_operation_phase.id` | N:1      | Fase clínica.    | `RESTRICT` |


UNIQUE (`procedure_type_id`, `phase_id`); UNIQUE (`procedure_type_id`, `sort_order`).

```json
{
  "id": "k1000004-0000-4000-8000-000000000004",
  "procedure_type_id": "a1000003-0000-4000-8000-000000000003",
  "phase_id": "b1000004-0000-4000-8000-000000000004",
  "sort_order": 1,
  "is_count_required": true,
  "active": true
}
```

---

#### 1.3.14 Tabla `instrument_reservation`

**Para qué:** pieza reservada a una operación (una reserva activa por pieza).


| Campo           | Tipo        | Nulo | Default             | Significado        |
| --------------- | ----------- | ---- | ------------------- | ------------------ |
| `id`            | UUID        | NO   | `gen_random_uuid()` | PK.                |
| `operation_id`  | UUID        | NO   | —                   | Operación.         |
| `instrument_id` | UUID        | NO   | —                   | Pieza.             |
| `reserved_at`   | TIMESTAMPTZ | NO   | `now()`             | Inicio de reserva. |
| `released_at`   | TIMESTAMPTZ | SÍ   | NULL                | Liberación.        |
| `active`        | BOOLEAN     | NO   | `TRUE`              | Reserva vigente.   |


**Hacia dónde apunta**


| Campo           | Apunta a              | Relación                                                                               | Por qué                    | ON DELETE  |
| --------------- | --------------------- | -------------------------------------------------------------------------------------- | -------------------------- | ---------- |
| `operation_id`  | `operation.id` (DS02) | N:1                                                                                    | Caso que retiene la pieza. | `CASCADE`  |
| `instrument_id` | `instrument.id`       | N:1 (con UNIQUE parcial activo: a lo sumo 1 reserva activa por pieza ≈ 0..1:1 vigente) | Pieza retenida.            | `RESTRICT` |


CHECK: `released_at` ≥ `reserved_at` si ambas existen.  
UNIQUE parcial: `instrument_id` WHERE `active = TRUE`.

```json
{
  "id": "r1000001-0000-4000-8000-000000000001",
  "operation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "instrument_id": "i1000001-0000-4000-8000-000000000001",
  "reserved_at": "2026-09-05T15:50:00Z",
  "released_at": null,
  "active": true
}
```

---

#### 1.3.15 Tabla `yolo_model`

**Para qué:** registro de versión del detector.


| Campo            | Tipo        | Nulo | Default             | Significado                          |
| ---------------- | ----------- | ---- | ------------------- | ------------------------------------ |
| `id`             | UUID        | NO   | `gen_random_uuid()` | PK.                                  |
| `version_tag`    | VARCHAR(32) | NO   | —                   | Etiqueta única de versión.           |
| `media_asset_id` | UUID        | SÍ   | NULL                | Archivo de pesos (DS05).             |
| `checksum`       | CHAR(64)    | SÍ   | NULL                | Hash del artefacto (p. ej. SHA-256). |
| `active`         | BOOLEAN     | NO   | `FALSE`             | Si es el modelo en producción.       |
| `published_at`   | TIMESTAMPTZ | NO   | `now()`             | Publicación.                         |


**Hacia dónde apunta**


| Campo            | Apunta a                | Relación | Por qué                  | ON DELETE  |
| ---------------- | ----------------------- | -------- | ------------------------ | ---------- |
| `media_asset_id` | `media_asset.id` (DS05) | N:0..1   | Bytes del `.pt`/weights. | `SET NULL` |


UNIQUE `version_tag`. UNIQUE parcial: un solo `active = TRUE`.

```json
{
  "id": "y1000001-0000-4000-8000-000000000001",
  "version_tag": "yolo26n-pef-2026-03",
  "media_asset_id": "m1000002-0000-4000-8000-000000000002",
  "checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "active": true,
  "published_at": "2026-03-20T18:00:00Z"
}
```

---

#### 1.3.16 Tabla `model_class`

**Para qué:** mapa clase YOLO → familia para una versión de modelo.


| Campo           | Tipo     | Nulo | Default             | Significado                       |
| --------------- | -------- | ---- | ------------------- | --------------------------------- |
| `id`            | UUID     | NO   | `gen_random_uuid()` | PK.                               |
| `model_id`      | UUID     | NO   | —                   | Modelo.                           |
| `family_id`     | UUID     | NO   | —                   | Familia detectada.                |
| `yolo_class_id` | SMALLINT | NO   | —                   | Entero de clase del modelo (≥ 0). |


**Hacia dónde apunta**


| Campo       | Apunta a               | Relación | Por qué               | ON DELETE  |
| ----------- | ---------------------- | -------- | --------------------- | ---------- |
| `model_id`  | `yolo_model.id`        | N:1      | Versión del detector. | `CASCADE`  |
| `family_id` | `instrument_family.id` | N:1      | Tipo de instrumento.  | `RESTRICT` |


UNIQUE (`model_id`, `yolo_class_id`); UNIQUE (`model_id`, `family_id`).

```json
{
  "id": "y1000002-0000-4000-8000-000000000002",
  "model_id": "y1000001-0000-4000-8000-000000000001",
  "family_id": "f1000001-0000-4000-8000-000000000001",
  "yolo_class_id": 0
}
```

---

### 1.4 Governance and Ownership


| Aspecto               | Responsable                                |
| --------------------- | ------------------------------------------ |
| Familias y kits       | PEF Core / estandarización de instrumental |
| Publicación de modelo | Visión / MLOps                             |
| Reservas              | Flujo de quirófano (portal/estación)       |


Cambiar clases de un modelo ya usado = publicar **otra** versión, no reescribir historia.

---

### 1.5 Justificación del estándar

**Por qué este bloque**

Hay que separar tres ideas que la gente mezcla: 1 tipo de instrumento, 2 pieza física con ciclo, 3 número de clase que escupió YOLO. Sin eso no se audita si “faltó un tipo” o “faltó la pieza NH-MH-014”, ni con qué modelo se contó.

**Decisiones tomadas**

1. **Stock = filas** `instrument`, no tabla `family_stock`. El conteo esperado viene del kit (familias); el inventario real son piezas.
2. **Kit = receta por familia**, no lista de UUIDs. Reutilizable y versionable; la sesión congela el esperado en DS04.
3. **Estado de ciclo actual en la pieza + historial en** `instrument_cycle_event`**.** Consulta rápida sin perder traza.
4. **Un solo** `yolo_model` **activo** + `model_class` por versión. Evita ambigüedad en el worker.
5. **Reserva única activa por pieza.** Impide doble asignación.
6. **Pesos y fotos solo por** `media_asset_id`**.** Este DS no administra buckets.

**Alternativas no elegidas**


| Alternativa                               | Por qué no                                                      |
| ----------------------------------------- | --------------------------------------------------------------- |
| Contar stock solo como entero por familia | Pierde serialización, ciclo y reserva por pieza.                |
| Kit que lista piezas concretas            | Cada cirugía armaría un kit distinto; no hay plantilla estable. |
| Clases YOLO hardcodeadas en el worker     | Al cambiar el modelo se rompe producción sin registro.          |
| Varios modelos `active` a la vez          | La estación no sabría cuál usar sin otra capa de routing.       |
| Meter bounding boxes aquí                 | Eso es telemetría de visión (Mongo / DS06), no catálogo.        |
| `family_stock` + `instrument`             | Duplica la verdad; los totales se calculan contando piezas.     |


---

## 2. Anexo A – Información general


| Campo             | Valor                                                     |
| ----------------- | --------------------------------------------------------- |
| Código            | PEF-DS03                                                  |
| Título            | Instrument Kits and Model Registry                        |
| Versión           | 0.2.0                                                     |
| Estado            | draft                                                     |
| Almacén           | PostgreSQL (§5.6 + catálogos de instrumental/fase)        |
| Fuera de alcance  | `expected_inventory`, `count_event`, bytes object storage |
| DDL de referencia | `esquema_base_datos_v2.md`                                |


