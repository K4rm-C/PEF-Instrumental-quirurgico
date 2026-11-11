# Data Standards PEF · Índice

Documentos que explican **qué datos maneja el sistema**, para qué sirven, cómo se escriben y leen, y con qué reglas. No sustituyen los diagramas de arquitectura ni el DDL: los hacen usables para alguien del equipo (o un heredero) que necesite entender el significado de cada cosa sin adivinar.

**Idioma:** nombres de archivo e IDs en inglés. Texto en español. Versión en inglés de estos mismos estándares: al cierre del proyecto.

**Fuente técnica de tablas:** `../esquema_base_datos_v2.md` y `../diagrama_er_completo.md`.

---

## Catálogo

| ID | Archivo | Qué cubre (dueño canónico) |
| :--- | :--- | :--- |
| PEF-DS01 | [PEF-DS01-Identity-and-Credentials.md](PEF-DS01-Identity-and-Credentials.md) | Institución, usuario, rol, contraseña hasheada, claves Redis de sesión/login |
| PEF-DS02 | [PEF-DS02-Clinical-Context-and-Identifiers.md](PEF-DS02-Clinical-Context-and-Identifiers.md) | Paciente, médico, sala, estación, operación, identificadores FHIR |
| PEF-DS03 | [PEF-DS03-Instrument-Kits-and-Model-Registry.md](PEF-DS03-Instrument-Kits-and-Model-Registry.md) | Familias, piezas, kits, fases de procedimiento, registro del modelo YOLO |
| PEF-DS04 | [PEF-DS04-Work-Session-and-Count-Facts.md](PEF-DS04-Work-Session-and-Count-Facts.md) | Sesión de trabajo, inventario esperado, eventos de conteo, discrepancias |
| PEF-DS05 | [PEF-DS05-Evidence-and-Object-Storage.md](PEF-DS05-Evidence-and-Object-Storage.md) | Evidencia en object storage (`media_asset`) |
| PEF-DS06 | [PEF-DS06-Mongo-Checkpoints-and-Telemetry.md](PEF-DS06-Mongo-Checkpoints-and-Telemetry.md) | Checkpoints y telemetría en Mongo |
| PEF-DS07 | [PEF-DS07-Privacy-Purposes-and-Subject-Rights.md](PEF-DS07-Privacy-Purposes-and-Subject-Rights.md) | Aviso, acuerdos, ARCO, auditoría de acceso |
| PEF-DS08 | [PEF-DS08-Machine-Clients-and-FHIR-Export.md](PEF-DS08-Machine-Clients-and-FHIR-Export.md) | Cliente máquina y export FHIR |
| PEF-DS09 | [PEF-DS09-API-Exchange-Contracts.md](PEF-DS09-API-Exchange-Contracts.md) | APIs REST, endpoints, JSON/XML |
| PEF-DS10 | [PEF-DS10-Vision-Realtime-Contracts.md](PEF-DS10-Vision-Realtime-Contracts.md) | Mensajes WSS estación–worker |

Tags útiles en el front matter de cada archivo:

- `applies_to`: WP (portal), BE (backend web), CS (estación), AP (apps móvil/escritorio), MS (microservicios), Worker
- `stores`: PostgreSQL, Redis, MongoDB, ObjectStorage

---

## Reglas anti-solape

1. Una tabla o colección tiene **un solo dueño** (un DS). Los demás solo dicen “ver PEF-DSxx”.
2. Un endpoint se define en detalle en **DS09** (REST) o **DS10** (WSS). Los DS de tablas pueden nombrar el endpoint que las usa, sin redescribir el contrato HTTP.
3. Bytes de imagen/GIF/pesos: **DS05**. Detecciones en Mongo: **DS06**. Hechos de conteo en PostgreSQL: **DS04**.
4. Contraseña y secretos: solo hashes en **DS01** / **DS08**. Nunca el valor en claro en un estándar ni en mocks.

### Matriz anti-solape (verificación)

| Artefacto | Dueño canónico | No redefinir en |
| :--- | :--- | :--- |
| `institution`, `user`, `role`, `user_role`, Redis auth | DS01 | DS08–10 |
| `patient`…`operation*`, `resource_identifier`, cats clínicos | DS02 | DS03–04 (solo FK) |
| Familias, kits, YOLO, `cat_operation_phase` | DS03 | DS04 (fase solo referencia) |
| `work_session`, `count_event`, discrepancy | DS04 | DS06 (no hechos SQL) |
| `media_asset` + bytes bucket | DS05 | DS06 (solo UUID/URI) |
| `checkpoint_frame`, `session_telemetry` | DS06 | DS04/DS05 |
| Aviso, acuerdo, ARCO, `access_audit`, purposes | DS07 | DS04 (sesión sin acuerdo ok) |
| `integration_client`, proyección FHIR | DS08 | DS01 (no mezclar con user) |
| Endpoints REST `/api/v1/*` | DS09 | DS01–08 (solo mención) |
| Mensajes WSS estación–worker | DS10 | DS09 |

---

## Estructura de cada estándar

Igual que el ejemplo de gobierno de datos del proyecto:

0. INTRODUCCION  
1. Explicación del estándar  
   - 1.1 Definiciones  
   - 1.2 Uso  
   - 1.3 Convenciones: **todas** las tablas dueñas, **todos** los campos (tipo/nulo/default/significado), FKs (a dónde, **relación/cardinalidad**, por qué / ON DELETE), y **ejemplo de fila completa** por tabla  
   - 1.4 Governance and Ownership  
   - 1.5 Justificación: decisiones de diseño **y** alternativas descartadas con motivo  
2. Anexo A – Información general  

---

## Convenciones de datos (todos los DS)

- Códigos de catálogo y nombres de tabla/columna en **inglés** en la base (`code`, `work_session`, etc.).
- Textos de UI pueden ir en español; el valor guardado sigue el `code` del catálogo.
- Identificadores internos: UUID.
- Timestamps: UTC (`TIMESTAMPTZ`).
- Mocks de demo: inventados, sin datos reales de pacientes.
- **Relación / cardinalidad** en FKs (desde la tabla actual → destino):
  - `N:1` — muchas filas aquí apuntan a una allá (FK típica obligatoria)
  - `N:0..1` — el destino es opcional (FK nullable)
  - `1:1` — a lo sumo un par (suele ir con UNIQUE)
  - `N:N` — conceptual entre dos entidades; en SQL se implementa con tabla puente (cada FK del puente es `N:1`)
