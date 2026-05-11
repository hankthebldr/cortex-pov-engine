# Contributing

How to land changes against `cortex-pov-engine`.

## Branch naming

```
claude/<phase-or-feature-slug>
```

Examples:

- `claude/phase-6-cortex-browser-attacker`
- `claude/ui-eal-campaign-builder`
- `claude/fix-airs-scorer-edge-case`

The `claude/` prefix is reserved for AI-assisted branches; humans use
their own prefixes.

## PR process

1. **Branch** off the latest `main`:
   ```bash
   git fetch origin && git checkout main && git pull origin main
   git checkout -b claude/<slug>
   ```
2. **Author** your change, with tests.
3. **Verify locally**:
   ```bash
   pytest tests/ --ignore=tests/installer
   pytest sources/cortex-vulnerable-llm/tests/
   pytest sources/cortex-prompt-attacker/tests/
   ```
4. **Open a draft PR** against `main` (the default branch).
5. **CI must be green** — `lint-shell` (shellcheck) is the active
   check on every PR.
6. **Codex auto-review** — the `chatgpt-codex-connector[bot]` posts
   review comments shortly after PR open. Address P1 / P2 findings
   before requesting human review.
7. **Mark ready** for human review.
8. **Merge** is squash-merge by default.

## Commit messages

Conventional-commit-ish, scoped:

```
feat(eal): add llm_provider_egress plugin

Body explains the why and the design tradeoffs. Reference any
research brief, design doc, or upstream issue.

Tests: 184/184 green (was 158, +26 new).
```

Common scopes: `feat(eal)`, `fix(koi)`, `docs(wiki)`, `test`, `ci`.

## Branch protection + default

The default branch is **`main`**. PRs target `main`. Every prior
naming convention (`feat/installer-plan-a`, etc.) is feature-archive
only — do not target a feature branch unless explicitly told.

## What needs tests

| Change | Test required? |
|---|---|
| New EAL plugin | Yes — params validation, dry-run path, mocked-httpx run path, safety, registry |
| New scenario YAML | Loader smoke test (verify `scenario_loader` accepts it) |
| New OWASP class in vulnerable-llm | Yes — both safe and compromised paths |
| New mutator / scorer in prompt-attacker | Yes — pure transformations / detection assertions |
| Doc-only change | No |
| New IaC module | Yes — module catalog test + generator regression test |

## What needs a brainstorm doc

Significant architecture changes — multi-tenancy, new plane, new top-
level service, breaking schema change — should land a brainstorm /
design doc under `docs/brainstorm/` (dated) or `docs/superpowers/specs/`
**before** the implementation PR. Smaller plugins / scenarios just need
the PR description.

## Wiki

The wiki is auto-generated from `docs/wiki/` on every merge to `main`
via `.github/workflows/wiki-sync.yml`. Edit pages by editing the
markdown in `docs/wiki/` — direct edits in the GitHub wiki UI will be
overwritten on the next merge.

To preview locally: any markdown renderer; the GitHub wiki uses GFM.

## Coding conventions (Python)

- Python 3.11+, type-annotated where it adds value
- Pydantic for any external schema (campaign YAML, plugin params,
  probe YAML, scenarios)
- `httpx.AsyncClient` for outbound HTTP (never `requests`)
- `asyncio.to_thread` for blocking syscalls
- ECS-shaped audit events for anything an operator needs to see
- No emojis unless explicitly requested
- No comments that just narrate WHAT the code does — only WHY when
  non-obvious

## Coding conventions (UI)

- React 18 + Vite + plain CSS (no Tailwind)
- Cortex design tokens — `--cortex-navy: #003366`,
  `--cortex-teal: #00C0E8`, `--cortex-steel: #6B7E8E`
- Font: Inter for UI, JetBrains Mono for code
- See `ui/src/styles/cortex-theme.css`

## Licensing

- Repo: source unrestricted; in-tree tools individually Apache-2.0
- **No GPL imports anywhere** — patterns / schemas may be mirrored
  from GPL projects but no source files cross over
- Every in-tree tool ships a `THIRD_PARTY_NOTICES.md` listing the
  patterns borrowed and their upstream licenses
