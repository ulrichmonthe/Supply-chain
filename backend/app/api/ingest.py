"""Import and export.

The import is two-step by design: upload and validate, look at the report, then
commit. A partially applied import is worse than a rejected one, because nobody can
tell afterwards which half of the data is real.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_session
from ..engine import seasonality
from ..io import excel_in, excel_out
from ..io.apply import apply_payload
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


@router.post("/imports/{batch_id}/commit")
def commit_import(batch_id: int, replace: bool = True, session: Session = Depends(get_session)):
    """Apply a validated import.

    ``replace`` wipes the country's network first. That is the honest default for a
    master-list refresh from a workbook: a merge that silently keeps orphaned
    facilities from last year's list is how a model drifts away from reality. A
    connector sync uses merge instead, and applies through the same function.
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
            "edges": counts["edges"],
            "products": counts["products_created"],
            "demand": counts["demand_rows"],
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
