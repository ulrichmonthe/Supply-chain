"""Result and roadmap exports."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..engine import kpis as kpi_mod
from ..engine.roadmap import build_roadmap
from ..io import excel_out
from ..models import Node, Result, Scenario

router = APIRouter(tags=["exports"])


def _latest_ok(session: Session, scenario_id: int) -> Result | None:
    return session.scalars(
        select(Result)
        .where(Result.scenario_id == scenario_id, Result.status == "ok")
        .order_by(Result.id.desc())
        .limit(1)
    ).first()


@router.get("/scenarios/{scenario_id}/export/results.xlsx")
def export_results(scenario_id: int, session: Session = Depends(get_session)):
    scenario = session.get(Scenario, scenario_id)
    if not scenario:
        raise HTTPException(404, f"No scenario with id {scenario_id}.")
    result = _latest_ok(session, scenario_id)
    if not result:
        raise HTTPException(409, "Run this scenario before exporting it.")

    country = scenario.country
    baseline = session.scalar(
        select(Scenario).where(Scenario.country_id == country.id, Scenario.is_baseline.is_(True))
    )
    baseline_result = _latest_ok(session, baseline.id) if baseline else None

    comparison = (
        kpi_mod.compare(baseline_result.kpi_set, result.kpi_set)
        if baseline_result and baseline and baseline.id != scenario.id
        else None
    )

    roadmap = None
    if baseline and baseline_result and baseline.id != scenario.id:
        nodes_by_code = {
            n.code: n for n in session.scalars(select(Node).where(Node.country_id == country.id))
        }
        roadmap = build_roadmap(
            baseline_scenario=baseline,
            baseline_result=baseline_result,
            scenario=scenario,
            result=result,
            nodes_by_code=nodes_by_code,
            currency=country.currency,
        )

    payload = excel_out.export_results(country, scenario, result, roadmap, comparison)
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "-" for c in scenario.name).strip()[:60]
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{country.code} - {safe_name}.xlsx"'},
    )
