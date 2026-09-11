"""Country, network and provenance endpoints."""

from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..engine import geo, seasonality, service
from ..engine.distance import cascade_summary
from ..engine.equity import compute_vulnerability
from ..models import AuditEntry, Country, Demand, Edge, Node, Product
from ..schemas import (
    AuditOut,
    CountryIn,
    CountryOut,
    CountryPatch,
    EdgeOut,
    EdgeOverride,
    NodeOut,
    ProductOut,
)

router = APIRouter(tags=["network"])


def _country_or_404(session: Session, country_id: int) -> Country:
    country = session.get(Country, country_id)
    if not country:
        raise HTTPException(404, f"No country with id {country_id}.")
    return country


#: What a country gets when nobody has told the tool anything about it yet. The bounding
#: box spans the world on purpose: an invented one would reject real facilities, and a
#: check that fires on correct data is worse than no check.
DEFAULT_CONFIG = {
    "bbox": {"min_lat": -90, "max_lat": 90, "min_lon": -180, "max_lon": 180},
    "level_labels": {"0": "National store", "1": "Regional store", "2": "District store", "3": "Facility"},
    "equity_definition": "Facilities ranked by how hard they are to reach, in five population-weighted groups.",
}


def _as_out(country: Country) -> dict:
    return {
        "id": country.id,
        "code": country.code,
        "name": country.name,
        "currency": country.currency,
        "config": country.config or {},
        "has_boundary": bool((country.boundary or {}).get("polygons") or (country.boundary or {}).get("buffers")),
    }


@router.get("/countries", response_model=list[CountryOut])
def list_countries(session: Session = Depends(get_session)):
    return [_as_out(c) for c in session.scalars(select(Country).order_by(Country.name))]


@router.post("/countries", response_model=CountryOut, status_code=201)
def create_country(payload: CountryIn, session: Session = Depends(get_session)):
    """Open a workspace for a new country.

    This is the step that used to require editing Python. The coarse land mask, the
    terrain detour factors and the seasonal profiles are all country data now, so a
    second country is a form rather than a release.
    """
    code = payload.code.strip().upper()
    existing = session.scalar(select(Country).where(Country.code == code))
    if existing:
        raise HTTPException(
            409,
            f"{code} already exists as '{existing.name}'. Open that workspace, or choose another code.",
        )

    country = Country(
        code=code,
        name=payload.name.strip(),
        currency=(payload.currency or "USD").strip().upper(),
        config={**DEFAULT_CONFIG, **(payload.config or {})},
        boundary=payload.boundary or {},
    )
    session.add(country)
    session.flush()

    session.add(
        AuditEntry(
            country_id=country.id,
            entity_type="global",
            entity_ref=code,
            field="created",
            new_value=f"Workspace opened for {country.name}",
            rationale="A new country workspace.",
            actor="analyst",
        )
    )
    session.commit()
    return _as_out(country)


@router.patch("/countries/{country_id}", response_model=CountryOut)
def update_country(country_id: int, payload: CountryPatch, session: Session = Depends(get_session)):
    """Change a country's settings — its name, currency, bounding box, terrain factors,
    seasonal profiles or land mask.

    Changing the boundary or the detour factors changes what the validator believes and
    what the cascade computes, so every change is written to the audit trail. Distances
    already stored are not recomputed: re-import or re-run the cascade if you want them
    to follow.
    """
    country = _country_or_404(session, country_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "No settings to change.")

    for field, value in changes.items():
        if value is None:
            continue
        old = getattr(country, field)
        setattr(country, field, value)
        session.add(
            AuditEntry(
                country_id=country.id,
                entity_type="global",
                entity_ref=country.code,
                field=field,
                old_value=("(a land mask)" if field == "boundary" else str(old))[:2000],
                new_value=("(a land mask)" if field == "boundary" else str(value))[:2000],
                rationale="Country settings changed.",
                actor="analyst",
            )
        )
    session.commit()
    return _as_out(country)


@router.get("/countries/{country_id}", response_model=CountryOut)
def get_country(country_id: int, session: Session = Depends(get_session)):
    return _country_or_404(session, country_id)


@router.get("/countries/{country_id}/nodes", response_model=list[NodeOut])
def list_nodes(country_id: int, session: Session = Depends(get_session)):
    _country_or_404(session, country_id)
    return list(
        session.scalars(select(Node).where(Node.country_id == country_id).order_by(Node.level, Node.code))
    )


@router.get("/countries/{country_id}/edges", response_model=list[EdgeOut])
def list_edges(country_id: int, session: Session = Depends(get_session)):
    _country_or_404(session, country_id)
    edges = list(session.scalars(select(Edge).where(Edge.country_id == country_id).order_by(Edge.code)))
    return [
        EdgeOut(
            **{key: getattr(edge, key) for key in EdgeOut.model_fields if hasattr(edge, key)},
            from_code=edge.from_node.code,
            to_code=edge.to_node.code,
            from_lat=edge.from_node.lat,
            from_lon=edge.from_node.lon,
            to_lat=edge.to_node.lat,
            to_lon=edge.to_node.lon,
        )
        for edge in edges
    ]


@router.get("/countries/{country_id}/products", response_model=list[ProductOut])
def list_products(country_id: int, session: Session = Depends(get_session)):
    _country_or_404(session, country_id)
    return list(session.scalars(select(Product).where(Product.country_id == country_id)))


@router.get("/countries/{country_id}/overview")
def overview(country_id: int, session: Session = Depends(get_session)):
    """Everything the map and the header need in one call."""
    country = _country_or_404(session, country_id)
    nodes = list(session.scalars(select(Node).where(Node.country_id == country_id)))
    edges = list(session.scalars(select(Edge).where(Edge.country_id == country_id)))
    products = list(session.scalars(select(Product).where(Product.country_id == country_id)))
    demand_rows = list(session.scalars(select(Demand).where(Demand.country_id == country_id)))

    product_by_id = {p.id: p for p in products}
    demand_m3: dict[int, float] = {}
    for row in demand_rows:
        product = product_by_id.get(row.product_id)
        if product:
            demand_m3[row.node_id] = demand_m3.get(row.node_id, 0.0) + row.quantity * product.volume_per_unit_cm3 / 1e6

    hubs = [n for n in nodes if n.hub_capable]
    facilities = [n for n in nodes if demand_m3.get(n.id, 0.0) > 0]
    edges_by_facility: dict[int, list[Edge]] = {}
    for edge in edges:
        if edge.to_node_id in demand_m3:
            edges_by_facility.setdefault(edge.to_node_id, []).append(edge)
    vulnerability = compute_vulnerability(facilities, hubs, edges_by_facility)

    scheduled = [e for e in edges if e.service_frequency and e.mode in ("sea", "air", "river")]
    services: dict[str, dict] = {}
    for edge in scheduled:
        name = edge.service_name or edge.code
        entry = services.setdefault(
            name,
            {
                "name": name,
                "mode": edge.mode,
                "frequency": edge.service_frequency,
                "service_days": edge.service_days,
                "reliability": edge.reliability,
                "facilities": 0,
                "capacity_per_trip_m3": 0.0,
                "population": 0.0,
                "origin": edge.from_node.code,
            },
        )
        entry["facilities"] += 1
        entry["capacity_per_trip_m3"] += edge.capacity_per_trip_m3
        entry["population"] += edge.to_node.catchment_population or 0.0

    for entry in services.values():
        entry["capacity_per_trip_m3"] = round(entry["capacity_per_trip_m3"], 2)
        entry["interval_days"] = round(service.interval_days(entry["frequency"]), 1)

    return {
        "country": CountryOut.model_validate(country).model_dump(),
        "counts": {
            "nodes": len(nodes),
            "facilities": len(facilities),
            "hubs": len(hubs),
            "hubs_operational": sum(1 for h in hubs if h.operating_status == "operational"),
            "edges": len(edges),
            "scheduled_services": len(services),
            "products": len(products),
        },
        "totals": {
            "population": round(sum(n.catchment_population or 0.0 for n in nodes)),
            "annual_demand_m3": round(sum(demand_m3.values()), 1),
            "provinces": sorted({n.admin1 for n in nodes if n.admin1}),
        },
        "distance_provenance": cascade_summary(edges),
        "services": sorted(services.values(), key=lambda s: -s["population"]),
        "vulnerability": {str(k): v.as_dict() for k, v in vulnerability.items()},
        "seasonality_profiles": seasonality.PROFILES,
        "months": seasonality.MONTHS,
    }


@router.get("/countries/{country_id}/basemap.geojson")
def basemap(country_id: int, session: Session = Depends(get_session)):
    """The land mask, as GeoJSON, so the map has a basemap with no internet.

    A network design workshop in a provincial office cannot assume a tile server is
    reachable. Rather than ship a second dataset, the map draws the same coarse land
    mask the validator screens coordinates against — which has the useful side effect
    that what you see is exactly what the validator believes, coarseness included.

    A country with no boundary drawn yet returns nothing rather than failing: the map
    falls back to whatever tiles it can reach, and the offshore check is simply not
    applied.
    """
    country = _country_or_404(session, country_id)
    boundary = country.boundary or {}
    polygons = boundary.get("polygons") or []
    buffers = boundary.get("buffers") or []
    if not polygons and not buffers:
        return {"type": "FeatureCollection", "features": []}

    features = [
        {
            "type": "Feature",
            "properties": {"kind": "landmass"},
            "geometry": {"type": "Polygon", "coordinates": [[*[list(point) for point in ring], list(ring[0])]]},
        }
        for ring in polygons
    ]

    # Circular buffers become 24-gon rings so the same fill layer can draw them.
    for entry in buffers:
        lat, lon, radius_km = entry[0], entry[1], entry[2]
        ring = []
        for step in range(25):
            angle = 2 * math.pi * step / 24
            dlat = (radius_km / 111.32) * math.cos(angle)
            dlon = (radius_km / (111.32 * max(0.2, math.cos(math.radians(lat))))) * math.sin(angle)
            ring.append([lon + dlon, lat + dlat])
        features.append(
            {
                "type": "Feature",
                "properties": {"kind": "island"},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        )

    return {
        "type": "FeatureCollection",
        "properties": {
            "note": (
                "Coarse land mask, accurate to roughly 10-25 km at the coast. It is a "
                "screening geometry, not a coastline."
            )
        },
        "features": features,
    }


@router.get("/countries/{country_id}/season/{month}")
def season_view(country_id: int, month: int, session: Session = Depends(get_session)):
    """What the network looks like in one month. Drives the season slider."""
    if not 1 <= month <= 12:
        raise HTTPException(400, "Month must be between 1 and 12.")
    _country_or_404(session, country_id)
    edges = list(session.scalars(select(Edge).where(Edge.country_id == country_id, Edge.active.is_(True))))

    closed: list[dict] = []
    degraded: list[dict] = []
    reachable_by_node: dict[int, bool] = {}
    surface_by_node: dict[int, bool] = {}
    node_names: dict[int, str] = {}
    node_info: dict[int, dict] = {}

    for edge in edges:
        access = seasonality.access(edge, month)
        node_names[edge.to_node_id] = edge.to_node.name
        node_info[edge.to_node_id] = {
            "node_id": edge.to_node_id,
            "code": edge.to_node.code,
            "name": edge.to_node.name,
            "admin1": edge.to_node.admin1,
            "lat": edge.to_node.lat,
            "lon": edge.to_node.lon,
            "population": edge.to_node.catchment_population,
        }
        reachable_by_node[edge.to_node_id] = reachable_by_node.get(edge.to_node_id, False) or access > 0
        if edge.mode in ("road", "river", "foot"):
            surface_by_node[edge.to_node_id] = surface_by_node.get(edge.to_node_id, False) or access > 0
        entry = {
            "edge_code": edge.code,
            "from": edge.from_node.code,
            "to": edge.to_node.code,
            "to_name": edge.to_node.name,
            "mode": edge.mode,
            "service_name": edge.service_name,
            "access": round(access, 3),
            "cost_multiplier": round(seasonality.cost_multiplier(edge, month), 3),
        }
        if access <= 0.0:
            closed.append(entry)
        elif access < 0.6:
            degraded.append(entry)

    cut_off = [node_info[node_id] for node_id, reachable in reachable_by_node.items() if not reachable]

    # The more common — and more interesting — outcome is not that a facility becomes
    # unreachable, but that its road or river closes and it falls back onto air freight
    # at several times the cost. "So what does the workaround cost?" is the question
    # that follows the map going red, and this is the list it is asked about.
    lost_surface = [
        node_info[node_id]
        for node_id, open_surface in surface_by_node.items()
        if not open_surface and reachable_by_node.get(node_id)
    ]

    return {
        "month": month,
        "month_name": seasonality.MONTHS[month - 1],
        "closed_lanes": closed,
        "degraded_lanes": degraded,
        "facilities_cut_off": cut_off,
        "facilities_losing_surface_access": lost_surface,
        "summary": {
            "lanes_closed": len(closed),
            "lanes_degraded": len(degraded),
            "facilities_cut_off": len(cut_off),
            "facilities_losing_surface_access": len(lost_surface),
            "population_losing_surface_access": round(
                sum(n["population"] or 0.0 for n in lost_surface)
            ),
        },
    }


@router.patch("/edges/{edge_id}", response_model=EdgeOut)
def override_edge(edge_id: int, payload: EdgeOverride, session: Session = Depends(get_session)):
    """Operator override of a lane, with the change written to the audit trail.

    This is the answer to "that road takes six hours, not two". The override is made
    here, from the UI, and the old value and the reason survive it.
    """
    edge = session.get(Edge, edge_id)
    if not edge:
        raise HTTPException(404, f"No lane with id {edge_id}.")

    changes = payload.model_dump(exclude_unset=True, exclude={"rationale", "actor"})
    if not changes:
        raise HTTPException(400, "No fields to change.")

    for field, value in changes.items():
        if value is None:
            continue
        old = getattr(edge, field)
        setattr(edge, field, value)
        if field == "distance_km":
            edge.distance_method = "manual"
            edge.distance_confidence = 0.95
            edge.distance_note = payload.rationale or "Operator override."
        session.add(
            AuditEntry(
                country_id=edge.country_id,
                entity_type="edge",
                entity_ref=edge.code,
                field=field,
                old_value=str(old),
                new_value=str(value),
                provenance="manual_override",
                confidence_marker="S" if payload.rationale else "I",
                rationale=payload.rationale,
                actor=payload.actor,
            )
        )

    session.commit()
    session.refresh(edge)
    return EdgeOut(
        **{key: getattr(edge, key) for key in EdgeOut.model_fields if hasattr(edge, key)},
        from_code=edge.from_node.code,
        to_code=edge.to_node.code,
        from_lat=edge.from_node.lat,
        from_lon=edge.from_node.lon,
        to_lat=edge.to_node.lat,
        to_lon=edge.to_node.lon,
    )


@router.get("/countries/{country_id}/audit", response_model=list[AuditOut])
def list_audit(country_id: int, limit: int = 200, session: Session = Depends(get_session)):
    _country_or_404(session, country_id)
    return list(
        session.scalars(
            select(AuditEntry)
            .where(AuditEntry.country_id == country_id)
            .order_by(AuditEntry.id.desc())
            .limit(limit)
        )
    )
