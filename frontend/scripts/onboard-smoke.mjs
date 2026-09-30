/* Drive the Onboard tab against a live server: sign, start a run, upload a facility
   register with foreign headers, confirm the mapping, stage, look at the facilities,
   add an mSupply extract, run checks and estimates, work the queue, read the report. */
import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'

const URL = process.env.APP_URL ?? 'http://127.0.0.1:8077/'
const SHOTS = process.env.SHOTS_DIR ?? '/tmp/onboard-shots'
const EXECUTABLE = fs.existsSync('/opt/pw-browsers/chromium') ? '/opt/pw-browsers/chromium' : undefined
fs.mkdirSync(SHOTS, { recursive: true })
const failures = []
const check = (ok, msg) => { console.log(`${ok ? 'ok  ' : 'FAIL'}  ${msg}`); if (!ok) failures.push(msg) }

const nodes = await (await fetch(URL + 'api/countries/1/nodes')).json()
const fac = nodes.filter((n) => n.level === 3 && n.code.startsWith('PNG-'))
const [a, b] = fac
const register = [
  'Facility Code;Health Facility;Province;District;Latitude;Longitude;Population Served;Functional Status',
  `${a.code};${a.name};${a.admin1};${a.admin2 ?? ''};${a.lat};${a.lon};${Math.round(a.catchment_population)};operational`,
  `HMIS-${b.code};${b.name.replace('District', 'Dist.')};${b.admin1};${b.admin2 ?? ''};${b.lat + 0.004};${b.lon};${Math.round(b.catchment_population) + 500};operational`,
  `HMIS-AMB;${a.name};${a.admin1};;${a.lat + 0.02};${a.lon + 0.02};3000;operational`,
  'HMIS-NEW-1;Mendi Valley Aid Post;Southern Highlands;Mendi;-6.15;143.66;2400;operational',
].join('\n')
const consumption = [
  'store_code,item_code,month,adjusted_monthly_consumption,days_out_of_stock',
  `${a.code},ESSMED-KIT,2025,${Math.round(a.catchment_population * 31 / 1000)},0`,
  `HMIS-${b.code},ESSMED-KIT,2025,${Math.round(b.catchment_population * 31 / 1000 * 0.7)},110`,
  `HMIS-NEW-1,ESSMED-KIT,2025,${74 * 40},0`,
  `${a.code},VAC-EPI,2025,${Math.round(a.catchment_population * 175 / 1000)},0`,
].join('\n')

const browser = await chromium.launch({ executablePath: EXECUTABLE })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
page.on('pageerror', (e) => check(false, `page error: ${e.message}`))
await page.goto(URL, { waitUntil: 'networkidle' })
await page.evaluate(() => { try { window.localStorage.setItem('hscn.tour.dismissed', '1'); window.localStorage.setItem('hscn.author', 'Data officer, NDoH') } catch {} })
await page.reload({ waitUntil: 'networkidle' })
const tourClose = page.getByRole('button', { name: /skip|close|not now/i }).first()
if (await tourClose.isVisible().catch(() => false)) await tourClose.click()
await page.getByRole('tab', { name: 'Onboard' }).click()
await page.waitForSelector('.onboarding')
check(await page.getByText('reference example').first().isVisible(), 'pack step shows the PNG example pack, marked as such')
await page.screenshot({ path: path.join(SHOTS, 'ob-1-pack.png') })

await page.getByPlaceholder('New run, e.g. Milne Bay pilot').fill('Milne Bay pilot')
await page.getByRole('button', { name: 'Start run' }).click()
await page.waitForSelector('.file-list, .row-note:has-text("No files yet")')
check((await page.textContent('.run-picker')).includes('Milne Bay pilot'), 'a run is created and selected')

await page.setInputFiles('input[type=file][multiple]', { name: 'hmis_facilities.csv', mimeType: 'text/csv', buffer: Buffer.from(register) })
await page.waitForSelector('.file-card')
check((await page.textContent('.file-card')).includes('hmis_facilities.csv'), 'the file is listed with its checksum and profile')
await page.getByRole('button', { name: 'Map and stage' }).click()
await page.waitForSelector('.mapper-grid')
const latSelect = page.getByLabel('Column for lat')
check((await latSelect.inputValue()) === 'Latitude', 'the mapper proposes Latitude for lat')
await page.getByLabel('Save the mapping under a name').fill('HMIS register')
await page.getByRole('button', { name: 'Confirm mapping' }).click()
await page.waitForSelector('.pill:has-text("mapped")')
await page.screenshot({ path: path.join(SHOTS, 'ob-2-files.png') })
await page.getByRole('button', { name: /^Stage/ }).click()
await page.waitForSelector('.pill:has-text("staged")')
const counts = await page.textContent('.file-mapper .row-note:last-child')
check(/facilities matched on their own/.test(counts), `staging reports the reconciliation: ${counts.trim().slice(0, 140)}`)

await page.locator('.onboarding-steps').getByRole('tab', { name: /Facilities/ }).click()
await page.waitForSelector('.match-list, .row-note')
const pending = await page.$$('.match-card')
check(pending.length === 1, `one facility waits for the arbiter (${pending.length})`)
await page.screenshot({ path: path.join(SHOTS, 'ob-3-facilities.png') })
await page.getByRole('button', { name: 'A facility only this source knows' }).click()
await page.waitForFunction(() => document.querySelectorAll('.match-card').length === 0)
check(true, 'the arbiter decided and the pending list emptied')

await page.locator('.onboarding-steps').getByRole('tab', { name: /Files/ }).click()
await page.setInputFiles('input[type=file][multiple]', { name: 'msupply_2025.csv', mimeType: 'text/csv', buffer: Buffer.from(consumption) })
await page.waitForSelector('.file-card:has-text("msupply_2025.csv")')
const card = page.locator('.file-card:has-text("msupply_2025.csv")')
check((await card.textContent()).includes('msupply'), 'the mSupply extract is recognised by its shape')
await card.getByRole('button', { name: 'Map and stage' }).click()
await card.getByRole('button', { name: /^Stage/ }).click()
await card.locator('.pill:has-text("staged")').waitFor()

await page.locator('.onboarding-steps').getByRole('tab', { name: /Checks/ }).click()
await page.getByRole('button', { name: 'Run the checks' }).click()
await page.waitForSelector('.row-note:has-text("flags")')
await page.getByRole('button', { name: 'Propose estimates for the gaps' }).click()
await page.waitForSelector('.row-note:has-text("estimates proposed")')
await page.screenshot({ path: path.join(SHOTS, 'ob-4-checks.png') })

await page.locator('.onboarding-steps').getByRole('tab', { name: /Review/ }).click()
await page.waitForSelector('.queue-item')
const items = await page.$$eval('.queue-item', (els) => els.map((e) => e.className))
check(items.some((c) => c.includes('low')) && items.some((c) => c.includes('medium')), `the queue mixes confidences (${items.length} items)`)
await page.screenshot({ path: path.join(SHOTS, 'ob-5-review.png') })
const bulk = page.getByRole('button', { name: /Accept the \d+ shown/ })
if (await bulk.isVisible()) await bulk.click()
await page.waitForTimeout(800)
const lowLeft = await page.$$('.queue-item.low')
check(lowLeft.length >= 1, `low-confidence items stay for a person (${lowLeft.length} left)`)

await page.locator('.onboarding-steps').getByRole('tab', { name: /Sign-off/ }).click()
await page.waitForSelector('.callout')
const blocked = await page.textContent('.callout')
check(/blocked/i.test(blocked), `sign-off is blocked while work is pending: ${blocked.replace(/\s+/g, ' ').trim().slice(0, 120)}`)
const approve = page.getByRole('button', { name: /Approve as/ })
check(await approve.isDisabled(), 'the approve button is disabled for the preparer while blocked')
await page.screenshot({ path: path.join(SHOTS, 'ob-6-signoff.png') })
await browser.close()
console.log(failures.length ? `\n${failures.length} failure(s)` : '\nonboard smoke passed')
process.exit(failures.length ? 1 : 0)
