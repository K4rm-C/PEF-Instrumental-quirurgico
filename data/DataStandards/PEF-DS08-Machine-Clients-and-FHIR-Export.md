---
id: PEF-DS08
title: Machine Clients and FHIR Export
version: "0.1.0"
status: draft
owner: PEF Core / Integraciones
applies_to: [BE, MS]
stores: [PostgreSQL]
related: [PEF-DS01, PEF-DS02, PEF-DS07]
last_updated: 2026-09-06
---

# PEF-DS08 · Machine Clients and FHIR Export

## INTRODUCCION

Este estándar define **clientes máquina** (`integration_client`) que consumen APIs o export FHIR sin ser usuarios humanos del portal, y las **reglas de exportación FHIR R4** (recursos y scopes) hacia sistemas externos.

Tabla dueña: `integration_client`.

Los recursos FHIR se **proyectan** desde tablas de DS01/DS02 (Patient, Practitioner, Encounter/Operation, Identifier, etc.); este DS no redefine esas tablas. El detalle HTTP genérico de microservicios está en PEF-DS09; aquí el foco es identidad máquina + contrato FHIR.

---

## 1. Explicación del estándar

### 1.1 Definiciones

| Término | Significado |
| :--- | :--- |
| **integration_client** | Aplicación/HIS registrada con `client_id` + `secret_hash` + scopes. |
| **Scope** | Cadena de permiso en JSONB (`fhir.patient.read`, …). |
| **Export FHIR** | Respuestas JSON FHIR R4 (y Bundle cuando aplique). |
| **Client credentials** | Flujo máquina: no usa email/password de `user`. |

### 1.2 Uso

Alta del cliente por admin → el HIS pide token con client credentials → lee recursos permitidos → cada lectura sensible genera `access_audit` con `actor_type=client` (DS07).

---

### 1.3 Convenciones

---

#### 1.3.1 Tabla `integration_client`

**Para qué:** identidad de sistema externo.

| Campo | Tipo | Nulo | Default | Significado |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | PK interno. |
| `client_id` | VARCHAR(128) | NO | — | Identificador público del cliente (login máquina). |
| `name` | VARCHAR(160) | NO | — | Nombre legible. |
| `secret_hash` | TEXT | NO | — | Hash del secreto (nunca el secreto en claro). |
| `scopes` | JSONB | NO | `[]` | Lista de scopes concedidos. |
| `active` | BOOLEAN | NO | `TRUE` | Si puede autenticarse. |
| `last_used_at` | TIMESTAMPTZ | SÍ | NULL | Último uso exitoso. |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Alta. |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Edición. |
| `institution_id` | UUID | NO | — | Tenant al que pertenece el cliente. |

**Hacia dónde apunta**

| Campo | Apunta a | Relación | Por qué | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `institution_id` | `institution.id` (DS01) | N:1 | Aísla datos por hospital. | `CASCADE` |

**Restricciones:** PK `id`; UNIQUE `client_id`.

**Scopes iniciales sugeridos**

| Scope | Permite |
| :--- | :--- |
| `fhir.patient.read` | Lectura Patient |
| `fhir.practitioner.read` | Lectura Practitioner |
| `fhir.encounter.read` | Lectura Encounter (proyección de `operation`) |
| `fhir.binary.read` | Lectura Binary/Attachment de evidencia elegible |
| `fhir.export.bundle` | Bundle de exportación acotado |

**Ejemplo de fila completa**

```json
{
  "id": "ic000001-0000-4000-8000-000000000001",
  "client_id": "his-demo-norte",
  "name": "HIS Demo Norte",
  "secret_hash": "$argon2id$v=19$m=65536,t=3,p=4$[REDACTED]",
  "scopes": [
    "fhir.patient.read",
    "fhir.encounter.read",
    "fhir.export.bundle"
  ],
  "active": true,
  "last_used_at": "2026-09-05T12:00:00Z",
  "created_at": "2026-06-01T09:00:00Z",
  "updated_at": "2026-09-05T12:00:00Z",
  "institution_id": "11111111-1111-1111-1111-111111111111"
}
```

---

#### 1.3.2 Proyección FHIR (contrato de datos, no tablas nuevas)

Mapeo lógico PEF → FHIR R4:

| Recurso FHIR | Fuente PEF | Notas |
| :--- | :--- | :--- |
| Patient | `patient` + `resource_identifier` | Identifier = system\|value |
| Practitioner | `physician` + identifiers | |
| Encounter | `operation` (+ pacientes/médicos) | Status desde `cat_operation_status` |
| Location | `operating_room` | |
| Device / Observation (opcional futuro) | estación / conteos | Fuera del MVP si no hay demanda |
| Binary / Attachment | `media_asset` | Solo si scope y acuerdo lo permiten |

**Ejemplo completo de Patient (respuesta mock)**

```json
{
  "resourceType": "Patient",
  "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  "identifier": [
    {
      "use": "official",
      "system": "https://demo.hospital/fhir/sid/mrn",
      "value": "MRN-0001"
    }
  ],
  "active": true,
  "name": [
    {
      "text": "Paciente Demo"
    }
  ],
  "gender": "female",
  "birthDate": "1988-04-12",
  "managingOrganization": {
    "reference": "Organization/11111111-1111-1111-1111-111111111111"
  }
}
```

**Ejemplo completo de token client-credentials (campos del payload de respuesta)**

| Campo | Tipo | Significado |
| :--- | :--- | :--- |
| `access_token` | string | JWT de acceso. |
| `token_type` | string | `Bearer`. |
| `expires_in` | int | Segundos de vida. |
| `scope` | string | Scopes concedidos separados por espacio. |

```json
{
  "access_token": "[JWT_REDACTED]",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "fhir.patient.read fhir.encounter.read fhir.export.bundle"
}
```

---

### 1.4 Governance and Ownership

| Aspecto | Responsable |
| :--- | :--- |
| Alta de clientes y scopes | Admin institucional + integraciones |
| Rotación de secretos | Ops / seguridad |
| Mapeo FHIR | Integraciones + PEF Core |

---

### 1.5 Justificación del estándar

**Decisiones tomadas**

1. **Cliente máquina ≠ `user`.** Secretos y scopes distintos de contraseñas humanas.
2. **Scopes en JSONB** listables y auditables.
3. **FHIR como proyección**, no segundo modelo de persistencia.
4. **Aprobación humana al dar de alta el cliente**, no en cada GET.

**Alternativas no elegidas**

| Alternativa | Por qué no |
| :--- | :--- |
| Reusar tabla `user` con flag `is_service` | Mezcla IAM humano y máquina; complica RBAC y auditoría. |
| Guardar secreto en claro | Inaceptable. |
| Microservicio FHIR obligatorio desde día 1 | El Backend Web puede exponer export; MS se añade si el volumen lo pide (DS09). |
| Duplicar Patient en colección Mongo | Rompe unicidad con DS02. |

---

## 2. Anexo A – Información general

| Campo | Valor |
| :--- | :--- |
| Código | PEF-DS08 |
| Título | Machine Clients and FHIR Export |
| Versión | 0.1.0 |
| Estado | draft |
| Almacén | PostgreSQL (`integration_client`); proyección FHIR desde DS02 |
| Fuera de alcance | Endpoints REST de apps Android/Desktop (DS09); WSS (DS10) |
| DDL de referencia | `esquema_base_datos_v2.md` §5.3 |
