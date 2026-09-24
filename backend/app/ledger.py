"""Writing to the ledger.

One function every writer calls, so a change recorded from an import, a lever, a
country setting or a typed correction all look the same in the audit trail and can
be filtered, grouped by sitting, and later reversed the same way.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .api.deps import ANONYMOUS
from .models import AuditEntry

#: Two slider moves by the same person on the same field inside this window are one
#: change in the ledger, not forty. The first old_value is kept, the last new_value
#: wins, and the row's timestamp moves to the latest touch.
COALESCE_WINDOW = timedelta(seconds=90)

#: Beyond this the old/new columns stop being readable and start being a dump.
MAX_VALUE = 2000


def render(value: Any) -> Optional[str]:
    """A value as the ledger shows it: JSON for structures, str for the rest, None for None."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    elif isinstance(value, float):
        text = f"{value:g}"
    else:
        text = str(value)
    return text[:MAX_VALUE]


def record(
    session: Session,
    *,
    country_id: int,
    entity_type: str,
    entity_ref: str = "",
    field: str = "",
    old_value: Any = None,
    new_value: Any = None,
    provenance: str = "manual_override",
    confidence_marker: str = "I",
    rationale: str = "",
    actor: str = "analyst",
    author_claim: str = ANONYMOUS,
    batch_id: Optional[str] = None,
    coalesce: bool = False,
) -> AuditEntry:
    """Add one ledger row; with ``coalesce``, fold it into a very recent row for the same
    thing by the same person instead."""
    old_text, new_text = render(old_value), render(new_value)

    if coalesce:
        recent = session.scalar(
            select(AuditEntry)
            .where(
                AuditEntry.country_id == country_id,
                AuditEntry.entity_type == entity_type,
                AuditEntry.entity_ref == entity_ref,
                AuditEntry.field == field,
                AuditEntry.author_claim == author_claim,
                AuditEntry.status == "applied",
            )
            .order_by(AuditEntry.id.desc())
            .limit(1)
        )
        if recent is not None and _fresh(recent.created_at):
            recent.new_value = new_text
            recent.created_at = datetime.now(timezone.utc)
            if recent.old_value == new_text:
                # Slid back to where it started: nothing changed in the end, and a row
                # saying "0.5 -> 0.5" would be noise.
                session.delete(recent)
                session.flush()
                return recent
            return recent

    entry = AuditEntry(
        country_id=country_id,
        entity_type=entity_type,
        entity_ref=entity_ref,
        field=field,
        old_value=old_text,
        new_value=new_text,
        provenance=provenance,
        confidence_marker=confidence_marker,
        rationale=rationale,
        actor=actor,
        author_claim=author_claim,
        batch_id=batch_id,
    )
    session.add(entry)
    session.flush()
    return entry


def changed_keys(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> List[str]:
    """Keys whose value differs between two dicts (levers, constraints, weights)."""
    before, after = before or {}, after or {}
    return sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))


def record_dict_changes(
    session: Session,
    *,
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
    field_prefix: str,
    **common: Any,
) -> List[AuditEntry]:
    """One ledger row per changed key of a JSON column, named ``prefix.key``."""
    before, after = before or {}, after or {}
    return [
        record(
            session,
            field=f"{field_prefix}.{key}",
            old_value=before.get(key),
            new_value=after.get(key),
            **common,
        )
        for key in changed_keys(before, after)
    ]


def _fresh(when: Optional[datetime]) -> bool:
    if when is None:
        return False
    if when.tzinfo is None:  # SQLite hands back naive datetimes
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - when < COALESCE_WINDOW


def entries_in(session: Session, batch_id: str) -> Iterable[AuditEntry]:
    return session.scalars(select(AuditEntry).where(AuditEntry.batch_id == batch_id).order_by(AuditEntry.id))
