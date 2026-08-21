# Vendored into this repository

Upstream `build-companion` ships as a **personal** skill (`~/.claude/skills/`). It is
vendored here instead, under the project's own `.claude/skills/`, so that anyone who
clones this repository gets the panel without installing anything — the same reason
`frontend/dist` is committed and `make run` needs no Node.

`SKILL.md`, `README.md`, `LICENSE`, `references/` and `scripts/` are copied verbatim.

## The two changes

**1. One fix to `assets/dashboard.html`.** The header row is
`display:flex; justify-content:space-between` with no `gap`, so a long project name
runs straight into the "view the file" button with no space between them — which this
project's name does. Added `gap:12px` to the row and `flex:0 0 auto; white-space:nowrap`
to the button. Worth sending upstream.

**2. A `make panel` target.** The upstream instruction is to run
`bash <skill>/scripts/dashboard.sh`. This repository drives everything through `make`,
so that is wrapped:

```
make panel                      # serves on 4321 and opens a browser
make panel PANEL_PORT=4322      # if something already owns that port
```

The script itself is unmodified and still works when called directly.

## One deliberate deviation on setup

`SKILL.md` says to initialize with an empty `features` array. That was not done here:
this project already had eight built features and a real history of decisions, reversals
and open questions, so `build-status.json` was populated with them. An empty panel would
have been accurate about nothing and useful to no one.

The timings in the file come from the actual commit dates. The `unsure` entries are real
— including the ones that are awkward, such as the demo running on illustrative demand
and cost figures rather than the ministry's own.

## Where the panel lives

- `build-status.json` — repository root, the source of truth, plain readable JSON
- `dashboard.html` — repository root, a single static file that reads it every 2 seconds

Both are committed. If the dashboard is deleted the record survives, which is the point.
