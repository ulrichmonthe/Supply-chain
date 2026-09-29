"""The table editor: every model table, every row, every column a person can defend.

Facilities already had an editor of their own. This gives lanes and products the same
treatment, lets every table be listed with its retired rows, and adds the one thing
a grid needs that a form does not: setting one field across many rows at once, with
one ledger row per row so nothing is applied silently.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import estimators, ledger
from ..db import INCLUDE_RETIRED, get_session
from ..engine.distance import resolve_distance
from ..io.apply import restore_node, retire_node
from ..models import Country, Demand, Edge, Node, Product
from ..schemas import BulkSet, DemandLine, DemandOut, DemandSet, EdgeCreate, EdgeOut, NodeOut, ProductIn, ProductOut, ProductPatch, RetireIn
from .deps import author_claim, new_batch_id
from .editing import set_node_demand

router = APIRouter(tags=["tables"])

TABLES = ("nodes", "edges", "products", "demand")


def _country_or_404(session: Session, country_id: int) -> Country:
    country = session.get(Country, country_id)
    if not country:
        raise HTTPException(404, f"No country with id {country_id}.")
    return country


def _edge_out(edge: Edge) -> dict:
    out = EdgeOut(
        **{key: getattr(edge, key) for key in EdgeOut.model_fields if hasattr(edge, key)},
        from_code=edge.from_node.code,
        to_code=edge.to_node.code,
        from_lat=edge.from_node.lat,
        from_lon=edge.from_node.lon,
        to_lat=edge.to_node.lat,
        to_lon=edge.to_node.lon,
    ).model_dump()
    out["retired_at"] = edge.retired_at
    out["retired_reason"] = edge.retired_reason
    return out


def _product_out(product: Product) -> dict:
    out = ProductOut.model_validate(product).model_dump()
    out["retired_at"] = product.retired_at
    out["retired_reason"] = product.retired_reason
    return out


# --- listing --------------------------------------------------------------------------


@router.get("/countries/{country_id}/tables/{table}")
def list_table(country_id: int, table: str, session: Session = Depends(get_session)):
    """Every row of one table, retired rows included and marked, for the grid."""
    _country_or_404(session, country_id)
    if table not in TABLES:
        raise HTTPException(404, f"No table called {table}. Choose from {', '.join(TABLES)}.")
    opts = {INCLUDE_RETIRED: True}
    if table == "nodes":
        rows = session.scalars(select(Node).where(Node.country_id == country_id).order_by(Node.level, Node.code).execution_options(**opts))
        return [NodeOut.model_validate(n).model_dump() for n in rows]
    if table == "edges":
        rows = session.scalars(select(Edge).where(Edge.country_id == country_id).order_by(Edge.code).execution_options(**opts))
        return [_edge_out(e) for e in rows]
    if table == "products":
        rows = session.scalars(select(Product).where(Product.country_id == country_id).order_by(Product.sku).execution_options(**opts))
        return [_product_out(p) for p in rows]
    nodes = {n.id: n for n in session.scalars(select(Node).where(Node.country_id == country_id).execution_options(**opts))}
    products = {p.id: p for p in session.scalars(select(Product).where(Product.country_id == country_id).execution_options(**opts))}
    rows = session.scalars(select(Demand).where(Demand.country_id == country_id).order_by(Demand.node_id, Demand.product_id, Demand.period))
    out = []
    for row in rows:
        node, product = nodes.get(row.node_id), products.get(row.product_id)
        if not node or not product:
            continue
        out.append(
            DemandOut(
                id=row.id, node_id=node.id, node_code=node.code, sku=product.sku, product_name=product.name,
                period=row.period, quantity=row.quantity, source=row.source, confidence=row.confidence, derivation=row.derivation,
            ).model_dump()
            | {"node_name": node.name, "admin1": node.admin1}
        )
    return out


# --- lanes ------------------------------------------------------------------------------


@router.post("/countries/{country_id}/edges", status_code=201)
def create_edge(
    country_id: int,
    payload: EdgeCreate,
    session: Session = Depends(get_session),
    author: str = Depends(author_claim),
):
    """Add a lane between two facilities. Distance and time come from the cascade
    unless typed, and the ledger says which."""
    country = _country_or_404(session, country_id)
    code = " ".join(payload.code.split()).strip()
    if not code:
        raise HTTPException(400, "A lane needs a code.")
    if session.scalar(select(Edge).where(Edge.country_id == country_id, Edge.code == code).execution_options(**{INCLUDE_RETIRED: True})):
        raise HTTPException(409, f"A lane with code {code} already exists.")
    origin = session.scalar(select(Node).where(Node.country_id == country_id, Node.code == payload.from_code))
    target = session.scalar(select(Node).where(Node.country_id == country_id, Node.code == payload.to_code))
    if not origin or not target:
        raise HTTPException(404, "Both ends of a lane must be facilities in this country.")
    if origin.id == target.id:
        raise HTTPException(400, "A lane needs two different ends.")
    distance = resolve_distance(
        origin.lat, origin.lon, target.lat, target.lon,
        mode=payload.mode, terrain_class=target.terrain_class,
        manual_km=payload.distance_km, manual_hours=payload.base_travel_time_hr,
        manual_note=payload.reason, config=country.config,
    )
    edge = Edge(
        country_id=country_id, code=code, from_node_id=origin.id, to_node_id=target.id, mode=payload.mode,
        **distance.as_edge_fields(),
        service_name=payload.service_name, service_frequency=payload.service_frequency,
        capacity_per_trip_m3=payload.capacity_per_trip_m3, cold_capacity_per_trip_m3=payload.cold_capacity_per_trip_m3,
        fixed_cost_per_trip=payload.fixed_cost_per_trip, variable_cost_per_km=payload.variable_cost_per_km,
        cost_per_m3=payload.cost_per_m3, reliability=payload.reliability, active=True,
    )
    session.add(edge)
    session.flush()
    ledger.record(
        session, country_id=country_id, entity_type="edge", entity_ref=code, field="created",
        new_value=f"{origin.code} → {target.code} by {payload.mode}, {distance.distance_km:.0f} km ({distance.method})",
        provenance="manual_override", confidence_marker=payload.confidence_marker, rationale=payload.reason,
        author_claim=author, batch_id=new_batch_id(),
    )
    session.commit()
    session.refresh(edge)
    return _edge_out(edge)


def _retire_edge(session: Session, edge: Edge, *, reason: str, author: str) -> None:
    edge.retired_at, edge.retired_reason = datetime.now(timezone.utc), reason
    ledger.record(
        session, country_id=edge.country_id, entity_type="edge", entity_ref=edge.code, field="retired",
        old_value=f"{edge.from_node.code} → {edge.to_node.code}", new_value=reason,
        provenance="manual_override", author_claim=author, batch_id=new_batch_id(),
    )


@router.post("/edges/{edge_id}/retire")
def retire_edge(edge_id: int, payload: RetireIn, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    edge = session.get(Edge, edge_id)
    if not edge:
        raise HTTPException(404, f"No lane with id {edge_id}.")
    if edge.retired_at is not None:
        raise HTTPException(409, f"{edge.code} is already retired.")
    _retire_edge(session, edge, reason=payload.reason or "Retired in the tool.", author=author)
    session.commit()
    return {"retired": edge.code}


@router.post("/edges/{edge_id}/restore")
def restore_edge(edge_id: int, payload: RetireIn, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    edge = session.scalar(select(Edge).where(Edge.id == edge_id).execution_options(**{INCLUDE_RETIRED: True}))
    if not edge:
        raise HTTPException(404, f"No lane with id {edge_id}.")
    if edge.retired_at is None:
        raise HTTPException(409, f"{edge.code} is not retired.")
    edge.retired_at, edge.retired_reason = None, ""
    ledger.record(
        session, country_id=edge.country_id, entity_type="edge", entity_ref=edge.code, field="restored",
        new_value=payload.reason or "Restored in the tool.", provenance="manual_override", author_claim=author, batch_id=new_batch_id(),
    )
    session.commit()
    session.refresh(edge)
    return _edge_out(edge)


# --- products ---------------------------------------------------------------------------


@router.post("/countries/{country_id}/products", status_code=201)
def create_product(country_id: int, payload: ProductIn, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    _country_or_404(session, country_id)
    sku = " ".join(payload.sku.split()).strip()
    if not sku:
        raise HTTPException(400, "A product needs a SKU.")
    if session.scalar(select(Product).where(Product.country_id == country_id, Product.sku == sku).execution_options(**{INCLUDE_RETIRED: True})):
        raise HTTPException(409, f"A product with SKU {sku} already exists.")
    product = Product(
        country_id=country_id, sku=sku, name=payload.name, temperature_band=payload.temperature_band,
        volume_per_unit_cm3=payload.volume_per_unit_cm3, unit_cost=payload.unit_cost, shelf_life_days=payload.shelf_life_days,
    )
    session.add(product)
    session.flush()
    ledger.record(
        session, country_id=country_id, entity_type="product", entity_ref=sku, field="created",
        new_value=f"{payload.name}, {payload.temperature_band}, {payload.volume_per_unit_cm3:g} cm³",
        provenance="manual_override", confidence_marker=payload.confidence_marker, rationale=payload.reason,
        author_claim=author, batch_id=new_batch_id(),
    )
    session.commit()
    session.refresh(product)
    return _product_out(product)


@router.patch("/products/{product_id}")
def update_product(product_id: int, payload: ProductPatch, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(404, f"No product with id {product_id}.")
    changes = payload.model_dump(exclude_unset=True, exclude={"confidence_marker", "reason"})
    if not changes:
        raise HTTPException(400, "No fields to change.")
    batch = new_batch_id()
    for field, value in changes.items():
        if value is None:
            continue
        old = getattr(product, field)
        if ledger.render(old) == ledger.render(value):
            continue
        setattr(product, field, value)
        ledger.record(
            session, country_id=product.country_id, entity_type="product", entity_ref=product.sku, field=field,
            old_value=old, new_value=value, provenance="manual_override", confidence_marker=payload.confidence_marker,
            rationale=payload.reason, author_claim=author, batch_id=batch,
        )
    session.commit()
    session.refresh(product)
    return _product_out(product)


@router.post("/products/{product_id}/retire")
def retire_product(product_id: int, payload: RetireIn, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(404, f"No product with id {product_id}.")
    when = datetime.now(timezone.utc)
    product.retired_at, product.retired_reason = when, payload.reason or "Retired in the tool."
    rows = list(session.scalars(select(Demand).where(Demand.product_id == product.id)))
    for row in rows:
        row.retired_at, row.retired_reason = when, f"{product.retired_reason} (its product {product.sku} was retired)"
    ledger.record(
        session, country_id=product.country_id, entity_type="product", entity_ref=product.sku, field="retired",
        old_value=product.name, new_value=product.retired_reason, rationale=f"With it: {len(rows)} demand rows.",
        provenance="manual_override", author_claim=author, batch_id=new_batch_id(),
    )
    session.commit()
    return {"retired": product.sku, "demand_rows": len(rows)}


@router.post("/products/{product_id}/restore")
def restore_product(product_id: int, payload: RetireIn, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    product = session.scalar(select(Product).where(Product.id == product_id).execution_options(**{INCLUDE_RETIRED: True}))
    if not product:
        raise HTTPException(404, f"No product with id {product_id}.")
    if product.retired_at is None:
        raise HTTPException(409, f"{product.sku} is not retired.")
    product.retired_at, product.retired_reason = None, ""
    rows = list(session.scalars(select(Demand).where(Demand.product_id == product.id).execution_options(**{INCLUDE_RETIRED: True})))
    brought = 0
    for row in rows:
        if row.retired_at is not None and product.sku in (row.retired_reason or ""):
            row.retired_at, row.retired_reason = None, ""
            brought += 1
    ledger.record(
        session, country_id=product.country_id, entity_type="product", entity_ref=product.sku, field="restored",
        new_value=payload.reason or "Restored in the tool.", rationale=f"With it: {brought} demand rows.",
        provenance="manual_override", author_claim=author, batch_id=new_batch_id(),
    )
    session.commit()
    session.refresh(product)
    return _product_out(product) | {"demand_rows": brought}


# --- one field, many rows ------------------------------------------------------------------

NODE_BULK_FIELDS = {
    "type", "level", "admin1", "admin2", "terrain_class", "operating_status", "hub_capable",
    "hub_fixed_cost", "hub_open_capex", "hub_throughput_m3", "catchment_population",
}
EDGE_BULK_FIELDS = {
    "mode", "service_frequency", "service_name", "capacity_per_trip_m3", "cold_capacity_per_trip_m3",
    "fixed_cost_per_trip", "variable_cost_per_km", "cost_per_m3", "reliability", "active",
}
PRODUCT_BULK_FIELDS = {"temperature_band", "volume_per_unit_cm3", "unit_cost", "shelf_life_days"}


@router.post("/countries/{country_id}/tables/{table}/bulk")
def bulk_set(
    country_id: int,
    table: str,
    payload: BulkSet,
    session: Session = Depends(get_session),
    author: str = Depends(author_claim),
):
    """Set one field to one value on many rows. One ledger row per row changed, one
    batch for the sitting, so the whole edit reverts together or not at all."""
    country = _country_or_404(session, country_id)
    if table not in TABLES:
        raise HTTPException(404, f"No table called {table}.")
    if not payload.ids:
        raise HTTPException(400, "Choose at least one row.")
    allowed = {"nodes": NODE_BULK_FIELDS, "edges": EDGE_BULK_FIELDS, "products": PRODUCT_BULK_FIELDS, "demand": {"quantity"}}[table]
    if payload.field not in allowed:
        raise HTTPException(400, f"{payload.field} cannot be set in bulk on {table}. Choose from {', '.join(sorted(allowed))}.")
    batch = new_batch_id()
    model = {"nodes": Node, "edges": Edge, "products": Product, "demand": Demand}[table]
    rows = list(session.scalars(select(model).where(model.country_id == country_id, model.id.in_(payload.ids))))
    changed, touched = 0, []
    value: Any = payload.value
    for row in rows:
        if table == "demand":
            if not isinstance(value, (int, float)) or value < 0:
                raise HTTPException(422, "Demand must be a number of units, zero or more.")
            old = row.quantity
            if old == value:
                continue
            row.quantity = float(value)
            estimators.pin_demand(row)
            node = session.get(Node, row.node_id)
            product = session.get(Product, row.product_id)
            ref = f"{node.code}/{product.sku}/{row.period}"
            touched.append(row.node_id)
        else:
            old = getattr(row, payload.field)
            if ledger.render(old) == ledger.render(value):
                continue
            setattr(row, payload.field, value)
            ref = row.code if table != "products" else row.sku
            if table == "nodes" and payload.field in ("catchment_population", "type", "level"):
                touched.append(row.id)
        ledger.record(
            session, country_id=country_id, entity_type=table[:-1] if table != "demand" else "demand", entity_ref=ref,
            field=payload.field, old_value=old, new_value=value, provenance="manual_override",
            confidence_marker=payload.confidence_marker, rationale=payload.reason or f"Set on {len(rows)} rows at once.",
            author_claim=author, batch_id=batch,
        )
        changed += 1
    recomputed = 0
    if touched:
        session.flush()
        recomputed = estimators.recompute(
            session, country, node_ids=sorted(set(touched)), reason="rows were edited in bulk", author_claim=author, batch_id=batch
        )
    session.commit()
    return {"changed": changed, "of": len(rows), "recomputed": recomputed, "batch_id": batch}


# --- demand, one row -------------------------------------------------------------------------


@router.put("/demand/{row_id}")
def set_demand_row(row_id: int, payload: DemandLine, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    """Edit one demand row from the grid: routed through the per-facility setter so an
    estimate is pinned and the storage that follows it recomputed, exactly as in the editor."""
    row = session.get(Demand, row_id)
    if not row:
        raise HTTPException(404, f"No demand row with id {row_id}.")
    product = session.get(Product, row.product_id)
    line = DemandLine(sku=product.sku, quantity=payload.quantity, period=row.period, source=payload.source, confidence=payload.confidence)
    return set_node_demand(row.node_id, DemandSet(lines=[line], confidence_marker="I", reason="Edited in the table."), session=session, author=author)
