"""Sessions: save the working state under a name, open one, read the difference."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import sessions as sessions_mod
from ..db import get_session
from ..models import Country, WorkSession
from ..schemas import SessionCreate, SessionPatch
from .deps import author_claim, new_batch_id

router = APIRouter(tags=["sessions"])


def _country_or_404(session: Session, country_id: int) -> Country:
    country = session.get(Country, country_id)
    if not country:
        raise HTTPException(404, f"No country with id {country_id}.")
    return country


def _session_or_404(session: Session, session_id: int) -> WorkSession:
    work = session.get(WorkSession, session_id)
    if not work:
        raise HTTPException(404, f"No session with id {session_id}.")
    return work


def _out(session: Session, work: WorkSession, country: Country) -> dict:
    is_current = country.current_session_id == work.id
    return {
        "id": work.id,
        "country_id": work.country_id,
        "name": work.name,
        "note": work.note,
        "kind": work.kind,
        "parent_id": work.parent_id,
        "author_claim": work.author_claim,
        "created_at": work.created_at,
        "ledger_position": work.ledger_position,
        "summary": work.summary or {},
        "size_bytes": work.snapshot.size_bytes if work.snapshot else 0,
        "is_current": is_current,
        "changes_since": sessions_mod.changes_since(session, country) if is_current else None,
    }


def _current(session: Session, country: Country) -> Optional[dict]:
    if not country.current_session_id:
        return None
    work = session.get(WorkSession, country.current_session_id)
    if not work:
        return None
    return {
        "session_id": work.id,
        "name": work.name,
        "kind": work.kind,
        "author_claim": work.author_claim,
        "created_at": work.created_at,
        "changes_since": sessions_mod.changes_since(session, country),
    }


@router.get("/countries/{country_id}/sessions")
def list_sessions(country_id: int, session: Session = Depends(get_session)):
    """The shelf: every session, newest first, and which one the working state came from."""
    country = _country_or_404(session, country_id)
    rows = list(
        session.scalars(
            select(WorkSession).where(WorkSession.country_id == country_id).order_by(WorkSession.id.desc())
        )
    )
    return {"current": _current(session, country), "sessions": [_out(session, w, country) for w in rows]}


@router.post("/countries/{country_id}/sessions", status_code=201)
def save_session(
    country_id: int,
    payload: SessionCreate,
    session: Session = Depends(get_session),
    author: str = Depends(author_claim),
):
    """Save the working state under a name. It becomes the session you are working from."""
    country = _country_or_404(session, country_id)
    name = " ".join(payload.name.split()).strip()
    if not name:
        raise HTTPException(400, "Give the session a name.")
    work = sessions_mod.save(
        session, country, name=name, note=payload.note or "", author_claim=author, batch_id=new_batch_id()
    )
    session.commit()
    session.refresh(work)
    return _out(session, work, session.get(Country, country_id))


@router.get("/sessions/{session_id}")
def get_work_session(session_id: int, session: Session = Depends(get_session)):
    work = _session_or_404(session, session_id)
    return _out(session, work, _country_or_404(session, work.country_id))


@router.patch("/sessions/{session_id}")
def rename_session(session_id: int, payload: SessionPatch, session: Session = Depends(get_session)):
    """Rename or annotate. The saved state itself is immutable."""
    work = _session_or_404(session, session_id)
    if payload.name is not None:
        name = " ".join(payload.name.split()).strip()
        if not name:
            raise HTTPException(400, "A session needs a name.")
        work.name = name
    if payload.note is not None:
        work.note = payload.note
    if payload.name is not None and work.kind == "draft":
        # Naming a draft is what makes it a save.
        work.kind = "saved"
    session.commit()
    return _out(session, work, _country_or_404(session, work.country_id))


@router.delete("/sessions/{session_id}", status_code=204)
def delete_work_session(session_id: int, session: Session = Depends(get_session)):
    work = _session_or_404(session, session_id)
    sessions_mod.delete_session(session, work)
    session.commit()


@router.post("/sessions/{session_id}/open")
def open_work_session(
    session_id: int,
    session: Session = Depends(get_session),
    author: str = Depends(author_claim),
):
    """Make the working state exactly this session. Unsaved work is kept as a draft first."""
    work = _session_or_404(session, session_id)
    country = _country_or_404(session, work.country_id)
    batch = new_batch_id()
    counts, draft = sessions_mod.open_session(session, country, work, author_claim=author, batch_id=batch)
    session.commit()
    country = session.get(Country, work.country_id)
    work = session.get(WorkSession, session_id)
    return {
        "restored": counts,
        "draft": _out(session, draft, country) if draft else None,
        "current": _current(session, country),
        "session": _out(session, work, country),
        "batch_id": batch,
    }


@router.get("/sessions/{session_id}/diff")
def diff_session(
    session_id: int,
    against: str = Query("current", description="'current' for the working state, or another session's id."),
    session: Session = Depends(get_session),
):
    """What differs between this session and the working state, or another session, in words."""
    work = _session_or_404(session, session_id)
    country = _country_or_404(session, work.country_id)
    a = sessions_mod.load_snapshot(work.snapshot)
    if against == "current":
        b = sessions_mod.capture(session, country)
        against_name = "the working state"
    else:
        try:
            other = _session_or_404(session, int(against))
        except ValueError:
            raise HTTPException(400, "'against' must be 'current' or a session id.")
        if other.country_id != work.country_id:
            raise HTTPException(400, "Sessions from different countries cannot be compared.")
        b = sessions_mod.load_snapshot(other.snapshot)
        against_name = other.name
    return {"from": work.name, "to": against_name, **sessions_mod.diff(a, b)}
