from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class CountryOut(BaseModel):
    id: int
    code: str
    name: str
    currency: str
    config: dict
    has_boundary: bool = False

    class Config:
        from_attributes = True


class CountryIn(BaseModel):
    """Everything needed to open a workspace for a country nobody has modelled yet.

    Only the code, the name and the currency are required. The rest has defaults that
    are honest rather than accurate: a world-sized bounding box that rejects nothing,
    the built-in terrain classes, and no land mask at all. Each can be replaced from
    the interface once somebody knows better, and the tool says which are still
    defaults rather than pretending they were chosen.
    """

    code: str = Field(min_length=2, max_length=8)
    name: str = Field(min_length=2, max_length=128)
    currency: str = Field(default="USD", max_length=8)
    config: dict = Field(default_factory=dict)
    boundary: dict = Field(default_factory=dict)


class CountryPatch(BaseModel):
    name: Optional[str] = None
    currency: Optional[str] = None
    config: Optional[dict] = None
    boundary: Optional[dict] = None


class NodeOut(BaseModel):
    id: int
    code: str
    name: str
    level: int
    type: str
    lat: float
    lon: float
    geocode_confidence: float
    geocode_source: str
    admin1: Optional[str]
    admin2: Optional[str]
    capacity: dict
    operating_status: str
    catchment_population: float
    terrain_class: str
    hub_capable: bool
    hub_fixed_cost: float
    hub_open_capex: float
    hub_throughput_m3: float
    external_ids: dict
    retired_at: Optional[datetime] = None
    retired_reason: str = ""

    class Config:
        from_attributes = True


class NodePatch(BaseModel):
    """An edit made in the tool. Only the fields a person can defend are here; distances
    the cascade computed and ids a connector merged are not theirs to type over."""

    name: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    level: Optional[int] = None
    type: Optional[str] = None
    admin1: Optional[str] = None
    admin2: Optional[str] = None
    terrain_class: Optional[str] = None
    catchment_population: Optional[float] = None
    operating_status: Optional[str] = None
    capacity: Optional[dict] = None
    hub_capable: Optional[bool] = None
    hub_fixed_cost: Optional[float] = None
    hub_open_capex: Optional[float] = None
    hub_throughput_m3: Optional[float] = None
    geocode_source: Optional[str] = None
    geocode_confidence: Optional[float] = None
    #: S = sourced, I = inferred, U = unverified -- declared at the moment of typing.
    confidence_marker: Literal["S", "I", "U"] = "I"
    reason: str = ""


class NodeCreate(BaseModel):
    code: str
    name: str
    lat: float
    lon: float
    level: int = 3
    type: str = "health_facility"
    admin1: Optional[str] = None
    admin2: Optional[str] = None
    terrain_class: str = "mainland_road"
    catchment_population: float = 0.0
    operating_status: str = "operational"
    capacity: dict = Field(default_factory=dict)
    geocode_source: str = "manual"
    geocode_confidence: float = 0.7
    confidence_marker: Literal["S", "I", "U"] = "I"
    reason: str = ""


class RetireIn(BaseModel):
    reason: str = ""


class DemandLine(BaseModel):
    sku: str
    quantity: float
    period: int = 0
    source: Literal["actual", "forecast", "proxy"] = "forecast"
    confidence: float = 0.6


class DemandSet(BaseModel):
    lines: List[DemandLine]
    confidence_marker: Literal["S", "I", "U"] = "I"
    reason: str = ""


class DemandOut(BaseModel):
    id: int
    node_id: int
    node_code: str
    sku: str
    product_name: str
    period: int
    quantity: float
    source: str
    confidence: float
    unit: str = "units"


class EdgeOut(BaseModel):
    id: int
    code: str
    from_node_id: int
    to_node_id: int
    from_code: str = ""
    to_code: str = ""
    from_lat: float = 0.0
    from_lon: float = 0.0
    to_lat: float = 0.0
    to_lon: float = 0.0
    mode: str
    distance_km: float
    base_travel_time_hr: float
    distance_method: str
    distance_confidence: float
    distance_note: Optional[str]
    service_name: Optional[str]
    service_frequency: Optional[str]
    service_days: Optional[list]
    capacity_per_trip_m3: float
    cold_capacity_per_trip_m3: float
    fixed_cost_per_trip: float
    variable_cost_per_km: float
    cost_per_m3: float
    monthly_access: list
    monthly_cost_multiplier: list
    reliability: float
    lead_time_sd_days: float
    active: bool


class ProductOut(BaseModel):
    id: int
    sku: str
    name: str
    temperature_band: str
    volume_per_unit_cm3: float
    unit_cost: float
    shelf_life_days: int

    class Config:
        from_attributes = True


class ScenarioIn(BaseModel):
    name: str
    description: str = ""
    parent_scenario_id: Optional[int] = None
    tags: List[str] = Field(default_factory=list)
    levers: dict = Field(default_factory=dict)
    constraints: dict = Field(default_factory=dict)
    objective_weights: dict = Field(default_factory=dict)


class ScenarioPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    #: An empty list clears the tags, so this one is distinguished by exclude_unset
    #: rather than by being None -- see update_scenario.
    tags: Optional[List[str]] = None
    levers: Optional[dict] = None
    constraints: Optional[dict] = None
    objective_weights: Optional[dict] = None


class ScenarioOut(BaseModel):
    id: int
    country_id: int
    name: str
    description: str
    parent_scenario_id: Optional[int]
    is_baseline: bool
    tags: List[str] = Field(default_factory=list)
    levers: dict
    constraints: dict
    objective_weights: dict
    latest_result_id: Optional[int] = None
    latest_status: Optional[str] = None
    updated_at: datetime

    class Config:
        from_attributes = True


class ResultSummary(BaseModel):
    id: int
    scenario_id: int
    status: str
    kpi_set: dict
    runtime_ms: int
    run_timestamp: datetime
    error: Optional[str]

    class Config:
        from_attributes = True


class EdgeOverride(BaseModel):
    """Manual override of a lane, recorded in the audit trail."""

    distance_km: Optional[float] = None
    base_travel_time_hr: Optional[float] = None
    service_frequency: Optional[str] = None
    capacity_per_trip_m3: Optional[float] = None
    monthly_access: Optional[list[float]] = None
    reliability: Optional[float] = None
    cost_per_m3: Optional[float] = None
    active: Optional[bool] = None
    rationale: str = ""
    actor: str = "analyst"


class AuditOut(BaseModel):
    id: int
    entity_type: str
    entity_ref: str
    field: str
    old_value: Optional[str]
    new_value: Optional[str]
    provenance: str
    confidence_marker: str
    rationale: str
    actor: str
    author_claim: str = "anonymous"
    batch_id: Optional[str] = None
    status: str = "applied"
    reverts_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ImportRowPatch(BaseModel):
    """A correction to one row of an import that has not been committed yet.

    The row is found by a business key rather than a position, because a spreadsheet
    row number stops meaning anything the moment somebody sorts the sheet.
    """

    sheet: Literal["nodes", "edges", "products", "demand"]
    key: str
    key_field: str = "code"
    values: dict
    reason: str = ""
