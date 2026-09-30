/* The home page solves. Loads the exported baseline model and the HiGHS WebAssembly
   build, draws the network, then re-optimises on this machine every time a control
   moves. Nothing is pre-solved; the status corner says how long each solve took. */
(async function () {
  'use strict'
  const root = document.getElementById('playground')
  if (!root) return
  const status = root.querySelector('.pg-status')
  const svg = root.querySelector('svg')
  const say = (t) => { if (status) status.textContent = t }

  const [model, land] = await Promise.all([
    fetch('site/model/baseline.json').then((r) => r.json()),
    fetch('app/api/basemap.json').then((r) => r.json()).catch(() => null),
  ])

  // --- projection ------------------------------------------------------------------
  const W = 900, H = 520
  const pts = model.facilities.concat(model.hubs).filter((p) => p.lat != null)
  const lats = pts.map((p) => p.lat), lons = pts.map((p) => p.lon)
  const padLat = (Math.max(...lats) - Math.min(...lats)) * 0.08, padLon = (Math.max(...lons) - Math.min(...lons)) * 0.06
  const lat0 = Math.min(...lats) - padLat, lat1 = Math.max(...lats) + padLat
  const lon0 = Math.min(...lons) - padLon, lon1 = Math.max(...lons) + padLon
  const X = (lon) => ((lon - lon0) / (lon1 - lon0)) * W
  const Y = (lat) => ((lat1 - lat) / (lat1 - lat0)) * H

  const ns = 'http://www.w3.org/2000/svg'
  const el = (tag, attrs, parent) => {
    const n = document.createElementNS(ns, tag)
    for (const k in attrs) n.setAttribute(k, attrs[k])
    ;(parent || svg).appendChild(n)
    return n
  }
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`)
  el('rect', { width: W, height: H, fill: '#0a1420' })
  if (land && land.features) {
    for (const f of land.features) {
      const rings = f.geometry.type === 'Polygon' ? f.geometry.coordinates : f.geometry.type === 'MultiPolygon' ? f.geometry.coordinates.flat() : []
      for (const ring of rings) {
        el('polygon', { points: ring.map(([lon, lat]) => `${X(lon).toFixed(1)},${Y(lat).toFixed(1)}`).join(' '), fill: '#16263a', stroke: '#2d4360', 'stroke-width': 0.8 })
      }
    }
  }
  const modeColour = { road: '#6f8fb8', sea: '#3fb984', air: '#e28b4a', river: '#a8b45c' }
  const hubById = new Map(model.hubs.map((h) => [h.id, h]))
  const facById = new Map(model.facilities.map((f) => [f.id, f]))
  const laneEls = new Map()
  const laneLayer = el('g', { id: 'lanes' })
  model.lanes.forEach((lane, i) => {
    const h = hubById.get(lane.hub_id), f = facById.get(lane.facility_id)
    if (!h || !f) return
    // A gentle arc, as in the tool: two stores reaching one island stay tellable apart.
    const x1 = X(h.lon), y1 = Y(h.lat), x2 = X(f.lon), y2 = Y(f.lat)
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2
    const dx = x2 - x1, dy = y2 - y1
    const len = Math.hypot(dx, dy) || 1
    const cx = mx - (dy / len) * len * 0.12, cy = my + (dx / len) * len * 0.12
    const path = el('path', { d: `M${x1.toFixed(1)},${y1.toFixed(1)} Q${cx.toFixed(1)},${cy.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`, fill: 'none', stroke: modeColour[lane.mode] || '#8fa3b8', 'stroke-width': 0.7, 'stroke-opacity': 0.12, 'stroke-linecap': 'round' }, laneLayer)
    laneEls.set(lane.edge_id, path)
  })
  const facEls = new Map()
  const facLayer = el('g', { id: 'facilities' })
  for (const f of model.facilities) {
    const r = 2.4 + Math.min(3.2, Math.sqrt(f.population || 0) / 140)
    facEls.set(f.id, el('circle', { cx: X(f.lon).toFixed(1), cy: Y(f.lat).toFixed(1), r: r.toFixed(1), fill: '#3c4f66', 'fill-opacity': 0.95 }, facLayer))
  }
  const hubEls = new Map()
  const hubLayer = el('g', { id: 'hubs' })
  for (const h of model.hubs) {
    const g = el('g', {}, hubLayer)
    hubEls.set(h.id, el('circle', { cx: X(h.lon).toFixed(1), cy: Y(h.lat).toFixed(1), r: 6.5, fill: '#4da3ff', stroke: '#ffffff', 'stroke-width': 1.4 }, g))
    el('text', { x: (X(h.lon) + 9).toFixed(1), y: (Y(h.lat) + 4).toFixed(1), 'font-size': 12, fill: '#dbe6f2', 'font-family': 'Public Sans, sans-serif', stroke: '#0a1420', 'stroke-width': 3, 'paint-order': 'stroke' }, g).textContent = h.name.replace(' Area Medical Store', '')
  }
  if (model.national_store) {
    el('circle', { cx: X(model.national_store.lon).toFixed(1), cy: Y(model.national_store.lat).toFixed(1), r: 7.5, fill: '#ffffff', stroke: '#0a1420', 'stroke-width': 1.5 })
  }
  const fillColour = (v) => (v >= 0.999 ? '#3fb984' : v >= 0.9 ? '#9ac95e' : v >= 0.7 ? '#e8b23a' : v >= 0.4 ? '#e28b4a' : '#ef6b6b')

  // --- controls ------------------------------------------------------------------------
  const q = (s) => root.querySelector(s)
  const controls = {
    equity: q('#pg-equity'), service: q('#pg-service'), minfill: q('#pg-minfill'), growth: q('#pg-growth'),
    optimize: q('#pg-optimize'), modes: Array.from(root.querySelectorAll('.pg-mode')),
  }
  const outputs = { equity: q('#pg-equity-out'), service: q('#pg-service-out'), minfill: q('#pg-minfill-out'), growth: q('#pg-growth-out') }
  const kpi = {
    cost: q('#k-cost'), costd: q('#k-cost-delta'), fill: q('#k-fill'), filld: q('#k-fill-delta'), worst: q('#k-worst'), worstd: q('#k-worst-delta'),
    people: q('#k-people'), peopled: q('#k-people-delta'), hubs: q('#k-hubs'), hubsd: q('#k-hubs-delta'), time: q('#k-time'),
  }
  const verdict = q('#pg-verdict')
  const money = (v) => (Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(2) + 'M' : Math.abs(v) >= 1e3 ? (v / 1e3).toFixed(0) + 'k' : v.toFixed(0)) + ' ' + (model.country.currency || '')
  const pct = (v, d = 1) => (v * 100).toFixed(d) + '%'
  const exact = (v) => Math.round(v).toLocaleString('en')
  const signed = (v) => (v > 0 ? '+' : '') + (v * 100).toFixed(1) + '%'

  say('Loading the solver…')
  const highs = await (window.Module ? window.Module({ locateFile: (f) => 'site/vendor/' + f }) : Promise.reject(new Error('solver script missing')))
  say('Solver ready')

  const readControls = () => {
    const minfill = Number(controls.minfill.value)
    return {
      weightCost: 1,
      weightService: Number(controls.service.value),
      weightEquity: Number(controls.equity.value),
      minFill: minfill > 0 ? minfill : null,
      demandGrowth: Number(controls.growth.value),
      optimizeHubs: controls.optimize.checked,
      allowedModes: controls.modes.filter((b) => b.getAttribute('aria-pressed') === 'true').map((b) => b.dataset.mode),
    }
  }
  const showOutputs = () => {
    outputs.equity.value = Number(controls.equity.value).toFixed(2)
    outputs.service.value = Number(controls.service.value).toFixed(2)
    outputs.minfill.value = Number(controls.minfill.value) > 0 ? pct(Number(controls.minfill.value), 0) : 'off'
    outputs.growth.value = signed(Number(controls.growth.value))
  }

  // Today's network: the reference every move is read against.
  const today = HSCNSolver.solve(highs, model, { weightCost: 1, weightService: 1, weightEquity: 0.5 })
  let last = today

  function paint(sol) {
    const used = new Map(sol.flows.map((f) => [f.edge_id, f]))
    const maxV = Math.max(1, ...sol.flows.map((f) => f.volume_m3))
    laneEls.forEach((path, edgeId) => {
      const f = used.get(edgeId)
      path.setAttribute('stroke-opacity', f ? 0.75 : 0.08)
      path.setAttribute('stroke-width', f ? (0.8 + 3.4 * Math.sqrt(f.volume_m3 / maxV)).toFixed(2) : 0.6)
    })
    for (const f of sol.facilities) facEls.get(f.id).setAttribute('fill', fillColour(f.fill))
    const open = new Set(sol.kpi.hubs_open_codes)
    for (const h of model.hubs) {
      const c = hubEls.get(h.id)
      c.setAttribute('fill', open.has(h.code) ? '#4da3ff' : 'transparent')
      c.setAttribute('stroke', open.has(h.code) ? '#ffffff' : '#7a8d9f')
      c.setAttribute('stroke-dasharray', open.has(h.code) ? '' : '2 1.5')
    }
  }
  function delta(el, now, base, better) {
    if (!el) return
    const d = base ? (now - base) / Math.abs(base) : 0
    const dir = Math.abs(d) < 0.0005 ? 'flat' : (d < 0) === (better === 'lower') ? 'better' : 'worse'
    el.className = 'delta ' + dir
    el.textContent = dir === 'flat' ? 'same as today' : signed(d) + ' vs today'
  }
  function report(sol) {
    if (!sol.feasible) {
      verdict.className = 'verdict warn'
      verdict.innerHTML = `<b>No feasible plan.</b> The floor you set cannot be met with these modes and stores. Loosen the fill rate or allow a mode.`
      kpi.time.textContent = sol.runtime_ms + ' ms'
      return
    }
    const k = sol.kpi, t = today.kpi
    kpi.cost.textContent = money(k.total_cost); delta(kpi.costd, k.total_cost, t.total_cost, 'lower')
    kpi.fill.textContent = pct(k.fill_rate); delta(kpi.filld, k.fill_rate, t.fill_rate, 'higher')
    kpi.worst.textContent = pct(k.worst_stratum_fill_rate); delta(kpi.worstd, k.worst_stratum_fill_rate, t.worst_stratum_fill_rate, 'higher')
    kpi.people.textContent = exact(k.people_not_fully_supplied)
    kpi.peopled.className = 'delta ' + (k.people_not_fully_supplied > t.people_not_fully_supplied ? 'worse' : k.people_not_fully_supplied < t.people_not_fully_supplied ? 'better' : 'flat')
    kpi.peopled.textContent = k.people_not_fully_supplied === t.people_not_fully_supplied ? 'same as today' : (k.people_not_fully_supplied - t.people_not_fully_supplied > 0 ? '+' : '') + exact(k.people_not_fully_supplied - t.people_not_fully_supplied) + ' vs today'
    kpi.hubs.textContent = k.hubs_open; kpi.hubsd.className = 'delta flat'; kpi.hubsd.textContent = k.hubs_open_codes.map((c) => c.replace('AMS-', '')).join(' · ')
    kpi.time.textContent = sol.runtime_ms + ' ms'
    const cheaper = k.total_cost < t.total_cost - 1, worse = k.worst_stratum_fill_rate < t.worst_stratum_fill_rate - 0.005
    verdict.className = 'verdict' + (cheaper && worse ? ' warn' : '')
    if (cheaper && worse) verdict.innerHTML = `This plan is <b>${pct(1 - k.total_cost / t.total_cost)} cheaper</b> than today, and the saving is funded by the hardest-to-reach band, whose supply falls to <b>${pct(k.worst_stratum_fill_rate)}</b>. <b>${exact(k.people_not_fully_supplied)}</b> people are no longer fully supplied.`
    else if (cheaper) verdict.innerHTML = `This plan is <b>${pct(1 - k.total_cost / t.total_cost)} cheaper</b> than today without giving up on the hardest-to-reach band.`
    else if (k.total_cost > t.total_cost + 1) verdict.innerHTML = `This plan costs <b>${pct(k.total_cost / t.total_cost - 1)} more</b> than today. What that buys is in the fill and band figures.`
    else verdict.innerHTML = `Today's network: <b>${money(k.total_cost)}</b> a year, <b>${pct(k.fill_rate)}</b> of demand met, every band supplied at <b>${pct(k.worst_stratum_fill_rate)}</b>. Move a control and the network re-solves here, in your browser.`
  }

  paint(today); report(today); showOutputs()
  say(`Solved ${model.facilities.length} facilities · ${model.lanes.length} lanes in ${today.runtime_ms} ms`)

  let timer = null
  function resolve() {
    showOutputs()
    clearTimeout(timer)
    timer = setTimeout(() => {
      say('Solving…')
      const sol = HSCNSolver.solve(highs, model, readControls())
      last = sol
      if (sol.feasible) paint(sol)
      report(sol)
      say(sol.feasible ? `Re-solved in ${sol.runtime_ms} ms · ${sol.lp_size.rows} constraints` : `No feasible plan (${sol.runtime_ms} ms)`)
    }, 60)
  }
  for (const key of ['equity', 'service', 'minfill', 'growth']) controls[key].addEventListener('input', resolve)
  controls.optimize.addEventListener('change', resolve)
  for (const b of controls.modes) b.addEventListener('click', () => { b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true'); resolve() })
  const presets = root.querySelectorAll('[data-preset]')
  for (const p of presets) p.addEventListener('click', () => {
    const preset = p.dataset.preset
    controls.equity.value = preset === 'cheapest' ? 0 : preset === 'fair' ? 1 : 0.5
    controls.service.value = preset === 'cheapest' ? 0.25 : 1
    controls.minfill.value = preset === 'fair' ? 0.95 : 0
    controls.growth.value = preset === 'growth' ? 0.3 : 0
    controls.optimize.checked = preset !== 'today'
    for (const b of controls.modes) b.setAttribute('aria-pressed', preset === 'roads' ? String(b.dataset.mode === 'road') : 'true')
    resolve()
  })
})().catch((e) => {
  const status = document.querySelector('#playground .pg-status')
  if (status) status.textContent = 'The live solver could not start here: ' + (e && e.message ? e.message : e)
})
