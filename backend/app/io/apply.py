"""Applying a validated payload to a country, from either a workbook or a connector.

Two modes, and the difference matters enough to be a deliberate choice at the point
of commit rather than a default buried in a connector.

``replace`` is a full refresh from a master list: everything in the file is applied,
and anything in the model that the file no longer mentions is *retired* -- kept, with
its history, invisible to the solver, restorable. Nothing is deleted. A merge that
silently kept orphaned facilities from last year's list is how a model drifts from
reality; a refresh that silently destroyed a coordinate somebody corrected by hand is
how a model loses the trust of the people who corrected it. Retiring is the honest
middle.

``merge`` upserts what the payload supplies and leaves the rest alone. It is the
right behaviour for a connector sync, because an LMIS has no view of the transport
network: a sync that dropped the boat timetables because DHIS2 does not know about
boats would destroy the part of the model that is hardest to rebuild. Lanes are
never merged in from a connector.

Both modes go through the same diff (``io/diff.py``). Where the file and a hand
correction disagree, that is a conflict: the correction wins unless the person
applying says otherwise, and either way the ledger records the choice.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import ledger
from ..db import INCLUDE_RETIRED
from ..engine.distance import resolve_distance
from ..models import AuditEntry, Country, Demand, Edge, Node, Product
from .diff import (
    DEMAND_FIELDS,
    EDGE_FIELDS,
    NODE_FIELDS,
    PRODUCT_FIELDS,
    SYNC_NODE_FIELDS,
    Changes,
    RowChange,
    compute_changes,
    states_distance,
    supplied,
)

MODES = ("replace", "merge")
#: What to do where the file disagrees with a correction made in the tool.
CONFLICT_POLICIES = ("keep", "take_file")


def apply_payload(
    session: Session,
    country: Country,
    parsed: dict,
    *,
    mode: str = "replace",
    source: str = "excel",
    reference: str = "",
    actor: str = "analyst",
    author_claim: str = "anonymous",
    batch_id: Optional[str] = None,
    conflict_policy: str = "keep",
    take_file: Iterable[str] = (),
) -> dict:
    """Diff, then apply. ``take_file`` names individual conflicts ("nodes:FAC-1:lat") to
    resolve in the file's favour regardless of ``conflict_policy``."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
    if conflict_policy not in CONFLICT_POLICIES:
        raise ValueError(f"conflict_policy must be one of {CONFLICT_POLICIES}, not {conflict_policy!r}")

    changes = compute_changes(session, country, parsed, mode=mode, source=source)
    return apply_changes(
        session,
        country,
        changes,
        source=source,
        reference=reference,
        actor=actor,
        author_claim=author_claim,
        batch_id=batch_id,
        conflict_policy=conflict_policy,
        take_file=set(take_file),
    )


def apply_changes(
    session: Session,
    country: Country,
    changes: Changes,
    *,
    source: str,
    reference: str = "",
    actor: str = "analyst",
    author_claim: str = "anonymous",
    batch_id: Optional[str] = None,
    conflict_policy: str = "keep",
    take_file: set = frozenset(),
) -> dict:
    now = datetime.now(timezone.utc)
    reason = f"Not in {reference or source}"
    common = dict(country_id=country.id, actor=actor, author_claim=author_claim, batch_id=batch_id)
    provenance = "import" if source == "excel" else "lmis_sync"
    node_fields = NODE_FIELDS if source == "excel" else SYNC_NODE_FIELDS

    resolved_conflicts = {"kept": 0, "took_file": 0}

    def resolve(sheet: str, row: RowChange, target, fields) -> None:
        """Apply a matched row: updates outright, conflicts by policy, and remember the file."""
        entity_type = sheet[:-1] if sheet != "demand" else "demand"
        for name, change in row.fields.items():
            if name == "endpoints":
                continue
            setattr(target, name, _merged(getattr(target, name, None), change["to"]))
        for name, conflict in row.conflicts.items():
            take = conflict_policy == "take_file" or f"{sheet}:{row.key}:{name}" in take_file
            if take:
                setattr(target, name, _merged(getattr(target, name, None), conflict["file"]))
                resolved_conflicts["took_file"] += 1
                # The correction was overridden on purpose, so it no longer stands: it
                # stops counting as a conflict next time and cannot be reverted into
                # the middle of a later import. It stays in the ledger, marked.
                session.execute(
                    update(AuditEntry)
                    .where(
                        AuditEntry.country_id == country.id,
                        AuditEntry.entity_type == entity_type,
                        AuditEntry.entity_ref == row.key,
                        AuditEntry.field == name,
                        AuditEntry.provenance == "manual_override",
                        AuditEntry.status == "applied",
                    )
                    .values(status="superseded")
                )
                rationale = (
                    f"Took the file's value over the one corrected in the tool "
                    f"(the tool had {ledger.render(conflict['model'])})."
                )
                old, new = conflict["model"], conflict["file"]
            else:
                resolved_conflicts["kept"] += 1
                rationale = (
                    f"Kept the value corrected in the tool; the file said "
                    f"{ledger.render(conflict['file'])}."
                )
                old, new = conflict["model"], conflict["model"]
            ledger.record(
                session,
                entity_type=entity_type,
                entity_ref=row.key,
                field=name,
                old_value=old,
                new_value=new,
                provenance=provenance,
                confidence_marker="S" if take else "I",
                rationale=rationale,
                **common,
            )
        target.last_import = _remember(row.record or {}, fields, existing=target.last_import)

    # ---- nodes ---------------------------------------------------------------------
    nodes: Dict[str, Node] = {}
    node_by_id = {
        n.id: n
        for n in session.scalars(
            select(Node).where(Node.country_id == country.id).execution_options(**{INCLUDE_RETIRED: True})
        )
    }
    for node in node_by_id.values():
        nodes[node.code] = node

    for row in changes.nodes.adds:
        node = _new_node(country, row.record)
        node.last_import = _remember(row.record, node_fields)
        session.add(node)
        nodes[row.key] = node
    for row in changes.nodes.updates + changes.nodes.conflicts:
        node = node_by_id[row.existing_id]
        resolve("nodes", row, node, node_fields)
        nodes[row.record.get("code")] = node
        if row.fields or row.conflicts:
            _note_update(session, "node", node.code, row, provenance, common)
    for row in changes.nodes.unchanged_rows:
        node = node_by_id[row.existing_id]
        node.last_import = _remember(row.record or {}, node_fields, existing=node.last_import)
        nodes[row.record.get("code")] = node
    # The source system's own key is written back to every facility it matched, so
    # the next sync matches on it directly rather than re-deriving the match from a
    # name -- and a facility renamed upstream is still the same facility here.
    for row in changes.nodes.matched:
        node = node_by_id[row.existing_id]
        merged = dict(node.external_ids or {})
        changed = False
        for key, value in ((row.record or {}).get("external_ids") or {}).items():
            if value and merged.get(key) != value:
                merged[key] = value
                changed = True
        if changed:
            node.external_ids = merged
    for row in changes.nodes.restores:
        node = node_by_id[row.existing_id]
        node.retired_at = None
        node.retired_reason = ""
        resolve("nodes", row, node, node_fields)
        nodes[row.record.get("code")] = node
        ledger.record(session, entity_type="node", entity_ref=node.code, field="restored",
                      new_value=f"Back in {reference or source}", provenance=provenance, **common)
    for row in changes.nodes.retires:
        retire_node(session, node_by_id[row.existing_id], reason=reason, when=now, provenance=provenance, **common)
    session.flush()

    # ---- products ------------------------------------------------------------------
    products: Dict[str, Product] = {
        p.sku: p
        for p in session.scalars(
            select(Product).where(Product.country_id == country.id).execution_options(**{INCLUDE_RETIRED: True})
        )
    }
    for row in changes.products.adds:
        product = _new_product(country, row.record)
        product.last_import = _remember(row.record, PRODUCT_FIELDS)
        session.add(product)
        products[row.key] = product
    for row in changes.products.updates + changes.products.conflicts:
        resolve("products", row, products[row.key], PRODUCT_FIELDS)
    for row in changes.products.restores:
        product = products[row.key]
        product.retired_at = None
        product.retired_reason = ""
        resolve("products", row, product, PRODUCT_FIELDS)
    for row in changes.products.retires:
        product = products[row.key]
        product.retired_at, product.retired_reason = now, reason
        ledger.record(session, entity_type="product", entity_ref=product.sku, field="retired",
                      old_value=product.name, new_value=reason, provenance=provenance, **common)
    session.flush()

    # ---- edges ---------------------------------------------------------------------
    edge_by_id = {
        e.id: e
        for e in session.scalars(
            select(Edge).where(Edge.country_id == country.id).execution_options(**{INCLUDE_RETIRED: True})
        )
    }
    distances = 0
    for row in changes.edges.adds:
        edge = _new_edge(country, row.record, nodes)
        if edge is None:
            continue
        edge.last_import = _remember(row.record, EDGE_FIELDS)
        session.add(edge)
        distances += 1
    for row in changes.edges.updates + changes.edges.conflicts + changes.edges.restores:
        edge = edge_by_id[row.existing_id]
        if row.kind == "restore":
            edge.retired_at, edge.retired_reason = None, ""
            ledger.record(session, entity_type="edge", entity_ref=edge.code, field="restored",
                          new_value=f"Back in {reference or source}", provenance=provenance, **common)
        moved = "endpoints" in row.fields
        if moved:
            origin = nodes.get(row.record.get("from_node"))
            destination = nodes.get(row.record.get("to_node"))
            if origin and destination:
                edge.from_node_id, edge.to_node_id = origin.id, destination.id
        resolve("edges", row, edge, EDGE_FIELDS)
        if (moved or "mode" in row.fields) and edge.distance_method != "manual":
            origin = nodes.get(row.record.get("from_node")) or edge.from_node
            destination = nodes.get(row.record.get("to_node")) or edge.to_node
            resolved = resolve_distance(
                origin.lat, origin.lon, destination.lat, destination.lon,
                mode=edge.mode, terrain_class=destination.terrain_class, config=country.config or {},
            )
            for key, value in resolved.as_edge_fields().items():
                setattr(edge, key, value)
            distances += 1
        if row.fields or row.conflicts:
            _note_update(session, "edge", edge.code, row, provenance, common)
    for row in changes.edges.retires:
        edge = edge_by_id[row.existing_id]
        edge.retired_at, edge.retired_reason = now, reason
        ledger.record(session, entity_type="edge", entity_ref=edge.code, field="retired",
                      old_value=edge.service_name or edge.code, new_value=reason, provenance=provenance, **common)
    session.flush()

    # ---- demand --------------------------------------------------------------------
    demand_by_id = {
        d.id: d
        for d in session.scalars(
            select(Demand).where(Demand.country_id == country.id).execution_options(**{INCLUDE_RETIRED: True})
        )
    }
    demand_rows = 0
    for row in changes.demand.adds:
        node = nodes.get(row.record.get("node"))
        product = products.get(row.record.get("product"))
        if not node or not product:
            continue
        demand = Demand(
            country_id=country.id,
            node_id=node.id,
            product_id=product.id,
            period=row.record.get("period", 0) or 0,
            quantity=row.record.get("quantity") or 0.0,
            source=row.record.get("source", "proxy"),
            confidence=row.record.get("confidence", 0.5),
            last_import=_remember(row.record, DEMAND_FIELDS),
        )
        session.add(demand)
        demand_rows += 1
    for row in changes.demand.updates + changes.demand.conflicts + changes.demand.restores:
        demand = demand_by_id[row.existing_id]
        if row.kind == "restore":
            demand.retired_at, demand.retired_reason = None, ""
        resolve("demand", row, demand, DEMAND_FIELDS)
        demand_rows += 1
    for row in changes.demand.retires:
        demand = demand_by_id[row.existing_id]
        demand.retired_at, demand.retired_reason = now, reason
    session.flush()

    counts = {
        "nodes_created": len(changes.nodes.adds),
        "nodes_updated": len(changes.nodes.updates) + len(changes.nodes.conflicts) + len(changes.nodes.restores),
        "nodes_retired": len(changes.nodes.retires),
        "nodes_restored": len(changes.nodes.restores),
        "products_created": len(changes.products.adds),
        "products_updated": len(changes.products.updates) + len(changes.products.conflicts),
        "edges": len(changes.edges.adds) + len(changes.edges.updates) + len(changes.edges.conflicts) + len(changes.edges.restores),
        "edges_retired": len(changes.edges.retires),
        "demand_rows": demand_rows,
        "demand_retired": len(changes.demand.retires),
        "distances_computed": distances,
        "conflicts": {"total": changes.conflict_count(), **resolved_conflicts},
    }

    ledger.record(
        session,
        entity_type="global",
        entity_ref=reference or source,
        field=f"{source}_{changes.mode}",
        new_value=(
            f"{counts['nodes_created']} facilities created, {counts['nodes_updated']} updated, "
            f"{counts['nodes_retired']} retired, {counts['demand_rows']} demand rows, {counts['edges']} lanes"
        ),
        provenance=provenance,
        confidence_marker="S",
        rationale=(
            f"Applied from {source} in {changes.mode} mode"
            + (f" ({reference})" if reference else "")
            + (
                ". Lanes were not touched: the source system has no view of the transport network."
                if changes.mode == "merge"
                else ". Nothing was deleted: rows the file no longer mentions were retired."
            )
            + (
                f" {counts['conflicts']['kept']} corrections made in the tool were kept over the file"
                f" and {counts['conflicts']['took_file']} were overridden by it."
                if changes.conflict_count()
                else ""
            )
        ),
        **common,
    )
    return counts


# --- retiring and restoring, shared with the editing endpoints --------------------------


def retire_node(
    session: Session,
    node: Node,
    *,
    reason: str,
    when: Optional[datetime] = None,
    provenance: str = "manual_override",
    **common,
) -> dict:
    """Retire a facility and everything that only makes sense with it: its lanes and
    its demand. Returns what was retired, for the response and the ledger."""
    when = when or datetime.now(timezone.utc)
    node.retired_at, node.retired_reason = when, reason
    lanes = list(
        session.scalars(
            select(Edge)
            .where(Edge.country_id == node.country_id, (Edge.from_node_id == node.id) | (Edge.to_node_id == node.id))
        )
    )
    for edge in lanes:
        edge.retired_at, edge.retired_reason = when, f"{reason} (its facility {node.code} was retired)"
    demand = list(session.scalars(select(Demand).where(Demand.node_id == node.id)))
    for row in demand:
        row.retired_at, row.retired_reason = when, f"{reason} (its facility {node.code} was retired)"
    ledger.record(
        session,
        entity_type="node",
        entity_ref=node.code,
        field="retired",
        old_value=node.name,
        new_value=reason,
        provenance=provenance,
        rationale=f"With it: {len(lanes)} lanes and {len(demand)} demand rows.",
        **common,
    )
    return {"lanes": len(lanes), "demand_rows": len(demand)}


def restore_node(session: Session, node: Node, *, reason: str, provenance: str = "manual_override", **common) -> dict:
    """Bring a retired facility back, with the lanes and demand retired alongside it."""
    stamp = node.retired_at
    node.retired_at, node.retired_reason = None, ""
    lanes = demand = 0
    if stamp is not None:
        for edge in session.scalars(
            select(Edge)
            .where(Edge.country_id == node.country_id, (Edge.from_node_id == node.id) | (Edge.to_node_id == node.id))
            .execution_options(**{INCLUDE_RETIRED: True})
        ):
            if edge.retired_at == stamp:
                edge.retired_at, edge.retired_reason = None, ""
                lanes += 1
        for row in session.scalars(
            select(Demand).where(Demand.node_id == node.id).execution_options(**{INCLUDE_RETIRED: True})
        ):
            if row.retired_at == stamp:
                row.retired_at, row.retired_reason = None, ""
                demand += 1
    ledger.record(
        session,
        entity_type="node",
        entity_ref=node.code,
        field="restored",
        new_value=reason,
        provenance=provenance,
        rationale=f"With it: {lanes} lanes and {demand} demand rows.",
        **common,
    )
    return {"lanes": lanes, "demand_rows": demand}


# --- helpers ----------------------------------------------------------------------------


def _merged(current: Any, incoming: Any) -> Any:
    """A dict from the file lands on top of the model's, so keys the sheet has no column
    for survive; anything else replaces outright."""
    if isinstance(current, dict) and isinstance(incoming, dict):
        return {**current, **incoming}
    return incoming


def _remember(record: dict, fields: Iterable[str], existing: Optional[dict] = None) -> dict:
    """The file's values for the tracked fields, on top of what was remembered before."""
    remembered = dict(existing or {})
    for name in fields:
        if supplied(record, name):
            remembered[name] = record[name]
    return remembered


def _note_update(session: Session, entity_type: str, ref: str, row: RowChange, provenance: str, common: dict) -> None:
    """One ledger row per updated entity naming its changed fields -- not one per field,
    which for a master-list refresh would be thousands of rows nobody reads."""
    if not row.fields:
        return
    ledger.record(
        session,
        entity_type=entity_type,
        entity_ref=ref,
        field="updated",
        old_value={k: v["from"] for k, v in row.fields.items()},
        new_value={k: v["to"] for k, v in row.fields.items()},
        provenance=provenance,
        confidence_marker="S",
        rationale=f"Fields taken from the file: {', '.join(sorted(row.fields))}.",
        **common,
    )


def _new_node(country: Country, record: dict) -> Node:
    return Node(
        country_id=country.id,
        code=record["code"],
        name=record["name"],
        level=record.get("level", 3),
        type=record.get("type", "health_facility"),
        lat=record.get("lat"),
        lon=record.get("lon"),
        geocode_confidence=record.get("geocode_confidence", 0.5),
        geocode_source=record.get("geocode_source", "import"),
        admin1=record.get("admin1"),
        admin2=record.get("admin2"),
        terrain_class=record.get("terrain_class", "mainland_road"),
        catchment_population=record.get("catchment_population", 0.0),
        operating_status=record.get("operating_status", "operational"),
        capacity=record.get("capacity") or {},
        hub_capable=record.get("hub_capable", False),
        hub_fixed_cost=record.get("hub_fixed_cost", 0.0),
        hub_open_capex=record.get("hub_open_capex", 0.0),
        hub_throughput_m3=record.get("hub_throughput_m3", 0.0),
        external_ids=record.get("external_ids") or {},
    )


def _new_product(country: Country, record: dict) -> Product:
    return Product(
        country_id=country.id,
        sku=record["sku"],
        name=record.get("name") or record["sku"],
        temperature_band=record.get("temperature_band", "ambient"),
        volume_per_unit_cm3=record.get("volume_per_unit_cm3") or 0.0,
        unit_cost=record.get("unit_cost") or 0.0,
        shelf_life_days=record.get("shelf_life_days") or 730,
    )


def _new_edge(country: Country, record: dict, nodes: Dict[str, Node]) -> Optional[Edge]:
    origin = nodes.get(record.get("from_node"))
    destination = nodes.get(record.get("to_node"))
    if not origin or not destination:
        return None

    manual_km = record.get("distance_km") if states_distance(record) else None

    resolved = resolve_distance(
        origin.lat,
        origin.lon,
        destination.lat,
        destination.lon,
        mode=record.get("mode", "road"),
        terrain_class=destination.terrain_class,
        manual_km=manual_km,
        manual_hours=record.get("base_travel_time_hr") or None,
        config=country.config or {},
        manual_note=record.get("distance_note") or "",
    )
    return Edge(
        country_id=country.id,
        code=record.get("code") or f"{record.get('from_node')}-{record.get('to_node')}",
        from_node_id=origin.id,
        to_node_id=destination.id,
        mode=record.get("mode", "road"),
        service_name=record.get("service_name"),
        service_frequency=record.get("service_frequency"),
        service_days=record.get("service_days"),
        capacity_per_trip_m3=record.get("capacity_per_trip_m3", 0.0),
        cold_capacity_per_trip_m3=record.get("cold_capacity_per_trip_m3", 0.0),
        fixed_cost_per_trip=record.get("fixed_cost_per_trip", 0.0),
        variable_cost_per_km=record.get("variable_cost_per_km", 0.0),
        cost_per_m3=record.get("cost_per_m3", 0.0),
        monthly_access=record.get("monthly_access") or [1.0] * 12,
        monthly_cost_multiplier=record.get("monthly_cost_multiplier") or [1.0] * 12,
        reliability=record.get("reliability", 0.9),
        lead_time_sd_days=record.get("lead_time_sd_days", 2.0),
        active=record.get("active", True),
        **resolved.as_edge_fields(),
    )
