# Captura de video y cierre por Operador

Actualización: 22 de octubre de 2026.

## Flujo efectivo

1. **Upload video** guarda un archivo temporal en VisionWorker. No reproduce ni procesa el recorrido completo. Solo infiere el frame 0. Si es oscuro, uniforme, sobreexpuesto o ilegible, informa el error; no lo sustituye por otra charola inicial.
2. **Register Initial Count** conserva en PostgreSQL un `auto_count` con `trigger=initial`, `source=first_frame`, familias, cajas, modelo, configuración, hash del video, índice, fecha y usuario. Una diferencia de total exige reconocimiento explícito. El inicial es inmutable.
3. **Run YOLO** requiere el inicial del mismo archivo. Los frames procesados aparecen en el mismo espacio de cámara, con sus detecciones y el progreso real del worker. No hay reproductor local adicional ni una segunda reproducción. Las muestras intermedias no reemplazan los extremos.
4. Al terminar se infiere el último frame utilizable dentro de la cola final configurada, conservando los índices rechazados por calidad. Sin imagen utilizable o con fallo de YOLO no se genera final. **Confirm Final Count** requiere `ready`, la inferencia final de esa ejecución y el mismo SHA-256 (o la nueva grabación de una re-captura). **Regla PEF (MS-03.1):** el total físico del último cuadro (cajas asignadas y no asignadas) debe ser **igual** a la referencia vigente (inicial + incorporaciones confirmadas por el Supervisor). Si difiere, se rechaza siempre (`close_blocked`, 409): no hay casilla ni confirmación para avanzar. La página pasa al paso de re-captura: el Operador revisa la charola, la graba de nuevo y sube el video nuevo (o procesa otra vez el mismo); el conteo inicial no cambia. Solo con un final igual a la referencia se abre Human Validation.
5. Human Validation conserva las predicciones y registra correcciones por separado con responsable y justificación. Solo corrige la **clasificación**: el total validado (familias + fuera del kit) debe ser el del conteo final; no sirve para resolver un bloqueo. Un faltante físico documentado no se concilia mediante una corrección numérica.
6. **Close Session** exige integridad física y recuperación explícitas. SessionService comprueba las condiciones bajo bloqueo de fila y reutiliza la liberación de reservas y transición a esterilización del cierre existente.

## Evidencias y autorización

Los dos eventos de extremo conservan el JPEG anotado (`frame_jpeg_b64`) y `frame_sha256` (SHA-256 del texto base64 UTF-8) en la misma transacción del conteo. La imagen puede recuperarse después de reiniciar el portal aun si falla el envío posterior a Garage/MongoDB. Esto detecta inconsistencias; no es una firma contra un administrador de base de datos. El envío de checkpoints a EvidenceService/MongoDB se mantiene.

El cierre autónomo rechaza imágenes ausentes o hashes diferentes. Las sesiones anteriores sin esta copia requieren Supervisor; no se fabrican evidencias retrospectivas. El inicial por familia debe coincidir con el inventario requerido. Las correcciones finales no ocultan diferencias iniciales; si es necesario interpretar un inicial equivocado, interviene el Supervisor.

Se conservan JWT, RBAC y separación institucional. Solo cierra el Operador asignado. Solicitudes repetidas no duplican cierre, inventario, reservas ni auditoría. Rechazar otra vez el final de la misma ejecución no crea otro incidente.

## Contratos

- Portal: `POST /operator/sessions/{id}/capture/upload`, `/capture/initial`, `/capture/play`, `/capture/final`; `GET /capture/state`; `POST /vision-validation`; `POST /close-session`.
- Worker interno: `POST /sessions/{id}/video`; `POST /sessions/{id}/video/{video_id}/process`; `GET /sessions/{id}/state`. El portal autoriza el registro y el inicio del procesamiento principal.
- SessionService: `POST /api/v1/sessions/{id}/operator-close`, rol `station_operator`, cuerpo `{"physical_integrity_confirmed":true,"material_recovered":true}`. El contrato JSON/XML y XSD ya existían en esta copia. Se añaden bloqueos `initial_family_mismatch` y `evidence_integrity`. No se acepta `safe_close_confirmed` del cliente.

## Modelo fijado por sesión y pesos verificados (MS-02)

**Qué modelo usa una sesión.** `work_session.yolo_model_id` se fija **una vez**:
- Al iniciar una sesión de visión, en la misma transacción con la fila bloqueada (`rf_session.start_session` del portal o `POST /api/v1/sessions/{id}/start` de SessionService). Se usa el modelo activo en ese momento.
- Si no había ninguno activo, se fija en el primer *bind* (primer uso de inferencia).

Cada fijación se audita (`session.model_pinned`), y el evento `session_open` guarda `yolo_model_id`, `model_version` y `model_sha256`. La migración `011_session_model_pin.sql` impide cambiarlo después. Activar otro modelo en Administración solo afecta a las sesiones **nuevas**.

***Bind* firmado.** `vision_bridge.bind_session` envía el modelo **fijado** (no el activo) con `model_id`, `model_version`, `sha256`, `class_names` y la referencia de almacenamiento de Administración (`bucket`, `key`, más una URL prefirmada de 5 minutos si es S3). El cuerpo va firmado con HMAC (`X-PEF-Signature`, `VISION_WS_SECRET`).

Respuestas del worker al *bind*:

| Situación | Respuesta |
|---|---|
| Sin firma o firma inválida | 401 |
| Sin modelo | 422 `model_required` |
| Otro modelo distinto del ya ligado a esa sesión | 409 `model_conflict` |
| Correcto | Devuelve el SHA-256 cargado; el portal lo compara con el fijado |

**Cómo obtiene el worker los pesos** (`apps/VisionWorker/model_store.py`), en este orden y siempre verificando el SHA-256 **antes** de cargar:
1. Caché en disco `VISION_MODEL_CACHE_DIR/<sha256>.pt`.
2. `VISION_WEIGHTS_PATH`, solo si su SHA-256 es el del modelo fijado.
3. Volumen de Administración en solo lectura (`VISION_MODEL_STORAGE_DIR`, bucket `local`).
4. Descarga por la URL prefirmada, solo desde hosts de `VISION_MODEL_FETCH_ALLOWED_HOSTS`. El worker no tiene credenciales S3.

La clave debe ser `models/<32 hex>.pt` (generada por Administración). Si el SHA-256 no coincide, falta el archivo o falla Garage, el *bind* responde un error (`model_checksum_mismatch`, `model_weights_missing`, `model_storage_unavailable`): **nunca se sustituye por otro modelo**.

**Memoria.** Los modelos cargados se guardan en un LRU por SHA-256 (`VISION_MODEL_CACHE_SIZE`, por defecto 2). Cada sesión guarda su SHA-256 y no se recarga nada por fotograma; dos sesiones con modelos distintos conviven. Un modelo expulsado del LRU se vuelve a verificar y cargar.

**Clases.** Al cargar, los `names` de los pesos deben coincidir con `yolo_model.class_names` y cada clase del `class_map` debe existir en el modelo; si no, 422 `model_classes_incompatible`.

**Trazabilidad de los conteos.** El estado del worker, el resultado de cada fotograma y los mensajes WS (`worker.status`, `detections.result`) incluyen `model_sha256`. `fetch_state` (la vía de **todos** los registros de conteo) y `fetch_state_quick` (evidencia de los *checkpoints*) rechazan el estado si ese SHA-256 no es el de la sesión. Así cada `count_event` queda ligado a pesos verificados: `work_session.yolo_model_id → yolo_model (checksum, version_tag, model_class)`.

**WebSocket.** El cliente no elige modelo: el ticket HMAC identifica la sesión y el estado en vivo copia la identidad del modelo del *bind*.

**Compatibilidad:**
- El modelo semilla `yolo26l-demo` no tiene archivo de pesos y su checksum es ficticio, así que **ya no sirve para inferir**. Administración debe subir y activar los pesos reales (RF-AD-08).
- Las sesiones cerradas sin modelo no se tocan.
- Una sesión en curso sin modelo, iniciada antes de MS-02, recibe el modelo activo en su primer *bind*, con auditoría.

## Revocación de la vista previa en vivo (MS-03)

- **Ticket:** `<session_id>.<user_id>.<exp>.<nonce>.<hmac>` (`VISION_WS_TICKET_TTL`, por defecto 120 s). El portal lo emite solo a una petición cuyo JWT AuthService acepta.
- **Control en el worker:** comprueba la autorización en cada mensaje y con un pulso de 1 s, aunque el cliente no envíe nada. Si el ticket caduca sin renovarse, deja de inferir, envía `error` con `session_expired` y cierra la conexión.
- **Renovación:** la página del Operador (`capture-live-ws.js`) renueva cada `ttl/3` con `session.renew`, usando un ticket nuevo de la **misma** sesión y el **mismo** usuario. Si es de otro usuario o no es válido, responde `unauthorized` y cierra.
- **Revocación inmediata:** `POST /auth/revoke` (firmado con HMAC como los *binds*) marca todas las conexiones del usuario; cada una se cierra en menos de 1 s con `revoked` y no recibe más resultados. El portal la llama, sin bloquear nunca la operación, al **desactivar** o **restablecer la contraseña** de un usuario (`admin_local.auth_sessions`) y al **cerrar sesión**.
- **Cliente:** ante `unauthorized`, `revoked` o `session_expired`, o si no obtiene un ticket nuevo, deja de enviar fotogramas, cierra la cámara y muestra "Live preview stopped: your authorization is no longer valid".
- **Cierre de sesión del portal:** ahora envía también el *refresh token* a AuthService, que ya revocaba el token "hermano" que recibe. Antes ese token seguía válido en el servidor hasta caducar.
- **Reglas de conteo:** no cambian. La vista previa no registra conteos; los conteos exigen siempre un JWT válido.

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

## MS-11: cámara de la estación, tracking y reconexión

- **Configuración de la cámara** (Administración: resolución, FPS, ROI; tipo `simulated` para fuentes de prueba). Llega a VisionWorker en el *bind* firmado y se vuelve a enviar al elegir la cámara. Una fuente simulada se marca `simulated: true` y **nunca** valida hardware. La calibración no está definida en los RF y no se implementó.
- **Tracking** (`GUIA_TRACKING.md`): IDs estables, recuperación tras oclusión, cambios de clase y eventos de ROI. No es un conteo.
- **Reconexión de la vista previa** (`capture-live-ws.js`):
  - Ante una pérdida inesperada del WebSocket (reinicio del worker, red), reconecta con espera exponencial (1–15 s, 6 intentos), con un **ticket nuevo** cada vez.
  - Un *watchdog* trata como pérdida un fotograma sin respuesta en 10 s.
  - **Nunca** reconecta tras una revocación, expiración o error de autorización, ni cuando el Operador detiene la vista previa.
  - El ticket de reconexión (`?bind=1`) vuelve a vincular la sesión, porque tras reiniciarse VisionWorker olvida las sesiones en memoria. Si el worker sigue caído, responde 503 y se reintenta.
