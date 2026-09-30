"""Facility reconciliation: the core of onboarding, because most countries have no
authoritative master facility list to match against.

Any number of facility lists come in. None is authoritative unless the country pack
names one. Each source record is scored against every candidate it might be -- the
facilities already in the model, the crosswalk the ministry owns, and records from
other lists accepted earlier in the run -- using name, admin unit, type, identifiers
and coordinates together. Every candidate gets a confidence and the reasons behind it.
Only a score above the pack's threshold, with a clear margin over the runner-up, is
accepted by the pipeline; everything else is a decision for the named arbiter, and
their decision is recorded as the source.

The name normalisation deliberately keeps the words that classify a facility.
"Kerema General Hospital" and "Kerema Hospital" are the same place; "Tabubil
Hospital" and "Tabubil Rural Clinic" are not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Tuple

from ..connectors.reconcile import normalise_name
from ..engine.geo import haversine_km

#: Words that qualify but do not classify. Stripped before comparing.
_QUALIFIERS = re.compile(r"\b(general|district|provincial|rural|urban|the|of|sub|and|&|hf|h/c|hc|st\.?|saint)\b", re.IGNORECASE)
#: Common abbreviations the same facility is written with.
_ALIASES = [
    (re.compile(r"\bh\s*/?\s*c\b", re.I), "health centre"),
    (re.compile(r"\bhealth\s+center\b", re.I), "health centre"),
    (re.compile(r"\bhosp\b\.?", re.I), "hospital"),
    (re.compile(r"\bgen\b\.?", re.I), "general"),
    (re.compile(r"\bmem\b\.?", re.I), "memorial"),
    (re.compile(r"\ba/?p\b", re.I), "aid post"),
    (re.compile(r"\bsub[-\s]?health\b", re.I), "sub health"),
    (re.compile(r"\bmt\b\.?", re.I), "mount"),
]

#: Type words, so a hospital and a clinic with the same town name are told apart.
_TYPE_WORDS = ("hospital", "clinic", "aid post", "health centre", "sub health", "dispensary", "store", "warehouse", "pharmacy")


@dataclass
class Candidate:
    code: str
    name: str
    source: str  # model | crosswalk | list:<file>
    score: float
    reasons: List[str] = field(default_factory=list)
    lat: Optional[float] = None
    lon: Optional[float] = None
    admin1: Optional[str] = None
    admin2: Optional[str] = None
    type: str = ""
    ids: Dict[str, str] = field(default_factory=dict)
    name_sim: float = 0.0
    id_match: bool = False

    def as_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name, "source": self.source, "score": round(self.score, 3), "name_similarity": round(self.name_sim, 3),
            "reasons": self.reasons, "lat": self.lat, "lon": self.lon, "admin1": self.admin1, "admin2": self.admin2, "type": self.type, "ids": self.ids,
        }


@dataclass
class Target:
    """Something a source record might be."""

    code: str
    name: str
    source: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    admin1: Optional[str] = None
    admin2: Optional[str] = None
    type: str = ""
    ids: Dict[str, str] = field(default_factory=dict)


def expand_aliases(name: str) -> str:
    text = name or ""
    for pattern, replacement in _ALIASES:
        text = pattern.sub(replacement, text)
    return text


def clean(name: str) -> str:
    """Lower case, aliases expanded, qualifiers dropped, punctuation gone."""
    text = expand_aliases(name or "").lower()
    text = _QUALIFIERS.sub(" ", text)
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


def tokens(name: str) -> set:
    return {t for t in clean(name).split() if len(t) > 1}


def type_word(name: str) -> str:
    text = clean(name)
    for word in _TYPE_WORDS:
        if word in text:
            return word
    return ""


def name_similarity(a: str, b: str) -> Tuple[float, str]:
    """0..1 with a reason. Exact after normalisation is 1; token overlap and character
    similarity otherwise, with a penalty when the classifying word differs."""
    ca, cb = clean(a), clean(b)
    if not ca or not cb:
        return 0.0, "no name to compare"
    if normalise_name(a) == normalise_name(b) or ca == cb:
        return 1.0, "same name"
    ta, tb = tokens(a), tokens(b)
    jaccard = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    ratio = SequenceMatcher(None, ca, cb).ratio()
    score = max(jaccard, ratio * 0.95)
    # The distinguishing word: a town name shared by a hospital and a clinic is not a match.
    wa, wb = type_word(a), type_word(b)
    if wa and wb and wa != wb:
        score *= 0.55
        return score, f"names alike ({score:.2f}) but one is a {wa} and the other a {wb}"
    core_a, core_b = ta - set(" ".join(_TYPE_WORDS).split()), tb - set(" ".join(_TYPE_WORDS).split())
    if core_a and core_b and core_a == core_b:
        score = max(score, 0.93)
        return score, "same name apart from the facility type words"
    return score, f"names {'alike' if score >= 0.7 else 'differ'} ({score:.2f})"


def _admin_match(a: Optional[str], b: Optional[str]) -> Optional[bool]:
    if not a or not b:
        return None
    return clean(a) == clean(b) or clean(a) in clean(b) or clean(b) in clean(a)


def score(record: dict, target: Target, *, geolocation_allowed: bool = True) -> Candidate:
    """One source record against one target. Reasons are sentences a reviewer reads."""
    reasons: List[str] = []
    total = 0.0

    # Identifiers first: the same key in the same system is close to proof.
    record_ids = {k: str(v) for k, v in (record.get("external_ids") or {}).items() if v}
    code = str(record.get("code") or "").strip()
    shared = [k for k, v in record_ids.items() if target.ids.get(k) == v]
    id_match = False
    if code and (code == target.code or code in target.ids.values()):
        total += 0.7
        id_match = True
        reasons.append(f"same code {code}")
    elif shared:
        total += 0.75
        id_match = True
        reasons.append(f"same {', '.join(shared)} identifier")

    sim, why = name_similarity(str(record.get("name") or ""), target.name)
    total += 0.55 * sim
    reasons.append(why)

    admin1 = _admin_match(record.get("admin1"), target.admin1)
    if admin1 is True:
        total += 0.15
        reasons.append(f"same {record.get('admin1')}")
    elif admin1 is False:
        total -= 0.2
        reasons.append(f"different admin unit ({record.get('admin1')} vs {target.admin1})")
    admin2 = _admin_match(record.get("admin2"), target.admin2)
    if admin2 is True:
        total += 0.08
    elif admin2 is False:
        total -= 0.05

    rtype, ttype = str(record.get("type") or "").lower(), (target.type or "").lower()
    if rtype and ttype:
        if rtype == ttype:
            total += 0.05
        else:
            total -= 0.05

    lat, lon = record.get("lat"), record.get("lon")
    if geolocation_allowed and lat not in (None, "") and lon not in (None, "") and target.lat is not None and target.lon is not None:
        try:
            km = haversine_km(float(lat), float(lon), target.lat, target.lon)
        except (TypeError, ValueError):
            km = None
        if km is not None:
            if km <= 1.0:
                total += 0.25
                reasons.append(f"{km:.1f} km apart")
            elif km <= 5.0:
                total += 0.12
                reasons.append(f"{km:.1f} km apart")
            elif km <= 20.0:
                reasons.append(f"{km:.0f} km apart")
            else:
                total -= 0.3
                reasons.append(f"{km:.0f} km apart, which is too far for the same place")

    return Candidate(target.code, target.name, target.source, max(0.0, min(1.0, total)), reasons, target.lat, target.lon, target.admin1, target.admin2, target.type, dict(target.ids), sim, id_match)


@dataclass
class Outcome:
    decision: str  # auto_accepted | pending | new
    confidence: float
    reason: str
    candidates: List[Candidate]

    @property
    def best(self) -> Optional[Candidate]:
        return self.candidates[0] if self.candidates else None


def match(record: dict, targets: Iterable[Target], *, auto_accept: float = 0.92, review_floor: float = 0.45, margin: float = 0.12, geolocation_allowed: bool = True, top: int = 4) -> Outcome:
    """Score every target, keep the top few, and decide what the pipeline may do alone."""
    scored = sorted((score(record, t, geolocation_allowed=geolocation_allowed) for t in targets), key=lambda c: -c.score)
    # Proximity and a shared province make a weak name stronger, but cannot make a
    # different name the same place: without a shared identifier, a candidate whose
    # name barely resembles the record is not a candidate at all.
    scored = [c for c in scored if c.score > 0.15 and (c.id_match or c.name_sim >= 0.4)][:top]
    if not scored:
        return Outcome("new", 0.0, "No facility in any list resembles this one.", [])
    best = scored[0]
    runner_up = scored[1].score if len(scored) > 1 else 0.0
    if best.score >= auto_accept and best.score - runner_up >= margin:
        return Outcome("auto_accepted", best.score, f"{best.name}: " + "; ".join(best.reasons), scored)
    if best.score < review_floor:
        return Outcome("new", best.score, f"Closest is {best.name} at {best.score:.2f}, below the review floor; treated as a facility only this source knows.", scored)
    if best.score - runner_up < margin and len(scored) > 1:
        return Outcome("pending", best.score, f"Ambiguous: {best.name} ({best.score:.2f}) and {scored[1].name} ({scored[1].score:.2f}) are both plausible.", scored)
    return Outcome("pending", best.score, f"Probably {best.name} ({best.score:.2f}), below the auto-accept threshold of {auto_accept:.2f}.", scored)


def targets_from_nodes(nodes: Iterable) -> List[Target]:
    return [
        Target(n.code, n.name, "model", n.lat, n.lon, n.admin1, n.admin2, n.type or "", {"model": n.code, **{k: str(v) for k, v in (n.external_ids or {}).items() if v}})
        for n in nodes
        if getattr(n, "level", 3) >= 1
    ]


def targets_from_crosswalk(rows: Iterable) -> List[Target]:
    return [Target(r.canonical_code, r.name, "crosswalk", r.lat, r.lon, r.admin1, r.admin2, r.type or "", {k: str(v) for k, v in (r.ids or {}).items()}) for r in rows]


def target_from_record(record: dict, source: str) -> Target:
    return Target(str(record.get("code") or ""), str(record.get("name") or ""), source, _float(record.get("lat")), _float(record.get("lon")), record.get("admin1"), record.get("admin2"), str(record.get("type") or ""), {source: str(record.get("code") or ""), **{k: str(v) for k, v in (record.get("external_ids") or {}).items() if v}})


def _float(value) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
