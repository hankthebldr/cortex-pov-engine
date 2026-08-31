# `docs/wiki/`

Source for the GitHub wiki at
**https://github.com/hankthebldr/cortex-pov-engine/wiki**.

The published wiki has **two halves**, and only one of them lives here.

| Half | Source | Count |
|---|---|---|
| **Narrative** — Home, Architecture, runbooks, how-tos | these files | 15 pages |
| **Catalog** — `SIM-*`, `Plane-*`, Scenario Index, ATT&CK Coverage | generated from the live corpus by `scripts/gen_wiki.py` | ~187 pages |

`.github/workflows/wiki-sync.yml` runs the generator on every merge to `main`,
assembles both halves into one tree, and force-pushes it to the wiki repo.
Direct edits in the wiki UI are overwritten.

## Why the catalog is generated, not committed

Two earlier attempts stored the catalog instead of generating it, and both
drifted:

- These narrative pages described a 53-scenario, 13-plane tree that stopped
  being true in **May 2026**.
- The live wiki carried 57 per-scenario pages from a one-shot script that was
  never re-run, frozen at **39 scenarios across 8 planes**.

Meanwhile `wiki-sync.yml` does `git rm -rfq .` before copying — so turning it
on while only the narrative half existed would have **deleted all 57 catalog
pages**. Generating the catalog is what makes this directory a superset of
what is published, which is the precondition for the destructive sync being
safe.

## Build it locally

```bash
make wiki          # -> build/wiki/, the exact tree the workflow publishes
```

`build/` is gitignored. The generator reads the corpus through the **real**
loader, so a scenario the engine rejects never reaches the wiki — it is
reported on stderr and excluded. That is deliberate: a published page for a
scenario that does not load is a claim the engine cannot back.

```bash
python3 scripts/gen_wiki.py --check     # build to a temp dir, report counts only
```

## Page conventions

- One `.md` file per page; the filename minus `.md` is the page title GitHub
  renders.
- Links use `[[Page Name]]`. Page names are the basename with hyphens read as
  spaces — `Detection-Planes.md` is linked `[[Detection Planes]]`.
- A pipe inside a `[[target|label]]` link **must be escaped** (`\|`) when it
  appears in a markdown table, or it terminates the cell.
- `_Sidebar.md` and `_Footer.md` are special — GitHub renders them on every
  page. Never link them.
- `README.md` (this file) is source-tree only and is not published.

## Adding a narrative page

1. Create `docs/wiki/My-Page.md`.
2. Cross-link it from `_Sidebar.md` and `Home.md`.
3. Open a PR. The workflow publishes on merge.

## Changing the catalog

Do not hand-write catalog pages — edit the scenario YAML, or edit the page
templates in `scripts/gen_wiki.py`. Adding a new plane also needs an entry in
that script's `PLANE_ENGINE` map so the plane page gets an engine name.
