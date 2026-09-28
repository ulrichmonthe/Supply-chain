"""Studies: a question, its ordered scenarios, the compare view and the diff map."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ledger
from .. import studies as studies_mod
from ..db import get_session
from ..models import Country, Scenario, Study
from ..schemas import ResultSummary, StudyIn, StudyPatch, StudyScenarioIn
from .deps import author_claim, new_batch_id

router = APIRouter(tags=["studies"])


def _country_or_404(session: Session, country_id: int) -> Country:
    country = session.get(Country, country_id)
    if not country:
        raise HTTPException(404, f"No country with id {country_id}.")
    return country


def _study_or_404(session: Session, study_id: int) -> Study:
    study = session.get(Study, study_id)
    if not study:
        raise HTTPException(404, f"No study with id {study_id}.")
    return study


@router.get("/study-presets")
def list_presets():
    return studies_mod.presets_out()


@router.get("/countries/{country_id}/studies")
def list_studies(country_id: int, session: Session = Depends(get_session)):
    _country_or_404(session, country_id)
    rows = session.scalars(select(Study).where(Study.country_id == country_id).order_by(Study.id.desc()))
    return [studies_mod.study_out(session, study) for study in rows]


@router.post("/countries/{country_id}/studies", status_code=201)
def create_study(
    country_id: int,
    payload: StudyIn,
    session: Session = Depends(get_session),
    author: str = Depends(author_claim),
):
    country = _country_or_404(session, country_id)
    question = " ".join(payload.question.split()).strip()
    if not question:
        raise HTTPException(400, "A study starts with a question.")
    try:
        study = studies_mod.create(
            session,
            country,
            question=question,
            note=payload.note or "",
            preset=payload.preset,
            scenario_ids=payload.scenario_ids,
            author_claim=author,
            batch_id=new_batch_id(),
        )
    except ValueError as error:
        raise HTTPException(400, str(error))
    session.commit()
    return studies_mod.study_out(session, study)


@router.get("/studies/{study_id}")
def get_study(study_id: int, session: Session = Depends(get_session)):
    return studies_mod.study_out(session, _study_or_404(session, study_id))


@router.patch("/studies/{study_id}")
def update_study(
    study_id: int,
    payload: StudyPatch,
    session: Session = Depends(get_session),
    author: str = Depends(author_claim),
):
    study = _study_or_404(session, study_id)
    if payload.question is not None:
        question = " ".join(payload.question.split()).strip()
        if not question:
            raise HTTPException(400, "A study needs its question.")
        study.question = question
    if payload.note is not None:
        study.note = payload.note
    if payload.scenario_ids is not None:
        known = {
            s.id: s
            for s in session.scalars(select(Scenario).where(Scenario.country_id == study.country_id))
        }
        ordered = [sid for sid in payload.scenario_ids if sid in known]
        baseline = next((s.id for s in known.values() if s.is_baseline), None)
        if baseline is not None:
            ordered = [baseline] + [sid for sid in ordered if sid != baseline]
        study.scenario_ids = list(dict.fromkeys(ordered))
    if "recommended_scenario_id" in payload.model_fields_set:
        if payload.recommended_scenario_id is not None and payload.recommended_scenario_id not in (study.scenario_ids or []):
            raise HTTPException(400, "The recommended scenario must be part of the study.")
        before = study.recommended_scenario_id
        study.recommended_scenario_id = payload.recommended_scenario_id
        if before != payload.recommended_scenario_id:
            named = session.get(Scenario, payload.recommended_scenario_id) if payload.recommended_scenario_id else None
            ledger.record(
                session,
                country_id=study.country_id,
                entity_type="study",
                entity_ref=study.question,
                field="recommended",
                old_value=(session.get(Scenario, before).name if before and session.get(Scenario, before) else None),
                new_value=named.name if named else None,
                provenance="assumption",
                author_claim=author,
                batch_id=new_batch_id(),
            )
    session.commit()
    return studies_mod.study_out(session, study)


@router.delete("/studies/{study_id}", status_code=204)
def delete_study(study_id: int, session: Session = Depends(get_session)):
    """Removes the study. Its scenarios stay; they were always ordinary scenarios."""
    session.delete(_study_or_404(session, study_id))
    session.commit()


@router.post("/studies/{study_id}/scenarios")
def add_scenario(study_id: int, payload: StudyScenarioIn, session: Session = Depends(get_session)):
    study = _study_or_404(session, study_id)
    scenario = session.get(Scenario, payload.scenario_id)
    if not scenario or scenario.country_id != study.country_id:
        raise HTTPException(404, "No such scenario in this country.")
    if scenario.id not in (study.scenario_ids or []):
        study.scenario_ids = list(study.scenario_ids or []) + [scenario.id]
    session.commit()
    return studies_mod.study_out(session, study)


@router.delete("/studies/{study_id}/scenarios/{scenario_id}")
def remove_scenario(study_id: int, scenario_id: int, session: Session = Depends(get_session)):
    study = _study_or_404(session, study_id)
    scenario = session.get(Scenario, scenario_id)
    if scenario and scenario.is_baseline:
        raise HTTPException(400, "The baseline stays in every study; every comparison is made against it.")
    study.scenario_ids = [sid for sid in (study.scenario_ids or []) if sid != scenario_id]
    if study.recommended_scenario_id == scenario_id:
        study.recommended_scenario_id = None
    session.commit()
    return studies_mod.study_out(session, study)


@router.post("/studies/{study_id}/run")
def run_study(study_id: int, session: Session = Depends(get_session)):
    study = _study_or_404(session, study_id)
    results = studies_mod.run_all(session, study)
    return {"ran": len(results), "results": [ResultSummary.model_validate(r).model_dump() for r in results]}


@router.get("/studies/{study_id}/compare")
def compare_study(study_id: int, session: Session = Depends(get_session)):
    return studies_mod.compare(session, _study_or_404(session, study_id))


@router.get("/scenarios/{scenario_a}/diff-map/{scenario_b}")
def diff_map(scenario_a: int, scenario_b: int, session: Session = Depends(get_session)):
    """The two scenarios' latest results as one network: lanes only A uses, only B, or both."""
    result_a = studies_mod.latest_ok(session, scenario_a)
    result_b = studies_mod.latest_ok(session, scenario_b)
    if not result_a or not result_b:
        raise HTTPException(409, "Both scenarios need a result before their networks can be compared.")
    return studies_mod.diff_map(result_a, result_b)
