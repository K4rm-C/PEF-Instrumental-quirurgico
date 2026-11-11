"""Kit-purpose (instrument type) colors for overlay + UI alignment.

Type (category-level label) = saturated hue.
Family (named instrument) = same hue, less saturated / softer.
"""

from __future__ import annotations

# BGR for OpenCV. Tuned for contrast on blue drapes + white trays.
_TYPE_BGR: dict[str, tuple[int, int, int]] = {
    "cutting": (60, 70, 220),       # rose/red
    "dissection": (40, 160, 230),   # amber
    "grasping": (150, 140, 40),     # teal
    "hemostasis": (200, 90, 180),   # violet
    "retraction": (30, 165, 255),   # orange-gold
    "suturing": (220, 140, 50),     # blue
    "suction": (160, 180, 80),      # cyan-green
    "other": (148, 163, 184),       # slate
}

_SLATE_BGR = (148, 163, 184)


def _norm_key(category: str | None) -> str:
    if not category:
        return "other"
    key = str(category).strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "cut": "cutting",
        "grasp": "grasping",
        "hemo": "hemostasis",
        "retract": "retraction",
        "suture": "suturing",
    }
    if key in _TYPE_BGR:
        return key
    for prefix, canon in aliases.items():
        if key.startswith(prefix):
            return canon
    return "other"


def type_bgr(category: str | None) -> tuple[int, int, int]:
    """Saturated color for type/category-level labels."""
    return _TYPE_BGR.get(_norm_key(category), _SLATE_BGR)


def family_bgr(category: str | None) -> tuple[int, int, int]:
    """Softer (less saturated) color for family-named boxes — same hue as type."""
    b, g, r = type_bgr(category)
    # Blend toward light gray for readability on dark overlay fill.
    t = 0.42
    return (
        int(b * (1 - t) + 200 * t),
        int(g * (1 - t) + 200 * t),
        int(r * (1 - t) + 200 * t),
    )


def unnamed_bgr() -> tuple[int, int, int]:
    return _SLATE_BGR


def overlay_bgr(
    *,
    category: str | None,
    family_code: str | None,
    high_confidence: bool,
    sticky_name: bool,
    resolution_reason: str | None,
) -> tuple[int, int, int]:
    """
    Type label / weak naming → saturated type color.
    Family named (high conf or sticky identity) → softer family color.
    Type-degraded without usable category → slate.
    """
    reason = resolution_reason or ""
    if reason == "type_degraded" and not family_code:
        return type_bgr(category) if category and _norm_key(category) != "other" else unnamed_bgr()
    if reason == "type_degraded":
        # Object counted only as type.
        return type_bgr(category)
    if family_code and (high_confidence or sticky_name):
        return family_bgr(category)
    if family_code:
        # Assigned to a cupo but naming still type-level (weak conf).
        return type_bgr(category)
    return unnamed_bgr()
