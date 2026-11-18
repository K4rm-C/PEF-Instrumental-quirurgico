# PEF — Integración hospitalaria FHIR R4 (MS-07, parte B)

Estado: **integrado y probado con un servidor FHIR de pruebas local (HAPI FHIR R4 v7.4.0, contenedor)**. **No** hay integración con un HIS hospitalario real; el mapeo semántico de DS08 (borrador 0.1.0) está **pendiente de aprobación** por Integraciones + PEF Core. Nada de esto se declara productivo.

| Nivel | Qué hay |
| :--- | :--- |
| Pruebas unitarias con doble HTTP | `tests/test_deliveries.py`: servidor local que responde como un FHIR (no valida FHIR) |
| Interoperabilidad con servidor de pruebas real | `scripts/e2e_ms07_ms08.py`: HAPI FHIR guarda y valida los recursos (`$validate`) |
| HIS real | **No**. Requiere convenio, endpoint, credenciales y aprobación del mapeo |

## 1. Recursos y mapeo (DS08 §1.3.2)

| FHIR R4 | Origen PEF | Campos |
| :--- | :--- | :--- |
| Organization | `institution` | id, identifier `urn:pef:institution`, active, name |
| Location | `operating_room` | id, identifiers (uuid, código), status active/inactive, name, mode instance, managingOrganization |
| Practitioner | `physician` | id, identifier `urn:pef:physician`, active, name.text (no se inventan cédulas) |
| Patient | `patient` + `resource_identifier` + `cat_gender` | solo con `fhir.patient.read`; name.text, gender (códigos idénticos a AdministrativeGender), birthDate; **ningún identificador inventado** |
| Encounter | `operation` | status (scheduled→planned, in_progress→in-progress, closed→finished, cancelled→cancelled), class = v3 NullFlavor `UNK` (el PEF no registra la clase), type.text = procedimiento, period, location, participant (médicos, rol como texto), subject (solo con alcance de paciente), serviceProvider |

**No mapeado:** sesiones de conteo, conteos, discrepancias y evidencias (DS08: "Device / Observation fuera del MVP"). Una sesión de conteo **no** se presenta como ningún evento clínico.

Identificadores: id lógico = UUID del PEF; además `urn:pef:<tabla>|<uuid>` y las filas de `resource_identifier` que existan.

Validación: estructural propia (`services/fhir.py::validate`) antes de enviar; en el E2E, `$validate` de HAPI sin errores. Advertencias conocidas de HAPI (registradas, no bloqueantes): `UNK` fuera del *value set* de clase, participante sin código de tipo, recurso sin narrativa. Son parte de la aprobación semántica pendiente.

## 2. API de lectura (clientes máquina)

`/fhir/r4/metadata` (público), `/{Organization|Location|Practitioner|Patient|Encounter}/{id}`, búsqueda `Location|Practitioner|Encounter?_count&_offset[&status]`, `Encounter/{id}/$pef-bundle` (alcance `fhir.export.bundle`). Solo **tokens de cliente** con el alcance del recurso; otra institución = 404 (`OperationOutcome`); cada lectura se audita con actor `client`.

## 3. Entregas a un servidor FHIR externo

1. El Administrador de TI registra el destino: `POST /api/v1/integration/endpoints` (https obligatorio; el host debe estar en `INTEGRATION_ALLOWED_HOSTS`, **vacío por defecto**: nada sale a ningún lado sin configurarlo).
2. Solicita la entrega de un Encounter con `Idempotency-Key` (misma clave + mismo cuerpo = la misma entrega; otro cuerpo = 422).
3. El worker arma, al enviar, un `Bundle` **transaction** con `PUT Tipo/<id PEF>` (update-as-create). Un reintento o una segunda entrega sobrescribe los mismos recursos: **sin duplicados** en el destino (en el E2E, HAPI conserva un solo Encounter tras dos entregas y una caída).
4. Resultados: 2xx enviado; 408/429/5xx/timeout = reintento con backoff exponencial hasta `INTEGRATION_DELIVERY_MAX_ATTEMPTS`; otro 4xx = fallido. Cada intento queda en `integration_delivery_attempt`; el resultado final se audita (`FHIR_DELIVERY_SENT/FAILED`).
5. El Patient solo se envía si el destino lo incluye en `resource_types`. El PEF nunca escribe en la base de datos de un HIS.

Variables (worker): `INTEGRATION_ALLOWED_HOSTS`, `INTEGRATION_ALLOW_PLAIN_HTTP` (solo pila de pruebas), `INTEGRATION_DELIVERY_TIMEOUT_SECONDS`, `INTEGRATION_DELIVERY_MAX_ATTEMPTS`, `INTEGRATION_DELIVERY_BACKOFF_SECONDS`.

## 4. Pendiente para producción

- Aprobación semántica del mapeo (clase del Encounter, roles de participante, narrativa, perfiles nacionales si aplican).
- Autenticación hacia el HIS (SMART Backend Services / mTLS) — hoy el destino de pruebas no exige autenticación.
- Interfaz de Administración para destinos y entregas (hoy solo API, no publicada en el Gateway).
- Disparadores automáticos de entrega (hoy la entrega se solicita explícitamente).
- Retención de `integration_delivery_attempt`.
