# PEF — Despliegue integral con Docker Compose

Una vez creada la configuración local con `python scripts/prepare_local.py` y colocado el modelo `best.pt`, desde la raíz del repositorio quedan en contenedores todos los componentes: las bases de datos, los siete microservicios con sus workers, el portal RF (BackendWebFlask) y VisionWorker.

```powershell
docker compose up -d --build
```

## 1. Arquitectura

```
Navegador ──► web :5000 (portal RF, gunicorn)        ──► auth / catalog / session / evidence / audit /
   │                                                     notifications / monitoring  (nombres de servicio)
   ├──► auth :5001 (script de login, cookie)           ──► vision:5002 (YOLO, CPU)
   └──► garage :3900 (URL firmadas de evidencias)      ──► postgres / redis / mongo / garage

monitoring-collector ──► http://web:5000, http://vision:5002, http://auth:5001, … (red interna)
notifications-worker ──► notification_outbox (PostgreSQL)
```

- Todos los contenedores están en la red `pef-network` y se llaman por **nombre de servicio**. Dentro de Docker no se usa `localhost` ni `host.docker.internal`.
- El navegador solo usa tres puertos publicados: el portal, AuthService (el login JS deja la cookie) y Garage (las URL firmadas de evidencias).
- El portal y VisionWorker guardan estado en memoria (instantáneas de visión, sesiones ligadas, hilo del outbox). Por eso corren con **gunicorn, 1 proceso y varios hilos** (`gthread`). No se deben escalar con `--scale`.

## 2. Servicios y puertos

| Servicio | Imagen / build | Puerto interno | Publicado en el host | Lo usa el navegador |
| :--- | :--- | :--- | :--- | :--- |
| web | `apps/BackendWebFlask` | 5000 | `WEB_PORT` (5000), todas las interfaces | Sí |
| auth | `apps/BackendAuthService` | 5001 | `AUTH_PORT` (5001), todas las interfaces | Sí (login) |
| garage (S3) | `dxflrs/garage:v2.4.1` | 3900 | `S3_API_PORT`, todas las interfaces | Sí (URL firmadas) |
| vision | `apps/VisionWorker` | 5002 | `127.0.0.1:VISION_PORT` | No |
| catalog / session / evidence / audit / notifications / monitoring | `apps/*Service` | 5003 / 5004 / 5005 / 5006 / 5007 / 5008 | `127.0.0.1:*_PORT` | No |
| postgres / redis / mongo | imágenes oficiales | 5432 / 6379 / 27017 | `127.0.0.1:*_PORT` | No |
| garage RPC / admin | — | 3901 / 3903 | `127.0.0.1` | No |
| notifications-worker, monitoring-collector | mismas imágenes de su servicio | — | — | No |

Los puertos en `127.0.0.1` sirven solo para diagnóstico y pruebas desde la misma máquina; no quedan expuestos a la red. Para cambiar un puerto, edita el `.env`; el puerto interno no cambia.

**Mismo host en el navegador:** abre el portal con el mismo host que `AUTH_BROWSER_URL`. Con el valor por defecto `http://127.0.0.1:5001`, la URL es `http://127.0.0.1:5000`. La cookie de sesión queda ligada a ese host. Si usas otro nombre (por ejemplo `localhost` o la IP de la máquina), ajusta `AUTH_BROWSER_URL`, `AUTH_ALLOWED_ORIGINS` y `S3_PUBLIC_ENDPOINT`.

## 3. Volúmenes y red

| Volumen | Contenido | ¿Sobrevive a `up --force-recreate`? |
| :--- | :--- | :--- |
| `postgres_data` | Base InstruMed | Sí |
| `redis_data` | JWT `auth:jwt:{jti}` con AOF (`--appendonly yes --appendfsync everysec`): las revocaciones sobreviven a un reinicio | Sí |
| `mongo_data` | DS06: `checkpoint_frame`, `session_telemetry` | Sí |
| `garage_meta`, `garage_data` | Objetos de evidencia (JPEG/GIF) | Sí |
| `web_outbox` | Cola durable de checkpoints del portal (`/data/outbox`) | Sí |
| `vision_data` | Spool durable de telemetría (`/data/telemetry_spool`) y videos subidos (`/data/uploads`) | Sí |

`best.pt` no es un volumen: es un **bind mount de solo lectura** de `apps/VisionWorker/weights` en `/models` (sección 6).

Los volúmenes y la red llevan el prefijo del proyecto de Compose. Por defecto ese prefijo es el nombre de la carpeta (`pef_integrado_admin_monolito_*`). No hay `container_name` fijo, así que esta copia convive con otros proyectos PEF y con el entorno de pruebas `pef_e2e`.

## 4. Variables

Para una copia nueva, ejecuta `python scripts/prepare_local.py`. Se crearán `.env` e `infra/garage.toml` con valores aleatorios, ambos ignorados por Git. El archivo `infra/garage.toml.example` es una plantilla. El script no sobrescribe una configuración existente. En configuraciones manuales, reemplaza también cada `CAMBIAR_*` de `.env.example` con un valor aleatorio. La clave de acceso de Garage tiene el formato `GK` + 24 hex y el secreto, 64 hex.

- **Obligatorias:** `POSTGRES_*`, `REDIS_PASSWORD`, `MONGO_*`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET_EVIDENCE`, `JWT_SECRET_KEY` y `SESSION_SECRET_KEY`. Si falta esta última, Compose se niega a arrancar.
- **Puertos publicados:** `WEB_PORT`, `AUTH_PORT`, `VISION_PORT`, `*_PORT` de cada servicio, `S3_API_PORT`, `GARAGE_RPC_PORT` y `GARAGE_ADMIN_PORT`.
- **Navegador:** `AUTH_BROWSER_URL`, `AUTH_ALLOWED_ORIGINS` y `S3_PUBLIC_ENDPOINT`.
- **Las URL internas no se configuran.** `docker-compose.yml` fija `http://auth:5001`, `http://vision:5002`, `postgres:5432`, etc. Los `*_SERVICE_URL`, `DB_*` y `*_URL` con `127.0.0.1` del `.env` solo los usa el modo de desarrollo con procesos del host (sección 9).
- **Producción:** `AUTH_RETURN_RESET_TOKEN=false`, y `AUTH_COOKIE_SECURE=true` cuando haya HTTPS delante.

Las imágenes no contienen secretos. Toda la configuración llega por variables de entorno en tiempo de ejecución, y los `.dockerignore` excluyen `.env*`, entornos virtuales, pruebas y pesos.

## 5. Arranque y operación

Todos los comandos se ejecutan desde la raíz del repositorio.

| Acción | Comando |
| :--- | :--- |
| Construir y arrancar todo | `docker compose up -d --build` |
| Estado y healthchecks | `docker compose ps` (columna STATUS: `healthy`) |
| Logs de un servicio | `docker compose logs -f web` (o `vision`, `auth`, `monitoring-collector`, …) |
| Reiniciar un servicio | `docker compose restart web` |
| Detener sin perder datos | `docker compose stop` (o `docker compose down`, que borra contenedores y red, pero **no** volúmenes) |
| Volver a arrancar | `docker compose start` / `docker compose up -d` |
| Reconstruir tras cambiar código | `docker compose up -d --build web` (solo ese servicio) |
| Recrear un contenedor | `docker compose up -d --force-recreate --no-deps web` (las colas de `web_outbox` y `vision_data` se conservan) |
| Salud por HTTP | `http://127.0.0.1:5000/health/ready`, `http://127.0.0.1:5001/health/ready`, `http://127.0.0.1:5002/health` |

> **Nunca uses `docker compose down -v` en el entorno principal:** borra PostgreSQL, Redis, MongoDB, Garage y las colas. Solo el entorno de pruebas `pef_e2e` se reinicia así, mediante `scripts/e2e/isolated.py reset`.

**Primer arranque:**
- postgres crea la base desde `data/` (esquema, catálogos y semilla demo);
- Garage crea el bucket por defecto;
- Notifications y Monitoring aplican sus migraciones idempotentes.

El portal espera solo a que **PostgreSQL y Redis** estén sanos (`depends_on: service_healthy`). Ningún otro servicio bloquea su arranque (sección 7).

**Recuperación:**
- **Un servicio `unhealthy`:** revisa `docker compose logs <servicio>`, corrige la causa y ejecuta `docker compose restart <servicio>`. Todos tienen `restart: unless-stopped`.
- **MongoDB o EvidenceService caídos:** el portal sigue contando. Los checkpoints esperan en `web_outbox` y la telemetría en `vision_data`, y se entregan solos (una vez, sin duplicados) cuando el servicio vuelve.
- **Notifications detenido:** los eventos esperan en `notification_outbox` y el worker los procesa al volver.
- **Redis reiniciado:** las sesiones y revocaciones se recuperan del AOF.
- **Cambio de `.env`:** ejecuta `docker compose up -d`, que recrea solo los contenedores cuya configuración cambió.

## 6. `best.pt` (pesos YOLO)

- **Dónde va:** después de clonar, copia el archivo a mano en `apps/VisionWorker/weights/best.pt`. No está en git (`.gitignore`: `*.pt`, `weights/`) y no debe subirse.
- **Cómo llega al contenedor:** `docker-compose.yml` monta la carpeta `./apps/VisionWorker/weights` en `/models` en **solo lectura** (`:ro`), y `VISION_WEIGHTS_PATH=/models/best.pt`. El archivo nunca se copia a la imagen: `apps/VisionWorker/.dockerignore` excluye `weights/` y `*.pt`. El contenedor no puede modificarlo.
- **Por qué la carpeta y no el archivo:** si el archivo faltara, Docker crearía un directorio vacío con su nombre.
- **Sin el archivo:** el worker arranca igual, `/health` informa `weights_present: false` y cada video termina con un error controlado (`Weights not found at /models/best.pt…`). El portal muestra su ruta controlada de "visión no disponible", y el Operador puede seguir con el conteo manual.
- **Sin descargas:** `YOLO_OFFLINE=1` evita que ultralytics consulte internet (verificación de versión y descargas) dentro del contenedor.

**CPU / GPU:** la imagen instala torch y torchvision **CPU** desde `download.pytorch.org/whl/cpu`, para que sea más liviana y no dependa de drivers. Para usar GPU harían falta:
- una imagen base con CUDA y la rueda de torch correspondiente;
- NVIDIA Container Toolkit;
- `deploy.resources.reservations.devices` en el servicio `vision`.

Esto no forma parte de esta etapa.

## 7. Independencia del Operador

| Si se detiene… | El Operador… |
| :--- | :--- |
| SessionService, CatalogService, AuditService | Sigue: lista, inicio, conteo con visión o manual, validación humana. Su flujo corre en el portal contra PostgreSQL. |
| NotificationsService (+ worker) | Sigue. Los avisos se generan después, desde el outbox. |
| MonitoringService (+ collector) | Sigue. Solo el estado del sistema queda sin datos nuevos. |
| MongoDB | Sigue. Checkpoints y telemetría esperan en `web_outbox` y `vision_data`. |
| EvidenceService / Garage | Sigue. Las evidencias se reintentan desde el outbox. |
| vision | Usa el conteo manual (ruta controlada). |

Las pantallas del Supervisor o del Administrador que dependen de un servicio detenido muestran un aviso controlado, nunca datos inventados. El cierre de sesión del Supervisor necesita SessionService.

## 8. Pruebas del despliegue

`scripts/e2e_docker.py` prueba el despliegue principal ya levantado. Cambia datos (crea una familia y sesiones con conteo coincidente; no marca piezas como perdidas). Además detiene, reinicia y recrea contenedores **de este proyecto**. Nunca ejecuta `down` ni borra volúmenes.

```powershell
docker compose up -d --build
python scripts/make_test_tray_videos.py .e2e/videos          # si no existen (necesita el entorno de vision)
python scripts/e2e_docker.py .e2e/videos                      # entorno con requests, psycopg, pymongo
```

**Qué comprueba:**
- imágenes sin `best.pt`, `.env` ni secretos, con usuario no root;
- los 15 servicios `healthy`;
- montaje `/models` de solo lectura;
- login y los tres roles;
- alta y edición en Administración;
- programación (Supervisor) e inicio (Operador);
- video procesado por `vision` con el modelo real, y error controlado con un worker temporal sin pesos;
- validación humana y cierre;
- PostgreSQL, checkpoints y telemetría en MongoDB;
- Redis: JWT, revocación y persistencia AOF tras reiniciar;
- auditoría, notificaciones y outbox;
- MonitoringService revisando `web` y `vision` por nombre interno;
- Operador con Session/Catalog/Audit/Notifications/Monitoring detenidos;
- reinicio y recuperación, y colas durables a través de una recreación de `web` y `vision`;
- logs sin contraseñas, JWT ni secretos.

El entorno E2E aislado (`GUIA_PRUEBAS.md`) puede correr el portal y VisionWorker contenerizados con `scripts/e2e/isolated.py all --mode containerized`. Usa los puertos 56000/56001/56002, así convive con este despliegue.

Los videos de prueba son **sintéticos** (generados desde una foto de referencia). Prueban el camino técnico, no la exactitud clínica del modelo.

## 9. Modo de desarrollo (procesos del host)

Para depurar el portal o VisionWorker con Python local, mientras el resto sigue en Docker:

```powershell
docker compose -f docker-compose.yml -f docker-compose.hostdev.yml up -d
```

- `docker-compose.hostdev.yml` mueve `web` y `vision` al perfil `containerized`, así este comando no los arranca. El collector los revisa en `host.docker.internal:5000` y `:5002`.
- Los procesos del host leen el `.env`: `*_SERVICE_URL`, `DATABASE_URL`, `REDIS_URL` y `MONGO_URL` con `127.0.0.1` y los puertos publicados.
- Para volver al modo integral: detén los procesos del host y ejecuta `docker compose up -d`.

## 10. Limitaciones

- **Un solo proceso:** `web` y `vision` corren con un proceso gunicorn cada uno (estado en memoria); escalar horizontalmente requeriría mover ese estado a un almacén compartido.
- **Sin HTTPS:** no hay terminación TLS. En producción hace falta un proxy inverso delante de `web`, `auth` y `garage`, con `AUTH_COOKIE_SECURE=true`. No hay API Gateway ni WebSockets, por diseño.
- **Solo CPU:** VisionWorker es CPU (sección 6). Un video de prueba tarda segundos o decenas de segundos, según la máquina.
- **Imagen pesada:** la imagen de `vision` pesa unos 2.8 GB (torch CPU + ultralytics + OpenCV); el primer build tarda varios minutos.
- **Sin respaldos automáticos:** los respaldos de volúmenes (`pg_dump`, `mongodump`, copia de Garage) no están automatizados en esta etapa.
- **Sin Swagger/Postman:** la evidencia con Swagger y Postman corresponde a la etapa siguiente.
