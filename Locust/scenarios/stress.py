"""
Profile: stress
Ramp to high concurrency looking for degradation. StressShape (~8 min, up to 50 users).
"""
from __future__ import annotations

from common.shapes import StressShape  # noqa: F401
from scenarios.mixed import MixedCatalogUser, MixedOperatorUser, MixedSpdUser  # noqa: F401
