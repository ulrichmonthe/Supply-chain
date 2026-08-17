"""Build the PNG reference network in the database.

Distances are not hard-coded here. They are produced by the distance cascade at load
time and stored with the method that produced them, so the seeded network carries the
same provenance as an imported one. Nothing is privileged for being built in.
"""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine import seasonality
from ..engine.distance import resolve_distance
from ..engine.geo import haversine_km
from ..models import AuditEntry, Country, Demand, Edge, Node, Product, Scenario
from . import png

#: Days of stock a facility can physically hold, by terrain class.
STORAGE_COVER_DAYS = {
    "mainland_road": 60.0,
    "coastal_road": 40.0,
    "highlands_road": 45.0,
    "riverine": 30.0,
    "island": 24.0,
    "remote_air_only": 26.0,
}
STORAGE_TYPE_MULTIPLIER = png.STORAGE_TYPE_MULTIPLIER

#: Road lanes are on demand, but a facility is still visited on a rhythm. This is the
#: default resupply rhythm for road; the delivery-frequency lever overrides it.
DEFAULT_ROAD_FREQUENCY = "MONTHLY"
ROAD_RELIABILITY = 0.92


class FacilitySpec(NamedTuple):
    """A row of ``png.FACILITIES``, named so the loader reads as prose."""

    code: str
    name: str
    type: str
    admin1: str
    admin2: str
    lat: float
    lon: float
    terrain: str
    population: int
    hub: str
    mode: str
    profile: str
    service_key: str | None


def country_exists(session: Session, code: str) -> bool:
    return session.scalar(select(Country).where(Country.code == code)) is not None


def _annual_volumes(population: float) -> tuple[float, dict[str, float], dict[str, float]]:
    """Annual demand for a catchment: total m3, m3 by band, and units by SKU."""
    per_band: dict[str, float] = {}
    units: dict[str, float] = {}
    total = 0.0
    for sku, _, band, volume_cm3, _, _, per_1000 in png.PRODUCTS:
        quantity = population / 1000.0 * per_1000
        volume_m3 = quantity * volume_cm3 / 1_000_000.0
        units[sku] = quantity
        per_band[band] = per_band.get(band, 0.0) + volume_m3
        total += volume_m3
    return total, per_band, units


def _storage_capacity(node_type: str, terrain: str, per_band: dict[str, float]) -> dict:
    cover = STORAGE_COVER_DAYS.get(terrain, 30.0) * STORAGE_TYPE_MULTIPLIER.get(node_type, 1.0)
    factor = cover / 365.0
    return {
        "dry_m3": round(per_band.get("ambient", 0.0) * factor, 2),
        "cold_by_band": {
            "+2-8": round(per_band.get("+2-8", 0.0) * factor, 3),
            "-20": round(per_band.get("-20", 0.0) * factor, 3),
            "-70": round(per_band.get("-70", 0.0) * factor, 3),
        },
        "cover_days_assumed": round(cover, 1),
    }


def _make_edge(
    country: Country,
    code: str,
    origin: Node,
    destination: Node,
    *,
    mode: str,
    profile_name: str,
    frequency: str | None,
    service_name: str | None = None,
    service_days: list[str] | None = None,
    capacity_per_trip_m3: float = 0.0,
    cold_capacity_per_trip_m3: float = 0.0,
    fixed_cost_per_trip: float = 0.0,
    variable_cost_per_km: float = 0.0,
    cost_per_m3: float = 0.0,
    reliability: float = 0.9,
    lead_time_sd_days: float = 2.0,
) -> Edge:
    resolved = resolve_distance(
        origin.lat,
        origin.lon,
        destination.lat,
        destination.lon,
        mode=mode,
        terrain_class=destination.terrain_class,
    )
    return Edge(
        country_id=country.id,
        code=code,
        from_node_id=origin.id,
        to_node_id=destination.id,
        mode=mode,
        service_name=service_name,
        service_frequency=frequency,
        service_days=service_days,
        capacity_per_trip_m3=capacity_per_trip_m3,
        cold_capacity_per_trip_m3=cold_capacity_per_trip_m3,
        fixed_cost_per_trip=fixed_cost_per_trip,
        variable_cost_per_km=variable_cost_per_km,
        cost_per_m3=cost_per_m3,
        monthly_access=seasonality.profile(profile_name),
        monthly_cost_multiplier=seasonality.cost_profile(profile_name),
        reliability=reliability,
        lead_time_sd_days=lead_time_sd_days,
        attributes={"access_profile": profile_name},
        **resolved.as_edge_fields(),
    )


def seed_png(session: Session) -> Country:
    """Create the PNG country workspace. Idempotent by country code."""
    existing = session.scalar(select(Country).where(Country.code == png.COUNTRY["code"]))
    if existing:
        return existing

    country = Country(
        code=png.COUNTRY["code"],
        name=png.COUNTRY["name"],
        currency=png.COUNTRY["currency"],
        config=png.COUNTRY["config"],
    )
    session.add(country)
    session.flush()

    # --- products -----------------------------------------------------------------
    products: dict[str, Product] = {}
    for sku, name, band, volume_cm3, unit_cost, shelf_life, _ in png.PRODUCTS:
        product = Product(
            country_id=country.id,
            sku=sku,
            name=name,
            temperature_band=band,
            volume_per_unit_cm3=volume_cm3,
            unit_cost=unit_cost,
            shelf_life_days=shelf_life,
        )
        session.add(product)
        products[sku] = product
    session.flush()

    # --- hubs ---------------------------------------------------------------------
    nodes: dict[str, Node] = {}
    for (code, name, level, lat, lon, admin1, dry, cold, throughput, fixed,
         status, capex) in png.HUBS:
        node = Node(
            country_id=country.id,
            code=code,
            name=name,
            level=level,
            type="national_store" if level == 0 else "area_store",
            lat=lat,
            lon=lon,
            geocode_confidence=0.85,
            geocode_source="town_centroid",
            admin1=admin1,
            terrain_class="mainland_road",
            capacity={"dry_m3": dry, "cold_by_band": {"+2-8": cold, "-20": cold * 0.1, "-70": cold * 0.04}},
            operating_status=status,
            catchment_population=0.0,
            hub_capable=level == 1,
            hub_fixed_cost=fixed,
            hub_open_capex=capex,
            hub_throughput_m3=throughput,
            external_ids={"mfl_code": code},
        )
        session.add(node)
        nodes[code] = node
    session.flush()

    # --- facilities ----------------------------------------------------------------
    facility_volumes: dict[str, float] = {}
    facility_units: dict[str, dict[str, float]] = {}
    facility_spec: dict[str, FacilitySpec] = {}

    for raw in png.FACILITIES:
        spec = FacilitySpec(*raw)
        total_m3, per_band, units = _annual_volumes(spec.population)
        facility_volumes[spec.code] = total_m3
        facility_units[spec.code] = units
        facility_spec[spec.code] = spec

        node = Node(
            country_id=country.id,
            code=spec.code,
            name=spec.name,
            level=3,
            type=spec.type,
            lat=spec.lat,
            lon=spec.lon,
            geocode_confidence=0.7,
            geocode_source="town_centroid",
            admin1=spec.admin1,
            admin2=spec.admin2,
            terrain_class=spec.terrain,
            catchment_population=float(spec.population),
            capacity=_storage_capacity(spec.type, spec.terrain, per_band),
            operating_status="operational",
            hub_capable=False,
            external_ids={"mfl_code": spec.code},
        )
        session.add(node)
        nodes[spec.code] = node
    session.flush()

    for code, units in facility_units.items():
        for sku, quantity in units.items():
            session.add(
                Demand(
                    country_id=country.id,
                    node_id=nodes[code].id,
                    product_id=products[sku].id,
                    period=0,
                    quantity=round(quantity, 2),
                    source="proxy",
                    confidence=0.4,
                )
            )
    session.flush()

    # --- primary lanes ---------------------------------------------------------------
    for (code, origin_code, destination_code, mode, frequency, capacity, cold,
         fixed, per_km, profile_name, reliability, service_name) in png.PRIMARY_LANES:
        session.add(
            _make_edge(
                country,
                code,
                nodes[origin_code],
                nodes[destination_code],
                mode=mode,
                profile_name=profile_name,
                frequency=frequency,
                service_name=service_name,
                capacity_per_trip_m3=capacity,
                cold_capacity_per_trip_m3=cold,
                fixed_cost_per_trip=fixed,
                variable_cost_per_km=per_km,
                reliability=reliability,
                lead_time_sd_days=3.0,
            )
        )

    # --- scheduled service lanes -------------------------------------------------------
    # A vessel's hold is shared by every facility on its run, so it is apportioned by
    # demand share rather than given to each lane in full. The trip cost is computed
    # once for the whole loop and expressed as a freight rate per cubic metre, which is
    # both how coastal freight is actually tariffed and the only way to avoid charging
    # a return journey to every port on the route.
    service_members: dict[str, list[str]] = {}
    for spec in facility_spec.values():
        if spec.service_key:
            service_members.setdefault(spec.service_key, []).append(spec.code)

    service_rate: dict[str, float] = {}
    for service_key, members in service_members.items():
        (name, mode, frequency, days, capacity, cold, fixed, per_km,
         quoted, reliability, lead_sd) = png.SERVICES[service_key]
        origin = nodes[facility_spec[members[0]].hub]

        if quoted > 0:
            rate = quoted
        else:
            ordered = sorted(
                members,
                key=lambda c: haversine_km(origin.lat, origin.lon, nodes[c].lat, nodes[c].lon),
            )
            route_km = 0.0
            previous = origin
            for member in ordered:
                route_km += haversine_km(previous.lat, previous.lon, nodes[member].lat, nodes[member].lon)
                previous = nodes[member]
            route_km += haversine_km(previous.lat, previous.lon, origin.lat, origin.lon)
            route_km *= 1.22  # coastal track rather than straight line
            rate = (fixed + per_km * route_km) / max(1e-6, capacity)
        service_rate[service_key] = rate

        total_service_m3 = sum(facility_volumes[c] for c in members) or 1.0
        for member in members:
            share = facility_volumes[member] / total_service_m3
            spec = facility_spec[member]
            session.add(
                _make_edge(
                    country,
                    f"LN-{member}-{service_key}",
                    nodes[spec.hub],
                    nodes[member],
                    mode=mode,
                    profile_name=spec.profile,
                    frequency=frequency,
                    service_name=name,
                    service_days=days,
                    capacity_per_trip_m3=round(capacity * share, 3),
                    cold_capacity_per_trip_m3=round(cold * share, 4),
                    cost_per_m3=round(rate, 2),
                    reliability=reliability,
                    lead_time_sd_days=lead_sd,
                )
            )

    # --- road lanes ---------------------------------------------------------------------
    for spec in facility_spec.values():
        if spec.service_key or spec.mode != "road":
            continue
        session.add(
            _make_edge(
                country,
                f"LN-{spec.code}-ROAD",
                nodes[spec.hub],
                nodes[spec.code],
                mode="road",
                profile_name=spec.profile,
                frequency=DEFAULT_ROAD_FREQUENCY,
                service_name=f"{nodes[spec.hub].name} road distribution",
                capacity_per_trip_m3=0.0,  # on demand: send another truck
                reliability=ROAD_RELIABILITY,
                lead_time_sd_days=2.0,
            )
        )

    # --- candidate hub lanes ---------------------------------------------------------------
    for hub_code, members in png.CANDIDATE_HUB_CATCHMENTS.items():
        hub = nodes[hub_code]
        for member in members:
            spec = facility_spec[member]
            if spec.service_key:
                service_key = spec.service_key
                (name, service_mode, frequency, days, capacity, cold, fixed, per_km,
                 quoted, reliability, lead_sd) = png.SERVICES[service_key]
                members_on_service = [m for m in service_members[service_key] if m in members]
                total_m3 = sum(facility_volumes[m] for m in members_on_service) or 1.0
                share = facility_volumes[member] / total_m3
                # Shorter run from a closer hub: the rate falls with the distance saved.
                base_rate = service_rate[service_key]
                original_km = haversine_km(
                    nodes[spec.hub].lat, nodes[spec.hub].lon, nodes[member].lat, nodes[member].lon
                )
                new_km = haversine_km(hub.lat, hub.lon, nodes[member].lat, nodes[member].lon)
                scale = max(0.35, min(1.0, (new_km + 40.0) / (original_km + 40.0)))
                session.add(
                    _make_edge(
                        country,
                        f"LN-{member}-{hub_code}",
                        hub,
                        nodes[member],
                        mode=service_mode,
                        profile_name=spec.profile,
                        frequency="WEEKLY" if frequency in ("FORTNIGHTLY", "MONTHLY") else frequency,
                        service_name=f"{hub.name} — {name}",
                        service_days=days,
                        capacity_per_trip_m3=round(capacity * share * 0.8, 3),
                        cold_capacity_per_trip_m3=round(cold * share * 0.8, 4),
                        cost_per_m3=round(base_rate * scale, 2),
                        reliability=min(0.95, reliability + 0.06),
                        lead_time_sd_days=max(1.5, lead_sd - 1.5),
                    )
                )
            else:
                session.add(
                    _make_edge(
                        country,
                        f"LN-{member}-{hub_code}",
                        hub,
                        nodes[member],
                        mode=spec.mode,
                        profile_name=spec.profile,
                        frequency=DEFAULT_ROAD_FREQUENCY,
                        service_name=f"{hub.name} road distribution",
                        reliability=ROAD_RELIABILITY,
                    )
                )

    # --- wet season air fallback ------------------------------------------------------------
    (name, mode, frequency, days, capacity, cold, fixed, per_km, quoted,
     reliability, lead_sd) = png.AIR_FALLBACK
    for member in png.AIR_FALLBACK_FOR:
        spec = facility_spec[member]
        session.add(
            _make_edge(
                country,
                f"LN-{member}-AIRFB",
                nodes[spec.hub],
                nodes[member],
                mode="air",
                profile_name="air_all_year",
                frequency=frequency,
                service_name=name,
                service_days=days,
                capacity_per_trip_m3=capacity,
                cold_capacity_per_trip_m3=cold,
                cost_per_m3=quoted,
                reliability=reliability,
                lead_time_sd_days=lead_sd,
            )
        )

    session.flush()
    _seed_audit(session, country)
    _seed_scenarios(session, country)
    session.commit()
    return country


def _seed_audit(session: Session, country: Country) -> None:
    """Record what is assumed, so the assumptions panel is populated from day one."""
    entries = [
        ("global", "", "data_status", "ILLUSTRATIVE", "seed", "U",
         "Facility locations, provinces, modes and network structure are real. Demand, "
         "storage, costs and every timetable are placeholders with plausible magnitudes."),
        ("global", "", "demand_method", "population proxy", "assumption", "I",
         "Demand is derived from catchment population and a per-1000 consumption rate per "
         "product. Replace with mSupply issues data. This is the same proxy-indicator "
         "strategy SCANIT uses, and it is a good one — but it must be labelled."),
        ("global", "", "storage_capacity", "days-of-cover assumption", "assumption", "U",
         "Facility storage is assumed as days of cover by terrain class: 60 mainland, 45 "
         "highlands, 40 coastal, 30 riverine, 26 air-only, 24 island, multiplied by "
         "facility type. Replace with the national cold chain inventory and a storage "
         "assessment."),
        ("global", "", "timetables", "assumed frequencies", "assumption", "U",
         "Every sailing and flight frequency, hold capacity and reliability figure is "
         "assumed. Confirming whether digitised maritime and air schedules exist for "
         "medical distribution is a Sprint 0 task, and the answer determines whether the "
         "scheduled-service model has any data behind it."),
        ("global", "", "distances", "detour factor cascade", "seed", "I",
         "No OSRM instance is configured in this deployment, so road distances are great "
         "circle distances inflated by a terrain-specific detour factor: 1.35 mainland, "
         "1.75 highlands, 1.45 coastal. Sea legs use 1.22, river 1.55, air 1.0."),
        ("global", "", "primary_network", "no POM road link", "assumption", "S",
         "Port Moresby has no road connection to Lae, Madang or the Highlands. Primary "
         "distribution to every hub other than Port Moresby is modelled as maritime, with "
         "the Highlands supplied by road from Lae along the Highlands Highway."),
        ("global", "", "vessel_cost_allocation", "hold apportioned by demand share", "assumption", "I",
         "A vessel's hold is shared across every facility on its run. Capacity is "
         "apportioned by demand share and the trip cost is expressed as a freight rate per "
         "cubic metre computed over the whole loop, so no facility is charged for a return "
         "journey the boat did not make on its behalf."),
    ]
    for entity_type, entity_ref, field, value, provenance, marker, rationale in entries:
        session.add(
            AuditEntry(
                country_id=country.id,
                entity_type=entity_type,
                entity_ref=entity_ref,
                field=field,
                new_value=value,
                provenance=provenance,
                confidence_marker=marker,
                rationale=rationale,
                actor="seed",
            )
        )


def _seed_scenarios(session: Session, country: Country) -> None:
    """The scenarios that carry the demo.

    They are ordinary scenarios, created through the same fields the UI writes, so
    nothing here is a special case the user cannot reproduce or edit.
    """
    baseline = Scenario(
        country_id=country.id,
        name="Baseline — network as it operates today",
        description=(
            "Five Area Medical Stores, current catchments, current timetables, and the "
            "standing policy that every facility is supplied. The cost model therefore "
            "delivers 100% of demand — that is the policy, not an observation. What actually "
            "happens is in the stockout risk column, which is driven by how often each "
            "facility is reached and how much it can hold."
        ),
        is_baseline=True,
        levers={
            "month": None,
            "allowed_modes": ["road", "sea", "air", "river"],
            "hub_nodes_open": [],
            "hub_nodes_closed": ["AMS-WWK", "AMS-ALO", "AMS-BUK"],
            "optimize_hubs": False,
            "integration_policy": "vertical",
            "third_party_share": 0.0,
            "fuel_index": 1.0,
            "demand_growth": 0.0,
            "safety_stock_days": 14.0,
        },
        constraints={"min_fill_rate": 1.0, "respect_capacity": True},
        objective_weights={"cost": 1.0, "service": 1.0, "equity": 0.5},
    )
    session.add(baseline)
    session.flush()

    scenarios = [
        Scenario(
            country_id=country.id,
            name="Cost optimisation — unconstrained",
            description=(
                "Minimise total cost with no service floor and no equity weighting. This is "
                "what a commercial network design tool produces if you let it. Read the "
                "equity panel before you read the saving."
            ),
            parent_scenario_id=baseline.id,
            levers={**baseline.levers, "optimize_hubs": True},
            constraints={"respect_capacity": True},
            objective_weights={"cost": 1.0, "service": 1.0, "equity": 0.0},
        ),
        Scenario(
            country_id=country.id,
            name="Cost optimisation with a 90% equity floor",
            description=(
                "The same optimisation, with a hard constraint that every vulnerability "
                "quintile reaches at least 90% of its demand. The saving is smaller. That "
                "difference is the price of not funding the saving out of the hardest-to-"
                "reach fifth of the population."
            ),
            parent_scenario_id=baseline.id,
            levers={**baseline.levers, "optimize_hubs": True},
            constraints={"equity_floor": 0.90, "respect_capacity": True},
            objective_weights={"cost": 1.0, "service": 1.0, "equity": 0.6},
        ),
        Scenario(
            country_id=country.id,
            name="Wet season stress test (March)",
            description=(
                "The baseline network under March conditions held for a full year. "
                "Floodplain and unsealed highland roads close; the cost of every remaining "
                "lane rises. Shows which facilities fall off the network and what the air "
                "workaround costs."
            ),
            parent_scenario_id=baseline.id,
            levers={**baseline.levers, "month": 3},
            constraints={"min_fill_rate": 1.0, "respect_capacity": True},
            objective_weights={"cost": 1.0, "service": 1.0, "equity": 0.5},
        ),
        Scenario(
            country_id=country.id,
            name="Island services halved (fortnightly → monthly)",
            description=(
                "Every weekly and fortnightly coastal run drops to monthly — the effect of "
                "a fuel price shock or a vessel going off charter. Cost falls. Look at what "
                "happens to stockout risk at island facilities, and at the equity panel."
            ),
            parent_scenario_id=baseline.id,
            levers={
                **baseline.levers,
                "service_frequency_overrides": {
                    "Milne Bay island run": "MONTHLY",
                    "Kokopo–New Ireland coastal run": "MONTHLY",
                    "Lae–Huon coastal run": "MONTHLY",
                    "Madang–Rai Coast workboat": "MONTHLY",
                    "Kokopo–West New Britain coastal run": "MONTHLY",
                    "Kokopo–Bougainville service": "MONTHLY",
                    "Madang–Sepik coastal service": "MONTHLY",
                },
            },
            constraints={"min_fill_rate": 0.90, "respect_capacity": True},
            objective_weights={"cost": 1.0, "service": 1.0, "equity": 0.5},
        ),
        Scenario(
            country_id=country.id,
            name="Open Alotau, Wewak and Buka stores",
            description=(
                "Bring the three proposed Area Medical Stores into service, shortening the "
                "island runs in Milne Bay, the Sepik coast and Bougainville. Capital cost "
                "appears in the roadmap; the operating effect appears here."
            ),
            parent_scenario_id=baseline.id,
            levers={
                **baseline.levers,
                "hub_nodes_closed": [],
                "hub_nodes_open": ["AMS-WWK", "AMS-ALO", "AMS-BUK"],
                "optimize_hubs": False,
            },
            constraints={"min_fill_rate": 1.0, "respect_capacity": True},
            objective_weights={"cost": 1.0, "service": 1.0, "equity": 0.5},
        ),
        Scenario(
            country_id=country.id,
            name="Integrated programme supply chain",
            description=(
                "Consolidate programme-specific stores (EPI, malaria, TB/HIV) into the "
                "shared network. The largest structural saving available, and the hardest "
                "politically."
            ),
            parent_scenario_id=baseline.id,
            levers={**baseline.levers, "integration_policy": "integrated", "third_party_share": 0.35},
            constraints={"min_fill_rate": 1.0, "respect_capacity": True},
            objective_weights={"cost": 1.0, "service": 1.0, "equity": 0.5},
        ),
    ]
    for scenario in scenarios:
        session.add(scenario)
