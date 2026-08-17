"""The distance cascade.

Every distance in the model is produced by exactly one of four methods, and the
method travels with the number for the rest of its life:

    manual        a human typed it, usually after a field interview        confidence 0.95
    osrm          routed on a self-hosted OSRM graph                        confidence 0.85
    detour_factor great-circle inflated by a terrain-specific factor        confidence 0.45-0.60
    great_circle  raw straight line, only ever correct for air              confidence 0.90 (air)

This is not bookkeeping for its own sake. In a validation workshop a provincial
health manager will say "that road takes six hours, not two". You need to answer
"that came from a 1.6x detour factor on a 90 km straight line, so let's overwrite
it" -- and then actually overwrite it, from the UI, with the override recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

from ..config import settings
from .geo import haversine_km

#: Straight-line to real-path inflation, by terrain class. Highland roads in PNG
#: switchback severely; the mainland highway network is comparatively direct.
DETOUR_FACTORS: dict[str, float] = {
    "mainland_road": 1.35,
    "highlands_road": 1.75,
    "coastal_road": 1.45,
    "island": 1.20,
    "riverine": 1.30,
    "remote_air_only": 1.00,
}

#: Average achievable speed (km/h) door to door, including loading and stops.
MODE_SPEEDS: dict[str, dict[str, float]] = {
    "road": {
        "mainland_road": 45.0,
        "highlands_road": 28.0,
        "coastal_road": 35.0,
        "island": 30.0,
        "riverine": 25.0,
        "remote_air_only": 20.0,
    },
    "sea": {"_default": 18.0},
    "river": {"_default": 14.0},
    "air": {"_default": 260.0},
    "foot": {"_default": 4.0},
    "drone": {"_default": 60.0},
}

#: Sea legs follow coastlines and channels rather than straight lines.
SEA_DETOUR = 1.22
RIVER_DETOUR = 1.55


@dataclass
class DistanceResult:
    distance_km: float
    travel_time_hr: float
    method: str
    confidence: float
    note: str

    def as_edge_fields(self) -> dict:
        return {
            "distance_km": round(self.distance_km, 2),
            "base_travel_time_hr": round(self.travel_time_hr, 2),
            "distance_method": self.method,
            "distance_confidence": self.confidence,
            "distance_note": self.note,
        }


def _speed(mode: str, terrain_class: str) -> float:
    table = MODE_SPEEDS.get(mode, MODE_SPEEDS["road"])
    return table.get(terrain_class, table.get("_default", 40.0))


def _osrm_route(lat1: float, lon1: float, lat2: float, lon2: float) -> Optional[tuple[float,float]]:
    """Query a self-hosted OSRM. Returns (km, hours) or None if unavailable.

    Self-hosted per region on purpose: OSM coverage in the Pacific is thin and the
    graph has to be patched with field-collected roads. A hosted API will not let
    you do that.
    """
    if not settings.osrm_url:
        return None
    url = f"{settings.osrm_url.rstrip('/')}/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"
    try:
        resp = httpx.get(url, params={"overview": "false"}, timeout=settings.osrm_timeout_s)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != "Ok" or not payload.get("routes"):
            return None
        route = payload["routes"][0]
        return route["distance"] / 1000.0, route["duration"] / 3600.0
    except Exception:  # noqa: BLE001 - the cascade must never fail hard on a routing miss
        return None


def resolve_distance(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    *,
    mode: str = "road",
    terrain_class: str = "mainland_road",
    manual_km: Optional[float] = None,
    manual_hours: Optional[float] = None,
    manual_note: str = "",
) -> DistanceResult:
    """Run the cascade and return the winning number with its provenance."""
    straight = haversine_km(lat1, lon1, lat2, lon2)

    if manual_km is not None and manual_km > 0:
        hours = manual_hours if manual_hours else manual_km / _speed(mode, terrain_class)
        return DistanceResult(
            manual_km,
            hours,
            "manual",
            0.95,
            manual_note or "Operator override; supersedes all computed values.",
        )

    if mode == "road":
        routed = _osrm_route(lat1, lon1, lat2, lon2)
        if routed:
            km, hours = routed
            return DistanceResult(km, hours, "osrm", 0.85, "Routed on self-hosted OSRM graph.")
        factor = DETOUR_FACTORS.get(terrain_class, settings.default_detour_factor)
        km = straight * factor
        return DistanceResult(
            km,
            km / _speed(mode, terrain_class),
            "detour_factor",
            0.60 if terrain_class in DETOUR_FACTORS else 0.45,
            f"Great circle {straight:.0f} km x {factor:.2f} detour factor ({terrain_class}). "
            "Replace with OSRM or a field measurement before publishing.",
        )

    if mode in ("sea", "river"):
        factor = SEA_DETOUR if mode == "sea" else RIVER_DETOUR
        km = straight * factor
        return DistanceResult(
            km,
            km / _speed(mode, terrain_class),
            "detour_factor",
            0.55,
            f"Great circle {straight:.0f} km x {factor:.2f} to approximate the "
            f"{'coastal' if mode == 'sea' else 'river'} track.",
        )

    if mode == "air":
        return DistanceResult(
            straight,
            straight / _speed(mode, terrain_class),
            "great_circle",
            0.90,
            "Air legs are flown close to the great circle.",
        )

    km = straight * settings.default_detour_factor
    return DistanceResult(
        km,
        km / _speed(mode, terrain_class),
        "detour_factor",
        0.40,
        f"No mode-specific rule for '{mode}'; default detour factor applied.",
    )


#: Ranking used by the UI to show what the cascade would fall back to.
CASCADE_ORDER = ["manual", "osrm", "detour_factor", "great_circle"]


def cascade_summary(edges: list) -> dict:
    """Distribution of distance methods across a network, for the provenance panel."""
    counts: dict[str, int] = {method: 0 for method in CASCADE_ORDER}
    weighted_conf = 0.0
    total_km = 0.0
    for edge in edges:
        counts[edge.distance_method] = counts.get(edge.distance_method, 0) + 1
        weighted_conf += edge.distance_confidence * edge.distance_km
        total_km += edge.distance_km
    return {
        "counts": counts,
        "total_edges": len(edges),
        "km_weighted_confidence": round(weighted_conf / total_km, 3) if total_km else 0.0,
        "osrm_configured": bool(settings.osrm_url),
    }
