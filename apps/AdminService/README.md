# AdminService

Microservicio de administración. Es dueño de los catálogos y la configuración que gestiona el administrador de TI:

- familias de instrumentos e instrumentos
- kits con su composición
- procedimientos con sus kits y fases de conteo
- usuarios y roles
- modelos de visión (YOLO) y su mapeo de clases
- institución, quirófanos y estaciones de captura

También expone el dashboard del administrador y la bitácora de auditoría. Cada escritura queda registrada en `access_audit`, en la misma transacción que el cambio.

El portal web (`apps/BackendWebFlask`) ya no toca esas tablas directamente: todas las pantallas de administración, más los catálogos de kits e instrumentos del supervisor SPD, llaman a este servicio con `clients/admin_service.py`.

## Arranque

Desde la raíz del repo, con los contenedores arriba (`docker compose up -d`):

```powershell
copy .env apps\AdminService\.env
cd apps\AdminService
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Comprueba: [http://127.0.0.1:5003/health](http://127.0.0.1:5003/health) debe responder `"ok": true` con `postgres` y `redis` en `true`.

| Variable | Default | Uso |
| --- | --- | --- |
| `ADMIN_SERVICE_PORT` | `5003` | Puerto del servicio (el `.env` compartido usa `PORT` para Auth) |
| `ADMIN_SERVICE_URL` | `http://127.0.0.1:5003` | URL que usa el portal web |
| `JWT_SECRET_KEY`, `JWT_ALGORITHM` | — | Los mismos que BackendAuthService (solo verifica tokens) |
| `REDIS_URL` | — | Sesiones JWT de Auth (`auth:jwt:{jti}`); un token revocado deja de servir |
| `DATABASE_URL` | — | PostgreSQL `InstruMed` |
| `ADMIN_DISPLAY_TIMEZONE` | `America/Mexico_City` | Hora mostrada y filtro por día en la bitácora |

## Seguridad

- Cada petición lleva `Authorization: Bearer <access_token>` emitido por BackendAuthService.
- El servicio valida:
  - la firma y la expiración del token;
  - que sea de tipo `access`;
  - que la sesión exista en Redis y no esté revocada;
  - que el usuario y su institución estén activos.
- Los permisos salen de los roles guardados en PostgreSQL, nunca de lo que diga el cliente:

| Rol | Acceso |
| --- | --- |
| `it_admin` | Todo |
| `spd_supervisor` | Instrumentos y kits (en la lista de kits solo ve los activos) y catálogos de lectura |
| `station_operator` | Nada |

- Un intento sin permiso responde `403` y queda auditado con `outcome = denied`.

## API (`/api/v1`)

| Recurso | Endpoints |
| --- | --- |
| Sesión | `GET /me` |
| Catálogos | `GET /catalogs?names=categories,cycle_statuses,phases,procedure_types,institutions,roles,families,active_families,kits,operating_rooms` |
| Dashboard | `GET /dashboard` |
| Familias | `GET/POST /families`, `GET/PUT /families/{id}` |
| Instrumentos | `GET/POST /instruments`, `GET/PUT /instruments/{id}` |
| Kits | `GET/POST /kits`, `GET/PUT /kits/{id}` |
| Procedimientos | `GET/POST /procedures`, `GET/PUT /procedures/{id}` |
| Usuarios | `GET/POST /users`, `GET/PUT /users/{id}`, `POST /users/{id}/password`, `POST /users/{id}/deactivate`, `POST /users/{id}/activate` |
| Roles | `GET/POST /roles`, `GET/PUT/DELETE /roles/{id}` |
| Modelos de visión | `GET/POST /vision-models`, `GET/PUT /vision-models/{id}`, `POST /vision-models/{id}/activate` |
| Institución | `GET/PUT /institution` |
| Quirófanos | `GET/POST /operating-rooms`, `GET/PUT /operating-rooms/{id}` |
| Estaciones | `GET/POST /capture-stations`, `GET/PUT /capture-stations/{id}` |
| Auditoría | `GET /audit-events?search=&user=&action=&entity=&outcome=&date=YYYY-MM-DD` |

- Las listas aceptan `search`, `status` (`active`, `inactive`, `all`), `page` y `per_page` (10, 20 o 50).
- Los errores responden `{"error": {"code", "message", "field"}}`. El portal traduce el `code`.

## Reglas de negocio

- **Nada se borra:** usuarios, familias, instrumentos, kits, quirófanos y estaciones se desactivan.
- **Roles:**
  - la tabla `role` no tiene columna `active`, así que un rol se elimina solo si nadie lo tiene asignado;
  - los tres roles del sistema nunca se eliminan.
- **Kits:**
  - cualquier cambio (nombre, composición o estado) crea una versión nueva y desactiva la anterior; los procedimientos pasan a apuntar a la nueva versión;
  - guardar sin cambios no crea versión;
  - las sesiones ya iniciadas conservan su `expected_inventory` congelado;
  - un kit debe tener al menos una familia.
- **Instrumentos:**
  - cambiar el estado de ciclo o el estado activo exige un motivo;
  - cada cambio deja un registro en `instrument_cycle_event`.
- **Usuarios:**
  - el administrador no puede desactivarse ni quitarse el rol `it_admin` a sí mismo;
  - siempre queda al menos un administrador activo;
  - las contraseñas se guardan solo como hash Werkzeug, el mismo esquema que valida Auth.
- **Modelos de visión:**
  - solo uno está activo;
  - la etiqueta de versión es inmutable;
  - el mapeo de clases del modelo activo no se puede editar (para cambiarlo se registra una nueva versión).
- **Institución:** no se puede desactivar la institución propia, porque bloquearía todas sus cuentas.

## Auditoría

Las acciones siguen el formato `VERBO_ENTIDAD`. Ejemplos:

- `CREATE_INSTRUMENT_FAMILY`, `UPDATE_INSTRUMENT`, `DEACTIVATE_INSTRUMENT`
- `CREATE_KIT`, `UPDATE_KIT`, `SUPERSEDE_KIT`
- `CREATE_PROCEDURE`
- `CREATE_USER`, `CHANGE_USER_ROLES`, `RESET_USER_PASSWORD`, `DEACTIVATE_USER`
- `CREATE_ROLE`, `DELETE_ROLE`
- `CREATE_VISION_MODEL`, `ACTIVATE_VISION_MODEL`
- `CREATE_OPERATING_ROOM`, `UPDATE_CAPTURE_STATION`, `UPDATE_INSTITUTION`

Auth registra `LOGIN` (éxito o `denied`) y `LOGOUT`.

Cada fila guarda:

- quién hizo la acción y su institución;
- la fecha y hora;
- la entidad y el registro afectado;
- el resultado;
- la IP del navegador, que el portal reenvía en `X-Forwarded-For`;
- un `correlation_id`.

## Indicadores del dashboard

Todos salen de PostgreSQL. Si todavía no hay datos, el portal muestra "Sin datos aún" y no inventa un valor.

- **Coincidencia IA-humano:** porcentaje de conteos por familia, capturados en sesiones con visión (`count_event` de tipo `manual_count` con `payload.ai = true`), que no necesitaron corrección humana.
- **Tiempo medio de resolución:** promedio entre el evento que originó cada discrepancia (o el inicio de la sesión) y su `resolved_at`.

## Pruebas

```powershell
cd apps\AdminService
python -m pytest tests                     # requiere PostgreSQL con 001 + seeds; Redis se simula
cd ..\..
python scripts\smoke_admin_service.py     # con Auth (5001) y AdminService (5003) corriendo
```
