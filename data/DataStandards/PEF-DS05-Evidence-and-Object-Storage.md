---
id: PEF-DS05
title: Evidence and Object Storage
version: "0.1.0"
status: draft
owner: PEF Core / Evidence / Worker
applies_to: [BE, Worker, MS, WP]
stores: [PostgreSQL, ObjectStorage]
related: [PEF-DS03, PEF-DS04, PEF-DS06, PEF-DS07]
last_updated: 2026-09-06
---

# PEF-DS05 · Evidence and Object Storage

## INTRODUCCION

Este estándar define **dónde viven los bytes** (JPEG, GIF, pesos del modelo) y **cómo se registran en PostgreSQL** sin meter el archivo dentro de la base relacional.

Tabla dueña: `media_asset`. El object storage (GCS en nube o MinIO on-prem con API S3-compatible) guarda el objeto; PG guarda metadata, URI y ciclo de purga.

No define el documento Mongo del checkpoint (PEF-DS06): ese documento **apunta** a `media_asset.id`. No define familias ni YOLO registry (PEF-DS03): solo el puntero desde esas tablas. No define el flujo ARCO completo (PEF-DS07), aunque reutiliza `blocked_at` / `purge_*`.

---

## 1. Explicación del estándar

### 1.1 Definiciones

| Término | Significado |
| :--- | :--- |
| **media_asset** | Fila de metadata de un objeto en storage. |
| **Object storage** | Bucket de objetos direccionables por clave (`bucket` + `object_key`). |
| **URI canónica** | Cadena en `gcs_uri` (nombre histórico; también vale para MinIO/S3). |
| **Purga** | Borrar el objeto del bucket y marcar `purged_at` en PG. |
| **Bloqueo** | `blocked_at` marcado (p. ej. ARCO); no se sirve el objeto aunque aún exista. |

### 1.2 Uso

**Quién escribe:** Worker (JPEG/GIF de evidencia), jobs MLOps (pesos), Backend/Evidence Service (altas autorizadas).  
**Quién lee:** Portal (URL firmada), MS Evidence, panel de supervisión.

**Flujo típico**

1. Subir bytes al bucket con `object_key` prefijado (`institution_id/session_id/...`).
2. INSERT `media_asset` con URI, kind, hash, retención.
3. Quien necesite el archivo (Mongo checkpoint, `family_example`, `yolo_model`) guarda solo el UUID.

---

### 1.3 Convenciones

En “Hacia dónde apunta”: esta tabla **no tiene FKs salientes**. Otras tablas apuntan **hacia** ella (N:0..1 desde DS03).

Naming de clave sugerido: `{institution_id}/{session_id|models}/{yyyy}/{uuid}.{ext}`.

---

#### 1.3.1 Tabla `media_asset`

**Para qué existe:** registro único de un objeto binario accesible por el sistema.

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK; es lo que referencian otras tablas/Mongo. |
| `gcs_uri` | TEXT | NO | — | URI completa de acceso lógico al objeto (gs://, s3://, o URL canónica interna). |
| `bucket` | VARCHAR(128) | NO | — | Nombre del bucket. |
| `object_key` | TEXT | NO | — | Clave dentro del bucket. |
| `content_type` | VARCHAR(128) | SÍ | NULL | MIME (`image/jpeg`, `image/gif`, `application/octet-stream`, …). |
| `kind` | VARCHAR(32) | NO | `'jpeg'` | Clase de asset: `jpeg`, `gif`, `weights`, `other`. |
| `storage_provider` | VARCHAR(32) | NO | `'gcs'` | `gcs`, `minio`, `other`. |
| `sha256` | CHAR(64) | SÍ | NULL | Hash del contenido (hex). |
| `size_bytes` | BIGINT | SÍ | NULL | Tamaño en bytes (≥ 0 si informado). |
| `retention_until` | TIMESTAMPTZ | SÍ | NULL | Hasta cuándo debe retenerse (job de TTL). |
| `purge_requested_at` | TIMESTAMPTZ | SÍ | NULL | Cuándo se pidió borrar (TTL o ARCO). |
| `purged_at` | TIMESTAMPTZ | SÍ | NULL | Cuándo se borró efectivamente del storage. |
| `blocked_at` | TIMESTAMPTZ | SÍ | NULL | Bloqueo de servicio (no entregar URL firmada). |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Alta de metadata. |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Última modificación de metadata. |

**Hacia dónde apunta**

| Campo | Apunta a | Relación | Por qué | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| — | — | — | Sin FK salientes; es hoja de metadata. | — |

**Quién apunta hacia aquí (referencia cruzada, no dueños de este DS)**

| Origen | Relación | Motivo |
| :--- | :--- | :--- |
| `family_example.media_asset_id` (DS03) | N:0..1 | Foto de ejemplo de familia. |
| `yolo_model.media_asset_id` (DS03) | N:0..1 | Archivo de pesos. |
| `checkpoint_frame.media_asset_id` (DS06, lógico) | N:0..1 | Evidencia del checkpoint. |

**Restricciones:** PK `id`; UNIQUE (`bucket`, `object_key`); CHECK `kind`; CHECK `storage_provider`; CHECK `size_bytes >= 0`; CHECK orden purga (`purged_at` ≥ `purge_requested_at` si ambas existen).

**Reglas de ciclo de vida**

1. Tras subir objeto → INSERT con `retention_until` (90 días base o 180 si la sesión tiene retención extendida; ver DS04/DS07).
2. Job idempotente: si `purge_requested_at` y aún no `purged_at` → borrar en storage → `purged_at = now()`.
3. Si `blocked_at` no es nulo → no emitir URL firmada aunque el objeto siga en el bucket.
4. Pesos (`kind = weights`) pueden tener retención distinta (política MLOps); no se purgan con el mismo TTL clínico sin revisión.

**Ejemplo de fila completa (JPEG de checkpoint)**

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

**Ejemplo de fila completa (GIF de ráfaga)**

```json
{
  "id": "m1000003-0000-4000-8000-000000000003",
  "gcs_uri": "s3://pef-minio-evidence/11111111-1111-1111-1111-111111111111/cccccccc-cccc-cccc-cccc-cccccccccccc/2026/09/burst-disc.gif",
  "bucket": "pef-minio-evidence",
  "object_key": "11111111-1111-1111-1111-111111111111/cccccccc-cccc-cccc-cccc-cccccccccccc/2026/09/burst-disc.gif",
  "content_type": "image/gif",
  "kind": "gif",
  "storage_provider": "minio",
  "sha256": "b1c2d3e4f5061728394a5b6c7d8e9f00112233445566778899aabbccddeeff00",
  "size_bytes": 1048576,
  "retention_until": "2027-03-04T18:25:00Z",
  "purge_requested_at": null,
  "purged_at": null,
  "blocked_at": null,
  "created_at": "2026-09-05T18:25:10Z",
  "updated_at": "2026-09-05T18:25:10Z"
}
```

**Ejemplo de fila completa (pesos YOLO)**

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

| Aspecto | Responsable |
| :--- | :--- |
| Alta de evidencia clínica | Worker + Backend / Evidence Service |
| Publicación de pesos | Visión / MLOps |
| Job de purga | Ops / Backend (idempotente) |
| Bloqueo ARCO | Cumplimiento vía DS07 |

---

### 1.5 Justificación del estándar

**Por qué existe**

Los bytes no caben ni deben vivir en PostgreSQL ni en Mongo como BLOB masivo de producción. PG necesita saber *qué* archivo existe y *hasta cuándo*; el bucket guarda el contenido.

**Decisiones tomadas**

1. **Una tabla `media_asset` para JPEG, GIF y weights** con `kind` discriminador.
2. **`storage_provider`** para portar GCS ↔ MinIO sin cambiar el modelo.
3. **Campos de purga/bloqueo en la misma fila** para que el job ARCO/TTL sea único.
4. **Nombre de columna `gcs_uri` conservado** aunque el provider sea MinIO (legado de diseño; el valor es la URI canónica del objeto).

**Alternativas no elegidas**

| Alternativa | Por qué no |
| :--- | :--- |
| Guardar bytes en PG (`BYTEA`) | Infla backups, empeora réplicas, complica CDN/URLs firmadas. |
| Solo path en Mongo sin fila PG | El portal y FHIR Attachment necesitan metadata consultable en SQL; ARCO multi-almacén se complica. |
| Tabla por tipo (`jpeg_asset`, `gif_asset`) | Duplica ciclo de purga y FKs. |
| Borrar solo el objeto y dejar fila huérfana sin `purged_at` | Mentira de inventario; el job debe cerrar el ciclo. |

---

## 2. Anexo A – Información general

| Campo | Valor |
| :--- | :--- |
| Código | PEF-DS05 |
| Título | Evidence and Object Storage |
| Versión | 0.1.0 |
| Estado | draft |
| Almacenes | PostgreSQL (`media_asset`); Object storage (GCS/MinIO) |
| Fuera de alcance | Documento Mongo; política legal ARCO (orquestación en DS07); contrato HTTP (DS09) |
| DDL de referencia | `esquema_base_datos_v2.md` §5.5 |
