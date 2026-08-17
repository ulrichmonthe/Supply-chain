"""KPI assembly.

The scorecard is the deliverable format the terms of reference describe, so the KPI
set is fixed and comparable across scenarios rather than assembled ad hoc per run.
Every figure here is annual, in the country's currency, and reconciles: transport +
hub fixed = total, and the per-facility costs sum to the total.
"""

from __future__ import annotations

#: The headline set, in the order the scorecard renders them.
KPI_ORDER = [
    "total_cost",
    "cost_per_m3_delivered",
    "cost_per_capita",
    "fill_rate",
    "population_coverage",
    "worst_stratum_fill_rate",
    "equity_gap",
    "mean_stockout_risk",
    "facilities_at_risk",
    "facilities_unreachable",
    "hubs_open",
]

KPI_META: dict[str, dict] = {
    "total_cost": {"label": "Total annual cost", "unit": "currency", "better": "lower"},
    "transport_cost": {"label": "Transport cost", "unit": "currency", "better": "lower"},
    "hub_fixed_cost": {"label": "Hub operating cost", "unit": "currency", "better": "lower"},
    "cost_per_m3_delivered": {"label": "Cost per m³ delivered", "unit": "currency", "better": "lower"},
    "cost_per_capita": {"label": "Cost per person served", "unit": "currency", "better": "lower"},
    "fill_rate": {"label": "Fill rate (volume)", "unit": "percent", "better": "higher"},
    "population_coverage": {"label": "Population coverage", "unit": "percent", "better": "higher"},
    "worst_stratum_fill_rate": {
        "label": "Fill rate, most vulnerable quintile",
        "unit": "percent",
        "better": "higher",
    },
    "equity_gap": {"label": "Equity gap (best − worst quintile)", "unit": "percent", "better": "lower"},
    "mean_stockout_risk": {"label": "Mean stockout risk", "unit": "percent", "better": "lower"},
    "facilities_at_risk": {"label": "Facilities above 20% stockout risk", "unit": "count", "better": "lower"},
    "facilities_unreachable": {"label": "Facilities with no open lane", "unit": "count", "better": "lower"},
    "hubs_open": {"label": "Hubs open", "unit": "count", "better": "neutral"},
    "unmet_m3": {"label": "Unmet volume", "unit": "m3", "better": "lower"},
    "delivered_m3": {"label": "Delivered volume", "unit": "m3", "better": "higher"},
    "cold_share": {"label": "Cold chain share of volume", "unit": "percent", "better": "neutral"},
    "m3_km": {"label": "Freight task", "unit": "m3-km", "better": "lower"},
}


def build_kpis(
    *,
    solution,
    facilities,
    node_detail: list[dict],
    equity: dict,
    population_by_node: dict[int, float],
    total_demand_m3: float,
    cold_demand_m3: float,
    m3_km: float,
    hubs_open: int,
    unreachable: int,
) -> dict:
    delivered = sum(solution.served.values())
    unmet = max(0.0, total_demand_m3 - delivered)

    population_total = sum(population_by_node.values())
    covered = 0.0
    for facility in facilities:
        demand = facility.demand_m3
        if demand <= 0:
            covered += population_by_node.get(facility.id, 0.0)
            continue
        fill = min(1.0, solution.served.get(facility.id, 0.0) / demand)
        covered += population_by_node.get(facility.id, 0.0) * fill

    risks = [(d.get("stockout_risk", 0.0), population_by_node.get(d["node_id"], 0.0)) for d in node_detail]
    weight = sum(w for _, w in risks)
    mean_risk = sum(r * w for r, w in risks) / weight if weight else 0.0
    at_risk = sum(1 for d in node_detail if d.get("stockout_risk", 0.0) > 0.20)

    return {
        "total_cost": round(solution.total_cost, 2),
        "transport_cost": round(solution.transport_cost, 2),
        "hub_fixed_cost": round(solution.hub_fixed_cost, 2),
        "delivered_m3": round(delivered, 2),
        "unmet_m3": round(unmet, 2),
        "cost_per_m3_delivered": round(solution.total_cost / delivered, 2) if delivered else 0.0,
        "cost_per_capita": round(solution.total_cost / covered, 2) if covered else 0.0,
        "fill_rate": round(delivered / total_demand_m3, 4) if total_demand_m3 else 0.0,
        "population_coverage": round(covered / population_total, 4) if population_total else 0.0,
        "population_served": round(covered),
        "worst_stratum_fill_rate": equity.get("worst_stratum_fill_rate", 0.0),
        "equity_gap": equity.get("equity_gap", 0.0),
        "mean_stockout_risk": round(mean_risk, 4),
        "facilities_at_risk": at_risk,
        "facilities_unreachable": unreachable,
        "hubs_open": hubs_open,
        "cold_share": round(cold_demand_m3 / total_demand_m3, 4) if total_demand_m3 else 0.0,
        "m3_km": round(m3_km, 1),
    }


def compare(baseline: dict, candidate: dict) -> dict:
    """Deltas for the scenario comparison scorecard."""
    out: dict[str, dict] = {}
    for key, meta in KPI_META.items():
        if key not in baseline or key not in candidate:
            continue
        base_value = baseline[key]
        value = candidate[key]
        if not isinstance(base_value, (int, float)) or not isinstance(value, (int, float)):
            continue
        delta = value - base_value
        pct = (delta / base_value) if base_value else None
        if meta["better"] == "lower":
            direction = "better" if delta < 0 else ("worse" if delta > 0 else "flat")
        elif meta["better"] == "higher":
            direction = "better" if delta > 0 else ("worse" if delta < 0 else "flat")
        else:
            direction = "flat"
        out[key] = {
            "baseline": base_value,
            "value": value,
            "delta": round(delta, 4),
            "delta_pct": round(pct, 4) if pct is not None else None,
            "direction": direction,
        }
    return out
