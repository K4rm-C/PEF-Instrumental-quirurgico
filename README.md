# PEF — Instrumental quirúrgico

Conteo y trazabilidad asistidos por visión de instrumental quirúrgico (PEF · UDEM).

Portal web (operador / SPD / administración), autenticación JWT, visión YOLO, evidencia en anillo, microservicios de catálogo/sesión/evidencia y app Android.

## Stack

| Capa | Componentes |
| --- | --- |
| Portal | `BackendWebFlask` (admin in-process vía `admin_local`) |
| Identidad | `BackendAuthService` — cookies portal + `/api/v1/auth` (Android) |
| Visión / evidencia OP | `VisionWorker`, `EvidenceWorker` |
| Microservicios | Catalog, Session, EvidenceService, Audit, Notifications, Monitoring, Integration |
| Cliente móvil | `apps/PEF-Android` |
| Infra | PostgreSQL, Redis, MongoDB, Garage (S3) |

`apps/AdminService/` es **legado**; el panel admin del portal ya no lo requiere. Catálogo de producto: `CatalogService` (`:5003`).

## Modos de despliegue

Hay dos composes (aún no unificados). **No mezclar** Auth/Web del host con el mismo servicio en Docker en el mismo puerto.

| Modo | Compose | Qué levanta |
| --- | --- | --- |
| **Híbrido (recomendado en Windows)** | `docker-compose.yml` | Solo infra. Auth, Web, Vision, EvidenceWorker y MS corren en el host. |
| **Integral** | `docker-compose.integral.yml` | Infra + Auth/MS/web/visión en Docker. Gateway opcional (`--profile gateway`). |

## Equipo

| Integrante | Matrícula |
| --- | --- |
| Benjamin Charles Legorreta | 599860 |
| Pedro Elidio Sora Gonzalez | 596630 |
| Angel Uriel Muñoz Moreno | 604386 |

Apoyo: Luis Carlos Rodriguez Medrano, Carlos Ignacio Huerta Carrizales, Juan Hermilo Reyes Perez.

Asesor: Dr. Raul Morales Salcedo.

---

## Prerrequisitos

- Docker Desktop en ejecución
- Python 3.11+ en `PATH`
- Git
- Pesos YOLO locales: `apps/VisionWorker/weights/best.pt` (no versionados; `*.pt` en `.gitignore`)
- `ffmpeg` en PATH (o `.\tools\ensure-ffmpeg.ps1` en Windows)

Puertos por defecto (ver `.env.example`):

| Rango | Uso |
| --- | --- |
| `5433`, `6379`, `27017`, `3900`–`3903` | Infra |
| `5000`–`5002` | Web, Auth, VisionWorker |
| `5003`–`5009` | Microservicios |
| `8080` | API Gateway (opcional) |

```powershell
docker --version
docker compose version
python --version
```

---

## Arranque

Todo desde la **raíz** del repositorio (`PEF-Instrumental-quirurgico`).

### 1. Configurar entorno

Solo la primera vez, o cuando cambie `.env.example`:

```powershell
copy .env.example .env
copy .env apps\BackendAuthService\.env
copy .env apps\BackendWebFlask\.env
copy .env apps\VisionWorker\.env
copy .env apps\EvidenceWorker\.env
```

Auth, Web, VisionWorker y EvidenceWorker leen el `.env` **de su carpeta**. Volver a copiar sobrescribe cambios locales.

Para MS en host, `.\scripts\run_ms.ps1` usa el `.env` del servicio si existe; si no, el de la raíz.

### 2. Elegir camino

#### Camino A — Híbrido (infra Docker + procesos en host)

**2.A.1 Infra**

```powershell
docker compose up -d
docker compose ps
```

Servicios esperados: `pef_postgres`, `pef_redis`, `pef_mongo`, `pef_garage`.

**Init automático (solo volumen Postgres vacío):**

1. `data/migrations/001_init.sql`
2. `data/seeds/002_seed_catalogs.sql`
3. `data/seeds/003_seed_demo.sql`

Reinicios posteriores **no** vuelven a ejecutar esos archivos. Para semilla limpia:

```powershell
docker compose down -v
docker compose up -d
```

**Bucket Garage** (tras el primer `up`):

```powershell
docker exec pef_garage /garage status
docker exec pef_garage /garage bucket create privacy-notices
docker exec pef_garage /garage bucket allow --read --write --owner privacy-notices --key MedAdmin3
docker exec pef_garage /garage bucket list
```

El bucket `evidence` y la key `MedAdmin3` se crean con `--default-bucket` si `S3_SECRET_KEY` es válida (≥ 16 caracteres).

**2.A.2 Migraciones MS (004–019)**

En el compose OP-local **no** se montan solas. Tras el primer `up` (o tras `down -v`):

```powershell
.\scripts\apply_ms_migrations.ps1
```

Son SQL aditivos e idempotentes. En el camino integral las aplica el servicio `db-migrate` del compose.

**2.A.3 Portal OP (cuatro terminales / venvs propios)**

```powershell
.\scripts\check_venvs.ps1   # opcional, tras crear venvs
```

**Auth** (`:5001` — portal `/login` + Android `/api/v1/auth/*`):

```powershell
cd apps\BackendAuthService
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

**Web** (`:5000`):

```powershell
cd apps\BackendWebFlask
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

**VisionWorker** (`:5002` — requiere `weights\best.pt`):

```powershell
cd apps\VisionWorker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Comprobar: [http://127.0.0.1:5002/health](http://127.0.0.1:5002/health) → `"weights_present": true`.

**EvidenceWorker** (clips; requiere Redis):

```powershell
cd apps\EvidenceWorker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Sin este proceso no se generan clips. Encode: ffmpeg H.264 → WebM → OpenCV → JPEG.

Atajo Windows (infra + ventanas Auth/Web/Vision/EvidenceWorker): `.\start-services.ps1`.

**2.A.4 Microservicios en host (venv compartido)**

Un solo entorno para atomicidad de dependencias:

```powershell
python -m venv apps\.venv-ms
apps\.venv-ms\Scripts\pip install -r apps\requirements-ms.txt
```

Arranque (una terminal por servicio):

```powershell
.\scripts\run_ms.ps1 CatalogService
.\scripts\run_ms.ps1 SessionService
.\scripts\run_ms.ps1 EvidenceService
.\scripts\run_ms.ps1 AuditService
.\scripts\run_ms.ps1 NotificationsService
.\scripts\run_ms.ps1 MonitoringService
.\scripts\run_ms.ps1 IntegrationService
```

En Docker integral cada imagen sigue usando su `requirements.txt` propio.

#### Camino B — Integral (todo en Docker)

```powershell
docker compose -f docker-compose.integral.yml --env-file .env up -d --build
docker compose -f docker-compose.integral.yml ps
```

Gateway (entrada única para Android / clientes externos):

```powershell
docker compose -f docker-compose.integral.yml --profile gateway up -d gateway
```

Coloca `best.pt` en `apps/VisionWorker/weights/` (montaje de solo lectura). No uses el camino A en paralelo sobre los mismos puertos.

### 3. Verificar

```powershell
.\scripts\smoke_module_health.ps1
.\scripts\smoke_auth.ps1
```

Login UI: [http://127.0.0.1:5000/sign-in](http://127.0.0.1:5000/sign-in).

---

## URLs y puertos

| Servicio | URL / endpoint |
| --- | --- |
| Portal | [http://127.0.0.1:5000](http://127.0.0.1:5000) |
| Auth (+ `/api/v1/auth`) | [http://127.0.0.1:5001](http://127.0.0.1:5001) |
| VisionWorker | [http://127.0.0.1:5002/health](http://127.0.0.1:5002/health) |
| CatalogService | [http://127.0.0.1:5003/health/ready](http://127.0.0.1:5003/health/ready) |
| SessionService | [http://127.0.0.1:5004/health](http://127.0.0.1:5004/health) |
| EvidenceService | [http://127.0.0.1:5005/health/ready](http://127.0.0.1:5005/health/ready) |
| AuditService | [http://127.0.0.1:5006/health/ready](http://127.0.0.1:5006/health/ready) |
| NotificationsService | [http://127.0.0.1:5007/health/ready](http://127.0.0.1:5007/health/ready) |
| MonitoringService | [http://127.0.0.1:5008/health/ready](http://127.0.0.1:5008/health/ready) |
| IntegrationService | [http://127.0.0.1:5009/health/ready](http://127.0.0.1:5009/health/ready) |
| API Gateway | [http://127.0.0.1:8080/health](http://127.0.0.1:8080/health) |
| EvidenceWorker | proceso local → `apps/EvidenceWorker/clips/` |
| Garage S3 | [http://127.0.0.1:3900](http://127.0.0.1:3900) |
| PostgreSQL | `localhost:5433` · db `InstruMed` |
| Redis | `localhost:6379` |
| MongoDB | `localhost:27017` |

| Puerto | Uso |
| --- | --- |
| 5433 | PostgreSQL |
| 6379 | Redis |
| 27017 | MongoDB |
| 3900 | Garage S3 |
| 3901 | Garage RPC |
| 3903 | Garage admin |
| 5000 | Web |
| 5001 | Auth |
| 5002 | VisionWorker |
| 5003 | CatalogService |
| 5004 | SessionService |
| 5005 | EvidenceService |
| 5006 | AuditService |
| 5007 | NotificationsService |
| 5008 | MonitoringService |
| 5009 | IntegrationService |
| 8080 | Gateway |

---

## Credenciales

### Object storage (Garage / S3)

| Campo | Valor |
| --- | --- |
| Endpoint | `http://localhost:3900` |
| Region | `garage` |
| Access key | `MedAdmin3` |
| Secret key | `StorageKeySecret027` |
| Path style | sí (`S3_FORCE_PATH_STYLE=true`) |
| Buckets | `evidence`, `privacy-notices` |

```powershell
$env:AWS_ACCESS_KEY_ID="MedAdmin3"
$env:AWS_SECRET_ACCESS_KEY="StorageKeySecret027"
$env:AWS_DEFAULT_REGION="garage"
aws --endpoint-url http://127.0.0.1:3900 s3 ls
```

### Usuarios demo (seed)

Roles RF: `station_operator`, `spd_supervisor`, `it_admin`. Hashes Werkzeug **3.1.8**.

| Rol | Email | Password |
| --- | --- | --- |
| station_operator | `operator@instrumed.com` | `DemopwdOP78!` |
| station_operator | `operador2@instrumed.com` | `DemopwdOP278!` |
| spd_supervisor | `supervisor@instrumed.com` | `DemopwdSPD78!` |
| spd_supervisor | `supervisor2@instrumed.com` | `DemopwdSPD278!` |
| it_admin | `admin@instrumed.com` | `DemopwdADM78!` |

```powershell
curl -i -X POST http://127.0.0.1:5001/login -H "Content-Type: application/json" -d "{\"email\":\"operator@instrumed.com\",\"password\":\"DemopwdOP78!\"}"
```

---

## Estructura del repositorio

```
PEF-Instrumental-quirurgico/
├── docker-compose.yml              # Infra OP-local
├── docker-compose.integral.yml     # Stack MS + portal en Docker
├── .env.example
├── start-services.ps1 / .sh
├── apps/
│   ├── requirements-ms.txt         # Dependencias del venv compartido MS
│   ├── .venv-ms/                   # Local (gitignored)
│   ├── BackendAuthService/
│   ├── BackendWebFlask/            # Portal + admin_local
│   ├── VisionWorker/weights/       # best.pt local
│   ├── EvidenceWorker/
│   ├── CatalogService/ … IntegrationService/
│   ├── AdminService/               # Legado
│   └── PEF-Android/
├── data/
│   ├── migrations/                 # 001 + 004…019
│   ├── seeds/                      # 002, 003
│   ├── DataStandards/
│   └── FunctionalRequirements/
├── infra/                          # Garage, gateway
└── scripts/
    ├── apply_ms_migrations.ps1
    ├── run_ms.ps1
    ├── check_venvs.ps1
    ├── smoke_module_health.ps1
    └── smoke_auth.ps1
```

---

## Android

Proyecto en `apps/PEF-Android`. Contra el stack híbrido apunta Auth a `http://10.0.2.2:5001` (emulador) o la IP LAN del host. Con Gateway integral, la entrada pública es `:8080`. Detalle de build y contratos: `apps/PEF-Android/README.md`.

---

## Evidencia (ciclo OP)

Flujo BD-first: encode → Garage S3 → `media_asset` (PG) → `checkpoint_frame` (Mongo). El portal OP-05/06 prioriza Mongo + URL firmada; lo local solo aplica si falló el upload (cola Redis `pef:evidence:upload_retry`). Con `EVIDENCE_PURGE_LOCAL_ON_UPLOAD=1` se puede borrar el MP4 tras upload OK.

Hourly: no dispara en idle; catch-up al reanudar; ratio vía VisionWorker `/state`.

Smoke sin Redis (desde `EvidenceWorker`): `python smoke_ring.py`.

---

## Internacionalización (i18n)

Flask-Babel en `BackendWebFlask`: fuente **en**, catálogo **es-MX** (`translations/es_MX/`). Preferencia: usuario autenticado → `ui_preferences.locale`; invitado → cookie `pef_locale`.

Flujo de cadenas nuevas (venv de Web activo, en `apps/BackendWebFlask`):

```powershell
pybabel extract -F babel.cfg -k N_ --no-wrap --sort-by-file -o messages.pot .
pybabel update -i messages.pot -d translations --no-wrap
# editar translations/es_MX/LC_MESSAGES/messages.po
pybabel compile -d translations --statistics
```

No se traducen datos de negocio capturados; sí etiquetas de sistema vía `i18n.py` (`localize_db_*`).

---

## Problemas frecuentes

| Síntoma | Acción |
| --- | --- |
| Garage no lista buckets | Esperar unos segundos; `docker compose logs garage`; `docker exec pef_garage /garage status` |
| Garage en bucle / secret corto | Alargar `S3_SECRET_KEY` en `.env`; `docker compose down`; borrar volúmenes `garage_meta` / `garage_data`; `up -d garage` |
| Falta `privacy-notices` | Comandos `bucket create` / `bucket allow` de la sección 2.A.1 |
| Postgres sin seeds | Solo en volumen nuevo: `docker compose down -v` + `up -d` |
| MS fallan por esquema | `.\scripts\apply_ms_migrations.ps1` |
| Login 401 | Auth con `Werkzeug==3.1.8` y seed actual; reiniciar Auth tras recrear volúmenes |
| Vision `"weights_present": false` | Colocar `apps/VisionWorker/weights/best.pt` |
| Puerto ocupado | No mezclar camino A y B; liberar `5000`–`5009` / `8080` |

---

## Guía rápida de prueba manual

Credenciales: sección anterior. Semilla sucia → `down -v` + `up -d` + migraciones + `privacy-notices` + reinicio Auth/Web.

1. **Infra / seed** — `docker compose ps`, `.\scripts\smoke_auth.ps1`, roles y kit demo en Postgres.
2. **Login** — cada rol en `/sign-in`. OP: Assigned Sessions / History / Profile. SPD: Dashboard, sesiones, kits, indicadores. Admin: panel in-process (`admin_local`), sin `AdminService`.
3. **Ciclo manual SPD → OP → SPD** — New session → OP Start / Continue → SPD resolve / Confirm close.
4. **Visión** — VisionWorker + pesos; sesión Via A → Capture (cámara WS o upload); Finish → validación humana; EvidenceWorker genera clips.
5. **Smokes opcionales** (ensucian seed):

```powershell
cd apps\BackendWebFlask
.\.venv\Scripts\Activate.ps1
python ..\..\scripts\smoke_rf_32.py
python ..\..\scripts\smoke_rf_33.py
```

---

## Ajuste fino VisionWorker

Pipeline demo `matrix_v1` (Hungarian por defecto; `VISION_ASSIGN_SOLVER=greedy` para A/B). Seed: modelo activo `yolo26l-demo` + 17 clases.

| Variable | Default | Rol |
| --- | --- | --- |
| `VISION_BOX_RECALL_CONF` | `0.12` | Piso de existencia de caja |
| `VISION_CONF_DISPLAY_THRESHOLD` | `0.70` | Overlay naming (kept_raw) |
| `VISION_CONF_DISPLAY_REASSIGN` | `0.60` | Naming si reassigned (+ raw top-1 ≥ 0.70) |
| `VISION_REASSIGN_MIN_SCORE` | `0.45` | Piso para reasignar familia |
| `VISION_REASSIGN_MAX_GAP` | `0.35` | Cercanía al top-1 raw |
| `VISION_ASSIGN_SOLVER` | `hungarian` | `hungarian` \| `greedy` |
| `VISION_FRAME_STRIDE_CUDA` | `1` | Inferir cada N frames (CUDA) |
| `VISION_FRAME_STRIDE_CPU` | `5` | Inferir cada N frames (CPU) |
| `VISION_FRAME_STRIDE` | (unset) | Fija CUDA y CPU al mismo N |
| `VISION_COUNT_SAMPLE_INTERVAL_S` | `1.0` | Tope de `count_event` video_window |
| `VISION_WORKER_WS_URL` | `ws://127.0.0.1:5002/ws` | WS que usa el navegador |
| `VISION_WS_SECRET` | demo | HMAC Flask ↔ worker |
| `VISION_WS_ORIGINS` | localhost:5000 | Orígenes handshake WS |
| `VISION_HOLD_SECONDS` | `1.25` | Hold con caja / oclusión |
| `VISION_NAME_HOLD_SECONDS` | `6.0` | Sticky naming |
| `VISION_CONFUSION_HOLD_SECONDS` | `2.5` | Hold top2–N |
| `VISION_HOLD_MIN_SCORE` | `0.35` | No sembrar hold débil |
| `VISION_HOLD_SEED_FRAMES` | `2` | Semilla en tiempo de cinta |
| `VISION_HOLD_ABSENT_STREAK` | `2` | Gracia de ausencia |
| `VISION_HOLD_ABSENT_SECONDS` | `0.55` | TTL corto sin overlap |
| `VISION_HOLD_ZONE_IOU` | `0.15` | Overlap / oclusión |
| `VISION_TRACK_MEMORY` | `1` | Pistas IoU + EMA (`0` = rollback) |
| `VISION_TRACK_EMA_ALPHA` | `0.35` | Peso frame actual |
| `VISION_TRACK_IOU` | `0.30` | IoU mínimo de asociación |
| `VISION_TRACK_MISS_SECONDS` | `0.50` | Caducidad de pista |
| `VISION_TRACK_PRIOR` | `0.20` | Bonus familia previa |
| `VISION_TRACK_CONTENTION_MIN` | `0.25` | Apertura a siguiente cupo |

Checklist evidencia (5A): compose + Auth/Web/Vision/EvidenceWorker; SPD Via A → OP Capture → fase/clips en drawer; opcional Garage caído (fallback local) e idle > 1 h (hourly catch-up).

---

## Estado del desarrollo

| Fase | Contenido | Estado |
| --- | --- | --- |
| 0–1 | Empaque Docker (PG/Redis/Mongo/Garage) + seeds | Listo |
| 2 | Auth JWT/Redis (DS01), cookies, locale | Listo |
| 3.x | Contrato RF OP/SPD, privacy, cierre supervisor | Listo |
| 4 | UI RF OP/SPD | Listo |
| 5a | VisionWorker + YOLO + matrix_v1 + evidencia BD-first | Listo (demo) |
| 6 | i18n `en` + `es-MX` | Listo |
| 7 | Admin portal (`admin_local`) + CatalogService | Listo |
| 8 | Microservicios Session/Evidence/Audit/… + Android | Integrado (dual compose) |

Idioma fuente: **inglés**; UI `es-MX` vía Babel. Rutas pre-RF del operador: `/legacy/...` (`apps/BackendWebFlask/legacy/README.md`). Plantillas V2 retiradas: `Legacy/` en la raíz.
