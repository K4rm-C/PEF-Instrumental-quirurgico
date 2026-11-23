---
id: PEF-DS08
title: Machine Clients and FHIR Export
version: "0.1.0"
status: draft
owner: PEF Core / Integrations
applies_to: [BE, MS]
stores: [PostgreSQL]
related: [PEF-DS01, PEF-DS02, PEF-DS07]
last_updated: 2026-09-06
---

# PEF-DS08 · Machine Clients and FHIR Export

## INTRODUCTION

This standard defines **machine clients** (`integration_client`) that consume APIs or FHIR exports without being human portal users, together with the **FHIR R4 export rules** (resources and scopes) toward external systems.

Owned table: `integration_client`.

FHIR resources are **projected** from DS01/DS02 tables (Patient, Practitioner, Encounter/Operation, Identifier, etc.); this DS does not redefine those tables. Generic microservice HTTP details live in PEF-DS09; here the focus is machine identity + FHIR contract.

---

## 1. Standard Explanation

### 1.1 Definitions

| Term | Meaning |
| :--- | :--- |
| **integration_client** | External application/HIS registered with `client_id` + `secret_hash` + scopes. |
| **Scope** | Permission string in JSONB (`fhir.patient.read`, …). |
| **FHIR export** | FHIR R4 JSON responses (and Bundles where applicable). |
| **Client credentials** | Machine flow: does not use `user` email/password. |

### 1.2 Usage

Admin registers the client → the HIS requests a token via client credentials → reads permitted resources → each sensitive read emits an `access_audit` row with `actor_type=client` (DS07).

---

### 1.3 Conventions

---

#### 1.3.1 Table `integration_client`

**Purpose:** external system identity.

| Field | Type | Nullable | Default | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `id` | UUID | NO | `gen_random_uuid()` | Internal PK. |
| `client_id` | VARCHAR(128) | NO | — | Public client identifier (machine login). |
| `name` | VARCHAR(160) | NO | — | Human-readable name. |
| `secret_hash` | TEXT | NO | — | Hash of the secret (never plaintext). Runtime: Werkzeug `pbkdf2:sha256`. |
| `scopes` | JSONB | NO | `[]` | List of granted scopes. |
| `active` | BOOLEAN | NO | `TRUE` | Whether it can authenticate. |
| `last_used_at` | TIMESTAMPTZ | YES | NULL | Last successful use. |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Created at. |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Updated at. |
| `institution_id` | UUID | NO | — | Tenant the client belongs to. |

**References**

| Field | Points to | Relationship | Rationale | ON DELETE |
| :--- | :--- | :--- | :--- | :--- |
| `institution_id` | `institution.id` (DS01) | N:1 | Isolates data per hospital. | `CASCADE` |

**Constraints:** PK `id`; UNIQUE `client_id`.

**Suggested initial scopes**

| Scope | Grants |
| :--- | :--- |
| `fhir.patient.read` | Read Patient |
| `fhir.practitioner.read` | Read Practitioner |
| `fhir.encounter.read` | Read Encounter (projection of `operation`) |
| `fhir.binary.read` | Read Binary/Attachment of eligible evidence |
| `fhir.export.bundle` | Scoped export Bundle |

**Complete example row**

```json
{
  "id": "ic000001-0000-4000-8000-000000000001",
  "client_id": "his-demo-norte",
  "name": "Demo North HIS",
  "secret_hash": "pbkdf2:sha256:600000$[REDACTED]",
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

#### 1.3.2 FHIR Projection (Data Contract, No New Tables)

Logical PEF → FHIR R4 mapping:

| FHIR Resource | PEF Source | Notes |
| :--- | :--- | :--- |
| Patient | `patient` + `resource_identifier` | Identifier = system\|value |
| Practitioner | `physician` + identifiers | |
| Encounter | `operation` (+ patients/physicians) | Status from `cat_operation_status` |
| Location | `operating_room` | |
| Device / Observation (optional future) | station / counts | Outside MVP unless demanded |
| Binary / Attachment | `media_asset` | Only if scope and agreement permit |

**Complete Patient example (mock response)**

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
      "text": "Demo Patient"
    }
  ],
  "gender": "female",
  "birthDate": "1988-04-12",
  "managingOrganization": {
    "reference": "Organization/11111111-1111-1111-1111-111111111111"
  }
}
```

**Complete client-credentials token example (response payload fields)**

| Field | Type | Meaning |
| :--- | :--- | :--- |
| `access_token` | string | Access JWT. |
| `token_type` | string | `Bearer`. |
| `expires_in` | int | Lifetime in seconds. |
| `scope` | string | Space-separated granted scopes. |

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

| Aspect | Owner |
| :--- | :--- |
| Client provisioning and scopes | Institutional admin + Integrations |
| Secret rotation | Ops / Security |
| FHIR mapping | Integrations + PEF Core |

---

### 1.5 Standard Rationale

**Design decisions taken**

1. **Machine client ≠ `user`.** Secrets and scopes are distinct from human passwords.
2. **Scopes in JSONB** are easy to list and audit.
3. **FHIR as a projection**, not a second persistence model. In v1 it is **planned, not yet designed** (no active routes or operational `integration_client` flows in Admin/App).
4. **Human approval when provisioning the client**, not on every GET request.

**Rejected alternatives**

| Alternative | Why not |
| :--- | :--- |
| Reuse `user` table with an `is_service` flag | Mixes human and machine IAM; complicates RBAC and auditing. |
| Store secret in plaintext | Unacceptable. |
| Mandatory dedicated FHIR microservice from day 1 | The Web Backend can expose exports; a dedicated MS can be added if traffic warrants it (DS09). |
| Duplicate Patient in a MongoDB collection | Breaks single source of truth with DS02. |

---

## 2. Annex A – General Information

| Field | Value |
| :--- | :--- |
| Code | PEF-DS08 |
| Title | Machine Clients and FHIR Export |
| Version | 0.1.0 |
| Status | draft |
| Store | PostgreSQL (`integration_client`); FHIR projection from DS02 |
| Out of scope | Android/Desktop REST endpoints (DS09); WSS (DS10) |
| Reference DDL | `esquema_base_datos_v2.md` §5.3 |
