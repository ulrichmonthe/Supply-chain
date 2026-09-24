/*
 * Editing one facility in the tool.
 *
 * The spreadsheet round trip stays -- it is how a whole master list moves. This is
 * for the other case: one coordinate a provincial officer knows is wrong, one clinic
 * that opened last month, one demand figure the last upload got from a stale forecast.
 * Downloading a workbook to change a number is how corrections stop being made.
 *
 * Every save asks how sure the person is. Sourced, inferred or unverified is declared
 * at the moment of typing, written beside the change in the ledger, and printed in
 * the report's assumptions annex. Editing becomes the act of documenting.
 */

import { useEffect, useId, useState } from 'react'
import { api } from '../api'
import type { ConfidenceMarker, DemandRow, EdgeRow, NodeRow, ValidationIssue } from '../types'
import { exact } from '../format'

const STATUSES = ['operational', 'closed', 'proposed', 'seasonal'] as const

export function MarkerPicker({
  value,
  onChange,
  compact,
}: {
  value: ConfidenceMarker
  onChange: (marker: ConfidenceMarker) => void
  compact?: boolean
}) {
  const options: { key: ConfidenceMarker; label: string; hint: string }[] = [
    { key: 'S', label: 'Sourced', hint: 'From a document, a system or a person who would know' },
    { key: 'I', label: 'Inferred', hint: 'Worked out from something else; a reasonable estimate' },
    { key: 'U', label: 'Unverified', hint: 'A placeholder until someone checks' },
  ]
  return (
    <div className="marker-picker" role="group" aria-label="How sure are you?">
      {options.map((option) => (
        <button
          type="button"
          key={option.key}
          className={`marker ${option.key}${value === option.key ? ' on' : ''}`}
          aria-pressed={value === option.key}
          title={option.hint}
          onClick={() => onChange(option.key)}
        >
          {compact ? option.key : option.label}
        </button>
      ))}
    </div>
  )
}

function Issues({ issues }: { issues: ValidationIssue[] }) {
  if (!issues.length) return null
  return (
    <div>
      {issues.map((issue) => (
        <div className={`issue ${issue.severity}`} key={`${issue.code}-${issue.entity}`}>
          <div className="issue-head">
            <span className={`pill ${issue.severity === 'error' ? 'bad' : issue.severity === 'warning' ? 'warn' : 'info'}`}>
              {issue.severity}
            </span>
            <code>{issue.code}</code>
          </div>
          <p>{issue.message}</p>
          {issue.suggestion && <p className="suggestion">{issue.suggestion}</p>}
        </div>
      ))}
    </div>
  )
}

export function FacilityEditor({
  node,
  edges,
  currency,
  onChanged,
  onRetired,
}: {
  node: NodeRow
  edges: EdgeRow[]
  currency: string
  onChanged: () => void
  onRetired: () => void
}) {
  const id = useId()
  const [name, setName] = useState(node.name)
  const [lat, setLat] = useState(String(node.lat))
  const [lon, setLon] = useState(String(node.lon))
  const [population, setPopulation] = useState(String(node.catchment_population))
  const [status, setStatus] = useState(node.operating_status)
  const [dry, setDry] = useState(String(node.capacity?.dry_m3 ?? ''))
  const [cold, setCold] = useState(String(node.capacity?.cold_by_band?.['+2-8'] ?? ''))
  const [marker, setMarker] = useState<ConfidenceMarker>('I')
  const [reason, setReason] = useState('')
  const [issues, setIssues] = useState<ValidationIssue[]>([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const [demand, setDemand] = useState<DemandRow[]>([])
  const [demandDraft, setDemandDraft] = useState<Record<string, string>>({})

  const lanes = edges.filter((edge) => edge.to_code === node.code || edge.from_code === node.code)
  const [laneDraft, setLaneDraft] = useState<Record<number, string>>({})

  // A different facility: start again from its values, not the last one's drafts.
  useEffect(() => {
    setName(node.name)
    setLat(String(node.lat))
    setLon(String(node.lon))
    setPopulation(String(node.catchment_population))
    setStatus(node.operating_status)
    setDry(String(node.capacity?.dry_m3 ?? ''))
    setCold(String(node.capacity?.cold_by_band?.['+2-8'] ?? ''))
    setIssues([])
    setMessage(null)
    setLaneDraft({})
    api
      .nodeDemand(node.id)
      .then((rows) => {
        setDemand(rows)
        setDemandDraft(Object.fromEntries(rows.map((row) => [row.sku, String(row.quantity)])))
      })
      .catch(() => setDemand([]))
  }, [node.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const number = (text: string): number | null => {
    const value = Number(String(text).replace(/,/g, '').trim())
    return text.trim() === '' || Number.isNaN(value) ? null : value
  }

  async function save() {
    const patch: Record<string, unknown> = {}
    if (name.trim() !== node.name) patch.name = name.trim()
    const latN = number(lat)
    const lonN = number(lon)
    if (latN !== null && latN !== node.lat) patch.lat = latN
    if (lonN !== null && lonN !== node.lon) patch.lon = lonN
    const popN = number(population)
    if (popN !== null && popN !== node.catchment_population) patch.catchment_population = popN
    if (status !== node.operating_status) patch.operating_status = status
    const dryN = number(dry)
    const coldN = number(cold)
    const capacity = { ...(node.capacity ?? {}) }
    let capacityChanged = false
    if (dryN !== null && dryN !== (node.capacity?.dry_m3 ?? null)) {
      capacity.dry_m3 = dryN
      capacityChanged = true
    }
    if (coldN !== null && coldN !== (node.capacity?.cold_by_band?.['+2-8'] ?? null)) {
      capacity.cold_by_band = { ...(node.capacity?.cold_by_band ?? {}), '+2-8': coldN }
      capacityChanged = true
    }
    if (capacityChanged) patch.capacity = capacity

    if (!Object.keys(patch).length) {
      setMessage('Nothing changed.')
      return
    }
    setBusy(true)
    setMessage(null)
    try {
      const outcome = await api.patchNode(node.id, { ...patch, confidence_marker: marker, reason })
      setIssues(outcome.issues)
      setMessage(
        outcome.issues.some((issue) => issue.severity === 'error')
          ? 'Saved, but the checks below need attention.'
          : `Saved ${Object.keys(patch).length} field${Object.keys(patch).length === 1 ? '' : 's'}. Re-run the scenario for the numbers to follow.`,
      )
      setReason('')
      onChanged()
    } catch (error) {
      setMessage(String(error))
    } finally {
      setBusy(false)
    }
  }

  async function saveDemand() {
    const lines = demand
      .filter((row) => number(demandDraft[row.sku] ?? '') !== null && number(demandDraft[row.sku]) !== row.quantity)
      .map((row) => ({ sku: row.sku, quantity: number(demandDraft[row.sku]) as number, period: row.period, source: 'actual', confidence: marker === 'S' ? 0.9 : marker === 'I' ? 0.6 : 0.3 }))
    if (!lines.length) {
      setMessage('No demand figure changed.')
      return
    }
    setBusy(true)
    try {
      const rows = await api.setNodeDemand(node.id, lines, marker, reason)
      setDemand(rows)
      setMessage(`Saved demand for ${lines.length} product${lines.length === 1 ? '' : 's'}.`)
      setReason('')
      onChanged()
    } catch (error) {
      setMessage(String(error))
    } finally {
      setBusy(false)
    }
  }

  async function saveLane(edge: EdgeRow) {
    const value = number(laneDraft[edge.id] ?? '')
    if (value === null || value === edge.capacity_per_trip_m3) return
    setBusy(true)
    try {
      await api.overrideEdge(edge.id, { capacity_per_trip_m3: value, rationale: reason || 'Capacity corrected in the tool.' })
      setMessage(`Saved the capacity of ${edge.service_name ?? edge.code}.`)
      onChanged()
    } catch (error) {
      setMessage(String(error))
    } finally {
      setBusy(false)
    }
  }

  async function retire() {
    const why = window.prompt(`Retire ${node.name}? It keeps its history and can be restored from the Data tab. Why?`, 'Closed')
    if (why === null) return
    setBusy(true)
    try {
      const outcome = await api.retireNode(node.id, why)
      setMessage(`Retired ${node.code} with ${outcome.lanes} lanes and ${outcome.demand_rows} demand rows.`)
      onRetired()
    } catch (error) {
      setMessage(String(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="editor">
      <div className="editor-head">
        <div>
          <h3 style={{ margin: 0 }}>{node.name}</h3>
          <div className="tiny dim">
            {node.code} · level {node.level} · {node.admin1 ?? '—'} · {node.geocode_source} ({node.geocode_confidence.toFixed(2)})
          </div>
        </div>
        {node.level > 0 && (
          <button type="button" className="btn small ghost" onClick={retire} disabled={busy}>
            Retire…
          </button>
        )}
      </div>

      <div className="editor-grid">
        <label className="field">
          <span>Name</span>
          <input id={`${id}-name`} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>Status</span>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Latitude</span>
          <input inputMode="decimal" value={lat} onChange={(e) => setLat(e.target.value)} />
        </label>
        <label className="field">
          <span>Longitude</span>
          <input inputMode="decimal" value={lon} onChange={(e) => setLon(e.target.value)} />
        </label>
        <label className="field">
          <span>Catchment population</span>
          <input inputMode="numeric" value={population} onChange={(e) => setPopulation(e.target.value)} />
        </label>
        <label className="field">
          <span>Dry storage (m³)</span>
          <input inputMode="decimal" value={dry} onChange={(e) => setDry(e.target.value)} placeholder="unknown" />
        </label>
        <label className="field">
          <span>Cold storage +2–8°C (m³)</span>
          <input inputMode="decimal" value={cold} onChange={(e) => setCold(e.target.value)} placeholder="unknown" />
        </label>
      </div>

      <div className="editor-sign">
        <MarkerPicker value={marker} onChange={setMarker} />
        <input
          className="editor-reason"
          value={reason}
          placeholder="Why? (e.g. 2024 census, provincial officer, site visit)"
          onChange={(e) => setReason(e.target.value)}
          aria-label="Reason for the change"
        />
        <button type="button" className="btn small primary" onClick={save} disabled={busy}>
          Save changes
        </button>
      </div>

      {message && <div className="lever-note editor-message" role="status">{message}</div>}
      <Issues issues={issues} />

      {demand.length > 0 && (
        <div className="section" style={{ paddingLeft: 0, paddingRight: 0 }}>
          <h3>Demand</h3>
          <table className="editor-table">
            <thead>
              <tr>
                <th>Product</th>
                <th className="n">Per year</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {demand.map((row) => (
                <tr key={row.id}>
                  <td>
                    {row.product_name}
                    <div className="row-note">{row.sku}</div>
                  </td>
                  <td className="n">
                    <input
                      className="cell-input"
                      inputMode="numeric"
                      aria-label={`${row.product_name} demand per year`}
                      value={demandDraft[row.sku] ?? ''}
                      onChange={(e) => setDemandDraft({ ...demandDraft, [row.sku]: e.target.value })}
                    />
                  </td>
                  <td>
                    <span className={`pill ${row.source === 'actual' ? 'good' : row.source === 'proxy' ? 'warn' : 'info'}`}>{row.source}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button type="button" className="btn small" onClick={saveDemand} disabled={busy} style={{ marginTop: 6 }}>
            Save demand
          </button>
        </div>
      )}

      {lanes.length > 0 && (
        <div className="section" style={{ paddingLeft: 0, paddingRight: 0 }}>
          <h3>Lanes and what they carry</h3>
          <table className="editor-table">
            <thead>
              <tr>
                <th>Lane</th>
                <th className="n">m³ per trip</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {lanes.map((edge) => (
                <tr key={edge.id}>
                  <td>
                    {edge.service_name ?? edge.code}
                    <div className="row-note">
                      {edge.mode} · {edge.from_code} → {edge.to_code} · {edge.distance_km.toFixed(0)} km
                    </div>
                  </td>
                  <td className="n">
                    <input
                      className="cell-input"
                      inputMode="decimal"
                      aria-label={`${edge.service_name ?? edge.code} capacity per trip`}
                      value={laneDraft[edge.id] ?? String(edge.capacity_per_trip_m3)}
                      onChange={(e) => setLaneDraft({ ...laneDraft, [edge.id]: e.target.value })}
                    />
                  </td>
                  <td>
                    <button type="button" className="btn small ghost" onClick={() => saveLane(edge)} disabled={busy}>
                      Save
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="tiny dim" style={{ marginTop: 6 }}>
        Costs are in {currency}. Population {exact(node.catchment_population)} people on record.
      </div>
    </div>
  )
}
