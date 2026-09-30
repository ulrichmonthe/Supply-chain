/*
 * Marketing site check: axe over every page, the live solver actually re-solves when a
 * lever moves, the CSV mapper plots the sample, the decision page renders, no horizontal
 * scroll at phone width. Screenshots land in SHOTS_DIR.
 *
 *   python3 -m http.server 8090 --directory docs   # in one terminal
 *   SITE_URL=http://127.0.0.1:8090/ node scripts/site-check.mjs
 */
import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const AXE = fs.readFileSync(path.join(HERE, '..', 'node_modules', 'axe-core', 'axe.min.js'), 'utf8')
const URL = process.env.SITE_URL ?? 'http://127.0.0.1:8090/'
const SHOTS = process.env.SHOTS_DIR ?? path.join(HERE, '..', '..', 'docs', 'site', 'preview')
const EXECUTABLE = fs.existsSync('/opt/pw-browsers/chromium') ? '/opt/pw-browsers/chromium' : undefined
const PAGES = ['index.html', 'data.html', 'ask.html', 'trust.html', 'decide.html', 'keep.html', 'compare.html']

fs.mkdirSync(SHOTS, { recursive: true })
const failures = []
const check = (ok, message) => { console.log(`${ok ? 'ok  ' : 'FAIL'}  ${message}`); if (!ok) failures.push(message) }

const browser = await chromium.launch({ executablePath: EXECUTABLE })
const page = await browser.newPage({ viewport: { width: 1360, height: 900 }, colorScheme: 'dark' })
page.on('pageerror', (e) => check(false, `page error: ${e.message}`))
page.on('console', (m) => { if (m.type() === 'error') console.log('   console:', m.text().slice(0, 200)) })

for (const p of PAGES) {
  await page.goto(URL + p, { waitUntil: 'networkidle' })
  if (p === 'index.html') {
    await page.waitForFunction(() => /Solved|Re-solved/.test(document.querySelector('.pg-status')?.textContent ?? ''), null, { timeout: 60000 })
    await page.keyboard.press('Tab')
    const first = await page.evaluate(() => document.activeElement?.textContent?.trim())
    check(first === 'Skip to content', `home: first tab stop is the skip link (${first})`)
    const before = await page.textContent('#k-cost')
    check(/PGK/.test(before), `home: baseline cost rendered (${before.trim()})`)
    await page.click('[data-preset="cheapest"]')
    await page.waitForFunction((b) => document.querySelector('#k-cost').textContent !== b && /Re-solved/.test(document.querySelector('.pg-status').textContent), before, { timeout: 30000 })
    const after = await page.textContent('#k-cost'), worst = await page.textContent('#k-worst'), status = await page.textContent('.pg-status')
    check(after !== before, `home: cheapest preset re-solved in the browser (${before.trim()} → ${after.trim()}, worst band ${worst.trim()}; ${status.trim()})`)
    const verdict = await page.textContent('#pg-verdict')
    check(/cheaper/.test(verdict), `home: verdict names the trade-off: "${verdict.trim().slice(0, 120)}"`)
    await page.screenshot({ path: path.join(SHOTS, 'home-cheapest.png') })
    await page.click('[data-preset="today"]')
    await page.waitForFunction((b) => document.querySelector('#k-cost').textContent === b, before, { timeout: 30000 }).catch(() => {})
    const back = await page.textContent('#k-cost')
    check(back === before, `home: "Today" preset returns to the baseline (${back.trim()})`)
    if (process.env.OG_PATH) {
      await page.setViewportSize({ width: 1200, height: 630 })
      const og = await page.addStyleTag({ content: 'html{scroll-behavior:auto!important}.pg-side{display:none}.playground{grid-template-columns:1fr;border-radius:0;border:0}.pg-map{height:630px}body{padding:0}' })
      await page.evaluate(() => document.getElementById('playground').scrollIntoView({ behavior: 'instant', block: 'start' }))
      await page.waitForTimeout(300)
      await page.screenshot({ path: process.env.OG_PATH, clip: { x: 0, y: 0, width: 1200, height: 630 } })
      await og.evaluate((n) => n.remove())
      await page.setViewportSize({ width: 1360, height: 900 })
      await page.evaluate(() => window.scrollTo(0, 0))
    }
  }
  if (p === 'data.html') {
    await page.click('#mapper-sample')
    await page.waitForFunction(() => /plotted/.test(document.querySelector('#mapper-status')?.textContent ?? ''), null, { timeout: 30000 })
    const st = await page.textContent('#mapper-status')
    check(/20 of 20 rows plotted/.test(st), `data: sample mapped and plotted: "${st.replace(/\s+/g, ' ').trim().slice(0, 140)}"`)
    const mapped = await page.evaluate(() => Object.fromEntries([...document.querySelectorAll('#map-fields select')].map((s) => [s.dataset.field, s.value])))
    check(mapped.lat === 'Latitude' && mapped.name === 'Health Facility' && mapped.catchment_population === 'Population Served' && mapped.code === 'Facility Code', `data: synonyms matched (${JSON.stringify(mapped)})`)
    await page.waitForTimeout(1500)
  }
  if (p === 'decide.html') {
    await page.waitForFunction(() => /Generated/.test(document.querySelector('#decide-status')?.textContent ?? ''), null, { timeout: 30000 })
    const v = await page.textContent('#decision-sheet .verdict')
    check(/less|more/.test(v), `decide: page one generated: "${v.trim().slice(0, 140)}"`)
    const n = await page.$$eval('#decide-pick option', (o) => o.length)
    check(n >= 5, `decide: ${n} scenarios selectable`)
    await page.selectOption('#decide-pick', { index: n - 1 })
    await page.waitForTimeout(800)
    const v2 = await page.textContent('#decision-sheet .verdict')
    check(v2 !== v, `decide: switching scenario re-renders`)
  }
  await page.screenshot({ path: path.join(SHOTS, p.replace('.html', '') + '.png'), fullPage: p !== 'index.html' })
  if (p === 'index.html') await page.screenshot({ path: path.join(SHOTS, 'home-full.png'), fullPage: true })
  await page.addScriptTag({ content: AXE })
  const result = await page.evaluate(async () => await window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'] } }))
  const serious = result.violations.filter((v) => ['critical', 'serious'].includes(v.impact))
  check(serious.length === 0, `${p}: axe ${result.violations.length} violations, ${serious.length} serious/critical` + (result.violations.length ? ' — ' + result.violations.map((v) => `${v.id}(${v.impact}) x${v.nodes.length}: ${v.nodes[0].target[0]}`).join('; ') : ''))
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1)
  check(!wide, `${p}: no horizontal scroll at 1360`)
}
// Phone width
const phone = await browser.newPage({ viewport: { width: 390, height: 844 }, colorScheme: 'light' })
for (const p of PAGES) {
  await phone.goto(URL + p, { waitUntil: 'networkidle' })
  await phone.waitForTimeout(400)
  const wide = await phone.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1)
  check(!wide, `${p}: no horizontal scroll at 390 (light)`)
  if (p === 'index.html') await phone.screenshot({ path: path.join(SHOTS, 'home-phone.png'), fullPage: true })
}
await browser.close()
console.log(failures.length ? `\n${failures.length} failure(s)` : '\nall site checks passed')
process.exit(failures.length ? 1 : 0)
