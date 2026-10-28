---
id: PEF-DS05
title: Evidence and Object Storage
version: "0.2.1"
status: draft
owner: PEF Core / Evidence / Worker
applies_to: [BE, Worker, MS, WP]
stores: [PostgreSQL, ObjectStorage]
related: [PEF-DS03, PEF-DS04, PEF-DS06, PEF-DS07]
last_updated: 2026-10-14
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

1. Subir bytes al bucket con `object_key` generado por el servidor (prefijo `institution_id/session_id/...`).
2. INSERT `media_asset` con URI, kind, hash, retención y, para evidencia clínica, `session_id` (v0.2.0).
3. Quien necesite el archivo (Mongo checkpoint, `family_example`, `yolo_model`) guarda solo el UUID.

---

### 1.3 Convenciones

En “Hacia dónde apunta”: desde v0.2.0 la tabla tiene **una** FK saliente opcional, `session_id` → `work_session` (evidencia clínica). Otras tablas apuntan **hacia** ella (N:0..1 desde DS03).

Naming de clave: `{institution_id}/{session_id}/{yyyy}/{uuid}.{ext}` para evidencia clínica (`{models}/...` para MLOps). El prefijo **organiza** el storage; **no** es frontera de seguridad ni fuente de autorización (ver §1.3.2).

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
| `session_id` | UUID | SÍ | NULL | **v0.2.0.** Sesión de conteo dueña de la evidencia clínica. NULL para assets DS03/MLOps (pesos, fotos de familia). |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Alta de metadata. |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Última modificación de metadata. |

**Hacia dónde apunta**

| Campo | Apunta a | Relación | Por qué | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `session_id` | `work_session.id` (DS04) | N:0..1 | Sesión de la evidencia clínica; define su tenant. | `RESTRICT`: la evidencia no desaparece ni pierde su tenant en silencio; sale solo por el ciclo bloqueo/purga. |

**Quién apunta hacia aquí (referencia cruzada, no dueños de este DS)**

| Origen | Relación | Motivo |
| :--- | :--- | :--- |
| `family_example.media_asset_id` (DS03) | N:0..1 | Foto de ejemplo de familia. |
| `yolo_model.media_asset_id` (DS03) | N:0..1 | Archivo de pesos. |
| `checkpoint_frame.media_asset_id` (DS06, lógico) | N:0..1 | Evidencia del checkpoint. |

**Restricciones:** PK `id`; UNIQUE (`bucket`, `object_key`); CHECK `kind`; CHECK `storage_provider`; CHECK `size_bytes >= 0`; CHECK orden purga (`purged_at` ≥ `purge_requested_at` si ambas existen); FK `fk_media_asset_session` (v0.2.0).

**Índices:** parciales por `retention_until` y `purge_requested_at` (job de purga); `idx_media_asset_session_created` (`session_id`, `created_at`, `id`) WHERE `session_id IS NOT NULL` (lista por sesión, v0.2.0).

**Reglas de ciclo de vida**

1. Tras subir objeto → INSERT con `retention_until` = `created_at` + 90 días, o + 180 días si `work_session.extended_retention` (ver DS04/DS06/DS07).
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
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
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
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
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
  "session_id": null,
  "created_at": "2026-03-20T17:55:00Z",
  "updated_at": "2026-03-20T17:55:00Z"
}
```

---

#### 1.3.2 Evidencia clínica (v0.2.0)

Reglas para los assets que crea Evidence Service (contrato HTTP en PEF-DS09 §1.3.5).

1. **Sesión y tenant.** La evidencia clínica siempre tiene `session_id` (obligatorio a nivel de API aunque la columna sea nullable). `tenant(asset)` = `media_asset.session_id` → `work_session.operation_id` → `operation.institution_id`. No existe `institution_id` en la fila. Un asset con `session_id IS NULL` (DS03/MLOps) queda **fuera** de la API clínica v1. **Nunca** se autoriza por `object_key`, `bucket` ni `gcs_uri`.
2. **Clave del objeto.** Evidence genera siempre `object_key` = `{institution_id}/{session_id}/{yyyy}/{uuid}.{ext}` (`ext` según el tipo real: `jpg` / `gif`); el cliente nunca la proporciona, por lo que no hay nombres de archivo de cliente ni recorridos de ruta en la clave. El prefijo es organización del bucket, no seguridad.
3. **Proveedor y URI.** En el despliegue actual (Garage, API S3-compatible) `storage_provider = 'other'` (Garage no es MinIO; el CHECK no cambia) y `gcs_uri = s3://{bucket}/{object_key}` (nombre histórico de columna conservado).
4. **Privacidad.** Solo se crea evidencia clínica para sesiones con `capture_mode = vision` (decisión congelada en `start`, con acuerdo DS07 vigente en ese momento). `manual_no_privacy` o `capture_mode` nulo → no se guarda evidencia. No se revalida el aviso de privacidad en cada subida; los cambios posteriores (p. ej. ARCO) se aplican con `blocked_at` / `purge_requested_at` / `purged_at`.
5. **Estados de la sesión que admiten subida (v1):** `in_progress`, `correction_required`, `awaiting_spd_review` (evidencia producida al final del conteo puede llegar de forma asíncrona durante la revisión). `scheduled`, `closed` y `aborted` la rechazan.
6. **Tipos v1:** solo `kind = jpeg` con contenido real `image/jpeg` y `kind = gif` con contenido real `image/gif`, validados por los bytes (firma de archivo), no por extensión ni nombre. `weights` y `other` existen en la tabla pero quedan fuera de la API clínica v1 (MLOps).
7. **Tamaño máximo (API v1, configurable por entorno, no constraint de PG):** JPEG 2 MiB, GIF 5 MiB. La subida se procesa en streaming / archivo temporal; nunca se carga en memoria un blob arbitrariamente grande.
8. **Integridad.** Evidence calcula siempre en servidor `sha256` (64 hex en minúsculas) y `size_bytes`; el cliente no los aporta como autoridad.
9. **Retención.** Evidence calcula `retention_until` (regla de ciclo de vida 1); el cliente no la envía.
10. **Consistencia storage ↔ PostgreSQL (sin columna de estado):** (1) validar sesión, auth, privacidad y archivo; (2) generar UUID y `object_key`; (3) PUT del objeto; (4) verificar tamaño y `sha256`; (5) transacción PG: INSERT `media_asset` + INSERT `access_audit`; COMMIT. Si el INSERT, la auditoría o el COMMIT fallan → DELETE compensatorio del objeto; si también falla, se registra en log como candidato huérfano. El job de reconciliación (regla 14) compara los objetos del bucket con `media_asset (bucket, object_key)` y elimina huérfanos antiguos.
11. **URL firmada.** Solo en el detalle, válida 300 s (configurable). Nunca si `blocked_at` o `purged_at` no son nulos. Se firma directamente contra el endpoint **público** del storage (`S3_PUBLIC_ENDPOINT`); las operaciones internas (PUT/HEAD/DELETE) usan `S3_INTERNAL_ENDPOINT`. No se reescribe el host de una URL ya firmada.
12. **Purga / retención (v0.2.1).** Job interno idempotente, sin endpoint público (no hay DELETE). Candidatas: filas clínicas (`session_id` no nulo) del bucket de evidencia con `purged_at` nulo y (`purge_requested_at` no nulo, o `retention_until` ≤ ahora). `blocked_at` por sí solo **no** purga (bloquear ≠ purgar). Purgar = borrar el objeto del storage y fijar `purged_at`; la fila **nunca** se borra y conserva el resto de columnas (trazabilidad). Cada asset se decide y borra bajo el bloqueo de su fila (`FOR UPDATE SKIP LOCKED`, recomprobando que sigue siendo candidata); borrar un objeto inexistente cuenta como éxito, de modo que si el objeto se borró pero `purged_at` no llegó a guardarse, la siguiente ejecución lo completa (consistencia eventual; el objeto no se restaura). Si el borrado en storage falla, `purged_at` queda nulo y el asset sigue siendo candidata. Assets DS03/MLOps (`session_id` nulo) quedan fuera (regla 4 de ciclo de vida).
13. **Auditoría** (`access_audit`, `resource_type = media_asset`, hasta Audit Service): `evidence.upload`, `evidence.download_url` (emisión de URL firmada), `evidence.block`, `evidence.purge`. No se audita la lectura de metadata de la lista. **Gap (v0.2.1):** `access_audit` solo admite actor `user` (con `actor_user_id`) o `client` (con `actor_client_id` → `integration_client`, DS07/DS08); los jobs internos no son ninguno de los dos, así que `evidence.purge` (y las acciones del job de reconciliación) **no** se escriben —no se finge un usuario ni un cliente— y el registro es el log técnico con el id de ejecución, hasta que exista una identidad máquina DS08 para los jobs.
14. **Reconciliación (v0.2.1).** Job interno: recorre el listado paginado del bucket y las filas `media_asset` del bucket. (a) **Objeto sin fila:** solo las claves con el formato clínico exacto de la regla 2 son candidatas (cualquier otra clave, p. ej. `models/…`, se informa y nunca se borra); un objeto más joven que el margen operativo `EVIDENCE_ORPHAN_GRACE_SECONDS` (edad por `LastModified`; protege subidas en curso entre el PUT y el INSERT) se ignora; las candidatas se borran solo con una orden explícita, tras volver a consultar PostgreSQL por (bucket, clave) justo antes del borrado; sin `access_audit` (no hay recurso clínico). (b) **Fila no purgada sin objeto:** se confirma (HeadObject + relectura) y solo se **informa**; no se modifica la metadata, no se fija `purged_at`, no se borra la fila ni se recrea el objeto. Una fila purgada sin objeto es correcta. El tamaño de lote y el margen son configuración operativa, no política clínica.

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
| Versión | 0.2.1 (0.2.0: `media_asset.session_id` + reglas de evidencia clínica §1.3.2; 0.2.1: semántica de los jobs de purga (regla 12) y reconciliación (regla 14), gap de auditoría de jobs (regla 13)) |
| Estado | draft |
| Almacenes | PostgreSQL (`media_asset`); Object storage (GCS/MinIO) |
| Fuera de alcance | Documento Mongo; política legal ARCO (orquestación en DS07); contrato HTTP (DS09) |
| DDL de referencia | `esquema_base_datos_v2.md` §5.5 + `data/migrations/004_media_asset_session.sql` (`session_id`) |
