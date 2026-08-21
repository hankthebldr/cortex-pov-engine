# Research Intake Process — turning weekly threat research into product

**Owner:** Domain Consulting (Cortex)
**Cadence:** Weekly (≈30 min triage + 30–60 min fill-in per report)
**Tooling:** `scripts/research_intake.py` (stdlib-only CLI, runs on any host)

## Why this exists

Fresh adversary research lands every week — Unit 42, CrowdStrike, The DFIR
Report, Mandiant, MSTIC. The recurring failure is that **the research never
reaches the product**: reading a writeup on Monday rarely turns into a loadable
detection by Friday, because hand-authoring a schema-valid TTP card *and* a
matching scenario from a blank file is fiddly enough to always slip.

The intake mechanism removes that friction. It does **not** invent detections —
it scaffolds the two schema-compliant files (a TTP card + a scenario) that pass
the validators unedited, so the only work left is the part that actually
requires a human: translating the research into real detection logic. That makes
ABIOC / correlation growth a **repeatable weekly habit** instead of a heroic
one-off, which is exactly where the flagship differentiator (behavioral-ML
coverage) needs sustained, incremental investment.

## The weekly loop

```
 ┌── Monday triage ──────────────────────────────────────────────────────────┐
 │  Pick 1–3 reports worth a detection. For each, note:                       │
 │    • title        • url         • one-line technique summary   • plane      │
 └────────────────────────────────────────────────────────────────────────────┘
              │
              ▼
 ┌── Scaffold (seconds) ─────────────────────────────────────────────────────┐
 │  scripts/research_intake.py --title "…" --url "…" --summary "…" --plane …  │
 │  → writes detection_scanner/ttps/TTP-2026-NNNN-sim-<plane>-NNN.json         │
 │  → writes scenarios/<plane>/<prefix>-NNN-<slug>.yml                         │
 │  Both are schema-valid and status: draft. detection_id slugs already wired. │
 └────────────────────────────────────────────────────────────────────────────┘
              │
              ▼
 ┌── Fill in the real detections (the human part) ───────────────────────────┐
 │  Replace the scaffold logic with the technique FROM THE RESEARCH:          │
 │   • card: real actors, MITRE techniques, payload, and the abioc / xql /    │
 │     correlation logic bodies. KEEP the detection NAMES so the scenario's    │
 │     detection_id slugs keep resolving. Flip status draft → active.          │
 │   • scenario: real steps + commands. Flip status → active.                  │
 └────────────────────────────────────────────────────────────────────────────┘
              │
              ▼
 ┌── Validate ───────────────────────────────────────────────────────────────┐
 │  python3 detection_scanner/scripts/validate.py                             │
 │  python3 .claude/hooks/lint-scenario.py scenarios/<plane>/<file>.yml        │
 └────────────────────────────────────────────────────────────────────────────┘
              │
              ▼
 ┌── Commit / PR ────────────────────────────────────────────────────────────┐
 │  git add <card.json> <scenario.yml>                                        │
 │  git commit -m 'feat(<plane>): SIM-<PLANE>-NNN from weekly research intake' │
 └────────────────────────────────────────────────────────────────────────────┘
```

## Step 1 — Triage

Open the week's reports and ask three questions per candidate:

1. **Is there a discrete, reproducible technique?** (Not "APT group did bad
   things" — an actual behavioral shape we can model and detect.)
2. **Which detection plane owns it?** EDR, CDR, NDR, ITDR, CLOUD_APP,
   ANALYTICS, AI_ACCESS, AIRS, AI_SPM, BROWSER, KOI, ASM, CSPM, TIM, EMAIL.
3. **Is it an ABIOC / correlation opportunity?** Behavioral-ML deviations and
   cross-signal stitches are the highest-value additions — prioritize them.

Record `title`, `url`, and a one-line `summary` for each keeper.

## Step 2 — Scaffold

Run the CLI. Either pass flags:

```bash
scripts/research_intake.py \
  --title "Muddled Libra abuses helpdesk MFA reset" \
  --url "https://unit42.paloaltonetworks.com/muddled-libra/" \
  --summary "Social-engineered MFA reset yields valid-account access to SaaS." \
  --plane ITDR
```

…or drop the same fields into a file and pass `--file`:

```
# intake.txt
title: Muddled Libra abuses helpdesk MFA reset
url: https://unit42.paloaltonetworks.com/muddled-libra/
summary: Social-engineered MFA reset yields valid-account access to SaaS.
plane: ITDR
```

```bash
scripts/research_intake.py --file intake.txt
```

Use `--dry-run` to preview both files without writing anything.

What the scaffolder guarantees:

- **Never reuses a TTP id.** It scans `detection_scanner/ttps/` on disk and
  picks the next free `TTP-2026-NNNN` (max existing + 1, monotonic per schema).
- **Correct next scenario id + filename.** It auto-detects the plane's id-token
  and filename prefix from the existing corpus (e.g. `AI_ACCESS` → `SIM-AIACC-`,
  `CLOUD_APP` → `SIM-CLOUD-`, `ANALYTICS` → `SIM-MP-`) so it stays in lockstep
  with what's already there.
- **Schema-valid on write.** The card passes `validate.py` and the scenario
  passes `lint-scenario.py` **unedited** — including the GAP-12 XQL grammar lint
  (the scaffold's abioc / xql / correlation bodies are lint-clean XQL against
  `xdr_data`, with no placeholder tokens).
- **detection_id slugs already wired.** The scenario's three `detection_id`
  references are computed with the loader's own slug algorithm from the card's
  detection objects, so GAP-4 (every scenario detection_id resolves to a card)
  holds from the first commit — as long as you keep the detection **names**.
- **Won't clobber.** It refuses to overwrite an existing card or scenario file.

Both files ship as `status: draft` so they are not auto-loaded until you promote
them.

## Step 3 — Fill in the real detections (the only manual part)

This is where the research actually becomes product. In the **card**:

- Set the real `threat_context.actors`, `mitre_attack.techniques`, and
  `execution.payload.code`.
- Replace the `detections.abiocs[].logic`, `detections.xql_queries[].query`, and
  `detections.correlation_rules[].logic` bodies with the technique from the
  research. **Keep the detection `name` fields** — the scenario's `detection_id`
  slugs are derived from them; renaming a detection breaks the link (or update
  both sides together).
- Add / adjust `references[]` (the report is already wired as the primary
  reference).
- Flip `status: draft` → `status: active` and bump `entry_version`.

In the **scenario**:

- Replace the two scaffold steps with the real ordered steps + commands.
- Confirm each step's `identity` is in `execution_identity.options`.
- Flip `status: draft` → `status: active`.

Keep bodies **lint-clean**: balanced quotes/parens, a `dataset =` / `preset =`
anchor on every BIOC/XQL/ABIOC body, a known dataset, and **no** placeholder
tokens (`TODO`, `FIXME`, `XXX`, `REPLACE WITH`, …). Correlation logic references
detections by name, not a dataset.

## Step 4 — Validate

```bash
python3 detection_scanner/scripts/validate.py           # whole corpus
python3 .claude/hooks/lint-scenario.py scenarios/<plane>/<file>.yml
```

`validate.py` must end `--- N pass, … , 0 fail ---`. `lint-scenario.py` must
print `OK: …`. If the boot loader is available (Python 3.11 image), a full app
boot is the ultimate check that the detection_id slugs resolve.

## Step 5 — Commit / PR

Commit the card + scenario together at a sensible boundary:

```bash
git add detection_scanner/ttps/TTP-2026-NNNN-sim-<plane>-NNN.json \
        scenarios/<plane>/<prefix>-NNN-<slug>.yml
git commit -m 'feat(<plane>): SIM-<PLANE>-NNN from weekly research intake'
```

Open a PR when the batch is ready. Regenerate detection exports first if your
workflow's CI `detection` job requires it (`sha256sum -c` deterministic check).

## Why the habit compounds

Each pass adds one behavioral or correlation detection grounded in current
adversary tradecraft. Fifty weeks of that is fifty research-backed ABIOC /
correlation additions — the exact axis where CortexSim's differentiator (see the
DL-01 ABIOC-coverage track) needs steady growth. The scaffolder makes the cost
of "landing" a report low enough that it happens every week instead of never.
