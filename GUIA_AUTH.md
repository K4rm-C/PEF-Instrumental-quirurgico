# PEF — Auth / Identity Service integrado con el portal

BackendAuthService (`apps/BackendAuthService`, puerto 5001) es el microservicio oficial **Auth / Identity**. Ya autenticaba (login, refresh, logout, JWT y sesiones Redis). Desde esta etapa también es dueño de **usuarios, roles, asignación de roles y contraseñas**. Las pantallas RF de Administración (Usuarios y Roles) conservan su aspecto y comportamiento, pero leen y escriben esos datos por HTTP.

- Contrato: [DS09 §1.3.2](data/DataStandards/PEF-DS09-API-Exchange-Contracts.md), complemento v0.1.4.
- Clave Redis nueva: [DS01 §1.3.5](data/DataStandards/PEF-DS01-Identity-and-Credentials.md).
- Documentación ejecutable: `http://127.0.0.1:5001/api/v1/auth/docs`.

## 1. Flujo

```
Pantalla RF Usuarios / Roles ─▶ admin_routes ─▶ facade ─▶ identity_remote ─HTTP JSON + Bearer─▶ AuthService /api/v1/auth ─▶ PostgreSQL ("user", user_role, role, access_audit) + Redis
Inicio de sesión (todos los roles, Operador incluido) ─▶ AuthService /login, /verify, /refresh, /logout (cookies HttpOnly, igual que antes)
Operador ─▶ rf_session (local, PostgreSQL) — solo usa AuthService para autenticarse
```

- **Siempre por AuthService.** El portal ya dependía de AuthService para iniciar sesión, así que usuarios y roles no tienen modo local ni respaldo.
  - Se eliminaron `admin_local/users.py`, `admin_local/roles.py`, sus rutas internas y la lectura local del catálogo `roles`.
  - El portal no ejecuta `INSERT`, `UPDATE` ni `DELETE` sobre `"user"`, `user_role` o `role`. La prueba `tests/test_identity_remote.py` lo vigila con un hook de SQLAlchemy.
  - Si AuthService no responde, la pantalla muestra "El servicio de identidad no está disponible temporalmente" (503) y no se escribe nada.
- **Identidad.** La Web envía el token de acceso que AuthService ya verificó para esa petición, en `Authorization: Bearer`, junto con `X-Correlation-ID`. AuthService vuelve a validar:
  - firma, algoritmo y expiración del JWT;
  - sesión Redis presente y no revocada;
  - usuario e institución activos;
  - rol `it_admin` efectivo de la institución del usuario.

  Nunca se acepta una cabecera propia como identidad.
- **Presentación.** `services/admin_local/identity_remote.py` devuelve las mismas estructuras que los módulos locales anteriores. Filtros, búsqueda y paginación siguen en la Web; las reglas de negocio, transacciones, auditoría y revocación están en AuthService.

## 2. Comparación de capacidades

| Capacidad | AuthService antes | `admin_local` (monolito) | AuthService ahora |
| :--- | :--- | :--- | :--- |
| Login, refresh con rotación, logout, JWT, sesiones Redis, rate limit | Sí | — | Sí, sin cambios de contrato |
| Perfil autenticado (`/me`) y permisos (`/permissions`) | Sí | — | Sí |
| Alta de usuario | Solo `/register`, sin roles, auditoría ni validaciones de Admin | Sí, con validaciones | `POST /users` con las mismas validaciones, más institución activa |
| Consulta y edición de usuarios | No | Sí | `GET /users`, `GET/PUT /users/{id}` |
| Activar y desactivar | No | Sí, pero el token seguía vivo en Redis | Sí, y revoca todas las sesiones Redis del usuario |
| Restablecer contraseña (administrador) | No | Sí, sin revocar sesiones | `POST /users/{id}/password` revoca sesiones |
| Cambiar la propia contraseña | Ruta del portal `/password/change`, sin auditoría | — | `POST /api/v1/auth/password/change`: auditoría y cierre de las demás sesiones |
| Roles: listar, crear, editar descripción, borrar | No | Sí | `/roles`, `/roles/{id}` |
| Asignar roles | No | En la edición de usuario | En `PUT /users/{id}` y en `PUT /users/{id}/roles` |
| Protecciones de roles y administradores | No | Roles de sistema y en uso; no auto-desactivarse; último admin (cálculo global) | Igual, pero el último admin se cuida **por institución**, con *advisory lock* |
| Auditoría `access_audit` | No | Sí | Sí, mismos nombres de acción, más `CHANGE_OWN_PASSWORD`; intentos denegados como `DENIED_*` |
| Roles efectivos en las rutas del portal | Todos los del usuario, incluidos los de otra institución | — | Solo los de la institución del usuario (como `/api/v1`) |
| Institución desactivada | Solo `/api/v1` la rechazaba | — | También `/verify` y `/refresh` del portal |
| JSON/XML, XSD, OpenAPI | 5 endpoints; archivo OpenAPI | — | Todos; `schemas/identity.xsd` valida el XML recibido; OpenAPI servido con Swagger UI |
| Docker | No había imagen | — | `Dockerfile` y servicio `auth` en `docker-compose.yml` |
| Alta pública `POST /register` | Abierta, sin roles ni auditoría | — | Deshabilitada (404) salvo `AUTH_ENABLE_PUBLIC_REGISTER=true` |

## 3. Reglas que se conservan

Se conservan los mismos códigos de error que ya mostraban las pantallas:

- **Nombre:** obligatorio, máximo 160 caracteres.
- **Correo:** obligatorio, válido y único sin distinguir mayúsculas; se guarda en minúsculas.
- **Institución:** debe existir.
  - Regla nueva: debe estar activa al dar de alta o al cambiar de institución.
- **Roles:** al menos uno, todos de esa institución.
- **Contraseña:** mínimo 8 caracteres; la confirmación debe coincidir.
- **Código de rol:** `^[a-z][a-z0-9_]*$`, único por institución.
- **Borrado de roles:** los roles de sistema no se borran, ni un rol asignado.
- **Administradores:**
  - nadie se desactiva ni se quita `it_admin` a sí mismo;
  - ninguna operación deja a una institución sin un `it_admin` activo.

Alcance, igual que en el monolito: el administrador gestiona usuarios y roles de todas las instituciones (el formulario elige la institución), y un rol nuevo se crea en su propia institución.

**Efecto inmediato.**
- Desactivar un usuario, moverlo de institución o restablecer su contraseña marca `revoked=true` en todas sus claves `auth:jwt:{jti}`. Para encontrarlas usa el índice `auth:user_tokens:{user_id}`.
- Además, cada petición (Auth, Catalog, portal) comprueba en PostgreSQL que usuario e institución estén activos. Así, un token emitido antes de este índice también deja de funcionar.
- Los cambios de roles valen desde la petición siguiente, sin nuevo login, porque los roles se leen de PostgreSQL en cada `/verify` y en cada servicio.

## 4. Lo que sigue en la Administración local

- Procedimientos, modelos de visión y configuración (institución, quirófanos, estaciones).
- Bitácora y dashboard: lecturas SQL locales, incluidos los conteos de usuarios y roles del dashboard.
- Lista de instituciones del formulario de usuario (lectura).
- Familias, instrumentos y kits van a CatalogService cuando `CATALOG_SERVICE_URL` está configurado (ver [GUIA_CATALOG.md](GUIA_CATALOG.md)).

## 5. Configuración y Docker

| Variable | Dónde | Uso |
| :--- | :--- | :--- |
| `AUTH_SERVICE_URL`, `AUTH_SERVICE_TIMEOUT` | BackendWebFlask | Auth para el portal y la Administración (`http://127.0.0.1:5001`, 5 s) |
| `AUTH_PORT` | compose | Puerto publicado del contenedor `auth` (5001) |
| `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET_KEY`, `JWT_ALGORITHM` | compose → auth | Los mismos que usa Catalog (el JWT debe coincidir) |
| `ACCESS_TOKEN_MINUTES`, `SPD_*`, `OP_*`, `AUTH_COOKIE_*`, `AUTH_ALLOWED_ORIGINS`, `AUTH_RETURN_RESET_TOKEN` | compose → auth | Igual que en la ejecución local; en compose, `AUTH_RETURN_RESET_TOKEN` vale `false` por defecto |

```powershell
docker compose up -d --build postgres redis auth catalog
curl http://127.0.0.1:5001/health/ready      # {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}
# Swagger UI: http://127.0.0.1:5001/api/v1/auth/docs  (Authorize con el access_token de POST /api/v1/auth/login)
```

Auth puede correr en Docker o en local (`python main.py`) en el mismo puerto, pero no los dos a la vez. No se crearon tablas ni volúmenes. La imagen usa gunicorn, un usuario sin privilegios y un *healthcheck* sobre `/health/ready`.

## 6. Pruebas

```powershell
cd apps/BackendAuthService; python -m pytest -q    # contratos v1 + administración de identidad (PostgreSQL/Redis reales)
cd apps/BackendWebFlask; python -m pytest -q       # incluye tests/test_identity_remote.py (adaptador)
# End to end con servicios vivos (Web :5000, Auth :5001, compose postgres/redis[/catalog]):
python scripts/e2e_identity.py                      # env: DATABASE_URL, REDIS_URL, JWT_SECRET_KEY
```

El E2E cubre:
- login de los usuarios demo;
- alta, edición, roles, desactivación, reactivación y restablecimiento desde las pantallas RF;
- revocación comprobada leyendo las claves de Redis;
- tokens expirados, inválidos y falsificados, y usuarios sin permiso;
- JSON y XML validados con el XSD;
- auditoría en PostgreSQL;
- el Operador trabajando, incluida la renovación de su sesión.

Crea sus propios usuarios y una institución de prueba; no cambia las contraseñas demo.
