"""The way out: the import workbook exactly as the model reads it, plus a provenance
sheet, and the crosswalk as a file the ministry can use anywhere.

The round trip is checked, not assumed: the test parses the exported workbook with the
importer and compares every value with staging.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Dict, List

from openpyxl import Workbook

from ..engine.seasonality import MONTH_ABBR
from ..io.excel_out import _autosize, _readme, _write_header
from ..io.schema_spec import DEMAND_COLUMNS, EDGE_COLUMNS, NODE_COLUMNS, PRODUCT_COLUMNS
from ..models import Crosswalk, SourceFile
from . import APPROVABLE
from .staging import value_of

PROVENANCE_COLUMNS = ["domain", "key", "field", "value", "class", "confidence", "source_system", "source_file", "location", "vintage", "unit", "transformation", "method", "inputs", "reason", "decided_by", "label"]
CONFIDENCE_NUMBER = {"high": 0.9, "medium": 0.6, "low": 0.3}


def _source_for(cls: str) -> str:
    return "actual" if cls in ("observed", "converted", "confirmed") else "proxy"


def records_as_rows(records: List) -> Dict[str, List[dict]]:
    """Staged records as the flat rows the workbook sheets take."""
    out: Dict[str, List[dict]] = {"Nodes": [], "Edges": [], "Products": [], "Demand": []}
    for record in records:
        if record.key.startswith("?"):
            continue
        fields = record.fields or {}
        if record.domain == "Demand":
            node, sku, period = (record.key.split("|") + ["0", "0"])[:3]
            item = fields.get("quantity") or {}
            if item.get("class") not in APPROVABLE or item.get("value") is None:
                continue
            out["Demand"].append({"node": node, "product": sku, "period": int(float(period)), "quantity": item.get("value"), "source": value_of(record, "source") or _source_for(item.get("class")), "confidence": value_of(record, "confidence") if value_of(record, "confidence") is not None else CONFIDENCE_NUMBER.get(item.get("confidence"), 0.5), "provenance_class": item.get("class")})
            continue
        row = {"code" if record.domain != "Products" else "sku": record.key}
        for name, item in fields.items():
            if item.get("class") in APPROVABLE and item.get("value") is not None:
                row[name] = item.get("value")
        access = row.pop("monthly_access", None)
        if isinstance(access, list) and len(access) == 12:
            for abbr, value in zip(MONTH_ABBR, access):
                row[f"access_{abbr.lower()}"] = value
        out[record.domain].append(row)
    return out


def build_workbook(country, run, records: List, sources: List[SourceFile]) -> bytes:
    """FR30: the four sheets in the template's columns, and the provenance sheet."""
    rows = records_as_rows(records)
    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet_name, columns in (("Nodes", NODE_COLUMNS), ("Edges", EDGE_COLUMNS), ("Products", PRODUCT_COLUMNS), ("Demand", DEMAND_COLUMNS + [("provenance_class", "observed | converted | confirmed | estimated")])):
        sheet = workbook.create_sheet(sheet_name)
        names = [name for name, _ in columns]
        _write_header(sheet, names)
        for row_index, row in enumerate(rows[sheet_name], start=2):
            for column_index, name in enumerate(names, start=1):
                value = row.get(name)
                if isinstance(value, (list, dict)):
                    value = json.dumps(value)
                sheet.cell(row=row_index, column=column_index, value=value)
        _autosize(sheet)

    by_id = {s.id: s for s in sources}
    prov = workbook.create_sheet("Provenance")
    _write_header(prov, PROVENANCE_COLUMNS)
    row_index = 2
    for record in records:
        if record.key.startswith("?"):
            continue
        for name, item in (record.fields or {}).items():
            source = by_id.get(item.get("source_file_id"))
            values = [record.domain, record.key, name, _cell(item.get("value")), item.get("class"), item.get("confidence"), item.get("system"), source.filename if source else "", item.get("location"), item.get("vintage"), item.get("unit"), item.get("transformation"), item.get("method"), json.dumps(item.get("inputs")) if item.get("inputs") else "", item.get("reason"), item.get("chosen_by") or item.get("by") or "", item.get("label", "")]
            for column_index, value in enumerate(values, start=1):
                prov.cell(row=row_index, column=column_index, value=value)
            row_index += 1
    _autosize(prov)

    _readme(workbook, f"{country.name} — onboarding {run.name}", [
        "The four data sheets are the model's import workbook, column for column. Upload them through Data → Import, or load this run from the Onboard tab.",
        "The Provenance sheet says where every value came from: its class (observed, converted, confirmed, estimated), the file and cell, the period it describes, any conversion applied, the estimation method and its inputs, and who decided it.",
        "Estimated values are directional, not for budgeting, until validated against an independent figure. Nothing illustrative or missing is in this file: those block sign-off.",
    ])
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _cell(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return value


def crosswalk_csv(rows: List[Crosswalk]) -> bytes:
    """FR11: the reconciled register, standalone."""
    systems = sorted({k for r in rows for k in (r.ids or {})})
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["canonical_code", "name", "type", "admin1", "admin2", "lat", "lon", "status", *systems, "history"])
    for row in rows:
        writer.writerow([row.canonical_code, row.name, row.type, row.admin1 or "", row.admin2 or "", row.lat if row.lat is not None else "", row.lon if row.lon is not None else "", row.status, *[(row.ids or {}).get(s, "") for s in systems], " | ".join(f"{h.get('at', '')[:10]} {h.get('change')}: {h.get('detail')}" for h in (row.history or []))])
    return stream.getvalue().encode("utf-8")
