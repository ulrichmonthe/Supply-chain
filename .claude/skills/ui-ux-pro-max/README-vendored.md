# Vendored from ulrichmonthe/brandwright

`SKILL.md` is copied verbatim from `.claude/skills/ui-ux-pro-max/SKILL.md` in
`ulrichmonthe/brandwright`, so this repository carries the same design rules the rest
of the estate uses.

**What did not come across.** In brandwright the skill also has `data/` and `scripts/`
symlinks pointing at `src/ui-ux-pro-max/`, which does not exist in that repository —
they are dangling there too. So the `search.py --design-system` workflow described in
the skill cannot run, here or there. What is usable is the Quick Reference: roughly 250
rules in ten priority categories, most of them citing Apple HIG or Material Design.

That is what was applied to this application. `docs/design-audit.md` records the audit
against those rules — what was already compliant, what was fixed, and what was
deliberately not.

To restore the CLI, commit `src/ui-ux-pro-max/{data,scripts}` in brandwright and
re-vendor.
