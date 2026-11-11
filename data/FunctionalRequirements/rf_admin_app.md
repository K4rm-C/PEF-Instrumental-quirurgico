# Contrato funcional formal · Administrador TI y Supervisor de piso (App)

## Cómo leer este documento

Cada requisito funcional (RF) sigue la misma plantilla de diez puntos que [rf_operador_supervisor_spd.md](rf_operador_supervisor_spd.md): identificador, actor, llegada, UI, acciones del usuario, efectos en el sistema, almacenes, configuración, salida, errores.

**Convención de escritura en bases de datos:** igual que el contrato OP/SPD (Backend Web escribe PostgreSQL; vision-worker no es dueño de PG; evidence-worker escribe storage + Mongo; el cliente nunca abre JDBC).

Este contrato **no sustituye** el de operador/SPD. Lo complementa: Admin es dueño de familias, catálogos de selección, identidad, topología y modelos; la App es vigilancia de piso. El conteo en sala, **kits**, **piezas individuales** y el cierre de ciclo de material siguen en RF-OP-* y RF-SP-*.

---

## Glosario de este contrato


| Término en UI | Significado | Dónde vive |
| --- | --- | --- |
| Familia | Tipo de instrumento que se cuenta / detecta | `instrument_family` + `family_example` |
| Pieza | Unidad física de stock (**SPD**, no Admin) | `instrument` |
| Kit (receta) | Cantidades por familia, versionada (**SPD**, no Admin) | `kit` + `kit_item` + `procedure_kit` |
| Modelo activo | Detector que tomarán las **nuevas** sesiones al Start | `yolo_model.active = true` (un solo TRUE) |
| Pin de modelo | Detector **congelado** en una sesión ya iniciada | `work_session.yolo_model_id` |
| Hold de sala | Faltante confirmado por humano; SPD aún no entra | `cat_session_status.code = or_hold` |
| Resolución sugerida | Relato OR (encontrado / no encontrado / búsqueda) | `discrepancy.suggested_resolution` |
| Liberación a SPD | El **OP** envía el caso a review SPD | status `correction_required` + `or_released_at` |
| Asignación de sala | SdP ↔ quirófano | `user_operating_room` |
| Aviso (biblioteca) | PDF/HTML versionado del hospital | `privacy_notice_version` |
| Acuerdo de sesión | Ligar un aviso a **esta** sesión (sigue siendo SPD) | `session_processing_agreement` |


**RACI catálogo vs operación (v1)**

1. **Admin** crea, edita y **desactiva** familias (con examples), procedimientos/catálogos de selección, avisos, usuarios, roles, salas, estaciones y modelos. Lo desactivado se **oculta** a OP/SPD/App; Admin sí lo lista.
2. **SPD** da de alta **piezas** (`instrument`), mueve ciclo (`instrument_cycle_event`) y **crea/gestiona kits** (RF-SP-09). Usa familias y procedimientos de Admin al programar (RF-SP-02). No publica pesos.
3. No hay DELETE físico si hay FKs; solo `active = false`.
4. FHIR / `integration_client`: **considerado en plan, no diseñado**.

---

## Roles y acceso


| Código | Nombre | Cliente | Puede |
| --- | --- | --- | --- |
| `it_admin` | Administrador TI | Portal web Admin | Familias, catálogos, IAM, topología, modelos, audit de acceso, ARCO stub |
| `or_charge` | Supervisor de piso (SdP) | App | Ve todas las salas (las suyas primero); **notif solo** de salas ligadas; comenta holds; **no** cierra ciclo SPD ni edita catálogo |
| `station_operator` | Operador | Estación | Conteo; documenta hold y **libera a SPD** (RF-OP-07) |
| `spd_supervisor` | Supervisor SPD | Portal SPD | Programar, kits, piezas, review post-liberación, ciclo de material |


**Reglas fijas**

1. JWT/Redis según DS01. El Backend aplica el rol antes de escribir PostgreSQL.
2. Admin no valida conteos ni cierra `work_session` de negocio.
3. App no abre WSS al vision-worker. Live de sesiones = lecturas PG (poll o WS al Backend) + evidencias de video ya guardadas.
4. Listas de App: **todas** las salas, ordenadas con las de `user_operating_room` del SdP **primero**. **Notificaciones in-app solo** si el hold/sesión es de una sala ligada a ese usuario.

---

## Estados de sesión (cruce con OP/SPD)

Códigos en `cat_session_status`. Admin/App **no** inventan otros.


| code | Quién lo provoca | Quién lo ve como cola |
| --- | --- | --- |
| `scheduled` | SPD programa | App (próximas) |
| `in_progress` | OP Start | App home / sesiones |
| `or_hold` | OP confirma **faltante** humano | App alertas + OP-07; **SPD no entra** |
| `correction_required` | OP **libera** el hold a SPD (RF-OP-07) | Cola SPD review |
| `awaiting_spd_review` | OP cierra **sin** faltante | Cola SPD cierre limpio |
| `closed` / `aborted` | SPD u aborto controlado | Histórico |

---

## Canales técnicos


| Canal | Uso en este contrato |
| --- | --- |
| HTTPS → Backend Web | Todo Admin y toda la App |
| WSS → vision-worker | No lo usa Admin ni App |
| Object storage | Pesos `.pt`, imágenes `family_example`, avisos PDF/HTML, evidencias de sesión (solo lectura App) |
| Mongo `checkpoint_frame` | App lee evidencias/video **ya guardados**; no escribe |

---

# PARTE A — ADMINISTRADOR TI

El administrador inicia sesión con `it_admin`. Home = dashboard de métricas. Lo desactivado permanece visible **solo** aquí.

---

## RF-AD-01 · Dashboard de métricas

### Actor

`it_admin`

### Cómo se llega

Login portal Admin → Dashboard (sidebar HOME).

### Qué muestra la interfaz

KPIs de solo lectura, por ejemplo:

- **Catálogo:** familias activas/inactivas, procedimientos, modelos (cuál está `active`).
- **Sistema:** Auth/PG/Redis/Mongo/storage alcanzables (health; sin fingir microservicios).
- **Operación:** sesiones por status (`in_progress`, `or_hold`, `correction_required`, `awaiting_spd_review`), usuarios `active = false`.

Sin botones que cierren casos clínicos. **No** es dueño de conteo de piezas ni de kits (esos KPIs, si aparecen, son de lectura).

### Qué hace el usuario

Consulta; navega a un RF-AD-* de detalle.

### Qué ocurre en el sistema

`GET` → SELECT agregados. Sin escritura.

### Almacenes

PostgreSQL lectura. Health: ping de servicios de app.

### Configuración

Umbrales de color de KPI.

### Salida

Navegación a familias / IAM / modelos / audit.

### Errores

Si un almacén no responde, el KPI de sistema muestra error; el resto del dashboard no se bloquea.

---

## RF-AD-02 · Familias de instrumentos y examples

### Actor

`it_admin`

### Cómo se llega

Sidebar CATÁLOGOS → Instrument families. Alta/edición desde la lista.

### Qué muestra la interfaz

Lista (incluye inactivas, badge). Formulario: `code` (el que usa SPD al alta de **pieza**), `name`, `category_id`, `identify_text`, `classify_text`, `function_text`, `active`. **Tabla de `family_example`:** imagen vía `media_asset`, `example_text`, `sort_order`.

Botones: Save, Deactivate / Reactivate. No hay Delete si existen `instrument`, `kit_item` o `model_class`.

**Admin no da de alta instrumentos individuales.** Eso es SPD (`instrument` + ciclo).

### Qué hace el usuario

Crea o corrige el maestro de **tipo** (familia) y sus fotos pedagógicas.

### Qué ocurre en el sistema

1. INSERT/UPDATE `instrument_family`.
2. Por cada example: subir archivo a storage, INSERT `media_asset`, INSERT/UPDATE `family_example`.
3. Desactivar: `active = false` (OP/SPD/App dejan de ofrecerla en altas nuevas; sesiones ya congeladas no cambian).
4. `access_audit` acción `catalog.family.upsert` / `catalog.family.deactivate`.

### Almacenes

PostgreSQL + object storage (imágenes).

### Configuración

MIME y tamaño máx. de example (default imagen, 5 MB).

### Salida

Lista de familias.

### Errores

Código duplicado (`uk_instrument_family_code`). Desactivar la familia del modelo activo: advertir y exigir confirmación.

---

## RF-AD-03 · Procedimientos y catálogos de selección

### Actor

`it_admin`

### Cómo se llega

CATÁLOGOS → Procedures (y subpantallas: fases / categorías / motivos).

### Qué muestra la interfaz

CRUD de `cat_procedure_type`. Donde el mockup lo permita: `cat_operation_phase` (orden, si el conteo es obligatorio), `cat_instrument_category`, `cat_discrepancy_reason`. OP y SPD solo **eligen** estos valores.

### Qué hace el usuario

Mantiene la biblioteca que alimenta RF-SP-02 y los dropdowns de corrección.

### Qué ocurre en el sistema

INSERT/UPDATE catálogos (`code` único). No borrar códigos referenciados (`ON DELETE RESTRICT`); ocultar o `active` si existe. `access_audit`.

### Almacenes

PostgreSQL.

### Configuración

Ninguna.

### Salida

Lista del catálogo editado.

### Errores

Código duplicado. Quitar un procedimiento con `operation` ligadas: bloquear o solo ocultar.

---

## RF-AD-04 · Biblioteca de avisos de privacidad

### Actor

`it_admin`

### Cómo se llega

Administración / Privacy notices.

### Qué muestra la interfaz

Lista de `privacy_notice_version` (`version`, `effective_at`, `active`, enlace al documento). Alta: subir PDF/HTML, versión, vigencia, marcar activa.

**SPD no es dueño de esta biblioteca.** En RF-SP-02 elige una versión activa y **liga el aviso a UNA sesión** (`session_processing_agreement`).

### Qué hace el usuario

Publica o desactiva versiones.

### Qué ocurre en el sistema

1. Subir a object storage; SHA-256; INSERT `privacy_notice_version`.
2. Varias activas permitidas por default (`privacy_notice_single_active = false`); el SPD elige.
3. `access_audit` `privacy.notice.publish`.
4. **No** INSERT `session_processing_agreement`.

### Almacenes

PostgreSQL + object storage (`privacy-notices/`).

### Configuración

`privacy_notice_max_mb`, MIME pdf/html (mismos defaults que RF-SP-02).

### Salida

Lista de versiones.

### Errores

Hash duplicado: advertir. Sesiones ya acordadas no cambian de versión.

---

## RF-AD-05 · Gestión de usuarios

### Actor

`it_admin`

### Cómo se llega

ADMINISTRATION → Users.

### Qué muestra la interfaz

Lista (activos e inactivos): nombre, email, roles, status, last login. Alta/edición: `name`, `email`, rol(es) vía `user_role` (`station_operator` / `spd_supervisor` / `it_admin` / `or_charge`), password inicial o reset, `active`. Modal desactivar.

Asignación de salas (`user_operating_room`) en el usuario `or_charge`: **sí se usa**. La App lista todas las salas (las suyas primero) y **notifica solo** las ligadas.

### Qué hace el usuario

Altas, resets, desactivación, ligar SdP ↔ quirófano. Nunca ve el password en claro después de guardar.

### Qué ocurre en el sistema

INSERT/UPDATE `user` (`password_hash`, `password_updated_at`). INSERT/DELETE `user_role`. INSERT/DELETE `user_operating_room`. Desactivar: `active = false` (login denegado). `access_audit`. Invalidar refresh Redis del usuario desactivado si existe.

### Almacenes

PostgreSQL + Redis (opcional blacklist).

### Configuración

Política de longitud de password.

### Salida

Lista.

### Errores

Email duplicado. Quitar el último `it_admin` activo: bloquear.

---

## RF-AD-06 · Gestión de roles

### Actor

`it_admin`

### Cómo se llega

ADMINISTRATION → Roles.

### Qué muestra la interfaz

`code`, `description`, `institution_id`. Los cuatro códigos de producto son fijos en v1: `station_operator`, `spd_supervisor`, `it_admin`, `or_charge`.

### Qué hace el usuario

Edita **descripción**. Crear códigos nuevos fuera de esa lista queda fuera de v1.

### Qué ocurre en el sistema

UPDATE `role.description`. INSERT de códigos no producto: rechazar en v1. `access_audit`.

### Almacenes

PostgreSQL.

### Configuración

Ninguna.

### Salida

Lista.

### Errores

Cambiar `code` de un rol en uso: bloqueado.

---

## RF-AD-07 · Quirofanos y estaciones de captura

### Actor

`it_admin`

### Cómo se llega

Configuration → Operating rooms / Capture stations.

### Qué muestra la interfaz

CRUD `operating_room` (`code`, `name`, `active`). CRUD `capture_station` (`name`, `room_id`, `roi` JSON, `active`).

### Qué hace el usuario

Registra o desactiva espacios y postes de cámara.

### Qué ocurre en el sistema

INSERT/UPDATE. Desactivar sala no borra sesiones históricas. `access_audit`.

### Almacenes

PostgreSQL.

### Configuración

Validación de ROI (claves `x,y,w,h,frame_w,frame_h`).

### Salida

Overview de configuration.

### Errores

Estación sin sala: bloquear. Código de sala duplicado en la institución.

---

## RF-AD-08 · Modelos de visión (alta, mapping, activar)

### Actor

`it_admin`

### Cómo se llega

ADMINISTRATION → Vision models. New / Edit / Activate.

### Qué muestra la interfaz

Lista: `version_tag`, `architecture`, `model_size`, checksum, `active`, `published_at`.

**Alta (dos pasos):**

1. Subir archivo de pesos (`.pt`). El Backend calcula SHA-256, guarda `media_asset`, intenta extraer `names` / `nc` y metadatos YOLO. Si el parse falla, pide `names.yaml` o JSON `{id: nombre}`.
2. Tabla de mapping: `yolo_class_id` → `instrument_family`. Autocompletar si el nombre coincide con `family.code` o `name`. Familias del catálogo **activo** que el modelo **no** trae: **overlay de aviso**; Admin debe **confirmar** para publicar. Clases del modelo sin familia: mapearlas o rechazar esa clase.

Activate: modal “este modelo será el activo para **nuevas** sesiones”. Las `in_progress` / `or_hold` conservan `work_session.yolo_model_id`.

No hay Delete; desactivar = `active = false` (v1: no dejar cero activos si hay estaciones vision).

### Qué hace el usuario

Sube, mapea, confirma el overlay de familias faltantes, publica, activa.

### Qué ocurre en el sistema

1. INSERT `yolo_model` (`version_tag`, `media_asset_id`, `checksum`, `architecture`, `model_size`, `active = false`).
2. INSERT `model_class` por clase mapeada.
3. Activar: transacción UPDATE el anterior `active = false`, el nuevo `active = true`.
4. **No** notifica al vision-worker para sesiones abiertas.
5. `access_audit` `vision.model.publish` / `vision.model.activate`.

### Almacenes

PostgreSQL + object storage.

### Configuración

Tamaño máx. `.pt`; extensiones; texto del overlay de familias faltantes.

### Salida

Lista de modelos.

### Errores

Checksum ya existente: rechazar. Activar sin `model_class`: bloquear. Parse fallido sin YAML/JSON: bloquear paso 2.

---

## RF-AD-09 · Audit log de accesos

### Actor

`it_admin`

### Cómo se llega

ADMINISTRATION → Audit log.

### Qué muestra la interfaz

Tabla de `access_audit`: `occurred_at`, actor, `action`, `resource_type`, `resource_id`, `outcome`, IP. Filtros por fecha, acción, usuario.

Esto **no** es el timeline clínico (`count_event`).

### Qué hace el usuario

Consulta. Sin editar filas (append-only).

### Qué ocurre en el sistema

SELECT filtrado. Sin UPDATE/DELETE.

### Almacenes

PostgreSQL.

### Configuración

Paginación.

### Salida

Misma pantalla.

### Errores

Rango de fechas vacío: no consultar.

---

## RF-AD-10 · Institución y configuración de aplicación

### Actor

`it_admin`

### Cómo se llega

Configuration overview / Edit institution.

### Qué muestra la interfaz

`institution.name`, `active`. Parámetros de aplicación visibles en UI (umbrales KPI, MIME de avisos).

### Qué hace el usuario

Corrige nombre; no borra la institución demo.

### Qué ocurre en el sistema

UPDATE `institution`. `access_audit`.

### Almacenes

PostgreSQL.

### Configuración

N/A (esta pantalla *es* la config).

### Salida

Overview.

### Errores

Dejar `active = false` en la única institución: bloquear en v1.

---

## RF-AD-11 · Solicitudes ARCO (stub de demo)

### Actor

`it_admin`

### Cómo se llega

Sidebar legal / ARCO.

### Qué muestra la interfaz

Lista de `privacy_request` si hay filas: tipo, status, paciente, fechas. Detalle de solo lectura.

Texto fijo de demo: *registro preparado; v1 no ejecuta el derecho.* Los tipos de fila deben parecerse a operaciones ARCO hospitalarias reales (acceso, rectificación, cancelación, oposición) para que la pantalla se entienda en la demo — investigar ese vocabulario institucional; **no** implementar el flujo.

Sin botones Efectuar borrado / Exportar bundle.

### Qué hace el usuario

Abre la pantalla para la demo.

### Qué ocurre en el sistema

SELECT. Sin jobs de purga.

### Almacenes

PostgreSQL lectura.

### Configuración

Ninguna.

### Salida

Lista.

### Errores

Tabla vacía: empty state, no error.

---

# PARTE B — APP SUPERVISOR DE PISO

Pestañas: **Inicio**, **Discrepancias/alertas**, **Sesiones**, **Personal**. El detalle de hold (RF-AP-03) es drill-in, no pestaña extra.

El sistema **no** levanta alertas por IA sola. La alerta existe cuando un humano confirmó faltante (`or_hold`).

**Salas:** el SdP **ve todas**. El orden de listas pone **primero** las de `user_operating_room` de ese usuario, luego el resto. **Notificación in-app (y badge de alerta nueva) solo** si `work_session` → `operation.room_id` (o estación/`capture_station.room_id`) está en las salas ligadas a ese `or_charge`.

---

## RF-AP-01 · Home (KPIs de piso)

### Actor

`or_charge`

### Cómo se llega

Login App → pestaña Inicio.

### Qué muestra la interfaz

KPIs: sesiones `in_progress`; holds `or_hold`; pendientes SPD ya liberadas; atajo a holds. Las cifras de “mis salas” pueden destacarse; el listado global sigue visible.

### Qué hace el usuario

Consulta; entra a un caso.

### Qué ocurre en el sistema

SELECT agregados sobre todas las salas. Orden: ligadas primero.

### Almacenes

PostgreSQL.

### Configuración

Ninguna.

### Salida

AP-02 / AP-03 / AP-04.

### Errores

Vacío: ceros, no error.

---

## RF-AP-02 · Cola de alertas (`or_hold`)

### Actor

`or_charge`

### Cómo se llega

Pestaña Discrepancias/alertas.

### Qué muestra la interfaz

Sesiones `or_hold` (faltante humano). Sala, procedimiento, operador, familias en falta, antigüedad. Badge “sin liberar a SPD”.

Lista: todas las salas, **las suyas primero**. **Push/in-app notif solo** de holds en salas ligadas (sin SMS).

### Qué hace el usuario

Abre un caso (RF-AP-03). No resuelve pérdida de pieza.

### Qué ocurre en el sistema

SELECT `work_session` status `or_hold` + `discrepancy.resolved = false`. JOIN sala. Notif: filtro `user_operating_room`.

### Almacenes

PostgreSQL.

### Configuración

TTL del badge de notif.

### Salida

RF-AP-03.

### Errores

Hold ya liberado: quitar de la cola; toast “ya en review SPD”.

---

## RF-AP-03 · Detalle de hold (solo enterarse; no libera a SPD)

### Actor

`or_charge` (lectura/comentario). El **OP dueño** libera en RF-OP-07.

### Cómo se llega

Desde AP-02 o desde sesión en hold.

### Qué muestra la interfaz

- Ficha (status, expected vs validado, faltantes).
- Timeline `count_event`.
- Evidencias: clips/checkpoints **ya guardados** (URL firmada, MP4). **No** stream de cámara.
- Hilo de comentarios (OP y SdP).
- Resolución sugerida si OP ya la cargó.
- Botón **Comentar**. **No** hay “Enviar a SPD”, “Marcar lost” ni “Confirm and close”.

El SdP se entera y documenta. **Liberar para SPD va desde OP** (RF-OP-07).

### Qué hace el usuario

Lee evidencias, escribe comentario, espera a que OP libere.

### Qué ocurre en el sistema

Comentar: INSERT `count_event` `or_hold_comment` (`user_id`, `payload` `{discrepancy_id, text, actor_role: or_charge}`). **No** cambia status. **No** llena `or_released_*`.

### Almacenes

PostgreSQL write (evento). Mongo + storage **lectura**.

### Configuración

Longitud mínima del comentario (default 10).

### Salida

Sigue en detalle o vuelve a la cola.

### Errores

Comentar sesión que ya no está `or_hold`: bloquear. WSS visión: no abrir.

---

## RF-AP-04 · Sesiones (tabla de conteo en vivo, sin cámara)

### Actor

`or_charge`

### Cómo se llega

Pestaña Sesiones.

### Qué muestra la interfaz

Filtros: programadas, en progreso, `or_hold`, procedimiento, sala. Lista **todas** las salas, ligadas primero.

Ficha: datos, inventario, timeline. **Live de tabla de conteo:** expected vs detected y `confidences` por caja al persistirse `count_event` (poll o WS Backend). **Sin** feed de cámara. **Sí** evidencias de video **ya guardadas** (URL firmada, MP4).

### Qué hace el usuario

Filtra y observa.

### Qué ocurre en el sistema

GET lista/detalle. Sin escritura salvo navegación. Notif de cambio a hold: solo si la sala está ligada.

### Almacenes

PostgreSQL. Mongo/storage lectura de evidencias. No WSS DS10.

### Configuración

Intervalo de poll (default 3 s).

### Salida

AP-03 si el status es `or_hold`.

### Errores

Sesión abortada: ficha histórica solo lectura.

---

## RF-AP-05 · Personal (médicos y usuarios)

### Actor

`or_charge`

### Cómo se llega

Pestaña Personal.

### Qué muestra la interfaz

Directorio de **`physician`** y **`user`** (sin hashes). Por persona:

- ficha (especialidad / rol, contacto mínimo);
- historial de sesiones en las que participó;
- sesiones **programadas**;
- si está **en sesión ahora**: cuál y **dónde** (sala).

Un cirujano **puede no tener** cuenta de login. No edita IAM (RF-AD-05).

### Qué hace el usuario

Consulta; atajo a la sesión relacionada.

### Qué ocurre en el sistema

SELECT `physician` + `physician_specialty` + `operation_physician` + `user` + `work_session` / `operation` / `operating_room`. Sin password. PHI: no listar `patient.display_name` en este directorio (sí en ficha de sesión ya autorizada).

### Almacenes

PostgreSQL.

### Configuración

Ninguna crítica.

### Salida

Detalle → RF-AP-04.

### Errores

Vacío: empty state.

---

# PARTE C — FUERA DE ALCANCE (explícito)

- Kits (CRUD receta): RF-SP-09.
- Instrumentos individuales y ciclo de material: SPD.
- Familias y examples: RF-AD-02.
- Clientes máquina FHIR / `integration_client` (considerado, **no diseñado**).
- Ejecutar derechos ARCO (solo RF-AD-11 stub de lista).
- Entrenamiento ML ni publicación desde dataset.
- Push/SMS. Stream de cámara en la App.
- App que programe sesiones, edite kits, active modelos o **libere hold a SPD**.
- Admin que valide conteos o cierre `work_session`.

---

*Fin del contrato v1 Admin + App. Cualquier cambio de pantalla o de escritura en BD debe actualizar el RF correspondiente aquí y, si toca conteo, kits o review, también [rf_operador_supervisor_spd.md](rf_operador_supervisor_spd.md).*
