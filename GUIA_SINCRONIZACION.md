# PEF — Sincronización y clientes externos (MS-07)

Servicio: **IntegrationService** (`apps/IntegrationService`, puerto 5009; worker `python worker.py`). Migración `014_sincronizacion_integracion.sql`. Contrato: `apps/IntegrationService/openapi.yaml` y `schemas/integration.xsd`.

No contiene reglas de negocio de sesiones, catálogo ni conteo: publica cambios, reenvía operaciones autorizadas al servicio dueño **una sola vez** y proyecta datos a FHIR (ver `GUIA_INTEGRACION_FHIR.md`).

## 1. Por qué un servicio nuevo

| Responsabilidad | Dónde quedó | Motivo |
| :--- | :--- | :--- |
| Registro de cambios por institución, cursor, compactación | IntegrationService (+ triggers en PostgreSQL) | Atraviesa catálogo, salas, estaciones, médicos y sesiones; ningún servicio dueño los ve todos |
| Idempotencia de operaciones de clientes | IntegrationService | Es común a todas las operaciones; la regla de negocio sigue en el dueño |
| Clientes máquina (token) | **AuthService** (`POST /api/v1/auth/token`) | Reutiliza la emisión, firma y revocación en Redis existentes |
| Administración de clientes máquina | Portal de Administración (monolito) | Regla de arquitectura: Admin = monolito |
| FHIR R4 (lectura y entregas) | IntegrationService | Proyección de solo lectura (DS08), sin segundo modelo de persistencia |

## 2. Registro de cambios (change log)

- `sync_change` lo llenan **triggers** en la misma transacción que el cambio (si la transacción se revierte, no se publica nada). No se reutilizó `notification_outbox`: ese outbox lleva eventos de negocio para notificar; aquí se necesita el estado de cada recurso. El patrón es el mismo (trigger transaccional + consumidor).
- Recursos: `instrument_family` (catálogo compartido), `kit`, `instrument`, `surgical_tray`, `operating_room`, `capture_station`, `capture_camera`, `physician`, `work_session`.
- Operación: `create`, `update` (solo si cambió algo), `deactivate` (`active` true→false, o charola `retired`), `delete`.
- `version`: contador monotónico por recurso (`sync_resource_version`, UPSERT bajo bloqueo de fila).
- Backfill de una sola vez por tipo (`sync_seed`): la primera sincronización recibe también lo que existía antes de la migración.

### 2.1 Consistencia sin depender del reloj del cliente

Cada fila guarda el `xid8` de su transacción. Una página solo entrega filas de transacciones **ya terminadas** (`txid < pg_snapshot_xmin(pg_current_snapshot())`), ordenadas por `(txid, seq)`; el cursor es el `(txid, seq)` de la última fila entregada. Una transacción que empezó antes y confirma después tiene `txid` ≥ la marca de agua usada antes, así que nunca queda "detrás" de un cursor: **ni omisiones ni duplicados**, aunque haya escrituras concurrentes durante la paginación (probado en `test_a_long_transaction_is_never_skipped`).

### 2.2 Cursor

Opaco y firmado (`v1.<payload>.<hmac>`, clave `SYNC_CURSOR_SECRET` o derivada del secreto JWT). Uno alterado → 400 `invalid_cursor`. Uno más viejo que `SYNC_CURSOR_MAX_AGE_DAYS` (30) → **410 `cursor_expired`**: el cliente vuelve a empezar sin cursor. La compactación (worker, cada hora) borra cambios con más de `SYNC_RETENTION_DAYS` **solo si existe uno posterior del mismo recurso**: el último cambio de cada recurso (incluidos borrados y desactivaciones) se conserva siempre, así que empezar sin cursor reconstruye el estado completo.

### 2.3 API

| Ruta | Quién | Notas |
| :--- | :--- | :--- |
| `GET /api/v1/sync/resources` | usuarios y clientes | Tipos que puede sincronizar |
| `GET /api/v1/sync/changes?cursor=&types=&limit=` | usuarios: `it_admin`, `spd_supervisor`, `station_operator`; clientes: `sync.catalog.read`, `sync.locations.read`, `sync.practitioners.read`, `sync.sessions.read` | `limit` 1–500; cada cambio trae `data` (proyección pública **actual**, sin datos del paciente, ROI ni secretos), `data_version` y `href` al API dueño |
| `POST /api/v1/sync/operations` | solo usuarios | `Idempotency-Key` obligatorio |

Visibilidad: el Operador solo recibe **sus** sesiones; Operador y clientes no reciben médicos salvo con el alcance; los kits reemplazados solo llegan como "inactivo" a quien no es Administrador. Las lecturas de clientes máquina se auditan (`SYNC_CHANGES_READ`, actor `client`).

## 3. Operaciones idempotentes

Tipos permitidos: `session.schedule` (SessionService), `tray.create` / `tray.update` (CatalogService), `notification.read` (NotificationsService). Se **reenvían al servicio dueño con el token del propio usuario**, así aplican los mismos roles, institución, validaciones, conflictos de versión (409 `version_conflict` de la charola) y auditoría.

**No existen** (400 `unsupported_operation`): conteos, conteo final, validación, cierres, auditoría, modelos YOLO. Esas acciones solo existen en los flujos del portal con sus reglas.

| Situación | Respuesta |
| :--- | :--- |
| Primera vez | Se reenvía con `X-Correlation-ID = op-<hash(principal, clave)>`; la respuesta definitiva (2xx o 4xx salvo 401/408/429/503) se guarda |
| Reintento, misma clave y mismo cuerpo | La misma respuesta, `Idempotent-Replayed: true`; el dueño no recibe nada |
| Misma clave, otro cuerpo | 422 `idempotency_key_mismatch` |
| Duplicado concurrente | 409 `operation_in_progress` |
| Resultado incierto (timeout, 5xx) | 502 `operation_outcome_unknown`; al reintentar se **reconcilia**: si el dueño auditó la operación con ese `correlation_id` (`session.scheduled`, `CREATE_TRAY`, `UPDATE_TRAY`), se completa con el id recuperado sin aplicarla otra vez; si no, se reenvía |
| 401/408/429/503 | No se guardan (el cliente reintenta con token nuevo o más tarde) |

Las claves son por principal (usuario o cliente); caducan a los 7 días.

## 4. Autenticación de clientes externos

| Cliente | Mecanismo | Notas |
| :--- | :--- | :--- |
| Escritorio / Android usado por una persona | `POST /api/v1/auth/login` (tokens en el cuerpo), `/refresh`, `/logout`, Bearer | El cliente **nunca** recibe secretos del servidor; revocación en Redis como en el portal |
| Cliente máquina (integración, HIS) | `POST /api/v1/auth/token` `grant_type=client_credentials` (form RFC 6749, JSON o HTTP Basic) | Token `client_access` de 15 min (`AUTH_CLIENT_TOKEN_SECONDS`), sin refresh; alcances DS08 |

- El secreto lo genera Administración, se muestra **una vez** y se guarda con hash (werkzeug, igual que las contraseñas). Rotación y desactivación revocan todos los tokens del cliente (`POST /api/v1/auth/clients/{id}/revoke`).
- IntegrationService valida en cada petición: firma y algoritmo, Redis (revocación), cliente activo, institución activa y la **misma** institución del token, y los alcances guardados **ahora** en PostgreSQL (los del token solo pueden reducirlos).
- Un token de cliente no sirve en ningún otro servicio (todos exigen `type = access` y un usuario): un cliente no puede hacerse pasar por usuario ni por servicio interno. Errores de credenciales: siempre 401 `invalid_client` (sin enumeración); intentos fallidos limitados como el login (429).

**Para producción falta:** almacenamiento de los secretos en un gestor de secretos del cliente; rotación periódica; mTLS opcional para HIS; *rate limit* por cliente además del de IP.

## 5. Gateway

Publicado: `POST /api/v1/auth/token` (límite estricto de login), `GET /api/v1/sync/{resources,changes,openapi.yaml,schemas/integration.xsd}`, `POST /api/v1/sync/operations`, `GET /fhir/r4/...` (lista explícita) y `/api/v1/catalog/trays[/id]`. No publicado: `/api/v1/integration/*` (consola de Administración) y cualquier recurso FHIR no listado. Ver `GUIA_API_GATEWAY.md`.

## 6. Pruebas

- `apps/IntegrationService/tests` (47, PostgreSQL y Redis reales): registro de cambios con datos **confirmados** (primera sincronización, incremental, paginación, transacción larga, cursores, visibilidad), operaciones (reenvío, repetición, discrepancia, concurrencia real, reconciliación, operaciones prohibidas), autenticación, FHIR, entregas y contrato. Los servicios dueños y los servidores FHIR de estas pruebas son **dobles HTTP locales** (dicho en cada archivo).
- `apps/BackendAuthService/tests/test_ms07_client_credentials.py` (10).
- `scripts/e2e_ms07_ms08.py`: servicios reales, Gateway y HAPI (ver `GUIA_PRUEBAS.md` §2.7).
