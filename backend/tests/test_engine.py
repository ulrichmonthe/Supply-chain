"""Tests for the modelling engine."""

from __future__ import annotations

import pytest

from app.engine import seasonality, service
from app.engine.allocation import FacilityIn, HubIn, LaneIn, SolveOptions, solve
from app.engine.costing import lane_cost
from app.engine.distance import resolve_distance
from app.engine.equity import assign_strata, compute_vulnerability, equity_penalty_weight
from app.engine.geo import haversine_km, offshore_km
from app.seed.png import BOUNDARY as PNG_BOUNDARY


# --- geo ---------------------------------------------------------------------------


def test_haversine_matches_known_distance():
    # Port Moresby to Lae is about 300 km great circle.
    km = haversine_km(-9.4438, 147.1803, -6.7333, 146.9833)
    assert 290 < km < 310


def test_land_mask_accepts_real_towns():
    for lat, lon in [
        (-9.4747, 147.1925),  # Port Moresby
        (-5.8575, 144.2300),  # Mount Hagen, deep interior
        (-4.3500, 152.2667),  # Kokopo, New Britain
        (-2.5744, 150.7967),  # Kavieng, New Ireland
        (-6.2167, 155.5667),  # Arawa, Bougainville
        (-2.0226, 147.2712),  # Lorengau, Manus
    ]:
        assert offshore_km(PNG_BOUNDARY, lat, lon) == 0.0, (lat, lon)


def test_land_mask_rejects_open_water():
    """The failure this whole check exists for: a facility in the Bismarck Sea."""
    assert offshore_km(PNG_BOUNDARY, -3.5, 149.0) > 100


def test_land_mask_absent_for_unconfigured_country():
    """A country nobody has drawn yet loses the check rather than failing every point."""
    assert offshore_km(None, -1.29, 36.82) is None
    assert offshore_km({}, -1.29, 36.82) is None
    assert offshore_km({"polygons": [], "buffers": []}, -1.29, 36.82) is None


# --- distance cascade ---------------------------------------------------------------


def test_manual_distance_wins_and_is_labelled():
    result = resolve_distance(
        -9.44, 147.18, -6.73, 146.98, mode="road", manual_km=615.0, manual_note="Field interview"
    )
    assert result.distance_km == 615.0
    assert result.method == "manual"
    assert result.confidence > 0.9
    assert "Field interview" in result.note


def test_detour_factor_inflates_and_records_terrain():
    straight = haversine_km(-5.85, 144.23, -5.49, 143.72)
    highlands = resolve_distance(-5.85, 144.23, -5.49, 143.72, mode="road", terrain_class="highlands_road")
    mainland = resolve_distance(-5.85, 144.23, -5.49, 143.72, mode="road", terrain_class="mainland_road")

    assert highlands.method == "detour_factor"
    assert highlands.distance_km > mainland.distance_km > straight
    assert "highlands_road" in highlands.note


def test_air_uses_great_circle():
    result = resolve_distance(-9.44, 147.18, -5.12, 141.63, mode="air")
    assert result.method == "great_circle"
    assert result.distance_km == pytest.approx(haversine_km(-9.44, 147.18, -5.12, 141.63))


# --- seasonality ---------------------------------------------------------------------


def test_every_profile_has_twelve_months():
    for name, vector in seasonality.PROFILES.items():
        assert len(vector) == 12, name
        assert all(0.0 <= value <= 1.0 for value in vector), name
    for name, vector in seasonality.COST_PROFILES.items():
        assert len(vector) == 12, name


def test_floodplain_closes_in_the_monsoon_and_opens_in_the_dry():
    vector = seasonality.PROFILES["lowland_floodplain"]
    assert vector[1] == 0.0  # February
    assert vector[6] == 1.0  # July


# --- scheduled services and stockout risk ----------------------------------------------


class FakeEdge:
    def __init__(self, **kwargs):
        self.monthly_access = kwargs.pop("monthly_access", [1.0] * 12)
        self.monthly_cost_multiplier = kwargs.pop("monthly_cost_multiplier", [1.0] * 12)
        self.service_frequency = kwargs.pop("service_frequency", None)
        self.capacity_per_trip_m3 = kwargs.pop("capacity_per_trip_m3", 0.0)
        self.cold_capacity_per_trip_m3 = kwargs.pop("cold_capacity_per_trip_m3", 0.0)
        self.reliability = kwargs.pop("reliability", 0.9)
        self.lead_time_sd_days = kwargs.pop("lead_time_sd_days", 2.0)
        self.fixed_cost_per_trip = kwargs.pop("fixed_cost_per_trip", 0.0)
        self.variable_cost_per_km = kwargs.pop("variable_cost_per_km", 0.0)
        self.cost_per_m3 = kwargs.pop("cost_per_m3", 0.0)
        self.distance_km = kwargs.pop("distance_km", 100.0)
        for key, value in kwargs.items():
            setattr(self, key, value)


def _risk(interval_days: float, storage_days: float = 24.0) -> float:
    return service.stockout_risk(
        interval_days_=interval_days,
        storage_days=storage_days,
        demand_cv=0.45,
        lead_time_sd_days=4.0,
        reliability=0.74,
        access_factor=1.0,
        safety_stock_days=14.0,
    )


def test_stockout_risk_rises_as_the_boat_comes_less_often():
    """The signature demo, as an assertion: halve the frequency, watch risk climb."""
    weekly = _risk(7.0)
    fortnightly = _risk(14.0)
    monthly = _risk(30.0)

    assert weekly < fortnightly < monthly
    assert weekly < 0.05
    assert fortnightly > 0.15
    assert monthly > 0.90


def test_storage_capacity_is_what_makes_the_interval_bite():
    """At the same interval, a cramped facility is materially worse off than a roomy one.

    Note that the roomy facility is not risk-free: on this lane the service only sails
    74% of the time, and a missed sailing doubles the interval. That residual is the
    reliability of the service, not the size of the store, and no amount of shelving
    removes it — which is itself worth being able to show a minister.
    """
    roomy = _risk(14.0, storage_days=90.0)
    cramped = _risk(14.0, storage_days=18.0)
    assert cramped > roomy * 2

    # Give the same cramped facility a dependable service and the risk collapses.
    dependable = service.stockout_risk(
        interval_days_=14.0,
        storage_days=18.0,
        demand_cv=0.45,
        lead_time_sd_days=4.0,
        reliability=0.99,
        access_factor=1.0,
        safety_stock_days=14.0,
    )
    assert dependable < cramped / 2


def test_unreachable_lane_is_certain_stockout():
    assert (
        service.stockout_risk(
            interval_days_=7.0,
            storage_days=60.0,
            demand_cv=0.3,
            lead_time_sd_days=2.0,
            reliability=0.95,
            access_factor=0.0,
        )
        == 1.0
    )


def test_scheduled_lane_capacity_scales_with_frequency_and_season():
    edge = FakeEdge(
        service_frequency="WEEKLY",
        capacity_per_trip_m3=20.0,
        reliability=1.0,
        monthly_access=seasonality.profile("sea_exposed"),
    )
    weekly = service.profile_for(edge, month=None)
    monthly = service.profile_for(edge, month=None, frequency_override="MONTHLY")
    assert weekly.annual_capacity_m3 > monthly.annual_capacity_m3

    calm = service.profile_for(edge, month=11)
    rough = service.profile_for(edge, month=7)
    assert calm.annual_capacity_m3 > rough.annual_capacity_m3


def test_on_demand_road_lane_is_not_capacity_limited():
    edge = FakeEdge(service_frequency="MONTHLY", capacity_per_trip_m3=0.0)
    assert service.profile_for(edge).annual_capacity_m3 == float("inf")


# --- costing ---------------------------------------------------------------------------


def test_quoted_rate_beats_a_reconstructed_one():
    edge = FakeEdge(cost_per_m3=2400.0, service_frequency="MONTHLY", capacity_per_trip_m3=0.0)
    cost = lane_cost(edge)
    assert cost.method == "quoted_rate"
    assert cost.transport_per_m3 == pytest.approx(2400.0)


def test_scheduled_trip_cost_is_spread_over_the_hold():
    edge = FakeEdge(
        service_frequency="WEEKLY",
        capacity_per_trip_m3=20.0,
        fixed_cost_per_trip=2000.0,
        variable_cost_per_km=3.0,
        distance_km=100.0,
    )
    cost = lane_cost(edge)
    assert cost.method == "trip_cost_over_hold"
    assert cost.transport_per_m3 == pytest.approx((2000.0 + 300.0) / 20.0)


def test_season_multiplier_raises_wet_month_cost():
    edge = FakeEdge(
        distance_km=100.0,
        monthly_cost_multiplier=seasonality.cost_profile("lowland_floodplain"),
    )
    assert lane_cost(edge, month=2).unit_cost_per_m3 > lane_cost(edge, month=7).unit_cost_per_m3


def test_fuel_index_moves_transport_but_not_handling():
    edge = FakeEdge(distance_km=200.0)
    base = lane_cost(edge, fuel_index=1.0)
    dearer = lane_cost(edge, fuel_index=1.5)
    assert dearer.transport_per_m3 > base.transport_per_m3
    assert dearer.handling_per_m3 == base.handling_per_m3


# --- equity -------------------------------------------------------------------------------


class FakeNode:
    def __init__(self, node_id, code, lat, lon, terrain, population):
        self.id = node_id
        self.code = code
        self.lat = lat
        self.lon = lon
        self.terrain_class = terrain
        self.catchment_population = population


def _equity_fixture():
    hub = FakeNode(1, "HUB", -9.45, 147.21, "mainland_road", 0)
    near = FakeNode(2, "NEAR", -9.47, 147.19, "mainland_road", 100000)
    far = FakeNode(3, "FAR", -5.12, 141.63, "remote_air_only", 20000)
    good_lane = FakeEdge(base_travel_time_hr=0.4, mode="road", monthly_access=[1.0] * 12)
    bad_lane = FakeEdge(
        base_travel_time_hr=3.0, mode="air", monthly_access=seasonality.profile("air_highlands")
    )
    return hub, near, far, {2: [good_lane], 3: [bad_lane]}


def test_vulnerability_is_bounded_and_ordered():
    hub, near, far, lanes = _equity_fixture()
    scores = compute_vulnerability([near, far], [hub], lanes)
    assert all(0.0 <= v.score <= 1.0 for v in scores.values())
    assert scores[far.id].score > scores[near.id].score


def test_facility_with_no_lane_is_maximally_isolated():
    hub, near, far, _ = _equity_fixture()
    scores = compute_vulnerability([near], [hub], {})
    assert scores[near.id].restricted_months == 12


def test_strata_partition_every_facility():
    hub, near, far, lanes = _equity_fixture()
    scores = compute_vulnerability([near, far], [hub], lanes)
    strata = assign_strata(scores)
    assert set(strata) == {near.id, far.id}
    assert all(0 <= s <= 4 for s in strata.values())


def test_equity_weight_makes_vulnerable_shortfalls_cost_more():
    assert equity_penalty_weight(1.0, 0.0) == pytest.approx(1.0)
    assert equity_penalty_weight(0.0, 1.0) == pytest.approx(1.0)
    assert equity_penalty_weight(1.0, 1.0) > equity_penalty_weight(0.2, 1.0) > 1.0


# --- allocation ------------------------------------------------------------------------------


def _tiny_model():
    facilities = [
        FacilityIn(id=1, code="F1", demand_m3=100.0, stratum=0, unmet_penalty=1000.0),
        FacilityIn(id=2, code="F2", demand_m3=100.0, stratum=4, vulnerability=0.9, unmet_penalty=1000.0),
    ]
    hubs = [HubIn(id=10, code="H", fixed_cost=0.0, throughput_m3=0.0, forced_open=True)]
    lanes = [
        LaneIn(edge_id=1, code="L1", hub_id=10, facility_id=1, unit_cost=10.0),
        LaneIn(edge_id=2, code="L2", hub_id=10, facility_id=2, unit_cost=1500.0),
    ]
    return facilities, hubs, lanes


def test_solver_serves_cheap_demand_and_drops_expensive_demand():
    facilities, hubs, lanes = _tiny_model()
    solution = solve(facilities, hubs, lanes, SolveOptions())
    assert solution.feasible
    assert solution.served[1] == pytest.approx(100.0)
    # Serving F2 costs more per m3 than the penalty for not serving it.
    assert solution.served[2] == pytest.approx(0.0, abs=1e-6)


def test_equity_floor_forces_the_expensive_facility_to_be_served():
    facilities, hubs, lanes = _tiny_model()
    solution = solve(facilities, hubs, lanes, SolveOptions(equity_floor=0.9))
    assert solution.feasible
    assert solution.served[2] >= 90.0 - 1e-6


def test_lane_capacity_is_respected():
    facilities, hubs, lanes = _tiny_model()
    lanes[0].capacity_m3 = 40.0
    solution = solve(facilities, hubs, lanes, SolveOptions())
    assert solution.served[1] == pytest.approx(40.0)


def test_hub_fixed_cost_can_make_opening_it_not_worth_it():
    facilities = [FacilityIn(id=1, code="F1", demand_m3=1.0, unmet_penalty=10.0)]
    hubs = [HubIn(id=10, code="H", fixed_cost=1_000_000.0, forced_open=None)]
    lanes = [LaneIn(edge_id=1, code="L1", hub_id=10, facility_id=1, unit_cost=1.0)]
    solution = solve(facilities, hubs, lanes, SolveOptions())
    assert solution.feasible
    assert solution.hubs_open == []


def test_infeasible_equity_floor_reports_what_is_attainable():
    facilities, hubs, lanes = _tiny_model()
    lanes[1].capacity_m3 = 10.0  # only 10 of F2's 100 m3 can ever be delivered
    solution = solve(facilities, hubs, lanes, SolveOptions(equity_floor=0.95))

    assert not solution.feasible
    diagnosis = solution.log["diagnosis"]
    assert diagnosis["max_attainable_equity_floor"] == pytest.approx(0.10, abs=0.01)
    assert "10%" in solution.log["reason"]


def test_model_with_no_lanes_fails_clearly_rather_than_crashing():
    facilities, hubs, _ = _tiny_model()
    solution = solve(facilities, hubs, [], SolveOptions())
    assert not solution.feasible
    assert solution.status == "no_lanes"
