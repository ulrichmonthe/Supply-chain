import type {
  AuditRow,
  ConfidenceMarker,
  Connection,
  ConnectionTest,
  ConnectorSpec,
  DemandRow,
  EdgeRow,
  ImportChanges,
  NodeRow,
  Overview,
  Result,
  Roadmap,
  Scenario,
  Scorecard,
  SeasonView,
  SyncPreview,
  ValidationIssue,
  ValidationReport,
} from './types'

const BASE = '/api'

/*
 * Who is making this change, as they chose to be recorded.
 *
 * Not authentication -- there are no accounts yet -- and not pretending to be. It is
 * the "signed" line on a hand-filled form: typed once per browser, sent with every
 * write, and shown beside the change in the ledger. Reads carry it too; it is harmless
 * there and one code path is simpler than two.
 */
const AUTHOR_KEY = 'hscn.author'

export function getAuthor(): string {
  try {
    return window.localStorage.getItem(AUTHOR_KEY) ?? ''
  } catch {
    return ''
  }
}

export function setAuthor(name: string): void {
  try {
    if (name.trim()) window.localStorage.setItem(AUTHOR_KEY, name.trim())
    else window.localStorage.removeItem(AUTHOR_KEY)
  } catch {
    /* private browsing: the name lasts for this page only */
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {}
  if (!(init?.body instanceof FormData)) headers['Content-Type'] = 'application/json'
  const author = getAuthor()
  if (author) headers['X-Author'] = author
  const response = await fetch(`${BASE}${path}`, { ...init, headers: { ...headers, ...(init?.headers as Record<string, string>) } })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* the body was not JSON; the status line is the best we have */
    }
    throw new Error(detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  countries: () => request<{ id: number; code: string; name: string }[]>('/countries'),
  overview: (countryId: number) => request<Overview>(`/countries/${countryId}/overview`),
  nodes: (countryId: number) => request<NodeRow[]>(`/countries/${countryId}/nodes`),
  edges: (countryId: number) => request<EdgeRow[]>(`/countries/${countryId}/edges`),
  basemap: (countryId: number) => request<GeoJSON.FeatureCollection>(`/countries/${countryId}/basemap.geojson`),
  season: (countryId: number, month: number) => request<SeasonView>(`/countries/${countryId}/season/${month}`),
  audit: (countryId: number, params: { entity_type?: string; entity_ref?: string; author?: string; limit?: number } = {}) => {
    const query = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== '') query.set(key, String(value))
    const suffix = query.toString()
    return request<AuditRow[]>(`/countries/${countryId}/audit${suffix ? `?${suffix}` : ''}`)
  },

  scenarios: (countryId: number) => request<Scenario[]>(`/countries/${countryId}/scenarios`),
  updateScenario: (id: number, patch: Partial<Scenario>) =>
    request<Scenario>(`/scenarios/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  cloneScenario: (id: number, payload: { name: string; description?: string }) =>
    request<Scenario>(`/scenarios/${id}/clone`, { method: 'POST', body: JSON.stringify(payload) }),
  deleteScenario: (id: number) => request<void>(`/scenarios/${id}`, { method: 'DELETE' }),

  run: (id: number) => request<{ id: number; status: string }>(`/scenarios/${id}/run`, { method: 'POST' }),
  runSet: (countryId: number, ids: number[]) =>
    request<{ ran: number }>(`/countries/${countryId}/run-set`, {
      method: 'POST',
      body: JSON.stringify(ids),
    }),

  result: (id: number) => request<Result>(`/results/${id}`),
  scorecard: (countryId: number) => request<Scorecard>(`/countries/${countryId}/scorecard`),
  roadmap: (scenarioId: number) => request<Roadmap>(`/scenarios/${scenarioId}/roadmap`),

  validateUpload: (countryId: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<ValidationReport>(`/countries/${countryId}/validate`, { method: 'POST', body: form })
  },
  commitImport: (batchId: number, replace: boolean, conflicts: 'keep' | 'take_file' = 'keep', takeFile: string[] = []) => {
    const query = new URLSearchParams({ replace: String(replace), conflicts })
    for (const key of takeFile) query.append('take_file', key)
    return request<{ committed: boolean; mode: string; counts: Record<string, number | Record<string, number>> }>(
      `/imports/${batchId}/commit?${query.toString()}`,
      { method: 'POST' },
    )
  },
  importChanges: (batchId: number, replace: boolean) => request<ImportChanges>(`/imports/${batchId}/changes?replace=${replace}`),
  /* --- editing in the tool --- */
  retiredNodes: (countryId: number) => request<NodeRow[]>(`/countries/${countryId}/nodes/retired`),
  patchNode: (
    nodeId: number,
    patch: Partial<NodeRow> & { confidence_marker?: ConfidenceMarker; reason?: string },
  ) => request<{ node: NodeRow; issues: ValidationIssue[] }>(`/nodes/${nodeId}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  createNode: (
    countryId: number,
    payload: {
      code: string
      name: string
      lat: number
      lon: number
      level?: number
      type?: string
      admin1?: string
      admin2?: string
      terrain_class?: string
      catchment_population?: number
      operating_status?: string
      confidence_marker?: ConfidenceMarker
      reason?: string
    },
  ) => request<{ node: NodeRow; issues: ValidationIssue[] }>(`/countries/${countryId}/nodes`, { method: 'POST', body: JSON.stringify(payload) }),
  retireNode: (nodeId: number, reason: string) =>
    request<{ retired: string; lanes: number; demand_rows: number }>(`/nodes/${nodeId}/retire`, { method: 'POST', body: JSON.stringify({ reason }) }),
  restoreNode: (nodeId: number, reason: string) =>
    request<{ node: NodeRow; lanes: number; demand_rows: number }>(`/nodes/${nodeId}/restore`, { method: 'POST', body: JSON.stringify({ reason }) }),
  nodeDemand: (nodeId: number) => request<DemandRow[]>(`/nodes/${nodeId}/demand`),
  setNodeDemand: (
    nodeId: number,
    lines: { sku: string; quantity: number; period?: number; source?: string; confidence?: number }[],
    confidence_marker: ConfidenceMarker,
    reason: string,
  ) => request<DemandRow[]>(`/nodes/${nodeId}/demand`, { method: 'PUT', body: JSON.stringify({ lines, confidence_marker, reason }) }),
  overrideEdge: (edgeId: number, patch: Partial<EdgeRow> & { rationale?: string }) =>
    request<EdgeRow>(`/edges/${edgeId}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  revertAudit: (entryId: number) => request<{ reverted: number; by: number }>(`/audit/${entryId}/revert`, { method: 'POST' }),
  createCountry: (body: { code: string; name: string; currency?: string; config?: Record<string, unknown> }) =>
    request<{ id: number; code: string; name: string }>('/countries', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  correctImportRow: (batchId: number, body: { sheet: string; key: string; values: Record<string, unknown>; reason?: string }) =>
    request<ValidationReport>(`/imports/${batchId}/rows`, { method: 'PATCH', body: JSON.stringify(body) }),

  templateUrl: () => `${BASE}/template.xlsx`,
  reportUrl: (scenarioId: number) => `${BASE}/scenarios/${scenarioId}/report.html`,
  networkExportUrl: (countryId: number) => `${BASE}/countries/${countryId}/export/network.xlsx`,
  resultsExportUrl: (scenarioId: number) => `${BASE}/scenarios/${scenarioId}/export/results.xlsx`,
}

export const connectorApi = {
  systems: () =>
    request<{ systems: ConnectorSpec[]; note: string }>('/connectors'),
  list: (countryId: number) => request<Connection[]>(`/countries/${countryId}/connections`),
  create: (countryId: number, body: Record<string, unknown>) =>
    request<Connection>(`/countries/${countryId}/connections`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  update: (id: number, body: Record<string, unknown>) =>
    request<Connection>(`/connections/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  remove: (id: number) => request<void>(`/connections/${id}`, { method: 'DELETE' }),
  test: (id: number) => request<ConnectionTest>(`/connections/${id}/test`, { method: 'POST' }),
  preview: (id: number) => request<SyncPreview>(`/connections/${id}/preview`, { method: 'POST' }),
  recordSync: (id: number, summary: Record<string, number>) =>
    request<Connection>(`/connections/${id}/record-sync`, {
      method: 'POST',
      body: JSON.stringify(summary),
    }),
}
