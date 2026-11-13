# Auditoría ZIP3: WebSocket y cambios de base de datos

## Resultados de la revisión del ZIP consolidado anterior

- Sí: V1 como base activa, `admin_local` y MS de V1.
- Sí: modelos ORM nuevos del ZIP3 y migración `008_consolidacion_zip3.sql` para usuarios-quirófanos,
  arquitectura/tamaño de modelo YOLO, pin de modelo de sesión, campos de discrepancias, estado `or_hold`
  y formatos de media.
- No: faltaba el endpoint de tickets en Flask y la UI activa de cámara.
- Parcial: el código ZIP2 de supervisor y el tracking/CUDA ZIP3 estaban referenciados,
  no portados a SessionService y VisionWorker respectivamente.

## Cambios de esta revisión (v2)

1. Se incorpora al `BackendWebFlask` la ruta `GET /operator/sessions/{id}/capture/ws-ticket`
   desde ZIP3 con autorización de operador, comprobación de institución y sesión activa.
2. Se incorpora el módulo de cámara WebSocket adaptado al contrato ZIP3 (`session.bind`,
   `frame.submit`, `detections.result`), selección de cámara y render de frames anotados.
3. Se habilitan `VISION_WS_SECRET`, `VISION_WORKER_WS_URL` y `VISION_WS_ORIGINS` en Docker y `.env.example`.
4. Se aísla el estado del streaming del estado de video y conteos de V1: nunca permite
   validar/cerrar una sesión por detecciones de vista previa.
5. Se conserva en `Legacy/porting_sources` el procesador CUDA/tracking del ZIP3 como fuente
   para futura adopción controlada; no se afirma que esté ejecutándose.

## Pruebas pendientes

- Verificar docker-compose con variables y migración 008 en PostgreSQL real.
- Arrancar VisionWorker con `best.pt` y observar vídeo de cámara/frames anotados por WebSocket.
- Comprobar revocación de tickets al cerrar sesión, controles por institución, TLS/WSS para clientes remotos.
- Probar 15→14 rechazo; 15→15 permitir; recaptura final. V1 conserva una ruta
  de aceptación manual de diferencia y **no cumple todavía** esa regla estricta.
- Comparar endpoint a endpoint ZIP2 vs SessionService antes de portar código en la etapa de microservicios.

**No se presenta esta entrega como versión final probada.**

## Pruebas locales ejecutadas durante la consolidación

- `python scripts/check_ws_integration_offline.py`: tres verificaciones PASS (firma HMAC entre backend/worker; aislamiento del conteo de video en sesión 15; rechazo de bind live si video en procesamiento).
- `node --check apps/BackendWebFlask/static/js/pages/capture-live-ws.js`: sintaxis válida.
- AST Python: 403 módulos comprobados sin error de análisis sintáctico.
- ZIP probado con `ZipFile.testzip()`: sin entradas corruptas.

Estas comprobaciones usan simulaciones y **no sustituyen** pruebas en navegador,
Docker Compose, cámaras, bases de datos o inferencia de un `best.pt` real.
