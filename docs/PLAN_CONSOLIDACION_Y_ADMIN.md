# Plan ejecutable — consolidación de 3 ZIP y cierre del Administrador

## Alcance y criterio de aceptación

**Fuera de alcance: RF-AD-11.** No desarrollar la vista ni el flujo de solicitudes ARCO. Se conservan tablas y documentación histórica si otros módulos las requieren; no constituyen una tarea abierta.

**Orden obligatorio:**
1. Integración ZIPs (esta entrega: base consolidada, sin alterar las reglas existentes de conteo).
2. Completar RF-AD-01 … RF-AD-10 en `apps/BackendWebFlask/services/admin_local`, sin AdminService activo, y sin llamadas de negocio desde Administración a servicios remotos.
3. Cerrar microservicios del Supervisor, fusionando responsabilidades sin desplegar necesariamente 20 procesos.

## Etapa 1 — Consolidación de versiones

- [x] Adoptar ZIP1 como núcleo: Auth, Catalog, Session, Evidence, Audit, Notifications, Monitoring, VisionWorker, UI Flask y pruebas.
- [x] Traer requerimientos y nuevos modelos del ZIP3 (`UserOperatingRoom`, `YoloModel`, `Discrepancy`, `WorkSession`).
- [x] Añadir migración incremental 008 con campos y eventos nuevos sin borrar 004–007.
- [x] Recuperar implementación de SupervisorService del ZIP2 como fuente, sin desplegarla paralelamente a SessionService.
- [x] Recuperar lógica AdminService ZIP3 como fuente de portabilidad, sin desplegarla.
- [x] Conservar funcionalidades avanzadas de VisionWorker V1 (conteo primera/última captura, Mongo) y habilitar infraestructura WS de vista previa; retener pipeline CUDA y tracking ZIP3 como código a portar.
- [ ] Correr migración en PostgreSQL de prueba y comprobar modelos/tablas reales.
- [ ] Ejecutar E2E con PostgreSQL + Redis + MongoDB + Garage + YOLO/best.pt.
- [ ] Comparar rutas exclusivas de SupervisorService ZIP2 contra SessionService y portar únicamente las ausentes.
- [ ] Validar el flujo UI de Operador y Supervisor de principio a fin después de reconciliar estados `or_hold`.

### Migraciones

**Base nueva:** Docker Compose monta 001, seeds, 004–008 en `/docker-entrypoint-initdb.d/`, en orden.

**Base existente:** initdb **no vuelve a ejecutar** los .sql montados; realizar respaldo, verificar migraciones aplicadas y ejecutar **solo las pendientes**, en orden (004→005→006→007→008); por ejemplo:

```bash
docker compose exec -T postgres sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < data/migrations/008_consolidacion_zip3.sql
```

No ejecutar `docker compose down -v` sobre una BD con datos. No regenerar 001 para una BD existente.

### Infraestructura

`infra/garage.toml` del equipo fue excluido; crear a partir de `infra/garage.toml.example` antes del arranque. Configurar secretos reales en `.env` (no subir a git). Puertos activos por defecto siguen siendo los de V1; **la renumeración del profesor a 5301… queda para la etapa de microservicios**, después de cerrar Admin.

## Etapa 2 — Administrador en monolito (RF-AD-01 ... RF-AD-10)

- [ ] **A01 Dashboard:** salud de servicios/dependencias, KPIs por estado de sesión y métricas reales.
- [ ] **A02 Familias:** fotografías de referencia/family_example, validaciones al desactivar familias usadas por YOLO.
- [ ] **A03 Procedimientos:** CRUD de catálogos auxiliares (fases, categorías y motivos), restricciones entre tablas.
- [ ] **A04 Avisos de privacidad:** publicación, vigencia, versionado y archivo PDF/HTML.
- [ ] **A05 Usuarios:** asignación `user_operating_room`, seguridad y revocación Redis, sin endpoints de administración remotos.
- [ ] **A06 Roles:** mantener los códigos fijos definidos por RF; prohibir alta/baja arbitraria.
- [ ] **A07 Quirófanos/estaciones:** editar y validar configuración ROI.
- [ ] **A08 YOLO:** cargar `.pt`, extraer clases, calcular checksum, validar familias y publicación controlada.
- [ ] **A09 Auditoría:** filtros completos, incluido intervalo de fechas; lectura local dentro del monolito.
- [ ] **A10 Configuración:** umbrales KPI, MIME y tamaños máximos, valores institucionales.
- [ ] Desconectar `admin_local.identity_remote`, `admin_local.catalog_remote` y `audit_remote` **solo tras sustituirlos por lógica local con pruebas**. Login es dependencia técnica aparte a decidir explícitamente.
- [ ] Eliminar del Administrador mutaciones de instrumentos físicos y kits: su propietario funcional es Supervisor SPD.
- [ ] Pruebas de permisos y aislamiento institucional de cada formulario, y auditoría de mutaciones.

**A11 ARCO = excluido; no tiene desarrollo pendiente.**

## Etapa 3 — Microservicios del Supervisor

- [ ] Auditar endpoint a endpoint `Legacy/porting_sources/SupervisorService_zip2` frente a `apps/SessionService`.
- [ ] Consolidar SessionService como propietario de sesiones, supervisión, comparación, discrepancias y validación; evitar doble escritura.
- [ ] Completar cámara, streaming (flujo oficial con confirmación final), tracking, kits, métricas y modelos IA.
- [ ] Completar evidencias, auditoría, notificaciones, monitoreo y reportes Highcharts.
- [ ] Sincronización e integración hospitalaria solo según alcance verificado (contratos y adaptadores de prueba si no existen clientes externos).
- [ ] Ajustar puertos 5301–5320 y contratos API con el profesor; varios dominios pueden compartir proceso.

## Pruebas de aceptación

1. Un solo `docker compose up --build` para servicios activos de V1; AdminService y SupervisorService referenciales no arrancan.
2. Login, vistas y operaciones del Administrador se ejecutan mediante servicios Flask locales (cuando la etapa 2 esté cerrada).
3. Operador no realiza llamadas de negocio a SessionService; captura final diferente a referencia bloquea validación humana.
4. Supervisor usa microservicios de negocio; sesiones, discrepancias, permisos y tenant scope correctos.
5. No se presentan indicadores de demo como si fueran reales.
6. Las 20 responsabilidades tienen prueba o una limitación de alcance explícita.


## Bloqueador de seguridad funcional detectado en la revisión

**Corregido en MS-03.1 (2026-11-04, ver `docs/MS_DIAGNOSTICO_Y_PLAN.md` §13).** Texto original: `apps/BackendWebFlask/services/rf_session.py::confirm_final_count()` todavía contiene una vía `acknowledge_mismatch=True` que puede registrar un conteo final distinto a la referencia. Además, el flujo de un único video impide reemplazar libremente la captura final conservando el conteo inicial. **No pasar esta base como versión final de Operador.**

- [x] Eliminar la autorización de avanzar con diferencia física (también si la UI manda `acknowledge_mismatch=1`).
- [x] Permitir una segunda captura final sin alterar la referencia inicial ni su auditoría.
- [x] Pruebas de 15→14 (rechazo y nuevo intento), 15→15 (permite validación), 15+2→17 (referencia ajustada), elementos fuera del kit y concurrencia con Supervisor.
- [x] Resolver las pruebas anteriores de V1 que explícitamente esperan aceptación manual de la diferencia, ajustándolas al requisito confirmado.

Este bloqueador queda consignado desde la etapa 1, aunque el orden de implementación del equipo sea consolidación → Administración → microservicios. No se oculta ni se considera prueba superada.

## Actualización de revisión WS v2

Se conectó cámara del navegador, ticket HMAC autorizado por sesión/institución,
`VisionWorker /ws` y endpoint de preview no autoritativo. Estado aislado para no
sobreescribir la referencia V1. Ver `docs/REVISION_WS_Y_BD_ZIP3.md`.
La inferencia CUDA y tracking especializado ZIP3 siguen pendientes de integración;
no hay pruebas con hardware, cámara ni `best.pt` real.
