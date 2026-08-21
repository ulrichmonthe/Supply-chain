# build-status.json — format reference

One file per project, in the project root. The board re-reads it every 2 seconds.
All prose is plain, non-technical (see Language rules in SKILL.md).

```jsonc
{
  "project": {
    "name": "Client portal",
    "summary": "Clients log in, see their documents, and get status updates."
  },
  "updated": "2026-08-21T14:03:00Z",     // ISO. Set on EVERY write.

  // ---- every time something reached users. Newest first. ----
  "deploys": [
    {
      "at": "2026-08-21T09:40:00Z",
      "env": "prod",                      // "prod" | "preview"
      "what": "Login page — 'forgot password' link",   // plain language
      "storyIds": ["f1s2"],               // which stories rode along
      "note": "Clients can reset their own password now"   // optional
    }
  ],

  "features": [
    {
      "id": "f1",                         // stable: f1, f2, f3…
      "name": "Login page",               // short, human, no jargon
      "oneLiner": "Clients sign in with email and password.",

      // ---- live narration; delete both when the turn ends ----
      "building": true,
      "currentAction": "Rewriting how large PDFs load…",

      // ---- approval request; delete once resolved ----
      "needsYou": {
        "title": "Ready to go to prod",
        "text": "All three stories are built and checked. Nothing changes for clients until you approve."
      },

      // ---- THE UNIT OF WORK ----
      "stories": [
        {
          "id": "f1s2",                   // featureId + "s" + n
          "title": "Reset a forgotten password",   // what a PERSON can do
          "state": "done",                // idea | planning | building | testing | done
          "env": "prod",                  // "prod" | "preview" | null
          "at": "2026-08-21T09:40:00Z",   // when it reached that env
          "note": "",                     // optional one line: why it moved, what's odd
          "unsure": ""                    // optional: what has NOT been checked
        }
      ],

      "decisions": [
        { "at": "2026-08-14T10:00:00Z",
          "what": "Email and password only for launch; social login parked." }
      ],

      "unsure": ["Only tried with our own test account."],   // whole-feature caveats

      "activity": [                       // newest first
        { "at": "2026-08-21T09:40:00Z", "kind": "live",
          "what": "'Forgot password' went to prod" }
        // kind: "work" · "ok" · "live" · "back"
      ]
    }
  ],

  "parkingLot": [
    { "name": "Social login (Google / Apple)",
      "tag": "Started, paused",           // or "Proposed, not built"
      "note": "Email login was enough for launch. The work so far is saved." }
  ]
}
```

## state vs env — read this twice

They are independent, and the board's most valuable column is the gap between them.

| `state` | `env` | What the board shows |
|---|---|---|
| `building` / `testing` | `null` | amber — being worked on right now |
| `done` | `null` | **built, not deployed** — finished, real users can't use it yet |
| `done` | `"prod"` | green — in prod |
| `done` | `"preview"` | on a preview URL, not in front of real users |

`env` changes **only** when a deploy actually happens. Never set `env: "prod"` to
mean "finished" — that erases the one thing the board exists to show.

## Rules of thumb

- **Timestamps are ISO 8601.** Never write "yesterday" — relative time is rendered
  from `at` at read time, so hand-written phrases go stale and mislead.
- **Never delete history.** `deploys`, `decisions`, and `activity` only grow
  (trim `activity` to the newest ~30 per feature).
- **Optional fields are removed, not falsified.** Delete `building`,
  `currentAction`, `needsYou` when no longer true — don't leave stale text.
- **A story is never deleted.** Dropped work moves to `parkingLot` with a reason.
- **Rolling back** sets the affected stories' `env` back to `null` and adds a
  `deploys` entry. The code is saved, not deleted — say so.
- The file must always be valid JSON. The board keeps the last good render if it
  isn't, but don't rely on that.

## Backwards compatibility

Files written in the older format (`pieces` + a feature-level `stage` 0–4) still
render: pieces are mapped to stories, `done` pieces on a `stage: 4` feature are
assumed to be in prod. Migrate a feature to `stories` the next time you touch it.
