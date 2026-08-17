"""Costed implementation roadmap.

The terms of reference ask for a costed roadmap. If the tool does not generate it,
the expert writes it by hand and the tool is decoration. So the roadmap is derived
mechanically from the difference between two scenarios -- what changed, what it
costs, what has to happen first -- and then the expert edits it. That is the right
division of labour: the model owns arithmetic and consistency, the expert owns
judgement and sequencing.

Phasing follows what the change actually requires:

    Phase 1  0-6 months    policy and planning changes, no capital, no new assets
    Phase 2  6-18 months   operational changes: timetables, catchment reassignment
    Phase 3  18-36 months  anything requiring capital or a new facility
"""

from __future__ import annotations

PHASES = {
    1: {"label": "Phase 1 — Policy and planning", "window": "0–6 months"},
    2: {"label": "Phase 2 — Operational redesign", "window": "6–18 months"},
    3: {"label": "Phase 3 — Capital investment", "window": "18–36 months"},
}


def _hub_index(result) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for flow in result.per_edge_flow or []:
        code = flow["hub_code"]
        entry = index.setdefault(code, {"volume_m3": 0.0, "facilities": set(), "cost": 0.0})
        entry["volume_m3"] += flow["volume_m3"]
        entry["facilities"].add(flow["facility_code"])
        entry["cost"] += flow["cost"]
    return index


def _assignment(result) -> dict[str, str]:
    """Facility -> hub carrying the largest share, for reassignment counting."""
    best: dict[str, tuple[str, float]] = {}
    for flow in result.per_edge_flow or []:
        facility = flow["facility_code"]
        current = best.get(facility)
        if current is None or flow["volume_m3"] > current[1]:
            best[facility] = (flow["hub_code"], flow["volume_m3"])
    return {facility: hub for facility, (hub, _) in best.items()}


def _frequencies(result) -> dict[str, str]:
    out: dict[str, str] = {}
    for flow in result.per_edge_flow or []:
        if flow.get("service_name") and flow.get("frequency"):
            out[flow["service_name"]] = flow["frequency"]
    return out


def build_roadmap(
    *,
    baseline_scenario,
    baseline_result,
    scenario,
    result,
    nodes_by_code: dict,
    currency: str = "USD",
) -> dict:
    """Produce the phased, costed step list between baseline and scenario."""
    steps: list[dict] = []

    base_kpis = baseline_result.kpi_set or {}
    kpis = result.kpi_set or {}
    base_levers = baseline_scenario.levers or {}
    levers = scenario.levers or {}
    base_constraints = baseline_scenario.constraints or {}
    constraints = scenario.constraints or {}

    base_hubs = set((baseline_result.solver_log or {}).get("hubs_open_codes", []))
    new_hubs = set((result.solver_log or {}).get("hubs_open_codes", []))

    opened = sorted(new_hubs - base_hubs)
    closed = sorted(base_hubs - new_hubs)

    base_hub_index = _hub_index(baseline_result)
    hub_index = _hub_index(result)

    # --- hub structure ------------------------------------------------------------
    for code in opened:
        node = nodes_by_code.get(code)
        volume = hub_index.get(code, {}).get("volume_m3", 0.0)
        facilities = len(hub_index.get(code, {}).get("facilities", set()))
        steps.append(
            {
                "phase": 3,
                "category": "network_structure",
                "title": f"Establish distribution hub at {node.name if node else code}",
                "detail": (
                    f"Stand up {node.name if node else code} as an operating hub serving "
                    f"{facilities} facilities and {volume:,.0f} m³ per year. Requires storage "
                    f"fit-out, cold chain commissioning, staffing and an inbound primary route."
                ),
                "one_off_cost": round(float(node.hub_open_capex) if node else 0.0, 2),
                "annual_cost_delta": round(float(node.hub_fixed_cost) if node else 0.0, 2),
                "owner": "NDoH infrastructure + provincial health authority",
                "depends_on": [],
                "evidence": f"Scenario '{scenario.name}' assigns {volume:,.0f} m³/yr to this hub.",
                "risk": "Capital approval and site availability are the critical path.",
            }
        )

    for code in closed:
        node = nodes_by_code.get(code)
        base_volume = base_hub_index.get(code, {}).get("volume_m3", 0.0)
        steps.append(
            {
                "phase": 3,
                "category": "network_structure",
                "title": f"Decommission hub role at {node.name if node else code}",
                "detail": (
                    f"Transfer {base_volume:,.0f} m³/yr and its facility catchment to the "
                    f"retained hubs. Retain the site as a provincial store if required for "
                    f"emergency buffer stock."
                ),
                "one_off_cost": round(base_volume * 12.0, 2),
                "annual_cost_delta": round(-float(node.hub_fixed_cost) if node else 0.0, 2),
                "owner": "NDoH supply chain directorate",
                "depends_on": [f"Establish distribution hub at {nodes_by_code[c].name}" for c in opened if c in nodes_by_code],
                "risk": "Politically sensitive. Requires provincial engagement before announcement.",
                "evidence": f"Baseline routed {base_volume:,.0f} m³/yr through this hub; scenario routes none.",
            }
        )

    # --- catchment reassignment -----------------------------------------------------
    base_assignment = _assignment(baseline_result)
    assignment = _assignment(result)
    reassigned = [
        (facility, base_assignment[facility], assignment[facility])
        for facility in assignment
        if facility in base_assignment and base_assignment[facility] != assignment[facility]
    ]
    if reassigned:
        steps.append(
            {
                "phase": 2,
                "category": "catchment",
                "title": f"Reassign {len(reassigned)} facilities to a different supplying hub",
                "detail": (
                    "Update the distribution plan, requisition routing in the eLMIS, and "
                    "transport contracts. Facilities affected: "
                    + ", ".join(f"{f} ({a}→{b})" for f, a, b in reassigned[:12])
                    + ("…" if len(reassigned) > 12 else "")
                ),
                "one_off_cost": round(len(reassigned) * 450.0, 2),
                "annual_cost_delta": 0.0,
                "owner": "MSPDB / provincial logistics officers",
                "depends_on": [],
                "risk": "Requires mSupply configuration change and re-training at each facility.",
                "evidence": f"{len(reassigned)} of {len(assignment)} facilities change supplying hub.",
            }
        )

    # --- scheduled services ----------------------------------------------------------
    base_freq = _frequencies(baseline_result)
    freq = _frequencies(result)
    for name, new_value in sorted(freq.items()):
        old_value = base_freq.get(name)
        if old_value and old_value != new_value:
            steps.append(
                {
                    "phase": 2,
                    "category": "scheduled_service",
                    "title": f"Change {name} from {old_value.lower()} to {new_value.lower()}",
                    "detail": (
                        "Renegotiate the charter or freight agreement and republish the "
                        "distribution calendar to every facility on the run."
                    ),
                    "one_off_cost": 3500.0,
                    "annual_cost_delta": None,
                    "owner": "MSPDB transport unit",
                    "depends_on": [],
                    "risk": "Carrier availability. Confirm the operator can hold the new slot.",
                    "evidence": f"Timetable lever changed for service '{name}'.",
                }
            )

    # --- policy levers ----------------------------------------------------------------
    if levers.get("integration_policy") != base_levers.get("integration_policy", "vertical"):
        steps.append(
            {
                "phase": 1,
                "category": "policy",
                "title": f"Move to a {levers.get('integration_policy')} programme supply chain",
                "detail": (
                    "Consolidate programme-specific storage and distribution (EPI, malaria, "
                    "TB/HIV) into the shared network. Modelled as a reduction in duplicated "
                    "hub operating cost."
                ),
                "one_off_cost": 45000.0,
                "annual_cost_delta": None,
                "owner": "NDoH executive + programme managers + funding partners",
                "depends_on": [],
                "risk": "The highest-value and hardest step. Programme funders must agree first.",
                "evidence": "Integration policy lever changed between scenarios.",
            }
        )

    third_party = float(levers.get("third_party_share", 0.0) or 0.0)
    base_third_party = float(base_levers.get("third_party_share", 0.0) or 0.0)
    if abs(third_party - base_third_party) > 0.01:
        direction = "increase" if third_party > base_third_party else "reduce"
        steps.append(
            {
                "phase": 2,
                "category": "policy",
                "title": f"{direction.capitalize()} third-party transport share to {third_party:.0%}",
                "detail": (
                    "Tender and contract commercial road transport for the affected lanes. "
                    "Retain in-house capacity for lanes no commercial operator will serve."
                ),
                "one_off_cost": 18000.0,
                "annual_cost_delta": None,
                "owner": "NDoH procurement",
                "depends_on": [],
                "risk": "Thin operator market outside the main corridors; verify before committing.",
                "evidence": f"Third-party share lever moved from {base_third_party:.0%} to {third_party:.0%}.",
            }
        )

    if constraints.get("equity_floor") and not base_constraints.get("equity_floor"):
        floor = float(constraints["equity_floor"])
        steps.append(
            {
                "phase": 1,
                "category": "policy",
                "title": f"Adopt a {floor:.0%} minimum fill rate for every vulnerability quintile",
                "detail": (
                    "Make the equity floor an explicit planning standard rather than an "
                    "outcome. It becomes a constraint on every subsequent network decision "
                    "and a reportable indicator."
                ),
                "one_off_cost": 0.0,
                "annual_cost_delta": round(
                    float(kpis.get("total_cost", 0)) - float(base_kpis.get("total_cost", 0)), 2
                ),
                "owner": "NDoH policy + UNICEF/Gavi partners",
                "depends_on": [],
                "risk": (
                    "Costs more than the unconstrained optimum. That is the point: it is the "
                    "price of not funding the saving out of the hardest-to-reach quintile."
                ),
                "evidence": (
                    f"Worst-quintile fill rate moves from "
                    f"{float(base_kpis.get('worst_stratum_fill_rate', 0)):.0%} to "
                    f"{float(kpis.get('worst_stratum_fill_rate', 0)):.0%}."
                ),
            }
        )

    # --- assemble ----------------------------------------------------------------------
    for index, step in enumerate(steps, start=1):
        step["id"] = f"S{index:02d}"
        step["phase_label"] = PHASES[step["phase"]]["label"]
        step["phase_window"] = PHASES[step["phase"]]["window"]

    one_off_total = sum(float(s.get("one_off_cost") or 0.0) for s in steps)
    annual_delta = float(kpis.get("total_cost", 0.0)) - float(base_kpis.get("total_cost", 0.0))
    annual_saving = -annual_delta

    payback_years = None
    if annual_saving > 0 and one_off_total > 0:
        payback_years = round(one_off_total / annual_saving, 2)

    return {
        "scenario": scenario.name,
        "baseline": baseline_scenario.name,
        "currency": currency,
        "generated_from": {
            "baseline_result_id": baseline_result.id,
            "result_id": result.id,
        },
        "steps": steps,
        "summary": {
            "one_off_cost_total": round(one_off_total, 2),
            "annual_cost_delta": round(annual_delta, 2),
            "annual_saving": round(annual_saving, 2),
            "annual_saving_pct": (
                round(annual_saving / float(base_kpis["total_cost"]), 4)
                if base_kpis.get("total_cost")
                else None
            ),
            "payback_years": payback_years,
            "worst_quintile_fill_baseline": base_kpis.get("worst_stratum_fill_rate"),
            "worst_quintile_fill_scenario": kpis.get("worst_stratum_fill_rate"),
            "population_coverage_baseline": base_kpis.get("population_coverage"),
            "population_coverage_scenario": kpis.get("population_coverage"),
        },
        "phases": [
            {
                **PHASES[phase],
                "phase": phase,
                "steps": [s["id"] for s in steps if s["phase"] == phase],
                "one_off_cost": round(
                    sum(float(s.get("one_off_cost") or 0.0) for s in steps if s["phase"] == phase), 2
                ),
            }
            for phase in sorted(PHASES)
        ],
    }
