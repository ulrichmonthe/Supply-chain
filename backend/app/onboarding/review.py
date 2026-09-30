"""The review queue and the decisions a person makes in it.

Triaged by impact, so the decisions that move the result come first. Low-confidence
items cannot be approved in bulk: each one is a click. Every decision is recorded with
who and when, and changes the staged record the way a decision should -- a chosen
alternative wins, a rejected estimate leaves the gap open, a typed figure is confirmed.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import OnboardingRun, ReviewItem, SourceFile, StagedRecord
from . import estimate as estimating
from .service import OnboardingError, _open
from .staging import choose_alternative, entry, missing

DECISIONS = ("accept", "reject", "choose", "correct", "answer")


def queue(session: Session, run: OnboardingRun, *, kind: Optional[str] = None, pending_only: bool = True, limit: int = 200) -> dict:
    stmt = select(ReviewItem).where(ReviewItem.run_id == run.id)
    if kind:
        stmt = stmt.where(ReviewItem.kind == kind)
    if pending_only:
        stmt = stmt.where(ReviewItem.decision == "pending")
    items = list(session.scalars(stmt.order_by(ReviewItem.impact.desc(), ReviewItem.id)))
    counts: Dict[str, int] = {}
    by_confidence: Dict[str, int] = {}
    for item in items:
        counts[item.kind] = counts.get(item.kind, 0) + 1
        by_confidence[item.confidence] = by_confidence.get(item.confidence, 0) + 1
    return {"total": len(items), "by_kind": counts, "by_confidence": by_confidence, "items": [as_dict(i) for i in items[:limit]], "truncated": max(0, len(items) - limit)}


def as_dict(item: ReviewItem) -> dict:
    return {
        "id": item.id, "kind": item.kind, "subject": item.subject, "title": item.title, "detail": item.detail,
        "confidence": item.confidence, "impact": round(item.impact, 3), "proposed_by": item.proposed_by,
        "payload": item.payload, "options": item.options, "decision": item.decision, "choice": item.choice,
        "comment": item.comment, "decided_by": item.decided_by, "decided_at": item.decided_at.isoformat() if item.decided_at else None,
    }


def decide(session: Session, run: OnboardingRun, item: ReviewItem, *, decision: str, by: str, choice: str = "", value=None, comment: str = "") -> ReviewItem:
    _open(run)
    if decision not in DECISIONS:
        raise OnboardingError(f"A decision is one of {DECISIONS}.")
    if item.decision != "pending":
        raise OnboardingError("This item was already decided.")
    if not by or by == "anonymous":
        raise OnboardingError("Sign your name before deciding: every decision is recorded with who made it.")
    now = datetime.now(timezone.utc)
    record = session.get(StagedRecord, (item.payload or {}).get("record_id")) if (item.payload or {}).get("record_id") else None
    field = (item.payload or {}).get("field")

    if item.kind == "estimate":
        if decision == "accept":
            _apply_estimate(record, item, by)
        elif decision == "reject":
            pass  # the gap stays a gap; sign-off will say so
        else:
            raise OnboardingError("An estimate is accepted or rejected.")
    elif item.kind == "conflict":
        if decision != "choose":
            raise OnboardingError("A conflict is settled by choosing one of its options.")
        try:
            index = int(choice)
        except (TypeError, ValueError):
            raise OnboardingError("Choose an option by its index.")
        fields = copy.deepcopy(record.fields or {})
        choose_alternative(fields, field, index, by=by, comment=comment)
        record.fields = fields
        record.status = "conflict" if any(i.get("alternatives") for i in fields.values()) else "staged"
    elif item.kind == "anomaly":
        if decision == "accept" or choice == "keep":
            _annotate(record, field, f"Reviewed by {by}: value kept. {comment}".strip())
        elif decision == "reject" or choice == "drop":
            fields = copy.deepcopy(record.fields or {})
            fields[field] = missing(f"dropped by {by}: {comment or item.title}")
            record.fields, record.status = fields, "blocked"
        elif decision == "correct":
            _correct(record, field, value, by, comment)
        else:
            raise OnboardingError("A flag is kept, dropped or corrected.")
    elif item.kind == "missing":
        if decision != "correct" or value in (None, ""):
            raise OnboardingError("A missing figure needs a typed value.")
        _correct(record, field, value, by, comment)
    elif item.kind in ("mapping", "match"):
        raise OnboardingError("Mappings are confirmed on the file and facility matches on the match itself.")
    elif item.kind == "question":
        if decision != "answer":
            raise OnboardingError("A question is answered.")
        if record and field and value not in (None, ""):
            _correct(record, field, value, by, comment or "answered by a local officer")
    elif item.kind == "unit":
        if decision == "correct" and value not in (None, ""):
            _correct(record, field, value, by, comment)
        elif decision != "accept":
            raise OnboardingError("A unit question is answered with a value or accepted as is.")

    item.decision = {"accept": "accepted", "reject": "rejected", "choose": "chosen", "correct": "accepted", "answer": "answered"}[decision]
    item.choice, item.comment, item.decided_by, item.decided_at = str(choice or ""), comment, by, now
    session.flush()
    return item


def bulk_decide(session: Session, run: OnboardingRun, item_ids: List[int], *, decision: str, by: str, comment: str = "") -> dict:
    """FR26: bulk only for items the pipeline was confident about. Low-confidence items
    in the list are left pending and named."""
    _open(run)
    done, skipped = [], []
    for item_id in item_ids:
        item = session.get(ReviewItem, item_id)
        if item is None or item.run_id != run.id or item.decision != "pending":
            continue
        if item.confidence == "low":
            skipped.append({"id": item.id, "title": item.title, "why": "low confidence: decide it on its own"})
            continue
        if item.kind not in ("estimate", "anomaly"):
            skipped.append({"id": item.id, "title": item.title, "why": f"a {item.kind} needs a choice, not a yes"})
            continue
        try:
            decide(session, run, item, decision=decision, by=by, choice="keep" if item.kind == "anomaly" and decision == "accept" else "", comment=comment)
            done.append(item.id)
        except OnboardingError as error:
            skipped.append({"id": item.id, "title": item.title, "why": str(error)})
    return {"decided": done, "skipped": skipped}


def ask_local(session: Session, run: OnboardingRun, item: ReviewItem, *, contact: str, question: str, by: str) -> ReviewItem:
    """FR29: a specific question to a local officer, recorded as a review item whose
    answer becomes a confirmed source."""
    _open(run)
    asked = ReviewItem(run_id=run.id, kind="question", subject=item.subject, title=f"Asked {contact}: {question[:200]}", detail=f"About: {item.title}", confidence="low", impact=item.impact, payload={**(item.payload or {}), "contact": contact, "question": question, "asked_by": by, "about_item": item.id}, options=[{"code": "answer", "label": "Record the answer"}])
    session.add(asked)
    session.flush()
    return asked


def _apply_estimate(record: StagedRecord, item: ReviewItem, by: str) -> None:
    payload = item.payload or {}
    proposal = estimating.Estimate(record.domain, record.key, payload["field"], payload["method"], payload["value"], payload.get("inputs") or {}, payload.get("formula") or "", item.confidence if item.confidence in ("high", "medium", "low") else "low")
    fields = copy.deepcopy(record.fields or {})
    new = proposal.entry()
    new["by"] = by
    old = fields.get(payload["field"])
    if old and old.get("class") in ("observed", "converted"):
        new["inputs"] = {**new["inputs"], "replaced": old.get("value")}
        new["chosen_over"] = [old]
    fields[payload["field"]] = new
    record.fields = fields
    record.status = "conflict" if any(i.get("alternatives") for i in fields.values()) else "staged"


def _correct(record: StagedRecord, field: str, value, by: str, comment: str) -> None:
    if record is None or not field:
        raise OnboardingError("Nothing to correct.")
    try:
        typed = float(value) if isinstance(value, (int, float)) or str(value).replace(".", "", 1).replace("-", "", 1).isdigit() else value
    except (TypeError, ValueError):
        typed = value
    fields = copy.deepcopy(record.fields or {})
    old = fields.get(field)
    new = entry(typed, cls="confirmed", confidence="high", reason=comment or "typed by a named person", by=by)
    if old:
        new["chosen_over"] = [dict(old, alternatives=None)]
    fields[field] = new
    record.fields = fields
    record.status = "conflict" if any(i.get("alternatives") for i in fields.values()) else "staged"


def _annotate(record: StagedRecord, field: str, note: str) -> None:
    if record is None or not field:
        return
    fields = copy.deepcopy(record.fields or {})
    item = dict(fields.get(field) or {})
    if not item:
        return
    item["reviewed"] = note
    if item.get("confidence") == "low":
        item["confidence"] = "medium"
    fields[field] = item
    record.fields = fields
    record.issues = [i for i in (record.issues or []) if i.get("field") != field]


def reviewer_hours(session: Session, run: OnboardingRun) -> dict:
    """The capacity metric: how many decisions people made, and over what span."""
    decided = [i for i in session.scalars(select(ReviewItem).where(ReviewItem.run_id == run.id, ReviewItem.decision != "pending")) if i.decided_at]
    if not decided:
        return {"decisions": 0, "reviewers": [], "first": None, "last": None}
    times = sorted(i.decided_at for i in decided)
    return {"decisions": len(decided), "reviewers": sorted({i.decided_by for i in decided if i.decided_by}), "first": times[0].isoformat(), "last": times[-1].isoformat(), "by_kind": {k: sum(1 for i in decided if i.kind == k) for k in {i.kind for i in decided}}}
