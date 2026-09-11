"""A scenario as a document, for people who will never open the tool.

The spreadsheet export is for someone who wants to check the arithmetic. This is for
the director who has to decide, and the reviewer who has to appraise it — neither of
whom will sort a pivot table. It is one self-contained HTML file with no external
requests, which means it opens on any machine, survives being emailed, and prints to
PDF from any browser without a print server or a template engine.

The order is deliberate. The recommendation, then what it costs, then who pays for it,
then what we are not sure about. A report that buries the equity consequence behind
four pages of methodology is a report that gets signed without anyone reading it.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Optional


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def _money(value: Optional[float], currency: str) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M {currency}"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.0f}k {currency}"
    if abs(value) >= 100:
        return f"{value:,.0f} {currency}"
    # Cost per person is a small number where the decimals are the whole point.
    return f"{value:,.2f} {currency}"


def _pct(value: Optional[float], places: int = 1) -> str:
    return "—" if value is None else f"{value * 100:.{places}f}%"


def _signed(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{value * 100:+.1f}%"


def _magnitude(value: Optional[float]) -> str:
    """The size of a change without its sign, for sentences that carry the direction."""
    return "" if value is None else f"{abs(value) * 100:.1f}%"


def _delta_class(value: Optional[float], good_when_negative: bool) -> str:
    if value is None or abs(value) < 1e-9:
        return "flat"
    improving = value < 0 if good_when_negative else value > 0
    return "good" if improving else "bad"


def _exact(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:,.0f}"


def render_report(
    *,
    country,
    scenario,
    result,
    baseline_scenario=None,
    baseline_result=None,
    roadmap: Optional[dict] = None,
    provenance: Optional[dict] = None,
    counts: Optional[dict] = None,
) -> str:
    currency = country.currency or "USD"
    kpi = result.kpi_set or {}
    base_kpi = (baseline_result.kpi_set or {}) if baseline_result else {}
    equity = result.equity_detail or {}
    log = result.solver_log or {}
    month_label = log.get("month_label") or "Annualised"
    provenance = provenance or {}
    counts = counts or {}

    def change(key: str) -> Optional[float]:
        here, there = kpi.get(key), base_kpi.get(key)
        if here is None or not there:
            return None
        return (here - there) / abs(there)

    cost_change = change("total_cost")
    q5_change = change("worst_stratum_fill_rate")
    is_baseline = bool(getattr(scenario, "is_baseline", False))

    # --- the sentence the whole report exists to deliver ----------------------------
    if is_baseline:
        verdict = (
            f"This is the network as it runs today, held at {_e(month_label).lower()} conditions. "
            f"It costs {_money(kpi.get('total_cost'), currency)} a year and reaches "
            f"{_pct(kpi.get('fill_rate'))} of demand by volume."
        )
        verdict_tone = "neutral"
    elif cost_change is not None and cost_change < -0.001 and q5_change is not None and q5_change < -0.001:
        lost = None
        if baseline_result:
            lost = _people_dropped(baseline_result, result)
        who = f" {_exact(lost)} people move from supplied to not supplied." if lost else ""
        verdict = (
            f"This option cuts the cost of the network by {_magnitude(cost_change)} — and the "
            f"saving is funded by the most vulnerable fifth of the population, whose supply "
            f"falls by {_magnitude(q5_change)}.{who}"
        )
        verdict_tone = "warn"
    elif cost_change is not None and cost_change < -0.001:
        verdict = (
            f"This option cuts the cost of the network by {_magnitude(cost_change)} without "
            f"reducing supply to the most vulnerable fifth."
        )
        verdict_tone = "good"
    elif cost_change is not None and cost_change > 0.001:
        verdict = (
            f"This option costs {_magnitude(cost_change)} more than the current network. "
            f"What that buys is in the comparison below."
        )
        verdict_tone = "neutral"
    else:
        verdict = (
            f"This option costs about the same as the current network, at "
            f"{_money(kpi.get('total_cost'), currency)} a year."
        )
        verdict_tone = "neutral"

    # --- headline figures -----------------------------------------------------------
    headline = [
        ("Total annual cost", _money(kpi.get("total_cost"), currency), cost_change, True),
        ("Demand met, by volume", _pct(kpi.get("fill_rate")), change("fill_rate"), False),
        ("Supply to the most vulnerable fifth", _pct(kpi.get("worst_stratum_fill_rate")), q5_change, False),
        ("Cost per person reached", _money(kpi.get("cost_per_capita"), currency), change("cost_per_capita"), True),
    ]
    headline_html = "".join(
        f'''<div class="fig">
              <span class="fig-label">{_e(label)}</span>
              <b>{_e(value)}</b>
              <span class="delta {_delta_class(delta, good)}">{_e(_signed(delta))}{
                  " vs today" if delta is not None and abs(delta) > 1e-9 else ""}</span>
            </div>'''
        for label, value, delta, good in headline
    )

    # --- who carries it -------------------------------------------------------------
    strata_rows = "".join(
        f'''<tr>
              <td>{_e(s.get("label"))}</td>
              <td class="n">{_exact(s.get("facilities"))}</td>
              <td class="n">{_exact(s.get("population"))}</td>
              <td class="n">{_pct(s.get("fill_rate"))}</td>
              <td class="n">{_e(f"{s.get('cost_per_capita', 0):.2f}")}</td>
            </tr>'''
        for s in equity.get("strata", [])
    )

    # --- facilities that lose out ---------------------------------------------------
    detail = sorted(
        (n for n in (result.per_node_detail or []) if (n.get("fill_rate") or 0) < 0.999),
        key=lambda n: (n.get("fill_rate") or 0, -(n.get("population") or 0)),
    )
    short_rows = "".join(
        f'''<tr>
              <td>{_e(n.get("name"))}<span class="sub">{_e(n.get("admin1"))}</span></td>
              <td class="n">{_exact(n.get("population"))}</td>
              <td class="n">{_pct(n.get("fill_rate"))}</td>
              <td class="n">{_pct(n.get("stockout_risk"))}</td>
              <td>{_e(n.get("served_by", [{}])[0].get("hub_name") if n.get("served_by") else "nothing")}</td>
            </tr>'''
        for n in detail[:25]
    )
    short_note = (
        f"<p class='note'>Showing the 25 worst of {len(detail)}. The full list is in the "
        f"spreadsheet export.</p>" if len(detail) > 25 else ""
    )

    # --- roadmap --------------------------------------------------------------------
    roadmap_html = ""
    # A phase lists step ids; the steps themselves sit at the top level.
    by_id = {step.get("id"): step for step in ((roadmap or {}).get("steps") or [])}
    filled = []
    for phase in ((roadmap or {}).get("phases") or []):
        steps = [by_id[i] for i in (phase.get("steps") or []) if i in by_id]
        if steps:
            filled.append((phase, steps))
    if filled:
        phases = "".join(
            f'''<div class="phase">
                  <h3>{_e(phase.get("label"))}</h3>
                  <p class="note">{_e(phase.get("window") or "")}
                     · {_money(phase.get("one_off_cost"), currency)} one-off</p>
                  <ul>{"".join(_step_li(step, currency) for step in steps)}</ul>
                </div>'''
            for phase, steps in filled
        )
        roadmap_html = f'''<section><h2>What it would take to get there</h2>
            <p class="lede">Phased against how the network runs today. One-off costs are shown
            separately from the yearly running costs above, so the plan cannot look cheaper
            than it is.</p>
            <div class="phases">{phases}</div></section>'''
    elif roadmap:
        roadmap_html = '''<section><h2>What it would take to get there</h2>
            <p class="lede">Nothing to build. This option changes how the existing network is
            used rather than what it is made of, so there are no stores to open, close or
            re-equip.</p></section>'''

    # --- what we are not certain about ----------------------------------------------
    pcounts = (provenance.get("counts") or {})
    total_edges = provenance.get("total_edges") or sum(pcounts.values()) or 0
    soft = pcounts.get("detour_factor", 0)
    confidence = provenance.get("km_weighted_confidence")
    caveats = []
    if total_edges and soft:
        caveats.append(
            f"{soft} of {total_edges} routes have an estimated distance rather than a measured "
            f"or routed one. Every distance in the model carries the method that produced it and "
            f"a confidence score; the weighted average is "
            f"{_e(f'{confidence:.2f}') if confidence is not None else 'recorded per route'}."
        )
    unreachable = int(kpi.get("facilities_unreachable") or 0)
    if unreachable:
        caveats.append(
            f"{_exact(unreachable)} {'facility' if unreachable == 1 else 'facilities'} cannot be "
            f"reached at all under {_e(month_label).lower()} conditions."
        )
    if log.get("lanes_dropped_by_season"):
        caveats.append(
            f"{_exact(log.get('lanes_dropped_by_season'))} "
            f"{'route is' if log.get('lanes_dropped_by_season') == 1 else 'routes are'} closed in this "
            f"month and were excluded from the plan."
        )
    caveats.append(
        "Seasonal access is drawn from typical conditions, not from records of routes actually "
        "closing. Worth checking with provincial staff before this is published."
    )
    caveats_html = "".join(f"<li>{c}</li>" for c in caveats)

    generated = datetime.now(timezone.utc).strftime("%d %B %Y")
    base_name = getattr(baseline_scenario, "name", None) or "the current network"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{_e(scenario.name)} — {_e(country.name)}</title>
<style>
  :root {{
    --ink: #17202a; --muted: #55626f; --faint: #7d8792;
    --rule: #d9dee3; --rule-soft: #eaedf0;
    --accent: #0e5f70; --accent-soft: #e6f0f2;
    --good: #2e7355; --bad: #9e4038; --warn-bg: #fdf3e2; --warn-line: #e6ce9f;
    --paper: #ffffff;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: #f2f4f5; color: var(--ink);
    font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  .sheet {{
    max-width: 900px; margin: 0 auto; background: var(--paper);
    padding: 54px 58px 70px; min-height: 100vh;
  }}
  header.doc {{ border-bottom: 2px solid var(--ink); padding-bottom: 18px; }}
  .eyebrow {{
    font-size: 11px; font-weight: 700; letter-spacing: 0.12em;
    text-transform: uppercase; color: var(--accent); margin: 0 0 10px;
  }}
  h1 {{ font-size: 30px; line-height: 1.15; letter-spacing: -0.02em; margin: 0 0 8px; }}
  .subtitle {{ color: var(--muted); margin: 0; font-size: 15.5px; }}
  .meta {{
    margin-top: 14px; font-size: 12.5px; color: var(--faint);
    display: flex; flex-wrap: wrap; gap: 18px;
  }}

  .verdict {{
    margin: 26px 0 0; padding: 20px 24px; border-radius: 6px;
    background: var(--accent-soft); border-left: 4px solid var(--accent);
    font-size: 18px; line-height: 1.45;
  }}
  .verdict.warn {{ background: var(--warn-bg); border-left-color: #b0620f; }}
  .verdict.good {{ background: #e9f2ec; border-left-color: var(--good); }}

  .figures {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1px; background: var(--rule); border: 1px solid var(--rule);
    margin: 28px 0 0; border-radius: 4px; overflow: hidden;
  }}
  .fig {{ background: var(--paper); padding: 15px 17px; }}
  .fig-label {{ display: block; font-size: 11.5px; color: var(--muted); margin-bottom: 5px; line-height: 1.35; }}
  .fig b {{ display: block; font-size: 22px; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }}
  .delta {{ display: block; font-size: 12px; margin-top: 3px; font-variant-numeric: tabular-nums; }}
  .delta.good {{ color: var(--good); }}
  .delta.bad {{ color: var(--bad); }}
  .delta.flat {{ color: var(--faint); }}

  section {{ margin-top: 42px; }}
  h2 {{ font-size: 19px; letter-spacing: -0.01em; margin: 0 0 6px; }}
  .lede {{ color: var(--muted); margin: 0 0 16px; max-width: 66ch; }}
  .note {{ color: var(--faint); font-size: 13px; margin: 10px 0 0; }}

  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th {{
    text-align: left; font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--faint); border-bottom: 1px solid var(--ink); padding: 8px 10px; font-weight: 700;
  }}
  td {{ padding: 9px 10px; border-bottom: 1px solid var(--rule-soft); vertical-align: top; }}
  td.n, th.n {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  td .sub {{ display: block; color: var(--faint); font-size: 12px; margin-top: 2px; }}
  .table-wrap {{ overflow-x: auto; }}

  .phases {{ display: grid; gap: 14px; }}
  .phase {{ border: 1px solid var(--rule); border-left: 3px solid var(--accent); border-radius: 4px; padding: 14px 18px; }}
  .phase h3 {{ margin: 0 0 4px; font-size: 15px; }}
  .phase ul {{ margin: 10px 0 0; padding-left: 18px; }}
  .phase li {{ margin-bottom: 10px; }}
  .phase li .sub {{ display: block; color: var(--muted); font-size: 13px; margin-top: 3px; }}

  .caveats {{ background: #fafbfb; border: 1px solid var(--rule); border-radius: 6px; padding: 18px 24px; }}
  .caveats ul {{ margin: 0; padding-left: 18px; }}
  .caveats li {{ margin-bottom: 8px; color: var(--muted); }}
  .caveats li:last-child {{ margin-bottom: 0; }}

  footer.doc {{
    margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--rule);
    font-size: 12px; color: var(--faint);
  }}

  @media print {{
    body {{ background: var(--paper); }}
    .sheet {{ max-width: none; padding: 0; min-height: 0; }}
    section {{ break-inside: avoid; }}
    .verdict, .figures, .phase {{ break-inside: avoid; }}
  }}
</style>
</head>
<body>
<div class="sheet">

  <header class="doc">
    <p class="eyebrow">Network design · {_e(country.name)}</p>
    <h1>{_e(scenario.name)}</h1>
    <p class="subtitle">{_e(scenario.description or "")}</p>
    <div class="meta">
      <span>Compared against: {_e(base_name)}</span>
      <span>Conditions: {_e(month_label)}</span>
      <span>Generated {_e(generated)}</span>
    </div>
  </header>

  <div class="verdict {verdict_tone}">{verdict}</div>

  <div class="figures">{headline_html}</div>

  <section>
    <h2>Who carries this plan</h2>
    <p class="lede">
      Every facility is ranked by how hard it is to reach, then grouped into five bands of
      roughly equal population. Reading cost and the bottom band together is the point: a
      plan that saves money while the bottom band falls has moved the cost onto the
      hardest-to-reach, rather than removed it.
    </p>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Group</th><th class="n">Facilities</th><th class="n">People</th>
          <th class="n">Demand met</th><th class="n">Cost per person</th>
        </tr></thead>
        <tbody>{strata_rows}</tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>Facilities that would not be fully supplied</h2>
    <p class="lede">
      Named, because a percentage is not a decision. Ordered by how badly they are served,
      then by how many people they cover.
    </p>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Facility</th><th class="n">People</th><th class="n">Demand met</th>
          <th class="n">Stockout risk</th><th>Supplied by</th>
        </tr></thead>
        <tbody>{short_rows or '<tr><td colspan="5">Every facility is fully supplied under this plan.</td></tr>'}</tbody>
      </table>
    </div>
    {short_note}
  </section>

  {roadmap_html}

  <section>
    <h2>What this rests on</h2>
    <p class="lede">
      Stated up front rather than in an appendix, because a reviewer will look for it and
      a decision-maker deserves it.
    </p>
    <div class="caveats"><ul>{caveats_html}</ul></div>
  </section>

  <footer class="doc">
    {_e(counts.get("facilities") or "")} facilities ·
    {_e(counts.get("scheduled_services") or "")} timetabled services ·
    solved in {_e(result.runtime_ms)} ms.
    Figures are model output, not measurements. The full dataset, the assumption log and
    every distance with its method and confidence are in the spreadsheet export.
  </footer>

</div>
</body>
</html>"""


def _step_li(step: dict, currency: str) -> str:
    """One roadmap step: what to do, why, what it costs once and what it costs every
    year after that. The two costs stay separate so a plan cannot look cheaper than it
    is."""
    bits = [f"<b>{_e(step.get('title') or '')}</b>"]
    if step.get("detail"):
        bits.append(f"<span class='sub'>{_e(step['detail'])}</span>")
    money_parts = []
    if step.get("one_off_cost"):
        money_parts.append(f"{_money(step['one_off_cost'], currency)} one-off")
    if step.get("annual_cost_delta"):
        money_parts.append(f"{_money(step['annual_cost_delta'], currency)} a year")
    if step.get("owner"):
        money_parts.append(_e(step["owner"]))
    if money_parts:
        bits.append(f"<span class='sub'>{' · '.join(money_parts)}</span>")
    return f"<li>{''.join(bits)}</li>"


def _people_dropped(baseline_result, result) -> Optional[int]:
    """People who were supplied in the baseline and are not supplied here.

    Counted at the facility level rather than scaled from a percentage, so the number
    can be defended facility by facility if somebody asks.
    """
    before = {n["code"]: n for n in (baseline_result.per_node_detail or [])}
    total = 0
    for node in result.per_node_detail or []:
        was = before.get(node.get("code"))
        if not was:
            continue
        if (was.get("fill_rate") or 0) >= 0.999 and (node.get("fill_rate") or 0) < 0.999:
            total += int(node.get("population") or 0)
    return total or None
