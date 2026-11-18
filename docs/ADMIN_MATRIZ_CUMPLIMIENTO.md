# ADMIN — Matriz de cumplimiento RF-AD-01…10 (diagnóstico ADMIN-00)

**Fecha:** 2026-11-01 · **Tarea:** ADMIN-00 · **Alcance:** Parte A de `data/FunctionalRequirements/rf_admin_app.md` (RF-AD-01…10). **RF-AD-11 excluido.**
**Naturaleza:** diagnóstico estático del código + línea base de pruebas ejecutada. **Ningún RF se declara cumplido**: no existe todavía una prueba de aceptación contra PostgreSQL real para ninguno.

Leyenda de estado: **NO CONFORME** (contradice el RF) · **FALTANTE** (no existe) · **PARCIAL** (existe parte, no verificada en PG) · **REMOTO** (la operación de negocio sale por HTTP del monolito).

---

## 1. Hallazgos críticos (resumen)

1. **Usuarios y roles (RF-AD-05/06) son 100 % remotos.** `services/admin_local/facade.py:30-31` envía todo `/users` y `/roles` a `identity_remote.py`, que llama a `BackendAuthService /api/v1/auth/users|roles` (`identity_admin.py`). No existe módulo local `users.py`/`roles.py` en `admin_local` (sí existe en `Legacy/porting_sources/AdminService_zip3/services/`).
2. **Familias, instrumentos y kits pueden ser remotos por configuración.** `facade.py:32-33` despacha a `catalog_remote.py` (CatalogService) cuando `CATALOG_SERVICE_URL` está definido, y **`.env.example:116` lo define** (`http://127.0.0.1:5003`). Con el `.env` de referencia, RF-AD-02 corre en CatalogService, no en el monolito.
3. **Audit log puede ser remoto por configuración.** `facade.py:34-35` → `services/audit_remote.py` (AuditService) cuando `AUDIT_SERVICE_URL` existe; **`.env.example:136` lo define**.
4. **Fuga entre instituciones en el audit log local.** `admin_local/audit_log.py:51-124` no filtra por `institution_id`: un `it_admin` ve filas `access_audit` de todas las instituciones (y las opciones de filtro también). Solo AuditService aplica el scope.
5. ~~**Roles: la UI permite crear y borrar roles arbitrarios**~~ → **Resuelto en ADMIN-01.1** (roles locales, crear/borrar rechazados en UI, dominio y AuthService).
6. ~~**El rol `or_charge` no existe**~~ → **Resuelto en ADMIN-01.1** (migración 009; `SYSTEM_ROLES` de AuthService). Sigue sin `ROLE_LABELS` en el portal a propósito: `or_charge` usa la App (Parte B, fuera de alcance).
7. **Admin tiene escritura sobre instrumentos físicos y kits** (`admin_local/api.py:91-144` con `require_roles(ADMIN, SUPERVISOR)`; rutas `admin_routes.py:192-425` con `require_role(SUPERVISOR, ADMIN)`; sidebar Admin `templates/partials/sidebar_admin.html:51,59`; `routes.py:347-348`). Contradice RACI (Admin no es dueño). **Las URLs `/admin/kits` y `/admin/instruments` son las mismas que usa el Supervisor** (`routes.py:336-337`), por lo que no pueden eliminarse.
8. **El formulario de Procedimientos escribe `procedure_kit`** (`admin_local/procedures.py:134-144`). El glosario del RF asigna `kit + kit_item + procedure_kit` al SPD. Conflicto de propiedad a decidir (D-4).
9. **Funciones del RF que no existen:** avisos de privacidad (RF-AD-04, sin rutas/plantillas Admin), `family_example` (sin uso fuera del modelo), edición de ROI (la estación se crea con `roi=None`, `configuration.py:150`), subida real de `.pt` (`vision_models.py` solo registra una referencia de texto), `user_operating_room` (sin uso en Admin), KPIs por estado de sesión y umbrales/MIME en configuración.
10. **Documentación desactualizada:** `GUIA_ADMIN_MONOLITO.md` afirma que el smoke SQLite pasa completo y que usuarios/roles son locales; hoy el smoke falla en `POST /admin/roles/new` (503) porque roles son remotos.

---

## 2. Separación (a) autenticación compartida vs. (b) negocio administrativo

### (a) Autenticación compartida — AuthService, **se conserva sin cambios** (los tres roles)

| Llamada | Ubicación | Uso | Decisión |
|---|---|---|---|
| `GET {AUTH_SERVICE_URL}/verify` | `controllers/routes.py:202,213` (`_current_user`) | Verifica JWT + Redis + usuario/institución activos; devuelve roles desde PG | **Mantener** |
| `POST /refresh` | `routes.py:208` | Renovación transparente (cookies) | **Mantener** |
| `POST /logout` | `routes.py:412` (`sign_out`) | Revoca jti en Redis | **Mantener** |
| `POST /preferences` | `routes.py:373` | Preferencias UI (identidad) | **Mantener** |
| Login | Navegador → `AUTH_BROWSER_URL/login` (`sign_in` solo renderiza) | Emisión de JWT | **Mantener** |

Propiedades verificadas en el código de AuthService que hacen viable mover el CRUD al monolito **sin** romper la autenticación:
- `verify_token` (`BackendAuthService/main.py:346-377`) consulta **PostgreSQL en cada petición** (`user.active` y `institution.active`): desactivar un usuario desde el monolito cierra su sesión en la siguiente petición aunque no se toque Redis.
- `/verify` (`main.py:751-787`) y `/refresh` (`main.py:683-704`) leen **roles desde PostgreSQL**: un cambio de `user_role` hecho localmente se refleja de inmediato.
- Contraseñas: AuthService usa `werkzeug.security.generate_password_hash/check_password_hash` (`identity_admin.py:38`); el portal ya depende de `Werkzeug==3.1.8` → un hash generado en Flask es compatible con el login de AuthService. **Misma tabla `"user"`, sin duplicar credenciales.**
- Lo único que **no** se resuelve solo con PG: **reset de contraseña** (los tokens emitidos siguen válidos) y la higiene de Redis tras desactivar. Ver estrategia D-1.

### (b) Operaciones de negocio Admin que hoy salen por HTTP (deben quedar en el monolito)

| Módulo remoto | Rutas afectadas | Servicio | Activación | Sustituto local existente |
|---|---|---|---|---|
| `admin_local/identity_remote.py` | `/users*` (desde ADMIN-01.1 ya **no** `/roles*` ni el catálogo `roles`, que son locales en `admin_local/roles.py`) | AuthService `/api/v1/auth` (CRUD) | **Siempre** | Usuarios: ninguno en `admin_local`; base portable en `Legacy/.../AdminService_zip3/services/users.py` + reglas más completas en `BackendAuthService/identity_admin.py` (ADMIN-01.2) |
| `admin_local/catalog_remote.py` | `/families*`, `/instruments*`, `/kits*`, catálogos `families/active_families/categories/cycle_statuses/kits` | CatalogService | Si `CATALOG_SERVICE_URL` | **Sí**: `families.py`, `instruments.py`, `kits.py`, `catalogs.py` |
| `services/audit_remote.py` | `/audit-events` (Admin) | AuditService | Si `AUDIT_SERVICE_URL` | **Sí**: `admin_local/audit_log.py` (falta scope institucional y rango) |
| `services/monitoring_remote.py` | `/admin/system-status` | MonitoringService | Si `MONITORING_SERVICE_URL` | No (health local solo PG en `/health/ready`) |

Nota: `audit_remote` y `monitoring_remote` también los usa el **Supervisor** (`/supervisor/audit-log`, `/supervisor/services-health`). El refactor Admin **no debe retirarlos**; solo dejar de usarlos en las rutas Admin.

---

## 3. Matriz requisito → endpoint/UI → módulo → dependencia remota → carencia → prueba

| RF | UI / endpoint Flask | Operación local (`admin_local`) | Dependencia remota | Lo que existe | Carencias contra el RF | Pruebas actuales | Estado |
|---|---|---|---|---|---|---|---|
| **01 Dashboard** | `GET /admin/dashboard` (`admin_routes.py:79-89`); `GET /admin/system-status` (`monitoring_routes.py:23-51`) | `dashboard.overview` (`dashboard.py:66-130`) | System status → MonitoringService | Familias activas, procedimientos, modelos/modelo activo, usuarios activos/inactivos, salas/estaciones, discrepancias, acuerdo IA-humano (scope institución en operación) | Sin KPIs por estado de sesión (`in_progress`, `or_hold`, `correction_required`, `awaiting_spd_review`); sin health Auth/PG/Redis/Mongo/storage en el dashboard; sin umbrales de color; sin manejo por-KPI de fallo (un error de PG tumba todo el payload); usuarios contados globalmente (todas las instituciones) | `test_dashboard_shows_service_values` (vista con dominio simulado); `test_admin_dashboard_isolation.py` ×4 **omitidas (sin PG)** | PARCIAL |
| **02 Familias + examples** | `/admin/instrument-families[/new|/<id>/edit]` (`admin_routes.py:94-189`) | `families.py` (list/get/create/update, auditoría `CREATE/UPDATE/ACTIVATE/DEACTIVATE_INSTRUMENT_FAMILY`) | CatalogService si `CATALOG_SERVICE_URL` (**activo en `.env.example`**) | CRUD, código único (409), desactivar vía status | Sin `family_example` (imagen `media_asset`, texto, orden), sin subida a storage ni límites MIME/5 MB; sin advertencia/confirmación al desactivar familia mapeada en el modelo activo; acciones de auditoría no coinciden con `catalog.family.upsert/deactivate` del RF (decidir convención) | `test_family_list_forwards_filters`, `test_validation_error_is_shown_translated`, `test_spanish_search_sends_localized_matches`; `test_catalog_remote.py` (adaptador remoto); smoke SQLite: alta/edición/409 **OK** | PARCIAL + REMOTO |
| **03 Procedimientos y catálogos** | `/admin/procedures[/new|/<id>/edit]` (`admin_routes.py:428-563`) | `procedures.py` (CRUD `cat_procedure_type` + `procedure_phase` + `procedure_kit`) | — | CRUD de procedimientos con fases (orden, conteo obligatorio) | Sin pantallas/operaciones para `cat_operation_phase`, `cat_instrument_category`, `cat_discrepancy_reason`; sin `active` en esas tablas (salvo fase) → solo bloqueo de borrado; escribe `procedure_kit` (propiedad SPD, D-4); auditoría sin nombres del RF | `test_procedure_rows_keep_their_checkbox_state`; smoke SQLite: alta + vínculos **OK** | PARCIAL |
| **04 Avisos de privacidad** | — (no hay ruta Admin) | — | — | Modelo `models/PrivacyNoticeVersion.py`; tabla `privacy_notice_version` (`001_init.sql:740`); el Supervisor **lee** versiones al programar (`templates/supervisor/sessions/schedule.html`, `SessionService`) | Todo el flujo Admin: lista, subida PDF/HTML a `privacy-notices/`, SHA-256, versión/vigencia/activa, advertencia de hash duplicado, auditoría `privacy.notice.publish`, límites `privacy_notice_max_mb` | Ninguna | FALTANTE |
| **05 Usuarios** | `/admin/users[/new|/<id>/edit|/password|/deactivate|/activate]` (`admin_routes.py:568-705`) | **Ninguna** | AuthService CRUD **siempre** | En AuthService: alta/edición, email único, contraseña ≥8, reset, activar/desactivar, último `it_admin` por institución (advisory lock), auto-protección, revocación Redis, auditoría | Negocio fuera del monolito; sin `user_operating_room` (asignación de salas a `or_charge`); sin rol `or_charge` asignable; scope: el admin gestiona usuarios de **todas** las instituciones (D-2); política de longitud de contraseña fija en código | `test_password_reset_and_deactivation_are_posts`; `test_identity_remote.py` (adaptador, 1 **falla por PG**); AuthService `test_identity_admin.py` **omitidas (sin PG)** | REMOTO + PARCIAL |
| **06 Roles** | `/admin/roles`, `/admin/roles/<id>/edit` (crear/borrar retirados → 404) | `admin_local/roles.py` (list/get/update descripción; `reject_code_change` para POST/DELETE) | **Ninguna** (AuthService solo lee `role` para autenticar) | 4 códigos fijos por institución (migración 009), solo descripción, scope institucional (404 a otra institución), código inmutable, crear/borrar → 405 `role_codes_fixed` + auditoría `DENIED_*`, 403 no-admin, `UPDATE_ROLE` auditado; AuthService `SYSTEM_ROLES` con `or_charge` y `POST /roles` limitado a los 4 códigos | Smoke manual en navegador | `tests/test_admin_roles.py` (13, PG real); `test_identity_remote.py`; AuthService `test_roles_lifecycle_and_protection`, `test_fixed_role_codes_are_read_from_postgresql`; smoke SQLite y smoke real (36/36) | **CUMPLE (verificado con pruebas automatizadas, ADMIN-01.1)** |
| **07 Salas y estaciones** | `/admin/configuration`, `/operating-rooms/*`, `/capture-stations/*` (`admin_routes.py:959-1127`) | `configuration.py` (scope por institución, código de sala único 409, estación exige sala de la institución, auditoría) | — | CRUD salas/estaciones, desactivar sin borrar | ROI no editable (se crea `roi=None`; UI solo muestra Configured/Not configured); sin validación `x,y,w,h,frame_w,frame_h` ni límites/relación con el frame | Smoke SQLite (sala/estación) no alcanzado por falla previa en roles | PARCIAL |
| **08 Modelos de visión** | `/admin/vision-models[/new|/<id>/edit|/<id>/activate]` (`admin_routes.py:809-930`) | `vision_models.py` (registro, mapping clase→familia, activación transaccional, bloqueo de mapping del activo, `classes_required`) | — | Un solo activo (índice `uk_yolo_model_active` + `_deactivate_others`), activar sin clases bloqueado, auditoría | Sin subida de `.pt` ni storage; checksum tecleado (opcional), no calculado; sin rechazo de checksum duplicado; sin extracción `names/nc` ni fallback YAML/JSON; sin `architecture`/`model_size` (columnas de 008); sin overlay de familias activas faltantes; permite activar desde el alta; sin desactivar explícito (regla "no cero activos"); auditoría sin `vision.model.publish/activate` | Ninguna específica; smoke SQLite no alcanzado | PARCIAL |
| **09 Audit log** | `/admin/audit-log` (`admin_routes.py:935-954`) | `audit_log.list_events` (`audit_log.py:51-124`) | AuditService si `AUDIT_SERVICE_URL` (**activo en `.env.example`**) | Filtros usuario/acción/entidad/resultado/búsqueda/**un día**, paginación (tope 5000 filas), solo lectura | **Sin filtro por institución (fuga cross-tenant)**; sin rango de fechas (desde/hasta) ni regla "rango vacío: no consultar"; paginación en memoria | `test_audit_log_renders_service_events` (vista simulada); `test_audit_remote.py` (adaptador) | PARCIAL + REMOTO + defecto de seguridad |
| **10 Institución y configuración** | `/admin/configuration`, `/admin/configuration/institution/edit` | `configuration.get/update_institution` | — | Editar nombre, auditoría `UPDATE_INSTITUTION`, bloquea desactivar la institución propia (más estricto que el RF, efecto equivalente en v1 de una institución) | Sin parámetros visibles (umbrales KPI, MIME/tamaños de avisos, examples, `.pt`); la regla del RF es "única institución activa" | Ninguna específica | PARCIAL |

**Resumen por tipo de trabajo**

| Requiere código nuevo | Requiere integración/refactor de lo existente |
|---|---|
| RF-AD-04 completo; `family_example` (02); catálogos auxiliares (03); ROI (07); subida/extracción `.pt`, overlay (08); rango de fechas (09); parámetros visibles (10); KPIs de sesión y health local (01); `users.py`/`roles.py` locales (05/06); migración `or_charge` | Desconectar `identity_remote`, `catalog_remote`, `audit_remote` de Admin; scope institucional del audit log; retirar escritura Admin de piezas/kits; bloquear crear/borrar roles; ajustar nombres de acciones de auditoría |

---

## 4. Instrumentos físicos y kits — retiro de la facultad de escritura Admin

**Situación:** las rutas `/admin/instruments*` y `/admin/kits*` son **compartidas** con el Supervisor SPD (su sidebar apunta a ellas, `routes.py:336-337`). Las escrituras se permiten a ambos roles en dos capas: `require_role(SUPERVISOR, ADMIN)` en `admin_routes.py` y `require_roles(ADMIN, SUPERVISOR)` en `admin_local/api.py`.

**Diseño propuesto (ADMIN-02, sin tocar funciones SPD):**
1. En `admin_local/api.py`: `POST/PUT /instruments*` y `/kits*` → `require_roles(SUPERVISOR)`; `GET` se mantiene para ambos (lectura permitida por RF-AD-01).
2. En `admin_routes.py`: `/new` y `/<id>/edit` → `require_role(SUPERVISOR)`; listas siguen `require_role(SUPERVISOR, ADMIN)` pero ocultan botones de alta/edición para `it_admin`.
3. Quitar `instruments`/`kits` del sidebar Admin (o dejarlos como "solo lectura" si se decide conservar la consulta).
4. Defensa en profundidad: la capa de dominio es la que manda (403 + `DENIED_*` en `access_audit`).
5. Pruebas: `it_admin` → 403 en POST/PUT y en GET de formularios; `spd_supervisor` → flujo completo intacto; listas de lectura 200 para ambos.
6. A futuro (Fase 2) estas rutas deberían migrar a un prefijo `/supervisor/...`; **no** se hace en Fase 1 para no romper navegación.

---

## 5. Estrategia incremental para localizar usuarios y roles sin duplicar identidad

Principios: **una sola tabla `"user"`/`role`/`user_role`** (la misma que lee AuthService); hash con `werkzeug.security` (compatible); **cero** login/JWT local; AuthService sigue siendo el único emisor y verificador de tokens.

1. **Migración `009_roles_fijos_or_charge.sql`** (idempotente, `INSERT … ON CONFLICT (institution_id, code) DO NOTHING`): crear `or_charge` en cada institución. Opcional: `CHECK` o trigger que limite `role.code` a los 4 códigos (evaluar impacto en roles existentes antes).
2. **`admin_local/roles.py` local:** list/get/update(description). `create`/`delete` → 400/409 `fixed_role_codes` (mensaje inglés). Reutilizar `Legacy/.../roles.py` quitando crear/borrar. UI: retirar "New Role" y "Delete".
3. **`admin_local/users.py` local:** portar las reglas de `BackendAuthService/identity_admin.py` (no las del Legacy, que son más débiles): email único case-insensitive, institución activa, roles de la institución, contraseña mínima, **último `it_admin` activo por institución con `pg_advisory_xact_lock(hashtext('pef:identity:administrators'))`** (misma clave → serializa también contra AuthService), auto-protección, auditoría con los mismos nombres de acción.
4. **`user_operating_room`:** alta/baja de salas solo para usuarios con `or_charge`; salas de la misma institución.
5. **Revocación (D-1):** desactivar/cambio de institución ya es efectivo por la verificación PG de AuthService. Para **reset de contraseña** hace falta revocar los jti en Redis. Opciones:
   - **D-1a (recomendada):** añadir en AuthService un endpoint de **sesión** `POST /api/v1/auth/sessions/revoke` (`user_id`, solo `it_admin`), usado por el monolito tras el commit. Es responsabilidad de revocación, permitida por la regla 3.
   - D-1b: que Flask escriba Redis con el esquema `auth:user_tokens:{id}`/`auth:jwt:{jti}` de `token_store.py` → duplica conocimiento de formato; **no recomendada**.
   - D-1c: añadir a `verify_token` la comparación `password_updated_at` > `iat` del token → revocación implícita sin Redis; cambio pequeño en AuthService con su propia prueba.
6. **Conmutar `facade.py`:** quitar `identity_remote.handles` del despacho y registrar `/users*`, `/roles*` en `api.py`; catálogo `roles` desde local. **Conservar** `identity_remote.py` hasta ADMIN-07 (no borrar) y sus pruebas marcadas como obsoletas.
7. **AuthService:** sus endpoints CRUD `/api/v1/auth/users|roles` quedan sin uso por el portal; no se eliminan en Fase 1 (decisión en ADMIN-07). Añadir `or_charge` a `SYSTEM_ROLES` para que su `DELETE /roles` no pueda borrarlo.
8. **`or_charge` en el portal:** hoy un usuario solo `or_charge` no tiene `ROLE_LABELS` → `_current_user()` devuelve `None` y se redirige a sign-in. La App (Parte B) está fuera de alcance; documentarlo y, como mínimo, mostrar mensaje claro (no bucle).

---

## 6. Decisiones pendientes (requieren confirmación del equipo)

| ID | Decisión | Recomendación |
|---|---|---|
| D-1 | Mecanismo de revocación Redis tras reset/desactivación desde el monolito | **RESUELTA (2026-11-01):** revocar mediante AuthService + Redis, reutilizando lo existente. AuthService ya tiene `RedisTokenStore.revoke_user_tokens` / `identity_admin.revoke_sessions`, pero solo como efecto de sus endpoints CRUD; no hay endpoint independiente → exponer esa función en un endpoint de sesión mínimo (ADMIN-01.3). |
| D-2 | Alcance institucional de Admin | **RESUELTA (2026-11-01):** cada `it_admin` solo consulta y modifica usuarios y recursos de **su** institución, incluidos usuarios, roles, auditoría y catálogos. Implica revisar también los conteos/listas hoy globales (`dashboard.py`, `catalogs._institutions`, familias/procedimientos) en sus bloques respectivos. |
| D-3 | Health de RF-AD-01 | **RESUELTA (ADMIN-01):** pings locales con timeout (Auth `/health`, PostgreSQL, Redis por RESP, MongoDB, almacenamiento) en el dashboard; `/admin/system-status` (MonitoringService) se conserva como vista técnica ampliada, no es operación de negocio. |
| D-4 | `procedure_kit` (vínculos procedimiento-kit) | **RESUELTA (ADMIN-01):** propiedad SPD según el glosario del RF. Administración los muestra solo lectura y el dominio rechaza escribirlos (`procedure_kits_owned_by_spd`); guardar un procedimiento conserva los vínculos. Pendiente para el bloque del Supervisor: pantalla SPD para mantenerlos. |
| D-5 | Nombres de acciones de auditoría | **RESUELTA (ADMIN-01):** se conserva la convención UPPER_SNAKE de la plataforma (compartida con CatalogService y AuditService) con equivalencias documentadas: `CREATE_/UPDATE_INSTRUMENT_FAMILY` = `catalog.family.upsert`, `DEACTIVATE_INSTRUMENT_FAMILY` = `catalog.family.deactivate`, `PUBLISH_PRIVACY_NOTICE` = `privacy.notice.publish`, `PUBLISH_VISION_MODEL` = `vision.model.publish`, `ACTIVATE_VISION_MODEL` = `vision.model.activate`. |
| D-6 | Parámetros RF-AD-10 | **RESUELTA (ADMIN-01, a petición del equipo):** persistentes por institución en la tabla `app_setting` (migración 010), editables, validados y auditados; sin fila = valor predeterminado documentado. |

---

## 7. Línea base de pruebas (2026-11-01)

Entorno: Windows 11, Python 3.14.2, venv aislado en el scratchpad de la sesión con `apps/BackendWebFlask/requirements.txt` + `pytest` (+ `PyJWT`, `redis`, `lxml` para AuthService). **Docker Desktop no estaba en ejecución** (`docker ps` → no daemon). En `localhost:5432` responde un PostgreSQL **ajeno al proyecto** que rechaza `postgres/postgres`; no se intentó acceder con otras credenciales. No hay `.env` en la raíz.

| # | Comando (desde la carpeta indicada) | Resultado |
|---|---|---|
| 1 | `apps/BackendWebFlask`: `python -m pytest tests/test_admin_routes.py tests/test_admin_dashboard_isolation.py tests/test_identity_remote.py tests/test_catalog_remote.py tests/test_audit_remote.py -q -p no:cacheprovider` | **43 aprobadas, 1 fallida, 4 omitidas** |
| 2 | `apps/BackendWebFlask`: `python -m pytest tests -q -p no:cacheprovider -rs --tb=no` | **186 aprobadas, 6 fallidas, 113 omitidas** |
| 3 | raíz: `python scripts/smoke_admin_monolith_sqlite.py` | **Parcial**: familias (alta/edición/409/400), kits, instrumentos, procedimientos y listas OK; **falla** en `POST /admin/roles/new` → 503 (roles son remotos); el resto del smoke no se ejecuta |
| 4 | `apps/BackendAuthService`: `python -m pytest tests -q -p no:cacheprovider -rs` | **11 aprobadas, 148 omitidas** (PG/Redis no disponibles) |

**Fallas (todas preexistentes y dependientes de PostgreSQL; no son regresiones):**
- `test_identity_remote.py::test_user_form_role_options_come_from_auth` — el formulario pide `institutions` al dominio local (PG) antes que `roles` a Auth; sin PG la página aborta el catálogo y Auth nunca se llama. Prueba **no hermética**.
- `test_db_labels.py::test_active_capture_localizes_names_but_keeps_worker_keys` y 4 de `test_session_remote.py` (detalle/review, adición confirmada, cierre con/sin faltante) — intentan conectar a PG y no se omiten cuando no está disponible.

**Omisiones:** 109 por PostgreSQL no alcanzable (multitenant, single video flow, count reference/traceability, vision rules, dashboard isolation), 16 por MongoDB no configurado. **Ninguna prueba de Admin se ha ejecutado contra PostgreSQL real.**

**Para reproducir con base real:** ver §7.2 (entorno aislado ya preparado en ADMIN-01.0).

### 7.2 Línea base con PostgreSQL/Redis/MongoDB reales (ADMIN-01.0, 2026-11-01)

Entorno: `docker-compose.test.yml` (proyecto `pef_admin_test`; PostgreSQL 16.15, Redis 7, Mongo 7; 127.0.0.1:55432/56379/47017; volúmenes propios). BD `pef_test` inicializada por `initdb` con 001 → 002 → 003 → 004 → 005 → 006 → 007 → 008 (mismo orden que `docker-compose.yml`), sin errores. Comprobado: 56 tablas, `user_operating_room`, `yolo_model.architecture/model_size`, `work_session.yolo_model_id`, columnas OR-hold de `discrepancy`, estado `or_hold`, 3 eventos `or_hold_*`, `media_asset.kind` con `mp4`; 1 institución, roles `it_admin, spd_supervisor, station_operator` (sin `or_charge`), 5 usuarios activos, 17 familias, 6 procedimientos, 1 aviso de privacidad, 1 modelo YOLO. 004–008 re-ejecutadas dos veces sobre la copia `pef_test_migcheck`: idempotentes.

| # | Comando (raíz) | Resultado |
|---|---|---|
| 1 | `python scripts/run_test_env.py web` | **302 aprobadas, 3 fallidas, 0 omitidas** |
| 2 | `python scripts/run_test_env.py admin` | **74 aprobadas** (admin_routes, dashboard_isolation, identity_remote, catalog_remote, audit_remote, multitenant) |
| 3 | `python scripts/run_test_env.py auth` | **159 aprobadas** |
| 4 | `python scripts/run_test_env.py smoke` | Falla conocida en `POST /admin/roles/new` (503): el smoke SQLite asume roles locales y además crea un código arbitrario (contrario a RF-AD-06). Debe reescribirse en ADMIN-01.1. |
| 5 | `python scripts/smoke_admin_test_env.py` | **34/34**: health Auth y Web (PG), login/página/refresh transparente/logout/token revocado en Redis/redirección para `station_operator`, `spd_supervisor`, `it_admin`; 403 a `/admin/users` para no-admin; 11 pantallas `/admin/*` con 200 y sin errores en logs (usuarios/roles leídos vía AuthService). |

**Clasificación de las 3 fallas de (1)** — todas del Operador, preexistentes (no hay cambios en `apps/` desde el commit base), fuera del alcance Admin:

| Prueba | Tipo | Causa |
|---|---|---|
| `test_single_video_flow.py::test_capture_page_shows_the_new_steps` | Conflicto de consolidación (prueba vs. funcionalidad a conservar) | La prueba V1 exige que la captura no tenga `<video>`/`autoplay`; la vista previa WebSocket del ZIP 3 añadió `<video id="capture-live-raw" … autoplay hidden>` (`templates/operator/sessions/capture_rf.html:185`). Resolver ajustando la prueba o el render condicional, sin retirar el WS. |
| `test_vision_count_rule.py::test_retry_after_14_of_15_uses_only_the_new_capture` | Funcional (regla de conteo) | Repetir la misma corrida rechazada (14 de 15) registra un solo `close_blocked` (esperado `[14, 14]`, obtenido `[14]`). Bloque GLOBAL-01/02. |
| `test_vision_count_rule.py::test_capture_page_shows_stage_and_mismatch_reason` | Funcional (pantalla de captura) | Tras `POST /capture/initial`, la página no muestra "Initial count registered: 15 instruments." (`capture_rf.html:213` depende de `count_reference`). Bloque GLOBAL-01/02. |

Fallas de configuración: ninguna con el entorno preparado (las 6 fallas y 113 omisiones de §7 eran por ausencia de PG/Mongo). Pendiente de mejora: las pruebas que fallan en vez de omitirse cuando no hay PG siguen sin ser herméticas.

---

## 8. Orden concreto de cambios propuesto

| Paso | Tarea | Contenido verificable | Depende de |
|---|---|---|---|
| 0 | **ADMIN-01.0 Entorno de prueba** | Docker activo; BD PostgreSQL de **prueba** con 001+seeds+004…008; `.env` local (no versionado); repetir línea base y registrar cifras con PG | — |
| 1 | **ADMIN-01.1 Roles fijos — HECHO 2026-11-01 (ver §9)** | Migración 009 (`or_charge`), `admin_local/roles.py` local, retirar crear/borrar en UI y dominio, `or_charge` en `ROLE_LABELS`/presenters y `SYSTEM_ROLES` de Auth; pruebas: lista 4 códigos, editar descripción, crear/borrar → rechazo, 403 no-admin, auditoría `UPDATE_ROLE` | 0 |
| 2 | **ADMIN-01.2 Usuarios locales** | `admin_local/users.py` con reglas de `identity_admin.py`; conmutar `facade`; pruebas: email duplicado, contraseña corta/no coincide, último admin, auto-desactivación, institución inactiva, roles de otra institución, login posterior en AuthService con la nueva contraseña | 1 |
| 3 | **ADMIN-01.3 Revocación** | Implementar D-1 elegida; prueba: reset → token previo rechazado en `/verify`; desactivar → 401 en siguiente petición | 2, D-1 |
| 4 | **ADMIN-01.4 Salas de SdP** | `user_operating_room` en formulario de usuario `or_charge`; pruebas de sala de otra institución y de usuario sin `or_charge` | 2 |
| 5 | **ADMIN-01.5 Cierre** | `identity_remote` fuera del despacho Admin; test que falle si Admin hace HTTP a `/api/v1/auth/users|roles`; login/logout/refresh/verify de los 3 roles probados | 1–4 |
| 6 | ADMIN-06 (adelantado parcial) | **Scope institucional en `audit_log.py`** (defecto de seguridad) + rango de fechas | 0 |
| 7 | ADMIN-02 | Retiro escritura Admin de piezas/kits; `family_example`; advertencia familia en modelo activo; catálogos auxiliares; desconectar `catalog_remote` de Admin | 5 |
| 8 | ADMIN-03 | Biblioteca de avisos | 7 (storage) |
| 9 | ADMIN-04 | ROI | 0 |
| 10 | ADMIN-05 | Subida `.pt`, SHA-256, extracción, overlay | 7 (storage) |
| 11 | ADMIN-06 | KPIs, health local, configuración visible, retirar `audit_remote` de Admin | 6 |
| 12 | ADMIN-07 | Regresión y cierre | todo |

**Riesgos que podrían impedir retirar el CRUD remoto (sin retirar AuthService):**
- Sin PostgreSQL de prueba no hay forma de verificar reglas transaccionales (advisory lock, índices únicos, `FOR UPDATE`); SQLite no las soporta.
- Revocación tras reset de contraseña requiere tocar AuthService (D-1) o aceptar ventana hasta expiración del access token.
- Concurrencia mixta durante la transición: si AuthService CRUD y el monolito escriben a la vez, el último-admin solo es seguro si ambos usan la **misma clave de advisory lock**.
- `.env.example` activa `CATALOG_SERVICE_URL` y `AUDIT_SERVICE_URL`; desconectar Admin de ellos no debe desconectar al Supervisor (que comparte `audit_remote`/rutas de kits/instrumentos).
- Pruebas no herméticas pueden ocultar regresiones: cada paso debe ejecutarse con PG real.

---

## 9. ADMIN-01.1 — RF-AD-06 Roles fijos (2026-11-01)

**Estado:** implementado en el monolito y **verificado con pruebas automatizadas** contra PostgreSQL/Redis reales (entorno `docker-compose.test.yml`). Pendiente solo el smoke manual en navegador.

### 9.1 Contrato aplicado

| Regla RF-AD-06 / decisión | Dónde se aplica | Prueba |
|---|---|---|
| Exactamente 4 códigos: `station_operator`, `spd_supervisor`, `it_admin`, `or_charge` | `data/migrations/009_roles_fijos.sql`; `admin_local/roles.PRODUCT_ROLES`; AuthService `SYSTEM_ROLES` | `test_migration_009_…`, `test_existing_seed_institution_has_the_four_product_roles`, `test_list_shows_the_four_roles…` |
| Editar solo la descripción | `roles.update_role` (código inmutable → 400 `role_code_immutable`; descripción obligatoria ≤255) | `test_edit_description_persists_and_is_audited`, `test_edit_validation`, `test_domain_rejects_create_delete_and_code_change` |
| No crear ni borrar códigos (backend, no solo UI) | Rutas HTML retiradas (404); dominio `POST /roles`, `DELETE /roles/<id>` → 405 `role_codes_fixed` + `DENIED_CREATE_ROLE`/`DENIED_DELETE_ROLE`; AuthService `POST /roles` → 422 `role_code_not_allowed` para códigos no producto, `DELETE` de producto → 409 `system_role` | `test_create_and_delete_routes_do_not_exist`, `test_domain_rejects_…`, `test_screens_offer_no_create_or_delete`, AuthService `test_roles_lifecycle_and_protection` |
| D-2: solo la institución propia | `roles._own_roles` / `roles._owned` (otra institución = 404); catálogo `roles` del formulario de usuarios local y filtrado | `test_admin_cannot_see_or_edit_roles_of_another_institution`, `test_domain_hides_roles_of_another_institution`, `test_user_form_role_catalog_is_local_and_scoped` |
| Solo `it_admin` | `require_role(ADMIN)` en rutas + `require_roles(ADMIN)` en dominio (403 + `DENIED_<MÉTODO>_ROLE`) | `test_supervisor_is_forbidden_on_screens_and_domain` |
| Auditoría `access_audit` | `UPDATE_ROLE` solo si cambia la descripción (actor, institución, recurso) | `test_edit_description_persists_and_is_audited` |
| Sin HTTP de negocio a AuthService | `facade.py` + `identity_remote.handles` solo `/users` | fixture `no_auth_http`; `test_only_users_are_identity_paths`; smoke real: 0 llamadas a `/api/v1/auth/roles` |
| AuthService sigue autenticando y lee roles de PG | Sin cambios en login/JWT/refresh/logout/Redis | AuthService 160/160 (incl. `test_fixed_role_codes_are_read_from_postgresql`: `or_charge` en `/permissions`, descripción editada visible en `/verify` sin nuevo login, login de usuario solo `or_charge`); smoke real 36/36 (login/refresh/logout/revocación de los 3 roles) |

### 9.2 Migración 009

- Idempotente: `INSERT … SELECT … CROSS JOIN (VALUES …) ON CONFLICT (institution_id, code) DO NOTHING`. No borra, renombra ni reasigna; respeta descripciones previas y `user_role`. Roles con otros códigos (si existieran en una BD antigua) no se tocan; AuthService aún permite borrarlos si no tienen usuarios.
- **BD existente** (`pef_test`, `pef_test_migcheck`): 1.ª ejecución `INSERT 0 1` (`or_charge`), 2.ª `INSERT 0 0`; roles previos y asignaciones idénticos antes/después.
- **Instalación nueva** (`pef_test_fresh`, 001→009): sin errores; la institución demo queda con los 4 roles. `docker-compose.yml` y `docker-compose.test.yml` montan `09_roles_fijos.sql` en `initdb` (solo afecta volúmenes nuevos).
- **Para una BD de desarrollo ya inicializada** (respaldar antes):
  `docker compose exec -T postgres sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < data/migrations/009_roles_fijos.sql`

### 9.3 Resultados

| Comando | Resultado |
|---|---|
| `python scripts/run_test_env.py admin` | 86 aprobadas |
| `python scripts/run_test_env.py web` | 314 aprobadas, 3 fallidas (las 3 del Operador de §7.2, sin cambios; 0 regresiones) |
| `python scripts/run_test_env.py auth` | 160 aprobadas |
| `python scripts/run_test_env.py smoke` | Completo OK (antes fallaba en roles) |
| `python scripts/smoke_admin_test_env.py` | 36/36 |

### 9.4 Riesgos y notas

- Un usuario con **solo** `or_charge` puede autenticarse en AuthService (política de tokens `default`), pero el portal web no tiene pantallas para ese rol (`ROLE_LABELS` sin `or_charge`): `_current_user()` lo trata como sin acceso. Es intencional mientras la App (Parte B) esté fuera de alcance.
- Los endpoints CRUD de roles de AuthService siguen existiendo (ya sin uso por el portal) y quedaron alineados con RF-AD-06; su retiro o conservación se decide en ADMIN-07.
- La lista de **usuarios** sigue viniendo de AuthService y aún muestra todas las instituciones (D-2 se aplicará en ADMIN-01.2).
- `scripts/e2e_admin.py` se adaptó pero no se ejecutó (requiere la pila completa en 5000/5001).


---

## 10. Cierre de Administración ADMIN-01 (RF-AD-01 … RF-AD-10), 2026-11-01

**Resultado:** los diez RF de la Parte A están implementados en `apps/BackendWebFlask` y **verificados con pruebas automatizadas** contra PostgreSQL 16, Redis 7, MongoDB 7 y Garage (S3) reales del entorno aislado, más un smoke real con AuthService y el portal en ejecución. **Pendiente para todos:** prueba manual en navegador (no se pudo realizar en este entorno). RF-AD-11 no se implementó (fuera de alcance).

### 10.1 Arquitectura final

- Toda operación administrativa corre en proceso (`services/admin_local/`). `facade.py` ya no despacha a `identity_remote` (marcado obsoleto, sin uso), ni a `audit_remote`, ni a `catalog_remote` para `it_admin`.
- AuthService: autenticación de los tres roles sin cambios. Nuevo endpoint mínimo `POST /api/v1/auth/users/{id}/sessions/revoke` (it_admin de la misma institución, reutiliza `revoke_user_tokens`, auditado `REVOKE_USER_SESSIONS`). Sus endpoints CRUD de usuarios/roles siguen existiendo pero el portal ya no los usa (decisión de retiro: equipo).
- Supervisor: `/admin/instruments` y `/admin/kits` solo para `spd_supervisor` (403 a it_admin); CatalogService/AuditService/MonitoringService/SessionService siguen sirviendo al Supervisor sin cambios.
- Evidencia de ausencia de HTTP de negocio: fixture `no_remote_http` en las pruebas de Admin (falla ante cualquier `requests`); smoke real: AuthService solo recibió `POST .../sessions/revoke` del portal.

### 10.2 Estado por requisito

| RF | Implementado | Archivos principales | Pruebas | Estado | Pendientes / riesgos |
|---|---|---|---|---|---|
| **01 Dashboard** | Sesiones por estado (4 colas) de la institución con colores por umbral; familias activas/inactivas, procedimientos, modelos/activo, usuarios activos/inactivos y roles de la institución; salud Auth/PG/Redis/Mongo/almacenamiento; secciones independientes ante fallos | `dashboard.py`, `health.py`, `templates/admin/dashboard.html` | `test_admin_dashboard.py` (8), `test_admin_dashboard_isolation.py` (5) | **CUMPLE (automatizado)** | El ping a Auth agrega hasta 2 s si AuthService no responde. Familias/procedimientos/modelos son globales (sin `institution_id`). |
| **02 Familias** | CRUD local, código único, confirmación al desactivar familia del modelo activo, fotos `family_example` + `media_asset` en almacenamiento (local/S3) con SHA-256, tipo por *magic bytes*, límites de RF-AD-10, servido con `nosniff` | `families.py`, `storage.py`, `media.py`, `instrument_families/form.html` | `test_admin_families.py` (10, incluye Garage S3) | **CUMPLE (automatizado)** | Catálogo compartido entre instituciones (límite de esquema, D-2). Las imágenes retiradas conservan su objeto y `media_asset` (trazabilidad). |
| **03 Procedimientos y catálogos** | Procedimientos con fases; vínculos con kits solo lectura (D-4); fases/categorías/motivos con código único inmutable, sin borrado, fases activables, auditoría | `procedures.py`, `selection_catalogs.py`, `selection_catalogs/list.html`, `procedures/form.html` | `test_admin_procedures.py` (8) | **CUMPLE (automatizado)** | Categorías y motivos no se pueden ocultar (no tienen columna `active`; ocultarlos exigiría cambiar consultas de OP/SPD). Mantenimiento de `procedure_kit` queda sin pantalla hasta el bloque SPD. |
| **04 Avisos de privacidad** | Biblioteca versionada: subida PDF/HTML, SHA-256, hash duplicado con confirmación, versión única, vigencia, activar/desactivar, opción de una sola activa, descarga segura; nunca escribe `session_processing_agreement` | `privacy.py`, `media.py`, `privacy_notices/*.html` | `test_admin_privacy.py` (11, incluye Garage S3) | **CUMPLE (automatizado)** | Biblioteca compartida (tabla sin `institution_id`). |
| **05 Usuarios** | Alta/edición/activación/desactivación/reset locales con reglas de `identity_admin.py`; política de contraseña configurable; quirófanos para `or_charge`; institución propia (D-2); revocación de sesiones por AuthService (D-1) con aviso si falla | `users.py`, `auth_sessions.py`, `users/*.html` | `test_admin_users.py` (21), AuthService `test_revoke_user_sessions_*` (2), smoke real (ciclo de identidad) | **CUMPLE (automatizado + smoke real)** | Si AuthService no responde, la desactivación igual cierra el acceso (AuthService verifica `active` en PostgreSQL), pero las sesiones de un reset de contraseña viven hasta expirar: se avisa en pantalla. |
| **06 Roles** | Cuatro códigos fijos, solo descripción, institución propia | `roles.py` (ADMIN-01.1) | `test_admin_roles.py` (13) | **CUMPLE (automatizado)** | — |
| **07 Salas y estaciones** | Código de sala único por institución, estación con sala propia, ROI editable y validado en el formato del worker, conserva claves extra, desactivar sin perder histórico | `configuration.py`, `capture_station_form.html` | `test_admin_configuration.py` (26 en total con RF-AD-10) | **CUMPLE (automatizado)** | — |
| **08 Modelos de visión** | Subida real `.pt` con límite, SHA-256, checksum duplicado rechazado, almacenamiento + `media_asset`; lectura de clases sin ejecutar el pickle; fallback `names.yaml`/JSON; mapping con autocompletado, rechazo de clases, advertencia de familias faltantes; activación transaccional; no dejar cero activos | `vision_models.py`, `yolo_weights.py`, `vision_models/upload.html`, `mapping.html` | `test_admin_vision_models.py` (23, incluye pickle malicioso y Garage S3) | **CUMPLE (automatizado)** — **validación con `best.pt` real pendiente** | No había pesos reales en el equipo: el lector se probó con checkpoints sintéticos con estructura Ultralytics. Solo se aceptan checkpoints zip (`torch.save` ≥ 1.6). VisionWorker no se notifica: desplegar el `.pt` es manual. |
| **09 Auditoría** | Local, solo lectura, institución obligatoria en SQL, filas sin institución excluidas, filtros usuario/acción/entidad/resultado/búsqueda, rango de fechas (vacío no consulta), paginación SQL | `audit_log.py`, `audit_log.html` | `test_admin_audit_log.py` (7), `test_audit_remote.py` | **CUMPLE (automatizado)** — defecto de seguridad corregido | — |
| **10 Configuración** | Nombre/estado de la institución (no se desactiva la propia, cubre "única institución"); parámetros persistentes por institución (umbrales KPI, contraseña, tamaños/MIME de fotos, avisos y pesos, una sola versión activa, texto de advertencia) con validación y auditoría | `settings.py`, `configuration.py`, `configuration/index.html`, migración 010 | `test_admin_configuration.py` (26 en total con RF-AD-07) | **CUMPLE (automatizado)** | Las extensiones de pesos (`.pt`) se muestran pero no son editables (único formato que carga el worker). |

### 10.3 Migraciones

| Migración | Contenido | Verificación |
|---|---|---|
| `009_roles_fijos.sql` | Cuatro roles de producto por institución | BD existente, copia y nueva; dentro de pruebas (§9) |
| `010_admin_configuracion.sql` | `app_setting`; `yolo_model.class_names` | Aplicada dos veces (idempotente) en `pef_test`, `pef_test_migcheck` y `pef_test_fresh` |

### 10.4 Resultados finales (entorno aislado, 2026-11-01)

| Comando | Resultado |
|---|---|
| `python scripts/run_test_env.py admin` | **190 aprobadas** |
| `python scripts/run_test_env.py web` | **418 aprobadas, 3 fallidas** (las 3 del Operador de §7.2, sin cambios; 0 regresiones) |
| `python scripts/run_test_env.py auth` | **162 aprobadas** |
| `python scripts/run_test_env.py smoke` | **OK completo** (incluye usuarios locales, avisos, catálogos, modelo y ROI) |
| `python scripts/smoke_admin_test_env.py` | **51/51** (3 roles por AuthService, 20 pantallas Admin, 403 en piezas/kits, ciclo de identidad con revocación real) |
| `docker build apps/BackendWebFlask` + contenedor contra la BD de prueba | Imagen construida, módulos nuevos importan, `/data/admin_storage` escribible por usuario no root, `/health/ready` OK |

### 10.5 Pendientes que no pudieron completarse

1. Prueba manual en navegador de todas las pantallas de Administración (no disponible en este entorno).
2. Validación de RF-AD-08 con un `best.pt` real de Ultralytics.
3. Garage en `docker-compose.yml` crea solo el bucket de evidencias: para `ADMIN_STORAGE_BACKEND=s3` en la pila principal hace falta un bucket escribible por la clave (por defecto se usa el volumen local).
4. `scripts/e2e_admin.py` (E2E antiguo de la pila completa) quedó marcado como sustituido; no se reescribió.
5. Decisión del equipo sobre retirar los endpoints CRUD de usuarios/roles de AuthService (sin uso por el portal).
6. Pantalla SPD para mantener `procedure_kit` (bloque del Supervisor).


---

## 11. Auditoría de integración y arquitectura (2026-11-03)

Rama `admin-01` sobre el checkpoint `234b437`, cambios sin commit. Sin tocar microservicios del Supervisor, VisionWorker ni reglas de conteo. Evidencia ejecutable: `scripts/audit_admin_integration.py`, `tests/test_admin_institution_scope.py`, `tests/test_admin_storage.py`, AuthService `test_revoke_*`.

### 11.1 Alcance institucional por recurso

| Recurso | Alcance esperado según RF / ER | Alcance actual en PostgreSQL | Permisos de Administración | Riesgo entre instituciones | Acción |
|---|---|---|---|---|---|
| Usuarios | Institución (RF-AD-05, `user.institution_id`, D-2) | `user.institution_id` | Solo su institución; otra → 404 | Ninguno detectado (barrido directo al dominio) | Hecho y probado |
| Roles | Institución (RF-AD-06 muestra `institution_id`) | `role.institution_id`, único `(institution_id, code)` | Solo su institución, solo descripción | Ninguno | Hecho y probado |
| Auditoría | Institución (D-2) | `access_audit.institution_id` (nullable) | Filtro obligatorio en SQL; filas sin institución nunca se muestran | Ninguno | Hecho y probado |
| Configuración | Institución (RF-AD-10 "la institución") | `institution` + `app_setting(institution_id, key)` | Solo la propia | Ninguno | Hecho y probado |
| Salas y estaciones | Institución (RF-AD-07 "código de sala duplicado en la institución") | `operating_room.institution_id`; estación vía sala | Solo su institución; estación solo en sala propia | Ninguno | Hecho y probado |
| Familias | **Global por diseño**: vocabulario del detector (RACI "Admin dueño de familias"; `model_class.family_id` del único modelo activo; el SPD las usa para piezas y kits de cada institución) | Sin `institution_id` | Cualquier it_admin, o solo la institución responsable si se define `ADMIN_CATALOG_STEWARD_INSTITUTION_ID` | Un it_admin de B podía modificar o desactivar familias que usa A | **Control nuevo** (steward, opcional) + documentado. No se agrega columna: rompería el mapeo global del modelo y las consultas de OP/SPD/CatalogService |
| Procedimientos y sus fases | Global: "biblioteca que alimenta RF-SP-02"; `cat_procedure_type` es catálogo | Sin `institution_id` | Igual que familias | Igual que familias | Steward + documentado |
| Vínculos procedimiento–kit | Por institución vía `kit.institution_id` (SPD) | `procedure_kit` → `kit.institution_id` | Solo lectura, filtrados a sus kits | Ninguno | Hecho (D-4) |
| Fases, categorías, motivos | Global: listas cerradas `cat_*` que "OP y SPD solo eligen" | Sin `institution_id` | Igual que familias; código inmutable, sin borrado | Igual que familias | Steward + documentado |
| Avisos de privacidad | **Ambiguo**: glosario "PDF/HTML versionado *del hospital*"; v1 = una sola institución ("la única institución", "institución demo") | Sin `institution_id`; SessionService ofrece cualquier aviso activo sin filtrar por institución | Igual que familias | En multi-institución el SPD de A podría ligar un aviso de B | Steward mitiga la escritura. **La solución completa requiere SessionService → propuesta P6, no implementada** |
| Modelos YOLO | Global: "un solo `active = TRUE`" (índice único sobre toda la tabla) | Sin `institution_id` | Igual que familias | Activar un modelo afecta las nuevas sesiones de todas las instituciones | Steward + documentado |
| Kits y piezas | Institución, dueño SPD | `kit` / `instrument.institution_id` | Ninguno en el portal (403) | CatalogService aún acepta escrituras de it_admin | **Propuesta P3** |

Conclusión: los recursos **sí** cumplen el RF v1 (una institución); los catálogos compartidos son globales por diseño del ER, no por omisión. Para despliegues con varias instituciones se debe definir `ADMIN_CATALOG_STEWARD_INSTITUTION_ID` (probado: 17 operaciones de escritura → 403 `shared_catalog_read_only` + `DENIED_*` auditado; la lectura sigue disponible; sin la variable el comportamiento no cambia).

### 11.2 Compatibilidad monolito ↔ microservicios del Supervisor

| Verificación | Resultado | Evidencia |
|---|---|---|
| Misma fuente de datos | CatalogService, SessionService y EvidenceService usan el mismo `DATABASE_URL` (`POSTGRES_DB`) que el portal en `docker-compose.yml` | Revisión de `config.py` y compose |
| Familia creada/desactivada por Admin visible en CatalogService | **Sí** (el token del Supervisor ve la familia y su `active`) | `audit_admin_integration.py` |
| Procedimiento creado por Admin aceptado por SessionService | **Sí** | `audit_admin_integration.py` |
| Aviso desactivado por Admin rechazado al programar | **Sí** (422 `invalid_privacy_notice`, sin escritura) | `audit_admin_integration.py` |
| Fase desactivada por Admin rechazada al programar | **No**: SessionService busca la fase solo por código (`phase_id_by_code`) | **Brecha P5** |
| Sala/estación desactivada rechazada al programar | **No**: `_references` no revisa `active` (revisión de código) | **Brecha P5** |
| Admin no modifica la composición de kits | Portal: 403. CatalogService: `STOCK_WRITERS` incluye `it_admin` (POST /kits pasa la autorización) | **Brecha P3** |
| Familias con un segundo escritor | CatalogService `POST/PUT /families` para it_admin, sin la confirmación de "familia del modelo activo" | **Brecha P3** |
| Altas nuevas con familias inactivas | CatalogService y `admin_local/instruments.py` / `kits.py` (camino SPD) aceptan familias inactivas en el backend | **Brecha P4** |
| Modelo activado por Admin enviado al worker | **Sí**: `vision_bridge.active_model_payload()` envía `version_tag` y `class_map` del modelo activo (las clases rechazadas quedan fuera) | `test_vision_bridge_sends_the_model_activated_by_administration` |
| Pesos que usa VisionWorker | **No** coinciden por construcción: el worker carga `VISION_WEIGHTS_PATH` (archivo local) y no lee `yolo_model`/`media_asset`; el briefing del esquema esperaba que el worker cargara el modelo activo | **Brecha P2** |
| Modelo fijado por sesión (`work_session.yolo_model_id`) | **Nadie lo escribe** (ni el portal del Operador ni SessionService); cada *bind* (cada carga de la página de captura) usa el modelo activo **actual**, así que activar un modelo cambia el `class_map` de sesiones en curso | **Brecha P1** (Operador / VisionWorker) |
| Evidencias de sesión | EvidenceService (bucket `evidence` + Mongo) no participa en archivos de Administración | Sin cruce |
| Documento del aviso para el Supervisor | La programación muestra `document_uri` como texto (`local://` o `s3://`): el Supervisor no tiene enlace de lectura | **Brecha P6** |

### 11.3 Propuestas (requieren tocar Operador, VisionWorker o microservicios del Supervisor: NO implementadas)

- **P1 Fijar el modelo por sesión:** al iniciar la sesión (Operador `rf_session` / SessionService `start`) guardar `work_session.yolo_model_id` = modelo activo; `vision_bridge.bind_session` debe usar el modelo fijado, no el activo.
- **P2 Pesos del worker = modelo de la sesión:** el worker descarga los pesos del modelo fijado (`media_asset` en S3, verificación SHA-256) o, como mínimo, publica en `/health` el SHA-256 de los pesos cargados y el portal advierte en el dashboard si no coincide con el modelo activo.
- **P3 CatalogService:** quitar `it_admin` de `STOCK_WRITERS`; familias de solo lectura en CatalogService (dueño: Administración del monolito) o con las mismas reglas.
- **P4 Altas con familias inactivas:** rechazarlas en CatalogService y en los módulos del camino SPD del monolito.
- **P5 SessionService:** rechazar fase, sala o estación inactivas al programar.
- **P6 Avisos por institución y lectura SPD:** si el equipo decide que el aviso es del hospital, migración `privacy_notice_version.institution_id` (nullable + backfill a la institución actual), alcance en Administración y filtro en SessionService; y una ruta de solo lectura del documento para `spd_supervisor`.

### 11.4 Almacenamiento

| Entorno | Backend | Verificado |
|---|---|---|
| Pruebas automatizadas | `local` (carpeta temporal por prueba) y `s3` (Garage de `docker-compose.test.yml`, bucket `pef-admin-test`) | Subida y lectura de fotos, avisos y pesos en ambos |
| Smoke y auditoría locales | `local` en `.e2e/admin_storage` (gitignored) | Sí |
| `docker-compose.yml` (pila principal) | `local` por defecto: volumen `web_admin_storage` en `/data/admin_storage`; `s3` opcional (`ADMIN_STORAGE_BACKEND=s3`, `S3_INTERNAL_ENDPOINT=http://garage:3900`, claves `S3_*`, `ADMIN_S3_BUCKET=pef-admin`) | Imagen construida; volumen escribible por el usuario no root (999); persistencia entre contenedores; *path traversal* rechazado |

- **Persistencia:** los 39 objetos de Garage creados en días anteriores sobrevivieron al reinicio del equipo y a `docker compose restart garage`; un archivo local escrito por un contenedor se leyó desde otro contenedor nuevo con el mismo volumen.
- **S3 en la pila principal:** misma imagen y modo (`--single-node --default-bucket`) que en pruebas; la clave por defecto **puede crear buckets** (bucket de sondeo creado y eliminado), así que `ADMIN_STORAGE_BACKEND=s3` funciona creando `pef-admin` en el primer uso. Falta: definir la variable en `.env`, tener `infra/garage.toml` (`scripts/prepare_local.py`) y probarlo en la pila completa (pendiente de integración).
- **Coherencia entre composes:** mismas variables `S3_*` y mismo direccionamiento; el compose de pruebas no levanta el portal (las pruebas lo arrancan como proceso).
- **Rutas y permisos:** las claves las genera el servidor (prefijo + uuid + extensión); la lectura local rechaza rutas fuera de la raíz; los archivos se sirven solo a it_admin y solo si están referenciados por un registro de Administración.

### 11.5 Seguridad de `POST /api/v1/auth/users/{id}/sessions/revoke`

| Aspecto | Resultado | Prueba |
|---|---|---|
| Autenticación | Solo *access token* Bearer válido (firma, tipo `access`, sesión Redis no revocada, usuario e institución activos). Sin token, Basic, refresh token, firma alterada, token revocado o llamante desactivado → 401 | `test_revoke_requires_a_valid_access_token` |
| Autorización | Rol efectivo `it_admin` en la institución del llamante; otros roles → 403 + `DENIED_POST_USER` | `test_revoke_is_denied_to_non_administrators_and_audited` |
| Servicio a servicio | No hay credencial de servicio: el portal reenvía el token del administrador que hizo el cambio (delegación). No existe secreto compartido que pueda filtrarse; un proceso en segundo plano no podría revocar | Revisión de código |
| Institución y usuario objetivo | Usuario de otra institución → 404 aunque el llamante tenga un rol admin de esa otra institución; id inválido, inexistente o con inyección → 404 | `test_revoke_cannot_reach_…`, `test_revoke_unknown_or_malformed_target` |
| Revocación efectiva | Revoca acceso **y** refresh (índice Redis por usuario); `/verify` y `/refresh` posteriores → 401; la cuenta puede volver a iniciar sesión | `test_revoke_is_idempotent_and_covers_refresh_tokens` |
| Solicitudes repetidas | Idempotente (segunda llamada `revoked = 0`); cada llamada auditada | ídem |
| Desactivación y reset | Revoca también usuarios ya desactivados; smoke real: tras desactivación y reset el token anterior → 401 y solo la nueva contraseña funciona | `test_revoke_of_a_deactivated_user_still_succeeds`, smoke real |
| Redis caído | 503; el portal muestra aviso y la desactivación ya cierra el acceso por PostgreSQL | `test_revocation_failure_keeps_the_change_and_is_reported` |

### 11.6 Pruebas (2026-11-03)

| Comando | Resultado |
|---|---|
| `python scripts/run_test_env.py admin` | **219 aprobadas** |
| `python scripts/run_test_env.py web` | **447 aprobadas, 3 fallidas** |
| `python scripts/run_test_env.py auth` | **172 aprobadas** |
| `python scripts/run_test_env.py smoke` | OK |
| `python scripts/smoke_admin_test_env.py` | **51/51** (AuthService solo recibe `sessions/revoke` del portal) |
| `python scripts/audit_admin_integration.py` | **9/9** + 2 brechas anotadas (P3, P5) |

Las 3 fallas del Operador (`test_capture_page_shows_the_new_steps`, `test_retry_after_14_of_15_uses_only_the_new_capture`, `test_capture_page_shows_stage_and_mismatch_reason`) se ejecutaron también sobre un *worktree* temporal del commit base `b417170` contra el mismo entorno: **fallan igual y con los mismos mensajes** → preexistentes, no introducidas por Administración.

### 11.7 Estado de cumplimiento con la clasificación solicitada

| RF | Implementado | Pruebas automatizadas aprobadas | Verificado manualmente | Pendiente de pesos reales | Pendiente de integración | Limitaciones conocidas |
|---|---|---|---|---|---|---|
| 01 | Sí | Sí | No | — | Probar en la pila Docker completa | Ping a Auth hasta 2 s si no responde |
| 02 | Sí | Sí | No | — | P3, P4 (CatalogService / camino SPD) | Familias globales (steward en multi-institución) |
| 03 | Sí | Sí | No | — | P5 (fase inactiva en SessionService) | `procedure_kit` sin pantalla SPD; categorías y motivos sin `active` |
| 04 | Sí | Sí | No | — | P6 (lectura SPD, alcance por institución) | Biblioteca global |
| 05 | Sí | Sí + smoke real | No | — | — | Reset sin AuthService: las sesiones viven hasta expirar (con aviso) |
| 06 | Sí | Sí | No | — | — | — |
| 07 | Sí | Sí | No | — | P5 (sala/estación inactivas en SessionService) | — |
| 08 | Sí | Sí (pesos sintéticos) | No | **Sí** (`scripts/verify_weights_names.py`) | P1, P2 (pin de sesión, pesos del worker) | Solo checkpoints zip |
| 09 | Sí | Sí | No | — | — | — |
| 10 | Sí | Sí | No | — | — | Extensión `.pt` fija |

La guía para la verificación manual está en `docs/ADMIN_GUIA_VALIDACION_MANUAL.md`.

### 11.8 Pasos para validar RF-AD-08 con el `best.pt` real

1. `python scripts/verify_weights_names.py <best.pt>`: anota SHA-256, `nc`, arquitectura y nombres leídos **sin ejecutar** el archivo.
2. En el entorno del VisionWorker (archivo de confianza): `python scripts/verify_weights_names.py <best.pt> --ultralytics` → debe decir `MATCH`.
3. Subir el archivo en `/admin/vision-models/new`; comprobar que el checksum mostrado es el del paso 1 y que las clases coinciden.
4. Mapear, publicar y activar; comprobar en `GET {VISION_WORKER_URL}/health` (`weights_path`) que el worker tiene **ese mismo archivo** (hoy hay que copiarlo a `VISION_WEIGHTS_PATH`, ver P2).
5. Abrir la captura de una sesión y verificar que el `class_map` del *bind* corresponde a las clases mapeadas.

## 12. ADMIN-09 — Validación final en navegador y preparación del cierre (2026-11-03)

**Cómo se validó.** Navegador real: Chromium 151 controlado por Playwright 1.62 (`scripts/browser_validate_admin.py`). Corrió contra el entorno aislado `pef_admin_test` con AuthService y el portal levantados por `scripts/run_admin_browser_env.py`; el almacenamiento fue Garage y una pasada final usó el backend local. El script entra por el formulario de *sign-in* y llena formularios, modales y `input[type=file]`. Revisa mensajes visibles, datos tras recargar, consola del navegador, errores de página y respuestas 5xx, y guarda capturas. Solo usa datos de prueba con etiqueta `ZZB…` y una segunda institución "ZZ Browser Institution B" creada en la BD de prueba. Nunca tocó la pila principal.

**Corrida final `222BEF`** (después de corregir H1 y H7): **95 PASS · 0 FAIL · 0 BLOCKED** y 0 respuestas 5xx. Hubo un único error de red del navegador: `net::ERR_ADDRESS_IN_USE` al cargar un recurso de `/admin/privacy-notices/new` durante las subidas grandes. Es un error de sockets de Windows con el servidor de desarrollo, igual al `ERR_NO_BUFFER_SPACE` visto una vez en una corrida de depuración; no se repite y no afecta a ninguna comprobación. Las respuestas 4xx registradas son las esperadas de las pruebas negativas (validaciones 400/409, 403, 404). La evidencia está en `docs/evidencias/ADMIN-09/` (`results.md`, `results.json` y 66 capturas JPEG, unos 5.1 MB). No contiene contraseñas, tokens ni cookies: los campos de contraseña salen enmascarados y los correos son de prueba (`*.invalid`).

> "Pruebas de navegador" = recorrido real de la UI hecho por un navegador automatizado. No es lo mismo que **verificación manual por una persona**, que sigue pendiente para lo listado en §12.6 (lectura visual, idioma es-MX, tema oscuro, otros navegadores).

### 12.1 Estado por RF

| RF | Implementación terminada | Pruebas automatizadas (pytest) | Pruebas de navegador | Evidencia | Defectos encontrados | Estado final de validación |
|---|---|---|---|---|---|---|
| 01 Dashboard | Sí | Aprobadas | **Aprobadas** (4/4): tarjetas y salud; color KPI según umbral (0/0 → *danger*); MongoDB detenido → solo su fila falla y el panel carga en < 3 s | `rf-ad-01_*` | — | Validado (navegador automatizado) |
| 02 Familias | Sí | Aprobadas | **Aprobadas** (9/9): crear, duplicado, editar y persistir, foto visible desde Garage, archivo no imagen y > 5 MB rechazados, confirmación para familia del modelo activo, desactivar/reactivar | `rf-ad-02_*` | — | Validado; P3/P4 pendientes de integración |
| 03 Procedimientos y catálogos | Sí | Aprobadas | **Aprobadas** (8/8): procedimiento con 2 fases en orden; kits visibles sin controles editables; motivo/categoría/fase; código inválido bloqueado en navegador **y** servidor; duplicado; fase desactivada | `rf-ad-03_*` | — | Validado; P5 pendiente de integración |
| 04 Avisos de privacidad | Sí | Aprobadas | **Aprobadas** (10/10): PDF y HTML; **aviso de 9 MB (objeto multiparte en Garage) descargado íntegro**; duplicado con advertencia y confirmación; `.exe` renombrado y PDF > 10 MB rechazados; descarga idéntica (SHA-256) como adjunto; HTML con CSP `sandbox`; desactivar/activar; parámetro "una sola activa" | `rf-ad-04_*` | H7 (corregido) | Validado; P6 pendiente de decisión |
| 05 Usuarios | Sí | Aprobadas | **Aprobadas** (8/8): alta `or_charge` con sala; email duplicado; contraseña corta (navegador + servidor); **la desactivación cierra la sesión abierta del usuario en otro navegador**; reactivación; reset (sesión cerrada, contraseña vieja rechazada, nueva aceptada); sin auto-desactivación; otra institución invisible y 404 | `rf-ad-05_*` | — | Validado |
| 06 Roles | Sí | Aprobadas | **Aprobadas** (5/5): exactamente 4 roles; sin crear/borrar; descripción editable (restaurada); código no editable; `/roles/new` y rol de otra institución → 404 | `rf-ad-06_*` | — | Validado |
| 07 Salas y estaciones | Sí | Aprobadas | **Aprobadas** (8/8): sala, duplicado, estación con ROI ("Configured"), ROI fuera del encuadre y parcial rechazados, sala desactivada visible, sala y estación de otra institución → 404 | `rf-ad-07_*` | — | Validado; P5 pendiente de integración |
| 08 Modelos YOLO | Sí | Aprobadas | **Aprobadas** (11/11) con un `best.pt` real **provisional** (§12.2) | `rf-ad-08_*` | — | **Validación administrativa aprobada**; inferencia real **pendiente (P1/P2)** |
| 09 Auditoría | Sí | Aprobadas | **Aprobadas** (6/6): búsqueda, filtro DENIED, filtro por evento, rango invertido, paginación, otra institución no ve los eventos | `rf-ad-09_*` | — | Validado |
| 10 Configuración | Sí | Aprobadas | **Aprobadas** (4/4): renombrar institución (persistente, restaurado), no puede desactivarse la propia; umbral inválido; lista MIME vacía; contraseña mínima 12 **aplicada** a altas nuevas (restaurada a 8). Límites de tamaño aplicados en RF-02/04/08 | `rf-ad-10_*` | — | Validado |

Transversal: **SEC** 11/11, **SEC-STEWARD** 4/4 y **PERSIST** 6/6 (§12.3 y §12.4). Al final la corrida restaura el estado de la BD de prueba (**RESTORE** 1/1: vuelve a activar el modelo que estaba activo).

### 12.2 RF-AD-08 con el `best.pt` real

- **Origen del archivo:** no está en este repositorio ni en `apps/VisionWorker/weights/` (montaje de la pila principal). Se encontró en dos copias hermanas del proyecto, `Documentos/Desarrollo/PEF_Integrado_Admin_Monolito/apps/VisionWorker/weights/best.pt` y `Documentos/Prueba2/…`, ambas idénticas. Pesa 53 068 645 bytes y su SHA-256 es `6215cb746e20eb9e10c812fe03679b59263bf268b1fb0d419724611db2c785d9`. Solo se leyó: no se copió al repositorio ni se modificó. **Confirmado por el equipo (2026-11-03): es una versión anterior del modelo, válida solo como modelo provisional de pruebas; no es la versión oficial definitiva.** RF-AD-08 debe repetirse con los pesos definitivos cuando existan (`scripts/verify_weights_names.py` + `scripts/browser_validate_admin.py --weights`).
- **`scripts/verify_weights_names.py`:** el lector seguro (no ejecuta el archivo) lee `nc=17`, arquitectura `yolo26l` y tamaño `l`. Con `--ultralytics`, ejecutado dentro de la imagen existente del VisionWorker (`pef-build-check/vision`, sin red y con el repositorio en solo lectura), **los nombres coinciden** con `YOLO(path).names` (salida 0).
- **Flujo administrativo en el navegador, todo aprobado:**
  - `.onnx` rechazado.
  - Con el máximo configurado en 1 MB, el archivo real se rechaza (luego se restauró a 500).
  - Subida real en unos 1.4 s; el paso 2 muestra el checksum, `yolo26l` y las 17 clases en orden.
  - La BD guarda el checksum igual al SHA-256 del archivo, y el objeto queda en Garage (`pef-admin-browser/models/<uuid>.pt`, 53 068 645 bytes).
  - Mapeo de 16 clases y rechazo deliberado de `Surgical probe`. Aparece la advertencia de familias activas sin clase y se publica tras confirmar; se guardan 16 clases, sin la rechazada.
  - Activación: queda como único modelo activo.
  - Subir de nuevo el mismo archivo se rechaza ("These weights are already registered…").
  - Desactivar el único modelo activo con estaciones activas se rechaza.
  - El *payload* de *bind* (`vision_bridge.active_model_payload`) lleva `model_id` y `model_version` del modelo activado y las clases 0–15, sin la 16.
- **Persistencia:** después de reiniciar el portal y el contenedor Garage del proyecto de prueba, el modelo sigue activo con sus 16 clases, y el objeto leído de Garage tiene el mismo SHA-256.
- **Integración de inferencia real: NO validada, depende de P1/P2.** El VisionWorker sigue cargando `VISION_WEIGHTS_PATH` y no los pesos activados en Administración. Además, si en el futuro descarga el objeto de Garage con botocore reciente, debe usar `response_checksum_validation='when_required'` (ver H3).

### 12.3 Seguridad y permisos

- **Los cuatro códigos de rol:** existen en cada institución (migración 009) y **AuthService los reconoce todos** (el `POST /login` devuelve el código). Las diferencias:
  - Roles funcionales del portal: `station_operator` (llega a `/operator/sessions`), `spd_supervisor` (llega a `/supervisor/dashboard`) e `it_admin` (llega a `/admin/dashboard`).
  - `or_charge` (supervisor de piso, RF-AD-05/06) es el cuarto rol del producto. Se asigna en Usuarios junto con sus salas y AuthService lo autentica, pero **el portal no tiene pantallas para él**: muestra "Your account has no application role." en *sign-in*. Es el comportamiento actual por diseño (su interfaz no está en la Parte A); no se modificó ningún rol.
- **Acceso a Administración:**
  - Anónimo → *sign-in*.
  - `spd_supervisor` y `station_operator` → 403 en dashboard, usuarios, modelos y configuración.
  - `or_charge` → sin acceso.
  - `it_admin` → 403 en piezas/kits (son del SPD).
  - Los intentos denegados quedan en la auditoría (DENIED).
- **Aislamiento institucional en la UI:** usuarios, roles, salas, estaciones y auditoría de la institución B no aparecen para A, y sus URL dan 404.
- **Revocación:** la desactivación y el reset cierran la sesión abierta del usuario en otro navegador. Ver también §11.5 (10 pruebas del endpoint).
- **Roles fijos:** no hay acciones de crear ni borrar, `/admin/roles/new` → 404 y el código no es editable.
- **`ADMIN_CATALOG_STEWARD_INSTITUTION_ID`** (probado en navegador reiniciando el portal):
  - Sin definir, cualquier `it_admin` (también el de B) edita los catálogos compartidos.
  - Con `steward = A`, B recibe **403 con el mensaje "Shared catalogs are maintained by the administrators of the steward institution."** en el mismo formulario (defecto H1, corregido). A sigue editando, y B sigue leyendo.
  - **Riesgo** de desplegar varias instituciones sin definirla: el administrador de cualquier hospital puede renombrar o desactivar familias, procedimientos, avisos o **activar otro modelo YOLO para todas las instituciones**. Queda auditado, pero no se impide.
  - **Configuración segura propuesta** para producción multiinstitucional (no aplicada; es decisión del equipo): (1) definir la variable con la institución responsable del catálogo; (2) comprobar al arrancar que el UUID existe; (3) documentarlo en `.env.example` como obligatorio cuando haya más de una institución activa; (4) opcionalmente, que el portal se niegue a arrancar si hay más de una institución activa y la variable está vacía. Ninguna de estas medidas cambia el modelo de permisos actual.

### 12.4 Persistencia y almacenamiento

| Comprobación | Resultado |
|---|---|
| Usuarios, configuración, modelo activo y mapeo tras reiniciar el portal | Aprobado (navegador) |
| Objeto de pesos en Garage tras `restart garage`: mismo SHA-256 | Aprobado |
| Aviso descargado tras reinicio: mismo SHA-256 | Aprobado |
| Foto de familia renderizada tras reinicio | Aprobado |
| Backend **local**: publicar aviso, reiniciar el portal y descargar los mismos bytes | Aprobado |
| Rutas fuera del directorio permitido | Aprobado en pytest (`tests/test_admin_storage.py`: *path traversal* rechazado, claves generadas por el servidor) y en la auditoría §11.4 (volumen Docker) |
| Garage en la **pila principal** | **Pendiente**: no probado para no tocar datos existentes (procedimiento seguro en `GUIA_ADMIN_MONOLITO.md` §4.1) |

### 12.5 Defectos y hallazgos de ADMIN-09

| Id | Tipo | Hallazgo | Acción |
|---|---|---|---|
| H1 | Defecto de Administración (corregido) | Un `it_admin` no responsable que guardaba un catálogo compartido veía la página genérica "Forbidden" de Werkzeug. El guardado sí se bloqueaba, pero sin explicación. | `controllers/admin_routes.py`: los 403 explicables (`shared_catalog_read_only`) se muestran en el formulario o como mensaje, conservando el código 403. Las denegaciones por rol siguen con el 403 simple. Regresión: `test_non_steward_sees_why_the_save_was_refused`, `test_role_denials_still_get_the_plain_forbidden_page`. |
| H2 | Prueba frágil (corregida) | `test_non_steward_administrators_cannot_change_shared_catalogs` tomaba un aviso cualquiera (`LIMIT 1` sin orden) y suponía que estaba activo. | Ahora elige un aviso activo o crea uno. |
| H3 | Riesgo de integración (P2) | Con botocore ≥ 1.36, `get_object` de un objeto **multiparte** de Garage falla con `FlexibleChecksumError`. En Administración se corrigió (H7); un VisionWorker que descargue los pesos tendría el mismo problema. | Documentado en P2: usar `response_checksum_validation='when_required'` y validar con el SHA-256 de `yolo_model.checksum`. EvidenceService usa otro cliente S3: conviene revisarlo cuando suba objetos de más de 8 MB. |
| H7 | **Defecto de Administración (corregido)** | Con `ADMIN_STORAGE_BACKEND=s3`, todo archivo de **más de 8 MB** (aviso de 8–10 MB con el límite por defecto, foto o aviso si se suben los límites) **se guardaba pero no se podía volver a leer**: `upload_file` lo sube en multiparte y Garage devuelve un CRC por partes que botocore 1.43 rechaza. Se detectó al verificar el objeto de pesos en Garage tras el reinicio y se reprodujo con una prueba de 9 MB. | `services/admin_local/storage.py`: el cliente S3 usa `request_checksum_calculation` y `response_checksum_validation='when_required'`; la integridad la da el SHA-256 calculado al recibir. Regresión: `tests/test_admin_storage.py::test_large_objects_round_trip_through_garage` (falla antes y pasa después) y comprobación de navegador "9 MB notice … downloaded intact". |
| H4 | Seguridad transversal del portal (no implementado) | El portal **no usa tokens CSRF ni comprueba `Origin`** en los POST (Operador, Supervisor y Administración). Depende de que las cookies sean `SameSite=Lax`; `docker-compose.yml` y `.env.example` lo fijan, pero **el valor por defecto en el código de AuthService es `None`**. | **Propuesta P7**: comprobar `Origin`/`Referer` en los POST/PUT/DELETE del portal (o Flask-WTF CSRF), y que AuthService use `Lax` por defecto. Afecta formularios del Operador y del Supervisor, así que necesita aprobación. |
| H5 | Configuración (no implementado) | En `docker-compose.yml` el servicio `web` no depende de `garage`. Con `ADMIN_STORAGE_BACKEND=s3`, una subida hecha antes de que Garage esté listo daría error. | Añadir `garage` a `depends_on` cuando se active S3 (§4.1 de la guía). |
| H6 | Observación (por diseño) | `or_charge` no tiene interfaz en el portal. | Documentado (§12.3). |
| — | Entorno de prueba | El lanzador de navegador necesitaba `AUTH_COOKIE_SAMESITE=Lax` igual que la pila principal: Chrome rechaza `SameSite=None` sin `Secure`. Ya corregido en `scripts/run_admin_browser_env.py`; no es un defecto del producto. | — |

### 12.6 Pendiente de verificación manual por una persona

Lo que la automatización no puede juzgar, en `docs/ADMIN_GUIA_VALIDACION_MANUAL.md` §3:

1. Revisión visual de textos y estética de las capturas.
2. Recorrido en **es-MX** y en tema oscuro.
3. Otro navegador (Firefox o Edge).
4. Subida del `.pt` real por red lenta (tiempo de *gunicorn*).
5. Garage en la pila principal.
6. Inferencia real con el VisionWorker (depende de P1/P2).

### 12.7 Regresión (2026-11-03, después de H1, H2 y H7)

| Suite | ADMIN-08 | ADMIN-09 |
|---|---|---|
| Admin (`run_test_env.py admin`) | 219 aprobadas | **222 aprobadas** (+2 regresión H1, +1 regresión H7) |
| BackendWebFlask (`web`) | 447 aprobadas / 3 fallidas | **450 aprobadas / 3 fallidas**: las mismas 3 del Operador, con los mismos mensajes que en `b417170` (`autoplay` en la captura WS; `[14]` ≠ `[14, 14]`; falta "Initial count registered…") |
| AuthService (`auth`) | 172 aprobadas | **172 aprobadas** |
| Smoke SQLite (`smoke`) | OK | **OK** |
| Smoke real (`smoke_admin_test_env.py`) | 51/51 | **51/51** |
| Integración Admin–microservicios (`audit_admin_integration.py`) | 9/9 + 2 notas | **9/9 + las mismas 2 notas** (P3, P5) |
| Navegador (`browser_validate_admin.py`) | — | **95/95** |

### 12.8 Propuestas pendientes de decisión (no implementadas)

P1–P6 (§11.3) siguen **pendientes de integración o de decisión arquitectónica**. Se añade **P7** (CSRF/`Origin` en el portal y `SameSite=Lax` por defecto en AuthService). Ninguna se implementó en ADMIN-09.
