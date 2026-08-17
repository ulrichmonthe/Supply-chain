from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CountryOut(BaseModel):
    id: int
    code: str
    name: str
    currency: str
    config: dict

    class Config:
        from_attributes = True


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
    admin1: str | None
    admin2: str | None
    capacity: dict
    operating_status: str
    catchment_population: float
    terrain_class: str
    hub_capable: bool
    hub_fixed_cost: float
    hub_open_capex: float
    hub_throughput_m3: float
    external_ids: dict

    class Config:
        from_attributes = True


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
    distance_note: str | None
    service_name: str | None
    service_frequency: str | None
    service_days: list | None
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
    parent_scenario_id: int | None = None
    levers: dict = Field(default_factory=dict)
    constraints: dict = Field(default_factory=dict)
    objective_weights: dict = Field(default_factory=dict)


class ScenarioPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    levers: dict | None = None
    constraints: dict | None = None
    objective_weights: dict | None = None


class ScenarioOut(BaseModel):
    id: int
    country_id: int
    name: str
    description: str
    parent_scenario_id: int | None
    is_baseline: bool
    levers: dict
    constraints: dict
    objective_weights: dict
    latest_result_id: int | None = None
    latest_status: str | None = None
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
    error: str | None

    class Config:
        from_attributes = True


class EdgeOverride(BaseModel):
    """Manual override of a lane, recorded in the audit trail."""

    distance_km: float | None = None
    base_travel_time_hr: float | None = None
    service_frequency: str | None = None
    capacity_per_trip_m3: float | None = None
    monthly_access: list[float] | None = None
    reliability: float | None = None
    cost_per_m3: float | None = None
    active: bool | None = None
    rationale: str = ""
    actor: str = "analyst"


class AuditOut(BaseModel):
    id: int
    entity_type: str
    entity_ref: str
    field: str
    old_value: str | None
    new_value: str | None
    provenance: str
    confidence_marker: str
    rationale: str
    actor: str
    created_at: datetime

    class Config:
        from_attributes = True
