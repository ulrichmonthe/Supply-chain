import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Scorecard, Study, StudyCompare } from '../types'
import { exact, money, pct, signedPct } from '../format'

/**
 * The Decision view: the same data as the expert screen, one-tenth of the surface.
 *
 * Levers and tabs are hidden. What is left is the map, the study's scenarios as large
 * cards to click between, the three numbers against today, the equity line and the
 * confidence line. It is what a shared, read-only session will open in later, so it
 * is built now as the front door.
 */
export function DecisionView({
  countryId,
  scorecard,
  selectedId,
  onSelect,
  currency,
}: {
  countryId: number
  scorecard: Scorecard | null
  selectedId: number | null
  onSelect: (id: number) => void
  currency: string
}) {
  const [studies, setStudies] = useState<Study[]>([])
  const [studyId, setStudyId] = useState<number | null>(null)
  const [compare, setCompare] = useState<StudyCompare | null>(null)

  useEffect(() => {
    api
      .studies(countryId)
      .then((list) => {
        setStudies(list)
        setStudyId((current) =>
          current !== null && list.some((s) => s.id === current) ? current : (list[0]?.id ?? null),
        )
      })
      .catch(() => setStudies([]))
  }, [countryId])

  useEffect(() => {
    if (studyId === null) {
      setCompare(null)
      return
    }
    api
      .compareStudy(studyId)
      .then(setCompare)
      .catch(() => setCompare(null))
  }, [studyId])

  const study = studies.find((s) => s.id === studyId) ?? null
  const rows =
    compare?.rows ??
    scorecard?.rows.map((r) => ({
      ...r,
      tags: [],
      hubs_open_codes: [],
      confidence: r.confidence ?? null,
      recommended: false,
    })) ??
    []
  const ran = rows.filter((r) => r.status === 'ok')
  const selected = ran.find((r) => r.scenario_id === selectedId) ?? ran[0] ?? null
  const baselineRow = rows.find((r) => r.is_baseline) ?? null
  const equity = compare?.equity.find((e) => e.scenario_id === selected?.scenario_id) ?? null
  const worst = equity?.strata[equity.strata.length - 1] ?? null
  const baseWorst =
    compare?.equity.find((e) => e.scenario_id === baselineRow?.scenario_id)?.strata.slice(-1)[0] ?? null
  const moved = selected && compare ? compare.facilities[String(selected.scenario_id)] : null

  return (
    <aside className="decision" aria-label="Decision view">
      <div className="section">
        {studies.length > 0 ? (
          <>
            <label htmlFor="decision-study" className="tiny dim">
              The question
            </label>
            <select
              id="decision-study"
              className="tag-input decision-question"
              value={studyId ?? ''}
              onChange={(event) => setStudyId(event.target.value === '' ? null : Number(event.target.value))}
            >
              {studies.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.question}
                </option>
              ))}
            </select>
          </>
        ) : (
          <p className="lever-note">
            No study yet. The cards below are every scenario that has been run; start a study in the expert
            view to put a question over them.
          </p>
        )}
        {compare && (
          <p className="decision-verdict" role="status">
            {compare.verdict}
          </p>
        )}
      </div>

      <div className="section">
        <div className="decision-cards" role="group" aria-label="Options">
          {ran.map((row) => (
            <button
              type="button"
              key={row.scenario_id}
              className={`decision-card${row.scenario_id === selected?.scenario_id ? ' on' : ''}${row.recommended ? ' backed' : ''}`}
              aria-pressed={row.scenario_id === selected?.scenario_id}
              onClick={() => onSelect(row.scenario_id)}
            >
              <span className="decision-card-name">
                {row.name}
                {row.is_baseline && <span className="pill info">today</span>}
                {row.recommended && <span className="pill good">recommended</span>}
              </span>
              <span className="decision-card-numbers">
                <span>
                  <b>{money(row.kpi_set.total_cost, currency)}</b>
                  {row.comparison?.total_cost && row.comparison.total_cost.delta_pct !== null && (
                    <span className={`delta ${row.comparison.total_cost.direction}`}>
                      {' '}
                      {signedPct(row.comparison.total_cost.delta_pct, 1)}
                    </span>
                  )}
                </span>
                <span>
                  <b>{pct(row.kpi_set.fill_rate, 1)}</b> <span className="dim">met</span>
                </span>
                <span>
                  <b>{pct(row.kpi_set.worst_stratum_fill_rate, 1)}</b> <span className="dim">Q5</span>
                </span>
              </span>
              {row.confidence && row.confidence.share > 0 && row.confidence.tested && (
                <span className={`row-note ${row.confidence.holds ? 'holds' : 'flips'}`}>
                  {row.confidence.holds
                    ? 'holds with the estimates a third either way'
                    : 'depends on the estimates'}
                </span>
              )}
            </button>
          ))}
          {ran.length === 0 && <p className="lever-note">Nothing has been run yet.</p>}
        </div>
      </div>

      {selected && (
        <div className="section">
          <h3>{selected.name}</h3>
          <div className="kpi-grid decision-three">
            <div className="kpi">
              <span className="kpi-label">Annual cost</span>
              <b>{money(selected.kpi_set.total_cost, currency)}</b>
              {selected.comparison?.total_cost && selected.comparison.total_cost.delta_pct !== null && (
                <span className={`delta ${selected.comparison.total_cost.direction}`}>
                  {signedPct(selected.comparison.total_cost.delta_pct, 1)} vs today
                </span>
              )}
            </div>
            <div className="kpi">
              <span className="kpi-label">Demand met</span>
              <b>{pct(selected.kpi_set.fill_rate, 1)}</b>
              {selected.comparison?.fill_rate && selected.comparison.fill_rate.delta_pct !== null && (
                <span className={`delta ${selected.comparison.fill_rate.direction}`}>
                  {signedPct(selected.comparison.fill_rate.delta_pct, 1)} vs today
                </span>
              )}
            </div>
            <div className="kpi">
              <span className="kpi-label">
                {selected.is_baseline ? 'People supplied in full' : 'People no longer supplied in full'}
              </span>
              <b>
                {selected.is_baseline
                  ? exact(selected.kpi_set.population_served)
                  : moved
                    ? moved.people_lost
                      ? `−${exact(moved.people_lost)}`
                      : 'none'
                    : '—'}
              </b>
            </div>
          </div>
          {worst && (
            <p className="decision-line">
              <b>Who carries it.</b> The most vulnerable fifth, {exact(worst.population)} people, gets{' '}
              {pct(worst.fill_rate, 1)} of its demand
              {baseWorst && !selected.is_baseline ? ` (today: ${pct(baseWorst.fill_rate, 1)})` : ''}.
            </p>
          )}
          {selected.confidence && (
            <p
              className={`decision-line ${selected.confidence.holds ? 'good' : selected.confidence.holds === false ? 'warn' : ''}`}
            >
              <b>How sure this is.</b> {selected.confidence.sentence}
            </p>
          )}
          <div className="session-actions" style={{ marginTop: 10 }}>
            <a
              className="btn small primary"
              href={api.reportUrl(selected.scenario_id)}
              target="_blank"
              rel="noreferrer"
            >
              Report for this option
            </a>
            {study && (
              <a className="btn small" href={api.studyReportUrl(study.id)} target="_blank" rel="noreferrer">
                Study report
              </a>
            )}
          </div>
        </div>
      )}
    </aside>
  )
}
