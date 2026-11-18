# Demo credentials (plaintext reference — NOT for production)

BD stores Werkzeug pbkdf2 hashes only. This file is for local Locust / QA.

## Canonical demo (003)

| Email | Password | Role(s) | Surface |
| --- | --- | --- | --- |
| `operator@instrumed.com` | `DemopwdOP78!` | station_operator (+ or_charge via 004) | Web OP / App |
| `operador2@instrumed.com` | `DemopwdOP278!` | station_operator | Web OP |
| `supervisor@instrumed.com` | `DemopwdSPD78!` | spd_supervisor | Web SPD |
| `supervisor2@instrumed.com` | `DemopwdSPD278!` | spd_supervisor | Web SPD |
| `admin@instrumed.com` | `DemopwdADM78!` | it_admin | Web Admin |
| `floor@instrumed.com` | `DemopwdFLR78!` | or_charge (Floor Supervisor) | **Mobile App (standard; institution clinical reads)** |

## Locust extras (004)

| Email | Password | Role(s) |
| --- | --- | --- |
| `ana.ruiz@instrumed.com` | `DemopwdOP378!` | station_operator |
| `luis.pena@instrumed.com` | `DemopwdOP478!` | station_operator |
| `carla.soto@instrumed.com` | `DemopwdSPD378!` | spd_supervisor |
| `ti.extra@instrumed.com` | `DemopwdADM278!` | it_admin |
| `nora.diaz@instrumed.com` | `DemopwdFLR278!` | or_charge |
| `dual.admin.spd@instrumed.com` | `DemopwdADMxSPD78!` | it_admin + spd_supervisor (web primary Admin) |
