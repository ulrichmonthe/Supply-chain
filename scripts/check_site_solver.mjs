// Proves the in-browser solver reproduces the server's answers for the captured demo.
// Usage: node scripts/check_site_solver.mjs [path-to-highs-package]
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import path from 'node:path'
const require = createRequire(import.meta.url)
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..')
const highsPkg = process.argv[2] || path.join(root, 'docs/site/vendor/highs.js')
const loadHighs = require(highsPkg)
const Solver = require(path.join(root, 'docs/site/solver.js'))
const model = JSON.parse(readFileSync(path.join(root, 'docs/site/model/baseline.json'), 'utf8'))
const card = JSON.parse(readFileSync(path.join(root, 'docs/site/model/scorecard.json'), 'utf8'))
const row = (start) => card.rows.find((r) => r.name.startsWith(start))
const highs = await loadHighs()
let failures = 0
const check = (label, got, want, tol) => {
  const ok = Math.abs(got - want) <= tol * Math.max(1, Math.abs(want))
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${label}: got ${got.toFixed ? got.toFixed(4) : got}, server ${want}`)
  if (!ok) failures++
}
// Baseline: today's stores forced, weights 1 / 1 / 0.5.
let s = Solver.solve(highs, model, { weightCost: 1, weightService: 1, weightEquity: 0.5 })
console.log('baseline status', s.status, 'in', s.runtime_ms, 'ms;', s.lp_size)
check('baseline total cost', s.kpi.total_cost, row('Baseline').kpi_set.total_cost, 0.001)
check('baseline fill', s.kpi.fill_rate, row('Baseline').kpi_set.fill_rate, 0.001)
check('baseline Q-worst fill', s.kpi.worst_stratum_fill_rate, row('Baseline').kpi_set.worst_stratum_fill_rate, 0.001)
// Cost optimisation, unconstrained: stores free, service weight 0.25, equity 0.
s = Solver.solve(highs, model, { weightCost: 1, weightService: 0.25, weightEquity: 0, optimizeHubs: true })
console.log('cost-opt status', s.status, 'in', s.runtime_ms, 'ms; hubs', s.kpi.hubs_open_codes.join(','))
const opt = row('Cost optimisation — unconstrained').kpi_set
check('cost-opt total cost', s.kpi.total_cost, opt.total_cost, 0.002)
check('cost-opt fill', s.kpi.fill_rate, opt.fill_rate, 0.002)
check('cost-opt worst band fill', s.kpi.worst_stratum_fill_rate, opt.worst_stratum_fill_rate, 0.005)
check('cost-opt hubs open', s.kpi.hubs_open, opt.hubs_open, 0)
// With the 90% equity floor.
s = Solver.solve(highs, model, { weightCost: 1, weightService: 0.25, weightEquity: 0, optimizeHubs: true, equityFloor: 0.9 })
const floor = row('Cost optimisation with a 90%').kpi_set
check('equity-floor total cost', s.kpi.total_cost, floor.total_cost, 0.002)
check('equity-floor worst band', s.kpi.worst_stratum_fill_rate, floor.worst_stratum_fill_rate, 0.005)
console.log(failures ? `\n${failures} check(s) failed` : '\nall checks passed')
process.exit(failures ? 1 : 0)
