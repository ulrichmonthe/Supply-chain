"""The workbook contract.

One definition of the columns, shared by the importer, the exporter and the blank
template, so the file a country downloads is exactly the file it can upload back.
The Excel round-trip is not a convenience feature: it is the single strongest
predictor of whether a tool in this sector is still in use two years later.
"""

from __future__ import annotations

from ..engine.seasonality import MONTH_ABBR

NODE_COLUMNS: list[tuple[str, str]] = [
    ("code", "Unique facility code. Use the master facility list code or DHIS2 UID."),
    ("name", "Facility name as it appears in the master list."),
    ("level", "0 central store, 1 area/regional store, 2 provincial, 3 district or facility."),
    ("type", "national_store | area_store | provincial_hospital | district_hospital | health_centre | aid_post"),
    ("lat", "Decimal degrees. Southern hemisphere is negative."),
    ("lon", "Decimal degrees."),
    ("geocode_source", "Where the coordinate came from: mfl | gps | town_centroid | estimated."),
    ("geocode_confidence", "0 to 1. Below 0.5 the point should be shown as approximate."),
    ("admin1", "Province."),
    ("admin2", "District."),
    ("terrain_class", "mainland_road | coastal_road | highlands_road | island | riverine | remote_air_only"),
    ("catchment_population", "People the facility serves. Drives equity strata and cost per person."),
    ("operating_status", "operational | non_operational | planned"),
    ("dry_m3", "Usable ambient storage volume, cubic metres."),
    ("cold_2_8_m3", "Usable +2 to +8 °C volume, cubic metres."),
    ("cold_minus20_m3", "Usable −20 °C volume, cubic metres."),
    ("cold_minus70_m3", "Usable −70 °C volume, cubic metres."),
    ("hub_capable", "TRUE if this node can supply others in a scenario."),
    ("hub_fixed_cost", "Annual cost of operating it as a hub."),
    ("hub_open_capex", "One-off cost to bring a currently closed hub into service."),
    ("hub_throughput_m3", "Annual throughput ceiling as a hub, cubic metres."),
    ("dhis2_uid", "External identifier."),
    ("msupply_id", "External identifier."),
    ("openlmis_code", "External identifier."),
    ("mfl_code", "External identifier."),
]

EDGE_COLUMNS: list[tuple[str, str]] = [
    ("code", "Unique lane code."),
    ("from_node", "Origin facility code."),
    ("to_node", "Destination facility code."),
    ("mode", "road | sea | air | river | foot | drone"),
    ("service_name", "Name of the scheduled service, e.g. 'Alotau–Misima coastal run'."),
    ("service_frequency", "Blank for on-demand road. Otherwise DAILY | TWICE_WEEKLY | WEEKLY | FORTNIGHTLY | MONTHLY | SIX_WEEKLY | QUARTERLY | BIANNUAL."),
    ("service_days", "Comma-separated sailing or flight days, e.g. TUE or MON,THU."),
    ("capacity_per_trip_m3", "Hold volume available per sailing or flight. Required for scheduled services."),
    ("cold_capacity_per_trip_m3", "Of that hold, how much is temperature controlled."),
    ("fixed_cost_per_trip", "Cost of the trip regardless of load."),
    ("variable_cost_per_km", "Cost per kilometre."),
    ("cost_per_m3", "Quoted freight rate per cubic metre, if one exists. Overrides the reconstructed cost."),
    ("distance_km", "Leave blank to let the distance cascade compute it."),
    ("base_travel_time_hr", "Leave blank to let the cascade compute it."),
    ("distance_method", "manual | osrm | detour_factor | great_circle | gtfs. Set to 'manual' when you type a measured distance."),
    ("distance_confidence", "0 to 1."),
    ("distance_note", "Why this number. A field interview reference belongs here."),
    ("access_profile", "Named seasonal profile, e.g. highlands_unsealed. Fills the twelve monthly columns."),
    *[(f"access_{m.lower()}", f"{m} access, 0 impassable to 1 normal.") for m in MONTH_ABBR],
    ("reliability", "0 to 1. Probability the service actually runs as timetabled."),
    ("lead_time_sd_days", "Standard deviation of arrival, in days."),
    ("active", "TRUE or FALSE."),
]

PRODUCT_COLUMNS: list[tuple[str, str]] = [
    ("sku", "Unique product code."),
    ("name", "Product name."),
    ("temperature_band", "ambient | +2-8 | -20 | -70"),
    ("volume_per_unit_cm3", "Packed volume of one unit, cubic centimetres."),
    ("unit_cost", "Procurement cost per unit."),
    ("shelf_life_days", "Shelf life in days."),
]

DEMAND_COLUMNS: list[tuple[str, str]] = [
    ("node", "Facility code."),
    ("product", "Product SKU."),
    ("period", "0 for an annual total, or 1–12 for a monthly profile."),
    ("quantity", "Units consumed in the period."),
    ("source", "actual | forecast | proxy. Say which numbers are measured and which are assumed."),
    ("confidence", "0 to 1."),
]

SHEETS = {
    "Nodes": NODE_COLUMNS,
    "Edges": EDGE_COLUMNS,
    "Products": PRODUCT_COLUMNS,
    "Demand": DEMAND_COLUMNS,
}


def normalise_header(value) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
