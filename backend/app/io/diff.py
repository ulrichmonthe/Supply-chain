"""What an import would change, before it changes anything.

An import used to be a wipe-and-reload. That is honest about what the file says and
brutal about everything else: a coordinate corrected by hand in the tool died at the
next master-list refresh, and a facility that dropped off the list took its history
with it. So an import is now a diff with a memory, in three parts:

* the **file** (what the incoming workbook or sync says),
* the **model** (what the tool holds now),
* the **last import** (what the file said the previous time, kept on every row).

Those three tell four stories apart. File and model agree: nothing. File differs and
the model still matches the last import: the file moved, apply it -- an *update*.
File differs and the model has moved away from the last import: somebody changed it
by hand since, and the file disagrees -- a *conflict*, which is a decision, not an
edit. In the model and absent from a full refresh: *retire*, never delete. Retired
and back in the file: *restore*.

Nothing here writes. ``apply.py`` does, from this.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..connectors.reconcile import reconcile
from ..db import INCLUDE_RETIRED
from ..models import AuditEntry, Country, Demand, Edge, Node, Product

#: The fields an import is allowed to set on each row, and therefore the fields the
#: diff looks at. Anything else on the model belongs to the tool (distances the
#: cascade computed, external ids merged by connectors) and is never a file change.
NODE_FIELDS = (
    "name", "level", "type", "lat", "lon", "geocode_confidence", "geocode_source",
    "admin1", "admin2", "terrain_class", "catchment_population", "operating_status",
    "capacity", "hub_capable", "hub_fixed_cost", "hub_open_capex", "hub_throughput_m3",
)
EDGE_FIELDS = (
    "mode", "service_name", "service_frequency", "service_days",
    "capacity_per_trip_m3", "cold_capacity_per_trip_m3", "fixed_cost_per_trip",
    "variable_cost_per_km", "cost_per_m3", "monthly_access", "monthly_cost_multiplier",
    "reliability", "lead_time_sd_days", "active",
    # Only when the file states a distance outright; a computed one is the tool's.
    "distance_km", "base_travel_time_hr",
)
PRODUCT_FIELDS = ("name", "temperature_band", "volume_per_unit_cm3", "unit_cost", "shelf_life_days")
DEMAND_FIELDS = ("quantity", "source", "confidence")

#: What a connector supplies. It has no view of storage or hubs, so a sync must not
#: propose to zero a capacity it never knew about.
SYNC_NODE_FIELDS = ("name", "lat", "lon", "admin1", "admin2", "operating_status")


@dataclass
class RowChange:
    key: str
    label: str
    kind: str  # add | update | retire | restore | conflict
    #: field -> {"from": current, "to": file}, for what would be applied outright
    fields: Dict[str, dict] = field(default_factory=dict)
    #: field -> {"model": current, "file": incoming, "last_import": remembered}
    conflicts: Dict[str, dict] = field(default_factory=dict)
    record: Optional[dict] = None
    existing_id: Optional[int] = None
    method: str = ""

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "fields": self.fields,
            "conflicts": self.conflicts,
            "method": self.method,
        }


@dataclass
class SheetChanges:
    adds: List[RowChange] = field(default_factory=list)
    updates: List[RowChange] = field(default_factory=list)
    retires: List[RowChange] = field(default_factory=list)
    restores: List[RowChange] = field(default_factory=list)
    conflicts: List[RowChange] = field(default_factory=list)
    #: Matched and identical. Kept as rows, not a count, because a matched row still
    #: has work to do on apply: remembering the file, and a sync writing its key back.
    unchanged_rows: List[RowChange] = field(default_factory=list)

    @property
    def unchanged(self) -> int:
        return len(self.unchanged_rows)

    @property
    def matched(self) -> List[RowChange]:
        return self.updates + self.conflicts + self.restores + self.unchanged_rows

    def counts(self) -> dict:
        return {
            "add": len(self.adds),
            "update": len(self.updates),
            "retire": len(self.retires),
            "restore": len(self.restores),
            "conflict": len(self.conflicts),
            "unchanged": self.unchanged,
        }

    def as_dict(self, sample: int = 60) -> dict:
        return {
            "counts": self.counts(),
            "adds": [c.as_dict() for c in self.adds[:sample]],
            "updates": [c.as_dict() for c in self.updates[:sample]],
            "retires": [c.as_dict() for c in self.retires[:sample]],
            "restores": [c.as_dict() for c in self.restores[:sample]],
            "conflicts": [c.as_dict() for c in self.conflicts],  # every one: each is a decision
        }


@dataclass
class Changes:
    mode: str
    nodes: SheetChanges = field(default_factory=SheetChanges)
    edges: SheetChanges = field(default_factory=SheetChanges)
    products: SheetChanges = field(default_factory=SheetChanges)
    demand: SheetChanges = field(default_factory=SheetChanges)
    collisions: List[dict] = field(default_factory=list)

    @property
    def sheets(self) -> Dict[str, SheetChanges]:
        return {"nodes": self.nodes, "edges": self.edges, "products": self.products, "demand": self.demand}

    def conflict_count(self) -> int:
        return sum(len(sheet.conflicts) for sheet in self.sheets.values())

    def headline(self) -> str:
        n = self.nodes.counts()
        parts = []
        if n["add"]:
            parts.append(f"{n['add']} new facilities")
        if n["update"]:
            parts.append(f"{n['update']} updated")
        if n["retire"]:
            parts.append(f"{n['retire']} would be retired")
        if n["restore"]:
            parts.append(f"{n['restore']} would come back")
        total_conflicts = self.conflict_count()
        if total_conflicts:
            parts.append(f"{total_conflicts} conflicts with corrections made in the tool")
        if not parts:
            return "The file matches the model. Nothing would change."
        return ", ".join(parts) + "."

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "headline": self.headline(),
            "conflicts": self.conflict_count(),
            "collisions": self.collisions,
            **{name: sheet.as_dict() for name, sheet in self.sheets.items()},
        }


# --- comparison ------------------------------------------------------------------------


def same(a: Any, b: Any) -> bool:
    """Equal for the purpose of 'did the file change this'. ``a`` is the file's value."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(a, str) and isinstance(b, str):
        return a.strip() == b.strip()
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        # Asymmetric on purpose: ``a`` is the file's view and a file can only say what
        # its columns can say. A model dict carrying an extra key the sheet has no
        # column for (an assumed cover in a capacity block) is not a change.
        return all(k in b and same(a[k], b[k]) for k in a)
    return a == b


def states_distance(record: dict) -> bool:
    """Does this lane row state its own distance, rather than leave it to the cascade?"""
    if record.get("distance_method") == "manual":
        return True
    try:
        return float(record.get("distance_km") or 0) > 0 and not record.get("distance_method")
    except (TypeError, ValueError):
        return False


def supplied(record: dict, field_name: str) -> bool:
    """A blank cell means 'no opinion', not 'set this to nothing'."""
    if field_name not in record:
        return False
    value = record[field_name]
    return value is not None and value != ""


def classify(
    record: dict,
    current: Any,
    fields: Iterable[str],
    last_import: Optional[dict],
    hand_made: Iterable[str] = (),
) -> tuple:
    """Split a matched row's fields into (updates, conflicts).

    ``hand_made`` names the fields the ledger says were changed by hand and still
    stand. It covers the case memory cannot: a row that was seeded or edited before it
    was ever imported has no ``last_import`` to compare against, and the correction
    would otherwise read as an ordinary update and be overwritten.
    """
    updates: Dict[str, dict] = {}
    conflicts: Dict[str, dict] = {}
    remembered = last_import or {}
    hand = set(hand_made)
    for name in fields:
        if not supplied(record, name):
            continue
        incoming = record[name]
        now = getattr(current, name, None)
        if same(incoming, now):
            continue
        if (name in remembered and not same(now, remembered[name])) or name in hand:
            # The model moved away from what the last import said, or the ledger says a
            # person set this: a hand correction the file disagrees with.
            conflicts[name] = {"model": now, "file": incoming, "last_import": remembered.get(name)}
        else:
            updates[name] = {"from": now, "to": incoming}
    return updates, conflicts


# --- the diff --------------------------------------------------------------------------


def standing_hand_changes(session: Session, country_id: int) -> Dict[tuple, set]:
    """``{(entity_type, entity_ref): {field, ...}}`` for every hand-made change still standing."""
    out: Dict[tuple, set] = {}
    rows = session.execute(
        select(AuditEntry.entity_type, AuditEntry.entity_ref, AuditEntry.field).where(
            AuditEntry.country_id == country_id,
            AuditEntry.provenance == "manual_override",
            AuditEntry.status == "applied",
        )
    )
    for entity_type, ref, field in rows:
        out.setdefault((entity_type, ref), set()).add(field)
    return out



def compute_changes(session: Session, country: Country, parsed: dict, *, mode: str, source: str = "excel") -> Changes:
    """What applying ``parsed`` in ``mode`` would do. Reads only."""
    changes = Changes(mode=mode)
    full_refresh = mode == "replace"
    node_fields = NODE_FIELDS if source == "excel" else SYNC_NODE_FIELDS
    hand_made = standing_hand_changes(session, country.id)

    # Retired rows are included here on purpose: a facility that comes back must be
    # restored, not re-created beside its own ghost.
    all_nodes = list(session.scalars(select(Node).where(Node.country_id == country.id).execution_options(**{INCLUDE_RETIRED: True})))
    all_products = list(session.scalars(select(Product).where(Product.country_id == country.id).execution_options(**{INCLUDE_RETIRED: True})))
    all_edges = list(session.scalars(select(Edge).where(Edge.country_id == country.id).execution_options(**{INCLUDE_RETIRED: True})))
    all_demand = list(session.scalars(select(Demand).where(Demand.country_id == country.id).execution_options(**{INCLUDE_RETIRED: True})))

    # ---- nodes
    incoming_nodes = parsed.get("nodes") or []
    matching = reconcile(incoming_nodes, all_nodes, source)
    changes.collisions = matching.collisions
    match_by_code = {m.incoming_code: m for m in matching.matched}
    node_by_id = {n.id: n for n in all_nodes}
    seen_ids = set()
    node_code_map: Dict[str, str] = {}  # incoming code -> existing code, for demand/edge keys

    for record in incoming_nodes:
        code = str(record.get("code") or "")
        match = match_by_code.get(code)
        if match is None or match.existing_id is None:
            changes.nodes.adds.append(RowChange(code, record.get("name") or code, "add", record=record))
            node_code_map[code] = code
            continue
        node = node_by_id[match.existing_id]
        seen_ids.add(node.id)
        node_code_map[code] = node.code
        updates, conflicts = classify(record, node, node_fields, node.last_import, hand_made.get(("node", node.code), ()))
        row = RowChange(node.code, node.name, "update", updates, conflicts, record, node.id, match.method)
        if node.retired_at is not None:
            row.kind = "restore"
            changes.nodes.restores.append(row)
        elif conflicts:
            row.kind = "conflict"
            changes.nodes.conflicts.append(row)
        elif updates:
            changes.nodes.updates.append(row)
        else:
            row.kind = "unchanged"
            changes.nodes.unchanged_rows.append(row)

    if full_refresh:
        for node in all_nodes:
            if node.retired_at is None and node.id not in seen_ids:
                changes.nodes.retires.append(RowChange(node.code, node.name, "retire", existing_id=node.id))

    # ---- products
    product_by_sku = {p.sku: p for p in all_products}
    seen_skus = set()
    for record in parsed.get("products") or []:
        sku = str(record.get("sku") or "")
        product = product_by_sku.get(sku)
        if product is None:
            changes.products.adds.append(RowChange(sku, record.get("name") or sku, "add", record=record))
            continue
        seen_skus.add(sku)
        updates, conflicts = classify(record, product, PRODUCT_FIELDS, product.last_import, hand_made.get(("product", sku), ()))
        row = RowChange(sku, product.name, "update", updates, conflicts, record, product.id)
        if product.retired_at is not None:
            row.kind = "restore"
            changes.products.restores.append(row)
        elif conflicts:
            row.kind = "conflict"
            changes.products.conflicts.append(row)
        elif updates:
            changes.products.updates.append(row)
        else:
            row.kind = "unchanged"
            changes.products.unchanged_rows.append(row)
    if full_refresh and (parsed.get("products") or []):
        for product in all_products:
            if product.retired_at is None and product.sku not in seen_skus:
                changes.products.retires.append(RowChange(product.sku, product.name, "retire", existing_id=product.id))

    # ---- edges (a sync never supplies lanes; a workbook without an Edges sheet leaves them alone)
    incoming_edges = parsed.get("edges")
    if incoming_edges is not None and (incoming_edges or full_refresh):
        edge_by_code = {e.code: e for e in all_edges}
        seen_edges = set()
        for record in incoming_edges:
            code = record.get("code") or f"{record.get('from_node')}-{record.get('to_node')}"
            record = {**record, "code": code}
            if not states_distance(record):
                # A blank or zero distance in the file means "work it out", not "zero".
                # The cascade's answer is the tool's, and never a change the file made.
                record.pop("distance_km", None)
                record.pop("base_travel_time_hr", None)
            edge = edge_by_code.get(code)
            label = record.get("service_name") or f"{record.get('from_node')} → {record.get('to_node')}"
            if edge is None:
                changes.edges.adds.append(RowChange(code, label, "add", record=record))
                continue
            seen_edges.add(code)
            updates, conflicts = classify(record, edge, EDGE_FIELDS, edge.last_import, hand_made.get(("edge", code), ()))
            endpoints_moved = (
                node_code_map.get(record.get("from_node"), record.get("from_node")) != edge.from_node.code
                or node_code_map.get(record.get("to_node"), record.get("to_node")) != edge.to_node.code
            )
            if endpoints_moved:
                updates["endpoints"] = {
                    "from": f"{edge.from_node.code} → {edge.to_node.code}",
                    "to": f"{record.get('from_node')} → {record.get('to_node')}",
                }
            row = RowChange(code, edge.service_name or label, "update", updates, conflicts, record, edge.id)
            if edge.retired_at is not None:
                row.kind = "restore"
                changes.edges.restores.append(row)
            elif conflicts:
                row.kind = "conflict"
                changes.edges.conflicts.append(row)
            elif updates:
                changes.edges.updates.append(row)
            else:
                row.kind = "unchanged"
                changes.edges.unchanged_rows.append(row)
        if full_refresh:
            for edge in all_edges:
                if edge.retired_at is None and edge.code not in seen_edges:
                    changes.edges.retires.append(
                        RowChange(edge.code, edge.service_name or edge.code, "retire", existing_id=edge.id)
                    )

    # ---- demand, keyed by facility / product / period
    node_code_by_id = {n.id: n.code for n in all_nodes}
    sku_by_id = {p.id: p.sku for p in all_products}
    demand_by_key = {
        f"{node_code_by_id.get(d.node_id)}/{sku_by_id.get(d.product_id)}/{d.period}": d for d in all_demand
    }
    seen_demand = set()
    for record in parsed.get("demand") or []:
        node_code = node_code_map.get(record.get("node"), record.get("node"))
        key = f"{node_code}/{record.get('product')}/{record.get('period', 0) or 0}"
        row_existing = demand_by_key.get(key)
        if row_existing is None:
            changes.demand.adds.append(RowChange(key, key, "add", record=record))
            continue
        seen_demand.add(key)
        updates, conflicts = classify(record, row_existing, DEMAND_FIELDS, row_existing.last_import, hand_made.get(("demand", key), ()))
        row = RowChange(key, key, "update", updates, conflicts, record, row_existing.id)
        if row_existing.retired_at is not None:
            row.kind = "restore"
            changes.demand.restores.append(row)
        elif conflicts:
            row.kind = "conflict"
            changes.demand.conflicts.append(row)
        elif updates:
            changes.demand.updates.append(row)
        else:
            row.kind = "unchanged"
            changes.demand.unchanged_rows.append(row)
    if full_refresh and (parsed.get("demand") or []):
        for key, row_existing in demand_by_key.items():
            if row_existing.retired_at is None and key not in seen_demand:
                changes.demand.retires.append(RowChange(key, key, "retire", existing_id=row_existing.id))
    elif not full_refresh:
        # A merge replaces demand only for the facility-and-product pairs it supplies.
        pass

    return changes
