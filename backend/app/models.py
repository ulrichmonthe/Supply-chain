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
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    LargeBinary,
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
    # temperature_bands, equity_definition, bbox, fx_rate, detour_factors, mode_speeds,
    # seasonal_profiles.
    config: Mapped[dict] = mapped_column(JSON, default=dict)

    #: Coarse land mask: {"polygons": [ring of (lon, lat), ...], "buffers": [(lat, lon, km)]}.
    #: Screens coordinates for being in the sea and doubles as the offline basemap, so the
    #: validator and the map always agree about where the land is. Empty is allowed and
    #: simply switches the offshore check off for that country.
    boundary: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    #: The session the working state was last saved as or opened from, and the ledger
    #: position at that moment, so "3 changes since" is a count rather than a guess.
    current_session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_session_position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

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

    admin1: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    admin2: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

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
    #: Fields whose value is an estimate that stays live: {"capacity": {"rule": ..., "params": ...}}.
    #: Typing over the field pins it -- the entry is removed and the value is a person's.
    derivations: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")

    # --- soft delete and the memory of the last import ---------------------------------
    #: Set instead of deleting. A retired row is invisible to every query unless it asks
    #: (see app.db: the retired filter), keeps its history, and can be restored.
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    retired_reason: Mapped[str] = mapped_column(String(255), default="", server_default="")
    #: The tracked fields exactly as the last import or sync set them. A current value
    #: that differs from this was changed by hand since -- which is what makes the next
    #: import's differing value a conflict to decide rather than an update to apply.
    last_import: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")

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
    distance_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- scheduled service (sea / air timetables) -------------------------------
    #: None for on-demand road lanes; else DAILY | WEEKLY | FORTNIGHTLY | MONTHLY | QUARTERLY
    service_frequency: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    #: e.g. ["TUE"] -- "the boat only goes on Tuesdays"
    service_days: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    service_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
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

    # --- soft delete and the memory of the last import ---------------------------------
    #: Set instead of deleting. A retired row is invisible to every query unless it asks
    #: (see app.db: the retired filter), keeps its history, and can be restored.
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    retired_reason: Mapped[str] = mapped_column(String(255), default="", server_default="")
    #: The tracked fields exactly as the last import or sync set them. A current value
    #: that differs from this was changed by hand since -- which is what makes the next
    #: import's differing value a conflict to decide rather than an update to apply.
    last_import: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")

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

    # --- soft delete and the memory of the last import ---------------------------------
    #: Set instead of deleting. A retired row is invisible to every query unless it asks
    #: (see app.db: the retired filter), keeps its history, and can be restored.
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    retired_reason: Mapped[str] = mapped_column(String(255), default="", server_default="")
    #: The tracked fields exactly as the last import or sync set them. A current value
    #: that differs from this was changed by hand since -- which is what makes the next
    #: import's differing value a conflict to decide rather than an update to apply.
    last_import: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")

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
    #: When the quantity is an estimate: {"rule", "params", "inputs", "formula", "at"}.
    #: Live -- recomputed when its inputs change -- until somebody types over it.
    derivation: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    #: How this figure entered the model, in the onboarding vocabulary:
    #: observed | converted | confirmed | estimated | illustrative | missing.
    #: Seeded demo figures are illustrative; the coverage printed beside every result
    #: is the count of demand by this column, so "0 illustrative" is a measurable claim.
    provenance_class: Mapped[str] = mapped_column(String(16), default="illustrative", server_default="illustrative")

    # --- soft delete and the memory of the last import ---------------------------------
    #: Set instead of deleting. A retired row is invisible to every query unless it asks
    #: (see app.db: the retired filter), keeps its history, and can be restored.
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    retired_reason: Mapped[str] = mapped_column(String(255), default="", server_default="")
    #: The tracked fields exactly as the last import or sync set them. A current value
    #: that differs from this was changed by hand since -- which is what makes the next
    #: import's differing value a conflict to decide rather than an update to apply.
    last_import: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")

class Scenario(Base):
    __tablename__ = "scenario"
    __table_args__ = (UniqueConstraint("country_id", "name", name="uq_scenario_country_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    parent_scenario_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("scenario.id", ondelete="SET NULL"), nullable=True
    )
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False)

    #: Free-text labels, cleaned by app.tagging before they get here. Deliberately a
    #: list on the scenario rather than a join table: tags are a filing convenience for
    #: one country's workspace, not an entity anything else refers to.
    tags: Mapped[list] = mapped_column(JSON, default=list)

    #: allowed_modes, hub_nodes_open, delivery_frequency_by_level, third_party_share,
    #: integration_policy, fuel_index, demand_growth, month, service_frequency_overrides
    levers: Mapped[dict] = mapped_column(JSON, default=dict)
    #: max_budget, min_fill_rate, equity_floor, respect_capacity
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    #: cost, service, equity
    objective_weights: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Data changes applied on top of the baseline data when this scenario runs, without
    #: touching the base: close a facility, scale demand, add a store, add or remove a lane.
    data_items: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")

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
    # The confidence budget: how much of the demand this was solved on is an estimate,
    # and what the answer becomes with every estimated figure a swing lower and higher.
    confidence: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    run_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    runtime_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    scenario: Mapped[Scenario] = relationship(back_populates="results")


class DatasetSnapshot(Base):
    """The whole working state of a country at one moment, gzipped and content-addressed.

    Two sessions saved from the same state share one snapshot: the hash is the identity.
    """

    __tablename__ = "dataset_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id", ondelete="CASCADE"), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WorkSession(Base):
    """A named, complete save: the dataset as it stood, the scenarios and their latest
    results, who saved it and why. Open one and the working state becomes exactly that;
    the saved session itself is never modified again.

    A ``draft`` is the same thing without a name: made automatically before another
    session is opened, so nothing typed since the last save can be lost.
    """

    __tablename__ = "work_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    note: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(12), default="saved")  # saved | draft
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("work_session.id", ondelete="SET NULL"), nullable=True
    )
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("dataset_snapshot.id", ondelete="CASCADE"))
    #: The last ledger entry id when this was saved; the diff between two sessions of
    #: one lineage is the ledger between their positions.
    ledger_position: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    author_claim: Mapped[str] = mapped_column(String(96), default="anonymous")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    snapshot: Mapped[DatasetSnapshot] = relationship()


class Study(Base):
    """A question, the ordered scenarios that answer it, and the one the analyst backs.

    Comparison as a workflow rather than a tab: "Can we close Wewak?" owns its
    scenarios, its compare view, its diff map and the decision page of its report.
    """

    __tablename__ = "study"

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(String(240))
    note: Mapped[str] = mapped_column(Text, default="")
    #: Ordered; the baseline is always first.
    scenario_ids: Mapped[list] = mapped_column(JSON, default=list)
    recommended_scenario_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    author_claim: Mapped[str] = mapped_column(String(96), default="anonymous")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


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

    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: seed | import | osrm | manual_override | field_interview | assumption
    provenance: Mapped[str] = mapped_column(String(32), default="assumption")
    #: S = sourced, I = inferred, U = unverified -- the marker system from the brief.
    confidence_marker: Mapped[str] = mapped_column(String(2), default="I")
    rationale: Mapped[str] = mapped_column(Text, default="")
    #: What produced the change: "seed", "analyst", a connector name. Kept for the
    #: system-side story; the person is author_claim.
    actor: Mapped[str] = mapped_column(String(96), default="system")

    # --- the ledger: who, in which sitting, and whether it still stands ---------------
    #: The name a person gave for the record ("Ulrich, JSI"). Typed once per browser and
    #: sent with every write; "anonymous" when nobody has. When accounts exist, each
    #: claim maps to a user by migration -- which is why it is a column, not a guess.
    author_claim: Mapped[str] = mapped_column(String(96), default="anonymous", server_default="anonymous")
    #: One id per request or import, so "what did that upload change" is one query.
    batch_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    #: applied | reverted | superseded. Undo is a row that reverses this one, never a
    #: delete; superseded means an import was told to override this correction.
    status: Mapped[str] = mapped_column(String(12), default="applied", server_default="applied")
    #: The entry this one reverses, when status of that one became "reverted".
    reverts_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("audit_entry.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ImportBatch(Base):
    """One proposed change to the data, with its validation report retained.

    Covers both a workbook upload and a connector sync, deliberately. Live data earns
    no separate path: it is validated, previewed and committed through exactly the
    same machinery, so the record of what was applied is the same record either way.
    """

    __tablename__ = "import_batch"

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    #: excel | dhis2 | msupply | openlmis
    source: Mapped[str] = mapped_column(String(32), default="excel")
    connection_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("connection.id", ondelete="SET NULL"), nullable=True
    )
    #: replace wipes the country first; merge upserts and leaves lanes alone.
    mode: Mapped[str] = mapped_column(String(16), default="replace")
    status: Mapped[str] = mapped_column(String(24), default="validated")
    committed: Mapped[bool] = mapped_column(Boolean, default=False)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Connection(Base):
    """A configured link to a logistics information system.

    **On the secret.** ``secret_env`` names an environment variable and is the way to
    run this in anything resembling production: the credential lives in the process
    environment, never in the database and never in a backup of it. ``secret`` stores
    the value directly, which is honest about being a convenience for a laptop during
    a workshop and is labelled as such in the UI. Neither is ever returned by the API.
    A proper secret manager is the right answer for a hosted deployment and is a
    deliberate gap, not an oversight.
    """

    __tablename__ = "connection"
    __table_args__ = (UniqueConstraint("country_id", "name", name="uq_connection_country_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(128))
    #: dhis2 | msupply | openlmis
    system: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(512), default="")

    #: basic | token | bearer
    auth_type: Mapped[str] = mapped_column(String(16), default="basic")
    username: Mapped[str] = mapped_column(String(128), default="")
    secret: Mapped[str] = mapped_column(String(512), default="")
    secret_env: Mapped[str] = mapped_column(String(128), default="")

    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_s: Mapped[float] = mapped_column(Float, default=30.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    #: Endpoints, field selectors, GraphQL documents and product mappings. Everything
    #: that differs between two installations of the same system lives here.
    config: Mapped[dict] = mapped_column(JSON, default=dict)

    last_tested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    last_test_detail: Mapped[dict] = mapped_column(JSON, default=dict)

    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_summary: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def resolved_secret(self) -> str:
        """The credential to use, preferring the environment over the database."""
        import os

        if self.secret_env:
            return os.environ.get(self.secret_env, "")
        return self.secret or ""

    def has_secret(self) -> bool:
        return bool(self.resolved_secret())


# --- data onboarding ---------------------------------------------------------------------
# The pipeline that turns a ministry's files into a signed-off, provenance-tagged dataset.
# Nothing here writes to the model tables above: loading is a separate human action after
# sign-off, through the same import pipeline as a workbook.


class CountryPack(Base):
    """Configuration completed before any data is ingested: source systems, who arbitrates
    facility identity, admin levels, transport ranges, units, currency, legal limits on
    data movement, roles, languages and thresholds. Versioned; changed only with approval.
    """

    __tablename__ = "country_pack"
    __table_args__ = (UniqueConstraint("country_id", "version", name="uq_country_pack_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    #: draft | approved | superseded
    status: Mapped[str] = mapped_column(String(12), default="draft")
    pack: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    author_claim: Mapped[str] = mapped_column(String(96), default="anonymous")
    approved_by: Mapped[str] = mapped_column(String(96), default="")
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class OnboardingRun(Base):
    """One pass from files to a signed-off dataset. ``refresh`` runs replay the saved
    mappings, the crosswalk and the rules, and send only what changed to review."""

    __tablename__ = "onboarding_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id", ondelete="CASCADE"), index=True)
    pack_id: Mapped[Optional[int]] = mapped_column(ForeignKey("country_pack.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(160))
    #: initial | refresh
    kind: Mapped[str] = mapped_column(String(12), default="initial")
    #: open | in_review | signed_off | loaded | rejected
    status: Mapped[str] = mapped_column(String(16), default="open")
    preparer: Mapped[str] = mapped_column(String(96), default="anonymous")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class SourceFile(Base):
    """A file somebody handed over, with its checksum, who uploaded it, when, which system
    it came from, and the period it describes -- which is not the upload date."""

    __tablename__ = "source_file"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("onboarding_run.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    uploader: Mapped[str] = mapped_column(String(96), default="anonymous")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    #: dhis2 | msupply | openlmis | excel | csv | unknown -- guessed by the profiler, then confirmed.
    source_system: Mapped[str] = mapped_column(String(32), default="unknown")
    #: Nodes | Edges | Products | Demand -- the canonical domain the file feeds.
    domain: Mapped[str] = mapped_column(String(16), default="")
    #: The period the data describes, e.g. "2025-01" to "2025-12".
    vintage_from: Mapped[str] = mapped_column(String(16), default="")
    vintage_to: Mapped[str] = mapped_column(String(16), default="")
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Our field -> their column, once confirmed. Also carries aux columns kept beside the record.
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    #: uploaded | profiled | mapped | staged
    status: Mapped[str] = mapped_column(String(16), default="uploaded")
    payload: Mapped[bytes] = mapped_column(LargeBinary, default=b"")


class StagedRecord(Base):
    """One entity in the canonical staging model, every field carrying its provenance.

    ``fields`` is ``{name: {"value", "unit", "class", "source_file_id", "location",
    "system", "vintage", "transformation", "confidence", "reason", "method", "inputs",
    "alternatives"}}``. A field with alternatives from two sources is a conflict until a
    person chooses; nothing is averaged and nothing is silently picked.
    """

    __tablename__ = "staged_record"
    __table_args__ = (UniqueConstraint("run_id", "domain", "key", name="uq_staged_run_domain_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("onboarding_run.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(160))
    label: Mapped[str] = mapped_column(String(255), default="")
    fields: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Columns the source carried that the model has no field for; kept for triangulation.
    aux: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Flags raised by the rules, each with a code, severity, message and resolution.
    issues: Mapped[list] = mapped_column(JSON, default=list)
    #: staged | conflict | blocked
    status: Mapped[str] = mapped_column(String(16), default="staged")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class FacilityMatch(Base):
    """A source facility against the candidates it might be, with a confidence and the
    reasons. Auto-accepted only above the pack's threshold; everything else is a person's."""

    __tablename__ = "facility_match"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("onboarding_run.id", ondelete="CASCADE"), index=True)
    source_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("source_file.id", ondelete="SET NULL"), nullable=True)
    source_key: Mapped[str] = mapped_column(String(160))
    source_name: Mapped[str] = mapped_column(String(255), default="")
    source_record: Mapped[dict] = mapped_column(JSON, default=dict)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    #: The chosen canonical code, once decided.
    canonical_code: Mapped[str] = mapped_column(String(64), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    #: auto_accepted | accepted | rejected | new | pending
    decision: Mapped[str] = mapped_column(String(16), default="pending")
    decided_by: Mapped[str] = mapped_column(String(96), default="")
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Crosswalk(Base):
    """The reconciled facility register the ministry owns: one row per facility, every
    identifier it carries in every system, and how it changed over time."""

    __tablename__ = "crosswalk"
    __table_args__ = (UniqueConstraint("country_id", "canonical_code", name="uq_crosswalk_country_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id", ondelete="CASCADE"), index=True)
    canonical_code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255), default="")
    type: Mapped[str] = mapped_column(String(64), default="")
    admin1: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    admin2: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    #: {"model": "PNG-NCD-001", "dhis2": "abc123", "hmis_2024.csv": "0412", ...}
    ids: Mapped[dict] = mapped_column(JSON, default=dict)
    #: open | closed | merged | unknown
    status: Mapped[str] = mapped_column(String(16), default="open")
    #: [{"at", "change", "detail", "by"}] -- opened, closed, renamed, merged, recategorised.
    history: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class ReviewItem(Base):
    """One decision for a person, triaged by how much it moves the result."""

    __tablename__ = "review_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("onboarding_run.id", ondelete="CASCADE"), index=True)
    #: mapping | match | conflict | anomaly | estimate | missing | unit | question
    kind: Mapped[str] = mapped_column(String(16))
    #: What the item is about: "Nodes:PNG-NCD-001", "file:3", "Demand:PNG-X|KIT|0/quantity"
    subject: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(Text, default="")
    #: high | medium | low
    confidence: Mapped[str] = mapped_column(String(8), default="medium")
    #: Larger moves the result more; the queue is sorted by it.
    impact: Mapped[float] = mapped_column(Float, default=0.0)
    #: rules | agent
    proposed_by: Mapped[str] = mapped_column(String(16), default="rules")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    options: Mapped[list] = mapped_column(JSON, default=list)
    #: pending | accepted | rejected | chosen | answered
    decision: Mapped[str] = mapped_column(String(16), default="pending")
    choice: Mapped[str] = mapped_column(String(200), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str] = mapped_column(String(96), default="")
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Approval(Base):
    """A named approver's decision on a run, with the report they saw."""

    __tablename__ = "approval"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("onboarding_run.id", ondelete="CASCADE"), index=True)
    approver: Mapped[str] = mapped_column(String(96))
    #: approved | rejected
    decision: Mapped[str] = mapped_column(String(12))
    comment: Mapped[str] = mapped_column(Text, default="")
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
