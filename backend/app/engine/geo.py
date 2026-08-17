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


# --- PNG coarse land mask ---------------------------------------------------------
# Rings are (lon, lat). Traced from well-known coastal settlements; accurate to roughly
# 10-25 km at the coast, which is well inside the tolerance we apply below.

PNG_MAINLAND = [
    # North coast, west to east: Wutung on the border, Vanimo, Aitape, Wewak,
    # the Sepik mouth, Bogia, Madang, the Rai Coast.
    (140.97, -2.61), (141.30, -2.66), (142.00, -2.98), (142.35, -3.10),
    (143.00, -3.36), (143.63, -3.48), (144.20, -3.72), (144.62, -3.88),
    (145.02, -4.22), (145.42, -4.58), (145.82, -5.16), (146.05, -5.32),
    (146.50, -5.55),
    # Huon Peninsula: north shore out to Sialum, round the point, back to Lae.
    (147.20, -5.86), (147.55, -6.00), (147.92, -6.32), (147.88, -6.66),
    (147.40, -6.76), (146.96, -6.72),
    # Morobe and Oro coasts, south-east to Milne Bay and East Cape.
    (147.08, -7.04), (147.60, -7.78), (148.25, -8.08), (148.52, -8.90),
    (149.35, -9.10), (150.05, -9.82), (150.52, -10.20), (150.92, -10.24),
    # South coast, east to west: Alotau, Abau, Port Moresby, Yule Island, Kerema.
    (150.45, -10.38), (149.90, -10.36), (149.00, -10.16), (148.20, -10.08),
    (147.80, -10.00), (147.18, -9.48), (146.53, -8.84), (145.78, -7.98),
    # Gulf of Papua and the Fly delta, out to the border at Torres Strait.
    (145.00, -7.96), (144.00, -7.82), (143.60, -8.12), (143.30, -8.62),
    (142.90, -8.82), (141.80, -8.62), (141.00, -9.13),
    # Western border, running back north along 141°E.
    (141.00, -6.00),
]

NEW_BRITAIN = [
    # North coast, Cape Gloucester east past the Willaumez Peninsula to the
    # Gazelle Peninsula and Rabaul.
    (148.42, -5.45), (149.30, -5.42), (150.00, -4.98), (150.30, -5.30),
    (151.00, -5.05), (151.60, -4.55), (152.20, -4.10), (152.45, -4.28),
    # East and south coasts, Kokopo round to Pomio, Gasmata and Kandrian.
    (152.35, -4.55), (151.90, -5.15), (151.60, -5.75), (151.30, -6.05),
    (150.80, -6.20), (150.20, -6.40), (149.40, -6.25), (148.70, -5.90),
]

BOUGAINVILLE = [
    (154.55, -5.20), (155.00, -5.55), (155.40, -6.00), (155.80, -6.50),
    (156.05, -6.85), (155.75, -7.05), (155.35, -6.75), (154.95, -6.20),
    (154.60, -5.60),
]

PNG_POLYGONS = [PNG_MAINLAND, NEW_BRITAIN, BOUGAINVILLE]

# Smaller islands and narrow chains: (lat, lon, radius_km). A circular buffer is a
# better model than a bad polygon for a 10 km wide island.
PNG_ISLAND_BUFFERS: list[tuple[float, float, float]] = [
    (-2.57, 150.80, 45.0),   # Kavieng / northern New Ireland
    (-3.10, 151.50, 45.0),   # central New Ireland
    (-3.67, 152.43, 45.0),   # Namatanai
    (-4.30, 152.90, 40.0),   # southern New Ireland
    (-3.12, 152.63, 20.0),   # Lihir
    (-2.02, 147.27, 45.0),   # Manus / Lorengau
    (-1.72, 146.30, 25.0),   # Bipi and the western Manus islands
    (-2.32, 147.83, 20.0),   # Rambutyo
    (-2.57, 147.28, 18.0),   # Baluan
    (-4.65, 145.97, 20.0),   # Karkar Island
    (-4.08, 145.03, 15.0),   # Manam Island
    (-8.51, 151.08, 30.0),   # Kiriwina / Trobriands
    (-9.35, 150.28, 25.0),   # Goodenough
    (-9.75, 150.80, 45.0),   # Fergusson / Normanby
    (-10.68, 152.75, 25.0),  # Misima
    (-10.62, 150.67, 20.0),  # Samarai
    (-9.07, 152.75, 25.0),   # Woodlark
    (-11.60, 153.30, 30.0),  # Louisiade / Sudest
    (-5.42, 154.67, 25.0),   # Buka
    (-4.68, 154.20, 20.0),   # Nissan / Green Islands
    (-2.70, 141.30, 20.0),   # Vanimo coastal strip
    (-9.08, 143.21, 25.0),   # Daru
    (-5.55, 147.90, 25.0),   # Umboi / Siassi
    (-2.85, 152.03, 22.0),   # Tabar group
    (-2.57, 150.20, 30.0),   # Lavongai / New Hanover
]


def distance_to_ring_km(lat: float, lon: float, ring: list[tuple[float, float]]) -> float:
    """Approximate distance from a point to a polygon ring (0 if inside)."""
    if point_in_ring(lat, lon, ring):
        return 0.0
    return min(haversine_km(lat, lon, ring_lat, ring_lon) for ring_lon, ring_lat in ring)


def land_distance_km(lat: float, lon: float) -> float:
    """Kilometres from the nearest modelled landmass. 0.0 means 'on land'.

    Only meaningful for PNG in this build; other countries return 0.0 (no mask
    configured) so the check degrades to "not applied" rather than "everything fails".
    """
    best = min(distance_to_ring_km(lat, lon, ring) for ring in PNG_POLYGONS)
    if best == 0.0:
        return 0.0
    for ilat, ilon, radius in PNG_ISLAND_BUFFERS:
        d = haversine_km(lat, lon, ilat, ilon) - radius
        best = min(best, max(0.0, d))
        if best == 0.0:
            return 0.0
    return best


LAND_MASKS = {"PNG": land_distance_km}


def offshore_km(country_code: str, lat: float, lon: float) -> Optional[float]:
    """How far offshore a point is, or None when no mask is configured."""
    mask = LAND_MASKS.get(country_code.upper())
    if mask is None:
        return None
    return mask(lat, lon)


def in_bbox(lat: float, lon: float, bbox: dict) -> bool:
    return (
        bbox["min_lat"] <= lat <= bbox["max_lat"] and bbox["min_lon"] <= lon <= bbox["max_lon"]
    )
