# PEF Instrumental Quirúrgico — Estado de Implementación

| | |
|---|---|
| Arquitectura actual | Monolito Flask (`apps/BackendWebFlask`) |
| Autenticación | Servicio separado `apps/BackendAuthService` (Flask + JWT) |
| Persistencia principal | PostgreSQL (`data/migrations/001_init.sql`) |
| Frontend | Plantillas Flask/Jinja + CSS/JS propios (sin framework SPA) |
| Fecha del estado | 2026-09-29 |
| Idiomas de la interfaz | Inglés (`en`, predeterminado) y español (`es`), Flask-Babel |

**Propósito.** Describir el estado *real* del código del repositorio en esta fecha: qué funciona, cómo
funciona el flujo completo, qué se agregó/cambió/eliminó y qué queda pendiente. Es una referencia
técnica para quien continúe el desarrollo sin revisar todo el historial.

Este documento describe la **versión actual del monolito**, no el diseño futuro por microservicios
(React/FastAPI/WebSocket/MongoDB/Redis/GCS) descrito en `README.md` y en `data/DataStandards/`.

> El repositorio no es un repositorio Git en este workspace, por lo que los cambios aquí listados se
> verificaron inspeccionando el código actual, no con `git log`/`git diff`.

---

> **Language rule.** English is the canonical language for persisted database business data and catalog data. UI localization is handled at the application layer.

## 1. Resumen ejecutivo

El flujo principal implementado es:

```
Administrator → Catálogos → Procedure / Kit
→ Operator → WorkSession → ExpectedInventory snapshot
→ Capture (JPEG) → Vision analysis → CountEvent (auto_count)
→ Discrepancy → Human Validation (manual_count / HumanCorrection)
→ Supervisor Review → Correction loop → Approval
→ Session Close → Dashboards / Audit
```

- El flujo de negocio está implementado de extremo a extremo en el código.
- Las pruebas se han ejecutado con **SQLite temporal** (con adaptaciones de tipos PG solo en el arnés de pruebas).
- **Falta la validación integral contra PostgreSQL real** (ver [Pending PostgreSQL Integration Validation](#pending-postgresql-integration-validation)).
- La inferencia actual usa el provider **`controlled`** (salida del modelo introducida manualmente). **No hay YOLO real.**
- El worker YOLO real se integrará cuando el modelo esté entrenado.

---

## 2. Base de datos y modelos

- **Fuente de verdad:** `data/migrations/001_init.sql` (50 tablas). No se modificó.
- Los 50 modelos SQLAlchemy de `apps/BackendWebFlask/models/` se alinearon con el SQL en: columnas,
  tipos/longitudes (`VARCHAR(n)`, `DATE`, `JSONB`, `INET`, `CHAR(64)`), nullability, PK, FK con
  `ON DELETE`, `UNIQUE` con nombre, `CHECK` con nombre, índices únicos parciales
  (`uk_yolo_model_active`, `uk_procedure_kit_default`, `uk_instrument_reservation_instrument_active`,
  `uk_instrument_institution_internal_code`, `uk_resource_identifier_system_value_active`) y
  `server_default` (`gen_random_uuid()`, `now()`, booleanos, literales, JSONB).
- `updated_at` usa además `onupdate=func.now()` (solo aplica a cambios vía ORM; el SQL no tiene triggers).
- Diferencias importantes corregidas: `User` (password_updated_at, last_login_at, ui_preferences,
  created_at/updated_at), `WorkSession` (closed_by_user_id, phase_changed_at, updated_at y nullability;
  relaciones `user` / `closed_by_user` con `foreign_keys` explícito), `MediaAsset` (kind,
  storage_provider, retención/purga/bloqueo), `Instrument`, `Kit`, `ExpectedInventory` (`source`),
  `Discrepancy` (updated_at, cantidades/familia nullable), `ResourceIdentifier` (defaults);
  además `role.code` pasó de UNIQUE global a `UNIQUE (institution_id, code)`.
- Los índices de consulta `idx_*` del SQL **no** se declararon en los modelos (solo importan si se
  usara `create_all`/autogeneración de migraciones).
- La comparación automatizada modelos ↔ SQL reporta **0 diferencias** (ver [Pruebas](#22-pruebas)).

Tablas **sin funcionalidad en rutas/servicios** (solo modelo): `integration_client`,
`privacy_notice_version`, `session_processing_agreement`, `privacy_request`, `instrument_reservation`,
`instrument_cycle_event`, `family_example`, `instrument_usage`, `resource_identifier`,
`cat_checkpoint_reason`, `cat_processing_purpose`. `operation`, `patient`, `physician`,
`operation_patient/physician` se **leen** (New Counting Session) pero **no existe UI para crearlas**:
deben existir en la BD (para pruebas: `data/seeds/004_seed_e2e.sql`).

---

## 3. Autenticación y autorización

- **BackendAuthService** (`apps/BackendAuthService/main.py`): login contra la tabla real `"user"`,
  contraseñas con `werkzeug.security` (`generate_password_hash` / `check_password_hash`), JWT access
  token (15 min por defecto) y refresh token (30 días), entregados en cookies.
- `token_required` revoca y rechaza tokens de usuarios inactivos o inexistentes; `/verify` además filtra
  `u.active = TRUE`. Un usuario desactivado no puede seguir navegando con un token previo.
- **LOGIN** se audita una vez en `/login` (misma transacción que `last_login_at`). `/verify` y `/refresh`
  **no** generan LOGIN. Intento fallido sobre una cuenta existente → `LOGIN` con `outcome = denied`;
  correo inexistente → no se audita (AccessAudit exige actor).
- **LOGOUT** se audita en `/logout` antes de revocar el token; si la auditoría falla, el logout continúa.
- **BackendWebFlask** valida cada request con `/verify` (`_current_user`) y autoriza con
  `require_role('operator_cde' | 'supervisor_quality' | 'it_admin')`. Roles vía `role` / `user_role`.
- No existe `DEMO_USERS` en el código.
- **Aislamiento institucional:** el `institution_id` se toma siempre del token verificado, nunca del
  formulario; entidades de otra institución por URL/UUID devuelven 404 y se auditan `ACCESS_* denied`.
- Limitación: el almacén de tokens (`token_store`) es **en memoria** del proceso del auth service
  (se pierde al reiniciar y no se comparte entre procesos).

---

## 4. Administración (IT Administrator)

Persistentes (crear/editar; activar/desactivar donde el esquema tiene `active`):

| Módulo | Notas |
|---|---|
| Instrument Families | catálogo global; código único |
| Instruments | institucional; `internal_code` único por institución; estado de ciclo desde catálogo |
| Kits + KitItems | institucional; composición en la misma transacción (agregar/actualizar/eliminar filas, sin duplicados, quantity > 0) |
| Procedures (`cat_procedure_type`) + ProcedureKit + ProcedurePhase | kits de la institución; kit default único; asociaciones retiradas se **desactivan**; fases con `sort_order` único |
| Users + UserRole | institución del admin; hash de contraseña; cambio de contraseña; desactivar (no borrar); no puede desactivarse a sí mismo |
| Roles | `UNIQUE (institution_id, code)`; código no editable |
| Operating Rooms / Capture Stations | institucionales (la estación vía su quirófano); ROI no editable (NULL al crear, se conserva) |
| YoloModel + ModelClass | global; asset como URI `gs://`, `s3://` o `minio://`; un único modelo activo (activación desactiva el anterior) |
| Institution configuration | solo el nombre de la propia institución |

- Validación en backend (`services/admin_service.py`, `FormError`/`ConflictError`); POST inválido → 400/409 con mensaje (flash), nunca 500.
- Una transacción por operación (commit/rollback) y auditoría en la misma transacción.
- **Limitación:** `role` no tiene columna `active` → no hay desactivación de Role sin cambiar el esquema (tampoco borrado).
- No hay UI para crear Institutions, Operations, Patients ni Physicians.

---

## 5. WorkSession

- `GET/POST /operator/sessions/new` (`services/session_service.py`).
- `user_id` = usuario autenticado (cualquier `user_id` del formulario se ignora).
- Requiere: `Operation` de la institución en estado `scheduled`/`in_progress` **con** `procedure_type_id`;
  kit activo de la institución asociado al procedimiento por `ProcedureKit` activo (sin fallback: si no hay
  ninguno → 409); estación activa de un quirófano activo (el mismo quirófano de la operación si tiene uno);
  fase de una `ProcedurePhase` **activa** del procedimiento (sin fallback a fases globales: si el
  procedimiento no tiene ninguna, el GET muestra un aviso y el POST responde 409); casilla de preparación
  confirmada. Las sesiones ya creadas conservan su `current_phase_id` aunque la fase se desactive después.
- Estado inicial: `cat_session_status.code = 'open'` (buscado por código). `phase_changed_at` si hay fase.
- Identificador: el esquema **no** tiene folio humano (`ResourceIdentifier` no admite `work_session`);
  se usa el **UUID**, abreviado a 8 caracteres en listas.

## 6. ExpectedInventory snapshot

```
KitItem ──(copia al crear la sesión)──► ExpectedInventory (source = 'kit_snapshot')
```

En la misma transacción que la WorkSession (+ auditoría `CREATE_SESSION`). Kit sin ítems → 409.
A partir de ahí las pantallas y la comparación usan **solo** `ExpectedInventory`:
kit original 6 → snapshot 6 → kit cambia a 8 → la sesión histórica sigue mostrando 6.

## 7. Captura y evidencia

- `POST /operator/sessions/<uuid>/capture` (`services/evidence_service.py`), `<input type="file" accept="image/jpeg">`
  con `capture="environment"` para cámara en móviles, vista previa local (`static/js/pages/capture-evidence.js`).
- Validación: MIME JPEG, bytes mágicos (SOI/EOI), tamaño > 0 y ≤ `CAPTURE_MAX_BYTES` (413); estado de sesión `open`/`counting`.
- Almacenamiento **local privado** (fuera de `/static`), escritura atómica (temporal + `os.replace`),
  compensación: si la BD falla se borra el archivo recién escrito. Capturas anteriores se conservan.
- `MediaAsset`: `kind='jpeg'`, `storage_provider='other'`, `bucket=CAPTURE_STORAGE_BUCKET`,
  `gcs_uri=local://<bucket>/<object_key>`, `sha256`, `size_bytes`, `content_type='image/jpeg'`.
- Asociación sin FK (el esquema no la tiene): `object_key =
  institutions/<institution_uuid>/sessions/<session_uuid>/captures/<asset_uuid>.jpg`; se consulta por ese prefijo exacto.
- Visualización autenticada: `/operator/sessions/<uuid>/media/<asset>` y `/supervisor/sessions/<uuid>/media/<asset>`
  (`send_file`, `Cache-Control: private, no-store`, sin exponer rutas físicas; activos de otra sesión → 404).
- La primera captura cambia `open → counting` y registra `START_COUNT`; cada captura registra `CAPTURE_EVIDENCE`.

## 8. Vision / inference

> **`VISION_INFERENCE_PROVIDER=controlled` NO es YOLO.** Es un provider de desarrollo: en la pantalla
> AI Suggested Count el operador introduce la salida del modelo (`yolo_class_id`, cantidad, confianza).
> Existe para probar el flujo de negocio mientras se entrena el modelo, y la UI lo rotula como
> "Controlled inference (development provider)".

```
Provider (controlled | futuro YOLO worker)
  → InferenceResult normalizado (inference_run_id, media_asset_id, model_id, version_tag,
    inferred_at, provider, confidence_threshold, resultados por clase)
  → CountEvent auto_count → comparación → Discrepancy
```

- `services/vision_service.py`: `PROVIDERS` / `get_provider()`; un worker YOLO real solo debe producir
  la misma estructura normalizada sin tocar la lógica posterior.
- Requiere exactamente un `YoloModel` activo con `version_tag` y `ModelClass` coherentes (si no → 409).
- Usa siempre la última captura autorizada (`get_latest_capture_for_session`); nunca un id del navegador.
- `yolo_class_id → ModelClass → InstrumentFamily`; clases sin mapeo se conservan como no identificadas.
- Idempotencia: `client_event_id = uuid5(inference_run_id, clave)`; un solo run por sesión (`counting → validating`).
- No se guardan bounding boxes en PostgreSQL.

## 9. CountEvent y comparación

Por cada familia de `ExpectedInventory ∪ familias detectadas` se crea un `CountEvent` `auto_count`
(`user_id = NULL`, payload con run, captura, modelo, versión, provider, umbral, confianza, clase).
Familia esperada no detectada → detectado 0; detectada no esperada → esperado 0; clase no mapeada →
evento con `family_id`/`expected` NULL.

| Regla | Resultado |
|---|---|
| detected − expected < 0 | `shortage` |
| detected − expected > 0 | `surplus` |
| = 0 | match (sin discrepancia de cantidad) |
| confianza < `VISION_CONFIDENCE_THRESHOLD` | `low_confidence` (puede coexistir con la anterior) |
| clase sin ModelClass | `unidentified` |

Los `auto_count` **nunca** se modifican.

## 10. Human validation

```
auto_count (IA) → manual_count #1 → manual_count #2 → …   (payload.reviewed_event_id / root_event_id)
```

- `services/review_service.py`. Se deben validar **todas** las filas del run; el valor inicial es el de la IA.
- Mismo valor → `manual_count` con `validation_type = confirmation` (auditoría `HUMAN_CONFIRMATION`, sin HumanCorrection).
- Valor distinto → `manual_count` `correction` + `HumanCorrection` (justificación obligatoria) apuntando al
  eslabón **anterior** de la cadena (respeta `UNIQUE count_event_id`); auditoría `HUMAN_CORRECTION`.
- `validation_batch_id` + `client_event_id = uuid5(batch, fila)` → reenvíos no duplican.
- `get_latest_human_count()` = último `manual_count` de la cadena (nunca el `auto_count`).
- Si el valor humano difiere del esperado y no hay discrepancia equivalente abierta, se crea una
  Discrepancy con origen en el `manual_count`.
- `validation_passed` solo se registra si no quedan discrepancias sin resolver.

Ejemplo conservado: IA 7 → humano 8 (HC 7→8) → Supervisor pide corrección → humano 7 (HC 8→7) → aprobación.

## 11. Discrepancies

Registros reales con `origin_event_id`, familia, cantidades, `reason_id`, `description`, `resolved`, `resolved_at`.
Motivos usados: `shortage`, `surplus`, `unidentified`, `low_confidence`. `misclassified` existe en el
catálogo pero el flujo automático **no** lo genera (no hay ground truth).

El esquema **no** tiene estado de workflow para Discrepancy; se deriva (`get_discrepancy_workflow_state`):

| Estado | Regla |
|---|---|
| APPROVED | `resolved = TRUE` |
| CORRECTION_REQUIRED | última decisión del Supervisor ≥ último resultado humano |
| UNDER_REVIEW | hay resultado humano posterior a la última decisión |
| OPEN | aún no hay resultado humano |

La validación humana **no** resuelve discrepancias; solo el Supervisor (Approve).

## 12. Supervisor

- Dashboard, Sessions, Session History, Session Details, Discrepancies, Review, Reports, Indicators y Audit Log, todos limitados a su institución.
- Review muestra esperado, IA, resultado humano, historial de correcciones, decisiones y evidencia.
- Acciones POST: `/supervisor/discrepancies/<id>/approve` | `request-correction` | `reject`.
  - Approve (solo en UNDER_REVIEW) → `resolved = TRUE`, `resolved_at`; `APPROVE_DISCREPANCY` + `SUPERVISOR_REVIEW`.
    La nota opcional del modal de aprobación **no se persiste** (no hay columna ni tipo de evento para ella).
  - Request Correction / Reject (nota obligatoria) → `CountEvent correction_requested` (payload con decisión y nota); no resuelve.
- Ciclo: Request Correction → Operator (`/operator/sessions/<uuid>/correction`) envía nuevo conteo → UNDER_REVIEW → Supervisor revisa de nuevo.

## 13. Session close

`POST /operator/sessions/<uuid>/close` vuelve a comprobar en backend:
estado `validating`, validación humana completa y **0 discrepancias sin resolver**.

- Bloqueado → HTTP 409; se persisten `CountEvent close_blocked` (motivos) y `CLOSE_SESSION` con `outcome = denied`; la sesión no cambia.
- Éxito → `status = closed`, `ended_at`, `closed_by_user_id`, `CountEvent session_close` (resumen final, nº de correcciones, discrepancias resueltas) y `CLOSE_SESSION` success.
- Un segundo cierre no crea duplicados ni modifica timestamps. Solo el rol Operator cierra.
- No existe flujo de cancelación de sesión (el estado `cancelled` existe solo en el catálogo).

## 14. Auditoría

`services/audit.py` (`record_audit` en la misma transacción que la operación; `record_denied` en transacción
propia). `AccessAudit` guarda actor, acción, recurso (tipo + id), resultado, institución, `occurred_at`,
`correlation_id` (cabecera `X-Request-ID`) e IP.

Eventos con productor real (lista no exhaustiva): `LOGIN`, `LOGOUT`, `CREATE_/UPDATE_/ACTIVATE_/DEACTIVATE_`
de INSTRUMENT_FAMILY, INSTRUMENT, KIT, OPERATING_ROOM, CAPTURE_STATION, USER, YOLO_MODEL; `CREATE_/UPDATE_`
PROCEDURE y ROLE; `CHANGE_USER_PASSWORD`, `UPDATE_INSTITUTION`, `ACTIVATE_YOLO_MODEL`,
`CREATE_SESSION`, `START_COUNT`, `CAPTURE_EVIDENCE`, `VISION_RESULT`, `CREATE_DISCREPANCY`,
`HUMAN_CONFIRMATION`, `HUMAN_CORRECTION`, `SUBMIT_VALIDATION`, `REQUEST_CORRECTION`, `REJECT_DISCREPANCY`,
`SUPERVISOR_REVIEW`, `APPROVE_DISCREPANCY`, `CLOSE_SESSION` y `ACCESS_<TABLA>` (denied).

## 15. Dashboards y analytics

Reales (sin valores demo): Operator Dashboard, Supervisor Dashboard, Admin Dashboard, Supervisor Reports
(filtros de periodo, kit, operador y estado), Supervisor Indicators, Admin Audit Log y Supervisor Audit Log
(filtros por búsqueda, usuario, evento, resultado, entidad, periodo y fecha). Implementación en
`services/analytics_service.py` (COUNT/GROUP BY en SQL).

Métricas: sesiones hoy / por estado / con y sin discrepancias / con correcciones / pendientes de revisión;
discrepancias totales, abiertas, resueltas, por motivo, por familia (NULL → "Unidentified") y top familias;
correcciones humanas (solo `HumanCorrection`).

- **AI-Human Agreement** = filas `auto_count` con resultado humano final donde `AI detected == último manual_count`
  ÷ filas revisadas. Sin revisiones → `N/A`. No se compara contra el esperado.
- **Average Resolution Time** = `resolved_at − occurred_at` del `CountEvent` origen (Discrepancy no tiene
  `created_at`). Sin resueltas → `N/A`.
- "Today" = día **UTC**. Audit logs muestran como máximo los **300** eventos más recientes (sin paginación).

## 16. Multi-tenancy

Aislado por institución: WorkSession (vía `user.institution_id` del operador), Users, Roles, Kits,
Instruments, OperatingRooms, CaptureStations (vía quirófano), Operations, CountEvent / Discrepancy /
HumanCorrection / MediaAsset (vía sesión), AccessAudit (`institution_id`) y todos los dashboards.

**Globales por esquema** (sin `institution_id`): InstrumentFamily, CatProcedureType y catálogos `cat_*`,
ProcedurePhase, YoloModel, ModelClass. Cualquier administrador puede editarlos. ProcedureKit solo toca
kits propios, pero el índice `uk_procedure_kit_default` es global por procedimiento.

## 17. Servicios y componentes nuevos

| Archivo | Responsabilidad |
|---|---|
| `services/audit.py` | Construir/registrar filas AccessAudit (política transaccional) |
| `services/admin_service.py` | Validación + persistencia de todos los CRUD administrativos |
| `services/session_service.py` | Opciones válidas, creación de WorkSession + snapshot, contexto de sesión |
| `services/evidence_service.py` | Validación JPEG, almacenamiento local, MediaAsset, `get_latest_capture_for_session` |
| `services/vision_service.py` | Providers, resultado normalizado, auto_count, comparación, discrepancias |
| `services/review_service.py` | Validación humana, cadenas, workflow derivado, Supervisor, cierre, `session_report` |
| `services/analytics_service.py` | Métricas, indicadores, filtros de reportes y audit logs |
| `templates/components/session_review.html` | Bloques compartidos de revisión (resultados, discrepancias, correcciones, evidencia, timeline) |
| `templates/components/analytics.html` | Gráficos/listas y filtros de audit log |
| `static/js/pages/capture-evidence.js` | Selección/captura, preview y envío de la foto |
| `static/js/table-filter.js` | Filtro en cliente de las listas sobre filas reales |
| `localization/__init__.py` | Resolución del idioma por petición, cambio de idioma, persistencia en `ui_preferences` |
| `localization/labels.py` | Etiquetas localizadas por código (catálogos, roles, estados de workflow) + formato de números/fechas |
| `translations/` + `babel.cfg` | Catálogos gettext (`messages.pot`, `es/LC_MESSAGES/messages.po/.mo`) y configuración de extracción |
| `static/js/i18n.js` | `PEF_I18N.t()` para los pocos textos que construye JavaScript |

`controllers/routes.py` y `controllers/view_data.py` se reescribieron parcialmente (66 rutas del blueprint).

## 18. Archivos eliminados y archivos solo de preview

Eliminados:
- `apps/BackendWebFlask/templates/operator/sessions/discrepancy.html` (la ruta redirige a Awaiting Review).
- `apps/BackendWebFlask/templates/components/modals/supervisor_review_required.html`.
- Código muerto: `controllers/view_data.audit_log_data` (reemplazado por `analytics_service.audit_entries`).

Sin Git no es posible verificar otras eliminaciones.

Se conservan **solo** como referencia visual para `devtools/frontend_preview.py` (no los sirve ninguna ruta
de la aplicación): `templates/operator/sessions/active.html`, `templates/admin/roles/edit.html` y
`static/js/pages/counting-session.js`.

## 19. Seeds y datos canónicos

**English is the canonical language for persisted database business data and catalog data. UI
localization is handled at the application layer.** La base de datos guarda un único valor canónico en
inglés (no copias por idioma); la lógica de negocio usa siempre códigos estables (`code`, UUID, `version_tag`)
y nunca nombres visibles. Los identificadores del esquema (`001_init.sql`) ya estaban en inglés y no cambiaron.

`data/seeds/002_seed_catalogs.sql` (idempotente, `ON CONFLICT DO UPDATE`): todos los `name` de catálogo están en
inglés (p. ej. `open` → "Open", `appendectomy` → "Appendectomy", `pre_incision` → "Initial Count Before
Incision", `shortage` → "Shortage against expected inventory"). Incluye además:
- `cat_discrepancy_reason`: `low_confidence`.
- `cat_event_type`: `correction_requested`.

Las bases existentes deben volver a ejecutar este seed antes de probar el sistema completo; si faltan,
las operaciones responden 409 pidiendo actualizar catálogos. `003_seed_demo.sql` **no** se modificó
(escenario histórico: contraseñas placeholder, operación cerrada, sin YoloModel/ModelClass).

`data/seeds/004_seed_e2e.sql` (nuevo, idempotente, **solo desarrollo**): escenario completo para la prueba
end-to-end (institución, usuarios Admin/Operator/Supervisor con hash werkzeug, roles, quirófano, estación,
familias, kit + ítems, ProcedureKit, ProcedurePhase, paciente, médico, operación `scheduled`, YoloModel de
desarrollo `e2e-controlled-dev` sin pesos + ModelClass), con valores en inglés (Kelly Forceps, Mosquito Forceps,
Metzenbaum Scissors, Mayo-Hegar Needle Holder, Farabeuf Retractor, "General Surgery Kit E2E", "Overhead Capture
Station E2E-OR-1", "Dr. E2E Surgeon"). Orden: `001 → 002 → 004`. Verificado solo de forma
estática (columnas, códigos de catálogo, gramática PostgreSQL con `pglast`); **aún no se ha ejecutado contra
PostgreSQL**. Procedimiento y credenciales: [`POSTGRESQL_E2E_TESTING.md`](POSTGRESQL_E2E_TESTING.md).

`data/migrations/005_canonical_english_data.sql` (nuevo, solo datos): convierte bases sembradas con los valores
anteriores en español (002, textos de familias de 003 y 004) a los valores canónicos en inglés. Localiza filas
por `code` / UUID fijo y solo reemplaza valores que siguen siendo exactamente el texto semilla original
(respeta ediciones de usuarios); sin DDL, sin DELETE, sin cambios de UUID/FK; transaccional e idempotente.
Probado ejecutándolo sobre SQLite (`english_migration_check`); **pendiente de ejecutar en PostgreSQL**.

`003_seed_demo.sql` (escenario histórico no usado por el flujo actual) sigue en español; si se carga, 005
convierte sus nombres/textos de familias, pero no el resto de sus datos demo.

## 19b. Localización de la interfaz (EN / ES)

**English is the canonical language for persisted database business data and catalog data. UI
localization is handled at the application layer.** La localización solo cambia la presentación: nunca
escribe filas de catálogo ni reescribe datos históricos.

| | |
|---|---|
| Idiomas | `en` (predeterminado y respaldo) y `es` (números/fechas con convenciones `es_MX`) |
| Tecnología | Flask-Babel 4 (gettext); Jinja usa gettext *newstyle* |
| Catálogos | `apps/BackendWebFlask/translations/messages.pot` (896 mensajes) y `translations/es/LC_MESSAGES/messages.po` (896 traducidos) + `.mo` compilado |
| Cambio de idioma | `POST /preferences/locale` con `locale` = `en` \| `es` y `next` = ruta local |

**Resolución del idioma** (`localization.resolve_locale`, una vez por petición en `before_request`, antes de
cualquier lógica de negocio):
1. Usuario autenticado → `user.ui_preferences.locale` (leído de la BD del monolito; el auth service no cambia).
2. Visitante anónimo → `session['locale']` (sesión web firmada de Flask).
3. En cualquier otro caso (valor ausente, distinto de `en`/`es`, JSON malformado, usuario inexistente,
   error de lectura) → `en`. No se usa el idioma del navegador.

Tras iniciar sesión manda la preferencia guardada del usuario (regla de `PEF-DS01`). Al cerrar sesión el
idioma se conserva en la sesión web para la landing y Sign In.

**Cambio de idioma** (`partials/language_selector.html`, el mismo control visual de siempre, ahora con un
`<form method="post">` por opción; accesible por teclado, `aria-current` en el idioma actual):
- Validación estricta: solo `en` o `es`; cualquier otro valor → **400** y no se guarda nada.
- Autenticado: actualiza **solo** la clave `locale` de `ui_preferences` en una única sentencia (PostgreSQL:
  `ui_preferences || '{"locale":"es"}'::jsonb`; SQLite de pruebas: `json_patch`); el resto de claves
  (`theme`, etc.) se conserva y `updated_at` se actualiza. También se guarda en la sesión web.
- Anónimo: solo en la sesión web.
- Redirige (303) a la misma página (`next`, solo rutas locales: sin open redirect). No toca cookies/tokens del
  auth service, no cierra la sesión, no cambia el estado de la WorkSession ni datos de negocio.

**Valores de la BD traducidos por código** (`localization/labels.py`): la clave de traducción es
`(catálogo, código)` → gettext `msgctxt` = catálogo, `msgid` = nombre canónico en inglés
(p. ej. `session_status.open` → "Open" / "Abierta", `procedure_type.appendectomy` → "Appendectomy" /
"Apendicectomía", `instrument_family.KELLY` → "Kelly Forceps" / "Pinza Kelly", `role.operator_cde` →
"CDE Operator" / "Operador CDE"). Contextos: todos los catálogos de `002` (`gender`, `specialty`,
`procedure_type`, `surgical_role`, `operation_status`, `session_status`, `instrument_cycle_status`,
`instrument_category`, `usage_context`, `discrepancy_reason`, `checkpoint_reason`, `operation_phase`,
`event_type`, `processing_purpose`), las familias sembradas de `004` (nombre y función), `role`,
`role_short`, `role_description` y códigos de la aplicación (`review_status`, `vision_result`,
`supervisor_decision`, `audit_outcome`, `inference_provider`, `validation_kind`). Las vistas reciben el código
junto a la etiqueta (`status_code`, `reason_code`, `phase_code`, `procedure_code`, `result_codes`, …).
Ninguna lógica compara nombres visibles.

**Lo que no se traduce (se muestra tal cual está guardado):**
- nombres creados por usuarios: kits, quirófanos, estaciones, pacientes, médicos, etiqueta de técnica,
  instituciones, roles personalizados, familias/procedimientos creados por un administrador;
- filas sembradas **renombradas** por un administrador (catálogos editables: procedimientos, familias,
  descripción de rol): si el valor guardado ya no es el canónico, es contenido del usuario;
- texto libre: justificaciones de corrección, notas del Supervisor, textos de auditoría;
- códigos y claves: códigos de catálogo, acciones de auditoría (`CREATE_SESSION`, …), `resource_type`,
  UUID, `version_tag` (`e2e-controlled-dev`), IDs de clase YOLO, confianza numérica, provider `controlled`;
- `Discrepancy.description` y los payloads JSON de CountEvent: se guardan en inglés canónico (no se
  reescriben); las pantallas no los muestran, sino etiquetas reconstruidas desde códigos y cantidades.

En los formularios de edición el campo muestra el valor canónico guardado y, para filas del sistema, una ayuda
con el nombre que ve la interfaz en el idioma actual.

**Fechas y números:** las fechas/horas se muestran en ISO `YYYY-MM-DD HH:MM` en ambos idiomas (sin cambios de
zona horaria ni de valores guardados); porcentajes, decimales, duraciones y días de la semana de los gráficos
se formatean según el idioma.

**JavaScript:** los textos que construye JS se leen de atributos `data-*` renderizados por Flask o de
`PEF_I18N.t(key)`; `base.html` inyecta una sola vez `localization.js_messages()` como JSON. No hay tablas de
traducción en JS.

**Añadir o cambiar un texto de la interfaz** (desde `apps/BackendWebFlask`):
```bash
pybabel extract -F babel.cfg -k _c:1c,2 --no-location --sort-output --project="PEF Instrumental Quirurgico" --copyright-holder="PEF UDEM" -o translations/messages.pot .
pybabel update -i translations/messages.pot -d translations -l es --no-fuzzy-matching --ignore-obsolete -w 100
# traducir los msgstr vacíos de translations/es/LC_MESSAGES/messages.po
pybabel compile -d translations
```
En plantillas: `{{ _("Texto %(n)s", n=valor) }}` (un `%` literal se escribe `%%`); en Python:
`from flask_babel import gettext as _`. Los mensajes que se **persisten** en la BD no se envuelven en `_()`.
Tras cambiar las traducciones basta reiniciar el proceso Flask.

**Añadir un código de catálogo traducible:** añadir la entrada `código: _c('<catálogo>', '<nombre canónico en
inglés>')` en `localization/labels.py` (el nombre debe ser el mismo que siembra la BD), ejecutar los tres
comandos anteriores y traducir la nueva entrada `msgctxt`. La suite `localization` falla si un código de
`002_seed_catalogs.sql` no tiene etiqueta o si alguna cadena del código fuente no está traducida.

## 20. Variables de entorno nuevas (`apps/BackendWebFlask/.env.example`)

| Variable | Default | Uso |
|---|---|---|
| `CAPTURE_STORAGE_ROOT` | vacío → `apps/BackendWebFlask/instance/evidence` | Carpeta privada de evidencias (ignorada por git) |
| `CAPTURE_STORAGE_BUCKET` | `pef-evidence` | Nombre lógico guardado en `media_asset.bucket` |
| `CAPTURE_MAX_BYTES` | `10485760` | Tamaño máximo de un JPEG (también fija `MAX_CONTENT_LENGTH`) |
| `VISION_INFERENCE_PROVIDER` | `controlled` | Provider de inferencia (único disponible hoy) |
| `VISION_CONFIDENCE_THRESHOLD` | `0.70` | Umbral bajo el cual se crea `low_confidence` |

## 21. Pruebas

Las suites están **en el repositorio**:

- `apps/BackendWebFlask/tests/` — `scenarios/*.py` (escenarios ejecutables, cada uno con su propia BD
  SQLite temporal y autenticación simulada) + wrappers `test_*.py` para pytest (`conftest.py` ejecuta cada
  escenario en un proceso separado).
- `apps/BackendAuthService/tests/` — escenario `auth_audit.py` (LOGIN/LOGOUT, `/verify` con usuarios inactivos).
- Dependencia de pruebas: `pytest` en `requirements-dev.txt` de cada app. `pglast` es opcional (gramática PG del seed).
  `Flask-Babel`/`Babel` forman parte de `requirements.txt` de BackendWebFlask (también los usa la suite `localization`).

```bash
pip install -r apps/BackendWebFlask/requirements-dev.txt -r apps/BackendAuthService/requirements-dev.txt
python -m pytest apps/BackendWebFlask/tests apps/BackendAuthService/tests -s
```

Resultados verificados desde el repositorio (2026-09-28), 12 tests pytest en verde (11 BackendWebFlask + 1 BackendAuthService) — actualizado 2026-09-29:

| Suite | Checks |
|---|---|
| `python -m compileall apps/BackendWebFlask apps/BackendAuthService` | OK |
| `schema_check` — modelos vs `001_init.sql` | 50/50 tablas, 0 diferencias |
| `app_boot` — `create_app()` + `configure_mappers()` | OK (68 reglas de URL) |
| `admin` | 149 |
| `sessions` (incluye 4 nuevos de ProcedurePhase estricto) | 69 |
| `evidence` | 56 |
| `vision` | 53 |
| `review` | 83 |
| `analytics` | 44 |
| `seed_check` — `004_seed_e2e.sql` estático + gramática PG de 001/002/004/005 | 61 (con `pglast`) |
| `english_migration_check` — seeds en inglés + ejecución de 005 sobre datos en español | 34 |
| `localization` — idioma EN/ES, preferencia, etiquetas por código, seguridad de datos, catálogos gettext | 112 |
| `auth_audit` (BackendAuthService) | 14 |

Los checks de `/verify` (5) y de LOGIN/LOGOUT (9) que antes estaban en `sessions`/`analytics` se movieron a
`auth_audit`; la cobertura total se conserva.

## Pending PostgreSQL Integration Validation

**Estado: EN CURSO.** Según el equipo (2026-09-29): en PostgreSQL 17 (`pef_instrumental_dev`) se aplicaron `001`, `002`, `004` y `005`; ambos servicios, el login real, las WorkSessions y el flujo E2E funcionan con `FRONTEND_DEMO_MODE=false`. Pendiente: repetir el flujo con el cambio de idioma EN → ES → EN (apartado 7 de [`POSTGRESQL_E2E_TESTING.md`](POSTGRESQL_E2E_TESTING.md)); la escritura de `ui_preferences` con `||` sobre JSONB solo se ha probado en SQLite.

Toda la lógica se ha probado principalmente con **SQLite temporal**. **El sistema no ha sido validado
todavía contra PostgreSQL real.** Es obligatorio probar en PostgreSQL:

- JSONB (payloads, `ui_preferences`, `roi`) y el CHECK `jsonb_typeof`;
- UUID, `gen_random_uuid()`, TIMESTAMPTZ y comparaciones de fechas con zona horaria;
- CHECK, UNIQUE e **índices parciales** (emulados en SQLite);
- FK y `ON DELETE`; defaults del servidor;
- transacciones y rollback/compensación reales;
- `002_seed_catalogs.sql` y `003_seed_demo.sql` sobre el esquema (los datos demo de sesiones/eventos fueron
  creados antes de este flujo y deben revisarse con las pantallas actuales);
- el auth service con `psycopg` (INSERT de auditoría con `CAST(... AS uuid/inet)`);
- el flujo completo de extremo a extremo.

## Remaining Work

### Required before final validation
- Seguir [`POSTGRESQL_E2E_TESTING.md`](POSTGRESQL_E2E_TESTING.md): crear la BD, aplicar `001_init.sql`,
  `002_seed_catalogs.sql` y `004_seed_e2e.sql`, configurar los `.env`, arrancar ambos servicios.
- Ejecutar el flujo end-to-end real; verificar archivos de evidencia en disco, audit logs y dashboards.

### Future integration
- Worker YOLO real y pesos entrenados, registrados como provider en `vision_service` en lugar de `controlled`.
- Opcionalmente MongoDB (checkpoints/boxes), Redis (tokens), GCS/MinIO real, microservicios e infraestructura futura.

### Known limitations
- `role` sin columna `active` (no se desactivan roles).
- UUID en lugar de folio humano de sesión.
- Discrepancy sin estado de workflow persistido (derivado).
- Tiempo de resolución basado en el `CountEvent` origen.
- "Today" y periodos en UTC.
- Audit logs limitados a 300 eventos; listas sin paginación; filtros de listas solo en cliente (requieren JS).
- Evidencia en disco local (no GCS); sin retención/purga automática (`retention_until` no se calcula).
- Validación JPEG sin decodificar la imagen (cabecera/cierre).
- Un solo run de inferencia por sesión; sin cancelación de sesión.
- Nota de aprobación del Supervisor no persistida.
- `token_store` del auth service en memoria.
- Localización: fechas/horas en formato ISO en ambos idiomas; solo `en`/`es`; el idioma elegido de forma
  anónima no se copia a `ui_preferences` al iniciar sesión (manda la preferencia guardada); el preview de
  `devtools` no traduce.

## 22. Estado de requisitos

| Área | Estado |
|---|---|
| Public Landing | Implemented (enlaces Privacy/Terms como texto; no hay páginas) |
| Authentication | Implemented |
| Admin CRUD | Implemented (sin UI para Institutions/Operations/Patients/Physicians) |
| WorkSession | Implemented |
| Expected Inventory Snapshot | Implemented |
| Capture | Implemented (almacenamiento local) |
| Vision workflow | Implemented with controlled provider |
| Automatic discrepancies | Implemented |
| Human validation | Implemented |
| Supervisor workflow | Implemented |
| Session closing | Implemented |
| Audit | Implemented |
| UI localization (EN / ES) | Implemented |
| Dashboards | Implemented |
| Reports/Indicators | Implemented |
| PostgreSQL integration validation | **Pending** |
| Real YOLO worker | **Pending** |
