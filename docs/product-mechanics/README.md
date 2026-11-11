# Product mechanics — how Cortex turns signal into a detection

CortexSim exists to generate signal that lights up a customer's Cortex tenant. When a
signal does not fire, the POV report reads it as *"Cortex missed it"*. Most of those
silences are not detection gaps. They are product mechanics nobody wrote down: a stitch
that needed the source port from the endpoint's point of view, an analytics alert still
inside its 30-day training period, a BIOC that saw the identity wrapper's command line
instead of the TTP's.

This directory is where we write those mechanics down and ask for the ones we lack.

## The chain every question walks

Henry's stitching fix is the template:

```
 symptom            mechanism              contract                    generator lever
 ───────            ─────────              ────────                    ───────────────
 NGFW and agent  →  data fusion engine  →  5-tuple must agree:      →  src from the agent's view,
 never stitched     (stitching)            src as the endpoint saw it,  dst from the firewall
                                           dst as the firewall saw it   transaction
```

A question is only worth asking if its answer moves a lever: a field an emitter fills, a
count or spread it emits, a window between two steps, a wrapper the agent uses, a
readiness gate, a scoring rule.

## The loop

```
  personal Mac (this repo, public)                         work Mac (PANW)
  ────────────────────────────────                         ───────────────
  ledger.yml ──packet──► PMQ-…md ──vault outbox──►   answer agent: Glean, Confluence,
     ▲                                                Jira, Drive, public docs
     │                                                        │
     │                                   internal findings ───┤──► stay on the work Mac
     │                                                        │    (work storage, never the vault)
     │                                                        ▼
  answers/PMA-…md ◄──ingest (refuses non-public)◄── _RoB/product-mechanics/answers/PMA-…md
     │
     └─► render → LEDGER.md      apply → change proposals for the generator (Henry approves)
```

| Path | What it is | Who writes it |
|---|---|---|
| [`ledger.yml`](ledger.yml) | Source of truth: mechanisms (`known`, `assumes`, `contrasts`) and questions | hand-edited, here |
| [`LEDGER.md`](LEDGER.md) | Rendered read view, with derived statuses | `render --apply` — never edit |
| `packets/PMQ-<date>-<slug>.md` | Outbound question packet, self-contained, immutable | `packet --apply` |
| `answers/PMA-<date>-<slug>.md` | Inbound answer file, validated, immutable | `ingest --apply` |
| vault `github-projects/cortex-pov-engine/product-mechanics/questions/` | Packet copy the work Mac reads (Obsidian Sync carries it; the vault's git ignores it) | `packet --apply --vault` or `send --apply`, each read back and hash-checked |
| vault `_RoB/product-mechanics/answers/` | Where the work-Mac agent writes its answer file | work Mac (its working area) |

The vault is resolved from `$OBSIDIAN_ROOT/$OBSIDIAN_VAULT_NAME` on each Mac, never a
hard-coded folder name.

## The confidentiality boundary (enforced, not advisory)

This repository is **public**, and the vault SOP (Hosts table) forbids internal-only PANW
material anywhere in the vault, on both Macs. So:

- An answer file may contain only facts with a public source (`public_doc`, `public_kb`,
  `public_blog`), our own `lab_observation`, or an `operator_report`.
- Anything found only in Confluence, Jira, Drive, Slack or internal search stays on the
  work Mac in work storage. The answer file marks that question `internal_only` and
  carries no answer, rule or source.
- `ingest` refuses the **whole file** if any source is not `distribution: public`, if any
  source kind is internal, or if an `internal_only` answer carries content
  (`ConfidentialityError`, `tests/test_product_mechanics.py`).
- An internal finding crosses only one way: as a lab experiment whose observed result is
  then reported as a `lab_observation`. Many questions carry an `experiment` for exactly
  this.
- Excerpts are capped at 25 words.

## Statuses

| status | meaning |
|---|---|
| `open` | authored, never sent |
| `asked` | listed in a packet under `packets/` |
| `internal_only` | answered on the work Mac; nothing crossed; candidate for a lab experiment |
| `unknown` / `provisional` / `conflicting` / `settled` | earned from the answers by `promote()` |
| `retired` | authored status: the question no longer matters |

**Answered is not settled.** `promote()` in `scripts/product_mechanics.py` decides what an
answer set has earned. Until its policy is written, nothing is ever `settled`, because a
settled rule is what the generator gets changed to.

## Commands

Every command previews by default and writes only with `--apply`.

```bash
.venv/bin/python scripts/product_mechanics.py check                      # validate; fail if LEDGER.md is stale
.venv/bin/python scripts/product_mechanics.py render --apply             # regenerate LEDGER.md
.venv/bin/python scripts/product_mechanics.py packet --priority P0 --slug p0-seed            # preview
.venv/bin/python scripts/product_mechanics.py packet --priority P0 --slug p0-seed --apply --vault
.venv/bin/python scripts/product_mechanics.py check --vault              # prove every packet is in the vault outbox, byte-identical
.venv/bin/python scripts/product_mechanics.py send --apply               # write any packet missing from the vault outbox
.venv/bin/python scripts/product_mechanics.py ingest "$OBSIDIAN_ROOT/$OBSIDIAN_VAULT_NAME/_RoB/product-mechanics/answers/PMA-….md"
```

A vault write is never reported on faith. The vault must be an Obsidian vault (it must
have `.obsidian/`, so a mistyped vault name or a sibling vault is refused before anything is
written), the target must resolve inside it, and the file is read back and its sha256
compared before the command prints `wrote + verified`. `check --vault` reports each packet as
`present`, `missing` or `differs` and fails on anything but `present`. `send` never
overwrites a copy that `differs`, because packets are immutable.

`check` also fails if a ref in `assumes` or `affects` names a repo path that no longer
exists, so a renamed module cannot leave the ledger citing a ghost.

## Writing a good question

The `product-mechanics` skill (`.claude/skills/product-mechanics/SKILL.md`) carries the
full quality bar. The short version: one product behaviour per question; name the fields,
counts and windows; say what CortexSim does today with a file reference; name the lever;
add `decides` branches when the answer forks the build; add an `experiment` when a lab can
settle it; keep internal knowledge out of the question text.

> Weak: *"How does stitching work?"*
>
> Strong: *"When NAT is in play, does stitching use the firewall's pre-NAT source (matching
> the endpoint's local IP) or the post-NAT source, and does it read the NAT fields to
> bridge the two?"* — with the lever *"which NGFW fields carry the endpoint's address"*.
