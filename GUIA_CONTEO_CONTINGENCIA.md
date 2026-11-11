# PEF — Conteo, contingencia técnica y escalamiento de diferencias físicas (MS-09)

Reglas del conteo (sin cambios desde MS-03.1): **conteo final físico = referencia vigente** (conteo inicial + incorporaciones confirmadas por el Supervisor). Un final distinto se rechaza (`close_blocked`), sin confirmación que lo omita; la validación humana solo corrige la **clasificación**, nunca el total. MS-09 cierra dos huecos alrededor de esas reglas sin inventar políticas clínicas.

## 1. Contingencia técnica (informe de cantidades sin YOLO)

### 1.1 Qué se encontró (Fase 1)

En el E2E de MS-06 la contingencia `worker_unavailable` **la indujo la prueba**. Pero el sistema lo permitía a cualquier Operador: el motivo solo se registraba, no se comparaba con el estado del worker, y el cierre del Supervisor la contaba como un cierre seguro normal.

### 1.2 Comprobaciones técnicas implementadas

| Motivo | Verificación en el servidor | Resultado |
| :--- | :--- | :--- |
| `worker_unavailable` | El worker no respondió por la sesión **y** su `/health` falla | **Verificada**. Si el worker responde y está sano: **rechazada (409)**, el Operador debe capturar con YOLO o declarar otro motivo |
| `processing_error` | El worker reporta un error para la sesión | Verificada; si no, **declarada** |
| `unreliable_detection` | Solo después de que YOLO produjo una detección (conteo automático o cuadros procesados) | Sin detección: **rechazada (409)**; con detección: declarada (juicio humano) |
| `other` | — (descripción obligatoria) | Declarada |

Se registra con el informe: motivo, notas, estado del worker (evidencia), `verification {status, basis, checked_at}`, estación, cámara, usuario y hora (evento `manual_close` + auditoría `session.contingency_close`).

**Una contingencia nunca produce un conteo final YOLO** (no existe ningún `auto_count` final; `vision_final_state.valid` sigue en falso) y nunca reemplaza un final rechazado (regla MS-03.1).

### 1.3 Revisión del Supervisor

- Confirmar la revisión (`contingency_reviewed`, ya existía).
- Si el motivo **no** fue verificado: reconocimiento explícito adicional (`unverified_contingency_acknowledged`); sin él, 409 `unverified_contingency`.
- El cierre registra `close_basis = contingency_count` y `contingency_verification` (`verified` / `declared`).
- Reportes cuenta estos cierres en **`approved_contingency`**, separados de `approved` (cierres con conteo final YOLO válido): un incidente de contingencia **no** se presenta como cierre normal.

**Decisión funcional pendiente:** si un cierre tras una contingencia debe seguir llamándose "cierre seguro" (`safe_close_confirmed = true`) o debe registrarse como no resuelto. Hoy se conserva el comportamiento anterior y se distingue por `close_basis`/`approved_contingency`.

## 2. Escalamiento de un posible faltante o sobrante físico

Antes de MS-09 el Operador solo podía volver a capturar. Ahora, tras un final rechazado, puede **reportar** la posible diferencia (`POST /operator/sessions/{id}/capture/escalate`, en el paso de recaptura de la pantalla).

| Condición (servidor) | Si no se cumple |
| :--- | :--- |
| Sesión de visión en curso, asignada al Operador | 403 / 409 |
| Último final rechazado (`close_blocked final_count_mismatch`) y ningún final válido después | 409 (p. ej. 15→15: no hay nada que reportar) |
| La referencia actual es la misma contra la que se rechazó la captura | 409 (si el Supervisor confirmó incorporaciones, capturar de nuevo contra la nueva referencia) |
| Notas de al menos 10 caracteres | 400 |

Efecto (mecanismos existentes): discrepancia abierta `shortage` (la captura vio menos: 15→14) o `surplus` (vio más: 15→16), con `expected = referencia`, `detected = final` y `origin_event_id` = la captura rechazada, cuya imagen y registro se conservan. La sesión pasa a `correction_required` y el disparador existente notifica al Supervisor (`discrepancy_pending`). Se audita `session.final_count_escalated`. No se registra ningún conteo final y la validación humana sigue cerrada.

Cierre: el Supervisor resuelve la discrepancia con los resultados existentes (perdido / recuperado / resuelto) y, para cerrar, debe reconocer que **no existe un conteo final válido** (`escalation_acknowledged`). El cierre queda con `safe_close_confirmed = false` y `close_basis = final_count_escalation`. Un faltante físico registrado exige además el reconocimiento de faltante no conciliado (regla vigente de MS-03.1).

**Decisiones funcionales pendientes:**
1. Qué resoluciones puede aprobar el Supervisor tras un escalamiento (p. ej., si un instrumento recuperado permite una recaptura y un cierre seguro normal).
2. Si el Supervisor puede devolver la sesión al Operador para recapturar.

**Observación (sin cambios):** el texto del cierre local dice "faltante no documentado como recuperado", pero la regla de MS-03.1 (portal y SessionService) trata **todo** faltante físico registrado como cierre no seguro, aunque se marque recuperado. La regla no se modificó; el texto se puede ajustar cuando se decida el punto 1.

## 3. Pruebas

- Portal `tests/test_ms09_count_safety.py` (11): contingencia contradicha, verificada (worker caído), declarada con reconocimiento, detección no confiable sin detección, error de procesamiento verificado; escalamientos 15→14 y 15→16, 15→15 rechazado, referencia modificada por incorporaciones, cierre nunca seguro (sobrante y faltante).
- SessionService `tests/test_ms09_close.py` (4): mismas comprobaciones en el cierre remoto, `approved_contingency`, booleanos.
- MS-10, navegador real (`scripts/browser_validate_flows.py`): el formulario de escalamiento estaba dentro del panel del conteo final, oculto en la etapa de recaptura, así que el Operador no lo veía. Se movió fuera del panel (sin cambios de reglas) y se añadió la regresión `test_escalation_form_is_not_inside_the_final_panel`. En el navegador: escalamiento, contingencia contradicha (409) y contingencia declarada, ninguno con final YOLO.
- E2E real `scripts/e2e_ms09_count_safety.py` (7, YOLO real, VisionWorker en contenedor): escalamiento de un final real rechazado (cierre por SessionService) y contingencia rechazada con el worker activo / verificada con el contenedor realmente detenido.
