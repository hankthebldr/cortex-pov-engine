# AGENTS.md — Agent permissions for cortex-pov-engine

> Project-scoped overrides to the global rules in `~/.claude/CLAUDE.md`.
> Anything specified here takes precedence for work in this repo.

## Push permission — granted

The global rule "DO NOT push to the remote repository unless the user
explicitly asks you to do so" is **overridden** for this project.

Agents working on `cortex-pov-engine` may push to the remote on their
own initiative once the following preconditions are all met:

1. **Tests are green.** Full vitest suite passes (`cd ui && npx vitest run`)
   AND Python suite passes (`.venv/bin/pytest tests/ -v`) for any
   touched layer. If a change only touches one layer, only that
   layer's suite is required — but it must pass.
2. **Build is clean.** `cd ui && npm run build` succeeds for any UI
   change.
3. **Commit hygiene.** Each push contains atomic, conventional commits
   (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`) with a
   `Co-Authored-By: Claude …` trailer.
4. **No secret material.** No `.env`, `*.key`, credentials, or
   in-repo `.terraform/` artifacts in the diff.
5. **Branch is not `main`/`master`.** Direct pushes to those branches
   still require an explicit user ask, even with this override.

When the above is satisfied, agents may run `git push` (or
`git push --set-upstream origin <branch>`) without an explicit prompt.

### Force pushes — still require an explicit ask

`git push --force` / `git push --force-with-lease` / any history
rewriting push remains opt-in per ask. Same for pushes that delete
remote refs.

### Hooks — never skipped

`--no-verify` and signature-bypass flags remain off-limits regardless
of this override.

## Everything else

Inherits from `~/.claude/CLAUDE.md` and `./CLAUDE.md`. In particular:
backup-before-edit, scope policy, OPSEC defaults, and the three-system
architecture (Things 3 / Claude Projects / repos) all still apply.
