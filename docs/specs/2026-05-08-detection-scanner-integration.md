# Spec: Detection_scanner → Wiki Integration

> Dated 2026-05-08.
> Purpose: define the **path** by which scheduled-research documents
> dropped into `Detection_scanner/` flow into the GitHub wiki without
> manual intervention.

## Context

The maintainer is standing up a new top-level folder
**`Detection_scanner/`** in this repo that will hold scheduled
research outputs (threat reports, detection coverage scorecards,
adversary-emulation post-mortems, weekly XSIAM hunting notes, etc.).
Documents land there on a recurring cadence — they need to:

1. Be discoverable from the GitHub wiki without manual republishing.
2. Carry enough metadata that the wiki can render a table-of-contents
   and a tag/topic facet.
3. Round-trip cleanly through GFM with no special preview tooling.

## TL;DR — recommended path

Two stages:

1. **Document convention.** Every research note ships with YAML
   frontmatter (title, date, tags, category, status, source).
2. **Auto-sync workflow.** Extend the existing
   `.github/workflows/wiki-sync.yml` to also crawl `Detection_scanner/`,
   transform each note into a wiki-named markdown page, and rebuild
   the index page on the wiki.

Net result: maintainer drops a `.md` (or `.markdown`) file under
`Detection_scanner/...`, opens a PR, merges to `main` → wiki has a
new page within ~30 seconds.

## Stage 1 — document convention

### Folder layout

```
Detection_scanner/
├── README.md                       ← human onramp, links to the index page
├── _index.yml                      ← optional taxonomy / category labels
└── <category>/                     ← one subdir per research lane
    └── YYYY-MM-DD-<slug>.md
```

Suggested category lanes (decisive but not exhaustive):

| Subdir | Purpose |
|---|---|
| `threat-reports/` | Distilled Unit42 / 3rd-party threat brief notes |
| `coverage-scorecards/` | Per-customer / per-plane Cortex detection scorecards |
| `adversary-emulation/` | Post-mortem of an ATT&CK group emulation |
| `xsiam-hunting/` | Weekly hunting notes / XQL snippets |
| `bioc-rules/` | Draft BIOC YAML + rationale, prior to upstreaming |

Maintainer can add new categories at will; the sync workflow doesn't
hard-code them — it discovers from the directory walk.

### Frontmatter schema

Every `.md` under `Detection_scanner/` (except `README.md` and any
`_*.md`) starts with YAML frontmatter:

```yaml
---
title: "Unit42 — Cobalt Strike Beacon Trends Q2 2026"
date: 2026-05-08
category: threat-reports
tags: [cobalt-strike, c2-beacon, unit42, q2-2026]
status: published       # draft | review | published | superseded
source:
  type: unit42
  url: https://unit42.paloaltonetworks.com/...
maps_to:                # optional — scenario / plane back-references
  scenarios:
    - SIM-NDR-001
    - SIM-MP-001
  planes: [NDR, ANALYTICS]
related_techniques:     # MITRE technique IDs the note touches
  - T1071.001
  - T1568
abstract: |
  Two-paragraph executive summary. Wiki uses this for the index page
  TOC and for the page's lede block.
---

# Full note content starts here in Markdown.

...
```

**Required fields:** `title`, `date`, `category`, `status`.
**Reserved fields:** `tags`, `source`, `maps_to`, `related_techniques`,
`abstract`.
**Free-form:** anything else — the sync passes it through.

### Naming convention

Filename: `YYYY-MM-DD-<kebab-case-slug>.md`

The leading date sorts the index page chronologically without any
runtime computation. Examples:

```
Detection_scanner/threat-reports/2026-05-08-cobalt-strike-trends-q2.md
Detection_scanner/coverage-scorecards/2026-05-06-acme-corp-week-3.md
Detection_scanner/adversary-emulation/2026-05-01-fin7-emulation-postmortem.md
```

### Document-quality conventions

- 200-2000 words. Notes longer than this should be split.
- Lede paragraph mirrors `abstract` frontmatter.
- Code blocks use language fences (` ```bash `, ` ```yaml `, ` ```xql `).
- XSIAM queries fenced as ` ```xql ` so the wiki gets correct syntax
  highlighting.
- Cross-link other research notes via `[[Detection Scanner — <slug>]]`
  (the sync workflow rewrites these to the published wiki page names).

## Stage 2 — auto-sync workflow

The existing `.github/workflows/wiki-sync.yml` already publishes
`docs/wiki/` to the GitHub wiki on every merge to `main`. We extend it
to also process `Detection_scanner/`.

### Workflow steps (extension)

```yaml
# Pseudo-yaml — full diff at end of this spec.
- name: Sync Detection_scanner -> wiki
  run: |
    python3 .github/scripts/detection_scanner_to_wiki.py \
      --source repo/Detection_scanner \
      --dest wiki/
```

The script `.github/scripts/detection_scanner_to_wiki.py`:

1. Walks `Detection_scanner/**/*.md` (skipping `README.md` and
   `_*.md`).
2. Parses frontmatter via `pyyaml` (already a runtime dep).
3. Skips files where `status == "draft"` (only `review` / `published`
   land in the wiki; `superseded` lands with a banner).
4. Computes the wiki page name as
   `Detection-Scanner-<category-cap>-<slug-cap>` to fit within
   GitHub's wiki page-name constraints.
5. Writes the page body with a header block that surfaces the
   frontmatter (title, date, category, tags, source URL,
   `maps_to.scenarios`).
6. Rewrites internal `[[Detection Scanner — <slug>]]` links to the
   published page names.
7. Rebuilds **`Detection-Scanner-Index.md`** — a single index page
   with all notes grouped by category, sorted by date desc, showing
   abstract + tag chips.
8. Updates **`_Sidebar.md`** so "Detection Scanner" is a top-level
   wiki nav section with a link to the index page.

### Why a script, not a templating engine

The existing `wiki-sync.yml` is a single bash workflow that just
copies markdown verbatim. Detection_scanner notes need light transform
(frontmatter → header block, index regeneration, sidebar update). A
small Python script is the right tool — pyyaml is already a dep, no
new packages needed, and the logic is testable.

### Trigger

Same as the existing wiki sync:

```yaml
on:
  push:
    branches: [main]
    paths:
      - "docs/wiki/**"
      - "Detection_scanner/**"
      - ".github/workflows/wiki-sync.yml"
      - ".github/scripts/detection_scanner_to_wiki.py"
  workflow_dispatch:
```

### Failure modes

- **Frontmatter missing / malformed** — workflow fails the build,
  PR is blocked on merge. Author fixes locally and re-pushes.
- **Status `draft`** — skipped silently; PR can still merge.
- **Duplicate slug across categories** — script fails, author renames.
- **Wiki repo doesn't exist** — same fallback as today (operator
  visits the wiki UI, creates first page, re-runs workflow).

## What the maintainer sees

### Authoring loop

```bash
git checkout -b detect/2026-05-08-cobalt-strike-trends
mkdir -p Detection_scanner/threat-reports
$EDITOR Detection_scanner/threat-reports/2026-05-08-cobalt-strike-trends-q2.md
# (write the note with frontmatter)
git add Detection_scanner/threat-reports/2026-05-08-cobalt-strike-trends-q2.md
git commit -m "research: cobalt strike trends q2 2026"
gh pr create --base main --draft
# merge once green
```

Within ~30 seconds of merge, the wiki has a new page named
`Detection-Scanner-Threat-Reports-Cobalt-Strike-Trends-Q2-2026` and
the index page lists it under **Threat Reports** with the abstract.

### Scheduled research lanes

For lanes that run on a recurring schedule (e.g. weekly XSIAM hunting
notes), add a separate `.github/workflows/scheduled-research.yml` that:

1. Runs on a cron (e.g. every Monday 09:00 UTC).
2. Creates a draft PR with a templated frontmatter for the new note,
   pre-filled date / category / slug.
3. Assigns the author for fill-in.

The author opens the draft PR, writes the content, sets
`status: published`, marks ready, merges. Wiki picks it up via the
sync workflow on the merge commit.

This keeps the cadence visible (uncompleted weekly note = an open
draft PR everyone can see) without requiring any out-of-band tooling.

## Concrete diff this spec proposes

Three files to add in a follow-up PR (not this one, since
`Detection_scanner/` does not yet exist):

1. **`.github/scripts/detection_scanner_to_wiki.py`** (~150 LoC)
   — frontmatter parser, page renderer, index + sidebar regenerator.
2. **`.github/workflows/wiki-sync.yml`** (edit) — add the
   `Detection_scanner/**` path trigger + the script step.
3. **`Detection_scanner/README.md`** — onramp for authors, links to
   this spec.

Optionally also:

4. **`.github/scripts/_detection_scanner_template.md`** — copy-paste
   skeleton with frontmatter pre-stubbed.
5. **`.github/workflows/scheduled-research.yml`** — cron-driven draft
   PR for weekly lanes.

## Open questions for the maintainer

1. **Folder name** — is `Detection_scanner` the final name or a
   working title? Snake-case is unusual for top-level dirs; kebab
   (`detection-scanner/`) or PascalCase (`DetectionScanner/`) would
   match the prevailing repo style better. *Recommend* renaming to
   `detection-scanner/` before the spec lands.
2. **Status values** — is `draft | review | published | superseded`
   the right list, or do you want a 5th value (`internal-only` —
   visible in repo but never published)?
3. **Cross-repo research** — if scheduled research outputs come from
   a *separate* repo (the user's earlier message used the word
   "repository"), the workflow needs cross-repo checkout via a PAT.
   Confirm this is in-repo before I prototype.
4. **TOC depth** — should the index page list every note, or
   collapse older-than-90-days notes behind a "Show archive" link?
5. **Search facet** — the GitHub wiki has weak built-in search.
   Should we additionally publish a `Detection-Scanner-Tags-Index`
   page that groups notes by tag for faceted browsing?

## See also

- [`docs/wiki/Roadmap.md`](../wiki/Roadmap.md) — current roadmap (this
  spec slots in under a future "research-ops" workstream).
- [`.github/workflows/wiki-sync.yml`](../../.github/workflows/wiki-sync.yml)
  — existing wiki sync this builds on.
- [`docs/brainstorm/2026-05-08-resolution-strategy.md`](../brainstorm/2026-05-08-resolution-strategy.md)
  — phase plan; this spec lives in Workstream A (DC Experience) as a
  Phase 7-companion documentation lane.
