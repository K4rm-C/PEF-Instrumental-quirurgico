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
│   ├── DataStandards/            # PEF-DS01…DS10
│   ├── FunctionalRequirements/   # RF operador/SPD y Admin/App
│   ├── ER PostgreSQL-MongoDB-v3.jpg
│   ├── migrations/
│   └── seeds/
└── apps/
    ├── BackendAuthService/
    ├── BackendWebFlask/
    └── VisionWorker/
        └── weights/best.pt   # local only (gitignored)
```



## Internationalization / i18n

La UI web (BackendWebFlask) usa **Flask-Babel**.

- **Idiomas:** `en` (fuente) y `es-MX`. Los códigos públicos son BCP 47 (`en`, `es-MX`) en `user.ui_preferences.locale`, la cookie `pef_locale` y `<html lang>`; solo el catálogo usa la forma POSIX `es_MX`.
- **Fuente:** los `msgid` son el texto en inglés; `en` no necesita catálogo.
- **Catálogo:** `apps/BackendWebFlask/translations/es_MX/LC_MESSAGES/messages.po` (+ `messages.mo` compilado, versionado).
- **Resolución del idioma:** usuario autenticado → `ui_preferences.locale`; invitado → cookie `pef_locale`; si no hay o es inválido → `en`.
- **Cambio de idioma:** el selector del encabezado hace `POST /locale` (Web). Invitado: solo cookie. Autenticado: Web llama `POST /preferences` del Auth Service (merge parcial de `ui_preferences`, conserva `theme`) y actualiza la cookie. La página se recarga en la misma URL (query y `#tab` incluidos).
- **JavaScript:** los textos visibles de `static/js` vienen traducidos desde `templates/base.html` (`window.PEF_I18N`, se leen con `window.pefT(key)`); no hay traducciones dentro de los `.js`.
- **No se traducen** datos de negocio (nombres, IDs, códigos de estado/rol/fase, textos capturados por usuarios); sí sus etiquetas visibles controladas por código.
- **Valores de BD controlados por el sistema** (catálogos `cat_*`, familias, kits, estaciones, quirófanos, roles, técnicas): se localizan solo al mostrarlos, con `localize_db_label` / `localize_db_text` / `localize_known_text` de `i18n.py`, usando el `code` (o UUID) como clave y un msgid inglés. La BD no cambia. Un registro cuyo texto guardado ya no coincide con un texto conocido (p. ej. renombrado por un admin) se muestra tal cual, y los inputs editables siempre muestran el valor guardado. Para un registro nuevo del sistema: agregar su entrada `N_("...")` en `i18n.py`, luego extract → update → traducir → compile.

Agregar un texto nuevo:

```jinja
{{ _("New text") }}                         {# plantilla #}
{{ _("Showing %(shown)s of %(total)s", shown=n, total=t) }}
```

En Python usa `gettext("...")` de `flask_babel` dentro de una petición, o `N_("...")` (de `i18n.py`) para etiquetas definidas a nivel de módulo que la plantilla traduce con `_()`.

Después: **extract → update → traducir → compile** (desde `apps/BackendWebFlask`, con el venv activo):

```powershell
pybabel extract -F babel.cfg -k N_ --no-wrap --sort-by-file -o messages.pot .
pybabel update -i messages.pot -d translations --no-wrap
# traducir los msgstr nuevos/vacíos (y revisar los marcados "fuzzy") en translations/es_MX/LC_MESSAGES/messages.po
pybabel compile -d translations --statistics
```

Pruebas: `python -m pytest tests` en `apps/BackendWebFlask` y en `apps/BackendAuthService`.

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
| 6    | i18n real (Flask-Babel): `en` + `es-MX`, selector persistente en `ui_preferences`                            | Listo        |


Idioma: **ingles** como fuente, traduccion `es-MX` con Flask-Babel (ver **Internationalization / i18n**). Rutas pre-RF del operador viven en `/legacy/...` (ver `apps/BackendWebFlask/legacy/README.md`). Plantillas V2/mocks retiradas estan en `Legacy/` (raiz del repo).

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
| `VISION_BOX_RECALL_CONF`        | `0.12`      | Piso para que una caja exista (permisivo)                                                                          |
| `VISION_CONF_DISPLAY_THRESHOLD` | `0.70`      | Overlay naming (kept_raw): familia vs tipo                                                                         |
| `VISION_CONF_DISPLAY_REASSIGN`  | `0.60`      | Naming si reassigned: además exige raw top-1 ≥ 0.70                                                                |
| `VISION_REASSIGN_MIN_SCORE`     | `0.45`      | Piso para reasignar a otra familia esperada                                                                        |
| `VISION_REASSIGN_MAX_GAP`       | `0.35`      | Cercania al score top-1 raw (scores sigmoid); lookalikes pueden competir por cupo libre                            |
| `VISION_ASSIGN_SOLVER`          | `hungarian` | Matching sobre la matriz: `hungarian` | `greedy`                                                                   |
| `VISION_FRAME_STRIDE_CUDA`      | `1`         | Inferir cada N frames en CUDA (~cada frame a ~60 ms)                                                               |
| `VISION_FRAME_STRIDE_CPU`       | `5`         | Inferir cada N frames en CPU (~6 fps a 30 fps). Fallback si no hay GPU                                             |
| `VISION_FRAME_STRIDE`           | (unset)     | Si se setea, pinea CUDA y CPU al mismo N (salvo que exista `_CUDA` / `_CPU`)                                       |
| `VISION_COUNT_SAMPLE_INTERVAL_S`| `1.0`       | Tope de `count_event` `video_window`: 1 por segundo de cinta (`video_t`) o de reloj en live                         |
| `VISION_WORKER_WS_URL`          | `ws://127.0.0.1:5002/ws` | URL que el navegador usa para el WS (Flask solo emite el ticket)                                      |
| `VISION_WS_SECRET`              | demo        | HMAC compartido Flask ↔ worker. Cambiar fuera de demo.                                                             |
| `VISION_WS_ORIGINS`             | localhost:5000 | Orígenes permitidos en el handshake WS                                                                          |
| `VISION_HOLD_SECONDS`           | `2.5`       | Hold largo mientras hay caja / oclusion en zona                                                                    |
| `VISION_NAME_HOLD_SECONDS`      | `4.0`       | Sticky naming: mientras YOLO siga viendo caja en el hueco (aunque conf < 0.70) se renueva; sin caja, caduca        |
| `VISION_HOLD_MIN_SCORE`         | `0.35`      | No sembrar/refrescar hold con fantasmas debiles                                                                    |
| `VISION_HOLD_SEED_FRAMES`       | `3`         | Calibrado a stride CPU: se convierte a segundos de cinta (`N × stride_cpu / fps`). CUDA espera el mismo tiempo, no 3 frames |
| `VISION_HOLD_ABSENT_STREAK`     | `3`         | Igual, gracia de ausencia en tiempo de cinta. Un parpadeo 1-frame no reinicia el reloj ni extiende el hold largo   |
| `VISION_HOLD_ABSENT_SECONDS`    | `1.1`       | TTL corto cuando se confirma ausencia (sin overlap en zona)                                                        |
| `VISION_HOLD_ZONE_IOU`          | `0.15`      | Overlap para tratar oclusion (retrasa el hold corto; no reinicia el timeout de 2.5 s)                              |
| `VISION_TRACK_MEMORY`           | `1`         | Pistas IoU + EMA de scores + prior de familia. Rollback: `0` (vuelve al matching frame-a-frame)                    |
| `VISION_TRACK_EMA_ALPHA`        | `0.35`      | Peso del frame actual en el promedio (CUDA ~0.2 s de memoria)                                                      |
| `VISION_TRACK_IOU`              | `0.30`      | IoU minimo para asociar la caja con la pista anterior                                                              |
| `VISION_TRACK_MISS_SECONDS`     | `0.50`      | Sin match, la pista caduca (no es el hold de 2.5 s)                                                                |
| `VISION_TRACK_PRIOR`            | `0.20`      | Bonus de contexto hacia la familia que ya tenia esa pista                                                          |
| `VISION_TRACK_CONTENTION_MIN`   | `0.25`      | Si el top-1 ya esta en otra caja, abre arista al siguiente del top con cupo libre                                  |


1. SPD: **New session** con privacy Via A (aviso placeholder) → OP ve **Notice OK · Vision**.
2. OP (`operator@…` u `operador2@…`): Review & Start → Capture.
3. En Capture: **Start camera** (gesto) pide `getUserMedia` solo video y abre WS directo al worker (`ws://127.0.0.1:5002/ws`) con ticket HMAC de Flask. El cliente pauta JPEG según `latency_ms` (slot 1, latest-wins; sin cola YOLO). El overlay llega por WS; el poll HTTP ~2.5 s solo persiste `count_event` (máx. 1/s de reloj). Upload de archivo queda para demo/lab; se puede **interrumpir y volver a cámara**. `getUserMedia` en `http://127.0.0.1` / `localhost` (LAN requiere HTTPS después).
3b. Alternativa archivo: subir `.mp4` / `.mkv` / etc. El worker procesa frames (stride CUDA=1 / CPU=5), construye matriz caja×familia, asigna (Hungarian/greedy) hasta cupo esperado, aplica hold/sticky **interno**, dibuja cajas con **color por tipo**. El overlay del archivo se pide al ritmo de `latency_ms`.
4. **Auditoria temporal:** como máximo 1 sample por segundo de video (`VISION_COUNT_SAMPLE_INTERVAL_S`) → NDJSON local (`*_timeline.ndjson`) con observables (raw/refined, boxes+topk) y latencia de esa ventana. En PG: **un** `count_event` por sample (`family_id` null, `scope=board`, `reason=video_window`) + un board final `video_ready` (con boxes). Discrepancias siguen llevando `family_id` propio y pueden apuntar al board como `origin_event_id`. Serie densa a largo plazo → Mongo DS06. Smoke: `python scripts/smoke_vision_board_events.py`.
5. **Finish counting** → resumen AI (incluye Raw vs refined) → **Human validation** (AI locked; validated editable). El cierre usa la ruta de conteo manual (discrepancias solo si validated ≠ expected). El worker se desliga al cerrar.
6. Cambio de fase en Capture es real en PG (`phase_change`) pero stub respecto a Mongo/Garage.
7. Fuera de alcance en este ciclo: Electron, evidencia Mongo/Garage, GIF de evidencia, disc. automaticas por ausencia temporal. Matching: Hungarian default; greedy via env para A/B. Ticket WS: `VISION_WS_SECRET` (mismo valor en Flask y worker). Smoke WS: `python scripts/smoke_vision_ws.py` con el worker arriba.

**5) Smokes CLI (opcionales; ensucian seed)**

Desde `apps/BackendWebFlask` con venv activo:

```powershell
python ..\..\scripts\smoke_rf_32.py
python ..\..\scripts\smoke_rf_33.py
```

Despues, si necesitas demos UI con seeds predecibles, vuelve a `down -v` + `up -d` + `privacy-notices`.