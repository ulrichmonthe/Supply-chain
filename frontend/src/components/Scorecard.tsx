import type { Scorecard as ScorecardData } from '../types'
import { formatKpi, signedPct } from '../format'

/**
 * The scenario comparison scorecard.
 *
 * Deliberately shows cost and equity side by side and never lets you read one
 * without the other: the cost column and the worst-quintile column sit in the same
 * table, in the same eye movement.
 */
export function Scorecard({
  data,
  currency,
  selectedId,
  onSelect,
}: {
  data: ScorecardData | null
  currency: string
  selectedId: number | null
  onSelect: (id: number) => void
}) {
  if (!data) return <div className="empty">Run a scenario set to build the scorecard.</div>

  const ran = data.rows.filter((row) => row.status === 'ok')
  if (!ran.length) {
    return (
      <div className="empty">
        No scenario has produced a result yet. Select scenarios in the left panel and run them.
      </div>
    )
  }

  const headline = ['total_cost', 'fill_rate', 'worst_stratum_fill_rate', 'mean_stockout_risk', 'cost_per_capita', 'hubs_open']

  return (
    <div>
      <table>
        <thead>
          <tr>
            <th>Scenario</th>
            {headline.map((key) => (
              <th className="n" key={key} title={data.kpi_meta[key]?.label}>
                {shortLabel(key)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row) => (
            <tr
              key={row.scenario_id}
              className={`clickable${row.scenario_id === selectedId ? ' selected' : ''}`}
              onClick={() => onSelect(row.scenario_id)}
            >
              <td>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span>{row.name}</span>
                  {row.is_baseline && <span className="pill info">base</span>}
                </div>
                {row.status !== 'ok' ? (
                  <div className="row-note" style={{ color: 'var(--bad)' }}>
                    {row.status === 'not_run' ? 'not run yet' : row.error ?? row.status}
                  </div>
                ) : (
                  <div className="row-note">
                    {row.month_label} · {row.runtime_ms} ms
                  </div>
                )}
              </td>
              {headline.map((key) => {
                const meta = data.kpi_meta[key]
                const value = row.kpi_set?.[key]
                const comparison = row.comparison?.[key]
                return (
                  <td className="n" key={key}>
                    {value === undefined ? (
                      <span className="dim">—</span>
                    ) : (
                      <>
                        <div>{formatKpi(value, meta?.unit ?? 'count', key === 'total_cost' ? currency : '')}</div>
                        {comparison && comparison.delta_pct !== null && Math.abs(comparison.delta) > 1e-9 && (
                          <div className={`delta ${comparison.direction}`}>
                            {signedPct(comparison.delta_pct, 1)}
                          </div>
                        )}
                      </>
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>

      <div className="callout">
        <h4>How to read this</h4>
        Read the cost column and the <b>Q5 fill</b> column together, never separately. A scenario that
        cuts cost while Q5 falls has not found a saving — it has moved the cost onto the hardest-to-reach
        fifth of the population. Whether that is acceptable is a decision for the ministry and its
        partners; the model's job is to make sure nobody makes it by accident.
      </div>
    </div>
  )
}

function shortLabel(key: string): string {
  return (
    {
      total_cost: 'Cost',
      fill_rate: 'Fill',
      worst_stratum_fill_rate: 'Q5 fill',
      mean_stockout_risk: 'Risk',
      cost_per_capita: 'Per head',
      hubs_open: 'Hubs',
    }[key] ?? key
  )
}
