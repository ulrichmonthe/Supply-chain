import type {
  ConfidenceAssessment,
  Scorecard as ScorecardData,
} from "../types";
import { exact, formatKpi, money, pct, signedPct } from "../format";

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
  data: ScorecardData | null;
  currency: string;
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  if (!data)
    return (
      <div className="empty">Run a scenario set to build the scorecard.</div>
    );

  const ran = data.rows.filter((row) => row.status === "ok");
  if (!ran.length) {
    return (
      <div className="empty">
        No scenario has produced a result yet. Select scenarios in the left
        panel and run them.
      </div>
    );
  }

  const headline = [
    "total_cost",
    "fill_rate",
    "worst_stratum_fill_rate",
    "mean_stockout_risk",
    "cost_per_capita",
    "hubs_open",
  ];

  return (
    <div>
      <div className="scroll-x">
        <table>
          <thead>
            <tr>
              <th>Scenario</th>
              {headline.map((key) => (
                <th className="n" key={key} title={data.kpi_meta[key]?.label}>
                  {shortLabel(key)}
                </th>
              ))}
              <th
                className="n"
                title="Share of the demand this was solved on that is an estimate from a rule, and whether the answer holds with every estimate 30% lower or higher"
              >
                Estimated
              </th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => (
              <tr
                key={row.scenario_id}
                className={`clickable${row.scenario_id === selectedId ? " selected" : ""}`}
                onClick={() => onSelect(row.scenario_id)}
              >
                <td>
                  <div
                    style={{ display: "flex", alignItems: "center", gap: 6 }}
                  >
                    <span>{row.name}</span>
                    {row.is_baseline && <span className="pill info">base</span>}
                  </div>
                  {row.status !== "ok" ? (
                    <div className="row-note" style={{ color: "var(--bad)" }}>
                      {row.status === "not_run"
                        ? "not run yet"
                        : (row.error ?? row.status)}
                    </div>
                  ) : (
                    <div className="row-note">
                      {row.month_label} · {row.runtime_ms} ms
                    </div>
                  )}
                </td>
                {headline.map((key) => {
                  const meta = data.kpi_meta[key];
                  const value = row.kpi_set?.[key];
                  const comparison = row.comparison?.[key];
                  return (
                    <td className="n" key={key}>
                      {value === undefined ? (
                        <span className="dim">—</span>
                      ) : (
                        <>
                          <div>
                            {formatKpi(
                              value,
                              meta?.unit ?? "count",
                              key === "total_cost" ? currency : "",
                            )}
                          </div>
                          {comparison &&
                            comparison.delta_pct !== null &&
                            Math.abs(comparison.delta) > 1e-9 && (
                              <div className={`delta ${comparison.direction}`}>
                                {signedPct(comparison.delta_pct, 1)}
                              </div>
                            )}
                        </>
                      )}
                    </td>
                  );
                })}
                <td className="n confidence-cell">
                  <ConfidenceCell assessment={row.confidence} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ConfidenceNote
        row={data.rows.find((row) => row.scenario_id === selectedId) ?? null}
        currency={currency}
      />

      <div className="callout">
        <h4>How to read this</h4>
        Read the cost column and the <b>Q5 fill</b> column together, never
        separately. A scenario that cuts cost while Q5 falls has not found a
        saving — it has moved the cost onto the hardest-to-reach fifth of the
        population. Whether that is acceptable is a decision for the ministry
        and its partners; the model's job is to make sure nobody makes it by
        accident.
      </div>
    </div>
  );
}

function shortLabel(key: string): string {
  return (
    {
      total_cost: "Cost",
      fill_rate: "Fill",
      worst_stratum_fill_rate: "Q5 fill",
      mean_stockout_risk: "Risk",
      cost_per_capita: "Per head",
      hubs_open: "Hubs",
    }[key] ?? key
  );
}

function ConfidenceCell({ assessment }: { assessment?: ConfidenceAssessment }) {
  if (!assessment) return <span className="dim">—</span>;
  if (assessment.share <= 0)
    return <span title={assessment.sentence}>none</span>;
  return (
    <>
      <div>{pct(assessment.share, 0)}</div>
      {assessment.tested && assessment.holds !== null && (
        <div
          className={`row-note ${assessment.holds ? "holds" : "flips"}`}
          title={assessment.sentence}
        >
          {assessment.holds ? "holds ±30%" : "depends"}
        </div>
      )}
    </>
  );
}

/**
 * The confidence budget for the selected scenario, in words and as a range.
 *
 * "This holds even if our demand guess is a third off" is the sentence a minister
 * needs; "it flips if demand is 30% higher" is the one an analyst needs before the
 * minister asks. Either way it comes from re-solving, not from a caveat.
 */
function ConfidenceNote({
  row,
  currency,
}: {
  row: ScorecardData["rows"][number] | null;
  currency: string;
}) {
  const assessment = row?.confidence;
  if (!row || !assessment || row.status !== "ok") return null;
  const swing = assessment.swing ?? 0.3;
  const tone =
    assessment.share <= 0 || assessment.holds
      ? "good"
      : assessment.holds === false
        ? "bad"
        : "";
  const rows: [string, string, (v: number) => string][] = [
    ["total_cost", "Annual cost", (v) => money(v, currency)],
    ["fill_rate", "Demand met", (v) => pct(v, 1)],
    ["worst_stratum_fill_rate", "Q5 fill", (v) => pct(v, 1)],
    ["mean_stockout_risk", "Mean stockout risk", (v) => pct(v, 1)],
    ["hubs_open", "Stores open", (v) => exact(v)],
  ];
  return (
    <div
      className={`callout confidence ${tone}`}
      role="status"
      data-tour="confidence"
    >
      <h4>How sure this is · {row.name}</h4>
      <p className="sentence">{assessment.sentence}</p>
      {assessment.changes.length > 1 && (
        <ul>
          {assessment.changes.slice(1).map((change) => (
            <li key={change}>{change}</li>
          ))}
        </ul>
      )}
      {assessment.notes.length > 0 && (
        <ul>
          {assessment.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
      {assessment.tested && (
        <table className="range-table">
          <thead>
            <tr>
              <th>Figure</th>
              <th className="n">Estimates {pct(swing, 0)} lower</th>
              <th className="n">As modelled</th>
              <th className="n">Estimates {pct(swing, 0)} higher</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([key, label, format]) => {
              const values = assessment.range[key];
              if (!values) return null;
              return (
                <tr key={key}>
                  <td>{label}</td>
                  {values.map((value, index) => (
                    <td
                      className={`n${index === 1 ? " modelled" : ""}`}
                      key={index}
                    >
                      {value === null ? "not feasible" : format(value)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {assessment.tested &&
        assessment.baseline_tested === false &&
        !row.is_baseline && (
          <p className="row-note" style={{ marginTop: 6 }}>
            Today's network has not been re-run since the estimates changed, so
            the comparison at each end is not yet made. Run the baseline again.
          </p>
        )}
    </div>
  );
}
