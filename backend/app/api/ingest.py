"""Import and export.

The import is two-step by design: upload and validate, look at the report, then
commit. A partially applied import is worse than a rejected one, because nobody can
tell afterwards which half of the data is real.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_session
from .deps import author_claim, new_batch_id
from ..engine import seasonality
from ..io import csv_out, excel_in, excel_out, mapper
from ..io.apply import CONFLICT_POLICIES, apply_payload
from ..io.diff import compute_changes
from ..io.validation import validate_dataset
from ..models import Country, Demand, Edge, ImportBatch, Node, Product
from ..schemas import ImportRowPatch

router = APIRouter(tags=["data"])

#: Used when a country has not set its own. Rejects nothing, which is the honest
#: default: a made-up bounding box would reject real facilities.
WORLD_BBOX = {"min_lat": -90, "max_lat": 90, "min_lon": -180, "max_lon": 180}


def _country_or_404(session: Session, country_id: int) -> Country:
    country = session.get(Country, country_id)
    if not country:
        raise HTTPException(404, f"No country with id {country_id}.")
    return country


@router.get("/template.xlsx")
def download_template():
    return Response(
        content=excel_out.build_template(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="network-design-template.xlsx"'},
    )


@router.get("/countries/{country_id}/export/network.xlsx")
def export_network(country_id: int, session: Session = Depends(get_session)):
    country = _country_or_404(session, country_id)
    nodes = list(session.scalars(select(Node).where(Node.country_id == country_id).order_by(Node.level, Node.code)))
    edges = list(session.scalars(select(Edge).where(Edge.country_id == country_id).order_by(Edge.code)))
    products = list(session.scalars(select(Product).where(Product.country_id == country_id)))
    demand = list(session.scalars(select(Demand).where(Demand.country_id == country_id)))
    payload = excel_out.export_network(country, nodes, edges, products, demand)
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{country.code}-network.xlsx"'},
    )


def _loaded(session: Session, country_id: int):
    nodes = list(session.scalars(select(Node).where(Node.country_id == country_id).order_by(Node.level, Node.code)))
    edges = list(session.scalars(select(Edge).where(Edge.country_id == country_id).order_by(Edge.code)))
    products = list(session.scalars(select(Product).where(Product.country_id == country_id)))
    demand = list(session.scalars(select(Demand).where(Demand.country_id == country_id)))
    return nodes, edges, products, demand


@router.get("/countries/{country_id}/export/csv.zip")
def export_csv_dump(country_id: int, session: Session = Depends(get_session)):
    """The whole model as one CSV per table, zipped: the way out for every system that is
    not a spreadsheet."""
    country = _country_or_404(session, country_id)
    payload = csv_out.dump(country, *_loaded(session, country_id))
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{country.code}-network-csv.zip"'},
    )


@router.get("/countries/{country_id}/export/{table}.csv")
def export_table_csv(country_id: int, table: str, session: Session = Depends(get_session)):
    """One table as CSV, in the template's columns, importable back on its own."""
    country = _country_or_404(session, country_id)
    if table not in csv_out.TABLES:
        raise HTTPException(404, f"No table called {table}. Choose from {', '.join(csv_out.TABLES)}.")
    payload = csv_out.one_table(table, *_loaded(session, country_id))
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{country.code}-{table}.csv"'},
    )


@router.post("/countries/{country_id}/validate")
async def validate_upload(
    country_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    """Parse and validate a workbook. Nothing is written to the model."""
    country = _country_or_404(session, country_id)
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB.")

    try:
        parsed = excel_in.parse_workbook(data)
    except Exception as exc:  # noqa: BLE001 - surface the real reason to the user
        raise HTTPException(
            400,
            f"That file could not be read as an Excel workbook ({exc}). Save it as .xlsx and "
            f"try again — .xls and .csv are not supported here.",
        ) from exc

    report = validate_dataset(
        country_code=country.code,
        bbox=(country.config or {}).get("bbox", WORLD_BBOX),
        boundary=country.boundary or {},
        nodes=parsed["nodes"],
        edges=parsed["edges"],
        products=parsed["products"],
        demand=parsed["demand"],
    )

    batch = ImportBatch(
        country_id=country_id,
        filename=file.filename or "upload.xlsx",
        status="blocked" if report.blocking else "validated",
        committed=False,
        report={**report.as_dict(), "missing_sheets": parsed["missing_sheets"], "parsed": parsed},
    )
    session.add(batch)
    session.commit()
    session.refresh(batch)

    payload = report.as_dict()
    payload["batch_id"] = batch.id
    payload["missing_sheets"] = parsed["missing_sheets"]
    payload["preview"] = {
        "nodes": parsed["nodes"][:5],
        "edges": parsed["edges"][:5],
        "products": parsed["products"][:5],
    }
    return payload


@router.patch("/imports/{batch_id}/rows")
def correct_import_row(
    batch_id: int,
    payload: ImportRowPatch,
    session: Session = Depends(get_session),
    author: str = Depends(author_claim),
):
    """Correct a row inside an import that has not been committed, and re-check it.

    This is the answer to a validation report that says a clinic is three kilometres out
    to sea. Before this existed the only remedy was to reopen the workbook, find the
    row, fix it, and upload the file again — which is a bulk operation to change one
    cell, and sends the person who spotted the problem away from the screen that showed
    it to them.

    Nothing live is touched. The correction is applied to the parsed payload held in the
    uncommitted batch, the whole dataset is validated again, and the report comes back.
    The model still changes only at commit, exactly as before.
    """
    batch = session.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, f"No import batch with id {batch_id}.")
    if batch.committed:
        raise HTTPException(409, "That import has already been applied; corrections would have nowhere to go.")

    country = _country_or_404(session, batch.country_id)
    report_blob = dict(batch.report or {})
    parsed = dict(report_blob.get("parsed") or {})

    sheet = payload.sheet
    rows = list(parsed.get(sheet) or [])
    if not rows:
        raise HTTPException(400, f"This import has no {sheet} to correct.")

    index = next((i for i, row in enumerate(rows) if str(row.get(payload.key_field)) == str(payload.key)), None)
    if index is None:
        raise HTTPException(404, f"No {sheet[:-1]} with {payload.key_field} '{payload.key}' in this import.")

    row = dict(rows[index])
    before = {field: row.get(field) for field in payload.values}
    row.update(payload.values)
    rows[index] = row
    parsed[sheet] = rows

    report = validate_dataset(
        country_code=country.code,
        bbox=(country.config or {}).get("bbox", WORLD_BBOX),
        boundary=country.boundary or {},
        nodes=parsed.get("nodes") or [],
        edges=parsed.get("edges") or [],
        products=parsed.get("products") or [],
        demand=parsed.get("demand") or [],
        partial=bool(report_blob.get("partial")),
        existing_node_codes=set(report_blob.get("existing_node_codes") or []) or None,
        existing_product_skus=set(report_blob.get("existing_product_skus") or []) or None,
    )

    corrections = list(report_blob.get("corrections") or [])
    corrections.append(
        {
            "sheet": sheet,
            "key": payload.key,
            "before": before,
            "after": payload.values,
            "reason": payload.reason or "Corrected during review.",
            "author": author,
        }
    )

    batch.report = {
        **report_blob,
        **report.as_dict(),
        "parsed": parsed,
        "corrections": corrections,
    }
    batch.status = "blocked" if report.blocking else "validated"
    session.commit()

    payload_out = report.as_dict()
    payload_out["batch_id"] = batch.id
    payload_out["corrections"] = corrections
    payload_out["corrected"] = {"sheet": sheet, "key": payload.key, "values": payload.values}
    return payload_out


@router.get("/imports/{batch_id}/changes")
def preview_changes(batch_id: int, replace: bool = True, session: Session = Depends(get_session)):
    """What applying this import would do -- adds, updates, retirements, restorations and
    conflicts -- computed against the model as it is now. Reads only.

    A conflict is a field the file changed *and* somebody changed by hand in the tool
    since the last import. Those are decisions, so they are all listed, never sampled.
    """
    batch = session.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, f"No import with id {batch_id}.")
    if batch.committed:
        raise HTTPException(409, "That import has already been applied.")
    country = _country_or_404(session, batch.country_id)
    parsed = batch.report.get("parsed") or {}
    changes = compute_changes(
        session, country, parsed, mode="replace" if replace else "merge", source=batch.source or "excel"
    )
    return {"batch_id": batch.id, **changes.as_dict()}


@router.post("/imports/{batch_id}/commit")
def commit_import(
    batch_id: int,
    replace: bool = True,
    conflicts: str = "keep",
    take_file: list[str] = Query(default=[], description='Conflicts to resolve in the file\'s favour, as "sheet:key:field".'),
    session: Session = Depends(get_session),
    author: str = Depends(author_claim),
):
    """Apply a validated import.

    ``replace`` is a full refresh: rows the file no longer mentions are retired -- kept
    with their history, hidden from the solver, restorable -- never deleted. A merge
    (what a connector sync uses, through this same function) leaves them alone.

    ``conflicts`` decides what happens where the file disagrees with a correction made
    in the tool since the last import: ``keep`` the correction (the default) or
    ``take_file``. ``take_file`` may also name individual conflicts to override while
    keeping the rest. Every resolution is written to the ledger either way.
    """
    batch = session.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, f"No import batch with id {batch_id}.")
    if batch.committed:
        raise HTTPException(409, "That import has already been applied.")
    if batch.report.get("blocking"):
        raise HTTPException(
            409,
            "This import has errors that must be fixed first. Correct the rows here and they "
            "are re-checked as you go, or fix the workbook and upload it again.",
        )

    if conflicts not in CONFLICT_POLICIES:
        raise HTTPException(400, f"conflicts must be one of {CONFLICT_POLICIES}.")
    country = _country_or_404(session, batch.country_id)
    parsed = batch.report.get("parsed") or {}
    mode = "replace" if replace else "merge"

    counts = apply_payload(
        session,
        country,
        parsed,
        mode=mode,
        source=batch.source or "excel",
        reference=batch.filename,
        author_claim=author,
        batch_id=new_batch_id(),
        conflict_policy=conflicts,
        take_file=take_file,
    )

    batch.committed = True
    batch.status = "committed"
    batch.mode = mode
    session.commit()

    return {
        "committed": True,
        "mode": mode,
        "counts": {
            "nodes": counts["nodes_created"] + counts["nodes_updated"],
            "nodes_created": counts["nodes_created"],
            "nodes_updated": counts["nodes_updated"],
            "nodes_retired": counts["nodes_retired"],
            "nodes_restored": counts["nodes_restored"],
            "edges": counts["edges"],
            "edges_retired": counts["edges_retired"],
            "products": counts["products_created"],
            "demand": counts["demand_rows"],
            "demand_retired": counts["demand_retired"],
            "conflicts": counts["conflicts"],
        },
        "distances_computed_by_cascade": counts["distances_computed"],
        "note": (
            "Existing scenarios were kept. Re-run them: their results refer to the previous "
            "network until you do."
        ),
    }


@router.get("/imports/{batch_id}")
def get_import(batch_id: int, session: Session = Depends(get_session)):
    batch = session.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, f"No import batch with id {batch_id}.")
    report = dict(batch.report)
    report.pop("parsed", None)
    return {
        "id": batch.id,
        "filename": batch.filename,
        "status": batch.status,
        "committed": batch.committed,
        "created_at": batch.created_at,
        "report": report,
    }


@router.get("/countries/{country_id}/imports")
def list_imports(country_id: int, session: Session = Depends(get_session)):
    _country_or_404(session, country_id)
    batches = session.scalars(
        select(ImportBatch).where(ImportBatch.country_id == country_id).order_by(ImportBatch.id.desc())
    )
    out = []
    for batch in batches:
        report = dict(batch.report)
        report.pop("parsed", None)
        report.pop("issues", None)
        out.append(
            {
                "id": batch.id,
                "filename": batch.filename,
                "status": batch.status,
                "committed": batch.committed,
                "created_at": batch.created_at,
                "counts": report.get("counts", {}),
                "headline": report.get("headline", ""),
            }
        )
    return out


@router.get("/seasonality/profiles")
def seasonality_profiles():
    return {
        "months": seasonality.MONTHS,
        "access": seasonality.PROFILES,
        "cost": seasonality.COST_PROFILES,
    }


# --- the column mapper -------------------------------------------------------------------


def _csv_bytes(data: bytes, filename: str) -> None:
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB.")
    if filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "That is a workbook. Drop it on the workbook target above; this door is for CSV files.")


@router.post("/countries/{country_id}/imports/csv/inspect")
async def inspect_csv(country_id: int, file: UploadFile = File(...), session: Session = Depends(get_session)):
    """What is in the file, which of our sheets it resembles, and a first guess at the
    mapping -- plus any mapping saved for this country whose columns are all present."""
    country = _country_or_404(session, country_id)
    data = await file.read()
    _csv_bytes(data, file.filename or "")
    try:
        columns, rows, delimiter = mapper.read_csv(data)
    except ValueError as error:
        raise HTTPException(400, str(error))
    sheet = mapper.guess_sheet(columns)
    saved = mapper.matching_saved(country, columns)
    guesses = {name: mapper.guess_mapping(columns, name) for name in mapper.COLUMNS}
    if saved:
        sheet = saved["sheet"]
        guesses[sheet] = {**guesses[sheet], **saved["mapping"]}
    return {
        "filename": file.filename,
        "columns": columns,
        "row_count": len(rows),
        "sample": [{k: v for k, v in row.items() if k != "_row"} for row in rows[:5]],
        "delimiter": delimiter,
        "sheet": sheet,
        "guesses": guesses,
        "fields": {name: mapper.our_fields(name) for name in mapper.COLUMNS},
        "matched_saved": saved["name"] if saved else None,
        "saved": mapper.saved_mappings(country),
    }


@router.post("/countries/{country_id}/imports/csv")
async def import_csv(
    country_id: int,
    file: UploadFile = File(...),
    sheet: str = Form(...),
    mapping: str = Form(..., description="JSON: our field -> the file's column."),
    save_as: str = Form("", description="Keep this mapping under a name for next time."),
    session: Session = Depends(get_session),
):
    """Map the file's columns to ours and validate it as a partial import.

    Partial, because a CSV is one sheet: a facility list carries no lanes by design and
    must not be rejected for it. The batch it produces goes through the same preview
    and commit as a workbook, in merge mode -- rows the file does not mention are left
    alone, never retired."""
    country = _country_or_404(session, country_id)
    if sheet not in mapper.COLUMNS:
        raise HTTPException(400, f"sheet must be one of {', '.join(mapper.COLUMNS)}.")
    try:
        chosen = json.loads(mapping or "{}")
        if not isinstance(chosen, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "mapping must be a JSON object of our field -> the file's column.")
    data = await file.read()
    _csv_bytes(data, file.filename or "")
    try:
        columns, rows, _ = mapper.read_csv(data)
    except ValueError as error:
        raise HTTPException(400, str(error))
    unknown = [column for column in chosen.values() if column and column not in columns]
    if unknown:
        raise HTTPException(400, f"The file has no column called {', '.join(repr(c) for c in unknown)}.")
    missing = mapper.missing_required(sheet, chosen)
    if missing:
        raise HTTPException(400, f"A {sheet} file needs a column for {', '.join(missing)}. Map them and try again.")

    raw = mapper.apply_mapping(rows, chosen)
    parsed = excel_in.assemble(
        raw if sheet == "Nodes" else [],
        raw if sheet == "Edges" else [],
        raw if sheet == "Products" else [],
        raw if sheet == "Demand" else [],
        [name for name in mapper.COLUMNS if name != sheet],
    )
    mapped_keys = [field for field, column in chosen.items() if column]
    sheet_key = {"Nodes": "nodes", "Edges": "edges", "Products": "products", "Demand": "demand"}[sheet]
    parsed[sheet_key] = mapper.prune(parsed[sheet_key], sheet, mapped_keys)
    existing_nodes = {n.code for n in session.scalars(select(Node).where(Node.country_id == country.id))}
    existing_products = {p.sku for p in session.scalars(select(Product).where(Product.country_id == country.id))}
    report = validate_dataset(
        country_code=country.code,
        bbox=(country.config or {}).get("bbox", WORLD_BBOX),
        boundary=country.boundary or {},
        nodes=parsed["nodes"],
        edges=parsed["edges"],
        products=parsed["products"],
        demand=parsed["demand"],
        partial=True,
        existing_node_codes=existing_nodes,
        existing_product_skus=existing_products,
    )
    if save_as.strip():
        mapper.save_mapping(
            country,
            name=" ".join(save_as.split()).strip()[:80],
            sheet=sheet,
            mapping=chosen,
            columns=columns,
            saved_at=datetime.now(timezone.utc).isoformat(),
        )
    batch = ImportBatch(
        country_id=country_id,
        filename=file.filename or "upload.csv",
        source="csv",
        mode="merge",
        status="blocked" if report.blocking else "validated",
        committed=False,
        report={**report.as_dict(), "missing_sheets": parsed["missing_sheets"], "parsed": parsed, "sheet": sheet, "mapping": chosen},
    )
    session.add(batch)
    session.commit()
    session.refresh(batch)
    payload = report.as_dict()
    payload["batch_id"] = batch.id
    payload["missing_sheets"] = parsed["missing_sheets"]
    payload["source"] = "csv"
    payload["sheet"] = sheet
    payload["rows"] = len(raw)
    payload["preview"] = {key: parsed[key][:5] for key in ("nodes", "edges", "products", "demand")}
    payload["saved"] = mapper.saved_mappings(country)
    return payload


@router.get("/countries/{country_id}/column-mappings")
def list_column_mappings(country_id: int, session: Session = Depends(get_session)):
    return mapper.saved_mappings(_country_or_404(session, country_id))


@router.delete("/countries/{country_id}/column-mappings/{name}", status_code=204)
def delete_column_mapping(country_id: int, name: str, session: Session = Depends(get_session)):
    country = _country_or_404(session, country_id)
    mapper.delete_mapping(country, name)
    session.commit()
