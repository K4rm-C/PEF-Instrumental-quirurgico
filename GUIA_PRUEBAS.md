# PEF — Pruebas reproducibles y aisladas

Esta guía explica cómo ejecutar las pruebas del PEF de forma **repetible**, sin depender de ejecuciones anteriores y sin alterar los datos de desarrollo.

## 1. Por qué fallaban

| Problema | Causa |
| :--- | :--- |
| EvidenceService: 85 fallos sobre una base compartida, todo correcto sobre una limpia | Varias pruebas suponían una base vacía. `audits()` sin `asset_id` leía todas las filas `evidence.%` de `access_audit`. Además, los jobs de purga y reconciliación recorren **todos** los `media_asset` por diseño, y las evidencias confirmadas por los E2E aparecían como blobs faltantes o elegibles. |
| E2E: inventario agotado | Cada corrida de los E2E de sesión marca piezas como `lost` (regla de cierre seguro). Sobre la misma base, tras unas corridas, la familia del kit demo quedaba sin piezas disponibles y la programación respondía `stock_block`. |
| E2E: estado acumulado | Las sesiones abiertas, los usuarios e instituciones creados, las evidencias en Garage y los checkpoints en Mongo se sumaban corrida tras corrida en el entorno de validación. |

## 2. Pruebas unitarias e integración por servicio

Cada servicio aísla sus datos:

| Suite | Aislamiento |
| :--- | :--- |
| SessionService, CatalogService, AuditService, NotificationsService, MonitoringService, BackendWebFlask | Cada prueba corre en una transacción externa que se revierte. Las pocas que necesitan confirmar datos (concurrencia) crean filas con identificadores únicos y las borran. |
| **EvidenceService** | Desde esta etapa usa **su propia base de datos**, `<base>_evidence_test`. Se recrea desde `data/` (las mismas migraciones que el initdb) al inicio de cada corrida y se elimina al final. Solo se borra una base con ese sufijo. Las pruebas de Garage real usan un prefijo propio (`<institución nueva>/…`) y borran solo sus objetos. |
| BackendAuthService | Datos con identificadores únicos y limpieza propia. |

```powershell
# Variables de conexión: cualquier PostgreSQL/Redis de desarrollo. EvidenceService NO escribe en esa base: deriva la suya.
$env:EVIDENCE_TEST_DATABASE_URL = "postgresql+psycopg://usuario:clave@127.0.0.1:5433/InstruMed"
$env:EVIDENCE_TEST_REDIS_URL    = "redis://:clave@127.0.0.1:6379/0"
cd apps/EvidenceService; python -m pytest -q
# Opcional, Garage real: EVIDENCE_TEST_S3_ACCESS_KEY / _SECRET_KEY / _API_PORT / _REGION / _BUCKET_EVIDENCE
```

Las demás suites usan `<SERVICIO>_TEST_DATABASE_URL` y `<SERVICIO>_TEST_REDIS_URL`; por ejemplo `SESSION_TEST_DATABASE_URL`.

## 3. Entorno E2E aislado (`scripts/e2e/isolated.py`)

Los E2E cambian estado persistente a propósito. Por eso corren en **su propio proyecto de Compose**, `pef_e2e`:
- **Contenedores, red y volúmenes propios:** postgres, redis, mongo, garage y los microservicios. Todos los puertos están en el rango 56xxx: portal **56000**, Auth **56001**, VisionWorker **56002**. Así el entorno aislado convive con el despliegue principal (5000 / 5001 / 5002). Los scripts reciben las URLs en `PEF_WEB_URL`, `PEF_AUTH_URL` y `PEF_VISION_URL`.
- **Datos iniciales:** la base la crea el initdb de postgres desde `data/` (esquema, catálogos y semilla demo). Cada entorno nuevo empieza con los mismos usuarios, kits e inventario.
- **Secretos:** se generan una sola vez en `.e2e/e2e.env`, que git ignora. Nunca se usan los del `.env` de desarrollo.
- **Dos modos para el portal y VisionWorker** (`--mode`; `up` lo recuerda en `.e2e/mode`):
  - `host` (predeterminado): procesos Python del host. Usa `docker-compose.hostdev.yml`, y el collector los revisa por `host.docker.internal`.
  - `containerized`: los servicios `web` y `vision` del `docker-compose.yml`, con las mismas imágenes del despliegue integral, dentro de `pef_e2e`. `scripts/e2e/compose.containerized.yml` monta `.e2e/outbox` como cola del portal, para que `e2e_mongo_ds06.py` vea los checkpoints pendientes desde el host. `best.pt` se monta en solo lectura.

  El runner pasa los archivos de Compose en `COMPOSE_FILE`, así los `docker compose stop/start` de los scripts usan la misma configuración.

**Qué borra `reset`:** solo los volúmenes del proyecto `pef_e2e`, que el propio runner crea. No toca otros proyectos (por ejemplo `pef_validacion`), otros contenedores ni ningún otro volumen. `all` compara al terminar los contenedores ajenos al proyecto (id, estado y hora de arranque) y falla si alguno cambió.

### Preparación (una vez)

```powershell
python -m venv .venv-web;    .\.venv-web\Scripts\pip install -r scripts/e2e/requirements.txt   # portal + scripts E2E
python -m venv .venv-vision; .\.venv-vision\Scripts\pip install -r apps/VisionWorker/requirements.txt
# apps/VisionWorker/weights/best.pt debe existir (se agrega a mano; no está en git)
```

### Ejecución

```powershell
python scripts/e2e/isolated.py all --web-python .venv-web\Scripts\python.exe --vision-python .venv-vision\Scripts\python.exe
python scripts/e2e/isolated.py all --mode containerized --web-python .venv-web\Scripts\python.exe --vision-python .venv-vision\Scripts\python.exe
python scripts/e2e/isolated.py all --suites session reports ...   # solo algunas suites
```

En modo `containerized`, `--vision-python` solo se usa para generar los videos de prueba si faltan. `--web-python` corre los scripts E2E.

`all` hace, en orden:
1. `reset` del proyecto `pef_e2e`;
2. `up --build` y espera a que todos los servicios respondan `/health/ready`;
3. en modo `host`, arranca el portal y VisionWorker como procesos (en modo `containerized` ya los levantó `up`, que espera su healthcheck);
4. corre las suites;
5. detiene el portal y VisionWorker, y para el proyecto conservando sus volúmenes.

Al final imprime el inventario disponible del kit demo antes y después de las suites, y la comprobación de los contenedores ajenos.

Los comandos sueltos son `up`, `host`, `run [--suites]`, `stop-host`, `down` (detiene, conserva volúmenes), `reset`, `env` (imprime las URLs para correr un script a mano) y `status`.

| Suite | Script | Cambia (solo dentro de `pef_e2e`) |
| :--- | :--- | :--- |
| identity | `scripts/e2e_identity.py` | Usuarios, roles, institución de prueba, sesiones Redis |
| catalog | `scripts/e2e_catalog.py` | Familias, instrumentos y kits; detiene e inicia `catalog` |
| admin | `scripts/e2e_admin.py` | Usuarios, procedimientos, configuración |
| ops | `scripts/e2e_ops.py` | Sesiones RF del Operador y Supervisor, conteo YOLO, cierre |
| session | `scripts/e2e_session.py` | Sesiones, inventario esperado, piezas `lost`; detiene e inicia `session` |
| notifications | `scripts/e2e_notifications.py` | Sesiones, notificaciones; detiene e inicia `notifications` |
| audit | `scripts/e2e_audit.py` | Eventos de auditoría; detiene e inicia `audit` |
| reports | `scripts/e2e_reports.py` | Sesión nueva por flujo real; detiene e inicia `session` |
| mongo | `scripts/e2e_mongo_ds06.py` | Checkpoints en MongoDB y evidencias en Garage |
| monitoring | `scripts/e2e_monitoring.py` | Historial de monitoreo; detiene e inicia varios servicios del proyecto |
| evidence-unit | `apps/EvidenceService` (pytest) | Su base dedicada y objetos de prueba en el Garage del proyecto |

Los videos de prueba (`tray_full.mp4`, `tray_masked.mp4`) se generan en `.e2e/videos` con `scripts/make_test_tray_videos.py` si no existen. Se puede pasar `--videos <carpeta>`.

### Reglas para escribir un E2E nuevo

- Lee las URLs y credenciales de las variables que da el runner (`PEF_WEB_URL`, `PEF_AUTH_URL`, `PEF_VISION_URL`, `DATABASE_URL`, `*_SERVICE_URL`, `COMPOSE_ENV_FILE`, `COMPOSE_PROJECT`, `COMPOSE_FILE`). Nunca uses puertos ni secretos fijos de un entorno concreto.
- Si detienes o inicias contenedores, usa `COMPOSE_PROJECT`, que el runner fija en `pef_e2e`.
- Crea tus propios datos con identificadores únicos. No supongas que la base está vacía; solo confía en la semilla demo.
- Agrega el script a `SUITES` en `scripts/e2e/isolated.py`.

## 4. Cierre seguro y faltante físico

Pruebas específicas:
- `apps/SessionService/tests/test_physical_shortage.py` cubre `resolved` (rechazado), `lost` y `recovered`. En ninguna combinación de decisiones el cierre queda con `safe_close_confirmed = true`; queda `false` con los ids del faltante y la decisión auditada.
- En el portal, `tests/test_multitenant.py::test_same_tenant_full_flow` lo comprueba en la implementación local.
- En los E2E, `session`, `reports` y `ops` lo comprueban en vivo.

Reglas en `GUIA_REPORTES.md` §7.

## 5. Fragilidades corregidas en esta etapa

| Prueba | Dependencia oculta | Corrección |
| :--- | :--- | :--- |
| EvidenceService (85 fallas) | Filas confirmadas por otras ejecuciones; jobs que recorren toda la tabla | Base dedicada que se recrea en cada corrida |
| `test_probes.py::test_notifications_worker_connection_and_backlog` (MonitoringService) | Eventos del outbox sin procesar de otras suites (sin worker) → la sonda informa, con razón, `degraded` | La prueba vacía el backlog dentro de su propia transacción antes de medir |
| `e2e_session.py`, carrera D (reporte del Operador frente a edición del Supervisor) | Suponía que siempre ganaba el reporte | Acepta los dos órdenes válidos de serialización y verifica que ninguno deje un estado mezclado |
| `e2e_reports.py`, coherencia con el detalle | Usaba "hoy en UTC"; la sesión se programa en hora local (después de las 18:00 en México la fecha UTC cambia) | Ventana alrededor del `scheduled_at` real de la sesión |
| `e2e_monitoring.py`, historial up → down → up | En un entorno recién creado, las primeras comprobaciones son anteriores a que el servicio esté listo | Busca la secuencia up, caída, up, sin exigir el primer valor de la ventana |
| E2E de sesión: inventario agotado | Las piezas `lost` se acumulaban en la base compartida | Entorno `pef_e2e` sembrado desde `data/`, reiniciado por `all` |
