---
id: PEF-DS01
title: Identity and Credentials
version: "0.2.2"
status: draft
owner: PEF Core / BackendAuthService
applies_to: [WP, BE, CS]
stores: [PostgreSQL, Redis]
related: [PEF-DS02, PEF-DS07, PEF-DS08]
last_updated: 2026-11-19
---

# PEF-DS01 · Identity and Credentials

## INTRODUCTION

This standard defines **who can access the system** and **how human identity is stored**.

It covers these PostgreSQL tables: `institution`, `role`, `user`, `user_role`, `user_operating_room`. It also covers temporary authentication keys in Redis (JWT / rate limit). These are not the same thing as a counting `work_session`.

Product v1 roles: `station_operator`, `spd_supervisor`, `it_admin`, `or_charge`.

It does not cover patients or physicians involved in an operation (PEF-DS02). It does not cover HIS-style machine clients (`integration_client`, PEF-DS08). It does not cover privacy notices or ARCO requests (PEF-DS07).

For every table in this standard, **all** fields, FK targets and rationales, and one **complete example row** are documented.

---

## 1. Standard Explanation

### 1.1 Definitions


| Term | Meaning in PEF |
| --- | --- |
| **Institution** | Organization that owns operational data (hospital, clinic, unit). May have a parent if organized hierarchically. |
| **User** | Person with a login account: email, password hash, institution. |
| **Role** | Named permission **within an institution** (`code` + description). |
| **Role assignment** | Row in `user_role` linking a user to a role. |
| **Credential** | Email + password. Only the **hash** exists in the DB, never plaintext. |
| **Authentication session (Redis)** | Temporary token / login-attempt state. Distinct from `work_session`. |


### 1.2 Usage

**Who uses this:** web portal (login and IAM administration), web backend (validates JWT and permissions), capture station (same human user).

**Purpose**

1. Determine which institution the authenticated user belongs to.
2. Control screens and actions via roles.
3. Anchor `user.id` and `institution.id` for auditing and the rest of the model.

**Typical flow**

1. The person enters their email and password.
2. BackendAuthService looks up the user, verifies the hash, and checks `active`.
3. It issues a JWT (and/or HttpOnly cookies on portal routes) and records required state in Redis (TTL, blacklist, login failures).
4. Subsequent requests carry the token; the backend resolves `user_id` and institution.

Surfaces: portal/browser uses legacy cookie routes (`/login`, …) and, behind portal-lan (lab HTTPS :5443), same-origin `AUTH_BROWSER_URL=/auth` is valid (relative path; an absolute URL is not required). Android and MS use `/api/v1/auth` via Gateway :8080.

**Out of scope:** does not define counting, clinical patients, or data-processing consent.

---

### 1.3 Conventions

Global conventions for this DS:

- Role codes in English snake_case. Fixed product v1 set: `station_operator`, `spd_supervisor`, `it_admin`, `or_charge` (floor app). The UI may display localized labels.
- Login email is globally unique across the entire system.
- Password: slow hash (current runtime: Werkzeug `pbkdf2:sha256`). Plaintext is prohibited in the DB, logs, real mocks, or tickets.
- Timestamps in UTC (`TIMESTAMPTZ`).
- Each user belongs to **one** institution. Roles are defined **per** institution.
- Under "References", the **Relationship** column is read from this table toward the target: `N:1` = many rows here to one there; `N:0..1` = optional target (nullable FK); `1:1` = at most one pair; `N:N` is achieved via a junction table (each junction FK remains `N:1`).

---

#### 1.3.1 Table `institution`

**Purpose:** multi-tenant anchor. Almost all business tables hang from an institution.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | Internal institution identifier. |
| `name` | VARCHAR(200) | NO | — | Human-readable name (hospital, unit, network). |
| `active` | BOOLEAN | NO | `TRUE` | If `false`, new users/operations must not be created under it (app policy). |
| `parent_id` | UUID | YES | NULL | Parent institution in a hierarchy (network → hospital → satellite). |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `parent_id` | `institution.id` | N:0..1 (children→parent; a parent can have 0..N children) | Enables a site tree without a separate "group" table. | `SET NULL` — if the parent is deleted, the child remains without a parent. |


**Constraints:** PK `id`.

**Complete example row**

```json
{
  "id": "11111111-1111-1111-1111-111111111111",
  "name": "Demo North Hospital",
  "active": true,
  "parent_id": null
}
```

Child example (same table, another row):

```json
{
  "id": "12121212-1212-1212-1212-121212121212",
  "name": "Demo North East Surgical Unit",
  "active": true,
  "parent_id": "11111111-1111-1111-1111-111111111111"
}
```

---

#### 1.3.2 Table `role`

**Purpose:** permission catalog **per institution**. The same `code` may repeat in another hospital with a different UUID.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | Role identifier. |
| `code` | VARCHAR(64) | NO | — | Stable permission code. Product v1: `station_operator`, `spd_supervisor`, `it_admin`, `or_charge`. |
| `description` | VARCHAR(255) | NO | — | Explanatory text for UI/administration. |
| `institution_id` | UUID | NO | — | Institution owning this role. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `institution_id` | `institution.id` | N:1 | Roles are not global: each hospital maintains its own matrix. | `CASCADE` — if the institution is removed, its roles are removed. |


**Constraints:** PK `id`; UNIQUE (`institution_id`, `code`).

**Complete example row**

```json
{
  "id": "33333333-3333-3333-3333-333333333333",
  "code": "or_charge",
  "description": "Floor supervisor. Monitors room holds; does not close SPD cycle.",
  "institution_id": "11111111-1111-1111-1111-111111111111"
}
```

---

#### 1.3.3 Table `user`

**Purpose:** human access account. This is the row represented by the JWT.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | User identifier. |
| `name` | VARCHAR(160) | NO | — | Display name. |
| `active` | BOOLEAN | NO | `TRUE` | If `false`, login must be rejected. |
| `email` | VARCHAR(255) | NO | — | Login email; globally unique. |
| `password_hash` | TEXT | NO | — | Password hash (never plaintext). Runtime: Werkzeug `pbkdf2:sha256`. |
| `password_updated_at` | TIMESTAMPTZ | YES | NULL | Last time the password was changed. |
| `last_login_at` | TIMESTAMPTZ | YES | NULL | Last successful login. |
| `ui_preferences` | JSONB | NO | `{"locale":"en","theme":"light"}` | Interface preferences (multiple settings in one field). |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Account creation timestamp. |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Last row modification timestamp. |
| `institution_id` | UUID | NO | — | Institution the user belongs to. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `institution_id` | `institution.id` | N:1 | Everything the user does is scoped to one tenant. | `RESTRICT` — an institution cannot be deleted while it still has users. |


**Constraints:** PK `id`; UNIQUE `email`; `ui_preferences` must be a JSON object (`jsonb_typeof = object`).

**Agreed keys inside `ui_preferences`** (extensible without column migrations):

| Key | Expected values | Default | Meaning |
| :--- | :--- | :--- | :--- |
| `locale` | Short BCP 47: `en`, `es`, `sv`, … | `en` | Interface language (does not translate business codes in the DB). |
| `theme` | `light`, `dark` | `light` | UI visual theme. |

Additional UI keys (density, etc.) may be added later in the same JSONB; do not add new columns for each cosmetic preference.

**Cookies / localStorage vs. this field:** before login, the client may store locale/theme in a cookie. After login, `user.ui_preferences` **wins** and the client should sync cookie ← DB. When changing a preference in an authenticated UI, both DB and cookie are updated.

**Complete example row**

```json
{
  "id": "22222222-2222-2222-2222-222222222222",
  "name": "Ana Perez",
  "active": true,
  "email": "ana.perez@demo.local",
  "password_hash": "pbkdf2:sha256:600000$[REDACTED]",
  "password_updated_at": "2026-08-01T15:00:00Z",
  "last_login_at": "2026-09-05T17:40:00Z",
  "ui_preferences": {
    "locale": "es",
    "theme": "dark"
  },
  "created_at": "2026-03-10T12:00:00Z",
  "updated_at": "2026-09-05T17:40:00Z",
  "institution_id": "11111111-1111-1111-1111-111111111111"
}
```

---

#### 1.3.4 Table `user_role`

**Purpose:** N:M between user and role. A user may hold multiple roles simultaneously.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | Assignment identifier. |
| `assigned_at` | TIMESTAMPTZ | NO | `now()` | When the role was assigned. |
| `user_id` | UUID | NO | — | User receiving the role. |
| `role_id` | UUID | NO | — | Assigned role. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `user_id` | `user.id` | N:1 (the user↔role pair is N:N via this table) | Assignment owner. | `CASCADE` — deleting a user removes their role assignments. |
| `role_id` | `role.id` | N:1 (the user↔role pair is N:N via this table) | Specific permission. | `CASCADE` — deleting a role removes its assignments. |


**Constraints:** PK `id`; UNIQUE (`user_id`, `role_id`).

**Application note:** the `role` should belong to the same `institution_id` as the `user`. The DDL does not enforce this with a composite check; the application (or a trigger) must validate it.

**Complete example row**

```json
{
  "id": "44444444-4444-4444-4444-444444444444",
  "assigned_at": "2026-03-10T12:05:00Z",
  "user_id": "22222222-2222-2222-2222-222222222222",
  "role_id": "33333333-3333-3333-3333-333333333333"
}
```

---

#### 1.3.5 Table `user_operating_room`

**Purpose:** N:M user ↔ operating room. Feeds the floor supervisor (`or_charge`). The App **lists all** rooms (linked rooms **first**); **in-app notifications** fire only if the session/hold belongs to a room in this pairing.


| Field | Type | Nullable | Default | Meaning |
| --- | --- | --- | --- | --- |
| `id` | UUID | NO | `gen_random_uuid()` | PK. |
| `user_id` | UUID | NO | — | Assigned user. |
| `room_id` | UUID | NO | — | Assigned operating room. |
| `assigned_at` | TIMESTAMPTZ | NO | `now()` | When it was assigned. |


**References**


| Field | Points to | Relationship | Rationale | ON DELETE |
| --- | --- | --- | --- | --- |
| `user_id` | `user.id` | N:1 | Assignment owner. | `CASCADE` |
| `room_id` | `operating_room.id` (DS02) | N:1 | Physical room. | `CASCADE` |


**Constraints:** PK `id`; UNIQUE (`user_id`, `room_id`) = `uk_user_operating_room_user_room`.

```json
{
  "id": "15151515-0000-4000-8000-000000000001",
  "user_id": "22222222-2222-2222-2222-222222222226",
  "room_id": "12121212-0000-4000-8000-000000000001",
  "assigned_at": "2026-10-29T18:00:00Z"
}
```

---

#### 1.3.6 Redis · Authentication (Not a SQL Table)

Redis stores **temporary** login state. It does not replace `user`.


| Key (pattern) | Expected fields / value | TTL | Purpose |
| --- | --- | --- | --- |
| `auth:jwt:{jti}` | JSON or flag: `{ "user_id", "institution_id", "revoked" }` | Equal to JWT lifetime | Validate / revoke token by `jti`. |
| `auth:login_fail:{email_or_ip}` | Integer (counter) | Minutes (e.g., 15) | Brute-force rate limiting. |


**Complete example value in `auth:jwt:{jti}`**

```json
{
  "jti": "55555555-5555-5555-5555-555555555555",
  "user_id": "22222222-2222-2222-2222-222222222222",
  "institution_id": "11111111-1111-1111-1111-111111111111",
  "revoked": false,
  "exp": "2026-09-05T19:40:00Z"
}
```

**Complete example value in `auth:login_fail:...`**

```json
{
  "key": "auth:login_fail:ana.perez@demo.local",
  "fail_count": 2,
  "ttl_seconds": 900
}
```

---

#### 1.3.7 Business Rules

1. Login is permitted only if `user.active = true` and the institution is usable.
2. Password change → new `password_hash` + `password_updated_at`.
3. Successful login → update `last_login_at`; clear Redis failure counter; return `ui_preferences` to the client.
4. Logout / revocation → mark `revoked` or delete `auth:jwt:{jti}`.
5. Authenticated language/theme change → merge into `ui_preferences` + `updated_at` (do not add new columns for each UI preference).
6. `integration_client` (machine clients) does **not** live here → PEF-DS08.

---

### 1.4 Governance and Ownership


| Aspect | Owner |
| --- | --- |
| Standard owner | BackendAuthService / PEF Core |
| User provisioning | Institutional administration (portal) or bootstrap |
| Role assignment | Administration with IAM permission |
| Changes to this DS | Review with security if touching hashing, JWT, or email uniqueness |


**Downstream dependencies:** nearly the entire model references `user.id` or `institution.id`.

---

### 1.5 Standard Rationale

**Why this block is separate**

Without an identity standard, each screen invents "who the user is." Duplicate accounts, poorly stored passwords, and inconsistent role names quickly appear. Separating human identity (this DS) from clinical context (DS02) and machine clients (DS08) prevents conflating "Ana logging in," "the surgery patient," and "the HIS calling the API."

**Design decisions taken**

1. **One user → one institution.** Simplifies multi-tenant permissions and reporting. If someone works at two hospitals, two accounts are modeled (or an explicit bridge later; not today).
2. **Per-institution roles, not global roles.** Each site instantiates the product codes (`station_operator`, …) with its own UUID without affecting other sites.
3. **Globally unique login email.** One login string across the entire deployment; less ambiguity for support.
4. **Hash only in PostgreSQL; session state in Redis.** PG is the source of truth for the person. Redis can be flushed without losing users.
5. **Junction table `user_role`.** Supports multiple roles without brittle arrays or boolean permission columns.
6. **`parent_id` on institution.** Lightweight hierarchy without a separate `institution_group` table.
7. **Single `ui_preferences` JSONB** for locale, theme, and future UI preferences. Avoids schema migrations for every cosmetic toggle; business codes remain in English.

**Rejected alternatives**


| Alternative | Why not |
| --- | --- |
| Single global roles across the entire system | Conflicts with hospitals that name the same permission differently; forces migrations during onboarding. |
| Multi-institution N:M user from day 1 | More accurate long-term for large networks, but complicates JWT, auditing, and filters in v1. Deferred. |
| Store permissions as JSON on `user` | Fast to prototype; poor for querying "who has role X" and for auditing. |
| Server-cookie-only session without Redis | Valid for a small monolith; with multiple replicas/backends, JWT + Redis (or shared store) scales better. |
| LDAP/OIDC as the sole identity from day 1 | Desirable in a real hospital; the PEF core first needs predictable local identity. OIDC can be layered later without changing `user` UUIDs. |
| Merge `user` with `physician` | The surgery physician is not always the person logging into the station. They are distinct concepts (DS02). |
| Cookie-only language/theme, no field on `user` | Loses preferences across devices (portal vs. station) and when clearing browser data. |
| Separate `locale` column + `theme` column + … | Acceptable if they never grow; JSONB covers both today and leaves room without ALTER. |


---

## 2. Annex A – General Information


| Field | Value |
| --- | --- |
| Code | PEF-DS01 |
| Title | Identity and Credentials |
| Version | 0.2.2 |
| Status | draft |
| Stores | PostgreSQL (`institution`, `role`, `user`, `user_role`, `user_operating_room`); Redis (auth) |
| Out of scope | Patient, physician, operation; `integration_client`; ARCO privacy; `work_session` |
| Reference DDL | `esquema_base_datos_v2.md` §5.3 |
