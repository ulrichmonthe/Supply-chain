"""Source adapters: each turns a file into raw staging rows keyed by our field names,
recording where every value came from (sheet, row, column) on the way.

Three arrive here. The generic adapter takes a confirmed column mapping. The DHIS2 and
mSupply adapters recognise the structures those systems export and need no mapping:
a DHIS2 pivot of org unit × data element × period, an mSupply store consumption
extract. Adding an adapter for another system is a new function in this file and a
line in ``ADAPTERS``; the core does not change.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from ..io import mapper
from ..io.schema_spec import normalise_header
from . import profile as profiling

#: A raw staging row: our field -> value, plus where each value came from.
#: {"fields": {"code": "X"}, "locations": {"code": "Sheet1!B12"}, "aux": {...}, "row": 12}
Raw = dict


def _cell_ref(sheet: str, column_index: Optional[int], row: int) -> str:
    if column_index is None:
        return f"{sheet}!row {row}"
    letters = ""
    n = column_index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{sheet}!{letters}{row}"


def generic(sheet_name: str, columns: List[str], rows: List[dict], mapping: Dict[str, Optional[str]], *, aux_columns: Optional[List[str]] = None) -> List[Raw]:
    """Rows through a confirmed mapping. Columns not mapped but named in ``aux_columns``
    ride along beside the record for triangulation; everything else is left behind."""
    index = {column: i for i, column in enumerate(columns)}
    out: List[Raw] = []
    for row in rows:
        fields, locations = {}, {}
        for field, column in mapping.items():
            if not column or column not in row:
                continue
            value = row[column]
            if value in ("", None):
                continue
            fields[field] = value
            locations[field] = _cell_ref(sheet_name, index.get(column), row.get("_row", 0))
        aux = {}
        for column in aux_columns or []:
            if column in row and row[column] not in ("", None):
                aux[normalise_header(column)] = row[column]
        out.append({"fields": fields, "locations": locations, "aux": aux, "row": row.get("_row", 0)})
    return out


def dhis2_consumption(sheet_name: str, columns: List[str], rows: List[dict], mapping: Optional[dict] = None, **_) -> List[Raw]:
    """A DHIS2 data value export: one row per org unit × data element × period.

    Recognised columns (any case, any separator): orgunit / orgunitcode / orgunit name,
    dataelement / dataelement name, period, value. The data element is the product; the
    country pack's product aliases map it to a sku downstream.
    """
    lookup = {normalise_header(c).replace("_", ""): c for c in columns}

    def pick(*names):
        for name in names:
            if name in lookup:
                return lookup[name]
        return None

    org = pick("orgunitcode", "orgunit", "organisationunitcode", "organisationunit", "orgunituid", "orgunitid")
    org_name = pick("orgunitname", "organisationunitname", "orgunitdisplayname")
    element = pick("dataelementcode", "dataelement", "dataelementname", "dataelementid", "dx")
    period = pick("period", "pe", "periodcode")
    value = pick("value", "quantity", "total")
    if not (org and element and value):
        raise ValueError("Not a DHIS2 data value export: it needs an org unit, a data element and a value column.")
    index = {column: i for i, column in enumerate(columns)}
    out: List[Raw] = []
    for row in rows:
        quantity = row.get(value)
        if quantity in ("", None):
            continue
        fields = {"node": row.get(org), "product": row.get(element), "quantity": quantity}
        if period and row.get(period) not in ("", None):
            fields["period_code"] = str(row.get(period)).strip()
        aux = {}
        if org_name and row.get(org_name):
            aux["node_name"] = row.get(org_name)
        locations = {k: _cell_ref(sheet_name, index.get(c), row.get("_row", 0)) for k, c in (("node", org), ("product", element), ("quantity", value)) if c}
        out.append({"fields": fields, "locations": locations, "aux": aux, "row": row.get("_row", 0)})
    return out


def msupply_consumption(sheet_name: str, columns: List[str], rows: List[dict], mapping: Optional[dict] = None, **_) -> List[Raw]:
    """An mSupply store consumption extract: store code, item code, a month, a quantity,
    optionally stock on hand and days out of stock."""
    lookup = {normalise_header(c): c for c in columns}

    def pick(*names):
        for name in names:
            if name in lookup:
                return lookup[name]
        return None

    store = pick("store_code", "store", "store_name", "customer_code", "name_code")
    item = pick("item_code", "item", "item_name", "code")
    month = pick("month", "period", "date")
    quantity = pick("adjusted_monthly_consumption", "amc", "consumption", "quantity", "issued", "total_quantity")
    if not (store and item and quantity):
        raise ValueError("Not an mSupply consumption extract: it needs a store, an item and a consumption column.")
    soh = pick("stock_on_hand", "soh")
    stockout = pick("days_out_of_stock", "stockout_days", "days_out")
    index = {column: i for i, column in enumerate(columns)}
    out: List[Raw] = []
    for row in rows:
        value = row.get(quantity)
        if value in ("", None):
            continue
        fields = {"node": row.get(store), "product": row.get(item), "quantity": value}
        if month and row.get(month) not in ("", None):
            fields["period_code"] = str(row.get(month)).strip()
        aux = {}
        if soh and row.get(soh) not in ("", None):
            aux["stock_on_hand"] = row.get(soh)
        if stockout and row.get(stockout) not in ("", None):
            aux["stockout_days"] = row.get(stockout)
        locations = {k: _cell_ref(sheet_name, index.get(c), row.get("_row", 0)) for k, c in (("node", store), ("product", item), ("quantity", quantity)) if c}
        out.append({"fields": fields, "locations": locations, "aux": aux, "row": row.get("_row", 0)})
    return out


ADAPTERS: Dict[str, Callable[..., List[Raw]]] = {
    "generic": generic,
    "dhis2": dhis2_consumption,
    "msupply": msupply_consumption,
}


def choose(system: str, domain: str, columns: List[str]) -> str:
    """Which adapter serves this file: a known system's export when the columns say
    so, the generic mapper otherwise."""
    if domain == "Demand":
        if system == "dhis2":
            try:
                dhis2_consumption("probe", columns, [])
                return "dhis2"
            except ValueError:
                pass
        if system == "msupply":
            try:
                msupply_consumption("probe", columns, [])
                return "msupply"
            except ValueError:
                pass
    return "generic"


def run(adapter: str, sheet_name: str, columns: List[str], rows: List[dict], mapping: Dict[str, Optional[str]], aux_columns: Optional[List[str]] = None) -> List[Raw]:
    fn = ADAPTERS.get(adapter) or generic
    return fn(sheet_name, columns, rows, mapping, aux_columns=aux_columns)


def period_to_int(code: str) -> Optional[int]:
    """"2025-03" or "202503" -> 3; "2025" -> 0 (annual); a quarter -> None (not monthly)."""
    if not code:
        return None
    text = str(code).strip()
    if len(text) == 4 and text.isdigit():
        return 0
    period = profiling._as_period(text)
    if not period:
        return None
    if "Q" in period:
        return None
    return int(period[-2:])
