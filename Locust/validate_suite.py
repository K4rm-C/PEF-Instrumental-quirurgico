#!/usr/bin/env python3
"""Validate Locust suite structure and imports WITHOUT starting a load test.

Usage (from Locust/):
  python validate_suite.py

Exit 0 when every scenario type is importable and Locust is installed.
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

EXPECTED = (
    "auth_api",
    "operator_portal",
    "spd_portal",
    "admin_portal",
    "floor_api",
    "catalog_api",
    "session_api",
    "health",
    "mixed",
    "baseline",
    "load",
    "spike",
    "stress",
    "soak",
)

PROFILE_TYPES = ("baseline", "load", "spike", "stress", "soak")


def main() -> int:
    errors: list[str] = []

    try:
        import locust  # noqa: F401
    except ImportError:
        errors.append("locust is not installed (pip install -r Locust/requirements.txt)")

    from common.config import PROFILE_TYPES as CFG_PROFILES
    from common.config import SCENARIO_TYPES

    if tuple(SCENARIO_TYPES) != EXPECTED:
        errors.append(f"SCENARIO_TYPES mismatch: {SCENARIO_TYPES!r} != {EXPECTED!r}")
    if tuple(CFG_PROFILES) != PROFILE_TYPES:
        errors.append(f"PROFILE_TYPES mismatch: {CFG_PROFILES!r} != {PROFILE_TYPES!r}")

    scenarios_dir = ROOT / "scenarios"
    for name in EXPECTED:
        path = scenarios_dir / f"{name}.py"
        if not path.is_file():
            errors.append(f"missing locustfile: {path.relative_to(ROOT)}")
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"syntax error in {name}.py: {exc}")
            continue
        has_user = any(
            isinstance(node, ast.ClassDef)
            and any(
                (isinstance(b, ast.Name) and b.id == "HttpUser")
                or (isinstance(b, ast.Attribute) and b.attr == "HttpUser")
                for b in node.bases
            )
            for node in tree.body
        )
        # Profile locustfiles import HttpUser classes from mixed; allow ImportFrom.
        if not has_user and name in PROFILE_TYPES:
            has_import_users = any(
                isinstance(node, ast.ImportFrom)
                and node.module
                and "mixed" in node.module
                and any(a.name.endswith("User") for a in (node.names or []))
                for node in tree.body
            )
            if not has_import_users:
                errors.append(f"{name}.py profile must import mixed *User classes")
        elif not has_user:
            errors.append(f"{name}.py has no HttpUser subclass")

        if "locust" in sys.modules:
            try:
                importlib.import_module(f"scenarios.{name}")
            except Exception as exc:  # noqa: BLE001 — collect all import failures
                errors.append(f"import scenarios.{name} failed: {exc}")

    for script in ("run_type.ps1", "run_all.ps1"):
        if not (ROOT / script).is_file():
            errors.append(f"missing {script}")

    reports = ROOT / "reports"
    if not reports.is_dir():
        errors.append("missing reports/ directory")

    if errors:
        print("FAIL Locust suite validation")
        for item in errors:
            print(f"  - {item}")
        return 1

    print("OK Locust suite ready")
    print(f"  types ({len(EXPECTED)}): {', '.join(EXPECTED)}")
    print(f"  profiles: {', '.join(PROFILE_TYPES)}")
    print("  runner: run_type.ps1 / run_all.ps1")
    print("  reports: reports/<type>/<timestamp>/ (English summary.txt + Locust HTML/CSV)")
    print("  note: this script does not execute load tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
