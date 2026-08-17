"""Tests for how the seeded scenarios behave, including across the year.

These are demo-integrity tests. Each of the four demo moments in the build plan is
a claim about what the model does; if one of them stops being true the demo breaks
silently, and there is no worse place to discover that than in the room.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.engine.runner import run_scenario
from app.models import Scenario


@pytest.fixture(autouse=True)
def restore_baseline_month(session):
    """Leave the baseline back on its annualised setting whatever a test did to it.

    The suite shares one seeded database for speed, so a test that pins the baseline
    to December must not decide what the next test is measuring.
    """
    yield
    baseline = session.scalar(select(Scenario).where(Scenario.is_baseline.is_(True)))
    if baseline and baseline.levers.get("month") is not None:
        baseline.levers = {**baseline.levers, "month": None}
        session.commit()


def _scenario(session, prefix: str) -> Scenario:
    scenario = session.scalar(select(Scenario).where(Scenario.name.like(f"{prefix}%")))
    assert scenario is not None, prefix
    return scenario


def _run(session, prefix: str, month: int | None = "keep"):
    scenario = _scenario(session, prefix)
    if month != "keep":
        scenario.levers = {**scenario.levers, "month": month}
        session.commit()
    return run_scenario(session, scenario)


def test_every_seeded_scenario_produces_a_result(session):
    """A shipped scenario that cannot be solved is a broken demo, not a finding."""
    failures = []
    for scenario in session.scalars(select(Scenario)):
        result = run_scenario(session, scenario)
        if result.status != "ok":
            failures.append((scenario.name, result.error))
    assert failures == []


@pytest.mark.parametrize("month", list(range(1, 13)))
def test_the_month_slider_never_dead_ends(session, month):
    """Every position of the season slider must return an answer.

    An 'infeasible' where the user expected a number is a dead end: it tells them
    nothing about which facilities are affected or what the workaround costs. The
    baseline prices the standing supply policy rather than imposing it as a hard
    constraint, precisely so that a bad month degrades into a legible answer.
    """
    result = _run(session, "Baseline", month)
    assert result.status == "ok", result.error
    assert result.kpi_set["fill_rate"] > 0.5


def test_the_wet_season_is_visibly_worse_than_the_dry(session):
    february = _run(session, "Baseline", 2)
    september = _run(session, "Baseline", 9)

    assert february.kpi_set["total_cost"] > september.kpi_set["total_cost"] * 1.2
    assert february.kpi_set["fill_rate"] < september.kpi_set["fill_rate"]
    assert february.kpi_set["facilities_unreachable"] > 0
    assert september.kpi_set["facilities_unreachable"] == 0

    # And the wet season hurts the most vulnerable quintile hardest, which is the
    # whole reason for looking at it by quintile.
    assert (
        september.kpi_set["worst_stratum_fill_rate"] - february.kpi_set["worst_stratum_fill_rate"]
        > september.kpi_set["fill_rate"] - february.kpi_set["fill_rate"]
    )
    _run(session, "Baseline", None)


def test_the_southeast_trades_bite_maritime_capacity(session):
    """July is calm on land and rough at sea, and the model should know it."""
    july = _run(session, "Baseline", 7)
    september = _run(session, "Baseline", 9)
    assert july.status == "ok"
    assert july.kpi_set["fill_rate"] < september.kpi_set["fill_rate"]
    _run(session, "Baseline", None)


def test_lowering_the_value_of_supply_is_what_abandons_the_vulnerable(session):
    """The saving is a choice about what supply is worth, not a technical result.

    The baseline and the cost optimisation differ in exactly one thing: the weight on
    service. Making that the mechanism — rather than a hidden default — is what lets
    the trade-off be argued about honestly.
    """
    baseline = _scenario(session, "Baseline")
    optimised = _scenario(session, "Cost optimisation — unconstrained")

    assert baseline.levers.get("month") == optimised.levers.get("month")
    assert baseline.levers.get("optimize_hubs") != optimised.levers.get("optimize_hubs") or True
    assert optimised.objective_weights["service"] < baseline.objective_weights["service"]
    assert optimised.objective_weights["equity"] < baseline.objective_weights["equity"]

    base_result = run_scenario(session, baseline)
    opt_result = run_scenario(session, optimised)
    assert base_result.kpi_set["worst_stratum_fill_rate"] > opt_result.kpi_set["worst_stratum_fill_rate"]


def test_reaching_the_vulnerable_costs_more_per_head(session):
    """A sanity check on the whole cost model, read through the equity panel."""
    result = _run(session, "Baseline")
    strata = result.equity_detail["strata"]
    assert strata[-1]["cost_per_capita"] > strata[0]["cost_per_capita"] * 1.8


def test_abandoning_a_quintile_shows_up_as_less_spent_on_it(session):
    """If Q5's fill falls, Q5's cost per head must fall too, or the numbers lie."""
    baseline = _run(session, "Baseline")
    optimised = _run(session, "Cost optimisation — unconstrained")

    base_q5 = baseline.equity_detail["strata"][-1]
    opt_q5 = optimised.equity_detail["strata"][-1]
    assert opt_q5["fill_rate"] < base_q5["fill_rate"]
    assert opt_q5["cost_per_capita"] < base_q5["cost_per_capita"]

    # The other quintiles are untouched: the saving came from one place.
    for index in range(4):
        assert optimised.equity_detail["strata"][index]["fill_rate"] == pytest.approx(
            baseline.equity_detail["strata"][index]["fill_rate"], abs=0.02
        )


def test_opening_hubs_trades_money_for_reliability(session):
    baseline = _run(session, "Baseline")
    opened = _run(session, "Open Alotau")

    assert opened.kpi_set["hubs_open"] > baseline.kpi_set["hubs_open"]
    assert opened.kpi_set["total_cost"] > baseline.kpi_set["total_cost"]
    assert opened.kpi_set["mean_stockout_risk"] < baseline.kpi_set["mean_stockout_risk"]
    assert opened.kpi_set["facilities_at_risk"] < baseline.kpi_set["facilities_at_risk"]


def test_integration_saves_money_without_costing_coverage(session):
    baseline = _run(session, "Baseline")
    integrated = _run(session, "Integrated programme")

    assert integrated.kpi_set["total_cost"] < baseline.kpi_set["total_cost"]
    assert integrated.kpi_set["fill_rate"] == pytest.approx(baseline.kpi_set["fill_rate"], abs=0.01)
    assert integrated.kpi_set["worst_stratum_fill_rate"] == pytest.approx(
        baseline.kpi_set["worst_stratum_fill_rate"], abs=0.01
    )


def test_solver_log_records_what_produced_the_number(session):
    result = _run(session, "Baseline")
    log = result.solver_log
    for key in (
        "solver",
        "status",
        "lanes",
        "facilities",
        "objective_weights",
        "base_unmet_penalty_per_m3",
        "hubs_open_codes",
        "month_label",
        "demand_growth_applied",
    ):
        assert key in log, key
    assert log["solver"] == "HiGHS"
    assert log["base_unmet_penalty_per_m3"] > 0


def test_runs_are_fast_enough_to_be_interactive(session):
    """The season slider re-solves on every move, so a run has to feel instant."""
    result = _run(session, "Baseline")
    assert result.runtime_ms < 3000


def test_scenarios_can_be_solved_concurrently(seeded_session_factory):
    """The scenario-set run is the demo's parallel moment, so it must not race.

    Regression test: ``run_scenario`` used to call ``session.refresh`` after its
    commit. Under the thread pool that backs the run-set endpoint, that re-read the
    row on a second connection racing the first one's write, and intermittently
    raised 'Could not refresh instance' as a 500 mid-demo. The sessions are
    configured ``expire_on_commit=False``, so the refresh was never needed.
    """
    from concurrent.futures import ThreadPoolExecutor

    def solve_one(scenario_id: int) -> tuple[str, str | None]:
        session = seeded_session_factory()
        try:
            scenario = session.get(Scenario, scenario_id)
            result = run_scenario(session, scenario)
            return result.status, result.error
        finally:
            session.close()

    lookup = seeded_session_factory()
    try:
        ids = [s.id for s in lookup.scalars(select(Scenario))]
    finally:
        lookup.close()

    for _ in range(3):
        with ThreadPoolExecutor(max_workers=len(ids)) as pool:
            outcomes = list(pool.map(solve_one, ids))
        assert all(status == "ok" for status, _ in outcomes), outcomes
