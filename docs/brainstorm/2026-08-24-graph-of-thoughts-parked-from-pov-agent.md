# Graph of Thoughts — parked here after rejection in `cortex-pov-agent`

> **Audience:** whoever picks up generative authoring in CortexSim — scenarios,
> TTPs, detections, assertions.
>
> **Status:** parked idea, no code written in either repo. This note exists so the
> evaluation does not have to be redone, and so the *condition* that would make it
> worth building is written down while it is still fresh.
>
> **Origin:** evaluated 2026-08-24 for `cortex-pov-agent`'s Evidence → Readout path.
> Rejected there. The reason it was rejected does not apply here, which is the whole
> reason this note is in this repo.

---

## TL;DR

Graph of Thoughts ([spcl/graph-of-thoughts](https://github.com/spcl/graph-of-thoughts),
AAAI 2024) buys exactly one thing over tree-of-thought: **N→1 aggregation and cycles**.
It was proposed for the POV Agent readout and rejected — that repo had already solved
the problem *structurally* (a SQL `CHECK` and a pure function), which beats a
probabilistic scorer outright.

CortexSim has no equivalent structural answer for **authoring** scenarios/TTPs/detections,
so the idea survives here. But it is gated on one precondition, in §5, and the
precondition is the same thing this repo already says loudest: *authored is not proven*.

---

## 1 · What it actually is, mechanically

```
CoT    A → B → C                    one chain, no alternatives
ToT    A →┬─ B1 ─┬─ C1              branch + prune; every node has ONE parent,
          └─ B2  └─ C2              so partial solutions can never be merged
GoT    A →┬─ B1 ─┐
          ├─ B2 ─┼─→ D  ←┐          D has THREE parents (merge), and
          └─ B3 ─┘       │          D can feed itself (scored refine loop)
                    └────┘
```

Operation set: `Generate`, `Aggregate` (N→1), `Refine` (self-loop), `Score`, `KeepBest`,
`GroundTruth`. Everything else in the framework is graph bookkeeping.

The paper reports ~62% quality gain and >31% cost reduction vs ToT — **on sorting,
set intersection, keyword counting**. Tasks where `score()` is `count_errors()`. That
caveat is load-bearing and is the entire subject of §5.

## 2 · Do not `pip install graph_of_thoughts`

| | PyPI | GitHub `main` |
|---|---|---|
| version | **0.0.2** | 0.0.3 (never published) |
| date | **2023-09-26** | — |
| `openai` pin | **`>=0.27.7`** — pre-1.0 SDK | `>=1.0.0,<2.0.0` |

The README's install command gets the September 2023 build pinned to the pre-1.0 OpenAI
SDK, which broke completely in Nov 2023. The fix exists on `main` and was never released.
Both pull `torch`, `transformers`, `bitsandbytes`, `accelerate`, `numpy<2.0` — multi-GB,
and the `numpy<2.0` ceiling will fight `core/requirements.txt`.

Commits since 2024 are typo fixes; latest (2026-03) is literally `fix occurences/occured
typos`. **Treat upstream as a paper with a reference implementation attached, not as a
dependency.** The operation set above is a few hundred lines to write natively.

Also note: a GoT-shaped MCP server already exists and exposes
`seed / generate / score / refine / aggregate / visualize / status`. Its `score()` is
**caller-supplied** — the server stores a number and prunes below 0.3, contributing zero
judgment. Fine for ad-hoc reasoning; it is not a scorer.

## 3 · Why it was rejected in `cortex-pov-agent`

That repo's readout path (`src/server/readout/compose.ts`) is a **pure function** —
grepping the whole path for `anthropic|claude|model-router|inference|prompt` returns
nothing. Its rules:

- `countsTowardProof = (link) => link.reviewState === 'reviewed'` — the entire definition
- only reviewed evidence composes; an unproven criterion becomes a stated open item
- composed bodies are **byte-identical** to stored artifacts, asserted as bytes in tests
- `criterion_evidence.reviewed_by_kind` is `CHECK`-constrained to `'dc'` — only a human blesses

The pitch had been "GoT gives you a hallucination gate on a customer-facing document."
But nothing there is generated, so there is nothing to hallucinate. Verbatim-and-reviewed
is categorically stronger than generated-and-scored, and adding a GoT-composed readout
would have been a **regression** — trading an attestable document for a scored one.

**The transferable lesson:** when a constraint can be expressed in the schema, an LLM
scorer is strictly worse — it can be wrong, and it cannot be tested as bytes. Reach for
scored iteration only where no structural expression exists.

## 4 · Why that argument does not rule it out here

CortexSim authoring has no equivalent structure to lean on:

| `cortex-pov-agent` | CortexSim |
|---|---|
| `review_state ∈ draft/reviewed/rejected` | no review gate on authored content |
| `reviewed_by_kind = 'dc'` — human blesses | no human-blessing column |
| readout cites stored bodies verbatim | scenarios/TTPs/detections are *authored*, not cited |
| the answer is a lookup | the answer is a generation |

Candidate surfaces, in rough order of fit:

- **`detection_scanner/ttps/`** — generate TTP candidates for a technique, score, aggregate
  complementary ones. Currently hand-authored (see the three untracked `TTP-2026-017x`
  DLP files on `feat/signal-library`).
- **`assertions/pos/`** — generate POS assertion candidates against a detection.
- **`scenarios/`** — compose multi-step scenarios by aggregating validated fragments.
  This is the one where N→1 merge genuinely beats a linear prompt.

The `detection_type` vocabulary (`BIOC | XQL | Analytics | Correlation | IOC | ABIOC`)
plus `detections.modeling_rules[]` gives a real coverage axis to score against — closer
to arithmetic than to taste.

## 5 · The precondition — do not skip this

**Do not build this until there is a deterministic scorer.**

Without one, `Score` is the authoring model grading its own output and then iterating on
its own grade. That converges on fluent, confident, wrong content — and in *this* repo
that failure mode is the exact one the top of `CLAUDE.md` exists to prevent:

> ***tenant-verified is 0.*** … **Authored is not proven** — do not report the two as one number.

A GoT loop with an LLM judge manufactures *authored* volume and self-reported quality.
Pointing it at a repo whose central discipline is refusing to conflate authored with
proven would be actively corrosive — it would inflate the numerator of the number this
project is careful about.

So the gate is: **what is the arithmetic scorer?** Candidates worth investigating first,
none of them confirmed:

- an **assertion** that mechanically passes/fails a generated artifact (`assertions/`)
- **loader validation** — does the generated scenario/TTP parse and resolve
  (`core/engine/scenario_loader.py`)
- **coverage delta** against the `detection_type` vocabulary or `modeling_rules[]`
- eventually, and best: **alert read-back** from a real tenant via `core/connectors/` —
  which is the only one that would make `Score` mean *proven* rather than *authored*

If the honest answer turns out to be "there is no scorer, the judge is a model," then the
answer is the same as it was in the POV agent: **don't build it.** Use the MCP server for
ad-hoc thinking and spend the time on something that moves tenant-verified off zero.

## 6 · Open items

- [ ] Identify a deterministic scorer for one authoring surface — §5 — unowned
- [ ] If a scorer exists: prototype `Generate → Score → Aggregate` on `detection_scanner/ttps/`
      for a single technique, and compare against a plain fan-out baseline before adding
      a graph — unowned
- [ ] Decide whether the graph formalism earns its keep over fan-out + filter at all;
      it only does when you genuinely need **cycles** (scored refine) and **non-tree
      merges**. Absent both, calling it GoT is branding, not architecture — unowned

## 7 · Context for whoever picks this up

Nothing was built. No code was written in `cortex-pov-agent` or in this repo; this note
is the only artifact of the evaluation.

Read in this order: §5 first (it is the gate — if it fails, stop), then §3 for the
reasoning pattern that killed it next door, then §4 for where it might land here.

The load-bearing question is not "how do I implement Graph of Thoughts." It is **"what
here can tell a generated artifact from a good one, without asking a model?"** Answer
that and the implementation is a few hundred lines. Fail to answer it and the
implementation is a machine for producing confident, unproven content.
