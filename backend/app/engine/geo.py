"""Geodesy helpers and a coarse land mask.

The land mask exists for one reason: a facility master list that plots Central
Province health centres into the Bismarck Sea must fail loudly at import, not
silently produce a beautiful map that every person in the room knows is wrong.

The mask is deliberately coarse -- big landmasses as polygons, small islands as
circular buffers -- and it reports itself as coarse. It is a screening test with a
generous tolerance, not a coastline. Its job is to catch errors of hundreds of
kilometres, which is the error class that actually occurs in master lists.
"""

from __future__ import annotations

import math
from typing import Optional

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


def centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Arithmetic centroid of (lat, lon) points. Fine at country scale."""
    if not points:
        return (0.0, 0.0)
    return (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )


def point_in_ring(lat: float, lon: float, ring: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon. Ring is a list of (lon, lat) pairs."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def distance_to_ring_km(lat: float, lon: float, ring: list[tuple[float, float]]) -> float:
    """Approximate distance from a point to a polygon ring (0 if inside)."""
    if point_in_ring(lat, lon, ring):
        return 0.0
    return min(haversine_km(lat, lon, ring_lat, ring_lon) for ring_lon, ring_lat in ring)


def offshore_km(boundary: Optional[dict], lat: float, lon: float) -> Optional[float]:
    """Kilometres from the nearest modelled landmass, or None when the country carries
    no boundary.

    ``boundary`` is ``{"polygons": [ring, ...], "buffers": [(lat, lon, radius_km), ...]}``
    where a ring is a list of (lon, lat) pairs. Buffers cover small islands that are
    real places but too small to trace.

    None means "not checked", which is deliberately different from 0.0 meaning "on
    land". A country nobody has drawn yet loses the offshore screen rather than having
    every one of its coordinates rejected.
    """
    if not boundary:
        return None
    polygons = boundary.get("polygons") or []
    buffers = boundary.get("buffers") or []
    if not polygons and not buffers:
        return None

    best: Optional[float] = None
    for ring in polygons:
        distance = distance_to_ring_km(lat, lon, [(point[0], point[1]) for point in ring])
        best = distance if best is None else min(best, distance)
        if best == 0.0:
            return 0.0
    for entry in buffers:
        blat, blon, radius = entry[0], entry[1], entry[2]
        distance = max(0.0, haversine_km(lat, lon, blat, blon) - radius)
        best = distance if best is None else min(best, distance)
        if best == 0.0:
            return 0.0
    return best


def in_bbox(lat: float, lon: float, bbox: dict) -> bool:
    return (
        bbox["min_lat"] <= lat <= bbox["max_lat"] and bbox["min_lon"] <= lon <= bbox["max_lon"]
    )
