"""Request-scoped things every router needs.

Only one so far: who is making the change, as they chose to be recorded.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import Header

#: What the ledger records when nobody has signed their work. Visible on purpose:
#: a column of "anonymous" beside a colleague's name is the nudge that works.
ANONYMOUS = "anonymous"
MAX_CLAIM = 96


def clean_claim(value: Optional[str]) -> str:
    """One line, trimmed and collapsed, bounded, never empty."""
    text = " ".join((value or "").split())[:MAX_CLAIM].strip()
    return text or ANONYMOUS


def author_claim(x_author: Optional[str] = Header(default=None, alias="X-Author")) -> str:
    """The name the person typed for the record, sent by the interface on every write.

    Not authentication -- there are no accounts yet -- and not pretending to be. It
    is the "signed" line on a hand-filled form: cheap, honest, and it survives into
    real accounts by a migration rather than a rewrite when they arrive.
    """
    return clean_claim(x_author)


def new_batch_id() -> str:
    """One id per request or import, so 'what did that upload change' is one query."""
    return str(uuid.uuid4())
