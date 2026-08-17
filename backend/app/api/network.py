"""Country, network and provenance endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..engine import seasonality, service
from ..engine.distance import cascade_summary
from ..engine.equity import compute_vulnerability
from ..models import AuditEntry, Country, Demand, Edge, Node, Product
from ..schemas import AuditOut, CountryOut, EdgeOut, EdgeOverride, NodeOut, ProductOut

router = APIRouter(tags=["network"])


def _country_or_404(session: Session, country_id: int) -> Country:
    country = session.get(Country, country_id)
    if not country:
        raise HTTPException(404, f"No country with id {country_id}.")
    return country


@router.get("/countries", response_model=list[CountryOut])
def list_countries(session: Session = Depends(get_session)):
    return list(session.scalars(select(Country).order_by(Country.name)))


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
