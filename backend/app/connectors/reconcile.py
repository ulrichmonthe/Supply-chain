"""Matching incoming records to the facilities already in the model.

A national facility list is not a table you overwrite. It is a register that two
systems disagree about, and the disagreements are the interesting part: a facility
DHIS2 has that the model does not, a coordinate that moved 40 km since last year, a
site that closed. The point of reconciliation is to put those in front of a person
before anything is written, not to resolve them silently.

Matching runs in order of how much the identifier is worth:

1. **The system's own key.** A DHIS2 UID matching a stored ``dhis2_uid`` is proof.
2. **Facility code.** Strong, but codes get reissued and retyped.
3. **Any shared external identifier.** A master facility list code in common.
4. **Name and proximity.** Only when the names agree after normalisation *and* the
   two points are within a few kilometres. Name alone is not a match — "Kerema
   Health Centre" exists in more than one country, let alone more than one district.

Anything unmatched is reported as new rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..engine.geo import haversine_km

#: How far a facility may move before the preview calls it out. Below this it is
#: usually a better geocode; above it, somebody should look.
MOVED_THRESHOLD_KM = 2.0
#: Maximum separation for a name-and-proximity match.
NAME_MATCH_RADIUS_KM = 5.0

SYSTEM_KEYS = {
    "dhis2": "dhis2_uid",
    "msupply": "msupply_id",
    "openlmis": "openlmis_code",
}

# Only words that qualify a facility are stripped, never words that *classify* one.
# "Kerema General Hospital" and "Kerema Hospital" are the same place. "Tabubil
# Hospital" and "Tabubil Rural Clinic" are not, and a normalisation that stripped
# "hospital" and "clinic" would silently merge them — losing a facility from a
# national list, which is a far worse error than reporting one as new.
_NOISE = re.compile(r"\b(general|district|provincial|rural|urban|the|of)\b", re.IGNORECASE)


def normalise_name(name: str) -> str:
    """Strip the words that every facility name contains and nothing distinguishes."""
    without_noise = _NOISE.sub(" ", name or "")
    return re.sub(r"[^a-z0-9]+", "", without_noise.lower())


@dataclass
class Match:
    incoming_code: str
    incoming_name: str
    existing_id: Optional[int]
    existing_code: Optional[str]
    existing_name: Optional[str]
    method: str
    changes: Dict[str, list] = field(default_factory=dict)
    distance_moved_km: Optional[float] = None

    def as_dict(self) -> dict:
        return {
            "incoming_code": self.incoming_code,
            "incoming_name": self.incoming_name,
            "existing_id": self.existing_id,
            "existing_code": self.existing_code,
            "existing_name": self.existing_name,
            "method": self.method,
            "changes": self.changes,
            "distance_moved_km": (
                round(self.distance_moved_km, 2) if self.distance_moved_km is not None else None
            ),
        }


@dataclass
class Reconciliation:
    matched: List[Match] = field(default_factory=list)
    new: List[Match] = field(default_factory=list)
    #: In the model but not in this pull. Not necessarily closed — could be a filter.
    absent: List[dict] = field(default_factory=list)
    #: Two incoming records claiming the same existing facility.
    collisions: List[dict] = field(default_factory=list)

    def summary(self) -> dict:
        moved = [m for m in self.matched if (m.distance_moved_km or 0) > MOVED_THRESHOLD_KM]
        renamed = [m for m in self.matched if "name" in m.changes]
        gained = [m for m in self.matched if "lat" in m.changes and m.distance_moved_km is None]
        return {
            "matched": len(self.matched),
            "new": len(self.new),
            "absent": len(self.absent),
            "collisions": len(self.collisions),
            "moved": len(moved),
            "renamed": len(renamed),
            "gained_coordinates": len(gained),
            "by_method": _count_by(m.method for m in self.matched),
        }

    def as_dict(self, sample: int = 40) -> dict:
        return {
            "summary": self.summary(),
            "matched": [m.as_dict() for m in self.matched[:sample]],
            "new": [m.as_dict() for m in self.new[:sample]],
            "absent": self.absent[:sample],
            "collisions": self.collisions[:sample],
            "truncated": {
                "matched": max(0, len(self.matched) - sample),
                "new": max(0, len(self.new) - sample),
                "absent": max(0, len(self.absent) - sample),
            },
            "headline": self.headline(),
        }

    def headline(self) -> str:
        counts = self.summary()
        parts = [f"{counts['matched']} facilities matched", f"{counts['new']} are new"]
        if counts["absent"]:
            parts.append(f"{counts['absent']} in the model were not in this pull")
        if counts["moved"]:
            parts.append(f"{counts['moved']} would move more than {MOVED_THRESHOLD_KM:.0f} km")
        if counts["gained_coordinates"]:
            parts.append(f"{counts['gained_coordinates']} would gain a coordinate")
        if counts["renamed"]:
            parts.append(f"{counts['renamed']} would be renamed")
        if counts["collisions"]:
            parts.append(f"{counts['collisions']} collisions need resolving first")
        return ", ".join(parts) + "."


def _count_by(values) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


#: Fields worth reporting a change on. Deliberately excludes anything an LMIS does not
#: know about, so a sync never proposes to zero a storage capacity it has no view of.
TRACKED_FIELDS = ("name", "lat", "lon", "admin1", "admin2", "operating_status")


def reconcile(incoming: List[dict], existing_nodes: List, system: str) -> Reconciliation:
    """Match a connector's facilities against the nodes already in the country."""
    result = Reconciliation()
    system_key = SYSTEM_KEYS.get(system, "")

    by_system_key: Dict[str, object] = {}
    by_code: Dict[str, object] = {}
    by_external: Dict[str, object] = {}
    by_name: Dict[str, list] = {}

    for node in existing_nodes:
        external = node.external_ids or {}
        if system_key and external.get(system_key):
            by_system_key[str(external[system_key])] = node
        by_code[node.code] = node
        for value in external.values():
            if value:
                by_external.setdefault(str(value), node)
        by_name.setdefault(normalise_name(node.name), []).append(node)

    claimed: Dict[int, str] = {}

    for record in incoming:
        code = str(record.get("code") or "")
        name = str(record.get("name") or "")
        external = record.get("external_ids") or {}

        node, method = _find(record, system_key, external, code, name, by_system_key, by_code, by_external, by_name)

        if node is None:
            result.new.append(Match(code, name, None, None, None, "new"))
            continue

        if node.id in claimed:
            result.collisions.append(
                {
                    "existing_code": node.code,
                    "existing_name": node.name,
                    "claimed_by": [claimed[node.id], code],
                    "detail": (
                        "Two incoming facilities matched the same record. Resolve the duplicate "
                        "in the source system before syncing; importing either one would be a "
                        "guess."
                    ),
                }
            )
            continue
        claimed[node.id] = code

        changes: Dict[str, list] = {}
        for field_name in TRACKED_FIELDS:
            new_value = record.get(field_name)
            old_value = getattr(node, field_name, None)
            if field_name in ("lat", "lon"):
                continue  # handled together, below
            if new_value not in (None, "") and new_value != old_value:
                changes[field_name] = [old_value, new_value]

        moved = None
        new_lat, new_lon = record.get("lat"), record.get("lon")
        if new_lat is not None and new_lon is not None:
            if node.lat is None or node.lon is None:
                changes["lat"] = [None, new_lat]
                changes["lon"] = [None, new_lon]
            else:
                moved = haversine_km(node.lat, node.lon, new_lat, new_lon)
                if moved > 0.05:
                    changes["lat"] = [node.lat, new_lat]
                    changes["lon"] = [node.lon, new_lon]

        result.matched.append(
            Match(code, name, node.id, node.code, node.name, method, changes, moved)
        )

    matched_ids = set(claimed)
    for node in existing_nodes:
        # Hubs and the central store are network structure, not LMIS records; their
        # absence from a facility pull is expected and not worth reporting.
        if node.id not in matched_ids and node.level >= 2 and not node.hub_capable:
            result.absent.append(
                {"id": node.id, "code": node.code, "name": node.name, "admin1": node.admin1}
            )

    return result


def _find(record, system_key, external, code, name, by_system_key, by_code, by_external, by_name):
    if system_key and external.get(system_key):
        node = by_system_key.get(str(external[system_key]))
        if node is not None:
            return node, "system_id"

    node = by_code.get(code)
    if node is not None:
        return node, "code"

    for value in external.values():
        if value:
            node = by_external.get(str(value))
            if node is not None:
                return node, "external_id"

    candidates = by_name.get(normalise_name(name)) or []
    lat, lon = record.get("lat"), record.get("lon")
    if len(candidates) == 1 and lat is not None and lon is not None:
        candidate = candidates[0]
        if candidate.lat is not None and candidate.lon is not None:
            if haversine_km(candidate.lat, candidate.lon, lat, lon) <= NAME_MATCH_RADIUS_KM:
                return candidate, "name_and_proximity"
    return None, "new"
