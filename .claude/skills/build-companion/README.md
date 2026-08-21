# Build Companion — a Claude Code skill

**A live, plain-language build dashboard for non-developers.** Keep it open
beside your Claude Code window and always know what exists, what Claude is
doing *right now*, what was agreed, what hasn't been checked yet, and what
needs your approval — without ever hearing the words "branch", "deploy", or
"merge".

Built for people who build with Claude Code but don't come from software:
founders, designers, operators, students.

## What it looks like

A warm, quiet panel showing, per project:

- **Needs you** — the only loud element. "Version 1 is ready to go live —
  reply in the chat to approve." Nothing goes live without you.
- **Right now** — a live line of what Claude is doing this second
  ("Rewriting how large PDFs load…"), updating as it works.
- **A five-stop track per feature** — Idea → Planning → Building → Testing →
  Live. Features can move *backward* without drama: "↩ Back in Building —
  testing found an issue with large files. Nothing is lost."
- **What was agreed** — click any stage to see the decisions made there.
- **Not yet checked** — honest uncertainty, per feature: "Works with sample
  files — not yet tested over 50MB."
- **Versions in plain words** — "Version 3 · live · Added 'forgot password'",
  with reassurance that going back never deletes anything.
- **Parking lot** — paused or proposed ideas, clearly tagged, never lost.

Everything renders from one human-readable file, `build-status.json`, that
lives in your project and that you own.

## How it works

```
┌─────────────────────┐        ┌──────────────────────────┐
│  Claude Code (chat)  │ writes │   build-status.json      │
│  = the workspace     │ ─────▶ │   (in your project root)  │
└─────────────────────┘        └───────────┬──────────────┘
                                     reads every 2s
                               ┌───────────▼──────────────┐
                               │  dashboard.html           │
                               │  (browser, beside chat)   │
                               └──────────────────────────┘
```

- **`SKILL.md`** teaches Claude Code to keep the status file updated as a
  natural part of working: starting features, moving stages, recording
  decisions, flagging uncertainty, requesting approval before going live.
- **`dashboard.html`** is a single static file, zero dependencies, that polls
  the JSON and renders the panel. No build step, no framework, no server logic.
- The panel is **read-only by design**: decisions happen in the conversation,
  the panel reflects them. That keeps the architecture honest and dead simple.

## Install (2 minutes)

Requires [Claude Code](https://code.claude.com/docs) and Python 3 (only for
the one-line local file server).

```bash
git clone https://github.com/YOUR-USERNAME/build-companion.git ~/.claude/skills/build-companion
```

Restart Claude Code. That's it — it's a personal skill, so it works in
**every** project on your machine.

## Use

In any project, just start building, or say:

> "Set up the build companion for this project."

Claude creates `build-status.json` and copies `dashboard.html` into the
project. Then open the panel:

```bash
bash ~/.claude/skills/build-companion/scripts/dashboard.sh
```

…and place the browser window next to your Claude Code window. From then on
the panel updates itself as you and Claude talk.

Try the demo without a real project: copy `assets/build-status.example.json`
to a folder as `build-status.json`, copy `assets/dashboard.html` beside it,
run the script.

## The status file is yours

`build-status.json` is plain, documented JSON
(see [`references/STATUS-FORMAT.md`](references/STATUS-FORMAT.md)). Open it,
edit it, commit it to git, diff it between sessions. The dashboard is just a
view; the file is the source of truth. If you delete the dashboard, the record
survives.

## Forking & customizing

This repo is meant to be forked. Common tweaks, all easy:

- **Rename the stages** — edit the `STAGES` list in `dashboard.html` and the
  stage table in `SKILL.md` (keep them in sync; stages are stored as numbers
  0–4, so renaming never breaks existing files).
- **Change the look** — every color and font is a CSS variable at the top of
  `dashboard.html`.
- **Change the voice** — the plain-language rules live in one section of
  `SKILL.md` ("Language rules"). Translate them, make them stricter, make them
  yours.
- **Share with a team** — put the skill in a repo's `.claude/skills/` folder
  instead of `~/.claude/skills/` and commit it; everyone who clones the
  project gets it.

If you build something good — different stage models, other languages, a
mobile layout — PRs are welcome.

## Repo layout

```
build-companion/
├── SKILL.md                        # instructions Claude Code follows
├── README.md
├── LICENSE                         # MIT
├── assets/
│   ├── dashboard.html              # the panel (single static file)
│   └── build-status.example.json   # demo data + init template
├── references/
│   └── STATUS-FORMAT.md            # full field-by-field schema
└── scripts/
    └── dashboard.sh                # serve + open the panel
```

## FAQ

**Does the panel need internet?** No. Everything is local: your file, your
browser, a localhost server.

**Can the panel's buttons control Claude?** No — approvals happen by replying
in the chat, and the panel reflects them. Read-only is a feature: one source
of truth, no magic.

**Multiple projects at once?** Each project has its own file and panel; run
the script on a different port per project (`bash dashboard.sh 4322`).

**What if Claude forgets to update the file?** Just ask "update the build
status" — the skill covers it. Power users can add a Claude Code
[hook](https://code.claude.com/docs/en/hooks-guide) (e.g. on `Stop`) to make
updates automatic every turn.

## License

MIT — see [LICENSE](LICENSE).
