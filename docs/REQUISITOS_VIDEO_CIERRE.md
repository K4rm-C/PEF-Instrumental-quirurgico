# Requisitos afectados: Operador CDE y Supervisor

## Captura

- Una sesión de visión utiliza un video identificado por SHA-256.
- El inicial corresponde al frame 0. Una imagen inicial no utilizable bloquea el registro.
- El registro inicial es explícito, persistente, por familia, inmutable e idempotente.
- El procesamiento requiere el inicial. Se utiliza una sola vista de cámara para el primer frame, las muestras YOLO y el último frame, sin reproductor duplicado.
- El final procede del último frame válido, con inferencia independiente y sin acumulación temporal.
- Los extremos conservan imagen, inferencia, video, responsable y fechas.
- La validación humana conserva predicciones originales y justifica las correcciones.

## Cierre

- Solo cierra el Operador asignado de la institución.
- Requiere validación completa, conciliación por familia con kit y referencia inicial, evidencia íntegra, ausencia de discrepancias y confirmaciones físicas explícitas.
- SessionService decide bajo bloqueo transaccional y conserva la idempotencia de efectos sobre piezas, reservas y auditoría.
- Diferencias iniciales por familia, evidencia incompleta, contingencias, instrumentos extra o discrepancias requieren Supervisor.
- Se mantiene `physical_shortage_needs_piece_outcome`: una nota no resuelve un faltante físico.
- Se mantiene `unreconciled_shortage_acknowledged` y la identificación de discrepancias no conciliadas.
- El cierre administrativo con faltante físico no conciliado conserva `safe_close_confirmed=false`.
- El Supervisor consulta los cierres del Operador sin aprobación previa.

Procedimiento: [GUIA_CAPTURA_VIDEO.md](../GUIA_CAPTURA_VIDEO.md).
