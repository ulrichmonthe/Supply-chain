"""The canonical staging model: helpers for the field entries every staged value carries.

A field entry is ``{"value", "unit", "class", "source_file_id", "location", "system",
"vintage", "transformation", "confidence", "reason", "method", "inputs",
"alternatives"}``. It is a dict rather than a table row so a record's provenance sheet
is one JSON column, and because a conflict is two entries for one field until a person
chooses -- which a normalised row-per-value table would make awkward to express.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import APPROVABLE, CLASSES, DIRECTIONAL

CONFIDENCE = ("high", "medium", "low")


def entry(
    value: Any,
    *,
    cls: str,
    source_file_id: Optional[int] = None,
    location: str = "",
    system: str = "",
    vintage: str = "",
    unit: str = "",
    transformation: str = "",
    confidence: str = "high",
    reason: str = "",
    method: str = "",
    inputs: Optional[dict] = None,
    by: str = "",
) -> dict:
    if cls not in CLASSES:
        raise ValueError(f"provenance class must be one of {CLASSES}, not {cls!r}")
    if confidence not in CONFIDENCE:
        raise ValueError(f"confidence must be one of {CONFIDENCE}, not {confidence!r}")
    out = {
        "value": value,
        "unit": unit,
        "class": cls,
        "source_file_id": source_file_id,
        "location": location,
        "system": system,
        "vintage": vintage,
        "transformation": transformation,
        "confidence": confidence,
        "reason": reason,
        "method": method,
        "inputs": inputs or {},
        "by": by,
    }
    if cls == "estimated":
        out["label"] = DIRECTIONAL
    return out


def missing(reason: str = "no value in any source and no defensible method") -> dict:
    return entry(None, cls="missing", confidence="low", reason=reason)


def value_of(record, field: str, default=None):
    """The value of a field on a staged record, or the default."""
    fields = record.fields if hasattr(record, "fields") else record
    item = (fields or {}).get(field)
    if not item:
        return default
    return item.get("value", default)


def is_conflict(item: dict) -> bool:
    return bool(item and item.get("alternatives"))


def add_alternative(fields: Dict[str, dict], field: str, incoming: dict) -> Dict[str, dict]:
    """Two sources for one field: keep both. The field stays a conflict until chosen."""
    existing = fields.get(field)
    if existing is None or existing.get("class") in ("missing", "illustrative"):
        fields[field] = incoming
        return fields
    if _same(existing.get("value"), incoming.get("value")):
        # Agreement is worth recording: a second source that says the same raises confidence.
        existing.setdefault("agreed_by", []).append({k: incoming.get(k) for k in ("source_file_id", "system", "location", "vintage")})
        if existing.get("confidence") == "medium":
            existing["confidence"] = "high"
        return fields
    existing.setdefault("alternatives", []).append(incoming)
    return fields


def choose_alternative(fields: Dict[str, dict], field: str, index: int, *, by: str, comment: str = "") -> Dict[str, dict]:
    """Resolve a conflict: ``index`` 0 keeps the current entry, n picks alternatives[n-1].
    The losers stay on the record as ``rejected`` so the decision can be audited."""
    item = fields.get(field) or {}
    alternatives = list(item.get("alternatives") or [])
    if index < 0 or index > len(alternatives):
        raise ValueError("That option does not exist.")
    options = [dict(item, alternatives=None)] + alternatives
    chosen = dict(options[index])
    chosen.pop("alternatives", None)
    chosen["chosen_by"] = by
    chosen["chosen_over"] = [dict(o, alternatives=None) for i, o in enumerate(options) if i != index]
    if comment:
        chosen["comment"] = comment
    fields[field] = chosen
    return fields


def _same(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def coverage(records: List) -> dict:
    """Counts of field entries by class and domain: the first table of the report."""
    out: Dict[str, Dict[str, int]] = {}
    for record in records:
        by_class = out.setdefault(record.domain, {c: 0 for c in CLASSES})
        for item in (record.fields or {}).values():
            by_class[item.get("class", "missing")] = by_class.get(item.get("class", "missing"), 0) + 1
    totals = {c: sum(d.get(c, 0) for d in out.values()) for c in CLASSES}
    total = sum(totals.values())
    return {
        "by_domain": out,
        "totals": totals,
        "shares": {c: (round(n / total, 4) if total else 0.0) for c, n in totals.items()},
        "approvable": sum(totals[c] for c in APPROVABLE),
        "blocking": totals["illustrative"] + totals["missing"],
    }
