"""Runtime hosts and credentials for Locust (env-overridable, demo defaults)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
REPORTS_DIR = ROOT / "reports"

# Prefer Gateway for API scenarios; portal for HTML RF surfaces.
GATEWAY_URL = os.getenv("PEF_GATEWAY_URL", os.getenv("PEF_LOCUST_HOST", "http://127.0.0.1:8080")).rstrip("/")
WEB_URL = os.getenv("PEF_WEB_URL", "http://127.0.0.1:5000").rstrip("/")
AUTH_URL = os.getenv("PEF_AUTH_URL", "http://127.0.0.1:5001").rstrip("/")

_GEN_PWD = "DemopwdGEN78!"

# Demo passwords from data/seeds/DEMO_CREDENTIALS.md (dev/QA only).
# Includes Locust seed accounts + seed_demo_volume gen.* users for higher concurrency.
OPERATORS = [
    ("operator@instrumed.com", "DemopwdOP78!"),
    ("operador2@instrumed.com", "DemopwdOP278!"),
    ("ana.ruiz@instrumed.com", "DemopwdOP378!"),
    ("luis.pena@instrumed.com", "DemopwdOP478!"),
    ("gen.op1@instrumed.com", _GEN_PWD),
    ("gen.op2@instrumed.com", _GEN_PWD),
    ("gen.op3@instrumed.com", _GEN_PWD),
    ("gen.op4@instrumed.com", _GEN_PWD),
]
SUPERVISORS = [
    ("supervisor@instrumed.com", "DemopwdSPD78!"),
    ("supervisor2@instrumed.com", "DemopwdSPD278!"),
    ("carla.soto@instrumed.com", "DemopwdSPD378!"),
    ("gen.spd1@instrumed.com", _GEN_PWD),
    ("gen.spd2@instrumed.com", _GEN_PWD),
]
# Pure it_admin (no kit RACI). Use for users/dashboard-only surfaces.
ADMINS = [
    ("admin@instrumed.com", "DemopwdADM78!"),
    ("ti.extra@instrumed.com", "DemopwdADM278!"),
]
# it_admin + spd_supervisor — required for /admin/kits (SPD RACI) and other admin pages.
DUAL_ADMINS = [
    ("dual.admin.spd@instrumed.com", "DemopwdADMxSPD78!"),
]
FLOOR = [
    ("floor@instrumed.com", "DemopwdFLR78!"),
    ("nora.diaz@instrumed.com", "DemopwdFLR278!"),
    ("gen.floor1@instrumed.com", _GEN_PWD),
]

# Gateway auth login is rate-limited (~10 req/min per IP). Keep auth_api gentle.
DEFAULT_USERS = {
    "auth_api": 2,
    "operator_portal": 8,
    "spd_portal": 6,
    "admin_portal": 4,
    "floor_api": 6,
    "catalog_api": 4,
    "session_api": 4,
    "health": 10,
    "mixed": 12,
    # Load profiles (use mixed traffic + optional LoadTestShape).
    "baseline": 5,
    "load": 20,
    "spike": 40,
    "stress": 50,
    "soak": 15,
}

# Default run times (max 10m per type). Override with -RunTime.
DEFAULT_RUNTIME = {
    "auth_api": "3m",
    "operator_portal": "4m",
    "spd_portal": "4m",
    "admin_portal": "4m",
    "floor_api": "3m",
    "catalog_api": "3m",
    "session_api": "3m",
    "health": "2m",
    "mixed": "5m",
    "baseline": "3m",
    "load": "5m",
    "spike": "4m",
    "stress": "8m",
    "soak": "10m",
}

SCENARIO_TYPES = tuple(DEFAULT_USERS.keys())
SURFACE_TYPES = (
    "auth_api",
    "operator_portal",
    "spd_portal",
    "admin_portal",
    "floor_api",
    "catalog_api",
    "session_api",
    "health",
    "mixed",
)
PROFILE_TYPES = ("baseline", "load", "spike", "stress", "soak")


def pick(pool: list[tuple[str, str]], index: int) -> tuple[str, str]:
    return pool[index % len(pool)]
