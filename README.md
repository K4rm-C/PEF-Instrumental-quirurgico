# PEF Surgical Instrument Traceability

Vision-assisted counting and digital traceability of surgical instruments (PEF · UDEM).

Web portal (operator / SPD / administration), JWT authentication, YOLO vision, ring evidence, catalog/session/evidence microservices, and an Android app.

## Stack

| Layer | Components |
| --- | --- |
| Portal | `BackendWebFlask` (admin in-process via `admin_local`) |
| Identity | `BackendAuthService` - portal cookies + `/api/v1/auth` (Android) |
| Vision / OP evidence | `VisionWorker` (host, CUDA) · `EvidenceWorker` (Docker) |
| Microservices | Catalog, Session, EvidenceService, Audit, Notifications, Monitoring, Integration |
| Mobile client | `apps/PEF-Android` |
| Infra | PostgreSQL, Redis, MongoDB, Garage (S3) |

Admin UI runs in-process via `admin_local` on the portal. Product catalog: `CatalogService` (`:5003`).

## Team

| Member | Student ID |
| --- | --- |
| Benjamin Charles Legorreta | 599860 |
| Pedro Elidio Sora Gonzalez | 596630 |
| Angel Uriel Muñoz Moreno | 604386 |

Support: Luis Carlos Rodriguez Medrano, Carlos Ignacio Huerta Carrizales, Juan Hermilo Reyes Perez.

Advisor: Dr. Raul Morales Salcedo.

---

## Prerequisites

- Docker Desktop running
- Python 3.11+ on `PATH` (only for VisionWorker on the host)
- Git
- YOLO weights: `apps/VisionWorker/weights/best.pt` (not versioned; `*.pt` in `.gitignore`)
- `ffmpeg` on PATH (or `.\tools\ensure-ffmpeg.ps1` on Windows) - used by EvidenceWorker in Docker and Vision on the host

### Canonical Compose

For the MS + portal + seeds stack use `docker-compose.integral.yml` (overlays: `docker-compose.vision-cpu.yml`, `gateway` profile).  
`docker-compose.yml` remains as legacy infra reference (older e2e/VM scripts); it is not the daily demo startup.

Default ports (see `.env.example`):

| Range | Use |
| --- | --- |
| `5433`, `6379`, `27017`, `3900`–`3903` | Infra |
| `5000`–`5001` | Web, Auth (Docker) |
| `5002` | VisionWorker (host) |
| `5003`–`5009` | Microservices (Docker) |
| `8080` | API Gateway (optional) |

```powershell
docker --version
docker compose version
python --version
```

---

## Startup (single flow)

Everything from the repository root (`PEF-Instrumental-quirurgico`).

Stack in Docker (infra + Auth/MS/portal/`evidence-worker`) and one terminal with VisionWorker on the host (CUDA).

### 1. Configure environment

First time only, or when `.env.example` changes:

```powershell
copy .env.example .env
copy .env apps\VisionWorker\.env
```

VisionWorker reads the `.env` in its own folder. Other services take variables from Compose / the root `.env`.

### 2. Start Docker

```powershell
docker compose -f docker-compose.integral.yml --env-file .env up -d --build
docker compose -f docker-compose.integral.yml ps
```

On first boot (empty Postgres volume) these run automatically:

1. `data/migrations/001_init.sql` + migrations `004`…`021`
2. `data/seeds/002_seed_catalogs.sql` and `003_seed_demo.sql` (canonical + merged Locust accounts/ORs)
3. `garage-init` - buckets `privacy-notices` and `pef-admin`
4. `seed-family-examples` - uploads `data/seeds/example_images/` to Garage and creates `media_asset` + `family_example`
5. `seed-demo-volume` - re-applies `004_seed_demo_locust.sql` (idempotent) and generates history/KPIs/audit/monitoring with variable counts (`SEED_*` in `.env`)

Later restarts do not re-run the init SQL; photo and volume seeds do run again and are idempotent. Clean seed:

```powershell
docker compose -f docker-compose.integral.yml --env-file .env down -v
docker compose -f docker-compose.integral.yml --env-file .env up -d --build
```

Optional gateway (Android / single entry):

```powershell
docker compose -f docker-compose.integral.yml --env-file .env --profile gateway up -d gateway
```

### Flexible LAN IP (portal / App / CORS / Vision WS)

Do not hardcode IPs and do not rely on localhost for demos. On any Wi-Fi:

```powershell
.\scripts\pef_up.ps1
```

That detects the LAN IP, rewrites `.env` + Android `local.properties`, mints a lab TLS cert for that IP, and starts Gateway + HTTPS portal (`docker-compose.lan-https.yml` → port `:5443`).

Open the portal at `https://<HOST_LAN_IP>:5443/` (accept the lab certificate once). Browsers only allow the live camera in a secure context; plain `http://<LAN>:5000` can still upload videos, but Start camera needs HTTPS.

What gets updated: `HOST_LAN_IP`, `AUTH_ALLOWED_ORIGINS`, `AUTH_BROWSER_URL=/auth` (same-origin via portal-lan), `VISION_WORKER_WS_URL=wss://<LAN>:5443/ws`, `PORTAL_COOKIE_SECURE`, `CSRF_TRUSTED_ORIGINS`, `S3_PUBLIC_ENDPOINT`, and `pef.apiBaseUrl.debug`. Services bind on `HOST_BIND=0.0.0.0`. Restart host VisionWorker after the script if you use live capture. The LAN nginx template always allows `OPTIONS` on `/auth/*` so CORS preflight cannot 403 after redeploy.

Android (debug): the app discovers the Gateway at runtime on the current Wi-Fi. Tap Retry after changing networks - no rebuild required for IP changes.

#### Windows firewall (Gateway + VisionWorker + portal HTTPS)

Allow inbound TCP so phones/other PCs can reach the stack. Run PowerShell as Administrator:

```powershell
New-NetFirewallRule -DisplayName "PEF Portal HTTPS LAN 5443" -Direction Inbound -Protocol TCP -LocalPort 5443 -Action Allow
New-NetFirewallRule -DisplayName "PEF Gateway LAN 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow
New-NetFirewallRule -DisplayName "PEF VisionWorker LAN 5002" -Direction Inbound -Protocol TCP -LocalPort 5002 -Action Allow
```

Remove later if needed:

```powershell
Remove-NetFirewallRule -DisplayName "PEF Portal HTTPS LAN 5443"
Remove-NetFirewallRule -DisplayName "PEF Gateway LAN 8080"
Remove-NetFirewallRule -DisplayName "PEF VisionWorker LAN 5002"
```

### 3. VisionWorker on the host (CUDA)

```powershell
cd apps\VisionWorker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Check: [http://127.0.0.1:5002/health](http://127.0.0.1:5002/health) → `"weights_present": true`.  
From another LAN client (after firewall rule): `http://<HOST_LAN_IP>:5002/health`.

The portal and `evidence-worker` reach Vision at `host.docker.internal:5002`. Browser live WS uses `VISION_WORKER_WS_URL` (LAN IP after `pef_lan_endpoint.ps1`).

### 4. Verify

```powershell
.\scripts\smoke_module_health.ps1
.\scripts\smoke_auth.ps1
```

UI login: [http://127.0.0.1:5000/sign-in](http://127.0.0.1:5000/sign-in).

---

## Option B - VisionWorker in Docker (CPU only)

No CUDA/GPU. Do not also run Vision on the host (port `:5002` conflict).

```powershell
docker compose -f docker-compose.integral.yml -f docker-compose.vision-cpu.yml --profile vision-container --env-file .env up -d --build
```

Place `best.pt` in `apps/VisionWorker/weights/` (read-only mount). Published as `${HOST_BIND:-0.0.0.0}:${VISION_PORT:-5002}:5002`.

---

## URLs and ports

| Service | URL / endpoint |
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
| EvidenceWorker | container → volume `evidence_worker_clips` |
| Garage S3 | [http://127.0.0.1:3900](http://127.0.0.1:3900) |
| PostgreSQL | `localhost:5433` · db `InstruMed` |
| Redis | `localhost:6379` |
| MongoDB | `localhost:27017` |

| Port | Use |
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
| 5003–5009 | Microservices |
| 8080 | Gateway |

---

## Credentials

### Object storage (Garage / S3)

| Field | Value |
| --- | --- |
| Endpoint | `http://localhost:3900` |
| Region | `garage` |
| Access key | `MedAdmin3` |
| Secret key | `StorageKeySecret027` |
| Path style | yes (`S3_FORCE_PATH_STYLE=true`) |
| Buckets | `evidence`, `privacy-notices`, `pef-admin` |

```powershell
$env:AWS_ACCESS_KEY_ID="MedAdmin3"
$env:AWS_SECRET_ACCESS_KEY="StorageKeySecret027"
$env:AWS_DEFAULT_REGION="garage"
aws --endpoint-url http://127.0.0.1:3900 s3 ls
```

### Demo users (seed)

RF roles: `station_operator`, `spd_supervisor`, `it_admin`, `or_charge` (App). Werkzeug hashes `3.1.8`.  
Full table (canonical + Locust + `gen.*`): [`data/seeds/DEMO_CREDENTIALS.md`](data/seeds/DEMO_CREDENTIALS.md).

| Role | Email | Password | Surface |
| --- | --- | --- | --- |
| station_operator | `operator@instrumed.com` | `DemopwdOP78!` | Web OP |
| station_operator | `operador2@instrumed.com` | `DemopwdOP278!` | Web OP |
| spd_supervisor | `supervisor@instrumed.com` | `DemopwdSPD78!` | Web SPD |
| spd_supervisor | `supervisor2@instrumed.com` | `DemopwdSPD278!` | Web SPD |
| it_admin | `admin@instrumed.com` | `DemopwdADM78!` | Web Admin |
| or_charge (App) | `floor@instrumed.com` | `DemopwdFLR78!` | Mobile app (standard account) |
| Locust OP | `ana.ruiz@` / `luis.pena@instrumed.com` | see DEMO_CREDENTIALS | multi-user load |
| Locust SPD/Admin/Floor | `carla.soto@` / `ti.extra@` / `nora.diaz@` / `dual.admin.spd@` | see DEMO_CREDENTIALS | load / mixed roles |

`floor@` / `or_charge` is the canonical App demo account. The one-shot `seed-demo-volume` adds extra ORs, closed/scheduled sessions, discrepancies, `access_audit`, notifications and `monitoring_check` (tunable with `SEED_OR_COUNT`, `SEED_CLOSED_SESSIONS`, `SEED_RNG`, etc.).

```powershell
curl -i -X POST http://127.0.0.1:5001/login -H "Content-Type: application/json" -d "{\"email\":\"operator@instrumed.com\",\"password\":\"DemopwdOP78!\"}"
```

---

## Documentation (canonical)

| Kind | Path | What it covers |
| --- | --- | --- |
| Data standards (DS01–DS10) | [`data/DataStandards/`](data/DataStandards/README.md) | Identity, clinical context, kits/YOLO, sessions, evidence, Mongo, privacy, FHIR clients, REST, vision WSS |
| Functional requirements (RF) | [`data/FunctionalRequirements/`](data/FunctionalRequirements/) | Operator / SPD screens ([`rf_operador_supervisor_spd.md`](data/FunctionalRequirements/rf_operador_supervisor_spd.md)), Admin / App ([`rf_admin_app.md`](data/FunctionalRequirements/rf_admin_app.md)) |
| Schema briefing | [`data/briefing_esquema_bd.md`](data/briefing_esquema_bd.md) | Short narrative of what the DB covers |
| Medical / interoperability notes | [`data/estandares_medicos.md`](data/estandares_medicos.md) | Clinical standards alignment notes |
| DDL snapshot (historical) | [`data/esquema_base_datos_v2.md`](data/esquema_base_datos_v2.md) | Base DDL reference; live schema is `data/migrations/` |
| Demo credentials | [`data/seeds/DEMO_CREDENTIALS.md`](data/seeds/DEMO_CREDENTIALS.md) | Seed users / Locust accounts |
| Locust load suite | [`Locust/README.md`](Locust/README.md) | Per-type HTTP load tests and English reports |
| Docs stub | [`docs/README.md`](docs/README.md) | Points here and to `data/` (no separate pack) |

Contracts and RFs are written in English. UI copy uses English msgids (`en` source) with Babel `es-MX`. Seed free-text that the UI shows (kit names, role blurbs, station labels) is stored in English and mapped in `apps/BackendWebFlask/i18n.py` for locale display.

## Repository layout

```
PEF-Instrumental-quirurgico/
├── docker-compose.integral.yml     # Main stack (Docker)
├── docker-compose.vision-cpu.yml   # Option B: Vision in container (CPU)
├── .env.example
├── Locust/                         # Load scenarios + reports/
├── apps/
│   ├── BackendAuthService/
│   ├── BackendWebFlask/            # Portal + admin_local
│   ├── VisionWorker/weights/       # local best.pt (+ .venv only here)
│   ├── EvidenceWorker/
│   ├── CatalogService/ … IntegrationService/
│   └── PEF-Android/
├── data/
│   ├── migrations/                 # 001 + 004…023
│   ├── seeds/                      # 002, 003(+Locust), 004, DEMO_CREDENTIALS.md, example_images/
│   ├── DataStandards/              # PEF-DS01…DS10
│   └── FunctionalRequirements/     # Operator/SPD + Admin/App RFs
├── docs/                           # stub only; contracts live under data/
├── infra/                          # Garage, gateway
└── scripts/
    ├── garage_init_buckets.py      # Compose service garage-init
    ├── seed_family_examples.py     # Compose service seed-family-examples
    ├── seed_demo_volume.py         # Locust ensure + KPI/history volume (Compose seed-demo-volume)
    ├── pef_up.ps1                  # Detect LAN IP + compose up (gateway) - preferred bring-up
    ├── pef_lan_endpoint.ps1        # LAN IP + CORS + Vision WS + Android URL
    ├── smoke_module_health.ps1
    └── smoke_auth.ps1
```

---

## Android

Project under `apps/PEF-Android`. Emulator → Auth `http://10.0.2.2:5001` or host LAN IP. With Gateway, public entry `:8080`. Details: `apps/PEF-Android/README.md`.

---

## Evidence (OP cycle)

BD-first flow: encode → Garage S3 → `media_asset` (PG) → `checkpoint_frame` (Mongo). OP-05/06 portal prefers Mongo + signed URL; local files only if upload failed (Redis queue `pef:evidence:upload_retry`). With `EVIDENCE_PURGE_LOCAL_ON_UPLOAD=1` the MP4 can be deleted after a successful upload.

Hourly: does not fire while idle; catch-up on resume; ratio via VisionWorker `/state`.

---

## Internationalization (i18n)

Flask-Babel in `BackendWebFlask`: source language `en`, catalog `es-MX` (`translations/es_MX/`). Preference: authenticated user → `ui_preferences.locale`; guest → cookie `pef_locale`.

UI strings use English msgids in code/templates (`_()` / `N_()`).  
System DB labels (catalog `code`s, known seed names) are stored in English and mapped in `i18n.py` (`localize_db_label` / `localize_db_text`) to English msgids, then translated by Babel for `es-MX`. Free-text entered by users is not translated.

After adding portal screens (e.g. Hospital operations, capture traffic strip), re-run extract/update/compile and fill empty or fuzzy `msgstr` entries in `es_MX` - fuzzy auto-matches can attach the wrong Spanish string to a new msgid.

New strings (environment with Babel installed, e.g. web image or a temporary venv):

```powershell
cd apps\BackendWebFlask
pybabel extract -F babel.cfg -k N_ --no-wrap --sort-by-file -o messages.pot .
pybabel update -i messages.pot -d translations --no-wrap
# edit translations/es_MX/LC_MESSAGES/messages.po
pybabel compile -d translations --statistics
```

---

## Common issues

| Symptom | Action |
| --- | --- |
| Garage does not list buckets | `docker compose -f docker-compose.integral.yml logs garage garage-init` |
| Missing `privacy-notices` / `pef-admin` | Re-run `garage-init` (`compose up`) or recreate Garage volumes |
| Postgres without seeds | Only on a new volume: `down -v` + `up -d --build` |
| Empty family photos | `docker compose -f docker-compose.integral.yml logs seed-family-examples` |
| Login 401 | Auth with current seed; restart after `down -v` |
| Vision `"weights_present": false` | Place `apps/VisionWorker/weights/best.pt` |
| Port `5002` in use | Do not mix host Vision + option B; free the port |
| LAN capture WS fails | Re-run `pef_lan_endpoint.ps1`, recreate `web`, restart VisionWorker, add firewall rule for TCP `5002` |

Manual photo re-upload (if needed outside Compose): `.\scripts\seed_family_examples.ps1`.

---

## Quick manual test guide

Credentials: previous section. Dirty seed → `down -v` + `up -d --build` + Vision on host.

1. Stack - `compose ps`, `.\scripts\smoke_auth.ps1`.
2. Login - each role at `/sign-in`.
3. SPD → OP → SPD cycle - New session → OP Start / Continue → SPD resolve / Confirm close.
4. Vision - VisionWorker + weights; capture → Finish → validation; EvidenceWorker produces clips.
5. Admin families - reference photos visible; Replace/Delete removes the object from the bucket.

---

## VisionWorker fine-tuning

Demo pipeline `matrix_v1` (Hungarian by default; `VISION_ASSIGN_SOLVER=greedy` for A/B). Seed: active model `yolo26l-demo` + 17 classes.

| Variable | Default | Role |
| --- | --- | --- |
| `VISION_BOX_RECALL_CONF` | `0.12` | Box existence floor |
| `VISION_CONF_DISPLAY_THRESHOLD` | `0.70` | Overlay naming (kept_raw) |
| `VISION_CONF_DISPLAY_REASSIGN` | `0.60` | Naming when reassigned (+ raw top-1 ≥ 0.70) |
| `VISION_REASSIGN_MIN_SCORE` | `0.45` | Floor to reassign family |
| `VISION_REASSIGN_MAX_GAP` | `0.35` | Closeness to raw top-1 |
| `VISION_ASSIGN_SOLVER` | `hungarian` | `hungarian` \| `greedy` |
| `VISION_FRAME_STRIDE_CUDA` | `1` | Infer every N frames (CUDA) |
| `VISION_FRAME_STRIDE_CPU` | `5` | Infer every N frames (CPU) |
| `VISION_FRAME_STRIDE` | (unset) | Forces CUDA and CPU to the same N |
| `VISION_COUNT_SAMPLE_INTERVAL_S` | `1.0` | Cap for `count_event` video_window |
| `VISION_WORKER_WS_URL` | `ws://<LAN>:5002/ws` (script) / loopback | Browser WebSocket |
| `VISION_WS_SECRET` | demo | HMAC Flask ↔ worker |
| `VISION_WS_ORIGINS` | localhost + LAN portal | WS handshake origins |
| `VISION_HOLD_SECONDS` | `0.275` | Hold with box / occlusion |
| `VISION_NAME_HOLD_SECONDS` | `0.35` | Sticky naming |
| `VISION_CONFUSION_HOLD_SECONDS` | `0.20` | Hold top2–N |
| `VISION_HOLD_MIN_SCORE` | `0.50` | Do not enter hold if conf ≤ 0.49 |
| `VISION_HOLD_DROP_SCORE` | `0.35` | Immediate hold drop if conf < 0.35 |
| `VISION_HOLD_SEED_FRAMES` | `3` | Seed in tape time |
| `VISION_HOLD_ABSENT_STREAK` | `1` | Absence grace |
| `VISION_HOLD_ABSENT_SECONDS` | `0.09` | Short TTL without overlap |
| `VISION_HOLD_ZONE_IOU` | `0.22` | Overlap / occlusion |
| `VISION_TRACK_MEMORY` | `1` | IoU tracks + EMA (`0` = rollback) |
| `VISION_TRACK_EMA_ALPHA` | `0.35` | Current-frame weight |
| `VISION_TRACK_IOU` | `0.30` | Minimum association IoU |
| `VISION_TRACK_MISS_SECONDS` | `0.50` | Track expiry |
| `VISION_TRACK_PRIOR` | `0.20` | Prior-family bonus |
| `VISION_TRACK_CONTENTION_MIN` | `0.25` | Open next quota |

---

## Development status

| Phase | Content | Status |
| --- | --- | --- |
| 0–1 | Docker packaging (PG/Redis/Mongo/Garage) + seeds | Done |
| 2 | Auth JWT/Redis (DS01), cookies, locale | Done |
| 3.x | OP/SPD RF contract, privacy, supervisor close | Done |
| 4 | OP/SPD RF UI | Done |
| 5a | VisionWorker + YOLO + matrix_v1 + BD-first evidence | Done (demo) |
| 6 | i18n `en` + `es-MX` | Done |
| 7 | Admin portal (`admin_local`) + CatalogService | Done |
| 8 | Session/Evidence/Audit/… microservices + Android | Integrated |

Code comments: English. UI copy: `en` + `es-MX` via Babel.
Data contracts: `data/DataStandards/`. Screen RFs: `data/FunctionalRequirements/`.
