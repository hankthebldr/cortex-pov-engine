# Next-Generation Detection Engine — Research & Strategy

**Status:** research + strategy proposal · **Date:** 2026-08-22
**Companions:** [`caldera-parity-and-next-generation-strategy.md`](caldera-parity-and-next-generation-strategy.md) (execution engine) ·
[`production-readiness-and-sprint-plan.md`](production-readiness-and-sprint-plan.md) (shipping)

**Method.** Source-level reading of MITRE CTID's *Summiting the Pyramid* (v4 docs
tree + its 88/93-row scored-analytics datasets), DeTT&CT, and CTID's sensor-mapping
and attack-flow repos — all cloned and read directly. Plus a **measured
self-assessment of this repo's own 1,777 detection objects**, run with the scripts
in §6. Where a number is heuristic rather than exact, it is labelled as such.

---

## 0. The gap, in one paragraph

The two companion documents cover how the engine **executes** and how it **ships**.
Neither addresses the thing the product is actually made of: **1,777 detection
objects across 170 cards, none of which carry any measure of how good they are.**
We count detections. We do not measure them. As a direct consequence, the corpus
has drifted toward brittle artifact matching while wearing behavioural labels — and
**74% of every aggregated detection in the corpus carries a threshold that cannot
fail.** A next-generation detection engine is not one with more detections. It is
one that can **score its own content for robustness, prove each detection is capable
of failing, and know whether the customer can even see the telemetry it depends on.**

---

## 1. The external bar in 2026

### 1.1 Robustness is now a formal, scored property — Summiting the Pyramid

MITRE CTID's *Summiting the Pyramid* (STP) is the standard that did not exist when
this corpus was designed. It scores an analytic on **how hard it is to evade**, on
two axes:

**Five analytic-robustness levels** (1 = trivially evadable → 5 = forces the
adversary onto a different technique entirely):

| L | Name | What it keys on | Evasion cost |
|---|---|---|---|
| **1** | Ephemeral Values | hashes, filenames, domains, PIDs | change one bit |
| **2** | Core to Adversary-Brought Tool | Cobalt Strike / ADFind / Mimikatz specifics | recompile or reconfigure the tool |
| **3** | Core to Pre-Existing Tool (LotL) | defender-managed binaries — powershell, certutil, wmic | hard to modify, but blends with benign use |
| **4** | Core to *Some* Implementations | low-variance behaviours | requires a substantially different implementation |
| **5** | Core to the (Sub-)Technique | invariant behaviours / chokepoints | forces a different technique |

**Event-robustness columns** — where the telemetry originates, because an analytic is
only as trustworthy as the sensor under it: host-based **A**pplication → **U**ser-mode
→ **K**ernel-mode; network **P**ayload → **H**eader. A score is written `<level><column>`
— `1K`, `2H`, `4U`.

The reference dataset is the finding: of **88 real open-source Sigma analytics** CTID
scored, **39 are Level 1** and only **8 reach Level 4**. Most shipping detection
content in the industry is the most evadable kind. That is the bar we are measured
against — and the opportunity.

### 1.2 Detections are software, or they rot

The 2026 detection-engineering consensus treats rules as code with a backlog, peer
review, a **test suite**, a deprecation policy, and CI that tests rules against
labelled data before deploy. Programs measure MTTD, false-positive rate, and rule
lifecycle as first-class metrics — not alert counts.

We are partway there: the corpus is version-controlled, schema-validated, and CI-gated
for *reference integrity*. Nothing tests whether a detection **works**, and nothing
tests whether it **can fail**.

### 1.3 Coverage is meaningless without visibility — DeTT&CT

DeTT&CT (Rabobank CDC) scores data-source quality on five dimensions — **completeness,
field completeness, timeliness, consistency, retention** — and separates *visibility*
(can I see it at all?) from *detection* (do I have a rule?). STP carries the same idea
as a **Telemetry Quality score** and *minimum telemetry requirements* per analytic.

This is upstream of everything we do. A POV that reports "Cortex missed it" when the
customer never ingested the data source is not a detection finding — it is a
manufactured false negative, the same failure class the payload shelf was built to
prevent for tool delivery.

---

## 2. Self-assessment: what our corpus actually is

**Corpus:** 170 cards · **1,777 detection objects** · 1,147 with a readable logic body.

| Kind | Objects |
|---|---:|
| xql_queries | 563 |
| analytics_modules | 329 |
| iocs | 301 |
| biocs | 262 |
| correlation_rules | 183 |
| abiocs | 134 |
| modeling_rules | 5 |

### 2.1 The finding: three quarters of aggregated detections cannot fail

Across the corpus, 364 detections aggregate with `comp count() as X … | filter X >= N`.
The distribution of `N`:

| Threshold | Count |
|---|---:|
| **`>= 0`** | 9 |
| **`>= 1`** | **261** |
| `>= 2` … `>= 600` | 94 |

**270 of 364 (74%) use `>= 0` or `>= 1`.** After a `comp`, a group only exists if it
has at least one member — so `filter X >= 1` is a tautology. These detections *look*
statistical and behavioural. They filter nothing. The aggregation is decorative.

Worst affected is the tier that is supposed to be most behavioural: **64 of 134
ABIOCs (48%)** carry the no-op, versus 171/563 XQL and 43/262 BIOCs.

The 94 detections with real thresholds (`>= 2` … `>= 600`) prove the authors know how
to write them. **This is a content-quality defect with a mechanical fix, not an
architectural problem.**

This is exactly the defect class the repo *already solved once*: `core/engine/
assertions.py` code **A-17** rejects an assertion that cannot produce both a pass and
a fail — *"this check can never fail and therefore proves nothing."* That principle
was never extended to the detection corpus, which is 1,777 objects and the actual
product.

### 2.2 What the detections key on (indicative, not certified STP)

Classifying the **anchor predicate** of each body — the clause that would kill the
detection if the adversary changed it:

| Anchor | Share |
|---|---:|
| Literal artifact (file / domain / hash) → **STP L1** | 14.4% |
| Adversary tool name → **STP L2** | 0.6% |
| Other literal predicate → **L2–L3** | 44.8% |
| Structural: identity / process lineage → **L3+** | 22.3% |
| No literal anchor → **L4** | 17.9% |

*Heuristic, deliberately conservative — real STP scoring is per-observable.* An earlier,
looser pass that credited any body containing a behavioural token scored 71% at L4+;
sampling disproved it. Representative case, an **ABIOC**:

```
| filter action_process_command_line contains "ADRecon"      ← L2: rename the script
| comp count() as adrecon_runs by agent_hostname
| filter adrecon_runs >= 1                                   ← tautology
```

A Level-2 artifact match wearing a Level-4 label, with a threshold that cannot fail.

The good news is equally real — this pattern is common and is genuinely L3+:

```
| filter action_process_image_name in ("python3","perl","ruby")
| filter action_process_image_command_line contains_any ("/tmp/","/var/tmp/","/dev/shm/")
| filter actor_effective_username in ("www-data","apache","nginx","nobody")
```

No literal artifact; interpreter class × staging-directory class × service-account
identity. **The identity harness is what makes that expressible** — it is our
structural advantage, and 22.3% of the corpus already uses it.

---

## 3. The three capabilities that make this next-generation

### R1 · Robustness as a first-class, scored property

Every detection object gains `stp: {level: 1-5, column: A|U|K|P|H, rationale: "…"}`.
Scored at authoring, validated in CI, surfaced in the console and the POV report.

Unlocks:
- **A defensible claim**: "63 of your detections are Level 4+; the industry's scored
  open-source baseline is 8 of 88." That is a competitive statement no BAS vendor can
  make, backed by MITRE's own methodology.
- **A quality ratchet**: a floor per detection type — an **ABIOC below L3 does not
  load**, the same way a scenario with a dangling `tc_ref` does not load today.
- **The honest version of the difficulty ladder** proposed in the Caldera doc: STP
  is the axis, so the ladder measures against a published standard instead of an
  invented scale.

### R2 · Falsifiability gate — extend A-17 to the corpus

Port the assertions guard to detection content: **a detection that cannot fail does
not ship.** Concretely — reject `filter <agg> >= 0|1`; require an aggregating
detection to declare the population it discriminates against; require every card to
carry a **negative control** (the A-18 pattern) — an input on which this detection
must *not* fire.

Fixes 270 detections and permanently closes the regression path. This is the highest
value-per-hour work available anywhere in the repo.

### R3 · Telemetry readiness — validate visibility before validating detection

Each detection declares its minimum telemetry requirement (dataset, fields, event
type). Before a run, a **readiness probe** answers *"can this tenant see this at
all?"* — separating three outcomes the engine currently collapses into one:

| Outcome | Today | Should be |
|---|---|---|
| Data source absent | "missed" | **`not_visible`** — an ingestion finding |
| Data present, no detection fired | "missed" | **`missed`** — a real detection gap |
| Data present, detection fired | detected | detected |

This maps onto the existing `pending` / `not_applicable` verdict discipline and reuses
the preflight dataset-presence check already built. It converts our most damaging
failure mode — blaming Cortex for the customer's ingestion — into a sellable finding.

---

## 4. How they compose — the detection quality ledger

Per detection, four independent axes, none currently measured:

```
  ROBUSTNESS   how hard to evade         STP level+column        (R1)
  FALSIFIABLE  can it fail at all        A-17-style gate         (R2)
  VISIBLE      can the tenant see it     telemetry readiness     (R3)
  PROVEN       has it ever fired         tenant-verified count   (existing: 0)
```

That last row is already in CLAUDE.md and still reads **tenant-verified: 0**. The
ledger's discipline is the same one this repo already applies to coverage: *authored
is not proven.* Robustness-scored is not proven either — but it is measurable
offline, today, without a tenant, which is precisely what makes R1 and R2 shippable
now.

**The product claim this unlocks:** not "we ran 200 scenarios," but —

> *Of the 47 detections exercised, 31 are Level 4+ (evasion-resistant), 4 are Level 1
> and will break on rename, 6 could not fail as written, and 3 never had the telemetry
> to fire. Here is the rewrite for each.*

No BAS or AEV vendor ships that today.

---

## 5. Roadmap increments

Sized for the cadence model; slots after the Sprint 1–2 production blockers.

| # | Increment | 🍅 | Gate |
|---|---|---:|---|
| D-1 | Falsifiability linter: flag `>= 0\|1` aggregations, report per card | 2 | 270 known offenders enumerated |
| D-2 | Fix the 270 — real threshold, or drop the decorative aggregation | 4 | zero tautological thresholds |
| D-3 | CI gate blocking reintroduction (the A-17 analogue) | 2 | regression impossible |
| D-4 | `stp` field in the card schema + scoring rubric doc | 2 | schema accepts, docs explain |
| D-5 | Score the 134 ABIOCs (highest-label / lowest-evidence tier first) | 4 | every ABIOC carries a level |
| D-6 | Per-type robustness floor enforced at load | 2 | sub-floor ABIOC rejected |
| D-7 | Negative control per card (A-18 analogue) | 4 | every card declares a must-not-fire input |
| D-8 | Telemetry requirement per detection + readiness probe | 4 | `not_visible` distinct from `missed` |
| D-9 | Robustness + falsifiability columns in the POV report | 2 | the §4 claim renders |

D-1 → D-3 is one sprint and fixes a real, counted, customer-visible quality defect.
Start there.

---

## 6. Verified vs inferred

**Exact** (regex over literal file contents, reproducible):
```bash
# corpus shape
python3 - <<'P'
import json,glob,collections
k=collections.Counter()
for f in glob.glob('detection_scanner/ttps/*.json'):
    for kind,v in (json.load(open(f)).get('detections') or {}).items():
        if isinstance(v,list): k[kind]+=len(v)
print(dict(k), sum(k.values()))
P
# tautological thresholds
grep -roE 'filter [a-z_]+ >= [0-9]+' detection_scanner/ttps/*.json | sort | uniq -c | sort -rn | head
```
- 1,777 objects / 170 cards / 1,147 bodies — exact.
- 364 aggregating detections; 261 at `>= 1`, 9 at `>= 0` — exact.
- 64/134 ABIOCs carrying the no-op — exact.

**Heuristic** (labelled as such above): the §2.2 anchor-predicate distribution. It is a
triage signal for where to start scoring, **not** a certified STP score. Real STP
scoring is per-observable and requires human judgement; the rubric in D-4 is what
makes it defensible.

---

## 7. Sources

- [Summiting the Pyramid — CTID project](https://ctid.mitre.org/projects/summiting-the-pyramid/) · [repo](https://github.com/center-for-threat-informed-defense/summiting-the-pyramid) (levels, columns, scored-analytics datasets read directly)
- [Bring the Pain with Robust and Accurate Detection](https://ctid.mitre.org/blog/2024/12/16/summiting-the-pyramid-bring-the-pain/)
- [DeTT&CT — data-source quality, visibility vs detection scoring](https://github.com/rabobank-cdc/DeTTECT)
- [Detection Engineering in 2026: the complete lifecycle](https://kravensecurity.com/detection-engineering-lifecycle/)
- [Detection Engineering Maturity Matrix — Kyle Bailey](https://kyle-bailey.medium.com/detection-engineering-maturity-matrix-f4f3181a5cc7)
- [Detection rule validation](https://www.deepwatch.com/glossary/detection-rule-validation/)
- [What ATT&CK techniques to detect first — Securelist](https://securelist.com/detection-engineering-backlog-prioritization/113099/)
