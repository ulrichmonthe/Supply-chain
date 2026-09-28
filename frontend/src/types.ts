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
  retired_at: string | null
  retired_reason: string
  derivations: Record<string, Derivation>
}

/** The rule behind an estimated value, kept beside it so the number can show its working. */
export type Derivation = {
  rule: string
  params: Record<string, unknown>
  inputs: Record<string, unknown>
  formula: string
  at: string
}

export type ProductRow = {
  id: number
  sku: string
  name: string
  temperature_band: string
  volume_per_unit_cm3: number
  unit_cost: number
  shelf_life_days: number
}

export type EstimatorInfo = {
  rules: { key: string; target: string; label: string; detail: string; live: boolean }[]
  products: {
    sku: string
    name: string
    rows: number
    estimated: number
    blank: number
    population_rate: { available: boolean; per_1000: number | null; basis: string }
  }[]
  capacity: { blank: number; estimated: number; cover_days: number }
  demand: { rows: number; estimated: number; share: number }
}

export type EstimateRule = 'population_rate' | 'peer_median' | 'capacity_cover'

export type EstimatePreview = {
  rule: EstimateRule
  count: number
  proposals: { node_id: number; node_code: string; node_name: string; sku: string | null; field: string; current: unknown; proposed: unknown; formula: string; confidence: number }[]
  skipped: string[]
  skipped_count: number
}

export type DemandRow = {
  id: number
  node_id: number
  node_code: string
  sku: string
  product_name: string
  period: number
  quantity: number
  source: string
  confidence: number
  unit: string
  derivation: Derivation | null
}

export type ConfidenceMarker = 'S' | 'I' | 'U'

export type ChangeRow = {
  key: string
  label: string
  kind: string
  fields: Record<string, { from: unknown; to: unknown }>
  conflicts: Record<string, { model: unknown; file: unknown; last_import: unknown }>
  method: string
}

export type SheetChanges = {
  counts: { add: number; update: number; retire: number; restore: number; conflict: number; unchanged: number }
  adds: ChangeRow[]
  updates: ChangeRow[]
  retires: ChangeRow[]
  restores: ChangeRow[]
  conflicts: ChangeRow[]
}

export type ImportChanges = {
  batch_id: number
  mode: string
  headline: string
  conflicts: number
  collisions: unknown[]
  nodes: SheetChanges
  edges: SheetChanges
  products: SheetChanges
  demand: SheetChanges
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
  estimated?: {
    demand_rows: number
    demand_rows_estimated: number
    demand_share: number
    demand_m3_estimated_share: number
    facilities_with_estimated_storage: number
  }
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
  tags: string[]
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

/** What the estimates mean for a result: how much rests on them, and whether the
 *  answer survives every estimated figure a swing lower and higher. */
export type ConfidenceAssessment = {
  share: number
  share_rows: number
  facilities_with_estimated_demand: number
  facilities: number
  facilities_with_estimated_storage: number
  tested: boolean
  swing: number | null
  holds: boolean | null
  changes: string[]
  notes: string[]
  /** [low, as modelled, high]; an end that could not be solved is null. */
  range: Record<string, [number | null, number | null, number | null]>
  baseline_tested: boolean | null
  sentence: string
}

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
  confidence?: ConfidenceAssessment
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
  author_claim: string
  batch_id: string | null
  status: string
  reverts_id: number | null
  created_at: string
}

/* ---------------------------------------------------------------- connectors */

export type ConnectorSpec = {
  system: string
  label: string
  description: string
  docs_url: string
  auth_types: string[]
  verified_against_live_instance: boolean
  config_spec: Record<string, { default: unknown; help: string; kind: string }>
}

export type Connection = {
  id: number
  country_id: number
  name: string
  system: string
  base_url: string
  auth_type: string
  username: string
  secret_env: string
  secret_set: boolean
  secret_source: 'environment' | 'stored' | 'none'
  verify_tls: boolean
  timeout_s: number
  enabled: boolean
  config: Record<string, unknown>
  last_tested_at: string | null
  last_test_ok: boolean | null
  last_test_detail: ConnectionTest | Record<string, never>
  last_sync_at: string | null
  last_sync_summary: Record<string, number>
}

export type ConnectionTest = {
  ok: boolean
  system: string
  version: string
  detail: string
  checks: { name: string; ok: boolean; detail: string }[]
}

export type ReconciliationMatch = {
  incoming_code: string
  incoming_name: string
  existing_code: string | null
  existing_name: string | null
  method: string
  changes: Record<string, unknown[]>
  distance_moved_km: number | null
}

export type Reconciliation = {
  summary: {
    matched: number
    new: number
    absent: number
    collisions: number
    moved: number
    renamed: number
    gained_coordinates: number
    by_method: Record<string, number>
  }
  matched: ReconciliationMatch[]
  new: ReconciliationMatch[]
  absent: { id: number; code: string; name: string; admin1: string | null }[]
  collisions: { existing_code: string; existing_name: string; claimed_by: string[]; detail: string }[]
  truncated: { matched: number; new: number; absent: number }
  headline: string
}

export type SyncPreview = ValidationReport & {
  connection: Connection
  reconciliation: Reconciliation
  connector_warnings: string[]
  stats: Record<string, unknown>
  commit_mode: string
  commit_note: string
}

/** A named, complete save of the working state. */
export type WorkSession = {
  id: number
  country_id: number
  name: string
  note: string
  kind: 'saved' | 'draft'
  parent_id: number | null
  author_claim: string
  created_at: string
  ledger_position: number
  summary: {
    facilities?: number
    stores?: number
    lanes?: number
    products?: number
    demand_rows?: number
    scenarios?: number
    results?: number
    scenario_kpis?: { name: string; is_baseline: boolean; status: string | null; kpi_set: Record<string, number> }[]
  }
  size_bytes: number
  is_current: boolean
  changes_since: number | null
}

export type SessionShelf = {
  current: {
    session_id: number
    name: string
    kind: 'saved' | 'draft'
    author_claim: string
    created_at: string
    changes_since: number
  } | null
  sessions: WorkSession[]
}

export type SessionDiff = {
  from: string
  to: string
  same: boolean
  sentences: string[]
  detail: Record<string, unknown>
}

/** A study: a question, its ordered scenarios (baseline first), and the one the analyst backs. */
export type Study = {
  id: number
  country_id: number
  question: string
  note: string
  scenario_ids: number[]
  scenarios: { id: number; name: string; is_baseline: boolean; has_result: boolean }[]
  recommended_scenario_id: number | null
  author_claim: string
  created_at: string
  updated_at: string
}

export type StudyPreset = { key: string; label: string; description: string }

export type LeverDifference = {
  group: string
  key: string
  label: string
  baseline: unknown
  value: unknown
  text: string
}

export type MovedFacility = {
  code: string
  name: string
  admin1: string | null
  population: number
  fill_before: number
  fill_after: number
  served_delta_m3: number
  from_hub?: string
  to_hub?: string
}

export type StudyCompare = {
  study: Study
  baseline_scenario_id: number | null
  rows: (ScorecardRow & {
    tags: string[]
    hubs_open_codes: string[]
    confidence: ConfidenceAssessment | null
    recommended: boolean
  })[]
  lever_diff: Record<string, { differences: LeverDifference[]; sentence: string }>
  equity: {
    scenario_id: number
    name: string
    strata: { label: string; fill_rate: number; cost_per_capita: number; population: number }[]
  }[]
  facilities: Record<
    string,
    {
      gained: MovedFacility[]
      lost: MovedFacility[]
      gained_count: number
      lost_count: number
      resupplied: MovedFacility[]
      resupplied_count: number
      people_lost: number
    }
  >
  verdict: string
  kpi_meta: KpiMeta
}

export type DiffMap = {
  a: { result_id: number; scenario_id: number; scenario_name: string; hubs_open: string[] }
  b: { result_id: number; scenario_id: number; scenario_name: string; hubs_open: string[] }
  lanes: {
    edge_id: number
    edge_code: string
    hub_code: string
    facility_code: string
    mode: string
    status: 'a' | 'b' | 'both'
    volume_a: number
    volume_b: number
  }[]
  facilities: {
    code: string
    name: string
    admin1: string | null
    population: number
    hub_a: string | null
    hub_b: string | null
    hub_a_name: string | null
    hub_b_name: string | null
    fill_a: number
    fill_b: number
    change: 'supplier' | 'gained' | 'lost'
  }[]
  summary: {
    lanes_only_a: number
    lanes_only_b: number
    lanes_shared: number
    facilities_changed_supplier: number
    facilities_gained: number
    facilities_lost: number
    hubs_only_a: string[]
    hubs_only_b: string[]
  }
}
