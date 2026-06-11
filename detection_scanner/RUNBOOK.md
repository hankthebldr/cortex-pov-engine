# detection_scanner RUNBOOK

Operator playbook for keeping this corpus alive. Read this first if you've inherited the repo, are resuming after a gap, or are running the recurring jobs that feed the cortex-pov-engine.

## What this corpus is, in one line

A versioned, schema-validated JSON library of attack TTPs grounded in MITRE ATT&CK and Unit 42 reporting, with explicit Cortex / PANW product mappings — consumed by the `cortex-pov-engine` to drive POV simulations and score outcomes.

## The four files that matter most

| File | What it is | When to touch |
|---|---|---|
| `schema/ttp-entry.schema.json` | The contract every TTP must satisfy. JSON Schema 2020-12, strict (`additionalProperties: false`). | Only when changing the schema itself. Bump `schema_version` const on any breaking change and migrate all existing entries. |
| `sources/source-registry.json` | The source list the scraper crawls. Each source has tier, weight, update cadence, discovery query templates. | When adding a new source, retiring a dead one, or updating `last_canvassed`. Bump `registry_version` on any change. |
| `scripts/validate.py` | The canonical validator — the single source of truth for what "valid" means. CI, the engine, and contributors all run it. Now also runs a lightweight XQL/BIOC **grammar sanity lint** (GAP-12, check 13). | Only when changing what "valid" means (and update this RUNBOOK + README to match). |
| `.tasks/recurring-tasks.json` | Declarative list of recurring jobs. | When the operating cadence changes. |

Everything else is either a TTP entry (`ttps/*.json`) or supporting context.

> **No manifest.** The engine has no load-time index file. It enumerates the
> corpus by globbing `ttps/*.json` directly (non-recursive, skipping
> `_drafts/`) — see `core/engine/ttp_catalog.py:load()`. `scripts/build-manifest.py`
> still exists for ad-hoc human reporting, and its output (`manifest.json`) is
> gitignored and never committed; do **not** treat it as an engine input or a
> CI artifact.

## Recurring work — the rhythm

### Weekly (every Monday)

1. **Canvas Tier 1 sources** — Unit 42 + MITRE ATT&CK updates.
   ```bash
   # Pull the latest Unit 42 RSS, diff against sources/unit42-index.json
   # Add any new high-signal posts as P0/P1 entries
   ```
2. **Run the validator** — catches drift from external edits. This is the CI gate.
   ```bash
   python3 scripts/validate.py            # must exit 0
   ```
3. **Regenerate exports** and commit if changed — these are the deployable
   artifacts customers consume. `git diff --exit-code detection_scanner/exports/`
   is the CI guard that catches stale exports.
   ```bash
   python3 scripts/export_artifacts.py --clean
   git diff --exit-code detection_scanner/exports/   # CI gate
   ```

### Monthly (first business day)

1. **Canvas Tier 2/3 sources** — Mandiant, MSTIC, CrowdStrike, CISA. Use the discovery query templates in `sources/discovery-queries.json`.
2. **Audit source health** — fetch each `homepage` and confirm it still resolves. Update `last_canvassed` per source.
3. **Promote draft TTPs** out of `ttps/_drafts/` once enriched with Cortex-specific fields.
4. **Review the BlackSuit-Blitz chained scenario** — re-run TTP-2026-0002 → 0004 → 0005 → 0006 in lab and confirm correlation rules still fire as expected. Cortex product UI changes break BIOC syntax fairly often.

### Quarterly

1. **Schema review** — does the corpus stress the schema in ways the contract didn't anticipate? Bump to a new minor version if so.
2. **PANW product enum refresh** — new modules (e.g., when a new Cortex submodule ships), retired SKUs. Edit `schema/ttp-entry.schema.json` `panw_mapping.products.module` enum.
3. **MITRE ATT&CK version bump** — when MITRE issues a new ATT&CK release, pull the new STIX, check for technique renames/deprecations across the corpus.

### Ad-hoc — when a Unit 42 post drops

1. Add the URL to `sources/unit42-index.json` with a P0/P1/P2 priority.
2. If P0, author the TTP entry directly (don't wait for the scraper).
3. Validate + regenerate exports as above.

## How to add a new TTP — step by step

1. **Pick the next free ID** — find `MAX(TTP-2026-NNNN)` and increment. IDs are never reused.
2. **Copy an existing entry as a template.** For a new endpoint TTP, start from `TTP-2026-0002`; cloud from `0003`; identity from `0001`. Adjust every field.
3. **Anchor it to a source.** Exactly one `references[]` entry must have `primary: true`. That source's `publisher_id` must appear in `metadata.source_refs[]`. Both must resolve to `sources/source-registry.json`.
4. **Resolve MITRE.** Every `technique_id` matches `Txxxx`; every `subtechnique_id` matches `Txxxx.xxx` and starts with the parent technique. Tactic IDs match `TAxxxx`.
5. **Write the execution payload** as the engine will literally run it. Use `${VAR}` placeholders for runtime substitution; declare each in `payload.input_variables[]`.
6. **Fill `expected_artifacts[]`** so the engine can verify telemetry post-run. This is what makes the test deterministic.
7. **Write 2–4 BIOCs** in XSIAM-flavored XQL. Each BIOC must reference at least one MITRE technique that's also in `mitre_attack.techniques[]`.
8. **Map every applicable PANW module.** For each module: `coverage_tier`, `rule_ids` (use `TBD-MODULE-001` placeholders if unconfirmed), `license_required`, and `evidence_query` the SE pastes into the product UI on demo day.
9. **Author one use case + ≥1 test case.** Each test case carries `success_criteria[]` (verifiable statements) and `expected_score_weight ∈ [0,1]`. Per use case, weights must sum to ≤ 1.0.
10. **Write `cleanup`** if `safety_class != "safe-by-design"`. The engine enforces this.
11. **Validate.**
    ```bash
    python3 scripts/validate.py
    ```
12. **Regenerate exports, commit.**
    ```bash
    python3 scripts/export_artifacts.py --clean
    git add ttps/TTP-YYYY-NNNN-*.json exports/
    git commit -m "Add TTP-YYYY-NNNN: <name>"
    ```

## How to add a new source

1. Choose an ID matching `SRC-[A-Z0-9-]+`.
2. Append to `sources/source-registry.json` with: `id`, `name`, `tier` (1–5), `weight` (0–1), `publisher_class`, `homepage`, `purpose`, `update_cadence`, `discovery_queries`. Add `feeds[]` and `scrape_hints` if the scraper will crawl it.
3. Add the source's first canonical URL to `sources/<source-id>-index.json` (or the existing index file for that source).
4. Bump `registry_version` minor (e.g., 1.1.0 → 1.2.0).
5. Validate + regenerate exports.

## How to retire a TTP

Do NOT delete the file. Flip `status` from `active` to `deprecated` (or `withdrawn` if it was wrong, not just outdated). Add a changelog entry explaining why. The engine ignores non-active entries; historical POVs that reference them by ID still resolve.

## Common pitfalls

- **Schema `additionalProperties: false` is strict.** Adding a stray field anywhere fails validation. If you legitimately need a new field, edit the schema first.
- **`identity.summary` is capped at 400 chars.** Long-form belongs in `identity.description`.
- **BIOC XQL dialects drift.** The queries in this corpus target XSIAM 2.x. `scripts/validate.py` now runs a grammar sanity lint (balanced quotes/parens, a `dataset =` / `preset =` anchor on every BIOC/XQL body, no leftover placeholder/skeleton tokens — see "XQL/BIOC grammar lint" below), so structurally-broken bodies fail CI rather than shipping silently. The lint is *grammar*, not *semantics*: `dataset` names and `event_sub_type` enums still drift between tenant versions, so re-validate the field names against the customer's live tenant before each POV.
- **Actor naming collisions.** Same actor, different name per vendor: Muddled Libra (Unit 42) == Scattered Spider (CrowdStrike) == UNC3944 (Mandiant) == Octo Tempest (MSTIC). Always cross-resolve before treating two reports as distinct.
- **Don't run destructive TTPs (`destructive: true`) outside a sanctioned lab.** TTP-2026-0006 (ESXi mass encrypt) uses SAFE-MODE marker files in this corpus — but a future entry might not. The engine reads `metadata.pov_engine.destructive` per card and enforces destructive consent at launch time (the launch consent gate), not from any manifest.
- **rule_ids are illustrative, not deployable.** The `XSIAM-AN-*`, `XDR-BIOC-*`, `XSOAR-PB-*` IDs in `panw_mapping.products[].rule_ids` are placeholders for a customer's own rule namespace — they are NOT shippable rule identifiers. The *deployable* content is the BIOC/XQL/correlation logic in `detections{}`, surfaced as the artifacts under `exports/`. Replace the placeholder rule_ids with real tenant IDs as POVs surface them.

## Detection traceability is card-level, name-derived (GAP-4 — resolve-by-design)

A scenario binds a card detection via `expected_detections[].ttp_ref` +
`detection_id`. The cards do **not** carry an explicit `detection_id` on each
BIOC/XQL/correlation/IOC object; instead the engine **synthesizes** the
`detection_id` from the detection's `name` (`core/engine/ttp_catalog.py::_slug`):

- BIOC name → `bioc-<slug>` · XQL name → `xql-<slug>` · correlation → its
  `rule_id` verbatim (e.g. `CR-ASM-0002`) when present, else `correlation-<slug>` ·
  IOC → `ioc-<ioc_type>-<value-slug>`.
- `_slug(name)` = lowercase the name, replace every non-alphanumeric run with a
  single `-`, strip leading/trailing `-`, cap at 120 chars.

**This name-derived slug is the working contract, by design.** It is *card-level
plus name-keyed*, not a per-detection GUID — a scenario author writes the slug
that the card's detection name will produce, and the loader resolves it at boot.
The contract holds: **341 of 342** expected-detection slugs across the corpus
resolve to a real card detection object (the lone exception is the SIM-NDR-005
pre-flight step `S-05a`, which intentionally carries no `detection_id`). We do
**not** rewrite the cards to carry per-detection ids — that would be churn for no
gain given the slug round-trips deterministically. To find the exact slug a card
emits, run the catalog (see "Useful commands" → "Show the slugs a card emits").
When you author a new card+scenario pair, copy the slug from that command rather
than hand-typing it.

## MITRE ATLAS mappings on the AI planes (GAP-9)

AI-plane cards (AIRS / AI Access / AI-SPM) map onto ATT&CK techniques that don't
fit the threat well (prompt injection → T1656, token DoS → T1499, LLM egress →
T1567 used as a 6-card catch-all). To stop over-loading ATT&CK *without*
regressing ATT&CK coverage, these cards now also carry an **`atlas_techniques[]`**
block under `mitre_attack`, mapping the threat to MITRE ATLAS ids (`AML.TXXXX`):

```jsonc
"mitre_attack": {
  "techniques": [ /* UNCHANGED ATT&CK ids — additive, no regression */ ],
  "atlas_techniques": [
    { "atlas_id": "AML.T0051.000", "name": "LLM Prompt Injection: Direct",
      "atlas_tactic": "Initial Access" }
  ]
}
```

- The field is **optional** and **additive** — the ATT&CK `techniques[]` block is
  never touched, so the coverage heatmap and the by-technique audit do not lose
  any ATT&CK technique. Only the 10 AIRS/AI_ACCESS cards (TTP-2026-0007..0016)
  carry it today.
- `atlas_id` validates against `^AML\.T[0-9]{4}(\.[0-9]{3})?$`. Source the ids
  from the registered `SRC-MITRE-ATLAS` source (added in registry 1.3.0); any
  card using `atlas_techniques` should add `SRC-MITRE-ATLAS` to its
  `metadata.source_refs` and the `mitre-atlas` tag.

## Validated vs. mapped analytics modules (GAP-11)

`detections.analytics_modules[]` are **named references** to Cortex analytics
modules, not testable BIOC/XQL logic — they map to a module name but cannot be
*validated* post-run the way a query can. To let the coverage rollup separate a
**validated detection** (backed by a BIOC/XQL/correlation on the same card) from
a **merely-mapped** analytics module, each entry may now be either:

- a **bare string** (back-compat) — treated as `validated: false` by the rollup, or
- an **object** `{ "module": "<name>", "validated": <bool>, "note": "<why>" }`.

Set `validated: true` **only** when a deployable BIOC/XQL/correlation on the
*same card* exercises that module, and put the backing detection name in `note`.
Default is `false` — a named analytics module is aspirational mapping, not a
validated detection. All pre-existing cards keep the bare-string form (counted as
unvalidated); the new GAP-1/GAP-8 cards demonstrate the object form.

## XQL/BIOC grammar lint (GAP-12)

`scripts/validate.py` check 13 runs a lightweight grammar sanity lint over every
deployable `detections.biocs[].logic`, `detections.xql_queries[].query`, and
(when present) `detections.correlation_rules[].logic` body. It is a hard error
(the corpus must stay lint-clean). It catches:

1. **Unbalanced double-quotes** (odd `"` count).
2. **Unbalanced parentheses** — counted *outside* string literals, so XQL tokens
   like `contains_any (".connect(")` are not false-positived.
3. **Placeholder / skeleton tokens** left over from the draft generator
   (`AUTO-GENERATED SKELETON`, `TODO`, `FIXME`, `XXX`, `REPLACE_WITH`,
   `<placeholder>`, `predicate matching the bioc name`, …).
4. **Missing dataset/preset anchor** — every BIOC/XQL body must reference
   `dataset =` or `preset =` (correlation bodies are exempt; they reference
   `BIOC(...)` / `XQL(...)` names).

It is a **grammar** check, not a semantic XQL parser — it deliberately errs
toward few false positives. Field-name/`event_sub_type` drift against a specific
tenant version is still your responsibility at POV time.

## Contracts with adjacent systems

- **cortex-scraper** reads `sources/*.json`, writes drafts to `ttps/_drafts/`. Drafts always have `status: "draft"` and are local-only (gitignored). A human reviewer must enrich and promote.
- **cortex-pov-engine** enumerates the corpus by globbing `ttps/*.json` directly (non-recursive, skipping `_drafts/`) — there is no manifest. See `core/engine/ttp_catalog.py:load()`. The engine respects `auto_load`, `destructive`, `safety_class`, and the per-test scoring weights from each card.
- **CI** runs `scripts/validate.py` (must exit 0) and verifies the generated `exports/` tree is in sync with the corpus (`git diff --exit-code detection_scanner/exports/` after `scripts/export_artifacts.py --clean`).

## Open questions still parked

Tracked in `README.md` "Open contracts" section. Re-check at every quarterly schema review:

- BIOC syntax dialect freeze against current XSIAM grammar. *(Partially addressed:
  validate.py check 13 now enforces grammar sanity — balanced quotes/parens,
  dataset/preset anchor, no placeholder tokens — so structural breakage fails CI.
  What remains is semantic field-name/`event_sub_type` drift, which only a live
  tenant can confirm.)*
- Score normalization — partial-weight tolerance vs. `--strict` enforcement.
- Cleanup orchestration — engine-enforced vs. operator-confirmed.

## Useful commands

```bash
# Full validation (CI / pre-commit) — must exit 0
python3 scripts/validate.py

# Strict mode — require per-UC weights == 1.0
python3 scripts/validate.py --strict

# Regenerate the deployable exports (sigma / xql / correlation / xsoar)
python3 scripts/export_artifacts.py --clean

# Show MITRE coverage (read straight from the corpus)
python3 -c "import json,glob; ts=set(); [ts.update(t.get('technique_id') for t in json.load(open(f)).get('mitre_attack',{}).get('techniques',[])) for f in glob.glob('ttps/*.json')]; print('\n'.join(sorted(ts)))"

# Show TTPs targeting a specific PANW module
python3 -c "import json,glob; [print(d['id']+': '+d['identity']['name']) for f in glob.glob('ttps/*.json') for d in [json.load(open(f))] if any(p.get('module')=='cortex-xdr' for p in d.get('panw_mapping',{}).get('products',[]))]"

# Find TTPs with destructive payloads
python3 -c "import json,glob; [print(d['id']) for f in glob.glob('ttps/*.json') for d in [json.load(open(f))] if d.get('metadata',{}).get('pov_engine',{}).get('destructive')]"

# Show the detection_id slugs a card emits (copy these verbatim into the
# scenario's expected_detections[].detection_id — the name-derived contract, GAP-4)
# (run from the repo root via the simcore image; PYTHONPATH must include core/)
python3 -c "import sys; sys.path.insert(0,'core'); from engine.ttp_catalog import catalog; catalog.load('detection_scanner/ttps'); e=catalog.get_entry('TTP-2026-0071'); [print(d.kind,'::',d.detection_id) for d in e.detections]"
```

> **Note on `scripts/build-manifest.py`:** it still exists for ad-hoc human
> reporting, but its `manifest.json` output is gitignored and is **not** an
> engine input or a CI artifact. The engine globs `ttps/*.json` directly.
