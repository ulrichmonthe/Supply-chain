import type {
  AuditRow,
  Connection,
  ConnectionTest,
  ConnectorSpec,
  SyncPreview,
  EdgeRow,
  NodeRow,
  Overview,
  Result,
  Roadmap,
  Scenario,
  Scorecard,
  SeasonView,
  ValidationReport,
} from './types'

const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: init?.body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
    ...init,
  })
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
  audit: (countryId: number) => request<AuditRow[]>(`/countries/${countryId}/audit`),

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
  commitImport: (batchId: number, replace: boolean) =>
    request<{ committed: boolean; counts: Record<string, number> }>(
      `/imports/${batchId}/commit?replace=${replace}`,
      { method: 'POST' },
    ),

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
