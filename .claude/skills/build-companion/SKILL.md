---
name: build-companion
description: >
  Maintain a plain-language visual build dashboard for non-developers. Use whenever
  starting a new project or feature, building/testing/deploying anything, when the
  user asks "where are we", "what's the status", "what are you working on", "is X
  done", "open the dashboard", when a decision is agreed, when work is paused or an
  idea is parked, and before anything goes live. Also use when the user mentions the
  build companion, build status, parking lot, or the status panel.
---

# Build Companion

You are keeping a live, honest, plain-language record of the build so a
non-developer can always see what exists, what is happening right now, what was
agreed, and what still needs their attention. The record lives in one file —
`build-status.json` in the project root — and a static page, `dashboard.html`,
renders it as a visual panel the user keeps open beside the conversation.

**The conversation stays primary. The panel is context beside it, never a
replacement for talking to the user.**

## First time in a project (initialize)

If `build-status.json` does not exist in the project root:

1. Copy `assets/dashboard.html` from this skill folder into the project root.
2. Create `build-status.json` from `assets/build-status.example.json`, replacing
   the example content with this project's real name, a one-sentence summary in
   the user's words, and an empty `features` array and `parkingLot`.
3. Tell the user how to open the panel, in one short line:
   "Run `bash <path-to-this-skill>/scripts/dashboard.sh` (or
   `python3 -m http.server 4321` in the project folder) and open
   http://localhost:4321/dashboard.html — keep it beside this window."
   Offer to start the server for them.

Never overwrite an existing `build-status.json`. If it exists but is invalid
JSON, fix it conservatively and tell the user what you repaired.

## The core loop (do this without being asked)

Update `build-status.json` at every one of these moments, as part of the same
turn in which the event happens — not later, not on request:

| Moment | What to write |
|---|---|
| A new feature is agreed | Add a feature: next sequential id (`f1`, `f2`, …), short human name, one-line description in the user's words, `stage: 0` or `1`, empty arrays for the rest |
| You start actively working | Set `building: true` and keep `currentAction` updated to a short present-tense line ("Rewriting how large PDFs load…"). Update it as you move between tasks within the turn |
| You stop working / turn ends | Set `building: false`, clear `currentAction`, append an `activity` entry summarizing what happened |
| A stage changes | Update `stage`. Moving **backward is normal** — when it happens, set `wentBack` with a calm, blame-free note ("Back in Building — testing found an issue with large files. Nothing is lost.") and append an activity entry with `kind: "back"` |
| A decision is agreed in conversation | Append to `decisions` with the current stage, plain wording of what was agreed, and when |
| A version is completed | Prepend to `versions` with a plain note of what changed ("Added 'forgot password' link") |
| You know something is untested or uncertain | Add it to `unsure` ("Works with sample files — not yet tested over 50MB"). Remove entries once actually verified |
| Work is paused or an idea is proposed but not built | Move/add it to `parkingLot` with tag `"Started, paused"` or `"Proposed, not built"` and a reassuring note that nothing is lost |
| Something is ready to go live | Set `needsYou` (see Approvals below). Do NOT deploy yet |
| Anything notable happens | Append to `activity` (newest first): `when`, plain-language `what`, `kind` of `work`, `ok`, `live`, or `back` |

Always update the top-level `updated` field with the current ISO timestamp on
every write. The dashboard polls the file every 2 seconds, so each save is
immediately visible to the user.

## Approvals — the safety rule

Never put anything live (deploy, publish, release, make visible to end users)
while a `needsYou` item is unresolved. The flow:

1. When something is ready, set on that feature:
   `"needsYou": { "title": "Version 2 is ready to go live", "text": "Passed its checks. Nothing changes for anyone until you approve." }`
2. Ask for approval **in the conversation** in plain words. The panel shows the
   same request with the hint "reply in the chat to approve" — the panel is
   read-only; the conversation is where decisions happen.
3. On approval: clear `needsYou`, set `stage: 4`, mark the version `"live": true`,
   append an activity entry `"You approved it — Version 2 went live"` with
   `kind: "live"`.
4. On "not yet": clear `needsYou`, append "You chose to wait — nothing changed",
   and leave the stage as is.
5. If the user asks to roll back, do it, then mark the previous version live
   again and reassure in both chat and activity that the newer version is saved,
   not deleted.

## Language rules (this is the whole point)

The reader may not know what a branch, deploy, commit, or environment is.

- Say "went live", "saved version", "checking it works", "moved back to
  Building" — never "merged", "deployed to prod", "CI passed", "reverted HEAD".
- `activity` and `versions` notes describe outcomes a user can picture: "The
  error message now says what went wrong instead of just 'Error'."
- Be honest, not promotional. `unsure` is a first-class feature: if you have not
  verified something, say so there. An empty `unsure` on a feature you just
  built is usually a lie.
- Backward movement is never framed as failure.
- Keep every string short. The panel is narrow.

## Stages

`stage` is an integer 0–4: `0 Idea · 1 Planning · 2 Building · 3 Testing · 4 Live`.
Features loop backward freely. `parkingLot` is for work outside the flow
entirely (paused or never started).

## Multiple projects

This is a personal skill: it works in every project. Each project gets its own
`build-status.json` + `dashboard.html` in its own root, on its own port if the
user runs several panels at once. If the user asks for a view across all
projects ("what's happening across everything?"), read the `build-status.json`
from each project folder they name (or scan their stated workspace directory)
and answer in chat.

## Reference

Full field-by-field schema with examples: `references/STATUS-FORMAT.md`.
Read it before writing the file for the first time in a session if you are
unsure about any field.
