export type Country = {
  id: number
  code: string
  name: string
  currency: string
  config: {
    bbox?: { min_lat: number; max_lat: number; min_lon: number; max_lon: number }
    center?: { lat: number; lon: number; zoom: number }
    level_labels?: Record<string, string>
    equity_definition?: string
    data_provenance?: {
      status: string
      real: string
      illustrative: string
      before_use: string
    }
  }
}

export type NodeRow = {
  id: number
  code: string
  name: string
  level: number
  type: string
  lat: number
  lon: number
  admin1: string | null
  admin2: string | null
  terrain_class: string
  catchment_population: number
  operating_status: string
  hub_capable: boolean
  hub_fixed_cost: number
  hub_open_capex: number
  hub_throughput_m3: number
  capacity: { dry_m3?: number; cold_by_band?: Record<string, number>; cover_days_assumed?: number }
  geocode_confidence: number
  geocode_source: string
}

export type EdgeRow = {
  id: number
  code: string
  from_code: string
  to_code: string
  from_lat: number
  from_lon: number
  to_lat: number
  to_lon: number
  mode: string
  distance_km: number
  base_travel_time_hr: number
  distance_method: string
  distance_confidence: number
  distance_note: string | null
  service_name: string | null
  service_frequency: string | null
  service_days: string[] | null
  capacity_per_trip_m3: number
  cost_per_m3: number
  monthly_access: number[]
  reliability: number
  active: boolean
}

export type ServiceSummary = {
  name: string
  mode: string
  frequency: string
  service_days: string[] | null
  reliability: number
  facilities: number
  capacity_per_trip_m3: number
  population: number
  origin: string
  interval_days: number
}

export type Overview = {
  country: Country
  counts: Record<string, number>
  totals: { population: number; annual_demand_m3: number; provinces: string[] }
  distance_provenance: {
    counts: Record<string, number>
    total_edges: number
    km_weighted_confidence: number
    osrm_configured: boolean
  }
  services: ServiceSummary[]
  vulnerability: Record<string, { vulnerability: number; restricted_months: number; population: number }>
  months: string[]
}

export type Levers = {
  month?: number | null
  allowed_modes?: string[]
  hub_nodes_open?: string[]
  hub_nodes_closed?: string[]
  optimize_hubs?: boolean
  service_frequency_overrides?: Record<string, string>
  delivery_frequency_by_level?: Record<string, string>
  third_party_share?: number
  integration_policy?: string
  fuel_index?: number
  demand_growth?: number
  safety_stock_days?: number
}

export type Constraints = {
  min_fill_rate?: number
  equity_floor?: number
  max_budget?: number
  respect_capacity?: boolean
}

export type Weights = { cost?: number; service?: number; equity?: number }

export type Scenario = {
  id: number
  country_id: number
  name: string
  description: string
  is_baseline: boolean
  parent_scenario_id: number | null
  levers: Levers
  constraints: Constraints
  objective_weights: Weights
  latest_result_id: number | null
  latest_status: string | null
}

export type KpiSet = Record<string, number>

export type Stratum = {
  index: number
  label: string
  facilities: number
  population: number
  demand: number
  served: number
  cost: number
  fill_rate: number
  cost_per_capita: number
  mean_vulnerability: number
}

export type NodeDetail = {
  node_id: number
  code: string
  name: string
  admin1: string | null
  level: number
  lat: number
  lon: number
  terrain_class: string
  population: number
  demand_m3: number
  served_m3: number
  unmet_m3: number
  fill_rate: number
  cost: number
  cost_per_capita: number | null
  vulnerability: number
  stratum: number
  restricted_months: number
  served_by: { hub_code: string; hub_name: string; edge_code: string; mode: string; volume_m3: number; share: number }[]
  primary_mode: string | null
  service_name: string | null
  service_frequency: string | null
  stockout_risk: number
  storage_days: number
  storage_binding: boolean
  reachable: boolean
}

export type EdgeFlow = {
  edge_id: number
  edge_code: string
  hub_code: string
  facility_code: string
  mode: string
  volume_m3: number
  unit_cost: number
  cost: number
  from_lat: number
  from_lon: number
  to_lat: number
  to_lon: number
  distance_km: number
  distance_method: string
  distance_confidence: number
  service_name: string | null
  frequency: string | null
  capacity_utilisation: number | null
  cost_breakdown: Record<string, number | string>
  inbound_cost_per_m3: number
}

export type Result = {
  id: number
  scenario_id: number
  scenario_name: string
  status: string
  kpi_set: KpiSet
  per_node_detail: NodeDetail[]
  per_edge_flow: EdgeFlow[]
  equity_detail: { strata: Stratum[]; worst_stratum_fill_rate: number; equity_gap: number }
  solver_log: Record<string, unknown>
  runtime_ms: number
  error: string | null
}

export type KpiMeta = Record<string, { label: string; unit: string; better: string }>

export type ScorecardRow = {
  scenario_id: number
  result_id?: number
  name: string
  description?: string
  is_baseline: boolean
  status: string
  error?: string | null
  kpi_set: KpiSet
  equity?: Stratum[]
  month_label?: string
  runtime_ms?: number
  comparison: Record<string, { baseline: number; value: number; delta: number; delta_pct: number | null; direction: string }>
}

export type Scorecard = {
  baseline_scenario_id: number
  baseline_result_id: number | null
  kpi_meta: KpiMeta
  kpi_order: string[]
  rows: ScorecardRow[]
}

export type SeasonView = {
  month: number
  month_name: string
  closed_lanes: { edge_code: string; to_name: string; mode: string; service_name: string | null; access: number }[]
  degraded_lanes: { edge_code: string; to_name: string; mode: string; access: number }[]
  facilities_cut_off: { node_id: number; code: string; name: string; admin1: string | null; population: number }[]
  facilities_losing_surface_access: {
    node_id: number
    code: string
    name: string
    admin1: string | null
    population: number
  }[]
  summary: {
    lanes_closed: number
    lanes_degraded: number
    facilities_cut_off: number
    facilities_losing_surface_access: number
    population_losing_surface_access: number
  }
}

export type ValidationIssue = {
  severity: 'error' | 'warning' | 'info'
  code: string
  message: string
  suggestion: string
  sheet: string
  row: number | null
  entity: string
}

export type ValidationReport = {
  blocking: boolean
  counts: { error: number; warning: number; info: number }
  issues: ValidationIssue[]
  headline: string
  batch_id: number
  missing_sheets: string[]
  stats: Record<string, number>
}

export type RoadmapStep = {
  id: string
  phase: number
  phase_label: string
  phase_window: string
  category: string
  title: string
  detail: string
  one_off_cost: number | null
  annual_cost_delta: number | null
  owner: string
  risk: string
  evidence: string
}

export type Roadmap = {
  scenario: string
  baseline: string
  currency: string
  steps: RoadmapStep[]
  summary: {
    one_off_cost_total: number
    annual_cost_delta: number
    annual_saving: number
    annual_saving_pct: number | null
    payback_years: number | null
    worst_quintile_fill_baseline: number
    worst_quintile_fill_scenario: number
    population_coverage_baseline: number
    population_coverage_scenario: number
  }
  phases: { phase: number; label: string; window: string; steps: string[]; one_off_cost: number }[]
}

export type AuditRow = {
  id: number
  entity_type: string
  entity_ref: string
  field: string
  old_value: string | null
  new_value: string | null
  provenance: string
  confidence_marker: string
  rationale: string
  actor: string
  created_at: string
}
