"""Data onboarding: the country pack, runs, files, mappings, staging, facility
reconciliation, checks, estimates, the review queue, the report, sign-off, export,
loading and refresh.

Every write carries the signing name from ``X-Author``; decisions refuse an anonymous
one, because a decision nobody made is not a decision.
"""

from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_session
from ..models import Country, Crosswalk, FacilityMatch, OnboardingRun, ReviewItem, SourceFile, StagedRecord
from ..onboarding import agent as agents, export as exporting, pack as packs, review as reviewing, service, signoff
from ..onboarding import estimate as estimating
from ..onboarding.service import OnboardingError
from .deps import author_claim

router = APIRouter(tags=["onboarding"])


def _country_or_404(session: Session, country_id: int) -> Country:
    country = session.get(Country, country_id)
    if not country:
        raise HTTPException(404, f"No country with id {country_id}.")
    return country


def _run_or_404(session: Session, run_id: int) -> OnboardingRun:
    run = session.get(OnboardingRun, run_id)
    if not run:
        raise HTTPException(404, f"No onboarding run with id {run_id}.")
    return run


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (OnboardingError, ValueError) as error:
        raise HTTPException(409, str(error))


# --- the country pack --------------------------------------------------------------------


class PackIn(BaseModel):
    pack: dict
    note: str = ""


class ApproveIn(BaseModel):
    comment: str = ""


@router.get("/countries/{country_id}/onboarding/pack")
def get_pack(country_id: int, session: Session = Depends(get_session)):
    country = _country_or_404(session, country_id)
    pack = packs.ensure(session, country)
    session.commit()
    out = packs.as_dict(pack)
    out["agent"] = agents.allowed(packs.validate(pack.pack))
    out["methods"] = estimating.METHODS
    return out


@router.get("/countries/{country_id}/onboarding/packs")
def list_packs(country_id: int, session: Session = Depends(get_session)):
    _country_or_404(session, country_id)
    return [packs.as_dict(p) for p in session.scalars(select(packs.CountryPack).where(packs.CountryPack.country_id == country_id).order_by(packs.CountryPack.version.desc()))]


@router.post("/countries/{country_id}/onboarding/packs", status_code=201)
def create_pack(country_id: int, payload: PackIn, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    country = _country_or_404(session, country_id)
    pack = _guard(packs.new_version, session, country, payload.pack, author=author, note=payload.note)
    session.commit()
    return packs.as_dict(pack)


@router.post("/onboarding/packs/{pack_id}/approve")
def approve_pack(pack_id: int, payload: ApproveIn, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    pack = session.get(packs.CountryPack, pack_id)
    if not pack:
        raise HTTPException(404, "No such pack.")
    if author == "anonymous":
        raise HTTPException(409, "Approving the pack needs a name.")
    _guard(packs.approve, session, pack, approver=author)
    if payload.comment:
        pack.note = (pack.note + "\n" if pack.note else "") + f"Approved: {payload.comment}"
    session.commit()
    return packs.as_dict(pack)


# --- runs -----------------------------------------------------------------------------------


class RunIn(BaseModel):
    name: str = ""
    kind: str = "initial"


def _run_out(session: Session, run: OnboardingRun) -> dict:
    files = list(session.scalars(select(SourceFile).where(SourceFile.run_id == run.id).order_by(SourceFile.id)))
    records = list(session.execute(select(StagedRecord.domain, StagedRecord.status).where(StagedRecord.run_id == run.id)))
    pending = session.scalar(select(ReviewItem.id).where(ReviewItem.run_id == run.id, ReviewItem.decision == "pending").limit(1)) is not None
    by_domain: dict = {}
    for domain, status in records:
        by_domain.setdefault(domain, {"records": 0, "conflicts": 0, "blocked": 0})
        by_domain[domain]["records"] += 1
        if status == "conflict":
            by_domain[domain]["conflicts"] += 1
        if status == "blocked":
            by_domain[domain]["blocked"] += 1
    matches = list(session.execute(select(FacilityMatch.decision).where(FacilityMatch.run_id == run.id)))
    return {
        "id": run.id, "country_id": run.country_id, "pack_id": run.pack_id, "name": run.name, "kind": run.kind, "status": run.status,
        "preparer": run.preparer, "summary": run.summary or {}, "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "files": [_file_out(f) for f in files], "staged": by_domain, "has_pending": pending,
        "matches": {d: sum(1 for (x,) in matches if x == d) for d in ("auto_accepted", "accepted", "new", "pending", "rejected")},
    }


def _file_out(source: SourceFile) -> dict:
    return {
        "id": source.id, "filename": source.filename, "sha256": source.sha256, "size_bytes": source.size_bytes, "uploader": source.uploader,
        "uploaded_at": source.uploaded_at.isoformat() if source.uploaded_at else None, "source_system": source.source_system, "domain": source.domain,
        "vintage_from": source.vintage_from, "vintage_to": source.vintage_to, "status": source.status, "mapping": source.mapping or {},
        "profile": {k: v for k, v in (source.profile or {}).items()},
    }


@router.get("/countries/{country_id}/onboarding/runs")
def list_runs(country_id: int, session: Session = Depends(get_session)):
    _country_or_404(session, country_id)
    return [_run_out(session, r) for r in session.scalars(select(OnboardingRun).where(OnboardingRun.country_id == country_id).order_by(OnboardingRun.id.desc()))]


@router.post("/countries/{country_id}/onboarding/runs", status_code=201)
def create_run(country_id: int, payload: RunIn, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    country = _country_or_404(session, country_id)
    run = _guard(service.create_run, session, country, name=payload.name, preparer=author, kind=payload.kind)
    session.commit()
    return _run_out(session, run)


@router.get("/onboarding/runs/{run_id}")
def get_run(run_id: int, session: Session = Depends(get_session)):
    return _run_out(session, _run_or_404(session, run_id))


# --- files ---------------------------------------------------------------------------------


@router.post("/onboarding/runs/{run_id}/files", status_code=201)
async def add_files(run_id: int, files: List[UploadFile] = File(...), source_system: str = Form(""), domain: str = Form(""), vintage_from: str = Form(""), vintage_to: str = Form(""), session: Session = Depends(get_session), author: str = Depends(author_claim)):
    """FR1: several files in one go, each recorded with its checksum, uploader and vintage."""
    run = _run_or_404(session, run_id)
    out = []
    for upload in files:
        data = await upload.read()
        if len(data) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"{upload.filename} is larger than {settings.max_upload_mb} MB.")
        try:
            source = service.add_file(session, run, filename=upload.filename or "upload", data=data, uploader=author, source_system=source_system, domain=domain, vintage_from=vintage_from, vintage_to=vintage_to)
        except (OnboardingError, ValueError) as error:
            raise HTTPException(409, f"{upload.filename}: {error}")
        out.append(_file_out(source))
    session.commit()
    return out


class MappingIn(BaseModel):
    mapping: dict
    sheet: Optional[str] = None
    domain: Optional[str] = None
    aux_columns: List[str] = Field(default_factory=list)
    units: dict = Field(default_factory=dict)
    save_as: str = ""


@router.put("/onboarding/files/{file_id}/mapping")
def set_mapping(file_id: int, payload: MappingIn, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    source = session.get(SourceFile, file_id)
    if not source:
        raise HTTPException(404, "No such file.")
    run = _run_or_404(session, source.run_id)
    _guard(service.set_mapping, session, run, source, mapping=payload.mapping, sheet=payload.sheet, aux_columns=payload.aux_columns, domain=payload.domain, units=payload.units, save_as=payload.save_as, by=author)
    session.commit()
    return _file_out(source)


@router.post("/onboarding/files/{file_id}/stage")
def stage(file_id: int, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    source = session.get(SourceFile, file_id)
    if not source:
        raise HTTPException(404, "No such file.")
    run = _run_or_404(session, source.run_id)
    counts = _guard(service.stage_file, session, run, source, by=author)
    session.commit()
    return {"file": _file_out(source), "counts": counts}


# --- staging and facilities --------------------------------------------------------------


def _record_out(record: StagedRecord) -> dict:
    return {"id": record.id, "domain": record.domain, "key": record.key, "label": record.label, "fields": record.fields, "aux": record.aux, "issues": record.issues, "status": record.status}


@router.get("/onboarding/runs/{run_id}/records")
def list_records(run_id: int, domain: Optional[str] = None, status: Optional[str] = None, q: Optional[str] = None, limit: int = Query(200, le=2000), offset: int = 0, session: Session = Depends(get_session)):
    run = _run_or_404(session, run_id)
    stmt = select(StagedRecord).where(StagedRecord.run_id == run.id)
    if domain:
        stmt = stmt.where(StagedRecord.domain == domain)
    if status:
        stmt = stmt.where(StagedRecord.status == status)
    if q:
        stmt = stmt.where(StagedRecord.key.ilike(f"%{q}%") | StagedRecord.label.ilike(f"%{q}%"))
    rows = list(session.scalars(stmt.order_by(StagedRecord.domain, StagedRecord.key).offset(offset).limit(limit)))
    return {"items": [_record_out(r) for r in rows], "offset": offset, "limit": limit}


@router.get("/onboarding/runs/{run_id}/matches")
def list_matches(run_id: int, decision: Optional[str] = None, session: Session = Depends(get_session)):
    run = _run_or_404(session, run_id)
    stmt = select(FacilityMatch).where(FacilityMatch.run_id == run.id)
    if decision:
        stmt = stmt.where(FacilityMatch.decision == decision)
    return [_match_out(m) for m in session.scalars(stmt.order_by(FacilityMatch.confidence.desc(), FacilityMatch.id))]


def _match_out(m: FacilityMatch) -> dict:
    return {"id": m.id, "source_file_id": m.source_file_id, "source_key": m.source_key, "source_name": m.source_name, "record": m.source_record, "candidates": m.candidates, "canonical_code": m.canonical_code, "confidence": round(m.confidence, 3), "reason": m.reason, "decision": m.decision, "decided_by": m.decided_by, "decided_at": m.decided_at.isoformat() if m.decided_at else None}


class MatchDecision(BaseModel):
    choice: str
    comment: str = ""


@router.post("/onboarding/matches/{match_id}/decide")
def decide_match(match_id: int, payload: MatchDecision, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    match = session.get(FacilityMatch, match_id)
    if not match:
        raise HTTPException(404, "No such match.")
    if author == "anonymous":
        raise HTTPException(409, "Deciding a facility match needs the arbiter's name.")
    run = _run_or_404(session, match.run_id)
    _guard(service.decide_match, session, run, match, choice=payload.choice, by=author, comment=payload.comment)
    session.commit()
    return _match_out(match)


@router.get("/countries/{country_id}/crosswalk")
def crosswalk(country_id: int, session: Session = Depends(get_session)):
    _country_or_404(session, country_id)
    rows = list(session.scalars(select(Crosswalk).where(Crosswalk.country_id == country_id).order_by(Crosswalk.canonical_code)))
    return [{"id": r.id, "canonical_code": r.canonical_code, "name": r.name, "type": r.type, "admin1": r.admin1, "admin2": r.admin2, "lat": r.lat, "lon": r.lon, "ids": r.ids, "status": r.status, "history": r.history} for r in rows]


@router.get("/countries/{country_id}/crosswalk.csv")
def crosswalk_csv(country_id: int, session: Session = Depends(get_session)):
    country = _country_or_404(session, country_id)
    rows = list(session.scalars(select(Crosswalk).where(Crosswalk.country_id == country_id).order_by(Crosswalk.canonical_code)))
    return Response(content=exporting.crosswalk_csv(rows), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{country.code}-facility-crosswalk.csv"'})


# --- checks, estimates, review ------------------------------------------------------------


@router.post("/onboarding/runs/{run_id}/checks")
def run_checks(run_id: int, session: Session = Depends(get_session)):
    run = _run_or_404(session, run_id)
    out = _guard(service.run_rules, session, run)
    session.commit()
    return out


@router.post("/onboarding/runs/{run_id}/estimates")
def run_estimates(run_id: int, session: Session = Depends(get_session)):
    run = _run_or_404(session, run_id)
    out = _guard(service.run_estimates, session, run)
    session.commit()
    return out


@router.get("/onboarding/runs/{run_id}/queue")
def get_queue(run_id: int, kind: Optional[str] = None, pending: bool = True, limit: int = Query(200, le=2000), session: Session = Depends(get_session)):
    run = _run_or_404(session, run_id)
    return reviewing.queue(session, run, kind=kind, pending_only=pending, limit=limit)


class DecisionIn(BaseModel):
    decision: str
    choice: str = ""
    value: Optional[object] = None
    comment: str = ""


@router.post("/onboarding/items/{item_id}/decide")
def decide_item(item_id: int, payload: DecisionIn, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    item = session.get(ReviewItem, item_id)
    if not item:
        raise HTTPException(404, "No such review item.")
    run = _run_or_404(session, item.run_id)
    _guard(reviewing.decide, session, run, item, decision=payload.decision, by=author, choice=payload.choice, value=payload.value, comment=payload.comment)
    session.commit()
    return reviewing.as_dict(item)


class BulkIn(BaseModel):
    item_ids: List[int]
    decision: str = "accept"
    comment: str = ""


@router.post("/onboarding/runs/{run_id}/items/bulk")
def bulk(run_id: int, payload: BulkIn, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    run = _run_or_404(session, run_id)
    if author == "anonymous":
        raise HTTPException(409, "Sign your name before deciding.")
    out = _guard(reviewing.bulk_decide, session, run, payload.item_ids, decision=payload.decision, by=author, comment=payload.comment)
    session.commit()
    return out


class AskIn(BaseModel):
    contact: str
    question: str = ""


@router.post("/onboarding/items/{item_id}/ask")
def ask_local(item_id: int, payload: AskIn, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    item = session.get(ReviewItem, item_id)
    if not item:
        raise HTTPException(404, "No such review item.")
    run = _run_or_404(session, item.run_id)
    pack = service.run_pack(session, run)
    question = payload.question or agents.proposer(pack).draft_question(item.payload or {"message": item.title, "key": item.subject.split(":")[-1]}, language=(pack.languages or ["en"])[0])
    asked = _guard(reviewing.ask_local, session, run, item, contact=payload.contact, question=question, by=author)
    session.commit()
    return reviewing.as_dict(asked)


# --- report, sign-off, export, load ------------------------------------------------------


@router.get("/onboarding/runs/{run_id}/report")
def get_report(run_id: int, session: Session = Depends(get_session)):
    run = _run_or_404(session, run_id)
    if run.status not in ("open", "in_review"):
        approval = session.scalar(select(signoff.Approval).where(signoff.Approval.run_id == run.id).order_by(signoff.Approval.id.desc()))
        if approval:
            return {**approval.report, "run": {**approval.report.get("run", {}), "status": run.status}, "approval": {"approver": approval.approver, "decision": approval.decision, "comment": approval.comment, "at": approval.decided_at.isoformat() if approval.decided_at else None}}
    return signoff.report(session, run)


class SignOffIn(BaseModel):
    decision: str = "approved"
    comment: str = ""


@router.post("/onboarding/runs/{run_id}/sign-off")
def sign_off(run_id: int, payload: SignOffIn, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    run = _run_or_404(session, run_id)
    approval = _guard(signoff.sign_off, session, run, approver=author, decision=payload.decision, comment=payload.comment)
    session.commit()
    return {"run": _run_out(session, run), "approval": {"approver": approval.approver, "decision": approval.decision, "comment": approval.comment}}


@router.get("/onboarding/runs/{run_id}/export.xlsx")
def export_workbook(run_id: int, session: Session = Depends(get_session)):
    run = _run_or_404(session, run_id)
    country = _country_or_404(session, run.country_id)
    records = list(session.scalars(select(StagedRecord).where(StagedRecord.run_id == run.id)))
    sources = list(session.scalars(select(SourceFile).where(SourceFile.run_id == run.id)))
    return Response(content=exporting.build_workbook(country, run, records, sources), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{country.code}-onboarding-{run.id}.xlsx"'})


@router.post("/onboarding/runs/{run_id}/load")
def load_run(run_id: int, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    run = _run_or_404(session, run_id)
    if author == "anonymous":
        raise HTTPException(409, "Loading the model needs a name.")
    out = _guard(signoff.load, session, run, by=author)
    session.commit()
    return {"run": _run_out(session, run), **out}
