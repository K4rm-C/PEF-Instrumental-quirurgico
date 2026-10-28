---
id: PEF-DS09
title: API Exchange Contracts
version: "0.1.8"
status: draft
owner: Integraciones / Backend Web
applies_to: [MS, AP, BE]
stores: [PostgreSQL, Redis, MongoDB, ObjectStorage]
related: [PEF-DS01, PEF-DS03, PEF-DS04, PEF-DS05, PEF-DS07, PEF-DS08]
last_updated: 2026-10-13
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

**Regla canónica `X-Correlation-ID` (v0.1.5).** Formato válido: `^[A-Za-z0-9._:-]{1,64}$` (máximo 64 caracteres, igual que `access_audit.correlation_id` VARCHAR(64), DS07). Si el header falta, no cumple el formato o supera 64 caracteres, el servicio **genera un UUID nuevo**; nunca lo trunca ni lo corrige. El mismo string exacto se usa en el header `X-Correlation-ID` de la respuesta, en `correlation_id` del envelope de error, en los logs y en `access_audit.correlation_id`.

**Error común (campos del body)**

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `error` | string | Código estable (`invalid_credentials`, `conflict`, …). |
| `message` | string | Texto legible (localizable en UI). |
| `correlation_id` | string | Eco del header válido, o el UUID generado (regla de arriba). |
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

> **Complemento v0.1.2 (implementación Auth API v1).** Completa los huecos de §1.3.2 sin cambiar el request/response de `POST /login` ni la versión `/api/v1/`. Implementado en `apps/BackendAuthService/api_v1.py` (OpenAPI: `apps/BackendAuthService/openapi.yaml`). Las rutas sin versión (`/login`, `/refresh`, `/logout`, `/verify`, `/preferences`, …) siguen siendo las del portal web (cookies HttpOnly) y no forman parte de este contrato.

**Reglas comunes**

- Los tokens van en el body (no cookies). `Authorization: Bearer <access_token>` hacia Auth y hacia los microservicios.
- JWT firmado con exactamente `JWT_ALGORITHM` (default `HS256`); cualquier otro algoritmo, incluido `none`, → 401. Claims obligatorios: `sub`, `jti`, `type` (`access`\|`refresh`), `exp`. Sesión Redis `auth:jwt:{jti}` (DS01 §1.3.5) presente, no revocada, del mismo `token_type` y `user_id`.
- Usuario e institución activos en PostgreSQL; la institución efectiva es `user.institution_id` (la de Redis debe coincidir). `user.roles` y los permisos son los códigos de `user_role` → `role` **de esa institución** (`station_operator`, `spd_supervisor`, `it_admin`); nunca el claim `role` del JWT.
- `POST /login` con usuario o institución inactivos → 401 `invalid_credentials`; demasiados intentos fallidos → 429 `rate_limited`. `ui_preferences.locale` usa los valores de DS01 (`en`, `es-MX`).

**Request completo `POST /refresh`** (rotación: el refresh presentado queda revocado)

| Campo | Tipo | Nulo | Significado |
| :--- | :--- | :--- | :--- |
| `refresh_token` | string | NO | Refresh token emitido por `/login` o `/refresh`. |

```json
{ "refresh_token": "[REFRESH_REDACTED]" }
```

**Response completo `POST /refresh`**: mismos cuatro campos de token que `/login`, sin `user`.

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `access_token` | string | JWT nuevo. |
| `refresh_token` | string | Refresh nuevo (el anterior ya no sirve). |
| `token_type` | string | `Bearer`. |
| `expires_in` | int | Segundos del access. |

Se aplican las mismas reglas de sesión que el portal (duración máxima por rol; inactividad, salvo operador con sesión de conteo en curso). Refresh inválido, expirado, revocado, de tipo `access` o rechazado por política → 401 `unauthorized`.

**Request completo `POST /logout`** (JWT = Bearer access token; body **obligatorio**, v0.1.3)

| Campo | Tipo | Nulo | Significado |
| :--- | :--- | :--- | :--- |
| `refresh_token` | string | NO | Refresh token de la misma sesión: `type=refresh`, presente y no revocado en Redis, del mismo `user_id` que el access token. |

```json
{ "refresh_token": "[REFRESH_REDACTED]" }
```

Respuesta **204** sin body: access y refresh quedan revocados en Redis (cualquier microservicio rechaza el access; `/refresh` rechaza el refresh). Ambos se validan antes de revocar; si algo falla no se revoca nada: `refresh_token` ausente o vacío → 400 `invalid_request`; refresh inválido, expirado, revocado, ausente en Redis o de tipo `access` → 401 `unauthorized`; refresh de otro usuario → 403 `forbidden` (no se revoca ningún token de ese usuario).

**Response completo `GET /me`**: el mismo objeto `user` de `/login` (`id`, `name`, `email`, `institution_id`, `ui_preferences`, `roles`), leído de PostgreSQL en cada llamada. No incluye hash, tokens ni datos de Redis.

**Response completo `GET /permissions`**

PEF autoriza por rol (DS01, sin tabla de permisos): los permisos efectivos son los códigos de rol efectivos.

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `user_id` | uuid | Usuario autenticado. |
| `institution_id` | uuid | Institución efectiva (PostgreSQL). |
| `roles` | array | Códigos de rol efectivos de esa institución. |

```json
{
  "user_id": "22222222-2222-2222-2222-222222222223",
  "institution_id": "11111111-1111-1111-1111-111111111111",
  "roles": ["it_admin"]
}
```

**Códigos de error Auth API**: `invalid_request` 400, `invalid_credentials` 401 (solo `/login`), `unauthorized` 401, `forbidden` 403 (solo `/logout`), `not_found` 404, `method_not_allowed` 405, `payload_too_large` 413, `rate_limited` 429, `dependency_unavailable` 503 (`details.dependency`: `database`\|`redis`).

**XML (Auth)**: mismas reglas que Catalog (§1.3.3, “Representación XML”). Raíces: request `login_request`, `refresh_request`, `logout_request`; response `login_response`, `refresh_response`, `user` (`/me`), `permissions`, `error_response`. Lista de roles: `roles/role`.

```xml
<login_response>
  <access_token>[JWT_REDACTED]</access_token>
  <refresh_token>[REFRESH_REDACTED]</refresh_token>
  <token_type>Bearer</token_type>
  <expires_in>900</expires_in>
  <user>
    <id>22222222-2222-2222-2222-222222222222</id>
    <name>Ana Pérez</name>
    <email>ana.perez@demo.local</email>
    <institution_id>11111111-1111-1111-1111-111111111111</institution_id>
    <ui_preferences>
      <locale>es-MX</locale>
      <theme>dark</theme>
    </ui_preferences>
    <roles>
      <role>station_operator</role>
    </roles>
  </user>
</login_response>
```

**Health (Auth)**: `/health/live`, `/health/ready`, `/health/database`, `/health/redis` con el formato de §1.3.7 (Auth no usa MongoDB ni object storage).

> **Complemento v0.1.4 (administración de identidad).** Usuarios, roles, asignación de roles y contraseñas, que la Administración del portal ejecutaba en proceso. Los contratos anteriores de §1.3.2 (`/login`, `/refresh`, `/logout`, `/me`, `/permissions`) **no cambian**. Implementado en `apps/BackendAuthService/identity_admin.py` (OpenAPI ejecutable: `GET /api/v1/auth/docs`; XSD: `GET /api/v1/auth/schemas/identity.xsd`).

| Método | Endpoint | Propósito | Acceso |
| :--- | :--- | :--- | :--- |
| GET / POST | `/users` | Lista (todas las instituciones) / alta | JWT + `it_admin` |
| GET / PUT | `/users/{id}` | Detalle / reemplazo de nombre, correo, institución, roles y estado | JWT + `it_admin` |
| PUT | `/users/{id}/roles` | Asignar roles (de la institución del usuario) | JWT + `it_admin` |
| POST | `/users/{id}/activate`, `/users/{id}/deactivate` | Reactivar / desactivar | JWT + `it_admin` |
| POST | `/users/{id}/password` | Restablecimiento por el administrador | JWT + `it_admin` |
| POST | `/password/change` | Cambio de la propia contraseña (`current_password`, `new_password`) → 204 | JWT |
| GET / POST | `/roles` | Lista (todas las instituciones) / alta en la institución del administrador | JWT + `it_admin` |
| GET / PUT / DELETE | `/roles/{id}` | Detalle / descripción (el código es inmutable) / baja | JWT + `it_admin` |

Representación: `managed_user` (`id`, `name`, `email`, `active`, `institution {id, name}`, `roles[] {code, description}`, `last_login_at`, `password_updated_at`, `created_at`, `updated_at`) y `managed_role` (`id`, `code`, `description`, `institution {id, name}`, `assigned_users`, `system`). Nunca incluyen contraseña, hash ni tokens. XML: raíces de petición `user_create_request`, `user_update_request`, `user_roles_request`, `password_reset_request`, `password_change_request`, `role_create_request`, `role_update_request`; respuesta `managed_user`, `managed_users/managed_user`, `managed_role`, `managed_roles/managed_role`, `role_deleted`; listas `role_codes/role_code`, `roles/role`. Todo cuerpo XML se valida contra el XSD (fallo → 400, `details.reason = "xsd_validation_failed"`, `details.schema`, `details.errors[]`).

Reglas (las mismas del monolito): `name` obligatorio (≤160); `email` obligatorio, válido, único sin distinguir mayúsculas y guardado en minúsculas; institución existente y **activa** (al dar de alta o cambiar de institución); al menos un rol, todos de esa institución; contraseña ≥8 y confirmación igual si se envía; código de rol `^[a-z][a-z0-9_]*$` (≤64), único por institución; los roles de sistema (`it_admin`, `spd_supervisor`, `station_operator`) no se borran; un rol asignado no se borra. Protección de administradores: nadie se desactiva ni se quita `it_admin` a sí mismo, y ninguna operación deja a una institución sin un `it_admin` activo (comprobado bajo un *advisory lock* transaccional). Errores: 400 cuerpo mal formado o campo desconocido; 404 id inexistente o mal formado; 409 `email_exists`, `role_code_exists`, `last_admin`, `cannot_deactivate_self`, `cannot_remove_own_admin`, `system_role`, `role_in_use`; 422 `name_required`, `name_too_long`, `email_required`, `email_too_long`, `invalid_email`, `institution_required`, `invalid_institution`, `institution_inactive`, `roles_required`, `invalid_roles`, `password_too_short`, `password_mismatch`, `invalid_current_password`, `code_required`, `invalid_role_code`, `description_required`. Un rol sin permiso → 403 y `DENIED_<MÉTODO>_<RECURSO>` en `access_audit`.

Sesiones: desactivar, mover de institución o restablecer la contraseña revoca en Redis todas las sesiones del usuario (índice `auth:user_tokens:{user_id}`, DS01 §1.3.5); además cada petición comprueba en PostgreSQL que usuario e institución sigan activos. Los cambios de roles valen desde la petición siguiente (los roles se leen de PostgreSQL). Auditoría en la misma transacción: `CREATE_USER`, `UPDATE_USER`, `ACTIVATE_USER`, `DEACTIVATE_USER`, `CHANGE_USER_ROLES`, `RESET_USER_PASSWORD`, `CHANGE_OWN_PASSWORD`, `CREATE_ROLE`, `UPDATE_ROLE`, `DELETE_ROLE`.

Reglas compartidas con las rutas del portal (`/login`, `/verify`, `/refresh`): roles efectivos solo de la institución del usuario, y un token de una institución desactivada deja de ser válido.


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

> **Complemento v0.1.1 (implementación Catalog Service).** Lo siguiente completa huecos de §1.3.3 sin cambiar los contratos anteriores ni la versión `/api/v1/`. Implementado en `apps/CatalogService` (OpenAPI: `apps/CatalogService/openapi.yaml`).

**Listas (`GET /families`, `GET /instruments`, `GET /kits`)**

El cuerpo JSON es un **arreglo** de ítems (sin envoltura, sin paginación, filtros ni búsqueda). Orden determinista (orden de bytes, `COLLATE "C"`):

| Endpoint | Orden |
| :--- | :--- |
| `GET /families` | `code` ascendente |
| `GET /instruments` | `family_code`, `internal_code` (nulos al final), `id` |
| `GET /kits` | `name`, `version`, `id` |
| `GET /kits/{id}` → `items` | `family_code` |

`GET /families` devuelve el catálogo global (activas e inactivas; ver `active`). `GET /instruments` y `GET /kits` devuelven solo filas de la institución del usuario autenticado; la institución se deriva del JWT/usuario, nunca de query o body.

**Request completo `POST /families`** (JWT + rol efectivo `it_admin` de la institución del usuario)

| Campo | Tipo | Nulo | Significado |
| :--- | :--- | :--- | :--- |
| `code` | string ≤64 | NO | Código único global (DS03). Se recorta espacio. |
| `name` | string ≤160 | NO | Nombre legible. |
| `category_code` | string | NO | `cat_instrument_category.code`; se resuelve a `category_id`. |
| `identify_text` | string | SÍ | Opcional; vacío → `null`. |
| `classify_text` | string | SÍ | Opcional; vacío → `null`. |
| `function_text` | string | SÍ | Opcional; vacío → `null`. |
| `active` | boolean | NO | Opcional; default `true`. |

Campos no listados (p. ej. `category_id`) se rechazan con 400. Alta transaccional (todo o nada).

```json
{
  "code": "mayo_hegar_needle_holder",
  "name": "Portaagujas Mayo-Hegar",
  "category_code": "suturing",
  "identify_text": "Texto opcional",
  "classify_text": null,
  "function_text": null,
  "active": true
}
```

Respuesta: **201** con el ítem de familia definido arriba (`id`, `code`, `name`, `category_code`, `active`).

| HTTP | `error` | Caso |
| :--- | :--- | :--- |
| 400 | `invalid_request` | JSON/XML mal formado, tipos incorrectos, campos obligatorios ausentes o desconocidos, `Content-Type` no soportado. |
| 401 | `unauthorized` | Token ausente/inválido/expirado, sesión Redis ausente o revocada, usuario inactivo. |
| 403 | `forbidden` | Sin rol efectivo `it_admin`. |
| 409 | `conflict` | `code` ya existe. |
| 422 | `validation_error` | Valor vacío tras recortar o más largo que la columna. |
| 422 | `invalid_reference` | `category_code` inexistente. |
| 503 | `dependency_unavailable` | PostgreSQL o Redis no disponibles (`details.dependency`). |

**Response completo ítem de `GET /instruments`**

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `id` | uuid | PK pieza (DS03). |
| `internal_code` | string\|null | Código interno de la institución. |
| `family_id` | uuid | Familia. |
| `family_code` | string | `instrument_family.code`. |
| `cycle_status_code` | string | `cat_instrument_cycle_status.code` actual. |
| `institution_id` | uuid | Siempre la del usuario autenticado. |
| `active` | boolean | |

```json
{
  "id": "i1000001-0000-4000-8000-000000000001",
  "internal_code": "NH-MH-014",
  "family_id": "f1000001-0000-4000-8000-000000000001",
  "family_code": "mayo_hegar_needle_holder",
  "cycle_status_code": "clean",
  "institution_id": "11111111-1111-1111-1111-111111111111",
  "active": true
}
```

**Response completo ítem de `GET /kits`** (sin `items`; se obtienen con `GET /kits/{id}`)

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `id` | uuid | |
| `name` | string | |
| `version` | int | |
| `active` | boolean | |
| `institution_id` | uuid | |

```json
{
  "id": "k1000001-0000-4000-8000-000000000001",
  "name": "Kit hernia inguinal abierta",
  "version": 1,
  "active": true,
  "institution_id": "11111111-1111-1111-1111-111111111111"
}
```

**`GET /kits/{id}` · 404**: si `id` no es UUID, no existe o pertenece a otra institución (respuesta idéntica en los tres casos; no revela existencia).

**Representación XML (Catalog)**

`Accept: application/xml` → XML; `application/json`, `*/*` o sin `Accept` → JSON. `POST /families` acepta `Content-Type: application/json` o `application/xml`. Reglas:

1. Nombres de elemento = nombres de campo JSON.
2. Lista = elemento contenedor con un hijo por entrada: `families/family`, `instruments/instrument`, `kits/kit`, `items/item`.
3. Objeto raíz: `family`, `kit`; error: `error_response`; health: `health`.
4. Boolean `true`/`false` (en request también `1`/`0`); `null` = elemento vacío con `xsi:nil="true"` (namespace `http://www.w3.org/2001/XMLSchema-instance`).
5. Sin DTD ni declaraciones de entidad (se rechazan con 400). UTF-8.

```xml
<families>
  <family>
    <id>f1000001-0000-4000-8000-000000000001</id>
    <code>mayo_hegar_needle_holder</code>
    <name>Portaagujas Mayo-Hegar</name>
    <category_code>suturing</category_code>
    <active>true</active>
  </family>
</families>
```

```xml
<family>
  <code>mayo_hegar_needle_holder</code>
  <name>Portaagujas Mayo-Hegar</name>
  <category_code>suturing</category_code>
  <identify_text>Texto opcional</identify_text>
  <active>true</active>
</family>
```

```xml
<instruments xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <instrument>
    <id>i1000001-0000-4000-8000-000000000001</id>
    <internal_code xsi:nil="true" />
    <family_id>f1000001-0000-4000-8000-000000000001</family_id>
    <family_code>mayo_hegar_needle_holder</family_code>
    <cycle_status_code>clean</cycle_status_code>
    <institution_id>11111111-1111-1111-1111-111111111111</institution_id>
    <active>true</active>
  </instrument>
</instruments>
```

```xml
<kit>
  <id>k1000001-0000-4000-8000-000000000001</id>
  <name>Kit hernia inguinal abierta</name>
  <version>1</version>
  <active>true</active>
  <institution_id>11111111-1111-1111-1111-111111111111</institution_id>
  <items>
    <item>
      <family_id>f1000001-0000-4000-8000-000000000001</family_id>
      <family_code>mayo_hegar_needle_holder</family_code>
      <quantity>2</quantity>
    </item>
  </items>
</kit>
```

```xml
<error_response>
  <error>conflict</error>
  <message>An instrument family with this code already exists</message>
  <correlation_id>corr-demo-1001</correlation_id>
  <details>
    <field>code</field>
    <value>mayo_hegar_needle_holder</value>
  </details>
</error_response>
```

---

> **Complemento v0.1.2 (gestión de catálogo, Catalog Service).** Añade las operaciones que la Administración del portal ejecutaba en proceso. Los cinco contratos anteriores **no cambian** (mismos campos y orden). Implementado en `apps/CatalogService` (OpenAPI ejecutable: `GET /api/v1/catalog/docs`; XSD: `GET /api/v1/catalog/schemas/catalog.xsd`).

| Método | Endpoint | Propósito | Acceso |
| :--- | :--- | :--- | :--- |
| GET | `/categories`, `/cycle-statuses` | Catálogos DS03 (`id`, `code`, `name`) | JWT |
| GET | `/families?expand=detail` | Lista con `category_id`, `category_name`, textos y `available_count` | JWT |
| GET | `/families/{id}` | Detalle (+ `kits_using`) | JWT |
| PUT | `/families/{id}` | Edición (`code` inmutable) | JWT + `it_admin` |
| GET | `/instruments?expand=detail` | Lista con nombres de familia, categoría y estado de ciclo | JWT |
| POST | `/instruments` | Alta de pieza en la institución del usuario + evento de ciclo `registered` | JWT + `it_admin` o `spd_supervisor` |
| GET / PUT | `/instruments/{id}` | Detalle con últimos 10 eventos de ciclo / edición (`internal_code` inmutable) | GET JWT; PUT `it_admin` o `spd_supervisor` |
| GET | `/kits?expand=detail`, `/kits/{id}?expand=detail` | `family_count`, `total_expected`, `procedures[]`, ítems con nombre, categoría y `available_count` | JWT |
| POST | `/kits` | Alta (versión = siguiente para institución + nombre) | JWT + `it_admin` o `spd_supervisor` |
| PUT | `/kits/{id}` | Cambio = **nueva versión**: la editada queda inactiva y sus `procedure_kit` pasan a la nueva (201, `superseded_kit_id`); sin cambios → 200 | JWT + `it_admin` o `spd_supervisor` |

Reglas: `expand` solo admite `detail` (otro valor → 400). Cuerpos JSON o XML (raíces `family`, `instrument`, `kit`; composición `items/item`). Campos desconocidos → 400. Errores de valor → 422 con `details.reason` estable: `name_required`, `invalid_category`, `invalid_family_or_cycle`, `change_reason_required` (cambio de estado de ciclo o activo sin motivo), `empty_composition`, `invalid_quantity` (1..32767), `duplicate_family`, `invalid_family`. Conflictos 409: `instrument_code_exists` (código interno por institución), `kit_exists`. Instrumentos y kits siempre de la institución del usuario (otra institución → 404). Cada cambio escribe `access_audit` en la misma transacción (`CREATE_/UPDATE_/ACTIVATE_/DEACTIVATE_<ENTIDAD>`, `SUPERSEDE_KIT`); un intento con rol sin permiso → 403 y `DENIED_<MÉTODO>_<RECURSO>`. `available_count` y `kits_using` cuentan solo la institución del usuario.

**Kits inactivos.** Solo `it_admin` lista, lee y edita kits inactivos. Cualquier otro rol (incluido `spd_supervisor`) recibe solo los kits activos de su institución y `404` para uno inactivo en `GET /kits/{id}` y `PUT /kits/{id}`, también en llamadas HTTP directas.

**Validación XML.** Todo cuerpo XML se valida contra `GET /api/v1/catalog/schemas/catalog-requests.xsd` después de los controles de forma (bien formado, raíz, duplicados, tipos). Un documento que no cumple → `400 invalid_request` con `details.reason = "xsd_validation_failed"`, `details.schema`, `details.errors[]` (`line`, `message`) y, si hay elementos no admitidos, `details.unknown_fields[]`. Las reglas de negocio (422) dan el mismo `reason` en JSON y en XML.

#### 1.3.4 Session / Counting Service

Base: `/api/v1/sessions` — **Session API v1 (documento v0.1.4)**. Tablas y significado de campos: PEF-DS04 v0.2.2.

| Método | Endpoint | Rol | Estados válidos | Resultado |
| :--- | :--- | :--- | :--- | :--- |
| GET | `/` | OP, SPD | todos | Lista acotada por rol |
| POST | `/` | SPD | — | Crea sesión `scheduled` sobre una operación existente |
| GET | `/{id}` | OP, SPD | todos | Detalle |
| GET | `/{id}/expected-inventory` | OP, SPD | todos | Líneas esperadas |
| PUT | `/{id}/expected-inventory` | SPD | `scheduled`, `in_progress` | Reemplazo total de líneas |
| POST | `/{id}/start` | OP asignado | `scheduled` | → `in_progress` |
| POST | `/{id}/vision-counts` | OP asignado | `in_progress` + `vision` | Hecho `auto_count` (lote) |
| POST | `/{id}/count-report` | OP asignado | `in_progress` | → `awaiting_spd_review` o `correction_required` |
| GET | `/{id}/counts` | OP, SPD | todos | Hechos de conteo |
| GET | `/{id}/discrepancies` | OP, SPD | todos | Discrepancias |
| POST | `/{id}/discrepancies/{discrepancy_id}/resolve` | SPD | `correction_required` | Resuelve; última → `awaiting_spd_review` |
| POST | `/{id}/close` | SPD | `awaiting_spd_review` | → `closed` |

OP = `station_operator`; SPD = `spd_supervisor`. **`it_admin` no tiene acceso clínico** a Session API (403 en todos los endpoints).

**Fuera de Session API v1:** `abort` (estado `aborted` sin comportamiento), cambio de fase (stub), `PUT /sessions/{id}` (Session no edita la operación clínica: operador, sala, paciente, médicos, procedimiento o `scheduled_at` pertenecen al flujo que gestiona `operation`), escritura del acuerdo de privacidad (DS07), y cualquier endpoint de video, cámara, frames, WebSocket o imágenes.

**Reglas comunes**

1. **Auth:** `Authorization: Bearer <access_token>` validado igual que Catalog/Auth (algoritmo exacto `JWT_ALGORITHM`, sesión Redis `auth:jwt:{jti}`, usuario e institución activos). Roles efectivos desde `user_role` → `role` de la institución del usuario; nunca solo el claim `role`.
2. **Tenant:** tenant efectivo = `user.institution_id` del usuario autenticado; tenant de la sesión = `operation.institution_id`. Deben coincidir. El cliente nunca envía ni elige `institution_id`.
3. **Visibilidad y errores:** sesión de otra institución → **404**; sesión no asignada al operador que la pide → **404**; rol sin permiso para una acción válida dentro del tenant → **403**. Los mensajes no revelan la institución dueña.
4. **Concurrencia:** `start`, `count-report`, `PUT expected-inventory`, `resolve` y `close` se ejecutan en una transacción que bloquea la fila de `work_session` (`SELECT … FOR UPDATE` o equivalente; `resolve` bloquea además la discrepancia). Si el estado cambió concurrentemente → **409** `conflict`. `vision-counts` usa además `client_event_id` (UNIQUE) para idempotencia.
5. **Auditoría:** mientras no exista Audit Service, las acciones de escritura se registran en `access_audit` (DS07) mediante un adapter aislado, con `correlation_id`. `count_event` es hecho de negocio de Session, no auditoría. **Excepción (v0.1.6):** los hechos normalizados de visión (`POST /vision-counts`, persistidos como `count_event` `auto_count`) **no** generan una fila `access_audit` por snapshot: el `auto_count` es el registro autoritativo (`user_id` NULL, producido por el modelo) y los logs técnicos conservan el `X-Correlation-ID`.
6. **Excepción transitoria DS03:** `resolve` (lost/recovered) y `close` (reservas, `sterilization`) siguen las transiciones de PEF-DS03 §1.3.9.1 (v0.2.1) y escriben directamente `instrument`, `instrument_reservation` e `instrument_cycle_event`, siempre limitados a piezas de la institución de la sesión, aislados en un adapter específico, hasta que Catalog exponga esos contratos.
7. **Formato:** JSON y XML con la misma semántica (ver “Representación XML (Catalog)” en §1.3.3). Errores con el envelope de §1.3.1. `X-Correlation-ID` y health checks (`/health/live`, `/health/ready`, `/health/database`, `/health/redis`) como Auth/Catalog.
8. **Captura agnóstica:** Session recibe **hechos de conteo normalizados**, nunca video, stream de cámara, frames, WebSocket ni imágenes. El contrato es idéntico con el video subido actual y con la cámara en vivo futura:

```
Capture transport (video file | live camera → WebSocket Gateway)
   → Vision pipeline (VisionWorker; boxes/telemetría → DS06; imágenes → DS05)
   → normalized count {client_event_id, occurred_at, model_version, trigger, counts[]}
   → Session API (POST /sessions/{id}/vision-counts)
```

**Session List Item (`GET /sessions`)**

| Rol | Alcance |
| :--- | :--- |
| `station_operator` | Solo sesiones con `work_session.user_id` = usuario autenticado (y del mismo tenant). |
| `spd_supervisor` | Sesiones cuya `operation.institution_id` = institución del usuario. |

Orden determinista: `scheduled_at` descendente (nulos al final), luego `id`. Sin paginación ni filtros en v1.

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `id` | uuid | `work_session.id` |
| `status_code` | string | DS04 (`scheduled`, `in_progress`, `awaiting_spd_review`, `correction_required`, `closed`, `aborted`) |
| `capture_mode` | string\|null | `vision` \| `manual_no_privacy`; null mientras `scheduled` |
| `scheduled_at` | string ISO\|null | `operation.scheduled_at` |
| `started_at` | string ISO\|null | |
| `ended_at` | string ISO\|null | |
| `user_id` | uuid | Operador asignado |
| `kit_id` | uuid\|null | |
| `open_discrepancies` | int | Discrepancias sin resolver |

**Response completo `GET /sessions/{id}`** (reemplaza el ejemplo de v0.1.0, que usaba códigos inexistentes)

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `id` | uuid | |
| `operation_id` | uuid | Operación (DS02) |
| `user_id` | uuid | Operador asignado |
| `station_id` | uuid\|null | |
| `kit_id` | uuid\|null | |
| `status_code` | string | |
| `capture_mode` | string\|null | |
| `current_phase_code` | string\|null | `cat_operation_phase.code` |
| `scheduled_at` | string ISO\|null | |
| `started_at` | string ISO\|null | Null mientras `scheduled` |
| `ended_at` | string ISO\|null | |
| `closed_by_user_id` | uuid\|null | |
| `open_discrepancies` | int | |
| `privacy_agreement` | boolean | **Read-only**, derivado de DS07: existe acuerdo para la sesión. No expone el acuerdo; Session no es dueño de esa información. |

```json
{
  "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "operation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "user_id": "22222222-2222-2222-2222-222222222221",
  "station_id": "13131313-0000-4000-8000-000000000001",
  "kit_id": "51515151-0000-4000-8000-000000000001",
  "status_code": "scheduled",
  "capture_mode": null,
  "current_phase_code": "start",
  "scheduled_at": "2026-09-05T16:00:00Z",
  "started_at": null,
  "ended_at": null,
  "closed_by_user_id": null,
  "open_discrepancies": 0,
  "privacy_agreement": true
}
```

**Request completo `POST /sessions`** (la operación **debe existir antes**)

| Campo | Tipo | Nulo | Significado y validación |
| :--- | :--- | :--- | :--- |
| `operation_id` | uuid | NO | Operación del mismo tenant, en estado `scheduled` (DS02). |
| `user_id` | uuid | NO | Operador asignado: mismo tenant, activo, rol efectivo `station_operator` de ese tenant. |
| `station_id` | uuid | NO | Estación de una sala del tenant; si la operación tiene sala, debe ser esa sala. |
| `kit_id` | uuid | NO | Kit del mismo tenant. |
| `phase_code` | string | NO | `cat_operation_phase.code` activo (fase inicial). |
| `expected_lines` | array | NO | Al menos una línea; cada familia una sola vez. |
| `expected_lines[].family_id` | uuid | NO | Familia válida (catálogo global DS03). |
| `expected_lines[].expected_quantity` | int | NO | ≥ 1 y ≤ piezas `available` activas de esa familia en el tenant. |

```json
{
  "operation_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  "user_id": "22222222-2222-2222-2222-222222222221",
  "station_id": "13131313-0000-4000-8000-000000000001",
  "kit_id": "51515151-0000-4000-8000-000000000001",
  "phase_code": "start",
  "expected_lines": [
    { "family_id": "41414141-0000-4000-8000-000000000010", "expected_quantity": 1 }
  ]
}
```

XML equivalente (raíz `session_request`, v0.1.5):

```xml
<session_request>
  <operation_id>aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa</operation_id>
  <user_id>22222222-2222-2222-2222-222222222221</user_id>
  <station_id>13131313-0000-4000-8000-000000000001</station_id>
  <kit_id>51515151-0000-4000-8000-000000000001</kit_id>
  <phase_code>start</phase_code>
  <expected_lines>
    <line>
      <family_id>41414141-0000-4000-8000-000000000010</family_id>
      <expected_quantity>1</expected_quantity>
    </line>
  </expected_lines>
</session_request>
```

Respuesta **201** con el detalle (`status_code: scheduled`, `started_at` y `capture_mode` null). `POST /sessions` crea **solo** el dominio Session (`work_session` + `expected_inventory`; `source` = `kit_snapshot` si la cantidad coincide con el kit, si no `schedule_additional`). **No** crea `operation`, `patient`, `operation_patient`, `physician`, `operation_physician` ni el acuerdo de privacidad.

Errores: 400 `invalid_request` (estructura/tipos); 422 `invalid_reference` (operación, usuario, estación, kit o familia inexistente, de otro tenant o inconsistente; sin revelar cuál tenant); 409 `conflict` (operación en estado no apto); 422 `validation_error` (cantidad > stock disponible, `details.family_id`).

**`GET /sessions/{id}/expected-inventory`** — orden `family_code`, `family_id`.

```json
[
  { "family_id": "41414141-0000-4000-8000-000000000010", "family_code": "MAYOHEG", "expected_quantity": 2, "source": "kit_snapshot" }
]
```

`source` ∈ (`kit_snapshot`, `schedule_additional`, `live_add`, `manual`) según DS04.

**`PUT /sessions/{id}/expected-inventory`** (SPD) — reemplazo total.

```json
{ "lines": [ { "family_id": "41414141-0000-4000-8000-000000000010", "expected_quantity": 2 } ] }
```

Mismas validaciones de línea que `POST /sessions`. `scheduled`: líneas nuevas/cambiadas con `source` según el kit. `in_progress`: `source = live_add`, en transacción con bloqueo de la sesión (incompatible con un `count-report` concurrente) y auditada. Después del `count-report` (o cualquier otro estado) → **409**. Respuesta 200 = lista como el GET.

**`POST /sessions/{id}/start`** (OP asignado) — body vacío o `{}`.

Bloquea la sesión, verifica asignación y estado `scheduled`, lee el acuerdo DS07 (con acuerdo → `capture_mode = vision`; sin acuerdo → `manual_no_privacy`; inmutable después), fija `started_at`, fase inicial si faltaba, copia las líneas del kit solo si la sesión no tenía inventario esperado, registra `session_open` y pasa a `in_progress`. No contiene lógica de video, cámara ni frames. Respuesta 200 = detalle actualizado. Errores: 404 (no existe, otro tenant o no asignada); 409 (no `scheduled`).

**`POST /sessions/{id}/vision-counts`** (OP asignado, v1 con el JWT del usuario) — **una llamada = un snapshot/lote completo**, no un evento por familia.

| Campo | Tipo | Nulo | Significado |
| :--- | :--- | :--- | :--- |
| `client_event_id` | uuid | NO | Idempotencia (`count_event.client_event_id`, UNIQUE). |
| `occurred_at` | string ISO | NO | Momento del resultado. |
| `model_version` | string | NO | `yolo_model.version_tag` (DS03). |
| `trigger` | string | NO | `stable_window` \| `final`. |
| `counts` | array | NO | Una entrada por familia del inventario esperado detectada; cada familia una sola vez. |
| `counts[].family_id` | uuid | NO | Familia del inventario esperado de la sesión (otra → 422). |
| `counts[].detected_quantity` | int | NO | ≥ 0. |

```json
{
  "client_event_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
  "occurred_at": "2026-09-05T18:20:00Z",
  "model_version": "yolo26l-demo",
  "trigger": "final",
  "counts": [
    { "family_id": "41414141-0000-4000-8000-000000000010", "detected_quantity": 2 }
  ]
}
```

Condiciones: `status = in_progress` y `capture_mode = vision` (si no → 409). Persiste **un** `auto_count` (DS04: `family_id` null, payload normalizado `{model_version, trigger, counts[]}`, `user_id` null). Primer envío → **201**; replay idéntico (mismo `client_event_id`, sesión y contenido) → **200** con el mismo `count_event_id`; `client_event_id` reutilizado con otra sesión o contenido → **409**. No acepta boxes, frames, JPEG/base64, `video_t`, `frames_processed` ni metadatos de upload/cámara (campos desconocidos → 400).

```json
{ "count_event_id": "ce000001-0000-4000-8000-000000000001", "client_event_id": "dddddddd-dddd-dddd-dddd-dddddddddddd", "trigger": "final", "occurred_at": "2026-09-05T18:20:00Z" }
```

**`POST /sessions/{id}/count-report`** (OP asignado) — reporte final y validación humana, para `manual_no_privacy` y `vision`.

| Campo | Tipo | Nulo | Significado |
| :--- | :--- | :--- | :--- |
| `reports` | array | NO | Cada familia del inventario esperado exactamente una vez. |
| `reports[].family_id` | uuid | NO | |
| `reports[].suggested_quantity` | int | SÍ | Sugerencia de IA mostrada al operador. Null en `manual_no_privacy`. |
| `reports[].reported_quantity` | int | NO | Cantidad final del humano (≥ 0). |
| `reports[].reason_code` | string | SÍ | Obligatorio solo si `reported ≠ expected`; código de `cat_discrepancy_reason` (DS04). `other` no es válido. |
| `reports[].notes` | string | SÍ | Justificación de la corrección humana (v0.1.6): **obligatoria** (no vacía ni solo espacios) si `suggested` no es null y `reported ≠ suggested`, y se guarda como `human_correction.justification`; **debe ser null/ausente** en cualquier otro caso (IA = humano, sin resultado `final`, sesión `manual_no_privacy`). No se guarda en otro sitio. |

```json
{
  "reports": [
    { "family_id": "41414141-0000-4000-8000-000000000010", "suggested_quantity": 1, "reported_quantity": 2, "reason_code": null, "notes": "Second piece was under a gauze" }
  ]
}
```

Reglas (el servidor es autoritativo):

1. **Sugerencia de IA:** en `vision`, si existe un `auto_count` con `trigger = final`, `suggested_quantity` es obligatorio y debe coincidir con el valor almacenado de esa familia (0 si no aparece en el lote); si no coincide → **409**. Si no hay resultado `final` (visión no disponible), `suggested_quantity` debe ser null. En `manual_no_privacy` debe ser null.
2. **AI ≠ humano** → `human_correction` sobre el `manual_count` de esa familia.
3. **Humano ≠ esperado** → `discrepancy` (`shortage`/`surplus` según el signo; `origin_event_id` = `manual_count`). **Humano = esperado** → sin discrepancia.
4. Persiste `manual_count` por familia (`detected_quantity` = reportado, `expected_quantity`, `user_id`) y `manual_close`; fija `ended_at`; estado → `correction_required` si hay discrepancias, si no `awaiting_spd_review`.

Respuesta 200:

```json
{ "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "status_code": "awaiting_spd_review", "open_discrepancies": 0, "human_corrections": 1 }
```

Errores: 400 (familias faltantes/duplicadas, cantidades negativas, `reason_code` o `notes` faltantes, `notes` presente sin corrección humana, `suggested_quantity` en sesión manual); 404; 409 (estado ≠ `in_progress`, o `suggested_quantity` distinto del resultado de visión almacenado); 422 (`reason_code` fuera de catálogo).

**`GET /sessions/{id}/counts`** — orden `occurred_at`; a igual `occurred_at`, prioridad de tipo (v0.1.6): `session_open` → hechos de la sesión (`auto_count`, `manual_count` y cualquier otro tipo) → `manual_close` → `session_close`; por último `id`. Así los `manual_count` de un reporte (mismo instante) aparecen siempre antes de su `manual_close`. Estructura fija (campos no aplicables en null o lista vacía) para equivalencia JSON/XML; el `payload` crudo no se expone.

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `id` | uuid | `count_event.id` |
| `event_type_code` | string | DS04 |
| `occurred_at` | string ISO | |
| `user_id` | uuid\|null | |
| `client_event_id` | uuid\|null | |
| `family_id`, `family_code` | uuid\|null, string\|null | Eventos por familia |
| `expected_quantity`, `detected_quantity` | int\|null | En `manual_count`, `detected_quantity` = reportado |
| `reason_code` | string\|null | `manual_count` |
| `model_version`, `trigger` | string\|null | `auto_count` (`trigger` null en datos históricos) |
| `counts` | array | `auto_count`: `[{family_id, detected_quantity}]` |
| `human_correction` | object\|null | `{justification, user_id, recorded_at}` |

**`GET /sessions/{id}/discrepancies`** — orden: abiertas primero, `family_code`, `id`. Texto libre sin traducir.

```json
[
  {
    "id": "di000001-0000-4000-8000-000000000001",
    "reason_code": "shortage",
    "family_id": "41414141-0000-4000-8000-000000000010",
    "family_code": "MAYOHEG",
    "expected_quantity": 2,
    "detected_quantity": 1,
    "resolved": false,
    "resolved_at": null,
    "origin_event_id": "ce000001-0000-4000-8000-000000000001",
    "description": "Shortfall on Mayo-Hegar needle holder: expected 2, reported 1."
  }
]
```

**`POST /sessions/{id}/discrepancies/{discrepancy_id}/resolve`** (SPD del mismo tenant; sesión en `correction_required`)

| Campo | Tipo | Nulo | Significado |
| :--- | :--- | :--- | :--- |
| `notes` | string | NO | No vacío ni solo espacios; se agrega a `description` sin traducir. |
| `outcome` | string | NO | `resolved` \| `lost` \| `recovered`. |
| `instrument_id` | uuid | condicional (v0.1.7) | Pieza física (`instrument.id`, DS03 §1.3.9.1). **Obligatorio** con `lost`/`recovered`; **debe ser null/ausente** con `resolved`. El servidor nunca elige una pieza por familia. |

```json
{ "notes": "Found in the auxiliary tray", "outcome": "resolved" }
```

```json
{ "notes": "Not found after full tray and room check", "outcome": "lost", "instrument_id": "i1000001-0000-4000-8000-000000000001" }
```

```xml
<resolve_request>
  <notes>Not found after full tray and room check</notes>
  <outcome>lost</outcome>
  <instrument_id>i1000001-0000-4000-8000-000000000001</instrument_id>
</resolve_request>
```

`notes` obligatorio (se agrega a `description` sin traducir). `lost` / `recovered` aplican a la pieza indicada exactamente las transiciones de PEF-DS03 §1.3.9.1 (`lost`: `available`/`reserved`/`in_use` activa → `lost`, `active = FALSE`, liberación de la reserva activa de esa pieza; `recovered`: `lost` → `available`, `active = TRUE`, sin crear reservas), con un `instrument_cycle_event` cada una. Errores de la pieza: `instrument_id` ausente con `lost`/`recovered`, presente con `resolved` o no UUID → **400**; pieza inexistente, de otra institución o de otra familia que la discrepancia (o discrepancia sin familia) → **422** `invalid_reference` (mismo mensaje, sin revelar la institución); estado de origen no permitido → **409** (`details.cycle_status_code`). Validaciones: `discrepancy.session_id = {id}`; otra sesión/tenant → **404**; ya resuelta → **409**; sesión no en `correction_required` (incluido `awaiting_spd_review`) → **409**. Última abierta → `awaiting_spd_review`; si quedan → sigue `correction_required`.

```json
{ "discrepancy_id": "di000001-0000-4000-8000-000000000001", "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "open_remaining": 0, "session_status_code": "awaiting_spd_review" }
```

**`POST /sessions/{id}/close`** (SPD; `awaiting_spd_review`)

```json
{ "material_recovered": true }
```

Precondiciones: `material_recovered = true` (si no → 400); 0 discrepancias abiertas (si no → 409, `details.open_discrepancies`); estado `awaiting_spd_review` (si no → 409). Efectos: `closed`, `closed_by_user_id`, `ended_at` si faltaba, evento `session_close`; efectos DS03 transitorios (PEF-DS03 §1.3.9.1): se liberan las reservas activas de la operación de la sesión cuyas piezas son del tenant; de esas piezas, solo las `active` en `available`/`reserved`/`in_use` pasan a `sterilization` (un `instrument_cycle_event` cada una); las demás conservan su estado. Respuesta 200 = detalle con `status_code: closed`.

**XML (Session):** mismas reglas que Catalog. Raíces: `sessions/session` (lista), `session` (detalle), `expected_inventory/line`, `counts/count` (con `counts/count` anidado para el lote), `discrepancies/discrepancy`, request `session_request` (`POST /sessions`, líneas en `expected_lines/line`), request `count_report/reports/report`, request `vision_counts` (con `counts/count`), request `expected_inventory_update/lines/line`, `start_request` vacío opcional, `resolve_request`, `close_request`, respuesta `count_report_result`, `vision_count_result`, `resolve_result`, `error_response`.

```xml
<count_report>
  <reports>
    <report>
      <family_id>41414141-0000-4000-8000-000000000010</family_id>
      <suggested_quantity>1</suggested_quantity>
      <reported_quantity>2</reported_quantity>
      <reason_code xsi:nil="true" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"/>
      <notes>Second piece was under a gauze</notes>
    </report>
  </reports>
</count_report>
```

```xml
<vision_counts>
  <client_event_id>dddddddd-dddd-dddd-dddd-dddddddddddd</client_event_id>
  <occurred_at>2026-09-05T18:20:00Z</occurred_at>
  <model_version>yolo26l-demo</model_version>
  <trigger>final</trigger>
  <counts>
    <count>
      <family_id>41414141-0000-4000-8000-000000000010</family_id>
      <detected_quantity>2</detected_quantity>
    </count>
  </counts>
</vision_counts>
```

---

> **Complemento v0.2.0 (operaciones del Supervisor, etapa 8).** SessionService asume las operaciones de sesión del Supervisor que el portal ejecutaba en proceso; el flujo de conteo del Operador permanece en el portal. Los contratos v0.1.7 se conservan (lecturas y `POST /sessions`); los cambios son aditivos salvo lo indicado. OpenAPI ejecutable: `GET /api/v1/sessions/docs`; XSD de peticiones: `GET /api/v1/sessions/schemas/session-requests.xsd`; `GET /health`.

| Método | Endpoint | Propósito | Acceso |
| :--- | :--- | :--- | :--- |
| POST | `/sessions/schedule` | Programar: operation + paciente + equipo quirúrgico + work_session + expected_inventory (+ acuerdo DS07 opcional), una transacción | `spd_supervisor` |
| PUT | `/sessions/{id}` | Edición completa mientras `scheduled` | `spd_supervisor` |
| PUT | `/sessions/{id}/privacy-agreement` | Acuerdo de privacidad (Vía A) mientras `scheduled` y sin `capture_mode` | `spd_supervisor` |
| PUT | `/sessions/{id}/expected-inventory` | + `physically_added`: incorporaciones confirmadas → evento `instruments_added` | `spd_supervisor` |
| GET | `/sessions/discrepancies` | Cola de discrepancias de la institución (`status=open\|resolved`) | `spd_supervisor` |
| GET | `/sessions?expand=detail`, `/sessions/{id}?expand=detail` | Instantánea de sesión con que el portal construye las pantallas RF (filtro `status`, paginación `page`/`per_page` con cabeceras `X-Total-*`) | JWT (alcance por rol) |

**Reglas (las del portal).** Programación: operador activo con `station_operator` efectivo, sala de la institución y estación de esa sala, kit, paciente y médicos (≥1, únicos, activos) de la institución, rol quirúrgico conocido, procedimiento y fase existentes, líneas 1..32767 dentro del stock disponible (validación puntual, sin reserva). Incorporación confirmada (`physically_added`, solo `in_progress`): los aumentos sobre la cantidad guardada se registran en un `instruments_added` (expected = total previo, detected = agregado, payload `added[]`, `confirmed_by_role`, `reference_before`) que eleva la referencia de conteo (conteo inicial YOLO + incorporaciones posteriores); reenviar las mismas cantidades no suma nada; una edición sin la confirmación es administrativa y nunca cambia la referencia. Resolución: en `correction_required` o `awaiting_spd_review` (nunca tras el cierre); sin `instrument_id`, `lost` marca una pieza en servicio de la familia (primero la reservada para la operación) y `recovered` reactiva una pieza perdida o lo anota; la última resolución pasa a `awaiting_spd_review`. Cierre SPD: solo `awaiting_spd_review`, sin discrepancias abiertas, `material_recovered` y, tras un conteo de contingencia, `contingency_reviewed`; un segundo cierre → 409.

**Errores de negocio** (`details.reason`): 404 `session_not_found`, `discrepancy_not_found`; 409 `not_scheduled`, `privacy_not_scheduled`, `capture_mode_frozen`, `inventory_locked`, `session_not_reviewable`, `already_resolved`, `not_awaiting_spd_review`, `open_discrepancies`; 400/422 `invalid_operator`, `operator_role_required`, `invalid_room`, `invalid_station`, `invalid_kit`, `invalid_patient`, `invalid_procedure`, `physicians_required`, `physician_required`, `duplicate_physician`, `invalid_physician`, `invalid_surgical_role`, `invalid_phase`, `invalid_scheduled_at`, `lines_required`, `invalid_quantity`, `stock_block`, `invalid_privacy_notice`, `notes_required`, `material_not_recovered`, `contingency_review_required`, `xsd_validation_failed`.

**Cambios de compatibilidad.** (1) `POST /start`, `/vision-counts` y `/count-report` responden **410** (`details.reason = operator_flow_in_portal`) salvo `SESSION_OPERATOR_WRITES_ENABLED=true`: el conteo del Operador (referencia inicial/final, `live_add`, contingencia) vive en el portal y estos endpoints serían una segunda implementación más antigua. (2) `resolve` admite `awaiting_spd_review` y ya no exige `instrument_id` para `lost`/`recovered`; la nota anexa usa `marked lost`/`marked recovered`. (3) Un 403 por rol escribe `access_audit` `DENIED_<MÉTODO>_WORK_SESSION` (outcome `denied`). (4) Los cuerpos XML se validan contra el XSD de peticiones.

**Concurrencia con el Operador.** Toda escritura del Supervisor bloquea primero la fila `work_session` (`SELECT … FOR UPDATE`), la misma fila que bloquean las transacciones del Operador en el portal (inicio, finalización YOLO, reporte/validación): una edición y un reporte nunca se intercalan; el segundo espera y evalúa el estado ya confirmado.

> **Complemento v0.3.1 (cierre seguro y faltante físico).** Un **faltante físico** es una discrepancia con motivo `shortage`: el conteo humano confirmado quedó por debajo de lo esperado o de la referencia. Un error de clasificación que el humano corrigió no genera discrepancia.

- **Resolución:** `outcome = resolved` (solo nota) responde 409 `physical_shortage_needs_piece_outcome`. Se usa `lost` o `recovered`, que dejan efecto y traza en el inventario.
- **Cierre:** el sistema no registra qué pieza concreta entró a la cirugía ni tiene un reconteo posterior al reporte del Operador. Por eso ninguna resolución documenta que el instrumento faltante se recuperó físicamente, y una sesión con faltante físico **nunca** es cierre seguro:
  - `POST /sessions/{id}/close` exige `unreconciled_shortage_acknowledged: true`; sin él responde 409 `unreconciled_physical_shortage` con `details.discrepancy_ids`;
  - el evento `session_close` queda con `safe_close_confirmed = false` y `unreconciled_physical_shortages` (ids).
- **Sin faltante físico:** el cierre seguro no cambia (`safe_close_confirmed = true`, `unreconciled_physical_shortages = []`).
- **Respuesta del cierre:** sigue siendo el detalle de la sesión (contrato v0.1.7).

> **Complemento v0.3.0 (reportes del Supervisor, etapa 12).** Consultas de solo lectura sobre las sesiones, discrepancias y hechos de conteo de la institución del usuario. Cada consulta corre en una transacción PostgreSQL `READ ONLY`, y el aislamiento por institución va en el `WHERE` de cada consulta. Acceso: `spd_supervisor`; otro rol → 403, registrado como `DENIED_GET_WORK_SESSION`. XSD de las respuestas XML: `GET /api/v1/sessions/schemas/session-reports.xsd`.

| Método | Endpoint | Propósito |
| :--- | :--- | :--- |
| GET | `/sessions/reports/summary` | Sesiones por estado, duración promedio, % con discrepancias, discrepancias (totales, por motivo, por familia, tiempo de resolución), acuerdo IA–humano y actividad por día |
| GET | `/sessions/reports/kits` | Actividad por kit (paginado): sesiones, versión actual, cantidades esperadas y reportadas, sesiones con discrepancias |
| GET | `/sessions/reports/operators` | Actividad por Operador (paginado): sesiones, conteos completados, familias contadas, correcciones humanas, discrepancias, acuerdo IA–humano |
| GET | `/sessions/reports/options` | Kits y operadores de la institución y códigos de estado (para los filtros) |

**Filtros:**
- `from` / `to`: fecha ISO (día completo en `tz`) o fecha-hora; por defecto los últimos 30 días; ventana máxima de 366 días.
- `tz`: zona IANA; por defecto UTC.
- `kit_id` y `operator_id`: deben pertenecer a la institución; si no, 400 sin revelar datos.
- `status`: código de estado de la sesión.
- `page` / `per_page` (≤ 100): en kits y operadores.

**Definiciones:**
- **Periodo:** `operation.scheduled_at` en [from, to).
- **Duración:** `ended_at − started_at` (del inicio al reporte del Operador).
- **% con discrepancias:** sesiones reportadas con discrepancias / sesiones reportadas.
- **Tiempo de resolución:** `resolved_at − COALESCE(evento de origen, inicio de la sesión)`.
- **Cantidad reportada:** último `manual_count` de cada sesión y familia.
- **Acuerdo IA–humano:** cada `manual_count` de visión comparado con la sugerencia de YOLO que validó (`payload.ai_suggested_quantity` o el último lote `auto_count` final), **no** con el inventario esperado. Es la misma regla del panel de Administración. Con menos de 5 pares es `null`.
- **Corrección humana:** un par cuyas cantidades difieren.
- **Métricas sin denominador:** son `null`, nunca 0.

Errores: 400 `invalid_request` (`details.field`, `details.unknown_parameters`), 401, 403, 503.

#### 1.3.5 Evidence / File Service

Base: `/api/v1` — **Evidence API v1 (documento v0.1.8)**. Datos y reglas de almacenamiento: PEF-DS05 v0.2.0 (§1.3.1 tabla `media_asset`, §1.3.2 evidencia clínica). Solo evidencia **clínica** (`media_asset.session_id` no nulo); los assets DS03/MLOps (`session_id` nulo) están fuera de esta API.

| Método | Endpoint | Rol | Resultado |
| :--- | :--- | :--- | :--- |
| POST | `/evidence` | OP asignado (v1) | Sube un JPEG/GIF de la sesión → 201 metadata |
| GET | `/evidence/{id}` | OP asignado, SPD | Metadata + URL firmada temporal (si disponible) |
| GET | `/sessions/{id}/evidence` | OP asignado, SPD | Lista de metadata de la sesión, sin URLs |

No hay `DELETE`, ni endpoints de vídeo, bloqueo o purga en v1 (la purga/retención es un job interno, PEF-DS05 §1.3.2 regla 12).

**Reglas comunes**

1. **Auth y roles:** igual que Session (Bearer, algoritmo exacto `JWT_ALGORITHM`, sesión Redis `auth:jwt:{jti}`, usuario e institución activos, roles efectivos desde `user_role` → `role`). `station_operator`: solo evidencia de sesiones asignadas a él (`work_session.user_id`). `spd_supervisor`: evidencia de las sesiones de su tenant. `it_admin` u otro rol → **403**. Multi-rol: alcance de supervisor para lectura.
2. **Tenant:** `tenant(asset)` = `media_asset.session_id` → `work_session.operation_id` → `operation.institution_id`; debe coincidir con `user.institution_id`. Nunca se autoriza por `object_key`, `bucket` ni URI. Asset o sesión inexistente, de otro tenant, no asignada (OP), sin operación o asset sin sesión → **404** indistinguible.
3. **Subida v1 con contexto humano (transitorio):** `POST /evidence` usa el Bearer de `station_operator` asignado, igual que `POST /sessions/{id}/vision-counts`. La identidad máquina de PEF-DS08 (`integration_client`) reemplazará después este contexto del productor.
4. **Privacidad y estado (PEF-DS05 §1.3.2):** solo sesiones con `capture_mode = vision` (si no → **409**, `details.capture_mode`) y en `in_progress`, `correction_required` o `awaiting_spd_review` (si no → **409**, `details.status_code`). No se revalida el aviso de privacidad en cada subida; los cambios posteriores se aplican por bloqueo/purga.
5. **El servidor es autoritativo:** genera `id`, `object_key` (`{institution_id}/{session_id}/{yyyy}/{uuid}.{ext}`), `bucket`, `storage_provider`, `gcs_uri`, `sha256`, `size_bytes`, `content_type`, `retention_until`. El cliente no puede enviarlos.
6. **Formatos:** la subida es `multipart/form-data` (binario; no JSON/XML). Las respuestas de metadata y los errores son JSON o XML según `Accept` (mismas reglas que Catalog/Session). `X-Correlation-ID` según §1.3.1 (máx. 64): se refleja en header, errores, logs y `access_audit`; no se escribe en el objeto del storage.
7. **Auditoría** (`access_audit`, `resource_type = media_asset`, `resource_id` = asset, `institution_id` = tenant, misma transacción que el alta): `evidence.upload` (alta), `evidence.download_url` (cada emisión de URL firmada). La lista no se audita. `evidence.block` / `evidence.purge` quedan reservadas para el job/flujo DS07.
8. **Health:** `/health/live`, `/health/ready` (PostgreSQL + Redis + storage), `/health/database`, `/health/redis`, `/health/storage` (§1.3.7). Sin dependencia de Mongo, VisionWorker ni de Session/Catalog por HTTP: lee el PostgreSQL compartido.

**`POST /evidence`** — `Content-Type: multipart/form-data`

| Parte | Tipo | Obligatoria | Significado |
| :--- | :--- | :--- | :--- |
| `session_id` | texto (UUID) | Sí | Sesión dueña de la evidencia. |
| `kind` | texto | Sí | `jpeg` \| `gif`. |
| `file` | binario | Sí | Contenido; su tipo real (firma de bytes) debe coincidir con `kind`. El nombre/extensión del archivo se ignoran. |

Cualquier otra parte (p. ej. `institution_id`, `object_key`, `bucket`, `storage_provider`, `sha256`, `size_bytes`, `retention_until`, `reason`) → **400**. `reason` pertenece a `checkpoint_frame` (PEF-DS06), que referencia el `media_asset.id` devuelto.

Límites v1 (configurables por entorno; no son constraints de PG): JPEG ≤ 2 MiB, GIF ≤ 5 MiB; si se superan → **413**. La subida se procesa en streaming/archivo temporal.

Orden del alta (PEF-DS05 §1.3.2 regla 10): validar → generar clave → PUT del objeto → verificar tamaño y SHA-256 → transacción PG (`media_asset` + `evidence.upload`) → COMMIT; si la transacción falla, se borra el objeto (compensación) y la respuesta es 5xx.

Respuesta **201** = Evidence metadata (con `signed_url: null`; la URL se obtiene del detalle).

**Evidence metadata** (POST 201, detalle e ítems de lista; estructura fija para equivalencia JSON/XML)

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `id` | uuid | `media_asset.id` |
| `session_id` | uuid | Sesión dueña |
| `kind` | string | `jpeg` \| `gif` |
| `content_type` | string | `image/jpeg` \| `image/gif` (detectado por el servidor) |
| `size_bytes` | int | Tamaño calculado en servidor |
| `sha256` | string | 64 hex en minúsculas, calculado en servidor |
| `storage_provider` | string | `gcs` \| `minio` \| `other` (`other` = Garage en el despliegue actual) |
| `retention_until` | string ISO\|null | `created_at` + 90 días (+180 si `work_session.extended_retention`) |
| `created_at` | string ISO | |
| `blocked` | boolean | `blocked_at` no nulo |
| `purged` | boolean | `purged_at` no nulo |
| `signed_url` | string\|null | Solo en `GET /evidence/{id}`; null en lista, en POST y si `blocked`/`purged` |
| `signed_url_expires_at` | string ISO\|null | Expiración de `signed_url` |

Nunca se devuelven `bucket`, `object_key`, `gcs_uri`, endpoints internos, credenciales ni rutas de archivo.

```json
{
  "id": "m1000001-0000-4000-8000-000000000001",
  "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "kind": "jpeg",
  "content_type": "image/jpeg",
  "size_bytes": 245760,
  "sha256": "a3f5c8e2b91d0476f0aa1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcd",
  "storage_provider": "other",
  "retention_until": "2026-12-04T18:20:00Z",
  "created_at": "2026-09-05T18:20:01Z",
  "blocked": false,
  "purged": false,
  "signed_url": "https://storage.example/evidence/...(firma, 300 s)",
  "signed_url_expires_at": "2026-09-05T18:25:01Z"
}
```

**`GET /evidence/{id}`** — metadata; si el asset no está bloqueado ni purgado, `signed_url` temporal (GET del objeto) válida 300 s (configurable), firmada directamente contra el endpoint **público** del storage (`S3_PUBLIC_ENDPOINT`; las operaciones internas usan `S3_INTERNAL_ENDPOINT`; nunca se reescribe el host de una URL firmada). Cada emisión → `evidence.download_url`. Bloqueado/purgado → 200 con `signed_url: null` (no se sirven bytes). La API nunca transmite los bytes; la URL firmada es un token al portador de vida corta.

**`GET /sessions/{id}/evidence`** — mismas reglas de acceso que la sesión (§1.3.4: OP asignado o SPD del tenant; 404 indistinguible). Lista de Evidence metadata ordenada por `created_at` ASC, `id` ASC; incluye assets bloqueados/purgados (metadata) y `signed_url`/`signed_url_expires_at` siempre null; sin auditoría; sin paginación en v1.

**XML (Evidence):** mismas reglas que Catalog. Raíces: `evidence` (POST 201 y detalle), `evidence_list/evidence` (lista), `error_response`, `health`. La subida no tiene representación XML (multipart).

**Errores**

| HTTP | `error` | Caso |
| :--- | :--- | :--- |
| 400 | `invalid_request` | Multipart mal formado, partes faltantes/desconocidas, `session_id` no UUID, `kind` fuera de `jpeg`/`gif`, archivo vacío |
| 401 | `unauthorized` | Token ausente/inválido |
| 403 | `forbidden` | Rol sin permiso (p. ej. `it_admin`; SPD-only en POST) |
| 404 | `not_found` | Asset/sesión inexistente, de otro tenant, no asignada (OP) o asset sin sesión |
| 409 | `conflict` | `capture_mode` ≠ `vision`; estado de sesión no admitido |
| 413 | `payload_too_large` | Archivo por encima del límite de su tipo |
| 422 | `validation_error` | El contenido real no corresponde a `kind` (firma de bytes) |
| 500 | `internal_error` | Fallo interno (alta revertida y objeto compensado) |
| 503 | `dependency_unavailable` | PostgreSQL, Redis o storage no disponibles (`details.dependency`) |

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

> **Complemento v0.1.0 (Audit / Event Service, etapa 9).** Implementado en `apps/AuditService` (OpenAPI ejecutable: `GET /api/v1/audit/docs`; XSD de las respuestas XML: `GET /api/v1/audit/schemas/audit.xsd`; `GET /health`). Es una **API de lectura** sobre los registros reales: no crea una segunda bitácora. Los productores (Auth, Catalog, Session, Evidence y el portal) siguen escribiendo `access_audit` (DS07) y `count_event` (DS04) dentro de sus transacciones; AuditService los lee en transacciones `READ ONLY`. No hay endpoint de inserción (ningún productor lo necesita): el historial es inmutable y una corrección es otro evento de su productor.

| Método | Endpoint | Propósito |
| :--- | :--- | :--- |
| GET | `/api/v1/audit/events` | Búsqueda paginada (`page`, `per_page` ≤ 100, `sort=-occurred_at\|occurred_at`) |
| GET | `/api/v1/audit/events/{id}` | Un evento (fila de `access_audit` o de `count_event`) |
| GET | `/api/v1/audit/events/summary` | Agregados por categoría, origen, resultado, tipo de registro, acción, recurso, usuario y sesión |

Filtros: `institution_id` (solo la propia; otra → 403 `out_of_scope`), `user_id`, `action`, `category` (`security\|business\|operation`), `outcome` (`success\|denied\|blocked`), `source_service` (`auth\|catalog\|session\|evidence\|web\|audit`), `record_type` (`access_audit\|count_event`), `resource_type`, `session_id`, `correlation_id`, `from` / `to` (ISO-8601), `q`. Parámetros desconocidos o inválidos → 400 (`details.field`, `details.unknown_parameters`).

**Evento** (amplía el `Audit event` de arriba): `id`, `record_type`, `occurred_at`, `category`, `source_service`, `action`, `outcome`, `actor {type, user_id, name, email, role_codes}`, `institution_id`, `resource {type, id, label}`, `session_id`, `correlation_id`, `ip`, `details` (solo `count_event`: cantidades, familia y claves del hecho — `trigger`, `reason`, `scope`…; nunca cajas, imágenes, notas ni texto libre). `source_service` es el dueño del dominio de la fila (ninguna tabla registra el proceso que la escribió). `count_event.close_blocked` → `outcome = blocked`.

**Alcance aplicado por el servicio:** `it_admin` → todos los eventos de su institución; `spd_supervisor` → traza operativa de su institución (recursos `work_session`, `instrument`, `kit` y todos los `count_event`); otro rol → 403 y `DENIED_GET_AUDIT_EVENT`. El correo del actor solo para `it_admin`. Eventos nuevos de AuthService: `LOGIN` (misma transacción que `last_login_at`), `LOGIN_FAILED` (cuenta existente, outcome `denied`) y `LOGOUT`, con `resource_type = user` e IP de la conexión.

**Notification**

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `id` | uuid | |
| `title` | string | |
| `body` | string | |
| `read` | boolean | |
| `created_at` | string | |

> **Complemento v0.1.0 (Notifications Service, etapa 10).** Implementado en `apps/NotificationsService` (OpenAPI ejecutable: `GET /api/v1/notifications/docs`; XSD: `GET /api/v1/notifications/schemas/notifications.xsd`; `GET /health` con cabecera `X-Outbox-Pending`). Notificaciones internas **del usuario autenticado**: el destinatario es siempre el usuario del token y solo se ven las de su institución actual (la de otro usuario → 404). **No hay endpoint de creación**: las genera el proceso `worker.py` a partir de hechos ya persistidos (`access_audit`, `count_event`), capturados por triggers en `notification_outbox` dentro de la transacción del productor. Ningún productor llama al servicio; si está detenido, los eventos esperan en el outbox y se procesan al reiniciar, sin duplicados (`UNIQUE (source_table, source_id, recipient_user_id, type)`).

| Método | Endpoint | Propósito |
| :--- | :--- | :--- |
| GET | `/api/v1/notifications` | Lista paginada (`status=unread\|read`, `type`, `from` / `to` ISO-8601, `page`, `per_page` ≤ 100, `sort=-created_at\|created_at`); incluye `unread` total |
| GET | `/api/v1/notifications/{id}` | Una notificación propia |
| GET | `/api/v1/notifications/unread-count` | `{unread}` |
| PATCH | `/api/v1/notifications/{id}/read` | Marca leída (idempotente; `read_at` del primer marcado) |
| PATCH | `/api/v1/notifications/read-all` | Marca leídas todas, o solo `type` / anteriores a `before` (JSON o XML `read_all_request`, validado con XSD) → `{updated, unread}` |

**Notificación** (amplía la tabla de arriba): `id`, `type` (`session_scheduled\|discrepancy_pending\|correction_required\|discrepancy_resolved\|session_closed`), `status` (`unread\|read`; equivale a `read`), `title`, `message` (equivale a `body`), `session_id`, `data {session_label, scheduled_at?, open_discrepancies?}`, `created_at`, `read_at`. Nunca datos de paciente, médico ni texto libre.

| Evento de origen | Tipo | Destinatarios |
| :--- | :--- | :--- |
| `access_audit` `session.scheduled` | `session_scheduled` | Operador asignado |
| `count_event` `manual_close` con `next_status = correction_required` | `discrepancy_pending` | Supervisor que programó la sesión (si no se conoce: Supervisores SPD activos de la institución) |
| (mismo evento) | `correction_required` | Operador asignado |
| `access_audit` `discrepancy.resolve` | `discrepancy_resolved` | Operador asignado (uno por sesión mientras siga sin leer) |
| `count_event` `session_close` | `session_closed` | Operador asignado y Supervisor que programó, excepto quien cerró |

El actor de un evento no recibe aviso de su propia acción (salvo `correction_required`). Errores: 400 `invalid_request` (`details.field`, `details.unknown_parameters`, `details.reason = xsd_validation_failed`), 401 `unauthorized`, 404 `not_found`, 405, 413, 503 `dependency_unavailable`.

**Monitor service status**

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `name` | string | Nombre del MS |
| `status` | string | up\|degraded\|down |
| `latency_ms` | number\|null | |
| `checked_at` | string | |

> **Complemento v0.1.0 (Monitoring Service, etapa 11).** Implementado en `apps/MonitoringService` (OpenAPI ejecutable: `GET /api/v1/monitoring/docs`; XSD de las respuestas XML: `GET /api/v1/monitoring/schemas/monitoring.xsd`; `GET /health` con cabecera `X-Last-Run-Age-Seconds`). API **de solo lectura**: las comprobaciones las hace el proceso `collector.py` (compose `monitoring-collector`) cada `MONITORING_INTERVAL_SECONDS` (30 s por defecto), en paralelo y con timeout por componente, y se guardan en PostgreSQL (`monitoring_run`, `monitoring_check`; migración `007_monitoring.sql`; retención `MONITORING_RETENTION_DAYS` = 7 días). Ningún otro componente llama al servicio.

| Método | Endpoint | Propósito | Acceso |
| :--- | :--- | :--- | :--- |
| GET | `/api/v1/monitoring/services` | Estado actual de cada componente | `it_admin` (detalle técnico) y `spd_supervisor` (`name`, `label`, `status`, `checked_at`, `freshness`) |
| GET | `/api/v1/monitoring/services/{name}` | Detalle, últimas comprobaciones (`recent` ≤ 100) y agregados de 24 h | `it_admin` |
| GET | `/api/v1/monitoring/history` | Comprobaciones guardadas (`component`, `status`, `from` / `to`, `page`, `per_page` ≤ 200, `sort=-checked_at\|checked_at`) | `it_admin` |
| GET | `/api/v1/monitoring/summary` | Agregados por componente en una ventana (`from` / `to`, por defecto 24 h, máx. 31 días; `component`; `bucket=1m\|5m\|15m\|1h\|6h\|1d` añade una serie) | `it_admin`; `spd_supervisor` solo disponibilidad |

**Componentes:** `web`, `auth`, `catalog`, `session`, `evidence`, `audit`, `notifications`, `notifications-worker`, `vision`, `postgres`, `redis`, `mongo`, `garage`. **Estados** (amplían `up|degraded|down`): `unknown` = no configurado, nunca comprobado, o última comprobación más antigua que `3 × intervalo` (`freshness = stale`); un estado no verificado recientemente nunca se presenta como disponible.

**Comprobación** (`monitoring_check`): `id`, `run_id`, `component`, `checked_at`, `status`, `latency_ms` (medida real del viaje de la comprobación), `http_status`, `dependencies [{name, status}]` (los `checks` que reporta el componente), `error_type` (`timeout`, `connect_timeout`, `connection_refused`, `dns_error`, `connection_error`, `dependency_failed`, `unexpected_status`, `invalid_response`, `slow_response`, `model_weights_missing`, `process_not_connected`, `backlog_stalled`, `not_configured`…), `endpoint` (`scheme://host:port/path`, nunca credenciales). **Agregados:** `availability_pct = (up + degraded) / (checks − unknown)`, `up_pct = up / (checks − unknown)`, latencia `avg / p95 / min / max` sobre las comprobaciones respondidas. Errores: 400 `invalid_request` (`details.field`, `details.unknown_parameters`), 401, 403 `forbidden`, 404 (componente inexistente), 405, 503.

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
| Versión | 0.1.8 (0.1.1: complemento Catalog — request POST /families, ítems de /instruments y /kits, listas, XML; 0.1.2: complemento Auth API v1 — refresh, logout, me, permissions, XML, errores, health; 0.1.3: `refresh_token` obligatorio en `POST /logout`; 0.1.4: Session API v1 completa — operación preexistente, roles/tenant, start, expected-inventory, vision-counts, count-report, resolve, close, XML, concurrencia/idempotencia; 0.1.5: `X-Correlation-ID` máximo 64 caracteres sin truncamiento (UUID nuevo si falta/inválido/>64) y raíz XML `session_request` de `POST /sessions`; 0.1.6: sin `access_audit` por snapshot de `vision-counts`, `notes` solo con corrección humana, orden de `GET /counts` a igual `occurred_at`; 0.1.7: `instrument_id` condicional en `resolve` para `lost`/`recovered` y referencias a las transiciones de PEF-DS03 §1.3.9.1; 0.1.8: Evidence API v1 completa — `POST /evidence` multipart, detalle con URL firmada de 300 s, lista sin URLs, roles/tenant por sesión, privacidad, estados, errores, auditoría y XML) |
| Estado | draft |
| Fuentes | `Definicion APIs Modulos Microservicios.docx.md`, `arquitectura_servicios_y_cobertura.md` |
| Fuera de alcance | WSS visión (DS10); DDL de tablas |
