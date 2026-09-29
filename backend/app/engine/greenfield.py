"""Greenfield site selection: where new stores should go, from the demand itself.

Given every facility's demand and coordinates, propose K new store locations that
minimise demand-weighted distance -- the centre-of-gravity method, done as a weighted
k-means on the sphere -- and snap each to the nearest real facility, because a store
goes where there is a town, a road and staff, not at a point in the sea. Existing
open stores can be kept fixed, so the answer is "where would the next K go", or
dropped, so it is "if we started again".

The proposal is not the decision. Adopting it turns it into a scenario made of data
items -- a planned store and a lane to each facility it would serve -- with the solver
free to open or leave it closed, so the answer still has to earn its place on cost,
service and equity against every other option in the study.
"""

from __future__ import annotations

import math
import random
from statistics import median
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Country, Demand, Edge, Node, Product
from .geo import haversine_km


def _demand_m3_by_node(session: Session, country_id: int) -> Dict[int, float]:
    products = {p.id: p for p in session.scalars(select(Product).where(Product.country_id == country_id))}
    out: Dict[int, float] = {}
    for row in session.scalars(select(Demand).where(Demand.country_id == country_id)):
        product = products.get(row.product_id)
        if not product:
            continue
        out[row.node_id] = out.get(row.node_id, 0.0) + float(row.quantity or 0.0) * float(product.volume_per_unit_cm3 or 0.0) / 1_000_000.0
    return out


def _weighted_centroid(points: List[Tuple[float, float, float]]) -> Tuple[float, float]:
    """Demand-weighted mean position; on a country's scale, a plane is close enough."""
    weight = sum(w for _, _, w in points) or 1.0
    return sum(lat * w for lat, _, w in points) / weight, sum(lon * w for _, lon, w in points) / weight


def propose(
    session: Session,
    country: Country,
    *,
    k: int = 2,
    keep_existing: bool = True,
    admin1: Optional[str] = None,
    iterations: int = 40,
    seed: int = 7,
) -> dict:
    k = max(1, min(8, int(k)))
    nodes = list(session.scalars(select(Node).where(Node.country_id == country.id)))
    demand = _demand_m3_by_node(session, country.id)
    facilities = [
        n for n in nodes
        if n.level >= 2 and demand.get(n.id, 0.0) > 0 and n.lat is not None and n.lon is not None
        and (admin1 is None or (n.admin1 or "") == admin1)
    ]
    if not facilities:
        raise ValueError("No facilities with demand to place stores for." + (f" None in {admin1}." if admin1 else ""))
    stores = [n for n in nodes if n.hub_capable and n.level <= 1 and n.operating_status == "operational" and n.lat is not None]
    fixed: List[Tuple[float, float]] = [(s.lat, s.lon) for s in stores] if keep_existing else []
    points = [(f.lat, f.lon, demand[f.id]) for f in facilities]

    # k-means++ seeding on demand weight, deterministic.
    rng = random.Random(seed)
    centres: List[Tuple[float, float]] = []
    first = rng.choices(range(len(points)), weights=[p[2] for p in points])[0]
    centres.append((points[first][0], points[first][1]))
    while len(centres) < k:
        dists = []
        for lat, lon, w in points:
            nearest = min(haversine_km(lat, lon, c[0], c[1]) for c in centres + fixed)
            dists.append(w * nearest * nearest)
        if sum(dists) <= 0:
            break
        pick = rng.choices(range(len(points)), weights=dists)[0]
        centres.append((points[pick][0], points[pick][1]))

    assignment: List[int] = [0] * len(points)  # index into centres, or -1 for an existing store
    for _ in range(iterations):
        changed = False
        for i, (lat, lon, _) in enumerate(points):
            best, best_d = -1, min((haversine_km(lat, lon, c[0], c[1]) for c in fixed), default=float("inf"))
            for j, c in enumerate(centres):
                d = haversine_km(lat, lon, c[0], c[1])
                if d < best_d:
                    best, best_d = j, d
            if assignment[i] != best:
                assignment[i] = best
                changed = True
        for j in range(len(centres)):
            members = [points[i] for i in range(len(points)) if assignment[i] == j]
            if members:
                centres[j] = _weighted_centroid(members)
        if not changed:
            break

    # Snap each centre to the nearest facility, and describe what it would serve.
    hub_costs = [float(s.hub_fixed_cost or 0.0) for s in stores if s.hub_fixed_cost]
    hub_capex = [float(s.hub_open_capex or 0.0) for s in stores if s.hub_open_capex]
    default_fixed = median(hub_costs) if hub_costs else 150000.0
    default_capex = median(hub_capex) if hub_capex else 800000.0
    proposals = []
    total_before = total_after = 0.0
    for j, centre in enumerate(centres):
        member_ids = [i for i in range(len(points)) if assignment[i] == j]
        if not member_ids:
            continue
        host = min(facilities, key=lambda f: haversine_km(f.lat, f.lon, centre[0], centre[1]))
        site = (host.lat, host.lon)
        served = [facilities[i] for i in member_ids]
        m3 = sum(demand[f.id] for f in served)
        before = sum(
            demand[f.id] * min((haversine_km(f.lat, f.lon, s.lat, s.lon) for s in stores), default=0.0) for f in served
        )
        after = sum(demand[f.id] * haversine_km(f.lat, f.lon, site[0], site[1]) for f in served)
        total_before += before
        total_after += after
        proposals.append(
            {
                "code": f"GF-{j + 1}",
                "name": f"New store near {host.name}",
                "host_code": host.code,
                "host_name": host.name,
                "admin1": host.admin1,
                "lat": round(site[0], 5),
                "lon": round(site[1], 5),
                "centroid": {"lat": round(centre[0], 5), "lon": round(centre[1], 5)},
                "facilities": [f.code for f in served],
                "facility_count": len(served),
                "demand_m3": round(m3, 1),
                "population": int(sum(float(f.catchment_population or 0.0) for f in served)),
                "mean_km_before": round(before / m3, 1) if m3 else None,
                "mean_km_after": round(after / m3, 1) if m3 else None,
                "m3_km_before": round(before, 0),
                "m3_km_after": round(after, 0),
                "hub_fixed_cost": round(default_fixed, 0),
                "hub_open_capex": round(default_capex, 0),
                "hub_throughput_m3": round(m3 * 1.5, 0),
            }
        )
    unassigned = sum(1 for a in assignment if a == -1)
    return {
        "k": k,
        "keep_existing": keep_existing,
        "admin1": admin1,
        "facilities_considered": len(facilities),
        "facilities_kept_by_existing_stores": unassigned,
        "existing_stores": [s.code for s in stores],
        "proposals": proposals,
        "m3_km_before": round(total_before, 0),
        "m3_km_after": round(total_after, 0),
        "reduction": round(1 - total_after / total_before, 4) if total_before else None,
        "sentence": _sentence(proposals, total_before, total_after, keep_existing),
    }


def _sentence(proposals: list, before: float, after: float, keep_existing: bool) -> str:
    if not proposals:
        return "Every facility is already closest to an existing store; a new one would not shorten anything."
    names = ", ".join(p["host_name"] for p in proposals)
    reduction = (1 - after / before) if before else 0.0
    return (
        f"{len(proposals)} new store{'s' if len(proposals) > 1 else ''} near {names} would serve "
        f"{sum(p['facility_count'] for p in proposals)} facilities and cut their freight task "
        f"(m³·km to the nearest store) by {reduction:.0%}"
        + (", with today's stores kept." if keep_existing else ", starting from a clean map.")
        + " Adopt it as a scenario and the solver decides whether each store earns its cost."
    )


def adopt(session: Session, country: Country, proposals: List[dict], *, keep_existing: bool = True) -> dict:
    """The data items for a scenario that opens the proposed stores as candidates."""
    nodes = {n.code: n for n in session.scalars(select(Node).where(Node.country_id == country.id))}
    national = next((n for n in nodes.values() if n.level == 0), None)
    has_sea = session.scalar(select(Edge.id).where(Edge.country_id == country.id, Edge.mode == "sea").limit(1)) is not None
    road_lanes = [e for e in session.scalars(select(Edge).where(Edge.country_id == country.id, Edge.mode == "road"))]
    road_rate = median([float(e.cost_per_m3) for e in road_lanes if e.cost_per_m3]) if any(e.cost_per_m3 for e in road_lanes) else None
    items: List[dict] = []
    for p in proposals:
        code = str(p.get("code") or f"GF-{len(items) + 1}")
        items.append(
            {
                "kind": "add_node", "code": code, "name": p.get("name") or code, "lat": float(p["lat"]), "lon": float(p["lon"]),
                "level": 1, "type": "area_store", "admin1": p.get("admin1"), "hub_capable": True, "operating_status": "planned",
                "hub_fixed_cost": float(p.get("hub_fixed_cost") or 0.0), "hub_open_capex": float(p.get("hub_open_capex") or 0.0),
                "hub_throughput_m3": float(p.get("hub_throughput_m3") or 0.0), "terrain_class": "mainland_road",
            }
        )
        if national is not None:
            far = haversine_km(national.lat, national.lon, float(p["lat"]), float(p["lon"]))
            mode = "sea" if has_sea and far > 250 else "road"
            items.append({"kind": "add_lane", "code": f"{national.code}-{code}", "from_code": national.code, "to_code": code, "mode": mode,
                          "capacity_per_trip_m3": 60.0 if mode == "sea" else 0.0, "service_frequency": "FORTNIGHTLY" if mode == "sea" else None})
        for facility_code in p.get("facilities") or []:
            if facility_code in nodes:
                lane = {"kind": "add_lane", "code": f"{code}-{facility_code}", "from_code": code, "to_code": facility_code, "mode": "road"}
                if road_rate:
                    lane["cost_per_m3"] = road_rate
                items.append(lane)
    if not keep_existing:
        for n in nodes.values():
            if n.hub_capable and n.level == 1 and n.operating_status == "operational":
                items.append({"kind": "set_node_field", "code": n.code, "field": "operating_status", "value": "non_operational"})
    return {"items": items, "store_codes": [str(p.get("code")) for p in proposals]}
