# PEF Locust load suite

HTTP load scenarios by surface type, plus performance profiles (baseline → soak).

This folder measures concurrent traffic against a running demo stack.

## Prerequisites

1. Integral stack up (`pef_up.ps1` or compose). Gateway required for API types (`:8080`).
2. Python venv with Locust:

```powershell
cd Locust
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python validate_suite.py
```

## Scenario types (surfaces)

| Type | Host | What it stresses | Default users | Default time |
| --- | --- | --- | --- | --- |
| `auth_api` | Gateway | login / me / refresh (JSON body) | 2 (login rate-limited) | 3m |
| `operator_portal` | Portal `:5000` | OP session list / history / detail | 8 | 4m |
| `spd_portal` | Portal | SPD queue / history / detail | 6 | 4m |
| `admin_portal` | Portal | IT admin + SPD kits (primary-role RACI) | 4 | 4m |
| `floor_api` | Gateway | Floor JWT: sessions + me + notifications | 6 | 3m |
| `catalog_api` | Gateway | Catalog families / kits / categories | 4 | 3m |
| `session_api` | Gateway | Session list / detail (OP + SPD) | 4 | 3m |
| `health` | Gateway | `/healthz` + public OpenAPI | 10 | 2m |
| `mixed` | Portal (+ Gateway catalog) | OP + SPD + catalog together | 12 | 5m |

## Performance profiles (LoadTestShape, mixed traffic)

| Profile | Intent | Shape peak | Default time (cap 10m) |
| --- | --- | --- | --- |
| `baseline` | Calm latency reference | 5 users | 3m |
| `load` | Sustained expected load | 20 users | 5m |
| `spike` | Burst then recovery | 40 users | 4m |
| `stress` | Ramp toward breaking point | 50 users | 8m |
| `soak` | Endurance at moderate load | 15 users | 10m |

Accounts: demo / Locust / `gen.*` users from `data/seeds/DEMO_CREDENTIALS.md`.
`admin_portal` kits use `spd_supervisor` accounts (portal JWT primary-role check; dual it_admin does not unlock kits).
API types retry login on HTTP 429 so users are not left tokenless (avoids 401 cascades).

Out of scope (on purpose): Vision WS/GPU, video upload, inventory mutations, SPD close writes.

## Run one type (saves report)

```powershell
.\Locust\run_type.ps1 -Type operator_portal
.\Locust\run_type.ps1 -Type baseline
.\Locust\run_type.ps1 -Type auth_api -Users 2 -SpawnRate 1 -RunTime 3m
.\Locust\run_type.ps1 -Type health -Headless:$false
```

`-RunTime` is soft-capped at **10 minutes**.

## Run all types

```powershell
.\Locust\run_all.ps1
.\Locust\run_all.ps1 -OnlyProfiles
.\Locust\run_all.ps1 -OnlySurfaces
```

Env overrides: `PEF_GATEWAY_URL`, `PEF_WEB_URL`, `PEF_AUTH_URL`.

## Validate without load

```powershell
cd Locust
python validate_suite.py
```
