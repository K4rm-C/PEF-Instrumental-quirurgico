# Contrato funcional formal · Operador de estación y Supervisor SPD

## Cómo leer este documento

Cada requisito funcional (RF) sigue la misma plantilla:

1. **Identificador y nombre**
2. **Actor** (quién puede ejecutarlo)
3. **Cómo se llega** (navegación / condición de entrada)
4. **Qué muestra la interfaz** (campos, botones, mensajes exactos esperados)
5. **Qué hace el usuario**
6. **Qué ocurre en el sistema** (orden de pasos: Backend, SQL sobre tablas, vision-worker, evidence-worker, Mongo, storage)
7. **Cuándo se escribe o lee cada almacén**
8. **Configuración** (parámetros que alguien debe poder ajustar sin redesplegar lógica de negocio si es posible)
9. **Salida / pantalla siguiente**
10. **Errores y bloqueos** que la UI debe contemplar

**Convención de escritura en bases de datos**


| Quién escribe              | Qué escribe                                                                                        | Cómo                                                                                                                                                                    |
| -------------------------- | -------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Backend Web**            | Hechos de negocio en PostgreSQL                                                                    | SQL parametrizado vía repositorio/ORM de la aplicación. **No** hay stored procedures obligatorias en v1; si el equipo añade SP después, el RF no cambia de significado. |
| **Vision-worker**          | Solo mensajes WSS de percepción; **no** es dueño de PostgreSQL                                     | Inferencia + JSON DS10 hacia la estación                                                                                                                                |
| **Evidence-worker**        | Object storage + fila `media_asset` (vía Backend o adaptador) + documento Mongo `checkpoint_frame` | Cola asíncrona; no bloquea la cámara                                                                                                                                    |
| **Cliente (Electron/web)** | Nunca abre conexión JDBC/SQL ni credenciales de Mongo/MinIO                                        | Solo HTTPS al Backend y WSS al vision-worker                                                                                                                            |


**Fuera de este contrato:** administración de usuarios/roles/contraseñas, ARCO, clientes FHIR, importaciones masivas, catálogos maestro y App de piso (eso vive en [rf_admin_app.md](rf_admin_app.md)).

---

## Glosario operativo (lenguaje de pantallas)


| Término en UI               | Significado                                                     | Dónde vive en datos                                                                                        |
| --------------------------- | --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Sesión / Counting session   | Periodo de conteo ligado a una operación                        | Tabla `work_session`                                                                                       |
| Operación / Procedure case  | Acto quirúrgico programado (sala, paciente, médicos, tipo)      | Tabla `operation` (+ `operation_patient`, `operation_physician`)                                           |
| Kit                         | Receta de familias e instrumentos con cantidades                | `kit` + `kit_item`                                                                                         |
| Expected inventory          | Lista congelada de lo que **debe** haber en esta sesión         | `expected_inventory`                                                                                       |
| Detected / AI detected      | Cantidad sugerida por el modelo                                 | Viene del vision-worker; se refleja en `count_event.detected_quantity` cuando el Backend persiste el hecho |
| Validated count             | Cantidad aceptada por humano                                    | Correcciones en `human_correction` y/o `count_event` manual                                                |
| Discrepancia                | Diferencia esperada vs validada/detectada que requiere atención | `discrepancy`                                                                                              |
| Precaución / marca temporal | Ausencia prolongada que luego se recupera sola                  | `discrepancy` con resolución automática + evidencia                                                        |
| Fase / Counting phase       | Etapa clínica del conteo (pre, intra, cierre, …)                | `work_session.current_phase_id` → `cat_operation_phase`                                                    |
| Estación / Capture station  | Puesto de cámara                                                | `capture_station`                                                                                          |
| GIF de evidencia            | Ráfaga visual del momento                                       | Archivo en MinIO/GCS + `media_asset` + Mongo `checkpoint_frame`                                            |
| Aviso de privacidad         | Texto legal versionado que el hospital usa para el tratamiento  | Tabla `privacy_notice_version` (`document_uri`, `content_sha256`, `version`, `active`)                     |
| Acuerdo de tratamiento      | Confirmación de que **esta** sesión tiene aviso aceptado        | Tabla `session_processing_agreement` (1 fila máx. por `session_id`)                                        |
| Modo de captura             | Con visión (cámara + IA) o solo conteo manual                   | Se **congela al Start Session**: `vision` si hay acuerdo; `manual_no_privacy` si no                        |


**Regla formal de privacidad ↔ captura (v1)**

1. Programar una sesión **no exige** todavía el acuerdo; la sesión puede quedar `scheduled` sin fila en `session_processing_agreement`.
2. El supervisor **debe poder** subir un PDF/HTML nuevo o **confirmar** una versión ya activa del aviso y ligarla a la sesión (ver RF-SP-02). Sin eso, la sesión sigue siendo válida.
3. Al pulsar **Start Session**, el Backend mira si existe `session_processing_agreement` para esa sesión:
  - **Sí** → modo `vision` → pantalla live con cámara (RF-OP-04).
  - **No** → modo `manual_no_privacy` → **no** se abre WSS, **no** hay captura ni GIF de evidencia de conteo; el operador entra a la vista de progreso manual (RF-OP-04M) hasta el cierre con cantidades reportadas a mano.
4. El modo queda escrito en `work_session` (campo de aplicación o clave en `payload` JSON: `capture_mode`) y **no cambia** aunque SPD agregue el acuerdo después del Start. Motivo: no mezclar mitad de sesión sin cámara con mitad con IA.
5. `purpose_quality_ops` en el acuerdo es siempre `TRUE` (CHECK de BD). `purpose_model_improvement` es opt-in del supervisor al confirmar.

---

## Roles y acceso


| Código de rol      | Nombre                    | Cliente                                        | Puede                                                                                                                             |
| ------------------ | ------------------------- | ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `station_operator` | Operador de estación      | App estación (web o Electron) en el PC de sala | Solo sesiones asignadas; begin/continue; live **o** progreso manual sin cámara; validación/cierre; documentar hold y liberar a SPD |
| `spd_supervisor`   | Supervisor SPD            | Portal web                                     | Programar sesiones **incluyendo aviso de privacidad**; ver todas; dashboard; review **post-liberación**; extras; kits; métricas   |
| `or_charge`        | Supervisor de piso (SdP)  | App de piso                                    | Cola de `or_hold`, comentarios y lectura de evidencias. **No** cierra ciclo SPD. Contrato: [rf_admin_app.md](rf_admin_app.md)     |
| `it_admin`         | Administrador TI          | Portal Admin                                   | Catálogos, IAM, modelos. Contrato: [rf_admin_app.md](rf_admin_app.md)                                                              |


**Reglas de acceso fijas**

1. Tras login, el Backend emite sesión/JWT (Redis según DS01).
2. Cada request de negocio lleva identidad; el Backend aplica el rol antes de tocar PostgreSQL.
3. El operador **no** tiene pantalla de métricas globales ni edición de kits.
4. El vision-worker **no** cierra sesiones ni acepta discrepancias.

---

## Estados oficiales de sesión (`cat_session_status.code`)

 Estos códigos son el contrato de estado. La UI muestra la etiqueta en español/inglés del mockup; el Backend guarda el `code`.


| code                  | Etiqueta UI            | Quién lo provoca                                                                                          |
| --------------------- | ---------------------- | --------------------------------------------------------------------------------------------------------- |
| `scheduled`           | Scheduled / Programada | Supervisor crea/guarda la sesión                                                                          |
| `in_progress`         | In Progress            | Operador pulsa **Start Session**                                                                          |
| `or_hold`             | OR hold                | Operador confirma **faltante** humano (validated/reported < expected). **Extras no** disparan este estado |
| `awaiting_spd_review` | Awaiting Review        | Operador termina validación **sin** faltantes (match o solo extras documentados)                          |
| `correction_required` | Correction Required    | Operador **libera** el hold a SPD (RF-OP-07) con resolución sugerida                                      |
| `closed`              | Closed                 | Supervisor SPD confirma recuperación / cierra el caso                                                     |
| `aborted`             | Aborted                | Cancelación controlada (SPD u operador según permiso)                                                     |


---

## Canales técnicos (siempre los mismos)


| Canal                                       | Uso                                                                                                                 |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| HTTPS → Backend Web                         | Login, listas, start, cambio de fase, validación, cierre, programación SPD, **aviso de privacidad**, kits, métricas |
| WSS → vision-worker                         | Solo frames y detecciones (DS10) mientras la sesión está `in_progress` **y** `capture_mode = vision`                |
| Cola `pef-evidence-queue` → evidence-worker | Armar GIF, subir storage, escribir Mongo (**no** se usa en `manual_no_privacy`)                                     |


---

# PARTE A — OPERADOR DE ESTACIÓN

El operador abre la app de estación, inicia sesión con su usuario `station_operator`, y **entra directo** a la lista de sesiones. No existe dashboard de métricas para este rol.

---

## RF-OP-01 · Lista de mis sesiones

### Actor

`station_operator`

### Cómo se llega

1. Login exitoso en la app de estación.
2. La app navega automáticamente a **Sessions** (única home del operador).

### Qué muestra la interfaz

Tabla con una fila por sesión asignada al operador (`work_session.user_id` = usuario actual).


| Columna UI             | Origen de dato                                                                                                                                  |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Session ID             | Prefijo visual `WS-` + identificador corto derivado de `work_session.id` (o código de negocio si se añade después; v1 puede mostrar UUID corto) |
| Procedure              | Nombre del tipo de procedimiento + sala (desde `operation` / `cat_procedure_type` / `operating_room`)                                           |
| Kit type               | `kit.name` vía `work_session.kit_id`                                                                                                            |
| OR                     | Código/nombre de `operating_room`                                                                                                               |
| Created / Scheduled at | Fecha-hora de programación (timestamp de creación de sesión u `operation` según se guarde al programar)                                         |
| Status                 | Badge según `cat_session_status.code`                                                                                                           |
| Privacy / Capture      | Si hay `session_processing_agreement` (o `capture_mode=vision` tras Start): “Aviso OK · Visión”; si no: “Sin aviso · Manual” (badge distinto)   |
| Discrepancies          | Número de filas `discrepancy` con `resolved = false` de esa sesión                                                                              |
| Action                 | Solo botones permitidos (abajo)                                                                                                                 |


**Filtros del operador (opcionales y locales a “mis sesiones”):** buscar por texto; filtrar por estado. **No** hay selector “todas las salas del hospital”.

**Botones de acción por fila (únicos permitidos)**


| Botón            | Visible cuando el status es | Acción                                                                                                                                                                    |
| ---------------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **View details** | cualquiera de la lista      | Abre panel/página de solo lectura con el resumen del caso                                                                                                                 |
| **Begin**        | `scheduled`                 | Va a RF-OP-03 (confirmación pre-inicio)                                                                                                                                   |
| **Continue**     | `in_progress`               | Según `capture_mode` congelado: `vision` → RF-OP-04 (o RF-OP-05/06 si ya iba en cierre); `manual_no_privacy` → RF-OP-04M (o RF-OP-06M si ya iba en reporte de cantidades) |
| **Continue hold** | `or_hold`                  | RF-OP-07 (documentar y liberar a SPD) |


No mostrar en esta lista: Close Session, Review Count, ni acciones de supervisor.

### Qué hace el usuario

Revisa la lista; elige View details, Begin o Continue.

### Qué ocurre en el sistema

1. App → `GET /api/.../my-sessions` (HTTPS) con JWT.
2. Backend verifica rol `station_operator`.
3. Backend ejecuta **SELECT** (SQL) uniendo:
  - `work_session`
  - `cat_session_status`
  - `operation`, `operating_room`, `cat_procedure_type`
  - `kit`
  - conteo `COUNT(discrepancy)` donde `session_id` y `resolved = false`
  - filtro `work_session.user_id = :current_user_id`
4. Respuesta JSON → pinta la tabla.
5. **No** hay WSS. **No** escribe tablas.

### Configuración

- Formato de fecha/hora de la columna temporal (zona hospitalaria).  
- Longitud del ID corto mostrado.

### Salida

Misma pantalla, o detalle (RF-OP-02), o Begin (RF-OP-03), o Continue (RF-OP-04/05).

### Errores UI

- Sin sesiones: mensaje “No tienes sesiones asignadas”.  
- Error de red/auth: mensaje de reintentar / volver a login.

---

## RF-OP-02 · Ver detalle breve (solo lectura)

### Actor

`station_operator`

### Cómo se llega

Botón **View details** en RF-OP-01.

### Qué muestra la interfaz

Página o panel modal de **solo lectura** con:

- Session ID, status  
- Procedimiento, OR, estación prevista  
- Paciente(s) y médico(s) listados  
- Kit y tabla de expected inventory **previsto** (si aún `scheduled`: desde borrador de programación; si ya inició: desde `expected_inventory`)  
- Estado de aviso de privacidad:
  - Si existe `session_processing_agreement`: versión del aviso (`privacy_notice_version.version`), fecha `agreed_at`, enlace de solo lectura al `document_uri`, y finalidades (`quality_ops` siempre sí; `model_improvement` sí/no).
  - Si no existe: mensaje claro *“Sin acuerdo de aviso de privacidad. Al iniciar, esta sesión entrará en modo conteo manual (sin cámara ni IA).”*
- Número de discrepancias abiertas  
- Sin botones de editar kit, personal ni stock

Botones: **Close/Back** a la lista; si aplica, **Begin** / **Continue** iguales a RF-OP-01.

### Qué ocurre en el sistema

`GET` detalle → Backend **SELECT** de las mismas tablas + `expected_inventory` o snapshot de programación + `operation_patient` / `operation_physician` / `physician` / `patient` + **LEFT JOIN** `session_processing_agreement` → `privacy_notice_version`. Sin escritura.

### Salida

Vuelve a RF-OP-01 o avanza a Begin/Continue.

---

## RF-OP-03 · Confirmación pre-inicio (Begin > Start Session)

### Actor

`station_operator`  
(La misma plantilla visual la usa el supervisor al **programar**; ver RF-SP-02. Aquí el operador **no edita** los campos de programación.)

### Cómo se llega

**Begin** desde sesión en estado `scheduled`.

### Qué muestra la interfaz (sección por sección)

**Sección 1 — Session setup (todo read-only para operador)**  

- Operator (nombre del usuario asignado)  
- Procedure  
- Patient  
- Physician / Surgeon  
- Operating room  
- Kit  
- Counting phase inicial (primera fase activa del procedimiento)  
- Date & time programados  
- Capture station

**Sección 2 — Expected inventory preview**  
Tabla: Instrument (nombre de familia) | Expected quantity.  
Fila total: suma de cantidades.  
Banner fijo: *“Expected inventory will be frozen when the session starts.”*

**Sección 3 — Instrument readiness**  
Checkbox obligatorio: *“I confirm the instrument set is physically present and matches the expected inventory.”*  
Debe estar marcado para habilitar Start.

**Sección 4 — Aviso de privacidad (solo lectura para el operador)**  


| Condición al abrir Begin              | Qué ve el operador                                                                                                                                                                                                                                                                   |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Existe `session_processing_agreement` | Badge verde “Aviso confirmado · captura con visión habilitada”; versión del aviso; enlace “Ver documento”; texto de finalidades.                                                                                                                                                     |
| No existe acuerdo                     | Badge ámbar “Sin aviso de privacidad · al iniciar NO habrá cámara ni IA”; texto fijo: *“Podrás confirmar el instrumental e iniciar. Después reportarás las cantidades a mano y cerrarás la sesión. Si alguna cantidad queda por debajo de lo esperado, se abrirá una discrepancia.”* |


El operador **no** sube ni acepta el aviso en esta pantalla (eso es del supervisor en RF-SP-02). Solo se informa del modo que tendrá al Start.

**Pie**  

- Cancel → vuelve a RF-OP-01 sin cambios.  
- **Start Session** (habilitado solo con checkbox de instrumental).

### Qué hace el usuario

Lee la información programada por SPD, verifica la charola física, marca el checkbox, pulsa **Start Session**.

### Qué ocurre en el sistema al pulsar Start Session

Todo en una **transacción PostgreSQL** del Backend, excepto los pasos de evidencia (solo si hay modo visión):

1. Backend revalida: sesión existe, status = `scheduled`, `user_id` = operador actual.
2. Backend determina `capture_mode`:
  - **SELECT** `session_processing_agreement` WHERE `session_id = :id`
  - Si hay fila → `capture_mode = 'vision'`
  - Si no hay fila → `capture_mode = 'manual_no_privacy'`
3. **UPDATE** `work_session`:
  - `status_id` → código `in_progress`  
  - `started_at` = now()  
  - `station_id` = estación mostrada  
  - `current_phase_id` = fase inicial  
  - `phase_changed_at` = now()  
  - `updated_at` = now()  
  - Persiste `capture_mode` (columna de aplicación o `payload->>'capture_mode'`; v1 contrato: **obligatorio** que quede consultable después del Start)
  - **Si** `capture_mode = vision`: **UPDATE** `work_session.yolo_model_id` = `yolo_model.id` del registro con `active = true`. Ese pin **no** cambia si Admin activa otro peso después. Worker y UI de sesiones ya abiertas leen esta FK, **no** el activo global. Sesiones `scheduled` dejan `yolo_model_id` NULL hasta el Start.  
  - **Si** `manual_no_privacy`: `yolo_model_id` permanece NULL.
4. **Congelar inventario esperado** — **INSERT** en `expected_inventory` una fila por familia:
  - `session_id`, `family_id`, `expected_quantity`  
  - `source = 'kit_snapshot'` para líneas del kit  
  - `source = 'manual'` para líneas que SPD agregó/quitó al programar (el set ya viene resuelto desde RF-SP-02)  
  - Si ya existían borradores previos de la misma sesión, se reemplazan de forma controlada (DELETE+INSERT o UPSERT) **solo en este Start**; después quedan inmutables salvo RF-SP-04.
5. Si hay piezas serializadas preasignadas al case cart: **INSERT** `instrument_reservation` (`active = true`, `operation_id`, `instrument_id`) y, si aplica, **UPDATE** `instrument.cycle_status_id` hacia estado de reservado/en uso según catálogo de ciclo.
6. **INSERT** `count_event`: tipo de evento de apertura (`cat_event_type.code` = p. ej. `session_start`), `session_id`, `user_id`, `occurred_at`, `payload` JSON con `phase_id`, `phase_code`, `capture_mode`, y `model_version` **solo si** `capture_mode = vision`.
7. **INSERT** `access_audit`: actor operador, acción `session.start`, outcome success, resource `work_session`.

**Solo si** `capture_mode = vision` **(pasos 8–10):**

1. Backend **encola** job en `pef-evidence-queue`: `{ session_id, reason: "start", phase_id, frame_snapshot_ref }`.
2. Evidence-worker (proceso aparte):
  - Congela N frames del buffer o captura de arranque  
  - Construye GIF  
  - Sube a MinIO/GCS  
  - **INSERT** `media_asset` (SQL vía Backend/adaptador)  
  - **INSERT** documento Mongo `checkpoint_frame` (`session_id`, `reason`, `phase_id`/`phase_code`, `media_asset_id`, detecciones si hay, timestamp)
3. Estación abre **WSS** `session.bind` al vision-worker con `session_id`, `station_id` y el `yolo_model_id` **pineado** en `work_session` (no el activo global).

**Si** `capture_mode = manual_no_privacy`**:**

1. **No** encolar evidencia de cámara.
2. **No** abrir WSS.
3. **No** llamar al vision-worker.
4. Motivo: sin `session_processing_agreement` no hay base de tratamiento documentada para frames/evidencia de la charola; el conteo sigue por reporte humano.

**Motivo de congelar expected:** lo que se compare después en live o en cierre manual debe ser exactamente lo confirmado al inicio; editar el kit maestro mañana no debe alterar esta sesión.

### Configuración

- Texto del checkbox, del banner de expected y de los badges de privacidad (i18n).  
- Número de frames / duración del GIF de `start` (evidence-worker) — solo modo visión.  
- Timeout para fallar bind WSS (solo modo visión; la sesión ya puede estar `in_progress`).

### Salida


| `capture_mode`      | Pantalla siguiente                    |
| ------------------- | ------------------------------------- |
| `vision`            | RF-OP-04 Conteo en vivo               |
| `manual_no_privacy` | RF-OP-04M Progreso manual sin captura |


### Errores UI

- Checkbox sin marcar: Start deshabilitado.  
- Sesión ya no `scheduled`: mensaje y volver a lista.  
- Fallo SQL: rollback completo; mensaje “No se pudo iniciar; reintente”.  
- Fallo evidencia (solo visión): Start **sí** puede completarse; se registra warn de evidencia pendiente (la cámara no depende del GIF).

---

## RF-OP-04 · Conteo en vivo (charola + expected)

### Actor

`station_operator`  
Sesión en `in_progress` **y** `capture_mode = vision`.

### Cómo se llega

Tras Start Session con acuerdo de privacidad, o **Continue** desde la lista cuando `capture_mode = vision`.

### Qué muestra la interfaz

**Encabezado de contexto (siempre visible)**  
Session ID, Procedure, Patient, Physician, OR, Kit, Station, badge **In Progress**, fase actual, badge **Visión / Aviso OK**.

**Panel izquierdo — Overhead capture**  

- Video/stream o frame actual de la charola.  
- Cajas dibujadas con nombre de familia (no inventar nombres fuera del mapeo `model_class`).  
- Timestamp del último frame mostrado.  
- Estado de IA: p. ej. “Detecting…” / “AI Detection active”.

**Panel derecho — Expected vs detected (ventana reciente)**  
Tabla por cada fila de `expected_inventory`:


| Columna    | Contenido                                                                                                                                           |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Instrument | Nombre de `instrument_family`                                                                                                                       |
| Expected   | `expected_quantity` congelado                                                                                                                       |
| Detected   | Cantidad agregada en la **ventana deslizante de los últimos N segundos** (default **5 s**)                                                          |
| Difference | Detected − Expected (o Expected − Detected; la UI debe fijar una convención y mostrarla igual en todas las pantallas: **v1 = Detected − Expected**) |
| Confidence | Confianza agregada de las cajas de esa familia en la ventana (promedio)                                                                             |
| Result     | Match / Missing / Extra / Possible miss según umbrales                                                                                              |


Totales: Total Expected | Total Detected (ventana).

**Controles**  

- **Change phase** (abre selector de siguiente fase válida).  
- **Finish counting / Proceed to closing summary** (detiene live y va a RF-OP-05).  
- Texto fijo: los resultados de IA son sugerencias.

### Qué hace el usuario

Observa la charola y el panel; cambia de etapa cuando el procedimiento lo requiera; al terminar el conteo asistido pasa a cierre de detección.

### Qué ocurre en el sistema (ciclo continuo)

**Cada frame (o cada stride configurado)**  

1. Estación envía WSS `frame.submit` (imagen o referencia + `frame_id`, `session_id`).
2. Vision-worker:
  - Corre el modelo (`yolo_model` activo / pin de sesión)  
  - Traduce clase → `family_code` con `model_class`  
  - **Afina** contra `expected_inventory` de la sesión (familias no esperadas se degradan o se marcan Extra; no inventan líneas de expected)  
  - Responde WSS `detections.result` con cajas, `family_code`, confidence, y `counts_by_family`
3. Estación actualiza el panel derecho con agregación de **últimos 5 s**.
4. Estación mantiene **ring buffer** en RAM (últimos M segundos de frames) para evidencia.

**Persistencia de hechos de conteo (no cada frame)**  
El Backend recibe de la estación (HTTPS) o de un hook autorizado un resumen cuando:

- cambia el agregado de forma estable, o  
- hay heartbeat periódico (p. ej. cada 10–30 s), o  
- hay evento de fase / precaución

Entonces Backend hace **INSERT** `count_event` (`event_type` = `auto_count` u homólogo), con `family_id` (o `NULL` si el snapshot es de tablero), `expected_quantity`, `detected_quantity`, `occurred_at`, `session_id`, `payload`:

- fase, `window_seconds` / muestreo, `model_version`
- **`confidences`:** arreglo de scores 0..1, **una entrada por caja reportada** (en snapshot de tablero: `payload.board.families[].confidences`). Es la medida transparente de cómo rindió el modelo en ese hecho.
- **No** se guardan `avg` / `best` / `worst`: si la UI los muestra, los deriva de esa lista.
- Bboxes / top-k completos **no** son obligatorios en cada `auto_count` (siguen opcionales en hitos o en Mongo DS06).

**Motivo de no guardar cada frame en PostgreSQL:** evitar inflar PG; el detalle visual va a Mongo solo en hitos/alertas.

### Cambio de etapa (subflujo de RF-OP-04)

1. Usuario elige la siguiente fase permitida (orden `procedure_phase.sort_order`).
2. Backend **UPDATE** `work_session.current_phase_id`, `phase_changed_at`.
3. Backend **INSERT** `count_event` con `event_type` = `phase_change` y `payload` `{ from_phase, to_phase, phase_id, phase_code }`.
4. Si hay regla de “familias solo de la fase que termina”: Backend evalúa missing de esa fase y puede **INSERT** `discrepancy` abierta con fase en descripción/payload del evento origen.
5. Backend encola evidencia `reason = state_change` → evidence-worker (GIF → storage → `media_asset` → Mongo).

### Precauciones temporales (subflujo automático)

**Configuración**


| Parámetro                        | Default v1 | Significado                                                                      |
| -------------------------------- | ---------- | -------------------------------------------------------------------------------- |
| `absence_warn_seconds`           | 1200       | Segundos sin detección estable de una familia esperada antes de abrir precaución |
| `absence_resolve_stable_seconds` | 10         | Segundos de re-detección estable para auto-resolver                              |
| `detection_window_seconds`       | 5          | Ventana del panel live                                                           |
| `conf_display_threshold`         | 0.70       | Umbral visual (el val del train puede ser distinto)                              |


**Cuando se cumple ausencia**  

1. Backend **INSERT** `discrepancy` (`resolved = false`, descripción tipo “Absence > Ns”, `family_id`, expected/detected, `origin_event_id` si hay).
2. Encola GIF `reason = discrepancy`.
3. UI puede mostrar chip “Precaución: [instrumento] ausente”.

**Cuando vuelve la detección estable**  

1. Backend **UPDATE** `discrepancy` → `resolved = true`, `resolved_at = now()`, reason/nota de auto-resolve “re-detected”.
2. **INSERT** `count_event` anotando la resolución.
3. La precaución **no** escala sola a SPD si quedó resuelta antes del cierre.

**Motivo:** dejar rastro de “estuvo fuera X tiempo” sin obligar a SPD si el modelo ya recuperó la detección durante la cirugía.

### Configuración adicional live

- `frame_stride` (1 de cada N frames).  
- `max_det` del modelo.  
- Modelo activo (`yolo_model.active = true`).

### Salida

**Finish counting** → RF-OP-05. WSS se cierra o se deja en idle sin `frame.submit`.

### Errores UI

- Pérdida de WSS: banner “Visión desconectada — puede continuar en modo manual al validar”; no borrar expected.  
- Modelo caído: mismo banner; operador sigue hacia validación humana.  
- Si por error de datos `capture_mode ≠ vision`: la UI **no** debe abrir esta pantalla; redirigir a RF-OP-04M.

---

## RF-OP-04M · Progreso manual sin captura (sin acuerdo de aviso)

### Actor

`station_operator`  
Sesión en `in_progress` **y** `capture_mode = manual_no_privacy`.

### Cómo se llega

1. Tras **Start Session** cuando no existía `session_processing_agreement` al momento del Start.
2. **Continue** desde RF-OP-01 mientras la sesión sigue en este modo y aún no se envió el cierre de cantidades.

### Qué muestra la interfaz

**Encabezado**  
Session ID, Procedure, Patient, Physician, OR, Kit, Station, badge **In Progress**, fase actual (si aplica), badge fijo ámbar **Sin aviso · Sin cámara**.

**Banner permanente **  
*“Esta sesión no tiene aviso de privacidad confirmado. No hay captura de video ni detección por IA. Confirmaste el instrumental al inicio; al cerrar reportarás las cantidades presentes. Si reportas menos de lo esperado en alguna familia, se abrirá una discrepancia.”*

**Cuerpo — Expected inventory (solo referencia)**  
Tabla de solo lectura:


| Columna    | Contenido                        |
| ---------- | -------------------------------- |
| Instrument | Nombre de `instrument_family`    |
| Expected   | `expected_quantity` congelado    |
| Reported   | Vacío o “—” hasta el cierre      |
| Status     | “Pendiente de reporte al cierre” |


Totales: Total Expected. **No** hay columna Detected/IA.

**Qué NO aparece**

- Panel de video / overhead capture  
- Botones de IA, confidence, Match/Missing por detección  
- Controles que disparen WSS  
- “Finish counting” hacia resumen de IA (RF-OP-05)

**Controles permitidos**

- **Change phase** (opcional; mismo UPDATE SQL de fase que RF-OP-04 pero **sin** encolar evidencia de cámara ni GIF).  
- **Proceed to quantity report / Close counting** → RF-OP-06M.

### Qué hace el usuario

Mantiene la sesión “en progreso” mientras el caso clínico lo requiera (sin cámara). Cuando corresponde cerrar el conteo, pasa al reporte de cantidades.

### Qué ocurre en el sistema mientras permanece en esta vista

1. Backend solo atiende HTTPS (cambio de fase, heartbeat opcional, lectura de expected).
2. **Prohibido:** `frame.submit`, bind WSS, jobs de evidence-worker por visión, INSERT Mongo `checkpoint_frame` de charola.
3. Cambio de fase (si se usa): **UPDATE** `work_session.current_phase_id` + **INSERT** `count_event` `phase_change` con `payload.capture_mode = manual_no_privacy`. Sin cola de evidencia visual.
4. La sesión permanece en status `in_progress` hasta RF-OP-06M.

**Motivo:** el operador ya confirmó el set físico (checkbox de RF-OP-03); falta el reporte numérico humano porque no hay modelo que sugiera cantidades.

### Configuración

- Textos i18n del banner y del badge.  
- `change_phase_allowed_without_privacy` = true|false (default **true**).

### Salida

**Proceed to quantity report** → RF-OP-06M.

### Errores UI

- Intento de abrir cámara: bloquear en cliente y rechazar en Backend si llega un frame.  
- Si SPD agregó el acuerdo **después** del Start: esta pantalla **sigue** siendo la correcta (modo ya congelado); mostrar nota informativa “El aviso se registró tarde; esta sesión ya corre en modo manual”.

---

## RF-OP-05 · Resumen final de detección (modelo ya no alimenta live)

### Actor

`station_operator`  
Solo sesiones con `capture_mode = vision`. Las sesiones `manual_no_privacy` **no** pasan por esta pantalla.

### Cómo se llega

Al salir del live (Finish counting). En este punto el loop de frames **terminó**.

### Qué muestra la interfaz

- Encabezado de sesión (igual que live) + status In Progress.  
- Izquierda: **última captura** de charola (versión sin cajas y/o con cajas del último resultado).  
- Derecha: tabla por instrumento del expected:


| Columna        | Contenido                                                                              |
| -------------- | -------------------------------------------------------------------------------------- |
| Instrument     | Familia                                                                                |
| Expected       | Congelado                                                                              |
| AI Detected    | Del snapshot/cierre (no ventana 5 s)                                                   |
| Difference     | Detected − Expected                                                                    |
| Avg confidence | Promedio de confidences de **todas las cajas** de esa familia en el snapshot de cierre |
| Result         | Match / Missing / Extra                                                                |


- Totales Expected vs AI Detected.  
- Bloque “AI Analysis Details”: Vision model name, `yolo_model.version_tag`, confidence threshold usado, timestamp de análisis.  
- Disclaimer: sugerencia; requiere validación humana.  
- Botón **Proceed to Validation**.

### Qué ocurre en el sistema

1. Al entrar a esta pantalla (o al Finish): Backend **INSERT** `count_event`(s) de cierre de detección (`event_type` ej. `detection_close`) por familia con expected/detected del snapshot.
2. Encola evidencia `reason` alineado a cierre de visión (`close` o equivalente de catálogo de checkpoints).
3. Evidence-worker guarda GIF/JPEG final.
4. Lectura de `yolo_model` para el bloque de metadata.
5. **No** se cambia aún a `awaiting_spd_review` (falta validación humana).

### Salida

RF-OP-06 Validación.

---

## RF-OP-06 · Validación humana, correcciones y salida del operador

### Actor

`station_operator`  
Solo tras RF-OP-05 (`capture_mode = vision`).

### Cómo se llega

**Proceed to Validation** desde RF-OP-05.

### Qué muestra la interfaz

Tabla:


| Columna         | Contenido                                               |
| --------------- | ------------------------------------------------------- |
| Instrument      | Familia                                                 |
| Expected        | Congelado                                               |
| AI Detected     | Valor de IA **bloqueado** (candado visual; no editable) |
| Validated count | Control − / + ; inicia en AI Detected                   |
| Difference      | Validated − Expected                                    |
| Status          | Confirmed (match) / Pending / Corrected                 |


Al seleccionar una fila con diferencia ≠ 0 o al corregir:

- Original AI quantity (locked)  
- Corrected quantity  
- **Reason** (dropdown de catálogo + opción **Other**)  
- Notes (texto; obligatorio si Reason = Other)  
- Pie: “Corrected by: [nombre] | Timestamp: [ahora]”

Botones:  

- Back to AI Results → RF-OP-05  
- **Submit Validation**

### Qué hace el usuario

Ajusta cantidades validadas, documenta motivos, envía.

### Qué ocurre en el sistema al Submit Validation

1. Backend valida sesión `in_progress` y usuario dueño.
2. Por cada familia:
  - Si Validated == AI Detected y == Expected: confírmase match (evento opcional).  
  - Si Validated ≠ AI Detected: **INSERT** `count_event` tipo `manual_count` con detected/validated en columnas o payload; **INSERT** `human_correction` (`justification` = motivo + notas, `user_id`, `count_event_id`).  
  - Si Validated ≠ Expected: asegura `discrepancy` abierta o actualiza cantidades; si el usuario “cerró” la diferencia en sala, **UPDATE** `discrepancy.resolved = true` con `reason_id` adecuado.
3. Decisión de estado final de sesión:
  - **Faltante** (alguna familia con Validated < Expected, `resolved = false`) → **UPDATE** `work_session.status` = `or_hold` (no `correction_required`). **INSERT** `count_event` tipo `or_hold_opened`. Notificación in-app a usuarios `or_charge`. El operador **no termina**: pasa a RF-OP-07. `ended_at` **aún no** se llena.  
  - **Sin faltantes** (match o solo extras documentados) → **UPDATE** status = `awaiting_spd_review`, `ended_at` = now() (fin del trabajo de estación; SPD confirma cierre). Extra: se documenta (`discrepancy` y/o evento); **no** dispara hold ni cola SPD de corrección.
4. `closed_by_user_id` del operador **no** implica `closed` final; el cierre administrativo es SPD (RF-SP-05) **después** de `awaiting_spd_review` o, si hubo hold, **después** de RF-OP-07 → `correction_required`.
5. INSERT `access_audit` de submit validation.

**Motivo del hold:** un faltante humano se investiga **en sala** (evidencias, comentarios, resolución sugerida) antes de que SPD reciba el caso. SPD no entra a `or_hold`.

### Configuración

- Catálogo de motivos de corrección (UI) mapeado a `cat_discrepancy_reason` o lista paralela + `other`.  
- Si `other`, notes mínimas N caracteres (default 10).

### Salida

| Resultado | Pantalla siguiente |
| --- | --- |
| Hay faltante → `or_hold` | **RF-OP-07** vista hold (el operador **no** termina) |
| Sin faltante → `awaiting_spd_review` | **Reporte final** (solo lectura): totales, extras si los hay, evidencias, **Back to sessions** → RF-OP-01 |

Si no hay hold, el operador **termina aquí** su flujo.

### Errores UI

- Submit con reason Other sin notes: bloquear.  
- Submit parcial: no permitir; o definir “guardar borrador” (v1: no hay borrador; Submit es atómico).

---

## RF-OP-06M · Reporte manual de cantidades y cierre (sin IA)

### Actor

`station_operator`  
Sesión `in_progress` con `capture_mode = manual_no_privacy`.

### Cómo se llega

**Proceed to quantity report / Close counting** desde RF-OP-04M.

### Qué muestra la interfaz

Encabezado de sesión + badge **Sin aviso · Sin cámara**.

Tabla editable (una fila por `expected_inventory`):


| Columna           | Contenido                                                                         |
| ----------------- | --------------------------------------------------------------------------------- |
| Instrument        | Familia                                                                           |
| Expected          | Congelado (solo lectura)                                                          |
| Reported quantity | Control − / + ; valor inicial = Expected (el operador ajusta lo real)             |
| Difference        | Reported − Expected (**v1** misma convención numérica: reported − expected)       |
| Status            | Match si Diff = 0; Shortfall si Reported < Expected; Extra si Reported > Expected |


Al marcar Shortfall o Extra (o al cambiar cantidad):

- **Reason** (dropdown + **Other**) — obligatorio si Diff ≠ 0  
- Notes — obligatorio si Reason = Other  
- Pie: “Reported by: [nombre] | Timestamp: [ahora]”

Botones:

- Back → RF-OP-04M (sin guardar cierre)  
- **Submit report and close counting**

Texto fijo bajo la tabla:  
*“Si reportas una cantidad menor a la esperada, la sesión entra en OR hold: documentas evidencias y una resolución sugerida antes de enviarla a SPD. Los extras se documentan y no abren hold. Si todas coinciden (o solo hay extras), irá a Awaiting Review.”*

### Qué hace el usuario

Ingresa las cantidades físicas presentes, documenta diferencias, envía y cierra su parte del proceso.

### Qué ocurre en el sistema al Submit report (orden estricto, transacción SQL)

1. Backend valida: sesión `in_progress`, `user_id` dueño, `capture_mode = manual_no_privacy`, y que **todas** las familias del expected tengan cantidad reportada (entero ≥ 0).
2. Por cada familia:
  - **INSERT** `count_event` tipo `manual_count` (o `manual_close`):  
    - `expected_quantity` = congelado  
    - `detected_quantity` = **NULL** o 0 con nota en `payload` de que no hubo IA (`"ai": false`, `"capture_mode": "manual_no_privacy"`)  
    - cantidad reportada en columna de validado o en `payload.reported_quantity` / `validated_quantity` según columnas tipadas del esquema  
    - `user_id`, `occurred_at`, `session_id`
  - Si Reported ≠ Expected: **INSERT** `human_correction` con `justification` = reason + notes.
  - **Regla de discrepancia (contrato v1):**  
    - Si **Reported < Expected** → **INSERT** `discrepancy` con `resolved = false`, `family_id`, cantidades expected/detected_or_reported, motivo ligado al reason del operador. **No** se auto-resuelve.  
    - Si Reported = Expected → no se abre discrepancia por esa familia.  
    - Si Reported > Expected → **INSERT** `discrepancy` (o evento) de Extra **documentado**; **no** dispara `or_hold`. Si `extra_auto_ack_on_manual_close = true`, se marca resuelta; default **false** = queda abierta como dato, sin hold ni cola SPD de corrección.
3. Decisión de status de sesión (igual espíritu que RF-OP-06):
  - Si **algún** Reported < Expected → **UPDATE** `work_session.status` = `or_hold`. **INSERT** `count_event` `or_hold_opened`. Notif in-app a `or_charge`. `ended_at` **no** se llena. El operador pasa a RF-OP-07.  
  - Si **cero** faltantes → **UPDATE** status = `awaiting_spd_review`, `ended_at` = now().
4. **INSERT** `access_audit` acción `session.manual_close`.
5. **No** WSS, **No** evidence-worker, **No** Mongo de frames.

**Motivo:** sin aviso no hay evidencia visual de cámara; el hecho auditable es el reporte humano. El hold de faltante igual ocurre (RF-OP-07) para que sala documente la búsqueda antes de SPD.

### Configuración

- Valor inicial del spinner: `expected` (default) o `0`.  
- `extra_auto_ack_on_manual_close` (default false).  
- Motivos de shortfall/extra (mismo catálogo que correcciones).  
- Notes mínimas si Other (default 10).

### Salida

| Resultado | Pantalla siguiente |
| --- | --- |
| Hay faltante → `or_hold` | **RF-OP-07** (el operador **no** termina) |
| Sin faltante → `awaiting_spd_review` | **Reporte final** (solo lectura): expected vs reported, extras, aviso de que no hubo captura/IA, **Back to sessions** → RF-OP-01 |

Si no hay hold, el operador **termina aquí**. El cierre administrativo final sigue siendo SPD (RF-SP-05 / RF-SP-06) **después** de `awaiting_spd_review` o de la liberación OP-07.

### Errores UI

- Falta cantidad en alguna fila: bloquear Submit.  
- Shortfall/Extra sin reason: bloquear.  
- Other sin notes: bloquear.  
- Submit atómico: todo o nada.

---

## RF-OP-07 · Vista hold de sala (evidencias, comentarios, liberación a SPD)

### Actor

`station_operator` (dueño de la sesión). `or_charge` puede **leer y comentar** la misma ficha desde la App (RF-AP-03); **no** sustituye el envío del operador.

### Cómo se llega

Automático tras RF-OP-06 / RF-OP-06M cuando el status queda `or_hold`. También **Continue** desde RF-OP-01 si la sesión ya está en hold.

### Qué muestra la interfaz

- Banner **OR hold · faltante confirmado**. SPD **no** aparece como cola todavía.
- Familias en falta (expected vs validated/reported).
- Evidencias notables: GIFs/checkpoints de la sesión (Mongo + URL firmada). En `manual_no_privacy` se indica que no hay cámara; el relato es textual.
- Timeline `count_event` (incluido `or_hold_opened` y comentarios).
- Campo **Comentario** (hilo OP + SdP).
- Campo **Resolución sugerida** (texto obligatorio para liberar): encontrado / no encontrado / se sigue buscando / otra.
- Botones: **Guardar comentario**, **Enviar a SPD** (habilitado solo con resolución sugerida). No hay “Confirm and close” de ciclo.

### Qué hace el usuario

Revisa evidencias, escribe comentarios, redacta la resolución sugerida, pulsa **Enviar a SPD**.

### Qué ocurre en el sistema

**Comentar:** INSERT `count_event` tipo `or_hold_comment` (`user_id`, `payload` `{text, discrepancy_id?}`).

**Enviar a SPD** (transacción):

1. Revalida status = `or_hold` y usuario dueño.
2. **UPDATE** cada `discrepancy` de faltante abierta: `suggested_resolution` = texto, `or_released_at` = now(), `or_released_by_user_id` = operador.
3. **INSERT** `count_event` tipo `or_hold_released`.
4. **UPDATE** `work_session.status` = `correction_required`, `ended_at` = now() (fin del trabajo de estación; entra a cola SPD).
5. `access_audit` `session.or_hold.release`.
6. **No** reabre WSS. **No** cierra ciclo de instrumento.

### Almacenes

PostgreSQL write. Mongo/storage solo lectura de evidencias ya existentes.

### Configuración

Longitud mínima de resolución sugerida (default 10). Catálogo libre de texto en v1 (no hay `cat_or_resolution`).

### Salida

Reporte final de estación: status `correction_required`, texto enviado, **Back to sessions**. El operador **termina** aquí. SPD toma el caso en RF-SP-06.

### Errores UI

- Enviar sin resolución sugerida: bloquear.
- Status ya no `or_hold`: mensaje y volver a lista.
- SdP no puede pulsar Enviar a SPD en v1 (solo OP dueño).

---

# PARTE B — SUPERVISOR SPD

El supervisor inicia sesión en el **portal web** con rol `spd_supervisor`. Su home es el **dashboard**. Desde el menú accede a sesiones, kits, métricas y audit de configuración.

---

## RF-SP-01 · Dashboard SPD

### Actor

`spd_supervisor`

### Cómo se llega

Login portal → home Dashboard.

### Qué muestra la interfaz

Tarjetas / widgets con números actuales, por ejemplo:

- Sesiones `in_progress`  
- Sesiones `in_progress` en modo `manual_no_privacy` (opcional, contador aparte)  
- Sesiones `scheduled` **sin** `session_processing_agreement` (aviso pendiente)  
- Sesiones `awaiting_spd_review` (cierre limpio / solo extras)  
- Sesiones `correction_required` (**solo post-liberación** OR; **no** incluir `or_hold`)  
- Discrepancias abiertas **ya liberadas** a SPD (`or_released_at` NOT NULL)  
- (Opcional) Instrumentos en estados dirty / sterilizing

Lista corta “Pending review” **solo** `awaiting_spd_review` y `correction_required`, con atajos a RF-SP-05 / RF-SP-06. Las sesiones `or_hold` **no** aparecen aquí (viven en App / RF-OP-07).

### Qué ocurre en el sistema

`GET` dashboard → Backend **SELECT** agregados SQL sobre `work_session` + `discrepancy` (+ `instrument` por ciclo). Solo lectura.

### Configuración

Umbrales de color (p. ej. >N correcciones = rojo).

### Salida

Navegación a otras RF-SP-*.

---

## RF-SP-02 · Programar sesión (origen de lo que ve el operador), kit ± y aviso de privacidad

### Actor

`spd_supervisor`

### Cómo se llega

Menú **Sessions** → **New session** / editar sesión `scheduled` (también desde detalle si aún no inició).

### Qué muestra la interfaz

Formulario editable (a diferencia del operador):

- Operador asignado (select de usuarios `station_operator`)  
- Procedimiento / tipo  
- Paciente(s), médico(s) y roles quirúrgicos  
- Sala OR  
- Estación de captura prevista  
- Kit (default del procedimiento o elección)  
- Fecha/hora programada  
- Fase inicial

**Editor de inventario esperado (kit ±)**  

1. Al elegir kit, se cargan líneas de `kit_item` (familia + quantity).
2. Supervisor puede:
  - **Agregar** familia desde catálogo `instrument_family` (buscador) con cantidad ≥ 1  
  - **Quitar** una línea  
  - **Cambiar cantidad** de una línea
3. Tras cada cambio, el Backend recalcula semáforo de stock (ver configuración abajo) y la UI muestra OK / WARN / BLOCK con texto claro.
4. Guardar programación solo si no hay BLOCK.

**Sección obligatoria en UI · Aviso de privacidad para el uso del sistema**

Esta sección aparece en la misma pantalla de programar/editar sesión `scheduled`. El supervisor elige **una** de estas dos vías (ambas terminan en las mismas tablas):

**Vía A — Confirmar versión ya existente**

1. Selector de `privacy_notice_version` con `active = true` (muestra `version`, `effective_at`, enlace “Ver documento”).
2. Checkbox obligatorio al confirmar: *“Confirmo que el aviso de privacidad versión [X] aplica a esta sesión y a las finalidades marcadas.”*
3. Toggle opt-in: **Permitir uso de evidencia para mejora de modelo** → `purpose_model_improvement` (default OFF).
4. `purpose_quality_ops` no es editable: siempre ON (la BD lo exige).

**Vía B — Subir archivo nuevo de aviso**

1. Control **Upload** (PDF o HTML; MIME permitidos configurables).
2. Campo `version` (etiqueta única, p. ej. `2026.09.2`) y `effective_at`.
3. Al subir: el Backend guarda el archivo en object storage (MinIO/GCS), calcula SHA-256, y deja listo un **INSERT** `privacy_notice_version` (`document_uri`, `content_sha256`, `version`, `effective_at`, `active = true`).
4. Tras el alta, la UI selecciona automáticamente esa versión y pide el mismo checkbox de confirmación + toggle de model improvement (igual que Vía A).

**Estados visuales de la sección**


| Estado                              | Qué muestra la UI                                                             | ¿Se puede guardar la sesión?                                                   |
| ----------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Sin archivo/versión y sin confirmar | Badge ámbar “Aviso pendiente — la sesión arrancará en modo manual sin cámara” | **Sí** (programación completa; captura no habilitada hasta que exista acuerdo) |
| Versión elegida + checkbox marcado  | Badge verde “Aviso confirmado para esta sesión”                               | Sí                                                                             |
| Upload en progreso / hash fallido   | Spinner / error “No se pudo registrar el aviso”                               | Guardar sesión permitido **sin** acuerdo; no crear fila de acuerdo a medias    |


Botones de la sección (además del Guardar global):

- **Confirm privacy for this session** — crea/actualiza el acuerdo (ver SQL abajo). Disponible solo con versión válida + checkbox.  
- **Clear / Remove pending confirmation** — solo si aún no hay acuerdo guardado; limpia selección local.  
- Si ya existe acuerdo: muestra versión, `agreed_at`, quien confirmó (`agreed_by_user_id`), enlace al documento; botón **Replace agreement** solo mientras status = `scheduled` (borra/reemplaza la fila única por sesión).

**Nota:** el acuerdo **no** se puede “activar captura” a mitad de una sesión ya iniciada en `manual_no_privacy`. Si la sesión sigue `scheduled`, sí se puede agregar o reemplazar el acuerdo antes del Start del operador.

### Qué ocurre en el sistema al Guardar (programación clínica)

1. Backend valida rol.
2. **INSERT/UPDATE** `operation` (+ patients, physicians).
3. **INSERT/UPDATE** `work_session`: status `scheduled`, `user_id` = operador elegido, `kit_id`, `station_id`, `operation_id`, timestamps de programación.
4. Guarda el **borrador de esperado** (opción v1 formal):
  - Tabla de trabajo: se materializa ya en `expected_inventory` provisional **o** en estructura de borrador en `payload`/tabla auxiliar.  
  - **Contrato v1 elegido:** al guardar programación se guarda el set resuelto en filas preparadas ligadas a la sesión; en **Start Session (RF-OP-03)** se **re-congela** con el mismo contenido (idempotente) y se marca como definitivo. Las líneas del kit llevan trazabilidad `source = kit_snapshot`; las alteradas por SPD `source = 'manual'`.
5. Soft reservations opcionales a nivel familia (lógica app); hard `instrument_reservation` si el supervisor asigna seriales concretos en esta pantalla (si la UI de seriales existe; si no, solo familias en v1).
6. **INSERT** `access_audit`: “session.scheduled” / “session.updated”.
7. El Guardar de programación **no** crea por sí solo `session_processing_agreement` si el supervisor no pulsó Confirm privacy (o no marcó el checkbox en el mismo submit compuesto).

### Qué ocurre al Confirmar aviso (mismo submit o acción dedicada)

Orden en transacción PostgreSQL (+ storage si hubo upload):

**Si Vía B (archivo nuevo) y aún no existe la versión:**

1. Subir bytes a bucket de documentos legales (path p. ej. `privacy-notices/{version}/{sha256}.pdf`).
2. **INSERT** `privacy_notice_version` (`version`, `effective_at`, `document_uri`, `content_sha256`, `active = true`).
3. Si `version` ya existe (UK): error UI “Versión duplicada”; no sobrescribir.

**Siempre (Vía A o B tras tener** `privacy_notice_version_id`**):**

1. Validar que la sesión esté `scheduled` (o aún no haya `capture_mode` congelado).
2. **UPSERT** `session_processing_agreement` (UNIQUE `session_id`):
  - `session_id`  
  - `privacy_notice_version_id`  
  - `purpose_quality_ops = TRUE`  
  - `purpose_model_improvement` = valor del toggle  
  - `agreed_at = now()`  
  - `agreed_by_user_id` = supervisor actual
3. **INSERT** `access_audit`: action `privacy.session_agreement`, resource_type `work_session`, resource_id = sesión.
4. Respuesta UI: badge verde; la lista de sesiones y el Begin del operador mostrarán “Aviso OK · Visión”.

**Si el supervisor guarda la sesión sin confirmar aviso:** no hay fila en `session_processing_agreement`. Al Start del operador → `manual_no_privacy` (RF-OP-04M → RF-OP-06M).

### Semáforo de stock (cómo se calcula)

Para cada familia del esperado programado:

- `stock_total` = COUNT `instrument` activos de esa familia  
- `stock_available` = COUNT con `cycle_status` usable (`available`) y **sin** `instrument_reservation.active`  
- `requested` = cantidad del esperado


| Condición                                            | UI                                       | ¿Permite guardar?              |
| ---------------------------------------------------- | ---------------------------------------- | ------------------------------ |
| `requested` ≤ `stock_available`                      | OK                                       | Sí                             |
| `requested` ≤ `stock_total` pero > `stock_available` | WARN (turnaround / en uso / sterilizing) | Sí, con confirmación explícita |
| `requested` > `stock_total`                          | **BLOCK**                                | No                             |


Warn adicional de concurrencia: si dos sesiones `scheduled`/`in_progress` piden kits cuya suma por familia > stock, mostrar WARN de “no hay holgura para dos procesos simultáneos”.

### Configuración

- Lista de estados de ciclo que cuentan como “available”.  
- Política: WARN con confirmación vs BLOCK en solapes.  
- SLA minutos de reproceso (solo texto de WARN, no inventa disponibilidad).  
- MIME permitidos del aviso (`application/pdf`, `text/html`).  
- Tamaño máximo del archivo de aviso (default 10 MB).  
- Bucket/prefijo de storage para avisos (`privacy_notice_storage_prefix`).  
- Si `require_privacy_before_schedule = true`: el Guardar de sesión **falla** sin acuerdo (default v1 = **false**, para permitir el flujo manual).

### Salida

Sesión aparece en lista global SPD y en RF-OP-01 del operador asignado, con indicador de aviso OK o pendiente.

---

## RF-SP-03 · Lista global de sesiones

### Actor

`spd_supervisor`

### Cómo se llega

Menú Sessions.

### Qué muestra la interfaz

Igual estructura de columnas que el mockup de sesiones, pero **sin** filtro “solo mías”.

Filtros: Search, Procedures, Statuses, Date range, Privacy (Con aviso / Sin aviso).  
Paginación: “Showing X of Y”.

Columnas adicionales respecto al operador: **Privacy** (OK / Pendiente según EXISTS `session_processing_agreement`) y, si ya inició, **Capture mode** (`vision` / `manual_no_privacy`).

Acciones por fila según status, entre ellas:


| Botón                     | Status típico                                | Va a                      |
| ------------------------- | -------------------------------------------- | ------------------------- |
| View details              | cualquiera                                   | Detalle                   |
| Edit / Confirm privacy    | `scheduled` sin acuerdo                      | RF-SP-02 sección aviso    |
| Review count / Review     | `awaiting_spd_review`, `correction_required` (nunca `or_hold`) | RF-SP-05 / RF-SP-06       |
| Open live / View progress | `in_progress` + `vision`                     | Detalle + extras RF-SP-04 |
| View manual progress      | `in_progress` + `manual_no_privacy`          | Detalle (sin live cámara) |
| View OR hold (solo lectura) | `or_hold`                                 | Ficha; **sin** review SPD |
| Confirm close             | review limpia                                | RF-SP-05                  |


### Qué ocurre en el sistema

`GET` con filtros → **SELECT** SQL global (sin filtrar por `user_id` del supervisor). Solo lectura.

---

## RF-SP-04 · Extra de instrumental en sesión en progreso

### Actor

`spd_supervisor`

### Cómo se llega

Detalle de sesión `in_progress` → acción **Add instruments**.

### Qué muestra la interfaz

Selector de familia + cantidad; resultado de semáforo stock; Confirm / Cancel.  
Lista actual de expected con highlight de lo nuevo.

### Qué ocurre en el sistema al confirmar

1. Revalida semáforo (BLOCK impide).
2. **UPSERT** `expected_inventory`: si la familia existe, suma cantidad; si no, INSERT con `source = 'manual'`.
3. Opcional **INSERT** `instrument_reservation` si se elige pieza concreta.
4. **INSERT** `count_event` tipo ajuste de esperado (`payload`: before/after qty, actor SPD).
5. **INSERT** `access_audit`.
6. La estación, en el siguiente ciclo de UI o vía notificación HTTPS/SSE, refresca expected; el vision-worker afina con el nuevo set en los siguientes frames.

**Motivo:** complicaciones reales mid-case; el esperado debe crecer de forma auditable.

### Salida

Detalle actualizado; operador ve nuevas filas en el panel expected.

---

## RF-SP-05 · Review de recuperación y cierre administrativo

### Actor

`spd_supervisor`

### Cómo se llega

Cola “Awaiting Review” o botón Review en sesión `awaiting_spd_review` (sin discrepancias abiertas, o tras resolverlas en RF-SP-06).

### Qué muestra la interfaz

Resumen de sesión, totales validados, lista de evidencias (miniaturas/links a GIF), estado de ciclo de piezas asociadas, checkbox *“Material recovered / reprocessing started”* o flujo equivalente, botón **Confirm and close**.

### Qué ocurre en el sistema al confirmar cierre

1. **UPDATE** piezas: `instrument_cycle_event` + `cycle_status_id` → `dirty` o `sterilizing` según proceso SPD capturado en UI.
2. **UPDATE** `instrument_reservation`: `active = false`, `released_at = now()` cuando la política libere la pieza (si la liberación es solo al volver `available`, se documenta en el mismo flujo o en un paso posterior “Mark sterilized/available”).
3. **Contrato v1 de disponibilidad:** el instrumento **no** pasa a `available` en el Submit del operador; pasa a `available` cuando SPD registra fin de esterilización (puede ser esta misma pantalla en dos pasos o pantalla de ciclo).
4. **UPDATE** `work_session.status` = `closed`, `closed_by_user_id` = supervisor, `ended_at` ya venía del operador o se ajusta.
5. `access_audit` de cierre SPD.

### Salida

Sesión Closed; desaparece de colas pendientes.

---

## RF-SP-06 · Resolución de discrepancias (SPD) + evidencias + timeline

### Actor

`spd_supervisor`

### Cómo se llega

Sesión `correction_required` (**después** de RF-OP-07) o discrepancia con `or_released_at` NOT NULL desde dashboard/lista. **No** se entra a sesiones `or_hold`.

### Qué muestra la interfaz

Igual espíritu que validación del operador, **más**:

- **Relato OR (solo lectura):** `discrepancy.suggested_resolution`, `or_released_at`, `or_released_by_user_id`, y eventos `or_hold_comment` / `or_hold_released`. SPD **confirma** ese relato; **no** reabre el hilo de sala ni escribe `or_hold_comment`.  
- Visor de **GIFs/checkpoints** de la sesión (lista por reason + timestamp + fase).  
- **Historial de** `count_event`: timestamp, tipo, familia, expected, detected, fase (de `payload`).  
- Acciones por discrepancia:  
  - Marcar **resuelta** (motivo + notas; implica confirmar el relato OR)  
  - Marcar **pérdida de instrumento**

### Qué ocurre si “resuelta”

1. **UPDATE** `discrepancy` resolved.
2. `human_correction` o `count_event` manual de cierre.
3. Si ya no quedan abiertas → status puede pasar a `awaiting_spd_review` o directo a flujo RF-SP-05.

### Qué ocurre si “pérdida”

1. **UPDATE** `instrument.cycle_status_id` → código `lost` o `retired` (catálogo). **No DELETE.**
2. **INSERT** `instrument_cycle_event` con notas de pérdida, `operation_id`/`session_id`.
3. Cierra `discrepancy` con reason de pérdida.
4. Libera/anula reserva si existía.
5. El stock efectivo baja → futuros RF-SP-02/10 muestran WARN/BLOCK con números nuevos.
6. `access_audit`.

### Cómo se leen los GIF

1. Backend **SELECT**/query Mongo `checkpoint_frame` por `session_id`.
2. Resuelve `media_asset` → genera **URL firmada** temporal a MinIO/GCS.
3. El browser solo usa esa URL; sin credenciales de bucket.

### Salida

Discrepancia cerrada; sesión avanza según queden o no abiertas.

---

## RF-SP-07 · Audit log de configuración de sesiones

### Actor

`spd_supervisor`

### Cómo se llega

Menú **Audit** / Session configuration log.

### Qué muestra la interfaz

Tabla de eventos de **programación y edición** (crear sesión, cambiar kit, cambiar operador, cancelar, add extra), con: timestamp, actor, acción, session id, resumen.

**No** lista `count_event` (esos se ven dentro del detalle de sesión / RF-SP-06).

### Qué ocurre en el sistema

`GET` → **SELECT** `access_audit` filtrado por tipos de acción de configuración. Solo lectura.

---

## RF-SP-08 · Métricas

### Actor

`spd_supervisor`

### Cómo se llega

Menú Metrics.

### Qué muestra la interfaz

Gráficas/tablas configurables, como mínimo v1:

- Tiempo promedio de sesión por tipo de procedimiento  
- Conteo de sesiones por status en un rango de fechas  
- Tasa de sesiones con discrepancias  
- (Opcional) tiempo promedio entre fases

### Qué ocurre en el sistema

`GET` con rango de fechas → agregaciones SQL sobre `work_session`, `operation`, `discrepancy`, `count_event`. Solo lectura. Mongo solo si se cuenta número de evidencias.

### Configuración

Rango por defecto (7/30 días); procedimientos incluidos.

---

## RF-SP-09 · Configuración de kits de operación

### Actor

`spd_supervisor` (rol de catálogo SPD)

### Cómo se llega

Menú Kits.

### Qué muestra la interfaz

CRUD de kits: nombre, versión, líneas familia+qty, vínculo a tipos de procedimiento (`procedure_kit`, default).

Al guardar una línea/kit:

### Reglas de validación (contrato)


| Regla                                                                                                                 | Comportamiento UI                                                                         | Backend                      |
| --------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ---------------------------- |
| `quantity` de una familia > número de piezas físicas activas de esa familia                                           | **Hard block** + mensaje “No existen suficientes unidades en catálogo”                    | Rechaza SAVE                 |
| Tras pérdidas, la suma de qty de kits default usados en procedimientos concurrentes típicos deja holgura < 2 procesos | **WARN** no bloqueante al editar kit; **WARN/BLOCK** al programar segundo caso (RF-SP-02) | Cálculo de stock             |
| Eliminar familia de kit referenciada por sesiones `scheduled`/`in_progress`                                           | WARN + confirmación o block                                                               | Backend decide por FK lógica |


Escritura: **INSERT/UPDATE/DELETE** SQL en `kit`, `kit_item`, `procedure_kit`. `access_audit` en cambios.

---

# PARTE C — TABLA MAESTRA DE ESCRITURA POR EVENTO


| Evento de negocio             | PostgreSQL                                                                                       | Mongo                      | Storage            | Quién                     |
| ----------------------------- | ------------------------------------------------------------------------------------------------ | -------------------------- | ------------------ | ------------------------- |
| Login                         | — (Redis sesión)                                                                                 | —                          | —                  | Backend                   |
| Programar sesión              | `operation*`, `work_session`, borrador expected, `access_audit`                                  | —                          | —                  | Backend                   |
| Subir/confirmar aviso         | `privacy_notice_version` (±), `session_processing_agreement`, `access_audit`                     | —                          | PDF/HTML del aviso | Backend                   |
| Start Session (visión)        | `work_session` (+ `capture_mode`, **pin** `yolo_model_id`), `expected_inventory`, `count_event`, reservas | `checkpoint_frame` (async) | GIF start          | Backend + evidence-worker |
| Start Session (sin aviso)     | `work_session` (`capture_mode=manual_no_privacy`), `expected_inventory`, `count_event`, reservas | —                          | —                  | Backend                   |
| Frame live                    | —                                                                                                | —                          | —                  | WSS vision-worker         |
| Auto count persistido         | `count_event` (`payload.confidences` por caja)                                                   | —                          | —                  | Backend                   |
| Cambio fase (visión)          | `work_session`, `count_event`, ± `discrepancy`                                                   | checkpoint async           | GIF                | Backend + evidence-worker |
| Cambio fase (manual)          | `work_session`, `count_event`                                                                    | —                          | —                  | Backend                   |
| Precaución ausencia           | `discrepancy`, `count_event`                                                                     | checkpoint async           | GIF                | Backend + evidence-worker |
| Auto-resolve precaución       | `discrepancy`, `count_event`                                                                     | —                          | —                  | Backend                   |
| Cierre detección (visión)     | `count_event`                                                                                    | checkpoint async           | GIF cierre         | Backend + evidence-worker |
| Validación / corrección       | `count_event`, `human_correction`, `discrepancy`; status `or_hold` si faltante, si no `awaiting_spd_review` | — | — | Backend |
| Cierre manual sin IA (OP-06M) | Igual regla de faltante → `or_hold`; extra documentado sin hold | — | — | Backend |
| Hold OR (OP-07)               | `count_event` (`or_hold_comment` / `or_hold_released`), `discrepancy` (resolución sugerida, `or_released_*`), status `correction_required` | lectura checkpoints | — | Backend |
| Liberación vista App (SdP)    | Solo `or_hold_comment` (RF-AP-03); **no** cambia status | lectura | — | Backend |
| Extra mid-case SPD            | `expected_inventory`, `count_event`, ± reserva                                                   | —                          | —                  | Backend                   |
| Review / close SPD            | `work_session`, ciclo instrumento, reservas                                                      | —                          | —                  | Backend                   |
| Pérdida                       | `instrument`, `instrument_cycle_event`, `discrepancy`                                            | —                          | —                  | Backend                   |
| Kit CRUD                      | `kit`, `kit_item`, `procedure_kit`                                                               | —                          | —                  | Backend                   |


---

# PARTE D — PARÁMETROS DE CONFIGURACIÓN CENTRALIZADOS (v1)


| Clave                                  | Default                   | Usado en                        |
| -------------------------------------- | ------------------------- | ------------------------------- |
| `detection_window_seconds`             | 5                         | Panel live                      |
| `absence_warn_seconds`                 | 60                        | Precauciones                    |
| `absence_resolve_stable_seconds`       | 10                        | Auto-resolve                    |
| `conf_display_threshold`               | 0.70                      | UI resultados                   |
| `frame_stride`                         | 1 o 2                     | Estación → WSS                  |
| `evidence_gif_frames`                  | 8–16                      | Evidence-worker                 |
| `evidence_queue_name`                  | `pef-evidence-queue`      | Cola                            |
| `diff_convention`                      | `detected_minus_expected` | Todas las tablas Diff           |
| `session_id_display_prefix`            | `WS-`                     | Listas                          |
| `require_privacy_before_schedule`      | false                     | RF-SP-02 Guardar sesión         |
| `privacy_notice_max_mb`                | 10                        | Upload aviso                    |
| `privacy_notice_allowed_mime`          | pdf, html                 | Upload aviso                    |
| `privacy_notice_storage_prefix`        | `privacy-notices/`        | Object storage                  |
| `change_phase_allowed_without_privacy` | true                      | RF-OP-04M                       |
| `manual_report_qty_default`            | `expected`                | RF-OP-06M valor inicial spinner |
| `extra_auto_ack_on_manual_close`       | false                     | RF-OP-06M extras                |


Estos valores viven en configuración de aplicación (archivo/env/tabla `configuration`); cambiarlos no requiere alterar el significado de los RF.

---

# PARTE E — FUERA DE ALCANCE (explícito)

- Altas de usuarios, roles, reset de contraseñas, familias, catálogos de selección, modelos y App de piso: [rf_admin_app.md](rf_admin_app.md) (RF-AD-02, AD-05, AD-06, AD-08, RF-AP-*).
- Procesos ARCO ejecutados: RF-AD-11 es stub de lista. El **acuerdo de sesión** y el aviso al programar siguen en RF-SP-02; la **biblioteca** de avisos es RF-AD-04.
- Clientes máquina FHIR / `integration_client`: considerado en plan, **no diseñado**.
- Entrenamiento ML (solo se **consume** el `yolo_model` pineado en la sesión; Admin publica pesos en RF-AD-08). El opt-in `purpose_model_improvement` solo marca permiso de uso futuro de evidencia.
- Preferencias avanzadas multi-hospital. App: ve todas las salas (las del SdP primero); **notif solo** `user_operating_room`.

---

*Fin del contrato v1 (incluye rama sin aviso / captura manual). Cualquier cambio de comportamiento de pantallas o de escritura en BD debe actualizar el RF correspondiente en este archivo antes de implementarse como “hecho”.*