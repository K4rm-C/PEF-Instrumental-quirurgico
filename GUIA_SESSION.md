# PEF — SessionService integrado con los flujos RF

SessionService (`apps/SessionService`, puerto 5004) asume las **operaciones de sesión del Supervisor** y autoriza el **cierre autónomo del Operador**. Captura y validación del Operador permanecen en BackendWebFlask (`services/rf_session.py`). El cierre real usa SessionService cuando está configurado. Ver [flujo de video y evidencias](GUIA_CAPTURA_VIDEO.md) y [requisitos actualizados](docs/REQUISITOS_VIDEO_CIERRE.md).

PostgreSQL sigue siendo la única fuente de verdad: ningún componente guarda una copia propia del estado operativo.

- Contrato: [DS09 §1.3.4](data/DataStandards/PEF-DS09-API-Exchange-Contracts.md), complemento v0.2.0.
- Documentación ejecutable: `http://127.0.0.1:5004/api/v1/sessions/docs`.

## 1. Quién hace qué

| Operación | Dueño | Cómo llega |
| :--- | :--- | :--- |
| Programar sesión (operación, paciente, equipo quirúrgico, inventario, aviso de privacidad opcional) | **SessionService** | RF Supervisor → `POST /api/v1/sessions/schedule` |
| Edición completa mientras está programada | **SessionService** | `PUT /api/v1/sessions/{id}` |
| Acuerdo de privacidad (Vía A) | **SessionService** | `PUT /api/v1/sessions/{id}/privacy-agreement` |
| Inventario esperado e incorporaciones confirmadas (`instruments_added`) | **SessionService** | `PUT /api/v1/sessions/{id}/expected-inventory` (`physically_added`) |
| Consultar sesiones, detalle, historial, cola de discrepancias | **SessionService** | `GET …?expand=detail`, `GET /sessions/discrepancies` |
| Resolver discrepancias | **SessionService** | `POST /sessions/{id}/discrepancies/{d}/resolve` |
| Cierre seguro SPD (casos escalados) | **SessionService** | `POST /sessions/{id}/close` |
| Cierre por el Operador de una sesión conciliada sin discrepancias | **SessionService** | RF Operador → `POST /sessions/{id}/operator-close` (`station_operator`) |
| Iniciar sesión, conteo inicial (primer cuadro) y final (último cuadro) del mismo video YOLO, validación humana, reporte de contingencia, cambio de fase | **Monolito (Operador)** | `rf_session.py` sobre PostgreSQL; usa solo AuthService y VisionWorker |
| Reportes e indicadores del Supervisor, bitácora | Monolito (lecturas locales) | Fuera del alcance de esta etapa |
| Opciones de formularios (operadores, salas, kits, stock, médicos) | Monolito (lecturas locales) | Son datos de Catálogo y Configuración, no de sesión |

`controllers/supervisor_sessions.py` es el único punto de entrada de las pantallas del Supervisor:
- Con `SESSION_SERVICE_URL` configurado, cada operación va a SessionService (`services/session_remote.py`) y nunca también al código local. No hay respaldo silencioso: si SessionService no responde, la pantalla muestra "El servicio de sesiones no está disponible temporalmente" con el estado de los servicios (503), y no se escribe nada.
- Sin `SESSION_SERVICE_URL`, se usa la implementación local (modo de desarrollo).

**Presentación idéntica.** Las pantallas se construyen desde una **instantánea de sesión** con la misma forma en las dos fuentes:
- remota: SessionService `expand=detail`;
- local: `services/session_snapshot.py`, la que usa el Operador.

`controllers/view_data.py` convierte la instantánea en las filas RF, con las mismas etiquetas, grupos y traducción. El E2E comprueba que ambas fuentes dan instantáneas idénticas en sesiones reales. Antes de refactorizar se comparó la salida en 79 vistas reales: 0 diferencias.

## 2. Acoplamiento por PostgreSQL compartido y cómo se controla

El Operador (en el monolito) y el Supervisor (en SessionService) escriben filas de la misma sesión: `work_session`, `expected_inventory`, `count_event` y `discrepancy`. Es un acoplamiento real a nivel de datos y se controla así:

1. **Un solo punto de serialización.** Toda transacción que cambia una sesión bloquea primero su fila `work_session` con `SELECT … FOR UPDATE`, en ambos lados:
   - Operador: `start_session`, `register_initial_count`, `confirm_final_count`, `submit_manual_close` y el cierre por el Operador;
   - Supervisor: inventario, resolución, cierre, edición, privacidad.

   La segunda transacción espera y luego evalúa el estado que la primera dejó confirmado. Ejemplos:
   - Un reporte del Operador después de una incorporación usa el inventario nuevo.
   - Una edición después del reporte responde `409 inventory_locked`.
2. **Reglas de estado en cada escritura.** Cada operación verifica el estado después de tomar el bloqueo. Las transiciones inválidas responden 409 con `details.status_code` y `details.reason`:
   - edición y privacidad: solo en `scheduled`;
   - inventario: en `scheduled` o `in_progress`;
   - resolución: en `correction_required` o `awaiting_spd_review`;
   - cierre SPD: en `awaiting_spd_review`, sin discrepancias abiertas;
   - cierre por el Operador: en `awaiting_spd_review` y solo si se cumplen todas las condiciones de la sección 2.1.
3. **Sin escrituras duplicadas.** Cada operación tiene un solo dueño (tabla anterior), y la fachada nunca ejecuta las dos implementaciones.
4. **Sin copias del estado.** El portal no guarda en caché lo que lee de SessionService. La única caché local es la foto del conteo final YOLO (`_VISION_SNAPSHOTS`, en memoria del portal, del lado del Operador):
   - se borra cuando el Supervisor confirma una incorporación;
   - aun si no se borrara, `vision_final_state` recalcula la validez desde PostgreSQL al validar.
5. **Idempotencia.**
   - Reenviar el mismo inventario no suma otra incorporación.
   - Un segundo cierre o una segunda resolución responde 409, sin nuevos eventos.
   - Dos cierres o dos resoluciones concurrentes dan 200 + 409.
   - El cierre por el Operador repetido (doble clic o dos peticiones concurrentes del mismo Operador) responde 200 con la sesión ya cerrada, sin un segundo `session_close`.
   - Registrar el conteo inicial o confirmar el final dos veces devuelve el conteo ya registrado (mismo video / misma ejecución).
6. **Cambios que exigen coordinación.** Cualquier cambio de esquema de estas tablas, o del significado de `count_event.payload`, afecta a los dos componentes. La instantánea compartida y la prueba de paridad del E2E detectan una divergencia de lectura.

### 2.1 Cierre por el Operador (sin discrepancias)

`POST /api/v1/sessions/{id}/operator-close` (`services/session_operator_close.py`). SessionService vuelve a evaluar todo bajo el bloqueo de la fila; la pantalla nunca se toma como prueba. Solo cierra si se cumple **todo**:

- sesión en `awaiting_spd_review`, modo `vision`, asignada al Operador que llama (otra sesión u otra institución → 404);
- el Operador terminó por validación humana YOLO (`manual_close.operator_close_path = vision_validation`): sin conteo de contingencia, sin instrumentos ajenos al kit confirmados y sin diferencia YOLO arrastrada (`known_final_mismatch`);
- ninguna discrepancia (abierta o resuelta) y ningún conteo final rechazado (`close_blocked`);
- conteo inicial del **primer cuadro** y conteo final del **último cuadro** del **mismo video** (SHA-256), el final posterior a la última incorporación confirmada e igual a la referencia (inicial + incorporaciones); ninguno de los dos lotes con detecciones ajenas al kit;
- la referencia igual al inventario esperado y la cantidad validada de **cada familia** igual a la esperada;
- cuerpo con `physical_integrity_confirmed: true` y `material_recovered: true`.

Si algo falla: 409 `operator_close_not_allowed` con `details.blockers`; la sesión queda para el Supervisor. Efectos idénticos al cierre SPD (reservas liberadas y piezas a esterilización), `session_close` con `closed_by = station_operator` y `safe_close_confirmed = true`, auditoría `session.operator_close`. Un faltante físico es siempre una discrepancia, así que nunca llega a este camino: sigue la regla del cierre SPD (`physical_shortage_needs_piece_outcome`, `unreconciled_shortage_acknowledged`, `safe_close_confirmed = false`).

Sin `SESSION_SERVICE_URL`, el portal aplica las mismas reglas en `rf_session.operator_close_session` (modo de desarrollo).

## 3. Endpoints antiguos de conteo del Operador en SessionService

`POST /start`, `/vision-counts` y `/count-report` eran una segunda implementación, más antigua, del flujo del Operador. No conocen la referencia de conteo inicial y final, las incorporaciones posteriores ni el reporte de contingencia.

Responden **410** (`operator_flow_in_portal`) salvo `SESSION_OPERATOR_WRITES_ENABLED=true`, un modo de compatibilidad que no usa ningún cliente; sus pruebas heredadas lo activan. Las lecturas siguen disponibles para el Operador, limitadas a sus propias sesiones.

## 4. Seguridad

Validación del token en cada petición:
- `Authorization: Bearer` (token de AuthService): firma con exactamente `JWT_ALGORITHM`, expiración y `type=access`;
- sesión Redis `auth:jwt:{jti}` presente y no revocada;
- usuario e institución activos en PostgreSQL;
- roles efectivos de su institución.

Ninguna cabecera propia es identidad.

Alcance de los datos:
- Todo se limita a la institución del usuario; una sesión de otra institución responde 404.
- Un rol sin permiso recibe 403 y queda en `access_audit` como `DENIED_<MÉTODO>_WORK_SESSION`.

Auditoría: cada cambio escribe `access_audit` en la misma transacción, con los mismos nombres de acción que usaba el portal:
- `session.scheduled`, `session.scheduled_updated`, `privacy.session_agreement`;
- `session.inventory_updated`, `session.instruments_added`;
- `discrepancy.resolve`, `session.spd_close`, `session.operator_close`.

En el portal (Operador): `session.initial_count`, `session.final_count`, `session.manual_close`.

## 5. Configuración y Docker

| Variable | Dónde | Uso |
| :--- | :--- | :--- |
| `SESSION_SERVICE_URL`, `SESSION_SERVICE_TIMEOUT` | BackendWebFlask | `http://127.0.0.1:5004` (vacío = implementación local) |
| `SESSION_OPERATOR_WRITES_ENABLED` | compose → session | `false` (endpoints antiguos del Operador → 410) |
| `SESSION_PORT` | compose | Puerto publicado |

```powershell
docker compose up -d --build postgres redis auth catalog session
curl http://127.0.0.1:5004/health
```

No se crearon tablas ni volúmenes. MongoDB, EvidenceService y los pesos YOLO no se modificaron. VisionWorker añadió el flujo de un solo video (ver GUIA_CAPTURA_VIDEO.md).

## 6. Pruebas

```powershell
cd apps/SessionService; python -m pytest -q        # contratos v0.1.7 + Supervisor (tests/test_supervisor_flow.py), PostgreSQL/Redis reales
cd apps/BackendWebFlask; python -m pytest -q       # incluye tests/test_session_remote.py
python scripts/e2e_session.py <carpeta_videos>     # servicios vivos: Web, Auth, VisionWorker(best.pt), compose postgres/redis/session
```

El E2E recorre los 16 pasos pedidos:
- programación RF;
- conteo inicial YOLO, incorporación confirmada por el Supervisor y referencia actualizada;
- conteo final rechazado contra la referencia nueva;
- reporte de contingencia;
- validación humana;
- discrepancias, resolución y cierre;
- permisos, transiciones inválidas, JSON/XML y auditoría;
- SessionService detenido con el Operador trabajando, y reinicio.

También incluye la concurrencia (reporte del Operador contra inventario del Supervisor, resolución doble, cierre doble) y la paridad de la instantánea entre las dos fuentes.
