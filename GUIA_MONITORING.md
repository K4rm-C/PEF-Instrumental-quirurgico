# PEF — Monitoring Service

MonitoringService (`apps/MonitoringService`, puerto 5008) observa la disponibilidad, las dependencias y la latencia de los 13 componentes del PEF.

- Contrato: [DS09 §1.3.6](data/DataStandards/PEF-DS09-API-Exchange-Contracts.md), complemento v0.1.0.
- Documentación ejecutable: `http://127.0.0.1:5008/api/v1/monitoring/docs`.

**Regla central: ningún componente depende de MonitoringService.**
- El Operador, el Supervisor y los demás microservicios nunca lo llaman.
- Solo lo consultan dos pantallas: "Estado del sistema" del Administrador y "Estado de los servicios" del Supervisor.
- No escribe nada fuera de sus propias tablas ni toca las sesiones quirúrgicas.

## 1. Análisis inicial

| Componente | Mecanismo existente | ¿Verificable tal cual? |
| :--- | :--- | :--- |
| Auth, Catalog, Session, Evidence, Audit, Notifications | `/health/ready` DS09: `{status, checks}`, 200 / 503 | Sí |
| VisionWorker | `/health`: `{ok, weights_present, …}` | Sí |
| Garage | API de administración `GET /health` (sin token) | Sí (en Compose no tenía healthcheck) |
| PostgreSQL, Redis, MongoDB | Healthchecks de Compose (`pg_isready`, `redis-cli ping`, `mongosh ping`) | Sí, con su protocolo: `SELECT 1`, `PING`, `ping` |
| Portal BackendWebFlask | **No tenía endpoint de salud** (`/health` daba 404) | Ajuste mínimo |
| notifications-worker | Sin puerto HTTP ni healthcheck; su conexión no se distinguía en PostgreSQL | Ajuste mínimo |

**Ajustes mínimos de healthchecks:**
1. **Portal:** `GET /health`, `/health/ready` y `/health/live`, sin autenticación (`controllers/health_routes.py`).
   - Solo hacen `SELECT 1` en PostgreSQL.
   - No llaman a AuthService ni a ningún otro servicio.
2. **NotificationsService:** sus procesos se identifican en PostgreSQL con `application_name` (`pef-notifications` y `pef-notifications-worker`). El monitoreo comprueba que el worker mantiene su conexión y que el evento pendiente más antiguo del outbox no esté atascado.

**Lo que no se mide.** La infraestructura actual no expone CPU, memoria, tasa de peticiones ni errores por endpoint, y el servicio no los inventa. Se mide:
- si el componente responde;
- el código HTTP;
- las dependencias que el propio componente reporta;
- la latencia real de la comprobación.

## 2. Arquitectura

```
monitoring-collector (collector.py) ── cada intervalo, en paralelo, con timeout por componente ──▶ 13 componentes
        │ INSERT (1 transacción por ciclo)
        ▼
PostgreSQL: monitoring_run, monitoring_check  ◀── SELECT ── monitoring (API) ◀── HTTP + Bearer ── pantallas RF (Admin / Supervisor)
```

- **Una corrida por intervalo** (30 s por defecto). Cada componente se comprueba una vez, en su propio hilo y con su propio timeout (3 s): un componente colgado o caído se registra como `down` sin retrasar a los demás. Toda la corrida dura aproximadamente un timeout.
- **Carga mínima.**
  - Una petición por componente y ciclo, siempre al endpoint de salud más barato.
  - En PostgreSQL solo `SELECT 1` y dos consultas pequeñas (`pg_stat_activity` y el outbox).
  - Cada ciclo escribe 1 fila de corrida y 13 de comprobación. A 30 s son unas 37 000 filas al día, y la retención de 7 días las acota a unas 260 000.
- **Estados:**
  - `up`: respondió con todas sus dependencias bien, dentro del umbral de latencia (1500 ms).
  - `degraded`: respondió, pero alguna dependencia falló, fue lento, la respuesta no era válida o VisionWorker no tiene los pesos del modelo.
  - `down`: no respondió (rechazo, timeout, DNS), o respondió que nada funciona.
  - `unknown`: no configurado, nunca comprobado, o con la última comprobación más vieja que 3 intervalos (`freshness = stale`). Si el recolector se detiene, la API **no repite** el último estado conocido: muestra `unknown`. El último estado medido se conserva aparte en `measured_status`.
- **Agregados reales en SQL:**
  - disponibilidad = (up + degraded) / comprobaciones conocidas;
  - `up_pct` = up / conocidas;
  - latencia promedio, p95, mínima y máxima, sobre las comprobaciones respondidas (un timeout es una caída, no una muestra de latencia);
  - series por intervalo con `date_bin`.

## 3. Componentes y cómo se verifica cada uno

| Componente | Destino en Docker | Comprobación |
| :--- | :--- | :--- |
| `web` | `http://host.docker.internal:5000` | `/health/ready` |
| `auth` | `http://auth:5001` | `/health/ready` |
| `catalog` | `http://catalog:5003` | `/health/ready` (database, redis) |
| `session` | `http://session:5004` | `/health/ready` |
| `evidence` | `http://evidence:5005` | `/health/ready` (database, redis, storage) |
| `audit` | `http://audit:5006` | `/health/ready` |
| `notifications` | `http://notifications:5007` | `/health/ready` |
| `notifications-worker` | PostgreSQL | Conexión `pef-notifications-worker` y antigüedad del backlog (> 120 s = `degraded`) |
| `vision` | `http://host.docker.internal:5002` | `/health` y `weights_present` |
| `postgres` | conexión del servicio | `SELECT 1` con `statement_timeout` |
| `redis` | `redis:6379` | `PING` |
| `mongo` | `mongo:27017` | `ping` |
| `garage` | `http://garage:3903` | `GET /health` de la API de administración |

**Portal y VisionWorker fuera de Docker.** Hoy corren en el host y el recolector los alcanza por `host.docker.internal`. En Linux eso funciona gracias a `extra_hosts: host-gateway`. Lo comprobé en Docker Desktop (Windows) con ambos procesos escuchando en `127.0.0.1`. Cuando se contenericen, basta con cambiar dos variables, por ejemplo `MONITORING_TARGET_WEB_URL=http://web:5000` y `MONITORING_TARGET_VISION_URL=http://vision:5002`. Si una variable queda vacía, el componente aparece como `unknown` (`not_configured`).

## 4. Tablas, migración y retención

`data/migrations/007_monitoring.sql` es idempotente (solo `CREATE … IF NOT EXISTS`). Se aplica:
- en volúmenes nuevos, como `07_monitoring.sql` del initdb de postgres;
- en bases existentes, al iniciar la API y el recolector, bajo `pg_advisory_xact_lock`.

| Tabla | Contenido |
| :--- | :--- |
| `monitoring_run` | `id`, `started_at`, `finished_at`, `components`, `failures` |
| `monitoring_check` | `id`, `run_id` (FK, `ON DELETE CASCADE`), `component`, `checked_at`, `status`, `latency_ms`, `http_status`, `dependencies` (jsonb), `error_type`, `endpoint`, `details` (jsonb) |

- No hay ninguna FK hacia tablas operativas.
- Nunca se guardan tokens, contraseñas ni cadenas de conexión: `endpoint` es solo `scheme://host:port/path`.
- **Retención:** el recolector borra cada hora, en lotes, las corridas con más de `MONITORING_RETENTION_DAYS` (7) días; sus comprobaciones se borran en cascada.

## 5. API y seguridad

Endpoints:
- `GET /api/v1/monitoring/services`;
- `/services/{name}`;
- `/history`, con filtros por componente, estado y rango de fechas, y paginación;
- `/summary`, con ventana, componente e intervalo de la serie.

Formatos:
- JSON o XML según `Accept`, validados por XSD;
- `X-Correlation-ID` en cada respuesta;
- errores con el sobre DS09.

Validación de cada petición:
- JWT con firma, expiración y `type=access`;
- sesión Redis no revocada;
- usuario e institución activos.

Ninguna cabecera propia es identidad.

| Rol | Ve |
| :--- | :--- |
| `it_admin` | Todo: latencias, códigos HTTP, dependencias, tipo de error, endpoint, historial y agregados |
| `spd_supervisor` | Solo `/services` (nombre, estado y última comprobación) y `/summary` (disponibilidad). Sin latencias, endpoints, errores ni historial (403) |
| Otros | 403 |

## 6. Portal

- **Administración → Sistema → "Estado del sistema" (`/admin/system-status`).** Es una pantalla nueva, porque el Administrador no tenía ninguna, y reutiliza los mismos componentes visuales. Muestra:
  - contadores por estado;
  - la tabla de componentes: estado, última comprobación, latencia, HTTP, dependencias y error controlado;
  - disponibilidad y latencia promedio / p95 de la ventana elegida (1 h, 6 h, 24 h o 7 d);
  - una **gráfica Highcharts** de latencia promedio por componente, alimentada solo con la serie real de `/summary`. Sin mediciones no se dibuja: aparece "Aún no hay mediciones";
  - el historial reciente filtrable y paginado.
- **Supervisor → "Estado de los servicios" (`/supervisor/services-health`).** Esta pantalla ya existía, pero mostraba filas fijas ("direct", "pending"). Ahora muestra el resumen autorizado: estado, última comprobación y disponibilidad de 24 h. También es la página que aparece cuando SessionService no responde.
- **MonitoringService caído:** ambas pantallas muestran un aviso controlado (503) y el resto del portal sigue igual.
- **Sin `MONITORING_SERVICE_URL`:** aparece "El monitoreo no está habilitado". No hay respaldo local ni estados inventados.

## 7. Docker, arranque y apagado

| Variable | Dónde | Uso |
| :--- | :--- | :--- |
| `MONITORING_SERVICE_URL`, `MONITORING_SERVICE_TIMEOUT` | BackendWebFlask | `http://127.0.0.1:5008`, 3 s |
| `MONITORING_PORT` | compose | Puerto publicado de la API |
| `MONITORING_INTERVAL_SECONDS`, `MONITORING_CHECK_TIMEOUT_SECONDS`, `MONITORING_RETENTION_DAYS` | compose | 30 s, 3 s, 7 días |
| `MONITORING_TARGET_<NOMBRE>_URL` | compose → collector | Destinos (nombres DNS de Compose por defecto) |
| `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET_KEY` | compose → monitoring | El recolector no recibe el secreto JWT |

```powershell
docker compose up -d --build monitoring monitoring-collector    # tras postgres y redis (depends_on: healthy)
docker compose logs -f monitoring-collector                      # una línea por ciclo con el estado de cada componente
curl -i http://127.0.0.1:5008/health                             # X-Last-Run-Age-Seconds
docker compose stop monitoring monitoring-collector              # apagado: nada más se ve afectado
cd apps/MonitoringService; python -m pytest -q                   # PostgreSQL/Redis reales
python scripts/e2e_monitoring.py                                 # servicios vivos; detiene y reinicia catalog, notifications y el monitoreo
```

No se agregan volúmenes: el historial vive en el PostgreSQL existente.
