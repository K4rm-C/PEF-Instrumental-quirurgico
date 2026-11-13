# PEF — Kits y charolas (MS-08)

Migración `015_charolas.sql` (idempotente). Dueño: **CatalogService** (las charolas son del SPD, como los kits — MS-01/P3).

## 1. Kit frente a charola

| | Kit | Charola (`surgical_tray`) |
| :--- | :--- | :--- |
| Qué es | Definición: composición versionada (cada cambio crea una versión nueva y desactiva la anterior) | Objeto físico individual con su etiqueta (`code`, única por institución) |
| Relación | — | Opcional: la **versión** de kit con que está configurada |
| Dos con el mismo kit | — | Son dos charolas distintas |

## 2. Reglas implementadas

- Escribe `spd_supervisor`; cualquier usuario de la institución lee. Institución siempre la del usuario; otra institución = 404. Un *trigger* impide además asignar un kit de otra institución.
- Al crear o cambiar el kit, debe ser una versión **activa** de la institución. Si después el kit se reemplaza por una versión nueva, la charola **conserva** la versión con que se configuró (`kit_superseded: true`); nada se mueve automáticamente: el Supervisor decide.
- Estados **administrativos**: `active`, `out_of_service`, `retired`.
- Concurrencia optimista: `PUT` exige la `version` leída; si cambió, 409 `version_conflict` con la versión actual.
- Historial automático (`surgical_tray_history`, *trigger*): versión, fecha, usuario, tipo (`create`/`update`/`status`), antes y después. Auditoría `CREATE_TRAY`, `UPDATE_TRAY`, `TRAY_STATUS_*`.
- Sesiones: `work_session.tray_id` opcional al programar (SessionService). Debe ser una charola activa de la institución y configurada con el **mismo** kit de la sesión (o sin kit); si no, 422 (`invalid_tray`, `inactive_tray`, `tray_kit_mismatch`). Las cantidades esperadas siguen congeladas en la sesión: **cambiar la charola o su kit nunca modifica una sesión**. `GET /api/v1/sessions/trays/{id}` lista las sesiones que la usaron.
- Sincronización: las charolas están en el registro de cambios (MS-07); `retired` se publica como `deactivate`.

## 3. Pantallas

Supervisor → Catálogo → **Charolas** (`/supervisor/trays`): lista con filtro de estado y aviso de kit reemplazado; detalle con edición (versión oculta), historial y sesiones que la usaron; alta.

## 4. Decisiones pendientes (no implementadas)

1. **Ciclo operativo** de la charola (en uso, en esterilización, en tránsito…): los RF no lo definen; no se infiere de las sesiones.
2. ¿Puede una charola estar en dos sesiones a la vez? Hoy no se impide.
3. ¿La charola es obligatoria al programar? Hoy es opcional (no cambia el flujo del Supervisor ni del Operador).
4. Charola en el formulario de programación del portal: el API la acepta; el formulario del portal no la pide todavía (evita cambiar la pantalla sin decisión).
5. Vínculo de instrumentos individuales con una charola.

## 5. Pruebas

CatalogService `tests/test_ms08_trays.py` (5); SessionService `tests/test_ms08_session_trays.py` (4); portal `tests/test_ms08_trays_portal.py` (5); E2E `scripts/e2e_ms07_ms08.py` (charola creada por la API de sincronización, usada por una sesión real y visible en la pantalla del Supervisor).

## 6. MS-09: consolidación técnica

- **Historial de asignaciones:** cada asignación, cambio o retiro de la charola de una sesión se audita (`session.tray_assigned`: quién y cuándo); el historial de la propia charola sigue en `surgical_tray_history`.
- **Cambio mientras está programada:** `PUT /api/v1/sessions/{id}` acepta `tray_id` (ausente = sin cambio, `null` = retirar) con las mismas validaciones; si se cambia el kit de la sesión, una charola configurada con otro kit impide el cambio (422 `tray_kit_mismatch`), sin aplicar nada.
- **Concurrencia:** la charola se bloquea `FOR SHARE` al validarla, así un retiro o cambio de kit simultáneo espera a la programación (y viceversa); `PUT` de la charola mantiene el control de versión.
- **Integridad en la base:** migración `016_ms09_integridad.sql`: un *trigger* impide que una sesión apunte a una charola de otra institución, también para escrituras fuera de la API.
- **Sin cambios automáticos:** cambiar la charola o la versión de su kit nunca modifica la referencia de conteo ni las cantidades esperadas.
- Siguen **pendientes de decisión**: obligatoriedad, uso simultáneo y ciclo operativo (§4).
