"""Twelve linked months with stock carried between them: the wet season as it is
lived, not as one set of conditions held all year."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.engine.allocation import FacilityIn, HubIn, LaneIn, SolveOptions, solve_multi_period
from app.engine.runner import run_scenario
from app.models import Scenario


def _facility(storage: float) -> FacilityIn:
    return FacilityIn(id=1, code="F", demand_m3=120.0, unmet_penalty=1000.0, storage_m3=storage)


def _hub() -> HubIn:
    return HubIn(id=10, code="H", fixed_cost=0.0, throughput_m3=0.0, forced_open=True, annualised_capex=0.0, currently_open=True)


def test_a_facility_stocks_up_before_its_road_closes_when_it_has_the_shelf():
    # The only lane is closed in months 3 and 4 (index 2, 3). With shelf space, the
    # model delivers ahead and carries stock; without it, those months go short.
    closed = [None] * 12
    closed[2] = closed[3] = 0.0
    lane = LaneIn(edge_id=1, code="L", hub_id=10, facility_id=1, unit_cost=10.0, monthly_unit_cost=[10.0] * 12, monthly_capacity=closed)
    with_shelf = solve_multi_period([_facility(storage=40.0)], [_hub()], [lane], SolveOptions(), holding_cost_per_m3_month=0.1)
    assert with_shelf.feasible
    assert sum(with_shelf.unmet.values()) < 1e-6, "everything is delivered ahead of the closure"
    assert with_shelf.monthly["stock_m3"][1] >= 19.9, "February closes with two months of stock on the shelf"
    assert with_shelf.monthly["fill_rate"][2] == 1.0 and with_shelf.monthly["fill_rate"][3] == 1.0

    no_shelf = solve_multi_period([_facility(storage=0.0)], [_hub()], [lane], SolveOptions(), holding_cost_per_m3_month=0.1)
    assert no_shelf.feasible
    assert abs(sum(no_shelf.unmet.values()) - 20.0) < 1e-6, "two months of demand cannot be met"
    assert no_shelf.monthly["facilities_short"][2] == 1 and no_shelf.monthly["facilities_short"][3] == 1
    assert no_shelf.monthly["facilities"][1]["months_short"] == 2


def test_a_dearer_month_is_avoided_by_buying_ahead():
    costs = [10.0] * 12
    costs[6] = 100.0  # July freight is ten times the price
    lane = LaneIn(edge_id=1, code="L", hub_id=10, facility_id=1, unit_cost=10.0, monthly_unit_cost=costs, monthly_capacity=[None] * 12)
    solution = solve_multi_period([_facility(storage=15.0)], [_hub()], [lane], SolveOptions(), holding_cost_per_m3_month=0.5)
    assert solution.feasible
    flow = solution.flows[0]["monthly_m3"]
    assert flow[6] < 0.01, "nothing is bought in the dear month"
    assert solution.monthly["stock_m3"][5] >= 9.9, "June closes with July's stock already on the shelf"


def test_the_year_is_cyclic_so_stock_cannot_appear_from_nowhere():
    closed = [None] * 12
    closed[0] = 0.0  # January closed: December must carry the stock
    lane = LaneIn(edge_id=1, code="L", hub_id=10, facility_id=1, unit_cost=10.0, monthly_unit_cost=[10.0] * 12, monthly_capacity=closed)
    solution = solve_multi_period([_facility(storage=12.0)], [_hub()], [lane], SolveOptions(), holding_cost_per_m3_month=0.1)
    assert solution.feasible and sum(solution.unmet.values()) < 1e-6
    assert solution.monthly["stock_m3"][11] >= 9.9


def test_the_seeded_network_runs_over_twelve_months(isolated_session_factory):
    # Its own database: run_scenario commits, and the shared one must not remember this.
    session = isolated_session_factory()
    baseline = next(s for s in session.scalars(select(Scenario)) if s.is_baseline)
    baseline.levers = {**(baseline.levers or {}), "multi_period": True, "month": 3}
    result = run_scenario(session, baseline)
    assert result.status == "ok", result.error
    log = result.solver_log
    assert log["month_label"] == "Twelve months, stock carried" and log["multi_period"] is True
    assert log["month"] is None, "a pinned month is overridden: the year is the point"
    monthly = log["monthly"]
    assert len(monthly["fill_rate"]) == 12 and len(monthly["stock_m3"]) == 12
    assert "worst_month_fill_rate" in result.kpi_set and "months_with_shortfall" in result.kpi_set
    assert result.kpi_set["worst_month_fill_rate"] <= result.kpi_set["fill_rate"] + 1e-9
    node = next(n for n in result.per_node_detail if n.get("monthly"))
    assert len(node["monthly"]["fill_rate"]) == 12 and "peak_stock_m3" in node["monthly"]
    flows_with_months = [f for f in result.per_edge_flow if f.get("monthly_m3")]
    assert flows_with_months and len(flows_with_months[0]["monthly_m3"]) == 12
    # The confidence budget still rides on the run.
    assert result.confidence["sensitivity"]["low"]["status"] == "ok"
    assert result.runtime_ms < 60000
    baseline.levers = {k: v for k, v in baseline.levers.items() if k not in ("multi_period", "month")}
    session.commit()
    session.close()
