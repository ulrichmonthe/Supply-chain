"""The confidence budget: a result knows how much of its demand was estimated, what
the answer becomes with every estimate a swing lower and higher, and says so in one
sentence a decision-maker can carry."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_session
from app.engine import confidence as confidence_mod
from app.engine.runner import run_scenario
from app.main import app
from app.models import Demand, Scenario


@pytest.fixture
def client(isolated_session_factory):
    def _session_override():
        session = isolated_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = _session_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _scenario(session, name_start: str) -> Scenario:
    return next(s for s in session.scalars(select(Scenario)) if s.name.startswith(name_start))


@pytest.fixture(scope="module")
def solved(isolated_session_factory):
    session = isolated_session_factory()
    baseline = run_scenario(session, _scenario(session, "Baseline"))
    option = run_scenario(session, _scenario(session, "Cost optimisation — unconstrained"))
    yield session, baseline, option
    session.close()


def test_a_result_records_how_much_of_its_demand_is_estimated(solved):
    _, baseline, _ = solved
    estimated = baseline.confidence["estimated"]
    # The seed is a population proxy everywhere, and says so.
    assert estimated["demand_share_m3"] == 1.0
    assert estimated["demand_rows_estimated"] == estimated["demand_rows"] > 0
    assert estimated["facilities_with_estimated_demand"] == estimated["facilities"] > 0
    assert estimated["facilities_with_estimated_storage"] == estimated["facilities"]


def test_the_same_scenario_is_solved_at_both_ends_of_the_swing(solved):
    _, baseline, _ = solved
    sensitivity = baseline.confidence["sensitivity"]
    assert sensitivity["swing"] == 0.3
    low, high = sensitivity["low"], sensitivity["high"]
    assert low["factor"] == 0.7 and high["factor"] == 1.3
    assert low["status"] == high["status"] == "ok"
    # Less to move costs less; more costs more.
    assert low["kpi_set"]["total_cost"] < baseline.kpi_set["total_cost"] < high["kpi_set"]["total_cost"]
    assert sensitivity["runtime_ms"] < 3000


def test_the_ends_are_not_persisted_as_results(solved):
    from app.models import Result

    session, baseline, option = solved
    ids = [r.id for r in session.scalars(select(Result).where(Result.scenario_id == baseline.scenario_id))]
    assert ids == [baseline.id]


def test_the_baseline_gets_a_sentence_about_its_own_estimates(solved):
    _, baseline, _ = solved
    assessment = confidence_mod.assess(baseline)
    assert assessment["share"] == 1.0
    assert assessment["tested"] is True
    assert assessment["holds"] is True
    assert assessment["sentence"].startswith("100% of the demand this rests on is estimated")
    assert "stores stay open" in assessment["sentence"]
    low, base, high = assessment["range"]["total_cost"]
    assert low < base < high


def test_an_option_is_compared_end_against_end(solved):
    _, baseline, option = solved
    assessment = confidence_mod.assess(option, baseline)
    assert assessment["baseline_tested"] is True
    assert assessment["holds"] is True
    assert "stays cheaper than today at both ends" in assessment["sentence"]


def test_a_reversal_is_named_rather_than_averaged_away():
    """A made-up pair where the option is cheaper as modelled but dearer at +30%."""

    class R:
        def __init__(self, cost, low, high, hubs=("A",), status="ok"):
            self.kpi_set = {"total_cost": cost, "fill_rate": 1.0, "worst_stratum_fill_rate": 0.9}
            self.solver_log = {"hubs_open_codes": list(hubs)}
            self.status = status
            self.confidence = {
                "estimated": {"demand_share_m3": 0.41, "facilities": 10, "facilities_with_estimated_demand": 4},
                "sensitivity": {
                    "swing": 0.3,
                    "low": {"status": "ok", "kpi_set": {"total_cost": low, "fill_rate": 1.0, "worst_stratum_fill_rate": 0.9}, "hubs_open_codes": list(hubs)},
                    "high": {"status": "ok", "kpi_set": {"total_cost": high, "fill_rate": 1.0, "worst_stratum_fill_rate": 0.9}, "hubs_open_codes": list(hubs)},
                },
            }

    baseline = R(100, 70, 130)
    option = R(95, 60, 140)  # cheaper today, dearer if demand is a third higher
    assessment = confidence_mod.assess(option, baseline)
    assert assessment["holds"] is False
    assert "41% of the demand" in assessment["sentence"]
    assert "reversing the comparison" in assessment["changes"][0]
    assert "30% higher" in assessment["changes"][0]


def test_a_changed_store_set_is_a_flip():
    class R:
        kpi_set = {"total_cost": 100, "fill_rate": 1.0, "worst_stratum_fill_rate": 0.9}
        solver_log = {"hubs_open_codes": ["LAE", "POM"]}
        status = "ok"
        confidence = {
            "estimated": {"demand_share_m3": 0.6, "facilities": 10, "facilities_with_estimated_demand": 6},
            "sensitivity": {
                "swing": 0.3,
                "low": {"status": "ok", "kpi_set": {"total_cost": 80, "fill_rate": 1.0, "worst_stratum_fill_rate": 0.9}, "hubs_open_codes": ["POM"]},
                "high": {"status": "infeasible", "kpi_set": {}, "hubs_open_codes": []},
            },
        }

    assessment = confidence_mod.assess(R())
    assert assessment["holds"] is False
    assert any("close LAE" in c for c in assessment["changes"])
    assert any("can no longer be met" in c for c in assessment["changes"])
    assert assessment["range"]["total_cost"] == [80, 100, None]


def test_nothing_estimated_means_nothing_to_test(isolated_session_factory):
    session = isolated_session_factory()
    # Pin every row: the figures are now "recorded", and the run has no ends to solve.
    # The database is shared with the other tests in this module, so the rules go back
    # afterwards.
    kept = {row.id: row.derivation for row in session.scalars(select(Demand))}
    try:
        for row in session.scalars(select(Demand)):
            row.derivation = None
        session.commit()
        result = run_scenario(session, _scenario(session, "Baseline"))
        assert result.confidence["estimated"]["demand_share_m3"] == 0.0
        assert result.confidence["sensitivity"] is None
        assessment = confidence_mod.assess(result)
        assert assessment["holds"] is True
        assert assessment["sentence"].startswith("Nothing in this plan's demand is estimated")
    finally:
        for row in session.scalars(select(Demand)):
            row.derivation = kept.get(row.id)
        session.commit()
        session.close()


def test_a_country_can_set_its_own_swing():
    class C:
        config = {"estimators": {"swing": 0.2}}

    assert confidence_mod.swing_for(C()) == 0.2
    C.config = {}
    assert confidence_mod.swing_for(C()) == confidence_mod.DEFAULT_SWING
    C.config = {"estimators": {"swing": 5}}
    assert confidence_mod.swing_for(C()) == 1.0


def test_the_scorecard_and_the_report_carry_the_confidence_line(client: TestClient):
    client.post("/api/scenarios/1/run")
    client.post("/api/scenarios/2/run")
    card = client.get("/api/countries/1/scorecard").json()
    rows = {row["scenario_id"]: row for row in card["rows"]}
    assert rows[1]["confidence"]["share"] == 1.0
    assert rows[2]["confidence"]["baseline_tested"] is True
    assert rows[2]["confidence"]["holds"] in (True, False)

    result = client.get(f"/api/results/{rows[2]['result_id']}").json()
    assert result["confidence"]["sensitivity"]["swing"] == 0.3

    page = client.get("/api/scenarios/2/report.html").text
    assert "How sure this is" in page
    assert "100% of the demand this rests on is estimated" in page
    assert "Estimates 30% lower" in page
