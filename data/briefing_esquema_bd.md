# Briefing · Esquema de base de datos (PEF)

---

## 1. Resumen

El esquema modela **quién cuenta, en qué acto quirúrgico, con qué kit e instrumental, qué detectó el sistema y qué decidió el humano**, sin pretender ser un HIS/EHR completo. PostgreSQL es la fuente de verdad de negocio y auditoría; MongoDB guarda evidencia visual ocasional; Google Cloud Storage guarda archivos; Redis solo tokens efímeros.

---

## 2. Qué cubre


| Área                             | Profundidad            | Qué incluye                                                                                              |
| -------------------------------- | ---------------------- | -------------------------------------------------------------------------------------------------------- |
| **Sesión de conteo y auditoría** | Alta                   | Sesión de trabajo, inventario esperado, eventos, discrepancias, correcciones humanas, estados de sesión  |
| **Instrumental y stock**         | Alta                   | Familia YOLO, pieza individual, usos, reservas por operación, kits versionables, ciclo de vida           |
| **Visión / modelo**              | Media–alta (metadatos) | Versión de pesos, mapeo de clase YOLO a familia, ROI por estación; **no** el tensor del modelo           |
| **Identidad y acceso**           | Alta                   | Institución, usuarios, roles N:M                                                                         |
| **Contexto clínico-operativo**   | Media (mínimo viable)  | Paciente, médico, operación, sala, estación; solo lo necesario para vincular el conteo                   |
| **Interoperabilidad FHIR**       | Media (contrato listo) | Identificadores `system` + `value`; sin HIS conectado aún                                                |
| **Evidencia visual**             | Media                  | Checkpoints en Mongo + ancla de media en PostgreSQL; tope 30–50 frames por sesión; retención 90/180 días |


---

## 3. Qué queda limitado


| Limitación                                        | Por qué                                                                                                                                            |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| **No es un EHR**                                  | Paciente y médico sin historial clínico, notas ni diagnósticos. El PEF es asistente de conteo, no expediente.                                      |
| **No hay stock agregado por familia**             | Stock = filas de instrumento individual. Total disponible = consulta (estado + reservas). Evita desincronizar un número agregado vs piezas reales. |
| **Detecciones no van a PostgreSQL frame a frame** | Alta frecuencia → Mongo y UI. PostgreSQL guarda **eventos de negocio**, no cada bbox a 10 FPS.                                                     |
| `resource_identifier` **sin FK física**           | Polimórfico (patrón FHIR Identifier). PostgreSQL no soporta bien “FK a N tablas según tipo”. Integridad por consulta y capa de aplicación.         |
| `cat_checkpoint_reason` **sin FK en PG**          | El motivo vive en Mongo como string. El catálogo en PostgreSQL es vocabulario de referencia.                                                       |
| **Redis no está en el diagrama ER**               | Solo JWT/TTL; no es dato de negocio persistente.                                                                                                   |
| **TTS / STT / HIS en vivo**                       | Fuera del núcleo o postergados; el esquema ya deja anclas FHIR.                                                                                    |


---

## 4. Cómo se reparte el almacenamiento


| Almacén        | Guarda                                                  | No guarda                  |
| -------------- | ------------------------------------------------------- | -------------------------- |
| **PostgreSQL** | Negocio, auditoría, metadatos, URI canónica de archivos | Bytes del modelo ni JPEG   |
| **MongoDB**    | Checkpoints de frame, telemetría de sesión              | Reglas de cierre, usuarios |
| **GCP**        | Pesos YOLO, JPEG de evidencia y ejemplos                | Lógica de negocio          |
| **Redis**      | Tokens, contadores efímeros                             | Auditoría permanente       |


---

## 5. Rundown de tablas importantes

### Identidad y portal

- `institution` — raíz multi-campus
- `user` **/** `role` **/** `user_role` — login y RBAC del portal (pacientes **no** son usuarios)

### Intercambio externo (integraciones)

- `resource_identifier` — diccionario `system|value` ↔ UUID local (paciente, médico, operación, sala, instrumento). Búsqueda antes de duplicar. Sin FK polimórfica en PostgreSQL.

### Contexto clínico (ligero)

- `operation` — acto donde ocurre el conteo
- `patient` **/** `physician` + puentes N:M
- `operating_room` **→** `capture_station` — sala + cámara/ROI
- `cat_procedure_type` — tipo de procedimiento (también alimenta usos de instrumental)

### Instrumental y visión

- `instrument_family` — vocabulario del conteo (código estable + textos pedagógicos)
- `instrument` — pieza física (stock real)
- `instrument_usage` — pieza × procedimiento × contexto
- `kit` **/** `kit_item` — plantilla; al abrir sesión se copia a `expected_inventory`
- `instrument_reservation` — pieza concreta amarrada a una operación
- `yolo_model` **/** `model_class` — qué pesos están activos y qué `class_id` es qué familia
- `media_asset` — ancla GCS (pesos, ejemplos, frames)

### Sesión de conteo (núcleo operativo)

- `work_session` — quién, cuándo, estación, kit, flags de retención
- `expected_inventory` — snapshot del kit al abrir sesión
- `count_event` — hechos auditables (`event_type` + `payload` JSON)
- `discrepancy` **/** `human_correction` — faltantes y justificación humana

### MongoDB

- `checkpoint_frame` — evidencia ocasional (detecciones + URI)
- `session_telemetry` — FPS/latencia (TTL corto)

---

## 6. Decisiones de diseño generales

1. **4FN / catálogos** `cat_`* — estados y listas cerradas en tablas propias; evita texto libre y columnas repetidas.
2. **Stock por pieza, no por familia** — reservas reales; asignar kit falla si no hay unidades libres.
3. **Kit vs inventario esperado** — plantilla mutable; la sesión conserva snapshot histórico.
4. **Modelo fuera del navegador** — PostgreSQL solo metadatos; el archivo de pesos vive en GCS y se carga en el servidor de inferencia.
5. **Fan-out post-inferencia** — mismo resultado filtrado → WebSocket (UI) + evento en PostgreSQL + checkpoint en Mongo solo si la política lo pide.
6. **FHIR Identifier centralizado** — preparado para HIS; el conteo usa UUID locales, no MRN en cada consulta.

---

## 7. Los 4 módulos mayores y qué tocan de la BD

### Módulo 1 — Portal / Auth / UI

**Tablas:** `institution`, `user`, `role`, `user_role`, Redis (tokens).  
**Rol BD:** autenticar, acotar por institución, abrir/cerrar sesión de trabajo.  
**No toca:** pesos YOLO, checkpoints, `resource_identifier` (salvo pantallas de supervisión que muestren ids externos).

### Módulo 2 — Inferencia / visión / estación

**Tablas:** `capture_station` (ROI), `yolo_model`, `model_class`, `instrument_family`, `media_asset`; colecciones Mongo de checkpoint y telemetría.  
**Rol BD:** cargar modelo activo, mapear `class_id` → familia, filtrar por ROI, emitir detecciones.  
**No toca:** crear pacientes del HIS; no escribe cada frame en PostgreSQL.

### Módulo 3 — Reglas de negocio / sesión de conteo

**Tablas:** `work_session`, `expected_inventory`, `count_event`, `discrepancy`, `human_correction`, `cat_session_status`, `kit`/`kit_item`, reservas.  
**Rol BD:** comparar conteo vs esperado, alertar, bloquear cierre, auditar correcciones.  
**Conexión clave:** `work_session` une usuario + operación + estación + kit.

### Módulo 4 — Integraciones / clínico-operativo / FHIR

**Tablas:** `resource_identifier`, `patient`, `physician`, `operation`, salas, especialidades.  
**Rol BD:** recibir ids externos (`system|value`), traducir a UUID local, vincular acto ↔ paciente/médico.  
**Limitado:** sin EHR; sin Mongo; el conteo no habla con el HIS directo.

```text
UI ──WS──► Inferencia ──► Reglas ──► PostgreSQL (eventos)
                │              │
                │              └──► (opcional) Mongo checkpoint + GCS
                └── lee: ROI, yolo_model, model_class, familias

Integraciones HIS ──► resource_identifier ──► patient / physician / operation
                              (no toca el pipeline de frames)
```

---

## 8. Diagrama

- **Línea sólida** = foreign key real en PostgreSQL.
- **Línea punteada o nota** = relación lógica (FHIR, Mongo, polimórfica).
- `resource_identifier` esta sin flechas sólidas: es correcto; la unión es por consulta (`resource_type` + `resource_id`).
- `cat_checkpoint_reason` no tiene FK entrante en PG; alimenta el campo `reason` en Mongo como catalogo)
- El orden visual del lienzo **no** tiene que coincidir con el diagrama Mermaid interno; lo que importa son tablas y FK documentadas.

---

## 9. Convenciones técnicas breves

- PostgreSQL 15+, PK `UUID`, timestamps en UTC.
- Identificadores FHIR: `system` (URI) + `value` (string).
- URI interna PEF: `https://pef.udem/conteo-cenital` + UUID del registro.
- Nombres de tablas en inglés en el DDL (`work_session`, `instrument_family`, etc.). (por el hecho que el pef es junto a Linnaeus)
- Total: 38 tablas en PostgreSQL + 2 colecciones Mongo en el manejo de datos.

