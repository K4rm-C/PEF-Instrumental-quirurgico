# PEF — CatalogService integrado con el portal

CatalogService (`apps/CatalogService`, puerto 5003) es ahora el dueño de las operaciones de **familias, instrumentos y kits** (DS03). Las pantallas RF de Administración y las de instrumentos y kits del Supervisor conservan su aspecto y comportamiento, pero leen y escriben esos datos por HTTP. Contrato: [DS09 §1.3.3](data/DataStandards/PEF-DS09-API-Exchange-Contracts.md), complemento v0.1.2. Documentación ejecutable: `http://127.0.0.1:5003/api/v1/catalog/docs`.

## 1. Flujo

```
Pantalla RF Admin / Supervisor ─▶ admin_routes ─▶ facade ─┬─ familias / instrumentos / kits ─▶ catalog_remote ─HTTP JSON + Bearer─▶ CatalogService ─▶ PostgreSQL (+ access_audit)
                                                          ├─ usuarios y roles ─▶ identity_remote ─HTTP─▶ AuthService (ver GUIA_AUTH.md)
                                                          └─ procedimientos, modelos, configuración, bitácora, dashboard ─▶ admin_local (en proceso)
Operador ─▶ rf_session (local, PostgreSQL)   ← no usa CatalogService
```

- **Un solo camino por petición.** Con `CATALOG_SERVICE_URL` configurado, esas operaciones van a CatalogService y nunca también a `admin_local`: no hay doble escritura ni caída silenciosa al modo local. Si Catalog no responde, la pantalla muestra "El servicio de catálogo no está disponible temporalmente" (503) y no se escribe nada.
- **Sin `CATALOG_SERVICE_URL`:** se usan los módulos `admin_local` de siempre. Es el respaldo de desarrollo y el modo de las pruebas unitarias.
- **Identidad:** la Web envía el token de acceso que AuthService ya verificó para esa petición, en `Authorization: Bearer`. CatalogService lo valida de nuevo por su cuenta:
  - firma y algoritmo del JWT, `type=access`;
  - sesión en Redis no revocada;
  - usuario e institución activos en PostgreSQL;
  - roles efectivos.

  Ninguna cabecera propia se acepta como identidad. También se envía `X-Correlation-ID`.
- **Presentación:** el adaptador (`services/admin_local/catalog_remote.py`) devuelve a las pantallas las mismas estructuras que `admin_local`. Filtros, búsqueda localizada y paginación siguen en la Web; las reglas de negocio, validaciones, transacciones y auditoría están en CatalogService.

## 2. Comparación de capacidades (antes → ahora)

| Capacidad | `admin_local` (monolito) | CatalogService antes | CatalogService ahora |
| :--- | :--- | :--- | :--- |
| Listar familias | Sí (filtros, búsqueda, paginación) | Sí (5 campos) | Sí (+ `expand=detail`) |
| Alta de familia | Sí, con auditoría | Sí, sin auditoría | Sí, con auditoría en la misma transacción |
| Detalle / edición de familia | Sí | **No** | `GET/PUT /families/{id}` (`code` inmutable) |
| Listar instrumentos | Sí (con nombres de familia, categoría y ciclo) | Sí (códigos) | Sí (+ `expand=detail`) |
| Alta / edición de instrumento, eventos de ciclo, motivo obligatorio | Sí | **No** | `POST /instruments`, `GET/PUT /instruments/{id}` |
| Kits: alta, composición validada, versionado, `procedure_kit` | Sí | **No** (solo lectura) | `POST /kits`, `PUT /kits/{id}` (versión nueva) |
| Detalle de kit con nombres, categorías, stock disponible y procedimientos | Sí | Parcial | `GET /kits/{id}?expand=detail` |
| Catálogos de categorías y estados de ciclo | Lectura local | **No** | `GET /categories`, `GET /cycle-statuses` |
| Auditoría de cambios y de accesos denegados | Sí | **No** | Sí (`access_audit`, mismos nombres de acción) |
| Roles (MS-01) | Familias `it_admin` (con `ADMIN_CATALOG_STEWARD_INSTITUTION_ID`, solo los de esa institución); instrumentos y kits **solo** `spd_supervisor` (RF: piezas y kits son del SPD, "no Admin") | Alta de familia `it_admin` | Igual que el portal: Administración recibe 403 en piezas y kits |
| JSON / XML | — | Sí (5 endpoints) | Sí en todos; XSD `schemas/catalog.xsd` |
| OpenAPI | — | Archivo | Archivo servido + Swagger UI |

Diferencia intencional: `available_count` y `kits_using` ahora cuentan **solo la institución del usuario**. `admin_local` los calculaba sobre todas las instituciones.

**Familias inactivas (MS-01 / P4).** Una pieza nueva, un cambio de familia de una pieza o un kit nuevo/versión que *añada* una familia inactiva → `422 inactive_family`. Las piezas y kits que ya tenían esa familia antes de desactivarse siguen editándose (no se borra ni reescribe nada).

**Kits inactivos (etapa 7).** CatalogService aplica por sí mismo la regla del Supervisor: solo `it_admin` lista, lee y edita kits inactivos; cualquier otro rol recibe solo los activos de su institución y `404` para uno inactivo (`GET` y `PUT /kits/{id}`), aunque llame a la API directamente.

**XML recibido (etapa 7).** Todo cuerpo XML se valida contra `schemas/catalog-requests.xsd` (`GET /api/v1/catalog/schemas/catalog-requests.xsd`). Si no cumple: `400 invalid_request` con `details.reason = "xsd_validation_failed"`, `details.schema`, `details.errors[]` y, si aplica, `details.unknown_fields[]`. Las reglas de negocio (422) dan el mismo `reason` en JSON y en XML.

## 3. Lo que sigue en la Administración local

Procedimientos (`cat_procedure_type`, `procedure_kit` y `procedure_phase` desde la pantalla de procedimientos), modelos de visión, configuración (institución, quirófanos, estaciones), bitácora y dashboard (sus conteos son lecturas SQL locales). Supervisor y Operador siguen leyendo kits e inventario directamente de PostgreSQL al programar e iniciar sesiones: eso corresponde a SessionService, en una etapa posterior.

## 4. Configuración y Docker

| Variable | Dónde | Uso |
| :--- | :--- | :--- |
| `CATALOG_SERVICE_URL` | BackendWebFlask | `http://127.0.0.1:5003` (vacío = `admin_local`) |
| `CATALOG_SERVICE_TIMEOUT` | BackendWebFlask | Segundos por petición (5) |
| `CATALOG_PORT` | compose | Puerto publicado del contenedor |
| `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET_KEY`, `JWT_ALGORITHM` | compose → catalog | Los mismos de AuthService (el JWT debe coincidir) |

```powershell
docker compose up -d --build postgres redis catalog
curl http://127.0.0.1:5003/health/ready
# Swagger UI: http://127.0.0.1:5003/api/v1/catalog/docs  (Authorize con el access_token de /api/v1/auth/login)
```

No se crearon tablas: Catalog usa `instrument_family`, `instrument`, `instrument_cycle_event`, `kit`, `kit_item`, `procedure_kit` (DS03), lee `cat_procedure_type` (DS02) y escribe `access_audit` (DS07).

## 5. Pruebas

```powershell
cd apps/CatalogService; python -m pytest -q          # contratos originales + gestión (PostgreSQL/Redis reales)
cd apps/BackendWebFlask; python -m pytest -q          # incluye tests/test_catalog_remote.py (adaptador)
# End to end con servicios vivos (Web con CATALOG_SERVICE_URL, Auth, compose postgres/redis/catalog):
$env:CATALOG_SERVICE_URL="http://127.0.0.1:5003"; $env:COMPOSE_ENV_FILE=".env"; python scripts/e2e_catalog.py
```

El E2E da de alta y edita desde las pantallas RF, verifica PostgreSQL y la auditoría, consulta CatalogService en JSON y XML (validado contra el XSD), comprueba usuarios autorizados y no autorizados (incluido un token revocado) y **detiene el contenedor `catalog`** para demostrar que el Operador sigue trabajando. Al final lo vuelve a iniciar.

## MS-12: equivalencias autorizadas de un kit (ms-kits-charolas)

- **Qué es:** una familia de instrumental **autorizada como equivalente** a una familia de un kit (p. ej. otro fabricante del mismo patrón). Tabla `kit_item_equivalence` (migración 019).
- **Quién:** el Supervisor SPD autoriza y revoca (como los kits y las charolas); cualquier usuario autenticado de la institución las lee. La institución sale del token, nunca del cuerpo.
- **Reglas:** solo sobre la **versión activa** del kit; `family_id` debe ser una familia del kit; la equivalente debe estar **activa**, ser distinta y **no** pertenecer ya al kit; una sola autorización activa por (kit, familia, equivalente). Revocar conserva la fila (quién y cuándo). Auditoría `AUTHORIZE_KIT_EQUIVALENCE` / `REVOKE_KIT_EQUIVALENCE`.
- **Lo que NO hace:** no cambia cantidades esperadas, la referencia ni la regla del conteo final, y no cierra discrepancias. Su uso clínico es la decisión **D6** (`docs/DECISIONES_PENDIENTES.md`).
- **API (publicada en el Gateway):** `GET /api/v1/catalog/kits/{id}/equivalences?status=active|all`, `POST /api/v1/catalog/kits/{id}/equivalences` (`family_id`, `equivalent_family_id`, `notes`), `DELETE /api/v1/catalog/kits/{id}/equivalences/{equivalence_id}`. JSON y XML (`catalog.xsd`: `equivalence`, `equivalences`).
- **Portal:** Supervisor → *Equivalencias de kit* (`/supervisor/kit-equivalences`).
- **Pruebas:** `apps/CatalogService/tests/test_ms12_equivalences.py` (PostgreSQL real), `apps/BackendWebFlask/tests/test_ms12_kit_equivalences_portal.py`, E2E Compose M3.
