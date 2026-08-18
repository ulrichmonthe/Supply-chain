# Design audit — `ui-ux-pro-max`

The skill lives in `.claude/skills/ui-ux-pro-max/`, vendored from `ulrichmonthe/brandwright`
(see the README beside it for what did and did not come across). Its Quick Reference is
about 250 rules in ten priority categories, most of them citing Apple's Human Interface
Guidelines, Material Design, or WCAG. This is the audit of the application against them:
what already complied, what was changed, and what was deliberately left alone.

Everything claimed here is checked by `npm run a11y` (`frontend/scripts/a11y-check.mjs`),
which runs axe-core over all nine tabs of the running app and then tests the things axe
cannot see. It exits non-zero on any failure. Current state: **23 checks, all passing, on
every tab, at six viewport widths.**

```
make run          # one terminal
cd frontend && npm run a11y
```

---

## 1. Accessibility (skill priority: CRITICAL)

### `color-contrast` — was failing

`--dim` was `#64788f`. It carries most of the explanatory prose in the application: the
lever notes, the row sub-lines under every facility, the provenance captions. Measured
against the surfaces it actually sits on:

| surface | old `#64788f` | new `#8697aa` |
| --- | --- | --- |
| `--bg` `#0d1219` | 4.14 | 6.28 |
| `--panel` `#141c26` | 3.78 | 5.74 |
| `--panel-2` `#1a2431` | 3.45 | 5.24 |
| `--panel-3` `#212d3d` | 3.07 | 4.66 |

WCAG AA wants 4.5:1 for body text. Three of the four surfaces failed, and the worst was
the one used for the densest text. The replacement holds the same hue and saturation and
clears 4.5:1 everywhere, worst case 4.66:1.

`--muted` (5.37 worst case) and `--text` (11.81 worst case) already passed and were not
touched. Reverting `--dim` to the old value makes the check fail with
`color-contrast ×46 [serious]` — the guard has teeth.

### `focus-states` — was absent

There were no focus styles anywhere, and one rule actively removed the map canvas's. A
single `:focus-visible` rule now draws a 2px `--accent` ring at 2px offset on every
button, link, input, select, textarea and `[tabindex]`. It is `:focus-visible`, not
`:focus`, so a mouse click does not leave a ring behind.

### `keyboard-nav` — six controls were unreachable

Six elements had `onClick` on a `<span>` or `<div>`. A mouse worked; a keyboard did not
reach them at all, and a screen reader announced nothing:

| where | was | now |
| --- | --- | --- |
| facility sort chips | `<span onClick>` | `<button aria-pressed>` in a labelled `role="group"` |
| validation filter chips | `<span onClick>` | same |
| sync preview tabs | `<span onClick>` | same |
| transport mode toggles | `<span onClick>` | same, group named by `aria-labelledby` |
| month cells and "Annualised" | `<div onClick>` | `<button aria-pressed>` in a named group |
| workbook dropzone | `<div onClick>` | `role="button" tabIndex=0` + Enter/Space handler |

Two `<div onClick>` remain, in `ConnectionsPanel` and `ScenarioPanel`. Both are
`stopPropagation` plumbing around a row of real buttons — event wiring, not controls, so
they take no role.

Selecting a scenario or a connection was a click handler on the whole row. The row still
responds to a mouse, but the name is now a real `.row-select` button carrying
`aria-pressed`, so selection is reachable by Tab and announced with its state.

### `aria-labels` — there were none

Zero ARIA attributes in the codebase before this pass. Added, in the places where the
markup could not say it on its own:

- Every toggle group is a `role="group"` with a name.
- Every icon-only or spinner-only button has an `aria-label` and `aria-busy`, so it keeps
  its accessible name while it is working. Three buttons in the connections panel
  (Test, Preview sync, Apply this sync) replace their entire label with a spinner while
  busy — they were nameless for the duration.
- All spinners are `aria-hidden`; the state is on the button.
- Legend swatches and dots are `aria-hidden` — they are pure colour, and the text beside
  them carries the meaning.
- The map canvas is a named `role="region"` that points at the Facilities tab, which is
  the accessible equivalent of what it draws: the same numbers, in a real table.
- The per-service frequency selects are truncated on screen at 34 characters. The select
  carries the full service name via `aria-label`, so nobody has to hover a tooltip to
  find out which lane they are changing.

### Tabs — `role="tablist"` with arrow keys

The nine result views were nine plain buttons: nine tab stops, no relationship to the
panel below. They are now a `role="tablist"` with `aria-selected`, `aria-controls`, a
roving `tabIndex`, and Left/Right/Home/End handling. That is one tab stop, not nine, and
it is the WAI-ARIA APG automatic-activation pattern — the panels are cheap to render, so
arrowing changes the view.

### Live regions

- Errors: `role="alert"` + `aria-live="assertive"`. They are always the result of
  something the user just did.
- Solve status and upload messages: `aria-live="polite"`. They should not interrupt.

### Skip link

The sidebar is a long list of scenarios and levers. A keyboard user paid for all of it
before reaching a single number. `Skip to results` is now the first tab stop, hidden off
screen until focused, and jumps to `#results` on the inspector.

### Headings and landmarks

The title was a `<strong>`. It is now `<h1>` — styled back down to body size, because the
topbar is a toolbar and the visual hierarchy there comes from the numbers, not the name.
`<header>`, `<aside aria-label="Results">` and the map region give the page real landmarks.

### Tables

The facility table sorts from the chips above it. It now carries a visually hidden
`<caption>` naming the current order and `aria-sort` on the three sortable numeric
columns. "Supplied by" is not sortable and correctly carries no `aria-sort` at all.

### `reduced-motion`

There was no `prefers-reduced-motion` block. There is now: animations and transitions
collapse to 0.01ms, and the one animation that carries information — the spinner — swaps
from a spin to a 1.6s opacity pulse rather than freezing. A frozen spinner reads as a
hung app.

---

## 2. Touch & interaction (CRITICAL)

### `touch-target-size`

Apple asks for 44pt, Material for 48dp. The app was built for a mouse: buttons at 26px,
chips at 22px, month cells at 26px. Shipping one compromise size would have made the
desktop layout worse for no one's benefit, so the hit size is a token:

```css
--hit: 32px;                                    /* pointer: fine */
@media (pointer: coarse) { :root { --hit: 44px; } }
```

Under a coarse pointer every `.btn`, `.chip`, `.month-cell` and `.tab` is at least 32px
and the primary ones are 44px. Verified with a real touch-emulating context: **0 targets
under 32px.**

### `touch-spacing`, `tap-delay`, `press-feedback`

Adjacent targets get 8px of separation under a coarse pointer, so a near-miss lands on
nothing rather than on the neighbour. Every interactive element carries
`touch-action: manipulation` (no 300ms double-tap-zoom wait) and a `scale(0.97)` press
state. Async buttons get `cursor: progress` via `[aria-busy='true']`.

---

## 5. Layout & responsive (HIGH)

The shell was three fixed columns: 310px sidebar, map, 470px inspector. Below roughly
1280px the map was squeezed past the point where a national network is readable, and
below that the page scrolled sideways — which hides the map controls.

Four breakpoints, giving way in order of what each column can afford to lose:

| width | change |
| --- | --- |
| ≤1440 | inspector 470 → 420 |
| ≤1280 | sidebar → 272, inspector → 380 |
| ≤1100 | sidebar → 248, inspector → 340, KPIs to one column, topbar wraps, month strip takes its own row and scrolls |
| ≤900 | single scrolling column: map first at 58vh, then the panels full width |

Tables cannot reflow without losing meaning, so the inspector scrolls in both directions
rather than clipping a column. **No horizontal page scroll at 1440, 1280, 1100, 1024, 900
or 768.**

---

## 6. Typography & colour (MEDIUM)

### `readable-font-size` and `font-scale`

There were 43 hardcoded `font-size` declarations across eight distinct values from 10px
to 17px, with no system behind them: 10px ×4, 10.5px ×7, 11px ×10, 11.5px ×10, 12px ×8,
13px ×2, 14px, 17px. They are now five tokens — `--fs-micro` 11px through `--fs-kpi` 19px
— and the 10px and 10.5px text is gone entirely. Zero hardcoded `font-size: Npx` remain
in the stylesheet. Body line-height went 1.45 → 1.5.

### `line-length-control`

The skill asks for 60–75 characters. Callouts — the app's prose, which argues a point and
gets read at length — are capped at 68ch and sit at body size rather than the dense-table
size. Lever notes and row notes cap at 60ch.

### `spacing-scale` and `z-index-management`

A 4px scale (`--sp-1` … `--sp-5`) and five named z-index tiers replace ad-hoc values. The
map overlay and tooltip now sit on named tiers instead of raw numbers.

### `number-tabular`

Already compliant. Every figure that a reader compares down a column was already
`font-variant-numeric: tabular-nums`.

### `color-semantic`

Already compliant: `--good`, `--warn`, `--bad`, `--accent` were in place and used
consistently, and no state is signalled by colour alone — the equity and season callouts
name the affected facilities in text.

---

## 7. Animation (MEDIUM)

`duration-timing` asks for 150–300ms and `easing` for ease-out on entering elements.
Transitions were previously undeclared or inherited. Two tokens now: `--dur-fast` 120ms,
`--dur` 180ms, both on `cubic-bezier(0.2, 0, 0, 1)`.

---

## 8. Forms & feedback (MEDIUM)

### `input-labels`

Ten `<label>` elements had no `htmlFor` and no wrapped control — they were being used as
styling, not as labels. Resolved three different ways depending on what each one actually
was:

- **Real field labels** (connector settings, name, base URL, credentials, system,
  authentication, programme integration, every slider, per-service frequency) — given
  `htmlFor` against a `useId()`-generated `id` on the control. Clicking the label now
  focuses the field.
- **Group headings** ("Modes allowed", "Basemap") — these name a set of controls, not one
  control. They became `<span>` with `aria-labelledby` wiring where a group exists.
- **Display-only text** (KPI captions, distance-cascade row labels) — not labels at all.
  They became `<span>`, with the CSS selectors widened so nothing moved.

The four remaining `<label>` without `htmlFor` all wrap their own `<input type="checkbox">`.
That is a valid implicit association and needs nothing.

---

## 9. Navigation (HIGH)

Covered by the tablist and skip-link work above. `aria-current` was considered for the
selected scenario and rejected: `aria-pressed` on a toggle button is the more accurate
description of what that control does — it is not navigation, and the row does not change
the page.

---

## What was deliberately not done

- **`prefers-color-scheme` light theme.** The app is dark-only and now says so once, in
  `color-scheme: dark` plus a matching meta tag, so form controls, scrollbars and the
  canvas behind the root all follow. A second palette is a real piece of work and it is
  not what a design partner needs before the pilot.
- **Below 768px.** The layout stacks and stays usable, but this is a workshop-and-desk
  tool. A phone-first pass would mean rethinking the map interaction, not adding a
  breakpoint.
- **The skill's `search.py --design-system` workflow.** Its `data/` and `scripts/`
  symlinks are dangling in brandwright itself, so the CLI cannot run in either
  repository. What was applied is the Quick Reference rule set.
- **Chart accessibility (priority 10, LOW).** The bars in the equity and cascade panels
  are decorative — every value they draw is printed as a number beside them. They were
  left as CSS, not promoted to `role="img"` with a description that would duplicate the
  text already there.
- **Code-splitting the 1.3MB bundle.** Flagged by the skill under Performance and by Vite
  on every build. The bundle is MapLibre plus React; splitting it would delay the map,
  which is the first thing anyone looks at. Worth revisiting if a second heavy view
  appears.
