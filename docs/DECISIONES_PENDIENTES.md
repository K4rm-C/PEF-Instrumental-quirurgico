# PEF — Decisiones funcionales pendientes (MS-10, ampliado en MS-12)

Fecha: 2026-11-09. Cinco puntos que **no** se pueden cerrar con los RF ni con criterio técnico. Nada de lo descrito aquí está implementado más allá de lo que se indica como "comportamiento actual". El detalle técnico vive en las guías citadas; este documento solo reúne lo que hay que decidir.

| # | Decisión | Responsable funcional | Bloquea |
| :--- | :--- | :--- | :--- |
| D1 | Cierres tras una contingencia técnica | Jefatura de CEyE / SPD + Calidad | Semántica de "cierre seguro" en reportes |
| D2 | Resolución de un posible faltante/sobrante físico escalado | Jefatura de CEyE / SPD + Seguridad del paciente | Flujo de resolución del Supervisor |
| D3 | P6: avisos de privacidad por institución | Privacidad / Jurídico del hospital | Despliegue multiinstitución |
| D4 | Ciclo operativo de la charola | Jefatura de CEyE | Uso de charolas como objeto operativo |
| D5 | Mapeo FHIR y autorización de intercambio | Integraciones (hospital) + PEF Core + Privacidad | Integración con un HIS real |
| D6 | Uso clínico de los instrumentos equivalentes autorizados (MS-12) | Jefatura de CEyE / SPD + Calidad | Que una equivalencia cierre una discrepancia de clasificación |
| D7 | Firma de la validación humana (MS-12) | Calidad + Jurídico / Cumplimiento | Valor legal de la validación |
| D8 | Destino y periodicidad de los lotes de exportación (MS-12) | Integraciones (hospital) + Privacidad | Entrega de lotes a un sistema del hospital |

---

## D1. Cierres tras una contingencia técnica

- **Comportamiento actual:** el Operador puede informar cantidades sin YOLO solo con un motivo verificado o declarado (`worker_unavailable` contradicho → 409; `unreliable_detection` sin detección → 409). Nunca existe conteo final YOLO. El Supervisor confirma la revisión y, si el motivo no fue verificado, lo reconoce explícitamente. El cierre registra `close_basis = contingency_count` y Reportes lo separa como `approved_contingency`. `safe_close_confirmed` conserva el comportamiento anterior (puede ser `true`). Detalle: `GUIA_CONTEO_CONTINGENCIA.md` §1.
- **Riesgo:** un consumidor de datos que lea solo `safe_close_confirmed` podría tratar un cierre por contingencia como un cierre con conteo automático válido.
- **Opciones:**
  1. Mantener el estado actual: el cierre puede ser "seguro" y se distingue por `close_basis`.
  2. Cierre por contingencia siempre `safe_close_confirmed = false` (como el escalamiento).
  3. Exigir un segundo conteo (manual o recaptura posterior) antes de permitir el cierre.
- **Recomendación técnica:** opción 2 si Calidad considera que "seguro" implica verificación automática. Es un cambio pequeño y coherente con D2. Si no, mantener la opción 1 y documentarla en el manual de reportes.
- **Impacto código/BD:** opción 2: `confirm_spd_close` (portal) y `session_review.validate_close` (SessionService), sin migración. Opción 3: un nuevo paso en el flujo del Operador y del Supervisor (mayor).
- **Pruebas requeridas:** `test_ms09_count_safety.py` y `test_ms09_close.py` (valor de `safe_close_confirmed` por motivo), `e2e_ms06_reports.py` (contadores), E2E `e2e_ms09_count_safety.py`.

## D2. Resolución de un posible faltante o sobrante físico escalado

- **Comportamiento actual:** tras un final rechazado el Operador puede escalar. Se crea una discrepancia `shortage`/`surplus` ligada a la captura rechazada y la sesión pasa a `correction_required`. El Supervisor resuelve con los resultados existentes y cierra reconociendo que no hay conteo final válido (`escalation_acknowledged`). El cierre nunca es seguro (`close_basis = final_count_escalation`). Todo faltante físico registrado, aunque se marque recuperado, sigue siendo cierre no seguro (regla MS-03.1). Detalle: `GUIA_CONTEO_CONTINGENCIA.md` §2.
- **Riesgo:** no existe un camino para "el instrumento apareció, recapturar y cerrar normalmente". El Supervisor solo puede cerrar como no seguro. El texto del cierre local ("faltante no documentado como recuperado") no coincide exactamente con la regla vigente.
- **Opciones:**
  1. Mantener: escalamiento = cierre no seguro siempre.
  2. Permitir que el Supervisor **devuelva** la sesión al Operador (estado `in_progress`) para recapturar contra la misma referencia. Si el final coincide, cierre seguro normal y la discrepancia queda como resuelta/recuperada.
  3. Opción 2 más: si el material se declara perdido, alta automática del faltante en inventario (ya existe para `lost`).
- **Recomendación técnica:** opción 2. Reutiliza la regla del final sin modificarla (la recaptura sigue exigiendo final = referencia) y da una salida trazable. Ajustar el texto del cierre al decidirla.
- **Impacto código/BD:** nueva transición `correction_required → in_progress` en `rf_session` y SessionService, botón en `rf_details.html`, auditoría `session.returned_to_operator`. Sin migración (el estado ya existe en `cat_session_status`).
- **Pruebas requeridas:** recaptura tras devolución (15→14 escalado → devuelto → 15 aceptado), que no se pueda validar sin final, que la discrepancia quede resuelta, cierre seguro solo con final válido. Portal, SessionService y E2E real.

## D3. P6 — avisos de privacidad por institución

- **Comportamiento actual:** `privacy_notice_version` no tiene `institution_id`. Los avisos son globales (decisión D-2 de 2026-11-01). En la base de pruebas ningún aviso está compartido entre instituciones (`scripts/p6_privacy_usage.py`). Detalle: `GUIA_PRIVACIDAD_P6.md`.
- **Riesgo:** en un despliegue con varias instituciones, el Supervisor de una puede elegir el aviso publicado por otra.
- **Opciones:** (1) global + institucionales; (2) el institucional tiene prioridad; (3) solo institucionales; (a) mantener global y limitar el despliegue a una institución.
- **Recomendación técnica:** opción 1: compatible, reversible y sin cambio para el Operador. Propuesta SQL preparada y **no aplicada**: `docs/propuestas/p6_avisos_institucionales.sql`.
- **Impacto código/BD:** migración 017 (columna nula, índice, *trigger*), filtros en Admin (RF-AD-04) y en SessionService al programar (RF-SP-02).
- **Pruebas requeridas:** aislamiento en Admin y SessionService, acuerdos históricos sin cambios, migración idempotente, `check_migrations_concurrency.py` con 017.

## D4. Ciclo operativo de la charola

- **Comportamiento actual:** charola = instancia física de un kit con código, versión e historial (`surgical_tray`, `surgical_tray_history`). Es opcional al programar y no se pide en el formulario del portal (solo en la API). Hoy no se impide el uso simultáneo en dos sesiones. Existe un *trigger* de institución (016) y un bloqueo `FOR SHARE`. Nunca cambia la referencia del conteo. Detalle: `GUIA_CHAROLAS.md` §4 y §6.
- **Riesgo:** sin ciclo de estados la charola no sirve para trazabilidad de esterilización. La misma charola puede asignarse a dos sesiones activas.
- **Opciones:**
  1. Mantenerla como etiqueta opcional (estado actual).
  2. Estados mínimos (`disponible`, `en_uso`, `en_esterilizacion`, `fuera_de_servicio`), un uso activo a la vez y transición automática al iniciar o cerrar una sesión.
  3. Opción 2 más la vinculación de instrumentos individuales a la charola.
- **Recomendación técnica:** decidir primero si es obligatoria y si se permite el uso simultáneo, porque son las dos reglas que cambian datos. Los estados pueden venir después.
- **Impacto código/BD:** opción 2: migración (columna de estado y restricción de unicidad parcial sobre sesiones activas), CatalogService, SessionService (programar/iniciar/cerrar), formulario de programación del portal, sincronización (`sync_change` ya emite la charola).
- **Pruebas requeridas:** concurrencia (dos programaciones simultáneas con la misma charola), transiciones, sincronización, formulario del portal.

## D5. Mapeo FHIR y autorización de intercambio

- **Comportamiento actual:** proyección de lectura FHIR R4 (Organization, Location, Practitioner, Patient con alcance, Encounter). Las entregas son `Bundle` transaction con PUT, probadas solo con HAPI FHIR local. `INTEGRATION_ALLOWED_HOSTS` está vacío por defecto: nada sale. No se mapean conteos, discrepancias ni evidencias. Detalle: `GUIA_INTEGRACION_FHIR.md`.
- **Riesgo:** el mapeo (clase `UNK` del Encounter, roles como texto, sin narrativa) no está aprobado. Enviar datos de paciente a un HIS requiere base legal y convenio.
- **Opciones:**
  1. Solo lectura por clientes máquina (sin entregas).
  2. Entregas sin Patient (solo Encounter, Location y Practitioner).
  3. Entregas completas con autenticación SMART Backend Services o mTLS.
- **Recomendación técnica:** empezar por la opción 1 o la 2 con el HIS de pruebas del hospital y aprobar el mapeo con Integraciones antes de cualquier dato de paciente.
- **Impacto código/BD:** opción 3: autenticación saliente en el worker de IntegrationService, interfaz de Administración de destinos, retención de intentos. Sin cambios en el conteo.
- **Pruebas requeridas:** `$validate` contra los perfiles que exija el hospital, entrega idempotente, prueba de autorización (alcances del cliente), auditoría de cada lectura y entrega.

## D6. Uso clínico de los instrumentos equivalentes autorizados (MS-12)

- **Comportamiento actual:** el Supervisor SPD autoriza, por kit (versión activa), familias equivalentes a una familia del kit (`kit_item_equivalence`, CatalogService). Se registra quién y cuándo; la revocación conserva el historial. La equivalencia **no cambia** las cantidades esperadas, la referencia ni la regla del conteo final, y **no** cierra discrepancias: es información visible para el Supervisor (pantalla *Equivalencias de kit*) y por API para cualquier rol de la institución. Detalle: `GUIA_CATALOG.md` §MS-12.
- **Riesgo:** si el Operador encuentra una pieza equivalente en lugar de la original, hoy se registra como diferencia de clasificación y la resuelve el Supervisor como cualquier otra.
- **Opciones:**
  1. Solo informativa (estado actual).
  2. En la validación humana, permitir que el Operador declare "pieza equivalente autorizada" y que la discrepancia de clasificación se cree ya resuelta, con referencia a la autorización.
  3. Opción 2 más la cuenta de la equivalente dentro de la familia original en los reportes.
- **Recomendación técnica:** opción 1 hasta que CEyE defina si una equivalente sustituye a la original en el conteo; la opción 2 es la mínima que cambia datos.
- **Impacto código/BD:** opción 2: validación humana del portal, SessionService (resolución), reportes. Sin cambio en la regla del total.
- **Pruebas requeridas:** validación con equivalente autorizada/revocada, institución, reportes, auditoría.

## D7. Firma de la validación humana (MS-12)

- **Comportamiento actual:** al cerrar su parte, el Operador deja un **sello de integridad** SHA-256 del contenido validado (líneas, conteo final, hash del fotograma de evidencia) en el `manual_close`; SessionService lo recalcula (`GET /api/v1/sessions/{id}/validation-seal`: `intact` / `altered` / `not_sealed`). Es **evidencia de alteración**, no una firma electrónica: quien tenga escritura directa en la base podría recalcularlo.
- **Riesgo:** sin firma no hay no repudio ni valor legal de la validación.
- **Opciones:**
  1. Sello de integridad (estado actual).
  2. Re-autenticación del Operador (contraseña) al validar, registrada en la auditoría.
  3. Firma con clave del servidor (HMAC/asimétrica con custodia de claves) o firma electrónica avanzada del usuario.
- **Recomendación técnica:** decidir primero el requisito legal; la opción 2 no requiere custodia de claves; la 3 requiere gestión de claves (ver propuesta de firma asimétrica de AuthService).
- **Impacto código/BD:** opción 2: formulario de validación y AuthService; opción 3: servicio de firma, rotación de claves.
- **Pruebas requeridas:** verificación con clave rotada, re-autenticación fallida, auditoría.

## D8. Destino y periodicidad de los lotes de exportación (MS-12)

- **Comportamiento actual:** el administrador de TI (o un cliente máquina con el alcance `export.sessions.batch`) genera **lotes trazables** de sesiones cerradas en formato propio documentado (`pef-session-batch/1`, JSON o XML), sin datos de paciente ni de médicos; se guardan los bytes exactos con SHA-256, quién, cuándo y qué sesiones, y se auditan la creación y cada descarga. **Nada se envía** a ningún sistema.
- **Riesgo:** sin un destino acordado los lotes solo sirven como archivo descargable.
- **Opciones:** 1. Descarga manual (actual). 2. Lote periódico automático (diario) disponible para un cliente máquina. 3. Entrega a un sistema del hospital (SFTP/HTTP) con autenticación saliente.
- **Recomendación técnica:** opción 2 con el cliente máquina; la 3 depende de D5.
- **Impacto código/BD:** opción 2: planificador en el worker de IntegrationService; opción 3: destino y credenciales salientes.
- **Pruebas requeridas:** idempotencia por periodo, reintentos, auditoría, verificación de SHA-256 en el destino.
