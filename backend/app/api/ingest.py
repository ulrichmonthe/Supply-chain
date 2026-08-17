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
from ..engine.distance import resolve_distance
from ..io import excel_in, excel_out
from ..io.validation import validate_dataset
from ..models import AuditEntry, Country, Demand, Edge, ImportBatch, Node, Product

router = APIRouter(tags=["data"])


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
        bbox=(country.config or {}).get("bbox", {"min_lat": -90, "max_lat": 90, "min_lon": -180, "max_lon": 180}),
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


@router.post("/imports/{batch_id}/commit")
def commit_import(batch_id: int, replace: bool = True, session: Session = Depends(get_session)):
    """Apply a validated import.

    ``replace`` wipes the country's network first. That is the honest default for a
    master-list refresh: a merge that silently keeps orphaned facilities from last
    year's list is how a model drifts away from reality.
    """
    batch = session.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, f"No import batch with id {batch_id}.")
    if batch.committed:
        raise HTTPException(409, "That import has already been applied.")
    if batch.report.get("blocking"):
        raise HTTPException(
            409,
            "This import has errors that must be fixed first. Download the report, correct "
            "the workbook, and validate it again.",
        )

    country = _country_or_404(session, batch.country_id)
    parsed = batch.report.get("parsed") or {}

    if replace:
        for model in (Demand, Edge, Node, Product):
            for row in session.scalars(select(model).where(model.country_id == country.id)):
                session.delete(row)
        session.flush()

    nodes: dict[str, Node] = {}
    for record in parsed.get("nodes", []):
        node = Node(
            country_id=country.id,
            code=record["code"],
            name=record["name"],
            level=record["level"],
            type=record["type"],
            lat=record["lat"],
            lon=record["lon"],
            geocode_confidence=record["geocode_confidence"],
            geocode_source=record["geocode_source"],
            admin1=record["admin1"],
            admin2=record["admin2"],
            terrain_class=record["terrain_class"],
            catchment_population=record["catchment_population"],
            operating_status=record["operating_status"],
            capacity=record["capacity"],
            hub_capable=record["hub_capable"],
            hub_fixed_cost=record["hub_fixed_cost"],
            hub_open_capex=record["hub_open_capex"],
            hub_throughput_m3=record["hub_throughput_m3"],
            external_ids=record["external_ids"],
        )
        session.add(node)
        nodes[record["code"]] = node

    products: dict[str, Product] = {}
    for record in parsed.get("products", []):
        product = Product(
            country_id=country.id,
            sku=record["sku"],
            name=record["name"],
            temperature_band=record["temperature_band"],
            volume_per_unit_cm3=record["volume_per_unit_cm3"],
            unit_cost=record["unit_cost"],
            shelf_life_days=record["shelf_life_days"],
        )
        session.add(product)
        products[record["sku"]] = product
    session.flush()

    computed_distances = 0
    for record in parsed.get("edges", []):
        origin = nodes.get(record["from_node"])
        destination = nodes.get(record["to_node"])
        if not origin or not destination:
            continue

        manual_km = record["distance_km"] if record["distance_method"] == "manual" else None
        if record["distance_km"] and not record["distance_method"]:
            manual_km = record["distance_km"]

        resolved = resolve_distance(
            origin.lat,
            origin.lon,
            destination.lat,
            destination.lon,
            mode=record["mode"],
            terrain_class=destination.terrain_class,
            manual_km=manual_km,
            manual_hours=record["base_travel_time_hr"] or None,
            manual_note=record["distance_note"] or "",
        )
        if resolved.method != "manual":
            computed_distances += 1

        session.add(
            Edge(
                country_id=country.id,
                code=record["code"] or f"{record['from_node']}-{record['to_node']}",
                from_node_id=origin.id,
                to_node_id=destination.id,
                mode=record["mode"],
                service_name=record["service_name"],
                service_frequency=record["service_frequency"],
                service_days=record["service_days"],
                capacity_per_trip_m3=record["capacity_per_trip_m3"],
                cold_capacity_per_trip_m3=record["cold_capacity_per_trip_m3"],
                fixed_cost_per_trip=record["fixed_cost_per_trip"],
                variable_cost_per_km=record["variable_cost_per_km"],
                cost_per_m3=record["cost_per_m3"],
                monthly_access=record["monthly_access"] or [1.0] * 12,
                monthly_cost_multiplier=record["monthly_cost_multiplier"] or [1.0] * 12,
                reliability=record["reliability"],
                lead_time_sd_days=record["lead_time_sd_days"],
                active=record["active"],
                **resolved.as_edge_fields(),
            )
        )

    for record in parsed.get("demand", []):
        node = nodes.get(record["node"])
        product = products.get(record["product"])
        if not node or not product:
            continue
        session.add(
            Demand(
                country_id=country.id,
                node_id=node.id,
                product_id=product.id,
                period=record["period"],
                quantity=record["quantity"] or 0.0,
                source=record["source"],
                confidence=record["confidence"],
            )
        )

    batch.committed = True
    batch.status = "committed"
    session.add(
        AuditEntry(
            country_id=country.id,
            entity_type="global",
            entity_ref=batch.filename,
            field="import",
            new_value=(
                f"{len(parsed.get('nodes', []))} nodes, {len(parsed.get('edges', []))} lanes, "
                f"{len(parsed.get('demand', []))} demand rows"
            ),
            provenance="import",
            confidence_marker="S",
            rationale=(
                f"Workbook '{batch.filename}' committed with replace={replace}. "
                f"{computed_distances} lane distances were produced by the cascade rather than "
                f"supplied."
            ),
            actor="analyst",
        )
    )
    session.commit()

    return {
        "committed": True,
        "counts": {
            "nodes": len(parsed.get("nodes", [])),
            "edges": len(parsed.get("edges", [])),
            "products": len(parsed.get("products", [])),
            "demand": len(parsed.get("demand", [])),
        },
        "distances_computed_by_cascade": computed_distances,
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
