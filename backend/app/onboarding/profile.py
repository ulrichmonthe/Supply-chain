"""Profiling a file before anybody maps it: sheets, header rows, units, date ranges,
the domain it most likely feeds and the system it most likely came from.

A ministry hands over what it has. The profile is what lets the next step say "this
looks like a DHIS2 consumption export for January to December 2025, with quantities
in packs" instead of showing a grid of unlabelled columns.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from typing import Dict, List, Optional, Tuple

from openpyxl import load_workbook

from ..io import mapper
from ..io.schema_spec import normalise_header

#: Header words that betray the system a file came from. Scored, not matched exactly.
SYSTEM_SIGNATURES = {
    "dhis2": ["orgunit", "org_unit", "organisationunit", "dataelement", "data_element", "period", "uid", "categoryoptioncombo", "orgunitname"],
    "msupply": ["item_code", "item_name", "store", "store_name", "stock_on_hand", "soh", "expiry", "batch", "adjusted_monthly_consumption", "amc"],
    "openlmis": ["facilitycode", "facility_code", "productcode", "product_code", "program", "requisition", "stockonhand", "beginningbalance", "quantityreceived", "quantitydispensed", "totalconsumedquantity"],
}

#: Column names that carry a unit in their name, or a bracketed unit.
UNIT_PATTERNS = [
    (re.compile(r"\b(m3|m³|cubic\s*met)", re.I), "m3"),
    (re.compile(r"\b(litre|liter|ltr|\bl\b)", re.I), "litres"),
    (re.compile(r"\b(cm3|cc)\b", re.I), "cm3"),
    (re.compile(r"\bkm\b", re.I), "km"),
    (re.compile(r"\bmiles?\b", re.I), "miles"),
    (re.compile(r"\b(hours?|hrs?)\b", re.I), "hours"),
    (re.compile(r"\b(minutes?|mins?)\b", re.I), "minutes"),
    (re.compile(r"\bdays?\b", re.I), "days"),
    (re.compile(r"\b(usd|us\$)\b", re.I), "USD"),
    (re.compile(r"\b(aud|a\$)\b", re.I), "AUD"),
    (re.compile(r"\b(pgk|kina)\b", re.I), "PGK"),
    (re.compile(r"\b(packs?|cartons?|boxes|vials?|doses?|kg|tonnes?)\b", re.I), None),
]

PERIOD_RE = re.compile(r"^(?:(\d{4})[-/]?(0[1-9]|1[0-2])|(\d{4})(0[1-9]|1[0-2])|(\d{4})[-/]?Q([1-4]))$")
MONTH_NAMES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_workbook(filename: str, data: bytes) -> bool:
    return filename.lower().endswith((".xlsx", ".xlsm")) or data[:2] == b"PK"


def read_sheets(filename: str, data: bytes) -> Dict[str, Tuple[List[str], List[dict]]]:
    """Every sheet as (columns, rows). A CSV is one sheet named after the file.

    Header rows that are not the first row -- a title above the table, a merged
    banner -- are found by looking for the first row with mostly non-empty text cells.
    """
    if is_workbook(filename, data):
        workbook = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        out = {}
        for name in workbook.sheetnames:
            sheet = workbook[name]
            raw = [list(r) for r in sheet.iter_rows(values_only=True)]
            columns, rows = _table_from_rows(raw)
            if columns:
                out[name] = (columns, rows)
        workbook.close()
        return out
    # A CSV with a title line above its header is common enough to handle here rather
    # than send back: every line is read, and the header is found the same way as in a sheet.
    text = None
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("The file is not text in an encoding this reader knows.")
    lines = [line for line in text.lstrip("\ufeff").splitlines() if line.strip()]
    if not lines:
        raise ValueError("The file is empty.")
    counts = {candidate: max(line.count(candidate) for line in lines[:8]) for candidate in (",", ";", "\t", "|")}
    delimiter = max(counts, key=lambda c: counts[c]) if any(counts.values()) else ","
    raw = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text.lstrip("\ufeff")), delimiter=delimiter)]
    columns, rows = _table_from_rows(raw)
    return {filename: (columns, rows)}


def _table_from_rows(raw: List[list]) -> Tuple[List[str], List[dict]]:
    header_index = None
    for index, row in enumerate(raw[:20]):
        cells = [c for c in row if c not in (None, "")]
        if len(cells) >= 2 and sum(1 for c in cells if isinstance(c, str)) >= max(2, len(cells) * 0.6):
            header_index = index
            break
    if header_index is None:
        return [], []
    header = raw[header_index]
    # A merged two-row header: fill blanks from the row above and join.
    columns = []
    carry = ""
    for i, cell in enumerate(header):
        text = str(cell).strip() if cell not in (None, "") else ""
        above = str(raw[header_index - 1][i]).strip() if header_index > 0 and i < len(raw[header_index - 1]) and raw[header_index - 1][i] not in (None, "") else ""
        if above and text and header_index > 0 and _looks_like_group(raw[header_index - 1]):
            carry = above
            text = f"{above} {text}"
        elif not text and carry and header_index > 0 and _looks_like_group(raw[header_index - 1]):
            text = ""
        columns.append(text)
    rows = []
    for index, values in enumerate(raw[header_index + 1:], start=header_index + 2):
        if values is None or all(v in (None, "") for v in values):
            continue
        row = {columns[i]: ("" if values[i] is None else values[i]) for i in range(min(len(columns), len(values))) if columns[i]}
        row["_row"] = index
        rows.append(row)
    return [c for c in columns if c], rows


def _looks_like_group(row: list) -> bool:
    """A group header spans several columns with a few labels; a title line is one cell."""
    cells = [c for c in row if c not in (None, "")]
    return 2 <= len(cells) <= max(2, len(row) // 2)


def guess_system(columns: List[str]) -> Tuple[str, float]:
    normalised = {re.sub(r"[^a-z0-9_]", "", normalise_header(c)) for c in columns}
    best, best_score = "unknown", 0
    for system, words in SYSTEM_SIGNATURES.items():
        score = sum(1 for w in words if w in normalised)
        if score > best_score:
            best, best_score = system, score
    if best_score < 2:
        return "unknown", 0.0
    return best, min(1.0, best_score / 4)


#: An LMIS export with a quantity column is a demand file whatever the mapper's synonym
#: table makes of "item_code": the system's own shape decides.
_DEMAND_SHAPES = {
    "msupply": (("store_code", "store", "store_name", "customer_code", "name_code"), ("adjusted_monthly_consumption", "amc", "consumption", "quantity", "issued", "total_quantity")),
    "dhis2": (("orgunit", "orgunitcode", "organisationunit", "orgunituid", "orgunitid", "org_unit"), ("value", "quantity", "total")),
    "openlmis": (("facilitycode", "facility_code"), ("totalconsumedquantity", "quantitydispensed", "quantity", "stockonhand")),
}


def lmis_domain(system: str, columns: List[str]) -> Optional[str]:
    shape = _DEMAND_SHAPES.get(system)
    if not shape:
        return None
    have = {re.sub(r"[^a-z0-9_]", "", normalise_header(c)) for c in columns}
    have |= {h.replace("_", "") for h in have}
    if any(k in have for k in shape[0]) and any(q in have for q in shape[1]):
        return "Demand"
    return None


def guess_units(columns: List[str], rows: List[dict]) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    for column in columns:
        unit = None
        for pattern, name in UNIT_PATTERNS:
            if pattern.search(column):
                unit = name
                break
        bracket = re.search(r"[\(\[]([^\)\]]{1,12})[\)\]]", column)
        if bracket:
            unit = bracket.group(1).strip().lower()
        if unit:
            out[column] = unit
    return out


def guess_vintage(columns: List[str], rows: List[dict]) -> Tuple[str, str, Optional[str]]:
    """The period the data describes, from period-like columns or monthly column names."""
    periods: List[str] = []
    period_column = None
    for column in columns:
        normalised = normalise_header(column)
        if any(word in normalised for word in ("period", "month", "date", "year", "reporting")):
            values = [str(r.get(column, "")).strip() for r in rows[:5000]]
            found = [_as_period(v) for v in values]
            found = [f for f in found if f]
            if len(found) >= max(1, len(values) // 2):
                periods.extend(found)
                period_column = column
                break
    if not periods:
        # Monthly columns: "Jan 2025", "2025-02", "consumption_mar"...
        for column in columns:
            text = column.lower()
            match = re.search(r"(20\d{2})", text)
            month = next((i + 1 for i, m in enumerate(MONTH_NAMES) if re.search(rf"\b{m}", text)), None)
            if month:
                periods.append(f"{match.group(1) if match else '____'}-{month:02d}")
    if not periods:
        return "", "", period_column
    periods = sorted(p for p in periods if not p.startswith("____")) or sorted(periods)
    return periods[0], periods[-1], period_column


def _as_period(value: str) -> Optional[str]:
    value = value.strip()
    match = PERIOD_RE.match(value)
    if match:
        if match.group(1):
            return f"{match.group(1)}-{match.group(2)}"
        if match.group(3):
            return f"{match.group(3)}-{match.group(4)}"
        return f"{match.group(5)}-Q{match.group(6)}"
    match = re.match(r"^(\d{4})-(\d{2})-\d{2}", value)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    match = re.match(r"^(\d{1,2})/(\d{4})$", value)
    if match:
        return f"{match.group(2)}-{int(match.group(1)):02d}"
    return None


def profile(filename: str, data: bytes, *, saved_mappings: Optional[List[dict]] = None) -> dict:
    """Everything the mapping step needs to know about a file before showing it."""
    sheets = read_sheets(filename, data)
    out_sheets = []
    for name, (columns, rows) in sheets.items():
        system, system_confidence = guess_system(columns)
        domain = lmis_domain(system, columns) or mapper.guess_sheet(columns)
        vintage_from, vintage_to, period_column = guess_vintage(columns, rows)
        guesses = {sheet: mapper.guess_mapping(columns, sheet) for sheet in mapper.COLUMNS}
        saved = None
        present = set(columns)
        for candidate in saved_mappings or []:
            if set(candidate.get("mapping", {}).values()) <= present:
                saved = candidate
                break
        out_sheets.append(
            {
                "name": name,
                "columns": columns,
                "row_count": len(rows),
                "sample": [{k: v for k, v in r.items() if k != "_row"} for r in rows[:5]],
                "domain": domain,
                "system": system,
                "system_confidence": system_confidence,
                "units": guess_units(columns, rows),
                "vintage_from": vintage_from,
                "vintage_to": vintage_to,
                "period_column": period_column,
                "guesses": guesses,
                "missing_required": {sheet: mapper.missing_required(sheet, guesses[sheet]) for sheet in mapper.COLUMNS},
                "matched_saved": saved.get("name") if saved else None,
            }
        )
    best = max(out_sheets, key=lambda s: s["row_count"]) if out_sheets else None
    return {
        "filename": filename,
        "sha256": sha256(data),
        "size_bytes": len(data),
        "kind": "workbook" if is_workbook(filename, data) else "csv",
        "sheets": out_sheets,
        "domain": best["domain"] if best else "",
        "system": best["system"] if best else "unknown",
        "vintage_from": best["vintage_from"] if best else "",
        "vintage_to": best["vintage_to"] if best else "",
        "primary_sheet": best["name"] if best else None,
    }
