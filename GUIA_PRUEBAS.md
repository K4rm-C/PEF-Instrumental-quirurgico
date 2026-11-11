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

### 2.1 Ejecutor unificado de los 8 servicios (MS-01, `scripts/run_ms_tests.py`)

Ejecuta las suites de AuthService, CatalogService, SessionService, EvidenceService, AuditService,
NotificationsService, MonitoringService y VisionWorker **contra el entorno aislado** de `docker-compose.test.yml`
(`.env.test`: PostgreSQL `pef_test`, Redis, MongoDB 47017, Garage 59000; puertos de PostgreSQL y Redis en `.env.test`, hoy 55632 y 57379 porque Windows reservó los anteriores, ver §2.3). Se niega a arrancar si la
URL de la base no es la de prueba, así que nunca toca la pila principal ni sus volúmenes.

```powershell
docker compose -f docker-compose.test.yml --env-file .env.test up -d --wait   # solo el entorno de pruebas
python scripts/run_ms_tests.py                        # las 8 suites
python scripts/run_ms_tests.py catalog session        # algunas (nombres: python scripts/run_ms_tests.py --list)
python scripts/run_ms_tests.py session -k ms01        # filtro de pytest
python scripts/run_ms_tests.py vision --build-vision-image   # reconstruye pef-ms-test/vision antes
```

- **Python (Windows o Linux):** el intérprete que lanza el script ejecuta las suites; debe tener instalados los
  `apps/*/requirements-dev.txt` (en este equipo, el venv del portal, al que se añadió `pyyaml` para AuditService).
- **VisionWorker (Docker):** necesita torch, ultralytics y cv2, así que se ejecuta dentro de la imagen
  `pef-ms-test/vision` construida desde `apps/VisionWorker/Dockerfile`, sin cambios de código. El código se monta
  en solo lectura y `pytest` se instala solo en el contenedor desechable. No usa pesos: sus pruebas no los necesitan.
  MongoDB de pruebas: `host.docker.internal:47017`.
- **EvidenceService:** recibe `EVIDENCE_TEST_S3_*` del Garage de pruebas (bucket `pef-evidence-test`, que el
  ejecutor crea si falta), así que sus pruebas de Garage ya no se omiten.
- **Resultado:** por suite, aprobadas / fallidas / errores / omitidas, leídas del XML JUnit de pytest (nunca del
  texto). Cada problema se clasifica como **CODE** o **INFRA** (dependencia caída o no instalada). Una omisión
  **nunca** cuenta como aprobada. Estados: `OK`, `OK (with skips)`, `INCOMPLETE (INFRA)` (se omitieron pruebas
  porque faltaba una dependencia) y `FAIL`.
- **Código de salida:** 1 si hay fallos o errores, 2 si está incompleto por infraestructura, 0 si todo pasó.
  Los registros y `summary.json` quedan en `.e2e/ms_tests/` (ignorado por git).

### 2.2 MS-02: inferencia real con el modelo fijado

```powershell
python scripts/run_ms_tests.py vision --weights <ruta al best.pt provisional>   # + 2 pruebas de inferencia real
docker run --rm -v ${PWD}:/repo:ro -v ${PWD}/.e2e/videos:/out --entrypoint python pef-ms-test/vision:latest `
    /repo/scripts/make_test_tray_videos.py /out        # videos sintéticos de charola (una vez)
python scripts/e2e_ms02_model_pin.py                    # E2E real: Operador, worker en contenedor, Garage, WS en Chromium
```

- El E2E necesita el `best.pt` provisional ya registrado en la BD de prueba (lo deja `browser_validate_admin.py`),
  `.e2e/videos/tray_full.mp4` y `.e2e/videos/tray.jpg`.
- El worker se ejecuta sin pesos empaquetados en la red `pef_admin_test_default`, así que descarga los pesos de Garage por la URL prefirmada.
- Restaura al final el modelo activo y solo escribe datos de prueba.

### 2.3 MS-03: evidencias reales del Supervisor y revocación del WebSocket

```powershell
python scripts/e2e_ms03_evidence.py      # Supervisor (SessionService) → Operador (worker real) → evidencias
                                         # (MongoDB + EvidenceService/Garage) → Supervisor en Chromium
```

- **Procesos reales:** AuthService :15001, portal :15000, SessionService :15004, EvidenceService :15005 (bucket `pef-evidence-test`), VisionWorker en contenedor, MongoDB `pef_ms03_e2e`.
- **Casos negativos reales:**
  - Sesión o evidencia inexistentes, Supervisor de otra institución, Operador o Administrador en la ruta de imagen.
  - Archivo borrado de Garage, Garage detenido, tokens revocados por `sign-out`.
  - Reconexión y revocación del WebSocket con la conexión abierta.
- **Requisitos:** los mismos que §2.2 (`best.pt` provisional registrado, `.e2e/videos`). Con la consola de Windows, ejecutar con `PYTHONIOENCODING=utf-8`.
- **Puerto de PostgreSQL de prueba:** en Windows, Hyper-V/WinNAT puede reservar rangos de puertos tras un reinicio (`netsh interface ipv4 show excludedportrange protocol=tcp`). Si `127.0.0.1:55432` queda dentro de un rango, cambia `TEST_POSTGRES_PORT` en `.env.test` (hoy 55632). Los volúmenes no se tocan y todos los scripts leen el puerto de `.env.test`.

### 2.4 MS-03.1: regla del conteo final (escenarios A y B) y WebSocket > 120 s

```powershell
# Videos Motion-JPEG (necesitan OpenCV; por ejemplo, dentro de la imagen del worker):
docker run --rm -v "${PWD}:/repo" -w /repo pef-ms-test/vision:latest python scripts/make_ms031_count_videos.py /repo/.e2e/videos
$env:PYTHONIOENCODING="utf-8"; python scripts/e2e_ms031_final_count.py
```

- Mismos procesos que §2.3. Escenario A (último cuadro con un instrumento cubierto: rechazo, sin bypass, re-captura),
  escenario B (mismo total: validación), API directa, evidencias del Supervisor y WebSocket (más de 120 s con
  renovación; revocación directa en AuthService ≤ 120 s). Tarda unos 8 minutos.
- `e2e_ms02_model_pin.py` y `e2e_ms03_evidence.py` usan ahora `ms031_full.avi`: el mp4 anterior daba totales
  distintos en su primer y último cuadro con el modelo provisional, y la regla rechaza esa diferencia.

### 2.5 MS-04: API Gateway, CSRF y contratos

```powershell
$env:PYTHONIOENCODING="utf-8"; python scripts/e2e_gateway.py     # unos 3 minutos
```

- **Procesos reales:**
  - AuthService (`AUTH_TRUSTED_PROXIES=1`), portal, CatalogService, SessionService, EvidenceService, AuditService, NotificationsService y MonitoringService en el host (puertos 15000–15008).
  - VisionWorker en contenedor.
  - El gateway con la configuración del repositorio, dos veces:
    - desarrollo: HTTP en 127.0.0.1:18080;
    - producción: TLS en 127.0.0.1:18443 y redirección en 18081, con un certificado desechable generado con `openssl` en un directorio temporal que se borra al terminar.
- **Qué comprueba:**
  - Autorización a través del gateway: token válido, expirado, revocado en Redis, firma inválida, `alg=none`, rol sin permiso, otra institución, anónimo y rol `or_charge`.
  - Más de 25 rutas internas no publicadas.
  - Límites de cuerpo y de peticiones, métodos, `X-Correlation-ID` y cabeceras.
  - WebSocket a través del gateway.
  - Validación de contratos (`scripts/validate_contracts.py`; resultado en `.e2e/ms04_contracts.txt`).
  - TLS 1.2/1.3, HSTS y redirección.
  - En Chromium, el portal detrás del gateway TLS: login en el mismo origen, cookies `Secure`/`HttpOnly` y CSRF.
- **Limpieza de Redis:** borra, solo en el Redis de prueba, las claves `auth:login_fail:*` al empezar y al terminar. La propia prueba del límite de login deja bloqueada la dirección del cliente durante `AUTH_LOGIN_FAIL_TTL_SECONDS`.
- **IPv6 en Docker Desktop:** `host.docker.internal` puede resolver también a IPv6 inalcanzable. La prueba usa la IPv4 del host, resuelta desde la red de pruebas.
- **Dependencias nuevas** del intérprete de los E2E (`scripts/e2e/requirements.txt`): `jsonschema` y `websocket-client`.

**CSRF en las pruebas y en los scripts.**
- **Suite del portal:** usa `tests/conftest.CsrfClient`, que envía en cada petición que modifica estado el token que el portal emite (`GET /csrf-token`). Toda la suite corre con la protección **activa**. Para las pruebas negativas se usa `csrf=False` (`tests/test_ms04_csrf.py`).
- **Scripts con `requests`:** usan `scripts/portal_csrf.py`, que pide el token con las mismas cookies justo antes de cada POST/PUT/PATCH/DELETE al portal.
- **`browser_validate_admin.py`:** sus llamadas directas `page.request.post` envían la cabecera `X-CSRF-Token`. Los formularios reales la llevan en un campo oculto.

### 2.6 MS-05 y MS-06: estaciones, cámaras, reportes, notificaciones (integración completa)

```powershell
$env:PYTHONIOENCODING="utf-8"
python scripts/e2e_ms05_stations.py     # 14 comprobaciones, unos 3 minutos
python scripts/e2e_ms06_reports.py      # 15 comprobaciones (Parte C), unos 4 minutos
```

- **Requisitos:** la pila aislada levantada, `best.pt` publicado por Administración (como en MS-02) y `.e2e/videos/ms031_masked.avi` (§2.4). Solo la pila `pef_admin_test`; nunca la principal.
- **`e2e_ms05_stations.py`:** cámara registrada por Administración (formulario, auditada) → sesión en la estación → el Operador elige la cámara (auditado) → latido del navegador reenviado por el portal → estación "conectada" pero cámara solo "sin verificar" → cuadros reales por el WebSocket (Chromium) → el colector los lee de VisionWorker `/activity` y la cámara pasa a "funcionando" (verificada por el servidor) → error y recuperación de cámara → la estación deja de reportar (sin comunicación + alerta) y se recupera → latidos de otro rol, anónimos o de otra institución rechazados → estación deshabilitada → reinicio de MonitoringService con el estado conservado → el Supervisor lo ve en Salud de servicios → el conteo inicial guarda estación y cámara sin cambiar la regla.
- **`e2e_ms06_reports.py` (Parte C):** AuthService, portal, SessionService, NotificationsService + worker, MonitoringService + colector y VisionWorker reales. El canal de correo del worker apunta a un **receptor SMTP local** que arranca el propio script (en memoria; sin proveedor, sin credenciales, nada sale del equipo). Comprueba: latido → Monitoring; error de cámara → `station_event` → outbox → `station_alert` al Supervisor y al Administrador de TI (no al Operador), sin duplicar mientras no se lea; S1 por visión con conteo YOLO real, validación humana con el instrumento esperado faltante, discrepancias, registro del faltante y cierre reconocido → `session_unresolved`; S2 por informe de contingencia → `review_pending` → cierre seguro → `session_closed`; reportes con filtro de estación que crecen exactamente (+2 sesiones, +1 aprobada, +1 no resuelta, +1 final aceptado); estación de otra institución rechazada (400); pantalla de Reportes; notificaciones visibles en el portal; correos recibidos y entregas `sent`; exportación CSV/PDF por el portal, prohibida al Operador, auditada dos veces.
- **Dependencia nueva:** `fpdf2` (SessionService, PDF de la exportación).
- **Windows:** si Docker no puede publicar el puerto de Redis de prueba ("access permissions" / rango reservado), cambie `TEST_REDIS_PORT` en su `.env.test` local (en este equipo se usa `57379`); `.env.test` no se versiona.

### 2.7 MS-07 y MS-08: sincronización, FHIR, charolas (integración completa, Parte D)

```powershell
$env:PYTHONIOENCODING="utf-8"
python scripts/e2e_ms07_ms08.py         # 25 comprobaciones, unos 6 minutos (la primera vez descarga HAPI, ~700 MB)
```

- **Procesos reales:** AuthService, portal, CatalogService, SessionService, EvidenceService, NotificationsService + worker, IntegrationService + worker, VisionWorker (contenedor, `best.pt` provisional), API Gateway (nginx, configuración del repositorio) y **HAPI FHIR R4 v7.4.0** (contenedor local `pef-ms07-hapi`, puerto 18090). HAPI es un servidor FHIR **de pruebas**: valida interoperabilidad, no es un HIS.
- **Recorrido:** cliente máquina creado en Administración (secreto mostrado una vez) → token por el Gateway → primera sincronización y cursor sin repeticiones → charola creada dos veces con la misma clave (una sola) y vista una vez en la sincronización incremental → conflicto de versión y clave reutilizada → operaciones de conteo inexistentes → sesión programada con la charola, reintentada (una sola) → conteo YOLO real, validación, revisión y cierre → evidencias en EvidenceService → pantallas del Supervisor → reportes → notificaciones → Encounter FHIR por el Gateway (sin paciente sin alcance; otra institución 404; token de usuario 403) → `$validate` de HAPI sin errores → entrega idempotente a HAPI (Encounter y Location guardados, sin Patient) → HAPI pausado: reintento con *backoff* y envío, un solo Encounter → auditoría completa → rotación del secreto y desactivación revocan los tokens.
- **Al terminar:** elimina los contenedores (VisionWorker, Gateway, HAPI) y restaura el modelo activo.
- **Datos de prueba:** los E2E que registran un faltante físico como perdido (MS-06) devuelven al terminar, solo en la base aislada, los instrumentos que ellos mismos marcaron (`scripts/e2e_restore_stock.py`); si una corrida se interrumpe, `python scripts/e2e_restore_stock.py --all-e2e-lost` (se niega a correr fuera del PostgreSQL de pruebas). Sin esto, programar falla con `stock_block`, que es la regla correcta.
- **Pruebas de servicio nuevas:** IntegrationService (47; en `scripts/run_ms_tests.py` como suite `integration`), AuthService `test_ms07_client_credentials.py`, CatalogService `test_ms08_trays.py`, SessionService `test_ms08_session_trays.py`, portal `test_ms07_integration_clients.py` y `test_ms08_trays_portal.py`.

### 2.8 MS-09: seguridad del conteo

```powershell
$env:PYTHONIOENCODING="utf-8"
python scripts/e2e_ms09_count_safety.py   # 7 comprobaciones, unos 3 minutos (YOLO real, VisionWorker en contenedor)
```

- **A. Escalamiento:** un final real distinto del inicial se rechaza; el Operador reporta la posible diferencia; sin conteo final ni validación; discrepancia ligada a la captura rechazada; el Supervisor no puede cerrar sin reconocerlo y el cierre (por SessionService) nunca es seguro.
- **B. Contingencia:** con VisionWorker activo, "worker no disponible" se rechaza; con el contenedor **realmente detenido**, se acepta como verificada y se cierra con la revisión.
- Pruebas nuevas de servicio: portal `test_ms09_count_safety.py` (11) y `test_ms09_role_matrix.py` (6); SessionService `test_ms09_close.py` (4) y 3 nuevas en `test_ms08_session_trays.py`.
- Las 3 pruebas históricas del Operador se corrigieron (pruebas desactualizadas / *fixture* incorrecto; ningún defecto funcional): la suite del portal corre sin fallos.

### 2.9 MS-10: cierre técnico

```powershell
$env:PYTHONIOENCODING="utf-8"
python scripts/verify_architecture.py            # estático (sin servicios): roles, emisor de tokens, puertos, migraciones
python scripts/check_migrations_concurrency.py   # base NUEVA pef_ms10_fresh (se borra al final) + 5 arranques simultáneos
python scripts/browser_validate_flows.py         # Chromium real: Operador y Supervisor, YOLO real (unos 25 minutos)
python scripts/browser_validate_admin.py --weights <best.pt>   # Administración RF-AD-01..10 (ADMIN-09)
```

- `verify_architecture.py`: Administración y Operador en el portal; el Supervisor usa los clientes de microservicios; solo AuthService firma tokens y cada servicio valida el JWT y la revocación en Redis; puertos iguales en Compose, systemd y nginx; migraciones 001 y 004–016 montadas en orden en ambos Compose; copias de migraciones idénticas.
- `check_migrations_concurrency.py`: 001 + 002 + 003 + 004..016 en una base vacía, segunda pasada idempotente (sin 001 ni 003), tablas y *triggers* clave; luego Monitoring, Notifications e Integration (API y workers) arrancan a la vez cinco veces, sin *deadlock*.
- `browser_validate_flows.py`: programación real (asistente + modal de privacidad), inicio, cámara, inicial, final rechazado por una diferencia física real, recaptura, validación que no puede cambiar el total, revisión, historial; escalamiento y contingencia (contradicha → 409; declarada sin final YOLO); Supervisor: evidencias reales, resolución, tres cierres (`final_count`, `final_count_escalation` nunca seguro, `contingency_count`), notificaciones, reportes, CSV/PDF, indicadores, salud, auditoría, charolas. Capturas en `docs/evidencias/MS-10/` (no se versionan). Donde el asistente oculta pasos, los campos se llenan por script y el envío pasa por el `submit` real de la página (CSRF, validación y diálogos de confirmación incluidos); queda indicado en `results.md`.
- **Defecto encontrado por el navegador y corregido:** el formulario de escalamiento (MS-09) estaba dentro del panel del conteo final, que la página oculta en la etapa de recaptura: el Operador nunca lo veía. Prueba de regresión `test_escalation_form_is_not_inside_the_final_panel`.

### 2.10 MS-11: herramientas del despliegue

```powershell
python scripts/vm/smoke_local_harness.py   # pila aislada + Gateway nginx TLS real (2 hosts, HTTPS, WSS) + YOLO real -> smoke_post_deploy.py
python scripts/vm/compare_inventory.py <inventario.txt>   # tabla instalado/falta/actualizar/conflicto
```

- `smoke_post_deploy.py` solo habla con nginx (sin base de datos ni SSH), lee las credenciales de variables `PEF_SMOKE_*` y se niega a correr sin `--confirm-test-institution`. `--resolve host:ip` permite probar antes de que exista el DNS.
- `inspect_vm.sh` y `compare_inventory.py` se probaron en contenedores CentOS Stream 10; `s3_mirror.py`, contra el Garage de pruebas.

### 2.11 MS-11: responsabilidades completadas y Docker Compose integral

```powershell
python scripts/compose_ms11.py up          # pila COMPLETA en Compose, proyecto aislado pef_ms11 (Gateway TLS, puertos 26xxx)
python scripts/e2e_ms11_compose.py         # 31 comprobaciones: tres roles, pasos 0-19 y escenarios A-G
python scripts/compose_ms11.py down        # conserva los volúmenes
docker run ... pef-ms-test/vision:latest python /scripts/measure_tracking.py <videos>   # ver GUIA_TRACKING.md §4.2
```

- Pruebas nuevas:
  - VisionWorker `test_tracking.py` (16);
  - portal `test_ms11_model_evaluations.py` (13) y `test_ms11_tracking_camera.py` (8);
  - NotificationsService `test_ms11_notifications.py` (5);
  - MonitoringService `test_collector.py` (+2).
- `check_migrations_concurrency.py` escribe filas de los tipos de 017/018 **entre** las dos pasadas: es la regresión del defecto de las restricciones redefinidas (`NOT VALID`).
- Escenarios del E2E de Compose:
  - A: faltante escalado.
  - B: contingencia contradicha.
  - C: acceso desde otra institución.
  - D: revocación en 4 servicios.
  - E: caída y recuperación de CatalogService (Gateway, colector, alerta).
  - F: reinicio de VisionWorker y nuevo *bind*.
  - G: persistencia tras reiniciar PostgreSQL, Redis, MongoDB y Garage.

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

## 6. MS-12: pruebas de los 20 dominios

| Qué | Comando | Tipo |
| :--- | :--- | :--- |
| Equivalencias de kit | `apps/CatalogService/tests/test_ms12_equivalences.py`; `apps/BackendWebFlask/tests/test_ms12_kit_equivalences_portal.py` | Integración (PostgreSQL real) / portal |
| Calibración de cámaras | `apps/BackendWebFlask/tests/test_ms12_camera_calibration.py`; `apps/VisionWorker/tests/test_ms12_camera_calibration.py` (en la imagen del worker) | Integración / unitaria |
| Bitácora de tracking | `apps/BackendWebFlask/tests/test_ms12_tracking_events.py`; `apps/SessionService/tests/test_ms12_tracking_events.py` | Integración |
| Sello de la validación | `apps/BackendWebFlask/tests/test_ms12_validation_seal.py`; `apps/SessionService/tests/test_ms12_validation_seal.py` (mismo vector de prueba en ambas) | Integración |
| Sección de integridad del Supervisor | `apps/BackendWebFlask/tests/test_ms12_supervisor_integrity.py` | Portal |
| Regresión de modelos | `apps/BackendWebFlask/tests/test_ms12_model_regressions.py`; corrida real: `scripts/model_regression.py` (ver `GUIA_ADMIN_MONOLITO.md` §8) | Integración / inferencia real |
| Lotes de exportación | `apps/IntegrationService/tests/test_ms12_exports.py` | Integración |
| Monitoreo de integración | `apps/MonitoringService/tests/test_ms12_probes.py` | Integración (conexiones reales por `application_name`) |
| Recuperación de evidencias | `apps/BackendWebFlask/tests/test_ms12_evidence_recovery.py` | Integración (MongoDB real) |
| Consistencia de auditoría | `python scripts/check_audit_consistency.py --database-url <url> [--since <ISO>]` | Solo lectura; código de salida 1 si hay violaciones duras |
| Migración 019 | `python scripts/check_migrations_concurrency.py` (base nueva + reaplicación + arranques simultáneos) | Integración |
| Integral Docker Compose | `python scripts/compose_ms11.py up` y `python scripts/e2e_ms11_compose.py` (0–19, A–G, **M1–M10**) | E2E con contenedores reales |
| Diagramas | `python scripts/make_ms12_diagrams.py` (regenera `docs/diagramas/*.drawio`) | — |

Lo que **no** cubren: cámaras físicas (solo fuente `simulated`), un HIS real (solo HAPI de pruebas), la comparación entre dos modelos distintos (solo existe `best.pt`), firma legal de la validación (D7).
