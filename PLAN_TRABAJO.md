# PEF — Plan maestro de desarrollo y seguimiento

**Proyecto:** Conteo y trazabilidad de instrumental quirúrgico  
**Base:** `PEF-Instrumental-quirurgico-Consolidado-WS-DB-v2` (ZIP 1 como núcleo; fuentes de ZIP 2; WebSocket/esquema de ZIP 3)  
**Última actualización del plan:** 2026-11-10 (MS-11: desarrollo de las 20 responsabilidades e integración en Docker Compose)  
**Estado general:** Administración en `5600edd`; MS-01 en `7caf1de`; MS-02 en `766ebf2` (rama `microservices_phase2`). **MS-03 + MS-03.1 en `c7355d2`; MS-04 en `1a1c7a9`.** **MS-07 + MS-08 en `62542e4`; MS-09 en `bc680c6`; MS-10 en `6ae2aa4`; MS-11A/B en `10807db`. MS-11 terminado sin commit** (tracking real en VisionWorker, cámaras configuradas en el worker y fuentes simuladas, reconexión WSS, evaluaciones de modelos, notificaciones de escalamiento y alertas de componentes, `db-migrate`; E2E de Docker Compose de los tres roles 31/31; 6 defectos de integración corregidos — `docs/MS_DIAGNOSTICO_Y_PLAN.md` §23). Historial — MS-11A/B (inventario de solo lectura, comparación, respaldo con verificación, plan por etapas, seguridad, prueba de humo por nginx; riesgos R1–R11 verificados en CentOS Stream 10 — `docs/MS11_PREPARACION_DESPLIEGUE.md`; **no se tocó la VM; MS-11C requiere autorización**). Historial — MS-10 (arquitectura verificada contra el código 57/57; base nueva y arranque concurrente sin *deadlock*; Operador y Supervisor en navegador real 33/33 con YOLO real; defecto del formulario de escalamiento oculto corregido; guía de despliegue reescrita con inventario y comandos de inspección separados; decisiones pendientes D1–D5; lista de aceptación — `docs/MS_DIAGNOSTICO_Y_PLAN.md` §21). Historial — MS-09 (contingencia verificada, escalamiento de finales rechazados, 3 pruebas históricas del Operador corregidas — portal sin fallos —, charolas consolidadas, P6 preparado y pendiente, seguridad de infraestructura y guía de despliegue GCP/CentOS 10 — `docs/MS_DIAGNOSTICO_Y_PLAN.md` §20). Historial — MS-05 + MS-06 en `136a698`; MS-07 + MS-08: (IntegrationService: sincronización, operaciones idempotentes, FHIR R4 de solo lectura y entregas probadas con HAPI de pruebas; clientes máquina en AuthService/Administración; charolas; P6 pendiente de decisión; bloqueador de contingencia registrado — `docs/MS_DIAGNOSTICO_Y_PLAN.md` §19). Historial — MS-05/06: (ver bitácora y `docs/MS_DIAGNOSTICO_Y_PLAN.md` §15–§18): registro de cámaras, latido de estaciones verificado por el servidor, alertas técnicas, reportes de resultados/conteo automático/estaciones, exportación CSV/PDF auditada, notificaciones nuevas y canal de correo opcional. **No empezar MS-07 sin autorización.** Historial — MS-04: API Gateway (nginx, TLS en producción), CSRF del portal (P7 resuelta) y validación de contratos. Antes, MS-03.1: el conteo final debe ser igual a la referencia vigente, sin confirmación que lo omita; re-captura del final; la validación humana solo corrige la clasificación (GLOBAL-01/02 corregidos). **MS-03 terminado sin commit**: el Supervisor ve la evidencia real (imágenes de EvidenceService/Garage, detecciones, modelo y conteos) en Session Details y Review Detail, y el WebSocket en vivo se corta en el servidor al revocar. E2E real 19/19; ejecutor 1641/1641. Pendientes: P6, P7, aceptación manual. **No empezar MS-04 sin autorización.**

### Decisiones ratificadas por el equipo (2026-11-01)

- **D-1 Revocación:** al restablecer contraseña o desactivar un usuario se revocan sus sesiones **mediante AuthService y Redis**. Reutilizar la lógica existente antes de crear un endpoint nuevo (ver hallazgo en ADMIN-01.0).
- **D-2 Alcance institucional:** cada `it_admin` solo consulta y modifica usuarios y recursos **de su propia institución** (usuarios, roles, auditoría y catálogos). Obligatorio. Límite documentado: familias, procedimientos, catálogos de selección, avisos y modelos no tienen `institution_id` en el esquema y son compartidos.
- **D-3…D-6 (resueltas en ADMIN-01):** D-3 salud por pings locales; D-4 `procedure_kit` solo lectura para Admin (propiedad SPD); D-5 convención de auditoría UPPER_SNAKE con equivalencias al RF; D-6 parámetros persistentes en `app_setting` (ver matriz §6).
- AuthService se conserva como autenticación central de los tres roles; la administración de usuarios/roles se hace desde el monolito sobre la misma base de identidad. No modificar ni eliminar microservicios del Supervisor; conservar WebSockets y VisionWorker.

> **Este es el archivo principal de trabajo.** Claude debe leerlo al comienzo de cada intervención, respetar el orden de las etapas, actualizar sus casillas y agregar una entrada al registro de trabajo **solo tras ejecutar y verificar cambios**. El contrato funcional prevalece sobre este plan ante cualquier contradicción.

## 1. Reglas de arquitectura y alcance (no negociables)

1. **Administrador TI (`it_admin`): MONOLITO para operaciones de negocio.** Sus pantallas, controladores, validaciones y operaciones de RF-AD-01 a RF-AD-10 deben residir en `apps/BackendWebFlask/`. **Excepción explícita y obligatoria: AuthService compartido para autenticación de Admin**, igual que para Operador y Supervisor (login, logout, JWT, verificación del usuario/roles, refresh y revocación en Redis). **No crear autenticación local ni eliminar llamadas a AuthService necesarias para iniciar y mantener sesiones.** La administración CRUD de datos (usuarios, roles, familias, auditoría, etc.) debe seguir la regla monolítica y usar la misma fuente de identidad, sin duplicar credenciales.
2. **Operador (`station_operator`): MONOLITO para operaciones de negocio, AuthService para autenticación compartida.** Preservar login/logout/JWT/refresh/verificación/revocación a través de AuthService, además de capturas, conteos, persistencia y flujo v3. La transmisión técnica de vista previa a `VisionWorker /ws` ya existe; debe documentarse y validar su compatibilidad con la regla estricta del equipo. La vista previa **nunca** es conteo final autorizado.
3. **Supervisor SPD (`spd_supervisor`): AuthService compartido para autenticación y MICROservicios para operaciones de negocio.** Consolidar `apps/SessionService/` como propietario de sesiones, comparaciones, discrepancias y supervisión. No activar simultáneamente otro SupervisorService con escrituras duplicadas.
4. **Administración no es dueña de instrumentos físicos individuales ni de kits**: son responsabilidad del Supervisor SPD. Puede consultarlos si un RF requiere lectura, pero no debe existir CRUD de estas entidades bajo el rol Admin.
5. **RF-AD-11 (solicitudes ARCO) está EXCLUIDO.** No implementar su pantalla, sus endpoints ni sus pruebas como tarea de este plan. Los modelos históricos pueden permanecer si otras partes los necesitan. Tampoco implementar ahora la Parte B (App Supervisor de piso) de `rf_admin_app.md`: requeriría un alcance separado.
6. No perder ni sustituir módulos por una versión menos completa. Mantener `Legacy/` y las fuentes de portabilidad. **No reconstruir desde cero** implementaciones existentes sin justificación ni modificar el modelo YOLO entrenado.
7. No modificar ni destruir datos/volúmenes existentes. Antes de SQL, respaldar y revisar migraciones. Nunca hacer `docker compose down -v` para “arreglar” un problema. Prohibido subir `.env`, claves, pesos `.pt` o credenciales.
8. Los 20 puntos del profesor son **responsabilidades**, no 20 procesos obligatorios. Renumeración de puertos 5301–5320 y distribución definitiva se hace **después** de cerrar Admin, con contratos y pruebas.
9. **Regla de conteo físico pendiente y obligatoria:** referencia vigente = conteo inicial + incorporaciones validadas por Supervisor. Si última captura final detecta 14 y la referencia es 15, NO avanzar a validación humana; permitir nueva captura hasta igualar. La validación humana puede corregir **clasificación**, nunca justificar cantidad física menor.
10. No dar por “terminado” ningún RF sin prueba de aceptación y evidencia. El código que existe pero no está integrado es **parcial**, no completado.

### Fuentes de verdad que deben consultarse

- **RF Admin:** `data/FunctionalRequirements/rf_admin_app.md` (únicamente Parte A, RF-AD-01…10).
- **RF Operador / Supervisor:** `data/FunctionalRequirements/rf_operador_supervisor_spd.md`.
- **Antecedentes de consolidación:** `docs/PLAN_CONSOLIDACION_Y_ADMIN.md` y `docs/MATRIZ_PROCEDENCIA.md`.
- **WebSockets y BD:** `docs/REVISION_WS_Y_BD_ZIP3.md` y `data/migrations/008_consolidacion_zip3.sql`.
- **Guías:** `GUIA_ADMIN_MONOLITO.md`, `GUIA_AUTH.md`, `GUIA_DOCKER_INTEGRAL.md`, `GUIA_PRUEBAS.md`.
- **Código activo Admin:** `apps/BackendWebFlask/controllers/admin_routes.py`, `controllers/admin_presenters.py`, `services/admin_local/`, `templates/admin/`, `tests/test_admin_routes.py`, `tests/test_admin_dashboard_isolation.py`.
- **Fuentes históricas:** `Legacy/porting_sources/AdminService_zip3/`, `SupervisorService_zip2/` y `VisionRealtime_zip3/`.

> **Decisión de arquitectura ratificada:** Admin y Operador **sí usan AuthService** para autenticación; lo que permanece en Flask monolítico son sus operaciones de negocio. Supervisor también usa AuthService. La prohibición de microservicios para Admin/Operador no aplica a este servicio transversal de autenticación.

## 2. Convenciones de seguimiento

- `[x]`: implementado **y validado** según la aceptación descrita. Para la consolidación inicial hay casillas “estructura integrada” que no implican E2E.
- `[ ]`: pendiente, parcialmente implementado o no verificado. Registrar el estado concreto en la matriz.
- Para cada tarea completada registrar **fecha, archivos modificados, comandos de prueba, resultado, limitaciones y commit** (si existe) en el apartado 7.
- Cada sesión de Claude modifica **un bloque acotado** y deja los demás sin alterar; primero revisa el diff y los tests antes de declarar avance.
- Al fallar una prueba, no marcar la tarea como concluida; registrar el fallo y mantenerla pendiente.

## 3. Fase 0 — Base consolidada / estabilización inicial

**Objetivo:** mantener el mejor punto de partida y garantizar que sabemos qué funciona antes de alterar Admin.

- [x] Estructura V1 con BackendWebFlask, Auth, Catalog, Session, Evidence, Audit, Notifications, Monitoring y VisionWorker.
- [x] Fuentes exclusivas de ZIP 2 preservadas en `Legacy/porting_sources/SupervisorService_zip2/`; **no activadas**.
- [x] Fuentes Admin ZIP 3 conservadas en `Legacy/porting_sources/AdminService_zip3/`; **no activadas**.
- [x] WebSocket de vista previa de ZIP 3 conectado a la captura Flask y a `VisionWorker /ws`, con separación del conteo autoritativo V1.
- [x] Esquema adicional ZIP 3 incluido en modelos/migración `008_consolidacion_zip3.sql`.
- [x] **BASE-01:** crear rama de trabajo antes de cambios; revisar `git status` sin borrar cambios existentes. **Hecho (2026-11-01, ADMIN-01.0):** `git init -b main`, commit base `b417170` (781 archivos, sin `.env`, pesos, BD ni `__pycache__`; verificado con `git status --ignored`), rama de trabajo `admin-01`. `.gitignore` ampliado (`*.db`, `*.sqlite`, `*.dump`, `*.bak`, `backups/`, `.env.test`, `docker-compose.override.yml`) y `.gitattributes` (LF para `*.sh`, `*.sql`, `Dockerfile`, YAML/TOML, porque `core.autocrlf=true`). Sin remoto configurado.
- [x] **BASE-02:** comprobar arranque de Docker, salud de dependencias, migraciones en una BD de prueba y rutas de los tres roles. **Hecho para el alcance Admin (2026-11-01):** Docker Desktop 29.8.1 iniciado; `docker-compose.yml` validado con `config --quiet` (15 servicios) **sin levantarlo**; entorno aislado `docker-compose.test.yml` (PG 16, Redis 7, Mongo 7) sano; migraciones 001→008 aplicadas por `initdb` sin errores; 004–008 re-ejecutables (idempotentes) sobre una copia; login/verify/refresh/logout de los 3 roles probados (smoke real 34/34). No se levantó la pila completa de microservicios (fuera del alcance de esta tarea).
- [ ] **BASE-03:** correr suite de pruebas inicial, guardar comandos, aprobadas/fallidas y causas. No asumir que Python/JS sintácticamente válidos implican E2E. **Parcial (2026-11-01):** con PG/Redis/Mongo de prueba: BackendWebFlask **302 ok / 3 fallidas / 0 omitidas**; BackendAuthService **159 ok / 0 / 0**; Admin **74 ok / 0 / 0**. Faltan suites de Catalog/Session/Evidence/Audit/Notifications/Monitoring/VisionWorker (no requeridas para ADMIN-01).
- [ ] **BASE-04:** documentar problemas preexistentes; diferenciar defectos de base vs. regresiones nuevas. **Parcial:** las 3 fallas restantes son del **Operador** y preexistentes (sin cambios en `apps/` desde el commit base): 1 conflicto entre prueba V1 y vista previa WebSocket ZIP 3 (`<video … autoplay>` en `capture_rf.html:185`), 2 funcionales de regla/pantalla de conteo (bloque GLOBAL-01/02). Ver `docs/ADMIN_MATRIZ_CUMPLIMIENTO.md` §7.2. `scripts/smoke_admin_monolith_sqlite.py` falla en `POST /admin/roles/new` (503, roles remotos; además espera crear un código arbitrario, contrario a RF-AD-06). `GUIA_ADMIN_MONOLITO.md` desactualizada.

**Nota:** esta fase puede ejecutarse como auditoría previa en paralelo con la primera tarea de Admin, pero no bloquear el avance por ausencia de hardware YOLO. Se debe registrar qué pruebas fueron imposibles.

## 4. Fase 1 — Cerrar ADMIN en el monolito (PRIORIDAD ACTUAL)

**Definición de terminado de toda la fase:** RF-AD-01…10 cubiertos, las rutas `/admin/*` son locales en sus operaciones de negocio, permisos y aislamiento institucional correctos, auditoría registrada, y pruebas automatizadas más smoke test manual documentados. RF-AD-11 excluido.

### ADMIN-00 — Diagnóstico inicial y plan de cambio seguro (PRIMER ENCARGO A CLAUDE)

- [x] Recorrer todos los RF-AD-01…10 contra rutas, templates, modelos, lógica, tests y esquema SQL. Crear una matriz **requisito → endpoint/UI → módulo → dependencia remota → carencia → prueba**. → `docs/ADMIN_MATRIZ_CUMPLIMIENTO.md` §3.
- [x] Localizar las llamadas de negocio en `services/admin_local/facade.py` (`identity_remote`, `catalog_remote`, `audit_remote`). Verificar por separado que el login, la verificación de sesión y el refresh de `controllers/routes.py` sigan usando `AUTH_SERVICE_URL` para **los tres roles**; esas llamadas son correctas y NO deben eliminarse. → §2 (verificación estática: `routes.py:191-243, 246-259, 406-415`; despacho remoto `facade.py:27-36`).
- [x] Revisar pantallas de Instrumentos y Kits (`/admin/instruments`, `/admin/kits`), sus permisos, menú y el contrato SPD; diseñar el retiro de la facultad de escritura Admin **sin borrar la función del Supervisor**. → §4 (las URLs son compartidas con SPD; se restringe escritura a `spd_supervisor` en dominio y rutas, no se eliminan).
- [x] Establecer y probar la línea base: tests ya existentes, arranque si el entorno lo permite y observaciones de UI; documentar lo que no pueda ejecutarse. → §7 (ejecutado sin PG/Redis/Mongo; Docker no disponible; arranque y UI no probados, bloqueo justificado).
- [x] Proponer la implementación monolítica de **gestión administrativa de usuarios y roles**, manteniendo **autenticación centralizada en AuthService para todos los roles**. No duplicar contraseñas ni crear una segunda fuente de verdad; considerar revocación Redis, sesiones activas, rol `it_admin`, institución y sincronización de cambios con la verificación de tokens. → §5 y decisiones D-1/D-2.
- [x] Publicar resultado en `docs/ADMIN_MATRIZ_CUMPLIMIENTO.md` y actualizar este plan. **No refactorizar todos los RF en una sola intervención.**

**Aceptación ADMIN-00:** matriz real por RF y archivos, dependencias identificadas, pruebas de base ejecutadas o con bloqueo justificado, plan de refactor sin pérdida de funciones. **→ Cumplida 2026-11-01 (diagnóstico). No implica ningún RF verificado.**

**Hallazgos clave de ADMIN-00 (detalle en la matriz):**
- Usuarios/roles 100 % remotos (`identity_remote` → AuthService CRUD); no hay `users.py`/`roles.py` locales (sí en `Legacy/porting_sources/AdminService_zip3/services/`).
- `.env.example` activa `CATALOG_SERVICE_URL` y `AUDIT_SERVICE_URL` → familias/piezas/kits y audit log de Admin salen por HTTP con la configuración de referencia.
- **Defecto de seguridad:** `admin_local/audit_log.py` no filtra por institución (fuga cross-tenant cuando no hay AuditService).
- Roles: la UI permite crear/borrar códigos arbitrarios; `or_charge` no existe en semillas, código ni AuthService.
- Admin escribe piezas/kits (rutas compartidas con SPD) y `procedure_kit` desde Procedimientos (propiedad SPD según RF).
- Faltan: avisos de privacidad, `family_example`, ROI, subida `.pt`/SHA-256/extracción, `user_operating_room`, KPIs por estado de sesión, rango de fechas en audit, parámetros visibles.
- AuthService verifica `user.active`/`institution.active` y roles en PG en cada petición → el CRUD local se refleja de inmediato; solo el **reset de contraseña** requiere revocación explícita (D-1).

**Decisiones pendientes del equipo:** D-1 revocación Redis, D-2 alcance institucional de usuarios/roles, D-3 health local vs. MonitoringService, D-4 propiedad de `procedure_kit`, D-5 nombres de acciones de auditoría, D-6 parámetros RF-AD-10 (ver matriz §6).

### ADMIN-01 — Arquitectura local, identidad y roles (bloque prioritario)

**Subpasos verificables (definidos en ADMIN-00; ejecutar uno por intervención):**

- [x] **ADMIN-01.0 Entorno de prueba (2026-11-01):** git + rama `admin-01`; Docker activo; entorno aislado `docker-compose.test.yml` (proyecto `pef_admin_test`, puertos 127.0.0.1:55432/56379/47017, volúmenes `pef_admin_test_*`, credenciales en `.env.test` no versionado); migraciones verificadas; suites y smoke ejecutados con PG real. Las pruebas que fallaban solo por falta de PG ya no fallan con el entorno; **no se tocaron** (siguen sin ser herméticas: queda como mejora, no bloquea). Reproducción:

  ```powershell
  Copy-Item .env.test.example .env.test   # y cambiar las contraseñas (solo la primera vez)
  docker compose -f docker-compose.test.yml --env-file .env.test up -d --wait
  python -m pip install -r apps/BackendWebFlask/requirements.txt -r apps/BackendAuthService/requirements.txt pytest
  python scripts/run_test_env.py web      # BackendWebFlask completo
  python scripts/run_test_env.py admin    # subconjunto administrativo
  python scripts/run_test_env.py auth     # BackendAuthService
  python scripts/run_test_env.py smoke    # smoke SQLite heredado (falla conocida en roles)
  python scripts/smoke_admin_test_env.py  # smoke REAL Auth + Admin contra la BD de prueba
  docker compose -f docker-compose.test.yml --env-file .env.test stop   # detener SIN borrar datos
  ```

  **Hallazgo para D-1 / ADMIN-01.3:** AuthService ya tiene la lógica de revocación (`token_store.RedisTokenStore.revoke_user_tokens`, envuelta por `identity_admin.revoke_sessions`), pero **solo se dispara como efecto de sus endpoints CRUD** (`/users/<id>/deactivate`, `/password`, `PUT /users/<id>`). No existe endpoint de revocación independiente. Propuesta: exponer esa misma función en un endpoint de sesión mínimo (p. ej. `POST /api/v1/auth/users/<id>/sessions/revoke`, solo `it_admin` de la misma institución, auditado) y llamarlo desde el monolito tras el commit. No duplicar el formato Redis en Flask.
- [x] **ADMIN-01.1 Roles fijos (RF-AD-06) — 2026-11-01:**
  - **Migración `data/migrations/009_roles_fijos.sql`** (montada como `09_roles_fijos.sql` en `docker-compose.yml` y `docker-compose.test.yml`): inserta los 4 códigos de producto (`station_operator`, `spd_supervisor`, `it_admin`, `or_charge`) en cada institución que no los tenga, `ON CONFLICT (institution_id, code) DO NOTHING`. No borra, renombra ni reasigna roles; conserva descripciones y `user_role`. Verificada: BD existente `pef_test` (1.ª ejecución `INSERT 0 1` = `or_charge`; 2.ª `INSERT 0 0`), copia `pef_test_migcheck` (igual), instalación nueva `pef_test_fresh` (001→009 sin errores, 4 roles), y dentro de pruebas con instituciones nuevas y con una descripción previa personalizada.
  - **`services/admin_local/roles.py`** (nuevo): listar/ver/editar descripción **solo de la institución del administrador** (D-2; un rol de otra institución responde 404), orden fijo, `assigned_users`, auditoría `UPDATE_ROLE` solo si cambia; código inmutable (`role_code_immutable`, 400); `POST /roles` y `DELETE /roles/<id>` se rechazan en el dominio (`role_codes_fixed`, 405) y se auditan como `DENIED_CREATE_ROLE` / `DENIED_DELETE_ROLE`. No-administradores: 403 + `DENIED_<MÉTODO>_ROLE`.
  - **Despacho:** `api.py` registra `/roles`; `facade.py` ya no envía `/roles` ni el catálogo `roles` a AuthService; `identity_remote.py` solo maneja `/users` (hasta ADMIN-01.2). El catálogo `roles` del formulario de usuarios es local y limitado a la institución.
  - **UI:** rutas `/admin/roles/new` y `/admin/roles/<id>/delete` retiradas (404); lista sin "New Role"; formulario solo de edición (código, institución y usuarios asignados de solo lectura). Mismo diseño. Etiqueta "Floor Supervisor" y descripción de `or_charge` traducidas (es-MX, `.po` + `.mo` recompilado).
  - **AuthService (sin tocar login/JWT/refresh/logout/Redis):** `or_charge` añadido a `SYSTEM_ROLES` (no se puede borrar) y `POST /api/v1/auth/roles` solo acepta los 4 códigos de producto (`422 role_code_not_allowed`); `openapi.yaml` actualizado. Sigue leyendo roles y descripciones desde PostgreSQL en cada `/verify`, `/refresh`, `/me` y `/permissions`.
  - **Pruebas:** nuevo `tests/test_admin_roles.py` (13, PG real); adaptado `tests/test_identity_remote.py` (roles ya no remotos; pruebas de usuario herméticas); AuthService: `test_roles_lifecycle_and_protection` reescrita al contrato RF-AD-06 y nueva `test_fixed_role_codes_are_read_from_postgresql`. Smoke SQLite reescrito en roles (ya no crea `nurse_reviewer`; ahora verifica 404 de crear/borrar, edición, auditoría y aislamiento) y **pasa completo por primera vez**; `scripts/e2e_admin.py` sección 6 adaptada (solo validada sintácticamente; requiere la pila completa en 5000/5001); `scripts/smoke_admin_test_env.py` ampliado.
  - **Resultados (entorno aislado):** Web **314 ok / 3 fallidas** (las 3 del Operador ya conocidas; 0 regresiones); Admin **86 ok**; AuthService **160 ok**; smoke SQLite **OK**; smoke real **36/36**, 0 llamadas del portal a `/api/v1/auth/roles`.
  - **Pendiente:** smoke manual en navegador de la pantalla de roles; `or_charge` no tiene acceso al portal web (sin `ROLE_LABELS`, App = Parte B fuera de alcance); política de tokens `default` para `or_charge` en AuthService (sin cambios).
- [x] **ADMIN-01.2 Usuarios locales (RF-AD-05):** `admin_local/users.py` (reglas de `identity_admin.py`, mismo advisory lock, hash werkzeug, institución propia). **Verificado con pruebas automatizadas (2026-11-01)**; prueba manual en navegador pendiente.
- [x] **ADMIN-01.3 Revocación:** endpoint `POST /api/v1/auth/users/{id}/sessions/revoke` en AuthService (reutiliza `revoke_user_tokens`) + `admin_local/auth_sessions.py`; smoke real: tras reset y desactivación el token previo da 401. **Verificado con pruebas automatizadas (2026-11-01)**; prueba manual en navegador pendiente.
- [x] **ADMIN-01.4 Salas de SdP:** `user_operating_room` solo para `or_charge`, salas de la institución, auditoría `CHANGE_USER_ROOMS`. **Verificado con pruebas automatizadas (2026-11-01)**; prueba manual en navegador pendiente.
- [x] **ADMIN-01.5 Cierre:** `facade.py` ya no despacha a `identity_remote` (marcado obsoleto, sin borrar); fixture `no_remote_http` y smoke real (solo `sessions/revoke` llega a AuthService). **Verificado con pruebas automatizadas (2026-11-01)**; prueba manual en navegador pendiente.

- [x] Implementar adaptadores **locales** dentro de `BackendWebFlask` para usuarios y roles, sin despachar RF-AD-05/06 por HTTP a AuthService.
- [x] **Mantener sin cambios funcionales la autenticación de Admin, Operador y Supervisor a través de AuthService** (smoke real 51/51: login/refresh/logout/revocación de los 3 roles).
- [x] RF-AD-05: CRUD y activación/desactivación de usuarios, password inicial/reset, `user_operating_room`, último `it_admin`, auditoría y aislamiento institucional. **Verificado con pruebas automatizadas (2026-11-01)**; prueba manual en navegador pendiente.
- [x] RF-AD-06: **exactamente cuatro códigos fijos** (`station_operator`, `spd_supervisor`, `it_admin`, `or_charge`). Permitir editar descripción, bloquear crear/eliminar/cambiar códigos. → ADMIN-01.1 (pruebas automatizadas; smoke manual pendiente).
- [x] Refactorizar `admin_local/facade.py` para no invocar `identity_remote` en Administración.
- [x] Probar 401/403, último admin, contraseñas, revocación, roles, usuarios inactivos y aislamiento por institución (`test_admin_users.py`, `test_admin_roles.py`, smoke real).

**Aceptación:** RF-AD-05/06 funcionales y locales para el negocio administrativo; **los tres roles siguen autenticándose en AuthService** (login, logout, verificación, refresh y revocación probados); permisos y consistencia de identidad verificados.

### ADMIN-02 — Familias y catálogos de selección

- [x] RF-AD-02: familias locales, `family_example` + `media_asset`, subida segura (magic bytes, límites RF-AD-10, SHA-256, local/S3), confirmación al desactivar familia del modelo activo. **Verificado con pruebas automatizadas (2026-11-01)**; prueba manual en navegador pendiente.
- [x] RF-AD-03: procedimientos con fases; fases/categorías/motivos (`/admin/selection-catalogs`), código único inmutable, sin borrado, auditoría; `procedure_kit` solo lectura (D-4). **Verificado con pruebas automatizadas (2026-11-01)**; prueba manual en navegador pendiente.
- [x] Eliminar dependencia administrativa de `catalog_remote` (solo se usa para el Supervisor SPD si `CATALOG_SERVICE_URL` está configurado).
- [x] Retirar permisos Admin sobre **instrumentos físicos** y **kits**: rutas y dominio solo `spd_supervisor` (403 a it_admin), fuera del sidebar Admin; Supervisor sin cambios.
- [x] Tests CRUD, referencias, desactivaciones, archivos inválidos, permisos y aislamiento (`test_admin_families.py`, `test_admin_procedures.py`, `test_admin_routes.py`).

**Aceptación:** RF-AD-02/03 completos, negocio Admin local y propiedad de piezas/kits respetada.

### ADMIN-03 — Avisos de privacidad

- [x] RF-AD-04: biblioteca versionada, PDF/HTML por magic bytes, límites RF-AD-10, SHA-256 (duplicado con confirmación), vigencia, activar/desactivar, descarga segura, auditoría. **Verificado con pruebas automatizadas (2026-11-01)**; prueba manual en navegador pendiente.
- [x] No escribir `session_processing_agreement` (verificado en `test_admin_privacy.py`).
- [x] Versiones inmutables: una sesión existente conserva su versión (solo cambia `active`).
- [x] Probar duplicados, archivo inválido, recuperación, almacenamiento S3 real y controles de acceso. Límite: la tabla no tiene `institution_id` (biblioteca compartida).

**Aceptación:** biblioteca funcional en el monolito, auditoría y conservación de versiones.

### ADMIN-04 — Salas, estaciones y ROI

- [x] RF-AD-07: CRUD de salas/estaciones con ROI `{"shape":"rect",x,y,w,h,frame_w,frame_h}` validado. **Verificado con pruebas automatizadas (2026-11-01)**; prueba manual en navegador pendiente.
- [x] Probar código de sala único por institución, estación sin sala, ROI inválido, desactivación sin perder histórico y aislamiento (`test_admin_configuration.py`).

**Aceptación:** datos de configuración válidos y editables desde Admin.

### ADMIN-05 — Modelos YOLO

- [x] RF-AD-08: carga real de `.pt` con límite, SHA-256 en backend, storage y `media_asset`. **Verificado con pruebas automatizadas (2026-11-01)**; prueba manual en navegador pendiente. **Validación con `best.pt` real pendiente** (no disponible en el equipo).
- [x] Extracción segura de clases sin ejecutar el pickle (`yolo_weights.py`, probado con pickle malicioso); fallback `names.yaml`/JSON.
- [x] Overlay y confirmación de familias activas faltantes, rechazo explícito de clases, checksum duplicado bloqueado, activación sin `model_class` bloqueada.
- [x] Activación transaccional de un solo modelo activo; `work_session.yolo_model_id` de sesiones existentes intacto; no se deja cero activos con estaciones activas.
- [x] Tests de duplicados, mapping, permisos y almacenamiento S3 (`test_admin_vision_models.py`, 23). Concurrencia de activación: protegida por el índice único `uk_yolo_model_active`; sin prueba de carrera dedicada.

**Aceptación:** publicación y activación correctas en BD/storage, sin alterar sesiones iniciadas.

### ADMIN-06 — Dashboard, auditoría y configuración

- [x] RF-AD-01: KPIs por status de la institución con umbrales, familias activas/inactivas, modelos, usuarios inactivos, salud Auth/PG/Redis/Mongo/storage (D-3), secciones independientes. **Verificado con pruebas automatizadas (2026-11-01)**; prueba manual en navegador pendiente.
- [x] RF-AD-09: audit log local solo lectura, institución obligatoria en SQL (filas sin institución excluidas), filtros usuario/acción/resultado/**rango de fechas**, paginación SQL. **Verificado con pruebas automatizadas (2026-11-01)**; prueba manual en navegador pendiente.
- [x] RF-AD-10: institución (no se desactiva la propia) y parámetros persistentes por institución (`app_setting`, migración 010) con validación y auditoría. **Verificado con pruebas automatizadas (2026-11-01)**; prueba manual en navegador pendiente.
- [x] Retirar dependencia de `audit_remote` para lecturas Admin (AuditService sigue para el Supervisor).
- [x] Probar métricas, fallo de dependencias (Auth/Redis/Mongo caídos, sección con error de BD), filtros de fechas, tenant scope y auditoría de cambios.

**Aceptación:** RF-AD-01/09/10 correctos, comprobables y locales.

### ADMIN-07 — Regresión final y cierre de la fase

- [x] Verificar cada RF-AD-01…10 contra el contrato y evidencias de pruebas (`docs/ADMIN_MATRIZ_CUMPLIMIENTO.md` §10). Pendiente: prueba manual en navegador.
- [x] Confirmar que Administración no envía HTTP de negocio a Catalog/Audit/AdminService ni CRUD a AuthService (fixture `no_remote_http`; smoke real: solo `sessions/revoke`). Login/logout/verify/refresh/revocación intactos.
- [x] Confirmar `AdminService` NO activo en Compose ni requerido (no existe servicio `admin` en `docker-compose.yml`).
- [x] Confirmar que no hay alta/edición Admin de piezas/kits (403) y que el Supervisor conserva sus capacidades (suite completa sin regresiones; `test_catalog_remote.py` para SPD).
- [x] Probar login, logout, roles, recuperación de errores, permisos de no Admin, auditoría, institución y cambios de estado (suites Admin 190, AuthService 162, smoke real 51/51).
- [x] Completar la matriz con resultado verificado, rutas y pruebas (§10). RF-AD-11 sigue excluido.

**Puerta de salida:** no comenzar el desarrollo nuevo de microservicios hasta cerrar ADMIN-07 o documentar y acordar explícitamente las excepciones.

### Fase 2 — Microservicios del Supervisor (MS)

#### MS-12 — Adaptación a los 20 dominios funcionales del profesor (2026-11-10) — TERMINADO (commit local autorizado)

Detalle: `docs/MS12_DOMINIOS_FUNCIONALES.md` (matriz, puertos 5301–5320 → 5001–5009, contratos, pruebas, clientes) y `docs/MS_DIAGNOSTICO_Y_PLAN.md` §24. Diagramas editables: `docs/diagramas/*.drawio`.

- [x] Fase 0: commit de respaldo `949699d` (MS-11).
- [x] Fase 1–2: matriz de los 20 dominios y delimitación; sin procesos nuevos (9 servicios + portal; dominios compartidos justificados).
- [x] Fase 3 (brechas técnicas, migración 019): equivalencias autorizadas de kit (CatalogService + pantalla del Supervisor + aviso al Operador); calibración medida de cámaras (Administración, al *bind*); bitácora completa de tracking (`tracking_event`, lectura del Supervisor); sello de integridad de la validación humana (verificación en SessionService); corridas de regresión de modelos con inferencia real; lotes trazables de sesiones cerradas (IntegrationService); monitoreo de IntegrationService y su *worker*; recuperación de evidencias por ventana de tiempo; verificador de consistencia de la auditoría.
- [x] Fase 4: contratos (OpenAPI/XSD de Catalog, Session, Integration, Monitoring) y rutas del Gateway.
- [x] Fase 5: 4 diagramas draw.io (`scripts/make_ms12_diagrams.py`).
- [x] Fase 6–7: matriz de pruebas por dominio; E2E Docker Compose integral con M1–M10.
- [x] Fase 8: análisis de las apps de escritorio y Android (no existen; API disponible y faltantes documentados).
- [ ] Decisiones nuevas D6 (uso clínico de equivalentes), D7 (firma de la validación), D8 (destino de los lotes) — `docs/DECISIONES_PENDIENTES.md`.
- [x] Commit local de MS-12 autorizado (sin push).

#### MS-03 — Evidencias reales del Operador para el Supervisor (2026-11-04) — TERMINADO (sin commit)

Detalle: `docs/MS_DIAGNOSTICO_Y_PLAN.md` §12.

- [x] Commit de MS-02: `766ebf2`.
- [x] Revisión del caso 4/5 del E2E de MS-02: el Operador no quedó bloqueado, llegó a la validación humana y cerró su parte (`correction_required` con discrepancias para el Supervisor).
- [x] Evidencia real para el Supervisor:
  - Nuevo `services/supervisor_evidence.py` y ruta de imagen autorizada (sesión + pertenencia del activo + URL firmada y auditada por EvidenceService).
  - *Partial* v3 en Session Details → Timeline y en Review Detail.
  - `vision_model` en el detalle de SessionService.
  - Sin servicio nuevo y sin cambios en EvidenceService. La demo WS-026 queda separada.
- [x] Revocación del WebSocket activo **en el servidor**:
  - Ticket con usuario y caducidad (120 s), `session.renew`, `POST /auth/revoke` firmado.
  - *Hooks* en desactivar, restablecer y cerrar sesión; el cliente se detiene con un mensaje.
  - El `sign-out` revoca también el *refresh token*.
- [x] Pruebas: E2E real **19/19**; WS real 7/7; portal 17/17; ejecutor **1641/1641**; Web **490 / 3** (las mismas); Admin 222; smoke real 51/51; integración 13/13; E2E MS-02 13/13.
- [ ] Recorrido de aceptación manual del Supervisor.
- [ ] Commit de MS-03 (solo con autorización).

#### MS-02 — Trazabilidad de modelos YOLO e integración real con VisionWorker (2026-11-04) — TERMINADO (sin commit)

Detalle: `docs/MS_DIAGNOSTICO_Y_PLAN.md` §11.

- [x] Commit de cierre de MS-01: `7caf1de`, con el recorrido del Supervisor en navegador anotado como pendiente.
- [x] **P1:** `work_session.yolo_model_id` se fija una vez.
  - Cuándo: al iniciar la sesión de visión, en la transacción bloqueada del portal y de SessionService; si no había modelo activo, en el primer *bind*.
  - Se audita (`session.model_pinned`) y queda en el evento `session_open`.
  - Es inmutable: migración `011_session_model_pin.sql`.
- [x] **P2:** el VisionWorker infiere con los pesos del modelo fijado.
  - *Bind* firmado con HMAC.
  - Origen de los pesos: caché, `best.pt` empaquetado solo si coincide su SHA-256, volumen de Administración en solo lectura, o URL S3 prefirmada desde un host permitido.
  - SHA-256 verificado antes de cargar; LRU en memoria; compatibilidad de clases comprobada.
  - El portal rechaza conteos inferidos con otros pesos. Nunca hay sustitución.
- [x] Pruebas: VisionWorker +26 (2 con inferencia real), portal +15, SessionService +4 (incluida una concurrente real).
- [x] E2E real 13/13 (`scripts/e2e_ms02_model_pin.py`): inicio del Operador, descarga desde Garage, video, conteos, WebSocket en Chromium, reconexión, cambio de modelo a mitad de sesión, modelo sin pesos.
- [x] Resultados: ejecutor **1633/1633** (0 omitidas); Web **473 / 3** (las mismas 3 del Operador); Admin 222; smoke real 51/51; integración 13/13.
- [ ] El modelo semilla ya no infiere: hay que subir y activar los pesos reales en la pila principal.
- [ ] Revocación con un WS abierto (limitación; la propuesta requiere tocar el cliente del Operador).
- [ ] Repetir con los pesos definitivos.
- [ ] Commit de MS-02 (solo con autorización).

#### MS-01 — Respaldo, pruebas unificadas y P3/P4/P5 (2026-11-03) — TERMINADO (sin commit)

Detalle: `docs/MS_DIAGNOSTICO_Y_PLAN.md` §10.

- [x] Commit local de respaldo de Administración `5600edd` en `admin-01`. Quedaron fuera las 66 capturas, `.env`, `.pt` y el documento MS. Antes de confirmar se revisó que no hubiera secretos. Rama nueva `microservices_phase2`. Sin push.
- [x] `scripts/run_ms_tests.py`:
  - Las 8 suites contra el entorno aislado, con resultados del XML JUnit y clasificación CODE/INFRA.
  - Una omisión no cuenta como aprobada; código de salida 1/2/0.
  - VisionWorker en la imagen `pef-ms-test/vision`. Documentado en `GUIA_PRUEBAS.md` §2.1.
  - Línea base: **1532 aprobadas, 0 omitidas**.
- [x] **P3:** CatalogService. Piezas y kits solo `spd_supervisor`; familias solo `it_admin`, con institución responsable opcional. Las denegaciones se auditan. La variable se pasa a `catalog` y `web` en Compose.
- [x] **P4:** familias inactivas rechazadas al introducirlas (422 `inactive_family`) en CatalogService y en el respaldo SPD local del portal; las referencias históricas se conservan.
- [x] **P5:** sala, estación, kit y fase inactivos rechazados al programar (422 `inactive_*`) en SessionService y en el respaldo local del Supervisor; al editar se conservan las referencias existentes. El procedimiento no tiene estado activo en el esquema.
- [x] Resultado:
  - Ejecutor **1603/1603** (+71 pruebas).
  - Web **458 / 3** (las 3 del Operador, preexistentes); Admin 222; smoke real 51/51.
  - Integración **13/13 sin NOTE**.
- [ ] Recorrido en navegador del Supervisor (no ejecutado; cobertura de API e integración).
- [ ] Residual de P3: la API de familias no exige confirmar la desactivación de una familia del modelo activo (decisión de contrato).
- [ ] Commit de MS-01 (solo con autorización).

#### MS-00 — Diagnóstico y planificación (2026-11-03) — TERMINADO, sin implementación

Detalle completo: `docs/MS_DIAGNOSTICO_Y_PLAN.md`.

- [x] Inventario real de los 9 servicios (código, rutas, Compose, BD y autenticación), con sus suites ejecutadas contra el entorno aislado:
  - Auth 172, Catalog 179, Session 697, Evidence 323 (+11 omitidas: esperan credenciales Garage en el `.env` del repo), Audit 48, Notifications 40, Monitoring 41.
  - Vision 17 (+4 omitidas por MongoDB; ejecutadas en su imagen).
  - Web 450 / 3 fallidas (Operador, preexistentes).
- [x] Hallazgos que no estaban registrados:
  - **No hay API Gateway**, y los microservicios solo se publican en `127.0.0.1`: hoy no hay acceso posible para Android/Desktop.
  - La vista de evidencias del Supervisor está **simulada** (solo la demo WS-026).
  - `vision-counts` y `count-report` de SessionService no tienen consumidor web.
  - Notificaciones solo dentro de la aplicación.
  - Integración hospitalaria solo como esquema (`integration_client`, DS08).
  - Ningún microservicio reconoce `or_charge`.
- [x] Matriz de las 20 responsabilidades. Agrupación propuesta: 8 servicios existentes + 1 nuevo opcional (IntegrationService) + API Gateway de infraestructura.
- [x] Análisis técnico de P1–P7, con archivos, tablas, solución, pruebas y riesgos.
- [x] Plan por etapas:
  - **MS-01** integridad del Supervisor (P3/P4/P5) y ejecutor `ms`.
  - **MS-02** trazabilidad del modelo (P1/P2; requiere autorizar cambios en el VisionWorker y en el inicio del Operador).
  - **MS-03** evidencias reales para el Supervisor.
  - **MS-04** API Gateway + P7 + contratos JSON/XML.
  - **MS-05** cámaras y estaciones.
  - **MS-06** reportes y notificaciones.
  - **MS-07** sincronización e integración FHIR.
  - **MS-08** decisiones P6 y "charola".
- [ ] Revisión del plan por el equipo y autorización de MS-01.
- [ ] Commit de respaldo de Administración (solo con autorización).

### ADMIN-09 — Validación final y preparación del cierre (2026-11-03)

Detalle: `docs/ADMIN_MATRIZ_CUMPLIMIENTO.md` §12 · evidencia: `docs/evidencias/ADMIN-09/results.md`.

- [x] Entorno aislado verificado: PostgreSQL, Redis, MongoDB, Garage, AuthService y portal. Lanzador para pruebas en navegador: `scripts/run_admin_browser_env.py`.
- [x] **Pruebas en navegador real** (Chromium + Playwright, `scripts/browser_validate_admin.py`) de RF-AD-01 … RF-AD-10, seguridad, *steward* y persistencia: **95 PASS · 0 FAIL · 0 BLOCKED**, con 66 capturas.
- [x] **RF-AD-08 con el `best.pt` real** (SHA-256 `6215cb74…85d9`, 17 clases `yolo26l`, tomado en solo lectura de `Documentos/Desarrollo/PEF_Integrado_Admin_Monolito/apps/VisionWorker/weights/`):
  - Nombres de clase que coinciden con Ultralytics.
  - Subida, checksum, mapeo con una clase rechazada, activación, duplicado, límite de tamaño y `.onnx` rechazado.
  - Persistencia tras reinicio y *payload* de *bind*.
  - **Confirmado por el equipo (2026-11-03): es una versión anterior del modelo, válida solo como modelo provisional de pruebas; no es la versión oficial definitiva.** RF-AD-08 debe repetirse con los pesos definitivos cuando existan (`scripts/verify_weights_names.py` + `scripts/browser_validate_admin.py --weights`). La inferencia real con esos pesos sigue pendiente (P1/P2).
- [x] Seguridad:
  - Los 4 roles existen y AuthService los reconoce; `or_charge` no tiene pantallas en el portal (por diseño).
  - Sin acceso de otros roles a `/admin/*`; aislamiento por institución en la UI.
  - La revocación cierra la sesión abierta en otro navegador.
  - *Steward* probado con y sin la variable; riesgo de multi-institución sin definirla y configuración segura propuesta (§12.3).
- [x] Persistencia: Garage y backend local tras reinicios. Procedimiento seguro para usar Garage en la pila principal en `GUIA_ADMIN_MONOLITO.md` §4.1 (no aplicado).
- [x] Defectos de Administración corregidos con prueba de regresión:
  - **H1**: el 403 de catálogo compartido mostraba la página genérica "Forbidden".
  - **H7**: con S3, los archivos de más de 8 MB no se podían volver a leer.
  - **H2**: prueba frágil.
- [ ] **Revisión manual por una persona** (M1–M6 de la guía §3).
- [ ] Decisión sobre **P1–P7**. Nueva **P7**: CSRF/`Origin` en el portal y `SameSite=Lax` por defecto en AuthService (H4). Configuración: `depends_on: garage` al activar S3 (H5).
- [ ] Commit de cierre de Administración: **solo con autorización expresa**.

### ADMIN-08 — Auditoría de integración y arquitectura (2026-11-03)

Clasificación usada: **Implementado** · **Pruebas automatizadas aprobadas** · **Verificado manualmente** · **Pendiente de pesos reales** · **Pendiente de integración** · **Limitación conocida** (detalle por RF en `docs/ADMIN_MATRIZ_CUMPLIMIENTO.md` §11.7).

- [x] Alcance institucional por recurso analizado contra RF/ER (§11.1). Usuarios, roles, auditoría, configuración, salas y estaciones: institucionales y probados con barrido directo al dominio. Familias, procedimientos, catálogos, avisos y modelos: **globales por diseño** (no se agregan columnas). Nuevo control opcional `ADMIN_CATALOG_STEWARD_INSTITUTION_ID` para que en multi-institución solo la institución responsable escriba los catálogos compartidos (17 operaciones, `tests/test_admin_institution_scope.py`).
- [x] Compatibilidad con microservicios del Supervisor verificada con datos de prueba (`scripts/audit_admin_integration.py`, 9/9): familias de Admin visibles en CatalogService, procedimientos aceptados y avisos inactivos rechazados por SessionService; el *bind* al VisionWorker envía el modelo activado por Admin (`test_vision_bridge_sends_the_model_activated_by_administration`).
- [x] Almacenamiento: local y Garage (S3) con persistencia tras reinicio, lectura de avisos/fotos/pesos, rutas protegidas (`tests/test_admin_storage.py`); la clave de Garage puede crear el bucket de Administración (§11.4).
- [x] Seguridad del endpoint `sessions/revoke`: 10 pruebas nuevas en AuthService (autenticación, autorización, institución, idempotencia, refresh tokens, usuarios desactivados) (§11.5).
- [x] Fallas del Operador comparadas con la línea base `b417170` en un worktree temporal: idénticas → preexistentes.
- [x] Guía de verificación manual preparada (`docs/ADMIN_GUIA_VALIDACION_MANUAL.md`) y pasos para el `best.pt` real (`scripts/verify_weights_names.py`, §11.8).
- [ ] **Verificación manual en navegador** de los diez RF (no ejecutada: sin herramienta de navegador).
- [ ] **RF-AD-08 con `best.pt` real**.
- [ ] Decisiones del equipo sobre las propuestas que tocan Operador / VisionWorker / Supervisor (no implementadas):
  - **P1** fijar `work_session.yolo_model_id` al iniciar la sesión y que el *bind* use ese modelo (hoy nadie lo escribe y cada *bind* usa el modelo activo actual).
  - **P2** VisionWorker debe cargar los pesos del modelo fijado (o informar su SHA-256 en `/health`); hoy usa `VISION_WEIGHTS_PATH`.
  - **P3** CatalogService: `it_admin` aún es escritor de kits/piezas y tiene un CRUD de familias paralelo.
  - **P4** altas de piezas/kits con familias inactivas aceptadas en el backend (CatalogService y camino SPD del monolito).
  - **P5** SessionService acepta fase, sala o estación inactivas al programar.
  - **P6** avisos de privacidad por institución (si se decide) y enlace de lectura del documento para el Supervisor.

### Matriz de situación de Admin (actualizada al cierre de ADMIN-01, 2026-11-01)

| RF | Nombre | Estado diagnosticado | Dependencia remota actual | Tarea de cierre |
|---|---|---|---|---|
| 01 | Dashboard | **CUMPLE (automatizado)** | Ninguna (salud: pings locales; `/admin/system-status` sigue usando MonitoringService como vista técnica) | ADMIN-06 ✔ |
| 02 | Familias y examples | **CUMPLE (automatizado)** | Ninguna | ADMIN-02 ✔ |
| 03 | Procedimientos/catálogos | **CUMPLE (automatizado)**; `procedure_kit` solo lectura (D-4) | Ninguna | ADMIN-02 ✔ |
| 04 | Avisos privacidad | **CUMPLE (automatizado)** | Ninguna | ADMIN-03 ✔ |
| 05 | Usuarios | **CUMPLE (automatizado + smoke real)** | AuthService solo para revocar sesiones (D-1) | ADMIN-01.2–01.5 ✔ |
| 06 | Roles | **IMPLEMENTADO Y VERIFICADO (pruebas automatizadas, 2026-11-01)**: 4 códigos fijos (009), solo descripción, institución propia, crear/borrar rechazados en backend y UI; smoke manual en navegador pendiente | Ninguna (local); AuthService solo lee `role` para autenticar | ADMIN-01.1 ✔ |
| 07 | Salas/estaciones | **CUMPLE (automatizado)** | Ninguna | ADMIN-04 ✔ |
| 08 | Modelos visión | **CUMPLE (automatizado)**; validación con `best.pt` real pendiente | Ninguna | ADMIN-05 ✔ |
| 09 | Audit log | **CUMPLE (automatizado)**; defecto cross-tenant corregido | Ninguna (AuditService solo Supervisor) | ADMIN-06 ✔ |
| 10 | Configuración | **CUMPLE (automatizado)** | Ninguna | ADMIN-06 ✔ |
| 11 | ARCO | **FUERA DE ALCANCE** | — | No implementar |

> "CUMPLE (automatizado)" = pruebas automatizadas aprobadas contra PostgreSQL/Redis/MongoDB/Garage reales; la prueba manual en navegador sigue pendiente para todos. Detalle en `docs/ADMIN_MATRIZ_CUMPLIMIENTO.md` §10.

## 5. Fase 2 — Microservicios (DESPUÉS de Admin)

- [ ] **MS-00:** comparar `SessionService` contra `Legacy/porting_sources/SupervisorService_zip2`; aportar solo funciones faltantes y retirar duplicidad de responsabilidad.
- [ ] **MS-01:** Auth (5301): servicio transversal de **autenticación para los tres roles** (Admin, Operador, Supervisor): JWT, refresh, logout, verificación, revocación Redis y controles de institución/estación. Admin/Operador conservan su lógica de negocio monolítica; Supervisor utiliza microservicios de negocio.
- [ ] **MS-02:** Catálogos/Kits (5302/5316): familias/tipos/modelos, kits, composiciones versionadas y equivalencias; Admin local como dueño de ciertos maestros y SPD dueño de recetas/piezas. Definir contrato y dueño de escritura por entidad.
- [ ] **MS-03:** Sesiones/Supervisión (5303/5312/5313/5314/5315): estados y versiones, conteos, comparación, discrepancias, intervención humana, review y cierre.
- [ ] **MS-04:** Evidencias (5304), auditoría (5305) y notificaciones (5306): imágenes, videos, hashes, trazabilidad, avisos y recuperación.
- [ ] **MS-05:** Monitoreo (5307) y cámaras (5308): salud, errores, latencia, configuración ROI/calibración y asociación a estación.
- [ ] **MS-06:** WSS/video (5309), YOLO (5310), tracking (5311): pasar del preview a solución de seguimiento estable sin duplicar instrumentos. Preservar protocolo de conteo autoritativo.
- [ ] **MS-07:** Modelos IA (5317), reportes/Highcharts (5318): métricas, datasets, regresión, despliegue/rollback y datos reales.
- [ ] **MS-08:** Sincronización (5319) e integración hospitalaria JSON/XML (5320): contratos, idempotencia, resolución de conflictos y adaptadores de prueba si no hay externos reales.
- [ ] **MS-09:** puertos acordados con profesor, API contracts, seguridad y pruebas E2E por dominio. **No crear 20 procesos por obligación**.

## 6. Fase 3 — Validación global / bloqueadores

- [x] **GLOBAL-01 / BLOQUEADOR CRÍTICO (corregido en MS-03.1, 2026-11-04, sin commit; `docs/MS_DIAGNOSTICO_Y_PLAN.md` §13):** corregir `apps/BackendWebFlask/services/rf_session.py::confirm_final_count()` que permite `acknowledge_mismatch=True`; bloquear el avance con total físico distinto y permitir repetir captura final sin alterar conteo inicial.
- [x] **GLOBAL-02 (MS-03.1: pruebas deterministas en portal y SessionService + E2E real; concurrencia con reenvíos):** verificar referencias ajustadas (`instruments_added`) y escenarios 15→14 (no avanza), 15→15 (sí avanza), 15+2→17 (sí avanza), clasificación incorrecta y concurrencia.
- [ ] **GLOBAL-03:** pruebas con Docker, PostgreSQL, Redis, MongoDB, Garage, cámara, WebSocket y `best.pt` reales; validar reconexión sin duplicar eventos.
- [ ] **GLOBAL-04:** pruebas de permisos y cross-tenant, errores 400/401/403/404/409/422, auditoría, rollback y flujos de los tres roles.
- [ ] **GLOBAL-05:** actualizar diagramas, README, contrato OpenAPI, guías de arranque y video de demostración. No declarar “listo para uso clínico”.

## 7. Bitácora cronológica de trabajo (CLAUDE: ACTUALIZAR EN CADA INTERVENCIÓN)

| Fecha | Tarea | Qué se hizo y archivos clave | Pruebas ejecutadas y resultado | Estado / commit |
|---|---|---|---|---|
| 2026-11-10 | MS-12 Adaptación a los 20 dominios funcionales | Commit de respaldo `949699d`. Migración 019 (equivalencias de kit, calibración de cámaras, bitácora de tracking, regresión de modelos, lotes de exportación; historiales inmutables). CatalogService equivalencias; Administración calibración y regresión (`scripts/model_regression.py`, inferencia real); portal: bitácora persistida, sello de la validación, pantalla de equivalencias, aviso al Operador, sección de integridad del Supervisor, recuperación de evidencias por ventana de tiempo (**defecto corregido**: 5 intentos ≈ 15 s); SessionService `tracking-events` y `validation-seal`; IntegrationService lotes (`/exports`, alcance `export.sessions.batch`); MonitoringService `integration` e `integration-worker`; Gateway; `scripts/check_audit_consistency.py`; 4 diagramas draw.io; `docs/MS12_DOMINIOS_FUNCIONALES.md`; D6–D8; 83 traducciones. | Ejecutor 9 suites **1845/1845** (Auth 199, Catalog 245, Session 762, Evidence 334, Audit 48, Notifications 56, Monitoring 70, Integration 57, Vision 74 con inferencia real), 0 omitidas; Web **664**; Admin 222; Auth 199; arquitectura 58/58; migraciones (base nueva, reaplicación, 3 arranques simultáneos) 9/9; smoke SQLite OK; smoke real 51/51; integración Admin 13/13; E2E gateway 32/32 (contratos 207/207, 0 huecos, incluye las rutas nuevas); MS-02 13, MS-03 19, MS-03.1 19, MS-05 14, MS-06 16, MS-07/08 25, MS-09 7; navegador 33/33; **E2E Docker Compose integral 41/41** (dos ejecuciones; la primera 40/41 por una verificación que no esperaba al outbox asíncrono, corregida en la prueba); regresión real de modelo 3/3 videos idénticos; auditoría de la base de pruebas: 3146 filas sin violaciones. | MS-12 TERMINADO / commit local (sin push) |
| 2026-11-10 | MS-11 Responsabilidades e integración en Docker Compose | Commit de respaldo `10807db`. R11 tracking (`tracking.py`, húngaro con predicción, oclusiones, histéresis, eventos; medido en videos reales); R08 configuración de cámara hacia VisionWorker y tipo `simulated`; R09 reconexión de la vista previa; R17 evaluaciones de modelos inmutables con comparación; R06/R07 `final_count_escalated` y `service_alert` con transiciones de disponibilidad; migraciones 017 y 018; Compose: `db-migrate`, healthcheck por TCP, Gateway con re-resolución; `scripts/compose_ms11.py`, `scripts/e2e_ms11_compose.py`, `scripts/measure_tracking.py`; guías TRACKING y ARRANQUE_DEMO. 48 traducciones. | Ejecutor **1815/1815**; Web **619**; Admin 222; Auth 199; smoke SQLite OK; smoke real 51; integración 13; E2E MS-02 13, MS-03 19, MS-03.1 19, gateway 32 (0 huecos), MS-05 14, MS-06 16, MS-07/08 25, MS-09 7; **E2E Compose integral 31/31**; navegador 33/33 (sin errores de JavaScript); concurrencia 11/11. | MS-11 TERMINADO / sin commit |
| 2026-11-09 | MS-11A/B Preparación del despliegue GCP | Commit de MS-10 `6ae2aa4` (incluye los 11 ejemplos de `infra/vm/env/`; `monitoring.env.example` completado). `scripts/vm/`: `inspect_vm.sh` (solo lectura, probado en CentOS Stream 10 sin fugas de secretos), `compare_inventory.py`, `s3_mirror.py` (respaldo y restauración de Garage, probado), `smoke_post_deploy.py` + `smoke_local_harness.py` (Gateway TLS real). Hallazgos verificados en CentOS 10: Valkey en lugar de Redis (compatible: Auth 199, Session 752, Integration 47), nombres de paquetes, nginx 1.26.3, redirección 80→443 ignorada por conflicto con el servidor por defecto (corregido en `render_gateway_vm.py`), VisionWorker `degraded` sin `VISION_WEIGHTS_PATH` (mitigado), unidades ordenadas después de `valkey.service`. Guía de despliegue: respaldo y verificación (§5.1). | Prueba de humo por Gateway TLS real **31 PASS · 0 FAIL · 3 SKIP** (justificados); arquitectura 57/57; suites contra Valkey: Auth 199, Session 752, Integration 47; `systemd-analyze verify` sin errores; `nginx -t` en 1.26.3; respaldo y restauración de Garage verificados. VM: **no inspeccionada**. | MS-11A/B PREPARADO / sin commit; MS-11C pendiente de autorización |
| 2026-11-09 | MS-10 Cierre técnico y preparación del despliegue | Commit de MS-09 `bc680c6`. `scripts/verify_architecture.py` (57/57), `scripts/check_migrations_concurrency.py` (base nueva + 5 arranques simultáneos), `scripts/browser_validate_flows.py` (Operador y Supervisor en Chromium, YOLO real). Defectos corregidos: formulario de escalamiento oculto en la recaptura (+ regresión), ejemplos de entorno de la VM fuera del repositorio (`.gitignore`), credenciales S3 del portal ausentes en `web.env.example` (+ *preflight*), guía de despliegue sin el seed obligatorio 002. Documentos: `docs/DECISIONES_PENDIENTES.md`, `docs/CHECKLIST_ACEPTACION.md`, guías DESPLIEGUE (reescrita), PRUEBAS §2.9, CONTEO_CONTINGENCIA, SEGURIDAD_INFRAESTRUCTURA. | Arquitectura 57/57; base nueva y concurrencia 10/10; ejecutor **1791/1791**; Web **598/598**; Admin 222; Auth 199; smoke SQLite OK; smoke real 51/51; integración 13/13; E2E MS-02 13/13, MS-03 19/19, MS-03.1 19/19, gateway 32/32 (0 huecos), MS-05 14/14, MS-06 16/16, MS-07/08 25/25, MS-09 7/7; navegador Operador+Supervisor **33/33**, Administración **95/95**. | MS-10 TERMINADO / sin commit |
| 2026-11-09 | MS-09 Consolidación, seguridad del conteo, despliegue | Commit de MS-07 + MS-08 `62542e4`. Contingencia verificada en el servidor (rechazo de afirmaciones contradichas, verificada/declarada, reconocimiento del Supervisor, `close_basis`, `approved_contingency`); escalamiento de finales rechazados (portal + SessionService); 3 pruebas históricas del Operador corregidas (pruebas desactualizadas / fixture); charolas: historial de asignaciones, cambio programado, FOR SHARE, migración 016; P6: propuesta SQL no aplicada + opciones; clave de bloqueo de migraciones compartida (deadlock real); `infra/vm/` (systemd, entornos, nginx, Garage TLS), `render_gateway_vm.py`, `preflight_production.py`; guías CONTEO_CONTINGENCIA, SEGURIDAD_INFRAESTRUCTURA, DESPLIEGUE_GCP_CENTOS10; 17 traducciones. | Ejecutor **1791/1791**; Web **597/597 (0 fallos)**; Admin 222; Auth 199; smoke SQLite OK; smoke real 51/51; integración 13/13; E2E MS-02 13/13, MS-03 19/19, MS-03.1 19/19, gateway 32/32 (0 huecos), MS-05 14/14, MS-06 16/16 ×3, MS-07/08 25/25, MS-09 7/7. | MS-09 TERMINADO / sin commit |
| 2026-11-09 | MS-07 + MS-08 Sincronización, FHIR, charolas, P6 | Commit de MS-05 + MS-06 `136a698`. Fase 1: casos A (regla respetada) y B (contingencia sin evidencia de fallo → cierre seguro: bloqueador registrado, sin cambios). Nuevo `apps/IntegrationService` (sync con marca de agua xid8 y cursor firmado, operaciones idempotentes con reconciliación, FHIR R4 de solo lectura, entregas con reintentos, worker). Migraciones 014 y 015. AuthService `client_credentials.py` (`/token`, revocación). Administración: clientes máquina. CatalogService: charolas; SessionService: `tray_id` y sesiones por charola; portal: charolas del Supervisor. Gateway y Compose (`integration`, `integration-worker`). Contratos: 47 huecos → 0. P6: informe de solo lectura y propuesta. Guías: SINCRONIZACION, INTEGRACION_FHIR, CHAROLAS, PRIVACIDAD_P6, API_GATEWAY, PRUEBAS §2.7. 53 traducciones. | E2E MS-07/08 **25/25** (Gateway + HAPI FHIR real); ejecutor **1784/1784** (9 suites, IntegrationService 47); Web **577 / 3** (las mismas 3); Admin 222; Auth 199; smoke SQLite OK; smoke real 51/51; integración 13/13; E2E MS-02 13/13, MS-03 19/19, MS-03.1 19/19, gateway 32/32 (contratos 0 violaciones, 0 huecos), MS-05 14/14, MS-06 15/15. | MS-07 + MS-08 TERMINADOS / sin commit |
| 2026-11-09 | MS-05 + MS-06 Estaciones, cámaras, monitoreo, reportes y notificaciones | Commit de MS-04 `1a1c7a9`. Migraciones 012 (`capture_camera`, `station_status`, `station_event`, `work_session.camera_id`) y 013 (tipos `review_pending`, `session_unresolved`, `station_alert`; `notification_delivery`), idempotentes. MonitoringService: latido, estaciones, alertas, evidencia de cámara desde VisionWorker `/activity`. Portal: cámaras en Administración, selección de cámara y latido del Operador, estaciones y alertas en Salud de servicios, reportes ampliados (resultados, conteo automático, intervenciones, estaciones, filtro) y exportación CSV/PDF. SessionService: `/reports/stations`, `/reports/export` (auditada, protección de fórmulas, fpdf2). NotificationsService: tipos nuevos y canal de correo opcional. Gateway: solo el latido publicado; exportación bloqueada. 108 traducciones. Guías: MONITORING §8, NOTIFICATIONS §7, REPORTES §8, PRUEBAS §2.6, API_GATEWAY; `MS_DIAGNOSTICO_Y_PLAN` §15–§18. | E2E MS-05 **14/14**; E2E MS-06 (Parte C) **15/15**; ejecutor **1718/1718**; Web **565 / 3** (las mismas 3); Admin 222; Auth 189; smoke SQLite OK; smoke real 51/51; integración 13/13; E2E MS-02 13/13, MS-03 19/19, MS-03.1 19/19, gateway 32/32 (contratos: 0 violaciones, 47 huecos). | MS-05 + MS-06 TERMINADOS / sin commit; no iniciar MS-07 sin autorización |
| 2026-11-08 | MS-04 API Gateway, CSRF (P7) y contratos | Commit de MS-03 + MS-03.1 `c7355d2`. Gateway nginx (`infra/gateway/`, servicio `gateway` con perfil, `docker-compose.gateway-tls.yml`): lista explícita de rutas, Bearer únicamente, límites, TLS en producción con el portal en un mismo origen y puertos de `auth`/`web` cerrados. Portal: `services/csrf.py` (token firmado ligado al usuario, `Origin`/`Referer`), campo en 47 formularios, `static/js/csrf.js`, cookies `SameSite`/`Secure`/`HttpOnly`. AuthService: `AUTH_TRUSTED_PROXIES` (IP del cliente sin falsificación, también en la auditoría), `SameSite=Lax` por defecto, guardián de `Origin` en las rutas por cookie. Scripts: `portal_csrf.py`, `validate_contracts.py`, `e2e_gateway.py`. Guías: `GUIA_API_GATEWAY.md`, `GUIA_PRUEBAS.md` §2.5, `GUIA_AUTH.md` §5.1. Propuestas registradas: contingencia técnica y posible faltante físico (§14.6). | E2E gateway **31/31**; contratos 0 violaciones (47 huecos documentados); ejecutor **1670/1670**; Web **536 / 3** (las mismas); Admin 222; smoke SQLite OK; smoke real 51/51; integración 13/13; E2E MS-02 13/13, MS-03 19/19, MS-03.1 19/19; Admin en navegador 95/95. | MS-04 TERMINADO / sin commit |
| 2026-11-04 | MS-03.1 Regla del conteo final (GLOBAL-01/02) | Bypass de línea base: `acknowledge_mismatch` en `confirm_final_count`, validación que cambiaba el total y contingencia tras un rechazo. Portal: `rf_session.py` (`vision_final_state`, `confirm_final_count`, `final_recapture`, `submit_manual_close`), `routes.py` (subida/proceso de una re-captura, `pef.recapture`), `capture_rf.html` (sin casilla; paso de re-captura), `supervisor_evidence.py` (etiqueta), 5 traducciones. SessionService: `session_counts.py`, `count_reference` con `changed_at`, OpenAPI. Pruebas nuevas (portal y SessionService) y 10 pruebas antiguas actualizadas porque codificaban el bypass. Scripts: `make_ms031_count_videos.py`, `e2e_ms031_final_count.py`; E2E MS-02/MS-03 con video MJPG. Ningún dato histórico modificado. | E2E MS-03.1 **19/19**; ejecutor **1653/1653**; Web **505 / 3** (las mismas 3); Admin 222; smoke SQLite OK; smoke real 51/51; integración 13/13; E2E MS-02 **13/13**; E2E MS-03 **19/19**. | MS-03.1 TERMINADO; commit de MS-03 + MS-03.1 autorizado. Pruebas de navegador del Supervisor automatizadas y aprobadas; aceptación manual pendiente |
| 2026-11-04 | MS-03 Evidencias reales + revocación WS | Commit de MS-02 `766ebf2`. Portal: `supervisor_evidence.py`, ruta de imagen autorizada, *partial* `_evidence_traceability.html` + `evidence-overlay.js` en Session Details y Review Detail, `evidence_client` (lecturas), ticket WS con usuario y renovación, `revoke_live_user` (sign-out y administración), el `sign-out` revoca el refresh. SessionService: `vision_model` en el detalle. VisionWorker: `ws_ticket.ticket_claims`, `LiveAuth`, `/auth/revoke`. EvidenceService sin cambios. `.env.test`: PostgreSQL de prueba en 55632 (rango reservado por Windows). | E2E real **19/19**; ejecutor **1641/1641**; Web **490 / 3** (las mismas); Admin 222; smoke real 51/51; integración 13/13; E2E MS-02 13/13. | MS-03 TERMINADO / sin commit |
| 2026-11-04 | MS-02 Modelo fijado por sesión (P1, P2) | Commit de MS-01 `7caf1de`. Portal: `model_pinning.py`, fijación en `rf_session.start_session`, *bind* firmado con el modelo fijado y verificación del SHA-256 en `vision_bridge`, `presigned_get`. SessionService: fijación en `/start`. VisionWorker: `model_store.py`, `bind_auth.py`, `get_model(sha256)`, identidad del modelo en estado y WS. Migración 011 (inmutabilidad). Compose: volumen de Administración en solo lectura y host de Garage permitido para `vision`. Sin cambios en reglas de conteo, pantallas, flujo del Supervisor ni pesos. | E2E real **13/13**; ejecutor **1633/1633** (Vision 47 con inferencia real, Session 716); Web **473 / 3** (las mismas 3); Admin 222; smoke real 51/51; integración 13/13. | MS-02 TERMINADO / sin commit |
| 2026-11-03 | MS-01 Pruebas unificadas y P3/P4/P5 | Commit de respaldo `5600edd` (`admin-01`) y rama `microservices_phase2`. Nuevo `scripts/run_ms_tests.py`. CatalogService: matriz de escritores y control de institución responsable (P3), `inactive_family` (P4). SessionService: `inactive_room/station/kit/phase` (P5). Portal: mismas reglas en el respaldo SPD local y en el del Supervisor, más mensajes es-MX. Compose: `ADMIN_CATALOG_STEWARD_INSTITUTION_ID` a `catalog`/`web`. Pruebas nuevas: Catalog 56, Session 15, Web 8; 19 pruebas de Catalog adaptadas. Sin cambios en VisionWorker, reglas de conteo ni flujos del Operador. | Línea base **1532/1532** → final **1603/1603** (0 omitidas); Web **458 / 3** (las mismas 3 del Operador); Admin **222**; smoke SQLite OK; smoke real **51/51**; integración **13/13** (sin NOTE). | MS-01 TERMINADO / sin commit |
| 2026-11-03 | MS-00 Diagnóstico de microservicios | Inventario real de servicios, matriz de 20 responsabilidades, P1–P7 con referencias al código, arquitectura actual frente a la propuesta, etapas MS-01…MS-08. Documentación de Administración: `best.pt` **provisional** y aceptación manual pendiente. Nuevo: `docs/MS_DIAGNOSTICO_Y_PLAN.md`. Sin cambios de código. | Catalog **179**, Session **697**, Evidence **323** (+11 omitidas), Audit **48**, Notifications **40**, Monitoring **41**, Vision **17** (+4 omitidas), Auth **172**, Web **450 / 3** (preexistentes). | DIAGNÓSTICO TERMINADO; plan pendiente de revisión |
| 2026-11-03 | ADMIN-09 Validación final | Pruebas en navegador real (Chromium/Playwright) de RF-AD-01..10, seguridad, *steward*, persistencia Garage/local y RF-AD-08 con el `best.pt` real (lector seguro + Ultralytics en la imagen del worker). Corregidos H1 (`controllers/admin_routes.py`: 403 explicado en el formulario), H7 (`services/admin_local/storage.py`: objetos multiparte de Garage) y H2 (prueba frágil). Nuevos: `scripts/run_admin_browser_env.py`, `scripts/browser_validate_admin.py`, `docs/evidencias/ADMIN-09/`. Hallazgos sin implementar: H4/P7 (CSRF), H5 (`depends_on: garage`). Sin cambios en Operador, VisionWorker, microservicios ni AuthService. | navegador **95/95**; `admin` **222 ok**; `web` **450 ok / 3 fallidas** (mismas 3 y mismos mensajes que `b417170`); `auth` **172 ok**; smoke SQLite OK; smoke real **51/51**; integración **9/9** + 2 notas (P3, P5). | VALIDACIÓN COMPLETADA / sin commit; falta revisión manual humana y decisión P1–P7 |
| 2026-11-03 | ADMIN-08 Auditoría de integración | Alcance institucional (§11.1) y control opcional de catálogos compartidos (`security.require_catalog_steward`, `ADMIN_CATALOG_STEWARD_INSTITUTION_ID`, 17 escrituras). Integración real Admin→CatalogService/SessionService (`scripts/audit_admin_integration.py`). Almacenamiento (persistencia Garage y volumen local, permisos de bucket, *path traversal*). 10 pruebas de seguridad del endpoint de revocación. Comparación de las 3 fallas del Operador con `b417170` (worktree temporal, eliminado). Nuevos: `tests/test_admin_institution_scope.py`, `tests/test_admin_storage.py`, `scripts/audit_admin_integration.py`, `scripts/verify_weights_names.py`, `docs/ADMIN_GUIA_VALIDACION_MANUAL.md`. Sin cambios en microservicios del Supervisor, VisionWorker ni reglas de conteo. | `admin` **219 ok**; `web` **447 ok / 3 fallidas** (idénticas en `b417170`); `auth` **172 ok**; smoke SQLite OK; smoke real **51/51**; integración **9/9** + 2 brechas (P3, P5). | AUDITORÍA COMPLETADA / sin commit; manual y `best.pt` pendientes; propuestas P1–P6 para decidir |
| 2026-11-01 | ADMIN-01 completo (RF-AD-01…10) | Commit de respaldo `234b437` (ADMIN-01.0/01.1, sin push). Bloques: 1) RF-AD-09 scope institucional en `audit_log.py` + rango de fechas; 2) RF-AD-05 `users.py` local + endpoint AuthService `sessions/revoke` + `auth_sessions.py`, `facade.py` sin `identity_remote`/`audit_remote`/`catalog_remote` para Admin, piezas/kits solo SPD; 3) RF-AD-02 ejemplos + `storage.py` (local/S3) + `media.py`; RF-AD-03 `selection_catalogs.py` + procedimientos sin `procedure_kit`; RF-AD-04 `privacy.py`; RF-AD-07 ROI; RF-AD-08 `vision_models.py` + `yolo_weights.py`; RF-AD-01 `dashboard.py` + `health.py`; RF-AD-10 `settings.py`. Migración `010_admin_configuracion.sql`. Plantillas nuevas (avisos, catálogos, subida/mapeo de modelos). 143 traducciones es-MX. `boto3` en el portal; Garage en `docker-compose.test.yml`; volumen `web_admin_storage` y variables en `docker-compose.yml`/`.env.example`; `GUIA_ADMIN_MONOLITO.md` reescrita. | `admin`: **190 ok**; `web`: **418 ok / 3 fallidas** (Operador, preexistentes); `auth`: **162 ok**; smoke SQLite **OK**; smoke real **51/51** (incluye alta de usuario → login AuthService, desactivación y reset → token revocado); 010 idempotente en 3 BD; imagen Docker construida y contenedor `/health/ready` OK. | ADMIN-01 COMPLETADO (automatizado) / sin commit en `admin-01`; pendiente prueba manual en navegador y `best.pt` real |
| 2026-11-01 | ADMIN-01.1 Roles fijos (RF-AD-06) | Nuevos: `data/migrations/009_roles_fijos.sql`, `services/admin_local/roles.py`, `tests/test_admin_roles.py`. Modificados: `admin_local/{api,facade,catalogs,identity_remote,messages}.py`, `controllers/admin_routes.py`, `controllers/admin_presenters.py`, `i18n.py`, `templates/admin/roles/{list,form}.html`, `translations/es_MX/LC_MESSAGES/messages.{po,mo}`, `tests/test_identity_remote.py`, `BackendAuthService/{identity_admin.py,openapi.yaml,tests/test_identity_admin.py,tests/test_api_v1.py}`, `docker-compose.yml` y `docker-compose.test.yml` (montaje de 009), `scripts/{smoke_admin_monolith_sqlite,e2e_admin,smoke_admin_test_env,run_test_env}.py`. 009 aplicada a `pef_test` y `pef_test_migcheck`; nueva BD `pef_test_fresh` (001→009). Sin cambios en Operador, Supervisor, VisionWorker, WebSockets, reglas de conteo ni login/JWT/refresh/logout/Redis. | `run_test_env.py web`: **314 ok / 3 fallidas** (Operador, preexistentes). `admin`: **86 ok**. `auth`: **160 ok**. `smoke` SQLite: **OK completo**. `smoke_admin_test_env.py`: **36/36**. 009: idempotente (existente, copia, nueva). | ADMIN-01.1 COMPLETADO / sin commit en `admin-01` (pendiente de revisión) |
| 2026-11-01 | ADMIN-01.0 Entorno | `git init` + commit base `b417170` en `main`, rama `admin-01`; `.gitignore` ampliado; `.gitattributes`; nuevos `docker-compose.test.yml`, `.env.test.example`, `scripts/run_test_env.py`, `scripts/smoke_admin_test_env.py`; `.env.test` local (ignorado). Docker Desktop iniciado. BD `pef_test` creada por `initdb` (001, 002, 003, 004→008) en volumen nuevo `pef_admin_test_pg_data`; copia `pef_test_migcheck` para probar re-ejecución. **Ningún contenedor ni volumen existente fue tocado; sin cambios en `apps/`.** | `run_test_env.py web`: **302 ok / 3 fallidas / 0 omitidas** (3 del Operador, preexistentes). `admin`: **74 ok**. `auth`: **159 ok**. Smoke SQLite: falla conocida en `POST /admin/roles/new` (503). Smoke real `smoke_admin_test_env.py`: **34/34** (login/refresh/logout/revocación de 3 roles, 403 a no-admin, 11 pantallas Admin). 008 y 004–007 idempotentes. | ADMIN-01.0 COMPLETADO / cambios sin commit en `admin-01` |
| 2026-11-01 | ADMIN-00 Diagnóstico | Revisión de `admin_routes.py`, `admin_local/*`, `identity_remote`, `catalog_remote`, `audit_remote`, `monitoring_remote`, `routes.py` (auth), `BackendAuthService` (`main.py`, `identity_admin.py`, `token_store.py`), migraciones 001/008, semillas y `Legacy/.../AdminService_zip3`. Creado `docs/ADMIN_MATRIZ_CUMPLIMIENTO.md`; actualizado este plan. **Sin cambios de código.** | venv aislado (Python 3.14.2). Web Admin: 43 ok/1 falla/4 omitidas. Web completa: 186 ok/6 fallas/113 omitidas (fallas = dependencia de PG, preexistentes). Smoke SQLite Admin: parcial, falla en roles (503). AuthService: 11 ok/148 omitidas. Docker no disponible; sin PG/Redis/Mongo del proyecto. | ADMIN-00 COMPLETADO (diagnóstico) / sin commit (no es repo git) |
| 2026-11-01 | Aclaración de autenticación | Se documenta que **Admin, Operador y Supervisor comparten AuthService**; se corrigen ADMIN-00/01/07 y MS-01 sin cambiar código. | Revisión estática de `controllers/routes.py`; E2E no ejecutado | PLAN ACTUALIZADO / sin commit |
| 2026-11-01 | Preparación del plan | Se creó `PLAN_TRABAJO.md` y `PROMPT_CLAUDE_ADMIN_01.md` para iniciar diagnóstico y cierre del Administrador. **No se cambió lógica de negocio.** | Revisión de estructura/archivos del consolidado V2; pruebas integrales no realizadas en esta tarea | PLANIFICADO / sin commit |

### Plantilla obligatoria para cada avance

```text
Fecha:
ID de tarea:
Resumen del cambio:
Archivos modificados:
Pruebas (comando exacto y resultado):
Requisito(s) verificado(s):
Pendientes/bloqueadores:
Commit (si existe):
Siguiente tarea:
```

## 8. Instrucciones operativas para Claude

1. **Antes de editar:** leer este archivo, RFs citados, código relevante y `git status`. Informar el bloque específico que va a realizar y sus criterios de aceptación.
2. **Implementar en unidades pequeñas**, con tests por RF. No hacer refactors masivos ni reemplazar archivos completos sin revisar diferencias y compatibilidad.
3. Cuando se toque Admin, comprobar que no aparezcan llamadas HTTP de negocio innecesarias. **Nunca eliminar ni sustituir AuthService para login/logout/JWT/refresh/verificación/revocación de Admin u Operador.** No retirar servicios del Supervisor por el refactor de Admin.
4. Mantener UI **en inglés** (etiquetas, mensajes y rutas existentes cuando sea posible), sin romper traducciones actuales ni las pantallas v3. Preservar validaciones y mensajes de error.
5. Antes de terminar cada intervención: ejecutar tests disponibles, inspeccionar `git diff`, explicar resultados y **actualizar este archivo** más `docs/ADMIN_MATRIZ_CUMPLIMIENTO.md` cuando corresponda.
6. No declarar tarea como `[x]` si únicamente se escribió código o si no se ejecutaron tests. Si el entorno impide probar, registrar **pendiente de validación** y explicar cómo reproducir la prueba.
7. Proponer la siguiente tarea según dependencias, sin iniciar fases de microservicios mientras Admin no esté cerrado.

**Primer prompt reproducible:** `PROMPT_CLAUDE_ADMIN_01.md`.
