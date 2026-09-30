"""The estimation library: named methods only, each producing a labelled estimate.

A gap is filled by one of these or left empty and flagged. There is no fourth option.
Every estimate carries its method, its inputs and the label "directional, not for
budgeting" until it has been validated against an independent figure, and every one is
listed individually in the review queue and the report: nobody approves estimates in
bulk without seeing them.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Dict, List, Optional

from . import DIRECTIONAL
from .pack import Pack
from .rules import Context, _num, _split_key
from .staging import entry, value_of

METHODS = {
    "consumption_stockout_adjusted": {
        "label": "Consumption adjusted for stock-outs",
        "detail": "reported consumption ÷ (1 − days out of stock ÷ days in the period): what would have been used had stock been there",
        "needs": "a consumption figure and days out of stock for the same period",
    },
    "population_based": {
        "label": "From population",
        "detail": "catchment population × the country's annual rate per 1,000 for the product",
        "needs": "a catchment population and a per-1,000 rate in the country pack",
    },
    "peer_median": {
        "label": "Median of similar facilities",
        "detail": "the median of facilities of the same type in this dataset with an observed figure",
        "needs": "at least five facilities of the same type with observed demand for the product",
    },
    "service_based": {
        "label": "From consultations",
        "detail": "consultations × units per consultation from the country pack",
        "needs": "a consultation count and a per-consultation rate in the country pack",
    },
    "storage_from_cover": {
        "label": "Storage from cover days",
        "detail": "monthly demand volume × cover days ÷ 30",
        "needs": "demand with product volumes",
    },
}


@dataclass
class Estimate:
    domain: str
    key: str
    field: str
    method: str
    value: float
    inputs: dict
    formula: str
    confidence: str = "low"

    def entry(self) -> dict:
        return entry(self.value, cls="estimated", method=self.method, inputs=self.inputs, transformation=self.formula, confidence=self.confidence, reason=f"{METHODS[self.method]['label']}: {self.formula}. {DIRECTIONAL}.")


def propose(records: List, context: Context, *, node_types: Optional[Dict[str, str]] = None) -> List[Estimate]:
    """Estimates for every Demand quantity and Node storage that is missing, in the
    order the methods are trusted. A record for which no method applies stays missing."""
    out: List[Estimate] = []
    node_types = node_types or {}
    demand = [r for r in records if r.domain == "Demand"]
    observed_by_product_type: Dict[tuple, List[float]] = {}
    for record in demand:
        node, sku, period = _split_key(record.key)
        item = (record.fields or {}).get("quantity") or {}
        if item.get("class") in ("observed", "converted", "confirmed") and _num(item.get("value")) is not None:
            observed_by_product_type.setdefault((sku, node_types.get(node, "")), []).append(float(item["value"]))

    for record in demand:
        node, sku, period = _split_key(record.key)
        item = (record.fields or {}).get("quantity") or {}
        aux = record.aux or {}
        qty = _num(item.get("value"))
        # Stock-out adjustment applies to an observed figure that was depressed by empty shelves.
        stockout = _num(aux.get("stockout_days") or aux.get("days_out_of_stock"))
        if qty is not None and item.get("class") in ("observed", "converted") and stockout and stockout > 0:
            days = 365.0 if period in (0, None) else 30.0
            if stockout < days * 0.9:
                adjusted = qty / (1 - stockout / days)
                out.append(Estimate("Demand", record.key, "quantity", "consumption_stockout_adjusted", round(adjusted, 2), {"consumption": qty, "stockout_days": stockout, "period_days": days}, f"{qty:,.0f} ÷ (1 − {stockout:.0f}/{days:.0f}) = {adjusted:,.0f}", "medium"))
            continue
        if item.get("class") not in (None, "missing", "illustrative"):
            continue
        estimate = _for_missing_demand(record, node, sku, aux, context, node_types, observed_by_product_type)
        if estimate:
            out.append(estimate)

    for record in [r for r in records if r.domain == "Nodes"]:
        storage = (record.fields or {}).get("dry_m3") or {}
        # Only a gap a storage source left is a gap: a register with no storage column
        # says nothing about storage, and the model keeps what it has.
        if storage.get("class") not in ("missing", "illustrative"):
            continue
        m3_month = 0.0
        for d in demand:
            node, sku, period = _split_key(d.key)
            if node != record.key:
                continue
            qty, volume = _num(value_of(d, "quantity")), context.product_volume_cm3.get(sku)
            if qty and volume:
                m3_month += qty * volume / 1e6 / (12.0 if period in (0, None) else 1.0)
        if m3_month > 0:
            value = round(m3_month * context.cover_days / 30.0, 3)
            out.append(Estimate("Nodes", record.key, "dry_m3", "storage_from_cover", value, {"monthly_m3": round(m3_month, 4), "cover_days": context.cover_days}, f"{m3_month:.3f} m³/month × {context.cover_days:.0f} ÷ 30 = {value:.3f} m³", "medium"))
    return out


def _for_missing_demand(record, node, sku, aux, context: Context, node_types, observed) -> Optional[Estimate]:
    pack: Pack = context.pack
    consultations = _num(aux.get("consultations") or aux.get("visits") or aux.get("opd_visits"))
    per_consultation = _num((pack.products_units.per_consultation or {}).get(sku))
    if consultations and per_consultation:
        value = round(consultations * per_consultation, 2)
        return Estimate("Demand", record.key, "quantity", "service_based", value, {"consultations": consultations, "per_consultation": per_consultation}, f"{consultations:,.0f} × {per_consultation:g} = {value:,.0f}", "medium")
    pop = context.population.get(node)
    rate = _num((pack.products_units.per_1000_rates or {}).get(sku))
    if pop and pop > 0 and rate:
        value = round(pop * rate / 1000.0, 2)
        return Estimate("Demand", record.key, "quantity", "population_based", value, {"population": pop, "per_1000": rate}, f"{pop:,.0f} × {rate:g} ÷ 1,000 = {value:,.0f}", "medium")
    peers = observed.get((sku, node_types.get(node, "")), [])
    if len(peers) >= 5:
        value = round(median(peers), 2)
        return Estimate("Demand", record.key, "quantity", "peer_median", value, {"peers": len(peers), "type": node_types.get(node, "")}, f"median of {len(peers)} {node_types.get(node) or 'similar'} facilities = {value:,.0f}")
    return None
