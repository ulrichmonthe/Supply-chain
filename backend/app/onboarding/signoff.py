"""Sign-off and loading: the two human actions the pipeline can never take itself.

Sign-off needs a complete report with nothing blocking: no illustrative or missing
values, no unresolved conflicts, no pending decisions, and an approver who is not the
preparer. Loading is a separate step after that, through the same validate-and-apply
pipeline a workbook takes, so the model is changed by exactly one path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..io import excel_in
from ..io.apply import apply_payload
from ..io.validation import validate_dataset
from ..models import Approval, Country, ImportBatch, Node, OnboardingRun, Product, ReviewItem, SourceFile, StagedRecord
from . import APPROVABLE, export as exporting, review as reviewing
from . import pack as packs
from .service import OnboardingError, run_pack
from .staging import coverage

WORLD_BBOX = {"min_lat": -90, "max_lat": 90, "min_lon": -180, "max_lon": 180}


def report(session: Session, run: OnboardingRun) -> dict:
    """FR27: coverage by domain and class, every estimate, every conflict, every anomaly
    and how it was resolved, data vintage by source, and what still blocks."""
    records = list(session.scalars(select(StagedRecord).where(StagedRecord.run_id == run.id)))
    items = list(session.scalars(select(ReviewItem).where(ReviewItem.run_id == run.id)))
    sources = list(session.scalars(select(SourceFile).where(SourceFile.run_id == run.id)))
    cov = coverage(records)
    estimates = [{"domain": r.domain, "key": r.key, "label": r.label, "field": n, "value": i.get("value"), "method": i.get("method"), "inputs": i.get("inputs"), "formula": i.get("transformation"), "by": i.get("by")} for r in records for n, i in (r.fields or {}).items() if i.get("class") == "estimated"]
    conflicts = [{"domain": r.domain, "key": r.key, "field": n, "open": bool(i.get("alternatives")), "chosen": i.get("value") if not i.get("alternatives") else None, "chosen_by": i.get("chosen_by"), "options": [i.get("value")] + [a.get("value") for a in (i.get("alternatives") or [])] + [o.get("value") for o in (i.get("chosen_over") or [])]} for r in records for n, i in (r.fields or {}).items() if i.get("alternatives") or i.get("chosen_over")]
    anomalies = [{**reviewing.as_dict(i), "resolution": i.decision} for i in items if i.kind == "anomaly"]
    missing_values = [{"domain": r.domain, "key": r.key, "label": r.label, "field": n, "reason": i.get("reason")} for r in records for n, i in (r.fields or {}).items() if i.get("class") == "missing"]
    illustrative = [{"domain": r.domain, "key": r.key, "field": n} for r in records for n, i in (r.fields or {}).items() if i.get("class") == "illustrative"]
    pending = [reviewing.as_dict(i) for i in items if i.decision == "pending"]
    unresolved = [{"key": r.key, "issues": r.issues} for r in records if r.key.startswith("?")]
    blockers = blocking_reasons(session, run, cov, conflicts, pending, unresolved)
    return {
        "run": {"id": run.id, "name": run.name, "kind": run.kind, "status": run.status, "preparer": run.preparer, "created_at": run.created_at.isoformat() if run.created_at else None},
        "coverage": cov,
        "records": {"total": len(records), "by_domain": _count(r.domain for r in records), "unresolved_references": len(unresolved)},
        "sources": [{"id": s.id, "filename": s.filename, "system": s.source_system, "domain": s.domain, "vintage_from": s.vintage_from, "vintage_to": s.vintage_to, "uploader": s.uploader, "uploaded_at": s.uploaded_at.isoformat() if s.uploaded_at else None, "sha256": s.sha256, "status": s.status} for s in sources],
        "estimates": estimates,
        "conflicts": conflicts,
        "anomalies": anomalies,
        "missing": missing_values,
        "illustrative": illustrative,
        "pending": pending,
        "unresolved": unresolved,
        "reviewers": reviewing.reviewer_hours(session, run),
        "checks": (run.summary or {}).get("checks"),
        "blockers": blockers,
        "can_sign_off": not blockers,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def blocking_reasons(session: Session, run: OnboardingRun, cov: dict, conflicts: List[dict], pending: List[dict], unresolved: List[dict]) -> List[str]:
    reasons = []
    pack = session.get(packs.CountryPack, run.pack_id) if run.pack_id else None
    if pack is None or pack.status != "approved":
        reasons.append("The country pack this run uses is not approved.")
    if cov["totals"].get("illustrative"):
        reasons.append(f"{cov['totals']['illustrative']} values are still illustrative placeholders.")
    if cov["totals"].get("missing"):
        reasons.append(f"{cov['totals']['missing']} values are missing with no defensible method.")
    open_conflicts = sum(1 for c in conflicts if c["open"])
    if open_conflicts:
        reasons.append(f"{open_conflicts} conflicts between sources are unresolved.")
    if pending:
        reasons.append(f"{len(pending)} review items are still pending.")
    if unresolved:
        reasons.append(f"{len(unresolved)} rows refer to a facility or product nothing matches.")
    if not cov["approvable"]:
        reasons.append("Nothing has been staged.")
    return reasons


def sign_off(session: Session, run: OnboardingRun, *, approver: str, decision: str, comment: str = "") -> Approval:
    """FR28: a named approver, different from the preparer, seeing the report."""
    if run.status not in ("open", "in_review"):
        raise OnboardingError(f"This run is already {run.status.replace('_', ' ')}.")
    if not approver or approver == "anonymous":
        raise OnboardingError("Sign-off needs a name.")
    if approver.strip().lower() == (run.preparer or "").strip().lower():
        raise OnboardingError("The preparer cannot approve their own dataset. Ask the named approver.")
    if decision not in ("approved", "rejected"):
        raise OnboardingError("A sign-off is approved or rejected.")
    snapshot = report(session, run)
    if decision == "approved" and snapshot["blockers"]:
        raise OnboardingError("Sign-off is blocked: " + " ".join(snapshot["blockers"]))
    approval = Approval(run_id=run.id, approver=approver, decision=decision, comment=comment, report=snapshot)
    session.add(approval)
    run.status = "signed_off" if decision == "approved" else "rejected"
    run.summary = {**(run.summary or {}), "sign_off": {"approver": approver, "decision": decision, "at": approval.decided_at.isoformat() if approval.decided_at else datetime.now(timezone.utc).isoformat(), "comment": comment}}
    session.flush()
    return approval


def load(session: Session, run: OnboardingRun, *, by: str) -> dict:
    """Guardrail 1: loading is a person's action after sign-off, through the import pipeline."""
    if run.status != "signed_off":
        raise OnboardingError("Only a signed-off run can be loaded into the model.")
    country = session.get(Country, run.country_id)
    records = list(session.scalars(select(StagedRecord).where(StagedRecord.run_id == run.id)))
    sources = list(session.scalars(select(SourceFile).where(SourceFile.run_id == run.id)))
    workbook = exporting.build_workbook(country, run, records, sources)
    parsed = excel_in.parse_workbook(workbook)
    # Merged into what is loaded, so the validator runs in partial mode: a run that brings
    # facilities and demand but no lanes is a dataset, not a broken workbook.
    validation = validate_dataset(
        country_code=country.code, bbox=(country.config or {}).get("bbox", WORLD_BBOX), boundary=country.boundary or {},
        nodes=parsed["nodes"], edges=parsed["edges"], products=parsed["products"], demand=parsed["demand"], partial=True,
        existing_node_codes={n.code for n in session.scalars(select(Node).where(Node.country_id == country.id))},
        existing_product_skus={p.sku for p in session.scalars(select(Product).where(Product.country_id == country.id))},
    )
    if validation.blocking:
        raise OnboardingError("The signed-off dataset does not pass the model's validator: " + validation.headline())
    approval = session.scalar(select(Approval).where(Approval.run_id == run.id, Approval.decision == "approved").order_by(Approval.id.desc()))
    reference = f"onboarding run {run.id} ({run.name}), signed off by {approval.approver if approval else 'unknown'}"
    from ..api.deps import new_batch_id

    batch_id = new_batch_id()
    counts = apply_payload(session, country, parsed, mode="merge", source="excel", reference=reference, actor="onboarding", author_claim=by, batch_id=batch_id, conflict_policy="take_file")
    batch = ImportBatch(country_id=country.id, filename=f"onboarding-{run.id}.xlsx", source="excel", mode="merge", status="committed", committed=True, report={"onboarding_run": run.id, "validation": validation.as_dict(), "counts": counts, "batch_id": batch_id})
    session.add(batch)
    run.status = "loaded"
    run.summary = {**(run.summary or {}), "loaded": {"by": by, "at": datetime.now(timezone.utc).isoformat(), "counts": counts, "batch_id": batch_id}}
    session.flush()
    return {"counts": counts, "batch_id": batch_id, "validation": validation.headline()}


def _count(values) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out
