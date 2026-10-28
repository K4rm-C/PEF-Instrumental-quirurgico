# Captura de video y cierre por Operador

Actualización: 22 de octubre de 2026.

## Flujo efectivo

1. **Upload video** guarda un archivo temporal en VisionWorker. No reproduce ni procesa el recorrido completo. Solo infiere el frame 0. Si es oscuro, uniforme, sobreexpuesto o ilegible, informa el error; no lo sustituye por otra charola inicial.
2. **Register Initial Count** conserva en PostgreSQL un `auto_count` con `trigger=initial`, `source=first_frame`, familias, cajas, modelo, configuración, hash del video, índice, fecha y usuario. Una diferencia de total exige reconocimiento explícito. El inicial es inmutable.
3. **Run YOLO** requiere el inicial del mismo archivo. Los frames procesados aparecen en el mismo espacio de cámara, con sus detecciones y el progreso real del worker. No hay reproductor local adicional ni una segunda reproducción. Las muestras intermedias no reemplazan los extremos.
4. Al terminar se infiere el último frame utilizable dentro de la cola final configurada, conservando los índices rechazados por calidad. Sin imagen utilizable o con fallo de YOLO no se genera final. **Confirm Final Count** requiere `ready`, la inferencia final de esa ejecución y el mismo SHA-256. Al confirmar se abre directamente Human Validation.
5. Human Validation conserva las predicciones y registra correcciones por separado con responsable y justificación. Un faltante físico documentado no se concilia mediante una corrección numérica.
6. **Close Session** exige integridad física y recuperación explícitas. SessionService comprueba las condiciones bajo bloqueo de fila y reutiliza la liberación de reservas y transición a esterilización del cierre existente.

## Evidencias y autorización

Los dos eventos de extremo conservan el JPEG anotado (`frame_jpeg_b64`) y `frame_sha256` (SHA-256 del texto base64 UTF-8) en la misma transacción del conteo. La imagen puede recuperarse después de reiniciar el portal aun si falla el envío posterior a Garage/MongoDB. Esto detecta inconsistencias; no es una firma contra un administrador de base de datos. El envío de checkpoints a EvidenceService/MongoDB se mantiene.

El cierre autónomo rechaza imágenes ausentes o hashes diferentes. Las sesiones anteriores sin esta copia requieren Supervisor; no se fabrican evidencias retrospectivas. El inicial por familia debe coincidir con el inventario requerido. Las correcciones finales no ocultan diferencias iniciales; si es necesario interpretar un inicial equivocado, interviene el Supervisor.

Se conservan JWT, RBAC y separación institucional. Solo cierra el Operador asignado. Solicitudes repetidas no duplican cierre, inventario, reservas ni auditoría. Rechazar otra vez el final de la misma ejecución no crea otro incidente.

## Contratos

- Portal: `POST /operator/sessions/{id}/capture/upload`, `/capture/initial`, `/capture/play`, `/capture/final`; `GET /capture/state`; `POST /vision-validation`; `POST /close-session`.
- Worker interno: `POST /sessions/{id}/video`; `POST /sessions/{id}/video/{video_id}/process`; `GET /sessions/{id}/state`. El portal autoriza el registro y el inicio del procesamiento principal.
- SessionService: `POST /api/v1/sessions/{id}/operator-close`, rol `station_operator`, cuerpo `{"physical_integrity_confirmed":true,"material_recovered":true}`. El contrato JSON/XML y XSD ya existían en esta copia. Se añaden bloqueos `initial_family_mismatch` y `evidence_integrity`. No se acepta `safe_close_confirmed` del cliente.

## Límites

Brillo y contraste no prueban que la charola esté completa: el Operador debe revisar la imagen. El video original es temporal; el historial conserva hash, metadatos y frames. Reiniciar el worker puede exigir volver a cargar el mismo archivo. La vista de cámara muestra muestras del procesamiento de VisionWorker, no una reproducción a FPS nativos; su finalización real y los conteos persistidos son la autoridad. Dos imágenes en JSONB aumentan el tamaño del historial; Garage mantiene su función de almacenamiento de evidencias.

## Reconstrucción en PowerShell

Desde la raíz, después de revisar los resultados:

```powershell
$docker = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"
& $docker compose -p pef_prueba2_limpio --env-file .env up -d --build --no-deps web vision session
& $docker compose -p pef_prueba2_limpio --env-file .env ps web vision session
$webPort = (Get-Content .env | Where-Object { $_ -match '^WEB_PORT=' }) -replace '^WEB_PORT=', ''
Start-Process "http://localhost:$webPort/operator/sessions"
```

En navegador: iniciar sesión como Operador, abrir una sesión asignada en Capture, cargar el video y recorrer los pasos. Revisar familias y resumen antes del cierre. Una sesión discrepante debe requerir Supervisor. No se necesitan migraciones ni reconstruir otros servicios. Nunca usar `down -v`.
