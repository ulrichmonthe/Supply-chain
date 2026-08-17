"""Scenario execution: from levers to a persisted Result.

A run is deterministic and self-contained. Given a scenario's levers, constraints and
objective weights it resolves the network for the requested conditions, solves the
allocation model, and assembles the scorecard, the equity decomposition and the
per-facility detail in one pass.

Two run modes, and the distinction is worth stating plainly because it is what the
month slider means:

    month = None    annualised. Lanes use their mean access and mean cost multiplier.
    month = 3       "March conditions, annualised". Lanes use March's access and
                    March's cost multiplier, and the answer is what a year would look
                    like if March conditions held all year.

The second is the honest reading of a slider. It is not a March-only budget; it is
the cost and coverage of a network operating under March's constraints, which is the
question a provincial health adviser is actually asking.
"""

from __future__ import annotations

import time
from statistics import median
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Demand, Edge, Node, Product, Result, Scenario
from . import costing, equity as equity_mod, kpis as kpi_mod, seasonality, service
from .allocation import FacilityIn, HubIn, LaneIn, SolveOptions, solve

DEFAULT_WEIGHTS = {"cost": 1.0, "service": 1.0, "equity": 0.0}


def _volume_m3(quantity: float, product: Product) -> float:
    return quantity * (product.volume_per_unit_cm3 or 0.0) / 1_000_000.0


def _resolve_levers(scenario: Scenario) -> dict:
    levers = dict(scenario.levers or {})
    levers.setdefault("month", None)
    levers.setdefault("allowed_modes", ["road", "sea", "air", "river"])
    levers.setdefault("hub_nodes_open", [])
    levers.setdefault("hub_nodes_closed", [])
    levers.setdefault("optimize_hubs", False)
    levers.setdefault("service_frequency_overrides", {})
    levers.setdefault("delivery_frequency_by_level", {})
    levers.setdefault("third_party_share", 0.0)
    levers.setdefault("integration_policy", "vertical")
    levers.setdefault("fuel_index", 1.0)
    levers.setdefault("demand_growth", 0.0)
    # National buffer stock policy, in days. Fixed rather than proportional to the
    # delivery interval, which is how ministries actually write the standard -- and
    # which is why stretching an interval past a facility's shelf capacity bites.
    levers.setdefault("safety_stock_days", 14.0)
    return levers


def _frequency_for(edge: Edge, levers: dict) -> Optional[str]:
    overrides = levers.get("service_frequency_overrides") or {}
    if edge.code in overrides:
        return overrides[edge.code]
    if edge.service_name and edge.service_name in overrides:
        return overrides[edge.service_name]
    if edge.mode in overrides:
        return overrides[edge.mode]
    by_level = levers.get("delivery_frequency_by_level") or {}
    to_level = str(edge.to_node.level) if edge.to_node else None
    if to_level and to_level in by_level:
        return by_level[to_level]
    return edge.service_frequency


def run_scenario(session: Session, scenario: Scenario) -> Result:
    started = time.perf_counter()
    levers = _resolve_levers(scenario)
    constraints = dict(scenario.constraints or {})
    weights = {**DEFAULT_WEIGHTS, **(scenario.objective_weights or {})}
    month = levers.get("month")
    month = int(month) if month else None

    country_id = scenario.country_id
    nodes = list(session.scalars(select(Node).where(Node.country_id == country_id)))
    edges = list(session.scalars(select(Edge).where(Edge.country_id == country_id, Edge.active.is_(True))))
    products = {p.id: p for p in session.scalars(select(Product).where(Product.country_id == country_id))}
    demand_rows = list(session.scalars(select(Demand).where(Demand.country_id == country_id)))

    node_by_id = {n.id: n for n in nodes}

    # --- demand -------------------------------------------------------------------
    growth = costing.demand_scaling(levers)
    demand_m3: dict[int, float] = {}
    cold_m3: dict[int, float] = {}
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

    # --- hubs ---------------------------------------------------------------------
    hub_nodes = [n for n in nodes if n.hub_capable]
    forced_open = set(levers.get("hub_nodes_open") or [])
    forced_closed = set(levers.get("hub_nodes_closed") or [])
    optimize_hubs = bool(levers.get("optimize_hubs"))

    # Building a store is a capital cost, not an operating one. It enters the annual
    # comparison as an amortised charge and appears at full value in the roadmap, so a
    # scenario that opens three hubs is never made to look 4.5 million worse per year
    # than it is.
    amortisation_years = float(
        (scenario.country.config or {}).get("capex_amortisation_years") or 10.0
    )

    hubs: list[HubIn] = []
    for node in hub_nodes:
        if node.code in forced_closed:
            state: Optional[bool] = False
        elif node.code in forced_open:
            state = True
        elif optimize_hubs:
            state = None
        else:
            # Default: reproduce the network as it stands today.
            state = node.operating_status == "operational"
        hubs.append(
            HubIn(
                id=node.id,
                code=node.code,
                fixed_cost=costing.hub_annual_fixed_cost(
                    node, integration_policy=levers.get("integration_policy", "vertical")
                ),
                throughput_m3=float(node.hub_throughput_m3 or 0.0),
                forced_open=state,
                annualised_capex=float(node.hub_open_capex or 0.0) / max(1.0, amortisation_years),
                currently_open=node.operating_status == "operational",
            )
        )
    hub_ids = {h.id for h in hubs}

    # --- upstream (primary) leg ----------------------------------------------------
    # The cheapest way to get a cubic metre from the national store to each hub,
    # accumulated along the primary chain: Badili reaches Mount Hagen by sea to Lae and
    # then by road up the Highlands Highway, and the cost of both legs has to land on
    # every carton that ends up in Enga. Added to each outbound lane's unit cost, so the
    # primary leg is never silently free. Flow conservation at the hub is handled by the
    # hub throughput constraint, so this needs no extra flow variables.
    national_cold_share = total_cold / total_demand if total_demand else 0.0
    upstream_edges = [e for e in edges if e.to_node_id in hub_ids]
    inbound_cost: dict[int, float] = {node.id: 0.0 for node in nodes if node.level == 0}

    upstream_lane_cost = {
        edge.id: costing.lane_cost(
            edge,
            month=month,
            fuel_index=float(levers.get("fuel_index", 1.0)),
            third_party_share=float(levers.get("third_party_share", 0.0)),
            cold_share=national_cold_share,
        ).unit_cost_per_m3
        for edge in upstream_edges
    }
    # Bellman-Ford style relaxation. The primary network is a handful of nodes, so a
    # bounded number of passes is both sufficient and cheaper than building a heap.
    for _ in range(len(hub_ids) + 1):
        changed = False
        for edge in upstream_edges:
            origin_cost = inbound_cost.get(edge.from_node_id)
            if origin_cost is None:
                continue
            candidate = origin_cost + upstream_lane_cost[edge.id]
            if candidate < inbound_cost.get(edge.to_node_id, float("inf")) - 1e-9:
                inbound_cost[edge.to_node_id] = candidate
                changed = True
        if not changed:
            break

    # --- lanes ---------------------------------------------------------------------
    allowed_modes = set(levers.get("allowed_modes") or [])
    edges_by_facility: dict[int, list[Edge]] = {}
    for edge in edges:
        if edge.to_node_id in demand_m3:
            edges_by_facility.setdefault(edge.to_node_id, []).append(edge)

    lanes: list[LaneIn] = []
    lane_meta: dict[int, dict] = {}
    for edge in edges:
        if edge.from_node_id not in hub_ids or edge.to_node_id not in demand_m3:
            continue
        if edge.mode not in allowed_modes:
            continue
        access = seasonality.access(edge, month) if month else seasonality.annual_access(edge)
        if access <= 0.0:
            continue

        frequency = _frequency_for(edge, levers)
        facility_cold_share = (
            cold_m3.get(edge.to_node_id, 0.0) / demand_m3[edge.to_node_id]
            if demand_m3.get(edge.to_node_id)
            else 0.0
        )
        cost = costing.lane_cost(
            edge,
            month=month,
            fuel_index=float(levers.get("fuel_index", 1.0)),
            third_party_share=float(levers.get("third_party_share", 0.0)),
            cold_share=facility_cold_share,
        )
        prof = service.profile_for(edge, month=month, frequency_override=frequency)
        capacity = prof.annual_capacity_m3
        capacity = 0.0 if capacity == float("inf") else capacity

        unit_cost = cost.unit_cost_per_m3 + inbound_cost.get(edge.from_node_id, 0.0)
        lanes.append(
            LaneIn(
                edge_id=edge.id,
                code=edge.code,
                hub_id=edge.from_node_id,
                facility_id=edge.to_node_id,
                unit_cost=unit_cost,
                capacity_m3=capacity,
                mode=edge.mode,
            )
        )
        lane_meta[edge.id] = {
            "edge": edge,
            "cost": cost,
            "profile": prof,
            "frequency": frequency,
            "inbound_cost": inbound_cost.get(edge.from_node_id, 0.0),
        }

    # --- equity --------------------------------------------------------------------
    vulnerability = equity_mod.compute_vulnerability(demand_nodes, hub_nodes, edges_by_facility)
    strata = equity_mod.assign_strata(vulnerability)

    unit_costs = [lane.unit_cost for lane in lanes] or [100.0]
    base_penalty = float(
        (scenario.country.config or {}).get("unmet_penalty_per_m3") or median(unit_costs) * 4.0
    )

    facilities: list[FacilityIn] = []
    for node in demand_nodes:
        vuln = vulnerability[node.id]
        facilities.append(
            FacilityIn(
                id=node.id,
                code=node.code,
                demand_m3=demand_m3[node.id],
                cold_share=cold_m3.get(node.id, 0.0) / demand_m3[node.id],
                vulnerability=vuln.score,
                stratum=strata.get(node.id, 0),
                unmet_penalty=base_penalty
                * equity_mod.equity_penalty_weight(vuln.score, float(weights.get("equity", 0.0))),
            )
        )

    options = SolveOptions(
        weight_cost=float(weights.get("cost", 1.0)),
        weight_service=float(weights.get("service", 1.0)),
        weight_equity=float(weights.get("equity", 0.0)),
        min_fill_rate=constraints.get("min_fill_rate"),
        equity_floor=constraints.get("equity_floor"),
        max_budget=constraints.get("max_budget"),
        respect_capacity=bool(constraints.get("respect_capacity", True)),
    )

    solution = solve(facilities, hubs, lanes, options)

    if not solution.feasible:
        result = Result(
            scenario_id=scenario.id,
            status="infeasible",
            kpi_set={},
            solver_log={**solution.log, "base_unmet_penalty_per_m3": round(base_penalty, 2), "month": month},
            error=solution.log.get("reason", "The model could not be solved."),
            runtime_ms=int((time.perf_counter() - started) * 1000),
        )
        session.add(result)
        session.commit()
        return result

    # --- per-facility detail --------------------------------------------------------
    flows_by_facility: dict[int, list[dict]] = {}
    for flow in solution.flows:
        flows_by_facility.setdefault(flow["facility_id"], []).append(flow)

    lanes_by_facility_in_scope: dict[int, list[LaneIn]] = {}
    for lane in lanes:
        lanes_by_facility_in_scope.setdefault(lane.facility_id, []).append(lane)

    node_detail: list[dict] = []
    m3_km = 0.0
    unreachable = 0
    safety_days = float(levers.get("safety_stock_days", 0.0))

    for node in demand_nodes:
        demand = demand_m3[node.id]
        served = solution.served.get(node.id, 0.0)
        facility_flows = sorted(flows_by_facility.get(node.id, []), key=lambda f: -f["volume_m3"])
        available = lanes_by_facility_in_scope.get(node.id, [])
        if not available:
            unreachable += 1

        dominant_edge = None
        if facility_flows:
            dominant_edge = lane_meta[facility_flows[0]["edge_id"]]
        elif available:
            dominant_edge = lane_meta[available[0].edge_id]

        risk_detail = None
        if dominant_edge:
            edge = dominant_edge["edge"]
            m3_km += sum(f["volume_m3"] * edge.distance_km for f in facility_flows if f["edge_id"] == edge.id)
            for flow in facility_flows:
                if flow["edge_id"] != edge.id:
                    m3_km += flow["volume_m3"] * lane_meta[flow["edge_id"]]["edge"].distance_km
            risk_detail = service.facility_risk(
                node,
                edge,
                demand,
                month=month,
                frequency_override=dominant_edge["frequency"],
                safety_stock_days=safety_days,
            )

        vuln = vulnerability[node.id]
        fill = min(1.0, served / demand) if demand else 1.0
        # An unserved facility is at certain risk regardless of what its lane could do.
        stockout = 1.0 if not available else (risk_detail["stockout_risk"] if risk_detail else 1.0)
        stockout = max(stockout, 1.0 - fill)

        node_detail.append(
            {
                "node_id": node.id,
                "code": node.code,
                "name": node.name,
                "admin1": node.admin1,
                "level": node.level,
                "lat": node.lat,
                "lon": node.lon,
                "terrain_class": node.terrain_class,
                "population": float(node.catchment_population or 0.0),
                "demand_m3": round(demand, 3),
                "served_m3": round(served, 3),
                "unmet_m3": round(max(0.0, demand - served), 3),
                "fill_rate": round(fill, 4),
                "cost": round(solution.cost_by_facility.get(node.id, 0.0), 2),
                "cost_per_capita": (
                    round(solution.cost_by_facility.get(node.id, 0.0) / node.catchment_population, 2)
                    if node.catchment_population
                    else None
                ),
                "vulnerability": vuln.score,
                "stratum": strata.get(node.id, 0),
                "restricted_months": vuln.restricted_months,
                "served_by": [
                    {
                        "hub_code": node_by_id[f["hub_id"]].code,
                        "hub_name": node_by_id[f["hub_id"]].name,
                        "edge_code": f["edge_code"],
                        "mode": f["mode"],
                        "volume_m3": f["volume_m3"],
                        "share": round(f["volume_m3"] / served, 3) if served else 0.0,
                    }
                    for f in facility_flows
                ],
                "primary_mode": facility_flows[0]["mode"] if facility_flows else None,
                "service_name": dominant_edge["edge"].service_name if dominant_edge else None,
                "service_frequency": dominant_edge["frequency"] if dominant_edge else None,
                "stockout_risk": round(stockout, 4),
                "storage_days": risk_detail["storage_days"] if risk_detail else 0.0,
                "storage_binding": risk_detail["storage_binding"] if risk_detail else True,
                "reachable": bool(available),
            }
        )

    population_by_node = {n.id: float(n.catchment_population or 0.0) for n in demand_nodes}
    equity_report = equity_mod.equity_report(
        strata,
        vulnerability,
        solution.served,
        demand_m3,
        solution.cost_by_facility,
    )

    kpi_set = kpi_mod.build_kpis(
        solution=solution,
        facilities=facilities,
        node_detail=node_detail,
        equity=equity_report,
        population_by_node=population_by_node,
        total_demand_m3=total_demand,
        cold_demand_m3=total_cold,
        m3_km=m3_km,
        hubs_open=len(solution.hubs_open),
        unreachable=unreachable,
    )

    per_edge_flow = []
    for flow in solution.flows:
        meta = lane_meta[flow["edge_id"]]
        edge = meta["edge"]
        per_edge_flow.append(
            {
                **flow,
                "hub_code": node_by_id[flow["hub_id"]].code,
                "facility_code": node_by_id[flow["facility_id"]].code,
                "from_lat": edge.from_node.lat,
                "from_lon": edge.from_node.lon,
                "to_lat": edge.to_node.lat,
                "to_lon": edge.to_node.lon,
                "distance_km": edge.distance_km,
                "distance_method": edge.distance_method,
                "distance_confidence": edge.distance_confidence,
                "service_name": edge.service_name,
                "frequency": meta["frequency"],
                "cost_breakdown": meta["cost"].as_dict(),
                "inbound_cost_per_m3": round(meta["inbound_cost"], 2),
            }
        )

    result = Result(
        scenario_id=scenario.id,
        status="ok",
        kpi_set=kpi_set,
        per_node_detail=node_detail,
        per_edge_flow=per_edge_flow,
        equity_detail={
            **equity_report,
            "vulnerability": {
                str(node_id): value.as_dict() for node_id, value in vulnerability.items()
            },
        },
        solver_log={
            **solution.log,
            "month": month,
            "month_label": seasonality.MONTHS[month - 1] if month else "Annualised",
            "base_unmet_penalty_per_m3": round(base_penalty, 2),
            "hubs_open_codes": [node_by_id[h].code for h in solution.hubs_open],
            "lanes_dropped_by_season": sum(
                1
                for e in edges
                if e.from_node_id in hub_ids
                and e.to_node_id in demand_m3
                and e.mode in allowed_modes
                and (seasonality.access(e, month) if month else seasonality.annual_access(e)) <= 0.0
            ),
            "demand_growth_applied": growth,
        },
        runtime_ms=int((time.perf_counter() - started) * 1000),
    )
    session.add(result)
    session.commit()
    # No refresh: sessions are configured expire_on_commit=False, so the instance is
    # already fully populated. Refreshing would re-read the row, which under the
    # parallel scenario-set run means a second connection racing the first one's
    # write -- the source of an intermittent 500 on run-set.
    return result
