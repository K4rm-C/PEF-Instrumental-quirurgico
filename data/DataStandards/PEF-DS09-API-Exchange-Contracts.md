---
id: PEF-DS09
title: API Exchange Contracts
version: "0.1.0"
status: draft
owner: Integraciones / Backend Web
applies_to: [MS, AP, BE]
stores: [PostgreSQL, Redis, MongoDB, ObjectStorage]
related: [PEF-DS01, PEF-DS03, PEF-DS04, PEF-DS05, PEF-DS07, PEF-DS08]
last_updated: 2026-09-06
---

# PEF-DS09 · API Exchange Contracts

## INTRODUCCION

Este estándar es el **dueño canónico de los contratos HTTP REST** hacia clientes externos (Android JSON, Desktop XML) y de las convenciones compartidas (`/api/v1/`, JWT, correlation id, errores).

No redefine tablas: referencia PEF-DS01…DS08 para el significado de los campos. El portal/estación **no** están obligados a pasar por estos microservicios (van al Backend Web). Vision frame-a-frame es PEF-DS10 (WSS).

---

## 1. Explicación del estándar

### 1.1 Definiciones

| Término | Significado |
| :--- | :--- |
| **Microservicio** | API REST independiente en contenedor propio. |
| **Contrato** | Método + path + headers + cuerpo request/response + códigos HTTP. |
| **Equivalencia JSON/XML** | Misma semántica; distinto `Content-Type` / `Accept`. |

### 1.2 Uso

Android → HTTPS + JSON. Desktop → HTTPS + XML. Gateway aplica correlación y rate limit; cada MS valida JWT.

---

### 1.3 Convenciones

#### 1.3.1 Headers y envelope comunes

| Campo / header | Obligatorio | Significado |
| :--- | :--- | :--- |
| `Authorization` | Sí (rutas protegidas) | `Bearer <access_token>` |
| `Content-Type` | Sí si hay body | `application/json` o `application/xml` |
| `Accept` | Recomendado | Igual que el formato del cliente |
| `X-Correlation-ID` | Sí (o lo genera el gateway) | Trazabilidad punta a punta |

**Error común (campos del body)**

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `error` | string | Código estable (`invalid_credentials`, `conflict`, …). |
| `message` | string | Texto legible (localizable en UI). |
| `correlation_id` | string | Eco del header. |
| `details` | object/array | Opcional; sin secretos ni stack traces. |

```json
{
  "error": "conflict",
  "message": "La sesión ya está cerrada",
  "correlation_id": "corr-demo-1001",
  "details": {
    "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
    "status_code": "closed"
  }
}
```

Códigos HTTP: 400, 401, 403, 404, 409, 422, 429, 500/503.

---

#### 1.3.2 Auth / Identity Service

Base: `/api/v1/auth`

| Método | Endpoint | Propósito | Acceso |
| :--- | :--- | :--- | :--- |
| POST | `/login` | Credenciales → tokens | Público autenticable |
| POST | `/refresh` | Renueva access | Refresh token |
| POST | `/logout` | Revoca en Redis | JWT |
| GET | `/me` | Perfil + roles | JWT |
| GET | `/permissions` | Permisos efectivos | JWT |

**Request completo `POST /login`**

| Campo | Tipo | Nulo | Significado |
| :--- | :--- | :--- | :--- |
| `email` | string | NO | Login (DS01). |
| `password` | string | NO | Texto en tránsito TLS; no se loguea. |

```json
{
  "email": "ana.perez@demo.local",
  "password": "[REDACTED]"
}
```

**Response completo `POST /login`**

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `access_token` | string | JWT corto. |
| `refresh_token` | string | Token de renovación. |
| `token_type` | string | `Bearer`. |
| `expires_in` | int | Segundos del access. |
| `user` | object | Resumen de perfil. |
| `user.id` | uuid | |
| `user.name` | string | |
| `user.email` | string | |
| `user.institution_id` | uuid | |
| `user.ui_preferences` | object | Ver DS01. |
| `user.roles` | array | Códigos de rol. |

```json
{
  "access_token": "[JWT_REDACTED]",
  "refresh_token": "[REFRESH_REDACTED]",
  "token_type": "Bearer",
  "expires_in": 900,
  "user": {
    "id": "22222222-2222-2222-2222-222222222222",
    "name": "Ana Pérez",
    "email": "ana.perez@demo.local",
    "institution_id": "11111111-1111-1111-1111-111111111111",
    "ui_preferences": { "locale": "es", "theme": "dark" },
    "roles": ["scrub_nurse"]
  }
}
```

---

#### 1.3.3 Catalog Service

Base: `/api/v1/catalog`

| Método | Endpoint | Propósito | Acceso |
| :--- | :--- | :--- | :--- |
| GET | `/families` | Lista familias | JWT |
| POST | `/families` | Alta familia | JWT + admin |
| GET | `/instruments` | Lista piezas | JWT |
| GET | `/kits` | Lista kits | JWT |
| GET | `/kits/{id}` | Detalle + ítems | JWT |

**Response completo ítem de familia**

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `id` | uuid | PK familia (DS03). |
| `code` | string | Código estable. |
| `name` | string | Nombre. |
| `category_code` | string | Catálogo categoría. |
| `active` | boolean | |

```json
{
  "id": "f1000001-0000-4000-8000-000000000001",
  "code": "mayo_hegar_needle_holder",
  "name": "Portaagujas Mayo-Hegar",
  "category_code": "suturing",
  "active": true
}
```

**Response completo `GET /kits/{id}`**

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `id` | uuid | |
| `name` | string | |
| `version` | int | |
| `active` | boolean | |
| `institution_id` | uuid | |
| `items` | array | Líneas kit_item. |
| `items[].family_id` | uuid | |
| `items[].family_code` | string | |
| `items[].quantity` | int | |

```json
{
  "id": "k1000001-0000-4000-8000-000000000001",
  "name": "Kit hernia inguinal abierta",
  "version": 1,
  "active": true,
  "institution_id": "11111111-1111-1111-1111-111111111111",
  "items": [
    {
      "family_id": "f1000001-0000-4000-8000-000000000001",
      "family_code": "mayo_hegar_needle_holder",
      "quantity": 2
    }
  ]
}
```

---

#### 1.3.4 Session / Counting Service

Base: `/api/v1/sessions`

| Método | Endpoint | Propósito | Acceso |
| :--- | :--- | :--- | :--- |
| GET | `/` | Lista sesiones | JWT |
| POST | `/` | Crea sesión | JWT |
| GET | `/{id}` | Detalle | JWT |
| GET | `/{id}/expected-inventory` | Esperado | JWT |
| GET | `/{id}/counts` | Conteo hechos | JWT |
| GET | `/{id}/discrepancies` | Discrepancias | JWT |
| POST | `/{id}/close` | Cierre | JWT + permiso |

**Request completo `POST /sessions`**

| Campo | Tipo | Nulo | Significado |
| :--- | :--- | :--- | :--- |
| `operation_id` | uuid | SÍ | Caso clínico (política puede exigirlo). |
| `station_id` | uuid | SÍ | Estación. |
| `kit_id` | uuid | SÍ | Kit de referencia. |

```json
{
  "operation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "station_id": "f0f0f0f0-f0f0-f0f0-f0f0-f0f0f0f0f0f0",
  "kit_id": "k1000001-0000-4000-8000-000000000001"
}
```

**Response completo detalle sesión**

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `id` | uuid | |
| `status_code` | string | |
| `started_at` | string ISO | |
| `ended_at` | string ISO\|null | |
| `user_id` | uuid | |
| `operation_id` | uuid\|null | |
| `station_id` | uuid\|null | |
| `kit_id` | uuid\|null | |
| `current_phase_code` | string\|null | |
| `atypical_session` | boolean | |
| `extended_retention` | boolean | |

```json
{
  "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "status_code": "open",
  "started_at": "2026-09-05T16:15:00Z",
  "ended_at": null,
  "user_id": "22222222-2222-2222-2222-222222222222",
  "operation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "station_id": "f0f0f0f0-f0f0-f0f0-f0f0-f0f0f0f0f0f0",
  "kit_id": "k1000001-0000-4000-8000-000000000001",
  "current_phase_code": "pre_count",
  "atypical_session": false,
  "extended_retention": false
}
```

---

#### 1.3.5 Evidence / File Service

| Método | Endpoint | Propósito |
| :--- | :--- | :--- |
| POST | `/api/v1/evidence` | Metadata + carga autorizada |
| GET | `/api/v1/evidence/{id}` | Metadata + URI/firmada |
| GET | `/api/v1/sessions/{id}/evidence` | Lista por sesión |

**Response completo evidencia**

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `id` | uuid | `media_asset.id` |
| `kind` | string | jpeg/gif/… |
| `content_type` | string | |
| `size_bytes` | int\|null | |
| `storage_provider` | string | |
| `signed_url` | string\|null | Temporal; null si blocked |
| `retention_until` | string\|null | |
| `blocked` | boolean | Derivado de `blocked_at` |

```json
{
  "id": "m1000001-0000-4000-8000-000000000001",
  "kind": "jpeg",
  "content_type": "image/jpeg",
  "size_bytes": 245760,
  "storage_provider": "gcs",
  "signed_url": "https://storage.example/signed/...(ttl)",
  "retention_until": "2026-12-04T18:20:00Z",
  "blocked": false
}
```

---

#### 1.3.6 Audit / Notification / Monitoring (campos)

**Audit event**

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `id` | uuid | |
| `occurred_at` | string | |
| `actor_type` | string | user\|client |
| `action` | string | |
| `resource_type` | string | |
| `resource_id` | uuid | |
| `outcome` | string | |

**Notification**

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `id` | uuid | |
| `title` | string | |
| `body` | string | |
| `read` | boolean | |
| `created_at` | string | |

**Monitor service status**

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `name` | string | Nombre del MS |
| `status` | string | up\|degraded\|down |
| `latency_ms` | number\|null | |
| `checked_at` | string | |

---

#### 1.3.7 Health checks

| Endpoint | Valida |
| :--- | :--- |
| `/health/live` | Proceso vivo |
| `/health/ready` | Listo para tráfico |
| `/health/database` | PostgreSQL |
| `/health/redis` | Redis |
| `/health/mongodb` | Mongo |
| `/health/storage` | Object storage |

```json
{
  "status": "ready",
  "checks": {
    "database": "ok",
    "redis": "ok"
  },
  "correlation_id": "corr-health-1"
}
```

---

### 1.4 Governance and Ownership

| Aspecto | Responsable |
| :--- | :--- |
| OpenAPI por MS | Equipo Integraciones |
| Cambios breaking | Versionar `/api/v2` |
| Equivalencia XML | Mismos campos que JSON |

---

### 1.5 Justificación del estándar

**Decisiones tomadas**

1. **Un DS dueño de endpoints** para no redefinir HTTP en cada DS de tablas.
2. **JSON Android / XML Desktop** con semántica igual.
3. **Portal fuera del gateway** (Backend Web directo).
4. **409** para conflictos de estado de sesión.

**Alternativas no elegidas**

| Alternativa | Por qué no |
| :--- | :--- |
| Un solo monolito API para portal y móvil | Acopla ciclos de release y choca con el requisito del curso. |
| GraphQL único | Fuera del alcance acordado; REST versionado basta. |
| Documentar endpoints solo dentro de DS04/DS05 | Duplicación; este DS es el canónico. |

---

## 2. Anexo A – Información general

| Campo | Valor |
| :--- | :--- |
| Código | PEF-DS09 |
| Título | API Exchange Contracts |
| Versión | 0.1.0 |
| Estado | draft |
| Fuentes | `Definicion APIs Modulos Microservicios.docx.md`, `arquitectura_servicios_y_cobertura.md` |
| Fuera de alcance | WSS visión (DS10); DDL de tablas |
