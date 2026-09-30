"""The rules engine: core checks that hold in any country, plus absolute ranges that
come from the country pack and nowhere else.

The core checks are relative: a value is judged against the country's own
distribution or against another value in the same record, never against a number
somebody remembered from another country. That is what lets the same code onboard
Papua New Guinea and Madagascar.

Every issue says what is wrong in the language of the person who owns the data and
what to do about it, and carries an impact so the review queue can put first what
moves the result most.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from statistics import median
from typing import Dict, List, Optional

from .pack import Pack
from .staging import value_of

ERROR, WARNING, INFO = "error", "warning", "info"


@dataclass
class Issue:
    code: str
    severity: str
    message: str
    suggestion: str = ""
    domain: str = ""
    key: str = ""
    field: str = ""
    impact: float = 0.0
    values: dict = dc_field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "message": self.message, "suggestion": self.suggestion, "domain": self.domain, "key": self.key, "field": self.field, "impact": round(self.impact, 3), "values": self.values}


@dataclass
class Context:
    """What the rules read besides the staged records."""

    pack: Pack
    #: sku -> volume per unit in cm3, from staged products then the model.
    product_volume_cm3: Dict[str, float] = dc_field(default_factory=dict)
    #: canonical code -> catchment population, from staged nodes then the model.
    population: Dict[str, float] = dc_field(default_factory=dict)
    #: canonical code -> dry storage m3.
    storage_m3: Dict[str, float] = dc_field(default_factory=dict)
    #: (domain, key, field) -> value in the approved dataset (the model as it stands).
    previous: Dict[tuple, object] = dc_field(default_factory=dict)
    cover_days: float = 30.0


def _num(value) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _robust_outliers(values: Dict[str, float], *, threshold: float = 3.5) -> Dict[str, float]:
    """Modified z-scores from the median absolute deviation; keys beyond the threshold."""
    if len(values) < 8:
        return {}
    xs = list(values.values())
    med = median(xs)
    mad = median(abs(x - med) for x in xs)
    # A country whose figures are all alike has a zero MAD; then the mean deviation, and
    # failing that a twentieth of the median, is the scale a value has to stand out from.
    scale = mad if mad > 0 else (sum(abs(x - med) for x in xs) / len(xs))
    if scale <= 0:
        scale = abs(med) * 0.05
    if scale <= 0:
        return {}
    out = {}
    for key, x in values.items():
        z = 0.6745 * (x - med) / scale
        if abs(z) > threshold:
            out[key] = z
    return out


def run_checks(records: List, context: Context) -> List[Issue]:
    issues: List[Issue] = []
    by_domain: Dict[str, List] = {}
    for record in records:
        by_domain.setdefault(record.domain, []).append(record)
    issues += _travel_time_vs_distance(by_domain.get("Edges", []), context)
    issues += _seasonal_lanes(by_domain.get("Edges", []), context)
    issues += _cost_per_m3_by_mode(by_domain.get("Edges", []), context)
    issues += _demand_per_head(by_domain.get("Demand", []), context)
    issues += _demand_within_storage(by_domain.get("Demand", []), context)
    issues += _triangulation(by_domain.get("Demand", []), context)
    issues += _absolute_ranges(records, context)
    issues += _previous_dataset(records, context)
    return issues


# --- core relative checks ------------------------------------------------------------------


def _travel_time_vs_distance(edges: List, context: Context) -> List[Issue]:
    out = []
    modes = context.pack.transport.modes or {}
    for record in edges:
        km, hours, mode = _num(value_of(record, "distance_km")), _num(value_of(record, "base_travel_time_hr")), str(value_of(record, "mode") or "road")
        if not km or not hours or hours <= 0:
            continue
        speed = km / hours
        rng = (modes.get(mode) or {}).get("speed_kmh") or [5.0, 120.0]
        if speed < rng[0] or speed > rng[1]:
            out.append(Issue("travel_time_vs_distance", WARNING, f"{record.label or record.key}: {km:.0f} km in {hours:.1f} h is {speed:.0f} km/h by {mode}, outside the {rng[0]:.0f}–{rng[1]:.0f} km/h the country pack expects.", "Check whether the time or the distance is wrong, or whether the mode is mislabelled.", "Edges", record.key, "base_travel_time_hr", impact=km, values={"speed_kmh": round(speed, 1)}))
    return out


def _seasonal_lanes(edges: List, context: Context) -> List[Issue]:
    out = []
    modes = context.pack.transport.modes or {}
    for record in edges:
        mode = str(value_of(record, "mode") or "road")
        seasonal_mode = bool((modes.get(mode) or {}).get("seasonal"))
        flagged = str(value_of(record, "access_profile") or "").lower() not in ("", "all_weather") or str((record.aux or {}).get("seasonal", "")).lower() in ("1", "true", "yes", "y")
        access = value_of(record, "monthly_access")
        if (seasonal_mode or flagged) and isinstance(access, list) and all(_num(a) in (None, 1.0) for a in access):
            out.append(Issue("seasonal_lane_without_closures", WARNING, f"{record.label or record.key} is marked seasonal but has no month with reduced access.", "Fill the monthly access columns or name an access profile, or clear the seasonal flag.", "Edges", record.key, "monthly_access", impact=1.0))
    return out


def _cost_per_m3_by_mode(edges: List, context: Context) -> List[Issue]:
    out = []
    by_mode: Dict[str, Dict[str, float]] = {}
    for record in edges:
        cost = _num(value_of(record, "cost_per_m3"))
        if cost and cost > 0:
            by_mode.setdefault(str(value_of(record, "mode") or "road"), {})[record.key] = cost
    for mode, values in by_mode.items():
        for key, z in _robust_outliers(values).items():
            out.append(Issue("cost_per_m3_outlier", WARNING, f"Lane {key}: {values[key]:,.0f} per m³ is far {'above' if z > 0 else 'below'} other {mode} lanes (modified z {z:.1f}).", "Check the unit: per trip, per kg and per km rates are often typed into a per-m³ column.", "Edges", key, "cost_per_m3", impact=abs(values[key]), values={"z": round(z, 2)}))
    return out


def _demand_per_head(demand: List, context: Context) -> List[Issue]:
    out = []
    by_product: Dict[str, Dict[str, float]] = {}
    for record in demand:
        node, sku, period = _split_key(record.key)
        qty = _num(value_of(record, "quantity"))
        pop = context.population.get(node)
        if qty is None or not pop or pop <= 0 or period not in (0, None):
            continue
        by_product.setdefault(sku, {})[record.key] = qty / pop * 1000.0
    for sku, values in by_product.items():
        for key, z in _robust_outliers(values).items():
            out.append(Issue("demand_per_head_outlier", WARNING, f"{key.split('|')[0]}: {values[key]:,.1f} {sku} per 1,000 people is far {'above' if z > 0 else 'below'} the country's other facilities (modified z {z:.1f}).", "A referral hospital serving beyond its catchment is expected; a health centre at ten times its peers is a unit or a population error.", "Demand", key, "quantity", impact=abs(values[key]) * context.population.get(key.split('|')[0], 0) / 1000.0, values={"per_1000": round(values[key], 2), "z": round(z, 2)}))
    return out


def _demand_within_storage(demand: List, context: Context) -> List[Issue]:
    out = []
    monthly_m3: Dict[str, float] = {}
    for record in demand:
        node, sku, period = _split_key(record.key)
        qty = _num(value_of(record, "quantity"))
        volume = context.product_volume_cm3.get(sku)
        if qty is None or not volume:
            continue
        per_month = qty * volume / 1e6 / (12.0 if period in (0, None) else 1.0)
        monthly_m3[node] = monthly_m3.get(node, 0.0) + per_month
    for node, m3 in monthly_m3.items():
        storage = context.storage_m3.get(node)
        if storage is None or storage <= 0:
            continue
        needed = m3 * context.cover_days / 30.0
        if needed > storage * 1.05:
            out.append(Issue("demand_exceeds_storage", WARNING, f"{node}: {context.cover_days:.0f} days of demand is {needed:.1f} m³ but the facility has {storage:.1f} m³ of storage.", "Either the demand or the storage figure is in the wrong unit, or the facility genuinely cannot hold its resupply and needs more frequent deliveries.", "Nodes", node, "dry_m3", impact=needed - storage, values={"needed_m3": round(needed, 2), "storage_m3": storage}))
    return out


def _triangulation(demand: List, context: Context) -> List[Issue]:
    """Two figures that should agree: issued against consumed, consumption against
    consultations. Only when both are in the record."""
    out = []
    for record in demand:
        aux = record.aux or {}
        qty = _num(value_of(record, "quantity"))
        issued = _num(aux.get("issued") or aux.get("quantity_issued"))
        consumed = _num(aux.get("consumption") or aux.get("consumed") or aux.get("quantity_consumed"))
        if issued and consumed and consumed > 0:
            ratio = issued / consumed
            if ratio > 2.0 or ratio < 0.5:
                out.append(Issue("issued_vs_consumed", WARNING, f"{record.key.split('|')[0]}: {issued:,.0f} issued against {consumed:,.0f} consumed (ratio {ratio:.1f}).", "Issues far above consumption mean stock is building or leaking; far below means consumption is drawn from buffer. Ask the store which figure the model should use.", "Demand", record.key, "quantity", impact=abs(issued - consumed), values={"ratio": round(ratio, 2)}))
        consultations = _num(aux.get("consultations") or aux.get("visits") or aux.get("opd_visits"))
        if qty is not None and consultations and consultations > 0:
            per = qty / consultations
            if per > 50 or (qty > 0 and per < 0.001):
                out.append(Issue("consumption_vs_consultations", INFO, f"{record.key.split('|')[0]}: {per:,.2f} units per consultation.", "Compare with the programme's expected use per visit.", "Demand", record.key, "quantity", impact=qty, values={"per_consultation": round(per, 3)}))
    return out


# --- from the pack only --------------------------------------------------------------------

_RANGE_FIELDS = {"catchment_population": "Nodes", "dry_m3": "Nodes", "cold_2_8_m3": "Nodes", "distance_km": "Edges", "base_travel_time_hr": "Edges", "cost_per_m3": "Edges", "quantity": "Demand"}


def _absolute_ranges(records: List, context: Context) -> List[Issue]:
    out = []
    ranges = context.pack.thresholds.ranges or {}
    for record in records:
        for name, rng in ranges.items():
            if name == "demand_per_1000":
                continue
            if _RANGE_FIELDS.get(name, record.domain) != record.domain:
                continue
            value = _num(value_of(record, name))
            if value is None or not isinstance(rng, (list, tuple)) or len(rng) != 2:
                continue
            if value < rng[0] or value > rng[1]:
                out.append(Issue("outside_pack_range", ERROR, f"{record.label or record.key}: {name} is {value:,.1f}, outside the {rng[0]:,.0f}–{rng[1]:,.0f} the country pack allows.", "Check the unit and the source. Widen the pack range only if the figure is right.", record.domain, record.key, name, impact=abs(value)))
        if record.domain == "Demand" and "demand_per_1000" in ranges:
            node, sku, period = _split_key(record.key)
            qty, pop = _num(value_of(record, "quantity")), context.population.get(node)
            if qty is not None and pop and period in (0, None):
                per = qty / pop * 1000
                lo, hi = ranges["demand_per_1000"]
                if per < lo or per > hi:
                    out.append(Issue("outside_pack_range", ERROR, f"{node}: {per:,.0f} {sku} per 1,000 people is outside the {lo:,.0f}–{hi:,.0f} the country pack allows.", "Check the unit (packs against units) and the catchment population.", "Demand", record.key, "quantity", impact=qty))
    return out


def _previous_dataset(records: List, context: Context) -> List[Issue]:
    out = []
    if not context.previous:
        return out
    sensitivity = context.pack.thresholds.change_sensitivity or 0.5
    for record in records:
        for name, item in (record.fields or {}).items():
            before = context.previous.get((record.domain, record.key, name))
            after = item.get("value")
            b, a = _num(before), _num(after)
            if b is None or a is None or b == 0:
                continue
            change = (a - b) / abs(b)
            if abs(change) > sensitivity:
                out.append(Issue("large_change_vs_approved", WARNING, f"{record.label or record.key}: {name} moves from {b:,.1f} to {a:,.1f} ({change:+.0%}) against the approved dataset.", "Confirm the new figure with its owner before it replaces the approved one.", record.domain, record.key, name, impact=abs(a - b), values={"before": b, "after": a, "change": round(change, 3)}))
    return out


def baseline_total_check(records: List, context: Context) -> Optional[dict]:
    """FR21: staged totals against a known aggregate from the pack, as a gap to report."""
    known = context.pack.thresholds.known_aggregates or {}
    if not known:
        return None
    total_m3 = 0.0
    for record in records:
        if record.domain != "Demand":
            continue
        _, sku, period = _split_key(record.key)
        qty, volume = _num(value_of(record, "quantity")), context.product_volume_cm3.get(sku)
        if qty and volume:
            total_m3 += qty * volume / 1e6 * (1 if period in (0, None) else 1)
    facilities = sum(1 for r in records if r.domain == "Nodes")
    out = {}
    if "annual_demand_m3" in known and known["annual_demand_m3"]:
        out["annual_demand_m3"] = {"known": known["annual_demand_m3"], "staged": round(total_m3, 1), "gap": round((total_m3 - known["annual_demand_m3"]) / known["annual_demand_m3"], 3)}
    if "facilities" in known and known["facilities"]:
        out["facilities"] = {"known": known["facilities"], "staged": facilities, "gap": round((facilities - known["facilities"]) / known["facilities"], 3)}
    return out or None


def _split_key(key: str):
    parts = (key or "").split("|")
    node = parts[0] if parts else ""
    sku = parts[1] if len(parts) > 1 else ""
    try:
        period = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        period = None
    return node, sku, period
