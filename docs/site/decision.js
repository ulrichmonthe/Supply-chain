/* Page one, rendered in the browser from the demo's stored results: the same
   figures the tool's report prints, generated for whichever scenario the visitor picks. */
(async function () {
  'use strict'
  const pick = document.getElementById('decide-pick'), sheet = document.getElementById('decision-sheet'), status = document.getElementById('decide-status')
  if (!pick) return
  const snap = await fetch('app/api/snap/1-annual.json').then((r) => r.json())
  const rows = snap.scorecard.rows.filter((r) => r.status === 'ok')
  const base = rows.find((r) => r.is_baseline)
  const land = await fetch('app/api/basemap.json').then((r) => r.json()).catch(() => null)
  const cur = 'PGK'
  const money = (v) => (Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(2) + 'M' : (v / 1e3).toFixed(0) + 'k') + ' ' + cur
  const pct = (v, d = 1) => (v * 100).toFixed(d) + '%'
  const signed = (v) => (v > 0 ? '+' : '') + (v * 100).toFixed(1) + '%'
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
  for (const r of rows.filter((r) => !r.is_baseline)) { const o = document.createElement('option'); o.value = r.result_id; o.textContent = r.name; pick.appendChild(o) }

  function thumbnail(result) {
    const W = 760, H = 380
    const pts = result.per_node_detail
    const lats = pts.map((p) => p.lat), lons = pts.map((p) => p.lon)
    const lat0 = Math.min(...lats) - 0.6, lat1 = Math.max(...lats) + 0.6, lon0 = Math.min(...lons) - 0.6, lon1 = Math.max(...lons) + 0.6
    const X = (lon) => (((lon - lon0) / (lon1 - lon0)) * W).toFixed(1), Y = (lat) => (((lat1 - lat) / (lat1 - lat0)) * H).toFixed(1)
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Map of the network under this option: stores, the lanes used and facilities coloured by supply"><rect width="${W}" height="${H}" fill="#0a1420"/>`
    if (land) for (const f of land.features) { const rings = f.geometry.type === 'Polygon' ? f.geometry.coordinates : f.geometry.coordinates.flat(); for (const ring of rings) s += `<polygon points="${ring.map(([lon, lat]) => X(lon) + ',' + Y(lat)).join(' ')}" fill="#16263a" stroke="#2d4360" stroke-width="0.8"/>` }
    const colour = { road: '#6f8fb8', sea: '#3fb984', air: '#e28b4a', river: '#a8b45c' }
    const maxV = Math.max(1, ...result.per_edge_flow.map((f) => f.volume_m3))
    for (const f of result.per_edge_flow) s += `<line x1="${X(f.from_lon)}" y1="${Y(f.from_lat)}" x2="${X(f.to_lon)}" y2="${Y(f.to_lat)}" stroke="${colour[f.mode] || '#8fa3b8'}" stroke-opacity="0.7" stroke-width="${(0.6 + 2.6 * Math.sqrt(f.volume_m3 / maxV)).toFixed(2)}"/>`
    const fillColour = (v) => (v >= 0.999 ? '#3fb984' : v >= 0.9 ? '#9ac95e' : v >= 0.7 ? '#e8b23a' : v >= 0.4 ? '#e28b4a' : '#ef6b6b')
    for (const p of pts) s += `<circle cx="${X(p.lon)}" cy="${Y(p.lat)}" r="2.6" fill="${fillColour(p.fill_rate)}"/>`
    const hubs = new Map(); for (const f of result.per_edge_flow) if (!hubs.has(f.hub_code)) hubs.set(f.hub_code, f)
    for (const [code, f] of hubs) s += `<circle cx="${X(f.from_lon)}" cy="${Y(f.from_lat)}" r="6" fill="#4da3ff" stroke="#fff" stroke-width="1.3"/><text x="${(+X(f.from_lon) + 9).toFixed(1)}" y="${(+Y(f.from_lat) + 4).toFixed(1)}" font-size="11" fill="#dbe6f2" font-family="Public Sans, sans-serif" stroke="#0a1420" stroke-width="3" paint-order="stroke">${esc(code.replace('AMS-', ''))}</text>`
    return s + '</svg>'
  }

  async function render(resultId) {
    status.textContent = 'Generating…'
    const row = rows.find((r) => String(r.result_id) === String(resultId))
    const result = await fetch(`app/api/results/${resultId}.json`).then((r) => r.json())
    const k = row.kpi_set, b = base.kpi_set
    const cheaper = k.total_cost < b.total_cost, dc = k.total_cost / b.total_cost - 1
    const worst = (row.equity || []).reduce((m, s) => Math.min(m, s.fill_rate), 1)
    const bworst = (base.equity || []).reduce((m, s) => Math.min(m, s.fill_rate), 1)
    const lostPeople = result.per_node_detail.filter((n) => n.fill_rate < 0.999).reduce((a, n) => a + (n.population || 0), 0)
    const conf = row.confidence || result.confidence || {}
    const holds = conf.holds
    const hubs = (result.solver_log && result.solver_log.hubs_open_codes) || []
    const verdict = cheaper
      ? `<b>${esc(row.name)}</b> costs <b>${pct(-dc)} less</b> than today's network, at <b>${money(k.total_cost)}</b> a year${k.fill_rate < 0.999 ? `, and meets <b>${pct(k.fill_rate)}</b> of demand instead of ${pct(b.fill_rate)}` : ' and still meets every facility'}.`
      : `<b>${esc(row.name)}</b> costs <b>${pct(dc)} more</b> than today's network, at <b>${money(k.total_cost)}</b> a year${k.fill_rate < b.fill_rate - 0.001 ? `, and meets ${pct(k.fill_rate)} of demand` : ''}.`
    const equityLine = worst < bworst - 0.005
      ? `The saving is carried by the hardest-to-reach band, whose supply falls from ${pct(bworst)} to <b>${pct(worst)}</b>. <b>${lostPeople.toLocaleString('en')}</b> people are no longer fully supplied.`
      : lostPeople > 0 ? `<b>${lostPeople.toLocaleString('en')}</b> people are in facilities not fully supplied; the hardest-to-reach band holds at <b>${pct(worst)}</b>.` : `Every band is fully supplied.`
    const confLine = conf.sentence ? esc(conf.sentence) : holds == null ? 'No confidence budget recorded for this run.' : holds ? `The answer <b class="holds">holds</b> with every estimated figure 30% lower or higher.` : `The answer <b class="depends">depends</b> on the estimates: at ±30% it changes.`
    const fig = (label, v, bv, fmt, better) => { const d = bv ? (v - bv) / Math.abs(bv) : 0; const dir = Math.abs(d) < 0.0005 ? 'flat' : (d < 0) === (better === 'lower') ? 'better' : 'worse'; return `<div class="fig"><label>${label}</label><b>${fmt(v)}</b><span class="delta ${dir}">${dir === 'flat' ? 'same as today' : signed(d) + ' vs today'}</span></div>` }
    sheet.innerHTML = `
      <p class="eyebrow">Decision page · ${esc(row.name)} · ${esc(result.solver_log && result.solver_log.month_label || 'Annualised')}</p>
      <p class="verdict">${verdict}</p>
      ${thumbnail(result)}
      <p class="small-note">Stores open: <b>${hubs.map((h) => h.replace('AMS-', '')).join(', ') || '—'}</b>. Lanes carrying stock: ${result.per_edge_flow.length}. Facilities coloured by how much of their demand is met.</p>
      <div class="figs">
        ${fig('Annual cost', k.total_cost, b.total_cost, money, 'lower')}
        ${fig('Demand met', k.fill_rate, b.fill_rate, pct, 'higher')}
        ${fig('Worst band supplied', worst, bworst, pct, 'higher')}
      </div>
      <p class="line"><b>Who carries it.</b> ${equityLine}</p>
      <p class="line"><b>How sure.</b> ${confLine} ${conf.share != null ? `${pct(conf.share, 0)} of demand is estimated.` : ''}</p>
      <p class="line"><b>What the report adds.</b> The options considered, the figures against today, the facilities not fully supplied by name, the roadmap and its one-off costs, and the assumptions annex written from the ledger.</p>`
    status.textContent = `Generated from result ${resultId} in the browser`
  }
  pick.addEventListener('change', () => render(pick.value))
  const preferred = rows.find((r) => /cost/i.test(r.name) && !r.is_baseline) || rows.find((r) => !r.is_baseline)
  if (preferred) { pick.value = preferred.result_id; render(preferred.result_id) }
})().catch((e) => { const s = document.getElementById('decision-sheet'); if (s) s.textContent = 'Could not load the demo results: ' + e.message })
