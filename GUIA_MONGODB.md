# PEF — MongoDB (PEF-DS06): checkpoints y telemetría de visión

Implementación de las colecciones oficiales de [PEF-DS06](data/DataStandards/PEF-DS06-Mongo-Checkpoints-and-Telemetry.md). PostgreSQL sigue siendo la fuente de verdad de la sesión y del conteo ([DS04](data/DataStandards/PEF-DS04-Work-Session-and-Count-Facts.md)). MongoDB guarda solo el detalle visual y la telemetría. Los bytes de las imágenes viven en EvidenceService / Garage ([DS05](data/DataStandards/PEF-DS05-Evidence-and-Object-Storage.md)).

## 1. Arquitectura

```
Operador ─▶ BackendWebFlask ──(commit PostgreSQL: auto_count initial/final, close_blocked, phase_change)
                │
                └─ record_checkpoint() ─▶ cola local en disco (PEF_OUTBOX_DIR)        ← no hay red en la petición
                                              │ hilo pef-checkpoint-outbox
                                              ├─▶ MongoDB checkpoint_frame (insert idempotente)
                                              ├─▶ EvidenceService POST /api/v1/evidence (JPEG, token del Operador)
                                              └─▶ MongoDB: media_asset_id + gcs_uri + retention_until (una sola vez)

VisionWorker ─ process_video_file (mide fps/latencia/stride) ─▶ spool en disco ─▶ hilo pef-telemetry ─▶ MongoDB session_telemetry
```

| Colección | Quién escribe (DS06 §1.4 "Worker + adaptador") | Quién lee |
| :--- | :--- | :--- |
| `checkpoint_frame` | Adaptador de BackendWebFlask (`services/mongo_checkpoints.py`). Decide los eventos y tiene el token del Operador para la evidencia. | Panel del Supervisor (detalle de sesión, lectura en el servidor) y `scripts/mongo_ds06.py` |
| `session_telemetry` | VisionWorker (`telemetry.py`). Mide el procesamiento real. | Panel del Supervisor (agregación) y `scripts/mongo_ds06.py` |

MongoDB nunca se expone al navegador. EvidenceService no se modificó: su contrato actual (`POST /api/v1/evidence`, `GET /api/v1/sessions/{id}/evidence`, `GET /api/v1/evidence/{id}`) basta, y su prueba de alcance prohíbe `pymongo` en ese servicio.

### Eventos que generan checkpoints (todos reales)

| `reason` | Hecho en PostgreSQL | Cuadro clave / detecciones |
| :--- | :--- | :--- |
| `start` | `auto_count` `trigger = initial` (conteo inicial) | Última captura de la corrida inicial |
| `discrepancy` | `close_blocked` (conteo final rechazado) | Captura rechazada |
| `state_change` | `phase_change` en sesión `vision` | Cuadro actual del worker (si hay una corrida procesada; si no, sin detecciones ni evidencia) |
| `close` | `auto_count` `trigger = final` (final = referencia) | Captura final aceptada |
| `hourly`, `manual_pin` | — | No implementados: no existe ese disparador en el sistema |

No se generan ráfagas GIF. Cada checkpoint lleva un JPEG único, el *fallback* que prevé la regla 4 de DS06.

## 2. Esquemas (validador `$jsonSchema`, `validationLevel: strict`, `validationAction: error`)

**`checkpoint_frame`** (DS06 §1.3.2)

| Campo | Tipo | Notas |
| :--- | :--- | :--- |
| `_id` | ObjectId | Se fija al encolar, así el reintento no duplica |
| `session_id` | string (UUID) | `work_session.id` |
| `operation_id` | string (UUID) | `work_session.operation_id` |
| `media_asset_id` | string (UUID) | `media_asset.id`, solo si EvidenceService almacenó el JPEG |
| `gcs_uri` | string | `media_asset.gcs_uri` real, leído de PostgreSQL |
| `model_version` | string | `yolo_model.version_tag` que usó el worker |
| `reason` | enum | `start`, `close`, `hourly`, `state_change`, `discrepancy`, `manual_pin` |
| `phase_id` / `phase_code` | string | Fase de la sesión en ese momento |
| `captured_at` | date | Momento UTC del hecho |
| `detections[]` | array | `family_code` (obligatorio), `confidence` (0–1), `bbox` = `[x, y, w, h]` en píxeles del cuadro |
| `retention_until` | date | +90 días (o +180 con `extended_retention`); al vincular la evidencia se alinea con la de `media_asset` |
| `session_checkpoint_seq` | int ≥ 1 | Ascendente por sesión |

Los campos opcionales desconocidos se **omiten**, no se guardan como `null`, para que los índices *sparse* los excluyan. No se aceptan campos fuera de DS06, así que no puede entrar una imagen base64 ni un video.

**`session_telemetry`** (DS06 §1.3.3)

| Campo | Tipo | Medición real (por ventana de `VISION_COUNT_SAMPLE_EVERY` cuadros analizados, más el resto al terminar) |
| :--- | :--- | :--- |
| `session_id` | string (UUID) | `work_session.id` |
| `ts` | date | Cierre de la ventana |
| `fps_avg` | double | Cuadros analizados ÷ segundos de reloj de la ventana |
| `latency_ms` | double | Tiempo medio de `run_frame` (YOLO + asignación) por cuadro |
| `drops` | int | Cuadros leídos y no analizados (`VISION_FRAME_STRIDE`) |
| `roi_ok` | bool | **Ausente**: el worker no usa ROI todavía (no se inventa) |

`frames_processed`, `video_t` y la línea de tiempo NDJSON siguen siendo internas del worker; DS06 no se amplió.

## 3. Índices

| Colección | Índice | Opciones | Origen |
| :--- | :--- | :--- | :--- |
| `checkpoint_frame` | `session_captured_at` `(session_id, captured_at)` | — | DS06 |
| `checkpoint_frame` | `ttl_retention_until` `(retention_until)` | `expireAfterSeconds: 0` | DS06 (TTL) |
| `checkpoint_frame` | `operation_id_sparse`, `phase_code_sparse` | `sparse` | DS06 |
| `checkpoint_frame` | `session_checkpoint_seq_unique` `(session_id, session_checkpoint_seq)` | `unique` | Necesario para que la secuencia sea única con escritores concurrentes |
| `session_telemetry` | `session_ts` `(session_id, ts)` | — | Consultas por sesión |
| `session_telemetry` | `ttl_ts` `(ts)` | `expireAfterSeconds: 604800` | DS06 ("TTL 7 días") |

Tope por sesión: 50 checkpoints. Al excederlo se eliminan primero los `hourly` más antiguos; `start`, `close`, `state_change` y `discrepancy` nunca se borran (se registra un aviso).

## 4. Tolerancia a fallos

- **Ninguna petición del Operador toca MongoDB ni EvidenceService.** Solo escriben en la cola local; está probado con MongoDB detenido durante un conteo completo.
- **Checkpoints** (`PEF_OUTBOX_DIR`, por defecto `apps/BackendWebFlask/instance/mongo_outbox/`, en `.gitignore`):
  - Cada checkpoint es un archivo JSON, más su JPEG.
  - Reintento con *backoff* exponencial hasta 60 s.
  - Timeouts de MongoDB de `MONGO_TIMEOUT_MS` (1.5 s por defecto).
  - Las fallas se registran en el log `pef.checkpoints` con contador y último error.
  - **Límite:** `PEF_OUTBOX_MAX_JOBS` (500). Por encima se rechazan checkpoints nuevos y se registran; el conteo continúa.
  - **Reinicio:** los pendientes sobreviven. El token del Operador **no se guarda en disco**, así que un JPEG cuya subida no terminó antes del reinicio se descarta; el checkpoint queda sin `media_asset_id`.
  - **Sin duplicados:**
    - el `_id` se fija al encolar;
    - la secuencia es única por sesión;
    - antes de reintentar una subida a Evidence se busca un asset con el mismo SHA-256, porque EvidenceService no tiene clave de idempotencia;
    - la evidencia se vincula una sola vez (filtro `media_asset_id` inexistente).
  - **EvidenceService caído:** el checkpoint se guarda igual. La subida se reintenta `PEF_EVIDENCE_MAX_ATTEMPTS` (5) veces y luego se abandona (queda registrado).
- **Telemetría** (`TELEMETRY_SPOOL_DIR`, por defecto `apps/VisionWorker/uploads/telemetry_spool/`, en `.gitignore`):
  - Se escribe con `insert_many` desordenado e `_id` fijo, así que los duplicados se ignoran.
  - **Límite:** `TELEMETRY_SPOOL_MAX` (5000). Al superarlo se descartan **las más antiguas**, porque la telemetría no es evidencia legal. Se cuentan en `/health` → `telemetry.dropped`.
  - Un reinicio conserva el spool.
- **Reinicio de Flask:** el resumen y la validación de visión se reconstruyen desde PostgreSQL (lote `final` de DS04), sin MongoDB. Solo se pierde la imagen en memoria, que sigue disponible como evidencia.
- **Sin `MONGO_URL`:** ambos escritores quedan desactivados y el sistema funciona igual.

## 5. Docker y variables

`docker-compose.yml` ya incluye `mongo:7` (volumen `mongo_data`, healthcheck `mongosh ping`), `garage` y `evidence`. MongoDB no necesita script de inicio: las colecciones, validadores e índices se crean de forma idempotente en la primera escritura (`ensure_schema`) o con `python scripts/mongo_ds06.py init`.

| Variable | Dónde | Uso |
| :--- | :--- | :--- |
| `MONGO_USER`, `MONGO_PASSWORD`, `MONGO_PORT` | compose | Usuario root y puerto publicado |
| `MONGO_URL` | Web, VisionWorker | `mongodb://<user>:<pass>@127.0.0.1:<MONGO_PORT>/?authSource=admin` (vacío = desactivado) |
| `MONGO_DB` | Web, VisionWorker | Base de datos (`pef`) |
| `MONGO_TIMEOUT_MS` | Web, VisionWorker | Timeouts de conexión y selección de servidor |
| `PEF_OUTBOX_DIR`, `PEF_OUTBOX_MAX_JOBS`, `PEF_EVIDENCE_MAX_ATTEMPTS` | Web | Cola de checkpoints |
| `EVIDENCE_SERVICE_URL`, `EVIDENCE_SERVICE_TIMEOUT` | Web | EvidenceService (vacío = checkpoints sin imagen) |
| `TELEMETRY_SPOOL_DIR`, `TELEMETRY_SPOOL_MAX` | VisionWorker | Spool de telemetría |
| `GARAGE_RPC_PORT`, `GARAGE_ADMIN_PORT` | compose | Puertos de Garage en el host (antes fijos 3901/3903) |

Windows:
- Hyper-V reserva rangos de puertos. Consúltalos con `netsh int ipv4 show excludedportrange protocol=tcp`; por ejemplo, 57017–57116 no se puede publicar.
- La clave de Garage debe tener el formato `GK` + 24 caracteres hexadecimales, y el secreto 64 hexadecimales.
- Sin `container_name` fijos, el proyecto convive con otras copias del PEF; usa `-p <proyecto>` y puertos propios en `.env`.

Instalación (además de los `requirements.txt` actualizados con `pymongo==4.19.0`):

```powershell
docker compose up -d postgres redis mongo garage evidence
python scripts/mongo_ds06.py init         # opcional: crea colecciones, validadores e índices
python scripts/mongo_ds06.py indexes
```

## 6. Operaciones y consultas

`services/mongo_checkpoints.py`:

- **Inserción:** `record_checkpoint` → cola → `_insert`.
- **Consultas:**
  - por sesión, con filtros por motivo y fechas: `list_checkpoints(session_id, reason, since, until)`;
  - por identificadores de PostgreSQL: `find_by_operation(operation_id)` y `find_by_media_asset(media_asset_id)`.
- **Agregaciones:** `checkpoint_summary` (por motivo, con detecciones y evidencias) y `telemetry_summary` (promedio y mínimo de FPS, latencia media y máxima, cuadros sin analizar).
- **Actualizaciones permitidas:**
  - `attach_evidence`: `media_asset_id`, `gcs_uri` y `retention_until`, una sola vez;
  - `extend_retention`: alinea con `extended_retention` (+180 días).

Las detecciones, el motivo, `captured_at` y la secuencia nunca se modifican.

Consola: `python scripts/mongo_ds06.py session <uuid>` y `python scripts/mongo_ds06.py verify-ids`.

## 7. Reproducir las pruebas

```powershell
# Unitarias / integración (MongoDB y PostgreSQL reales; bases de prueba temporales)
cd apps/BackendWebFlask; $env:PEF_TEST_MONGO_URL = $env:MONGO_URL; python -m pytest -q
cd ../VisionWorker;      $env:PEF_TEST_MONGO_URL = $env:MONGO_URL; python -m pytest -q tests

# End to end (Web + Auth + VisionWorker con best.pt + compose)
python scripts/make_test_tray_videos.py test_videos
$env:COMPOSE_ENV_FILE = ".env"; $env:PEF_OUTBOX_DIR = "<la misma carpeta que usa el proceso Web>"
python scripts/e2e_mongo_ds06.py test_videos
```

El E2E crea dos sesiones de visión con los usuarios demo y comprueba las 10 evidencias: video procesado, consulta, telemetría, filtros, agregaciones, actualización permitida, índices, identificadores contra PostgreSQL, MongoDB detenido y recuperación. En el paso 9 detiene y vuelve a iniciar el servicio `mongo` del compose; no borra el volumen.

**Los videos de prueba salen de la foto de referencia de la interfaz, no de una captura auténtica.** Demuestran la persistencia técnica y **no validan la exactitud de YOLO**: con `best.pt` se detectan 5 instrumentos de unos 16 visibles, algunos con confianza cercana a 0.19.
