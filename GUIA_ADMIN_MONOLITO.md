# PEF — Administración (RF-AD-01 … RF-AD-10) en el monolito BackendWebFlask

Actualizada: 2026-11-03 · Tarea ADMIN-01 + auditoría ADMIN-08 + validación ADMIN-09 (cierre de la Parte A de `data/FunctionalRequirements/rf_admin_app.md`). RF-AD-11 (ARCO) está fuera de alcance y no se implementa.

## 1. Arquitectura

| Aspecto | Cómo queda |
|---|---|
| Operaciones de negocio de Administración | **Todas en proceso** dentro de `apps/BackendWebFlask` (`services/admin_local/`), sobre la misma sesión PostgreSQL del portal. Sin AdminService. |
| Autenticación de los tres roles | **AuthService sin cambios** (login, `/verify`, `/refresh`, logout, JWT, revocación Redis) en `controllers/routes.py`. |
| Usuarios y roles | Locales (`admin_local/users.py`, `roles.py`) sobre las **mismas tablas** `user` / `role` / `user_role` que AuthService lee en cada petición. Sin duplicar usuarios ni contraseñas: el hash `werkzeug` que escribe el portal es el que verifica AuthService. |
| Revocación de sesiones (D-1) | Tras desactivar o restablecer contraseña, el portal llama a `POST /api/v1/auth/users/{id}/sessions/revoke` (`admin_local/auth_sessions.py`), que reutiliza `token_store.revoke_user_tokens`. El portal no conoce el formato Redis. Es la **única** llamada de Administración a AuthService fuera de la autenticación. |
| Alcance institucional (D-2) | Cada `it_admin` solo ve y modifica usuarios, roles, salas, estaciones, parámetros, auditoría y kits vinculados **de su institución** (404 si no). Familias, procedimientos, catálogos de selección, avisos y modelos son **catálogos compartidos por diseño** (ER y RF v1: un vocabulario y un único modelo activo; sin `institution_id`). En despliegues con varias instituciones, `ADMIN_CATALOG_STEWARD_INSTITUTION_ID` restringe su escritura a los administradores de esa institución (los demás: solo lectura, 403 auditado). Análisis completo: `docs/ADMIN_MATRIZ_CUMPLIMIENTO.md` §11.1. |
| Supervisor | Sin cambios de arquitectura: piezas y kits (`/admin/instruments`, `/admin/kits`) son **solo del Supervisor SPD**; si `CATALOG_SERVICE_URL` está configurado los atiende CatalogService para él. AuditService/MonitoringService siguen para sus pantallas. |
| Almacenamiento de archivos | `admin_local/storage.py`: backend `local` (volumen) o `s3` (Garage, mismas variables y estilo path/SigV4 que EvidenceService). Tamaño limitado al recibir, SHA-256 en servidor, tipo por *magic bytes*, claves generadas por el servidor. |

Despacho: `controllers/admin_routes.py` → `admin_local/facade.py` → `admin_local/api.py` (router en proceso) → módulo de dominio. Cada operación vuelve a verificar el rol (`security.require_roles`) y audita las denegaciones (`DENIED_*`).

## 2. Requisitos y módulos

| RF | Pantallas | Módulo | Reglas principales |
|---|---|---|---|
| 01 Dashboard | `/admin/dashboard` | `dashboard.py`, `health.py` | Sesiones por estado (`in_progress`, `or_hold`, `correction_required`, `awaiting_spd_review`) de la institución con colores por umbral (RF-AD-10); familias activas/inactivas, procedimientos, modelos, usuarios inactivos; salud de Auth/PostgreSQL/Redis/MongoDB/almacenamiento con timeout corto; cada sección falla por separado sin bloquear el resto. |
| 02 Familias | `/admin/instrument-families[...]` | `families.py`, `media.py` | CRUD sin borrado; código único; desactivar una familia del modelo activo exige confirmación (409 `family_in_active_model`); fotos `family_example` + `media_asset` (imagen, límites RF-AD-10, 5 MB por defecto). |
| 03 Procedimientos y catálogos | `/admin/procedures[...]`, `/admin/selection-catalogs/<phases|categories|reasons>` | `procedures.py`, `selection_catalogs.py` | Procedimientos con fases; **vínculos con kits de solo lectura** (D-4, propiedad SPD); fases/categorías/motivos con código único inmutable y sin borrado; fases activables. |
| 04 Avisos de privacidad | `/admin/privacy-notices[...]` | `privacy.py`, `media.py` | PDF/HTML por *magic bytes*, 10 MB por defecto, `privacy-notices/`, SHA-256 (duplicado = advertencia con confirmación), versión única, vigencia, activar/desactivar (opción de una sola activa). Versiones inmutables; nunca escribe `session_processing_agreement`. Descarga como adjunto con CSP `sandbox`. |
| 05 Usuarios | `/admin/users[...]` | `users.py`, `auth_sessions.py` | Reglas portadas de `identity_admin.py`: email único, roles de la institución, política de longitud (RF-AD-10), último `it_admin` activo (mismo *advisory lock* que AuthService), no auto-desactivarse; quirófanos solo para `or_charge`; revocación de sesiones. |
| 06 Roles | `/admin/roles[...]` | `roles.py` | Cuatro códigos fijos (migración 009); solo descripción; crear/borrar rechazado en UI, dominio y AuthService. |
| 07 Salas y estaciones | `/admin/configuration[...]` | `configuration.py` | Código de sala único por institución; estación siempre con sala de la institución; ROI `{"shape":"rect",x,y,w,h,frame_w,frame_h}` validado (dentro del encuadre). |
| 08 Modelos de visión | `/admin/vision-models[...]` | `vision_models.py`, `yolo_weights.py` | Paso 1: subir `.pt` (zip PyTorch), SHA-256, checksum duplicado rechazado, clases leídas **sin ejecutar el archivo** (o `names.yaml`/JSON). Paso 2: mapear o rechazar cada clase, advertencia de familias activas faltantes. Activación transaccional (un solo activo; sesiones iniciadas conservan `work_session.yolo_model_id`); no se deja cero activos con estaciones activas. |
| 09 Auditoría | `/admin/audit-log` | `audit_log.py` | Local, solo lectura, **filtro por institución en SQL** (filas sin institución nunca se muestran), filtros usuario/acción/entidad/resultado/búsqueda/rango de fechas, rango vacío no se consulta, paginación SQL. |
| 10 Configuración | `/admin/configuration`, `/admin/configuration/settings` | `settings.py`, `configuration.py` | Nombre/estado de la institución (no se puede desactivar la propia); parámetros persistentes por institución en `app_setting` (migración 010) con validación y auditoría `UPDATE_SETTING`. |

Nombres de auditoría (D-5): se mantiene la convención de la plataforma (`CREATE_/UPDATE_/DEACTIVATE_INSTRUMENT_FAMILY`, `PUBLISH_PRIVACY_NOTICE`, `PUBLISH_/ACTIVATE_VISION_MODEL`…), compartida con CatalogService y AuditService; equivalen a `catalog.family.upsert`, `privacy.notice.publish`, `vision.model.publish/activate` del RF.

## 3. Migraciones

`docker-compose.yml` y `docker-compose.test.yml` montan, en orden: 001, semillas 002–003, 004–008, **009_roles_fijos** (cuatro roles por institución) y **010_admin_configuracion** (`app_setting`, `yolo_model.class_names`). Ambas son idempotentes y no borran datos.

BD ya inicializada (respaldar antes; `initdb` no vuelve a correr):

```powershell
Get-Content data/migrations/009_roles_fijos.sql | docker compose exec -T postgres sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
Get-Content data/migrations/010_admin_configuracion.sql | docker compose exec -T postgres sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

## 4. Configuración

| Variable | Uso |
|---|---|
| `AUTH_SERVICE_URL` | Autenticación de todos los roles y revocación de sesiones. |
| `REDIS_URL` | Solo el ping de salud del dashboard. |
| `MONGO_URL` | Ping de salud (y evidencias del Operador, ya existente). |
| `ADMIN_STORAGE_BACKEND` | `local` (por defecto; volumen `web_admin_storage` en Docker, `instance/admin_storage` fuera) o `s3`. |
| `ADMIN_STORAGE_DIR` | Carpeta del backend local. |
| `ADMIN_S3_BUCKET`, `S3_INTERNAL_ENDPOINT`/`S3_ENDPOINT`, `S3_REGION`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Backend `s3` (Garage). Verificado (2026-11-03): la clave por defecto de Garage (`--default-bucket`) puede crear buckets, así que `pef-admin` se crea en el primer uso. Para la pila principal basta `ADMIN_STORAGE_BACKEND=s3` en `.env` (pendiente de probar en la pila completa). |
| `ADMIN_CATALOG_STEWARD_INSTITUTION_ID` | Opcional. UUID de la institución cuyos it_admin mantienen los catálogos compartidos; sin definir, cualquier it_admin (instalación de una institución). |

Los límites de archivos, la política de contraseñas y los umbrales KPI **no** son variables de entorno: se editan en Configuración → Application Parameters (por institución).

### 4.1 Pasar la pila principal a Garage (procedimiento seguro, no aplicado)

ADMIN-09 validó el backend `s3` en el entorno aislado. En la pila principal **no se cambió**: sigue en `local`. Para hacerlo sin perder datos:

1. Respaldar el volumen local: `docker run --rm -v <proyecto>_web_admin_storage:/src:ro -v ${PWD}/backup:/dst alpine tar czf /dst/admin_storage.tgz -C /src .`
2. En `.env`: `ADMIN_STORAGE_BACKEND=s3` y `ADMIN_S3_BUCKET=pef-admin`. La clave por defecto de Garage puede crear el bucket; si se usa otra clave, debe tener permiso de escritura en ese bucket.
3. Añadir `garage` (`condition: service_started`) a `depends_on` del servicio `web` (hallazgo H5) para que el portal no arranque antes que Garage.
4. `docker compose up -d web`. Subir una foto de prueba, `docker compose restart web garage` y comprobar que se sigue viendo.
5. Los archivos ya subidos con `local` se siguen leyendo del volumen: la lectura se decide por el bucket guardado en cada archivo (`local`). Solo los nuevos van a Garage. Por eso **no hay que quitar el volumen `web_admin_storage`** ni migrar nada. Ningún paso borra datos.
6. El cliente S3 del portal ya tolera los objetos multiparte de Garage (> 8 MB, defecto H7 corregido). Si otro consumidor (p. ej. el VisionWorker de P2) descarga objetos grandes con botocore ≥ 1.36, debe usar `response_checksum_validation='when_required'` y validar con el SHA-256 guardado (hallazgo H3).

`VisionWorker` sigue cargando sus pesos de `VISION_WEIGHTS_PATH`: activar un modelo en Administración define el modelo de las **nuevas** sesiones en la BD; desplegar ese `.pt` al worker es un paso de operación (no se notifica al worker, RF-AD-08 §4).

## 5. Pruebas

```powershell
python scripts/run_test_env.py prepare            # una vez: config de Garage de pruebas en .e2e/
docker compose -f docker-compose.test.yml --env-file .env.test up -d --wait
python scripts/run_test_env.py admin              # tests/test_admin_*.py + adaptadores (PostgreSQL/Redis/Mongo/Garage reales)
python scripts/run_test_env.py web                # suite completa del portal
python scripts/run_test_env.py auth               # AuthService
python scripts/run_test_env.py smoke              # smoke SQLite de Administración
python scripts/smoke_admin_test_env.py            # AuthService + portal reales: login/refresh/logout de 3 roles,
                                                  # pantallas Admin, alta de usuario -> login, desactivación y reset -> revocación
python scripts/audit_admin_integration.py          # Admin -> CatalogService / SessionService (misma BD, sin escribir sesiones)
python scripts/verify_weights_names.py <best.pt>  # RF-AD-08: clases del best.pt real (lector seguro; --ultralytics en la imagen del worker)
python scripts/run_admin_browser_env.py           # AuthService + portal en 15001/15000 para probar en el navegador
python scripts/browser_validate_admin.py --weights <best.pt>   # ADMIN-09: RF-AD-01..10 en Chromium (Playwright), evidencia en docs/evidencias/ADMIN-09/
```

Resultados y pendientes: `docs/ADMIN_MATRIZ_CUMPLIMIENTO.md` §10 (cierre) y §11 (auditoría de integración). Guía de verificación manual: `docs/ADMIN_GUIA_VALIDACION_MANUAL.md`.

## 6. Estado e integración (validación ADMIN-09, 2026-11-03)

| Estado | Alcance |
|---|---|
| Implementado | RF-AD-01 … RF-AD-10 |
| Pruebas automatizadas aprobadas | Los diez: Admin 222; Web 450 con las 3 fallas preexistentes del Operador; AuthService 172; smoke real 51/51; integración 9/9 |
| Pruebas en navegador (Chromium + Playwright, entorno aislado) | **Los diez aprobados**: 95/95, incluido RF-AD-08 con el `best.pt` real. Evidencia en `docs/evidencias/ADMIN-09/` |
| Verificado manualmente por una persona | Pendiente: visual, es-MX, tema oscuro, otro navegador (`docs/ADMIN_GUIA_VALIDACION_MANUAL.md` §3) |
| Pesos reales | Validación administrativa **aprobada** con un `best.pt` **provisional** (versión anterior del modelo, no la definitiva; `6215cb74…`, 17 clases que coinciden con Ultralytics). Repetir con los pesos definitivos. Inferencia real con esos pesos: pendiente de P1/P2 |
| Pendiente de integración o decisión (fuera de Administración) | P1 fijar el modelo por sesión; P2 que el worker cargue los pesos del modelo activo; P3 CatalogService todavía permite a it_admin escribir kits/piezas y familias; P4 altas con familias inactivas; P5 SessionService acepta fase/sala/estación inactivas; P6 avisos por institución y enlace de lectura; **P7 CSRF/`Origin` en el portal y `SameSite=Lax` por defecto en AuthService** |
| Limitaciones conocidas | Catálogos compartidos (definir el *steward* en multi-institución); solo checkpoints `.pt` zip; reset sin AuthService disponible deja sesiones hasta expirar (con aviso); `or_charge` sin pantallas en el portal; Garage no probado en la pila principal (§4.1) |

Datos compartidos con los servicios del Supervisor: todos usan la misma PostgreSQL. Lo que el Admin crea o desactiva lo ven CatalogService y SessionService al instante (verificado con `audit_admin_integration.py`). VisionWorker recibe el `class_map` del modelo activo en cada *bind* (verificado con el modelo real), pero carga sus pesos de un archivo local: ver P1 y P2.

## 7. MS-11: evaluaciones de modelos y fuentes simuladas

- **Modelos → Evaluaciones** (`/admin/vision-models/<id>/evaluations`, API local `GET|POST /vision-models/<id>/evaluations`):
  - Registro de conjunto de datos (nombre, versión, SHA-256), partición, imágenes/instancias, precisión, recall, mAP50, mAP50-95, métricas por clase y umbrales usados.
  - Los valores los **declara** el administrador; PEF no los calcula ni los completa, y una métrica no medida queda vacía.
  - Registros **inmutables** (*trigger* de 018): una corrección es una nueva versión del conjunto de datos.
  - Comparación con otros modelos **sobre el mismo conjunto de datos** (señala una regresión).
  - Historial del modelo (auditoría de creación, publicación, activación y evaluaciones) y sesiones fijadas.
  - Auditoría `RECORD_VISION_MODEL_EVALUATION`. Pruebas: `tests/test_ms11_model_evaluations.py` (13).
- **Cámaras**: tipo `simulated` ("Fuente simulada, solo pruebas, no es hardware"; migración 018).

## 8. MS-12: calibración de cámaras y corridas de regresión de modelos

**Calibración (ms-camaras).** *Configuración → Cámaras → Calibrar*. El técnico coloca un objeto de **longitud conocida** sobre el plano de la charola, mide su longitud **en píxeles** en un fotograma de la cámara y registra ambos valores con el tamaño del fotograma. PEF solo calcula `mm/px = longitud / píxeles`; no estima distorsión, altura ni ningún otro parámetro físico. Reglas: cámara activa y de la institución; el fotograma debe tener la resolución configurada; la longitud medida no puede superar la diagonal; 3 decimales. Historial **inmutable** (`capture_camera_calibration`); si la resolución cambia después, la calibración se marca "no coincide" y deja de enviarse a VisionWorker. Auditoría `CALIBRATE_CAPTURE_CAMERA`.

**Regresión (ms-modelos-ia).** *Modelos de visión → Evaluaciones → Corridas de regresión*. Se ejecuta fuera del portal, con inferencia real, en la imagen de VisionWorker:

```bash
docker run --rm -v <repo>/apps/VisionWorker:/app:ro -v <repo>/scripts:/scripts:ro -v <videos>:/videos:ro \
  -v <base.pt>:/models/baseline.pt:ro -v <candidato.pt>:/models/candidate.pt:ro -v <salida>:/out \
  -e VISION_ALLOW_UNPINNED_BIND=1 -e VISION_UPLOAD_DIR=/tmp/u -e TELEMETRY_SPOOL_DIR=/tmp/s \
  pef-ms-test/vision:latest python /scripts/model_regression.py --reference-set "conjunto-v1" \
  --baseline /models/baseline.pt --candidate /models/candidate.pt --out /out/report.json /videos/*.avi
```

El JSON se sube en la pantalla. PEF verifica que el SHA-256 candidato sea el del modelo y que el de la base sea un modelo registrado, **recalcula** la concordancia por video (totales y conteos por clase del primer y último fotograma) y el hash del conjunto de referencia, y guarda la corrida inmutable (`yolo_model_regression_run`, auditoría `RECORD_VISION_MODEL_REGRESSION`). Es **concordancia con la base, no exactitud**. Corrida real disponible: `docs/evidencias/MS-12/model_regression_best_vs_best.json` (único modelo real disponible usado como base y candidato: comprueba reproducibilidad, 3/3 videos idénticos).
