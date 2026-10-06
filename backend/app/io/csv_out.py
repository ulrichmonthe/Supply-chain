"""The CSV dump: the same four tables as the workbook, as plain text.

A workbook is what a ministry opens; a CSV is what every other system reads. The dump
is a zip of one CSV per table in the template's columns, plus a note, so the model
can leave the tool for a database, a script or another tool without anyone opening
Excel in between. Each table is also available on its own.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timezone

from .excel_out import network_rows
from .schema_spec import SHEETS

TABLES = {"nodes": "Nodes", "edges": "Edges", "products": "Products", "demand": "Demand"}


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))
    return str(value)


def table_csv(headers, rows) -> bytes:
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    return stream.getvalue().encode("utf-8-sig")


def one_table(table: str, nodes, edges, products, demand) -> bytes:
    sheet = TABLES[table]
    headers, rows = network_rows(nodes, edges, products, demand)[sheet]
    return table_csv(headers, rows)


def dump(country, nodes, edges, products, demand) -> bytes:
    """A zip: nodes.csv, edges.csv, products.csv, demand.csv and README.txt."""
    tables = network_rows(nodes, edges, products, demand)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for table, sheet in TABLES.items():
            headers, rows = tables[sheet]
            archive.writestr(f"{table}.csv", table_csv(headers, rows))
        columns = "\n".join(
            f"\n{sheet}\n" + "\n".join(f"  {name}: {text}" for name, text in SHEETS[sheet]) for sheet in TABLES.values()
        )
        archive.writestr(
            "README.txt",
            f"{country.name} - network export as CSV, {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n\n"
            "One file per table, UTF-8 with a byte-order mark so Excel opens it cleanly, comma separated, "
            "a header row of the template's column names. Each file can be imported back on its own through "
            "Data -> Import a CSV, which maps the columns automatically because they are already ours.\n"
            f"\nColumns:{columns}\n",
        )
    return stream.getvalue()
