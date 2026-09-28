"""The column mapper: any CSV, mapped once to our columns, into the same hallway.

WMS and ERP data arrives as one-off CSV exports far more often than through a live
API: a facility list from the ministry's register, a consumption extract from the
warehouse system, a product catalogue from procurement. Each has its own headers.
Rather than a bespoke connector per system, a person maps the file's columns to ours
once, the mapping is kept per country under a name, and the rows flow into the same
validate → preview → apply pipeline as a workbook. One hallway, another door.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Dict, List, Optional, Tuple

from .schema_spec import DEMAND_COLUMNS, EDGE_COLUMNS, NODE_COLUMNS, PRODUCT_COLUMNS, normalise_header

#: What each sheet needs before a row means anything.
REQUIRED: Dict[str, List[str]] = {
    "Nodes": ["code", "name", "lat", "lon"],
    "Edges": ["code", "from_node", "to_node", "mode"],
    "Products": ["sku", "name"],
    "Demand": ["node", "product", "quantity"],
}

COLUMNS: Dict[str, List[Tuple[str, str]]] = {
    "Nodes": NODE_COLUMNS,
    "Edges": EDGE_COLUMNS,
    "Products": PRODUCT_COLUMNS,
    "Demand": DEMAND_COLUMNS,
}

#: The names other systems use for our columns. Matched after normalising, so
#: "Facility Code", "facility-code" and "FACILITY_CODE" are one entry.
SYNONYMS: Dict[str, Dict[str, List[str]]] = {
    "Nodes": {
        "code": ["facility_code", "hf_code", "site_code", "org_unit_code", "orgunit_code", "orgunit", "org_unit", "uid", "id", "facility_id", "mfl_code", "dhis2_uid"],
        "name": ["facility_name", "facility", "site_name", "org_unit_name", "orgunit_name", "health_facility", "hf_name"],
        "level": ["facility_level", "tier", "hierarchy_level"],
        "type": ["facility_type", "category", "ownership_type", "site_type"],
        "lat": ["latitude", "y", "gps_lat", "lat_dd", "latitude_dd"],
        "lon": ["longitude", "lng", "long", "x", "gps_lon", "lon_dd", "longitude_dd"],
        "admin1": ["province", "region", "state", "admin_1", "adm1", "county"],
        "admin2": ["district", "admin_2", "adm2", "sub_county", "llg"],
        "catchment_population": ["population", "catchment", "catchment_pop", "pop", "population_served", "people_served"],
        "operating_status": ["status", "functional_status", "operational_status", "open"],
        "dry_m3": ["storage_m3", "dry_storage_m3", "ambient_m3", "storage_volume"],
        "cold_2_8_m3": ["cold_m3", "cold_storage_m3", "fridge_m3", "cold_chain_m3"],
        "hub_capable": ["is_hub", "hub", "warehouse", "is_warehouse"],
    },
    "Edges": {
        "code": ["lane_code", "route_code", "lane", "route", "id"],
        "from_node": ["from", "origin", "from_code", "from_facility", "source", "hub"],
        "to_node": ["to", "destination", "to_code", "to_facility", "target", "facility"],
        "mode": ["transport_mode", "transport", "means"],
        "distance_km": ["distance", "km", "kms", "length_km"],
        "service_frequency": ["frequency", "schedule"],
        "capacity_per_trip_m3": ["capacity", "capacity_m3", "hold_m3", "load_m3"],
        "cost_per_m3": ["rate", "freight_rate", "rate_per_m3"],
        "fixed_cost_per_trip": ["trip_cost", "cost_per_trip"],
        "variable_cost_per_km": ["cost_per_km", "rate_per_km"],
    },
    "Products": {
        "sku": ["product_code", "item_code", "item", "code", "commodity_code", "product_id", "material"],
        "name": ["product_name", "item_name", "description", "commodity", "product"],
        "temperature_band": ["storage_condition", "temperature", "cold_chain", "storage_temp"],
        "volume_per_unit_cm3": ["volume", "unit_volume", "volume_cm3", "cm3", "pack_volume"],
        "unit_cost": ["price", "cost", "unit_price"],
        "shelf_life_days": ["shelf_life", "expiry_days"],
    },
    "Demand": {
        "node": ["facility", "facility_code", "orgunit", "org_unit", "site", "site_code", "hf_code", "code", "facility_id"],
        "product": ["sku", "item", "item_code", "product_code", "commodity", "commodity_code", "material"],
        "period": ["month", "period_month", "reporting_period", "period_code"],
        "quantity": ["qty", "consumption", "quantity_consumed", "consumed", "issued", "amount", "dispensed", "demand", "units"],
        "source": ["data_source", "kind"],
        "confidence": ["data_quality", "quality"],
    },
}


def our_fields(sheet: str) -> List[dict]:
    required = set(REQUIRED[sheet])
    return [{"key": key, "description": text, "required": key in required} for key, text in COLUMNS[sheet]]


# --- reading ------------------------------------------------------------------------


def read_csv(data: bytes, limit: Optional[int] = None) -> Tuple[List[str], List[dict], str]:
    """Columns as written, the rows as dicts of strings, and the delimiter found."""
    text = None
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("The file is not text in an encoding this reader knows.")
    # The header line decides the delimiter: the character that splits it most. The
    # Sniffer guesses well on big files and badly on a two-line extract, and a facility
    # list with three rows is exactly the kind of file that arrives here.
    first_line = text.lstrip("\ufeff").splitlines()[0] if text.strip() else ""
    counts = {candidate: first_line.count(candidate) for candidate in (",", ";", "\t", "|")}
    delimiter = max(counts, key=lambda c: counts[c]) if any(counts.values()) else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("The file is empty.")
    columns = [str(h or "").strip() for h in header]
    if not any(columns):
        raise ValueError("The first row has no column names.")
    rows: List[dict] = []
    for index, values in enumerate(reader, start=2):
        if not values or all(str(v).strip() == "" for v in values):
            continue
        row = {columns[i]: (values[i].strip() if i < len(values) else "") for i in range(len(columns)) if columns[i]}
        row["_row"] = index
        rows.append(row)
        if limit and len(rows) >= limit:
            break
    return columns, rows, delimiter


# --- guessing -----------------------------------------------------------------------


def _match(column: str, field: str, sheet: str) -> int:
    """0 no match; 3 exact; 2 synonym; 1 loose (field inside the header)."""
    header = normalise_header(column)
    header = re.sub(r"[^a-z0-9_]", "", header)
    if header == field:
        return 3
    if header in (normalise_header(s) for s in SYNONYMS.get(sheet, {}).get(field, [])):
        return 2
    if len(field) > 3 and (header.endswith("_" + field) or header.startswith(field + "_")):
        return 1
    return 0


def guess_mapping(columns: List[str], sheet: str) -> Dict[str, Optional[str]]:
    """Our field → their column, best match first, each column used once."""
    mapping: Dict[str, Optional[str]] = {}
    taken: set = set()
    candidates = []
    for field, _ in COLUMNS[sheet]:
        for column in columns:
            score = _match(column, field, sheet)
            if score:
                candidates.append((score, field, column))
    # Strong matches first, so "code" does not steal "facility_code" from a weaker field.
    for score, field, column in sorted(candidates, key=lambda c: (-c[0], c[1])):
        if field in mapping or column in taken:
            continue
        mapping[field] = column
        taken.add(column)
    for field, _ in COLUMNS[sheet]:
        mapping.setdefault(field, None)
    return mapping


def guess_sheet(columns: List[str]) -> str:
    """Which of our sheets the file most resembles: the one whose required fields it fills."""
    best, best_score = "Nodes", -1.0
    for sheet in COLUMNS:
        mapping = guess_mapping(columns, sheet)
        required_hit = sum(1 for f in REQUIRED[sheet] if mapping.get(f))
        score = required_hit / len(REQUIRED[sheet]) * 10 + sum(1 for v in mapping.values() if v)
        if required_hit == len(REQUIRED[sheet]):
            score += 100
        if score > best_score:
            best, best_score = sheet, score
    return best


def missing_required(sheet: str, mapping: Dict[str, Optional[str]]) -> List[str]:
    return [field for field in REQUIRED[sheet] if not mapping.get(field)]


# --- mapping ------------------------------------------------------------------------


def apply_mapping(rows: List[dict], mapping: Dict[str, Optional[str]]) -> List[dict]:
    """Rows with our headers, ready for the workbook record builders."""
    out = []
    for row in rows:
        record = {"_row": row.get("_row")}
        for field, column in mapping.items():
            if column and column in row:
                value = row[column]
                record[field] = None if value == "" else value
        out.append(record)
    return out


# --- saved mappings -------------------------------------------------------------------


def saved_mappings(country) -> List[dict]:
    return list(((country.config or {}).get("column_mappings")) or [])


def save_mapping(country, *, name: str, sheet: str, mapping: Dict[str, Optional[str]], columns: List[str], saved_at: str) -> List[dict]:
    """Keep a mapping under a name; the same name replaces. Returns the list."""
    kept = [m for m in saved_mappings(country) if m.get("name") != name]
    kept.append({"name": name, "sheet": sheet, "mapping": {k: v for k, v in mapping.items() if v}, "columns": columns, "saved_at": saved_at})
    config = dict(country.config or {})
    config["column_mappings"] = kept
    country.config = config
    return kept


def delete_mapping(country, name: str) -> List[dict]:
    kept = [m for m in saved_mappings(country) if m.get("name") != name]
    config = dict(country.config or {})
    config["column_mappings"] = kept
    country.config = config
    return kept


def matching_saved(country, columns: List[str]) -> Optional[dict]:
    """A saved mapping whose columns are all present in this file, if any."""
    present = set(columns)
    for saved in saved_mappings(country):
        if set(saved.get("mapping", {}).values()) <= present:
            return saved
    return None


# --- pruning ------------------------------------------------------------------------

#: Columns that the record builders fold into one nested field.
_NESTED = {
    "Nodes": {
        "capacity": {"dry_m3", "cold_2_8_m3", "cold_minus20_m3", "cold_minus70_m3"},
        "external_ids": {"dhis2_uid", "msupply_id", "openlmis_code", "mfl_code"},
    },
    "Edges": {
        "monthly_access": {"access_profile"} | {f"access_{m}" for m in ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")},
        "monthly_cost_multiplier": {"access_profile"},
    },
}
_BAND_COLUMNS = {"cold_2_8_m3": "+2-8", "cold_minus20_m3": "-20", "cold_minus70_m3": "-70"}


def prune(records: List[dict], sheet: str, mapped: List[str]) -> List[dict]:
    """Keep only what the file actually said.

    The record builders fill every column with its default so a workbook row is
    complete. A mapped CSV usually carries a handful of columns, and a default for a
    column the file never had is not a value, it is a guess -- and it would overwrite
    the real one. So everything the mapping did not cover is dropped, which the diff
    reads as "no opinion", and a new row still gets its defaults on the way in.
    """
    keep = set(mapped) | {"_row"}
    nested = _NESTED.get(sheet, {})
    for field, columns in nested.items():
        if columns & set(mapped):
            keep.add(field)
    out = []
    for record in records:
        kept = {k: v for k, v in record.items() if k in keep}
        if sheet == "Nodes" and "capacity" in kept:
            capacity = record.get("capacity") or {}
            slim: dict = {}
            if "dry_m3" in mapped:
                slim["dry_m3"] = capacity.get("dry_m3")
            bands = {band: capacity.get("cold_by_band", {}).get(band) for col, band in _BAND_COLUMNS.items() if col in mapped}
            if bands:
                slim["cold_by_band"] = bands
            kept["capacity"] = slim
        out.append(kept)
    return out
