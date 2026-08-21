---
name: build-companion
description: >
  Compile a live board of what has been built, what is in flight, what is finished but
  not yet in production, and what was discussed and never built. Use whenever starting a
  new project or feature, writing or shipping code, deploying anything, when the user asks
  "where are we", "what's the status", "what's in prod", "is X done", "what did we not
  build", "open the board", when a decision is agreed, when work is paused or an idea is
  parked, and before anything is deployed. Also use when the user mentions the build
  companion, build status, parking lot, or the board.
---

# Build Companion

This is a reverse issue tracker. Nobody writes tickets up front — you write them
down as you build, so that someone who is vibe-coding can look at one page and
see what exists, what is half-done, what is finished but not yet in front of real
users, and what was talked about and dropped.

The record lives in one file — `build-status.json` in the project root — and
`dashboard.html` renders it as a full-width board.

**The conversation stays primary. The board is a record beside it, never a
replacement for talking to the user.**

## The unit of work is a user story

A **feature** is a chunk of product the user would name out loud ("Login page").
A **story** is one thing a person can do, written from their side of the screen
("Reset a forgotten password"). Stories are what move; features just hold them.

Every story carries two independent facts, and conflating them is the main
mistake to avoid:

| Field | Meaning |
|---|---|
| `state` | how far it has got: `idea` → `planning` → `building` → `testing` → `done` |
| `env` | where it actually runs: `"prod"`, `"preview"`, or `null` |

`state: "done"` means the code is written and checked. `env: "prod"` means real
people can use it. **A story is routinely done and not deployed** — that gap is
the single most useful thing on the board, so never mark `env: "prod"` to signal
"finished". Only a real deploy sets it.

## First time in a project (initialize)

If `build-status.json` does not exist in the project root:

1. Copy `assets/dashboard.html` from this skill folder into the project root.
2. Create `build-status.json` from `assets/build-status.example.json`, replacing
   the example content with this project's real name, a one-sentence summary in
   the user's words, and empty `deploys`, `features` and `parkingLot`.
3. Tell the user how to open it, in one line: "Run
   `bash <path-to-this-skill>/scripts/dashboard.sh` (or `python3 -m http.server 4321`
   in the project folder) and open http://localhost:4321/dashboard.html."
   Offer to start the server.

Never overwrite an existing `build-status.json`. If it exists but is invalid
JSON, fix it conservatively and say what you repaired. Files using the older
`pieces` / `stage` shape still render — migrate them to `stories` the next time
you touch that feature.

## The core loop (do this without being asked)

Update `build-status.json` in the same turn as the event, not later:

| Moment | What to write |
|---|---|
| A feature is agreed | Add a feature: next id (`f1`, `f2`…), short human name, one-line description in the user's words |
| You work out what a feature needs to do | Add its stories at `state: "idea"` or `"planning"` — one line each, phrased as something a person can do |
| You start writing code for a story | Set that story to `state: "building"`; set the feature's `building: true` and `currentAction` to a short present-tense line |
| A story works and you have checked it | `state: "testing"` → then `"done"`. Leave `env` alone |
| You deploy | For every story that went out, set `env` and its `at`. Prepend one entry to the top-level `deploys` with `at`, `env`, plain-language `what`, and the `storyIds` that rode along |
| A story goes backwards | Just move `state` back and put the reason in the story's `note` ("Back in Building — large files were too slow. Nothing is lost.") Add an `activity` entry with `kind: "back"` |
| A decision is agreed in conversation | Append to the feature's `decisions` with `at` and plain wording |
| You know something is untested | Put it on the story's `unsure`, or the feature's `unsure` if it spans the whole thing. Remove only once actually verified |
| An idea comes up and is not built | Add to `parkingLot` with tag `"Started, paused"` or `"Proposed, not built"`, and a note saying nothing is lost |
| Something is ready to deploy | Set `needsYou` on the feature. Do NOT deploy yet |
| You stop working / turn ends | Clear `building` and `currentAction`; append an `activity` entry |

Set the top-level `updated` to the current ISO timestamp on **every** write. The
board re-reads the file every 2 seconds.

**Timestamps are ISO 8601, always.** Write `at`, never a frozen phrase like
"yesterday" — the board turns `at` into relative time at read time, so a hand-written
"4 days ago" becomes a lie a week later. On a board whose whole point is honesty,
that matters.

## Deploying — the safety rule

Never deploy, publish, or release while a `needsYou` is unresolved.

1. When stories are done and checked, set on the feature:
   `"needsYou": { "title": "Ready to go to prod", "text": "All three stories are built and checked. Nothing changes for anyone until you approve." }`
2. Ask in the conversation, in plain words. The board is read-only; approval happens in chat.
3. On approval: deploy, clear `needsYou`, set `env` and `at` on each story that
   went out, prepend a `deploys` entry, append activity with `kind: "live"`.
4. On "not yet": clear `needsYou`, append "You chose to wait — nothing was
   deployed". The stories stay `done` with `env: null`, which is exactly what the
   "built, not in prod" column is for.
5. On a rollback: flip the affected stories' `env` back to `null`, add a `deploys`
   entry describing the rollback, and reassure that the code is saved, not deleted.

## Language rules (this is the whole point)

The reader may not know what a branch, commit, or environment is.

- Say "went to prod", "real people can use it", "checking it works", "moved back
  to Building" — never "merged", "deployed to prod via CI", "reverted HEAD".
- Story titles are things a person does, not things the code has: "Download a
  document", not "Implement download endpoint".
- Be honest, not promotional. `unsure` is a first-class field: an empty `unsure`
  on something you just built is usually a lie.
- Backward movement is never framed as failure.
- Keep strings short — one line each. The board is dense.

## The parking lot matters

Half of what gets discussed while vibe-coding never gets built, and the reason it
was dropped is lost within a day. Every time an idea is raised and set aside, put
it in `parkingLot` **with why**, in the same turn. It is the cheapest thing on
this board and the one users thank you for.

## Multiple projects

Each project gets its own `build-status.json` + `dashboard.html` in its own root,
on its own port. If the user asks what's happening across everything, read the
`build-status.json` from each project folder they name and answer in chat.

## Reference

Full field-by-field schema: `references/STATUS-FORMAT.md`. Read it before your
first write in a session if you are unsure about any field.
