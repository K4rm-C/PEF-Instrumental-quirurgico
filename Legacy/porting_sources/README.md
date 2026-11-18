# Código de referencia de integración — NO se ejecuta

- `SupervisorService_zip2/`: endpoints y servicios del ZIP2 a cotejar contra `apps/SessionService` de V1. No activar un segundo dueño de sesiones.
- `AdminService_zip3/`: implementación del ZIP3 a incorporar en `apps/BackendWebFlask/services/admin_local`. No volver a desplegar AdminService.
- `VisionRealtime_zip3/`: pipeline CUDA, tracking temporal y cámara WebSocket del ZIP3; **no está conectado** al flujo de video de V1. Comparar y portar mediante pruebas de regresión.

Se conserva aquí el código fuente sin copiar por duplicado todos los activos, infraestructura y versiones del repositorio. No se requiere ni se debe activar ninguno de estos servicios de referencia.
