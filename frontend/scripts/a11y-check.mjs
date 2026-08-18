/*
 * Accessibility regression check.
 *
 * Runs axe-core over every tab of the running app, then exercises the things axe
 * cannot see: whether the skip link is really the first tab stop, whether the
 * tablist answers arrow keys, whether a month can be picked without a mouse, and
 * whether any layout produces horizontal page scroll.
 *
 *   make run            # in one terminal, serves the built app on :8000
 *   npm run a11y        # in another
 *
 * Exits non-zero on any violation, so it can gate a build.
 */
import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const AXE = fs.readFileSync(path.join(HERE, '..', 'node_modules', 'axe-core', 'axe.min.js'), 'utf8')
const URL = process.env.APP_URL ?? 'http://127.0.0.1:8000/'
const TABS = ['Scorecard', 'Equity', 'Facilities', 'Services', 'Season', 'Data', 'Live', 'Provenance', 'Roadmap']

// The container image ships one Chromium at a fixed path; fall back to whatever
// Playwright resolves for itself elsewhere.
const EXECUTABLE = fs.existsSync('/opt/pw-browsers/chromium') ? '/opt/pw-browsers/chromium' : undefined

const failures = []
const check = (ok, message) => {
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${message}`)
  if (!ok) failures.push(message)
}

const browser = await chromium.launch({ executablePath: EXECUTABLE })
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } })
const consoleErrors = []
page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()))
page.on('pageerror', (e) => consoleErrors.push(String(e)))

await page.goto(URL, { waitUntil: 'networkidle' })
await page.waitForTimeout(2500)

// --- the first tab stop is the skip link, and it goes somewhere -------------
await page.keyboard.press('Tab')
const firstStop = await page.evaluate(() => {
  const a = document.activeElement
  return { text: a?.textContent?.trim(), onScreen: (a?.getBoundingClientRect().left ?? -1) >= 0 }
})
check(firstStop.text === 'Skip to results', `first tab stop is the skip link (got ${JSON.stringify(firstStop.text)})`)
check(firstStop.onScreen, 'skip link is visible once focused')
await page.keyboard.press('Enter')
check((await page.evaluate(() => location.hash)) === '#results', 'skip link targets the results panel')

// --- axe, on every tab ------------------------------------------------------
const audit = async (label) => {
  await page.addScriptTag({ content: AXE })
  const result = await page.evaluate(
    async () =>
      await window.axe.run(document, {
        runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
      }),
  )
  check(result.violations.length === 0, `${label}: no WCAG 2.1 AA violations`)
  for (const v of result.violations) {
    console.log(`        [${v.impact}] ${v.id} ×${v.nodes.length}  ${v.nodes[0]?.target.join(' ')}`)
  }
}

await audit('Scorecard')
for (const name of TABS.slice(1)) {
  await page.getByRole('tab', { name, exact: true }).click()
  await page.waitForTimeout(600)
  await audit(name)
}

// --- the tablist answers arrow keys ----------------------------------------
await page.getByRole('tab', { name: 'Scorecard', exact: true }).click()
await page.getByRole('tab', { name: 'Scorecard', exact: true }).focus()
await page.keyboard.press('ArrowRight')
await page.keyboard.press('ArrowRight')
const selected = await page.evaluate(
  () => document.querySelector('[role=tab][aria-selected=true]')?.textContent?.trim(),
)
check(selected === 'Facilities', `two ArrowRight moves the tablist two tabs (got ${selected})`)

// --- a month can be chosen without a mouse ---------------------------------
await page.getByRole('tab', { name: 'Scorecard', exact: true }).click()
await page.getByRole('button', { name: 'Feb', exact: true }).focus()
await page.keyboard.press('Enter')
await page.waitForTimeout(3000)
const month = await page.evaluate(() => document.querySelector('.months [aria-pressed=true]')?.textContent?.trim())
check(month === 'Feb', `Enter on a month cell selects it (got ${month})`)

// --- focus is visible on a keyboard-focused control ------------------------
await page.getByRole('tab', { name: 'Facilities', exact: true }).click()
await page.waitForTimeout(500)
let ring = null
for (let i = 0; i < 40 && !ring; i++) {
  await page.keyboard.press('Tab')
  ring = await page.evaluate(() => {
    const a = document.activeElement
    if (!a || !String(a.className ?? '').includes('chip')) return null
    return getComputedStyle(a).outlineWidth
  })
}
check(ring !== null && parseFloat(ring) >= 2, `keyboard focus draws a ring of at least 2px (got ${ring})`)

// --- no layout produces horizontal page scroll -----------------------------
for (const width of [1440, 1280, 1100, 1024, 900, 768]) {
  await page.setViewportSize({ width, height: 900 })
  await page.waitForTimeout(600)
  const scrolls = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)
  check(!scrolls, `no horizontal page scroll at ${width}px`)
}

// --- every target is thumb-sized under a coarse pointer --------------------
const touch = await browser.newContext({ viewport: { width: 900, height: 1000 }, hasTouch: true, isMobile: true })
const touchPage = await touch.newPage()
await touchPage.goto(URL, { waitUntil: 'networkidle' })
await touchPage.waitForTimeout(2500)
const small = await touchPage.evaluate(() =>
  [...document.querySelectorAll('.btn, .chip, .month-cell, .tab')]
    .map((n) => ({ cls: n.className, h: Math.round(n.getBoundingClientRect().height) }))
    .filter((x) => x.h > 0 && x.h < 32),
)
check(small.length === 0, `every coarse-pointer target is at least 32px tall (${small.length} under)`)

check(consoleErrors.length === 0, `no console errors (${consoleErrors.length})`)
if (consoleErrors.length) console.log(consoleErrors.slice(0, 5).join('\n'))

await browser.close()
console.log(failures.length === 0 ? '\nall checks passed' : `\n${failures.length} check(s) failed`)
process.exit(failures.length === 0 ? 0 : 1)
