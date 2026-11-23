"""
Profile: load
Sustained expected concurrent load (mixed surfaces). LoadShape (~5 min, up to 20 users).
"""
from __future__ import annotations

from common.shapes import LoadShape  # noqa: F401
from scenarios.mixed import MixedCatalogUser, MixedOperatorUser, MixedSpdUser  # noqa: F401
