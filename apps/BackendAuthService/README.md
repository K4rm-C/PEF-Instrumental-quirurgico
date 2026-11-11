# PEF Auth / Identity Service

Issues and validates JWT for PEF users (PEF-DS01) and stores the session state in Redis
(`auth:jwt:{jti}`, `auth:login_fail:{identity}`). Port 5001 (local process).

It exposes two surfaces that share the same internal rules in `main.py`
(`authenticate_credentials`, `verify_token`, `_issue_pair_for_user`, `rotate_refresh`, `revoke_token`):

| Surface | Clients | Tokens | Errors | Contract |
| :--- | :--- | :--- | :--- | :--- |
| `/api/v1/auth/*` + `/health/{live,ready,database,redis}` | Android (JSON), Desktop (XML), microservices | Response body; `Authorization: Bearer` | DS09 envelope (JSON/XML), `X-Correlation-ID` | [PEF-DS09 §1.3.2](../../data/DataStandards/PEF-DS09-API-Exchange-Contracts.md), [openapi.yaml](openapi.yaml) |
| Legacy unversioned routes | BackendWebFlask / browser | HttpOnly cookies (`access_token`, `refresh_token`) or Bearer | `{"error": "<message>"}` (messages mapped by the sign-in page) | below |

## API v1 (external clients)

| Method | Path | Auth | Response |
| :--- | :--- | :--- | :--- |
| POST | `/api/v1/auth/login` | `{email, password}` | `access_token`, `refresh_token`, `token_type`, `expires_in`, `user` |
| POST | `/api/v1/auth/refresh` | `{refresh_token}` | new `access_token` + `refresh_token` (rotation) |
| POST | `/api/v1/auth/logout` | Bearer access + `{refresh_token}` (required, same user) | 204; both revoked |
| GET | `/api/v1/auth/me` | Bearer access | `user` object (PostgreSQL) |
| GET | `/api/v1/auth/permissions` | Bearer access | `{user_id, institution_id, roles}` |

```bash
curl -s -X POST http://127.0.0.1:5001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"operator@instrumed.com","password":"DemopwdOP78!"}'

curl -s -X POST http://127.0.0.1:5001/api/v1/auth/login \
  -H "Content-Type: application/xml" -H "Accept: application/xml" \
  -d '<login_request><email>operator@instrumed.com</email><password>DemopwdOP78!</password></login_request>'
```

Only the configured `JWT_ALGORITHM` (HS256/HS384/HS512, default HS256) is accepted on decode; any
other algorithm, including `none`, is rejected. CatalogService applies the same rule.

## Identity administration (DS09 §1.3.2 complement v0.1.4)

Users, roles, role assignment and passwords under `/api/v1/auth` (`identity_admin.py`): the rules
the portal's Administration ran in-process, with access_audit in the same transaction, `it_admin`
required (403 + `DENIED_*` otherwise), "last active administrator" protection and revocation of
every Redis session of a deactivated / moved / reset account (`auth:user_tokens:{user_id}`).
Contract: `openapi.yaml` (served at `/api/v1/auth/openapi.yaml`, Swagger UI at `/api/v1/auth/docs`)
and `schemas/identity.xsd` (`/api/v1/auth/schemas/identity.xsd`, every XML body is validated).
Portal integration and Docker: `GUIA_AUTH.md`.

## Legacy routes (BackendWebFlask)

The shared token rules now also require the user's institution to be active, and the roles
returned by `/login` and `/verify` (and used by `/refresh`) are only those of the user's institution.
`POST /register` (unauthenticated self-registration, no roles or audit) answers 404 unless
`AUTH_ENABLE_PUBLIC_REGISTER=true`; accounts are created by an administrator.

`POST /login`, `POST /refresh` (`?touch=0` silent heartbeat), `POST /logout`, `GET /verify`,
`POST /preferences` (partial merge of `ui_preferences`), `POST /register`, `POST /password/forgot`,
`POST /password/reset`, `POST /password/change`, `GET /health` (`{"ok", "checks"}`, used by
`scripts/smoke_auth.ps1`). `/health/redis` now answers in the DS09 health format.

## Run and test

```powershell
cd apps\BackendAuthService
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py

pip install pytest   # lxml comes with requirements.txt (XSD validation)
python -m pytest tests
```

`tests/test_preferences.py` uses fakes. `tests/test_api_v1.py` and `tests/test_identity_admin.py` need PostgreSQL and Redis (skipped
otherwise): it runs inside a rolled-back transaction, creates its own institutions/users/roles and
deletes every Redis key it writes. `AUTH_TEST_DATABASE_URL` / `AUTH_TEST_REDIS_URL` override the URLs.

Cross-service smoke (Auth on :5001 + Catalog on :5003):
`python scripts/smoke_auth_catalog_api.py` from the repository root.
