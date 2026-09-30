"""Estimates that show their working and stay live.

Where distribution data is missing, the honest move is not to leave a blank the
solver reads as zero, and not to type a guess that looks like a fact. It is to apply a
named rule, record the arithmetic beside the number, and keep the number tied to its
inputs so it moves when they do -- until a person types over it, at which point it is
theirs and the rule lets go.

Three rules, deliberately few:

* ``population_rate``  demand = catchment population × a per-1,000 annual rate.
  The rate comes, in order, from the country's settings, from the median of facilities
  with measured demand, or from the estimates already made.
* ``peer_median``      demand = the median of facilities of the same type (then level).
* ``capacity_cover``   storage = annual demand volume per temperature band × cover days.

Every applied or recomputed value is a ledger row with provenance ``derived`` and the
formula as its rationale. Rules are data on the country, so a consultant can set a rate
without a release, and a fixed vocabulary would be wrong in the second country.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ledger
from .models import Country, Demand, Node, Product

RULES = {
    "population_rate": {
        "target": "demand",
        "label": "From population",
        "detail": "catchment population × a per-1,000 annual rate for the product",
        "live": True,
    },
    "peer_median": {
        "target": "demand",
        "label": "Same as similar facilities",
        "detail": "the median demand of facilities of the same type (then the same level)",
        "live": False,
    },
    "capacity_cover": {
        "target": "capacity",
        "label": "Storage from cover days",
        "detail": "annual demand volume per temperature band × cover days ÷ 365",
        "live": True,
    },
}

DEFAULT_COVER_DAYS = 30.0
BANDS = ("+2-8", "-20", "-70")


@dataclass
class Proposal:
    node_id: int
    node_code: str
    node_name: str
    sku: Optional[str]
    field: str  # "quantity" or "capacity"
    current: Optional[object]
    proposed: object
    formula: str
    derivation: dict
    confidence: float

    def as_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "node_code": self.node_code,
            "node_name": self.node_name,
            "sku": self.sku,
            "field": self.field,
            "current": self.current,
            "proposed": self.proposed,
            "formula": self.formula,
            "confidence": self.confidence,
        }


@dataclass
class Basis:
    """Where a rule's parameter came from, so the formula can say so."""

    value: Optional[float]
    text: str
    confidence: float
    count: int = 0


# --- the world the rules read -----------------------------------------------------------


@dataclass
class World:
    country: Country
    nodes: Dict[int, Node]
    products: Dict[str, Product]
    product_by_id: Dict[int, Product]
    demand: List[Demand]
    rows: Dict[Tuple[int, int, int], Demand] = field(default_factory=dict)

    @classmethod
    def load(cls, session: Session, country: Country) -> "World":
        nodes = {n.id: n for n in session.scalars(select(Node).where(Node.country_id == country.id))}
        products = {p.sku: p for p in session.scalars(select(Product).where(Product.country_id == country.id))}
        demand = list(session.scalars(select(Demand).where(Demand.country_id == country.id)))
        world = cls(country, nodes, products, {p.id: p for p in products.values()}, demand)
        world.rows = {(d.node_id, d.product_id, d.period): d for d in demand}
        return world

    def facilities(self) -> List[Node]:
        return [n for n in self.nodes.values() if n.level == 3]

    def settings(self) -> dict:
        return (self.country.config or {}).get("estimators") or {}


# --- rates and bases ---------------------------------------------------------------------


def rate_basis(world: World, product: Product) -> Basis:
    """The per-1,000 annual rate for a product, and where it came from."""
    configured = (world.settings().get("per_1000") or {}).get(product.sku)
    if configured is not None:
        return Basis(float(configured), "rate from the country's settings", 0.6)

    measured = []
    remembered = []
    for row in world.demand:
        if row.product_id != product.id or row.period != 0:
            continue
        node = world.nodes.get(row.node_id)
        if not node or (node.catchment_population or 0) <= 0:
            continue
        if row.derivation:
            per_1000 = (row.derivation.get("params") or {}).get("per_1000")
            if per_1000 is not None:
                remembered.append(float(per_1000))
        elif row.source in ("actual", "forecast") and row.quantity > 0:
            measured.append(row.quantity / node.catchment_population * 1000.0)
    if measured:
        return Basis(
            statistics.median(measured),
            f"rate from the median of {len(measured)} facilities with measured demand",
            0.5,
            len(measured),
        )
    if remembered:
        return Basis(
            statistics.median(remembered),
            f"rate carried over from {len(remembered)} existing estimates",
            0.4,
            len(remembered),
        )
    return Basis(None, "no rate: set one in the country's settings, or load measured demand for this product", 0.0)


def peer_basis(world: World, node: Node, product: Product) -> Basis:
    """The median demand of this facility's peers for a product."""
    for scope, matches in (("type", lambda n: n.type == node.type), ("level", lambda n: n.level == node.level)):
        measured, estimated = [], []
        for row in world.demand:
            if row.product_id != product.id or row.period != 0 or row.node_id == node.id:
                continue
            peer = world.nodes.get(row.node_id)
            if not peer or not matches(peer) or row.quantity <= 0:
                continue
            (estimated if row.derivation else measured).append(row.quantity)
        if measured:
            return Basis(statistics.median(measured), f"median of {len(measured)} {scope}-matched facilities with measured demand", 0.5, len(measured))
        if estimated:
            return Basis(statistics.median(estimated), f"median of {len(estimated)} {scope}-matched facilities, themselves estimated", 0.3, len(estimated))
    return Basis(None, "no comparable facility has demand for this product", 0.0)


def cover_days(world: World, node: Node) -> float:
    existing = (node.derivations or {}).get("capacity", {}).get("params", {}).get("cover_days")
    if existing:
        return float(existing)
    return float(world.settings().get("cover_days") or DEFAULT_COVER_DAYS)


# --- computing one value ------------------------------------------------------------------


def _fmt(value: float) -> str:
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.2f}".rstrip("0").rstrip(".")


def demand_from_population(world: World, node: Node, product: Product, basis: Optional[Basis] = None) -> Optional[Proposal]:
    basis = basis or rate_basis(world, product)
    population = float(node.catchment_population or 0)
    if basis.value is None or population <= 0:
        return None
    quantity = round(population / 1000.0 * basis.value, 2)
    formula = f"{_fmt(population)} people × {_fmt(basis.value)} per 1,000 per year = {_fmt(quantity)} ({basis.text})"
    row = world.rows.get((node.id, product.id, 0))
    return Proposal(
        node.id, node.code, node.name, product.sku, "quantity",
        row.quantity if row else None, quantity, formula,
        {
            "rule": "population_rate",
            "params": {"per_1000": basis.value, "basis": basis.text},
            "inputs": {"population": population},
            "formula": formula,
            "at": datetime.now(timezone.utc).isoformat(),
        },
        basis.confidence,
    )


def demand_from_peers(world: World, node: Node, product: Product) -> Optional[Proposal]:
    basis = peer_basis(world, node, product)
    if basis.value is None:
        return None
    quantity = round(basis.value, 2)
    formula = f"{basis.text}: {_fmt(quantity)} per year"
    row = world.rows.get((node.id, product.id, 0))
    return Proposal(
        node.id, node.code, node.name, product.sku, "quantity",
        row.quantity if row else None, quantity, formula,
        {"rule": "peer_median", "params": {"median": basis.value, "basis": basis.text}, "inputs": {}, "formula": formula,
         "at": datetime.now(timezone.utc).isoformat()},
        basis.confidence,
    )


def capacity_from_cover(world: World, node: Node) -> Optional[Proposal]:
    """Storage per band = annual demand volume in that band × cover days ÷ 365."""
    days = cover_days(world, node)
    per_band: Dict[str, float] = {}
    for row in world.demand:
        if row.node_id != node.id or row.period != 0:
            continue
        product = world.product_by_id.get(row.product_id)
        if not product:
            continue
        per_band[product.temperature_band] = per_band.get(product.temperature_band, 0.0) + row.quantity * product.volume_per_unit_cm3 / 1e6
    if not per_band:
        return None
    factor = days / 365.0
    capacity = {
        **(node.capacity or {}),
        "dry_m3": round(per_band.get("ambient", 0.0) * factor, 2),
        "cold_by_band": {band: round(per_band.get(band, 0.0) * factor, 3) for band in BANDS},
        "cover_days_assumed": round(days, 1),
    }
    annual = sum(per_band.values())
    formula = f"{_fmt(annual)} m³ of annual demand × {_fmt(days)} cover days ÷ 365 = {_fmt(capacity['dry_m3'])} m³ dry, {_fmt(capacity['cold_by_band']['+2-8'])} m³ at +2–8°C"
    return Proposal(
        node.id, node.code, node.name, None, "capacity",
        node.capacity or None, capacity, formula,
        {"rule": "capacity_cover", "params": {"cover_days": days}, "inputs": {"annual_m3_by_band": {k: round(v, 3) for k, v in per_band.items()}},
         "formula": formula, "at": datetime.now(timezone.utc).isoformat()},
        0.5,
    )


# --- what is blank, and what is available ---------------------------------------------------


def blank_demand(world: World, product: Product) -> List[Node]:
    """Facilities with no demand row for the product, or a zero one that nobody typed."""
    out = []
    for node in world.facilities():
        row = world.rows.get((node.id, product.id, 0))
        if row is None or (row.quantity <= 0 and not row.derivation and row.source == "proxy"):
            out.append(node)
    return out


def blank_capacity(world: World) -> List[Node]:
    out = []
    for node in world.nodes.values():
        cap = node.capacity or {}
        if not cap.get("dry_m3") and not any((cap.get("cold_by_band") or {}).values()) and "capacity" not in (node.derivations or {}):
            out.append(node)
    return out


def describe(session: Session, country: Country) -> dict:
    """The rules, per product: what they would use, and how many rows are blank or estimated."""
    world = World.load(session, country)
    products = []
    for sku, product in sorted(world.products.items()):
        rows = [d for d in world.demand if d.product_id == product.id and d.period == 0]
        basis = rate_basis(world, product)
        products.append(
            {
                "sku": sku,
                "name": product.name,
                "rows": len(rows),
                "estimated": sum(1 for d in rows if d.derivation),
                "blank": len(blank_demand(world, product)),
                "population_rate": {"available": basis.value is not None, "per_1000": basis.value, "basis": basis.text},
            }
        )
    total = sum(1 for d in world.demand if d.period == 0)
    estimated = sum(1 for d in world.demand if d.period == 0 and d.derivation)
    return {
        "rules": [{"key": key, **spec} for key, spec in RULES.items()],
        "products": products,
        "capacity": {
            "blank": len(blank_capacity(world)),
            "estimated": sum(1 for n in world.nodes.values() if "capacity" in (n.derivations or {})),
            "cover_days": float(world.settings().get("cover_days") or DEFAULT_COVER_DAYS),
        },
        "demand": {"rows": total, "estimated": estimated, "share": round(estimated / total, 3) if total else 0.0},
    }


# --- preview and apply --------------------------------------------------------------------------


def propose(session: Session, country: Country, rule: str, *, node_id: Optional[int] = None, sku: Optional[str] = None) -> Tuple[List[Proposal], List[str]]:
    """What the rule would set. One row when a node (and sku) is named; every blank row otherwise."""
    if rule not in RULES:
        raise ValueError(f"No rule called {rule!r}.")
    world = World.load(session, country)
    proposals: List[Proposal] = []
    skipped: List[str] = []

    if RULES[rule]["target"] == "capacity":
        targets = [world.nodes[node_id]] if node_id else blank_capacity(world)
        for node in targets:
            proposal = capacity_from_cover(world, node)
            if proposal is None:
                skipped.append(f"{node.code}: no demand rows to size storage from")
            else:
                proposals.append(proposal)
        return proposals, skipped

    skus = [sku] if sku else sorted(world.products)
    for one in skus:
        product = world.products.get(one)
        if not product:
            raise ValueError(f"No product with sku {one!r}.")
        if node_id:
            targets = [world.nodes[node_id]]
        else:
            targets = blank_demand(world, product)
        basis = rate_basis(world, product) if rule == "population_rate" else None
        for node in targets:
            proposal = (
                demand_from_population(world, node, product, basis)
                if rule == "population_rate"
                else demand_from_peers(world, node, product)
            )
            if proposal is None:
                why = basis.text if basis and basis.value is None else "no population on record" if rule == "population_rate" else "no comparable facility"
                skipped.append(f"{node.code} / {one}: {why}")
            else:
                proposals.append(proposal)
    return proposals, skipped


def apply(session: Session, country: Country, proposals: Iterable[Proposal], *, reason: str = "", author_claim: str = "anonymous", batch_id: Optional[str] = None, actor: str = "analyst") -> int:
    """Write proposals to the model, each as a ledger row that shows its working."""
    world = World.load(session, country)
    applied = 0
    for proposal in proposals:
        node = world.nodes[proposal.node_id]
        if proposal.field == "capacity":
            old = node.capacity
            node.capacity = proposal.proposed
            node.derivations = {**(node.derivations or {}), "capacity": proposal.derivation}
            ref, entity, field_name = node.code, "node", "capacity"
        else:
            product = world.products[proposal.sku]
            row = world.rows.get((node.id, product.id, 0))
            old = row.quantity if row else None
            if row is None:
                row = Demand(country_id=country.id, node_id=node.id, product_id=product.id, period=0)
                session.add(row)
                world.rows[(node.id, product.id, 0)] = row
            row.quantity = proposal.proposed
            row.source = "proxy"
            row.confidence = proposal.confidence
            row.derivation = proposal.derivation
            row.provenance_class = "estimated"
            ref, entity, field_name = f"{node.code}/{product.sku}/0", "demand", "quantity"
        ledger.record(
            session,
            country_id=country.id,
            entity_type=entity,
            entity_ref=ref,
            field=field_name,
            old_value=old,
            new_value=proposal.proposed,
            provenance="derived",
            confidence_marker="I",
            rationale=(f"{reason} " if reason else "") + f"Estimated by {proposal.derivation['rule']}: {proposal.formula}",
            actor=actor,
            author_claim=author_claim,
            batch_id=batch_id,
        )
        applied += 1
    session.flush()
    return applied


# --- staying live ---------------------------------------------------------------------------------


def recompute(session: Session, country: Country, *, node_ids: Optional[Iterable[int]] = None, reason: str = "", author_claim: str = "system", batch_id: Optional[str] = None, actor: str = "system") -> int:
    """Re-run every live rule whose inputs may have changed. Demand first, then storage,
    because storage is sized from demand. Only values that actually move get a row."""
    world = World.load(session, country)
    wanted = set(node_ids) if node_ids is not None else None
    changed = 0

    for row in world.demand:
        if not row.derivation or row.derivation.get("rule") != "population_rate" or row.period != 0:
            continue
        if wanted is not None and row.node_id not in wanted:
            continue
        node = world.nodes.get(row.node_id)
        product = world.product_by_id.get(row.product_id)
        if not node or not product:
            continue
        per_1000 = (row.derivation.get("params") or {}).get("per_1000")
        if per_1000 is None:
            continue
        # The rate is a parameter fixed when the estimate was made; the population is
        # the live input. Re-learning the rate here would move every row whenever one
        # facility's real data changed the median -- churn, not liveness.
        basis = Basis(float(per_1000), (row.derivation.get("params") or {}).get("basis", "rate as first estimated"), row.confidence)
        proposal = demand_from_population(world, node, product, basis)
        if proposal is None or abs(proposal.proposed - row.quantity) < 1e-9:
            continue
        old = row.quantity
        row.quantity = proposal.proposed
        row.derivation = proposal.derivation
        ledger.record(
            session, country_id=country.id, entity_type="demand", entity_ref=f"{node.code}/{product.sku}/0",
            field="quantity", old_value=old, new_value=proposal.proposed, provenance="derived", confidence_marker="I",
            rationale=f"Recomputed{': ' + reason if reason else ''}. {proposal.formula}",
            actor=actor, author_claim=author_claim, batch_id=batch_id,
        )
        changed += 1
    session.flush()

    world = World.load(session, country)
    for node in world.nodes.values():
        if "capacity" not in (node.derivations or {}):
            continue
        if wanted is not None and node.id not in wanted:
            continue
        proposal = capacity_from_cover(world, node)
        if proposal is None:
            continue
        before = {k: (node.capacity or {}).get(k) for k in ("dry_m3", "cold_by_band")}
        after = {k: proposal.proposed.get(k) for k in ("dry_m3", "cold_by_band")}
        if before == after:
            continue
        old = node.capacity
        node.capacity = proposal.proposed
        node.derivations = {**(node.derivations or {}), "capacity": proposal.derivation}
        ledger.record(
            session, country_id=country.id, entity_type="node", entity_ref=node.code, field="capacity",
            old_value=old, new_value=proposal.proposed, provenance="derived", confidence_marker="I",
            rationale=f"Recomputed{': ' + reason if reason else ''}. {proposal.formula}",
            actor=actor, author_claim=author_claim, batch_id=batch_id,
        )
        changed += 1
    session.flush()
    return changed


def pin_demand(row: Demand) -> bool:
    """A person typed over an estimate: the rule lets go. Returns whether it was live."""
    if row.derivation:
        row.derivation = None
        return True
    return False


def pin_capacity(node: Node) -> bool:
    derivations = dict(node.derivations or {})
    if "capacity" in derivations:
        derivations.pop("capacity")
        node.derivations = derivations
        return True
    return False
