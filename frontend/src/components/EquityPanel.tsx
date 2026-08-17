import type { Result, ScorecardRow } from '../types'
import { exact, money, pct, signedPct } from '../format'

/**
 * Demo moment four: "who pays for the savings?"
 *
 * The panel exists to make one thing impossible to miss — when total cost falls,
 * where the fall came from. It always renders the baseline alongside the scenario,
 * because a quintile fill rate on its own means nothing.
 */
export function EquityPanel({
  result,
  baselineRow,
  currency,
  equityDefinition,
}: {
  result: Result | null
  baselineRow: ScorecardRow | null
  currency: string
  equityDefinition?: string
}) {
  if (!result || result.status !== 'ok') {
    return <div className="empty">Run this scenario to see who it reaches.</div>
  }

  const strata = result.equity_detail?.strata ?? []
  const baselineStrata = baselineRow?.equity ?? []
  const isBaseline = baselineRow?.scenario_id === result.scenario_id

  const costDelta =
    baselineRow && !isBaseline && baselineRow.kpi_set?.total_cost
      ? result.kpi_set.total_cost / baselineRow.kpi_set.total_cost - 1
      : null

  const worstDelta =
    baselineRow && !isBaseline && baselineRow.kpi_set?.worst_stratum_fill_rate !== undefined
      ? result.kpi_set.worst_stratum_fill_rate - baselineRow.kpi_set.worst_stratum_fill_rate
      : null

  const populationLost = strata.reduce((total, stratum, index) => {
    const base = baselineStrata[index]
    if (!base) return total
    return total + Math.max(0, base.fill_rate - stratum.fill_rate) * stratum.population
  }, 0)

  return (
    <div>
      {costDelta !== null && worstDelta !== null && (
        <div
          className={`callout ${costDelta < -0.005 && worstDelta < -0.005 ? 'bad' : worstDelta >= -0.005 ? 'good' : 'equity'}`}
        >
          <h4>
            {costDelta < -0.005 && worstDelta < -0.005
              ? 'This saving is being funded by the most vulnerable quintile'
              : costDelta < -0.005
                ? 'This saving does not come out of the most vulnerable quintile'
                : 'Cost and equity effect'}
          </h4>
          Total cost moves <b>{signedPct(costDelta)}</b> against the baseline, and the fill rate for the
          most vulnerable fifth of the population moves{' '}
          <b>{signedPct(worstDelta, 1).replace('%', ' points')}</b>
          {populationLost > 1000 && (
            <>
              . On these figures <b>{exact(populationLost)}</b> people move from supplied to not
              supplied
            </>
          )}
          .
        </div>
      )}

      <div className="equity-strata">
        {strata.map((stratum, index) => {
          const base = baselineStrata[index]
          const delta = base ? stratum.fill_rate - base.fill_rate : null
          return (
            <div className="stratum" key={stratum.index}>
              <div className="stratum-head">
                <b>{stratum.label}</b>
                <span className="num" style={{ color: delta && delta < -0.005 ? 'var(--bad)' : undefined }}>
                  {pct(stratum.fill_rate)}
                  {delta !== null && Math.abs(delta) > 0.005 && (
                    <span className={`delta ${delta < 0 ? 'worse' : 'better'}`} style={{ marginLeft: 6 }}>
                      {signedPct(delta, 1)}
                    </span>
                  )}
                </span>
              </div>
              <div className="bar-track">
                <div
                  className="bar-fill"
                  style={{
                    width: `${Math.max(1, stratum.fill_rate * 100)}%`,
                    background: `linear-gradient(90deg, #4da3ff, ${stratum.fill_rate > 0.95 ? '#3fb984' : '#e8b23a'})`,
                  }}
                />
                {base && delta !== null && delta < -0.005 && (
                  <div
                    style={{
                      marginTop: -5,
                      height: 5,
                      width: `${base.fill_rate * 100}%`,
                      borderRight: '2px solid rgba(255,255,255,0.55)',
                    }}
                    title={`Baseline: ${pct(base.fill_rate)}`}
                  />
                )}
              </div>
              <div className="stratum-meta">
                <div>
                  <b>{exact(stratum.population)}</b> people
                </div>
                <div>
                  <b>{stratum.facilities}</b> facilities
                </div>
                <div>
                  <b>{money(stratum.cost_per_capita, currency)}</b> per head
                </div>
                <div>
                  vulnerability <b>{stratum.mean_vulnerability.toFixed(2)}</b>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      <div className="callout">
        <h4>How the quintiles are built</h4>
        {equityDefinition ??
          'Population-weighted quintiles of a structural vulnerability index.'}{' '}
        Each band holds roughly a fifth of the <i>people</i>, not a fifth of the facilities. The index is
        computed from network structure before any optimisation runs, so it cannot be gamed by the
        solution it is used to judge.
      </div>
    </div>
  )
}
