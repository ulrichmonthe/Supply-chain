"""Landed cost per cubic metre on a lane.

Kept deliberately transparent: every cost a scenario reports must decompose into
line items an expert can read out in a workshop, because the tool's output is a
consultancy deliverable, not a black box. There are exactly four contributions:

    transport   distance x rate, or trip cost spread over the hold
    handling    per-m3 charge at the origin hub
    seasonal    the month's cost multiplier on the lane
    fixed       annualised hub operating cost, charged once per open hub

Levers that move these numbers (fuel index, third-party share, demand growth) are
applied here and nowhere else, so there is one place to look when a number moves.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import seasonality, service

#: Cost of a self-run road trip, per vehicle, before distance.
DEFAULT_ROAD_TRIP_FIXED = 120.0
#: Per-km running cost of a self-run 8 m3 truck (fuel, maintenance, driver time).
DEFAULT_ROAD_VARIABLE_PER_KM = 1.90
DEFAULT_ROAD_VEHICLE_M3 = 8.0

#: Third-party logistics rate, per m3 per km. Used when the 3PL lever is engaged.
THIRD_PARTY_RATE_PER_M3_KM = 0.42

#: Handling cost at an origin hub, per m3 moved (labour, picking, packing, dock).
HANDLING_COST_PER_M3 = 14.0
#: Cold chain adds active packaging, monitoring and a higher loss allowance.
COLD_HANDLING_UPLIFT_PER_M3 = 46.0


@dataclass(slots=True)
class LaneCost:
    unit_cost_per_m3: float
    transport_per_m3: float
    handling_per_m3: float
    season_multiplier: float
    method: str

    def as_dict(self) -> dict:
        return {
            "unit_cost_per_m3": round(self.unit_cost_per_m3, 2),
            "transport_per_m3": round(self.transport_per_m3, 2),
            "handling_per_m3": round(self.handling_per_m3, 2),
            "season_multiplier": round(self.season_multiplier, 3),
            "method": self.method,
        }


def lane_cost(
    edge,
    *,
    month: int | None = None,
    fuel_index: float = 1.0,
    third_party_share: float = 0.0,
    cold_share: float = 0.0,
) -> LaneCost:
    """Cost to move one cubic metre along ``edge``.

    ``cold_share`` is the fraction of the volume needing an active temperature band;
    it only affects handling, because the cold hold on a lane is priced through
    ``cold_capacity_per_trip_m3`` rather than a separate tariff.
    """
    multiplier = (
        seasonality.cost_multiplier(edge, month) if month else seasonality.annual_cost_multiplier(edge)
    )

    if edge.cost_per_m3 and edge.cost_per_m3 > 0:
        # A quoted freight rate (air cargo, coastal shipping tariff) beats a
        # reconstructed one. Distance still drives fuel-linked escalation.
        transport = edge.cost_per_m3 * fuel_index
        method = "quoted_rate"
    elif edge.service_frequency and edge.capacity_per_trip_m3 > 0:
        # Scheduled service: the trip sails whether or not you fill it, so the
        # honest unit cost spreads the trip cost over the hold actually available.
        trip_cost = edge.fixed_cost_per_trip + edge.variable_cost_per_km * edge.distance_km * fuel_index
        transport = trip_cost / max(1e-6, edge.capacity_per_trip_m3)
        method = "trip_cost_over_hold"
    else:
        # On-demand road movement, costed as a round trip on a standard vehicle.
        fixed = edge.fixed_cost_per_trip or DEFAULT_ROAD_TRIP_FIXED
        per_km = (edge.variable_cost_per_km or DEFAULT_ROAD_VARIABLE_PER_KM) * fuel_index
        trip_cost = fixed + per_km * edge.distance_km * 2.0
        own_cost = trip_cost / DEFAULT_ROAD_VEHICLE_M3
        third_party_cost = THIRD_PARTY_RATE_PER_M3_KM * edge.distance_km * fuel_index
        share = max(0.0, min(1.0, third_party_share))
        transport = own_cost * (1 - share) + third_party_cost * share
        method = "reconstructed_road" if share < 0.5 else "third_party_blend"

    handling = HANDLING_COST_PER_M3 + COLD_HANDLING_UPLIFT_PER_M3 * max(0.0, min(1.0, cold_share))
    unit = transport * multiplier + handling
    return LaneCost(unit, transport * multiplier, handling, multiplier, method)


def hub_annual_fixed_cost(node, *, integration_policy: str = "vertical") -> float:
    """Annualised cost of running a node as a hub.

    ``integration_policy`` reflects whether programme supply chains (EPI, malaria,
    TB/HIV) run their own parallel stores or share one. Integration is the single
    largest structural saving available in most LMIC networks, and also the hardest
    politically, which is exactly why it belongs in the scenario levers.
    """
    base = float(node.hub_fixed_cost or 0.0)
    if integration_policy == "integrated":
        return base * 0.78
    if integration_policy == "partial":
        return base * 0.90
    return base


def demand_scaling(levers: dict) -> float:
    """Growth lever applied uniformly to demand quantities."""
    return max(0.0, 1.0 + float(levers.get("demand_growth", 0.0) or 0.0))


def lane_annual_capacity_m3(edge, month: int | None, frequency_override: str | None) -> float:
    return service.profile_for(edge, month=month, frequency_override=frequency_override).annual_capacity_m3
