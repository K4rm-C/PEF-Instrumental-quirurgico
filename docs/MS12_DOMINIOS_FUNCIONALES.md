# MS-12 — Adaptación del PEF a los 20 dominios funcionales del profesor

Fecha: 2026-11-10. Rama `microservices_phase2`. Respaldo previo: commit **`949699d`** (MS-11). MS-12 queda **sin commit** y sin push.

**Fuente de los requisitos.** El documento original del profesor **no está en el repositorio**. La funcionalidad solicitada por dominio se toma del resumen registrado en `PLAN_TRABAJO.md` (lista MS-01…MS-08 con los puertos 5301–5320) y de las listas de los encargos MS-11 y MS-12. Donde el resumen no detalla una función, se marca "no especificado".

**Cómo leer los estados.** *Implementado y probado* = pruebas reales en el entorno aislado y en Docker Compose; no implica aceptación manual ni validación en un hospital. *Simulación* = fuente simulada identificada (video grabado, HAPI de pruebas). *Pendiente de hardware* / *de validación externa* / *de decisión funcional* según corresponda.

---

## 1. Correspondencia de puertos: propuestos (5301–5320) → reales (5001–5009)

Los 20 dominios **no** son 20 procesos (decisión de `PLAN_TRABAJO.md` §0.8). Se conservan los 9 servicios con sus puertos reales; el puerto 53xx es el identificador del dominio.

| Puerto propuesto | Dominio | Proceso real | Puerto real | Forma |
| :--- | :--- | :--- | :--- | :--- |
| 5301 | ms-auth | AuthService (`apps/BackendAuthService`) | 5001 | Propio |
| 5302 | ms-catalogos | CatalogService + Administración (portal) | 5003 / 5000 | Compartido |
| 5303 | ms-sesiones-conteo | Portal (Operador) + SessionService (Supervisor) | 5000 / 5004 | Compartido |
| 5304 | ms-evidencias | EvidenceService (+ Garage, MongoDB) | 5005 | Propio |
| 5305 | ms-auditoria | AuditService (consulta); cada servicio escribe | 5006 | Propio + transversal |
| 5306 | ms-notificaciones | NotificationsService + `notifications-worker` | 5007 | Propio |
| 5307 | ms-monitoreo | MonitoringService + `monitoring-collector` | 5008 | Propio |
| 5308 | ms-camaras | Administración + MonitoringService + VisionWorker (*bind*) | 5000 / 5008 / 5002 | Compartido |
| 5309 | ms-video-tiempo-real | VisionWorker (`/ws`) | 5002 | Compartido (latencia) |
| 5310 | ms-inferencia-vision | VisionWorker | 5002 | Compartido (latencia) |
| 5311 | ms-tracking-instrumental | VisionWorker + portal (persistencia) + SessionService (lectura) | 5002 / 5000 / 5004 | Compartido (latencia) |
| 5312 | ms-comparacion-inventario | Portal + SessionService | 5000 / 5004 | Compartido (transacción) |
| 5313 | ms-discrepancias | Portal + SessionService | 5000 / 5004 | Compartido (transacción) |
| 5314 | ms-validacion-humana | Portal (Operador) + SessionService (verificación del sello) | 5000 / 5004 | Compartido |
| 5315 | ms-supervision | Portal (pantallas del Supervisor) → microservicios | 5000 | Compartido |
| 5316 | ms-kits-charolas | CatalogService (+ SessionService para sesiones por charola) | 5003 | Compartido con catálogos |
| 5317 | ms-modelos-ia | Administración (portal) + almacén de modelos de VisionWorker | 5000 / 5002 | Compartido |
| 5318 | ms-reportes-indicadores | SessionService + portal | 5004 / 5000 | Compartido (mismas tablas) |
| 5319 | ms-sincronizacion | IntegrationService | 5009 | Compartido |
| 5320 | ms-integracion-hospitalaria | IntegrationService + `integration-worker` | 5009 | Compartido |

Diagrama: `docs/diagramas/ms12_dominios.drawio` (cada dominio dentro del proceso que lo aloja).

## 2. Delimitación de dominios (Fase 2) y justificación de los procesos compartidos

No se creó ningún proceso nuevo ni se reorganizó código entre servicios. Criterios aplicados:

| Agrupación | Dominios | Por qué no se separan |
| :--- | :--- | :--- |
| VisionWorker | video en tiempo real, inferencia, tracking | Comparten el fotograma en memoria, el modelo cargado en GPU/CPU y el reloj del video. Separarlos añadiría una serialización por fotograma (latencia) y otra copia del modelo. |
| Portal (Operador) + SessionService | sesiones/conteo, comparación, discrepancias, validación humana, reportes | Deben ser consistentes en una sola transacción de PostgreSQL (bloqueo de `work_session`, regla del final = referencia, discrepancias derivadas). El flujo del Operador vive en el monolito por decisión vigente; el Supervisor usa SessionService. |
| CatalogService | catálogos, kits, charolas, equivalencias | Mismo dueño de escritura (SPD/Administración), mismas tablas, mismo aislamiento institucional. |
| IntegrationService | sincronización, integración hospitalaria, lotes | Comparten autenticación de clientes máquina, alcances, auditoría y el *worker* de entregas. |
| Administración (portal) | cámaras (configuración y calibración), modelos de IA | Administración es el dueño de esos maestros; VisionWorker solo los recibe en el *bind* firmado. |
| AuthService | autenticación | Se conserva (restricción: no reemplazarlo). |

Aislamiento institucional: cada lectura y escritura nueva de MS-12 toma la institución del token (nunca del cuerpo) y responde 404 a recursos de otra institución.

## 3. Matriz de los 20 dominios

Columnas: servicio y puerto real · módulos · solicitado (resumen disponible) · implementado · faltante · endpoints y eventos · tablas · dependencias · pruebas · estado.

### ms-auth (5301)
- **Servicio:** AuthService :5001. **Módulos:** `apps/BackendAuthService` (login, refresh, logout, me, token de cliente).
- **Solicitado:** autenticación de los tres roles, JWT, refresh, logout, verificación, revocación en Redis, controles de institución/estación.
- **Implementado:** todo lo anterior; clientes máquina (`client_credentials`) con alcances leídos de PostgreSQL en cada petición.
- **Faltante:** firma asimétrica y rotación de claves (propuesta, no aplicada).
- **Endpoints/eventos:** `POST /api/v1/auth/login|refresh|logout|token`, `GET /api/v1/auth/me`. **Tablas:** `user`, `role`, `user_role`, `integration_client`; Redis `auth:jwt:*`.
- **Dependencias:** PostgreSQL, Redis. **Pruebas:** suite Auth; E2E Compose 0, D, G. **Estado:** **Implementado y probado.**

### ms-catalogos (5302)
- **Servicio:** CatalogService :5003 + Administración (portal :5000). **Módulos:** `services/catalog_service.py`, `management_service.py`; `admin_local/catalog_remote.py`.
- **Solicitado:** familias/tipos/modelos, dueño de escritura por entidad.
- **Implementado:** familias, categorías, estados de ciclo, instrumentos, kits versionados; dueños: IT admin (maestros), SPD (kits/charolas).
- **Faltante:** ninguno técnico.
- **Endpoints:** `/api/v1/catalog/(categories|cycle-statuses|families|instruments|kits|trays)`. **Tablas:** `instrument_family`, `instrument`, `kit`, `kit_item`, catálogos.
- **Pruebas:** suite Catalog; Admin. **Estado:** **Implementado y probado.**

### ms-sesiones-conteo (5303)
- **Servicio:** portal (Operador) + SessionService :5004 (Supervisor). **Módulos:** `rf_session.py`; `session_planning.py`, `session_review.py`, `session_writes.py`.
- **Solicitado:** estados y versiones, conteos.
- **Implementado:** programación, inicio, conteo inicial (primer fotograma), final = referencia vigente (MS-03.1, sin cambios), contingencia, cierre del Operador y cierre seguro del SPD.
- **Faltante:** cierres excepcionales (D1, D2).
- **Endpoints:** portal `/operator/sessions/*`; `/api/v1/sessions/*`. **Eventos:** `session.scheduled`, `session.initial_count`, `session.final_count`, `session.close`… (`access_audit`). **Tablas:** `work_session`, `count_event`, `expected_inventory`.
- **Pruebas:** web, Session, E2E Compose 2–13. **Estado:** **Implementado y probado** (D1/D2 pendientes de decisión).

### ms-evidencias (5304)
- **Servicio:** EvidenceService :5005. **Módulos:** `apps/EvidenceService`; outbox del portal `mongo_checkpoints.py`.
- **Solicitado:** imágenes, videos, hashes, trazabilidad, recuperación.
- **Implementado:** imagen por *checkpoint* con SHA-256, modelo, usuario, estación y hora; URL firmada; checkpoint DS06 en MongoDB. **MS-12:** la subida se reintenta durante una **ventana de tiempo** (10 min) en lugar de 5 intentos (~15 s): una caída de EvidenceService ya no pierde la imagen.
- **Faltante:** el video completo no se conserva (decisión de privacidad P6: solo fotogramas).
- **Endpoints:** `/api/v1/sessions/{id}/evidence`, `/api/v1/evidence/{id}`. **Tablas:** `media_asset`; Garage; MongoDB `checkpoint_frame`.
- **Pruebas:** Evidence; web `test_ms12_evidence_recovery`; E2E Compose 11, G, **M9**. **Estado:** **Implementado y probado.**

### ms-auditoria (5305)
- **Servicio:** AuditService :5006 (consulta) + escritura en cada servicio. **Módulos:** `apps/AuditService`; `scripts/check_audit_consistency.py` (**MS-12**).
- **Solicitado:** trazabilidad.
- **Implementado:** `access_audit` con actor (usuario o cliente), institución, recurso, resultado, correlación, IP. **MS-12:** verificación de **consistencia entre servicios** (institución del actor y del recurso, duplicados; estilos de nombres contados).
- **Faltante:** unificación de nombres de acciones (D-5, `UPPER_SNAKE` y `punto.minúsculas` coexisten).
- **Endpoints:** `GET /api/v1/audit/events`. **Tablas:** `access_audit`.
- **Pruebas:** Audit; E2E Compose 19, **M10**. **Estado:** **Implementado y probado.**

### ms-notificaciones (5306)
- **Servicio:** NotificationsService :5007 + `notifications-worker`. **Solicitado:** avisos y recuperación.
- **Implementado:** asignación, revisión, discrepancias, correcciones, cierres, estaciones/cámaras, escalamientos, alertas de componentes; outbox con *triggers*.
- **Faltante:** proveedor SMTP real. **Endpoints:** `/api/v1/notifications`. **Tablas:** `notification`, `notification_outbox`.
- **Pruebas:** Notifications; E2E Compose 14, A, E. **Estado:** **Implementado y probado** (correo: pendiente de validación externa).

### ms-monitoreo (5307)
- **Servicio:** MonitoringService :5008 + `monitoring-collector`. **Solicitado:** salud, errores, latencia; salud de colas, *workers* y bases (MS-12).
- **Implementado:** 15 componentes; transiciones de disponibilidad. **MS-12:** se monitorean **IntegrationService** y su **worker de entregas** (conexión real, entregas vencidas, fallidas en 24 h) — antes no estaban; PostgreSQL informa conexiones usadas / máximas.
- **Faltante:** ninguno técnico. **Endpoints:** `/api/v1/monitoring/*`. **Tablas:** `monitoring_run`, `monitoring_check`, `service_event`.
- **Pruebas:** Monitoring (`test_ms12_probes`); E2E Compose 15, E, **M7**. **Estado:** **Implementado y probado** (disponibilidad técnica).

### ms-camaras (5308)
- **Servicio:** Administración + MonitoringService + VisionWorker. **Solicitado:** configuración, ROI/calibración, asociación a estación.
- **Implementado:** cámaras por estación, resolución, FPS, ROI, tipo `simulated`, *bind* firmado. **MS-12:** **calibración medida por un técnico** (longitud de referencia en mm y su medida en píxeles → mm/px; historial inmutable; se informa si deja de coincidir con la resolución; llega al worker solo si coincide, como dato informativo).
- **Faltante:** hardware físico; corrección de distorsión (no definida; no se inventa).
- **Endpoints:** `/admin/configuration/cameras/{id}/calibration`; Admin API `/cameras/{id}/calibrations`. **Tablas:** `capture_camera`, `capture_camera_calibration`.
- **Pruebas:** web `test_ms12_camera_calibration`; Vision `test_ms12_camera_calibration`; E2E **M1**, **M4**. **Estado:** **Probado mediante simulación** · **Pendiente de hardware físico**.

### ms-video-tiempo-real (5309)
- **Servicio:** VisionWorker `/ws`. **Implementado:** WSS con ticket HMAC y Origin, revocación, reconexión con espera exponencial y nuevo *bind*.
- **Faltante:** cámara física a través de la VM real. **Pruebas:** Vision, E2E Compose F. **Estado:** **Implementado y probado** (hardware pendiente).

### ms-inferencia-vision (5310)
- **Servicio:** VisionWorker. **Implementado:** YOLO con el modelo fijado por sesión (SHA-256), mapeo de clases, resultados persistidos.
- **Faltante:** modelo definitivo. **Pruebas:** Vision (inferencia real con `best.pt`), E2E 7–9. **Estado:** **Implementado y probado** con el modelo provisional.

### ms-tracking-instrumental (5311)
- **Servicio:** VisionWorker (`tracking.py`) + portal + SessionService. **Solicitado:** seguimiento estable sin duplicar; bitácora de eventos y trazabilidad (MS-12).
- **Implementado:** IDs estables, oclusión, cambio de clase, ROI. **MS-12:** la **bitácora completa** del recorrido aceptado se persiste (`tracking_event`, inmutable, ligada al conteo final; se lee del worker paginada; si no responde se guarda la copia acotada y se marca incompleta) y el Supervisor la consulta.
- **Endpoints:** `GET /api/v1/sessions/{id}/tracking-events` (Supervisor). **Tablas:** `tracking_event`.
- **Pruebas:** web `test_ms12_tracking_events`; Session `test_ms12_tracking_events`; E2E **M5**. **Estado:** **Implementado y probado** (nunca es un conteo).

### ms-comparacion-inventario (5312)
- **Implementado:** esperado, inicial, referencia vigente, final, clasificación, incorporaciones. **Pruebas:** web, Session, E2E MS-03.1. **Estado:** **Implementado y probado.**

### ms-discrepancias (5313)
- **Implementado:** creación, clasificación, estados, evidencia, escalamiento, resolución, auditoría, avisos. **Faltante:** resolución tras escalamiento (D2); uso clínico de equivalentes (D6).
- **Pruebas:** web, Session, E2E 13, A. **Estado:** **Implementado y probado** (D2/D6 pendientes).

### ms-validacion-humana (5314)
- **Servicio:** portal (Operador) + SessionService. **Solicitado:** firmas y evidencia de validación humana (MS-12).
- **Implementado:** corrige solo la clasificación; registra usuario, fecha, motivos. **MS-12:** **sello de integridad SHA-256** del contenido validado (líneas, conteo final, hash del fotograma) en el `manual_close`; SessionService lo **recalcula** (`intact` / `altered` / `not_sealed` / `no_validation`); visible al Supervisor.
- **Faltante:** **firma** con valor legal o re-autenticación (D7).
- **Endpoints:** `GET /api/v1/sessions/{id}/validation-seal`. **Pruebas:** web y Session `test_ms12_validation_seal` (mismo vector de prueba); E2E **M6**. **Estado:** **Implementado y probado** (firma: pendiente de decisión).

### ms-supervision (5315)
- **Implementado:** bandeja, revisión, evidencias, aprobaciones, escalamientos, historial. **MS-12:** sección *Integridad de la validación y bitácora de seguimiento* en el detalle de la sesión; pantalla *Equivalencias de kit*.
- **Pruebas:** web `test_ms12_supervisor_integrity`, `test_ms12_kit_equivalences_portal`; E2E 12–16. **Estado:** **Implementado y probado.**

### ms-kits-charolas (5316)
- **Servicio:** CatalogService. **Solicitado:** composiciones versionadas y **equivalencias**; instrumentos equivalentes autorizados (MS-12).
- **Implementado:** kits versionados, charolas con historial. **MS-12:** **equivalencias autorizadas** por kit (SPD; versión activa; la familia equivalente no está en el kit y está activa; una autorización activa; revocación con historial; auditoría). **No** cambian cantidades ni la regla del final.
- **Faltante:** ciclo de la charola (D4); uso clínico de la equivalencia (D6).
- **Endpoints:** `GET|POST /api/v1/catalog/kits/{id}/equivalences`, `DELETE …/{equivalence_id}` (publicados en el Gateway). **Tablas:** `kit_item_equivalence`.
- **Pruebas:** Catalog `test_ms12_equivalences`; web; E2E **M3**. **Estado:** **Implementado y probado** (D4/D6 pendientes).

### ms-modelos-ia (5317)
- **Servicio:** Administración + VisionWorker. **Solicitado:** métricas, datasets, **regresión**, despliegue/rollback; versionado y pruebas de regresión (MS-12).
- **Implementado:** versiones, SHA-256, clases, activación (= despliegue) y desactivación (= *rollback*), fijación por sesión, evaluaciones declaradas. **MS-12:** **corridas de regresión** con inferencia real (`scripts/model_regression.py`, imagen de VisionWorker): primer y último fotograma de videos de referencia con el candidato y con un modelo base; PEF verifica ambos SHA-256 y **recalcula** la concordancia. Es **concordancia, no exactitud**.
- **Faltante:** segundo modelo real para comparar (solo existe `best.pt`; la corrida real es candidato = base, es decir, de reproducibilidad); métricas reales del modelo definitivo.
- **Endpoints:** `/admin/vision-models/{id}/evaluations` (+ formulario de regresión); Admin API `POST /vision-models/{id}/regressions`. **Tablas:** `yolo_model_regression_run`.
- **Pruebas:** web `test_ms12_model_regressions`; corrida real (`docs/evidencias/MS-12/model_regression_best_vs_best.json`); E2E **M2**. **Estado:** **Implementado y probado** (comparación entre dos modelos distintos: **pendiente de un segundo modelo**).

### ms-reportes-indicadores (5318)
- **Implementado:** sesiones, conteos, discrepancias, intervenciones, tiempos, CSV/PDF; nunca precisión de IA. **Pruebas:** Session, E2E 16. **Estado:** **Implementado y probado.**

### ms-sincronizacion (5319)
- **Implementado:** API incremental con cursores firmados, idempotencia, conflictos, revocación. **Faltante:** clientes Android y escritorio (no existen; §6). **Pruebas:** Integration, E2E 17. **Estado:** **Implementado y probado** (API).

### ms-integracion-hospitalaria (5320)
- **Servicio:** IntegrationService + `integration-worker`. **Solicitado:** JSON/XML, exportación y trazabilidad de lotes (MS-12).
- **Implementado:** FHIR R4 de lectura, entregas con reintentos e idempotencia. **MS-12:** **lotes trazables** de sesiones cerradas (`pef-session-batch/1`, JSON o XML, sin datos de paciente ni médicos), bytes exactos con SHA-256, sesiones incluidas, quién y cuándo, auditoría de creación y de cada descarga; para el IT admin o un cliente máquina con `export.sessions.batch`.
- **Faltante:** HIS real, mapeo aprobado (D5), destino de los lotes (D8).
- **Endpoints:** `POST|GET /api/v1/integration/exports`, `GET …/{id}`, `GET …/{id}/content` (publicados en el Gateway). **Tablas:** `integration_export_batch`, `integration_export_item`.
- **Pruebas:** Integration `test_ms12_exports`; E2E 18, **M8**. **Estado:** **Probado mediante simulación** (HAPI local) · **Pendiente de validación externa** (D5, D8).

## 4. Contratos por dominio (nuevos en MS-12)

Convenciones comunes (DS09): `/api/v1/`, JWT Bearer validado en cada servicio (revocación en Redis), JSON por defecto y XML con `Accept: application/xml`, `X-Correlation-ID`, errores `{error, message, correlation_id, details}`. Todos son **síncronos** salvo los indicados.

| Dominio | Operación | Entrada | Salida | Validaciones | Permiso | Errores | Dueño del dato | XSD / OpenAPI |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| ms-kits-charolas | `GET /api/v1/catalog/kits/{id}/equivalences?status=active\|all` | — | lista `Equivalence` | institución | cualquier rol autenticado | 400, 401, 404 | CatalogService | `catalog.xsd` `equivalences`; `openapi.yaml` |
| ms-kits-charolas | `POST /api/v1/catalog/kits/{id}/equivalences` | `family_id`, `equivalent_family_id`, `notes` (≤300) | `Equivalence` 201 | kit activo; familia del kit; equivalente activa, distinta y fuera del kit; una autorización activa | `spd_supervisor` | 403, 404, 409 `equivalence_exists`, 422 `kit_inactive` / `family_not_in_kit` / `equivalent_in_kit` / `equivalent_unavailable` / `same_family` | CatalogService | `catalog-requests.xsd` `equivalence` |
| ms-kits-charolas | `DELETE …/equivalences/{eid}` | — | `Equivalence` (revocada) | idempotente; conserva historial | `spd_supervisor` | 403, 404 | CatalogService | ídem |
| ms-camaras | Admin `POST /cameras/{id}/calibrations` (portal `/admin/configuration/cameras/{id}/calibration`) | `reference_length_mm`, `measured_pixels`, `frame_width`, `frame_height`, `notes` | calibración 201 | cámara activa y propia; fotograma = resolución configurada; longitud medida ≤ diagonal; 3 decimales | `it_admin` | 400 (códigos en `messages.py`), 404 | Administración | interno (portal) |
| ms-tracking-instrumental | `GET /api/v1/sessions/{id}/tracking-events?offset&limit` | — | `TrackingEvents` (resumen, eventos, `complete`) | institución; `limit` 1–500 | `spd_supervisor` | 400, 403, 404 | portal escribe, SessionService lee | `session-reports.xsd` `tracking_events`; `openapi.yaml` |
| ms-validacion-humana | `GET /api/v1/sessions/{id}/validation-seal` | — | `ValidationSeal` (`intact`/`altered`/`not_sealed`/`no_validation`) | recalcula desde las filas | `spd_supervisor` | 403, 404 | portal escribe el sello, SessionService verifica | `session-reports.xsd` `validation_seal` |
| ms-modelos-ia | Admin `POST /vision-models/{id}/regressions` | `report` (JSON `pef-model-regression/1`), `notes` | resumen 201 | formato; SHA-256 candidato = este modelo; base registrada; totales = suma por clase; video único; fecha no futura | `it_admin` (*steward*) | 400 (`invalid_regression_report`, `regression_model_mismatch`, `regression_baseline_unknown`…) | Administración | interno |
| ms-integracion-hospitalaria | `POST /api/v1/integration/exports` | `period_from`, `period_to`, `format` json\|xml | `ExportBatch` 201 | periodo ≤366 días, no futuro; ≤2000 sesiones; al menos una | `it_admin` o alcance `export.sessions.batch` | 400, 403, 415, 422 `validation_error` / `empty_batch` | IntegrationService | `integration.xsd` `export_batch`; `openapi.yaml` |
| ms-integracion-hospitalaria | `GET /api/v1/integration/exports[?session_id]`, `GET …/{id}`, `GET …/{id}/content` | — | lista / lote / documento exacto (`X-Content-SHA256`) | institución | ídem | 404 | IntegrationService | `export_list` |
| ms-monitoreo | componentes `integration`, `integration-worker` en `/api/v1/monitoring/*` | — | estado, dependencias, detalles | — | `it_admin`/`spd_supervisor` (portal) | — | MonitoringService | enumeración en `openapi.yaml` |

**Eventos (asíncronos)** — nuevas acciones de auditoría: `AUTHORIZE_KIT_EQUIVALENCE`, `REVOKE_KIT_EQUIVALENCE`, `CALIBRATE_CAPTURE_CAMERA`, `RECORD_VISION_MODEL_REGRESSION`, `EXPORT_SESSION_BATCH`, `DOWNLOAD_SESSION_BATCH`. El *outbox* de evidencias del portal es asíncrono (ventana de reintento de 10 min). Los contratos públicos anteriores **no cambiaron**; el único cambio visible en uno existente es el campo nuevo `camera.calibration` (nullable) en el *bind* interno portal → VisionWorker, que el worker filtra por tipo.

## 5. Matriz de pruebas por dominio (Fase 6)

Tipos: **U** unitaria · **I** integración con PostgreSQL/Redis/MongoDB reales · **E2E** Docker Compose integral · **IR** inferencia real con `best.pt` · **S** simulación identificada · **HW** pendiente de hardware · **EXT** pendiente de validación externa.

| Dominio | U | I | E2E Compose | IR | S | HW | EXT |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| ms-auth | ✔ | ✔ | 0, D, G | | | | |
| ms-catalogos | ✔ | ✔ | 17 | | | | |
| ms-sesiones-conteo | ✔ | ✔ | 2–13 | ✔ | video grabado | cámara física | |
| ms-evidencias | ✔ | ✔ | 11, G, M9 | ✔ | | | |
| ms-auditoria | | ✔ | 19, M10 | | | | |
| ms-notificaciones | ✔ | ✔ | 14, A, E | | | | SMTP |
| ms-monitoreo | ✔ | ✔ | 15, E, M7 | | | | |
| ms-camaras | ✔ | ✔ | 1, 4, M1, M4 | | fuente `simulated` | ✔ | |
| ms-video-tiempo-real | ✔ | ✔ | F | | | ✔ | |
| ms-inferencia-vision | ✔ | | 7–9 | ✔ | | | modelo definitivo |
| ms-tracking-instrumental | ✔ | ✔ | 6, M5 | ✔ | secuencias sintéticas | | |
| ms-comparacion-inventario | ✔ | ✔ | 8–10 | ✔ | | | |
| ms-discrepancias | ✔ | ✔ | 13, A | | | | |
| ms-validacion-humana | ✔ | ✔ | 10, M6 | | | | firma (D7) |
| ms-supervision | ✔ | ✔ | 12–16 | | | | |
| ms-kits-charolas | ✔ | ✔ | 17, M3 | | | | |
| ms-modelos-ia | ✔ | ✔ | 1, M2 | ✔ (regresión real) | | | 2.º modelo |
| ms-reportes-indicadores | ✔ | ✔ | 16 | | | | |
| ms-sincronizacion | ✔ | ✔ | 17 | | | | apps cliente |
| ms-integracion-hospitalaria | ✔ | ✔ | 18, M8 | | HAPI de pruebas | | HIS real (D5, D8) |

**No se declara** validación física de cámaras sin hardware real ni integración clínica FHIR sin un hospital autorizado.

## 6. Aplicaciones cliente: escritorio de la estación y Android (Fase 8)

**Estado: ninguna de las dos existe en el repositorio** (no hay proyectos Android, .NET, Electron, Flutter ni similares). Lo que existe es la API que podrían usar.

| Necesidad | App de escritorio (estación del Operador) | App Android (Supervisor / consulta) | Cubierto por |
| :--- | :--- | :--- | :--- |
| Iniciar sesión, renovar, salir | ✔ | ✔ | AuthService `login/refresh/logout` (tokens en el cuerpo) |
| Catálogo y ubicaciones sin conexión | ✔ | ✔ | Sync `GET /api/v1/sync/resources|changes` (cursores firmados) |
| Lista y detalle de sesiones | ✔ | ✔ | SessionService (lecturas para Operador y Supervisor) |
| Captura, conteo inicial/final, validación | **Falta** | — | El flujo del Operador vive en el portal; los endpoints de escritura del Operador en SessionService responden **410** por diseño. Una app de estación necesitaría una API JSON del flujo del portal o embebir el portal. |
| Video en tiempo real | **Falta** (ticket WS solo vía portal) | — | `/capture/ws-ticket` del portal (cookie + CSRF) |
| Programar sesión | — | ✔ | Sync `session.schedule` o `POST /api/v1/sessions/schedule` |
| Revisar y resolver discrepancias | — | ✔ (API) | SessionService `resolve`, `close` |
| Evidencias | — | ✔ | EvidenceService (URL firmada) |
| Avisos | — | parcial | `GET /api/v1/notifications` + `notification.read`; **no hay push** (FCM/APNs) |
| Bitácora, sello, equivalencias | — | ✔ | MS-12: `tracking-events`, `validation-seal`, `kits/{id}/equivalences` |
| XML (escritorio) | ✔ | — | Todos los servicios negocian `application/xml` (DS09) |

Faltantes para construirlas (no se desarrollaron, por instrucción): API JSON del flujo de captura del Operador (hoy solo HTML), emisión de ticket WS por API, notificaciones *push*, empaquetado y firma de las apps, gestión de la estación (registro del equipo).

## 7. Restricciones respetadas

Docker Compose sigue siendo el entorno principal; nada en Google Cloud; ningún contenedor artificial (ningún proceso nuevo); reglas de conteo sin cambios (la regla final = referencia no se tocó; equivalencias, bitácora y sello no alteran totales); AuthService no se reemplazó; Legacy intacto; pesos YOLO sin modificar (solo lectura); RF-AD-11 no implementado; responsabilidades de Administrador, Operador y Supervisor sin cambios; sin políticas clínicas inventadas (D6–D8 documentadas); D1–D5 sin implementar; sin push ni commits posteriores al respaldo.
