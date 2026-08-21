# build-status.json — format reference

One file per project, in the project root. The dashboard re-reads it every 2
seconds; every field below is rendered somewhere. All prose fields are written
in plain, non-technical language (see Language rules in SKILL.md).

```jsonc
{
  "project": {
    "name": "Client portal",                    // short human name
    "summary": "Clients log in, see their documents, and get status updates."
  },
  "updated": "2026-08-21T14:03:00Z",            // set on EVERY write (ISO 8601)

  "features": [
    {
      "id": "f3",                               // sequential, stable: f1, f2, f3…
      "name": "Document viewer",                // short human name, no jargon
      "oneLiner": "Clients open and download their PDFs inside the portal.",
      "stage": 2,                               // 0 Idea · 1 Planning · 2 Building · 3 Testing · 4 Live

      // ---- live narration (only while actively working) ----
      "building": true,                         // pulses on the dashboard
      "currentAction": "Rewriting how large PDFs load…",  // short, present tense; clear when done

      // ---- backward movement (optional; delete when no longer relevant) ----
      "wentBack": {
        "when": "yesterday",
        "note": "Back in Building — testing found an issue with very large files. Nothing is lost."
      },

      // ---- approval request (optional; only while awaiting the user) ----
      "needsYou": {
        "title": "Version 1 is ready to go live",
        "text": "Passed its checks. Nothing changes for clients until you approve."
      },

      // ---- the parts this feature is made of ----
      "pieces": [
        { "name": "List of documents", "done": true },
        { "name": "PDF preview", "done": false, "current": true },   // "current" = working on now
        { "name": "Download button", "done": false }
      ],

      // ---- saved versions, newest first; at most one "live": true ----
      "versions": [
        { "v": "Draft", "when": "today", "live": false,
          "note": "Work in progress — not visible to clients yet." }
      ],

      // ---- what was agreed, tied to the stage it was agreed in ----
      "decisions": [
        { "stage": 1, "when": "4 days ago",
          "what": "Preview inside the page, download button, no editing for now." }
      ],

      // ---- honest uncertainty; remove entries only once verified ----
      "unsure": [
        "Works with sample files — not yet tested with files over 50MB."
      ],

      // ---- event log, newest first ----
      "activity": [
        { "when": "yesterday", "kind": "back",
          "what": "Moved back to Building — large files loaded too slowly in testing" }
        // kind: "work" (progress) · "ok" (milestone/check) · "live" (went live) · "back" (moved backward)
      ]
    }
  ],

  "parkingLot": [
    { "name": "Social login (Google / Apple)",
      "tag": "Started, paused",                  // or "Proposed, not built"
      "note": "Email login was enough for launch. Work so far is saved." }
  ]
}
```

## Rules of thumb

- **Relative or absolute time both fine** in `when` ("today", "2 days ago", or a
  date) — pick what a non-developer reads most naturally, be consistent within a
  project.
- **Never delete history.** `versions`, `decisions`, and `activity` only grow
  (trim `activity` to the newest ~30 entries if it gets long).
- **Optional fields** (`building`, `currentAction`, `wentBack`, `needsYou`) are
  removed, not set to null/false-with-stale-text, when no longer true.
- **One `live` version max** per feature. Rolling back flips which entry has
  `"live": true` — it never removes an entry.
- The file must always be valid JSON — the dashboard shows a friendly error and
  keeps the last good render if it isn't, but don't rely on that.
