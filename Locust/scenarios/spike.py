"""
Profile: spike
Sudden concurrency peak then recovery. SpikeShape (~4 min, peak 40 users).
"""
from __future__ import annotations

from common.shapes import SpikeShape  # noqa: F401
from scenarios.mixed import MixedCatalogUser, MixedOperatorUser, MixedSpdUser  # noqa: F401
