# Vendored into this repository

Upstream `build-companion` ships as a **personal** skill (`~/.claude/skills/`). It is
vendored here instead, under the project's own `.claude/skills/`, so that anyone who
clones this repository gets the board without installing anything — the same reason
`frontend/dist` is committed and `make run` needs no Node.

**Every upstream file is byte-identical to the release.** There are no local patches.

## The one addition: `make board`

The upstream instruction is to run `bash <skill>/scripts/dashboard.sh`. This repository
drives everything through `make`, so that is wrapped:

```
make board                     # serves on 4321 and opens a browser
make board BOARD_PORT=4322     # if something already owns that port
```

`make panel` is kept as an alias, because an earlier version of these notes told the
user to type that. The script itself is unmodified and still works when called directly.

## Upgrade history

**v2 — the board.** Replaced the original narrow panel. The redesign changes the schema,
not just the styling:

- The unit of work is now a **user story** inside a feature, not a `piece`.
- A story carries two independent facts: `state` (how far it has got) and `env` (where it
  actually runs). Conflating them is the mistake the format exists to prevent.
- Timestamps must be ISO 8601. The board renders relative time at read time, so a
  hand-written "4 days ago" becomes a lie a week later.
- A top-level `deploys` array records everything that ever reached users.

`build-status.json` was migrated by hand to the new shape. The old file rendered fine
under the compatibility path — `pieces` map to stories — but the skill says to migrate a
feature the next time you touch it, and this touched all of them.

**One correction fell out of the migration.** The old file marked six features "Live"
with a `live: true` version. Under the new semantics that was wrong: `env: "prod"` means
real people can use it, and nobody uses this — it has never been deployed anywhere. All
37 stories are now `env: null`, of which 26 are `state: "done"`. The board reads
"0 live in prod · 26 built, not deployed", which is the truthful picture and precisely
the gap the redesign exists to show.

The local fix that v1 needed — a missing `gap` in the header flex row, which let a long
project name collide with the "view the file" link — is **obsolete**. The v2 header is
laid out differently and does not have the bug.

## One deliberate deviation on setup

`SKILL.md` says to initialize with empty `deploys`, `features` and `parkingLot`. That was
not done: this project already had eight built features and a real history of decisions,
reversals and open questions. An empty board would have been accurate about nothing.

Timings come from the actual commit dates. The `unsure` entries are the honest ones,
including that the demo runs on illustrative demand and cost figures rather than the
ministry's own, and that the LMIS connectors have only ever talked to a stand-in server.

## Where the board lives

- `build-status.json` — repository root, the source of truth, plain readable JSON
- `dashboard.html` — repository root, a single static file that reads it every 2 seconds

Both are committed. If the board is deleted the record survives, which is the point.
