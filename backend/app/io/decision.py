"""The decision page: page one of every report, and the study report built around it.

One page, in this order: the recommendation in a sentence, a map with the recommended
stores marked, a table of the stores with their capacity and the facilities each
serves, three numbers against today (annual cost, demand met, people newly reached or
lost), the equity line, and the confidence line. Every number links into the evidence
that follows, so a decision-maker who wants to check can, and the analyst who is asked
"where did that come from" points.

The map is inline SVG drawn from the coordinates already in the result, so the report
stays one self-contained file that survives being emailed and opens with no internet.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Iterable, List, Optional

from ..engine import confidence as confidence_mod


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def _money(value: Optional[float], currency: str) -> str:
    if value is None:
        return "—"
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.2f}M {currency}"
    if absolute >= 1_000:
        return f"{value / 1_000:.0f}k {currency}"
    return f"{value:,.0f} {currency}"


def _pct(value: Optional[float], places: int = 1) -> str:
    return "—" if value is None else f"{value * 100:.{places}f}%"


def _exact(value) -> str:
    try:
        return f"{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return "—"


def _signed_pct(value: Optional[float]) -> str:
    return "" if value is None else f"{value * 100:+.1f}%"


# --- people -------------------------------------------------------------------------


def people_moved(baseline_result, result) -> dict:
    """People newly reached, and people no longer supplied, facility by facility."""
    if not baseline_result:
        return {"gained": 0, "lost": 0}
    before = {n["code"]: n for n in (baseline_result.per_node_detail or [])}
    gained = lost = 0
    for node in result.per_node_detail or []:
        was = before.get(node.get("code"))
        if not was:
            continue
        then, now = (was.get("fill_rate") or 0) >= 0.999, (node.get("fill_rate") or 0) >= 0.999
        if then and not now:
            lost += int(node.get("population") or 0)
        elif now and not then:
            gained += int(node.get("population") or 0)
    return {"gained": gained, "lost": lost}


# --- the stores ----------------------------------------------------------------------


def stores_table(result, nodes_by_code: dict) -> List[dict]:
    """Every store the plan opens: capacity by band, facilities served, volume through it."""
    open_codes = list((result.solver_log or {}).get("hubs_open_codes") or [])
    served: dict = {}
    volume: dict = {}
    for node in result.per_node_detail or []:
        for leg in node.get("served_by") or []:
            served.setdefault(leg.get("hub_code"), set()).add(node.get("code"))
            volume[leg.get("hub_code")] = volume.get(leg.get("hub_code"), 0.0) + float(leg.get("volume_m3") or 0.0)
    rows = []
    for code in open_codes:
        node = nodes_by_code.get(code)
        capacity = (getattr(node, "capacity", None) or {}) if node else {}
        cold = capacity.get("cold_by_band") or {}
        rows.append(
            {
                "code": code,
                "name": getattr(node, "name", code),
                "admin1": getattr(node, "admin1", None),
                "status": getattr(node, "operating_status", None),
                "proposed": (getattr(node, "operating_status", "operational") != "operational") if node else False,
                "dry_m3": capacity.get("dry_m3"),
                "cold_m3": cold.get("+2-8"),
                "frozen_m3": cold.get("-20"),
                "throughput_m3": getattr(node, "hub_throughput_m3", None),
                "facilities": len(served.get(code, ())),
                "volume_m3": round(volume.get(code, 0.0), 1),
            }
        )
    return rows


# --- the map --------------------------------------------------------------------------


def map_thumbnail(result, nodes: Iterable, boundary: Optional[dict], width: int = 760, height: int = 420) -> str:
    """The plan as a picture: land, the lanes the plan uses, facilities coloured by how
    well they are supplied, and the stores it opens, named."""
    detail = {n["code"]: n for n in (result.per_node_detail or [])}
    points = [(float(n.lat), float(n.lon)) for n in nodes if n.lat is not None and n.lon is not None]
    if not points:
        return ""
    lats, lons = [p[0] for p in points], [p[1] for p in points]
    pad_lat, pad_lon = (max(lats) - min(lats) or 1) * 0.08, (max(lons) - min(lons) or 1) * 0.08
    lat0, lat1 = min(lats) - pad_lat, max(lats) + pad_lat
    lon0, lon1 = min(lons) - pad_lon, max(lons) + pad_lon

    def xy(lat: float, lon: float) -> tuple:
        x = (lon - lon0) / (lon1 - lon0) * width
        y = (lat1 - lat) / (lat1 - lat0) * height
        return round(x, 1), round(y, 1)

    parts = [
        f'<svg class="map" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
        f'aria-label="Map of the plan: facilities coloured by how well they are supplied, stores named">'
    ]
    parts.append(f'<rect width="{width}" height="{height}" fill="#eef3f6"/>')
    for ring in (boundary or {}).get("polygons") or []:
        if len(ring) < 3:
            continue
        coords = " ".join(f"{xy(lat, lon)[0]},{xy(lat, lon)[1]}" for lon, lat in ring)
        parts.append(f'<polygon points="{coords}" fill="#dfe7de" stroke="#b9c7b8" stroke-width="0.8"/>')
    for flow in result.per_edge_flow or []:
        try:
            a, b = xy(float(flow["from_lat"]), float(flow["from_lon"])), xy(float(flow["to_lat"]), float(flow["to_lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        w = min(3.0, 0.4 + (float(flow.get("volume_m3") or 0.0) ** 0.5) * 0.15)
        parts.append(f'<line x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}" stroke="#7f95ab" stroke-opacity="0.55" stroke-width="{w:.1f}"/>')
    open_codes = set((result.solver_log or {}).get("hubs_open_codes") or [])
    stores = []
    for node in nodes:
        if node.lat is None or node.lon is None:
            continue
        x, y = xy(float(node.lat), float(node.lon))
        if node.level == 0:
            stores.append((x, y, node.name, "#111111", True))
            continue
        if node.hub_capable:
            if node.code in open_codes:
                stores.append((x, y, node.name, "#0e5f70", True))
            else:
                parts.append(f'<circle cx="{x}" cy="{y}" r="4" fill="none" stroke="#9aa9b8" stroke-width="1" stroke-dasharray="2 1.5"/>')
            continue
        d = detail.get(node.code)
        fill = d.get("fill_rate") if d else None
        colour = "#c9d2da" if fill is None else ("#2e7355" if fill >= 0.999 else ("#d9a441" if fill >= 0.8 else "#9e4038"))
        r = 2.2 + min(2.5, (float(node.catchment_population or 0) ** 0.5) / 160)
        parts.append(f'<circle cx="{x}" cy="{y}" r="{r:.1f}" fill="{colour}" fill-opacity="0.9"/>')
    for x, y, name, colour, _ in stores:
        parts.append(f'<circle cx="{x}" cy="{y}" r="6.5" fill="{colour}" stroke="#ffffff" stroke-width="1.6"/>')
        tx = x + 9 if x < width * 0.8 else x - 9
        anchor = "start" if x < width * 0.8 else "end"
        parts.append(
            f'<text x="{tx}" y="{y + 4}" font-size="11" font-family="-apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif" '
            f'fill="#17202a" text-anchor="{anchor}" stroke="#ffffff" stroke-width="3" paint-order="stroke">{_e(name)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


# --- page one --------------------------------------------------------------------------


def page_one(
    *,
    country,
    scenario,
    result,
    baseline_result,
    nodes: list,
    verdict: str,
    verdict_tone: str,
    question: Optional[str] = None,
) -> str:
    currency = country.currency or "USD"
    kpi = result.kpi_set or {}
    base_kpi = (baseline_result.kpi_set or {}) if baseline_result else {}
    is_baseline = bool(getattr(scenario, "is_baseline", False))
    nodes_by_code = {n.code: n for n in nodes}

    def change(key: str) -> Optional[float]:
        here, there = kpi.get(key), base_kpi.get(key)
        if here is None or not there or is_baseline:
            return None
        return (here - there) / abs(there)

    people = people_moved(None if is_baseline else baseline_result, result)
    if is_baseline:
        people_text, people_note = _exact(kpi.get("population_served")), "people supplied in full"
    elif people["gained"] and not people["lost"]:
        people_text, people_note = f"+{_exact(people['gained'])}", "people newly supplied in full"
    elif people["lost"] and not people["gained"]:
        people_text, people_note = f"−{_exact(people['lost'])}", "people no longer supplied in full"
    elif people["gained"] or people["lost"]:
        people_text, people_note = f"+{_exact(people['gained'])} / −{_exact(people['lost'])}", "people newly supplied / no longer supplied"
    else:
        people_text, people_note = "no change", "everyone supplied today is supplied here"

    numbers = [
        ("Annual cost", _money(kpi.get("total_cost"), currency), change("total_cost"), True, "#figures"),
        ("Demand met", _pct(kpi.get("fill_rate")), change("fill_rate"), False, "#facilities"),
        (people_note, people_text, None, False, "#facilities"),
    ]
    numbers_html = "".join(
        f'<a class="fig" href="{anchor}"><span class="fig-label">{_e(label)}</span><b>{_e(value)}</b>'
        f'<span class="delta {"good" if (delta is not None and ((delta < 0) == good)) else ("bad" if delta is not None else "flat")}">'
        f'{_e(_signed_pct(delta))}{" vs today" if delta is not None and abs(delta) > 1e-9 else ""}</span></a>'
        for label, value, delta, good, anchor in numbers
    )

    stores = stores_table(result, nodes_by_code)
    store_rows = "".join(
        f'<tr><td>{_e(s["name"])}{" <span class=tag>proposed</span>" if s["proposed"] else ""}<span class="sub">{_e(s["admin1"] or "")}</span></td>'
        f'<td class="n">{_exact(s["dry_m3"]) if s["dry_m3"] is not None else "—"}</td>'
        f'<td class="n">{_exact(s["cold_m3"]) if s["cold_m3"] is not None else "—"}</td>'
        f'<td class="n">{_exact(s["facilities"])}</td><td class="n">{_exact(s["volume_m3"])}</td></tr>'
        for s in stores
    )
    stores_html = (
        f'<div class="table-wrap"><table><thead><tr><th>Store</th><th class="n">Dry m³</th><th class="n">+2–8°C m³</th>'
        f'<th class="n">Facilities served</th><th class="n">m³ a year</th></tr></thead><tbody>{store_rows}</tbody></table></div>'
        if stores
        else "<p class='note'>No store is open under this plan.</p>"
    )

    strata = (result.equity_detail or {}).get("strata") or []
    worst = strata[-1] if strata else None
    base_worst = ((baseline_result.equity_detail or {}).get("strata") or [None])[-1] if baseline_result and not is_baseline else None
    if worst:
        equity_line = (
            f"The most vulnerable fifth, {_exact(worst.get('population'))} people at {_exact(worst.get('facilities'))} facilities, "
            f"gets {_pct(worst.get('fill_rate'))} of its demand"
            + (f" (today: {_pct(base_worst.get('fill_rate'))})" if base_worst else "")
            + f"; the gap between the best and worst bands is {_pct(kpi.get('equity_gap'))}."
        )
    else:
        equity_line = "No equity bands were computed for this plan."

    assessment = confidence_mod.assess(result, None if is_baseline else baseline_result)
    conf_tone = "good" if assessment.get("holds") else ("warn" if assessment.get("holds") is False else "neutral")

    return f'''<section class="page-one" id="decision">
    {f'<p class="eyebrow">The question</p><h2 class="question">{_e(question)}</h2>' if question else ''}
    <div class="verdict {verdict_tone}">{verdict}</div>
    {map_thumbnail(result, nodes, country.boundary)}
    <h3>Stores under this plan</h3>
    {stores_html}
    <div class="figures three">{numbers_html}</div>
    <p class="line"><a href="#equity"><b>Who carries it.</b></a> {_e(equity_line)}</p>
    <p class="line {conf_tone}"><a href="#confidence"><b>How sure this is.</b></a> {_e(assessment.get("sentence"))}</p>
  </section>'''


# --- the assumptions annex --------------------------------------------------------------


PROVENANCE_LABELS = {
    "assumption": "Assumptions and scenario settings",
    "manual_override": "Hand-typed corrections",
    "derived": "Estimates from a rule",
    "import": "Imported from a file",
    "lmis_sync": "Synced from a country system",
    "seed": "Seeded illustrative data",
}


def assumptions_annex(entries: list, limit: int = 40) -> str:
    """The ledger, grouped by how each value entered the model, closing the report."""
    if not entries:
        return '<section id="annex"><h2>Assumptions annex</h2><p class="lede">The ledger is empty.</p></section>'
    counts: dict = {}
    authors: set = set()
    for entry in entries:
        counts[entry.provenance] = counts.get(entry.provenance, 0) + 1
        authors.add(entry.author_claim)
    count_rows = "".join(
        f"<tr><td>{_e(PROVENANCE_LABELS.get(key, key))}</td><td class='n'>{_exact(n)}</td></tr>"
        for key, n in sorted(counts.items(), key=lambda kv: -kv[1])
    )
    shown = [e for e in entries if e.provenance in ("assumption", "manual_override", "derived")][:limit]
    rows = "".join(
        f"<tr><td>{_e(e.entity_type)}<span class='sub'>{_e(e.entity_ref)}</span></td><td>{_e(e.field)}</td>"
        f"<td>{_e(e.old_value or 'blank')} → {_e(e.new_value or 'blank')}</td>"
        f"<td>{_e(e.confidence_marker)}</td><td>{_e(e.author_claim)}<span class='sub'>{_e(e.created_at.strftime('%d %b %Y') if isinstance(e.created_at, datetime) else e.created_at)}</span></td>"
        f"<td>{_e(e.rationale or '')}</td></tr>"
        for e in shown
    )
    return f'''<section id="annex">
    <h2>Assumptions annex</h2>
    <p class="lede">Every value in this model entered it one of these ways, and the ledger holds each one with
    who put it there and why. Counts first; then the most recent assumptions, corrections and estimates
    ({len(shown)} of {sum(counts.values())} entries; the full ledger is in the tool). Claimed by: {_e(", ".join(sorted(authors)))}.</p>
    <div class="table-wrap"><table><thead><tr><th>How it entered</th><th class="n">Entries</th></tr></thead><tbody>{count_rows}</tbody></table></div>
    <div class="table-wrap" style="margin-top:14px"><table>
      <thead><tr><th>What</th><th>Field</th><th>Change</th><th>S/I/U</th><th>Who</th><th>Why</th></tr></thead>
      <tbody>{rows or '<tr><td colspan="6">No assumptions, corrections or estimates recorded yet.</td></tr>'}</tbody></table></div>
  </section>'''


PAGE_ONE_CSS = """
  .page-one { margin-top: 24px; }
  .page-one .question { font-size: 24px; margin: 0 0 8px; letter-spacing: -0.01em; }
  .page-one .map { display: block; width: 100%; height: auto; margin: 22px 0 6px; border: 1px solid var(--rule); border-radius: 6px; }
  .page-one h3 { font-size: 15px; margin: 18px 0 8px; }
  .figures.three { grid-template-columns: repeat(3, 1fr); margin-top: 20px; }
  .fig { text-decoration: none; color: inherit; }
  .fig:hover { background: #f6f8f9; }
  .line { margin: 14px 0 0; font-size: 15px; line-height: 1.55; }
  .line a { color: var(--accent); text-decoration: none; }
  .line.good b { color: var(--good); } .line.warn b { color: #b0620f; }
  .tag { font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); margin-left: 6px; }
  .page-break { break-before: page; }
"""


# --- the options considered, for a study's report ----------------------------------------


def options_section(compare: dict, currency: str) -> str:
    rows = [r for r in compare.get("rows", [])]
    header = (
        "<tr><th>Option</th><th class='n'>Annual cost</th><th class='n'>Demand met</th>"
        "<th class='n'>Most vulnerable fifth</th><th class='n'>Stores</th><th>Holds at ±30%?</th></tr>"
    )
    body = ""
    for r in rows:
        kpi = r.get("kpi_set") or {}
        comp = r.get("comparison") or {}
        conf = r.get("confidence") or {}
        if r.get("status") != "ok":
            body += f"<tr><td>{_e(r['name'])}</td><td colspan='5'>not run</td></tr>"
            continue
        def cell(key, fmt):
            value = fmt(kpi.get(key))
            delta = (comp.get(key) or {}).get("delta_pct")
            return f"<td class='n'>{_e(value)}{f'<span class=sub>{_e(_signed_pct(delta))} vs today</span>' if delta is not None and abs(delta) > 1e-9 else ''}</td>"
        holds = "—" if conf.get("share", 0) <= 0 else ("holds" if conf.get("holds") else ("depends" if conf.get("holds") is False else "not tested"))
        name = _e(r["name"]) + (" <span class=tag>recommended</span>" if r.get("recommended") else "") + (" <span class=tag>today</span>" if r.get("is_baseline") else "")
        body += (
            f"<tr><td>{name}</td>{cell('total_cost', lambda v: _money(v, currency))}{cell('fill_rate', _pct)}"
            f"{cell('worst_stratum_fill_rate', _pct)}{cell('hubs_open', _exact)}<td>{_e(holds)}</td></tr>"
        )
    levers = ""
    for r in rows:
        diff = (compare.get("lever_diff") or {}).get(str(r.get("scenario_id")))
        if not diff:
            continue
        items = "".join(f"<li>{_e(d['text'])}</li>" for d in diff.get("differences", [])) or "<li>Nothing differs from today.</li>"
        moved = (compare.get("facilities") or {}).get(str(r.get("scenario_id"))) or {}
        moved_line = (
            f"<p class='note'>{_exact(moved.get('gained_count', 0))} facilities gain supply, {_exact(moved.get('lost_count', 0))} lose it"
            + (f"; {_exact(moved.get('people_lost'))} people move from supplied to not supplied" if moved.get("people_lost") else "")
            + ".</p>"
            if moved
            else ""
        )
        levers += f"<div class='phase'><h3>{_e(r['name'])}</h3><ul>{items}</ul>{moved_line}</div>"
    return f'''<section id="options">
    <h2>The options considered</h2>
    <p class="lede">{_e(compare.get("verdict") or "")}</p>
    <div class="table-wrap"><table><thead>{header}</thead><tbody>{body}</tbody></table></div>
    {f'<h3 style="margin-top:18px">What each option changes</h3><div class="phases">{levers}</div>' if levers else ''}
  </section>'''
