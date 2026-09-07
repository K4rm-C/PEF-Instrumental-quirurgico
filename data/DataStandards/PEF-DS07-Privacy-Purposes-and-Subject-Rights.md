---
id: PEF-DS07
title: Privacy Purposes and Subject Rights
version: "0.1.0"
status: draft
owner: PEF Core / Cumplimiento / Backend Web
applies_to: [WP, BE]
stores: [PostgreSQL]
related: [PEF-DS01, PEF-DS04, PEF-DS05, PEF-DS08]
last_updated: 2026-09-06
---

# PEF-DS07 · Privacy Purposes and Subject Rights

## INTRODUCCION

Este estándar define **finalidades de tratamiento**, **aviso de privacidad versionado**, **acuerdo por sesión de conteo**, **auditoría de acceso** a recursos sensibles y **solicitudes ARCO** (acceso, rectificación, cancelación, oposición).

Tablas dueñas: `privacy_notice_version`, `session_processing_agreement`, `access_audit`, `privacy_request`.

Catálogo dueño: `cat_processing_purpose` (vocabulario; el acuerdo usa booleanos, no FK a cada purpose).

No define `media_asset` (PEF-DS05) ni checkpoints (PEF-DS06): los orquesta vía flags de purga/bloqueo. No define login humano (PEF-DS01) ni cliente máquina (PEF-DS08), aunque `access_audit` puede registrar ambos actores.

---

## 1. Explicación del estándar

### 1.1 Definiciones

| Término | Significado |
| :--- | :--- |
| **Aviso de privacidad versionado** | Texto/URI del aviso con hash y vigencia. |
| **Acuerdo de sesión** | 0..1 por `work_session`: qué finalidades aplican al usar el conteo. |
| **quality_ops** | Finalidad siempre verdadera si hay acuerdo: operar y auditar el conteo. |
| **model_improvement** | Opt-in: permitir listar evidencia de esa sesión para entrenamiento offline. |
| **access_audit** | Log append-oriented de quién accedió a qué recurso (no es `count_event`). |
| **privacy_request** | Caso ARCO sobre un paciente sujeto. |

### 1.2 Uso

Al abrir/activar conteo con captura: materializar acuerdo. Al leer paciente/sesión/GIF: insertar `access_audit`. Al recibir ARCO: crear `privacy_request` y, si cancelación, bloquear/purgar multi-almacén.

**Cardinalidad clave:** `work_session` 0..1 `session_processing_agreement` (sesión puede existir sin acuerdo, p. ej. reserva previa).

---

### 1.3 Convenciones

---

#### 1.3.1 Catálogo `cat_processing_purpose`

Vocabulario de finalidades. El acuerdo **no** hace FK fila a fila: usa booleanos (`purpose_quality_ops`, `purpose_model_improvement`), igual patrón que reasons de checkpoint.

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `code` | VARCHAR(64) | NO | — | Solo: `quality_ops`, `model_improvement`, `external_sharing`. |
| `name` | VARCHAR(160) | NO | — | Etiqueta. |
| `active` | BOOLEAN | NO | `TRUE` | Si el purpose está habilitado en el producto. |

**Hacia dónde apunta:** ninguna FK.

**Ejemplo de fila completa**

```json
{
  "id": "p1000001-0000-4000-8000-000000000001",
  "code": "quality_ops",
  "name": "Calidad operativa y auditoría del conteo",
  "active": true
}
```

`external_sharing` existe para el futuro; no forma parte del flujo demo.

---

#### 1.3.2 Tabla `privacy_notice_version`

**Para qué:** congelar qué texto de aviso se mostró/aceptó.

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `version` | VARCHAR(32) | NO | — | Etiqueta de versión única (`2026.09.1`). |
| `effective_at` | TIMESTAMPTZ | NO | — | Desde cuándo rige. |
| `document_uri` | TEXT | NO | — | URI del PDF/HTML del aviso. |
| `content_sha256` | CHAR(64) | SÍ | NULL | Hash del contenido publicado. |
| `active` | BOOLEAN | NO | `TRUE` | Versión ofrecible a nuevos acuerdos. |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Alta. |

**Hacia dónde apunta:** ninguna FK saliente.

**Ejemplo de fila completa**

```json
{
  "id": "p2000001-0000-4000-8000-000000000001",
  "version": "2026.09.1",
  "effective_at": "2026-09-01T00:00:00Z",
  "document_uri": "https://demo.pef.local/legal/aviso-2026-09-1.pdf",
  "content_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "active": true,
  "created_at": "2026-08-28T12:00:00Z"
}
```

---

#### 1.3.3 Tabla `session_processing_agreement`

**Para qué:** registrar finalidades aceptadas para **una** sesión.

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `session_id` | UUID | NO | — | Sesión (único: 0..1 acuerdo). |
| `privacy_notice_version_id` | UUID | NO | — | Aviso vigente al acordar. |
| `purpose_quality_ops` | BOOLEAN | NO | `TRUE` | Siempre debe ser true (CHECK). |
| `purpose_model_improvement` | BOOLEAN | NO | `FALSE` | Opt-in mejora de modelo. |
| `agreed_at` | TIMESTAMPTZ | NO | `now()` | Momento del acuerdo. |
| `agreed_by_user_id` | UUID | SÍ | NULL | Quién aceptó. |

**Hacia dónde apunta**

| Campo | Apunta a | Relación | Por qué | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `session_id` | `work_session.id` (DS04) | 1:1 (UNIQUE session_id; sesión 0..1 acuerdo) | Ancla del tratamiento. | `CASCADE` |
| `privacy_notice_version_id` | `privacy_notice_version.id` | N:1 | Qué texto se aceptó. | `RESTRICT` |
| `agreed_by_user_id` | `user.id` (DS01) | N:0..1 | Actor humano. | `SET NULL` |

**Ejemplo de fila completa**

```json
{
  "id": "p3000001-0000-4000-8000-000000000001",
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "privacy_notice_version_id": "p2000001-0000-4000-8000-000000000001",
  "purpose_quality_ops": true,
  "purpose_model_improvement": false,
  "agreed_at": "2026-09-05T16:16:00Z",
  "agreed_by_user_id": "22222222-2222-2222-2222-222222222222"
}
```

---

#### 1.3.4 Tabla `access_audit`

**Para qué:** traza de acceso a recursos (PHI, evidencia, export). No es bitácora de conteo.

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `occurred_at` | TIMESTAMPTZ | NO | `now()` | Momento. |
| `actor_type` | VARCHAR(16) | NO | — | `user` o `client`. |
| `actor_user_id` | UUID | SÍ | NULL | Usuario si `actor_type=user`. |
| `actor_client_id` | UUID | SÍ | NULL | Cliente máquina si `actor_type=client`. |
| `action` | VARCHAR(64) | NO | — | Ej. `read`, `export`, `download`. |
| `resource_type` | VARCHAR(64) | NO | — | Ej. `patient`, `work_session`, `media_asset`. |
| `resource_id` | UUID | NO | — | UUID del recurso. |
| `institution_id` | UUID | SÍ | NULL | Tenant del acceso. |
| `outcome` | VARCHAR(16) | NO | `'success'` | `success` o `denied`. |
| `correlation_id` | VARCHAR(64) | SÍ | NULL | Correlación HTTP/WSS. |
| `ip` | INET | SÍ | NULL | IP de origen. |

**Hacia dónde apunta**

| Campo | Apunta a | Relación | Por qué | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `actor_user_id` | `user.id` (DS01) | N:0..1 | Actor humano. | `SET NULL` |
| `actor_client_id` | `integration_client.id` (DS08) | N:0..1 | Actor máquina. | `SET NULL` |
| `institution_id` | `institution.id` (DS01) | N:0..1 | Contexto tenant. | `SET NULL` |
| `resource_id` | Lógico según `resource_type` | N:1 lógico | Sin FK polimórfica. | N/A |

CHECK: par actor coherente con `actor_type`; `outcome` ∈ (`success`,`denied`).

**Ejemplo de fila completa**

```json
{
  "id": "p4000001-0000-4000-8000-000000000001",
  "occurred_at": "2026-09-05T18:30:00Z",
  "actor_type": "user",
  "actor_user_id": "22222222-2222-2222-2222-222222222222",
  "actor_client_id": null,
  "action": "download",
  "resource_type": "media_asset",
  "resource_id": "m1000003-0000-4000-8000-000000000003",
  "institution_id": "11111111-1111-1111-1111-111111111111",
  "outcome": "success",
  "correlation_id": "corr-demo-7788",
  "ip": "10.0.0.15"
}
```

---

#### 1.3.5 Tabla `privacy_request`

**Para qué:** expediente de derecho ARCO del titular (paciente).

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `request_type` | VARCHAR(16) | NO | — | `access`, `rectify`, `cancel`, `oppose`. |
| `status` | VARCHAR(32) | NO | `'received'` | `received`, `in_progress`, `completed`, `denied`. |
| `channel` | VARCHAR(32) | SÍ | NULL | Canal de recepción (`portal`, `email`, …). |
| `subject_patient_id` | UUID | NO | — | Paciente titular. |
| `requested_at` | TIMESTAMPTZ | NO | `now()` | Recepción. |
| `due_at` | TIMESTAMPTZ | SÍ | NULL | Plazo interno de respuesta. |
| `handled_by_user_id` | UUID | SÍ | NULL | Operador de cumplimiento. |
| `resolution_notes` | TEXT | SÍ | NULL | Notas de resolución. |
| `completed_at` | TIMESTAMPTZ | SÍ | NULL | Cierre (obligatorio si completed/denied). |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Alta. |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Edición. |

**Hacia dónde apunta**

| Campo | Apunta a | Relación | Por qué | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `subject_patient_id` | `patient.id` (DS02) | N:1 | Titular. | `RESTRICT` |
| `handled_by_user_id` | `user.id` (DS01) | N:0..1 | Quién atiende. | `SET NULL` |

**Cancelación (orientación operativa):** marcar `media_asset.blocked_at` / `purge_requested_at` de evidencias ligadas; borrar/anonymizar según política; reutilizar job de DS05; no “un checkbox”.

**Ejemplo de fila completa**

```json
{
  "id": "p5000001-0000-4000-8000-000000000001",
  "request_type": "cancel",
  "status": "in_progress",
  "channel": "portal",
  "subject_patient_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  "requested_at": "2026-09-06T10:00:00Z",
  "due_at": "2026-09-26T10:00:00Z",
  "handled_by_user_id": "22222222-2222-2222-2222-222222222222",
  "resolution_notes": "Inventario de sesiones y media en curso.",
  "completed_at": null,
  "created_at": "2026-09-06T10:00:00Z",
  "updated_at": "2026-09-06T11:00:00Z"
}
```

---

### 1.4 Governance and Ownership

| Aspecto | Responsable |
| :--- | :--- |
| Texto del aviso | Legal / cumplimiento institucional |
| Acuerdos de sesión | Estación / Backend al activar captura |
| access_audit | Todos los endpoints/pantallas que lean PHI |
| ARCO | Rol de cumplimiento |

---

### 1.5 Justificación del estándar

**Por qué existe**

Sin finalidades y sin traza de acceso, el conteo con evidencia nominativa no se puede explicar a un auditor ni atender ARCO.

**Decisiones tomadas**

1. **Acuerdo 0..1 por sesión**, no obligatorio al crear la sesión (reserva previa).
2. **`quality_ops` siempre true** en el acuerdo; **`model_improvement` opt-in**.
3. **Booleanos en el acuerdo** + catálogo de purposes (como checkpoint reasons).
4. **`access_audit` separado de `count_event`.**
5. **Actor user XOR client** en el mismo log.
6. **ARCO como entidad** que orquesta purga ya modelada en `media_asset`.

**Alternativas no elegidas**

| Alternativa | Por qué no |
| :--- | :--- |
| Consentimiento global por usuario sin sesión | La evidencia es por acto/sesión; el opt-in de entrenamiento debe acotarse. |
| FK N:M acuerdo↔purpose | Más flexible, más complejidad; hoy bastan dos flags + catálogo. |
| Mezclar ARCO en tickets genéricos | Pierde plazos, tipos y vínculo al paciente. |
| Auditar cada frame YOLO | Ruido inútil; se audita acceso a recurso nominativo/descarga. |

---

## 2. Anexo A – Información general

| Campo | Valor |
| :--- | :--- |
| Código | PEF-DS07 |
| Título | Privacy Purposes and Subject Rights |
| Versión | 0.1.0 |
| Estado | draft |
| Almacén | PostgreSQL (§5.7b + `cat_processing_purpose`) |
| Fuera de alcance | Bytes storage; FHIR export (DS08); contratos HTTP detalle (DS09) |
| DDL de referencia | `esquema_base_datos_v2.md` |
