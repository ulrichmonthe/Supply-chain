"""Seasonal access.

``Edge.monthly_access`` is a 12-element vector in [0, 1]. 1.0 is a normal month,
0.5 means the lane carries half its usual throughput (a road passable only in dry
spells, a sea route disrupted by the southeast trades), 0.0 means impassable.

Because the vector lives on every edge, seasonality is not a feature that had to be
bolted on -- it is a lookup. The month slider in the UI is this module.
"""

from __future__ import annotations

from typing import Optional

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

#: Named seasonal profiles used by the PNG seed. Each is a 12-vector, January first.
#: PNG's northwest monsoon runs roughly December-March and closes unsealed highland
#: and coastal roads; the southeast trades (May-October) make small-boat coastal runs
#: unreliable on exposed coasts.
PROFILES: dict[str, list[float]] = {
    "all_weather": [1.0] * 12,
    "highlands_unsealed": [0.25, 0.00, 0.25, 0.55, 0.90, 1.0, 1.0, 1.0, 1.0, 0.90, 0.65, 0.40],
    "highlands_sealed": [0.80, 0.75, 0.80, 0.95, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.95, 0.85],
    "lowland_floodplain": [0.00, 0.00, 0.00, 0.35, 0.85, 1.0, 1.0, 1.0, 1.0, 0.85, 0.45, 0.15],
    "coastal_road": [0.60, 0.55, 0.65, 0.85, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.90, 0.70],
    "sea_sheltered": [0.95, 0.90, 0.95, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.95],
    "sea_exposed": [0.90, 0.85, 0.90, 0.95, 0.70, 0.55, 0.50, 0.55, 0.70, 0.90, 0.95, 0.95],
    "river_navigable": [1.0, 1.0, 1.0, 1.0, 0.90, 0.70, 0.55, 0.50, 0.55, 0.75, 0.95, 1.0],
    "air_all_year": [0.95, 0.90, 0.90, 0.95, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.95, 0.95],
    "air_highlands": [0.75, 0.70, 0.75, 0.85, 0.95, 1.0, 1.0, 1.0, 0.95, 0.90, 0.85, 0.80],
}

#: Cost multipliers that go with the access profiles: a wet-season delivery costs more
#: even when it is possible (longer detours, more vehicle days, more spoilage).
COST_PROFILES: dict[str, list[float]] = {
    "all_weather": [1.0] * 12,
    "highlands_unsealed": [1.6, 1.7, 1.6, 1.3, 1.05, 1.0, 1.0, 1.0, 1.0, 1.05, 1.25, 1.45],
    "highlands_sealed": [1.15, 1.2, 1.15, 1.05, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.05, 1.1],
    "lowland_floodplain": [1.9, 2.2, 1.9, 1.4, 1.1, 1.0, 1.0, 1.0, 1.0, 1.1, 1.35, 1.6],
    "coastal_road": [1.35, 1.4, 1.3, 1.1, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.05, 1.2],
    "sea_sheltered": [1.05, 1.1, 1.05, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.05],
    "sea_exposed": [1.1, 1.15, 1.1, 1.05, 1.25, 1.4, 1.5, 1.4, 1.25, 1.05, 1.0, 1.05],
    "river_navigable": [1.0, 1.0, 1.0, 1.0, 1.1, 1.3, 1.45, 1.5, 1.45, 1.2, 1.05, 1.0],
    "air_all_year": [1.05, 1.1, 1.1, 1.05, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.05, 1.05],
    "air_highlands": [1.3, 1.35, 1.3, 1.15, 1.05, 1.0, 1.0, 1.0, 1.05, 1.1, 1.15, 1.25],
}


def access(edge, month: int) -> float:
    """Access factor for a 1-12 month, defaulting to open when the vector is absent."""
    vector = edge.monthly_access or [1.0] * 12
    if len(vector) != 12:
        return 1.0
    return max(0.0, min(1.0, float(vector[month - 1])))


def cost_multiplier(edge, month: int) -> float:
    vector = edge.monthly_cost_multiplier or [1.0] * 12
    if len(vector) != 12:
        return 1.0
    return max(0.0, float(vector[month - 1]))


def annual_access(edge) -> float:
    """Mean access across the year -- the factor used for annualised runs."""
    vector = edge.monthly_access or [1.0] * 12
    return sum(vector) / len(vector) if vector else 1.0


def annual_cost_multiplier(edge) -> float:
    vector = edge.monthly_cost_multiplier or [1.0] * 12
    return sum(vector) / len(vector) if vector else 1.0


def closed_months(edge, threshold: float = 0.0) -> list[int]:
    """1-based months in which the lane is at or below `threshold` access."""
    vector = edge.monthly_access or [1.0] * 12
    return [i + 1 for i, value in enumerate(vector) if value <= threshold]


def profiles_for(config: Optional[dict] = None) -> dict:
    """Named access profiles for a country: its own, over the built-in ones.

    A monsoon is not a monsoon everywhere. A country that knows its own wet season puts
    twelve numbers under ``seasonal_profiles`` in its config and the importer can then
    refer to them by name, exactly like the built-ins.
    """
    return {**PROFILES, **((config or {}).get("seasonal_profiles") or {})}


def profile(name: str) -> list[float]:
    return list(PROFILES.get(name, PROFILES["all_weather"]))


def cost_profile(name: str) -> list[float]:
    return list(COST_PROFILES.get(name, COST_PROFILES["all_weather"]))
