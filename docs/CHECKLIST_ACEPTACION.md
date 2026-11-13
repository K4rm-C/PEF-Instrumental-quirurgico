# PEF — Lista de aceptación final (MS-10)

Fecha de la última corrida: 2026-11-09. Rama `microservices_phase2`. Entorno: **pila aislada de pruebas** (`pef_admin_test`), nunca la VM.

**Estados.** **aprobado**: la prueba se ejecutó y dio el resultado esperado, con evidencia citada. **fallido**: se ejecutó y no lo dio. **bloqueado**: no se puede ejecutar todavía (falta hardware, VM, proveedor o decisión). **pendiente**: aún no se ejecuta; incluye **toda prueba manual de aceptación**.

**Regla:** una prueba automatizada aprobada **no** aprueba la prueba manual correspondiente. La columna "Tipo" lo distingue: **A** = automatizada en el entorno aislado, **N** = navegador real automatizado (Playwright/Chromium), **M** = manual por el responsable funcional.

Evidencias: `docs/evidencias/MS-10/` (capturas y `results.md` de `scripts/browser_validate_flows.py`), `docs/evidencias/ADMIN-09/` (Administración), registros `.e2e/reg_ms10_*.log` (no versionados).

## 1. Administración

| # | Tipo | Acción | Esperado | Observado | Evidencia | Estado |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| AD-1 | N | `browser_validate_admin.py` RF-AD-01..10 (tablero, familias, procedimientos, avisos, usuarios, cámaras, modelos, auditoría, configuración) | Todas las comprobaciones pasan | 95 PASS · 0 FAIL · 0 BLOCKED | `docs/evidencias/ADMIN-09/` | aprobado |
| AD-2 | A | Suite `admin` (aislamiento por institución, roles, revocación) | 0 fallos | 222/222 | `.e2e/reg_ms10_admin.log` | aprobado |
| AD-3 | A | Matriz de roles `test_ms09_role_matrix.py` | Cada rol solo llega a su área | Incluida en la suite web | `.e2e/reg_ms10_web.log` | aprobado |
| AD-4 | M | Recorrido manual RF-AD-01..10 (`docs/ADMIN_GUIA_VALIDACION_MANUAL.md` §3) | Conforme a los RF | — | — | pendiente |
| AD-5 | M | Clientes máquina, cámaras y charolas por un Administrador real | Conforme | — | — | pendiente |
| AD-6 | — | RF-AD-11 | Fuera de alcance (no implementado por instrucción) | — | — | bloqueado |

## 2. Operador

| # | Tipo | Acción | Esperado | Observado | Evidencia | Estado |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| OP-1 | N | Inicio de sesión y sesiones asignadas | La sesión programada aparece | Aparece | `007_operator-sessions.jpg` | aprobado |
| OP-2 | N | Iniciar sesión (asistente + diálogo "Start") | Estado `in_progress` | `in_progress` | `008_operator-started.jpg` | aprobado |
| OP-3 | N | Elegir cámara de la estación | `work_session.camera_id` guardado | Guardado | `009_operator-camera.jpg` | aprobado |
| OP-4 | N | Subir video y registrar el conteo inicial (YOLO real) | Un `auto_count` `first_frame`; diferencia con el kit reconocida explícitamente | Registrado | `010_operator-initial-count.jpg` | aprobado |
| OP-5 | N | Final con diferencia física real | Rechazado (`close_blocked`), sin final | Rechazado | `011_operator-final-rejected.jpg` | aprobado |
| OP-6 | N | Recaptura con la charola como al inicio | Final = referencia, aceptado | Aceptado | `012_operator-validation.jpg` | aprobado |
| OP-7 | N | Validación que cambia el total | Rechazada ("solo corrige la clasificación") | Rechazada | `013_operator-validation-refused.jpg` | aprobado |
| OP-8 | N | Validación con el mismo total | Sesión a revisión del Supervisor | A revisión | `014_operator-sent-to-review.jpg` | aprobado |
| OP-9 | N | Historial | Pantalla disponible | Disponible | `015_operator-history.jpg` | aprobado |
| OP-10 | N | Escalar un posible faltante tras un final rechazado | `correction_required`, sin final YOLO | Correcto (tras corregir el defecto del formulario oculto) | `016_operator-escalation.jpg` | aprobado |
| OP-11 | N | Contingencia "worker no disponible" con el worker activo | 409 | 409 | `results.md` | aprobado |
| OP-12 | N | Contingencia declarada ("otro") | Registrada como `declared`, sin final YOLO | Correcto | `017_operator-contingency.jpg` | aprobado |
| OP-13 | A | E2E MS-03.1 y MS-09 (regla del final, escalamiento, contingencia con worker realmente detenido) | Todo pasa | MS-03.1 19/19 · MS-09 7/7 | `.e2e/reg_ms10_e2e_ms031_final_count.log`, `..._ms09_...` | aprobado |
| OP-14 | M | Operador real con cámara física y video del hospital | Conteos correctos | — | — | bloqueado (hardware y video real) |
| OP-15 | M | Aceptación del flujo por el responsable de CEyE | Conforme | — | — | pendiente |

## 3. Supervisor

| # | Tipo | Acción | Esperado | Observado | Evidencia | Estado |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| SP-1 | N | Tablero con datos reales | Sin datos de demostración | Correcto | `001_supervisor-dashboard.jpg` | aprobado |
| SP-2 | N | Programar sesión (asistente de 3 pasos + modal de privacidad) | Sesión creada con kit, estación, médico y aviso | Creada | `002_supervisor-session-scheduled.jpg` | aprobado |
| SP-3 | N | Catálogos de kits e instrumentos; charolas (lista y alta) | Datos reales; charola creada | Correcto | `003`–`006` | aprobado |
| SP-4 | N | Sesiones pendientes | Las tres sesiones aparecen | Aparecen | `supervisor-sessions` | aprobado |
| SP-5 | N | Evidencias reales (imágenes de EvidenceService) | ≥ 2 imágenes cargadas | Cargadas | `supervisor-evidence` | aprobado |
| SP-6 | N | Resolver discrepancias (radio, notas, Resolve, confirmación) | Ninguna abierta | Todas resueltas | `supervisor-review` | aprobado |
| SP-7 | N | Cierre: normal, escalado y por contingencia (solo las casillas que muestra la página) | `final_count`; `final_count_escalation` no seguro; `contingency_count` | Los tres `close_basis` correctos; escalado `safe_close_confirmed = false` | `supervisor-closed` | aprobado |
| SP-8 | N | Notificaciones, reportes, indicadores, descargas CSV/PDF, salud de servicios y estaciones, auditoría | Pantallas con datos reales; CSV con BOM, PDF válido | Correcto; CSV con BOM y PDF `%PDF` | capturas `supervisor-*` | aprobado |
| SP-9 | A | E2E MS-05 y MS-06 (estaciones, reportes, exportaciones auditadas) | Todo pasa | MS-05 14/14 · MS-06 16/16 | `.e2e/reg_ms10_e2e_ms05_*`, `..._ms06_...` | aprobado |
| SP-10 | M | Revisión de evidencias, cierre de contingencia y de escalamiento por un Supervisor real | Conforme | — | — | pendiente |

## 4. Microservicios

| # | Tipo | Acción | Esperado | Observado | Evidencia | Estado |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| MS-1 | A | `verify_architecture.py` (roles, emisor de tokens, puertos, migraciones) | 57/57 | 57/57 | salida del script | aprobado |
| MS-2 | A | Ejecutor de las 9 suites | 0 fallos | 1791/1791, 0 omitidas | `.e2e/reg_ms10_runner.log` | aprobado |
| MS-3 | A | Suites del portal (web) y AuthService | 0 fallos | web 598/598 · auth 199/199 | `.e2e/reg_ms10_web.log`, `..._auth.log` | aprobado |
| MS-4 | A | Smoke SQLite, smoke real e integración Admin–Supervisor | Todo pasa | SQLite OK · real 51/51 · integración 13/13 | `.e2e/reg_ms10_smoke_*` | aprobado |

## 5. Seguridad

| # | Tipo | Acción | Esperado | Observado | Evidencia | Estado |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| SE-1 | A | Gateway: rutas internas no publicadas, CSRF, roles, contratos JSON/XML | 0 violaciones, 0 huecos | 32/32; contratos 192/192, 0 huecos (3 rutas sin id de muestra, no ejercitadas) | `.e2e/reg_ms10_e2e_gateway.log` | aprobado |
| SE-2 | A | `preflight_production.py` sobre los ejemplos | Falla con marcadores `<...>` (incluye credenciales S3 del portal, MS-10) | Falla como se espera | salida del script | aprobado |
| SE-3 | A | Contingencia contradicha y escalamiento nunca seguro | Rechazo / cierre no seguro | Ver OP-11, SP-7 | — | aprobado |
| SE-4 | M | Revisión de secretos en Secret Manager, rotación JWT, TLS real | Conforme | — | — | bloqueado (VM y certificados) |

## 6. Bases de datos

| # | Tipo | Acción | Esperado | Observado | Evidencia | Estado |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| DB-1 | A | Base nueva: 001, 002, 003, 004–016 y segunda pasada idempotente | Sin errores; tablas y *triggers* | 10/10 | `.e2e/reg_ms10_concurrency.log` | aprobado |
| DB-2 | A | 5 arranques simultáneos de Monitoring, Notifications e Integration | Sin *deadlock* | 10/10 | igual | aprobado |
| DB-3 | A | Datos de pruebas restaurados tras los E2E (`e2e_restore_stock.py`) | Sin instrumentos perdidos de las pruebas | Ejecutado en cada E2E | registros | aprobado |
| DB-4 | M | Respaldo y restauración probados en la VM | Restauración completa | — | — | bloqueado (VM) |

## 7. Visión por computadora

| # | Tipo | Acción | Esperado | Observado | Evidencia | Estado |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| CV-1 | A | E2E MS-02: modelo fijado por sesión, SHA-256 | Todo pasa | 13/13 | `.e2e/reg_ms10_e2e_ms02_model_pin.log` | aprobado |
| CV-2 | N | Inferencia real con `best.pt` provisional en el navegador | Inicial y finales coherentes | Ver OP-4..OP-6 | — | aprobado |
| CV-3 | M | Modelo definitivo y su precisión con videos reales | Aprobado por el equipo de visión | — | — | bloqueado (modelo definitivo) |

## 8. WebSockets

| # | Tipo | Acción | Esperado | Observado | Evidencia | Estado |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| WS-1 | A | Ticket HMAC, Origin, corte al revocar (suites VisionWorker/portal, E2E MS-03) | Todo pasa | Incluido en MS-2/OP-13 | registros | aprobado |
| WS-2 | M | Vista previa WSS en la VM a través de nginx | Imagen en vivo; corte al revocar | — | — | bloqueado (VM) |

## 9. Integración externa

| # | Tipo | Acción | Esperado | Observado | Evidencia | Estado |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| IN-1 | A | E2E MS-07/08: sincronización, operaciones idempotentes, FHIR con HAPI de pruebas | Todo pasa | 25/25 (HAPI FHIR de pruebas) | `.e2e/reg_ms10_e2e_ms07_ms08.log` | aprobado |
| IN-2 | M | HIS real | Mapeo aprobado y entregas autorizadas | — | — | bloqueado (decisión D5, convenio) |
| IN-3 | M | Correo (SMTP) | Avisos entregados | — | — | bloqueado (proveedor) |
| IN-4 | M | Clientes Android/escritorio | Sincronizan | — | — | bloqueado (no existen) |

## 10. Infraestructura

| # | Tipo | Acción | Esperado | Observado | Evidencia | Estado |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| IF-1 | A | Unidades systemd y nginx renderizado (`nginx -t` en contenedor) | Sintaxis válida, todo en 127.0.0.1 | Válida (MS-09) | `GUIA_SEGURIDAD_INFRAESTRUCTURA.md` | aprobado |
| IF-2 | M | Inventario de la VM (`GUIA_DESPLIEGUE_GCP_CENTOS10.md` §1) | Tabla §2 completa | — | — | pendiente |
| IF-3 | M | Instalación o actualización en la VM, comprobaciones §6 | Todo activo | — | — | bloqueado (sin autorización de despliegue) |
| IF-4 | M | SELinux *enforcing*, firewall y reglas VPC | Solo 443/80 públicos | — | — | bloqueado (VM) |

## 11. MS-11: responsabilidades completadas y Docker Compose integral (2026-11-10)

| # | Tipo | Acción | Esperado | Observado | Evidencia | Estado |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| M11-1 | A | E2E de los tres roles sobre la pila Compose completa (Gateway TLS, YOLO real, HAPI de pruebas) | Pasos 0–19 y escenarios A–G | 31/31 | `.e2e/ms11_compose_e2e.json` | aprobado |
| M11-2 | A | Tracking: secuencias sintéticas + videos reales (`measure_tracking.py`) | IDs estables, recuperación tras oclusión, sin conteo | 16/16; recupera 2/2 objetos ocultos donde solo-IoU fragmenta | `GUIA_TRACKING.md` §4 | aprobado |
| M11-3 | M | Tracking con video real del hospital y cámara física | Comportamiento estable, limitaciones aceptadas | — | — | bloqueado (hardware y videos reales) |
| M11-4 | A | Cámara: configuración (ROI, resolución, FPS) en VisionWorker; fuente simulada marcada | `camera.simulated = true`, ROI recibido | Recibido | E2E Compose paso 4 | aprobado (simulación) |
| M11-5 | M | Cámara física en una estación | Vista previa y captura reales | — | — | bloqueado (hardware) |
| M11-6 | A | Reconexión de la vista previa tras reiniciar VisionWorker | El ticket vuelve a vincular la sesión | Correcto | E2E Compose F | aprobado |
| M11-7 | M | Reconexión observada en el navegador del Operador (red inestable) | Mensajes de reconexión; nunca tras una revocación | — | — | pendiente |
| M11-8 | A | Evaluaciones de modelos: registro inmutable, comparación sobre el mismo dataset | Rechazo de valores inválidos, 409 en duplicados, *trigger* de inmutabilidad | 13/13 | `tests/test_ms11_model_evaluations.py` | aprobado |
| M11-9 | M | Registro de las métricas reales del modelo definitivo | Valores medidos por el equipo de visión | — | — | bloqueado (modelo definitivo) |
| M11-10 | A | Notificación de escalamiento y alertas de componente | `final_count_escalated` al Supervisor; `service_alert` a TI (caída y recuperación) | Correcto | E2E Compose A y E | aprobado |
| M11-11 | A | `db-migrate`: base existente actualizada en una transacción; reaplicación con datos de tipos nuevos | Sin errores | Correcto (antes fallaba) | `check_migrations_concurrency.py` | aprobado |
| M11-12 | A | Gateway tras recrear un contenedor | Re-resuelve el nombre; sin 502 permanente | Correcto (antes: 502 hasta recargar nginx) | E2E Compose E | aprobado |
| M11-13 | A | Persistencia tras reiniciar PostgreSQL, Redis, MongoDB y Garage | Datos, objeto de evidencia y revocación intactos | Correcto | E2E Compose G | aprobado |
