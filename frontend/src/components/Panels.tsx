import { useMemo, useState } from 'react'
import type {
  AuditRow,
  EdgeRow,
  NodeRow,
  Overview,
  Result,
  Roadmap,
  SeasonView,
  ValidationReport,
} from '../types'
import { api } from '../api'
import { exact, fillColour, frequencyLabel, money, pct, riskColour } from '../format'

/* ------------------------------------------------------------------ facilities */

export function FacilityTable({
  result,
  selectedCode,
  onSelect,
  currency,
}: {
  result: Result | null
  selectedCode: string | null
  onSelect: (code: string) => void
  currency: string
}) {
  const [sort, setSort] = useState<'risk' | 'cost' | 'fill' | 'vulnerability' | 'name'>('risk')

  const rows = useMemo(() => {
    const detail = [...(result?.per_node_detail ?? [])]
    const comparators = {
      risk: (a: typeof detail[0], b: typeof detail[0]) => b.stockout_risk - a.stockout_risk,
      cost: (a: typeof detail[0], b: typeof detail[0]) => b.cost - a.cost,
      fill: (a: typeof detail[0], b: typeof detail[0]) => a.fill_rate - b.fill_rate,
      vulnerability: (a: typeof detail[0], b: typeof detail[0]) => b.vulnerability - a.vulnerability,
      name: (a: typeof detail[0], b: typeof detail[0]) => a.name.localeCompare(b.name),
    }
    return detail.sort(comparators[sort])
  }, [result, sort])

  if (!result || result.status !== 'ok') return <div className="empty">Run this scenario to see facility detail.</div>

  return (
    <div>
      <div className="section" style={{ borderBottom: '1px solid var(--line)' }}>
        <div className="chips">
          {(['risk', 'cost', 'fill', 'vulnerability', 'name'] as const).map((key) => (
            <span key={key} className={`chip${sort === key ? ' on' : ''}`} onClick={() => setSort(key)}>
              {key === 'fill' ? 'worst fill' : key}
            </span>
          ))}
        </div>
      </div>
      <table>
        <thead>
          <tr>
            <th>Facility</th>
            <th>Supplied by</th>
            <th className="n">Fill</th>
            <th className="n">Risk</th>
            <th className="n">Cost</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((node) => (
            <tr
              key={node.code}
              className={`clickable${node.code === selectedCode ? ' selected' : ''}`}
              onClick={() => onSelect(node.code)}
            >
              <td>
                <div>{node.name}</div>
                <div className="row-note">
                  {node.admin1} · {exact(node.population)} people ·{' '}
                  {node.storage_days.toFixed(0)}d storage
                  {node.storage_binding && <span style={{ color: 'var(--warn)' }}> (binding)</span>}
                </div>
              </td>
              <td>
                <div>{node.served_by[0]?.hub_name ?? <span style={{ color: 'var(--bad)' }}>nothing</span>}</div>
                <div className="row-note">
                  {node.service_name ?? node.primary_mode ?? '—'} · {frequencyLabel(node.service_frequency)}
                </div>
              </td>
              <td className="n" style={{ color: fillColour(node.fill_rate) }}>
                {pct(node.fill_rate, 0)}
              </td>
              <td className="n" style={{ color: riskColour(node.stockout_risk) }}>
                {pct(node.stockout_risk, 0)}
              </td>
              <td className="n">{money(node.cost, currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ------------------------------------------------------------------ services */

export function ServicesPanel({
  overview,
  edges,
  result,
}: {
  overview: Overview | null
  edges: EdgeRow[]
  result: Result | null
}) {
  if (!overview) return <div className="empty">Loading services…</div>

  const riskByService = new Map<string, { risk: number; count: number; population: number }>()
  for (const node of result?.per_node_detail ?? []) {
    if (!node.service_name) continue
    const entry = riskByService.get(node.service_name) ?? { risk: 0, count: 0, population: 0 }
    entry.risk += node.stockout_risk * node.population
    entry.population += node.population
    entry.count += 1
    riskByService.set(node.service_name, entry)
  }

  const scheduled = overview.services.filter((service) => service.mode !== 'road')

  return (
    <div>
      <div className="callout">
        <h4>Timetabled services</h4>
        A road lane is on demand: if a truck is full you send another. A vessel or an aircraft leaves on
        a day, carries a hold, and if you miss it the next one is a fortnight away. Modelling that as a
        frequency rather than a capacity is what lets you change one number here and watch stockout risk
        move at the facilities on that run, with nothing else in the network touched.
      </div>
      <table>
        <thead>
          <tr>
            <th>Service</th>
            <th className="n">Every</th>
            <th className="n">Hold</th>
            <th className="n">Sites</th>
            <th className="n">Risk</th>
          </tr>
        </thead>
        <tbody>
          {scheduled.map((service) => {
            const risk = riskByService.get(service.name)
            const meanRisk = risk && risk.population ? risk.risk / risk.population : null
            return (
              <tr key={service.name}>
                <td>
                  <div>{service.name}</div>
                  <div className="row-note">
                    from {service.origin} · {service.mode}
                    {service.service_days?.length ? ` · sails ${service.service_days.join(', ')}` : ''} ·{' '}
                    {pct(service.reliability, 0)} reliable
                  </div>
                </td>
                <td className="n">{service.interval_days.toFixed(0)}d</td>
                <td className="n">
                  {service.capacity_per_trip_m3 > 0 ? `${service.capacity_per_trip_m3.toFixed(1)} m³` : 'charter'}
                </td>
                <td className="n">{service.facilities}</td>
                <td className="n" style={{ color: meanRisk !== null ? riskColour(meanRisk) : undefined }}>
                  {meanRisk === null ? '—' : pct(meanRisk, 0)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="callout">
        <h4>Where these come from</h4>
        Every frequency, hold and reliability figure in this table is an assumption, not a timetable.
        Confirming whether digitised maritime and air schedules exist for medical distribution is the
        single most important open question about this dataset: it decides whether this capability has
        real data behind it. Until then, treat the shape of the answers as informative and the levels as
        placeholders. {edges.length} lanes loaded.
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ season */

export function SeasonPanel({
  season,
  month,
  result,
  baselineCost,
  currency,
}: {
  season: SeasonView | null
  month: number | null
  result: Result | null
  baselineCost: number | null
  currency: string
}) {
  if (!month) {
    return (
      <div className="callout">
        <h4>Annualised view</h4>
        Lanes are using their mean access across the year. Pick a month on the slider below the map to
        see the network under that month's conditions — the answer is what a year would cost and reach
        if those conditions held all year, which is the question a provincial health adviser is actually
        asking.
      </div>
    )
  }
  if (!season) return <div className="empty">Loading…</div>

  const extra =
    result && baselineCost && result.status === 'ok' ? result.kpi_set.total_cost - baselineCost : null

  return (
    <div>
      <div className={`callout ${season.summary.lanes_closed ? 'bad' : 'good'}`}>
        <h4>{season.month_name} conditions</h4>
        <b>{season.summary.lanes_closed}</b> lanes are impassable and <b>{season.summary.lanes_degraded}</b>{' '}
        carry reduced throughput.{' '}
        {season.summary.facilities_cut_off > 0 && (
          <>
            <b>{season.summary.facilities_cut_off}</b> facilities have no open lane at all.{' '}
          </>
        )}
        {season.summary.facilities_losing_surface_access > 0 && (
          <>
            A further <b>{season.summary.facilities_losing_surface_access}</b> lose their road or river
            and fall back onto air freight, covering{' '}
            <b>{exact(season.summary.population_losing_surface_access)}</b> people.
          </>
        )}
        {extra !== null && Math.abs(extra) > 1 && (
          <>
            {' '}
            Holding the same level of supply through this month costs{' '}
            <b>{money(Math.abs(extra), currency)}</b> {extra > 0 ? 'more' : 'less'} a year than the
            baseline.
          </>
        )}
      </div>

      {season.facilities_cut_off.length > 0 && (
        <>
          <div className="section">
            <h3>Unreachable in {season.month_name}</h3>
          </div>
          <table>
            <tbody>
              {season.facilities_cut_off.map((facility) => (
                <tr key={facility.code}>
                  <td>
                    {facility.name}
                    <div className="row-note">{facility.admin1}</div>
                  </td>
                  <td className="n">{exact(facility.population)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {season.facilities_losing_surface_access.length > 0 && (
        <>
          <div className="section">
            <h3>Air-dependent in {season.month_name}</h3>
          </div>
          <table>
            <tbody>
              {season.facilities_losing_surface_access.map((facility) => (
                <tr key={facility.code}>
                  <td>
                    {facility.name}
                    <div className="row-note">{facility.admin1} · road or river closed</div>
                  </td>
                  <td className="n">{exact(facility.population)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <div className="section">
        <h3>Lanes closed</h3>
      </div>
      <table>
        <tbody>
          {season.closed_lanes.slice(0, 40).map((lane) => (
            <tr key={lane.edge_code}>
              <td>
                {lane.to_name}
                <div className="row-note">
                  {lane.service_name ?? lane.edge_code} · {lane.mode}
                </div>
              </td>
              <td className="n" style={{ color: 'var(--bad)' }}>
                closed
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ------------------------------------------------------------------ roadmap */

export function RoadmapPanel({
  roadmap,
  currency,
  exportUrl,
  error,
}: {
  roadmap: Roadmap | null
  currency: string
  exportUrl: string | null
  error: string | null
}) {
  if (error) return <div className="empty">{error}</div>
  if (!roadmap) return <div className="empty">Select a non-baseline scenario and run it to build a roadmap.</div>

  const summary = roadmap.summary
  return (
    <div>
      <div className="callout">
        <h4>
          {roadmap.scenario} <span className="dim">vs</span> {roadmap.baseline}
        </h4>
        One-off cost <b>{money(summary.one_off_cost_total, currency)}</b> · annual change{' '}
        <b>{money(summary.annual_cost_delta, currency)}</b>
        {summary.payback_years && (
          <>
            {' '}
            · payback <b>{summary.payback_years} years</b>
          </>
        )}
        . Worst quintile moves from <b>{pct(summary.worst_quintile_fill_baseline)}</b> to{' '}
        <b>{pct(summary.worst_quintile_fill_scenario)}</b>.
        {exportUrl && (
          <div style={{ marginTop: 8 }}>
            <a className="btn small" href={exportUrl} download>
              Download the full deliverable (.xlsx)
            </a>
          </div>
        )}
      </div>

      {roadmap.phases
        .filter((phase) => phase.steps.length)
        .map((phase) => (
          <div className="phase" key={phase.phase}>
            <div className="phase-head">
              <b>{phase.label}</b>
              <span className="pill">{phase.window}</span>
              {phase.one_off_cost > 0 && <span className="pill warn">{money(phase.one_off_cost, currency)}</span>}
            </div>
            {roadmap.steps
              .filter((step) => step.phase === phase.phase)
              .map((step) => (
                <div className="step" key={step.id}>
                  <div className="step-head">
                    <span className="dim num">{step.id}</span>
                    <b>{step.title}</b>
                  </div>
                  <p>{step.detail}</p>
                  <div className="step-meta">
                    {step.one_off_cost ? (
                      <span className="pill warn">one-off {money(step.one_off_cost, currency)}</span>
                    ) : null}
                    {step.annual_cost_delta ? (
                      <span className="pill">annual {money(step.annual_cost_delta, currency)}</span>
                    ) : null}
                    <span className="pill">{step.owner}</span>
                  </div>
                  <p className="tiny" style={{ color: 'var(--dim)' }}>
                    <b>Risk.</b> {step.risk}
                    <br />
                    <b>Evidence.</b> {step.evidence}
                  </p>
                </div>
              ))}
          </div>
        ))}
    </div>
  )
}

/* ------------------------------------------------------------------ data */

export function DataPanel({
  countryId,
  overview,
  nodes,
  onImported,
}: {
  countryId: number
  overview: Overview | null
  nodes: NodeRow[]
  onImported: () => void
}) {
  const [report, setReport] = useState<ValidationReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [over, setOver] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'error' | 'warning'>('all')

  async function upload(file: File) {
    setBusy(true)
    setMessage(null)
    try {
      setReport(await api.validateUpload(countryId, file))
    } catch (error) {
      setMessage(String(error))
    } finally {
      setBusy(false)
    }
  }

  async function commit() {
    if (!report) return
    setBusy(true)
    try {
      const outcome = await api.commitImport(report.batch_id, true)
      setMessage(
        `Imported ${outcome.counts.nodes} facilities, ${outcome.counts.edges} lanes and ` +
          `${outcome.counts.demand} demand rows. Re-run your scenarios — until you do, their ` +
          `results describe the previous network.`,
      )
      setReport(null)
      onImported()
    } catch (error) {
      setMessage(String(error))
    } finally {
      setBusy(false)
    }
  }

  const lowConfidence = nodes.filter((node) => node.geocode_confidence < 0.6).length
  const issues = (report?.issues ?? []).filter((issue) => filter === 'all' || issue.severity === filter)

  return (
    <div>
      <div className="section">
        <h3>Take the model with you</h3>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <a className="btn small" href={api.networkExportUrl(countryId)} download>
            Export network (.xlsx)
          </a>
          <a className="btn small" href={api.templateUrl()} download>
            Blank template
          </a>
        </div>
        <div className="lever-note" style={{ marginTop: 7 }}>
          The export and the template have identical columns, so whatever comes out can go back in. That
          round trip is the durability claim: the model does not need this application to survive.
        </div>
      </div>

      <div
        className={`dropzone${over ? ' over' : ''}`}
        onDragOver={(event) => {
          event.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault()
          setOver(false)
          const file = event.dataTransfer.files[0]
          if (file) void upload(file)
        }}
        onClick={() => document.getElementById('file-input')?.click()}
      >
        {busy ? (
          <>
            <span className="spinner" /> Working…
          </>
        ) : (
          <>
            Drop a workbook here, or click to choose one
            <div className="tiny dim" style={{ marginTop: 4 }}>
              It is validated first. Nothing is written until you say so.
            </div>
          </>
        )}
        <input
          id="file-input"
          type="file"
          accept=".xlsx"
          style={{ display: 'none' }}
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) void upload(file)
            event.target.value = ''
          }}
        />
      </div>

      {message && <div className="callout">{message}</div>}

      {report && (
        <>
          <div className={`callout ${report.blocking ? 'bad' : 'good'}`}>
            <h4>{report.headline}</h4>
            <div style={{ display: 'flex', gap: 6, marginTop: 7, alignItems: 'center', flexWrap: 'wrap' }}>
              <span className="pill bad">{report.counts.error} errors</span>
              <span className="pill warn">{report.counts.warning} warnings</span>
              <span className="pill info">{report.counts.info} notes</span>
              <button className="btn small primary" disabled={report.blocking || busy} onClick={() => void commit()}>
                Apply this import
              </button>
              <button className="btn small ghost" onClick={() => setReport(null)}>
                Discard
              </button>
            </div>
          </div>
          <div className="section">
            <div className="chips">
              {(['all', 'error', 'warning'] as const).map((key) => (
                <span key={key} className={`chip${filter === key ? ' on' : ''}`} onClick={() => setFilter(key)}>
                  {key}
                </span>
              ))}
            </div>
          </div>
          {issues.map((issue, index) => (
            <div className={`issue ${issue.severity}`} key={`${issue.code}-${index}`}>
              <div className="issue-head">
                <span className={`pill ${issue.severity === 'error' ? 'bad' : issue.severity === 'warning' ? 'warn' : 'info'}`}>
                  {issue.sheet || 'data'}
                  {issue.row ? ` row ${issue.row}` : ''}
                </span>
                <code>{issue.code}</code>
              </div>
              <p>{issue.message}</p>
              <p className="suggestion">{issue.suggestion}</p>
            </div>
          ))}
        </>
      )}

      {!report && overview && (
        <div className="callout">
          <h4>What is loaded</h4>
          {overview.counts.facilities} facilities across {overview.totals.provinces.length} provinces,{' '}
          {overview.counts.edges} lanes, {overview.counts.scheduled_services} timetabled services,{' '}
          {exact(overview.totals.population)} people in the modelled catchments and{' '}
          {exact(overview.totals.annual_demand_m3)} m³ of annual demand.
          {lowConfidence > 0 && (
            <>
              {' '}
              <b>{lowConfidence}</b> facilities have a geocode confidence below 0.6 and should be shown as
              approximate.
            </>
          )}
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ provenance */

export function ProvenancePanel({
  overview,
  audit,
  edges,
}: {
  overview: Overview | null
  audit: AuditRow[]
  edges: EdgeRow[]
}) {
  if (!overview) return <div className="empty">Loading…</div>
  const cascade = overview.distance_provenance
  const total = cascade.total_edges || 1

  const rows = [
    { key: 'manual', label: 'Manual — typed by an operator, usually after a field interview' },
    { key: 'osrm', label: 'OSRM — routed on a self-hosted road graph' },
    { key: 'detour_factor', label: 'Detour factor — great circle inflated for terrain' },
    { key: 'great_circle', label: 'Great circle — straight line, correct only for air' },
  ]

  const worst = [...edges]
    .filter((edge) => edge.distance_confidence < 0.6)
    .sort((a, b) => b.distance_km - a.distance_km)
    .slice(0, 8)

  return (
    <div>
      <div className={`callout ${cascade.osrm_configured ? 'good' : ''}`}>
        <h4>Where the distances came from</h4>
        Every distance in the model carries the method that produced it, so any number can be defended
        or overwritten in a workshop.{' '}
        {cascade.osrm_configured
          ? 'A self-hosted OSRM instance is configured and used for road lanes.'
          : 'No OSRM instance is configured here, so road distances are estimates from a great-circle distance and a terrain-specific detour factor. Standing one up on a regional extract is the single largest available improvement in accuracy.'}
      </div>

      <div className="section">
        <h3>Distance cascade</h3>
        {rows.map((row) => {
          const count = cascade.counts[row.key] ?? 0
          return (
            <div key={row.key} style={{ marginBottom: 9 }}>
              <div className="lever-head">
                <label>{row.label}</label>
                <b className="num">{count}</b>
              </div>
              <div className="bar-track">
                <div
                  className="bar-fill"
                  style={{
                    width: `${(count / total) * 100}%`,
                    background: row.key === 'manual' ? '#3fb984' : row.key === 'osrm' ? '#4da3ff' : '#e8b23a',
                  }}
                />
              </div>
            </div>
          )
        })}
        <div className="lever-note">
          Distance-weighted confidence across the network: <b>{cascade.km_weighted_confidence.toFixed(2)}</b>
        </div>
      </div>

      {worst.length > 0 && (
        <>
          <div className="section">
            <h3>Longest low-confidence lanes</h3>
            <div className="lever-note">Replace these first: they carry the most error.</div>
          </div>
          <table>
            <tbody>
              {worst.map((edge) => (
                <tr key={edge.code}>
                  <td>
                    {edge.service_name ?? edge.code}
                    <div className="row-note">
                      {edge.from_code} → {edge.to_code} · {edge.distance_method.replace(/_/g, ' ')}
                    </div>
                  </td>
                  <td className="n">{edge.distance_km.toFixed(0)} km</td>
                  <td className="n">{edge.distance_confidence.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <div className="section">
        <h3>Assumption log</h3>
        <div className="lever-note">
          Every assumption that entered the model, and who put it there. When a reviewer asks where a
          number came from, the answer is a row here rather than a memory.
        </div>
      </div>
      {audit.map((entry) => (
        <div className="issue info" key={entry.id}>
          <div className="issue-head">
            <span className="pill">{entry.provenance.replace(/_/g, ' ')}</span>
            <span className={`pill ${entry.confidence_marker === 'S' ? 'good' : entry.confidence_marker === 'U' ? 'bad' : 'warn'}`}>
              {entry.confidence_marker}
            </span>
            <code>
              {entry.entity_type}
              {entry.entity_ref ? `/${entry.entity_ref}` : ''} · {entry.field}
            </code>
          </div>
          <p>
            {entry.old_value ? (
              <>
                <span className="dim">{entry.old_value}</span> → <b>{entry.new_value}</b>
              </>
            ) : (
              <b>{entry.new_value}</b>
            )}
          </p>
          {entry.rationale && <p className="suggestion">{entry.rationale}</p>}
        </div>
      ))}
    </div>
  )
}
