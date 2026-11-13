# MS-00 — Diagnóstico de microservicios y plan de la siguiente fase

**Fecha:** 2026-11-03 · **Rama:** `admin-01` · **Alcance:** solo diagnóstico y planificación. No se implementó ninguna funcionalidad.

**Método.** Todo lo que sigue sale de inspeccionar el código, las rutas, los Dockerfile, `docker-compose*.yml`, las migraciones y `data/DataStandards`, y de **ejecutar las pruebas de cada servicio** contra el entorno aislado `pef_admin_test` (PostgreSQL, Redis, MongoDB y Garage de prueba). El VisionWorker se probó dentro de su imagen ya construida (`pef-build-check/vision`), con el código en solo lectura. La arquitectura objetivo se contrastó con `docs/Diagramas de Arquitectura.docx`. Ningún estado se tomó solo de la documentación.

Estados usados: **Implementado** (código + pruebas + consumidor real) · **Parcial** (existe, pero le falta una parte o un consumidor) · **Simulado** (solo datos de demostración) · **No implementado**.

---

## 1. Inventario real de servicios

### 1.1 Resumen

| Servicio | Carpeta | Puerto (Compose) | Prefijo API | Almacenamiento | Pruebas ejecutadas (2026-11-03) | Consumidor real | Estado |
|---|---|---|---|---|---|---|---|
| AuthService | `apps/BackendAuthService` | 5001 (público) | `/login`, `/verify`, `/refresh`, `/logout`, `/api/v1/auth/*` | PostgreSQL (`user`, `role`, `user_role`, `access_audit`) + Redis (`auth:jwt:{jti}`) | **172 aprobadas** | Portal (los 3 roles), todos los microservicios (vía Redis) | Implementado |
| CatalogService | `apps/CatalogService` | 5003 (solo loopback) | `/api/v1/catalog` | PostgreSQL | **179 aprobadas** | Portal: piezas y kits del Supervisor SPD (`CATALOG_SERVICE_URL`) | Implementado (P3, P4) |
| SessionService | `apps/SessionService` | 5004 (loopback) | `/api/v1/sessions` | PostgreSQL | **697 aprobadas** | Portal: todo el flujo del Supervisor + cierre del Operador (`operator-close`) + reportes | Implementado (P5) |
| EvidenceService | `apps/EvidenceService` | 5005 (loopback) | `/api/v1/evidence`, `/api/v1/sessions/<id>/evidence` | PostgreSQL (`media_asset`) + Garage S3 | **323 aprobadas, 11 omitidas** (piden credenciales Garage en el `.env` del repo) | Operador: subida de cuadros desde *checkpoints* (`mongo_checkpoints.py`) | Parcial: el Supervisor no lo consulta |
| AuditService | `apps/AuditService` | 5006 (loopback) | `/api/v1/audit` | PostgreSQL (`access_audit`, solo lectura) | **48 aprobadas** (con `pyyaml` de `requirements-dev`) | Portal: bitácora del Supervisor (`/supervisor/audit-log`) | Implementado (lectura) |
| NotificationsService | `apps/NotificationsService` (+ `notifications-worker`) | 5007 (loopback) | `/api/v1/notifications` | PostgreSQL (`notification`, `notification_outbox`, migración 006) | **40 aprobadas** | Portal: campana y `/notifications` | Implementado (solo dentro de la aplicación) |
| MonitoringService | `apps/MonitoringService` (+ `monitoring-collector`) | 5008 (loopback) | `/api/v1/monitoring` | PostgreSQL (`monitoring_run`, migración 007) + MongoDB | **41 aprobadas** | Portal: `/admin/system-status`, `/supervisor/services-health` | Implementado |
| VisionWorker | `apps/VisionWorker` | 5002 (loopback) | `/health`, `/sessions/<id>/{bind,unbind,video,process-video,state,timeline,cancel}`, **`/ws`** | Memoria + disco (`/data/uploads`) + MongoDB (telemetría) | **17 aprobadas, 4 omitidas** (MongoDB) | Portal (Operador): *bind*, video y WebSocket de captura | Implementado (P1, P2) |
| Portal BackendWebFlask (monolito) | `apps/BackendWebFlask` | 5000 (público) | HTML + `/operator/*`, `/supervisor/*`, `/admin/*` | PostgreSQL, Redis, MongoDB, almacenamiento Admin | **450 aprobadas / 3 fallidas** (Operador, preexistentes en `b417170`) | Navegador | Implementado |

**Fuera del Compose: no existe ningún API Gateway.** El documento de arquitectura pide uno para Android (JSON) y escritorio (XML), pero no hay servicio de enrutamiento (nginx, traefik u otro). Todos los microservicios publican su puerto **solo en `127.0.0.1`**, así que hoy ningún cliente externo puede consumirlos. Lo que sí está listo es la negociación JSON/XML: hay serializadores XML y XSD en Auth, Catalog, Session, Audit, Notifications y Monitoring.

### 1.2 Detalle por servicio

**AuthService.**
- Login, `/verify`, refresh, logout, cambio y recuperación de contraseña.
- Identidad administrativa en `/api/v1/auth/users|roles|permissions`; revocación por usuario en `sessions/revoke`.
- JWT HS256 con secreto compartido. Las sesiones y revocaciones viven en Redis.
- Implementado y probado. Incompleto:
  - No hay autenticación máquina a máquina (*client credentials*) para `integration_client` (DS08).
  - Siguen expuestos los endpoints `POST/DELETE /roles`, aunque se rechazan para los cuatro roles fijos.
  - El valor por defecto de `AUTH_COOKIE_SAMESITE` en el código es `None` (P7).

**CatalogService.**
- Lee categorías, ciclos, familias, piezas y kits.
- Escritura: familias solo para `it_admin`; piezas y kits para `it_admin` **y** `spd_supervisor`.
- Valida el JWT y la revocación en Redis; el alcance institucional aplica a piezas y kits.
- Lo usa el Supervisor para piezas y kits. Las familias las administra Administración en el monolito, así que el CRUD de familias de CatalogService queda duplicado (P3).

**SessionService.** Es el núcleo del Supervisor:
- Programación (`/schedule`), edición, aviso de privacidad, inventario esperado, inicio y cierre.
- Discrepancias (`/discrepancies`, `/resolve`), cierre del Operador (`/operator-close`) y reportes (`/reports/summary|kits|operators|options`).
- `vision-counts` y `count-report` están implementados y probados, **pero el portal no los llama**: el conteo del Operador vive en el monolito (`services/rf_session.py`). Son contratos para clientes externos.
- Escribe en `access_audit` y en `count_event`; los disparadores alimentan `notification_outbox`.

**EvidenceService.**
- Subida de imágenes (*magic bytes*, SHA-256, límite de tamaño) a Garage, registro en `media_asset` y listado por sesión.
- Trabajos de reconciliación y limpieza (`jobs/`).
- Consumidor real: los *checkpoints* del Operador.
- La pantalla "Evidence & Traceability" del Supervisor **solo existe para la sesión demo WS-026** (`view_data.v3_evidence` → `demo_data.WS026_EVIDENCE`); con una sesión real muestra un estado vacío. **La consulta de evidencias por el Supervisor está simulada.**

**AuditService.**
- API de solo lectura sobre `access_audit` (eventos, detalle, resumen), en JSON o XML.
- La escritura está repartida: cada servicio inserta directamente en `access_audit` mediante su propio `audit_adapter`. Es un modelo válido de tabla compartida, pero no hay un contrato de esquema único aplicado por código.

**NotificationsService.**
- Disparadores en `access_audit` y `count_event` → `notification_outbox` → `worker.py` (`FOR UPDATE SKIP LOCKED`, reintentos y reconciliación) → `notification`.
- API de lectura y de marcar como leída.
- **Sin canales externos** (correo, push, SMS): solo notificaciones dentro de la aplicación.

**MonitoringService.**
- El *collector* sondea `/health/*` de todos los servicios y guarda el historial (PostgreSQL y MongoDB); la API da el resumen, el historial y el estado por servicio.
- Lo consumen Admin y Supervisor.
- **No incluye cámaras ni estaciones.**

**VisionWorker.**
- YOLO (Ultralytics) cargado **una sola vez** desde `VISION_WEIGHTS_PATH` (`inference.get_model`).
- *Bind* con el inventario esperado y el `class_map` enviado por el portal.
- Proceso de video subido y canal **WebSocket `/ws`** con *ticket* HMAC (`VISION_WS_SECRET`, emitido por el portal en `/operator/sessions/<id>/capture/ws-ticket`).
- *Tracking* IoU + EMA de clases (`tracks.py`) y telemetría en MongoDB.
- El `model_version` que llega en el *bind* es **solo una etiqueta**: el modelo que infiere es el del archivo local (P2).

**Módulos internos del monolito relacionados con microservicios** (se quedan así por las reglas de arquitectura):
- Conteo, validación humana (`/operator/sessions/<id>/vision-validation`), reglas de cierre (`count_reference`, `close_blocked`) y captura con cámara del navegador (`static/js/pages/capture-live-ws.js`, `capture-rf.js`) en `rf_session.py` y `controllers/routes.py`.
- `services/vision_bridge.py` (*bind*) y `services/mongo_checkpoints.py` (*outbox* hacia MongoDB y EvidenceService).
- Modo dual del Supervisor: con `SESSION_SERVICE_URL` usa SessionService; sin ella, la implementación local `rf_session.py`, que solo es un respaldo de desarrollo.

---

## 2. Matriz de las 20 responsabilidades

| # | Responsabilidad | Quién la cubre hoy | Estado | Qué falta |
|---|---|---|---|---|
| 1 | Auth e identidad | AuthService (+ Admin en el monolito sobre las mismas tablas) | Implementado | *Client credentials* para clientes máquina; `SameSite=Lax` por defecto (P7) |
| 2 | Catálogos | Admin monolito (familias, procedimientos, catálogos) + CatalogService (lectura; piezas y kits) | Implementado | P3 y P4 resueltas (MS-01); falta la confirmación del modelo activo al desactivar una familia por la API |
| 3 | Sesiones y conteo | SessionService (Supervisor) + monolito `rf_session` (Operador) | Implementado | P1 (modelo por sesión); P5 resuelta (MS-01) |
| 4 | Evidencias | EvidenceService | **Implementado (MS-03)** | Las imágenes reales llegan al Supervisor; queda la retención extendida desde la pantalla (sin cambios) |
| 5 | Auditoría | `access_audit` (escritura distribuida) + AuditService (lectura) | Implementado | Contrato de evento común; auditoría de acceso a evidencias |
| 6 | Notificaciones | NotificationsService + worker | Parcial | Canales externos (correo como mínimo, opcional) y reglas configurables |
| 7 | Monitoreo | MonitoringService + collector | Implementado | Estado de cámaras y estaciones; alertas hacia Notificaciones |
| 8 | Cámaras | `capture_station` + ROI (Admin) + cámara del navegador | Parcial | Registro y estado de cámaras (latido), selección de dispositivo, reporte a Monitoreo |
| 9 | Video en tiempo real y WebSockets | VisionWorker `/ws` + ticket HMAC (DS10) | Implementado | Gateway WSS/TLS para despliegue remoto |
| 10 | Inferencia de visión | VisionWorker (YOLO) | Implementado | P2 (pesos del modelo activo) |
| 11 | Tracking de instrumental | VisionWorker `tracks.py` (IoU + EMA) | Parcial | IDs de track persistidos como evidencia y métricas de estabilidad; tracker robusto opcional |
| 12 | Comparación de inventario | SessionService (`expected-inventory`) + `rf_session` (`count_reference`) | Implementado | — (no cambiar reglas) |
| 13 | Discrepancias | SessionService (`/discrepancies`, `/resolve`) | Implementado | — (la revisión muestra evidencia real desde MS-03) |
| 14 | Validación humana | Monolito, Operador (`/vision-validation`) | Implementado | API equivalente para clientes externos (opcional) |
| 15 | Supervisión | Portal Supervisor → SessionService + EvidenceService | Implementado | — (Evidence & Traceability real desde MS-03); falta un recorrido de aceptación manual |
| 16 | Kits y charolas | CatalogService + Admin (procedimiento ↔ kit, solo lectura) | Parcial | "Charola" no existe como entidad (el kit es la charola); decidir si hace falta |
| 17 | Modelos de IA | Admin monolito (registro, mapeo, activación) | Parcial | P1 + P2: trazabilidad modelo → sesión → pesos cargados |
| 18 | Reportes e indicadores | SessionService `/reports/*` + portal `/supervisor/reports`, `/supervisor/indicators` | Implementado | Exportación (CSV/PDF/XML) — no existe |
| 19 | Sincronización | *Outbox* (Mongo checkpoints, notificaciones), reconciliación de evidencias | Parcial | Endpoints delta (`updated_since`) para clientes Android/escritorio; contrato de sincronización |
| 20 | Integración hospitalaria | Solo esquema: tabla `integration_client`, `access_audit.actor_client_id`, diseño DS08 (FHIR R4) | **No implementado** | Autenticación de clientes máquina + exportación FHIR R4 (`Procedure`, `DocumentReference`, `Bundle`) |

### 2.1 Agrupación propuesta: 20 responsabilidades, 9 servicios

No se necesitan 20 contenedores. Los servicios actuales ya agrupan responsabilidades con la misma fuente de datos y el mismo ciclo de vida:

| Servicio (existente salvo indicación) | Responsabilidades | Por qué juntas |
|---|---|---|
| AuthService | 1 | Identidad única para los tres roles; ya central |
| CatalogService | 2, 16 y la lectura de 17 | Datos maestros con la misma transacción y alcance; el registro de modelos se administra en el monolito y se **lee** aquí |
| SessionService (Workflow) | 3, 12, 13, 15, 18 y la API de 14 | Todas giran sobre `work_session` y `count_event`; separarlas obligaría a transacciones distribuidas. Los reportes son lecturas de esas mismas tablas |
| EvidenceService | 4 | Objetos binarios + `media_asset`; ciclo de retención propio |
| AuditService | 5 | Lectura transversal de `access_audit` |
| NotificationsService | 6 | Consumidor asíncrono de eventos (*outbox*) |
| MonitoringService | 7 y la parte de estado de 8 | Ya sondea la salud; el latido de cámaras y estaciones es el mismo patrón |
| VisionWorker | 8 (captura), 9, 10, 11, y la carga de pesos de 17 | Necesitan GPU/CPU y memoria de sesión; deben estar juntos por latencia |
| **IntegrationService (nuevo)** o módulo de SessionService | 20 y la API delta de 19 | Única pieza nueva justificable: contratos externos (FHIR/HL7) con su propio modelo de seguridad (clientes máquina) |

Se quedan como **módulos internos** sin afectar lo pedido: la validación humana y las reglas de conteo (monolito del Operador, por regla de arquitectura); las reglas de comparación de inventario (dentro de SessionService y `rf_session`); los reportes (dentro de SessionService); el *outbox* (patrón compartido, no un servicio).

---

## 3. Brechas P1–P7

| Id | Evidencia en el código | Servicios y tablas | Solución recomendada | Pruebas necesarias | Riesgo de regresión |
|---|---|---|---|---|---|
| **P1** — **resuelta en MS-02** (§11) | `work_session.yolo_model_id` existe (migración 008, `models/WorkSession.py:24`), pero **nadie lo escribe**. `vision_bridge.bind_session` llama a `active_model_payload()` en cada *bind* (`vision_bridge.py:41,94`). | Monolito (inicio de sesión del Operador en `rf_session` y *bind*), SessionService `/start`; tabla `work_session` | Al **iniciar** la sesión, fijar `yolo_model_id` = modelo activo, solo si es NULL. En el *bind*, enviar el `class_map` del modelo fijado (no del activo). Si es NULL (sesiones antiguas), usar el activo, como hoy. | Sesión iniciada con modelo A; Admin activa B; nuevo *bind* → sigue con A. Sesión antigua sin modelo → activo. Auditoría del valor fijado. | Toca el inicio de sesión del Operador (no sus reglas de conteo) → **requiere tu autorización**. Una sesión larga podría quedarse con un modelo luego desactivado (aceptable; queda trazado). |
| **P2** — **resuelta en MS-02** (§11) | `VisionWorker/inference.py:49-60` carga `YOLO(WEIGHTS_PATH)` una vez; `main.py:126` solo copia `model_version` como etiqueta. | VisionWorker, almacenamiento Admin (Garage/local), `yolo_model`, `media_asset` | El *bind* incluye `model_id`, `checksum` y la ubicación del objeto. El worker obtiene los pesos (S3 con `response_checksum_validation='when_required'`, por H3), verifica el SHA-256, los guarda en caché por checksum y mantiene **un modelo cargado por checksum** (LRU de 1–2). `/health` informa `loaded_checksum`. Respaldo: `VISION_WEIGHTS_PATH` si no hay modelo. | Pesos A y B: *bind* con A, luego con B → inferencia con el modelo correcto (comparando `names`). Checksum alterado → rechazo. Garage caído → error controlado o respaldo. Memoria. | **Modifica el VisionWorker** (prohibido hasta ahora) → requiere autorización. No cambia los pesos. Riesgo de memoria y latencia al cambiar de modelo; sin tocar el WS. |
| **P3** — **resuelta en MS-01** (§10) | `CatalogService/routes/catalog.py:32-33`: `FAMILY_WRITERS=(it_admin,)`, `STOCK_WRITERS=(it_admin, spd_supervisor)`. El portal da 403 a Admin en piezas y kits (RACI). | CatalogService | `STOCK_WRITERS=(spd_supervisor,)`. Familias: retirar la escritura de CatalogService (o dejarla en 403 con `family_managed_by_admin_portal`) para que haya una sola fuente (Admin monolito). Mantener lecturas. | Token `it_admin` → 403 en POST/PUT de piezas, kits y familias, y auditado. SPD sigue escribiendo. Las pruebas existentes de escritura con `it_admin` hay que actualizarlas. | Bajo. Algún cliente que hoy escriba como `it_admin` dejaría de poder hacerlo (en el portal no existe). |
| **P4** — **resuelta en MS-01** (§10) | `CatalogService/repositories/management_repository.py:117-121` (`existing_family_ids` sin filtro `active`), usado en `management_service.py:227` (piezas) y `:366` (kits). Monolito: `admin_local/instruments.py:109` y `kits.py` (camino SPD local). | CatalogService; monolito, camino SPD de respaldo | Al **crear** o al **cambiar** la familia, exigir `instrument_family.active`. No invalidar los kits o piezas existentes que ya usan una familia luego desactivada (solo avisar). | Alta con familia inactiva → 422 `inactive_family`. Edición sin cambiar la familia → permitida. | Bajo. Hay que decidir qué hacer con kits existentes que contienen familias inactivas (propuesta: aviso, no bloqueo). |
| **P5** — **resuelta en MS-01** (§10; procedimiento sin estado activo en el esquema) | `SessionService/services/session_planning.py:138-163`: sala, estación, kit y procedimiento se validan por existencia e institución, **sin `active`**. Fase: `repositories/supervisor_repository.py:279` (`phase_id_by_code` sin `active`). Lo mismo en el respaldo `rf_session.py:169-183`. | SessionService (`/schedule`, `PUT /<id>`); `operating_room`, `capture_station`, `kit`, `cat_procedure_type`, `cat_operation_phase` | Exigir `active` al **programar** y al **editar** (422 con código específico: `inactive_room`, `inactive_station`, `inactive_kit`, `inactive_procedure`, `inactive_phase`). Las sesiones ya programadas no se tocan. El respaldo `rf_session` se alinea solo si se autoriza (es código compartido con el Operador). | Un caso por referencia inactiva; edición de una sesión ya programada cuyo recurso se desactivó después (se permite guardar sin cambiarlo). | Bajo-medio: las pruebas de SessionService que usan datos semilla inactivos podrían fallar; hay 697 pruebas que lo detectan. |
| **P6** | `privacy_notice_version` no tiene `institution_id` (001_init.sql:740). SessionService exige que el aviso esté activo, pero no filtra por institución. El Supervisor ve `document_uri` como texto. | Admin monolito, SessionService, tabla `privacy_notice_version` | **Decisión del equipo.** (a) Mantenerlo global (un aviso por despliegue) y documentarlo. (b) Agregar `institution_id NULL` (NULL = global), filtrar en Admin y SessionService (migración 011 incremental). Además, endpoint de lectura del documento para el Supervisor (vía EvidenceService o el portal). | Aislamiento: el Supervisor de B no ve ni puede usar avisos de A; descarga autorizada. | Medio si se elige (b): migración de datos y pruebas de Admin (RF-AD-04) y de SessionService. |
| **P7** | El portal no tiene token CSRF ni comprueba `Origin` (`app.py` solo registra el log en `before_request`). `BackendAuthService/main.py:80`: `AUTH_COOKIE_SAMESITE` por defecto `"None"`. | Portal (todas las áreas), AuthService | (1) `before_request` en el portal: para POST/PUT/PATCH/DELETE, exigir `Origin` o `Referer` igual al host (o en una lista permitida) → 403. (2) AuthService: `SameSite=Lax` por defecto. (3) Opcional: Flask-WTF CSRF por formulario. | POST con `Origin` ajeno → 403; formularios propios OK; `fetch` del WS-ticket OK; regresión completa Web (450) + navegador (95). | Medio: afecta formularios del Operador y del Supervisor; detrás de un proxy hay que configurar el host permitido. | **Resuelta en MS-04 (§14.3).**

**Dependencias entre brechas:** P2 depende de P1 (el worker necesita saber qué modelo fijó la sesión). P2 depende de H3 (cliente S3 compatible con Garage, ya resuelto en Admin y documentado). P3 y P4 son independientes. P5 es independiente. P6 depende de una decisión. P7 es independiente, pero conviene hacerla antes de exponer servicios externamente (MS-04).

---

## 4. Arquitectura actual frente a la propuesta

**Actual (observada):**

```text
Navegador ──HTTPS──> Portal BackendWebFlask (5000) ──HTTP──> SessionService, CatalogService, EvidenceService,
   │                    │  (Admin + Operador locales)        AuditService, NotificationsService, MonitoringService
   │                    └──HTTP /bind──> VisionWorker (5002)        (todos solo en 127.0.0.1 / red interna)
   └──WS /ws (ticket HMAC)──> VisionWorker
Todos ──> PostgreSQL (fuente de verdad única) · Redis (JWT/revocación) · MongoDB (telemetría/checkpoints) · Garage S3
AuthService (5001, público) emite JWT; cada servicio lo valida (HS256 + Redis).
Sin API Gateway · sin clientes Android/Desktop · sin IntegrationService.
```

**Propuesta (incremental, sin reemplazar nada):**

```text
Android (JSON) / Desktop (XML) / HIS ──HTTPS──> API Gateway (nuevo, p. ej. nginx o traefik: TLS, /api/v1/* → servicios,
                                                 límite de peticiones, X-Correlation-ID)
Navegador ──> Portal (sin cambios de arquitectura) ──> mismos microservicios
Estación ──WSS (vía gateway)──> VisionWorker (pesos del modelo fijado por sesión: P1/P2)
IntegrationService (nuevo, opcional): clientes máquina (integration_client) + exportación FHIR R4 + delta de sincronización
MonitoringService: + latido de estaciones/cámaras · NotificationsService: + canal externo opcional
```

Lo que **no cambia**: Admin y Operador siguen en el monolito; el Supervisor sigue en microservicios; AuthService sigue central; WebSockets y pesos YOLO sin modificar; PostgreSQL sigue siendo la fuente de verdad compartida; las reglas de conteo no se tocan.

---

## 5. Etapas de implementación (por prioridad y dependencia)

| Etapa | Objetivo | Servicios | Responsabilidades | Archivos / módulos principales | Cambios de BD | Endpoints | Pruebas | Criterios de aceptación | Riesgos |
|---|---|---|---|---|---|---|---|---|---|
| **MS-01** Línea base e integridad del Supervisor — **TERMINADA** (§10) | Ejecutor único de pruebas de microservicios + P3, P4, P5 | CatalogService, SessionService (+ `scripts/run_test_env.py`) | 2, 3, 16 | `CatalogService/routes/catalog.py`, `repositories/management_repository.py`, `services/management_service.py`; `SessionService/services/session_planning.py`, `repositories/supervisor_repository.py`; `scripts/run_test_env.py` (nuevo modo `ms`) | Ninguno | Mismos endpoints; nuevos códigos 403/422 | Unitarias e integración de cada servicio (179 + 697 + nuevas); `audit_admin_integration.py` sin las 2 notas | `run_test_env.py ms` en verde; las notas P3 y P5 se convierten en PASS | Pruebas que usan `it_admin` como escritor o datos inactivos |
| **MS-02** Trazabilidad del modelo — **TERMINADA** (§11) | P1 + P2 | Monolito (inicio y *bind*), SessionService `/start`, VisionWorker | 10, 17, 3 | `services/vision_bridge.py`, inicio en `rf_session.py` y SessionService `session_writes.py`; `VisionWorker/inference.py`, `main.py` | Ninguno (la columna ya existe); índice opcional | *Bind* con `model_id` y `checksum`; `/health` con `loaded_checksum` | Unitarias (caché de pesos y verificación de checksum), integración (2 modelos), E2E con el WS real | Cada sesión registra su modelo y el worker infiere con exactamente esos pesos | **Requiere autorizar cambios en el VisionWorker y en el inicio del Operador**; memoria |
| **MS-03** Evidencias y supervisión reales — **TERMINADA** (§12) | Supervisor consulta evidencias de EvidenceService; evidencia final enlazada al cierre | EvidenceService, portal Supervisor, SessionService | 4, 13, 15, 5 | `services/evidence_client.py` (lectura), `controllers/supervisor_sessions.py`, plantillas `supervisor/discrepancies/_review_evidence.html` (camino real) | Ninguno o un índice | Uso de `GET /sessions/<id>/evidence` y `/evidence/<id>` (URL firmada o *proxy*) | Integración portal↔EvidenceService; navegador | La revisión de discrepancias muestra las evidencias reales de la sesión; acceso auditado | Volumen de imágenes, permisos por institución |
| **MS-04** Exposición segura y contratos externos — **TERMINADA** (§14) | API Gateway + P7 + verificación JSON/XML | Gateway (nuevo contenedor de infraestructura), AuthService, portal | 1, 9, 19 (transporte) | `docker-compose.yml` (gateway), `infra/` (configuración), portal `app.py` (`Origin`), `BackendAuthService/main.py` (SameSite) | Ninguno | `/api/v1/*` público vía gateway; WSS | Contratos (OpenAPI y XSD de cada servicio), seguridad (CSRF, CORS, *rate limit*), E2E por el gateway | Un cliente externo con JWT consume Catalog y Session en JSON y en XML a través del gateway; los POST del portal con `Origin` ajeno → 403 | Configuración TLS local; regresión de formularios |
| **MS-05** Cámaras y monitoreo de estaciones | Registro, latido y estado de cámaras y estaciones | VisionWorker (latido por WS), MonitoringService, NotificationsService, Admin (campos de estación) | 7, 8, 6 | `VisionWorker/live_ws.py` (emitir latido; **requiere autorización**), `MonitoringService/services/probes.py`, nueva vista en `/admin/system-status` | Migración 011: `capture_station.last_seen_at`, `device_label` (opcional) | `GET /api/v1/monitoring/stations` | Integración (latido → monitoreo → notificación); navegador | Una estación sin latido > N s aparece "offline" y genera una notificación | Ruido de latidos; no tocar el protocolo WS existente (solo un mensaje adicional) |
| **MS-06** Reportes, indicadores y notificaciones | Exportación de reportes (CSV/XML), umbrales KPI compartidos, canal de correo opcional | SessionService, NotificationsService | 18, 6 | `SessionService/routes/sessions.py` (`/reports/*/export`), `NotificationsService/services/dispatcher.py` (canal SMTP simulado en pruebas) | Opcional: preferencias de notificación | `GET /reports/summary.csv`, `Accept: application/xml` | Contrato (XSD), integración | El Supervisor descarga los reportes; las notificaciones críticas salen por un canal configurable | Datos personales en las exportaciones (privacidad) |
| **MS-07** Sincronización e integración hospitalaria | Clientes máquina (DS08), exportación FHIR R4, endpoints delta | AuthService (*client credentials*), IntegrationService (nuevo) o módulo de SessionService | 19, 20 | Nuevo `apps/IntegrationService` (o `SessionService/routes/fhir.py`), `BackendAuthService` (token de cliente) | Uso de `integration_client` (ya existe); quizá `sync_cursor` | `POST /api/v1/auth/token` (*client credentials*), `GET /api/v1/fhir/Procedure`, `DocumentReference`, `Bundle`; `?updated_since=` | Validación de recursos FHIR R4 contra el esquema oficial; seguridad por *scopes* | Un cliente HIS autorizado obtiene un `Bundle` FHIR válido de una sesión cerrada; un cliente sin *scope* → 403 | Alcance amplio; empezar solo con exportación de lectura |
| **MS-08** Decisiones pendientes | P6 (avisos por institución) y la entidad "charola" | Admin, SessionService, CatalogService | 2, 16, privacidad | Según decisión | Migración incremental si se decide | — | — | Decisión documentada e implementada | Migración de datos |

**Prioridad:** completar lo existente primero (MS-01 → MS-02 → MS-03). Después, la exposición (MS-04) antes de cualquier cliente externo. El único servicio nuevo (MS-07) va al final.

---

## 6. Riesgos y dependencias entre servicios

1. **PostgreSQL compartida** por el monolito y todos los microservicios: un cambio de esquema afecta a todos. Por eso solo migraciones incrementales e idempotentes, ejecutando todas las suites después de cada una.
2. **Secreto JWT compartido** (HS256) en todos los servicios: si se filtra, todo queda comprometido. A mediano plazo convendría pasar a RS256 con clave pública (solo AuthService firma).
3. **Redis como punto único** de validación: si cae, todas las APIs devuelven 503 (diseño correcto, *fail-closed*), pero sin alta disponibilidad.
4. **Doble implementación del Supervisor** (SessionService y `rf_session` como respaldo): las correcciones (P5) hay que hacerlas en los dos o retirar el respaldo en producción. Recomendado: obligar a definir `SESSION_SERVICE_URL` en producción.
5. **El VisionWorker y el inicio del Operador** son zonas protegidas: P1/P2/MS-05 requieren autorización explícita.
6. **Auditoría escrita por cada servicio** directamente en `access_audit`: riesgo de formatos distintos. Conviene un contrato común (`action`, `resource_type`, `outcome`, `correlation_id`) con prueba de conformidad por servicio.
7. **Clientes externos** sin gateway: hoy imposible, por diseño de Compose (solo loopback).
8. **Pruebas de EvidenceService** que leen credenciales del `.env` del repositorio (11 omitidas): se deben poder alimentar desde `.env.test`.
9. Las **3 fallas preexistentes del Operador** siguen abiertas; no se tocan en esta fase, pero cualquier etapa que toque `rf_session` debe compararlas antes y después.

---

## 7. Pruebas antes de programar

1. **Ejecutor único `ms`**: las 7 suites de microservicios contra el entorno aislado. Ya se ejecutaron a mano en este diagnóstico: Catalog 179, Session 697, Evidence 323 (+11 omitidas), Audit 48, Notifications 40, Monitoring 41, Vision 17 (+4 omitidas).
2. **E2E de la pila completa** en un proyecto Compose aislado (no la pila principal): `scripts/e2e_docker.py`, `e2e_catalog.py`, `e2e_identity.py`, `e2e_monitoring.py` con imágenes reconstruidas. Hay que confirmar que siguen en verde con los cambios de Administración, porque la imagen `pef-build-check/vision` es anterior a `flask_sock`.
3. **Contratos**: validar las respuestas XML contra cada XSD y las JSON contra cada `openapi.yaml`, servicio por servicio.
4. **Revocación cruzada**: un token revocado por AuthService debe dar 401 en los 6 microservicios (hoy probado servicio por servicio, no en conjunto).
5. **WebSocket real** con el `best.pt` provisional: captura, *bind* y conteo de una sesión de prueba. Es la línea base antes de P1/P2.
6. **Línea base de rendimiento** del VisionWorker (latencia por cuadro, memoria) para comparar después de P2.

---

## 8. Respaldo de Administración (pendiente de autorización)

Ver el informe de entrega (lista de archivos y mensaje). Se excluyen las 66 capturas JPEG de `docs/evidencias/ADMIN-09/`, unos 5 MB: se regeneran con `scripts/browser_validate_admin.py`. Se incluyen `results.md` y `results.json`. Quedan documentados en el plan dos puntos: el `best.pt` usado es **provisional** y la **aceptación manual de Administración sigue pendiente**.

---

## 9. Recomendación: primera etapa

**MS-01 — Línea base de pruebas e integridad de datos del Supervisor (P3, P4, P5).** Motivos:
- Solo toca microservicios del Supervisor (CatalogService y SessionService), sin tocar el Operador, el VisionWorker ni las reglas de conteo.
- Cierra dos de las brechas que la integración ya detecta automáticamente (las notas de `audit_admin_integration.py`).
- Deja un ejecutor de pruebas único para todas las etapas siguientes.
- El riesgo es bajo y lo cubren 876 pruebas existentes.

MS-02 (P1/P2) debería ser la segunda etapa, pero requiere autorizar antes cambios en el VisionWorker y en el inicio de sesión del Operador.

---

## 10. MS-01 — Resultado (2026-11-03)

**Rama:** `microservices_phase2`, creada desde el commit de respaldo de Administración `5600edd` (`admin-01`). Cambios sin commit, como se pidió.

### 10.1 Ejecutor unificado (`scripts/run_ms_tests.py`)

- Ejecuta las 8 suites contra el entorno aislado y se niega a usar una base que no sea la de prueba.
- Lee los resultados del XML JUnit de pytest y clasifica cada problema como CODE o INFRA.
- Estados por suite: `OK`, `OK (with skips)`, `INCOMPLETE (INFRA)` y `FAIL`. Las omisiones nunca cuentan como aprobadas.
- Código de salida: 1 si hay fallos, 2 si está incompleto por infraestructura, 0 si todo pasó.
- VisionWorker se ejecuta en la imagen `pef-ms-test/vision`, reconstruida desde su Dockerfile sin tocar código ni pesos.
- Verificación de la clasificación: con Redis de prueba detenido, AuditService terminó `INCOMPLETE (INFRA)` con código 2; con Redis arriba, `OK` con código 0.
- Uso documentado en `GUIA_PRUEBAS.md` §2.1.

| Suite | Diagnóstico MS-00 (manual) | **Línea base MS-01** (antes de cambios) | **Final MS-01** |
|---|---|---|---|
| AuthService | 172 | 172 | **172** |
| CatalogService | 179 | 179 | **235** (+56) |
| SessionService | 697 | 697 | **712** (+15) |
| EvidenceService | 323 + 11 omitidas | **334, 0 omitidas** (Garage de pruebas conectado) | **334** |
| AuditService | 48 | 48 | **48** |
| NotificationsService | 40 | 40 | **40** |
| MonitoringService | 41 | 41 | **41** |
| VisionWorker | 17 + 4 omitidas | **21, 0 omitidas** (MongoDB de pruebas conectado) | **21** |
| **Total** | — | **1532 aprobadas, 0 fallidas, 0 omitidas** | **1603 aprobadas, 0 fallidas, 0 omitidas** |

Portal y otros:

| Suite | Antes | Después |
|---|---|---|
| BackendWebFlask (`run_test_env.py web`) | 450 / 3 fallidas | **458 / 3 fallidas**: +8 pruebas MS-01; las mismas 3 del Operador, preexistentes en `b417170` |
| Admin | 222 | **222** |
| Smoke SQLite | OK | **OK** |
| Smoke real | 51/51 | **51/51** |
| Integración Admin–microservicios (`audit_admin_integration.py`) | 9/9 + 2 NOTE (brechas P3 y P5) | **13/13, sin NOTE**: las dos brechas pasan a PASS y se añaden P4 y sala inactiva |

### 10.2 P3 — Matriz de permisos de CatalogService (aplicada dentro del servicio)

| Operación | `it_admin` | `spd_supervisor` | `station_operator` | `or_charge` | `it_admin` solo de otra institución |
|---|---|---|---|---|---|
| `POST /families`, `PUT /families/{id}` | **Sí**; si `ADMIN_CATALOG_STEWARD_INSTITUTION_ID` está definida, solo la institución responsable (si no, 403 `shared_catalog_read_only`) | 403 | 403 | 403 | 403 |
| `POST /instruments`, `PUT /instruments/{id}` | **403** (antes: sí) | Sí | 403 | 403 | 403 |
| `POST /kits`, `PUT /kits/{id}` | **403** (antes: sí) | Sí | 403 | 403 | 403 |
| `GET` (familias, piezas, kits, catálogos) | Sí (sin cambios; también ve kits inactivos) | Sí (kits activos) | Sí | Sí | — |

- Fuente: el RACI de `rf_admin_app.md` (§0: piezas y kits son del SPD, "no Admin"; familias de Administración), igual que el portal.
- Toda escritura denegada responde 403 y se audita como `DENIED_<MÉTODO>_<RECURSO>` en `access_audit`, como antes. Sin token, o con un token inválido o revocado: 401.
- No se eliminó ningún endpoint ni cambió ningún contrato JSON/XML, salvo el nuevo código de error `shared_catalog_read_only`.
- `docker-compose.yml` ahora pasa `ADMIN_CATALOG_STEWARD_INSTITUTION_ID` a `catalog` y también a `web`: antes no llegaba a ninguno de los dos contenedores. Documentada en `.env.example`.
- **Archivos:** `CatalogService/routes/catalog.py` (`STOCK_WRITERS=(spd_supervisor,)`, `require_writer(..., shared_catalog=True)`), `CatalogService/config.py`, `openapi.yaml`, `GUIA_CATALOG.md`.
- **Pruebas:** `tests/test_ms01_permissions_and_integrity.py`: matriz de 6 roles × 6 operaciones con comprobación de auditoría, 401, lecturas, token revocado e institución responsable. Además se adaptaron 19 pruebas existentes que usaban `it_admin` como escritor de piezas y kits; ahora usan `spd_supervisor`, y donde afirmaban que `it_admin` creaba versiones de kits ahora afirman el 403.
- **Consecuencia conocida:** nadie puede "revivir" un kit inactivo por la API. El SPD no ve kits inactivos (regla previa) y `it_admin` ya no escribe kits. Es coherente con el portal: un kit retirado se reemplaza por uno nuevo.

### 10.3 P4 — Familias inactivas

| Camino | Regla | Archivos |
|---|---|---|
| CatalogService | Pieza nueva o cambio de familia hacia una inactiva → **422 `inactive_family`** (`field: family_id`). Kit nuevo o nueva versión que **añada** una familia inactiva → **422 `inactive_family`** (`field: items`). Igual en JSON y XML. | `services/management_service.py` (`_family_and_cycle`, `_check_families`), `repositories/management_repository.py` (`inactive_family_ids`) |
| Portal, respaldo SPD local (`admin_local/instruments.py`, `kits.py`), solo sin `CATALOG_SERVICE_URL` | Misma regla, error `inactive_family` traducido (es-MX). Es solo del Supervisor (`require_roles(SUPERVISOR)`); el Operador no lo usa. | `services/admin_local/instruments.py`, `kits.py`, `messages.py`, `.po`/`.mo` |

- Las familias desconocidas mantienen su respuesta anterior (`invalid_family_or_cycle` / `invalid_family`).
- **Histórico:** una pieza cuya familia se desactivó después puede editarse si conserva esa familia. Un kit que ya contenía una familia luego desactivada puede versionarse conservándola, o quitarla. No se borra ni se reescribe nada automáticamente.
- **Pruebas:** CatalogService (9 casos: activa, inactiva, inexistente, XML, cambio de familia, histórico de pieza, kit nuevo, añadir a un kit existente, histórico de kit) y portal (`tests/test_ms01_local_catalog_integrity.py`, 5).

### 10.4 P5 — Recursos inactivos al programar

| Camino | Regla | Archivos |
|---|---|---|
| SessionService `POST /schedule` | Sala, estación, kit o fase inicial inactivos → **422** `inactive_room` / `inactive_station` / `inactive_kit` / `inactive_phase` (`error: validation_error`, igual que los `invalid_*` existentes). Inexistente o de otra institución → los `invalid_*` de siempre. | `services/session_planning.py` (`_references(..., keep)`), `repositories/supervisor_repository.py` (`get_room`/`get_station`/`get_kit` con `active`, `phase_by_code`, `session_assignment`), `openapi.yaml` |
| SessionService `PUT /{id}` (sesión programada) | Una sala, estación o kit **nuevos** deben estar activos; los que la sesión **ya tenía** se pueden conservar aunque se hayan desactivado después. | ídem |
| Portal, respaldo local del Supervisor (`rf_session.schedule_session` / `update_scheduled_session`, solo sin `SESSION_SERVICE_URL`) | Misma regla (422 con mensaje traducido). Solo cambian los validadores que usan esas dos funciones del Supervisor y la fase inicial de `schedule_session`; captura, validación humana, cambio de fase y cierre del Operador no se tocan. | `services/rf_session.py`, `services/session_remote.py` (mensajes de los 4 códigos), `.po`/`.mo` |

- **Procedimiento:** `cat_procedure_type` no tiene columna `active` (`001_init.sql`), así que no existe un "procedimiento inactivo" y solo se valida su existencia. Si se quiere desactivar procedimientos hace falta una migración y la pantalla de Administración (decisión pendiente).
- **Pruebas:** SessionService `tests/test_ms01_inactive_resources.py` (15: cuatro inactivos, siete desconocidos o de otra institución, XML, edición que introduce un inactivo, conservación de referencias desactivadas después, listado de sesiones históricas) y portal `tests/test_ms01_local_schedule_inactive.py` (3).

### 10.5 Qué no se probó de punta a punta

- No se ejecutó un recorrido **en navegador** del Supervisor (programar o editar una sesión desde el portal con SessionService). La cobertura del Supervisor es de API e integración: SessionService 712, `audit_admin_integration.py` con procesos reales (portal + AuthService + CatalogService + SessionService sobre la misma BD) y las pruebas del portal.
- Tampoco se repitió la validación de Administración en navegador (`browser_validate_admin.py`). Administración solo cambió mensajes y el respaldo SPD local; la cubren Admin 222 y el smoke real 51/51.

### 10.6 Pendiente después de MS-01

- **P1, P2** (MS-02, requieren autorización para tocar el VisionWorker y el inicio del Operador), **P6** (decisión) y **P7** (CSRF/`Origin`).
- **Residual de P3:** `PUT /families/{id}` de CatalogService no exige la confirmación "familia del modelo activo" que sí exige el portal (RF-AD-02). Un `it_admin` llamando a la API directamente puede desactivar una familia mapeada en el modelo activo sin confirmar. Propuesta: aceptar `confirm_active_model` en la API (409 `family_in_active_model` si falta). Cambia el contrato, así que queda para decidir.
- Desactivar procedimientos (sin columna `active`).

---

## 11. MS-02 — Trazabilidad de modelos YOLO (P1, P2)

### 11.1 Flujo observado antes de MS-02 (código, no documentación)

| Paso | Dónde | Observación |
|---|---|---|
| Modelo activo | Admin, `admin_local/vision_models.py` (`yolo_model.active`, único por índice parcial) | Activar otro modelo solo cambia esa marca. |
| Creación de la sesión | SessionService `POST /schedule` (o el respaldo `rf_session.schedule_session`) | No toca modelos. |
| Inicio (Operador) | Monolito `rf_session.start_session` (fila bloqueada `FOR UPDATE`, `scheduled → in_progress`, `capture_mode`, evento `session_open`, auditoría `session.start`) y después `vision_bridge.bind_session` (fuera de la transacción; si falla, la pantalla muestra "vision offline"). SessionService `POST /{id}/start` existe para clientes API, pero el portal no lo llama. | `work_session.yolo_model_id` (migración 008) **nunca se escribía**. |
| Referencia de conteo | `rf_session.count_reference` (inicial + incorporaciones confirmadas) | No depende del modelo; MS-02 no la toca. |
| Operador ↔ worker | `bind` HTTP (portal → worker, sin autenticación) + WebSocket `/ws` con ticket HMAC (`VISION_WS_SECRET`) que solo identifica la sesión; el estado en vivo copia `class_map` y `model_version` del estado del *bind*. | El cliente WS no puede elegir modelo (bien). El *bind* HTTP aceptaba cualquier `class_map` de cualquiera que alcanzara el puerto. |
| `model_version` / `class_map` | `vision_bridge.active_model_payload()` en **cada** *bind*, incluido el re-*bind* automático de `fetch_state` | Cambiar el modelo activo a mitad de sesión cambiaba el `class_map` en el siguiente re-*bind*. |
| Pesos | `VisionWorker/inference.get_model()`: `YOLO(VISION_WEIGHTS_PATH)`, uno global | `model_version` era solo una etiqueta. |
| Detecciones y evidencia | El portal copia el estado del worker (`fetch_state`) a `count_event` (`auto_count`, `close_blocked`; payload con `model_version`), a los *checkpoints* de MongoDB (`model_version`) y, a partir de ellos, a EvidenceService (JPEG). | La única identificación del modelo era la etiqueta. |

### 11.2 Decisiones

1. **Momento de fijación.** En la misma transacción de inicio, con la fila bloqueada, si `capture_mode = 'vision'` y hay un modelo activo: `yolo_model_id` = modelo activo. Esto aplica a `rf_session.start_session` y a SessionService `/start`. Si no hay modelo activo en ese instante, se fija en el **primer *bind*** (primer uso de inferencia), también bloqueando la fila. Una vez fijado, **nunca se reasigna**. Auditoría `session.model_pinned` (`resource_type = work_session`). El evento `session_open` guarda `yolo_model_id`, `model_version` y `model_sha256` cuando se fija al inicio.
2. **Inmutabilidad en la base de datos.** Migración `011_session_model_pin.sql`: un *trigger* impide cambiar un `yolo_model_id` ya fijado, y por tanto borrar un modelo usado por sesiones. Es idempotente y no destructiva.
3. **Sesiones históricas.** Las sesiones sin modelo y ya cerradas no se tocan. Las sesiones en curso iniciadas antes de MS-02 reciben el modelo activo en su primer *bind* (auditado), porque es el que de hecho estaban usando. Si una sesión tuvo un modelo fijado (auditoría `session.model_pinned`) y la referencia desapareció, el *bind* falla y **no se vuelve a fijar**.
4. **Contrato del *bind*.** El portal envía el bloque `model`: `model_id`, `model_version`, `sha256`, `storage` (`bucket`, `key`) y `class_names`. El cuerpo va firmado con HMAC-SHA256 (`X-PEF-Signature`, mismo `VISION_WS_SECRET` que los tickets). El worker rechaza un *bind* sin firma válida (401) y un *bind* sin modelo (422 `model_required`). Lo segundo se puede relajar solo con `VISION_ALLOW_UNPINNED_BIND=true`, para pruebas.
5. **Obtención de pesos en el worker**, nunca a partir de una ruta enviada por el cliente:
   - La clave debe cumplir `^models/[0-9a-f]{32}\.pt$` (claves generadas por el servidor de Administración).
   - El bucket debe ser `local` (volumen de Administración montado en solo lectura) o el bucket S3 configurado.
   - Orden de búsqueda: caché en disco por SHA-256, luego el archivo empaquetado `VISION_WEIGHTS_PATH` (solo si su SHA-256 coincide), luego el almacenamiento.
   - Se **verifica el SHA-256 antes de cargar**. Si no coincide, se rechaza; nunca hay sustitución por otro modelo.
6. **Caché en memoria.** LRU de modelos cargados por SHA-256 (`VISION_MODEL_CACHE_SIZE`, por defecto 2), con un *lock* de carga. Cada sesión guarda su SHA-256: nada se recarga por fotograma, y dos sesiones con modelos distintos conviven.
7. **Compatibilidad de clases.** Al cargar, los `names` del modelo deben coincidir con los `class_names` registrados en Administración, y cada `yolo_class_id` del `class_map` debe existir en el modelo. Si no, 422 `model_classes_incompatible`.
8. **Verificación en el portal.** El *bind* devuelve el SHA-256 cargado y `fetch_state` (la ruta por la que se registran **todos** los conteos) comprueba que el worker infiere con el SHA-256 fijado en la sesión. Si no coincide, devuelve 409 y el conteo no se registra. Así cada `count_event` queda ligado a pesos verificados a través de `work_session.yolo_model_id → yolo_model (checksum, version_tag, model_class)`, sin duplicar el hash en cada fila.
9. **Lo que no cambia:** reglas de conteo, pantallas, flujo del Supervisor, permisos institucionales y pesos.

### 11.3 Implementación

| Pieza | Archivos |
|---|---|
| Fijación (portal) | `services/model_pinning.py` (nuevo); `services/rf_session.start_session`, que fija dentro de la transacción bloqueada y añade el modelo al payload de `session_open` (no toca reglas de conteo ni pantallas) |
| Fijación (SessionService) | `services/session_writes.start_session`, `repositories/write_repository.py` (`active_model`, `session_model_id`), `repositories/tables.py` (`work_session.yolo_model_id`), `adapters/audit_adapter.py` (`ACTION_MODEL_PINNED`) |
| Inmutabilidad | `data/migrations/011_session_model_pin.sql` (trigger + índice; idempotente, comprobada dos veces), montada en `docker-compose.yml` y `docker-compose.test.yml` |
| *Bind* y verificación en el portal | `services/vision_bridge.py`: `session_model_payload` (modelo fijado + referencia de almacenamiento), `_signed_post` (HMAC), comprobación del SHA-256 devuelto, `assert_session_model` en `fetch_state` y `fetch_state_quick`. `services/admin_local/storage.py`: `presigned_get` |
| Worker | `model_store.py` (nuevo: referencias, fuentes, SHA-256, descarga acotada, LRU, clases), `bind_auth.py` (nuevo: firma), `main.py` (*bind* firmado y fijado, `model_conflict`, `/health` con `loaded_models` y `bundled_sha256`), `inference.py` (`get_model(sha256)` e identidad del modelo en el estado y en los resultados), `live_ws.py` (la sesión en vivo hereda los pesos fijados; `model_sha256` en los mensajes) |
| Docker | `vision`: volumen de Administración en solo lectura (`/admin_storage`), `VISION_MODEL_FETCH_ALLOWED_HOSTS=garage:3900`, `VISION_MODEL_CACHE_DIR` |
| Pruebas | `VisionWorker/tests/test_model_store.py` (26), `test_edge_frames.py` adaptada al *bind* firmado; `BackendWebFlask/tests/test_ms02_model_pinning.py` (15); `SessionService/tests/test_ms02_model_pin.py` (4); `scripts/e2e_ms02_model_pin.py` (E2E real); `scripts/run_ms_tests.py --weights` |

### 11.4 Escenarios pedidos: cómo se comprobaron

"**Real**" = pesos `best.pt` provisionales cargados por Ultralytics e inferencia sobre imágenes. "**BD real**" = PostgreSQL de prueba con la lógica real y el worker sustituido por un doble HTTP. "**Doble**" = cargador o red simulados.

| # | Escenario | Comprobación | Cómo |
|---|---|---|---|
| 1 | Nueva sesión con modelo activo | Fijación + auditoría + `session_open` + *bind* firmado; el worker descarga de Garage, verifica y carga | **Real** (E2E) + BD real (portal, SessionService) |
| 2 | Cambio del modelo activo después de empezar | La sesión A mantiene sus pesos tras activar otro modelo y volver a hacer *bind*; el trigger impide el cambio en la BD | **Real** (E2E) + BD real |
| 3 | Dos sesiones con versiones diferentes | Fijaciones distintas (BD real); dos modelos YOLO **reales distintos** cargados a la vez (`best.pt`, 17 clases, y `yolov8n` sin entrenar, 80 clases, que no deriva de `best.pt`); en el E2E, la sesión B fija el otro modelo | **Real** (worker) + BD real |
| 4 | Modelo inexistente o desactivado | Sin modelo activo: el inicio no se bloquea y el *bind* da 409 `no_active_model`, que se fija después en el primer *bind*. Un modelo fijado que luego se desactiva sigue sirviendo. Fijación perdida: 409, sin reemplazo | BD real + **Real** (E2E: A sigue tras desactivar su modelo) |
| 5 | Archivo de pesos ausente | Modelo sin archivo (semilla `yolo26l-demo`): 404 `model_weights_missing`, sin usar `best.pt` | **Real** (E2E, sesión B) + doble |
| 6 | SHA-256 incorrecto | Archivo local o descarga alterados: `model_checksum_mismatch`, el archivo se descarta; caché corrupta no usada; worker que reporta otro SHA → 409 en el portal | Doble (worker) + BD real (portal) |
| 7 | Error de Garage | HTTP 500 o conexión rechazada → 503 `model_storage_unavailable`; host no permitido → rechazo | **Doble** (no se apagó Garage en el E2E) |
| 8 | Clases incompatibles | `names` distintos de los registrados o `class_map` con clases inexistentes → 422 `model_classes_incompatible`; con pesos reales: 17 nombres iguales a los de Administración | Doble + **Real** |
| 9 | Reconexión WebSocket | Ticket nuevo, mismo SHA-256, modelo reutilizado de memoria (sin recarga) | **Real** (E2E, Chromium) |
| 10 | Inicios concurrentes | Dos `POST /start` solapados en datos confirmados: 200 + 409, fijación y auditoría únicas | BD real (SessionService, concurrencia real); en el portal, la misma fila `FOR UPDATE` |
| 11 | Sesiones históricas sin modelo | Cerradas: intactas. En curso: se fijan en el primer *bind* con auditoría | BD real |
| 12 | Revocación del usuario durante la conexión | Sin JWT válido el portal no emite tickets nuevos ni registra conteos. **Limitación:** un WS ya abierto sigue mostrando la vista previa hasta cerrarse (el worker no consulta AuthService) | BD real (portal) |
| 13 | Reutilización de un modelo cargado | Varios *bind* y fotogramas, una sola carga; en el E2E, `loaded_models` no cambia al reconectar | Doble + **Real** |
| 14 | Carga de un modelo distinto | Otro SHA-256 → segunda carga; LRU de tamaño 1 → expulsión y recarga verificada | Doble + **Real** (dos modelos reales) |

### 11.5 Resultados (2026-11-04)

- **E2E real** (`scripts/e2e_ms02_model_pin.py`): **13/13**.
  - El worker arranca **sin** pesos empaquetados.
  - Inicio del Operador por el portal → modelo real fijado y auditado una vez.
  - El worker descargó los pesos de Garage por URL prefirmada (`source: s3`), verificó el SHA-256 `6215cb74…85d9` y los cargó; inicio + primer *bind* en unos 1.2 s.
  - Video subido: primer fotograma con 4 detecciones reales; conteo inicial (confirmación explícita del Operador por diferencia con el kit, regla existente); recorrido completo; conteo final.
  - El último fotograma mostró 5 frente a 4. El portal lo rechazó según la regla de conteo y el Operador lo confirmó explícitamente, así que queda para revisión del Supervisor. Es comportamiento previo: la compresión del video sintético cambia ligeramente los fotogramas.
  - Eventos `auto_count` con la versión fijada.
  - WebSocket real en Chromium: 2 fotogramas con detecciones y `model_sha256` fijado; reconexión sin recarga.
  - Activación de otro modelo a mitad de sesión: A conserva sus pesos.
  - La sesión B fija el modelo semilla, sin pesos: rechazada con `model_weights_missing`, sin sustituirla por `best.pt`.
- Ejecutor unificado (con `--weights`): **1633/1633, 0 omitidas** (MS-01: 1603). VisionWorker 47 (+26), SessionService 716 (+4); el resto sin cambios.
- BackendWebFlask: **473 / 3 fallidas** (+15). Las mismas 3 del Operador, con los mismos mensajes que en `b417170`.
- Admin 222; smoke SQLite OK; smoke real 51/51; integración Admin–Supervisor 13/13.

### 11.6 Cambios de comportamiento y limitaciones

1. **El modelo semilla `yolo26l-demo` ya no infiere** (no tiene pesos registrados y su checksum es ficticio). En la pila principal hay que subir y activar los pesos reales en Administración (RF-AD-08) antes de usar visión. Si su SHA-256 coincide con `apps/VisionWorker/weights/best.pt`, el worker lo usa sin descargarlo. Los E2E Docker antiguos (`e2e_docker.py` y similares) suponían el modelo semilla y no se volvieron a ejecutar.
2. **Revocación con un WS abierto:** el ticket solo se valida al conectar; un WS abierto sigue con la vista previa hasta cerrarse. Los conteos sí exigen un JWT válido. Propuesta: que el worker cierre el WS al vencer el ticket y que el cliente lo renueve (toca el cliente JS del Operador; requiere autorización).
3. **`VISION_WS_SECRET`** firma ahora tickets **y** *binds*. El valor por defecto de Compose (`pef-demo-vision-ws`) no sirve para producción.
4. **Carga de `.pt`:** Ultralytics deserializa con PyTorch, y la defensa es que solo se carga un artefacto publicado por Administración e identificado por su SHA-256. Un administrador malicioso podría publicar un `.pt` dañino (riesgo inherente a RF-AD-08).
5. **Memoria:** con `VISION_MODEL_CACHE_SIZE=2` pueden coexistir dos modelos grandes. Un tercero expulsa al menos usado, que se recargaría (con nueva verificación) si su sesión sigue activa.
6. **El `best.pt` es provisional:** hay que repetir RF-AD-08 y este E2E con los pesos definitivos.
7. El hash por fotograma no se guarda en cada `count_event`. Se obtiene por `work_session.yolo_model_id`, y el portal garantiza que coincide con el del worker en cada registro.

---

## 12. MS-03 — Evidencias reales del Operador para el Supervisor (2026-11-04)

### 12.1 Revisión del caso 4 frente a 5 (E2E de MS-02, sesión `146aa578…`)

| Pregunta | Resultado observado (BD de prueba + portal real) |
|---|---|
| ¿Quedó bloqueado el Operador? | **(Corregido en MS-03.1, §13: este comportamiento ya no es posible.)** No. El primer intento final se rechazó (`close_blocked`: referencia 4, detectado 5); con la confirmación explícita se registró el `auto_count` final = 5 y la auditoría `session.final_count`. |
| ¿Accedió a la validación humana? | Sí. La página muestra "Final count (last frame): 5 · Reference: 4. The totals differ: this difference will be documented for Supervisor review." |
| ¿Pudo cerrar? | Cerró **su parte**. Al enviar la validación (3 clasificados + 2 extra = 5; motivo por familia) la sesión pasó a `correction_required` con 11 discrepancias (`shortage` y `surplus`) y la auditoría `session.manual_close`. El cierre final queda en manos del Supervisor, según la regla existente. |
| Eventos | `session_open` (con modelo fijado) · `auto_count` inicial 4 · muestras `video_window`/`video_ready` · `close_blocked` 4/5 · `auto_count` final 5 · conteos humanos · discrepancias. Auditoría: `session.model_pinned → session.start → session.initial_count → session.final_count → session.manual_close`. |

### 12.2 Dónde se cortaba la conexión

- La cadena de datos ya existía: el Operador registra conteos (PostgreSQL), el *outbox* del portal escribe el checkpoint DS06 (MongoDB, con detecciones y `model_version`), sube el JPEG a EvidenceService/Garage y enlaza `media_asset_id`.
- Las pantallas reales del Supervisor (`rf_details`, `rf_review`) **no consultaban EvidenceService**: solo mostraban una tabla de checkpoints sin imágenes.
- La pestaña v3 "Evidence & Traceability" (`_review_evidence.html`) solo existe para la sesión demo WS-026 (`view_data.v3_evidence`). Se conserva tal cual, separada en modo demo.

### 12.3 Arquitectura implementada (sin servicio nuevo; EvidenceService sin cambios)

| Pieza | Qué hace |
|---|---|
| `services/supervisor_evidence.py` (portal, nuevo) | Une, una vez por página y cada una opcional: el detalle ya cargado (modelo fijado, eventos de conteo, discrepancias), los checkpoints DS06 (MongoDB; en Session Details reutiliza la lectura del panel, sin duplicarla) y la lista de evidencias de EvidenceService con el token del Supervisor. No inventa datos: lo que falta se muestra como no disponible. |
| `GET /supervisor/sessions/<sid>/evidence/<asset_id>/image` (portal, nuevo) | Solo `spd_supervisor`. Lista la evidencia de la sesión (EvidenceService autoriza institución, rol y asignación), exige que el activo **sea de esa sesión** y pide la URL firmada (auditada `evidence.download_url`, TTL corto). Redirige 302 `no-store`. Errores: 404 (desconocido, otra sesión o institución), 410 (purgado o bloqueado), 503 (servicio caído). |
| `services/evidence_client.py` | Añade `list_session_evidence` y `get_evidence` (contrato DS09 existente). |
| SessionService | El detalle expandido (`?expand=detail`) añade `vision_model` (`id`, `version_tag`, `sha256`) y la proyección de eventos conserva `yolo_model_id` y `model_sha256`. Igual en el respaldo local del portal. |
| Plantillas | Nuevo *partial* `supervisor/sessions/_evidence_traceability.html` (componentes v3: `v3-evidence`, tablas): imágenes reales con su motivo, hora, fase, `model_version`, número de detecciones y SHA-256 de la imagen; cajas YOLO reales (`evidence-overlay.js` las ajusta al área visible de la imagen); modelo y SHA-256 de los pesos; conteos de IA (con modelo) y validaciones humanas. Va dentro de **Session Details → Timeline** (en lugar de la tabla de checkpoints; se conserva la telemetría) y en **Review Detail** (antes de las tarjetas de discrepancias/resolución). Sin evidencias: estado vacío. Una imagen que no carga: *placeholder* sin romper la página. |
| Session History / Resolution | Sin cambios: el historial enlaza a Session Details; la resolución sigue en Review Detail, ahora con la evidencia encima. |

### 12.4 Revocación del WebSocket activo (P2 residual de MS-02)

Implementada **en el servidor** (worker), con el cliente como apoyo (detalle en `GUIA_CAPTURA_VIDEO.md`):
- Ticket con usuario y caducidad (120 s por defecto), comprobado en cada mensaje y con un pulso de 1 s.
- `session.renew` para la misma sesión y el mismo usuario.
- `POST /auth/revoke` firmado, que el portal llama al desactivar, restablecer la contraseña o cerrar sesión: corte en menos de 1 s con `revoked`, sin más resultados.
- El cliente deja de enviar fotogramas y muestra un mensaje.

Además, el `sign-out` del portal ahora revoca también el *refresh token*: antes seguía válido en el servidor.

### 12.5 Pruebas

| Tipo | Qué | Resultado |
|---|---|---|
| **E2E real** `scripts/e2e_ms03_evidence.py` | Supervisor programa (SessionService) → Operador con worker real y `best.pt` provisional → *outbox* a MongoDB + EvidenceService/Garage → Supervisor en Chromium: imágenes reales cargadas por la ruta autorizada, cajas YOLO dentro de la imagen, modelo y SHA-256, conteos idénticos a los del Operador, auditoría de cada enlace. Negativos reales: sesión inexistente, evidencia inexistente, otra institución, Operador o Administrador (403), archivo borrado de Garage (*placeholder*), Garage detenido (la página sigue), tokens revocados, reconexión WS, revocación con el WS abierto. | **19/19** |
| WebSocket real (servidor y cliente reales, YOLO simulado) `VisionWorker/tests/test_live_ws_auth.py` | Formato de ticket; revocación con la conexión abierta; aislamiento entre usuarios; caducidad; renovación válida y de otro usuario; ticket falso | **7/7** |
| Portal (BD real, MongoDB y EvidenceService simulados) `tests/test_ms03_supervisor_evidence.py` | Pantallas con evidencia real, estado vacío, servicio caído, ruta de imagen (302, 404, 410, 503, roles, otra institución), ticket con usuario, revocación firmada, *hooks* de sign-out y administración | **17/17** |
| SessionService | `vision_model` en el detalle | +1 |
| Regresión | Ejecutor **1641/1641** (0 omitidas; Vision 54 con inferencia real, Session 717); Web **490 / 3** (las mismas 3 del Operador, mismos mensajes); Admin 222; smoke SQLite OK; smoke real 51/51; integración 13/13; E2E MS-02 13/13 | sin regresiones |

### 12.6 Limitaciones y pendientes

1. La imagen guardada es el **fotograma ya anotado** por el worker. No existe un "original sin anotar" separado y no se inventa: Review Detail muestra una figura por checkpoint con las cajas reales superpuestas.
2. Cada vista de imagen genera una fila de auditoría `evidence.download_url` (diseño de DS05). Una página con muchas imágenes genera muchas filas.
3. La URL firmada apunta a `S3_PUBLIC_ENDPOINT`: en despliegue, Garage debe ser alcanzable por el navegador con esa dirección (en la pila principal ya se publica `S3_API_PORT`).
4. La revocación inmediata cubre las acciones **del portal** (desactivar, restablecer, cerrar sesión). Una revocación hecha directamente en AuthService por otra vía corta el WS como máximo al vencer el ticket (≤ 120 s).
5. Pendientes: **P6** y **P7**; recorrido de aceptación manual del Supervisor; modelo semilla sin pesos (MS-02 §11.6).

---

## 13. MS-03.1 — Regla del conteo final y cierre técnico de MS-03 (2026-11-04)

**Regla PEF:** total físico detectado en la última captura estable (cajas asignadas y no asignadas) = referencia
vigente (conteo inicial + incorporaciones confirmadas por el Supervisor). Si no coincide, el Operador repite la
captura; no hay confirmación, excepción ni botón para avanzar. La validación humana solo corrige la clasificación.

### 13.1 Diagnóstico del bypass

| Pregunta | Respuesta (código) |
|---|---|
| Endpoint que registra `close_blocked` | `POST /operator/sessions/<id>/capture/final` → `rf_session.confirm_final_count` → `_record_final_count_mismatch` (también `finish_vision_counting`, flujo antiguo de dos capturas, que ya bloqueaba sin excepción) |
| Parámetro que permitía continuar | `acknowledge_mismatch=1` (formulario; casilla `capture-final-ack-input` de `capture_rf.html`), leído en `routes._edge_count_request` |
| Función que aceptaba el final distinto | `confirm_final_count`: con `acknowledge_mismatch` registraba el `auto_count` `final` con `mismatch_acknowledged = true`; `vision_final_state` lo daba por `confirmed` |
| Ruta a la validación humana | `GET/POST /operator/sessions/<id>/vision-validation` → `submit_manual_close(close_path='vision_validation')`, abierta con `confirmed` (no con `valid`) |
| Validaciones existentes | Frontend: botón deshabilitado hasta marcar la casilla. Backend: 409 solo **sin** la casilla. La validación aceptaba cualquier total (familias + `extra_instruments`) y lo convertía en discrepancias |
| Llamada directa a la API | El mismo POST con `acknowledge_mismatch=1` (sin la interfaz) registraba el final distinto |
| Otros caminos | El **informe de cantidades (contingencia)** también avanzaba tras un rechazo de YOLO, documentándolo como discrepancia. SessionService: `POST /vision-counts` (`final`) guardaba cualquier total y `POST /count-report` no exigía un final válido (ambas escrituras del Operador están deshabilitadas por defecto: 410 sin `SESSION_OPERATOR_WRITES_ENABLED`) |
| Origen | **Línea base** `b417170` (ya estaba consignado como GLOBAL-01 en `PLAN_TRABAJO.md` y en `docs/PLAN_CONSOLIDACION_Y_ADMIN.md`). No lo introdujeron MS-01…MS-03 |

Limitación de diseño asociada: tras el conteo inicial solo se aceptaba **el mismo** video (mismo SHA-256), así que,
sin la confirmación, el Operador no tenía cómo repetir la captura final.

### 13.2 Corrección (backend; la interfaz solo refleja lo que decide el servidor)

- `vision_final_state`: `confirmed = valid = matches` (final más reciente que la última incorporación y total =
  referencia). Un final histórico con `mismatch_acknowledged` queda tal cual en la BD, marcado `legacy_acknowledged`,
  cuenta como diferencia conocida y **nunca** como final válido. Nuevo `last_rejected`.
- `confirm_final_count`: total ≠ referencia → siempre `close_blocked` + 409; `acknowledge_mismatch` se anota
  (`acknowledge_requested`) y se ignora. El final aceptado lleva `matches_reference = true` y `recapture`.
- **Re-captura** (`final_recapture`): tras un final rechazado y sin final válido posterior, el portal acepta subir y
  procesar una **nueva grabación** de la charola solo para el conteo final (`/capture/upload`, `/capture/play`,
  `confirm_final_count`); el conteo inicial, su video y su auditoría no cambian. Sin rechazo previo, otro video se
  sigue rechazando.
- `submit_manual_close`: validación humana solo con final válido; Σ validado + fuera del kit = total del final (400
  si no). Contingencia rechazada (409) si hay un final rechazado o histórico reconocido sin final válido posterior.
- Reintentos de la misma corrida: un solo `close_blocked` y una sola imagen de evidencia.
- `capture_rf.html`: sin casilla de confirmación del final; paso "recapture" (mensaje, subir/procesar habilitados).
- SessionService (`session_counts`): `final` con total ≠ referencia → `close_blocked` (mismo `client_event_id`) +
  409 `final_count_mismatch`; `count-report` exige final válido (409 `final_count_required`) y conserva el total
  (409 `validated_total_mismatch`). Sin referencia (sesiones DS09 sin conteo inicial) no cambia nada.
- Supervisor: el checkpoint de un final rechazado se etiqueta "Rejected final count".
- No se modificaron eventos ni sesiones existentes; la regla del conteo **inicial** frente al kit (con confirmación)
  no cambió.

### 13.3 Pruebas

- Portal (`test_single_video_flow.py`): referencia 15 con final 15/14/16; 15+2 con 17/15; referencia 4 con 5/4;
  detecciones no asignadas; confirmación explícita; llamada directa a la función de dominio; re-captura; otro video
  sin rechazo; validación que cambia el total; validación sin final válido; final histórico reconocido;
  reenvío/doble clic.
- SessionService (`test_ms031_final_count_rule.py`, 12): 6/5/7 frente a 6; 6+2 con 8/6; reintento con el mismo
  `client_event_id`; captura repetida; informe sin final válido; final anterior a una incorporación; informe que
  cambia el total; sesión histórica intacta; dos solicitudes concurrentes → un solo `close_blocked`.
- Pruebas antiguas actualizadas porque codificaban exactamente el bypass (documentado en cada una):
  `test_final_difference_needs_acknowledgment…` (→ `test_final_difference_cannot_be_acknowledged`),
  `test_unacknowledged_final_difference_cannot_be_hidden_by_a_contingency_report`,
  `test_physical_shortage_goes_to_the_supervisor…` (el faltante llega por contingencia sin rechazo),
  `test_instrument_outside_the_kit_needs_the_supervisor`, `test_missing_instrument_found_in_review_escalates`,
  `test_human_total_below_initial_escalates…`, `test_unconfirmed_out_of_kit_instrument_is_a_missing_piece`,
  `test_manual_report_cannot_hide_a_known_physical_shortage`,
  `test_contingency_report_with_the_missing_piece_has_a_single_discrepancy`,
  `test_vision_close_ai_equals_human_records_no_correction`.
- Las 3 fallas antiguas del Operador **no** son el bypass y siguen igual: `autoplay` de la vista WebSocket;
  `[14] == [14, 14]` (espera dos incidentes para la misma corrida; contradice la deduplicación por corrida);
  "Initial count registered: 15" (el *fixture* no trae calidad ni imagen del primer cuadro).
- **E2E real** (`scripts/e2e_ms031_final_count.py`, `best.pt` provisional, videos Motion-JPEG de
  `scripts/make_ms031_count_videos.py`): **19/19**.
  - A: referencia real 3, último cuadro 2 (una Crile Wood cubierta) → 409, también con confirmación;
    `close_blocked` conservado; sin final, sin validación ni contingencia; la API de SessionService no lo registra
    (410: escrituras del Operador deshabilitadas); repetir con el mismo video → 409; nueva grabación (3) → aceptada
    con `recapture`; validación que agrega un instrumento → 400; con el mismo total → aceptada.
  - B: 3 → 3 sin confirmación → validación; total físico conservado.
  - El caso exacto 4 → 5 no se puede forzar con el modelo provisional (con el mismo contenido da 3 en MJPG y daba
    4/5 en el mp4 comprimido: detecciones inestables); se cubre con pruebas deterministas, sin declararlo inferencia
    real.
  - WebSocket: conexión válida 135 s con 3 renovaciones; revocación directa en AuthService (`/logout`, sin pasar por
    el portal) → renovación imposible y cierre por el worker 112–113 s después (dos corridas) (`session_expired`, límite 120 s).
  - Supervisor: evidencias reales; el intento rechazado aparece como `close_blocked` y "Rejected final count",
    nunca como conteo final aceptado.

### 13.4 Riesgos y pendientes

- Sesiones en curso que ya tengan un final reconocido (anterior a MS-03.1) no pueden validar: deben re-capturar
  (los datos históricos no se tocan).
- El informe de contingencia sigue disponible cuando YOLO no puede usarse y no hubo rechazo; su total puede diferir
  de la referencia y va siempre al Supervisor como discrepancia. Decisión pendiente del equipo si también debe
  exigirse total = referencia.
- Un faltante físico real deja al Operador re-capturando hasta encontrarlo; no hay ruta de escalamiento propia del
  Operador más allá de la contingencia (sin rechazo previo) o de la intervención del Supervisor.
- **Propuesta futura (no implementada):** guardar también el fotograma original sin anotar junto al anotado.
- P6 sigue pendiente (P7 resuelta en MS-04, §14); repetir los E2E con los pesos definitivos.
- **Supervisor (MS-03/MS-03.1):** las pruebas de navegador están **automatizadas y aprobadas** (Chromium/Playwright en
  `e2e_ms03_evidence.py` y `e2e_ms031_final_count.py`); la **aceptación manual** por el equipo sigue pendiente.

---

## 14. MS-04 — API Gateway, CSRF (P7) y contratos JSON/XML (2026-11-08)

### 14.1 Comunicación antes de MS-04 (código)

- **Clientes externos:** no existía gateway. Los microservicios publicaban solo en `127.0.0.1`, así que ningún cliente externo podía consumirlos. AuthService (5001) y el portal (5000) eran los únicos públicos.
- **Portal:** llama a los servicios por la red interna con el Bearer del usuario (y a VisionWorker con HMAC). El navegador inicia sesión, renueva y cierra sesión **directamente** contra AuthService (rutas por cookie, CORS con lista de orígenes).
- **Hallazgos de seguridad:**
  1. El portal no tenía ninguna protección CSRF: 47 formularios POST y 17 llamadas `fetch` sin token, sin comprobar `Origin`, y la cookie de sesión de Flask sin `SameSite`.
  2. `AUTH_COOKIE_SAMESITE` valía `None` por defecto en el código.
  3. Las rutas por cookie de AuthService (`/logout`, `/refresh`, `/preferences`, `/password/change`) aceptaban peticiones de cualquier origen.
  4. AuthService tomaba la IP del cliente del primer valor de `X-Forwarded-For` enviado por cualquiera. Con el 5001 público, un atacante podía eludir el bloqueo de login por IP. La auditoría de login registraba la dirección del socket, que detrás de un proxy sería la del proxy.

### 14.2 API Gateway

Es nginx con configuración en `infra/gateway/`, el servicio `gateway` (perfil `gateway`) de `docker-compose.yml` y el override de producción `docker-compose.gateway-tls.yml`. Topología, rutas, variables y diferencias entre desarrollo y producción: `GUIA_API_GATEWAY.md`.
- Lista explícita de rutas, sin autenticación propia, y Bearer únicamente: elimina `Cookie` y `X-PEF-Signature` y oculta `Set-Cookie`.
- Sustituye `X-Forwarded-For` y garantiza un `X-Correlation-ID` único en la respuesta.
- Límites de peticiones (API, login, portal), de conexiones y de cuerpo, y tiempos de espera. Errores propios en JSON.
- `/ws` hacia VisionWorker.
- En producción: TLS 1.2/1.3, HSTS, redirección 308, el portal y las rutas de AuthService del navegador en un mismo origen HTTPS, y puertos propios de `auth` y `web` cerrados (`!reset`).
- No se generó ni se versionó ningún certificado.

**AuthService (justificación técnica, mínima):**
- `AUTH_TRUSTED_PROXIES` (0 por defecto): IP del cliente desde la entrada del proxy de confianza, también en la auditoría.
- `SameSite=Lax` por defecto.
- Guardián de `Origin` para las rutas que modifican estado autenticadas por cookie.

No cambian el JWT, Redis, los roles ni los endpoints.

### 14.3 CSRF del portal (P7) y cookies

- **Mecanismo** (`apps/BackendWebFlask/services/csrf.py`):
  - **Token:** doble envío firmado y ligado al usuario. El nonce va en una cookie `HttpOnly` y `SameSite=Strict` (`__Host-pef_csrf` con HTTPS). El token es `nonce.HMAC(SECRET_KEY, nonce|id del usuario o "anon")`.
  - **Dónde va:** en un campo oculto de los 47 formularios (`{{ csrf_field() }}`), en `<meta name="csrf-token">` y en la cabecera `X-CSRF-Token`, que `static/js/csrf.js` añade a `fetch`/XHR del mismo origen y a los formularios creados en el navegador.
  - **Alcance:** cada POST/PUT/PATCH/DELETE lo exige; el portal no tiene API Bearer, así que no hay excepciones.
  - **Defensa complementaria:** si llegan `Origin` o `Referer`, deben ser del propio sitio o de `CSRF_TRUSTED_ORIGINS`.
  - **Respuesta de rechazo:** 403 en JSON para AJAX o una página 403.
  - **Clientes de script:** `GET /csrf-token`.
- **WebSocket y servicios internos:** el ticket del WebSocket y el sondeo de captura son GET, y los microservicios solo aceptan Bearer. Ninguno se ve afectado.

| Cookie | Emisor | HttpOnly | SameSite | Secure |
|---|---|---|---|---|
| `access_token`, `refresh_token` | AuthService | Sí | `Lax` (antes `None` por defecto) | `AUTH_COOKIE_SECURE` (true en producción) |
| `session` (Flask) | Portal | Sí | `Lax` (antes sin atributo) | `PORTAL_COOKIE_SECURE` |
| `pef_csrf` / `__Host-pef_csrf` | Portal | Sí | `Strict` | `PORTAL_COOKIE_SECURE` (con prefijo `__Host-`) |
| `pef_locale` | Navegador | No (preferencia de idioma, no sensible) | `Lax` | — |

No se confía solo en `SameSite=Lax`: el token se verifica siempre.

### 14.4 Contratos JSON/XML

**Soporte declarado** (OpenAPI):
- Todas las operaciones de negocio de los siete servicios admiten JSON y XML por `Accept`; XML para los clientes de escritorio de DS09.
- Las de `/health` son JSON y además sirven XML no documentado.
- No se añadió XML a ningún endpoint.

**Validador** `scripts/validate_contracts.py`, ejecutado por `e2e_gateway.py` contra servicios reales: a través del gateway los publicados y directamente AuditService y MonitoringService.
- **Alcance:** recorre cada GET documentado en JSON y en XML, con identificadores reales, y además los errores 401 y 404.
- **Comprueba:**
  - que el código esté documentado;
  - JSON frente al esquema OpenAPI;
  - XML frente al XSD del servicio;
  - que `openapi.yaml` y los XSD servidos sean idénticos a los del repositorio.

**Resultado:** 176 respuestas comprobadas, **129 conformes, 0 violaciones y 47 huecos de documentación**; 1 operación sin identificador de muestra.
- **Violaciones:** ninguna. Las dos que aparecieron (`capture_mode` y `trigger` nulos) eran del validador: en OpenAPI 3.0 un `enum` anulable también admite `null`, y se corrigió en el conversor. No hizo falta cambiar el código de ningún servicio.
- **Huecos de documentación, que no contradicen el contrato** (listado completo en `.e2e/ms04_contracts.txt`):
  1. **Cobertura XSD (17):**
     - `identity.xsd` no declara `user` ni `permissions` (`/me`, `/permissions`);
     - `session-reports.xsd` solo cubre informes; no `sessions`, `session`, `expected_inventory`, `counts` ni `discrepancies`;
     - **EvidenceService no publica ningún XSD**, aunque documenta y sirve XML.
  2. **`/health/*` (30):** sirve XML que el OpenAPI no documenta; NotificationsService, AuditService y MonitoringService no documentan su cuerpo JSON. Son rutas internas, no publicadas por el gateway.

  Propuesta: completar los XSD de los recursos publicados (sesión, evidencias, identidad propia) antes del cliente de escritorio. Generarlos ahora habría ampliado MS-04 a un rediseño de contratos.

### 14.5 Autenticación y autorización a través del gateway

Probado con servicios reales (`e2e_gateway.py`):
- Token válido → 200.
- Expirado, firma inválida, `alg=none` o anónimo → 401 en los cuatro servicios publicados.
- Revocado en Redis (logout por el gateway) → 401 en todos, también su refresh.
- Rol sin permiso → 403: operador escribiendo en el catálogo, `it_admin` leyendo evidencias, operador programando una sesión.
- Otra institución → 404 en la sesión y en sus evidencias.
- Cookies a través del gateway → 401 (se eliminan).
- 27 rutas internas o administrativas → 404 del gateway.
- `X-Forwarded-For` falsificado → no llega a AuthService (la auditoría registra la dirección que vio el gateway).
- **`or_charge`:** AuthService lo reconoce (login, `/me`). No accede a sesiones ni evidencias (403). Lee el catálogo, como cualquier rol autenticado de su institución (diseño existente de CatalogService, `@require_auth()` sin roles), y sus propias notificaciones. MS-04 **no** le otorga ningún permiso nuevo; definirlo requiere un requisito funcional.

### 14.6 Decisiones funcionales pendientes (registradas, no implementadas)

1. **Contingencia técnica.** Cuando YOLO no esté disponible, el Operador podría registrar un *incidente técnico*:
   - estado del worker, motivo, responsable, hora y evidencias disponibles;
   - **sin** generar un conteo final YOLO aceptado y **sin** habilitar el cierre normal;
   - la sesión quedaría bloqueada hasta que el Supervisor la resuelva.

   Sustituiría el informe de cantidades actual, que admite un total distinto de la referencia cuando no hubo rechazo previo (§13.4). Requiere aprobación funcional y de seguridad.
2. **Posible faltante físico.** Desde un conteo final rechazado (`close_blocked`), el Operador podría *escalar* al Supervisor:
   - el incidente conserva las imágenes del intento rechazado, la referencia, el total y la auditoría;
   - **no** permite avanzar a la validación humana ni cerrar normalmente;
   - el Supervisor decide (búsqueda, faltante declarado, cierre no seguro).

   Hoy el Operador solo puede volver a capturar. Requiere aprobación funcional y de seguridad.
3. **P6** (avisos de privacidad por institución): sigue pendiente de decisión.

### 14.7 Resultados

| Suite | MS-03.1 | MS-04 |
|---|---|---|
| E2E del gateway (`e2e_gateway.py`, nuevo) | — | **31/31** |
| Contratos (dentro del E2E) | — | 176 comprobadas: 129 conformes, **0 violaciones**, 47 huecos de documentación |
| Ejecutor unificado (8 suites, inferencia real) | 1653 | **1670/1670**, 0 omitidas (AuthService 172 → 189) |
| BackendWebFlask (con CSRF activo en toda la suite) | 505 / 3 fallidas | **536 / 3 fallidas** (+31 de CSRF); las mismas 3 del Operador |
| Administración | 222 | **222** |
| Smoke SQLite | OK | **OK** |
| Smoke real | 51/51 | **51/51** |
| Integración Administración–Supervisor | 13/13 | **13/13** |
| E2E MS-02 / MS-03 / MS-03.1 (con CSRF) | 13 / 19 / 19 | **13/13 · 19/19 · 19/19** |
| Administración en navegador (formularios reales con CSRF) | 95/95 (ADMIN-09) | **95/95** |

Ninguna prueba se dio por buena estando omitida.
- **Administración en navegador:** registró un error transitorio de red de Windows (`net::ERR_NO_BUFFER_SPACE`) al cargar un recurso, sin efecto en las 95 comprobaciones. Sus evidencias versionadas de ADMIN-09 se restauraron; el resultado de esta corrida está en `.e2e/ms04_browser_admin_results.*`.
- **Puerto de Redis de prueba:** tras otro reinicio de Windows quedó reservado el 56379, y `.env.test` usa ahora 57379 (archivo local, sin tocar volúmenes).

### 14.8 Riesgos pendientes

- **Garage:** sigue publicado sin TLS (URLs prefirmadas de evidencias).
- **Límites:** son por instancia de nginx y no hay WAF.
- **`AUTH_TRUSTED_PROXIES` en desarrollo:** con el perfil `gateway`, AuthService sigue público en 5001, por eso vale 0 y los clientes del gateway comparten dirección ante el bloqueo de login. En producción vale 1 y el 5001 se cierra.
- **Cierre de sesión por GET:** `GET /sign-out` sigue existiendo (enlaces). Un cierre forzado entre sitios lo mitiga `SameSite=Lax` y su impacto es bajo.
- **`GET /capture/state`:** registra muestras de visión en un GET (comportamiento previo, idempotente y autenticado).
- **XSD incompletos:** ver §14.4.
- **JWT HS256 compartido:** sigue siendo un riesgo (§6.2); se propone RS256.
- **Validación pendiente:**
  - aceptación manual por el equipo del Supervisor y de Administración;
  - repetir los E2E con los pesos definitivos.

---

## 15. MS-05 — Cámaras, estaciones y monitoreo (2026-11-09)

### 15.1 Diagnóstico (antes de MS-05)

| Pieza | Qué existía | Qué faltaba |
|---|---|---|
| Salas y estaciones (Administración, monolito) | `capture_station` con ROI (RF-AD-07), formularios y auditoría | Ninguna tabla de cámaras |
| MonitoringService | Salud de servicios (colector cada 30 s, `monitoring_check`), API de solo lectura | Estaciones, cámaras, latido y alertas técnicas |
| Captura del Operador | Cámara del navegador (`getUserMedia`) para la vista previa por WebSocket; la sesión tenía `station_id` | Asociar la cámara usada; ninguna señal de estado de la estación |
| VisionWorker | `/health` con conexiones en vivo | Ninguna evidencia, del lado del servidor, de que llegan fotogramas de una cámara |

### 15.2 Implementado (sin servicios nuevos)

- **Migración 012** (`012_estaciones_camaras.sql`, idempotente y aplicada dos veces): `capture_camera` (con un *trigger* que impide asociar una cámara a otra institución), `station_status`, `station_event` (una alerta abierta por tipo como máximo) y `work_session.camera_id`.
- **Administración:** cámaras en *Configuración* (`/admin/configuration/cameras/new` y `…/edit`) con tipo, resolución, FPS, ROI, estado y auditoría (`CREATE_` / `UPDATE_` / `ACTIVATE_` / `DEACTIVATE_CAPTURE_CAMERA`).
- **MonitoringService:**
  - `POST /stations/{id}/heartbeat` (`station_operator`);
  - `GET /stations`, `GET /stations/{id}` y `GET /alerts` (Supervisor y Administrador);
  - el colector lee `GET /activity` de VisionWorker y abre `heartbeat_lost`;
  - JSON y XML (XSD actualizado), OpenAPI actualizado.
- **VisionWorker:** `GET /activity` con el último fotograma **válido** recibido en vivo por sesión. Solo cuenta un JPEG decodificado de una conexión autorizada; no incluye imágenes ni usuarios.
- **Operador:** elige la cámara entre las activas de la estación de la sesión (auditada) y la página envía un latido cada 30 s a través del portal. Los conteos guardan `capture_device`. Sin cambios en reglas, referencias ni validaciones, y sin bloqueos nuevos.
- **Supervisor:** *Salud de servicios* muestra alertas técnicas pendientes y estaciones con sus cámaras. La pantalla de Administración no cambió.
- **API Gateway:** publica **solo** el latido, porque lo necesitan los clientes de estación de escritorio. Las listas y las alertas son pantallas del portal y no se publican.

### 15.3 Pruebas de MS-05

- **MonitoringService:** `test_ms05_stations.py`, 24 pruebas con PostgreSQL y Redis reales: alta y relación cámara–estación, aislamiento entre instituciones (API y *trigger*), latido válido, sin autorización y falsificado, estación que deja de reportar, error de cámara, recuperación, cámara o estación desactivadas, consulta del Supervisor, colector con VisionWorker caído, XML frente a XSD y persistencia tras reinicio.
- **VisionWorker:** 2 pruebas con servidor y WebSocket reales. Solo fotogramas válidos de conexiones autorizadas; nada ante un ticket falso.
- **Portal:** `test_ms05_stations_portal.py`, 20 pruebas: cámaras en Administración, elección de cámara, evidencia en el conteo, latido que nunca bloquea la captura y vista del Supervisor.
- **E2E real** (`scripts/e2e_ms05_stations.py`): **14/14**, con AuthService, portal, SessionService, la API y el colector de MonitoringService, VisionWorker en contenedor con el `best.pt` provisional y Chromium.
- **Requieren hardware real (pendiente):** cámaras USB o IP físicas, errores reales del dispositivo, y resolución y FPS negociados.

## 16. MS-06 — Reportes, indicadores y notificaciones (2026-11-09)

### 16.1 Diagnóstico (antes de MS-06)

| Pieza | Qué existía | Qué faltaba |
|---|---|---|
| Reportes (SessionService `/reports/summary`, `/kits`, `/operators`, `/options`) | Sesiones por estado, discrepancias, acuerdo IA–humano (mínimo 5 pares), actividad diaria, kits y operadores; pantallas v3 Reportes e Indicadores | Resultado de cada cierre (aprobada / no resuelta), resultados del conteo automático, intervenciones humanas, actividad por estación, filtro de estación, exportación |
| NotificationsService | Outbox por *triggers*, worker, 5 tipos, API JSON/XML, campana del portal | Sesión pendiente de revisión, cierre no resuelto, alertas técnicas de estación/cámara, canal de correo |
| Escalamiento al Supervisor | Solo `discrepancy_pending` (el estado `or_hold` no existe en ningún flujo) | No se implementa un escalamiento nuevo: depende de las decisiones pendientes de contingencia técnica y faltante físico |

Para no duplicar, MS-06 **amplía** los endpoints y pantallas existentes; no hay un servicio de reportes ni cálculos en el portal.

### 16.2 Implementado

- **SessionService:**
  - `summary.outcomes`, `summary.automatic_counts` y `summary.interventions`;
  - `GET /reports/stations` (raíz XML `station_report`);
  - filtro `station_id` en todos los reportes (otra institución → 400);
  - `GET /reports/export?format=csv|pdf&lang=en|es-MX`: mismos filtros, todas las páginas, CSV con BOM y protección contra fórmulas, PDF (fpdf2), "Sin datos" en secciones vacías y auditoría `report.export.*`;
  - XSD y OpenAPI actualizados; dependencia nueva `fpdf2`.
- **Separación de métricas:** productividad (resultados), funcionamiento del sistema (conteo automático: finales aceptados al primer intento, intentos rechazados, recapturas, tiempos) e IA (acuerdo IA–humano). **No se presenta precisión del modelo**: no hay datos de referencia (ground truth); la pantalla y el archivo lo dicen.
- **Portal:** filtro de estación, sección A2 "Resultados y conteo automático", sección E "Actividad por estación" (tabla + Highcharts), botones Exportar CSV/PDF (`/supervisor/reports/export.<fmt>`, mismo filtro e idioma; nombre de archivo saneado). Sin rediseño.
- **NotificationsService (migración 013):**
  - tipos `review_pending`, `session_unresolved` (en lugar de `session_closed` cuando `safe_close_confirmed = false`) y `station_alert` (desde `station_event` de MS-05, para Supervisores SPD y Administradores de TI de la institución);
  - una alerta no leída por estación y tipo; las recuperaciones no avisan;
  - canal de correo opcional: tabla `notification_delivery`, envío en el worker después de confirmar la notificación in-app, reintentos y estado `failed`; desactivado sin `NOTIFICATIONS_SMTP_HOST` / `_FROM`. Sin credenciales en el repositorio; sin notificaciones push;
  - las notificaciones nunca cambian el estado de la sesión.
- **Gateway:** la exportación **no** se publica (descarga del navegador a través del portal); `reports/stations` queda bajo la regla existente de sesiones (cliente de escritorio del Supervisor).
- **Traducciones es-MX:** 108 cadenas nuevas o que faltaban (MS-02 a MS-06).

### 16.3 Pruebas de MS-06

- **SessionService:** `test_ms06_reports.py`, 12 pruebas con PostgreSQL real: resultados, conteo automático e intervenciones calculados a mano; actividad por estación; aislamiento del filtro; periodo vacío; CSV (BOM, encabezados, fórmulas neutralizadas); PDF; permisos; auditoría de descargas; XML frente al XSD.
- **NotificationsService:** `test_ms06_notifications.py`, 10 pruebas (ver `GUIA_NOTIFICATIONS.md` §7.3), con un servidor SMTP de prueba local. En `test_outbox.py`, la prueba "reporte sin discrepancias no avisa a nadie" se reemplazó por "pide revisión al Supervisor", porque MS-06 exige ese aviso.
- **Portal:** `test_ms06_reports_portal.py`, 9 pruebas: secciones nuevas, filtro de estación en todas las llamadas, gráfica, botones con los filtros, proxy de exportación (token, filtros, idioma, nombre hostil saneado, permisos, formatos, servicio caído o filtro rechazado) y tipos de notificación en inglés y español. `test_reports_remote.py` y `test_monitoring_remote.py` agregan las llamadas nuevas a la lista esperada (la pantalla ahora también lee estaciones y alertas).

## 17. Integración MS-05 + MS-06 (Parte C)

`scripts/e2e_ms06_reports.py`: **15/15** con procesos reales (AuthService, portal, SessionService, NotificationsService + worker, MonitoringService + colector, VisionWorker con el `best.pt` provisional) y un receptor SMTP local. Recorrido:

1. La estación reporta y Monitoring la registra.
2. Un error de cámara genera una alerta técnica notificada, sin duplicados.
3. S1, por visión en la estación: conteo YOLO real, validación con faltante y cierre reconocido → `session_unresolved`.
4. S2, por contingencia: `review_pending` → cierre seguro → `session_closed`.
5. Los reportes con filtro de estación crecen exactamente lo ocurrido; una estación de otra institución es rechazada.
6. Pantalla de Reportes del Supervisor y notificaciones en el portal.
7. Correos recibidos por el receptor local.
8. Exportación CSV/PDF auditada; el Operador no puede exportar.

## 18. Cobertura de las 20 responsabilidades después de MS-06

| # | Responsabilidad | Estado | Evidencia / lo que falta |
|---|---|---|---|
| 1 | Auth e identidad | Implementado | AuthService + gateway (MS-04). Falta: *client credentials* para clientes máquina |
| 2 | Catálogos | Implementado | MS-01 (P3, P4) |
| 3 | Sesiones y conteo | Implementado | Modelo fijado por sesión (MS-02), regla del conteo final (MS-03.1); estación y cámara guardadas como evidencia (MS-05) |
| 4 | Evidencias | Implementado | MS-03 (imágenes reales al Supervisor) |
| 5 | Auditoría | Implementado | Ahora también cámaras, selección de cámara y descargas de reportes. Falta: contrato de evento común |
| 6 | Notificaciones | **Implementado** (in-app + correo opcional) | MS-06. Reglas no configurables desde la interfaz; correo probado solo con un servidor SMTP de prueba (falta un proveedor real en el despliegue) |
| 7 | Monitoreo | Implementado | Servicios (antes) + estaciones, cámaras y alertas técnicas (MS-05) |
| 8 | Cámaras | **Implementado en software; parcial en campo** | Registro, selección, latido y verificación por el servidor (MS-05, E2E real). Falta: validación con cámaras USB/IP físicas, errores reales de dispositivo y resolución/FPS negociados |
| 9 | Video en tiempo real y WebSockets | Implementado | WSS a través del gateway (MS-04); revocación (MS-03) |
| 10 | Inferencia de visión | Implementado | Pesos verificados (MS-02). Falta: modelo YOLO definitivo |
| 11 | Tracking de instrumental | Parcial | Sin cambios en MS-05/06 |
| 12 | Comparación de inventario | Implementado | Sin cambios de reglas |
| 13 | Discrepancias | Implementado | Sin cambios |
| 14 | Validación humana | Implementado | Sin cambios (portal); API externa opcional |
| 15 | Supervisión | Implementado | Ahora también estaciones, alertas, resultados y exportación. Falta: aceptación manual del Supervisor |
| 16 | Kits y charolas | Parcial | "Charola" no existe como entidad |
| 17 | Modelos de IA | Implementado (trazabilidad) | MS-02. Falta: modelo definitivo; la precisión del modelo no se mide (sin ground truth) |
| 18 | Reportes e indicadores | **Implementado** | MS-06: resultados, conteo automático, intervenciones, estaciones, exportación CSV/PDF auditada. No hay exportación XML (los endpoints JSON/XML ya cubren el intercambio) |
| 19 | Sincronización | Parcial | Sin cambios (Android pendiente por instrucción) |
| 20 | Integración hospitalaria | No implementado | FHIR pendiente por instrucción |

### 18.1 Riesgos y pendientes después de MS-06

- TLS de Garage; cobertura XSD incompleta (huecos listados por `validate_contracts.py`); configuración de proxy/JWT de producción; P6; aceptación manual de Administración y Supervisor; modelo YOLO definitivo.
- Decisiones pendientes: contingencia técnica y posible faltante físico (no se implementó un escalamiento nuevo).
- Cámaras físicas (USB/IP) sin probar; correo sin proveedor real configurado.
- "Aceptados al primer intento", los tiempos y el acuerdo IA–humano describen el funcionamiento del sistema, no la precisión del modelo.

## 19. MS-07 + MS-08 — Sincronización, integración hospitalaria, charolas y privacidad (2026-11-09)

Respaldo previo: commit **`136a698`** (MS-05 + MS-06; capturas JPEG de ADMIN-09 excluidas). Sin push.

### 19.1 Fase 1 — Verificación de seguridad del conteo (sesiones del E2E MS-06)

**Caso A (S1, `443e02ce…`, visión):**
- Conteo inicial YOLO = 3; referencia vigente = 3 (inicial 3, incorporaciones 0); conteo final YOLO = 3 → aceptado sin confirmación.
- Validación humana: Pozzi 0 + 3 fuera del kit = 3 = total físico verificado (`verified_total = 3`).
- El "faltante" es frente al **inventario esperado del kit** (el kit esperaba 1 Pozzi; YOLO no clasificó ninguna caja como Pozzi), no frente al total físico. La validación cambió la **clasificación**, no la cantidad.
- Discrepancias: faltante (registrado como perdido por el Supervisor) y excedente fuera del kit (resuelto). Cierre por `supervisor@` con `safe_close_confirmed = false` y el faltante sin conciliar → `session_unresolved`.
- **Regla respetada; sin bypass.**

**Caso B (S2, `a9c17072…`, contingencia):**
- Motivo `worker_unavailable`, declarado con el worker **inactivo pero disponible** (`status idle`, 0 cuadros, sin video, sin referencia física).
- Cantidad informada 1 = esperado 1 → `awaiting_spd_review`.
- Revisión y cierre: `supervisor@instrumed.com`, `contingency_reviewed = true` → `safe_close_confirmed = true`. Es una **resolución excepcional**, pero Reportes la cuenta como "aprobada (cierre seguro)" (los informes de contingencia sí se cuentan aparte en "Intervenciones humanas").

**Bloqueador de seguridad registrado (no corregido sin decisión):** la contingencia técnica puede declararse sin evidencia de que el worker falló, y un cierre sin conteo físico automático termina como cierre seguro. Es exactamente la decisión pendiente §14.6.1 (contingencia técnica). No se modificó el flujo ni las pruebas.

### 19.2 MS-07 A — Sincronización y clientes externos

- **IntegrationService** nuevo (puerto 5009, worker), justificado por sus responsabilidades transversales; ninguna regla de negocio duplicada (ver `GUIA_SINCRONIZACION.md` §1).
- **Migración 014:** `sync_change` con `xid8` (triggers transaccionales en 9 recursos), `sync_resource_version`, `sync_seed` (backfill), `sync_operation` (idempotencia), columnas de rotación en `integration_client`, `integration_endpoint`, `integration_delivery`, `integration_delivery_attempt`.
- **Consistencia:** marca de agua `pg_snapshot_xmin` + orden `(txid, seq)`: sin omisiones ni duplicados con transacciones concurrentes (probado con una transacción larga que confirma tarde); cursor firmado (400 alterado, 410 caducado); compactación que conserva el último cambio de cada recurso.
- **Operaciones idempotentes:** `session.schedule`, `tray.create`, `tray.update`, `notification.read`, reenviadas al dueño con el token del usuario; repetición, discrepancia de clave (422), concurrencia (409), resultado incierto (502) reconciliado por la auditoría del dueño. Conteos, validación, cierres, auditoría y modelos: inexistentes (400).
- **Autenticación:** personas (escritorio/Android) = login API existente; máquinas = `POST /api/v1/auth/token` (client credentials, AuthService) con alcances DS08 + `sync.*`, revocables (rotación y desactivación en el portal de Administración, que revoca vía AuthService). Un token de cliente no sirve en ningún servicio de usuarios.
- **Gateway:** publica solo `/api/v1/auth/token`, `/api/v1/sync/*` (lista explícita), `/fhir/r4/*` (lista explícita) y `/api/v1/catalog/trays`; `/api/v1/integration/*` no.
- **Contratos:** los 47 huecos de MS-04 → **0** (0 violaciones; 192 respuestas conformes). Cambios solo de documentación: XML de salud en OpenAPI, raíces `user`, `permissions`, `health` y listas de sesiones en los XSD, y `EvidenceService/schemas/evidence.xsd`. Las raíces de sesiones y evidencias se declaran con contenido abierto (la estructura campo a campo está en el OpenAPI).

### 19.3 MS-07 B — Integración hospitalaria FHIR R4

- Proyección de **solo lectura** (DS08): Organization, Location, Practitioner, Patient (solo con `fhir.patient.read`), Encounter (← `operation`), búsquedas y Bundle `$pef-bundle`. Sesiones de conteo, conteos y evidencias **no** se mapean (DS08: fuera del MVP).
- Entregas a destinos autorizados (`INTEGRATION_ALLOWED_HOSTS`, vacío por defecto; https salvo en la pila de pruebas): Bundle `transaction` con `PUT Tipo/<id PEF>` (idempotente), reintentos con *backoff*, intentos registrados, auditoría.
- **Niveles de prueba (sin mezclarlos):** dobles HTTP en las pruebas unitarias; servidor FHIR de pruebas **real** (HAPI R4 v7.4.0, contenedor) en el E2E, que guardó un solo Encounter tras dos entregas y una caída, y cuyo `$validate` no dio errores (sí advertencias: clase `UNK`, participante sin código, sin narrativa). **No** hay integración con un HIS real.
- **Pendiente de validación externa:** aprobación semántica del mapeo, autenticación hacia el HIS, interfaz de destinos/entregas, disparadores automáticos.

### 19.4 MS-08 — Kits, charolas y privacidad

- **Charolas** (migración 015, CatalogService): entidad física con código, institución, versión de kit (referencia histórica: un kit reemplazado se conserva y se señala), estados administrativos, control de versión (409), historial automático, auditoría, sincronización. `work_session.tray_id` opcional y validado; las cantidades esperadas nunca cambian. Pantallas del Supervisor (lista, detalle con historial y sesiones, alta/edición). Decisiones pendientes: ciclo operativo, uso simultáneo, obligatoriedad, campo en el formulario de programación (`GUIA_CHAROLAS.md` §4).
- **P6:** **pendiente de decisión** (los RF no definen la transición de los avisos existentes y D-2 los declaró compartidos). Solo se entregó el informe de solo lectura `scripts/p6_privacy_usage.py` y la propuesta compatible (`GUIA_PRIVACIDAD_P6.md`). Sin migración, sin cambios en la aceptación de avisos.

### 19.5 Parte D — Integración del bloque

`scripts/e2e_ms07_ms08.py`: **25/25** con procesos reales, Gateway y HAPI:

1. Cliente máquina creado en Administración.
2. Token por el Gateway.
3. Primera sincronización y sincronización incremental.
4. Charola creada por operación idempotente (reintento sin duplicar).
5. Sesión programada con la charola (reintento sin duplicar).
6. Conteo YOLO real con las reglas intactas.
7. Evidencias en EvidenceService.
8. Revisión del Supervisor.
9. Reportes y notificaciones.
10. Encounter FHIR por el Gateway; entrega a HAPI idempotente y recuperada tras una caída.
11. Auditoría de todo el intercambio.
12. Revocación por rotación y desactivación.

Datos de prueba: los E2E que ejercen el faltante físico devuelven al final, solo en la base aislada, los instrumentos que ellos mismos marcaron como perdidos (`scripts/e2e_restore_stock.py`); el E2E de MS-07/08 resuelve su faltante como recuperado.

### 19.6 Cobertura de las 20 responsabilidades después de MS-08

| # | Responsabilidad | Estado | Evidencia / lo que falta |
|---|---|---|---|
| 1 | Auth e identidad | **Implementado y probado** | + clientes máquina (client credentials, revocación). Producción: gestor de secretos, mTLS opcional |
| 2 | Catálogos | Implementado y probado | + charolas en el catálogo |
| 3 | Sesiones y conteo | Implementado y probado | + charola opcional; **bloqueador registrado**: contingencia sin evidencia de fallo (§19.1) |
| 4 | Evidencias | Implementado y probado | XSD publicado (MS-07) |
| 5 | Auditoría | Implementado y probado | + actor `client` (tokens, sincronización, FHIR) |
| 6 | Notificaciones | Implementado y probado | Correo solo con SMTP de prueba (pendiente de validación externa) |
| 7 | Monitoreo | Implementado y probado | — |
| 8 | Cámaras | Implementado parcialmente | Hardware físico pendiente de validación externa |
| 9 | Video en tiempo real y WebSockets | Implementado y probado | — |
| 10 | Inferencia de visión | Implementado y probado | Modelo definitivo pendiente |
| 11 | Tracking de instrumental | Implementado parcialmente | Sin cambios |
| 12 | Comparación de inventario | Implementado y probado | Sin cambios de reglas |
| 13 | Discrepancias | Implementado y probado | — |
| 14 | Validación humana | Implementado y probado | API externa deliberadamente inexistente (las operaciones de conteo no se sincronizan) |
| 15 | Supervisión | Implementado y probado | + charolas. Aceptación manual pendiente |
| 16 | Kits y charolas | **Implementado parcialmente** | Charola física implementada y probada; ciclo operativo **pendiente de decisión funcional** |
| 17 | Modelos de IA | Implementado y probado (trazabilidad) | Modelo definitivo pendiente |
| 18 | Reportes e indicadores | Implementado y probado | — |
| 19 | Sincronización | **Implementado y probado** (API) | Clientes Android/escritorio aún no existen: probado con clientes HTTP reales por el Gateway |
| 20 | Integración hospitalaria | **Integrado con un servidor FHIR de pruebas; pendiente de validación externa** | Mapeo semántico sin aprobar; ningún HIS real |
| — | Privacidad institucional (P6) | **Pendiente de decisión funcional** | Informe de solo lectura y propuesta compatible |

### 19.7 Riesgos y decisiones pendientes después de MS-08

- **Seguridad del conteo:** contingencia técnica sin evidencia de fallo terminando en cierre seguro (§19.1, decisión §14.6.1); posible faltante físico (§14.6.2).
- P6; ciclo operativo de charolas y su obligatoriedad; aprobación semántica FHIR; autenticación hacia un HIS; UI de destinos/entregas FHIR.
- TLS de Garage; proxy/JWT de producción; secretos de clientes máquina en un gestor; aceptación manual de Administración y Supervisor; modelo YOLO definitivo; cámaras físicas; proveedor SMTP real.
- Las 3 fallas preexistentes del Operador siguen sin corregir (fuera de alcance).

## 20. MS-09 — Consolidación funcional, seguridad del conteo y preparación del despliegue (2026-11-09)

Respaldo previo: commit **`62542e4`** (MS-07 + MS-08). Sin push.

### 20.1 Seguridad del conteo

- **Contingencia técnica:** el `worker_unavailable` del E2E de MS-06 lo indujo la prueba, pero cualquier Operador podía declararlo (vulnerabilidad demostrable). Ahora el servidor verifica el motivo: *verificado* / *declarado*; `worker_unavailable` con el worker sano y "detección no confiable" sin detección se **rechazan**. Se guardan evidencia, estación, cámara, usuario y hora. Nunca produce un conteo final YOLO. El Supervisor reconoce explícitamente un motivo no verificado. El cierre registra `close_basis` y Reportes separa `approved_contingency`. Detalle: `GUIA_CONTEO_CONTINGENCIA.md` §1.
- **Escalamiento de un final rechazado** (nuevo, con mecanismos existentes): discrepancia ligada a la captura rechazada, `correction_required`, aviso al Supervisor, auditoría, sin final ni validación; nunca cierre seguro (`close_basis = final_count_escalation`). Probado 15→14, 15→16, 15→15 (rechazado) y referencia modificada por incorporaciones. Detalle: `GUIA_CONTEO_CONTINGENCIA.md` §2.
- **Ambos caminos de cierre** (portal y SessionService) aplican las mismas comprobaciones.

### 20.2 Las tres pruebas históricas del Operador (fallaban desde `b417170`)

| Prueba | Diagnóstico | Corrección |
| :--- | :--- | :--- |
| `test_capture_page_shows_the_new_steps` | Prueba desactualizada: el `<video autoplay>` es la vista previa en vivo (WebSocket, oculta, nunca cuenta), no el video subido | La prueba exige ahora exactamente ese elemento y ningún reproductor del video |
| `test_retry_after_14_of_15_uses_only_the_new_capture` | Expectativa incompatible con una regla deliberada (un `close_blocked` por corrida rechazada; los reintentos no inflan "intentos rechazados") | `[14]` en lugar de `[14, 14]`; el reintento sigue rechazado |
| `test_capture_page_shows_stage_and_mismatch_reason` | *Fixture* incorrecto: el cuadro simulado no tenía `quality.ok` ni la imagen JPEG exigidas por el servidor | *Fixture* completo |

Ningún defecto funcional; ninguna regla cambiada. **La suite del portal corre completa sin fallos.**

### 20.3 Charolas, P6, infraestructura y despliegue

- **Charolas:** historial de asignaciones (auditoría), cambio mientras está programada, bloqueo `FOR SHARE`, *trigger* de institución (migración 016), sin cambios automáticos de la referencia (`GUIA_CHAROLAS.md` §6).
- **P6:** sigue pendiente de decisión. Propuesta SQL compatible preparada y validada sin aplicar (`docs/propuestas/p6_avisos_institucionales.sql`); opciones de consentimiento e impacto en `GUIA_PRIVACIDAD_P6.md` §5.
- **Infraestructura:** `GUIA_SEGURIDAD_INFRAESTRUCTURA.md`: front TLS de Garage, nginx de producción renderizado desde las plantillas del Gateway (sintaxis verificada con `nginx -t`), *preflight* de secretos, propuesta de rotación JWT y migración a firma asimétrica (no implementada), endpoints internos, logs, límites y revocación.
- **Defecto corregido:** *deadlock* intermitente al arrancar Monitoring y Notifications a la vez (migraciones 012/013 sobre las mismas tablas con claves de bloqueo distintas) → una única clave compartida `pef:schema:migration`.
- **Despliegue:** `GUIA_DESPLIEGUE_GCP_CENTOS10.md` + `infra/vm/` (13 unidades systemd endurecidas en `127.0.0.1`, entornos de ejemplo, nginx, Garage TLS) + `scripts/render_gateway_vm.py`, `scripts/preflight_production.py`. Nada se ejecutó en la VM.

### 20.4 Roles

`tests/test_ms09_role_matrix.py`: `it_admin`, `spd_supervisor`, `station_operator` llegan solo a su área (403 en las demás); `or_charge` no recibe ningún área nueva (sin definición funcional); sin sesión todo redirige al inicio de sesión. Las mismas reglas están probadas en cada microservicio y a través del Gateway.

### 20.5 Matriz de aceptación

| Responsabilidad / función | Estado |
| :--- | :--- |
| 1 Auth e identidad (usuarios, clientes máquina, revocación) | Implementado y probado |
| 2 Catálogos | Implementado y probado |
| 3 Sesiones y conteo (incl. regla del final, contingencia verificada, escalamiento) | Implementado y probado · cierres excepcionales pendientes de decisión funcional |
| 4 Evidencias | Implementado y probado · pendiente de aceptación manual del Supervisor |
| 5 Auditoría | Implementado y probado |
| 6 Notificaciones | Implementado y probado · correo pendiente de integración externa (proveedor SMTP) |
| 7 Monitoreo | Implementado y probado |
| 8 Cámaras | Probado solo en entorno aislado · pendiente de hardware real |
| 9 Video en tiempo real / WebSocket | Implementado y probado · WSS en la VM pendiente del despliegue |
| 10 Inferencia de visión | Implementado y probado con el modelo provisional · modelo definitivo pendiente |
| 11 Tracking | Implementado parcialmente (sin cambios) |
| 12 Comparación de inventario | Implementado y probado |
| 13 Discrepancias | Implementado y probado |
| 14 Validación humana | Implementado y probado · pendiente de aceptación manual |
| 15 Supervisión | Implementado, pendiente de aceptación manual |
| 16 Kits y charolas | Implementado y probado (técnico) · ciclo operativo, obligatoriedad y uso simultáneo pendientes de decisión funcional |
| 17 Modelos de IA | Implementado y probado (trazabilidad) · modelo definitivo pendiente |
| 18 Reportes | Implementado y probado |
| 19 Sincronización | Implementado y probado (API) · clientes Android/escritorio aún no existen |
| 20 Integración hospitalaria | Probado solo en entorno aislado (HAPI de pruebas) · pendiente de integración externa y aprobación semántica |
| P6 privacidad institucional | Pendiente de decisión funcional |
| Despliegue GCP / CentOS 10 | Preparado; pendiente de ejecución y validación en la VM |
| Seguridad de infraestructura (TLS, secretos, JWT) | Preparado; probado solo en entorno aislado; certificados y Secret Manager pendientes |

### 20.6 Evidencias pendientes de aceptación manual

Recorrido del Supervisor (revisión, evidencias, cierre de contingencia y de escalamiento); recorrido de Administración (clientes máquina, cámaras, charolas); Operador con cámara física y video real del hospital; vista previa WSS en la VM; descarga de evidencias por HTTPS de Garage.

### 20.7 Resultados de las pruebas (MS-09)

| Tipo | Resultado |
| :--- | :--- |
| Ejecutor de microservicios (9 suites) | **1791/1791** (línea base 1784 + 7) |
| Portal BackendWebFlask (suite completa) | **597/597, 0 fallos** |
| Administración / AuthService | 222 / 199 |
| Smoke SQLite / smoke real / integración Admin–Supervisor | OK / 51/51 / 13/13 |
| E2E (inferencia YOLO real con el modelo provisional) | MS-02 13/13, MS-03 19/19, MS-03.1 19/19, MS-05 14/14, MS-06 16/16 (×3), MS-07/08 25/25, **MS-09 7/7** |
| E2E Gateway (TLS con certificado desechable) | 32/32; contratos 192/192 conformes, 0 huecos |
| Simulado (dobles HTTP) | Pruebas unitarias de servicios dueños/FHIR (IntegrationService), portal con servicios simulados |
| Hardware físico | **No probado** (cámaras USB/IP) |
| Producción (VM, certificados, Secret Manager) | **No ejecutado** (preparado y verificado en sintaxis) |

Incidencias de la regresión, corregidas: (1) *deadlock* de una prueba de SessionService causado por la validación de la propuesta P6 ejecutada a la vez sobre la misma base (no es un defecto; repetida: 0 errores); (2) *deadlock* real intermitente entre migraciones de Monitoring y Notifications al arrancar juntas → clave de bloqueo compartida (§20.3); MS-06 repetido tres veces sin fallos.

## 21. MS-10 — Cierre técnico, pruebas integrales y preparación del despliegue (2026-11-09)

Respaldo previo: commit **`bc680c6`** (MS-09). MS-10 queda **sin commit** y sin push, por instrucción.

### 21.1 Arquitectura verificada contra el código (`scripts/verify_architecture.py`, 57/57)

| Afirmación | Dónde se comprobó | Resultado |
| :--- | :--- | :--- |
| Administración en el portal (en proceso) | `services/admin_local/api.py` (`LocalAdminRouter`) | Sí |
| Operador en el portal | `services/rf_session.py` (inicial, final, validación, contingencia, escalamiento) | Sí |
| Supervisor mediante microservicios | `controllers/supervisor_sessions.py` → `session_remote`, `trays_remote`, `monitoring_remote`, `notifications_remote` (`*_SERVICE_URL`) | Sí |
| AuthService es el único emisor de tokens | `jwt.encode` solo en `apps/BackendAuthService`; los 7 servicios validan JWT y revocación `auth:jwt:` | Sí |
| VisionWorker usa el modelo fijado por sesión | `model_store.py` (SHA-256) + E2E MS-02 | Sí |
| EvidenceService guarda la evidencia (S3/Garage) | `requirements.txt` (boto3) + imágenes reales en el navegador | Sí |
| IntegrationService: sincronización e integración | `main.py` (`sync_bp`, `fhir_bp`, `integration_bp`) | Sí |
| nginx es el Gateway | plantillas `infra/gateway` + E2E gateway | Sí |
| Puertos 5000–5009 iguales en Compose, systemd y nginx VM | `docker-compose.yml`, `infra/vm/systemd`, `gateway.env.example` | Sí |
| Migraciones 001, 004–016 + seeds 002/003, en orden en ambos Compose; copias idénticas | `data/`, Compose, `apps/*/migrations` | Sí |

Base **nueva** y arranque concurrente (`scripts/check_migrations_concurrency.py`): esquema completo, segunda pasada idempotente y 5 arranques simultáneos de Monitoring, Notifications e Integration (API y workers) sin *deadlock* (10/10).

### 21.2 Navegador real (Chromium)

- **Operador y Supervisor** (`scripts/browser_validate_flows.py`): **33/33**, YOLO real con `best.pt` provisional. Recorrido: programación con el asistente y el modal de privacidad; inicio; cámara; inicial (3); final rechazado por una pieza retirada (2 ≠ 3); recaptura aceptada (3); validación que intenta cambiar el total rechazada; revisión; historial; escalamiento (`correction_required`, sin final); contingencia contradicha (409) y declarada (sin final); evidencias reales; resolución de discrepancias; tres cierres (`final_count`, `final_count_escalation` nunca seguro, `contingency_count`), con solo las casillas que la página muestra; notificaciones, reportes, indicadores, CSV/PDF, salud y auditoría. Capturas en `docs/evidencias/MS-10/` (no versionadas).
- **Administración** (`scripts/browser_validate_admin.py`): 95 PASS · 0 FAIL · 0 BLOCKED (RF-AD-01..10; la aceptación manual sigue pendiente; RF-AD-11 no se tocó).
- **Defecto encontrado y corregido:** el formulario de escalamiento de MS-09 estaba dentro del panel del conteo final, que la página oculta en la etapa de recaptura, así que el Operador nunca lo veía. Las pruebas de MS-09 solo comprobaban que el HTML existía. Se movió fuera del panel, sin cambiar reglas, y se añadió la regresión `test_escalation_form_is_not_inside_the_final_panel`, que falla con la plantilla anterior.

### 21.3 Despliegue

- `GUIA_DESPLIEGUE_GCP_CENTOS10.md` reescrita: inventario de solo lectura, tabla de decisiones (la VM no se supone vacía), comandos **[INSPECCIÓN]** separados de **[MODIFICA]**, `cp -n` y `diff` en lugar de sobrescribir, secuencia de base nueva corregida (001 solo en base vacía, **002 obligatorio**, **003 nunca**; la guía anterior omitía 002), Redis, MongoDB, Garage, TLS/WSS, SELinux, persistencia de archivos y modelos, comprobaciones de salud con las rutas reales (`/health/live`, VisionWorker `/health`) y reversión.
- **Defectos corregidos:** (1) los once `infra/vm/env/*.env.example` no estaban en `bc680c6` porque `.gitignore` ignoraba la carpeta; ahora se versionan solo los ejemplos. (2) `web.env.example` activaba el almacenamiento S3 de Administración sin credenciales ni bucket; añadidos, y `preflight_production.py` lo comprueba.

### 21.4 Decisiones y aceptación

- `docs/DECISIONES_PENDIENTES.md`: D1 cierres tras contingencia, D2 resolución de escalamientos, D3 P6, D4 ciclo de la charola, D5 FHIR. Cada una con comportamiento actual, riesgo, opciones, recomendación, impacto, pruebas y responsable. Nada implementado.
- `docs/CHECKLIST_ACEPTACION.md`: 10 grupos. Las pruebas manuales siguen **pendientes** o **bloqueadas** aunque su equivalente automatizado esté aprobado.

### 21.5 Regresión completa

| Prueba | Resultado |
| :--- | :--- |
| `verify_architecture.py` | 57/57 |
| `check_migrations_concurrency.py` | 10/10 |
| Ejecutor, 9 suites | **1791/1791**, 0 fallos, 0 omitidas |
| Portal (web) / Administración / AuthService | 598/598 / 222/222 / 199/199, 0 omitidas |
| Smoke SQLite / smoke real / integración Admin–Supervisor | OK / 51/51 / 13/13 |
| E2E MS-02 / MS-03 / MS-03.1 | 13/13 / 19/19 / 19/19 |
| E2E Gateway + contratos JSON/XML | 32/32; 192/192 conformes, 0 huecos |
| E2E MS-05 / MS-06 / MS-07+08 / MS-09 | 14/14 / 16/16 / 25/25 / 7/7 |
| Navegador: Operador y Supervisor / Administración | 33/33 / 95 PASS, 0 FAIL, 0 BLOCKED |

**Fallos:** ninguno en la corrida final. Los fallos de las corridas intermedias del navegador fueron del script (selectores, diálogos de confirmación), salvo el defecto real del formulario de escalamiento (§21.2).
**Omitidas o no ejercitadas:** 0 pruebas omitidas. 3 rutas del contrato del Gateway no se ejercitan porque no hay un id de muestra (ya documentado en MS-04).
**Dependencias externas usadas (no son mocks):** VisionWorker en contenedor con `best.pt` provisional (fuera del repositorio), PostgreSQL, Redis, MongoDB y almacenamiento S3 de la pila aislada, HAPI FHIR R4 v7.4.0 local. **Dobles de prueba:** pruebas unitarias del portal con servicios simulados y servidores HTTP locales en IntegrationService.
**Bloqueos y deadlocks:** ninguno en la regresión ni en los 5 arranques simultáneos.

### 21.6 Cobertura final de las 20 responsabilidades

| # | Responsabilidad | Dónde vive | Estado al cierre de MS-10 |
| :--- | :--- | :--- | :--- |
| 1 | Autenticación e identidad | AuthService (usuarios, clientes máquina, revocación) | Implementado y probado |
| 2 | Catálogos | CatalogService + Administración en el portal | Implementado y probado |
| 3 | Sesiones y conteo | Portal (Operador) + SessionService (Supervisor) | Implementado y probado (navegador real) · cierres excepcionales: decisiones D1/D2 |
| 4 | Evidencias | EvidenceService + Garage | Implementado y probado (imágenes reales en el navegador) · aceptación manual pendiente |
| 5 | Auditoría | AuditService + auditoría local | Implementado y probado |
| 6 | Notificaciones | NotificationsService + worker | Implementado y probado · correo bloqueado por proveedor SMTP |
| 7 | Monitoreo | MonitoringService + collector | Implementado y probado |
| 8 | Cámaras | Administración + estaciones | Probado solo en entorno aislado · hardware real pendiente |
| 9 | Video en tiempo real / WebSocket | VisionWorker `/ws` + nginx | Implementado y probado · WSS en la VM pendiente |
| 10 | Inferencia de visión | VisionWorker | Implementado y probado con el modelo provisional · modelo definitivo pendiente |
| 11 | Tracking | VisionWorker | Implementado parcialmente (sin cambios) |
| 12 | Comparación de inventario | Portal / SessionService | Implementado y probado |
| 13 | Discrepancias | Portal / SessionService | Implementado y probado (navegador real) |
| 14 | Validación humana | Portal | Implementado y probado (navegador real) · aceptación manual pendiente |
| 15 | Supervisión | Portal → microservicios | Implementado y probado (navegador real) · aceptación manual pendiente |
| 16 | Kits y charolas | CatalogService | Implementado y probado (técnico) · ciclo operativo: decisión D4 |
| 17 | Modelos de IA | Administración + Garage + VisionWorker | Implementado y probado (trazabilidad) · modelo definitivo pendiente |
| 18 | Reportes | SessionService + portal | Implementado y probado (CSV/PDF en el navegador) |
| 19 | Sincronización | IntegrationService | Implementado y probado (API) · clientes Android/escritorio no existen |
| 20 | Integración hospitalaria | IntegrationService (FHIR R4) | Probado solo con HAPI de pruebas · decisión D5 y convenio pendientes |
| — | P6 privacidad institucional | — | Pendiente de decisión (D3) |
| — | Despliegue GCP / CentOS 10 | `infra/vm`, guía | Preparado; no ejecutado |

## 22. MS-11A/B — Preparación del despliegue en Google Cloud (2026-11-09)

Respaldo previo: commit **`6ae2aa4`** (MS-10). **No se accedió a la VM**; nada se desplegó, reinició ni modificó en ella. El detalle completo está en `docs/MS11_PREPARACION_DESPLIEGUE.md`; este apartado solo resume.

- **Inventario y comparación:** `scripts/vm/inspect_vm.sh` (solo lectura, verificado en CentOS Stream 10: ningún secreto en la salida; único efecto, el índice `rpmdb.sqlite-shm` de `rpm -qa`) y `scripts/vm/compare_inventory.py`. **El estado de la VM sigue desconocido** hasta ejecutar el inventario.
- **Riesgos verificados sin la VM (R1–R11):** CentOS 10 trae **Valkey 8.1** (no Redis; compatible: Auth 199, Session 752 e Integration 47 pasan contra Valkey); nombres de paquetes corregidos; nginx 1.26.3 acepta la configuración; **la redirección 80→443 se ignoraba** por el `server_name _` del `nginx.conf` de CentOS (corregido en `render_gateway_vm.py` y verificado con peticiones); VisionWorker aparecía **`degraded`** sin `VISION_WEIGHTS_PATH` (mitigado con una copia del modelo activo, sin cambios de código); unidades verificadas con `systemd-analyze` y ordenadas también después de `valkey.service`.
- **Respaldo:** PostgreSQL, MongoDB, Valkey (`--rdb`), Garage (`scripts/vm/s3_mirror.py`, probado: mirror, verificación y restauración a un bucket vacío), modelos (SHA-256 contra `yolo_model`), configuración cifrada; cada uno con su verificación de restauración (guía §5.1).
- **Plan por etapas** con la disponibilidad de cada etapa, una sola ventana corta de mantenimiento y la reversión.
- **Prueba de humo** solo a través de nginx (15 puntos), validada localmente con un Gateway TLS real: **31 PASS · 0 FAIL · 3 SKIP** (los SKIP están justificados en el documento de MS-11 §10).
- **D1–D5** sin implementar, separando lo necesario para la demostración académica de lo necesario para el hospital.

## 23. MS-11 — Desarrollo de las 20 responsabilidades e integración en Docker Compose (2026-11-10)

Respaldo previo: commit **`10807db`** (MS-11A/B, herramientas de la VM). MS-11 queda **sin commit** y sin push.

### 23.1 Desarrollo nuevo

| Responsabilidad | Qué se desarrolló | Dónde |
| :--- | :--- | :--- |
| R11 Tracking | Tracker real: IDs estables, asignación húngara con predicción de velocidad, recuperación tras oclusión, histéresis de clase, confianza alineada al pipeline, eventos (`confirmed`, `lost`, `recovered`, `terminated`, `class_change`, `enter_roi`, `exit_roi`). Integrado en `run_frame`. Resumen persistido con el conteo final aceptado y visible al Supervisor. **Nunca es un conteo.** El `tracks.py` anterior no se usaba en el pipeline | `apps/VisionWorker/tracking.py`, `inference.py`, `main.py` (`GET /sessions/{id}/tracking`), `GUIA_TRACKING.md` |
| R08 Cámaras | Resolución, FPS y ROI llegan a VisionWorker en el *bind* firmado (antes solo se guardaban). Nuevo *bind* al elegir la cámara. Tipo `simulated` explícito (nunca valida hardware) | `vision_bridge.camera_payload_for_session`, migración 018 |
| R09 Tiempo real | Reconexión automática de la vista previa con espera exponencial; *watchdog* de 10 s; nunca reconecta tras una revocación o expiración; el ticket de reconexión vuelve a vincular la sesión (recupera un reinicio de VisionWorker) | `capture-live-ws.js`, `/capture/ws-ticket?bind=1` |
| R17 Modelos | Registro **inmutable** de evaluaciones (dataset, versión, SHA-256, partición, métricas declaradas, umbrales usados, métricas por clase), comparación con otros modelos sobre el mismo dataset (regresión), historial del modelo y sesiones fijadas. PEF no calcula ni inventa métricas | `admin_local/model_evaluations.py`, pantalla Modelos → Evaluaciones, migración 018 |
| R06 Notificaciones | `final_count_escalated` (**defecto corregido:** el escalamiento de MS-09 nunca llegaba a la bandeja, porque el *trigger* de 006 no lo escuchaba) y `service_alert` (caída y recuperación de componentes) | NotificationsService (dispatcher, contratos), migración 017 |
| R07 Monitoreo | Transiciones de disponibilidad con antirrebote (`service_event`); solo disponibilidad técnica | `MonitoringService/services/collector.py`, migración 017 |
| Compose | `db-migrate` (004–018 en una transacción, antes de los servicios); healthcheck de PostgreSQL por TCP (**defecto corregido**); 017/018 montadas; pila integral aislada con Gateway TLS | `docker-compose.yml`, `scripts/compose_ms11.py` |
| Migraciones | **Defecto corregido:** 013 (y 017) redefinían la restricción de tipos de notificación. Con filas de tipos más nuevos, volver a aplicarlas fallaba, y NotificationsService las vuelve a aplicar en cada arranque. Ahora usan `NOT VALID` (la restricción sigue aplicándose a filas nuevas) | 008, 013, 017, 018; regresión en `check_migrations_concurrency.py` |

### 23.2 Matriz final de las 20 responsabilidades

Estados permitidos: **Implementada y probada** · **Implementada parcialmente** · **Probada mediante simulación** · **Pendiente de hardware físico** · **Pendiente de validación externa** · **Pendiente de decisión funcional**. "Probada" significa pruebas reales en el entorno aislado y en Docker Compose; **no** implica aceptación manual ni validación en un hospital.

| # | Responsabilidad | Servicio responsable | Implementado | Pendiente | Endpoints principales | Almacenamiento | Integraciones | Pruebas | Estado real |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| R01 | Auth e identidad | AuthService | Login y logout, JWT HS256 y *refresh*, revocación en Redis, roles fijos, instituciones, clientes máquina (`client_credentials`) | Rotación y firma asimétrica (propuesta, no aplicada) | `/api/v1/auth/login\|refresh\|logout\|me\|token` | PostgreSQL `user`, `role`, `integration_client`; Redis `auth:jwt:*` | Los 9 servicios validan el JWT y la revocación | Suite Auth 199; E2E Compose pasos 0 y D (revocación en 4 servicios), G (la revocación persiste tras reiniciar Redis) | **Implementada y probada** |
| R02 | Catálogos | CatalogService + Administración (monolito) | Familias, tipos, instrumentos, kits, charolas; activos e inactivos; aislamiento institucional | — | `/api/v1/catalog/*` | PostgreSQL | Sesiones, sincronización | Suite Catalog 240; Admin 222 | **Implementada y probada** |
| R03 | Sesiones y conteo | Portal (Operador) + SessionService (Supervisor) | Ciclo completo; regla del final = referencia (sin cambios) | Cierres excepcionales (D1, D2) | Portal `/operator/sessions/*`; `/api/v1/sessions/*` | PostgreSQL `work_session`, `count_event` | VisionWorker, Evidence, Notifications | Session 752, web 619, E2E Compose 2–13, navegador MS-10 | **Implementada y probada** (D1/D2: **pendiente de decisión funcional**) |
| R04 | Evidencias | EvidenceService | Imagen por captura, modelo, SHA-256, usuario, estación, hora; URL firmada con permisos | Aceptación manual del Supervisor | `/api/v1/sessions/{id}/evidence`, `/api/v1/evidence/{id}` | Garage (S3) + PostgreSQL `media_asset` | Portal, SessionService | Evidence 334; E2E Compose 11 y G (el objeto persiste tras reiniciar Garage) | **Implementada y probada** |
| R05 | Auditoría | AuditService + auditoría local | Acciones con identidad, institución y sesión | — | `/api/v1/audit/events` (no publicado en el Gateway) | PostgreSQL `access_audit` | Todos | Audit 48; E2E Compose 19 | **Implementada y probada** |
| R06 | Notificaciones | NotificationsService + worker | Asignación, pendiente de revisión, discrepancias, corrección, cierre, cierre no resuelto, estaciones y cámaras, **escalamientos** y **alertas de componentes** (MS-11); correo opcional | Proveedor SMTP real | `/api/v1/notifications` | PostgreSQL `notification`, `notification_outbox` | *Triggers* 006/013/017, MonitoringService | Notifications 55; E2E Compose 14, A, E | **Implementada y probada** (correo: **pendiente de validación externa**) |
| R07 | Monitoreo | MonitoringService + collector | Salud de 13 componentes, latencias, errores, estaciones, cámaras, alertas, **transiciones de disponibilidad** (MS-11) | — | `/api/v1/monitoring/*` (portal) | PostgreSQL `monitoring_check`, `service_event`, `station_event` | Notificaciones | Monitoring 67; E2E Compose 15 y E | **Implementada y probada** (disponibilidad técnica, no corrección clínica) |
| R08 | Cámaras | Administración + estaciones + VisionWorker | Selección, resolución, FPS, ROI, asociación a la estación, estado y errores; la configuración **llega a VisionWorker** (MS-11); fuentes **simuladas** identificadas | Calibración (no definida en los RF); hardware físico | `/admin/configuration/cameras/*`, `/operator/sessions/{id}/capture/camera` | PostgreSQL `capture_camera` | VisionWorker (*bind* firmado), monitoreo | web (`test_ms11_tracking_camera`), E2E Compose 1 y 4 (fuente simulada) | **Probada mediante simulación** · **Pendiente de hardware físico** |
| R09 | Video en tiempo real | VisionWorker `/ws` + portal | WSS con ticket HMAC y Origin; revocación; **reconexión con espera exponencial, *watchdog* y nuevo *bind* tras reiniciar el worker** (MS-11) | WSS a través de la VM real | `/ws`, `/operator/sessions/{id}/capture/ws-ticket` | Memoria del worker | Gateway | Vision 72, web, E2E Compose F; MS-11A Gateway TLS | **Implementada y probada** (cámara física: **pendiente de hardware físico**) |
| R10 | Inferencia de visión | VisionWorker | YOLO, cajas, confianza, mapeo de clases, modelo fijado por sesión verificado por SHA-256, resultados persistidos | Modelo definitivo | `/sessions/{id}/*` (interno) | Garage (pesos), PostgreSQL | Portal | Vision 72; E2E Compose 7–9 con `best.pt` provisional | **Implementada y probada** con el modelo provisional · modelo definitivo **pendiente de validación externa** |
| R11 | Tracking | VisionWorker (`tracking.py`, MS-11) | IDs estables, asociación húngara con predicción, oclusiones y recuperación, cambios de clase, confianza, eventos de ROI, resumen persistido | Videos reales largos y con movimiento; validación clínica | `/sessions/{id}/tracking` (interno) | `count_event.payload.tracking` | Inferencia, Supervisor | 16 sintéticas, 6 videos reales medidos (`GUIA_TRACKING.md`), E2E Compose 6 | **Implementada y probada** (con secuencias reproducibles; limitaciones documentadas) |
| R12 | Comparación de inventario | Portal / SessionService | Esperado, inicial, referencia vigente, final, clasificación, incorporaciones | — | — | PostgreSQL | — | web, Session, E2E MS-03.1 | **Implementada y probada** |
| R13 | Discrepancias | Portal / SessionService | Creación, clasificación, estados, evidencias, escalamiento, resolución, auditoría, notificaciones; nunca un *bypass* del final | Resoluciones tras escalamiento (D2) | `/supervisor/discrepancies/*` | PostgreSQL `discrepancy` | Notificaciones | web, Session, E2E Compose 13 y A | **Implementada y probada** (D2: **pendiente de decisión funcional**) |
| R14 | Validación humana | Portal | Corrige la clasificación sin cambiar el total; registra usuario, fecha, motivos y evidencia | Aceptación manual | `/operator/sessions/{id}/vision-validation` | PostgreSQL | — | web, E2E Compose 10 | **Implementada y probada** |
| R15 | Supervisión | Portal → microservicios | Bandeja, revisión, evidencias, aprobaciones, escalamientos, estados no resueltos, historial | Devolución al Operador (D2) | `/supervisor/*` | — | Session, Evidence, Notifications, Monitoring | E2E Compose 12–16, navegador MS-10 | **Implementada y probada** (devoluciones: **pendiente de decisión funcional**) |
| R16 | Kits y charolas | CatalogService + SessionService | Kits, versiones, cantidades, charolas, historial, asignación, permisos, integridad entre instituciones | Ciclo operativo, obligatoriedad, uso simultáneo (D4) | `/api/v1/catalog/kits\|trays` | PostgreSQL `kit`, `surgical_tray*` | Sesiones, sincronización | Catalog, Session, E2E Compose 17 | **Implementada y probada** (técnico) · **pendiente de decisión funcional** (D4) |
| R17 | Modelos de IA | Administración (monolito) + VisionWorker | Versiones, pesos, SHA-256, clases, activación, fijación por sesión, **evaluaciones declaradas e inmutables, comparación sobre el mismo dataset, historial** (MS-11) | Métricas reales del modelo definitivo | `/admin/vision-models/*` (+ `/evaluations`) | PostgreSQL `yolo_model`, `model_class`, `yolo_model_evaluation`; Garage | VisionWorker | web (`test_ms11_model_evaluations` 13), E2E Compose 1 | **Implementada y probada** (sin métricas reales registradas: PEF no reporta precisión) |
| R18 | Reportes e indicadores | SessionService + portal | Sesiones, conteos, discrepancias, intervenciones, tiempos, monitoreo, exportación CSV/PDF, filtros, Highcharts; nunca presenta precisión de IA | — | `/supervisor/reports*` | PostgreSQL | — | Session, E2E MS-06, E2E Compose 16 | **Implementada y probada** |
| R19 | Sincronización | IntegrationService | API incremental, cursores firmados, idempotencia, conflictos, clientes externos, revocación, reintentos | Clientes Android y escritorio (no existen) | `/api/v1/sync/*` | PostgreSQL `sync_change`, `sync_operation` | Gateway, AuthService | Integration 47, E2E MS-07/08, E2E Compose 17 | **Implementada y probada** (API) |
| R20 | Integración hospitalaria | IntegrationService | Clientes autorizados, JSON/XML, FHIR R4 de lectura, entregas con reintentos e idempotencia, auditoría | HIS real, mapeo aprobado, autenticación saliente (D5) | `/fhir/r4/*`, `/api/v1/integration/*` (interno) | PostgreSQL `integration_*` | HAPI **de pruebas** | Integration 47, E2E MS-07/08, E2E Compose 18 | **Probada mediante simulación** (HAPI local) · **Pendiente de validación externa** (D5) |

### 23.3 Pruebas

| Prueba | Resultado |
| :--- | :--- |
| `verify_architecture.py` | 58/58 |
| `check_migrations_concurrency.py` (base nueva, filas de tipos nuevos entre pasadas, 5 arranques simultáneos) | 11/11 |
| Ejecutor, 9 suites | **1815/1815** (+24 nuevas), 0 omitidas |
| Portal / Administración / AuthService | **619** / 222 / 199, 0 fallos, 0 omitidas |
| Smoke SQLite / smoke real / integración Admin–Supervisor | OK / 51/51 / 13/13 |
| E2E MS-02 / MS-03 / MS-03.1 / Gateway (contratos 192/192, 0 huecos) / MS-05 / MS-06 / MS-07+08 / MS-09 | 13 / 19 / 19 / 32 / 14 / 16 / 25 / 7 |
| **E2E Docker Compose integral** (`e2e_ms11_compose.py`, 19 contenedores reales, Gateway TLS) | **31/31** |
| Navegador real Operador + Supervisor (MS-10) | 33/33 (sin errores de JavaScript) |
| Tracking en videos reales (`measure_tracking.py`) | Ver `GUIA_TRACKING.md` §4.2 |
| Suites de Redis contra **Valkey 8.1** (MS-11A) | Auth 199, Session 752, Integration 47 |

**Defectos encontrados por las pruebas integrales y corregidos (con regresión):**
1. El healthcheck de PostgreSQL daba "healthy" durante `initdb`.
2. Las restricciones redefinidas en 013/017 impedían volver a aplicar las migraciones con datos de tipos nuevos; NotificationsService no habría arrancado.
3. `db-migrate` no era una sola transacción (los `BEGIN;`/`COMMIT;` propios de los archivos).
4. El Gateway mantenía la IP vieja de un contenedor recreado (502 hasta recargar).
5. El escalamiento de MS-09 nunca llegaba a la bandeja de notificaciones.
6. Hubo un **deadlock al arrancar** NotificationsService mientras otro servicio escribía, observado en la validación en navegador. La migración que se vuelve a aplicar al iniciar bloqueaba `notification_outbox` y luego necesitaba `service_event`, mientras el colector tenía `service_event` y su *trigger* esperaba `notification_outbox`. El bloqueo consultivo de MS-09 solo ordena migraciones entre sí, no frente a escrituras normales.
   - Corrección: la migración bloquea primero las tablas de origen, en el orden de las escrituras, con `lock_timeout`. Notifications, Monitoring e Integration reintentan con espera si aun así ocurre un *deadlock*.
   - Regresión determinista: `test_startup_migration_does_not_deadlock_with_a_concurrent_trigger_write`. Sin la corrección reproduce `DeadlockDetected`; con ella, pasa.

**Dependencias externas usadas (reales, no simuladas):** contenedores de PostgreSQL 16, Redis 7, MongoDB 7, Garage 2.4.1, nginx 1.27.4, HAPI FHIR R4 7.4.0 (**servidor de pruebas**) y VisionWorker con `best.pt` provisional. **Simulaciones identificadas:** la fuente de cámara `simulated` (videos grabados) y las secuencias sintéticas del tracker.

## 24. MS-12 — Adaptación a los 20 dominios funcionales del profesor (2026-11-10)

Respaldo previo: commit **`949699d`** (MS-11). MS-12 queda **sin commit** y sin push. Documento principal: **`docs/MS12_DOMINIOS_FUNCIONALES.md`** (matriz de los 20 dominios, correspondencia de puertos 5301–5320 → 5001–5009, delimitación, contratos, matriz de pruebas y análisis de las apps cliente). Diagramas editables: `docs/diagramas/ms12_contenedores.drawio`, `ms12_dominios.drawio`, `ms12_flujo_conteo.drawio`, `ms12_despliegue_compose.drawio`.

### 24.1 Brechas cerradas (migración `019_ms12_dominios.sql`, aditiva e idempotente)

| Dominio | Brecha | Implementación | Pruebas |
| :--- | :--- | :--- | :--- |
| ms-kits-charolas | Equivalencias autorizadas | `kit_item_equivalence`; CatalogService `/kits/{id}/equivalences` (SPD escribe, todos leen, revocación con historial, auditoría); pantalla del Supervisor; aviso informativo al Operador. No cambia cantidades ni la regla del final | Catalog 5, portal 7, E2E M3 |
| ms-camaras | Calibración | `capture_camera_calibration` (longitud de referencia medida → mm/px; inmutable; "no coincide" si cambia la resolución); al *bind* solo si coincide | portal 15, Vision 2, E2E M1, M4 |
| ms-tracking-instrumental | Bitácora y trazabilidad | `tracking_event` (completa, inmutable, ligada al conteo aceptado); `GET /api/v1/sessions/{id}/tracking-events` | portal 5, Session 5, E2E M5 |
| ms-validacion-humana | Evidencia de la validación | Sello SHA-256 en el `manual_close`; `GET /api/v1/sessions/{id}/validation-seal` (`intact`/`altered`/`not_sealed`); visible al Supervisor | portal 2 + 2, Session 5, E2E M6 |
| ms-modelos-ia | Regresión | `scripts/model_regression.py` (inferencia real en la imagen del worker) + registro inmutable `yolo_model_regression_run`; PEF recalcula la concordancia | portal 12, corrida real 3/3, E2E M2 |
| ms-integracion-hospitalaria | Lotes trazables | `integration_export_batch/item`; `/api/v1/integration/exports` (bytes exactos + SHA-256, auditoría, sin datos de paciente; IT admin o alcance `export.sessions.batch`) | Integration 10, E2E M8 |
| ms-monitoreo | Colas, *workers*, bases | Componentes `integration` e `integration-worker` (entregas vencidas/fallidas); conexiones de PostgreSQL | Monitoring 3, E2E M7 |
| ms-evidencias | Recuperación tras desconexión | **Defecto corregido:** el outbox abandonaba la imagen tras 5 intentos (~15 s de caída de EvidenceService). Ahora reintenta durante 10 min (acotado por el token del Operador) | portal 2, E2E M9 (caída real de 25 s) |
| ms-auditoria | Consistencia entre servicios | `scripts/check_audit_consistency.py` (institución del actor y del recurso, duplicados; estilos de nombres contados, D-5) | E2E M10; base de pruebas 3146 filas sin violaciones |

### 24.2 Decisiones nuevas (no implementadas)

D6 uso clínico de los equivalentes, D7 firma de la validación, D8 destino de los lotes (`docs/DECISIONES_PENDIENTES.md`). D1–D5 siguen sin implementar.

### 24.3 Pruebas

Ejecutor 9 suites **1845/1845** (Auth 199, Catalog 245, Session 762, Evidence 334, Audit 48, Notifications 56, Monitoring 70, Integration 57, Vision 74 con inferencia real), 0 omitidas; Web **664**; Admin 222; Auth 199; arquitectura 58/58; migraciones (base nueva, reaplicación, 3 arranques simultáneos) 9/9; smoke SQLite OK; smoke real 51/51; integración Admin 13/13; E2E gateway 32/32 (contratos 207/207, 0 huecos, incluye las rutas nuevas); MS-02 13, MS-03 19, MS-03.1 19, MS-05 14, MS-06 16, MS-07/08 25, MS-09 7; navegador 33/33; **E2E Docker Compose integral 41/41** (dos ejecuciones; la primera 40/41 por una verificación que no esperaba al outbox asíncrono, corregida en la prueba); regresión real de modelo 3/3 videos idénticos; auditoría de la base de pruebas: 3146 filas sin violaciones. Comandos: `GUIA_PRUEBAS.md` §6. E2E integral Docker Compose: **41/41** (`docs/evidencias/MS-12/e2e_compose_ms12.json`). La primera ejecución dio 40/41: la verificación 11 contaba las imágenes sin esperar al *outbox* asíncrono (la tercera llegó segundos después); se corrigió la prueba para esperar, no el producto.
