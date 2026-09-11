import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import type React from 'react'
import { api } from './api'
import { MapView } from './components/MapView'
import type { ColourBy } from './components/MapView'
import { ScenarioPanel } from './components/ScenarioPanel'
import { Scorecard } from './components/Scorecard'
import { EquityPanel } from './components/EquityPanel'
import { DataPanel, FacilityTable, ProvenancePanel, RoadmapPanel, SeasonPanel, ServicesPanel } from './components/Panels'
import { ConnectionsPanel } from './components/ConnectionsPanel'
import type {
  AuditRow,
  EdgeRow,
  NodeRow,
  Overview,
  Result,
  Roadmap,
  Scenario,
  Scorecard as ScorecardData,
  SeasonView,
} from './types'
import { exact, formatKpi, money, signedPct } from './format'

const TABS = ['Scorecard', 'Equity', 'Facilities', 'Services', 'Season', 'Data', 'Live', 'Provenance', 'Roadmap'] as const
type Tab = (typeof TABS)[number]

const slug = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, '-')

const MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/*
 * Opening a workspace for a country nobody has modelled yet.
 *
 * Deliberately short. The only things asked for are the ones with no sensible default;
 * everything else — the bounding box, the terrain factors, the seasonal profiles, the
 * land mask — starts as an honest blank and is filled in later from the data itself.
 * Asking an analyst to invent a bounding box before they have seen a facility list is
 * how you get a check that rejects real places.
 */
function NewCountryDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (id: number) => void
}) {
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [currency, setCurrency] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const id = useId()

  async function create() {
    setBusy(true)
    setError(null)
    try {
      const created = await api.createCountry({
        code: code.trim(),
        name: name.trim(),
        currency: currency.trim() || undefined,
      })
      onCreated(created.id)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-scrim" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`}>
      <div className="modal">
        <h3 id={`${id}-title`}>Open a country workspace</h3>
        <p className="lever-note" style={{ marginTop: 0 }}>
          You will get an empty network. Load a facility list from a spreadsheet or a
          connected system next, and the tool will tell you what it still needs.
        </p>

        <div className="lever">
          <div className="lever-head">
            <label htmlFor={`${id}-code`}>Country code</label>
          </div>
          <input id={`${id}-code`} value={code} placeholder="SLB" onChange={(e) => setCode(e.target.value)} />
          <div className="lever-note">Short, and yours to choose. ISO three-letter codes are the usual habit.</div>
        </div>

        <div className="lever">
          <div className="lever-head">
            <label htmlFor={`${id}-name`}>Name</label>
          </div>
          <input id={`${id}-name`} value={name} placeholder="Solomon Islands" onChange={(e) => setName(e.target.value)} />
        </div>

        <div className="lever">
          <div className="lever-head">
            <label htmlFor={`${id}-currency`}>Currency</label>
          </div>
          <input id={`${id}-currency`} value={currency} placeholder="SBD" onChange={(e) => setCurrency(e.target.value)} />
          <div className="lever-note">Used for every cost shown. Defaults to USD.</div>
        </div>

        {error && <div className="callout bad" style={{ margin: '10px 0 0' }}>{error}</div>}

        <div style={{ display: 'flex', gap: 6, marginTop: 14 }}>
          <button
            className="btn small primary"
            disabled={busy || !code.trim() || !name.trim()}
            aria-busy={busy}
            onClick={() => void create()}
          >
            {busy ? <span className="spinner" aria-hidden="true" /> : 'Open workspace'}
          </button>
          <button className="btn small ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}

export default function App() {
  const [countryId, setCountryId] = useState<number | null>(null)
  const [countries, setCountries] = useState<{ id: number; code: string; name: string }[]>([])
  const [newCountry, setNewCountry] = useState(false)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [nodes, setNodes] = useState<NodeRow[]>([])
  const [edges, setEdges] = useState<EdgeRow[]>([])
  const [basemap, setBasemap] = useState<GeoJSON.FeatureCollection | null>(null)
  const [audit, setAudit] = useState<AuditRow[]>([])

  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [compareIds, setCompareIds] = useState<number[]>([])
  const [result, setResult] = useState<Result | null>(null)
  const [scorecard, setScorecard] = useState<ScorecardData | null>(null)
  const [roadmap, setRoadmap] = useState<Roadmap | null>(null)
  const [roadmapError, setRoadmapError] = useState<string | null>(null)

  const [month, setMonth] = useState<number | null>(null)
  const [season, setSeason] = useState<SeasonView | null>(null)
  const [colourBy, setColourBy] = useState<ColourBy>('fill')
  const [selectedCode, setSelectedCode] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('Scorecard')

  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showBanner, setShowBanner] = useState(true)

  const selected = scenarios.find((s) => s.id === selectedId) ?? null
  const currency = overview?.country.currency ?? ''

  /* ---------------------------------------------------------------- load */

  const loadCountries = useCallback(
    (select?: number) =>
      api
        .countries()
        .then((list) => {
          setCountries(list)
          if (select) setCountryId(select)
          else if (list.length) setCountryId((current) => current ?? list[0].id)
          else setError('No country workspace exists yet.')
        })
        .catch((e) => setError(String(e))),
    [],
  )

  useEffect(() => {
    void loadCountries()
  }, [loadCountries])

  const loadNetwork = useCallback(async (id: number) => {
    const [ov, ns, es, bm, au] = await Promise.all([
      api.overview(id),
      api.nodes(id),
      api.edges(id),
      api.basemap(id),
      api.audit(id),
    ])
    setOverview(ov)
    setNodes(ns)
    setEdges(es)
    setBasemap(bm)
    setAudit(au)
  }, [])

  const loadScenarios = useCallback(async (id: number) => {
    const list = await api.scenarios(id)
    setScenarios(list)
    setSelectedId(list.find((s) => s.is_baseline)?.id ?? list[0]?.id ?? null)
    setCompareIds(list.slice(0, 4).map((s) => s.id))
    // A workspace nobody has loaded data into yet has no baseline, and asking for a
    // scorecard would answer with an error. That is the expected state of something
    // created a moment ago, not a fault, so it gets an empty scorecard and the
    // interface says what to do next.
    setScorecard(list.length ? await api.scorecard(id) : null)
  }, [])

  useEffect(() => {
    if (countryId === null) return
    // Clear the previous country's answers first, so nothing from it is briefly shown
    // against the new one's name.
    setResult(null)
    setScorecard(null)
    setRoadmap(null)
    setError(null)
    Promise.all([loadNetwork(countryId), loadScenarios(countryId)]).catch((e) => setError(String(e)))
  }, [countryId, loadNetwork, loadScenarios])

  /* ------------------------------------------------------- selected result */

  useEffect(() => {
    if (!selected?.latest_result_id) {
      setResult(null)
      return
    }
    api.result(selected.latest_result_id).then(setResult).catch(() => setResult(null))
  }, [selected?.latest_result_id])

  // The month slider follows the scenario: a scenario pinned to March should show
  // March on the map without the user having to set it twice.
  useEffect(() => {
    if (selected) setMonth(selected.levers?.month ?? null)
  }, [selectedId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (countryId === null || month === null) {
      setSeason(null)
      return
    }
    api.season(countryId, month).then(setSeason).catch(() => setSeason(null))
  }, [countryId, month])

  useEffect(() => {
    if (!selected || selected.is_baseline || !selected.latest_result_id) {
      setRoadmap(null)
      setRoadmapError(selected?.is_baseline ? 'The baseline is the reference, so it has no roadmap.' : null)
      return
    }
    api
      .roadmap(selected.id)
      .then((data) => {
        setRoadmap(data)
        setRoadmapError(null)
      })
      .catch((e) => {
        setRoadmap(null)
        setRoadmapError(String(e))
      })
  }, [selected?.id, selected?.latest_result_id, selected?.is_baseline])

  /* ---------------------------------------------------------------- actions */

  const refreshAfterRun = useCallback(async () => {
    if (countryId === null) return
    const [list, card] = await Promise.all([api.scenarios(countryId), api.scorecard(countryId)])
    setScenarios(list)
    setScorecard(card)
  }, [countryId])

  async function runOne(id: number) {
    setRunning(true)
    setError(null)
    try {
      await api.run(id)
      await refreshAfterRun()
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  async function runSet() {
    if (countryId === null || !compareIds.length) return
    setRunning(true)
    setError(null)
    try {
      await api.runSet(countryId, compareIds)
      await refreshAfterRun()
      setTab('Scorecard')
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  async function patchScenario(id: number, patch: Partial<Scenario>) {
    setScenarios((current) => current.map((s) => (s.id === id ? { ...s, ...patch } : s)))
    try {
      await api.updateScenario(id, patch)
      if (patch.levers && 'month' in patch.levers) setMonth(patch.levers.month ?? null)
    } catch (e) {
      setError(String(e))
    }
  }

  async function cloneScenario(id: number) {
    const source = scenarios.find((s) => s.id === id)
    const name = window.prompt('Name for the new scenario', `${source?.name ?? 'Scenario'} (copy)`)
    if (!name) return
    try {
      const created = await api.cloneScenario(id, { name })
      await loadScenarios(countryId!)
      setSelectedId(created.id)
      setCompareIds((current) => [...current, created.id])
    } catch (e) {
      setError(String(e))
    }
  }

  async function deleteScenario(id: number) {
    if (!window.confirm('Delete this scenario and its results?')) return
    try {
      await api.deleteScenario(id)
      setSelectedId(null)
      setCompareIds((current) => current.filter((c) => c !== id))
      await loadScenarios(countryId!)
    } catch (e) {
      setError(String(e))
    }
  }

  /**
   * Move the month slider, pin the selected scenario to that month, and re-solve.
   *
   * Re-solving is not optional. If the map showed March's closed roads while the
   * scorecard still showed the annualised cost, the two would be read together and
   * the reader would draw a false conclusion. A run takes well under a second, so
   * the honest behaviour is also the affordable one.
   */
  async function pickMonth(next: number | null) {
    setMonth(next)
    if (!selected) return
    const levers = { ...selected.levers, month: next }
    setScenarios((current) => current.map((s) => (s.id === selected.id ? { ...s, levers } : s)))
    setRunning(true)
    setError(null)
    try {
      await api.updateScenario(selected.id, { levers })
      if (selected.latest_result_id) {
        await api.run(selected.id)
        await refreshAfterRun()
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  /* ---------------------------------------------------------------- derived */

  const kpiByScenario = useMemo(() => {
    const out: Record<number, Record<string, number>> = {}
    for (const row of scorecard?.rows ?? []) if (row.status === 'ok') out[row.scenario_id] = row.kpi_set
    return out
  }, [scorecard])

  const baselineRow = scorecard?.rows.find((row) => row.is_baseline) ?? null
  const baselineCost = baselineRow?.kpi_set?.total_cost ?? null
  const provenance = overview?.country.config.data_provenance

  const headlineKpis = useMemo(() => {
    if (!result || result.status !== 'ok') return []
    const comparison = scorecard?.rows.find((row) => row.scenario_id === result.scenario_id)?.comparison ?? {}
    return ['total_cost', 'fill_rate', 'worst_stratum_fill_rate', 'mean_stockout_risk'].map((key) => ({
      key,
      label: scorecard?.kpi_meta[key]?.label ?? key,
      unit: scorecard?.kpi_meta[key]?.unit ?? 'count',
      value: result.kpi_set[key],
      comparison: comparison[key],
    }))
  }, [result, scorecard])

  /* Arrow keys move the selection and the focus together, so the panel under the
     tablist changes as you arrow across it — the APG "automatic activation"
     pattern, which suits tabs that are cheap to render. */
  const onTabKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const delta =
      event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : event.key === 'Home' ? -Infinity : event.key === 'End' ? Infinity : 0
    if (!delta) return
    event.preventDefault()
    const current = TABS.indexOf(tab)
    const next =
      delta === -Infinity ? 0 : delta === Infinity ? TABS.length - 1 : (current + delta + TABS.length) % TABS.length
    setTab(TABS[next])
    document.getElementById(`tab-${slug(TABS[next])}`)?.focus()
  }

  /* ---------------------------------------------------------------- render */

  return (
    <div className="app">
      {/* First tab stop on the page. The sidebar is a long list of scenarios and
          levers; without this a keyboard user pays for it before every result. */}
      <a className="skip-link" href="#results">
        Skip to results
      </a>
      <header className="topbar">
        <div className="brand">
          <h1>Health Supply Chain Network Design</h1>
          {countries.length > 1 ? (
            <select
              className="country-picker"
              aria-label="Country workspace"
              value={countryId ?? ''}
              onChange={(event) => setCountryId(Number(event.target.value))}
            >
              {countries.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          ) : (
            <span>{overview?.country.name ?? 'Loading…'}</span>
          )}
        </div>

        {overview && (
          <div className="topbar-stats">
            <div className="stat">
              <b className="num">{overview.counts.facilities}</b>
              <span>facilities</span>
            </div>
            <div className="stat">
              <b className="num">{overview.counts.hubs_operational}</b>
              <span>stores open</span>
            </div>
            <div className="stat">
              <b className="num">{overview.counts.scheduled_services}</b>
              <span>timetables</span>
            </div>
            <div className="stat">
              <b className="num">{exact(overview.totals.population)}</b>
              <span>people</span>
            </div>
          </div>
        )}

        <div className="topbar-right">
          <button className="btn small ghost" onClick={() => setNewCountry(true)}>
            New country
          </button>
          {result?.status === 'ok' && (
            <span className="pill info">
              {String(result.solver_log.month_label ?? 'Annualised')} · {result.runtime_ms} ms
            </span>
          )}
          {selected && selected.latest_result_id && (
            <a
              className="btn small primary"
              href={api.reportUrl(selected.id)}
              target="_blank"
              rel="noreferrer"
              title="A document for people who will not open this tool. Prints to PDF from your browser."
            >
              Report
            </a>
          )}
          {selected && !selected.is_baseline && selected.latest_result_id && (
            <a className="btn small" href={api.resultsExportUrl(selected.id)} download>
              Spreadsheet
            </a>
          )}
        </div>
      </header>

      {showBanner && provenance && (
        <div className="banner">
          <b>{provenance.status} DATA</b>
          <span>
            Real: {provenance.real} Illustrative: {provenance.illustrative} {provenance.before_use}
          </span>
          <button className="btn small ghost" onClick={() => setShowBanner(false)} aria-label="Dismiss data provenance notice">
            Dismiss
          </button>
        </div>
      )}

      {newCountry && (
        <NewCountryDialog
          onClose={() => setNewCountry(false)}
          onCreated={(id) => {
            setNewCountry(false)
            void loadCountries(id)
          }}
        />
      )}

      {/* Errors here are the result of something the user just asked for, so they
          are announced rather than left to be noticed. */}
      <div role="alert" aria-live="assertive">
        {error && (
          <div className="banner" style={{ background: '#2a1717', borderColor: '#542b2b', color: '#f2b0b0' }}>
            <b>Problem</b>
            <span>{error}</span>
            <button className="btn small ghost" onClick={() => setError(null)} aria-label="Dismiss error">
              Dismiss
            </button>
          </div>
        )}
      </div>

      <div className="body">
        <ScenarioPanel
          scenarios={scenarios}
          selectedId={selectedId}
          compareIds={compareIds}
          currency={currency}
          services={overview?.services ?? []}
          running={running}
          kpiByScenario={kpiByScenario}
          onSelect={setSelectedId}
          onToggleCompare={(id) =>
            setCompareIds((current) => (current.includes(id) ? current.filter((c) => c !== id) : [...current, id]))
          }
          onPatch={patchScenario}
          onClone={cloneScenario}
          onDelete={deleteScenario}
          onRun={runOne}
          onRunSet={runSet}
        />

        <div className="centre">
          <MapView
            countryId={countryId ?? 0}
            nodes={nodes}
            edges={edges}
            basemap={basemap}
            result={result}
            season={season}
            month={month}
            colourBy={colourBy}
            onColourBy={setColourBy}
            selectedCode={selectedCode}
            onSelect={(code) => {
              setSelectedCode(code)
              if (code) setTab('Facilities')
            }}
            center={overview?.country.config.center}
          />

          <div className="season-bar">
            <div className="dim tiny" style={{ width: 74 }} role="status" aria-live="polite">
              {running ? (
                <>
                  <span className="spinner" aria-hidden="true" /> SOLVING
                </>
              ) : (
                'CONDITIONS'
              )}
            </div>
            <div className="months" role="group" aria-label="Conditions to hold for a year">
              <button
                type="button"
                className={`month-cell annual${month === null ? ' on' : ''}`}
                aria-pressed={month === null}
                onClick={() => pickMonth(null)}
              >
                Annualised
              </button>
              {MONTH_ABBR.map((abbr, index) => {
                const value = index + 1
                const closed = month === value ? season?.summary.lanes_closed ?? 0 : 0
                return (
                  <button
                    type="button"
                    key={abbr}
                    className={`month-cell${month === value ? ' on' : ''}`}
                    aria-pressed={month === value}
                    onClick={() => pickMonth(value)}
                    title={`${abbr} conditions, held for a year`}
                  >
                    {abbr}
                    {month === value && (
                      <div
                        className="month-bar"
                        style={{ background: closed > 0 ? 'var(--bad)' : 'var(--good)' }}
                      />
                    )}
                  </button>
                )
              })}
            </div>
            {season && (
              <div className="tiny" style={{ width: 190, textAlign: 'right' }}>
                <span style={{ color: season.summary.lanes_closed ? 'var(--bad)' : 'var(--muted)' }}>
                  {season.summary.lanes_closed} lanes closed
                </span>
                <span className="dim"> · {season.summary.facilities_losing_surface_access} air-dependent</span>
              </div>
            )}
          </div>
        </div>

        <aside className="inspector" id="results" aria-label="Results">
          {headlineKpis.length > 0 && (
            <div className="kpi-grid">
              {headlineKpis.map((kpi) => (
                <div className="kpi" key={kpi.key}>
                  <span className="kpi-label">{kpi.label}</span>
                  <b>{formatKpi(kpi.value, kpi.unit, kpi.key === 'total_cost' ? currency : '')}</b>
                  {kpi.comparison && kpi.comparison.delta_pct !== null && Math.abs(kpi.comparison.delta) > 1e-9 && (
                    <span className={`delta ${kpi.comparison.direction}`}>
                      {signedPct(kpi.comparison.delta_pct)} vs baseline
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}

          {result?.status === 'infeasible' && (
            <div className="callout bad">
              <h4>No feasible plan</h4>
              {result.error}
            </div>
          )}

          {/* A tablist is one tab stop, not eight: Tab reaches the selected tab,
              arrows move between them. That is the APG pattern and it is what a
              screen reader user expects when the role says tablist. */}
          <div className="tabs" role="tablist" aria-label="Result views" onKeyDown={onTabKeyDown}>
            {TABS.map((name) => (
              <button
                type="button"
                key={name}
                id={`tab-${slug(name)}`}
                role="tab"
                aria-selected={tab === name}
                aria-controls="tab-body"
                tabIndex={tab === name ? 0 : -1}
                className={`tab${tab === name ? ' on' : ''}`}
                onClick={() => setTab(name)}
              >
                {name}
              </button>
            ))}
          </div>

          <div className="tab-body" id="tab-body" role="tabpanel" aria-labelledby={`tab-${slug(tab)}`} tabIndex={0}>
            {tab === 'Scorecard' && (
              <Scorecard data={scorecard} currency={currency} selectedId={selectedId} onSelect={setSelectedId} />
            )}
            {tab === 'Equity' && (
              <EquityPanel
                result={result}
                baselineRow={baselineRow}
                currency={currency}
                equityDefinition={overview?.country.config.equity_definition}
              />
            )}
            {tab === 'Facilities' && (
              <FacilityTable
                result={result}
                selectedCode={selectedCode}
                onSelect={setSelectedCode}
                currency={currency}
              />
            )}
            {tab === 'Services' && <ServicesPanel overview={overview} edges={edges} result={result} />}
            {tab === 'Season' && (
              <SeasonPanel
                season={season}
                month={month}
                result={result}
                baselineCost={baselineCost}
                currency={currency}
              />
            )}
            {tab === 'Data' && countryId !== null && (
              <DataPanel
                countryId={countryId}
                overview={overview}
                nodes={nodes}
                onImported={() => {
                  void loadNetwork(countryId)
                  void loadScenarios(countryId)
                }}
              />
            )}
            {tab === 'Live' && countryId !== null && (
              <ConnectionsPanel
                countryId={countryId}
                onApplied={() => {
                  void loadNetwork(countryId)
                  void loadScenarios(countryId)
                }}
              />
            )}
            {tab === 'Provenance' && <ProvenancePanel overview={overview} audit={audit} edges={edges} />}
            {tab === 'Roadmap' && (
              <RoadmapPanel
                roadmap={roadmap}
                currency={currency}
                error={roadmapError}
                exportUrl={selected && !selected.is_baseline ? api.resultsExportUrl(selected.id) : null}
              />
            )}
          </div>

          {selected && (
            <div className="section" style={{ borderTop: '1px solid var(--line)', borderBottom: 'none' }}>
              <div className="tiny dim">
                {selected.name}
                {baselineCost && result?.status === 'ok' && !selected.is_baseline && (
                  <>
                    {' '}
                    · {money(result.kpi_set.total_cost - baselineCost, currency)} against baseline
                  </>
                )}
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}
