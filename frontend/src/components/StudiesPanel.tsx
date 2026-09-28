import { useCallback, useEffect, useId, useState } from 'react'
import { api } from '../api'
import type { DiffMap, Scenario, Study, StudyCompare, StudyPreset } from '../types'
import { exact, formatKpi, money, pct, signedPct } from '../format'

/**
 * Studies: comparison as a workflow rather than a tab.
 *
 * A study is a question and the ordered scenarios that answer it, the baseline always
 * first. Presets make the classic ones in one click. Compare shows what differs
 * between the options (a lever diff, not a spreadsheet), what that did (KPI deltas,
 * the equity bands side by side, the facilities that gained or lost supply by name),
 * and the diff map draws two answers as one network.
 */
export function StudiesPanel({
  countryId,
  scenarios,
  currency,
  onDiff,
  diff,
  onScenariosChanged,
  onError,
  onSelectScenario,
}: {
  countryId: number
  scenarios: Scenario[]
  currency: string
  diff: DiffMap | null
  onDiff: (diff: DiffMap | null) => void
  onScenariosChanged: () => Promise<void> | void
  onError: (message: string) => void
  onSelectScenario: (id: number) => void
}) {
  const [studies, setStudies] = useState<Study[]>([])
  const [presets, setPresets] = useState<StudyPreset[]>([])
  const [openId, setOpenId] = useState<number | null>(null)
  const [compare, setCompare] = useState<StudyCompare | null>(null)
  const [busy, setBusy] = useState(false)
  const [creating, setCreating] = useState(false)
  const [question, setQuestion] = useState('')
  const [preset, setPreset] = useState<string>('cost')
  const [addId, setAddId] = useState<number | ''>('')
  const [diffA, setDiffA] = useState<number | ''>('')
  const [diffB, setDiffB] = useState<number | ''>('')
  const [message, setMessage] = useState<string | null>(null)
  const ids = useId()

  const reload = useCallback(async () => {
    try {
      const list = await api.studies(countryId)
      setStudies(list)
      if (openId !== null && !list.some((s) => s.id === openId)) setOpenId(null)
    } catch {
      setStudies([])
    }
  }, [countryId, openId])

  useEffect(() => {
    void reload()
    api
      .studyPresets()
      .then(setPresets)
      .catch(() => setPresets([]))
  }, [reload])

  const loadCompare = useCallback(
    async (id: number) => {
      try {
        setCompare(await api.compareStudy(id))
      } catch (e) {
        onError(String(e))
      }
    },
    [onError],
  )

  useEffect(() => {
    if (openId === null) {
      setCompare(null)
      return
    }
    void loadCompare(openId)
  }, [openId, loadCompare])

  async function create() {
    if (!question.trim()) return
    setBusy(true)
    try {
      const study = await api.createStudy(countryId, { question: question.trim(), preset: preset || null })
      setQuestion('')
      setCreating(false)
      await onScenariosChanged()
      await reload()
      setOpenId(study.id)
      setMessage(
        study.scenarios.length > 1
          ? `“${study.question}” opened with ${study.scenarios.length - 1} scenario${study.scenarios.length === 2 ? '' : 's'} beside the baseline. Run the study to compare them.`
          : `“${study.question}” opened with the baseline. Add scenarios, then run the study.`,
      )
    } catch (e) {
      onError(String(e))
    } finally {
      setBusy(false)
    }
  }

  async function run(id: number) {
    setBusy(true)
    try {
      const outcome = await api.runStudy(id)
      setMessage(`Ran ${outcome.ran} scenario${outcome.ran === 1 ? '' : 's'}.`)
      await onScenariosChanged()
      await loadCompare(id)
      await reload()
    } catch (e) {
      onError(String(e))
    } finally {
      setBusy(false)
    }
  }

  async function recommend(study: Study, scenarioId: number | null) {
    try {
      await api.patchStudy(study.id, { recommended_scenario_id: scenarioId })
      await reload()
      await loadCompare(study.id)
    } catch (e) {
      onError(String(e))
    }
  }

  async function move(study: Study, scenarioId: number, direction: -1 | 1) {
    const order = [...study.scenario_ids]
    const index = order.indexOf(scenarioId)
    const target = index + direction
    if (index < 1 || target < 1 || target >= order.length) return
    ;[order[index], order[target]] = [order[target], order[index]]
    try {
      await api.patchStudy(study.id, { scenario_ids: order })
      await reload()
      await loadCompare(study.id)
    } catch (e) {
      onError(String(e))
    }
  }

  async function add(study: Study) {
    if (addId === '') return
    try {
      await api.addStudyScenario(study.id, addId)
      setAddId('')
      await reload()
      await loadCompare(study.id)
    } catch (e) {
      onError(String(e))
    }
  }

  async function remove(study: Study, scenarioId: number) {
    try {
      await api.removeStudyScenario(study.id, scenarioId)
      await reload()
      await loadCompare(study.id)
    } catch (e) {
      onError(String(e))
    }
  }

  async function removeStudy(study: Study) {
    if (!window.confirm(`Delete the study “${study.question}”? Its scenarios stay.`)) return
    try {
      await api.deleteStudy(study.id)
      if (openId === study.id) setOpenId(null)
      await reload()
    } catch (e) {
      onError(String(e))
    }
  }

  async function showDiff() {
    if (diffA === '' || diffB === '' || diffA === diffB) return
    try {
      onDiff(await api.diffMap(diffA, diffB))
    } catch (e) {
      onError(String(e))
    }
  }

  const open = studies.find((s) => s.id === openId) ?? null

  return (
    <div className="studies">
      <div className="section" data-tour="studies">
        <h3>Studies</h3>
        <p className="lever-note" style={{ marginBottom: 8 }}>
          A study is a question and the scenarios that answer it, the baseline always first. Compare shows
          what differs between them and what that did; the diff map draws two answers as one network.
        </p>
        {!creating ? (
          <button
            type="button"
            className="btn small primary"
            onClick={() => setCreating(true)}
            disabled={busy}
          >
            New study…
          </button>
        ) : (
          <form
            className="session-form"
            onSubmit={(event) => {
              event.preventDefault()
              void create()
            }}
          >
            <label htmlFor={`${ids}-q`} className="visually-hidden">
              The question this study answers
            </label>
            <input
              id={`${ids}-q`}
              className="tag-input"
              value={question}
              autoFocus
              placeholder="The question, e.g. Can we close Wewak?"
              maxLength={240}
              onChange={(event) => setQuestion(event.target.value)}
            />
            <label htmlFor={`${ids}-preset`} className="tiny dim">
              Start from
            </label>
            <select
              id={`${ids}-preset`}
              className="tag-input"
              value={preset}
              onChange={(event) => setPreset(event.target.value)}
            >
              <option value="">The baseline only; I will add scenarios</option>
              {presets.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.label}
                </option>
              ))}
            </select>
            {preset && <div className="lever-note">{presets.find((p) => p.key === preset)?.description}</div>}
            <div className="session-actions">
              <button type="submit" className="btn small primary" disabled={busy || !question.trim()}>
                Start study
              </button>
              <button type="button" className="btn small ghost" onClick={() => setCreating(false)}>
                Cancel
              </button>
            </div>
          </form>
        )}
        {message && (
          <div className="lever-note editor-message" role="status">
            {message}
          </div>
        )}

        {studies.length > 0 && (
          <ul className="session-list" aria-label="Studies">
            {studies.map((study) => (
              <li key={study.id} className={`session-row${study.id === openId ? ' current' : ''}`}>
                <div className="session-name">
                  <button
                    type="button"
                    className="row-select"
                    aria-pressed={study.id === openId}
                    onClick={() => setOpenId(study.id === openId ? null : study.id)}
                  >
                    {study.question}
                  </button>
                </div>
                <div className="row-note">
                  {study.scenarios.length} scenario{study.scenarios.length === 1 ? '' : 's'} ·{' '}
                  {study.scenarios.filter((s) => s.has_result).length} run · {study.author_claim}
                  {study.recommended_scenario_id
                    ? ` · backs “${study.scenarios.find((s) => s.id === study.recommended_scenario_id)?.name ?? ''}”`
                    : ''}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {open && (
        <StudyView
          study={open}
          compare={compare}
          scenarios={scenarios}
          currency={currency}
          busy={busy}
          addId={addId}
          onAddId={setAddId}
          onAdd={() => add(open)}
          onRemove={(id) => remove(open, id)}
          onMove={(id, dir) => move(open, id, dir)}
          onRun={() => run(open.id)}
          onRecommend={(id) => recommend(open, id)}
          onDelete={() => removeStudy(open)}
          onSelectScenario={onSelectScenario}
          diff={diff}
          diffA={diffA}
          diffB={diffB}
          onDiffA={setDiffA}
          onDiffB={setDiffB}
          onShowDiff={showDiff}
          onClearDiff={() => onDiff(null)}
        />
      )}
    </div>
  )
}

function StudyView(props: {
  study: Study
  compare: StudyCompare | null
  scenarios: Scenario[]
  currency: string
  busy: boolean
  addId: number | ''
  onAddId: (id: number | '') => void
  onAdd: () => void
  onRemove: (id: number) => void
  onMove: (id: number, direction: -1 | 1) => void
  onRun: () => void
  onRecommend: (id: number | null) => void
  onDelete: () => void
  onSelectScenario: (id: number) => void
  diff: DiffMap | null
  diffA: number | ''
  diffB: number | ''
  onDiffA: (id: number | '') => void
  onDiffB: (id: number | '') => void
  onShowDiff: () => void
  onClearDiff: () => void
}) {
  const { study, compare, currency } = props
  const ids = useId()
  const inStudy = new Set(study.scenario_ids)
  const candidates = props.scenarios.filter((s) => !inStudy.has(s.id))
  const rows = compare?.rows ?? []
  const headline = ['total_cost', 'fill_rate', 'worst_stratum_fill_rate', 'hubs_open']
  const withResults = study.scenarios.filter((s) => s.has_result)

  return (
    <>
      <div className="section">
        <h3>{study.question}</h3>
        {study.note && <p className="lever-note">{study.note}</p>}
        <div className="session-actions" style={{ marginBottom: 8 }}>
          <button type="button" className="btn small primary" onClick={props.onRun} disabled={props.busy}>
            Run the study
          </button>
          <label htmlFor={`${ids}-add`} className="visually-hidden">
            Scenario to add
          </label>
          <select
            id={`${ids}-add`}
            className="tag-input"
            style={{ maxWidth: 220 }}
            value={props.addId}
            onChange={(event) => props.onAddId(event.target.value === '' ? '' : Number(event.target.value))}
          >
            <option value="">Add a scenario…</option>
            {candidates.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <button type="button" className="btn small" onClick={props.onAdd} disabled={props.addId === ''}>
            Add
          </button>
          {withResults.length > 0 && (
            <a
              className="btn small"
              href={api.studyReportUrl(study.id)}
              target="_blank"
              rel="noreferrer"
              title="The question, the backed option's decision page, the options considered, and the evidence"
            >
              Study report
            </a>
          )}
          <button type="button" className="btn small ghost" onClick={props.onDelete}>
            Delete study
          </button>
        </div>

        <ol className="study-order" aria-label="Scenarios in this study, in order">
          {study.scenarios.map((s, index) => {
            const row = rows.find((r) => r.scenario_id === s.id)
            return (
              <li
                key={s.id}
                className={`study-card${study.recommended_scenario_id === s.id ? ' backed' : ''}`}
              >
                <div className="session-name">
                  <button type="button" className="row-select" onClick={() => props.onSelectScenario(s.id)}>
                    {s.name}
                  </button>
                  {s.is_baseline && <span className="pill info">base</span>}
                  {study.recommended_scenario_id === s.id && <span className="pill good">recommended</span>}
                  {row?.confidence && row.confidence.share > 0 && row.confidence.tested && (
                    <span
                      className={`pill ${row.confidence.holds ? 'good' : 'warn'}`}
                      title={row.confidence.sentence}
                    >
                      {row.confidence.holds ? 'holds ±30%' : 'depends'}
                    </span>
                  )}
                </div>
                {row && row.status === 'ok' ? (
                  <div className="study-kpis">
                    {headline.map((key) => (
                      <span key={key} className="study-kpi">
                        <span className="tiny dim">{shortLabel(key)}</span>{' '}
                        <b>
                          {formatKpi(
                            row.kpi_set[key],
                            compare?.kpi_meta[key]?.unit ?? 'count',
                            key === 'total_cost' ? currency : '',
                          )}
                        </b>
                        {row.comparison?.[key] &&
                          row.comparison[key].delta_pct !== null &&
                          Math.abs(row.comparison[key].delta) > 1e-9 && (
                            <span className={`delta ${row.comparison[key].direction}`}>
                              {' '}
                              {signedPct(row.comparison[key].delta_pct, 1)}
                            </span>
                          )}
                      </span>
                    ))}
                  </div>
                ) : (
                  <div className="row-note">
                    {row?.status === 'not_run' || !row ? 'not run yet' : row.status}
                  </div>
                )}
                {!s.is_baseline && compare?.lever_diff[String(s.id)] && (
                  <div className="row-note">{compare.lever_diff[String(s.id)].sentence}</div>
                )}
                <div className="session-buttons">
                  {!s.is_baseline && (
                    <>
                      <button
                        type="button"
                        className="btn small ghost"
                        onClick={() =>
                          props.onRecommend(study.recommended_scenario_id === s.id ? null : s.id)
                        }
                      >
                        {study.recommended_scenario_id === s.id ? 'Withdraw' : 'Recommend'}
                      </button>
                      <button
                        type="button"
                        className="btn small ghost"
                        onClick={() => props.onMove(s.id, -1)}
                        disabled={index < 2}
                        aria-label={`Move ${s.name} up`}
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        className="btn small ghost"
                        onClick={() => props.onMove(s.id, 1)}
                        disabled={index === study.scenarios.length - 1}
                        aria-label={`Move ${s.name} down`}
                      >
                        ↓
                      </button>
                      <button
                        type="button"
                        className="btn small ghost"
                        onClick={() => props.onRemove(s.id)}
                        aria-label={`Remove ${s.name} from the study`}
                      >
                        Remove
                      </button>
                    </>
                  )}
                </div>
              </li>
            )
          })}
        </ol>
      </div>

      {compare && rows.filter((r) => r.status === 'ok').length >= 2 && (
        <>
          <div className="section">
            <h3>Verdict</h3>
            <div className="callout" role="status">
              {compare.verdict}
            </div>
          </div>

          <div className="section">
            <h3>What differs, and what it did</h3>
            {rows
              .filter((r) => !r.is_baseline && r.status === 'ok')
              .map((r) => {
                const diffs = compare.lever_diff[String(r.scenario_id)]
                const moved = compare.facilities[String(r.scenario_id)]
                return (
                  <div key={r.scenario_id} className="study-diff">
                    <b>{r.name}</b>
                    {diffs && diffs.differences.length > 0 && (
                      <ul className="study-levers">
                        {diffs.differences.map((d) => (
                          <li key={d.group + d.key}>{d.text}</li>
                        ))}
                      </ul>
                    )}
                    {moved && (
                      <div className="row-note">
                        {moved.gained_count} facilities gained supply, {moved.lost_count} lost it
                        {moved.people_lost
                          ? ` (${exact(moved.people_lost)} people move from supplied to not supplied)`
                          : ''}
                        {moved.resupplied_count ? `; ${moved.resupplied_count} changed supplier` : ''}.
                      </div>
                    )}
                    {moved && moved.lost.length > 0 && (
                      <ul className="study-facilities">
                        {moved.lost.slice(0, 5).map((f) => (
                          <li key={f.code}>
                            <span className="delta worse">−</span> {f.name}
                            <span className="dim">
                              {' '}
                              · {f.admin1} · {exact(f.population)} people · {pct(f.fill_before, 0)} →{' '}
                              {pct(f.fill_after, 0)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                    {moved && moved.gained.length > 0 && (
                      <ul className="study-facilities">
                        {moved.gained.slice(0, 5).map((f) => (
                          <li key={f.code}>
                            <span className="delta better">+</span> {f.name}
                            <span className="dim">
                              {' '}
                              · {f.admin1} · {exact(f.population)} people · {pct(f.fill_before, 0)} →{' '}
                              {pct(f.fill_after, 0)}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )
              })}
          </div>

          <div className="section">
            <h3>Who carries each option</h3>
            <div
              className="scroll-x"
              tabIndex={0}
              role="region"
              aria-label="Fill rate by vulnerability band and scenario"
            >
              <table className="range-table">
                <thead>
                  <tr>
                    <th>Band</th>
                    {compare.equity.map((e) => (
                      <th className="n" key={e.scenario_id}>
                        {e.name.length > 22 ? e.name.slice(0, 21) + '…' : e.name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(compare.equity[0]?.strata ?? []).map((stratum, index) => (
                    <tr key={stratum.label}>
                      <td>{stratum.label}</td>
                      {compare.equity.map((e) => (
                        <td className="n" key={e.scenario_id}>
                          {pct(e.strata[index]?.fill_rate, 1)}
                          <span className="dim">
                            {' '}
                            · {money(e.strata[index]?.cost_per_capita, currency)}/head
                          </span>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {withResults.length >= 2 && (
        <div className="section">
          <h3>Diff map</h3>
          <p className="lever-note">
            Two answers as one network: lanes only the first uses are red, only the second green, both grey.
            Facilities that change supplier are amber; that gain supply green; that lose it red.
          </p>
          <div className="session-actions">
            <label htmlFor={`${ids}-a`} className="visually-hidden">
              First scenario
            </label>
            <select
              id={`${ids}-a`}
              className="tag-input"
              style={{ maxWidth: 170 }}
              value={props.diffA}
              onChange={(e) => props.onDiffA(e.target.value === '' ? '' : Number(e.target.value))}
            >
              <option value="">First…</option>
              {withResults.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            <label htmlFor={`${ids}-b`} className="visually-hidden">
              Second scenario
            </label>
            <select
              id={`${ids}-b`}
              className="tag-input"
              style={{ maxWidth: 170 }}
              value={props.diffB}
              onChange={(e) => props.onDiffB(e.target.value === '' ? '' : Number(e.target.value))}
            >
              <option value="">Second…</option>
              {withResults.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn small"
              onClick={props.onShowDiff}
              disabled={props.diffA === '' || props.diffB === '' || props.diffA === props.diffB}
            >
              Show on the map
            </button>
            {props.diff && (
              <button type="button" className="btn small ghost" onClick={props.onClearDiff}>
                Back to the result
              </button>
            )}
          </div>
          {props.diff && (
            <div className="callout" role="status">
              <b>{props.diff.a.scenario_name}</b> → <b>{props.diff.b.scenario_name}</b>:{' '}
              {props.diff.summary.lanes_only_a} lanes only the first uses, {props.diff.summary.lanes_only_b}{' '}
              only the second, {props.diff.summary.lanes_shared} both.{' '}
              {props.diff.summary.facilities_changed_supplier} facilities change supplier,{' '}
              {props.diff.summary.facilities_gained} gain supply, {props.diff.summary.facilities_lost} lose
              it.
              {props.diff.summary.hubs_only_a.length
                ? ` Only the first opens ${props.diff.summary.hubs_only_a.join(', ')}.`
                : ''}
              {props.diff.summary.hubs_only_b.length
                ? ` Only the second opens ${props.diff.summary.hubs_only_b.join(', ')}.`
                : ''}
            </div>
          )}
        </div>
      )}
    </>
  )
}

function shortLabel(key: string): string {
  return (
    { total_cost: 'Cost', fill_rate: 'Fill', worst_stratum_fill_rate: 'Q5 fill', hubs_open: 'Stores' }[key] ??
    key
  )
}
