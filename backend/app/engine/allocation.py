"""The allocation model: a mixed-integer program solved with HiGHS.

What it decides
    * which hubs are open (binary)
    * how much volume flows down each hub-to-facility lane (continuous)
    * what is left unserved, and where

Why continuous flows rather than binary single-sourcing: at national scale the
binary version is an order of magnitude harder for a difference no minister can see,
and the build plan is explicit that competing with Cosmic Frog on solver performance
is a losing frame. Aggregate flow at district level, disaggregate for costing.

Why HiGHS: MIT licensed. Commercial solver licensing per country would destroy the
unit economics of a multi-country platform, and on models this size HiGHS is
genuinely competitive.

The infeasibility path matters as much as the optimal one. "Infeasible" is a useless
answer in a workshop, so when an equity floor or a budget cannot be met the model
re-solves as a maximin problem and reports the best attainable floor instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import highspy

INF = highspy.kHighsInf


@dataclass
class FacilityIn:
    id: int
    code: str
    demand_m3: float
    cold_share: float = 0.0
    vulnerability: float = 0.0
    stratum: int = 0
    unmet_penalty: float = 0.0


@dataclass
class HubIn:
    id: int
    code: str
    fixed_cost: float = 0.0
    throughput_m3: float = 0.0  # 0 means unconstrained
    forced_open: Optional[bool] = None  # None = solver decides
    #: Capital cost of opening a closed hub, spread over its amortisation period. The
    #: full one-off figure belongs in the roadmap, never in an annual operating total.
    annualised_capex: float = 0.0
    currently_open: bool = True


@dataclass
class LaneIn:
    edge_id: int
    code: str
    hub_id: int
    facility_id: int
    unit_cost: float
    capacity_m3: float = 0.0  # 0 means unconstrained (on-demand road)
    mode: str = "road"


@dataclass
class SolveOptions:
    weight_cost: float = 1.0
    weight_service: float = 1.0
    weight_equity: float = 0.0
    min_fill_rate: Optional[float] = None
    equity_floor: Optional[float] = None
    max_budget: Optional[float] = None
    respect_capacity: bool = True
    n_strata: int = 5
    mip_rel_gap: float = 0.005
    time_limit_s: float = 30.0


@dataclass
class AllocationSolution:
    status: str
    feasible: bool
    objective: float = 0.0
    transport_cost: float = 0.0
    hub_fixed_cost: float = 0.0
    total_cost: float = 0.0
    flows: list[dict] = field(default_factory=list)
    unmet: dict[int, float] = field(default_factory=dict)
    served: dict[int, float] = field(default_factory=dict)
    cost_by_facility: dict[int, float] = field(default_factory=dict)
    hubs_open: list[int] = field(default_factory=list)
    runtime_ms: int = 0
    log: dict = field(default_factory=dict)


def _hub_annual_charge(hub: HubIn) -> float:
    """Annual cost of having this hub open: operating cost, plus an amortised capital
    charge when it has to be built first."""
    return hub.fixed_cost + (0.0 if hub.currently_open else hub.annualised_capex)


def _lane_upper_bound(lane: LaneIn, facility: FacilityIn, respect_capacity: bool) -> float:
    """A lane can never usefully carry more than its facility demands."""
    bound = facility.demand_m3
    if respect_capacity and lane.capacity_m3 > 0:
        bound = min(bound, lane.capacity_m3)
    return max(0.0, bound)


def solve(
    facilities: list[FacilityIn],
    hubs: list[HubIn],
    lanes: list[LaneIn],
    options: SolveOptions,
) -> AllocationSolution:
    started = time.perf_counter()

    facility_by_id = {f.id: f for f in facilities}
    hub_by_id = {h.id: h for h in hubs}
    lanes = [
        lane
        for lane in lanes
        if lane.facility_id in facility_by_id and lane.hub_id in hub_by_id
    ]

    total_demand = sum(f.demand_m3 for f in facilities)
    if total_demand <= 0 or not lanes:
        return AllocationSolution(
            status="no_demand" if total_demand <= 0 else "no_lanes",
            feasible=False,
            runtime_ms=int((time.perf_counter() - started) * 1000),
            log={"reason": "Model has no demand or no usable lanes after filtering."},
        )

    model = highspy.Highs()
    model.setOptionValue("output_flag", False)
    model.setOptionValue("mip_rel_gap", options.mip_rel_gap)
    model.setOptionValue("time_limit", options.time_limit_s)

    w_cost = max(0.0, options.weight_cost)
    w_service = max(0.0, options.weight_service)

    # --- variables ---------------------------------------------------------------
    lane_vars = []
    for lane in lanes:
        facility = facility_by_id[lane.facility_id]
        ub = _lane_upper_bound(lane, facility, options.respect_capacity)
        lane_vars.append(model.addVariable(lb=0.0, ub=ub, obj=w_cost * lane.unit_cost))

    unmet_vars = {}
    for facility in facilities:
        # Penalty is per m3 unserved, already inflated by the equity weight upstream.
        unmet_vars[facility.id] = model.addVariable(
            lb=0.0, ub=facility.demand_m3, obj=w_service * facility.unmet_penalty
        )

    hub_vars = {}
    for hub in hubs:
        if hub.forced_open is True:
            hub_vars[hub.id] = model.addVariable(lb=1.0, ub=1.0, obj=w_cost * _hub_annual_charge(hub))
        elif hub.forced_open is False:
            hub_vars[hub.id] = model.addVariable(lb=0.0, ub=0.0, obj=0.0)
        else:
            hub_vars[hub.id] = model.addBinary(obj=w_cost * _hub_annual_charge(hub))

    # --- constraints -------------------------------------------------------------
    lanes_by_facility: dict[int, list[int]] = {}
    lanes_by_hub: dict[int, list[int]] = {}
    for index, lane in enumerate(lanes):
        lanes_by_facility.setdefault(lane.facility_id, []).append(index)
        lanes_by_hub.setdefault(lane.hub_id, []).append(index)

    # Demand balance: everything is either delivered or explicitly unmet.
    for facility in facilities:
        indices = lanes_by_facility.get(facility.id, [])
        if indices:
            model.addConstr(
                sum(lane_vars[i] for i in indices) + unmet_vars[facility.id] == facility.demand_m3
            )
        else:
            # Nothing reaches this facility this month. Fix it as fully unmet so the
            # KPI set reports it rather than silently dropping it from the model.
            model.addConstr(unmet_vars[facility.id] == facility.demand_m3)

    # A lane can only be used if its hub is open.
    for index, lane in enumerate(lanes):
        hub = hub_by_id[lane.hub_id]
        if hub.forced_open is True:
            continue
        big_m = _lane_upper_bound(lane, facility_by_id[lane.facility_id], options.respect_capacity)
        if big_m > 0:
            model.addConstr(lane_vars[index] <= big_m * hub_vars[lane.hub_id])

    # Hub throughput.
    if options.respect_capacity:
        for hub in hubs:
            indices = lanes_by_hub.get(hub.id, [])
            if indices and hub.throughput_m3 > 0:
                model.addConstr(
                    sum(lane_vars[i] for i in indices) <= hub.throughput_m3 * hub_vars[hub.id]
                )

    # Service floor across the whole network.
    if options.min_fill_rate is not None:
        model.addConstr(
            sum(unmet_vars[f.id] for f in facilities) <= (1.0 - options.min_fill_rate) * total_demand
        )

    # The equity floor: every stratum must reach the same minimum fill rate.
    stratum_demand: dict[int, float] = {}
    stratum_members: dict[int, list[FacilityIn]] = {}
    for facility in facilities:
        stratum_demand[facility.stratum] = stratum_demand.get(facility.stratum, 0.0) + facility.demand_m3
        stratum_members.setdefault(facility.stratum, []).append(facility)

    if options.equity_floor is not None and options.equity_floor > 0:
        for stratum, members in stratum_members.items():
            demand = stratum_demand[stratum]
            if demand <= 0:
                continue
            model.addConstr(
                sum(unmet_vars[f.id] for f in members) <= (1.0 - options.equity_floor) * demand
            )

    # Budget ceiling on delivered cost (hub fixed costs included).
    if options.max_budget is not None and options.max_budget > 0:
        cost_expr = sum(lane_vars[i] * lanes[i].unit_cost for i in range(len(lanes)))
        cost_expr = cost_expr + sum(hub_vars[h.id] * _hub_annual_charge(h) for h in hubs)
        model.addConstr(cost_expr <= options.max_budget)

    model.setMinimize()
    model.solve()

    status = model.modelStatusToString(model.getModelStatus())
    runtime_ms = int((time.perf_counter() - started) * 1000)

    if status not in ("Optimal", "Feasible point found", "Time limit reached"):
        diagnosis = diagnose(facilities, hubs, lanes, options)
        return AllocationSolution(
            status=status,
            feasible=False,
            runtime_ms=runtime_ms,
            log={
                "reason": _explain_infeasibility(options, diagnosis),
                "diagnosis": diagnosis,
                "solver": "HiGHS",
            },
        )

    # --- extract -----------------------------------------------------------------
    flows: list[dict] = []
    served: dict[int, float] = {f.id: 0.0 for f in facilities}
    cost_by_facility: dict[int, float] = {f.id: 0.0 for f in facilities}
    transport_cost = 0.0

    lane_values = model.vals(lane_vars) if lane_vars else []
    for index, lane in enumerate(lanes):
        volume = float(lane_values[index])
        if volume <= 1e-6:
            continue
        cost = volume * lane.unit_cost
        transport_cost += cost
        served[lane.facility_id] += volume
        cost_by_facility[lane.facility_id] += cost
        flows.append(
            {
                "edge_id": lane.edge_id,
                "edge_code": lane.code,
                "hub_id": lane.hub_id,
                "facility_id": lane.facility_id,
                "mode": lane.mode,
                "volume_m3": round(volume, 4),
                "unit_cost": round(lane.unit_cost, 2),
                "cost": round(cost, 2),
                "capacity_m3": round(lane.capacity_m3, 2) if lane.capacity_m3 > 0 else None,
                "capacity_utilisation": (
                    round(volume / lane.capacity_m3, 4) if lane.capacity_m3 > 0 else None
                ),
            }
        )

    unmet = {f.id: max(0.0, float(model.val(unmet_vars[f.id]))) for f in facilities}
    hubs_open = [h.id for h in hubs if float(model.val(hub_vars[h.id])) > 0.5]
    hub_fixed = sum(_hub_annual_charge(hub_by_id[hid]) for hid in hubs_open)

    # Hub fixed costs are apportioned to facilities by delivered volume so that
    # cost-per-facility and cost-per-capita reconcile to the total.
    total_served = sum(served.values())
    if total_served > 0 and hub_fixed > 0:
        for fid, volume in served.items():
            cost_by_facility[fid] += hub_fixed * (volume / total_served)

    return AllocationSolution(
        status="optimal" if status == "Optimal" else status.lower().replace(" ", "_"),
        feasible=True,
        objective=float(model.getObjectiveValue()),
        transport_cost=round(transport_cost, 2),
        hub_fixed_cost=round(hub_fixed, 2),
        total_cost=round(transport_cost + hub_fixed, 2),
        flows=flows,
        unmet=unmet,
        served=served,
        cost_by_facility=cost_by_facility,
        hubs_open=hubs_open,
        runtime_ms=runtime_ms,
        log={
            "solver": "HiGHS",
            "status": status,
            "lanes": len(lanes),
            "facilities": len(facilities),
            "hubs": len(hubs),
            "binaries": sum(1 for h in hubs if h.forced_open is None),
            "mip_rel_gap_target": options.mip_rel_gap,
            "objective_weights": {
                "cost": options.weight_cost,
                "service": options.weight_service,
                "equity": options.weight_equity,
            },
        },
    )


def diagnose(
    facilities: list[FacilityIn],
    hubs: list[HubIn],
    lanes: list[LaneIn],
    options: SolveOptions,
) -> dict:
    """Answer 'how close could we have got?' when the constrained model fails.

    Solves the maximin relaxation: open every permitted hub, ignore the budget, and
    push the worst-off stratum's fill rate as high as physically possible. The
    answer is the honest ceiling to quote back to the room -- "an 85% floor is not
    reachable in March; 61% is, and here is what it costs."
    """
    relaxed = SolveOptions(
        weight_cost=options.weight_cost,
        weight_service=options.weight_service,
        weight_equity=options.weight_equity,
        respect_capacity=options.respect_capacity,
        n_strata=options.n_strata,
        time_limit_s=min(15.0, options.time_limit_s),
    )

    facility_by_id = {f.id: f for f in facilities}
    hub_by_id = {h.id: h for h in hubs}
    lanes = [lane for lane in lanes if lane.facility_id in facility_by_id and lane.hub_id in hub_by_id]
    if not lanes:
        return {"max_attainable_equity_floor": 0.0, "max_attainable_fill_rate": 0.0}

    model = highspy.Highs()
    model.setOptionValue("output_flag", False)
    model.setOptionValue("time_limit", relaxed.time_limit_s)

    lane_vars = []
    for lane in lanes:
        facility = facility_by_id[lane.facility_id]
        lane_vars.append(
            model.addVariable(lb=0.0, ub=_lane_upper_bound(lane, facility, relaxed.respect_capacity))
        )
    served_expr_by_facility: dict[int, list[int]] = {}
    for index, lane in enumerate(lanes):
        served_expr_by_facility.setdefault(lane.facility_id, []).append(index)

    floor_var = model.addVariable(lb=0.0, ub=1.0)

    stratum_members: dict[int, list[FacilityIn]] = {}
    for facility in facilities:
        stratum_members.setdefault(facility.stratum, []).append(facility)

    for facility in facilities:
        indices = served_expr_by_facility.get(facility.id, [])
        if indices:
            model.addConstr(sum(lane_vars[i] for i in indices) <= facility.demand_m3)

    for _, members in stratum_members.items():
        demand = sum(f.demand_m3 for f in members)
        if demand <= 0:
            continue
        indices = [i for m in members for i in served_expr_by_facility.get(m.id, [])]
        if not indices:
            # A stratum with no lane at all caps the attainable floor at zero.
            model.addConstr(floor_var <= 0.0)
            continue
        model.addConstr(sum(lane_vars[i] for i in indices) >= floor_var * demand)

    for hub in hubs:
        if hub.forced_open is False:
            for index, lane in enumerate(lanes):
                if lane.hub_id == hub.id:
                    model.addConstr(lane_vars[index] <= 0.0)
        elif relaxed.respect_capacity and hub.throughput_m3 > 0:
            indices = [i for i, lane in enumerate(lanes) if lane.hub_id == hub.id]
            if indices:
                model.addConstr(sum(lane_vars[i] for i in indices) <= hub.throughput_m3)

    model.maximize(floor_var)
    status = model.modelStatusToString(model.getModelStatus())
    if status != "Optimal":
        return {"max_attainable_equity_floor": None, "solver_status": status}

    attainable_floor = float(model.val(floor_var))
    total_demand = sum(f.demand_m3 for f in facilities)
    delivered = sum(float(v) for v in model.vals(lane_vars)) if lane_vars else 0.0

    return {
        "max_attainable_equity_floor": round(attainable_floor, 4),
        "max_attainable_fill_rate": round(delivered / total_demand, 4) if total_demand else 0.0,
        "solver_status": status,
    }


def _explain_infeasibility(options: SolveOptions, diagnosis: dict) -> str:
    attainable = diagnosis.get("max_attainable_equity_floor")
    parts = ["No plan satisfies all of the constraints you set."]
    if options.equity_floor and attainable is not None:
        if attainable < options.equity_floor:
            parts.append(
                f"The equity floor of {options.equity_floor:.0%} is the binding one: with every "
                f"permitted hub open and no budget limit, the most that can be guaranteed to "
                f"every stratum is {attainable:.0%}. Lower the floor, open another hub, or add a "
                f"lane that reaches the most vulnerable quintile."
            )
    if options.max_budget:
        parts.append(f"The budget ceiling of {options.max_budget:,.0f} may also be binding.")
    if options.min_fill_rate:
        parts.append(f"The network-wide fill rate floor is {options.min_fill_rate:.0%}.")
    return " ".join(parts)
