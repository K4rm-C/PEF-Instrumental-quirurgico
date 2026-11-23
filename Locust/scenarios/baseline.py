"""
Profile: baseline
Calm concurrent traffic (mixed OP/SPD/catalog) to measure steady latency.
Uses BaselineShape (~3 min, 5 users).
"""
from __future__ import annotations

from common.shapes import BaselineShape  # noqa: F401 — Locust discovers LoadTestShape
from scenarios.mixed import MixedCatalogUser, MixedOperatorUser, MixedSpdUser  # noqa: F401
