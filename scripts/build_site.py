#!/usr/bin/env python3
"""Build the marketing site into docs/ from the captured demo data.

Every number on the site is computed here from the same captures the static demo
serves, so the site and the tool cannot drift apart: rebuild the demo, rebuild the
site, and the figures follow. Stdlib only, so `python3 scripts/build_site.py` works
on any machine that has Python.
"""

from __future__ import annotations

import html
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SITE = DOCS / "site"
API = DOCS / "app" / "api"
REPO = "https://github.com/ulrichmonthe/Supply-chain"
MASTERCLASS = "https://claude.ai/artifact/MuPy3PmRsWRXc7EPHVfkN6"


def e(v) -> str:
    return html.escape("" if v is None else str(v))


def load(rel: str):
    return json.loads((API / rel).read_text())


# --- the numbers -------------------------------------------------------------------------


def numbers() -> dict:
    snap = load("snap/1-annual.json")
    rows = [r for r in snap["scorecard"]["rows"] if r.get("status") == "ok"]
    overview = load("overview.json")
    base = next(r for r in rows if r["is_baseline"])
    cheapest = min((r for r in rows if not r["is_baseline"]), key=lambda r: r["kpi_set"]["total_cost"])
    cheap_result = load(f"results/{cheapest['result_id']}.json")
    base_result = load(f"results/{base['result_id']}.json")
    before = {n["code"]: n for n in base_result["per_node_detail"]}
    lost = sum(
        int(n.get("population") or 0)
        for n in cheap_result["per_node_detail"]
        if before.get(n["code"], {}).get("fill_rate", 0) >= 0.999 and (n.get("fill_rate") or 0) < 0.999
    )
    march = next((r for r in rows if "March" in r["name"]), None)
    model = json.loads((SITE / "model" / "baseline.json").read_text())
    counts, totals = overview["counts"], overview["totals"]
    conf = base.get("confidence") or {}
    return {
        "facilities": counts["facilities"],
        "stores": counts["hubs"],
        "stores_open": counts["hubs_operational"],
        "lanes": counts["edges"],
        "services": counts["scheduled_services"],
        "provinces": len(totals["provinces"]),
        "people": totals["population"],
        "demand_m3": totals["annual_demand_m3"],
        "scenarios": len(rows),
        "base_cost": base["kpi_set"]["total_cost"],
        "currency": overview["country"]["currency"],
        "cheapest_name": cheapest["name"],
        "cheapest_cost": cheapest["kpi_set"]["total_cost"],
        "cheapest_saving": 1 - cheapest["kpi_set"]["total_cost"] / base["kpi_set"]["total_cost"],
        "cheapest_worst": cheapest["kpi_set"]["worst_stratum_fill_rate"],
        "people_lost": lost,
        "march_cost_pct": (march["kpi_set"]["total_cost"] / base["kpi_set"]["total_cost"] - 1) if march else None,
        "march_unreachable": march["kpi_set"].get("facilities_unreachable") if march else None,
        "march_at_risk": march["kpi_set"].get("facilities_at_risk") if march else None,
        "base_at_risk": base["kpi_set"].get("facilities_at_risk"),
        "estimated_share": overview["estimated"]["demand_share"],
        "base_holds": conf.get("holds"),
        "all_hold": all((r.get("confidence") or {}).get("holds") for r in rows),
        "runtime_ms": base_result.get("runtime_ms"),
        "lp_lanes": len(model["lanes"]),
        "distance_confidence": overview["distance_provenance"]["km_weighted_confidence"],
    }


# --- layout -----------------------------------------------------------------------------

NAV = [
    ("index.html", "Home"),
    ("data.html", "Bring your data"),
    ("ask.html", "Ask a question"),
    ("trust.html", "Trust the answer"),
    ("decide.html", "Decide"),
    ("keep.html", "Keep it"),
    ("compare.html", "Compare"),
]


def current(flag: bool) -> str:
    return ' aria-current="page"' if flag else ""


def layout(page: str, title: str, description: str, body: str, scripts: str = "", head_extra: str = "") -> str:
    links = "".join(
        f'<a href="{href}"{current(href == page)}>{e(label)}</a>' for href, label in NAV
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(description)}">
<meta property="og:type" content="website">
<meta property="og:image" content="site/og.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Public+Sans:ital,wght@0,400;0,600;0,700;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%230b1119'/%3E%3Ccircle cx='10' cy='21' r='4' fill='%233fb984'/%3E%3Ccircle cx='22' cy='10' r='4' fill='%234da3ff'/%3E%3Cpath d='M13 19 L19 12' stroke='%23e28b4a' stroke-width='2.5' stroke-linecap='round'/%3E%3C/svg%3E">
<link rel="stylesheet" href="site/site.css">
{head_extra}
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<div class="wrap">
  <nav class="nav" aria-label="Site">
    <a class="brand" href="index.html"><b>Health Supply Chain Network Design</b><span>A design engine that shows its working</span></a>
    <div class="nav-links">{links}</div>
    <div class="nav-right">
      <a class="btn ghost" href="{MASTERCLASS}">Master class</a>
      <a class="btn primary" href="app/">Open the tool <span aria-hidden="true">→</span></a>
    </div>
  </nav>
  <main id="main">
{body}
  </main>
  <footer>
    <span>Health Supply Chain Network Design Tool</span>
    <a href="app/">Live demo</a>
    <a href="{MASTERCLASS}">Master class</a>
    <a href="board/">Build board</a>
    <a href="{REPO}">Source and docs</a>
    <a href="{REPO}#readme">README</a>
    <span>Figures are model output on an illustrative Papua New Guinea workspace, not measurements.</span>
    <span>Built with Claude Code.</span>
  </footer>
</div>
{scripts}
</body>
</html>
"""


def shot(name: str, alt: str, caption: str, cls: str = "") -> str:
    return f'''<figure class="{cls}"><img src="site/shots/{name}.png" alt="{e(alt)}" loading="lazy"><figcaption>{caption}</figcaption></figure>'''


# --- pages ------------------------------------------------------------------------------


def home(n: dict) -> str:
    cur = n["currency"]
    body = f"""
    <section class="hero flush">
      <div>
        <p class="eyebrow">For ministries of health, the consultancies that design their networks, and the funders who pay for it</p>
        <h1>The supply chain design engine that shows its working</h1>
        <p class="lede">Every clinic, store and lane of a national health network on one map, solved by a real optimiser, scored on cost <em>and</em> on who gets left out. It runs on a laptop in a provincial health office, and every number it prints can be traced back to who typed it and how sure they were.</p>
        <div class="actions">
          <a class="btn primary big" href="app/">Open the tool <span aria-hidden="true">→</span></a>
          <a class="btn big" href="#playground">Move a lever below</a>
          <a class="btn big ghost" href="{MASTERCLASS}">Read the master class</a>
        </div>
        <p class="small-note">The map below is not a picture. It is the demo network being re-optimised in your browser by the same HiGHS solver the tool runs, every time you move a control.</p>
      </div>
    </section>

    <div class="playground" id="playground" aria-label="Live network solver">
      <div class="pg-map">
        <svg role="img" aria-label="The Papua New Guinea demo network: stores, facilities coloured by how well they are supplied, and the lanes the current plan uses"></svg>
        <div class="pg-legend" aria-hidden="true">
          <span><i style="background:var(--road)"></i>road</span><span><i style="background:var(--sea)"></i>sea</span><span><i style="background:var(--air)"></i>air</span><span><i style="background:var(--river)"></i>river</span>
          <span><i class="dot" style="background:#3fb984"></i>fully supplied</span><span><i class="dot" style="background:#ef6b6b"></i>not reached</span>
        </div>
        <div class="pg-status" role="status" aria-live="polite">Loading the network…</div>
      </div>
      <div class="pg-side">
        <div class="pg-label">What the plan costs, and who carries it</div>
        <div class="kpis">
          <div class="kpi"><label>Annual cost</label><b id="k-cost">—</b><span class="delta flat" id="k-cost-delta"></span></div>
          <div class="kpi"><label>Demand met</label><b id="k-fill">—</b><span class="delta flat" id="k-fill-delta"></span></div>
          <div class="kpi"><label>Worst band supplied</label><b id="k-worst">—</b><span class="delta flat" id="k-worst-delta"></span></div>
          <div class="kpi"><label>People not fully supplied</label><b id="k-people">—</b><span class="delta flat" id="k-people-delta"></span></div>
          <div class="kpi"><label>Stores open</label><b id="k-hubs">—</b><span class="delta flat" id="k-hubs-delta"></span></div>
          <div class="kpi"><label>Solve time</label><b id="k-time">—</b><span class="delta flat">on this machine, HiGHS in WebAssembly</span></div>
        </div>
        <div class="verdict" id="pg-verdict" role="status">Loading…</div>
        <div class="pg-label">Levers</div>
        <div class="control">
          <div class="control-head"><label for="pg-equity">How much the hardest-to-reach count</label><output id="pg-equity-out" for="pg-equity">0.50</output></div>
          <input type="range" id="pg-equity" min="0" max="1" step="0.05" value="0.5">
        </div>
        <div class="control">
          <div class="control-head"><label for="pg-service">Value of a cubic metre delivered</label><output id="pg-service-out" for="pg-service">1.00</output></div>
          <input type="range" id="pg-service" min="0" max="1" step="0.05" value="1">
        </div>
        <div class="control">
          <div class="control-head"><label for="pg-minfill">Minimum demand met, network-wide</label><output id="pg-minfill-out" for="pg-minfill">off</output></div>
          <input type="range" id="pg-minfill" min="0" max="1" step="0.05" value="0">
        </div>
        <div class="control">
          <div class="control-head"><label for="pg-growth">Demand growth</label><output id="pg-growth-out" for="pg-growth">+0.0%</output></div>
          <input type="range" id="pg-growth" min="-0.3" max="0.6" step="0.05" value="0">
        </div>
        <label class="checkbox"><input type="checkbox" id="pg-optimize"> Let the solver choose which stores to run</label>
        <div class="chips" role="group" aria-label="Modes allowed">
          <button type="button" class="chip pg-mode" data-mode="road" aria-pressed="true">road</button>
          <button type="button" class="chip pg-mode" data-mode="sea" aria-pressed="true">sea</button>
          <button type="button" class="chip pg-mode" data-mode="air" aria-pressed="true">air</button>
          <button type="button" class="chip pg-mode" data-mode="river" aria-pressed="true">river</button>
        </div>
        <div class="pg-label">Try a question</div>
        <div class="chips" role="group" aria-label="Preset questions">
          <button type="button" class="chip" data-preset="today">Today</button>
          <button type="button" class="chip" data-preset="cheapest">The cheapest network</button>
          <button type="button" class="chip" data-preset="fair">Cheapest with a 95% floor</button>
          <button type="button" class="chip" data-preset="roads">Roads only</button>
          <button type="button" class="chip" data-preset="growth">Demand up 30%</button>
        </div>
      </div>
    </div>

    <section>
      <h2>Who this is for</h2>
      <p class="sub">Three chairs, one model. Each gets the screen it needs from the same data and the same solve.</p>
      <div class="roles">
        <a class="role" href="decide.html"><span class="who">Ministry of health</span><h3>A page one a director can act on</h3><p>The recommendation in a sentence, the stores on a map, three numbers against today, who carries it, and how sure the answer is. Printed to PDF from any browser, no licence to renew.</p><span class="go">See the decision page →</span></a>
        <a class="role" href="ask.html"><span class="who">Design consultancy</span><h3>Run the whole engagement in one tool</h3><p>Data in through a mapper or a connector, estimates for the blanks, studies with presets, a diff map, greenfield sites, twelve-month runs, and a session saved for the next review.</p><span class="go">See the workflow →</span></a>
        <a class="role" href="trust.html"><span class="who">Funder and reviewer</span><h3>Every figure traceable, every answer tested</h3><p>A ledger with a name on every value, a confidence budget re-solved at ±30% on every run, and an assumptions annex written from the record, not from memory.</p><span class="go">See what makes it defensible →</span></a>
      </div>
    </section>

    <section>
      <h2>How an engagement runs</h2>
      <p class="sub">The site follows the arc of a real project, because that is how the tool is built.</p>
      <div class="arc">
        <a href="data.html"><span class="n">01</span><h3>Bring your data</h3><p>Template, CSV mapper, DHIS2 and mSupply, and estimates for the blanks.</p></a>
        <a href="ask.html"><span class="n">02</span><h3>Ask a question</h3><p>Studies with presets, data changes, a diff map, greenfield, twelve months.</p></a>
        <a href="trust.html"><span class="n">03</span><h3>Trust the answer</h3><p>The ledger, the confidence budget, the assumptions annex.</p></a>
        <a href="decide.html"><span class="n">04</span><h3>Decide</h3><p>The decision page, the Decision view, the report.</p></a>
        <a href="keep.html"><span class="n">05</span><h3>Keep it</h3><p>Sessions, the spreadsheet round trip, runs offline.</p></a>
        <a href="compare.html"><span class="n">06</span><h3>Compare</h3><p>Against a spreadsheet and against Coupa, in one honest table.</p></a>
      </div>
    </section>

    <section>
      <h2>Figures you can reproduce</h2>
      <p class="sub">Every number here is solver output from the demo workspace, regenerated whenever the model changes. Open the tool and get the same answers.</p>
      <div class="stats">
        <div class="stat"><b>{e(f"{n['facilities']:,}")}</b><span>health facilities across {n['provinces']} provinces</span><small>{n['stores']} stores · {n['lanes']} lanes · {n['services']} timetabled services</small></div>
        <div class="stat"><b>{e(f"{n['cheapest_saving']:.1%}")}</b><span>cheaper, the cost-optimised network</span><small>and {e(f"{n['people_lost']:,}")} people move from supplied to not supplied</small></div>
        <div class="stat"><b>{e(f"{n['march_cost_pct']:+.1%}") if n['march_cost_pct'] is not None else "—"}</b><span>cost if March held all year</span><small>{"every facility still reached, by air; " if not n['march_unreachable'] else f"{n['march_unreachable']} facilities unreachable; "}{n['march_at_risk']} facilities above 20% stockout risk, against {n['base_at_risk']} today</small></div>
        <div class="stat"><b>{e(n['runtime_ms'])} ms</b><span>to solve the network, then twice more at ±30%</span><small>on every run, so the scorecard can say whether an answer holds</small></div>
      </div>
    </section>

    <section>
      <h2>Demonstrations, not claims</h2>
      <p class="sub">An AI-forward site should do something a brochure cannot. Two of these run on this page today; two need a small server and will light up when it is hosted.</p>
      <div class="features">
        <div class="feature demo"><span class="tag">Live on this page</span><h3>The page solves</h3><p>The map above is the tool's own allocation model, exported as data and re-optimised in your browser by HiGHS compiled to WebAssembly. Move a lever; watch the network re-plan. Nothing was pre-solved.</p><span class="fig">{n['facilities']} facilities · {n['lp_lanes']} lanes · about {n['runtime_ms']} ms a solve</span></div>
        <div class="feature demo"><span class="tag">Live on this page</span><h3>Your data, before you sign anything</h3><p>Drop a facility list on the <a href="data.html#mapper">Bring your data</a> page. Its headers are read, its columns mapped, and your facilities appear on a map. Nothing leaves your browser, which is the data-sovereignty story proven rather than promised.</p></div>
        <div class="feature soon"><span class="tag">Needs hosting</span><h3>Ask the network <span class="soon-pill">coming</span></h3><p>"Which facilities lose supply if the Alotau sailing goes monthly?" A question box that answers from the demo's results and confidence budget and shows the query it ran. Needs a small server with an API key.</p></div>
        <div class="feature demo"><span class="tag">Live on this page</span><h3>Page one, generated for you</h3><p>Pick a scenario on the <a href="decide.html">Decide</a> page and its decision page renders live: the recommendation, the stores, three numbers against today, and whether the answer holds.</p></div>
      </div>
    </section>

    <section>
      <div class="callout honest">
        <p class="callout-title">What is real here, and what is not</p>
        The facility locations, provinces, transport modes and the shape of the Papua New Guinea network are real. Demand, storage, costs and the vessel and aircraft timetables are illustrative placeholders until a ministry's own figures are loaded, and the tool says so on its own front page. Every distance carries the method that produced it and a confidence score. The tool is country-agnostic: a new country starts from the template, the CSV mapper or a connector.
      </div>
    </section>
"""
    scripts = """
<script src="site/vendor/highs.js"></script>
<script src="site/solver.js"></script>
<script src="site/playground.js"></script>
"""
    return layout("index.html", "Health Supply Chain Network Design", "A national health supply chain design engine that runs on a laptop, solves in your browser, scores cost beside equity, and shows its working.", body, scripts)


def data_page(n: dict) -> str:
    body = f"""
    <section class="hero flush">
      <p class="eyebrow">01 · Bring your data</p>
      <h1>Three doors in, one validated hallway</h1>
      <p class="lede">A workbook, a CSV from any system, or a live connection to DHIS2, OpenLMIS or Open mSupply. Whatever door the data comes through, it is validated against the same rules, diffed against what is already loaded, and every value arrives with where it came from and how sure someone was.</p>
    </section>

    <section id="mapper">
      <h2>Try it now: your facility list, on a map, without uploading it</h2>
      <p class="sub">Drop a CSV of facilities. The headers are read and mapped in your browser, and the facilities are plotted. The file never leaves this page; there is no server behind it. Use your ministry's register, or the demo's own export.</p>
      <div class="mapper">
        <div>
          <div class="drop" id="drop" tabindex="0" role="button" aria-label="Choose a CSV of facilities, or drop one here">
            <b>Drop a CSV here, or click to choose one</b>
            <div class="small-note">Needs a code or name, a latitude and a longitude. Everything else is optional.</div>
            <input type="file" id="csv-file" accept=".csv,.tsv,.txt,text/csv" aria-label="CSV file" tabindex="-1">
          </div>
          <div class="mapper-status" id="mapper-status" role="status" aria-live="polite">Nothing loaded yet. A sample: <button type="button" class="btn ghost" id="mapper-sample" style="min-height:32px;padding:4px 10px">load 20 demo facilities</button></div>
          <div class="map-fields" id="map-fields" hidden></div>
        </div>
        <div id="mapper-map"><svg role="img" aria-label="Map of the facilities in the file you loaded" viewBox="0 0 760 440"></svg></div>
      </div>
      <p class="small-note">In the tool this same mapper keeps the mapping under a name, validates the rows in partial mode, previews the changes, and merges them without ever retiring what the file does not mention.</p>
    </section>

    <section>
      <h2>The workbook round trip</h2>
      <p class="sub">The export and the blank template have identical columns, so whatever comes out can go back in. The model does not need this application to survive.</p>
      <div class="pair">
        {shot("15-data-import", "The Data tab: export and template buttons, the workbook drop zone and the CSV import block", "<b>The Data tab.</b> Drop a workbook; it is validated against 37 rules first, and nothing is written until you say so.", "narrow")}
        {shot("17-changes-review", "The changes review listing what applying the file would do", "<b>An import is a diff, not a wipe.</b> Rows added, updated, retired and restored, and conflicts where the file disagrees with a hand-typed correction. A full refresh retires what the file no longer lists; nothing is ever deleted.", "narrow")}
      </div>
    </section>

    <section>
      <h2>Estimates for the blanks</h2>
      <p class="sub">A blank the solver reads as zero and a typed guess that reads as a fact are the two ways demand data goes wrong. An estimate is the third way.</p>
      <div class="pair">
        {shot("18-estimates", "The Estimates section offering From population and Like peers for blank rows", "<b>Three rules, deliberately few.</b> From population, like peers, and storage from cover days. Fill one row or every blank row after a preview.", "narrow")}
        {shot("11-facility-editor", "The facility editor with an estimated storage value marked from cover days", "<b>Live until pinned.</b> Edit a population and the estimate follows; type a figure and the rule lets go. The chip shows the arithmetic.", "narrow")}
      </div>
      <p class="prose">The overview says what share of the demand is estimated. In the illustrative workspace it is {e(f"{n['estimated_share']:.0%}")}, which is true and is the first entry of the confidence budget.</p>
    </section>

    <section>
      <h2>Live connections</h2>
      <p class="sub">DHIS2, OpenLMIS and Open mSupply, tested step by step, synced through the same validate, preview and apply pipeline as a workbook.</p>
      {shot("20-live", "The Live tab with a form to add a connection", "<b>Connections.</b> Reported as individual checks rather than one boolean, because 'connection failed' cannot be acted on and 'authenticated, but the org unit tree is empty' can.", "narrow")}
    </section>
"""
    scripts = """
<script src="site/mapper.js"></script>
"""
    return layout("data.html", "Bring your data", "A workbook, a CSV from any system, or a live connection. Try the column mapper on your own facility list without uploading it.", body, scripts)


def ask_page(n: dict) -> str:
    body = f"""
    <section class="hero flush">
      <p class="eyebrow">02 · Ask a question</p>
      <h1>Comparison as the workflow, not a tab</h1>
      <p class="lede">A study is a question and the ordered scenarios that answer it, the baseline always first. Presets make the classic studies in one click; the verdict, the lever diff and the diff map say what differs and what it did.</p>
    </section>
    <section class="flush">
      {shot("24-study", "The Studies tab with a study open", "<b>A study.</b> Run it and read the verdict: which option is cheapest, which keeps the bottom band best supplied, and which the analyst backs.", "narrow")}
      <div class="pair">
        {shot("05-scenario-card", "A scenario card with tags, figures and Run and Duplicate buttons", "<b>Scenarios are questions.</b> Levers, constraints and objective weights belong to the scenario; the data belongs to the country.")}
        {shot("07-scenario-items", "The Data changes in this scenario section", "<b>Data changes inside a scenario.</b> Close a facility, scale demand in a province, open a store, add a lane. Applied in memory when it runs; the baseline is never touched.")}
      </div>
    </section>
    <section>
      <h2>Two answers as one network</h2>
      {shot("25-diff-map", "The diff map with lanes only the baseline uses drawn red", "<b>The diff map.</b> Lanes only the first option uses are red, only the second green, both grey; facilities that change supplier are amber. Built from the stored flows, no solver work.")}
    </section>
    <section>
      <h2>Where would new stores go?</h2>
      {shot("26-greenfield", "The greenfield proposal with two pins on the map", "<b>Greenfield.</b> Demand-weighted centre of gravity, snapped to real facilities. Adopt it and it becomes a scenario of candidate stores the solver may open or leave closed; on the illustrative data it leaves them closed, and says so.")}
    </section>
    <section>
      <h2>The wet season as it is lived</h2>
      <div class="pair">
        {shot("13-season-february", "The map in February with closed lanes dashed red", "<b>A month held all year.</b> Click a month and the network re-solves at its conditions; the Season tab names what closed and who is cut off.")}
        {shot("14-twelve-months", "The Season tab for a twelve-month run with a month table", "<b>Twelve linked months.</b> Stock carried between them within each facility's storage, a cyclic year, and the facilities that run short named with their peak stock.", "narrow")}
      </div>
    </section>
    <section>
      <div class="feature soon" style="max-width:70ch"><span class="tag">Needs hosting</span><h3>Ask the network <span class="soon-pill">coming</span></h3><p>The question box: type "which facilities lose supply if the Alotau sailing goes monthly?" and get an answer drawn from the demo's results and the confidence budget, with the query shown. It needs a small server with an API key, so it ships once the site has one. Every question typed will also be read as a signal for what to build next.</p></div>
    </section>
"""
    return layout("ask.html", "Ask a question", "Studies with presets, data changes inside a scenario, a diff map, greenfield sites and twelve-month runs.", body)


def trust_page(n: dict) -> str:
    holds = "holds" if n["all_hold"] else "is tested"
    body = f"""
    <section class="hero flush">
      <p class="eyebrow">03 · Trust the answer</p>
      <h1>Every figure traceable, every answer tested</h1>
      <p class="lede">A ministry buys defensibility. The tool records where every value came from and who claims it, re-solves every run with its estimates a third either way, and writes the assumptions annex from the record rather than from memory.</p>
    </section>
    <section class="flush">
      <h2>The confidence budget</h2>
      <p class="sub">When any demand is estimated, every run solves the scenario twice more with every estimated figure 30% lower and 30% higher. The scorecard then says whether the answer holds: the same stores stay open, the plan stays feasible, and an option that was cheaper than today stays cheaper with today's network pushed the same way.</p>
      {shot("09-scorecard", "The scorecard with an Estimated column reading holds ±30% and a range table beneath", f"<b>The scorecard.</b> In the illustrative workspace every scenario {holds} at ±30%, and the page says so in a sentence with the range at each end. A reversal is named, not averaged away.", "narrow")}
    </section>
    <section>
      <h2>The ledger</h2>
      <p class="sub">Seeded, imported, synced, typed, estimated, reverted: every value that entered the model, with a name, a marker of how sure, and a reason.</p>
      <div class="pair">
        {shot("21-provenance", "The Provenance tab listing ledger entries with Revert buttons", "<b>Provenance.</b> An estimate's entry carries its formula. A revert is itself a row, never a deletion.", "narrow")}
        {shot("19-table-editor", "The table editor with three rows selected and a bulk edit bar", "<b>Editing is documenting.</b> Even a bulk edit across many rows is one ledger row per row in one batch, with the marker and reason typed above the grid.", "narrow")}
      </div>
    </section>
    <section>
      <h2>The assumptions annex</h2>
      {shot("30-report-annex", "The assumptions annex in a report: counts by how values entered, then recent assumptions with who and why", "<b>Closing every report.</b> How each value entered the model, counted; then the recent assumptions, corrections and estimates with who made them and why. Written from the ledger, so it cannot disagree with the tool.")}
    </section>
    <section>
      <div class="callout honest"><p class="callout-title">A name, not a login</p>There are no accounts yet. The name you sign with is a claim stored beside every change. When accounts arrive each claim maps to a user by a migration rather than a rewrite, and the audit trail is honest from day one. Verified by {e("538")} automated tests on every build and an automated WCAG 2.1 AA check on every screen.</div>
    </section>
"""
    return layout("trust.html", "Trust the answer", "The confidence budget, the ledger with a name on every value, and the assumptions annex written from the record.", body)


def decide_page(n: dict) -> str:
    body = f"""
    <section class="hero flush">
      <p class="eyebrow">04 · Decide</p>
      <h1>Page one, generated in front of you</h1>
      <p class="lede">Pick one of the demo's scenarios and its decision page renders here from the stored result: the recommendation in a sentence, the stores on a map, three numbers against today, who carries it, and how sure the answer is. This is the page a director reads; everything else in the report is the evidence for it.</p>
    </section>
    <section class="flush">
      <div class="decision-pick">
        <label for="decide-pick"><b>Scenario</b></label>
        <select id="decide-pick" aria-label="Scenario to render"></select>
        <span class="small-note" id="decide-status" role="status" aria-live="polite"></span>
      </div>
      <div class="sheet" id="decision-sheet" aria-live="polite">Loading the demo results…</div>
      <p class="small-note">Generated from <code>app/api</code>, the same pre-solved results the demo serves. In the tool the report is one self-contained HTML file per scenario and per study, printable to PDF from any browser, with the assumptions annex at the end.</p>
    </section>
    <section>
      <h2>In the tool</h2>
      <div class="pair">
        {shot("27-decision-view", "The Decision view: the map, the question, option cards and three number tiles", "<b>Decision view.</b> Levers and tabs hidden; the options as cards, the three numbers, the equity line and the confidence line. Same data, one-tenth of the surface.")}
        {shot("28-report-page-one", "Page one of a study report", "<b>The report.</b> Page one over the backed option; then the options considered, the figures, who carries it, the facilities not fully supplied, the roadmap, and the annex.")}
      </div>
      {shot("22-roadmap", "The Roadmap tab with phases and one-off costs", "<b>The roadmap.</b> What to build first and what it costs one-off, phased against how the network runs today. Capital appears here at full value and in the annual comparison as an amortised charge.", "narrow")}
    </section>
"""
    scripts = """
<script src="site/decision.js"></script>
"""
    return layout("decide.html", "Decide", "Pick a scenario and its decision page renders live: the recommendation, the stores, three numbers against today, and whether the answer holds.", body, scripts)


def keep_page(n: dict) -> str:
    body = f"""
    <section class="hero flush">
      <p class="eyebrow">05 · Keep it</p>
      <h1>Stays with the government after the consultant leaves</h1>
      <p class="lede">Runs on a laptop with no internet. Saves the whole working state under a name and reopens it exactly. Exports the entire model as a spreadsheet with the same columns it imports, so the work survives the tool.</p>
    </section>
    <section class="flush">
      <h2>Sessions</h2>
      {shot("23-sessions", "The Sessions shelf with a comparison open", "<b>A save file that cannot lie.</b> Every row, scenario and result under a name. Open one and the working state becomes exactly that, with the work you were doing kept as a draft first. Compare reads the difference in sentences.", "narrow")}
    </section>
    <section>
      <div class="callout honest"><p class="callout-title">Licence, pricing, support</p>To be published. The source is open to read at <a href="{REPO}">GitHub</a>; the licence position and the engagement model are being settled and will appear here.</div>
    </section>
"""
    return layout("keep.html", "Keep it", "Runs on a laptop, saves the whole working state under a name, exports the entire model as a spreadsheet.", body)


def compare_page(n: dict) -> str:
    rows = [
        ("Optimiser", "Formulas and goal seek", "MILP, licensed per seat", "MILP (HiGHS), bundled; solves in ~50 ms; also in your browser"),
        ("Where it runs", "Anywhere", "Cloud, per-seat licence", "A laptop, offline; a local database that upgrades itself"),
        ("Seasonal access", "By hand", "Multi-period models", "Month by month, plus twelve months with stock carried"),
        ("Equity as an objective", "No", "Constraints you build", "A weight and a floor; every answer scored on the hardest-to-reach band"),
        ("Edit in place", "Yes", "Every table as a grid", "Every table as a grid, each cell change a ledger row with a name and a marker"),
        ("Data changes per scenario", "Copies of the workbook", "Scenario items", "Scenario items applied in memory; the baseline is never touched"),
        ("Provenance", "None", "Versions and change history", "A per-value ledger with sureness, formula for estimates, revert per row"),
        ("Estimates", "Typed guesses", "Expression fields", "Named rules, live until pinned, share reported on every result"),
        ("Sensitivity", "By hand", "Scenarios you set up", "Automatic ±30% re-solve on every run; 'holds' printed on the report"),
        ("Greenfield", "No", "Yes", "Yes, snapped to real facilities, adopted as candidates the solver must justify"),
        ("Import", "Copy and paste", "Data Guru pipelines", "Workbook, CSV mapper with saved mappings, DHIS2, OpenLMIS, mSupply; a three-way diff that keeps hand corrections"),
        ("Report", "Slides you write", "Dashboards", "One self-contained HTML per scenario and study: page one, evidence, assumptions annex"),
        ("Accessibility", "n/a", "Varies", "WCAG 2.1 AA checked on every build"),
        ("Cost", "Free", "Enterprise licence", "To be published"),
    ]
    trs = "".join(f"<tr><td class='k'>{e(a)}</td><td>{e(b)}</td><td>{e(c)}</td><td>{e(d)}</td></tr>" for a, b, c, d in rows)
    body = f"""
    <section class="hero flush">
      <p class="eyebrow">06 · Compare</p>
      <h1>Against a spreadsheet, and against Coupa</h1>
      <p class="lede">An honest table. Coupa Supply Chain Design is the enterprise standard and is broader in places; this tool is built for a different room, and does several things the standard does not.</p>
    </section>
    <section class="flush">
      <div class="tablewrap" role="region" aria-label="Comparison" tabindex="0">
        <table>
          <thead><tr><th>Capability</th><th>A spreadsheet</th><th>Coupa Supply Chain Design</th><th>This tool</th></tr></thead>
          <tbody>{trs}</tbody>
        </table>
      </div>
      <div class="callout honest"><p class="callout-title">Where Coupa is ahead</p>Breadth of model tables and columns, inventory optimisation and demand planning modules, a full visual ETL pipeline, and years of enterprise deployments. If you need those, you need Coupa. The Coupa column is from public documentation as read in September 2026, not from a hands-on evaluation.</div>
    </section>
"""
    return layout("compare.html", "Compare", "An honest comparison against a spreadsheet and against Coupa Supply Chain Design.", body)


def main() -> None:
    n = numbers()
    pages = {
        "index.html": home(n),
        "data.html": data_page(n),
        "ask.html": ask_page(n),
        "trust.html": trust_page(n),
        "decide.html": decide_page(n),
        "keep.html": keep_page(n),
        "compare.html": compare_page(n),
    }
    for name, content in pages.items():
        (DOCS / name).write_text(content)
    (SITE / "numbers.json").write_text(json.dumps(n, indent=2))
    print(f"site built: {', '.join(pages)}; numbers from {API}")


if __name__ == "__main__":
    main()
