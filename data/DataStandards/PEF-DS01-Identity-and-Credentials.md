---
id: PEF-DS01
title: Identity and Credentials
version: "0.2.1"
status: draft
owner: PEF Core / Backend Web
applies_to: [WP, BE, CS]
stores: [PostgreSQL, Redis]
related: [PEF-DS02, PEF-DS07, PEF-DS08]
last_updated: 2026-09-06
---

# PEF-DS01 · Identity and Credentials

## INTRODUCCION

Este estándar define **quién puede entrar al sistema** y **cómo se guarda la identidad humana**.

Cubre estas tablas PostgreSQL: `institution`, `role`, `user`, `user_role`. Cubre también las claves temporales de autenticación en Redis (JWT / rate limit). No son la misma cosa que una `work_session` de conteo.

No cubre pacientes ni médicos de la operación (PEF-DS02). No cubre clientes máquina tipo HIS (`integration_client`, PEF-DS08). No cubre aviso de privacidad ni ARCO (PEF-DS07).

Para cada tabla de este estándar se documentan **todos** los campos, hacia dónde apuntan las FKs y por qué, y un ejemplo de **fila completa**.

---

## 1. Explicación del estándar

### 1.1 Definiciones


| Término                             | Significado en PEF                                                                                      |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------- |
| **Institución**                     | Organización dueña de datos operativos (hospital, clínica, unidad). Puede tener padre si hay jerarquía. |
| **Usuario**                         | Persona con cuenta de login: correo, hash de contraseña, institución.                                   |
| **Rol**                             | Permiso nombrado **dentro de una institución** (`code` + descripción).                                  |
| **Asignación de rol**               | Fila en `user_role` que une un usuario con un rol.                                                      |
| **Credencial**                      | Correo + contraseña. En BD solo existe el **hash**, nunca el texto en claro.                            |
| **Sesión de autenticación (Redis)** | Estado temporal del token / intentos de login. Distinto de `work_session`.                              |


### 1.2 Uso

**Quién usa esto:** portal (login y administración IAM), backend web (valida JWT y permisos), estación de captura (mismo usuario humano).

**Para qué sirve**

1. Saber a qué institución pertenece quien entra.
2. Decidir pantallas y acciones vía roles.
3. Dejar `user.id` e `institution.id` como anclas para auditoría y resto del modelo.

**Flujo típico**

1. La persona escribe correo y contraseña.
2. El backend busca el usuario, verifica el hash, revisa `active`.
3. Emite JWT y registra en Redis lo necesario (TTL, blacklist, fallos de login).
4. Las peticiones siguientes llevan el token; el backend resuelve `user_id` e institución.

**Qué no hace:** no define conteo, paciente clínico ni consentimiento de tratamiento de datos.

---

### 1.3 Convenciones

Convenciones globales de este DS:

- Códigos de rol en inglés snake_case (`scrub_nurse`). La UI puede mostrar español.
- Email de login único en todo el sistema.
- Contraseña: solo algoritmos de hash lentos (bcrypt/argon2). Prohibido texto plano en BD, logs, mocks reales o tickets.
- Timestamps en UTC (`TIMESTAMPTZ`).
- Un usuario pertenece a **una** institución. Los roles se definen **por** institución.
- En “Hacia dónde apunta”, la columna **Relación** se lee desde esta tabla hacia el destino: `N:1` = muchas filas aquí a una allá; `N:0..1` = el destino es opcional (FK nullable); `1:1` = a lo sumo un par; `N:N` se logra con tabla puente (cada FK del puente sigue siendo `N:1`).

---

#### 1.3.1 Tabla `institution`

**Para qué existe:** ancla multi-tenant. Casi todas las tablas de negocio cuelgan de una institución.


| Campo       | Tipo         | Nulo | Default             | Significado                                                                         |
| ----------- | ------------ | ---- | ------------------- | ----------------------------------------------------------------------------------- |
| `id`        | UUID         | NO   | `gen_random_uuid()` | Identificador interno de la institución.                                            |
| `name`      | VARCHAR(200) | NO   | —                   | Nombre legible (hospital, unidad, red).                                             |
| `active`    | BOOLEAN      | NO   | `TRUE`              | Si es `false`, no se deben crear usuarios/operaciones nuevas ahí (política de app). |
| `parent_id` | UUID         | SÍ   | NULL                | Institución padre en una jerarquía (red → hospital → satélite).                     |


**Hacia dónde apunta**


| Campo       | Apunta a         | Relación                                              | Por qué                                           | ON DELETE                                                              |
| ----------- | ---------------- | ----------------------------------------------------- | ------------------------------------------------- | ---------------------------------------------------------------------- |
| `parent_id` | `institution.id` | N:0..1 (hijos→padre; un padre puede tener 0..N hijos) | Permite árbol de sedes sin otra tabla de “grupo”. | `SET NULL` — si se borra el padre, el hijo sigue existiendo sin padre. |


**Restricciones:** PK `id`.

**Ejemplo de fila completa**

```json
{
  "id": "11111111-1111-1111-1111-111111111111",
  "name": "Hospital Demo Norte",
  "active": true,
  "parent_id": null
}
```

Ejemplo hijo (misma tabla, otra fila):

```json
{
  "id": "12121212-1212-1212-1212-121212121212",
  "name": "Unidad Quirúrgica Demo Norte Oriente",
  "active": true,
  "parent_id": "11111111-1111-1111-1111-111111111111"
}
```

---

#### 1.3.2 Tabla `role`

**Para qué existe:** catálogo de permisos **por institución**. El mismo `code` puede repetirse en otro hospital con otro UUID.


| Campo            | Tipo         | Nulo | Default             | Significado                                                       |
| ---------------- | ------------ | ---- | ------------------- | ----------------------------------------------------------------- |
| `id`             | UUID         | NO   | `gen_random_uuid()` | Identificador del rol.                                            |
| `code`           | VARCHAR(64)  | NO   | —                   | Código estable de permiso (`scrub_nurse`, `supervisor`, `admin`). |
| `description`    | VARCHAR(255) | NO   | —                   | Texto explicativo para UI/administración.                         |
| `institution_id` | UUID         | NO   | —                   | Institución dueña de este rol.                                    |


**Hacia dónde apunta**


| Campo            | Apunta a         | Relación | Por qué                                                  | ON DELETE                                                        |
| ---------------- | ---------------- | -------- | -------------------------------------------------------- | ---------------------------------------------------------------- |
| `institution_id` | `institution.id` | N:1      | Los roles no son globales: cada hospital arma su matriz. | `CASCADE` — si desaparece la institución, desaparecen sus roles. |


**Restricciones:** PK `id`; UNIQUE (`institution_id`, `code`).

**Ejemplo de fila completa**

```json
{
  "id": "33333333-3333-3333-3333-333333333333",
  "code": "scrub_nurse",
  "description": "Personal de conteo en charola",
  "institution_id": "11111111-1111-1111-1111-111111111111"
}
```

---

#### 1.3.3 Tabla `user`

**Para qué existe:** cuenta humana de acceso. Es la fila que el JWT representa.


| Campo                 | Tipo         | Nulo | Default                                      | Significado                                      |
| --------------------- | ------------ | ---- | -------------------------------------------- | ------------------------------------------------ |
| `id`                  | UUID         | NO   | `gen_random_uuid()`                          | Identificador del usuario.                       |
| `name`                | VARCHAR(160) | NO   | —                                            | Nombre para mostrar.                             |
| `active`              | BOOLEAN      | NO   | `TRUE`                                       | Si es `false`, el login debe rechazarse.         |
| `email`               | VARCHAR(255) | NO   | —                                            | Correo de login; único global.                   |
| `password_hash`       | TEXT         | NO   | —                                            | Hash de la contraseña (nunca el texto en claro). |
| `password_updated_at` | TIMESTAMPTZ  | SÍ   | NULL                                         | Última vez que se cambió la contraseña.          |
| `last_login_at`       | TIMESTAMPTZ  | SÍ   | NULL                                         | Último login exitoso.                            |
| `ui_preferences`      | JSONB        | NO   | `{"locale":"en","theme":"light"}`            | Preferencias de interfaz (varias en un solo campo). |
| `created_at`          | TIMESTAMPTZ  | NO   | `now()`                                      | Alta de la cuenta.                               |
| `updated_at`          | TIMESTAMPTZ  | NO   | `now()`                                      | Última modificación de la fila.                  |
| `institution_id`      | UUID         | NO   | —                                            | Institución a la que pertenece el usuario.       |


**Hacia dónde apunta**


| Campo            | Apunta a         | Relación | Por qué                                                | ON DELETE                                                       |
| ---------------- | ---------------- | -------- | ------------------------------------------------------ | --------------------------------------------------------------- |
| `institution_id` | `institution.id` | N:1      | Todo lo que haga el usuario queda acotado a un tenant. | `RESTRICT` — no se borra una institución si aún tiene usuarios. |


**Restricciones:** PK `id`; UNIQUE `email`; `ui_preferences` debe ser un objeto JSON (`jsonb_typeof = object`).

**Claves acordadas dentro de `ui_preferences`** (extensible sin migrar columnas):

| Clave | Valores esperados | Default | Significado |
| :--- | :--- | :--- | :--- |
| `locale` | BCP 47 corto: `en`, `es`, `sv`, … | `en` | Idioma de la interfaz (no traduce códigos de negocio en BD). |
| `theme` | `light`, `dark` | `light` | Apariencia de la UI. |

Otras claves de UI (densidad, etc.) pueden añadirse después en el mismo JSONB; no van columnas nuevas por cada preferencia cosmética.

**Cookies / localStorage vs este campo:** sin login, el cliente puede guardar locale/theme en cookie. Tras login, **gana** `user.ui_preferences` y el cliente debería sincronizar cookie ← BD. Al cambiar preferencia en UI autenticada, se actualiza BD y cookie.

**Ejemplo de fila completa**

```json
{
  "id": "22222222-2222-2222-2222-222222222222",
  "name": "Ana Pérez",
  "active": true,
  "email": "ana.perez@demo.local",
  "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$[REDACTED]",
  "password_updated_at": "2026-08-01T15:00:00Z",
  "last_login_at": "2026-09-05T17:40:00Z",
  "ui_preferences": {
    "locale": "es",
    "theme": "dark"
  },
  "created_at": "2026-03-10T12:00:00Z",
  "updated_at": "2026-09-05T17:40:00Z",
  "institution_id": "11111111-1111-1111-1111-111111111111"
}
```

---

#### 1.3.4 Tabla `user_role`

**Para qué existe:** N:M entre usuario y rol. Un usuario puede tener varios roles a la vez.


| Campo         | Tipo        | Nulo | Default             | Significado                     |
| ------------- | ----------- | ---- | ------------------- | ------------------------------- |
| `id`          | UUID        | NO   | `gen_random_uuid()` | Identificador de la asignación. |
| `assigned_at` | TIMESTAMPTZ | NO   | `now()`             | Cuándo se asignó el rol.        |
| `user_id`     | UUID        | NO   | —                   | Usuario que recibe el rol.      |
| `role_id`     | UUID        | NO   | —                   | Rol asignado.                   |


**Hacia dónde apunta**


| Campo     | Apunta a  | Relación                                     | Por qué                 | ON DELETE                                             |
| --------- | --------- | -------------------------------------------- | ----------------------- | ----------------------------------------------------- |
| `user_id` | `user.id` | N:1 (el par user↔role es N:N vía esta tabla) | Dueño de la asignación. | `CASCADE` — al borrar usuario, se quitan sus roles.   |
| `role_id` | `role.id` | N:1 (el par user↔role es N:N vía esta tabla) | Permiso concreto.       | `CASCADE` — al borrar el rol, se quitan asignaciones. |


**Restricciones:** PK `id`; UNIQUE (`user_id`, `role_id`).

**Nota de aplicación:** el `role` debería pertenecer a la misma `institution_id` que el `user`. El DDL no lo fuerza con un check compuesto; la app (o un trigger) debe validarlo.

**Ejemplo de fila completa**

```json
{
  "id": "44444444-4444-4444-4444-444444444444",
  "assigned_at": "2026-03-10T12:05:00Z",
  "user_id": "22222222-2222-2222-2222-222222222222",
  "role_id": "33333333-3333-3333-3333-333333333333"
}
```

---

#### 1.3.5 Redis · autenticación (no es tabla SQL)

Redis guarda estado **temporal** de login. No sustituye `user`.


| Clave (patrón)                  | Campos / valor esperado                                   | TTL                     | Para qué                           |
| ------------------------------- | --------------------------------------------------------- | ----------------------- | ---------------------------------- |
| `auth:jwt:{jti}`                | JSON o flag: `{ "user_id", "institution_id", "revoked" }` | Igual a la vida del JWT | Validar / revocar token por `jti`. |
| `auth:login_fail:{email_or_ip}` | Entero (contador)                                         | Minutos (p. ej. 15)     | Rate limit ante fuerza bruta.      |


**Ejemplo completo de valor en `auth:jwt:{jti}`**

```json
{
  "jti": "55555555-5555-5555-5555-555555555555",
  "user_id": "22222222-2222-2222-2222-222222222222",
  "institution_id": "11111111-1111-1111-1111-111111111111",
  "revoked": false,
  "exp": "2026-09-05T19:40:00Z"
}
```

**Ejemplo completo de valor en `auth:login_fail:...`**

```json
{
  "key": "auth:login_fail:ana.perez@demo.local",
  "fail_count": 2,
  "ttl_seconds": 900
}
```

---

#### 1.3.6 Reglas de negocio

1. Login solo si `user.active = true` e institución usable.
2. Cambio de contraseña → nuevo `password_hash` + `password_updated_at`.
3. Login OK → actualizar `last_login_at`; limpiar contador Redis de fallos; devolver `ui_preferences` al cliente.
4. Logout / revocación → marcar `revoked` o borrar `auth:jwt:{jti}`.
5. Cambio de idioma/tema autenticado → merge en `ui_preferences` + `updated_at` (no crear columnas nuevas por cada preferencia UI).
6. `integration_client` (máquinas) **no** vive aquí → PEF-DS08.

---

### 1.4 Governance and Ownership


| Aspecto             | Responsable                                                 |
| ------------------- | ----------------------------------------------------------- |
| Dueño del estándar  | Backend Web / PEF Core                                      |
| Alta de usuarios    | Administración institucional (portal) o bootstrap           |
| Asignación de roles | Administración con permiso IAM                              |
| Cambios a este DS   | Revisar con seguridad si toca hash, JWT o unicidad de email |


**Dependencias aguas abajo:** casi todo el modelo referencia `user.id` o `institution.id`.

---

### 1.5 Justificación del estándar

**Por qué existe este bloque aparte**

Sin un estándar de identidad, cada pantalla inventa “quién es el usuario”. Aparecen cuentas duplicadas, contraseñas mal guardadas y roles con nombres distintos. Separar identidad humana (este DS) de contexto clínico (DS02) y de cliente máquina (DS08) evita mezclar “Ana que hace login”, “el paciente de la cirugía” y “el HIS que llama la API”.

**Decisiones de diseño que sí se tomaron**

1. **Un usuario → una institución.** Simplifica permisos y reportes multi-tenant. Si alguien trabaja en dos hospitales, se modelan dos cuentas (o más adelante un puente explícito; hoy no).
2. **Roles por institución, no globales.** Cada sede puede tener `scrub_nurse` con matices distintos sin contaminar a otras.
3. **Email único global institucional.** Un solo login string en todo el despliegue; menos ambigüedad en soporte.
4. **Solo hash en PostgreSQL; sesión en Redis.** La fuente de verdad de la persona es PG. Redis se puede vaciar sin perder usuarios.
5. **Tabla puente `user_role`.** Permite varios roles sin arrays frágiles ni columnas booleanas por permiso.
6. **`parent_id` en institución.** Jerarquía ligera sin tabla `institution_group` aparte.
7. **Un solo `ui_preferences` JSONB** para locale, theme y futuras prefs de UI. Evita migración por cada toggle cosmético; los códigos de negocio siguen en inglés.

**Acercamientos que se podrían haber hecho y no se hicieron**


| Alternativa                                    | Por qué no                                                                                                                                       |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Roles globales únicos en todo el sistema       | Choca con hospitales que bautizan distinto el mismo permiso; fuerza migraciones al onboarding.                                                   |
| Usuario multi-institución N:M desde el día 1   | Más correcto a largo plazo en redes grandes, pero complica JWT, auditoría y filtros en v1. Se pospone.                                           |
| Guardar permisos como JSON en `user`           | Rápido de prototipar; malo para consultar “quién tiene rol X” y para auditoría.                                                                  |
| Sesión solo en cookie de servidor sin Redis    | Válido en monolito chico; con varias réplicas/backends el JWT + Redis (o store compartido) escala mejor.                                         |
| LDAP/OIDC como única identidad desde el inicio | Deseable en hospital real; el núcleo PEF primero necesita identidad local predecible. OIDC puede montarse después sin cambiar el UUID de `user`. |
| Mezclar `user` con `physician` | El médico de la cirugía no siempre es quien inicia sesión en la estación. Son conceptos distintos (DS02). |
| Solo cookie para idioma/tema, sin campo en `user` | Pierde preferencia entre dispositivos (portal vs estación) y al limpiar el navegador. |
| Una columna `locale` + otra `theme` + … | Válido si nunca crecerán; JSONB cubre las mismas dos hoy y deja espacio sin ALTER. |


---

## 2. Anexo A – Información general


| Campo             | Valor                                                                              |
| ----------------- | ---------------------------------------------------------------------------------- |
| Código            | PEF-DS01                                                                           |
| Título            | Identity and Credentials                                                           |
| Versión           | 0.2.1                                                                              |
| Estado            | draft                                                                              |
| Almacenes         | PostgreSQL (`institution`, `role`, `user`, `user_role`); Redis (auth)              |
| Fuera de alcance  | Paciente, médico, operación; `integration_client`; privacidad ARCO; `work_session` |
| DDL de referencia | `esquema_base_datos_v2.md` §5.3                                                    |


