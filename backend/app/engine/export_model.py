"""The allocation model as data, for a solver that is not this one.

The marketing site solves the demo network in the visitor's browser with the same
HiGHS engine compiled to WebAssembly. For that it needs exactly what the runner hands
the solver: every facility with its demand, band and penalty; every store with its
annual charge and throughput; every lane with its unit cost and capacity. This module
assembles that from the same helpers the runner uses -- costing, service profiles,
seasonality, equity -- and a test proves that solving the export reproduces the
runner's own answer, so the two cannot drift apart unnoticed.
"""

from __future__ import annotations

from statistics import median
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Demand, Edge, Node, Product, Scenario
from . import costing, equity as equity_mod, overlay, seasonality, service
from .runner import DEFAULT_WEIGHTS, _frequency_for, _resolve_levers, _volume_m3


def assemble(session: Session, scenario: Scenario) -> dict:
    """Facilities, hubs and lanes as the solver sees them, plus what a map needs."""
    levers = _resolve_levers(scenario)
    constraints = dict(scenario.constraints or {})
    weights = {**DEFAULT_WEIGHTS, **(scenario.objective_weights or {})}
    multi_period = bool(levers.get("multi_period"))
    month = levers.get("month")
    month = int(month) if month and not multi_period else None
    country = scenario.country
    country_id = scenario.country_id

    nodes = list(session.scalars(select(Node).where(Node.country_id == country_id)))
    edges = list(session.scalars(select(Edge).where(Edge.country_id == country_id, Edge.active.is_(True))))
    products = {p.id: p for p in session.scalars(select(Product).where(Product.country_id == country_id))}
    demand_rows = list(session.scalars(select(Demand).where(Demand.country_id == country_id)))
    nodes, edges, demand_rows, _ = overlay.apply(list(scenario.data_items or []), nodes, edges, demand_rows, products, config=country.config)

    growth = costing.demand_scaling(levers)
    demand_m3: dict = {}
    cold_m3: dict = {}
    for row in demand_rows:
        product = products.get(row.product_id)
        if not product:
            continue
        volume = _volume_m3(row.quantity, product) * growth
        demand_m3[row.node_id] = demand_m3.get(row.node_id, 0.0) + volume
        if product.temperature_band != "ambient":
            cold_m3[row.node_id] = cold_m3.get(row.node_id, 0.0) + volume
    demand_nodes = [n for n in nodes if demand_m3.get(n.id, 0.0) > 0]
    total_demand = sum(demand_m3.values())
    total_cold = sum(cold_m3.values())

    hub_nodes = [n for n in nodes if n.hub_capable]
    forced_open = set(levers.get("hub_nodes_open") or [])
    forced_closed = set(levers.get("hub_nodes_closed") or [])
    optimize_hubs = bool(levers.get("optimize_hubs"))
    amortisation_years = float((country.config or {}).get("capex_amortisation_years") or 10.0)
    hubs = []
    for node in hub_nodes:
        if node.code in forced_closed:
            state: Optional[bool] = False
        elif node.code in forced_open:
            state = True
        elif optimize_hubs:
            state = None
        else:
            state = node.operating_status == "operational"
        fixed = costing.hub_annual_fixed_cost(node, integration_policy=levers.get("integration_policy", "vertical"))
        capex = float(node.hub_open_capex or 0.0) / max(1.0, amortisation_years)
        currently_open = node.operating_status == "operational"
        hubs.append(
            {
                "id": node.id, "code": node.code, "name": node.name, "lat": node.lat, "lon": node.lon,
                "fixed_cost": round(fixed, 2), "annualised_capex": round(capex, 2), "currently_open": currently_open,
                "annual_charge": round(fixed + (0.0 if currently_open else capex), 2),
                "throughput_m3": float(node.hub_throughput_m3 or 0.0), "forced_open": state,
            }
        )
    hub_ids = {h["id"] for h in hubs}

    national_cold_share = total_cold / total_demand if total_demand else 0.0
    upstream_edges = [e for e in edges if e.to_node_id in hub_ids]
    inbound_cost = {node.id: 0.0 for node in nodes if node.level == 0}
    upstream_lane_cost = {
        edge.id: costing.lane_cost(
            edge, month=month, fuel_index=float(levers.get("fuel_index", 1.0)),
            third_party_share=float(levers.get("third_party_share", 0.0)), cold_share=national_cold_share,
        ).unit_cost_per_m3
        for edge in upstream_edges
    }
    for _ in range(len(hub_ids) + 1):
        changed = False
        for edge in upstream_edges:
            origin = inbound_cost.get(edge.from_node_id)
            if origin is None:
                continue
            candidate = origin + upstream_lane_cost[edge.id]
            if candidate < inbound_cost.get(edge.to_node_id, float("inf")) - 1e-9:
                inbound_cost[edge.to_node_id] = candidate
                changed = True
        if not changed:
            break

    allowed_modes = set(levers.get("allowed_modes") or [])
    edges_by_facility: dict = {}
    for edge in edges:
        if edge.to_node_id in demand_m3:
            edges_by_facility.setdefault(edge.to_node_id, []).append(edge)
    lanes = []
    for edge in edges:
        if edge.from_node_id not in hub_ids or edge.to_node_id not in demand_m3:
            continue
        access = seasonality.access(edge, month) if month else seasonality.annual_access(edge)
        if access <= 0.0:
            continue
        frequency = _frequency_for(edge, levers)
        facility_cold_share = cold_m3.get(edge.to_node_id, 0.0) / demand_m3[edge.to_node_id] if demand_m3.get(edge.to_node_id) else 0.0
        cost = costing.lane_cost(
            edge, month=month, fuel_index=float(levers.get("fuel_index", 1.0)),
            third_party_share=float(levers.get("third_party_share", 0.0)), cold_share=facility_cold_share,
        )
        prof = service.profile_for(edge, month=month, frequency_override=frequency)
        capacity = prof.annual_capacity_m3
        capacity = 0.0 if capacity == float("inf") else capacity
        lanes.append(
            {
                "edge_id": edge.id, "code": edge.code, "hub_id": edge.from_node_id, "facility_id": edge.to_node_id,
                "mode": edge.mode if edge.mode in allowed_modes else edge.mode,
                "allowed": edge.mode in allowed_modes,
                "unit_cost": round(cost.unit_cost_per_m3 + inbound_cost.get(edge.from_node_id, 0.0), 4),
                "capacity_m3": round(capacity, 3), "distance_km": edge.distance_km,
                "frequency": frequency, "service_name": edge.service_name,
            }
        )

    vulnerability = equity_mod.compute_vulnerability(demand_nodes, hub_nodes, edges_by_facility)
    strata = equity_mod.assign_strata(vulnerability)
    unit_costs = [lane["unit_cost"] for lane in lanes if lane["allowed"]] or [100.0]
    base_penalty = float((country.config or {}).get("unmet_penalty_per_m3") or median(unit_costs) * 4.0)
    facilities = [
        {
            "id": node.id, "code": node.code, "name": node.name, "admin1": node.admin1, "level": node.level,
            "lat": node.lat, "lon": node.lon, "population": float(node.catchment_population or 0.0),
            "demand_m3": round(demand_m3[node.id], 4), "cold_share": round(cold_m3.get(node.id, 0.0) / demand_m3[node.id], 4),
            "vulnerability": round(vulnerability[node.id].score, 4), "stratum": strata.get(node.id, 0),
        }
        for node in demand_nodes
    ]
    return {
        "scenario": {"id": scenario.id, "name": scenario.name, "is_baseline": scenario.is_baseline},
        "country": {"code": country.code, "name": country.name, "currency": country.currency},
        "conditions": "Twelve months" if multi_period else (seasonality.MONTHS[month - 1] if month else "Annualised"),
        "weights": weights,
        "constraints": constraints,
        "levers": {k: levers.get(k) for k in ("allowed_modes", "optimize_hubs", "hub_nodes_open", "hub_nodes_closed", "month", "demand_growth", "fuel_index")},
        "base_penalty": round(base_penalty, 4),
        "penalty_rule": "unmet_penalty = base_penalty × (1 + 3 × equity_weight × vulnerability)",
        "strata_labels": equity_mod.STRATUM_LABELS,
        "facilities": facilities,
        "hubs": hubs,
        "lanes": lanes,
        "national_store": next(({"id": n.id, "code": n.code, "name": n.name, "lat": n.lat, "lon": n.lon} for n in nodes if n.level == 0), None),
        "totals": {"demand_m3": round(total_demand, 3), "cold_m3": round(total_cold, 3), "population": round(sum(f["population"] for f in facilities))},
    }
