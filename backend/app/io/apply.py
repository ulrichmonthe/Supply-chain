"""Applying a validated payload to a country, from either a workbook or a connector.

Two modes, and the difference matters enough to be a deliberate choice at the point
of commit rather than a default buried in a connector.

``replace`` wipes the country's network and reloads it. This is the honest behaviour
for a master-list refresh from a workbook: a merge that silently keeps orphaned
facilities from last year's list is how a model drifts away from reality without
anybody noticing.

``merge`` upserts facilities, adds products it has not seen, replaces demand only for
the facility-and-product pairs actually supplied, and **never touches lanes**. This is
the right behaviour for a connector sync, because an LMIS has no view of the transport
network. A sync that dropped the boat timetables because DHIS2 does not know about
boats would destroy the part of the model that is hardest to rebuild.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..connectors.reconcile import TRACKED_FIELDS, reconcile
from ..engine.distance import resolve_distance
from ..models import AuditEntry, Country, Demand, Edge, Node, Product

MODES = ("replace", "merge")


def apply_payload(
    session: Session,
    country: Country,
    parsed: dict,
    *,
    mode: str = "replace",
    source: str = "excel",
    reference: str = "",
    actor: str = "analyst",
) -> dict:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {mode!r}")

    if mode == "replace":
        counts = _replace(session, country, parsed)
    else:
        counts = _merge(session, country, parsed, source=source)

    session.add(
        AuditEntry(
            country_id=country.id,
            entity_type="global",
            entity_ref=reference or source,
            field=f"{source}_{mode}",
            new_value=(
                f"{counts['nodes_created']} facilities created, "
                f"{counts['nodes_updated']} updated, "
                f"{counts['demand_rows']} demand rows, "
                f"{counts['edges']} lanes"
            ),
            provenance="import" if source == "excel" else "lmis_sync",
            confidence_marker="S",
            rationale=(
                f"Applied from {source} in {mode} mode"
                + (f" ({reference})" if reference else "")
                + (
                    ". Lanes were not touched: the source system has no view of the transport "
                    "network."
                    if mode == "merge"
                    else "."
                )
            ),
            actor=actor,
        )
    )
    return counts


# --- replace ---------------------------------------------------------------------------


def _replace(session: Session, country: Country, parsed: dict) -> dict:
    for model in (Demand, Edge, Node, Product):
        for row in session.scalars(select(model).where(model.country_id == country.id)):
            session.delete(row)
    session.flush()

    nodes: Dict[str, Node] = {}
    for record in parsed.get("nodes", []):
        node = _new_node(country, record)
        session.add(node)
        nodes[record["code"]] = node

    products: Dict[str, Product] = {}
    for record in parsed.get("products", []):
        product = _new_product(country, record)
        session.add(product)
        products[record["sku"]] = product
    session.flush()

    edges = _apply_edges(session, country, parsed.get("edges", []), nodes)
    demand_rows = _apply_demand(session, country, parsed.get("demand", []), nodes, products, replace_all=True)

    return {
        "nodes_created": len(nodes),
        "nodes_updated": 0,
        "products_created": len(products),
        "products_updated": 0,
        "edges": edges,
        "demand_rows": demand_rows,
        "distances_computed": edges,
    }


# --- merge -----------------------------------------------------------------------------


def _merge(session: Session, country: Country, parsed: dict, *, source: str) -> dict:
    existing_nodes = list(session.scalars(select(Node).where(Node.country_id == country.id)))
    existing_products = {
        p.sku: p for p in session.scalars(select(Product).where(Product.country_id == country.id))
    }

    incoming_nodes = parsed.get("nodes", [])
    matching = reconcile(incoming_nodes, existing_nodes, source)
    by_incoming_code = {m.incoming_code: m for m in matching.matched}
    node_by_id = {n.id: n for n in existing_nodes}

    nodes: Dict[str, Node] = {n.code: n for n in existing_nodes}
    created = 0
    updated = 0

    for record in incoming_nodes:
        match = by_incoming_code.get(record["code"])
        if match is None or match.existing_id is None:
            node = _new_node(country, record)
            session.add(node)
            nodes[record["code"]] = node
            created += 1
            continue

        node = node_by_id[match.existing_id]
        touched = False
        for field_name in TRACKED_FIELDS:
            value = record.get(field_name)
            if value not in (None, "") and getattr(node, field_name, None) != value:
                setattr(node, field_name, value)
                touched = True

        # The source system's own key is written back, so the next sync matches on it
        # directly rather than re-deriving the match from a name.
        merged_ids = dict(node.external_ids or {})
        for key, value in (record.get("external_ids") or {}).items():
            if value and merged_ids.get(key) != value:
                merged_ids[key] = value
                touched = True
        if touched:
            node.external_ids = merged_ids
            if record.get("lat") is not None and record.get("lon") is not None:
                node.geocode_source = record.get("geocode_source") or node.geocode_source
                node.geocode_confidence = max(
                    node.geocode_confidence, float(record.get("geocode_confidence") or 0.0)
                )
            updated += 1
        nodes[node.code] = node
        # A facility may be known by a different code upstream; index both so demand
        # rows keyed either way still land.
        nodes[record["code"]] = node

    products = dict(existing_products)
    products_created = 0
    for record in parsed.get("products", []):
        sku = record["sku"]
        if sku in products:
            continue
        product = _new_product(country, record)
        session.add(product)
        products[sku] = product
        products_created += 1
    session.flush()

    # Lanes are never merged in from a connector; only a workbook supplies them.
    edges = _apply_edges(session, country, parsed.get("edges", []), nodes) if parsed.get("edges") else 0
    demand_rows = _apply_demand(session, country, parsed.get("demand", []), nodes, products, replace_all=False)

    return {
        "nodes_created": created,
        "nodes_updated": updated,
        "products_created": products_created,
        "products_updated": 0,
        "edges": edges,
        "demand_rows": demand_rows,
        "distances_computed": edges,
    }


# --- shared -----------------------------------------------------------------------------


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


def _apply_edges(session: Session, country: Country, records: List[dict], nodes: Dict[str, Node]) -> int:
    applied = 0
    for record in records:
        origin = nodes.get(record.get("from_node"))
        destination = nodes.get(record.get("to_node"))
        if not origin or not destination:
            continue

        manual_km = record.get("distance_km") if record.get("distance_method") == "manual" else None
        if record.get("distance_km") and not record.get("distance_method"):
            manual_km = record["distance_km"]

        resolved = resolve_distance(
            origin.lat,
            origin.lon,
            destination.lat,
            destination.lon,
            mode=record.get("mode", "road"),
            terrain_class=destination.terrain_class,
            manual_km=manual_km,
            manual_hours=record.get("base_travel_time_hr") or None,
            manual_note=record.get("distance_note") or "",
        )
        session.add(
            Edge(
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
        )
        applied += 1
    return applied


def _apply_demand(
    session: Session,
    country: Country,
    records: List[dict],
    nodes: Dict[str, Node],
    products: Dict[str, Product],
    *,
    replace_all: bool,
) -> int:
    if not replace_all and records:
        # Clear only what this payload supplies. A pull covering one programme must
        # not silently delete the consumption history of every other programme.
        session.flush()
        touched = {
            (nodes[r["node"]].id, products[r["product"]].id, r.get("period", 0))
            for r in records
            if r.get("node") in nodes and r.get("product") in products
        }
        if touched:
            for row in session.scalars(select(Demand).where(Demand.country_id == country.id)):
                if (row.node_id, row.product_id, row.period) in touched:
                    session.delete(row)
            session.flush()

    applied = 0
    for record in records:
        node = nodes.get(record.get("node"))
        product = products.get(record.get("product"))
        if not node or not product:
            continue
        session.add(
            Demand(
                country_id=country.id,
                node_id=node.id,
                product_id=product.id,
                period=record.get("period", 0),
                quantity=record.get("quantity") or 0.0,
                source=record.get("source", "proxy"),
                confidence=record.get("confidence", 0.5),
            )
        )
        applied += 1
    return applied
