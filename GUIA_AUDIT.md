# PEF — Audit / Event Service

AuditService (`apps/AuditService`, puerto 5006) centraliza la **consulta** de la auditoría y la trazabilidad del PEF sobre los registros reales que ya existen en PostgreSQL.

- No crea una segunda bitácora: lee `access_audit` (DS07) y `count_event` (DS04).
- Es de solo lectura: el historial es inmutable.
- Contrato: [DS09 §1.3.6](data/DataStandards/PEF-DS09-API-Exchange-Contracts.md), complemento v0.1.0.
- Documentación ejecutable: `http://127.0.0.1:5006/api/v1/audit/docs`.

## 1. Auditoría existente (análisis)

| Tabla | Qué guarda | Quién la escribe (en su propia transacción) |
| :--- | :--- | :--- |
| `access_audit` | Acciones de usuarios: actor, acción, recurso, institución, resultado (`success`/`denied`), correlation id, IP | AuthService (usuarios, roles, contraseñas, **LOGIN / LOGIN_FAILED / LOGOUT**), CatalogService (familias, instrumentos, kits), SessionService (programación, inventario, `instruments_added`, resolución, cierre SPD, privacidad, `DENIED_*`), EvidenceService (`evidence.upload`, `evidence.download_url`), portal (procedimientos, modelos, configuración; Operador: `session.start`, `session.manual_close`, `session.contingency_close`) |
| `count_event` | Hechos del conteo: `session_open`, `auto_count` (inicial, `stable_window`, final), `close_blocked`, `manual_count`, `manual_close`, `instruments_added`, `session_close`, `phase_change` | Portal (flujo del Operador) y SessionService (incorporaciones, cierre) |

**Clasificación usada en las consultas** (derivada en SQL, filtrable):
- **`security`:** identidad y acceso. Incluye sign-in, usuarios y roles, contraseñas, denegados y lecturas de evidencias.
- **`business`:** decisiones administrativas y del Supervisor. Incluye catálogo, configuración, programación, inventario, resolución y cierre.
- **`operation`:** el flujo del conteo. Incluye inicio, cierre del Operador, captura de evidencias y todos los `count_event`.
- **`source_service`:** el dominio dueño de la fila. Ninguna tabla registra qué proceso la escribió.

**Campos necesarios para las consultas.** Fecha, actor (y su nombre y roles efectivos), acción, recurso y su etiqueta legible, institución, resultado, correlation id, IP y, para los conteos, cantidades, familia y claves del hecho.

**Lo que no se expone nunca:**
- de los conteos: cajas, imágenes, notas ni justificaciones de texto libre;
- en general: tokens, contraseñas o hashes (ninguna tabla de auditoría los guarda) ni datos del paciente.

**Hallazgo corregido.** La bitácora RF del Administrador leía `access_audit` de **todas** las instituciones. Con AuditService queda limitada a la institución del administrador.

## 2. Arquitectura

```
Productores (Auth, Catalog, Session, Evidence, portal/Operador) ──INSERT en su transacción──▶ PostgreSQL (access_audit, count_event)
                                                                                                   │ SELECT (READ ONLY)
Bitácora RF Admin / Supervisor ─▶ services/audit_remote.py ─HTTP + Bearer─▶ AuditService /api/v1/audit
```

- **Sin publicación de eventos ni endpoint de inserción.** Los registros transaccionales que ya funcionan se conservan. Un bus o una inserción por HTTP agregaría puntos de falla y riesgo de duplicados sin resolver un caso real.
- **El Operador no depende de AuditService.** Ningún productor lo llama. Con AuditService detenido, el conteo, el inicio de sesión y todas las escrituras siguen guardando sus eventos. Al reiniciarlo, esos eventos aparecen sin pérdidas ni duplicados (lo demuestra el E2E).
- **Inmutable.** Las consultas corren en `SET TRANSACTION READ ONLY`, así que PostgreSQL rechaza cualquier escritura. La única escritura del servicio es la fila `DENIED_GET_AUDIT_EVENT` de un acceso denegado, en su propia transacción.

## 3. Seguridad y alcance

Validación de cada petición:
- JWT con firma (exactamente `JWT_ALGORITHM`), expiración y `type=access`;
- sesión Redis `auth:jwt:{jti}` presente y no revocada;
- usuario e institución activos;
- roles efectivos de su institución.

Ninguna cabecera propia es identidad.

Alcance, aplicado en cada consulta SQL sin importar lo que pida el cliente:

| Rol | Ve |
| :--- | :--- |
| `it_admin` | Todos los eventos de su institución (con correo del actor e IP) |
| `spd_supervisor` | Traza operativa de su institución: sesiones, instrumentos, kits y todos los `count_event` (sin correo) |
| Operador y otros | 403, registrado como `DENIED_GET_AUDIT_EVENT` |

- `institution_id` de otra institución → 403 `out_of_scope`.
- Un evento fuera del alcance → 404, igual que uno inexistente.

## 4. Bitácoras RF

`/admin/audit-log` y `/supervisor/audit-log` conservan su apariencia, filtros y paginación, pero ahora consultan AuditService cuando `AUDIT_SERVICE_URL` está configurado:
- La fecha local de los filtros se convierte a un rango UTC.
- Las opciones de los filtros salen de `GET /events/summary`.

No hay respaldo silencioso: si AuditService no responde, las pantallas muestran "El servicio de auditoría no está disponible temporalmente" (503). Sin `AUDIT_SERVICE_URL` se usan los lectores locales (modo de desarrollo).

## 5. Configuración, Docker y pruebas

| Variable | Dónde | Uso |
| :--- | :--- | :--- |
| `AUDIT_SERVICE_URL`, `AUDIT_SERVICE_TIMEOUT` | BackendWebFlask | `http://127.0.0.1:5006` |
| `AUDIT_PORT` | compose | Puerto publicado |
| `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET_KEY`, `JWT_ALGORITHM` | compose → audit | Los mismos de los demás servicios |

```powershell
docker compose up -d --build postgres redis auth audit
curl http://127.0.0.1:5006/health
cd apps/AuditService; python -m pytest -q          # PostgreSQL/Redis reales (AUDIT_TEST_DATABASE_URL / AUDIT_TEST_REDIS_URL)
python scripts/e2e_audit.py <carpeta_videos>       # servicios vivos; detiene y reinicia el contenedor audit
```

No se crearon tablas, columnas ni volúmenes.
