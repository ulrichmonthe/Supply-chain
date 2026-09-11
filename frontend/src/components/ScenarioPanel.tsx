import { useId, useState } from 'react'
import type { Scenario, ServiceSummary } from '../types'
import { FREQUENCIES, frequencyLabel, money, pct } from '../format'

type Props = {
  scenarios: Scenario[]
  selectedId: number | null
  compareIds: number[]
  currency: string
  services: ServiceSummary[]
  running: boolean
  onSelect: (id: number) => void
  onToggleCompare: (id: number) => void
  onPatch: (id: number, patch: Partial<Scenario>) => void
  onClone: (id: number) => void
  onDelete: (id: number) => void
  onRun: (id: number) => void
  onRunSet: () => void
  kpiByScenario: Record<number, { total_cost?: number; fill_rate?: number; worst_stratum_fill_rate?: number }>
}

const MODES = ['road', 'sea', 'air', 'river']

export function ScenarioPanel(props: Props) {
  const selected = props.scenarios.find((s) => s.id === props.selectedId) ?? null

  return (
    <div className="sidebar">
      <div className="section">
        <h3>Scenarios</h3>
        {props.scenarios.length === 0 && (
          <div className="callout">
            <h4>Nothing loaded yet</h4>
            This workspace is empty. Open the <b>Data</b> tab, download the blank template, and
            upload a facility list — the tool checks it and tells you what is missing before
            anything is saved. A baseline scenario appears once there is a network to run it on.
          </div>
        )}
        {props.scenarios.map((scenario) => {
          const kpis = props.kpiByScenario[scenario.id]
          return (
            <div
              key={scenario.id}
              className={`scenario${scenario.id === props.selectedId ? ' active' : ''}`}
              onClick={() => props.onSelect(scenario.id)}
            >
              <div className="scenario-head">
                <input
                  type="checkbox"
                  checked={props.compareIds.includes(scenario.id)}
                  onChange={() => props.onToggleCompare(scenario.id)}
                  onClick={(event) => event.stopPropagation()}
                  style={{ accentColor: 'var(--accent)' }}
                  aria-label={`Include ${scenario.name} in the next scenario set run`}
                  title="Include in the next scenario set run"
                />
                {/* The whole row stays clickable for a mouse, but selection also has
                    a real control so it is reachable and announced without one. */}
                <button
                  type="button"
                  className="row-select"
                  aria-pressed={scenario.id === props.selectedId}
                  onClick={() => props.onSelect(scenario.id)}
                >
                  {scenario.name}
                </button>
                {scenario.is_baseline && <span className="pill info">base</span>}
              </div>
              {scenario.id === props.selectedId && scenario.description && (
                <div className="scenario-desc">{scenario.description}</div>
              )}
              {kpis?.total_cost !== undefined && (
                <div className="scenario-kpis">
                  <div>
                    <span>cost </span>
                    <b className="num">{money(kpis.total_cost, props.currency)}</b>
                  </div>
                  <div>
                    <span>fill </span>
                    <b className="num">{pct(kpis.fill_rate, 0)}</b>
                  </div>
                  <div>
                    <span>Q5 </span>
                    <b className="num">{pct(kpis.worst_stratum_fill_rate, 0)}</b>
                  </div>
                </div>
              )}
              {scenario.latest_status === 'infeasible' && (
                <div className="scenario-kpis">
                  <span className="pill bad">no feasible plan</span>
                </div>
              )}
              {scenario.id === props.selectedId && (
                <div className="scenario-actions" onClick={(event) => event.stopPropagation()}>
                  <button className="btn small primary" disabled={props.running} onClick={() => props.onRun(scenario.id)}>
                    Run
                  </button>
                  <button className="btn small" onClick={() => props.onClone(scenario.id)}>
                    Duplicate
                  </button>
                  {!scenario.is_baseline && (
                    <button className="btn small ghost" onClick={() => props.onDelete(scenario.id)}>
                      Delete
                    </button>
                  )}
                </div>
              )}
            </div>
          )
        })}

        <button
          className="btn primary"
          style={{ width: '100%', marginTop: 6 }}
          disabled={props.running || props.compareIds.length === 0}
          aria-busy={props.running}
          onClick={props.onRunSet}
        >
          {props.running ? (
            <>
              <span className="spinner" aria-hidden="true" /> Running…
            </>
          ) : (
            `Run ${props.compareIds.length} scenario${props.compareIds.length === 1 ? '' : 's'} in parallel`
          )}
        </button>
      </div>

      {selected && <LeverEditor scenario={selected} services={props.services} onPatch={props.onPatch} />}
    </div>
  )
}

function LeverEditor({
  scenario,
  services,
  onPatch,
}: {
  scenario: Scenario
  services: ServiceSummary[]
  onPatch: (id: number, patch: Partial<Scenario>) => void
}) {
  const [openServices, setOpenServices] = useState(false)
  const panelId = useId()
  const levers = scenario.levers ?? {}
  const constraints = scenario.constraints ?? {}
  const weights = scenario.objective_weights ?? {}

  const setLever = (patch: Record<string, unknown>) =>
    onPatch(scenario.id, { levers: { ...levers, ...patch } })
  const setConstraint = (patch: Record<string, unknown>) =>
    onPatch(scenario.id, { constraints: { ...constraints, ...patch } })
  const setWeight = (patch: Record<string, unknown>) =>
    onPatch(scenario.id, { objective_weights: { ...weights, ...patch } })

  const allowed = levers.allowed_modes ?? MODES
  const overrides = levers.service_frequency_overrides ?? {}
  const scheduled = services.filter((s) => s.mode !== 'road').slice(0, 14)

  return (
    <>
      <div className="section">
        <h3>Objective</h3>
        <Slider
          label="Cost"
          value={weights.cost ?? 1}
          min={0}
          max={2}
          step={0.05}
          format={(v) => v.toFixed(2)}
          onChange={(v) => setWeight({ cost: v })}
        />
        <Slider
          label="Service"
          value={weights.service ?? 1}
          min={0}
          max={2}
          step={0.05}
          format={(v) => v.toFixed(2)}
          onChange={(v) => setWeight({ service: v })}
        />
        <Slider
          label="Equity"
          value={weights.equity ?? 0}
          min={0}
          max={1}
          step={0.05}
          format={(v) => v.toFixed(2)}
          note="Raises the cost of failing to reach a vulnerable facility, so the solver protects it before it protects a cheap one."
          onChange={(v) => setWeight({ equity: v })}
        />
      </div>

      <div className="section">
        <h3>Constraints</h3>
        <OptionalSlider
          label="Minimum fill rate"
          value={constraints.min_fill_rate ?? null}
          onChange={(v) => setConstraint({ min_fill_rate: v })}
          note="Network-wide floor on the share of demand delivered."
        />
        <OptionalSlider
          label="Equity floor"
          value={constraints.equity_floor ?? null}
          onChange={(v) => setConstraint({ equity_floor: v })}
          note="Every vulnerability quintile must reach at least this fill rate. The lever that decides who pays for a saving."
        />
        <label className="checkbox" style={{ marginTop: 8 }}>
          <input
            type="checkbox"
            checked={constraints.respect_capacity !== false}
            onChange={(event) => setConstraint({ respect_capacity: event.target.checked })}
          />
          Respect vessel holds and hub throughput
        </label>
      </div>

      <div className="section">
        <h3>Network levers</h3>

        <div className="lever">
          <div className="lever-head">
            <span className="lever-label" id={`${panelId}-modes`}>
              Modes allowed
            </span>
          </div>
          <div className="chips" role="group" aria-labelledby={`${panelId}-modes`}>
            {MODES.map((mode) => (
              <button
                type="button"
                key={mode}
                className={`chip${allowed.includes(mode) ? ' on' : ''}`}
                aria-pressed={allowed.includes(mode)}
                onClick={() =>
                  setLever({
                    allowed_modes: allowed.includes(mode)
                      ? allowed.filter((m) => m !== mode)
                      : [...allowed, mode],
                  })
                }
              >
                {mode}
              </button>
            ))}
          </div>
        </div>

        <label className="checkbox" style={{ marginBottom: 10 }}>
          <input
            type="checkbox"
            checked={Boolean(levers.optimize_hubs)}
            onChange={(event) => setLever({ optimize_hubs: event.target.checked })}
          />
          Let the solver choose which hubs stay open
        </label>

        <div className="lever">
          <div className="lever-head">
            <label htmlFor={`${panelId}-integration`}>Programme integration</label>
          </div>
          <select
            id={`${panelId}-integration`}
            value={levers.integration_policy ?? 'vertical'}
            onChange={(event) => setLever({ integration_policy: event.target.value })}
          >
            <option value="vertical">Vertical — programmes run separate stores</option>
            <option value="partial">Partial — shared storage, separate transport</option>
            <option value="integrated">Integrated — one shared network</option>
          </select>
          <div className="lever-note">
            The largest structural saving available in most networks, and the hardest politically.
          </div>
        </div>

        <Slider
          label="Third-party transport"
          value={levers.third_party_share ?? 0}
          min={0}
          max={1}
          step={0.05}
          format={(v) => pct(v, 0)}
          onChange={(v) => setLever({ third_party_share: v })}
        />
        <Slider
          label="Fuel index"
          value={levers.fuel_index ?? 1}
          min={0.6}
          max={2}
          step={0.05}
          format={(v) => `${v.toFixed(2)}×`}
          onChange={(v) => setLever({ fuel_index: v })}
        />
        <Slider
          label="Demand growth"
          value={levers.demand_growth ?? 0}
          min={-0.2}
          max={0.5}
          step={0.05}
          format={(v) => pct(v, 0)}
          onChange={(v) => setLever({ demand_growth: v })}
        />
        <Slider
          label="Buffer stock policy"
          value={levers.safety_stock_days ?? 14}
          min={0}
          max={60}
          step={1}
          format={(v) => `${v.toFixed(0)} days`}
          note="How much cover a facility holds beyond one delivery cycle. Capped by what its store can physically hold."
          onChange={(v) => setLever({ safety_stock_days: v })}
        />
      </div>

      <div className="section">
        <h3>
          Timetables{' '}
          <button className="btn small ghost" style={{ float: 'right', marginTop: -3 }} onClick={() => setOpenServices(!openServices)}>
            {openServices ? 'hide' : `${scheduled.length} services`}
          </button>
        </h3>
        {openServices && (
          <>
            <div className="lever-note" style={{ marginBottom: 8 }}>
              Change how often a service calls and watch stockout risk at the facilities on that run.
              Nothing else in the network moves.
            </div>
            {scheduled.map((svc) => (
              <div className="lever" key={svc.name}>
                <div className="lever-head">
                  {/* The visible name is truncated to fit the column, so the control
                      carries the full one for anyone who cannot see the tooltip. */}
                  <label htmlFor={`${panelId}-svc-${svc.name}`} title={svc.name}>
                    {svc.name.length > 34 ? `${svc.name.slice(0, 33)}…` : svc.name}
                  </label>
                  <b className="dim">{svc.facilities} sites</b>
                </div>
                <select
                  id={`${panelId}-svc-${svc.name}`}
                  aria-label={`${svc.name} — call frequency`}
                  value={overrides[svc.name] ?? svc.frequency}
                  onChange={(event) => {
                    const next = { ...overrides }
                    if (event.target.value === svc.frequency) delete next[svc.name]
                    else next[svc.name] = event.target.value
                    setLever({ service_frequency_overrides: next })
                  }}
                >
                  {FREQUENCIES.map((frequency) => (
                    <option key={frequency} value={frequency}>
                      {frequencyLabel(frequency)}
                      {frequency === svc.frequency ? ' (current)' : ''}
                    </option>
                  ))}
                </select>
                {svc.service_days?.length ? (
                  <div className="lever-note">Sails {svc.service_days.join(', ')}</div>
                ) : null}
              </div>
            ))}
          </>
        )}
      </div>
    </>
  )
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  format,
  note,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  format: (value: number) => string
  note?: string
  onChange: (value: number) => void
}) {
  const id = useId()
  return (
    <div className="lever">
      <div className="lever-head">
        <label htmlFor={id}>{label}</label>
        <b>{format(value)}</b>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      {note && <div className="lever-note">{note}</div>}
    </div>
  )
}

function OptionalSlider({
  label,
  value,
  note,
  onChange,
}: {
  label: string
  value: number | null
  note?: string
  onChange: (value: number | null) => void
}) {
  return (
    <div className="lever">
      <div className="lever-head">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={value !== null && value !== undefined}
            onChange={(event) => onChange(event.target.checked ? 0.9 : null)}
          />
          {label}
        </label>
        <b>{value === null || value === undefined ? 'off' : pct(value, 0)}</b>
      </div>
      {value !== null && value !== undefined && (
        <input
          type="range"
          aria-label={label}
          min={0.5}
          max={1}
          step={0.01}
          value={value}
          onChange={(event) => onChange(Number(event.target.value))}
        />
      )}
      {note && <div className="lever-note">{note}</div>}
    </div>
  )
}
