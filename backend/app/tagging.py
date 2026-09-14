"""Tidying up scenario tags.

Tags are typed by hand, by several people, over months. Left alone that produces
"Ministerial", "ministerial" and " ministerial " as three different labels, and a
filter built on them is then quietly wrong — the one thing a filter must not be.
So every tag is cleaned at the boundary rather than at the point of comparison,
and what comes back out is what will be stored.

Deliberately not a controlled vocabulary. Whoever is using this knows what their
own work needs to be called, and a fixed list would be wrong in the second country.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

#: Long enough for "wet season stress test", short enough to stay a chip.
MAX_LENGTH = 32
#: Past a dozen, tags have stopped being a filter and become a second description.
MAX_TAGS = 12


def clean_tag(value: Any) -> str:
    """One tag, trimmed and collapsed. Returns "" for anything unusable."""
    text = " ".join(str(value).replace(",", " ").split())
    return text[:MAX_LENGTH].strip()


def normalise_tags(values: Optional[Iterable[Any]]) -> List[str]:
    """Clean a list of tags, dropping blanks and case-insensitive duplicates.

    The first spelling wins, so a scenario tagged "Ministerial" keeps its capital
    even when somebody later types "ministerial" — the tag is theirs, the matching
    is ours.
    """
    if not values:
        return []
    if isinstance(values, (str, bytes)):
        values = [values]

    out: List[str] = []
    seen = set()
    for raw in values:
        tag = clean_tag(raw)
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) == MAX_TAGS:
            break
    return out
