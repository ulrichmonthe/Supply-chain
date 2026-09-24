"""Editing the model in the tool, one facility at a time.

The spreadsheet round trip stays -- it is how a whole master list moves. This is for
the other case: one coordinate a provincial officer knows is wrong, one clinic that
opened last month, one demand figure that the last upload got from a stale forecast.
Downloading a workbook to change a number is how corrections stop being made.

Every edit is a ledger row with a name on it and a declared confidence, re-checked by
the same rules an import runs, and survivable: an edit made here is what the next
import calls a conflict rather than something it silently reverts.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.orm import Session

from .. import ledger
from ..db import INCLUDE_RETIRED, get_session
from ..io.apply import restore_node, retire_node
from ..io.validation import validate_dataset
from ..models import AuditEntry, Country, Demand, Edge, Node, Product
from ..schemas import DemandOut, DemandSet, NodeCreate, NodeOut, NodePatch, RetireIn
from .deps import author_claim, new_batch_id

router = APIRouter(tags=["editing"])

WORLD_BBOX = {"min_lat": -90, "max_lat": 90, "min_lon": -180, "max_lon": 180}

#: The node fields the validator reads, so an edit is checked exactly as an import is.
NODE_RECORD_FIELDS = (
    "code", "name", "level", "type", "lat", "lon", "geocode_source", "geocode_confidence",
    "admin1", "admin2", "terrain_class", "catchment_population", "operating_status",
    "capacity", "hub_capable", "hub_fixed_cost", "hub_open_capex", "hub_throughput_m3",
)


def _node_or_404(session: Session, node_id: int, *, include_retired: bool = False) -> Node:
    query = select(Node).where(Node.id == node_id)
    if include_retired:
        query = query.execution_options(**{INCLUDE_RETIRED: True})
    node = session.scalar(query)
    if not node:
        raise HTTPException(404, f"No facility with id {node_id}.")
    return node


def _check(session: Session, country: Country, node: Node) -> List[dict]:
    """Run the import rules over this one facility and return what they say."""
    record = {name: getattr(node, name) for name in NODE_RECORD_FIELDS}
    report = validate_dataset(
        country_code=country.code,
        bbox=(country.config or {}).get("bbox", WORLD_BBOX),
        boundary=country.boundary or {},
        nodes=[record],
        edges=[],
        products=[],
        demand=[],
        partial=True,
    )
    return [issue for issue in report.as_dict()["issues"] if issue["sheet"] == "Nodes"]


# --- facilities ----------------------------------------------------------------------


@router.get("/countries/{country_id}/nodes/retired", response_model=list[NodeOut])
def list_retired(country_id: int, session: Session = Depends(get_session)):
    """Facilities retired by an import or by hand. Invisible everywhere else."""
    return list(
        session.scalars(
            select(Node)
            .where(Node.country_id == country_id, Node.retired_at.is_not(None))
            .order_by(Node.retired_at.desc())
            .execution_options(**{INCLUDE_RETIRED: True})
        )
    )


@router.post("/countries/{country_id}/nodes", status_code=201)
def create_node(
    country_id: int,
    payload: NodeCreate,
    session: Session = Depends(get_session),
    author: str = Depends(author_claim),
):
    """Add a facility from the tool. Blocked by the same errors an import would be
    blocked by; warned about by the same warnings."""
    country = session.get(Country, country_id)
    if not country:
        raise HTTPException(404, f"No country with id {country_id}.")
    code = payload.code.strip()
    clash = session.scalar(
        select(Node).where(Node.country_id == country_id, Node.code == code).execution_options(**{INCLUDE_RETIRED: True})
    )
    if clash:
        hint = " It is retired; restore it instead." if clash.retired_at else ""
        raise HTTPException(409, f"A facility with code {code} already exists.{hint}")

    fields = payload.model_dump(exclude={"confidence_marker", "reason"})
    fields["code"] = code
    node = Node(country_id=country_id, **fields)
    issues = _check(session, country, node)
    if any(issue["severity"] == "error" for issue in issues):
        raise HTTPException(422, {"detail": "This facility would fail validation.", "issues": issues})

    session.add(node)
    session.flush()
    ledger.record(
        session,
        country_id=country_id,
        entity_type="node",
        entity_ref=node.code,
        field="created",
        new_value=f"{node.name} at {node.lat:.4f}, {node.lon:.4f}",
        provenance="manual_override",
        confidence_marker=payload.confidence_marker,
        rationale=payload.reason,
        author_claim=author,
        batch_id=new_batch_id(),
    )
    session.commit()
    session.refresh(node)
    return {"node": NodeOut.model_validate(node), "issues": issues}


@router.patch("/nodes/{node_id}")
def update_node(
    node_id: int,
    payload: NodePatch,
    session: Session = Depends(get_session),
    author: str = Depends(author_claim),
):
    """Change what is known about a facility, field by field, with a name on each change."""
    node = _node_or_404(session, node_id)
    country = session.get(Country, node.country_id)
    changes = payload.model_dump(exclude_unset=True, exclude={"confidence_marker", "reason"})
    if not changes:
        raise HTTPException(400, "No fields to change.")

    batch = new_batch_id()
    moved = False
    for field, value in changes.items():
        if value is None:
            continue
        old = getattr(node, field)
        if ledger.render(old) == ledger.render(value):
            continue
        setattr(node, field, value)
        moved = moved or field in ("lat", "lon")
        ledger.record(
            session,
            country_id=node.country_id,
            entity_type="node",
            entity_ref=node.code,
            field=field,
            old_value=old,
            new_value=value,
            provenance="manual_override",
            confidence_marker=payload.confidence_marker,
            rationale=payload.reason,
            author_claim=author,
            batch_id=batch,
        )
    if moved and "geocode_source" not in changes:
        # A typed coordinate is a claim by a person, and the record should say so.
        node.geocode_source = "manual"
        node.geocode_confidence = {"S": 0.95, "I": 0.7, "U": 0.4}[payload.confidence_marker]

    issues = _check(session, country, node)
    session.commit()
    session.refresh(node)
    return {"node": NodeOut.model_validate(node), "issues": issues}


@router.post("/nodes/{node_id}/retire")
def retire(
    node_id: int,
    payload: RetireIn,
    session: Session = Depends(get_session),
    author: str = Depends(author_claim),
):
    """Retire a facility -- not delete. Its lanes and demand go with it; its history stays."""
    node = _node_or_404(session, node_id)
    if node.level == 0:
        raise HTTPException(400, "The national store anchors the network and cannot be retired.")
    taken = retire_node(
        session,
        node,
        reason=payload.reason or "Retired in the tool.",
        country_id=node.country_id,
        author_claim=author,
        batch_id=new_batch_id(),
    )
    session.commit()
    return {"retired": node.code, **taken}


@router.post("/nodes/{node_id}/restore")
def restore(
    node_id: int,
    payload: RetireIn,
    session: Session = Depends(get_session),
    author: str = Depends(author_claim),
):
    node = _node_or_404(session, node_id, include_retired=True)
    if node.retired_at is None:
        raise HTTPException(409, f"{node.code} is not retired.")
    brought = restore_node(
        session,
        node,
        reason=payload.reason or "Restored in the tool.",
        country_id=node.country_id,
        author_claim=author,
        batch_id=new_batch_id(),
    )
    session.commit()
    session.refresh(node)
    return {"node": NodeOut.model_validate(node), **brought}


# --- demand ----------------------------------------------------------------------------


def _demand_out(row: Demand, node: Node, product: Product) -> DemandOut:
    return DemandOut(
        id=row.id,
        node_id=node.id,
        node_code=node.code,
        sku=product.sku,
        product_name=product.name,
        period=row.period,
        quantity=row.quantity,
        source=row.source,
        confidence=row.confidence,
    )


@router.get("/nodes/{node_id}/demand", response_model=list[DemandOut])
def node_demand(node_id: int, session: Session = Depends(get_session)):
    node = _node_or_404(session, node_id)
    products = {p.id: p for p in session.scalars(select(Product).where(Product.country_id == node.country_id))}
    rows = session.scalars(select(Demand).where(Demand.node_id == node.id).order_by(Demand.product_id, Demand.period))
    return [_demand_out(row, node, products[row.product_id]) for row in rows if row.product_id in products]


@router.put("/nodes/{node_id}/demand", response_model=list[DemandOut])
def set_node_demand(
    node_id: int,
    payload: DemandSet,
    session: Session = Depends(get_session),
    author: str = Depends(author_claim),
):
    """Set a facility's demand, per product. Rows named here are created or updated;
    rows not named are left alone -- this is not a replace."""
    node = _node_or_404(session, node_id)
    products = {p.sku: p for p in session.scalars(select(Product).where(Product.country_id == node.country_id))}
    existing = {
        (row.product_id, row.period): row
        for row in session.scalars(select(Demand).where(Demand.node_id == node.id))
    }
    batch = new_batch_id()
    for line in payload.lines:
        product = products.get(line.sku)
        if not product:
            raise HTTPException(404, f"No product with sku {line.sku}.")
        if line.quantity < 0:
            raise HTTPException(422, f"Demand for {line.sku} cannot be negative.")
        row = existing.get((product.id, line.period))
        ref = f"{node.code}/{product.sku}/{line.period}"
        if row is None:
            row = Demand(
                country_id=node.country_id,
                node_id=node.id,
                product_id=product.id,
                period=line.period,
                quantity=line.quantity,
                source=line.source,
                confidence=line.confidence,
            )
            session.add(row)
            existing[(product.id, line.period)] = row
            ledger.record(
                session, country_id=node.country_id, entity_type="demand", entity_ref=ref, field="quantity",
                new_value=line.quantity, provenance="manual_override",
                confidence_marker=payload.confidence_marker, rationale=payload.reason,
                author_claim=author, batch_id=batch,
            )
            continue
        for field in ("quantity", "source", "confidence"):
            old, new = getattr(row, field), getattr(line, field)
            if ledger.render(old) == ledger.render(new):
                continue
            setattr(row, field, new)
            ledger.record(
                session, country_id=node.country_id, entity_type="demand", entity_ref=ref, field=field,
                old_value=old, new_value=new, provenance="manual_override",
                confidence_marker=payload.confidence_marker, rationale=payload.reason,
                author_claim=author, batch_id=batch,
            )
    session.commit()
    by_id = {p.id: p for p in products.values()}
    rows = session.scalars(select(Demand).where(Demand.node_id == node.id).order_by(Demand.product_id, Demand.period))
    return [_demand_out(row, node, by_id[row.product_id]) for row in rows if row.product_id in by_id]


# --- undo ------------------------------------------------------------------------------

REVERTIBLE = {"node": Node, "edge": Edge, "demand": Demand}


def _parse_back(model, field: str, text: Optional[str]) -> Any:
    """A ledger value back into the column's type."""
    if text is None:
        return None
    column = sa_inspect(model).columns[field]
    try:
        kind = column.type.python_type
    except NotImplementedError:
        return json.loads(text)  # JSON columns
    if kind is bool:
        return text.strip().lower() in ("true", "1", "yes")
    if kind in (int, float):
        return kind(float(text))
    if kind in (dict, list):
        return json.loads(text)
    return text


@router.post("/audit/{entry_id}/revert")
def revert(entry_id: int, session: Session = Depends(get_session), author: str = Depends(author_claim)):
    """Undo one hand-made change. Undo is a row that reverses it, never a deletion:
    the original stays, marked reverted, and the reversal names it."""
    entry = session.get(AuditEntry, entry_id)
    if not entry:
        raise HTTPException(404, f"No ledger entry with id {entry_id}.")
    if entry.status != "applied":
        raise HTTPException(409, "That change has already been reverted.")
    if entry.provenance != "manual_override" or entry.entity_type not in REVERTIBLE:
        raise HTTPException(400, "Only changes made by hand in the tool can be reverted here; an import is undone by importing again.")
    model = REVERTIBLE[entry.entity_type]
    if entry.field not in sa_inspect(model).columns:
        raise HTTPException(400, f"'{entry.field}' is not a value that can be put back.")

    if entry.entity_type == "demand":
        node_code, sku, period = entry.entity_ref.split("/")
        target = session.scalar(
            select(Demand)
            .join(Node, Node.id == Demand.node_id)
            .join(Product, Product.id == Demand.product_id)
            .where(Node.code == node_code, Product.sku == sku, Demand.period == int(period), Demand.country_id == entry.country_id)
        )
    else:
        target = session.scalar(
            select(model).where(model.country_id == entry.country_id, model.code == entry.entity_ref)
        )
    if target is None:
        raise HTTPException(409, f"{entry.entity_ref} is no longer in the model, so there is nothing to put back.")

    current = getattr(target, entry.field)
    if ledger.render(current) != entry.new_value:
        raise HTTPException(
            409,
            f"{entry.entity_ref}.{entry.field} has changed again since (it is now {ledger.render(current)}); "
            "revert the later change first.",
        )
    setattr(target, entry.field, _parse_back(model, entry.field, entry.old_value))
    entry.status = "reverted"
    reversal = ledger.record(
        session,
        country_id=entry.country_id,
        entity_type=entry.entity_type,
        entity_ref=entry.entity_ref,
        field=entry.field,
        old_value=entry.new_value,
        new_value=entry.old_value,
        provenance="manual_override",
        confidence_marker=entry.confidence_marker,
        rationale=f"Reverted the change made by {entry.author_claim}.",
        author_claim=author,
        batch_id=new_batch_id(),
    )
    reversal.reverts_id = entry.id
    session.commit()
    return {"reverted": entry.id, "by": reversal.id, "field": entry.field, "value": entry.old_value}
