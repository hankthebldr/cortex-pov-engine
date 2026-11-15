---
name: product-mechanics
description: Write down how Cortex XSIAM/XDR actually turns telemetry into a detection, and author the precise product questions we still owe, so CortexSim emits signal that really fires. Use when a run executed but nothing fired ("why didn't it stitch?", "why no analytics alert?", "what does XSIAM need to trigger X?"), when designing a scenario or emitter that depends on a product mechanism (stitching, causality/CGO, BIOC, ABIOC, analytics thresholds and training, correlation rules, IOC matching, prevention, incident grouping, tenant readiness), when sending questions to the work-Mac answer agent, or when an answer file comes back to be ingested. Maintains docs/product-mechanics/ledger.yml through scripts/product_mechanics.py.
---

# product-mechanics

CortexSim's job is to generate signal that lights up a Cortex tenant. When a signal stays
dark, the POV report reads it as *"Cortex missed it"*. This skill exists to stop that from
happening for reasons that are really undocumented product mechanics. It does two things:

1. **Writes the knowledge down**: per mechanism, what we know (cited), what CortexSim
   assumes today (with a file reference), and how it differs from neighbouring mechanisms.
2. **Asks for what's missing**: questions sharp enough that each answer becomes a rule the
   generator can follow. The answers come from the work-Mac answer agent (Glean, Confluence,
   Jira, Drive, public docs) or from a lab experiment.

Read `docs/product-mechanics/README.md` before your first run. It holds the loop, the file
map, the statuses and the confidentiality boundary. This file is the method.

## The one rule you cannot bend

This repo is **public**, and the vault forbids internal-only PANW material on both Macs.
Nothing sourced only from internal systems may be written here or into the vault: not in a
`known` fact, not in a question, not in an answer file. `ingest` enforces this and refuses
the whole file. Do not work around a refusal. Tell Henry, and propose a lab experiment
that would turn the internal finding into a `lab_observation`.

## Modes

Pick the mode from what Henry asked. If it's ambiguous, ask one sharp question.

### ask: author questions from a symptom, scenario, emitter or mechanism

1. **Orient.** Read `docs/product-mechanics/LEDGER.md` for the mechanism(s) involved. If an
   existing question already covers it, sharpen that question instead of adding a
   duplicate.
2. **Walk the chain**: symptom → mechanism → contract → generator lever. Read the actual
   artifacts: the scenario YAML (`scenarios/**`), the TTP card's detection
   (`detection_scanner/ttps/*.json`), the emitter or agent code that produces the signal
   (`core/eal_simulator/plugins/*`, `agent/**`), and the stitch/scoring code
   (`core/engine/stitch_context.py`, `core/connectors/matcher.py`,
   `core/eal_simulator/analytics_alerts.py`). Every assumption you find becomes an
   `assumes` row with a `ref` (path:line).
3. **Write questions** to the quality bar below, appended under the mechanism in
   `ledger.yml`. The id is `PM-<MECH>-NN`, next free number. Priority:
   - **P0**: a wrong guess produces a silent miss in a POV today.
   - **P1**: it weakens fidelity, attribution or MTTD.
   - **P2**: refinement.
4. **Validate and render:**
   ```bash
   .venv/bin/python scripts/product_mechanics.py render --apply
   .venv/bin/python scripts/product_mechanics.py check
   ```
   `check` refuses unknown keys, mismatched ids and refs to paths that don't exist. Fix the
   ledger. Never loosen the validator.

### packet: send questions to the work Mac

```bash
.venv/bin/python scripts/product_mechanics.py packet --priority P0 --slug <slug>           # preview
.venv/bin/python scripts/product_mechanics.py packet --priority P0 --slug <slug> --apply --vault
.venv/bin/python scripts/product_mechanics.py render --apply                                # marks them `asked`
.venv/bin/python scripts/product_mechanics.py check --vault                                 # proves the vault copy landed
```

Report the packet as sent only after `check --vault` prints `present` for it. If it says
`missing`, run `send --apply`. If it says `differs`, stop and tell Henry: someone changed
the packet after it was sent.

Select with `--priority`, `--mechanism` or `--ids`. The packet is self-contained: it embeds
the answer instructions, the confidentiality rule and the reply format, so the existing
work-Mac answer agent needs nothing else. `--vault` writes outside the repo, so confirm
with Henry before running it. Packets are immutable: a new batch gets a new `--slug`.

### ingest: take an answer file back

```bash
.venv/bin/python scripts/product_mechanics.py ingest "$OBSIDIAN_ROOT/$OBSIDIAN_VAULT_NAME/_RoB/product-mechanics/answers/<PMA-…>.md"
.venv/bin/python scripts/product_mechanics.py ingest <same> --apply && .venv/bin/python scripts/product_mechanics.py render --apply
```

Preview first and show Henry the per-question summary. If the file is `REFUSED
(confidentiality)`, stop: report which question and source tripped it. Don't edit the
answer file to make it pass. Fixing it belongs on the work Mac.

After ingest, update the mechanism's `known` list **only** with facts whose source is
crossable (public doc/KB/blog, lab observation, operator report), citing it. Then turn
each answer's `follow_ups` into new questions (ask mode), and rewrite any FAQ entry that
now renders **Needs review**.

### apply: propose generator changes from answers

For each question whose status is `provisional` or `settled` and which carries a `rule`,
write a **proposal**, and don't edit code in this mode. Each proposal names:

- the file and the current behaviour (from the question's `assumes` ref),
- the change,
- the answer id and status it rests on,
- the test or lab run that will prove it.

`provisional` proposals are labelled as such. For `internal_only` questions, propose the
lab experiment from the question's `experiment` field. Henry approves before anything is
built, and the build then follows the repo's normal branch/Gate A flow.

### faq: answer what a DC asks when a signal stays dark

FAQ entries live under `faq:` in `ledger.yml` and render to `docs/product-mechanics/FAQ.md`.
Write one when a mechanism's facts, answers or assumptions change what a DC should do
in a POV, e.g. "why didn't my firewall session stitch?" Each entry carries:

- `basis`: `documented` (needs an `https://` ref to the public page; `check` refuses it
  otherwise) · `lab` · `operator` · `working_model` · `unknown`. Pick the weakest
  basis that is true. An answer built from our own reading is `working_model`, even
  when it cites a doc.
- `see`: the question ids whose answers would change the entry. The FAQ shows their
  live status, so a reader can tell a settled answer from a guess.
- `reviewed`: the date you last checked the answer against the ledger. When a `see`
  question is answered after that date, the entry renders **Needs review**; rewrite
  it, then bump the date.

Write the answer for the DC: what to check first, what CortexSim does today, and what
is still unknown. Never more certain than its basis.

## The quality bar for a question

A question earns its place when **all** of these hold:

- **One behaviour.** It asks about one mechanism decision, not "how does X work".
- **Generatable terms.** It names fields, counts, distinct-value counts, windows, record
  types, sides of a join, settings: things an emitter or agent step can be changed to.
- **Grounded.** `context` says what CortexSim does today, with a file ref, and why that
  might be wrong.
- **A lever.** `lever` names exactly what changes in the generator or scorer.
- **Forks made explicit.** If the answer splits the build, `decides` lists each branch and
  its action.
- **Settleable.** If a lab tenant could answer it, `experiment` describes the minimal run
  (a graduated sweep, an off-by-one, an A/B of two forms).
- **Clean.** It contains nothing learned only from internal sources.

> Weak: *"How does stitching work?"*
>
> Strong (PM-STI-03): *"When NAT is in play, which side of the translation does stitching
> use: the firewall's pre-NAT source (matching the endpoint's local IP) or the post-NAT
> source? Does the engine read the NGFW's NAT fields to bridge the two?"* Lever: which
> NGFW fields carry the endpoint's address, and whether emitters must also fill NAT fields.

## "How does this work vs that" (contrasts)

When a mechanism is easily confused with another (stitching vs incident grouping vs
correlation; BIOC vs ABIOC vs analytics; IOC vs BIOC; prevention vs detection), record the
difference as a `contrasts` entry on the mechanism, stated as our working model. When an
answer confirms or corrects a contrast, update it and cite the answer id.

## Done means

`check` passes, `LEDGER.md` and `FAQ.md` are regenerated, `pytest tests/test_product_mechanics.py`
is green, and the change is committed on a topic branch with files staged by name.
Report counts as `render` prints them, and never call a question settled because it was
answered.
