# PEF — Notifications Service

NotificationsService (`apps/NotificationsService`, puerto 5007) entrega **avisos internos** al Operador, al Supervisor y al Administrador sobre las sesiones de conteo en las que participan.

- Contrato: [DS09 §1.3.6](data/DataStandards/PEF-DS09-API-Exchange-Contracts.md), complemento v0.1.0.
- Documentación ejecutable: `http://127.0.0.1:5007/api/v1/notifications/docs`.

**Regla central: el Operador nunca depende de este servicio.** Ningún flujo del portal ni de SessionService lo llama, y ninguna operación crítica hace HTTP hacia él. Tampoco depende de AuditService.

## 1. Arquitectura

```
Portal / SessionService ──INSERT en su transacción──▶ access_audit / count_event
                                                        │ AFTER INSERT trigger (misma transacción)
                                                        ▼
                                                 notification_outbox ◀── barrido de reconciliación (cada 60 s)
                                                        │ FOR UPDATE SKIP LOCKED
                                       notifications-worker (worker.py) ──▶ notification
                                                                                │ SELECT / UPDATE
Campana y /notifications ─▶ services/notifications_remote.py ─HTTP + Bearer─▶ notifications (API)
```

- **Generación desacoplada (outbox transaccional).** Dos triggers `AFTER INSERT` copian al outbox solo los hechos relevantes:
  - en `access_audit`: `session.scheduled` y `discrepancy.resolve`;
  - en `count_event`: `manual_close` y `session_close`.

  Lo hacen en la misma transacción del productor: si el productor confirma, el evento queda encolado; si revierte, desaparece. No se modificó el código de ningún productor.
- **Fallos aislados.** Si el trigger fallara, emite un `WARNING` y no aborta la operación del productor. El barrido de reconciliación del worker vuelve a leer las tablas de origen (desde la instalación, ventana de 7 días) y encola lo que falte.
- **Idempotencia y deduplicación:**
  - un solo registro de outbox por fila de origen: `UNIQUE (source_table, source_id)`;
  - una sola notificación por evento, destinatario y tipo: `UNIQUE (source_table, source_id, recipient_user_id, type)`, con `ON CONFLICT DO NOTHING`;
  - el evento se marca procesado en la misma transacción que sus notificaciones.
- **Concurrencia y errores:**
  - varios workers pueden correr en paralelo (`FOR UPDATE SKIP LOCKED`);
  - un evento que falla se reintenta en los ciclos siguientes (`attempts`, `last_error`) sin bloquear a los demás;
  - tras 10 intentos queda visible en los logs.
- **Recuperación tras una caída.** Con el servicio detenido, los eventos se acumulan en `notification_outbox`. Al reiniciarse, el worker reconcilia y procesa todo lo pendiente; reprocesar no duplica.
- **Sin datos clínicos.** El contenido es la etiqueta de la sesión (`WS-XXXXXXXX`) y, cuando existen, la hora programada o el número de discrepancias abiertas.

## 2. Eventos y destinatarios

| Caso | Evento de origen | Tipo | Destinatario |
| :--- | :--- | :--- | :--- |
| Sesión programada | `access_audit` `session.scheduled` (SessionService) | `session_scheduled` | Operador asignado (`work_session.user_id`) |
| Discrepancia pendiente | `count_event` `manual_close` con `next_status = correction_required` (Operador, monolito) | `discrepancy_pending` | Supervisor que programó la sesión. Si no se conoce (datos antiguos), los Supervisores SPD activos de la institución |
| Corrección solicitada | (mismo evento) | `correction_required` | Operador asignado |
| Discrepancia resuelta | `access_audit` `discrepancy.resolve` (SessionService) | `discrepancy_resolved` | Operador asignado (un solo aviso por sesión mientras no lo lea, aunque se resuelvan varias discrepancias) |
| Cierre seguro | `count_event` `session_close` (SessionService) | `session_closed` | Operador asignado y Supervisor que programó, excepto quien cerró |

Reglas comunes:
- Los destinatarios deben ser usuarios **activos** de la institución de la sesión.
- El actor no recibe aviso de su propia acción, salvo en `correction_required`: es la respuesta del sistema al reporte del Operador.
- El Administrador no recibe avisos de sesión; su página muestra la lista vacía.

## 3. Tablas y migración

`data/migrations/006_notifications.sql` es idempotente y conserva los datos: solo `CREATE … IF NOT EXISTS`, `CREATE OR REPLACE` y triggers recreados.

Se aplica de tres formas:
- en volúmenes nuevos, como `06_notifications.sql` del initdb de postgres;
- en bases existentes, al iniciar la API y el worker, bajo `pg_advisory_xact_lock`;
- el servicio lleva una copia en `apps/NotificationsService/migrations/`, y un test verifica que sea idéntica.

| Tabla | Contenido |
| :--- | :--- |
| `notification_stream` | Instalación del flujo (`installed_at`: la reconciliación no retrocede más allá) |
| `notification_outbox` | `source_table`, `source_id`, `event_code`, `session_id`, `occurred_at`, `enqueued_at`, `processed_at`, `attempts`, `last_error` |
| `notification` | `recipient_user_id` (FK `user`), `institution_id`, `type`, `status` (`unread`/`read`, coherente con `read_at` por `CHECK`), `session_id`, `data` (jsonb), `source_table`, `source_id`, `created_at`, `read_at` |

## 4. API y seguridad

Endpoints:
- `GET /api/v1/notifications`, con filtros `status`, `type`, `from` y `to`, paginación y orden;
- `GET /{id}`;
- `GET /unread-count`;
- `PATCH /{id}/read`;
- `PATCH /read-all`, con cuerpo opcional JSON o XML validado con XSD.

Formato y errores:
- JSON o XML según `Accept`;
- `X-Correlation-ID` en cada respuesta;
- errores con el sobre DS09 (`error`, `message`, `correlation_id`, `details`).

Validación de cada petición:
- JWT con firma (exactamente `JWT_ALGORITHM`), expiración y `type=access`;
- sesión Redis `auth:jwt:{jti}` no revocada;
- usuario e institución activos.

Ninguna cabecera propia es identidad.

Alcance:
- El destinatario es el usuario del token.
- Cada consulta filtra por `recipient_user_id` **y** por `institution_id`.
- La notificación de otro usuario responde 404: no se revela si existe.
- No hay endpoint de creación ni de borrado (405).

## 5. Portal

- **Campana del encabezado.** Ahora es un enlace a `/notifications`, con el mismo diseño. El contador se carga con `static/js/notifications.js` **después** de renderizar la página y se refresca cada 60 s, de modo que ninguna página espera al servicio.
- **`/notifications`** (los tres roles):
  - lista con filtros de estado y tipo, y paginación;
  - marca de no leída;
  - "Marcar como leída" y "Marcar todas como leídas";
  - "Ver sesión", que marca el aviso y abre el detalle autorizado del rol: `/operator/sessions/<id>` o `/supervisor/sessions/<id>`. Esas rutas vuelven a comprobar la propiedad y la institución.
  - Textos traducidos a es-MX.
- **Servicio caído:**
  - `/notifications` muestra "Las notificaciones no están disponibles temporalmente. Tu trabajo no se ve afectado" (503);
  - `/notifications/unread-count` responde `{available: false}`;
  - la campana queda sin contador;
  - el resto del portal sigue igual.
- **Sin `NOTIFICATIONS_SERVICE_URL`**, las notificaciones quedan deshabilitadas en el portal. No hay respaldo local.

## 6. Configuración, Docker y pruebas

| Variable | Dónde | Uso |
| :--- | :--- | :--- |
| `NOTIFICATIONS_SERVICE_URL`, `NOTIFICATIONS_SERVICE_TIMEOUT` | BackendWebFlask | `http://127.0.0.1:5007`, 3 s |
| `NOTIFICATIONS_PORT` | compose | Puerto publicado de la API |
| `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET_KEY`, `JWT_ALGORITHM` | compose → notifications | Los mismos de los demás servicios (el worker solo recibe `DATABASE_URL`) |
| `NOTIFICATIONS_WORKER_*`, `NOTIFICATIONS_RECONCILE_*` | compose → worker | Ver `apps/NotificationsService/README.md` |

```powershell
docker compose up -d --build postgres redis notifications notifications-worker
docker compose logs -f notifications-worker              # cada evento procesado queda en el log
curl -i http://127.0.0.1:5007/health                     # X-Outbox-Pending: eventos en espera
cd apps/NotificationsService; python -m pytest -q        # PostgreSQL/Redis reales
python scripts/e2e_notifications.py <carpeta_videos>     # servicios vivos; detiene y reinicia notifications y notifications-worker
```
