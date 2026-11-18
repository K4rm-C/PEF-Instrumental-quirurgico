# Credenciales demo (solo desarrollo / Locust / QA)

Los hashes en BD son Werkzeug `pbkdf2:sha256` (misma familia que `BackendAuthService`).  
**No usar en producción.** Este archivo va en el repo a propósito para que cualquiera pueda probar tras un `down -v`.

## Canónicos (`003_seed_demo.sql`)

| Email | Password | Rol(es) | Superficie |
| --- | --- | --- | --- |
| `operator@instrumed.com` | `DemopwdOP78!` | `station_operator` + `or_charge` | Web OP / App |
| `operador2@instrumed.com` | `DemopwdOP278!` | `station_operator` | Web OP |
| `supervisor@instrumed.com` | `DemopwdSPD78!` | `spd_supervisor` | Web SPD |
| `supervisor2@instrumed.com` | `DemopwdSPD278!` | `spd_supervisor` | Web SPD |
| `admin@instrumed.com` | `DemopwdADM78!` | `it_admin` | Web Admin |
| `floor@instrumed.com` | `DemopwdFLR78!` | `or_charge` | **App móvil (cuenta estándar)** |

## Locust / multi-usuario (fusionado en `003` + re-apply `004_seed_demo_locust.sql`)

| Email | Password | Rol(es) |
| --- | --- | --- |
| `ana.ruiz@instrumed.com` | `DemopwdOP378!` | `station_operator` |
| `luis.pena@instrumed.com` | `DemopwdOP478!` | `station_operator` |
| `carla.soto@instrumed.com` | `DemopwdSPD378!` | `spd_supervisor` |
| `ti.extra@instrumed.com` | `DemopwdADM278!` | `it_admin` |
| `nora.diaz@instrumed.com` | `DemopwdFLR278!` | `or_charge` |
| `dual.admin.spd@instrumed.com` | `DemopwdADMxSPD78!` | `it_admin` + `spd_supervisor` |

## Generados por `scripts/seed_demo_volume.py`

| Email | Password | Rol |
| --- | --- | --- |
| `gen.opN@instrumed.com` | `DemopwdGEN78!` | `station_operator` |
| `gen.spdN@instrumed.com` | `DemopwdGEN78!` | `spd_supervisor` |
| `gen.floorN@instrumed.com` | `DemopwdGEN78!` | `or_charge` |

Cantidad controlada por `SEED_EXTRA_*_USERS` en `.env` (ver `.env.example`).
