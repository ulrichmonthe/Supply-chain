"""Estimates: fill what is missing with a rule that shows its working, and keep it live."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import estimators
from ..db import get_session
from ..models import Country
from ..schemas import EstimateRequest
from .deps import author_claim, new_batch_id

router = APIRouter(tags=["estimates"])


def _country_or_404(session: Session, country_id: int) -> Country:
    country = session.get(Country, country_id)
    if not country:
        raise HTTPException(404, f"No country with id {country_id}.")
    return country


@router.get("/countries/{country_id}/estimators")
def list_estimators(country_id: int, session: Session = Depends(get_session)):
    """The rules, what each would use for each product, and how many rows are blank or
    already estimated -- enough for the Data tab to say "fill 22 blank rows for KIT
    from population at 37.5 per 1,000" before anybody clicks."""
    return estimators.describe(session, _country_or_404(session, country_id))


@router.post("/countries/{country_id}/estimates/preview")
def preview_estimate(country_id: int, payload: EstimateRequest, session: Session = Depends(get_session)):
    """What the rule would set. Reads only."""
    country = _country_or_404(session, country_id)
    try:
        proposals, skipped = estimators.propose(session, country, payload.rule, node_id=payload.node_id, sku=payload.sku)
    except (ValueError, KeyError) as error:
        raise HTTPException(400, str(error))
    return {
        "rule": payload.rule,
        "count": len(proposals),
        "proposals": [p.as_dict() for p in proposals[:200]],
        "skipped": skipped[:50],
        "skipped_count": len(skipped),
    }


@router.post("/countries/{country_id}/estimates/apply")
def apply_estimate(
    country_id: int,
    payload: EstimateRequest,
    session: Session = Depends(get_session),
    author: str = Depends(author_claim),
):
    """Apply the rule. Each value becomes a ledger row carrying its formula, and stays
    live until somebody types over it."""
    country = _country_or_404(session, country_id)
    try:
        proposals, skipped = estimators.propose(session, country, payload.rule, node_id=payload.node_id, sku=payload.sku)
    except (ValueError, KeyError) as error:
        raise HTTPException(400, str(error))
    batch = new_batch_id()
    applied = estimators.apply(session, country, proposals, reason=payload.reason, author_claim=author, batch_id=batch)
    # Storage sized from demand follows the demand that was just estimated.
    followed = estimators.recompute(session, country, reason="demand was estimated", author_claim=author, batch_id=batch) if applied else 0
    session.commit()
    return {"rule": payload.rule, "applied": applied, "recomputed": followed, "skipped": skipped[:50], "skipped_count": len(skipped), "batch_id": batch}


@router.post("/countries/{country_id}/estimates/recompute")
def recompute_estimates(country_id: int, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    """Re-run every live rule against the inputs as they stand now. Normally unnecessary --
    edits and imports do this themselves -- but harmless, and the honest answer to
    'are these numbers current?'."""
    country = _country_or_404(session, country_id)
    changed = estimators.recompute(session, country, reason="on request", author_claim=author, batch_id=new_batch_id())
    session.commit()
    return {"recomputed": changed}
