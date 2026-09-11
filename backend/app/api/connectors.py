"""Live LMIS connections: configure, test, preview, apply.

The shape of this API is the argument. There is no "sync now" button that reaches
out and rewrites a national facility list. There is *test*, which tells you what the
account can and cannot read; *preview*, which pulls, validates and reconciles without
writing anything; and then the ordinary commit endpoint the Excel importer already
uses. A live connection is a faster way to fill the same funnel, not a way round it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import connectors
from ..connectors.base import ConnectorError
from ..connectors.reconcile import reconcile
from ..db import get_session
from ..io.validation import validate_dataset
from ..models import Connection, Country, ImportBatch, Node, Product

router = APIRouter(tags=["connections"])

DEFAULT_BBOX = {"min_lat": -90, "max_lat": 90, "min_lon": -180, "max_lon": 180}


class ConnectionIn(BaseModel):
    name: str
    system: str
    base_url: str = ""
    auth_type: str = "basic"
    username: str = ""
    #: Write-only. Never echoed back by any endpoint.
    secret: Optional[str] = None
    #: Name of an environment variable holding the credential. Preferred.
    secret_env: str = ""
    verify_tls: bool = True
    timeout_s: float = 30.0
    enabled: bool = True
    config: dict = Field(default_factory=dict)


class ConnectionPatch(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    auth_type: Optional[str] = None
    username: Optional[str] = None
    secret: Optional[str] = None
    secret_env: Optional[str] = None
    verify_tls: Optional[bool] = None
    timeout_s: Optional[float] = None
    enabled: Optional[bool] = None
    config: Optional[dict] = None


def _public(connection: Connection) -> dict:
    """A connection as the API returns it: everything except the credential."""
    return {
        "id": connection.id,
        "country_id": connection.country_id,
        "name": connection.name,
        "system": connection.system,
        "base_url": connection.base_url,
        "auth_type": connection.auth_type,
        "username": connection.username,
        "secret_env": connection.secret_env,
        "secret_set": connection.has_secret(),
        "secret_source": (
            "environment" if connection.secret_env else ("stored" if connection.secret else "none")
        ),
        "verify_tls": connection.verify_tls,
        "timeout_s": connection.timeout_s,
        "enabled": connection.enabled,
        "config": connection.config or {},
        "last_tested_at": connection.last_tested_at,
        "last_test_ok": connection.last_test_ok,
        "last_test_detail": connection.last_test_detail or {},
        "last_sync_at": connection.last_sync_at,
        "last_sync_summary": connection.last_sync_summary or {},
    }


def _connection_or_404(session: Session, connection_id: int) -> Connection:
    connection = session.get(Connection, connection_id)
    if not connection:
        raise HTTPException(404, f"No connection with id {connection_id}.")
    return connection


def _country_or_404(session: Session, country_id: int) -> Country:
    country = session.get(Country, country_id)
    if not country:
        raise HTTPException(404, f"No country with id {country_id}.")
    return country


@router.get("/connectors")
def list_connectors():
    """What this build can connect to, and what each connection needs configuring."""
    return {
        "systems": connectors.describe_all(),
        "note": (
            "Connectors are read-only: nothing here ever writes to an LMIS. None of them "
            "returns transport lanes either — no logistics system knows which boat calls at "
            "which island, so the network comes from the workbook and from interviews."
        ),
    }


@router.get("/countries/{country_id}/connections")
def list_connections(country_id: int, session: Session = Depends(get_session)):
    _country_or_404(session, country_id)
    rows = session.scalars(
        select(Connection).where(Connection.country_id == country_id).order_by(Connection.id)
    )
    return [_public(row) for row in rows]


@router.post("/countries/{country_id}/connections", status_code=201)
def create_connection(country_id: int, payload: ConnectionIn, session: Session = Depends(get_session)):
    _country_or_404(session, country_id)
    if payload.system not in connectors.REGISTRY:
        raise HTTPException(
            400,
            f"'{payload.system}' is not a system this build can connect to. Available: "
            f"{', '.join(sorted(connectors.REGISTRY))}.",
        )
    if session.scalar(
        select(Connection).where(Connection.country_id == country_id, Connection.name == payload.name)
    ):
        raise HTTPException(409, f"A connection named '{payload.name}' already exists.")

    fields = payload.model_dump()
    secret = fields.pop("secret", None)
    connection = Connection(country_id=country_id, secret=secret or "", **fields)
    session.add(connection)
    session.commit()
    return _public(connection)


@router.patch("/connections/{connection_id}")
def update_connection(
    connection_id: int, payload: ConnectionPatch, session: Session = Depends(get_session)
):
    connection = _connection_or_404(session, connection_id)
    for field_name, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(connection, field_name, value)
    session.commit()
    return _public(connection)


@router.delete("/connections/{connection_id}", status_code=204)
def delete_connection(connection_id: int, session: Session = Depends(get_session)):
    session.delete(_connection_or_404(session, connection_id))
    session.commit()


@router.post("/connections/{connection_id}/test")
def test_connection(connection_id: int, session: Session = Depends(get_session)):
    """Reach the server, authenticate, and confirm each thing the sync will need.

    Reported as individual checks rather than one boolean, because 'connection
    failed' cannot be acted on and 'authenticated, but this account cannot read
    organisation units' can.
    """
    connection = _connection_or_404(session, connection_id)
    if not connection.base_url:
        raise HTTPException(400, "This connection has no base URL yet.")

    try:
        connector = connectors.build(connection)
        info = connector.test().as_dict()
    except ConnectorError as exc:
        info = {
            "ok": False,
            "system": connection.system,
            "version": "",
            "detail": str(exc),
            "checks": [{"name": exc.step or "connection", "ok": False, "detail": str(exc)}],
            "error": exc.as_dict(),
        }

    connection.last_tested_at = datetime.now(timezone.utc)
    connection.last_test_ok = bool(info.get("ok"))
    connection.last_test_detail = info
    session.commit()
    return info


@router.post("/connections/{connection_id}/preview")
def preview_sync(
    connection_id: int,
    include_demand: bool = True,
    limit: Optional[int] = None,
    session: Session = Depends(get_session),
):
    """Pull, validate and reconcile. Writes an uncommitted batch and nothing else.

    The result is deliberately the same shape the Excel validation endpoint returns,
    plus a reconciliation block, so the interface that reviews a spreadsheet reviews a
    live pull with no special cases.
    """
    connection = _connection_or_404(session, connection_id)
    country = _country_or_404(session, connection.country_id)
    if not connection.enabled:
        raise HTTPException(409, "This connection is disabled.")

    try:
        connector = connectors.build(connection)
        fetched = connector.fetch(include_demand=include_demand, limit=limit)
    except ConnectorError as exc:
        raise HTTPException(502, detail=exc.as_dict()) from exc

    parsed = fetched.as_payload()

    existing = list(session.scalars(select(Node).where(Node.country_id == country.id)))
    existing_products = {
        p.sku for p in session.scalars(select(Product).where(Product.country_id == country.id))
    }

    # The identical validator the workbook goes through, in partial mode. A facility
    # pulled from a ministry's own DHIS2 with a coordinate in the sea is still a
    # facility in the sea, and still blocks. What partial mode relaxes is only the
    # rules that assume the payload is the entire model -- a connector carries no
    # lanes by design, and rejecting a national facility list for that would make the
    # integration useless without making the data any better.
    report = validate_dataset(
        country_code=country.code,
        bbox=(country.config or {}).get("bbox", DEFAULT_BBOX),
        boundary=country.boundary or {},
        nodes=parsed["nodes"],
        edges=parsed["edges"],
        products=parsed["products"],
        demand=parsed["demand"],
        partial=True,
        existing_node_codes={n.code for n in existing},
        existing_product_skus=existing_products,
    )

    matching = reconcile(parsed["nodes"], existing, connection.system)

    batch = ImportBatch(
        country_id=country.id,
        filename=f"{connection.name} ({connection.system})",
        source=connection.system,
        connection_id=connection.id,
        mode="merge",
        status="blocked" if report.blocking else "validated",
        committed=False,
        report={
            **report.as_dict(),
            "missing_sheets": [],
            "parsed": parsed,
            "reconciliation": matching.as_dict(),
            "connector_warnings": fetched.warnings,
            "connector_stats": fetched.stats,
        },
    )
    session.add(batch)
    session.commit()

    payload = report.as_dict()
    payload.update(
        {
            "batch_id": batch.id,
            "connection": _public(connection),
            "reconciliation": matching.as_dict(),
            "connector_warnings": fetched.warnings,
            "stats": fetched.stats,
            "commit_mode": "merge",
            "commit_note": (
                "Applying this merges facilities and the demand it supplies. Lanes are left "
                "untouched: no logistics system knows the transport network, and dropping it "
                "would destroy the part of the model that is hardest to rebuild."
            ),
            "preview": {"nodes": parsed["nodes"][:5], "products": parsed["products"][:5]},
        }
    )
    return payload


@router.post("/connections/{connection_id}/record-sync")
def record_sync(connection_id: int, summary: dict, session: Session = Depends(get_session)):
    """Stamp a connection after its batch has been committed."""
    connection = _connection_or_404(session, connection_id)
    connection.last_sync_at = datetime.now(timezone.utc)
    connection.last_sync_summary = summary or {}
    session.commit()
    return _public(connection)
