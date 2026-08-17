"""Scheduled services and the stockout risk they imply.

This is the signature capability. A road lane is on-demand: you send a vehicle when
you need one. A timetabled maritime or air service is not -- it leaves on Tuesday,
it has a fixed hold, and if you miss it the next one is a fortnight away. Modelling
that as "an edge with a capacity" loses the entire effect. Modelling it as a
*frequency* is what makes the demo work: halve the frequency and stockout risk at
island facilities climbs while nothing else in the network changes.

The risk model is a deliberately simple newsvendor-style approximation, and it is
simple on purpose -- it has to be defensible to a technical reviewer in one
paragraph, and it has to respond instantly to a slider. It says:

  * a facility is resupplied every I days, where I comes from the timetable;
  * it can only hold as much as its shelf and cold-chain space allow;
  * demand over a cycle is uncertain, and the service itself is not perfectly
    reliable, so sometimes the interval is really 2I;
  * stockout risk is the probability that consumption before the next delivery
    exceeds what could be held after the last one.

Everything else -- full discrete-event simulation of queues, wastage and
expiry -- is deferred to the DES layer, as the build plan says it should be.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from . import seasonality

#: Deliveries per year implied by a timetable label.
TRIPS_PER_YEAR: dict[str, float] = {
    "DAILY": 260.0,        # working days
    "TWICE_WEEKLY": 104.0,
    "WEEKLY": 52.0,
    "FORTNIGHTLY": 26.0,
    "MONTHLY": 12.0,
    "SIX_WEEKLY": 8.7,
    "QUARTERLY": 4.0,
    "BIANNUAL": 2.0,
}

FREQUENCY_ORDER = list(TRIPS_PER_YEAR.keys())

#: Coefficient of variation of daily demand at a facility, by level. District
#: facilities see lumpier consumption than provincial hospitals.
DEMAND_CV_BY_LEVEL: dict[int, float] = {0: 0.15, 1: 0.20, 2: 0.30, 3: 0.45}


def trips_per_year(frequency: Optional[str]) -> float:
    if not frequency:
        return TRIPS_PER_YEAR["MONTHLY"]
    return TRIPS_PER_YEAR.get(frequency.upper(), TRIPS_PER_YEAR["MONTHLY"])


def interval_days(frequency: Optional[str]) -> float:
    return 365.0 / max(1e-6, trips_per_year(frequency))


def _phi(z: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@dataclass
class ServiceProfile:
    """What a lane can actually deliver, once the timetable and season are applied."""

    trips_per_year: float
    effective_trips: float
    interval_days: float
    effective_interval_days: float
    annual_capacity_m3: float
    annual_cold_capacity_m3: float
    is_scheduled: bool
    access_factor: float


def profile_for(edge, month: Optional[int] = None, frequency_override: Optional[str] = None) -> ServiceProfile:
    """Resolve a lane's delivery profile for a month (or annualised when month is None)."""
    frequency = frequency_override or edge.service_frequency
    is_scheduled = bool(edge.service_frequency)

    base_trips = trips_per_year(frequency) if frequency else TRIPS_PER_YEAR["MONTHLY"]
    access_factor = (
        seasonality.access(edge, month) if month else seasonality.annual_access(edge)
    )

    effective_trips = base_trips * access_factor * edge.reliability
    interval = 365.0 / max(1e-6, base_trips)
    effective_interval = 365.0 / effective_trips if effective_trips > 1e-6 else float("inf")

    per_trip = edge.capacity_per_trip_m3 or 0.0
    cold_per_trip = edge.cold_capacity_per_trip_m3 or 0.0

    if is_scheduled and per_trip > 0:
        annual_capacity = base_trips * per_trip * access_factor * edge.reliability
        annual_cold = base_trips * cold_per_trip * access_factor * edge.reliability
    else:
        # On-demand road lanes are not capacity-limited by a timetable; they are
        # limited by cost and by whether the road is open at all.
        annual_capacity = float("inf") if access_factor > 0 else 0.0
        annual_cold = float("inf") if access_factor > 0 else 0.0

    return ServiceProfile(
        trips_per_year=base_trips,
        effective_trips=effective_trips,
        interval_days=interval,
        effective_interval_days=effective_interval,
        annual_capacity_m3=annual_capacity,
        annual_cold_capacity_m3=annual_cold,
        is_scheduled=is_scheduled,
        access_factor=access_factor,
    )


def stockout_risk(
    *,
    interval_days_: float,
    storage_days: float,
    demand_cv: float,
    lead_time_sd_days: float,
    reliability: float,
    access_factor: float,
    safety_stock_days: float = 0.0,
) -> float:
    """Probability that a facility runs dry before its next delivery, per cycle.

    Returns a value in [0, 1]. A lane with zero access this month returns 1.0: the
    facility is unreachable, so it is certain to be relying on whatever is on its
    shelf, which the model treats as a stockout against the delivery plan.
    """
    if access_factor <= 0.0:
        return 1.0
    if not math.isfinite(interval_days_):
        return 1.0

    # Days of stock the facility can actually hold after a delivery.
    cover = min(storage_days, interval_days_ + safety_stock_days)
    if cover <= 0:
        return 1.0

    def _risk(cycle_days: float) -> float:
        mean_consumption = cycle_days
        sd = math.sqrt((demand_cv**2) * max(cycle_days, 1.0) + lead_time_sd_days**2)
        if sd <= 1e-9:
            return 1.0 if mean_consumption > cover else 0.0
        return 1.0 - _phi((cover - mean_consumption) / sd)

    # The service does not always sail. When it misses, the interval doubles.
    reliability = max(0.0, min(1.0, reliability))
    risk = reliability * _risk(interval_days_) + (1.0 - reliability) * _risk(2 * interval_days_)
    return max(0.0, min(1.0, risk))


def storage_days_for(node, annual_demand_m3: float, temperature_band: str = "ambient") -> float:
    """How many days of demand a node's shelf and cold-chain space can hold."""
    if annual_demand_m3 <= 0:
        return 365.0
    capacity = node.capacity or {}
    if temperature_band == "ambient":
        space = float(capacity.get("dry_m3", 0.0) or 0.0)
    else:
        cold = capacity.get("cold_by_band", {}) or {}
        space = float(cold.get(temperature_band, 0.0) or 0.0)
    if space <= 0:
        return 0.0
    daily = annual_demand_m3 / 365.0
    return space / daily if daily > 0 else 365.0


def facility_risk(
    node,
    edge,
    annual_demand_m3: float,
    *,
    month: Optional[int] = None,
    frequency_override: Optional[str] = None,
    safety_stock_days: float = 0.0,
) -> dict:
    """Full risk picture for one facility served by one lane."""
    prof = profile_for(edge, month=month, frequency_override=frequency_override)
    storage_days = storage_days_for(node, annual_demand_m3)
    cv = DEMAND_CV_BY_LEVEL.get(node.level, 0.4)

    risk = stockout_risk(
        interval_days_=prof.interval_days,
        storage_days=storage_days,
        demand_cv=cv,
        lead_time_sd_days=edge.lead_time_sd_days,
        reliability=edge.reliability,
        access_factor=prof.access_factor,
        safety_stock_days=safety_stock_days,
    )

    # Storage is "binding" when the facility physically cannot hold a full delivery
    # cycle. This is the mechanism behind the boat-frequency demo: stretching the
    # interval past the shelf capacity makes risk climb non-linearly.
    storage_binding = storage_days < prof.interval_days

    return {
        "node_code": node.code,
        "node_name": node.name,
        "edge_code": edge.code,
        "service_name": edge.service_name,
        "mode": edge.mode,
        "frequency": frequency_override or edge.service_frequency,
        "interval_days": round(prof.interval_days, 1),
        "storage_days": round(storage_days, 1),
        "storage_binding": storage_binding,
        "access_factor": round(prof.access_factor, 3),
        "stockout_risk": round(risk, 4),
        "expected_days_out_per_year": round(risk * 365.0, 1),
    }
