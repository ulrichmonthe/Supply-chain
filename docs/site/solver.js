/* The allocation model, solved in the visitor's browser.
 *
 * This is the same model the tool's server hands to HiGHS, exported as data
 * (`model/baseline.json`) and rebuilt here as CPLEX LP text for the HiGHS
 * WebAssembly build. Nothing is pre-solved: move a weight and the network is
 * re-optimised on this machine. The test in scripts/check_site_solver.mjs proves the
 * answer matches the server's own result for the baseline.
 *
 * Works in a browser (global `HSCNSolver`) and in Node (module.exports).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory()
  else root.HSCNSolver = factory()
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict'

  function penalty(model, facility, equityWeight) {
    const v = Math.max(0, Math.min(1, facility.vulnerability))
    return model.base_penalty * (1 + 3 * Math.max(0, equityWeight) * v)
  }

  /** Build the LP text for the model under the given controls. */
  function buildLp(model, controls) {
    const c = Object.assign(
      {
        weightCost: 1,
        weightService: 1,
        weightEquity: 0,
        minFill: null, // 0..1 or null
        equityFloor: null, // 0..1 or null
        maxBudget: null,
        optimizeHubs: false,
        allowedModes: null, // array or null = all
        respectCapacity: true,
        demandGrowth: 0,
        closedHubs: [], // codes forced closed
        openHubs: [], // codes forced open
      },
      controls || {},
    )
    const growth = 1 + (c.demandGrowth || 0)
    const modes = c.allowedModes ? new Set(c.allowedModes) : null
    const facilities = model.facilities
    const hubs = model.hubs
    const lanes = model.lanes.filter((l) => (modes ? modes.has(l.mode) : true))
    const fById = new Map(facilities.map((f) => [f.id, f]))
    const hById = new Map(hubs.map((h) => [h.id, h]))
    const demand = (f) => f.demand_m3 * growth

    const hubState = (h) => {
      if (c.closedHubs.includes(h.code)) return false
      if (c.openHubs.includes(h.code)) return true
      if (c.optimizeHubs) return null
      return h.forced_open
    }

    const obj = []
    const bounds = []
    const rows = []
    const binaries = []
    const laneVar = (i) => `x${i}`
    const unmetVar = (f) => `u${f.id}`
    const hubVar = (h) => `y${h.id}`

    const ub = (lane, f) => {
      let b = demand(f)
      if (c.respectCapacity && lane.capacity_m3 > 0) b = Math.min(b, lane.capacity_m3)
      return Math.max(0, b)
    }

    lanes.forEach((lane, i) => {
      const f = fById.get(lane.facility_id)
      const coef = c.weightCost * lane.unit_cost
      obj.push(`${fmt(coef)} ${laneVar(i)}`)
      bounds.push(`0 <= ${laneVar(i)} <= ${fmt(ub(lane, f))}`)
    })
    facilities.forEach((f) => {
      obj.push(`${fmt(c.weightService * penalty(model, f, c.weightEquity))} ${unmetVar(f)}`)
      bounds.push(`0 <= ${unmetVar(f)} <= ${fmt(demand(f))}`)
    })
    hubs.forEach((h) => {
      const state = hubState(h)
      obj.push(`${fmt(c.weightCost * h.annual_charge)} ${hubVar(h)}`)
      if (state === true) bounds.push(`${hubVar(h)} = 1`)
      else if (state === false) bounds.push(`${hubVar(h)} = 0`)
      else {
        bounds.push(`0 <= ${hubVar(h)} <= 1`)
        binaries.push(hubVar(h))
      }
    })

    // Demand balance.
    const lanesByFacility = new Map()
    const lanesByHub = new Map()
    lanes.forEach((lane, i) => {
      if (!lanesByFacility.has(lane.facility_id)) lanesByFacility.set(lane.facility_id, [])
      lanesByFacility.get(lane.facility_id).push(i)
      if (!lanesByHub.has(lane.hub_id)) lanesByHub.set(lane.hub_id, [])
      lanesByHub.get(lane.hub_id).push(i)
    })
    facilities.forEach((f) => {
      const idx = lanesByFacility.get(f.id) || []
      const terms = idx.map((i) => laneVar(i)).concat([unmetVar(f)])
      rows.push(`d${f.id}: ${terms.join(' + ')} = ${fmt(demand(f))}`)
    })
    // Lanes need their hub open.
    lanes.forEach((lane, i) => {
      const h = hById.get(lane.hub_id)
      if (hubState(h) === true) return
      const big = ub(lane, fById.get(lane.facility_id))
      if (big > 0) rows.push(`l${i}: ${laneVar(i)} - ${fmt(big)} ${hubVar(h)} <= 0`)
    })
    // Hub throughput.
    if (c.respectCapacity) {
      hubs.forEach((h) => {
        const idx = lanesByHub.get(h.id) || []
        if (idx.length && h.throughput_m3 > 0) {
          rows.push(`t${h.id}: ${idx.map((i) => laneVar(i)).join(' + ')} - ${fmt(h.throughput_m3)} ${hubVar(h)} <= 0`)
        }
      })
    }
    const total = facilities.reduce((s, f) => s + demand(f), 0)
    if (c.minFill !== null && c.minFill !== undefined) {
      rows.push(`fill: ${facilities.map(unmetVar).join(' + ')} <= ${fmt((1 - c.minFill) * total)}`)
    }
    if (c.equityFloor !== null && c.equityFloor !== undefined && c.equityFloor > 0) {
      const byStratum = new Map()
      facilities.forEach((f) => {
        if (!byStratum.has(f.stratum)) byStratum.set(f.stratum, [])
        byStratum.get(f.stratum).push(f)
      })
      byStratum.forEach((members, s) => {
        const d = members.reduce((a, f) => a + demand(f), 0)
        if (d > 0) rows.push(`eq${s}: ${members.map(unmetVar).join(' + ')} <= ${fmt((1 - c.equityFloor) * d)}`)
      })
    }
    if (c.maxBudget) {
      const terms = lanes.map((l, i) => `${fmt(l.unit_cost)} ${laneVar(i)}`).concat(hubs.map((h) => `${fmt(h.annual_charge)} ${hubVar(h)}`))
      rows.push(`budget: ${terms.join(' + ')} <= ${fmt(c.maxBudget)}`)
    }

    const lp =
      'Minimize\n obj: ' +
      obj.join(' + ') +
      '\nSubject To\n ' +
      rows.join('\n ') +
      '\nBounds\n ' +
      bounds.join('\n ') +
      (binaries.length ? '\nBinary\n ' + binaries.join(' ') : '') +
      '\nEnd\n'
    return { lp, lanes, controls: c, total }
  }

  function fmt(n) {
    if (!isFinite(n)) return '0'
    const s = Number(n).toFixed(6)
    return s.replace(/\.?0+$/, '') || '0'
  }

  /** Solve and read the answer back as KPIs, per-facility fill and per-lane flow. */
  function solve(highs, model, controls) {
    const built = buildLp(model, controls)
    const t0 = (typeof performance !== 'undefined' ? performance : Date).now()
    let result
    try {
      result = highs.solve(built.lp, { output_flag: false, mip_rel_gap: 0.005 })
    } catch (e) {
      return { status: 'error', error: String(e), runtime_ms: 0 }
    }
    const runtime_ms = Math.round((typeof performance !== 'undefined' ? performance : Date).now() - t0)
    if (result.Status !== 'Optimal') return { status: result.Status, runtime_ms, feasible: false }
    const col = (name) => (result.Columns[name] ? result.Columns[name].Primal : 0)
    const growth = 1 + (built.controls.demandGrowth || 0)
    const served = new Map()
    const flows = []
    let transport = 0
    built.lanes.forEach((lane, i) => {
      const v = col(`x${i}`)
      if (v <= 1e-6) return
      transport += v * lane.unit_cost
      served.set(lane.facility_id, (served.get(lane.facility_id) || 0) + v)
      flows.push({ edge_id: lane.edge_id, hub_id: lane.hub_id, facility_id: lane.facility_id, mode: lane.mode, volume_m3: v })
    })
    const hubsOpen = model.hubs.filter((h) => col(`y${h.id}`) > 0.5)
    const hubFixed = hubsOpen.reduce((s, h) => s + h.annual_charge, 0)
    const total = built.total
    let delivered = 0
    let covered = 0
    let population = 0
    const perFacility = model.facilities.map((f) => {
      const d = f.demand_m3 * growth
      const s = Math.min(d, served.get(f.id) || 0)
      delivered += s
      const fill = d > 0 ? s / d : 1
      population += f.population
      covered += f.population * fill
      return { id: f.id, code: f.code, name: f.name, fill, served_m3: s, demand_m3: d, stratum: f.stratum, population: f.population, lat: f.lat, lon: f.lon }
    })
    // Fill by band, Q1 least vulnerable ... Q5 most.
    const bands = (model.strata_labels || []).map((label, i) => {
      const members = perFacility.filter((f) => f.stratum === i)
      const d = members.reduce((a, f) => a + f.demand_m3, 0)
      const s = members.reduce((a, f) => a + f.served_m3, 0)
      return { label, fill: d > 0 ? s / d : 1, population: members.reduce((a, f) => a + f.population, 0), facilities: members.length }
    })
    const fills = bands.filter((b) => b.facilities).map((b) => b.fill)
    const notFull = perFacility.filter((f) => f.fill < 0.999)
    return {
      status: 'optimal',
      feasible: true,
      runtime_ms,
      kpi: {
        total_cost: transport + hubFixed,
        transport_cost: transport,
        hub_fixed_cost: hubFixed,
        fill_rate: total > 0 ? delivered / total : 1,
        delivered_m3: delivered,
        unmet_m3: Math.max(0, total - delivered),
        worst_stratum_fill_rate: fills.length ? Math.min(...fills) : 1,
        q5_fill_rate: bands.length ? bands[bands.length - 1].fill : 1,
        equity_gap: fills.length ? Math.max(...fills) - Math.min(...fills) : 0,
        population_coverage: population > 0 ? covered / population : 1,
        people_not_fully_supplied: notFull.reduce((a, f) => a + f.population, 0),
        facilities_not_fully_supplied: notFull.length,
        hubs_open: hubsOpen.length,
        hubs_open_codes: hubsOpen.map((h) => h.code),
      },
      bands,
      facilities: perFacility,
      flows,
      lp_size: { rows: (built.lp.match(/\n/g) || []).length, lanes: built.lanes.length },
    }
  }

  return { buildLp, solve, penalty }
})
