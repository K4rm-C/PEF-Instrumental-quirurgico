# Encargo 01 para Claude — Diagnóstico de Admin del Consolidado V2

Estamos desarrollando el proyecto PEF de conteo y trazabilidad de instrumental quirúrgico. La carpeta abierta en VS Code contiene la **versión Consolidado V2**, construida sobre `microservices_v1`, con fuentes aprovechables de ZIP 2 y avances de WebSocket/base de datos del ZIP 3.

**Antes de tocar código**, lee y utiliza estos archivos en este orden:

1. `PLAN_TRABAJO.md` (plan maestro y bitácora que debes actualizar).
2. `data/FunctionalRequirements/rf_admin_app.md`, únicamente **PARTE A: RF-AD-01 a RF-AD-10**.
3. `docs/PLAN_CONSOLIDACION_Y_ADMIN.md`, `docs/MATRIZ_PROCEDENCIA.md`, `GUIA_ADMIN_MONOLITO.md`.
4. Rutas, templates, modelos, tests y servicios actuales de `apps/BackendWebFlask`, especialmente `controllers/admin_routes.py`, `controllers/routes.py`, `services/admin_local/facade.py`, `identity_remote.py`, `catalog_remote.py`, `audit_log.py` y `services/audit_remote.py`.
5. Para comparar trabajo ya hecho: `Legacy/porting_sources/AdminService_zip3/` y, si corresponde, modelos/migración `data/migrations/008_consolidacion_zip3.sql`.

## Reglas obligatorias

- **Admin y Operador SÍ deben conservar AuthService para autenticación compartida**, igual que Supervisor: login, logout, JWT, `/verify`, `/refresh`, revocación de sesión en Redis y permisos. **NO migres la autenticación a Flask ni elimines este servicio.** Admin mantiene la lógica de negocio RF-AD-01…10 dentro de `apps/BackendWebFlask` (monolito). Las actuales llamadas HTTP de CRUD de usuarios/roles, catálogos y auditoría necesitan análisis/refactor según la regla monolítica, pero **no confundas ese CRUD con la autenticación del usuario**. Evita duplicar credenciales o fuentes de verdad.
- **Operador mantiene lógica de negocio monolítica y AuthService para autenticación**; **Supervisor SPD utiliza AuthService y microservicios de negocio**. No modificar ni borrar componentes funcionales de estos roles en el diagnóstico.
- **No desarrollar RF-AD-11 / ARCO**. Tampoco desarrollar la Parte B (App de Supervisor de piso), ni microservicios nuevos por ahora.
- **Admin no crea/edita instrumentos físicos ni kits**. Estas operaciones pertenecen al Supervisor SPD. No perder las funciones del Supervisor al corregir las rutas Admin.
- Preservar PostgreSQL, Redis, MongoDB, Garage, WebSocket de vista previa, la referencia de conteos, `Legacy` y los assets v3. No modificar el modelo `.pt` ni borrar bases/volúmenes.
- No afirmar que algo funciona sin pruebas. Proteger aislamiento institucional, rol, contraseñas y auditoría. La UI debe mantener sus textos en inglés.

## Trabajo de esta intervención: SOLO `ADMIN-00`

1. Confirma `git status` y revisa la estructura existente. **No descartes cambios locales**.
2. Elabora la matriz exhaustiva RF-AD-01 a RF-AD-10: qué código existe, qué cumple realmente, qué falta, por qué endpoint/función, dónde está la dependencia remota y qué tests lo cubren. No considerar un endpoint existente como prueba de RF terminado.
3. Identifica separadamente **(a) autenticación compartida por AuthService, que debe mantenerse para los tres roles**, y **(b) operaciones CRUD administrativas de usuarios/roles, catálogos y auditoría, que deben ser monolíticas**. Explica una estrategia incremental para localizar solo las operaciones de negocio dentro de `BackendWebFlask`, preservando identidad única, hash de contraseñas, roles fijos, revocación, institución y permisos. **No diseñes autenticación local alternativa**.
4. Localiza las escrituras Admin indebidas de instrumentos físicos y kits y propone cómo restringirlas sin retirar CRUD SPD.
5. Ejecuta las pruebas existentes de Admin y el smoke de arranque si tu entorno lo permite. Incluye **comandos exactos**, cantidades aprobadas/fallidas y causa de bloqueos; no uses resultados inventados.
6. Crea `docs/ADMIN_MATRIZ_CUMPLIMIENTO.md` con la matriz y un orden concreto de cambios. Modifica `PLAN_TRABAJO.md`: avanza solo las casillas **realmente verificadas** y agrega entrada a su bitácora.

**En esta primera intervención NO implementes en bloque ADMIN-01 a ADMIN-07.** Necesitamos una línea base fiel y un plan específico antes de refactorizar identidad, familias, roles y modelos.

## Formato de respuesta que debes darme

- Resumen de hallazgos y dependencias críticas.
- Estado RF-AD-01 a 10, destacando cuáles requieren código nuevo vs. integración.
- Archivos creados/modificados y `git diff --stat`.
- Pruebas ejecutadas y resultados reales.
- Riesgos que impedirían retirar las llamadas remotas de CRUD administrativo, **sin retirar AuthService para autenticación**.
- **Siguiente intervención propuesta: ADMIN-01**, dividida en subpasos verificables.

Recuerda: **actualiza el plan en raíz después de cada avance** para que siempre podamos reanudar el proyecto desde ahí.
