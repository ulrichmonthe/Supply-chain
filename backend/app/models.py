"""ORM models.

This is the schema described in the MVP plan, with three deliberate choices carried
through verbatim because retrofitting them after country #2 would be a rewrite:

1. ``Node.level`` is an integer, not an enum. "AMS" is a label in the PNG country
   config, not a database concept. Country two will have four tiers, or six.
2. Every distance carries the method that produced it and a confidence. When a
   provincial health manager says "that road takes six hours, not two", you need to
   know instantly whether the number came from OSRM or a field interview -- and you
   need to overwrite it from the UI, not from a database console.
3. ``Edge.monthly_access`` is a 12-element vector on every edge. That single decision
   is what makes seasonality free rather than a bolt-on.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Country(Base):
    """A country workspace. All model objects hang off one of these."""

    __tablename__ = "country"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))

    currency: Mapped[str] = mapped_column(String(8), default="USD")
    # Config is intentionally free-form JSON: level_labels, terrain_classes, seasons,
    # temperature_bands, equity_definition, bbox, fx_rate.
    config: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    nodes: Mapped[list["Node"]] = relationship(back_populates="country", cascade="all, delete-orphan")
    edges: Mapped[list["Edge"]] = relationship(back_populates="country", cascade="all, delete-orphan")
    products: Mapped[list["Product"]] = relationship(back_populates="country", cascade="all, delete-orphan")
    scenarios: Mapped[list["Scenario"]] = relationship(back_populates="country", cascade="all, delete-orphan")


class Node(Base):
    __tablename__ = "node"
    __table_args__ = (
        UniqueConstraint("country_id", "code", name="uq_node_country_code"),
        Index("ix_node_country_level", "country_id", "level"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id", ondelete="CASCADE"), index=True)

    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))

    #: 0 = central store, 1 = intermediate (PNG: "AMS"), 2 = province, 3 = district/facility.
    level: Mapped[int] = mapped_column(Integer, default=3)
    type: Mapped[str] = mapped_column(String(64), default="health_facility")

    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    geocode_confidence: Mapped[float] = mapped_column(Float, default=0.5)
    geocode_source: Mapped[str] = mapped_column(String(64), default="unknown")

    admin1: Mapped[str | None] = mapped_column(String(128), nullable=True)
    admin2: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #: {"dry_m3": float, "cold_by_band": {"+2-8": m3, "-20": m3, "-70": m3}}
    capacity: Mapped[dict] = mapped_column(JSON, default=dict)

    operating_status: Mapped[str] = mapped_column(String(32), default="operational")
    catchment_population: Mapped[float] = mapped_column(Float, default=0.0)
    terrain_class: Mapped[str] = mapped_column(String(32), default="mainland_road")

    #: Can this node act as a distribution hub in a scenario?
    hub_capable: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Annualised fixed cost of operating this node as a hub.
    hub_fixed_cost: Mapped[float] = mapped_column(Float, default=0.0)
    #: Cost to open a currently-closed hub (one-off, appears in the roadmap).
    hub_open_capex: Mapped[float] = mapped_column(Float, default=0.0)
    #: Throughput ceiling in m3 per year when operating as a hub.
    hub_throughput_m3: Mapped[float] = mapped_column(Float, default=0.0)

    #: {"dhis2_uid": ..., "msupply_id": ..., "openlmis_code": ..., "mfl_code": ...}
    external_ids: Mapped[dict] = mapped_column(JSON, default=dict)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)

    country: Mapped[Country] = relationship(back_populates="nodes")


class Edge(Base):
    """A lane between two nodes. Roads, coastal shipping, air charters, river, foot."""

    __tablename__ = "edge"
    __table_args__ = (
        UniqueConstraint("country_id", "code", name="uq_edge_country_code"),
        Index("ix_edge_from_to", "from_node_id", "to_node_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id", ondelete="CASCADE"), index=True)

    code: Mapped[str] = mapped_column(String(96))
    from_node_id: Mapped[int] = mapped_column(ForeignKey("node.id", ondelete="CASCADE"))
    to_node_id: Mapped[int] = mapped_column(ForeignKey("node.id", ondelete="CASCADE"))

    #: road | sea | air | river | foot | drone
    mode: Mapped[str] = mapped_column(String(16), default="road")

    distance_km: Mapped[float] = mapped_column(Float, default=0.0)
    base_travel_time_hr: Mapped[float] = mapped_column(Float, default=0.0)

    #: osrm | detour_factor | manual | gtfs | great_circle
    distance_method: Mapped[str] = mapped_column(String(24), default="detour_factor")
    distance_confidence: Mapped[float] = mapped_column(Float, default=0.5)
    distance_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- scheduled service (sea / air timetables) -------------------------------
    #: None for on-demand road lanes; else DAILY | WEEKLY | FORTNIGHTLY | MONTHLY | QUARTERLY
    service_frequency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: e.g. ["TUE"] -- "the boat only goes on Tuesdays"
    service_days: Mapped[list | None] = mapped_column(JSON, nullable=True)
    service_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    capacity_per_trip_m3: Mapped[float] = mapped_column(Float, default=0.0)
    cold_capacity_per_trip_m3: Mapped[float] = mapped_column(Float, default=0.0)

    fixed_cost_per_trip: Mapped[float] = mapped_column(Float, default=0.0)
    variable_cost_per_km: Mapped[float] = mapped_column(Float, default=0.0)
    #: Per-m3 handling levied on this lane (port fees, air freight rate per m3).
    cost_per_m3: Mapped[float] = mapped_column(Float, default=0.0)

    # --- seasonality ------------------------------------------------------------
    #: 12 floats in [0, 1]. 0 = impassable that month. Jan first.
    monthly_access: Mapped[list] = mapped_column(JSON, default=lambda: [1.0] * 12)
    monthly_cost_multiplier: Mapped[list] = mapped_column(JSON, default=lambda: [1.0] * 12)

    #: Reliability of the service actually running as timetabled, 0-1.
    reliability: Mapped[float] = mapped_column(Float, default=0.9)
    #: Days of lead-time variability, feeds the stockout risk model.
    lead_time_sd_days: Mapped[float] = mapped_column(Float, default=2.0)

    active: Mapped[bool] = mapped_column(Boolean, default=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)

    country: Mapped[Country] = relationship(back_populates="edges")
    from_node: Mapped[Node] = relationship(foreign_keys=[from_node_id])
    to_node: Mapped[Node] = relationship(foreign_keys=[to_node_id])


class Product(Base):
    __tablename__ = "product"
    __table_args__ = (UniqueConstraint("country_id", "sku", name="uq_product_country_sku"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id", ondelete="CASCADE"), index=True)

    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    #: ambient | +2-8 | -20 | -70
    temperature_band: Mapped[str] = mapped_column(String(16), default="ambient")
    volume_per_unit_cm3: Mapped[float] = mapped_column(Float, default=1.0)
    unit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    shelf_life_days: Mapped[int] = mapped_column(Integer, default=730)

    country: Mapped[Country] = relationship(back_populates="products")


class Demand(Base):
    __tablename__ = "demand"
    __table_args__ = (
        Index("ix_demand_node_product", "node_id", "product_id"),
        UniqueConstraint("node_id", "product_id", "period", name="uq_demand_node_product_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("node.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"))

    #: 1-12 for a monthly profile, 0 for an annual total.
    period: Mapped[int] = mapped_column(Integer, default=0)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    #: actual | forecast | proxy -- proxy means derived from catchment population.
    source: Mapped[str] = mapped_column(String(16), default="proxy")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)


class Scenario(Base):
    __tablename__ = "scenario"
    __table_args__ = (UniqueConstraint("country_id", "name", name="uq_scenario_country_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    parent_scenario_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenario.id", ondelete="SET NULL"), nullable=True
    )
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False)

    #: allowed_modes, hub_nodes_open, delivery_frequency_by_level, third_party_share,
    #: integration_policy, fuel_index, demand_growth, month, service_frequency_overrides
    levers: Mapped[dict] = mapped_column(JSON, default=dict)
    #: max_budget, min_fill_rate, equity_floor, respect_capacity
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    #: cost, service, equity
    objective_weights: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    country: Mapped[Country] = relationship(back_populates="scenarios")
    results: Mapped[list["Result"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan", order_by="Result.id.desc()"
    )


class Result(Base):
    __tablename__ = "result"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenario.id", ondelete="CASCADE"), index=True)

    status: Mapped[str] = mapped_column(String(24), default="pending")
    kpi_set: Mapped[dict] = mapped_column(JSON, default=dict)
    per_node_detail: Mapped[list] = mapped_column(JSON, default=list)
    per_edge_flow: Mapped[list] = mapped_column(JSON, default=list)
    equity_detail: Mapped[dict] = mapped_column(JSON, default=dict)
    solver_log: Mapped[dict] = mapped_column(JSON, default=dict)
    run_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    runtime_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    scenario: Mapped[Scenario] = relationship(back_populates="results")


class AuditEntry(Base):
    """Every assumption that entered the model, and who put it there.

    This is the consultancy defensibility layer. When a reviewer at UNICEF asks
    "where did the 1.35 detour factor come from", the answer is a row in this table,
    not a memory.
    """

    __tablename__ = "audit_entry"

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id", ondelete="CASCADE"), index=True)

    #: node | edge | demand | product | scenario | global
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_ref: Mapped[str] = mapped_column(String(128), default="")
    field: Mapped[str] = mapped_column(String(64), default="")

    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: seed | import | osrm | manual_override | field_interview | assumption
    provenance: Mapped[str] = mapped_column(String(32), default="assumption")
    #: S = sourced, I = inferred, U = unverified -- the marker system from the brief.
    confidence_marker: Mapped[str] = mapped_column(String(2), default="I")
    rationale: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(96), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ImportBatch(Base):
    """One upload of a workbook, with its validation report retained."""

    __tablename__ = "import_batch"

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(24), default="validated")
    committed: Mapped[bool] = mapped_column(Boolean, default=False)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
