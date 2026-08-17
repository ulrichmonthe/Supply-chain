"""Scenario CRUD, cloning, running and comparison."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_session
from ..engine import kpis as kpi_mod
from ..engine.roadmap import build_roadmap
from ..engine.runner import run_scenario
from ..models import Country, Node, Result, Scenario
from ..schemas import ResultSummary, ScenarioIn, ScenarioOut, ScenarioPatch

router = APIRouter(tags=["scenarios"])


def _scenario_or_404(session: Session, scenario_id: int) -> Scenario:
    scenario = session.get(Scenario, scenario_id)
    if not scenario:
        raise HTTPException(404, f"No scenario with id {scenario_id}.")
    return scenario


def _to_out(session: Session, scenario: Scenario) -> ScenarioOut:
    latest = session.scalars(
        select(Result).where(Result.scenario_id == scenario.id).order_by(Result.id.desc()).limit(1)
    ).first()
    return ScenarioOut(
        **{key: getattr(scenario, key) for key in ScenarioOut.model_fields if hasattr(scenario, key)},
        latest_result_id=latest.id if latest else None,
        latest_status=latest.status if latest else None,
    )


@router.get("/countries/{country_id}/scenarios", response_model=list[ScenarioOut])
def list_scenarios(country_id: int, session: Session = Depends(get_session)):
    scenarios = list(
        session.scalars(
            select(Scenario)
            .where(Scenario.country_id == country_id)
            .order_by(Scenario.is_baseline.desc(), Scenario.id)
        )
    )
    return [_to_out(session, s) for s in scenarios]


@router.post("/countries/{country_id}/scenarios", response_model=ScenarioOut, status_code=201)
def create_scenario(country_id: int, payload: ScenarioIn, session: Session = Depends(get_session)):
    if not session.get(Country, country_id):
        raise HTTPException(404, f"No country with id {country_id}.")
    if session.scalar(
        select(Scenario).where(Scenario.country_id == country_id, Scenario.name == payload.name)
    ):
        raise HTTPException(409, f"A scenario named '{payload.name}' already exists.")

    scenario = Scenario(country_id=country_id, **payload.model_dump())
    session.add(scenario)
    session.commit()
    session.refresh(scenario)
    return _to_out(session, scenario)


@router.get("/scenarios/{scenario_id}", response_model=ScenarioOut)
def get_scenario(scenario_id: int, session: Session = Depends(get_session)):
    return _to_out(session, _scenario_or_404(session, scenario_id))


@router.patch("/scenarios/{scenario_id}", response_model=ScenarioOut)
def update_scenario(scenario_id: int, payload: ScenarioPatch, session: Session = Depends(get_session)):
    scenario = _scenario_or_404(session, scenario_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(scenario, field, value)
    session.commit()
    session.refresh(scenario)
    return _to_out(session, scenario)


@router.delete("/scenarios/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: int, session: Session = Depends(get_session)):
    scenario = _scenario_or_404(session, scenario_id)
    if scenario.is_baseline:
        raise HTTPException(400, "The baseline cannot be deleted; every comparison is made against it.")
    session.delete(scenario)
    session.commit()


@router.post("/scenarios/{scenario_id}/clone", response_model=ScenarioOut, status_code=201)
def clone_scenario(scenario_id: int, payload: ScenarioIn | None = None, session: Session = Depends(get_session)):
    source = _scenario_or_404(session, scenario_id)
    name = (payload.name if payload and payload.name else f"{source.name} (copy)")
    suffix = 2
    while session.scalar(
        select(Scenario).where(Scenario.country_id == source.country_id, Scenario.name == name)
    ):
        name = f"{source.name} (copy {suffix})"
        suffix += 1

    clone = Scenario(
        country_id=source.country_id,
        name=name,
        description=payload.description if payload and payload.description else source.description,
        parent_scenario_id=source.id,
        levers=dict(source.levers or {}),
        constraints=dict(source.constraints or {}),
        objective_weights=dict(source.objective_weights or {}),
    )
    if payload:
        if payload.levers:
            clone.levers = {**clone.levers, **payload.levers}
        if payload.constraints:
            clone.constraints = {**clone.constraints, **payload.constraints}
        if payload.objective_weights:
            clone.objective_weights = {**clone.objective_weights, **payload.objective_weights}

    session.add(clone)
    session.commit()
    session.refresh(clone)
    return _to_out(session, clone)


@router.post("/scenarios/{scenario_id}/run", response_model=ResultSummary)
def run_one(scenario_id: int, session: Session = Depends(get_session)):
    scenario = _scenario_or_404(session, scenario_id)
    return run_scenario(session, scenario)


def _run_in_own_session(scenario_id: int) -> int:
    """Run one scenario on its own session so a set can run in parallel."""
    session = SessionLocal()
    try:
        scenario = session.get(Scenario, scenario_id)
        if not scenario:
            return -1
        result = run_scenario(session, scenario)
        return result.id
    finally:
        session.close()


@router.post("/countries/{country_id}/run-set")
def run_set(country_id: int, scenario_ids: list[int], session: Session = Depends(get_session)):
    """Run several scenarios at once.

    Running a scenario set in parallel is the demo moment that makes a scorecard
    appear while the room is still watching. Workers are threads here because the
    solve releases the GIL inside HiGHS; the production path swaps this for Celery
    workers without changing the interface.
    """
    if not scenario_ids:
        raise HTTPException(400, "Give at least one scenario id.")
    known = {
        s.id
        for s in session.scalars(
            select(Scenario).where(Scenario.country_id == country_id, Scenario.id.in_(scenario_ids))
        )
    }
    missing = [i for i in scenario_ids if i not in known]
    if missing:
        raise HTTPException(404, f"Scenarios not found in this country: {missing}.")

    with ThreadPoolExecutor(max_workers=min(8, len(scenario_ids))) as pool:
        result_ids = list(pool.map(_run_in_own_session, scenario_ids))

    results = list(session.scalars(select(Result).where(Result.id.in_([r for r in result_ids if r > 0]))))
    return {
        "ran": len(results),
        "results": [ResultSummary.model_validate(r).model_dump() for r in results],
    }


@router.get("/results/{result_id}")
def get_result(result_id: int, session: Session = Depends(get_session)):
    result = session.get(Result, result_id)
    if not result:
        raise HTTPException(404, f"No result with id {result_id}.")
    return {
        "id": result.id,
        "scenario_id": result.scenario_id,
        "scenario_name": result.scenario.name,
        "status": result.status,
        "kpi_set": result.kpi_set,
        "per_node_detail": result.per_node_detail,
        "per_edge_flow": result.per_edge_flow,
        "equity_detail": result.equity_detail,
        "solver_log": result.solver_log,
        "runtime_ms": result.runtime_ms,
        "run_timestamp": result.run_timestamp,
        "error": result.error,
    }


def _latest_result(session: Session, scenario: Scenario) -> Result | None:
    return session.scalars(
        select(Result)
        .where(Result.scenario_id == scenario.id, Result.status == "ok")
        .order_by(Result.id.desc())
        .limit(1)
    ).first()


def _baseline(session: Session, country_id: int) -> Scenario | None:
    return session.scalar(
        select(Scenario).where(Scenario.country_id == country_id, Scenario.is_baseline.is_(True))
    )


@router.get("/countries/{country_id}/scorecard")
def scorecard(country_id: int, session: Session = Depends(get_session)):
    """Every scenario's latest result, compared against the baseline."""
    baseline = _baseline(session, country_id)
    if not baseline:
        raise HTTPException(404, "This country has no baseline scenario.")
    baseline_result = _latest_result(session, baseline)

    scenarios = list(
        session.scalars(
            select(Scenario)
            .where(Scenario.country_id == country_id)
            .order_by(Scenario.is_baseline.desc(), Scenario.id)
        )
    )

    rows = []
    for scenario in scenarios:
        result = _latest_result(session, scenario)
        if not result:
            latest_any = session.scalars(
                select(Result).where(Result.scenario_id == scenario.id).order_by(Result.id.desc()).limit(1)
            ).first()
            rows.append(
                {
                    "scenario_id": scenario.id,
                    "name": scenario.name,
                    "is_baseline": scenario.is_baseline,
                    "status": latest_any.status if latest_any else "not_run",
                    "error": latest_any.error if latest_any else None,
                    "kpi_set": {},
                    "comparison": {},
                }
            )
            continue
        rows.append(
            {
                "scenario_id": scenario.id,
                "result_id": result.id,
                "name": scenario.name,
                "description": scenario.description,
                "is_baseline": scenario.is_baseline,
                "status": result.status,
                "kpi_set": result.kpi_set,
                "equity": result.equity_detail.get("strata", []) if result.equity_detail else [],
                "month_label": (result.solver_log or {}).get("month_label", "Annualised"),
                "runtime_ms": result.runtime_ms,
                "comparison": (
                    kpi_mod.compare(baseline_result.kpi_set, result.kpi_set)
                    if baseline_result and not scenario.is_baseline
                    else {}
                ),
            }
        )

    return {
        "baseline_scenario_id": baseline.id,
        "baseline_result_id": baseline_result.id if baseline_result else None,
        "kpi_meta": kpi_mod.KPI_META,
        "kpi_order": kpi_mod.KPI_ORDER,
        "rows": rows,
    }


@router.get("/scenarios/{scenario_id}/roadmap")
def roadmap(scenario_id: int, session: Session = Depends(get_session)):
    """The costed implementation roadmap between the baseline and this scenario."""
    scenario = _scenario_or_404(session, scenario_id)
    baseline = _baseline(session, scenario.country_id)
    if not baseline:
        raise HTTPException(404, "This country has no baseline scenario.")
    if baseline.id == scenario.id:
        raise HTTPException(400, "A roadmap describes the change from the baseline, so the baseline has none.")

    result = _latest_result(session, scenario)
    baseline_result = _latest_result(session, baseline)
    if not result or not baseline_result:
        raise HTTPException(
            409,
            "Both the baseline and this scenario need a successful run before a roadmap can "
            "be generated. Run the scenario set first.",
        )

    nodes_by_code = {
        n.code: n for n in session.scalars(select(Node).where(Node.country_id == scenario.country_id))
    }
    country = session.get(Country, scenario.country_id)
    return build_roadmap(
        baseline_scenario=baseline,
        baseline_result=baseline_result,
        scenario=scenario,
        result=result,
        nodes_by_code=nodes_by_code,
        currency=country.currency,
    )
