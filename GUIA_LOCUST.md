# PEF — Pruebas de carga con Locust

Pruebas de carga sobre la **pila integral local** (`docker-compose.integral.yml`), llamando a los servicios **directamente** (`:5000`–`:5007`), sin pasar por el gateway. Cubren las lecturas de cada rol y el flujo de sesión completo, sin YOLO.

## 1. Qué simula

| Usuario Locust | Peso | Cuentas (rotación) | Qué hace |
| --- | --- | --- | --- |
| `OperatorUser` | 4 | operator@, operador2@, ana.ruiz@, luis.pena@ | Panel, sesiones, historial, detalle, notificaciones, perfil (portal) |
| `SupervisorUser` | 3 | supervisor@, supervisor2@, carla.soto@ | Panel, sesiones, detalle + conteos, discrepancias, reportes, indicadores, catálogo, instrumentos, salud de servicios |
| `AppUser` | 3 | floor@, nora.diaz@ (`or_charge`) | Las llamadas de la App Android (`PefApis.kt`): sesiones, detalle, evidencias, resumen, notificaciones, catálogo, `/me` |
| `AdminUser` | 1 | admin@, ti.extra@ | Panel, usuarios, familias, procedimientos, auditoría, estado del sistema |
| `SessionFlowUser` | fijo (`PEF_FLOW_USERS`, 1 por defecto) | SPD: supervisor@ / carla.soto@; OP: operador2@ / ana.ruiz@ | El SPD programa (portal → SessionService), el OP inicia, hace el reporte manual con las cantidades esperadas, y el SPD cierra |

- **Sesión de cada usuario:** cada usuario Locust hace login **una sola vez** (`on_start`) y reutiliza el token. Con un 401 renueva el token (`/refresh`) y reintenta. Así nunca se dispara el bloqueo de Auth (8 fallos por email o IP → 429 durante 15 min).
- **El flujo no deja instrumentos perdidos:** se programa sin aviso de privacidad (modo `manual_no_privacy`) y se reporta el conteo exacto, así que ningún instrumento queda en `lost` ni aparece `stock_block`. Cada iteración sí deja una operación y una sesión cerradas, más su auditoría.
- **Entrada `FLOW` en el reporte:** el tiempo total de programar a cerrar aparece como una entrada propia, `FLOW session flow schedule->close`.
- **Portal:** cada POST lleva `X-CSRF-Token` (`GET /csrf-token`). Una redirección a `/sign-in` cuenta como fallo.

Código: `tests/load/` (`locustfile.py`, `common.py`, `users/*.py`, `profiles/*.conf`, `run_load.ps1`).

## 2. Requisitos

- La pila integral levantada (ver `README.md`). VisionWorker **no** es necesario.
- `data/seeds/DEMO_CREDENTIALS.local.md` (no versionado; de ahí se leen las contraseñas). Otra ruta: `PEF_LOCUST_CREDENTIALS`.
- Python con Locust:

```powershell
python -m pip install -r tests\load\requirements.txt
```

## 3. Ejecución

Siempre desde la raíz del repositorio. El script espera a que los servicios estén sanos, aplica `data/seeds/004_seed_demo_locust.sql` (idempotente; Compose no lo monta) y corre el perfil.

```powershell
.\tests\load\run_load.ps1 -Profile smoke                 # 5 usuarios, 1 min: todo debe dar 0 fallos
.\tests\load\run_load.ps1 -Profile load -Reset           # semilla limpia (down -v / up --build) y 50 usuarios, 10 min
.\tests\load\run_load.ps1 -Profile stress -FlowUsers 2   # escalones de 50 usuarios hasta 200, 3 min cada uno
.\tests\load\run_load.ps1 -Ui                            # interfaz web de Locust en http://127.0.0.1:8089
```

**Opciones:**
- `-SkipSeed`: no vuelve a aplicar la semilla 004.
- `-Python <exe>`: usa otro intérprete.
- `-EnvFile <ruta>`: usa otro archivo de entorno.

**Manual:**
```powershell
python -m locust --config tests/load/profiles/smoke.conf
```

**Reportes:** `tests/load/reports/<perfil>.html` y `<perfil>_stats.csv`, `_failures.csv` y `_stats_history.csv`. La carpeta está ignorada por git.

**Limpieza:** `run_load.ps1 -Reset` en la siguiente campaña. `e2e_restore_stock.py` no aplica aquí, porque solo corre contra la base de pruebas.

## 4. Criterios de aceptación

Locust termina con código 1 si hubo **cualquier** fallo. Además, `locustfile.py` revisa estos umbrales al terminar y deja código 1 si se exceden:

| Umbral | Valor por defecto | Variable |
| --- | --- | --- |
| p95 por endpoint de lectura (GET) | 800 ms | `PEF_MAX_READ_P95_MS` |
| p95 por endpoint de escritura (POST…) | 2000 ms | `PEF_MAX_WRITE_P95_MS` |
| p95 del flujo completo (`FLOW`) | 20000 ms | `PEF_MAX_FLOW_P95_MS` |
| Proporción de errores | 1 % | `PEF_MAX_ERROR_RATIO` |

## 5. Variables

| Variable | Por defecto | Uso |
| --- | --- | --- |
| `PEF_WEB_URL`, `PEF_AUTH_URL` | `http://127.0.0.1:5000`, `:5001` | Portal y AuthService |
| `PEF_CATALOG_URL`, `PEF_SESSION_URL`, `PEF_EVIDENCE_URL`, `PEF_NOTIFICATIONS_URL` | `:5003`, `:5004`, `:5005`, `:5007` | Microservicios |
| `PEF_API_URL` | (vacío) | Envía todo el tráfico Bearer, incluido `/api/v1/auth/*` de la App, por el gateway (ej. `http://127.0.0.1:8080`) |
| `PEF_FLOW_USERS` | 1 | Flujos de sesión simultáneos; como máximo, las cuentas de `PEF_LOCUST_FLOW_OPERATORS` |
| `PEF_LOCUST_OPERATORS`, `_SUPERVISORS`, `_ADMINS`, `_APP`, `_FLOW_OPERATORS`, `_FLOW_SUPERVISORS` | ver `common.py` | Cuentas por rol (separadas por comas) |
| `PEF_LOCUST_PROCEDURE_ID`, `PEF_LOCUST_KIT`, `PEF_LOCUST_ROOM`, `PEF_LOCUST_STATION` | Demo / Video Demo Kit 1 / OR-01 / CS-01 | Datos del flujo |
| `PEF_LOAD_SHAPE=stress`, `PEF_STRESS_MAX_USERS`, `PEF_STRESS_STEP_SECONDS` | 200, 180 | Forma escalonada (la activa `-Profile stress`) |

## 6. Corrida opcional por el gateway

Desde una sola IP, los límites de nginx frenan a Locust antes de que llegue al backend: login 10 por minuto y API 20 r/s con ráfaga de 40, ambos por IP. Para medir el costo del gateway, sube los límites al levantarlo y usa solo los usuarios de la App:

```powershell
$env:GATEWAY_RATE_AUTH='600r/m'; $env:GATEWAY_BURST_AUTH='200'; $env:GATEWAY_RATE_API='2000r/s'; $env:GATEWAY_BURST_API='2000'
docker compose -f docker-compose.integral.yml --env-file .env --profile gateway up -d gateway
$env:PEF_API_URL='http://127.0.0.1:8080'
python -m locust -f tests/load/locustfile.py AppUser --headless -u 50 -r 5 -t 5m --html tests/load/reports/gateway.html --csv tests/load/reports/gateway
```

## 7. Resultados

| Fecha | Perfil | Usuarios | Peticiones | Fallos | p95 agregado | Observaciones |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-11-20 | smoke | 5 (1 flujo) | 120 | 0 | 1000 ms | 3 flujos completos (p95 3.1 s). Un pico aislado de 13 s en `GET /api/v1/sessions/[id]/evidence`; medido aparte, 40–280 ms. |
| 2026-11-20 | load (reducido) | 25 (2 flujos), 5 min | 2390 (8 r/s) | 55 (2.3 %) | 180 ms | Mediana de 30 ms. Los 55 fallos son los dos bugs del portal de abajo. 28 flujos completos, sin fallos (p95 1.2 s). `GET /supervisor/sessions/new`: p95 de 3.2 s con mediana de 34 ms. Equipo con solo ~0.3 GB de RAM libre. |
| 2026-11-20 | load | 50 (2 flujos), 8 min | 7227 (15 r/s) | 1 (0.01 %) | 170 ms | Ya con las plantillas de reportes e indicadores corregidas. Mediana de 33 ms; umbrales de latencia OK. El único fallo fue un error de conexión (`HTTP 0`) en `/operator/sessions`. 42 flujos completos sin fallos (p95 1.1 s). Equipo con ~0.2–0.3 GB de RAM libre. |
| _pendiente_ | stress | ≤200 | | | | |

**Defectos encontrados (2026-11-20):**
- `/supervisor/reports` responde 500 en modo real: `supervisor/reports.html` es la plantilla del demo V3 y espera `reports.filters`, pero recibe otra estructura (`UndefinedError: 'list object' has no attribute 'filters'`).
- `/supervisor/indicators` responde 500 por la misma causa: `TypeError: unhashable type: 'dict'` en `{{ _(period) }}` (`indicators.html`).
- **Causa y corrección:** el merge a `main` (`139dd4d`) conservó las plantillas del demo V3, mientras que `report_routes.py` se quedó con la versión de microservicios. Se restauraron las dos plantillas desde `523e117`; esa versión cubre el modo demo y el real. Ambas páginas responden 200.
