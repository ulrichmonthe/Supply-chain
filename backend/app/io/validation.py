"""Data validation with human-readable errors.

Garbage-in is the sector's defining problem, so this file is not plumbing -- it is
the product. Two rules govern every message written here:

1. **Say what is wrong in the language of the person who owns the data.** Not
   "constraint violation on node.lat", but "Kupiano Health Centre plots 46 km
   offshore in the Coral Sea. Check the longitude."
2. **Say what to do about it.** Every issue carries a suggestion. A validation
   report that a provincial data officer cannot act on has failed.

The geographic checks exist because of a specific, real failure mode: facility
master lists that place Central Province health centres in the Bismarck Sea. That
map gets shown to a room, everybody sees it is wrong, and the engagement never
recovers. It must fail at import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from ..engine.geo import haversine_km, offshore_km

ERROR = "error"
WARNING = "warning"
INFO = "info"

VALID_MODES = {"road", "sea", "air", "river", "foot", "drone"}
VALID_TEMPERATURE_BANDS = {"ambient", "+2-8", "-20", "-70"}
VALID_DEMAND_SOURCES = {"actual", "forecast", "proxy"}
VALID_TERRAIN = {
    "mainland_road",
    "coastal_road",
    "highlands_road",
    "island",
    "riverine",
    "remote_air_only",
}
VALID_FREQUENCIES = {
    "DAILY",
    "TWICE_WEEKLY",
    "WEEKLY",
    "FORTNIGHTLY",
    "MONTHLY",
    "SIX_WEEKLY",
    "QUARTERLY",
    "BIANNUAL",
}

#: How far offshore a point may sit before it is treated as an error. The land mask
#: is coarse, so the tolerance is generous: this catches errors of tens of
#: kilometres and up, not cartographic quibbles about where a jetty ends.
OFFSHORE_ERROR_KM = 18.0
OFFSHORE_WARNING_KM = 6.0


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    suggestion: str = ""
    sheet: str = ""
    row: int | None = None
    entity: str = ""

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "suggestion": self.suggestion,
            "sheet": self.sheet,
            "row": self.row,
            "entity": self.entity,
        }


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def add(self, issue: Issue) -> None:
        self.issues.append(issue)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def blocking(self) -> bool:
        return bool(self.errors)

    def as_dict(self) -> dict:
        return {
            "blocking": self.blocking,
            "counts": {
                "error": len(self.errors),
                "warning": len(self.warnings),
                "info": sum(1 for i in self.issues if i.severity == INFO),
            },
            "issues": [i.as_dict() for i in self.issues],
            "stats": self.stats,
            "headline": self.headline(),
        }

    def headline(self) -> str:
        if self.blocking:
            return (
                f"{len(self.errors)} problem{'s' if len(self.errors) != 1 else ''} must be fixed "
                f"before this data can be loaded, and {len(self.warnings)} should be reviewed."
            )
        if self.warnings:
            return (
                f"Data can be loaded. {len(self.warnings)} item"
                f"{'s' if len(self.warnings) != 1 else ''} should be reviewed with the "
                f"provincial team before any result is published."
            )
        return "Data passed every check."


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_dataset(
    *,
    country_code: str,
    bbox: dict,
    nodes: list[dict],
    edges: list[dict],
    products: list[dict],
    demand: list[dict],
) -> ValidationReport:
    report = ValidationReport()
    node_by_code: dict[str, dict] = {}

    _validate_nodes(report, country_code, bbox, nodes, node_by_code)
    _validate_products(report, products)
    _validate_edges(report, edges, node_by_code)
    _validate_demand(report, demand, node_by_code, products)
    _validate_coverage(report, nodes, edges, demand, node_by_code)

    report.stats.update(
        {
            "nodes": len(nodes),
            "edges": len(edges),
            "products": len(products),
            "demand_rows": len(demand),
        }
    )
    return report


# --- nodes ----------------------------------------------------------------------------


def _validate_nodes(
    report: ValidationReport,
    country_code: str,
    bbox: dict,
    nodes: list[dict],
    node_by_code: dict[str, dict],
) -> None:
    seen_coords: dict[tuple[float, float], str] = {}
    province_points: dict[str, list[tuple[float, float]]] = {}
    offshore_count = 0

    for node in nodes:
        row = node.get("_row")
        code = str(node.get("code") or "").strip()
        name = str(node.get("name") or "").strip() or code or "(unnamed)"

        if not code:
            report.add(
                Issue(
                    ERROR,
                    "node.missing_code",
                    f"Row {row}: a facility has no code.",
                    "Every facility needs a stable code — use the master facility list code "
                    "(MFL) or the DHIS2 UID so the record can be matched on the next refresh.",
                    "Nodes",
                    row,
                    name,
                )
            )
            continue

        if code in node_by_code:
            report.add(
                Issue(
                    ERROR,
                    "node.duplicate_code",
                    f"Facility code '{code}' appears more than once ({name}).",
                    "Codes must be unique. If these are genuinely two facilities, give the "
                    "second one its own code; if it is a duplicate row, delete it.",
                    "Nodes",
                    row,
                    code,
                )
            )
            continue
        node_by_code[code] = node

        lat = _to_float(node.get("lat"))
        lon = _to_float(node.get("lon"))

        if lat is None or lon is None:
            report.add(
                Issue(
                    ERROR,
                    "node.missing_coordinates",
                    f"{name} ({code}) has no coordinates.",
                    "Geocode it from the facility master list, or place it at its district "
                    "headquarters and set geocode_confidence to 0.3 so the map shows it is "
                    "approximate.",
                    "Nodes",
                    row,
                    code,
                )
            )
            continue

        if abs(lat) < 0.01 and abs(lon) < 0.01:
            report.add(
                Issue(
                    ERROR,
                    "node.null_island",
                    f"{name} ({code}) is at 0°, 0° — in the Atlantic off West Africa.",
                    "This is the classic 'empty coordinate' failure. Leave the cells blank "
                    "rather than filling them with zero.",
                    "Nodes",
                    row,
                    code,
                )
            )
            continue

        in_box = bbox["min_lat"] <= lat <= bbox["max_lat"] and bbox["min_lon"] <= lon <= bbox["max_lon"]
        swapped_in_box = (
            bbox["min_lat"] <= lon <= bbox["max_lat"] and bbox["min_lon"] <= lat <= bbox["max_lon"]
        )

        if not in_box and swapped_in_box:
            report.add(
                Issue(
                    ERROR,
                    "node.swapped_coordinates",
                    f"{name} ({code}) is at {lat:.4f}, {lon:.4f}, which is outside {country_code} — "
                    f"but {lon:.4f}, {lat:.4f} is inside it.",
                    f"Latitude and longitude are almost certainly swapped. Set lat={lon:.4f} "
                    f"and lon={lat:.4f}.",
                    "Nodes",
                    row,
                    code,
                )
            )
            continue

        if not in_box:
            report.add(
                Issue(
                    ERROR,
                    "node.outside_country",
                    f"{name} ({code}) is at {lat:.4f}, {lon:.4f}, which is outside the "
                    f"{country_code} bounding box.",
                    "Check the sign on the latitude (southern hemisphere values are negative) "
                    "and that the coordinate is in decimal degrees, not degrees-minutes-seconds.",
                    "Nodes",
                    row,
                    code,
                )
            )
            continue

        sea_distance = offshore_km(country_code, lat, lon)
        if sea_distance is not None and sea_distance > OFFSHORE_ERROR_KM:
            offshore_count += 1
            report.add(
                Issue(
                    ERROR,
                    "node.offshore",
                    f"{name} ({code}) plots {sea_distance:.0f} km offshore, in open water.",
                    "Health facilities are on land. Re-check the coordinate against the "
                    "district headquarters; a transposed digit in the longitude is the usual "
                    "cause. This is the error that makes a map indefensible in a workshop.",
                    "Nodes",
                    row,
                    code,
                )
            )
            continue
        if sea_distance is not None and sea_distance > OFFSHORE_WARNING_KM:
            report.add(
                Issue(
                    WARNING,
                    "node.near_offshore",
                    f"{name} ({code}) plots {sea_distance:.0f} km from the modelled coastline.",
                    "Probably a small island or a coastal spit the coarse land mask does not "
                    "carry. Confirm with the provincial team and then ignore this warning.",
                    "Nodes",
                    row,
                    code,
                )
            )

        key = (round(lat, 5), round(lon, 5))
        if key in seen_coords:
            report.add(
                Issue(
                    WARNING,
                    "node.duplicate_coordinates",
                    f"{name} ({code}) sits at exactly the same point as {seen_coords[key]}.",
                    "Usually means both were geocoded to the same district centre. Distances "
                    "and catchments for both will be wrong. Get a better coordinate for at "
                    "least one of them.",
                    "Nodes",
                    row,
                    code,
                )
            )
        else:
            seen_coords[key] = f"{name} ({code})"

        if lat == int(lat) and lon == int(lon):
            report.add(
                Issue(
                    WARNING,
                    "node.low_precision",
                    f"{name} ({code}) has whole-degree coordinates — accurate to about 110 km.",
                    "Whole degrees are a placeholder, not a location. Any distance computed "
                    "from this is meaningless; flag the facility for geocoding.",
                    "Nodes",
                    row,
                    code,
                )
            )

        admin1 = str(node.get("admin1") or "").strip()
        if admin1:
            province_points.setdefault(admin1, []).append((lat, lon))

        population = _to_float(node.get("catchment_population"))
        if population is None or population <= 0:
            report.add(
                Issue(
                    WARNING,
                    "node.no_population",
                    f"{name} ({code}) has no catchment population.",
                    "Equity strata and cost-per-person are weighted by population. Without it "
                    "this facility is invisible to the equity panel. Use the census projection "
                    "for the catchment ward as a proxy and mark the source.",
                    "Nodes",
                    row,
                    code,
                )
            )

        terrain = str(node.get("terrain_class") or "").strip()
        if terrain and terrain not in VALID_TERRAIN:
            report.add(
                Issue(
                    WARNING,
                    "node.unknown_terrain",
                    f"{name} ({code}) has terrain class '{terrain}', which the model "
                    f"does not recognise.",
                    f"Use one of: {', '.join(sorted(VALID_TERRAIN))}. Terrain drives the "
                    f"detour factor and the vulnerability index, so an unknown value silently "
                    f"defaults to mainland assumptions.",
                    "Nodes",
                    row,
                    code,
                )
            )

        capacity = node.get("capacity") or {}
        dry = _to_float(capacity.get("dry_m3")) if isinstance(capacity, dict) else None
        if str(node.get("hub_capable", "")).strip().lower() in ("true", "1", "yes") and not dry:
            report.add(
                Issue(
                    WARNING,
                    "node.hub_without_capacity",
                    f"{name} ({code}) is marked hub-capable but has no storage capacity.",
                    "A hub with no throughput ceiling will absorb unlimited volume in the "
                    "optimisation. Enter the usable pallet volume, even approximately.",
                    "Nodes",
                    row,
                    code,
                )
            )

    # Province outlier check: a facility far from the centre of its own province is
    # the single most reliable signal of a bad coordinate in a real master list.
    for province, points in province_points.items():
        if len(points) < 4:
            continue
        # Component-wise median, not the mean: the point we are hunting for would drag
        # a mean centroid towards itself and then look unremarkable relative to it.
        clat = median(p[0] for p in points)
        clon = median(p[1] for p in points)
        distances = [haversine_km(lat, lon, clat, clon) for lat, lon in points]
        typical = median(distances)
        threshold = max(120.0, typical * 3.0)
        for node in nodes:
            if str(node.get("admin1") or "").strip() != province:
                continue
            lat, lon = _to_float(node.get("lat")), _to_float(node.get("lon"))
            if lat is None or lon is None:
                continue
            distance = haversine_km(lat, lon, clat, clon)
            if distance > threshold:
                report.add(
                    Issue(
                        WARNING,
                        "node.province_outlier",
                        f"{node.get('name')} ({node.get('code')}) is {distance:.0f} km from the "
                        f"centre of {province}, where the typical facility is {typical:.0f} km.",
                        "Either the coordinate is wrong or the province label is. Check both "
                        "against the master facility list before the validation workshop.",
                        "Nodes",
                        node.get("_row"),
                        str(node.get("code")),
                    )
                )

    if offshore_count:
        report.stats["offshore_nodes"] = offshore_count


# --- products --------------------------------------------------------------------------


def _validate_products(report: ValidationReport, products: list[dict]) -> None:
    seen: set[str] = set()
    for product in products:
        row = product.get("_row")
        sku = str(product.get("sku") or "").strip()
        if not sku:
            report.add(
                Issue(ERROR, "product.missing_sku", f"Row {row}: product has no SKU.", "Give every product a SKU.", "Products", row)
            )
            continue
        if sku in seen:
            report.add(
                Issue(
                    ERROR,
                    "product.duplicate_sku",
                    f"SKU '{sku}' appears more than once.",
                    "Merge the rows or give the second one a distinct SKU.",
                    "Products",
                    row,
                    sku,
                )
            )
            continue
        seen.add(sku)

        band = str(product.get("temperature_band") or "ambient").strip()
        if band not in VALID_TEMPERATURE_BANDS:
            report.add(
                Issue(
                    ERROR,
                    "product.bad_temperature_band",
                    f"Product '{sku}' has temperature band '{band}'.",
                    f"Use one of: {', '.join(sorted(VALID_TEMPERATURE_BANDS))}. The band "
                    f"determines which cold chain capacity the product consumes.",
                    "Products",
                    row,
                    sku,
                )
            )

        volume = _to_float(product.get("volume_per_unit_cm3"))
        if volume is None or volume <= 0:
            report.add(
                Issue(
                    ERROR,
                    "product.no_volume",
                    f"Product '{sku}' has no unit volume.",
                    "The whole model is volumetric — a product with no volume consumes no "
                    "transport and no storage, so it will appear free. Take the packed volume "
                    "per dose or per pack from the EVM assessment.",
                    "Products",
                    row,
                    sku,
                )
            )


# --- edges -----------------------------------------------------------------------------


def _validate_edges(report: ValidationReport, edges: list[dict], node_by_code: dict[str, dict]) -> None:
    seen: set[str] = set()
    for edge in edges:
        row = edge.get("_row")
        code = str(edge.get("code") or "").strip()
        origin = str(edge.get("from_node") or "").strip()
        destination = str(edge.get("to_node") or "").strip()
        label = code or f"{origin}→{destination}"

        if code and code in seen:
            report.add(
                Issue(ERROR, "edge.duplicate_code", f"Lane code '{code}' appears more than once.", "Lane codes must be unique.", "Edges", row, code)
            )
            continue
        if code:
            seen.add(code)

        if origin not in node_by_code:
            report.add(
                Issue(
                    ERROR,
                    "edge.unknown_origin",
                    f"Lane {label} starts at '{origin}', which is not in the Nodes sheet.",
                    "Add the facility to Nodes, or correct the code. Lanes to unknown nodes "
                    "are dropped silently by most tools — this one refuses.",
                    "Edges",
                    row,
                    label,
                )
            )
        if destination not in node_by_code:
            report.add(
                Issue(
                    ERROR,
                    "edge.unknown_destination",
                    f"Lane {label} ends at '{destination}', which is not in the Nodes sheet.",
                    "Add the facility to Nodes, or correct the code.",
                    "Edges",
                    row,
                    label,
                )
            )
        if origin and origin == destination:
            report.add(
                Issue(ERROR, "edge.self_loop", f"Lane {label} starts and ends at the same node.", "Delete the row.", "Edges", row, label)
            )

        mode = str(edge.get("mode") or "road").strip()
        if mode not in VALID_MODES:
            report.add(
                Issue(
                    ERROR,
                    "edge.unknown_mode",
                    f"Lane {label} has mode '{mode}'.",
                    f"Use one of: {', '.join(sorted(VALID_MODES))}.",
                    "Edges",
                    row,
                    label,
                )
            )

        frequency = str(edge.get("service_frequency") or "").strip().upper()
        if frequency:
            if frequency not in VALID_FREQUENCIES:
                report.add(
                    Issue(
                        ERROR,
                        "edge.unknown_frequency",
                        f"Lane {label} has service frequency '{frequency}'.",
                        f"Use one of: {', '.join(sorted(VALID_FREQUENCIES))}.",
                        "Edges",
                        row,
                        label,
                    )
                )
            capacity = _to_float(edge.get("capacity_per_trip_m3"))
            rate = _to_float(edge.get("cost_per_m3"))
            # A lane needs either a hold or a price. A liner run has a hold and you get
            # your share of it. A charter has no fixed hold -- if you need more space you
            # buy another flight -- so its constraint is the rate, not the volume. Road is
            # exempt from both: if a truck is full you send another truck.
            if (
                mode in ("sea", "air", "river", "drone")
                and (not capacity or capacity <= 0)
                and (not rate or rate <= 0)
            ):
                report.add(
                    Issue(
                        ERROR,
                        "edge.scheduled_without_capacity",
                        f"Lane {label} runs on a {frequency.lower()} timetable but has neither "
                        f"a hold capacity nor a freight rate.",
                        "Give it one or the other. A vessel on a run has a hold, so enter "
                        "capacity_per_trip_m3. A chartered aircraft has no fixed hold — you "
                        "buy another flight — so enter cost_per_m3 instead. With neither, the "
                        "lane behaves as if it were free and infinite, which removes the whole "
                        "point of modelling the timetable.",
                        "Edges",
                        row,
                        label,
                    )
                )

        access = edge.get("monthly_access")
        if isinstance(access, list):
            if len(access) != 12:
                report.add(
                    Issue(
                        ERROR,
                        "edge.bad_access_vector",
                        f"Lane {label} has {len(access)} monthly access values instead of 12.",
                        "The vector is January to December. Twelve values, each 0 to 1.",
                        "Edges",
                        row,
                        label,
                    )
                )
            elif any((value is None or value < 0 or value > 1) for value in access):
                report.add(
                    Issue(
                        ERROR,
                        "edge.access_out_of_range",
                        f"Lane {label} has monthly access values outside 0–1.",
                        "0 means impassable, 1 means normal throughput. A value of 100 is "
                        "probably a percentage — divide by 100.",
                        "Edges",
                        row,
                        label,
                    )
                )
            elif all(value <= 0 for value in access):
                report.add(
                    Issue(
                        WARNING,
                        "edge.never_open",
                        f"Lane {label} is impassable in all twelve months.",
                        "It will never carry anything. Either the vector is wrong or the lane "
                        "should be marked inactive.",
                        "Edges",
                        row,
                        label,
                    )
                )


# --- demand ----------------------------------------------------------------------------


def _validate_demand(
    report: ValidationReport,
    demand: list[dict],
    node_by_code: dict[str, dict],
    products: list[dict],
) -> None:
    product_codes = {str(p.get("sku") or "").strip() for p in products}
    proxy_rows = 0

    for row_data in demand:
        row = row_data.get("_row")
        node_code = str(row_data.get("node") or "").strip()
        sku = str(row_data.get("product") or "").strip()

        if node_code not in node_by_code:
            report.add(
                Issue(
                    ERROR,
                    "demand.unknown_node",
                    f"Row {row}: demand is recorded against facility '{node_code}', which is "
                    f"not in the Nodes sheet.",
                    "Add the facility or correct the code. Unmatched demand is the most common "
                    "reason a total in this tool does not agree with a total in the eLMIS.",
                    "Demand",
                    row,
                    node_code,
                )
            )
        if sku not in product_codes:
            report.add(
                Issue(
                    ERROR,
                    "demand.unknown_product",
                    f"Row {row}: demand is recorded against product '{sku}', which is not in "
                    f"the Products sheet.",
                    "Add the product or correct the SKU.",
                    "Demand",
                    row,
                    sku,
                )
            )

        quantity = _to_float(row_data.get("quantity"))
        if quantity is None:
            report.add(
                Issue(ERROR, "demand.missing_quantity", f"Row {row}: demand has no quantity.", "Enter the annual quantity, or delete the row.", "Demand", row, node_code)
            )
        elif quantity < 0:
            report.add(
                Issue(
                    ERROR,
                    "demand.negative",
                    f"Row {row}: demand for {sku} at {node_code} is negative ({quantity:,.0f}).",
                    "Negative consumption usually means a stock adjustment was exported instead "
                    "of an issue. Re-extract from the eLMIS using issues to facilities.",
                    "Demand",
                    row,
                    node_code,
                )
            )

        source = str(row_data.get("source") or "proxy").strip()
        if source not in VALID_DEMAND_SOURCES:
            report.add(
                Issue(
                    WARNING,
                    "demand.unknown_source",
                    f"Row {row}: demand source '{source}' is not recognised.",
                    f"Use one of: {', '.join(sorted(VALID_DEMAND_SOURCES))}. The source is what "
                    f"lets you say which numbers are measured and which are assumed.",
                    "Demand",
                    row,
                    node_code,
                )
            )
        if source == "proxy":
            proxy_rows += 1

    if demand:
        share = proxy_rows / len(demand)
        if share > 0.5:
            report.add(
                Issue(
                    INFO,
                    "demand.mostly_proxy",
                    f"{share:.0%} of demand rows are population proxies rather than measured "
                    f"consumption.",
                    "That is a legitimate starting point and it is how SCANIT is designed to "
                    "work. Say so on every slide, and replace the proxies with mSupply issue "
                    "data before the roadmap is costed.",
                    "Demand",
                )
            )


# --- coverage ---------------------------------------------------------------------------


def _validate_coverage(
    report: ValidationReport,
    nodes: list[dict],
    edges: list[dict],
    demand: list[dict],
    node_by_code: dict[str, dict],
) -> None:
    inbound: dict[str, int] = {}
    for edge in edges:
        destination = str(edge.get("to_node") or "").strip()
        if destination:
            inbound[destination] = inbound.get(destination, 0) + 1

    demand_nodes = {str(d.get("node") or "").strip() for d in demand}
    stranded = [code for code in demand_nodes if code in node_by_code and not inbound.get(code)]

    if stranded:
        preview = ", ".join(stranded[:8]) + ("…" if len(stranded) > 8 else "")
        report.add(
            Issue(
                ERROR,
                "coverage.unreachable_facilities",
                f"{len(stranded)} facilities have demand but no lane reaching them: {preview}.",
                "Every facility that consumes something must have at least one way of "
                "receiving it. If the real answer is 'staff collect it when they come to town', "
                "model that explicitly as a lane — it has a cost and it has a frequency.",
                "Edges",
                None,
                preview,
            )
        )

    hub_codes = [
        str(n.get("code"))
        for n in nodes
        if str(n.get("hub_capable", "")).strip().lower() in ("true", "1", "yes")
    ]
    for hub in hub_codes:
        if not inbound.get(hub):
            report.add(
                Issue(
                    WARNING,
                    "coverage.hub_without_inbound",
                    f"Hub {hub} has no inbound lane from the central store.",
                    "Its primary supply leg will be costed at zero, which understates total "
                    "cost. Add the lane from the national medical store.",
                    "Edges",
                    None,
                    hub,
                )
            )
