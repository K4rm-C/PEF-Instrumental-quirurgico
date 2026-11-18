# PEF — Instrumental quirúrgico

Conteo y trazabilidad asistidos por visión de instrumental quirúrgico (PEF · UDEM).

Portal web (operador / SPD / administración), autenticación JWT, visión YOLO, evidencia en anillo, microservicios de catálogo/sesión/evidencia y app Android.

## Stack

| Capa | Componentes |
| --- | --- |
| Portal | `BackendWebFlask` (admin in-process vía `admin_local`) |
| Identidad | `BackendAuthService` — cookies portal + `/api/v1/auth` (Android) |
| Visión / evidencia OP | `VisionWorker` (host, CUDA) · `EvidenceWorker` (Docker) |
| Microservicios | Catalog, Session, EvidenceService, Audit, Notifications, Monitoring, Integration |
| Cliente móvil | `apps/PEF-Android` |
| Infra | PostgreSQL, Redis, MongoDB, Garage (S3) |

`apps/AdminService/` es **legado**; el panel admin del portal ya no lo requiere. Catálogo de producto: `CatalogService` (`:5003`).

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
- Python 3.11+ en `PATH` (**solo** para VisionWorker en el host)
- Git
- Pesos YOLO: `apps/VisionWorker/weights/best.pt` (no versionados; `*.pt` en `.gitignore`)
- `ffmpeg` en PATH (o `.\tools\ensure-ffmpeg.ps1` en Windows) — lo usa EvidenceWorker en Docker y Vision en el host

Puertos por defecto (ver `.env.example`):

| Rango | Uso |
| --- | --- |
| `5433`, `6379`, `27017`, `3900`–`3903` | Infra |
| `5000`–`5001` | Web, Auth (Docker) |
| `5002` | VisionWorker (host) |
| `5003`–`5009` | Microservicios (Docker) |
| `8080` | API Gateway (opcional) |

```powershell
docker --version
docker compose version
python --version
```

---

## Arranque (flujo único)

Todo desde la **raíz** del repositorio (`PEF-Instrumental-quirurgico`).

Stack en Docker (infra + Auth/MS/portal/`evidence-worker`) y **una terminal** con VisionWorker en el host (CUDA).

### 1. Configurar entorno

Solo la primera vez, o cuando cambie `.env.example`:

```powershell
copy .env.example .env
copy .env apps\VisionWorker\.env
```

VisionWorker lee el `.env` de su carpeta. El resto de servicios toman variables desde Compose / `.env` de la raíz.

### 2. Levantar Docker

```powershell
docker compose -f docker-compose.integral.yml --env-file .env up -d --build
docker compose -f docker-compose.integral.yml ps
```

En el primer arranque (volumen Postgres vacío) se aplican solos:

1. `data/migrations/001_init.sql` + migraciones `004`…`019`
2. `data/seeds/002_seed_catalogs.sql` y `003_seed_demo.sql`
3. `garage-init` — buckets `privacy-notices` y `pef-admin`
4. `seed-family-examples` — sube `data/seeds/example_images/` a Garage y crea `media_asset` + `family_example` (`s3://pef-admin/family-examples/…`)

Reinicios posteriores **no** re-ejecutan los SQL de init; el seed de fotos sí corre otra vez y es **idempotente** (mismo SHA-256 → skip). Semilla limpia:

```powershell
docker compose -f docker-compose.integral.yml --env-file .env down -v
docker compose -f docker-compose.integral.yml --env-file .env up -d --build
```

Gateway opcional (Android / entrada única):

```powershell
docker compose -f docker-compose.integral.yml --env-file .env --profile gateway up -d gateway
```

### 3. VisionWorker en el host (CUDA)

```powershell
cd apps\VisionWorker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Comprobar: [http://127.0.0.1:5002/health](http://127.0.0.1:5002/health) → `"weights_present": true`.

El portal y `evidence-worker` apuntan a Vision en `host.docker.internal:5002`.

### 4. Verificar

```powershell
.\scripts\smoke_module_health.ps1
.\scripts\smoke_auth.ps1
```

Login UI: [http://127.0.0.1:5000/sign-in](http://127.0.0.1:5000/sign-in).

---

## Opción B — VisionWorker en Docker (solo CPU)

Sin CUDA/GPU. No corras Vision en el host al mismo tiempo (conflicto en `:5002`).

```powershell
docker compose -f docker-compose.integral.yml -f docker-compose.vision-cpu.yml --profile vision-container --env-file .env up -d --build
```

Coloca `best.pt` en `apps/VisionWorker/weights/` (montaje de solo lectura).

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
| EvidenceWorker | contenedor → volumen `evidence_worker_clips` |
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
| 5003–5009 | Microservicios |
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
| Buckets | `evidence`, `privacy-notices`, `pef-admin` |

```powershell
$env:AWS_ACCESS_KEY_ID="MedAdmin3"
$env:AWS_SECRET_ACCESS_KEY="StorageKeySecret027"
$env:AWS_DEFAULT_REGION="garage"
aws --endpoint-url http://127.0.0.1:3900 s3 ls
```

### Usuarios demo (seed)

Roles RF: `station_operator`, `spd_supervisor`, `it_admin`, `or_charge` (App). Hashes Werkzeug **3.1.8**.

| Rol | Email | Password | Superficie |
| --- | --- | --- | --- |
| station_operator | `operator@instrumed.com` | `DemopwdOP78!` | Web OP |
| station_operator | `operador2@instrumed.com` | `DemopwdOP278!` | Web OP |
| spd_supervisor | `supervisor@instrumed.com` | `DemopwdSPD78!` | Web SPD |
| spd_supervisor | `supervisor2@instrumed.com` | `DemopwdSPD278!` | Web SPD |
| it_admin | `admin@instrumed.com` | `DemopwdADM78!` | Web Admin |
| **or_charge (App)** | `floor@instrumed.com` | `DemopwdFLR78!` | **App móvil (cuenta estándar)** |

`floor@` / `or_charge` es la cuenta demo canónica de la App (alcance institucional: sesiones, holds, evidencia, alertas). No tiene home web. SPD (`supervisor@`) es portal, no App. Extras Locust y claves en claro: `data/seeds/DEMO_CREDENTIALS.local.md` (gitignored vía `*.local`).

```powershell
curl -i -X POST http://127.0.0.1:5001/login -H "Content-Type: application/json" -d "{\"email\":\"operator@instrumed.com\",\"password\":\"DemopwdOP78!\"}"
```

---

## Estructura del repositorio

```
PEF-Instrumental-quirurgico/
├── docker-compose.integral.yml     # Stack principal (Docker)
├── docker-compose.vision-cpu.yml   # Opción B: Vision en contenedor (CPU)
├── .env.example
├── apps/
│   ├── BackendAuthService/
│   ├── BackendWebFlask/            # Portal + admin_local
│   ├── VisionWorker/weights/       # best.pt local (+ .venv solo aquí)
│   ├── EvidenceWorker/
│   ├── CatalogService/ … IntegrationService/
│   ├── AdminService/               # Legado
│   └── PEF-Android/
├── data/
│   ├── migrations/                 # 001 + 004…019
│   ├── seeds/                      # 002, 003 + example_images/
│   ├── DataStandards/
│   └── FunctionalRequirements/
├── infra/                          # Garage, gateway
└── scripts/
    ├── garage_init_buckets.py      # Servicio Compose garage-init
    ├── seed_family_examples.py     # Servicio Compose seed-family-examples
    ├── smoke_module_health.ps1
    └── smoke_auth.ps1
```

---

## Android

Proyecto en `apps/PEF-Android`. Emulador → Auth `http://10.0.2.2:5001` o IP LAN del host. Con Gateway, entrada pública `:8080`. Detalle: `apps/PEF-Android/README.md`.

---

## Evidencia (ciclo OP)

Flujo BD-first: encode → Garage S3 → `media_asset` (PG) → `checkpoint_frame` (Mongo). El portal OP-05/06 prioriza Mongo + URL firmada; lo local solo aplica si falló el upload (cola Redis `pef:evidence:upload_retry`). Con `EVIDENCE_PURGE_LOCAL_ON_UPLOAD=1` se puede borrar el MP4 tras upload OK.

Hourly: no dispara en idle; catch-up al reanudar; ratio vía VisionWorker `/state`.

---

## Internacionalización (i18n)

Flask-Babel en `BackendWebFlask`: fuente **en**, catálogo **es-MX** (`translations/es_MX/`). Preferencia: usuario autenticado → `ui_preferences.locale`; invitado → cookie `pef_locale`.

Cadenas nuevas (en un entorno con Babel instalado, p. ej. imagen/web o venv temporal):

```powershell
cd apps\BackendWebFlask
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
| Garage no lista buckets | `docker compose -f docker-compose.integral.yml logs garage garage-init` |
| Falta `privacy-notices` / `pef-admin` | Re-correr `garage-init` (`compose up`) o recrear volúmenes Garage |
| Postgres sin seeds | Solo en volumen nuevo: `down -v` + `up -d --build` |
| Fotos de familia vacías | `docker compose -f docker-compose.integral.yml logs seed-family-examples` |
| Login 401 | Auth con seed actual; reiniciar tras `down -v` |
| Vision `"weights_present": false` | Colocar `apps/VisionWorker/weights/best.pt` |
| Puerto `5002` ocupado | No mezclar Vision host + opción B; liberar el puerto |

Re-subida manual de fotos (si hace falta fuera de Compose): `.\scripts\seed_family_examples.ps1`.

---

## Guía rápida de prueba manual

Credenciales: sección anterior. Semilla sucia → `down -v` + `up -d --build` + Vision en host.

1. **Stack** — `compose ps`, `.\scripts\smoke_auth.ps1`.
2. **Login** — cada rol en `/sign-in`.
3. **Ciclo SPD → OP → SPD** — New session → OP Start / Continue → SPD resolve / Confirm close.
4. **Visión** — VisionWorker + pesos; captura → Finish → validación; EvidenceWorker genera clips.
5. **Admin familias** — fotos de referencia visibles; Replace/Delete quita el objeto del bucket.

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
| `VISION_HOLD_SECONDS` | `0.275` | Hold con caja / oclusión |
| `VISION_NAME_HOLD_SECONDS` | `0.35` | Sticky naming |
| `VISION_CONFUSION_HOLD_SECONDS` | `0.20` | Hold top2–N |
| `VISION_HOLD_MIN_SCORE` | `0.50` | No entrar en hold si conf ≤ 0.49 |
| `VISION_HOLD_DROP_SCORE` | `0.35` | Drop inmediato de hold si conf < 0.35 |
| `VISION_HOLD_SEED_FRAMES` | `3` | Semilla en tiempo de cinta |
| `VISION_HOLD_ABSENT_STREAK` | `1` | Gracia de ausencia |
| `VISION_HOLD_ABSENT_SECONDS` | `0.09` | TTL corto sin overlap |
| `VISION_HOLD_ZONE_IOU` | `0.22` | Overlap / oclusión |
| `VISION_TRACK_MEMORY` | `1` | Pistas IoU + EMA (`0` = rollback) |
| `VISION_TRACK_EMA_ALPHA` | `0.35` | Peso frame actual |
| `VISION_TRACK_IOU` | `0.30` | IoU mínimo de asociación |
| `VISION_TRACK_MISS_SECONDS` | `0.50` | Caducidad de pista |
| `VISION_TRACK_PRIOR` | `0.20` | Bonus familia previa |
| `VISION_TRACK_CONTENTION_MIN` | `0.25` | Apertura a siguiente cupo |

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
| 8 | Microservicios Session/Evidence/Audit/… + Android | Integrado |

Idioma fuente: **inglés**; UI `es-MX` vía Babel. Rutas pre-RF del operador: `/legacy/...` (`apps/BackendWebFlask/legacy/README.md`). Plantillas V2 retiradas: `Legacy/` en la raíz.
