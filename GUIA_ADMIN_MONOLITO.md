# PEF — Administración RF integrada en BackendWebFlask

Fecha: 20 de octubre de 2026 · Migración de AdminService a monolito · Siguiente etapa: microservicios oficiales.

## 1. Resultado y límites

Las pantallas RF de Administrador se conservaron sin rediseño. Se migró la lógica completa del AdminService anterior (consultas, CRUD, validaciones, auditoría y manejo de errores) al paquete **`apps/BackendWebFlask/services/admin_local/`**. El administrador ahora consulta **directamente PostgreSQL por la sesión de Flask-SQLAlchemy**. No se realiza una petición HTTP a AdminService; tampoco se lanza el contenedor `admin`.

El portal verifica las cookies JWT con BackendAuthService mediante `controllers.routes._current_user()`. `admin_local/security.py` aprovecha **la identidad ya verificada**, con comprobación adicional de roles al entrar en las operaciones locales. AuthService y sus reglas JWT/Redis permanecen intactos. **No** se sustituyó AuthService por una autenticación local ni se añadió un AdminService nuevo.

Se conservaron las pantallas RF del Operador y Supervisor. Su futura integración con SessionService y CatalogService corresponde a una siguiente etapa: esta migración deja Admin como funcionalidad completa dentro del portal **antes** de redistribuir responsabilidades a los servicios oficiales.

## 2. Estructura y cambios

| Archivo | Uso |
|---|---|
| `controllers/admin_routes.py` | Rutas HTML RF, formularios y permisos web |
| `controllers/admin_presenters.py` | Formato de datos para las pantallas RF |
| `services/admin_local/api.py` | Catálogo de 47 operaciones internas locales (GET/POST/PUT/DELETE conceptuales, **no endpoints HTTP**) |
| `services/admin_local/router.py`, `facade.py` | Despachador directo desde rutas de Admin sin `requests` |
| `services/admin_local/{families,instruments,kits,procedures,users,roles,vision_models,configuration,...}.py` | Consultas, validaciones y operaciones de negocio trasladadas |
| `services/admin_local/models.py` | Mapeos de las mismas tablas de PostgreSQL, con metadata independiente para evitar duplicados de SQLAlchemy |
| `services/admin_local/database.py` | **Misma sesión PostgreSQL** del portal Flask (`extensions.db.session`), sin engine independiente |
| `services/admin_local/security.py` | Reutiliza la identidad que validó BackendAuthService |
| `services/admin_local/audit.py`, `audit_log.py` | Alta y consulta de eventos `access_audit` |
| `services/admin_local/messages.py` | Mensajes y códigos de error de negocio localizados |

Se eliminó `apps/AdminService`, el cliente HTTP `apps/BackendWebFlask/clients/admin_service.py`, variables `ADMIN_SERVICE_URL/PORT/TIMEOUT` y el contenedor AdminService de `docker-compose.yml`. El `AdminService` del ZIP **anterior** sigue disponible para comparar si alguna regla necesita ser revisada, pero **no se ejecuta ni se necesita**.

## 3. Cómo levantarlo (Windows)

1. Descomprimir el ZIP en una carpeta nueva y abrir terminal PowerShell allí.
2. Copiar `.env.example` a `.env` (`Copy-Item .env.example .env`) y definir credenciales `POSTGRES_*`, `DB_*`, `JWT_SECRET_KEY`, `REDIS_PASSWORD`, etc. No compartir `.env` ni sustituir las contraseñas con valores de ejemplo. El servidor de PostgreSQL publicado y la DB del portal tienen que referirse al mismo volumen y nombre de base.
3. Levantar infraestructura y servicios ya existentes: `docker compose config` seguido de `docker compose up -d --build`.
4. Iniciar `BackendAuthService` en puerto 5001, `BackendWebFlask` en 5000 y `VisionWorker` en 5002 con sus respectivos `requirements.txt`; alternativamente usar `start-services.ps1`. **No levantar AdminService.**
5. Acceder como usuario con rol `it_admin` a `http://127.0.0.1:5000/admin/dashboard`. Navegar a Familias, Instrumentos, Kits, Procedimientos, Usuarios, Roles, Modelos, Bitácora y Configuración.
6. Comprobar inserciones/modificaciones directamente en las tablas PostgreSQL (`instrument_family`, `instrument`, `kit`, `cat_procedure_type`, `user`, `role`, `yolo_model`, `operating_room`, `capture_station`, `access_audit`). Validar que **no existe** un contenedor `pef_admin` en `docker compose ps` de esta versión. Si quedó uno de la versión anterior, detenerlo por separado y no reutilizar su puerto.

### Puertos del proyecto

| Componente | Puerto | Ejecución |
|---|---:|---|
| BackendWebFlask (Operador, Supervisor, Admin RF) | 5000 | Python local |
| Auth / Identity | 5001 | Python local |
| VisionWorker | 5002 | Python local |
| CatalogService | 5003 | Docker |
| SessionService | 5004 | Docker |
| EvidenceService | 5005 | Docker |
| PostgreSQL | 5433 (host) / 5432 (Docker) | Docker |
| Redis | 6379 | Docker |
| MongoDB | 27017 | Docker |

## 4. Evidencia y pruebas

Pruebas de vistas que **simulan los resultados del dominio**:

```powershell
cd apps/BackendWebFlask
python -m pytest tests/test_admin_routes.py -q
```

Resultados de la ejecución de referencia: **15/15** pruebas de vistas RF aprobadas, sin HTTP a AdminService. Además se ejecutó `python scripts/smoke_admin_monolith_sqlite.py` desde la raíz del proyecto, un smoke aislado con SQLAlchemy + SQLite de prueba, verificando inserción y edición de familias, instrumentos, procedimientos y sus relaciones, kits, roles, usuarios, registro de modelo de visión, quirófano, estación de captura, auditoría de alta, errores 400 y 409, roles 403 y redirección al iniciar sesión. SQLite **no** es sustituto de PostgreSQL: consultas de auditoría y panel usan funciones específicas de PostgreSQL y están **pendientes de validación real**.

**Pruebas del profesor aún pendientes:** levantar PostgreSQL + AuthService reales, ejecutar todos los formularios en un navegador, evidenciar base de datos, errores, seguridad, JSON/XML según microservicios oficiales, Swagger, Docker y flujos de integración. No se realizaron pruebas integrales en este entorno por falta de Docker/PostgreSQL. La suite completa de tests de Operador/Supervisor también necesita PostgreSQL para sus fixtures.

## 5. Próxima etapa y regla de arquitectura

Admin queda dentro del monolito. En la etapa siguiente extraeremos responsabilidades por dominio a Auth, Catalog, Session, Evidence, Audit, Notifications y Monitoring, **sin crear un microservicio por rol**. El objetivo inmediato de este ZIP es hacer que la Administración RF funcione sin depender de un AdminService separado.
