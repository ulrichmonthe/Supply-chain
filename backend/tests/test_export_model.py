"""The exported model, solved by the same allocation solver, reproduces the runner."""

from __future__ import annotations

from sqlalchemy import select

from app.engine import export_model
from app.engine.allocation import FacilityIn, HubIn, LaneIn, SolveOptions, solve
from app.engine.runner import run_scenario
from app.models import Scenario


def _solve_export(model: dict):
    facilities = [
        FacilityIn(
            id=f["id"], code=f["code"], demand_m3=f["demand_m3"], cold_share=f["cold_share"],
            vulnerability=f["vulnerability"], stratum=f["stratum"],
            unmet_penalty=model["base_penalty"] * (1 + 3 * max(0.0, model["weights"].get("equity", 0.0)) * min(1.0, max(0.0, f["vulnerability"]))),
        )
        for f in model["facilities"]
    ]
    hubs = [
        HubIn(id=h["id"], code=h["code"], fixed_cost=h["fixed_cost"], throughput_m3=h["throughput_m3"],
              forced_open=h["forced_open"], annualised_capex=h["annualised_capex"], currently_open=h["currently_open"])
        for h in model["hubs"]
    ]
    lanes = [
        LaneIn(edge_id=l["edge_id"], code=l["code"], hub_id=l["hub_id"], facility_id=l["facility_id"], unit_cost=l["unit_cost"], capacity_m3=l["capacity_m3"], mode=l["mode"])
        for l in model["lanes"] if l["allowed"]
    ]
    w = model["weights"]
    c = model["constraints"]
    return solve(facilities, hubs, lanes, SolveOptions(
        weight_cost=float(w.get("cost", 1.0)), weight_service=float(w.get("service", 1.0)), weight_equity=float(w.get("equity", 0.0)),
        min_fill_rate=c.get("min_fill_rate"), equity_floor=c.get("equity_floor"), max_budget=c.get("max_budget"),
        respect_capacity=bool(c.get("respect_capacity", True)),
    ))


def test_the_exported_model_reproduces_the_runner(session):
    for name_start in ("Baseline", "Cost optimisation — unconstrained", "Open Alotau"):
        scenario = next(s for s in session.scalars(select(Scenario)) if s.name.startswith(name_start))
        result = run_scenario(session, scenario)
        model = export_model.assemble(session, scenario)
        solution = _solve_export(model)
        assert solution.feasible
        assert abs(solution.total_cost - result.kpi_set["total_cost"]) / result.kpi_set["total_cost"] < 0.001, name_start
        assert abs(sum(solution.served.values()) - result.kpi_set["delivered_m3"]) < 0.5, name_start
        assert set(solution.hubs_open) == set(h["id"] for h in model["hubs"] if h["code"] in result.solver_log["hubs_open_codes"]), name_start
    assert model["penalty_rule"].startswith("unmet_penalty")
    assert model["national_store"]["code"] and len(model["strata_labels"]) == 5
    session.rollback()


def test_the_endpoint_serves_it(session):
    from fastapi.testclient import TestClient
    from app.db import get_session
    from app.main import app

    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as client:
            body = client.get("/api/scenarios/1/model").json()
            assert body["scenario"]["is_baseline"] is True
            assert len(body["facilities"]) == 134 and len(body["hubs"]) >= 5 and len(body["lanes"]) > 100
            assert all("unit_cost" in lane and "capacity_m3" in lane for lane in body["lanes"])
    finally:
        app.dependency_overrides.clear()
