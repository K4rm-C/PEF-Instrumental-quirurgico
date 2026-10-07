# PEF-Instrumental-quirurgico

Conteo y trazabilidad asistidos por vision de instrumental quirurgico (PEF UDEM).

Stack actual: **Flask** (portal operador/SPD/admin), **Auth Service** (Flask/JWT), **PostgreSQL**, **Redis**, **MongoDB**, **Garage** (S3-compatible; reemplaza MinIO porque su imagen ya no esta en Docker Hub).

## Equipo


| Integrante                 | Matricula |
| -------------------------- | --------- |
| Benjamin Charles Legorreta | 599860    |
| Pedro Elidio Sora Gonzalez | 596630    |
| Angel Uriel Muñoz Moreno   | 604386    |


Equipo de apoyo: Luis Carlos Rodriguez Medrano, Carlos Ignacio Huerta Carrizales, Juan Hermilo Reyes Perez. 

Asesor: Dr. Raul Morales Salcedo.

## Prerrequisitos

- Docker Desktop en ejecucion
- Python 3.11+ en PATH
- Git
- Puertos libres: `5433`, `6379`, `27017`, `3900`, `3901`, `3903`, `5000`, `5001`, `5002`

```powershell
docker --version
docker compose version
python --version
```



## Arranque rapido

Desde la **raiz** del repo (`PEF-Instrumental-quirurgico`).

### 1) Entorno

Solo la **primera vez**, o cuando cambies `.env.example` / tu `.env` de raiz:

```powershell
copy .env.example .env
copy .env apps\BackendAuthService\.env
copy .env apps\BackendWebFlask\.env
```

Nota: No hace falta repetir `copy` en cada terminal nueva. Auth y Web leen el `.env` **de su carpeta** al arrancar una terminal nueva solo necesita `cd` + activar venv + `python ...`.  
Volver a hacer `copy` sobrescribe esos `.env` con el de la raiz (util si editaste la raiz; malo si solo habias tocado el de un app).

### 2) Contenedores

Si antes fallaste en alguno o dio error al iniciar puedes intentarlo de nuevo (comando ps valida status)

```powershell
docker compose down
docker compose up -d
docker compose ps
```

**No tienes que ejecutar SQL a mano.** Al subir Postgres por primera vez (volumen vacio), Docker monta y corre solo, en este orden:

1. `data/migrations/001_init.sql`
2. `data/seeds/002_seed_catalogs.sql`
3. `data/seeds/003_seed_demo.sql`

Eso pasa **dentro** del contenedor al inicializar la BD. En arranques siguientes, si el volumen ya existe, Postgres **no** vuelve a correr esos archivos.

Para forzar que se apliquen de nuevo (borra datos de todos los volumenes del compose):

```powershell
docker compose down -v
docker compose up -d
```

Servicios esperados Up: `pef_postgres`, `pef_redis`, `pef_mongo`, `pef_garage`.

Tras el primer `up` de Garage, crea el segundo bucket:

```powershell
docker exec pef_garage /garage status
docker exec pef_garage /garage bucket create privacy-notices
docker exec pef_garage /garage bucket allow --read --write --owner privacy-notices --key MedAdmin3
docker exec pef_garage /garage bucket list
docker exec pef_garage /garage key list
```

El bucket `evidence` y la key `MedAdmin3` se crean solos con `--default-bucket` si `S3_SECRET_KEY` es valida.

### 3) Auth, Web y VisionWorker (tres terminales)

**Terminal A — Auth**

```powershell
cd apps\BackendAuthService
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

**Terminal B — Web**

```powershell
cd apps\BackendWebFlask
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

**Terminal C — VisionWorker (YOLO)**

Coloca los pesos **antes** del primer arranque (archivo grande, **no** va a GitHub; `*.pt` esta en `.gitignore`):

```powershell
# Ruta esperada (desde la raiz del repo):
# apps\VisionWorker\weights\best.pt
cd apps\VisionWorker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Comprueba: [http://127.0.0.1:5002/health](http://127.0.0.1:5002/health) debe reportar `"weights_present": true`.

Opcional Windows: `.\start-services.ps1` (compose + abre Auth, Web y VisionWorker).

### 4) URLs y puertos


| Servicio      | Donde                                                        |
| ------------- | ------------------------------------------------------------ |
| Portal web    | [http://127.0.0.1:5000](http://127.0.0.1:5000)               |
| Auth          | [http://127.0.0.1:5001](http://127.0.0.1:5001)               |
| VisionWorker  | [http://127.0.0.1:5002/health](http://127.0.0.1:5002/health) |
| Garage S3 API | [http://127.0.0.1:3900](http://127.0.0.1:3900)               |
| PostgreSQL    | `localhost:5433` / db `InstruMed`                            |
| Redis         | `localhost:6379`                                             |
| MongoDB       | `localhost:27017`                                            |



| Puerto | Uso              |
| ------ | ---------------- |
| 5433   | PostgreSQL       |
| 6379   | Redis            |
| 27017  | MongoDB          |
| 3900   | Garage S3        |
| 3901   | Garage RPC       |
| 3903   | Garage admin API |
| 5000   | Backend Web      |
| 5001   | Auth             |
| 5002   | VisionWorker     |




### 5) Credenciales

**Object storage (Garage / S3)**


| Campo      | Valor                           |
| ---------- | ------------------------------- |
| Endpoint   | `http://localhost:3900`         |
| Region     | `garage`                        |
| Access key | `MedAdmin3`                     |
| Secret key | `StorageKeySecret027`           |
| Path style | si (`S3_FORCE_PATH_STYLE=true`) |
| Buckets    | `evidence`, `privacy-notices`   |


Prueba S3 (opcional, con AWS CLI v2):

```powershell
$env:AWS_ACCESS_KEY_ID="MedAdmin3"
$env:AWS_SECRET_ACCESS_KEY="StorageKeySecret027"
$env:AWS_DEFAULT_REGION="garage"
aws --endpoint-url http://127.0.0.1:3900 s3 ls
aws --endpoint-url http://127.0.0.1:3900 s3 ls s3://evidence
```

**Usuarios demo (Postgres seed; hashes Werkzeug 3.1.8)**  
Códigos de rol RF: `station_operator`, `spd_supervisor`, `it_admin`.


| Rol (RF)         | Email                       | Password         |
| ---------------- | --------------------------- | ---------------- |
| station_operator | `operator@instrumed.com`    | `DemopwdOP78!`   |
| station_operator | `operador2@instrumed.com`   | `DemopwdOP278!`  |
| spd_supervisor   | `supervisor@instrumed.com`  | `DemopwdSPD78!`  |
| spd_supervisor   | `supervisor2@instrumed.com` | `DemopwdSPD278!` |
| it_admin         | `admin@instrumed.com`       | `DemopwdADM78!`  |


```powershell
curl -i -X POST http://127.0.0.1:5001/login -H "Content-Type: application/json" -d "{\"email\":\"operator@instrumed.com\",\"password\":\"DemopwdOP78!\"}"
```

Smoke Auth + Redis (tras `pip install -r requirements.txt` en Auth y contenedores Up):

```powershell
.\scripts\smoke_auth.ps1
```

Login por UI: [http://127.0.0.1:5000/sign-in](http://127.0.0.1:5000/sign-in) con las mismas credenciales.

## Estructura

```
PEF-Instrumental-quirurgico/
├── docker-compose.yml
├── .env.example
├── start-services.ps1
├── start-services.sh
├── infra/garage.toml
├── data/
└── apps/
    ├── BackendAuthService/
    ├── BackendWebFlask/
    └── VisionWorker/
        └── weights/best.pt   # local only (gitignored)
```



## Problemas frecuentes

**Garage no lista buckets**  
Espera unos segundos y revisa logs: `docker compose logs garage`. Luego `docker exec pef_garage /garage status`.

**Garage reinicia en bucle**  
Revisa `docker logs pef_garage`. Si dice `Secret keys should be at least 16 characters long`, alarga `S3_SECRET_KEY` en `.env`, luego:

```powershell
docker compose down
docker volume rm pef-instrumental-quirurgico_garage_meta pef-instrumental-quirurgico_garage_data
docker compose up -d garage
```

**Bucket privacy-notices falta**  

```powershell
docker exec pef_garage /garage bucket create privacy-notices
docker exec pef_garage /garage bucket allow --read --write --owner privacy-notices --key MedAdmin3
```

**Postgres sin seeds**  
Init solo en volumen nuevo: `docker compose down -v` y `up -d` de nuevo.

**Login 401**  
Auth debe tener `Werkzeug==3.1.8` y el seed actual

## Estado del desarrollo


| Fase | Contenido                                                                                                    | Estado       |
| ---- | ------------------------------------------------------------------------------------------------------------ | ------------ |
| 0–1  | Empaque Docker (PG/Redis/Mongo/Garage) + seeds + README                                                      | Listo        |
| 2    | Auth JWT en Redis (DS01), cookies demo, `ui_preferences.locale`, smoke login                                 | Listo        |
| 3.1  | Contrato RF: estados, `capture_mode`, roles RF, seeds ± aviso, OP-01/OP-02 lectura                           | Listo        |
| 3.2  | SP-02 programar + privacy Via A, OP-03 Start, OP-04M/06M manual                                              | Listo        |
| 3.3  | Seeds D/E + SP-05/06 cierre + detalle RF supervisor                                                          | Listo        |
| 4    | UI RF OP/SPD (sidebars, listas, schedule, kits SPD, stubs reports/profile/health)                            | Listo        |
| 5a   | Vision upload + VisionWorker + YOLO (`best.pt`) + matrix_v1 (Hungarian/greedy) + count_event + validacion UI | Listo (demo) |
| 5b   | Electron WSS camara live + Mongo/Garage evidence-worker                                                      | Pendiente    |
| 6    | i18n real (Flask-Babel)                                                                                      | Diferido     |


Idioma: **ingles** como fuente; Babel = Fase 6. Rutas pre-RF del operador viven en `/legacy/...` (ver `apps/BackendWebFlask/legacy/README.md`). Plantillas V2/mocks retiradas estan en `Legacy/` (raiz del repo).

### Pruebas (usabilidad y debugging)

Credenciales: seccion **5) Credenciales**. Si la semilla quedo sucia tras smokes o demos, recrea volumenes (`down -v` + `up -d`) y vuelve a crear `privacy-notices`. Reinicia Auth/Web despues.

**1) Infra y semilla**

```powershell
docker compose ps
.\scripts\smoke_auth.ps1
docker exec -i pef_postgres psql -U MedAdmin -d InstruMed -c "SELECT code FROM role ORDER BY 1;"
docker exec -i pef_postgres psql -U MedAdmin -d InstruMed -c "SELECT LEFT(id::text,8) AS sid, (SELECT code FROM cat_session_status s WHERE s.id=w.status_id) AS status, capture_mode FROM work_session w ORDER BY id;"
docker exec -i pef_postgres psql -U MedAdmin -d InstruMed -c "SELECT name FROM kit WHERE id='51515151-0000-4000-8000-000000000001';"
docker exec -i pef_postgres psql -U MedAdmin -d InstruMed -c "SELECT f.code, ki.quantity FROM kit_item ki JOIN instrument_family f ON f.id=ki.family_id WHERE ki.kit_id='51515151-0000-4000-8000-000000000001' ORDER BY 1;"
docker exec -i pef_postgres psql -U MedAdmin -d InstruMed -c "SELECT code FROM cat_operation_phase WHERE code LIKE 'demo_phase%' OR code IN ('start','final_count') ORDER BY 1;"
```

Esperado con semilla limpia: roles RF; kit **Video Demo Kit 1** (13 familias, FARABEUF×2); fases `start`, `demo_phase_1..3`, `final_count`; sesiones demo en estados variados; ≥5 `available` por familia del kit.

**2) Login UI y sidebars**

1. [http://127.0.0.1:5000/sign-in](http://127.0.0.1:5000/sign-in) con cada rol demo.
2. **OP** sidebar: Assigned Sessions → Session History → Profile. Home = `/operator/sessions`.
3. **SPD** sidebar: Dashboard → Sessions / New session / Discrepancies / History → Kits / Instruments → Indicators / Reports (stub) / Audit → Services health (stub) → Profile. Privacy catalog = admin only.
4. **Admin** → panel admin (privacy notices, users, etc.).

**3) Ciclo SPD → OP → SPD (ruta manual — utilizable sin YOLO)**

1. SPD: **New session** (`/supervisor/sessions/new`). Sin phase dropdown (fase inicial del procedimiento). Sin privacy → OP vera Manual; con privacy Via A → Vision.
2. OP: sesion **No notice · Manual** → Review & Start → Start → Continue → reporte de cantidades.
  - Match → `awaiting_spd_review`.
  - Baja una cantidad + reason → `correction_required` + discrepancia.
3. SPD:
  - **Correction Required** → Review → resolve → `awaiting_spd_review`.
  - **Awaiting Review** → Confirm close → `closed`.
4. Detalles OP/SPD: expected vs reported, privacy badge, timeline cuando aplica. OP no cierra sesiones.

**4) Rama vision (upload video + YOLO + worker)**

Prerrequisitos: VisionWorker en `:5002` y pesos en `apps/VisionWorker/weights/best.pt` (no estan en GitHub). Seed incluye `yolo_model` activo `yolo26l-demo` + `model_class` (17 etiquetas). Pipeline demo: `matrix_v1` (NMS con scores por clase + matching por cupos; solver default **Hungarian**, `VISION_ASSIGN_SOLVER=greedy` para A/B).

Umbrales por defecto (override con env `VISION_*`):


| Variable                        | Default     | Rol                                                                                                                |
| ------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------ |
| `VISION_BOX_RECALL_CONF`        | `0.15`      | Piso para que una caja exista (permisivo)                                                                          |
| `VISION_CONF_DISPLAY_THRESHOLD` | `0.70`      | Overlay naming (kept_raw): familia vs tipo                                                                         |
| `VISION_CONF_DISPLAY_REASSIGN`  | `0.60`      | Naming si reassigned: además exige raw top-1 ≥ 0.70                                                                |
| `VISION_REASSIGN_MIN_SCORE`     | `0.45`      | Piso para reasignar a otra familia esperada                                                                        |
| `VISION_REASSIGN_MAX_GAP`       | `0.15`      | Cercania al score top-1 raw (scores sigmoid)                                                                       |
| `VISION_ASSIGN_SOLVER`          | `hungarian` | Matching sobre la matriz: `hungarian` | `greedy`                                                                   |
| `VISION_FRAME_STRIDE`           | `5`         | Inferir cada N frames (~6 fps a 30 fps)                                                                            |
| `VISION_COUNT_SAMPLE_EVERY`     | `10`        | Muestrear tallies a timeline NDJSON (+ `count_event` `video_window` al ready). `1` = cada inferencia (mas costoso) |
| `VISION_HOLD_SECONDS`           | `2.5`       | Hold largo mientras hay caja / oclusion en zona                                                                    |
| `VISION_NAME_HOLD_SECONDS`      | `4.0`       | Sticky naming: no degradar familia→tipo si nadie lo supera                                                         |
| `VISION_HOLD_MIN_SCORE`         | `0.35`      | No sembrar/refrescar hold con fantasmas debiles                                                                    |
| `VISION_HOLD_SEED_FRAMES`       | `3`         | Inferencias live seguidas antes de sembrar hold (anti fantasma 1 frame)                                            |
| `VISION_HOLD_ABSENT_STREAK`     | `3`         | Frames en 0 live antes de acortar TTL (gracia a mano)                                                              |
| `VISION_HOLD_ABSENT_SECONDS`    | `1.1`       | TTL corto cuando se confirma ausencia (sin overlap en zona)                                                        |
| `VISION_HOLD_ZONE_IOU`          | `0.12`      | Si alguna caja live solapa el hold, se trata como oclusion (TTL largo)                                             |


1. SPD: **New session** con privacy Via A (aviso placeholder) → OP ve **Notice OK · Vision**.
2. OP (`operator@…` u `operador2@…`): Review & Start → Capture.
3. En Capture: subir video `.mp4` / `.mkv` / etc. El worker procesa frames (stride), construye matriz caja×familia, asigna (Hungarian/greedy) hasta cupo esperado, aplica hold/sticky **interno** (sin texto hold/sticky al operador), dibuja cajas con **color por tipo** (saturado = tipo, suave = familia) y el panel lateral resalta cada seccion de tipo.
4. **Auditoria temporal:** cada `VISION_COUNT_SAMPLE_EVERY` inferencias → NDJSON local (`*_timeline.ndjson`) con observables (raw/refined, boxes+topk). En PG: **un** `count_event` por sample (`family_id` null, `scope=board`, `reason=video_window`) + un board final `video_ready` (con boxes). Discrepancias siguen llevando `family_id` propio y pueden apuntar al board como `origin_event_id`. Serie densa a largo plazo → Mongo DS06. Smoke: `python scripts/smoke_vision_board_events.py`.
5. **Finish counting** → resumen AI (incluye Raw vs refined) → **Human validation** (AI locked; validated editable). El cierre usa la ruta de conteo manual (discrepancias solo si validated ≠ expected). El worker se desliga al cerrar.
6. Cambio de fase en Capture es real en PG (`phase_change`) pero stub respecto a Mongo/Garage.
7. Fuera de alcance en este ciclo: Electron/camara live, evidencia Mongo/Garage, disc. automaticas por ausencia temporal. Matching: Hungarian default; greedy via env para A/B.

**5) Smokes CLI (opcionales; ensucian seed)**

Desde `apps/BackendWebFlask` con venv activo:

```powershell
python ..\..\scripts\smoke_rf_32.py
python ..\..\scripts\smoke_rf_33.py
```

Despues, si necesitas demos UI con seeds predecibles, vuelve a `down -v` + `up -d` + `privacy-notices`.