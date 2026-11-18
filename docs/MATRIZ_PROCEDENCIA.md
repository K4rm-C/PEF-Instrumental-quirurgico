# Inventario y procedencia de la versión consolidada

| Origen | Qué quedó activo | Qué se preservó como referencia | Motivo |
|---|---|---|---|
| ZIP1 microservices_v1 | `apps/BackendWebFlask`, `services/admin_local`, Auth, Catalog, Session, Evidence, Audit, Notifications, Monitoring, VisionWorker, Docker, pruebas, Mongo y guías | — | Mayor cobertura funcional, separación segura de Supervisor y validaciones del Operador |
| ZIP2 Supervisor | El trabajo aporta contratos y escenarios a revisar | `Legacy/porting_sources/SupervisorService_zip2` | Se superpone con SessionService y no debe existir un segundo escritor de sesiones |
| ZIP3 main | `data/FunctionalRequirements`, modelos ORM de usuarios-quirófanos, YOLO, sesiones y discrepancias; `008_consolidacion_zip3.sql`, estándares DS07/08/10 y ER v3 | `Legacy/porting_sources/AdminService_zip3` y `VisionRealtime_zip3` | AdminService depende de HTTP y no respeta arquitectura objetivo; captura live difiere del protocolo seguro primera/última de V1 |

## Decisiones de no sobreescritura

- Las rutas, templates y controladores de V1 se conservaron: en ZIP3 hay regresión de tenant guards, auditoría, reportes, conteo de referencia e integración Mongo.
- El Administrador de V1 es `admin_local`, pero **aún llama a Auth/Catalog/Audit remotos**: esto se corrige en etapa 2, no se declara resuelto.
- El código live del ZIP3 se conservó para portar y probar; la captura activa sigue protegida por reglas V1.
- Se habilitó el endpoint WebSocket de **vista previa** `/ws` en VisionWorker V1 (HMAC/Origin); por ahora su resultado no sustituye el conteo definitivo de inicio/final ni el flujo aprobado de validación.
- Los puertos de V1 se mantienen temporalmente para no romper la integración. Puertos del profesor se adoptan con refactor posterior.
- `Legacy/` se conserva íntegramente y contiene los fuentes adicionales; no se eliminó.
- `ui_reference` y `ui_reference_v2` se omitieron para reducir el ZIP; se conserva `ui_reference_v3`.
- `infra/garage.toml` local se omitió por configuración sensible/no portable; existe `infra/garage.toml.example`.

**Pendiente de validación:** esta entrega es una consolidación de código y estructura; no equivale a haber certificado las 20 responsabilidades ni a haber ejecutado la app contra todos los servicios reales.

**Advertencia:** conservar el flujo V1 no significa que sea conforme a la regla física final: `confirm_final_count` acepta una diferencia con acuse; se requiere corrección y nuevo flujo de recaptura final, indicado en el plan.

## Actualización de revisión WS v2

Se conectó cámara del navegador, ticket HMAC autorizado por sesión/institución,
`VisionWorker /ws` y endpoint de preview no autoritativo. Estado aislado para no
sobreescribir la referencia V1. Ver `docs/REVISION_WS_Y_BD_ZIP3.md`.
La inferencia CUDA y tracking especializado ZIP3 siguen pendientes de integración;
no hay pruebas con hardware, cámara ni `best.pt` real.
