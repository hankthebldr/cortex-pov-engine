# Index gaps surfaced by the v2.2 crosswalk

What binding all 162 scenarios to the v2.2 master index revealed. Everything
here is a **decision for the index owner**, not an engine defect — the engine
side is closed (162/162 resolve, zero S-10/S-11/S-12/S-15).

Generated against index v2.2 on 2026-07-31 by `scripts/uctc_crosswalk_v2.2.py`.

---

## 1 · The index cannot absorb the corpus 1:1

| | count |
|---|---|
| Index test cases | 266 |
| …**DET/HNT** (detection-backable) | **107** |
| …POS/PLT/AUT (posture / platform / automation assertions) | 159 |
| Engine detection scenarios | **162** |

Per plane it is tighter still: 26 CDR scenarios against 3 `UC-CDR` DET rows,
20 ITDR against 4, 6 ASM against 1.

This is why the join is `tc_refs` (a set) rather than `tc_ref` (one id). The
index's own model already works this way — `scenario_library_v2.2.csv` binds one
POV-SC payload to many test cases — so the engine now matches it rather than
forcing a 1:1 the index never claimed.

**Coverage today: 86 of 266 test cases evidenced by a scenario (67 of 107
DET/HNT), plus 18 bound by an assertion artifact (8 of them net-new) → a union
of 94 of 266. None of the 18 has been executed against a live tenant, so
*authored* is 94 and *proven* is 86 + 0. See §4a.**

---

## 2 · 16 proposed v2.3 test cases — and the reason is licensing

19 scenarios resolved as `NET-NEW`. Their detection *capability* has an honest
home in the index; their **SKU** does not.

| Plane | Scenarios | Bound to today | Why that is wrong for a quote |
|---|---|---|---|
| BROWSER | 6 | `UC-DLP`, `UC-AES` | Prisma Browser is not the **Endpoint DLP** SKU |
| AI_ACCESS | 5 | `UC-DLP`, `UC-AIRS` | Cortex AI Access Security is its own line |
| KOI | 8 | `UC-SCA`, `UC-AGTX`, `UC-AIRS` | agentic endpoint is the **AES** add-on |

Left as-is, Phase 3 license gating would quote Endpoint DLP for a Prisma
Browser POV. The proposed test cases are in
[`proposed-tc-v2.3.csv`](proposed-tc-v2.3.csv):

- **`UC-AIACC`** (proposed new UC) — 5 TCs for AI Access Security.
- **`UC-BROWSER`** (proposed new UC) — 6 TCs for Prisma Browser.
- **`UC-AEPS`** (**existing** UC — already carries the correct agentic-endpoint
  SKU) — 5 TCs. Its three current rows are AUT/PLT capability assertions; the
  threat cases simply do not exist yet.

Until v2.3 merges, those scenarios stay bound to their nearest honest capability
match so the corpus remains strict-clean. Re-key them by adding rows to
`CROSSWALK` in `scripts/uctc_crosswalk_v2.2.py` and re-running `--apply`.

### Open: Cloud App Security has no SKU

`sku_catalog.csv` carries no Cloud App Security line. The 9 `CLOUD_APP`
scenarios are bound to `UC-ITDR` (the IdP/OAuth surface — correct for the
identity half) and `TC-DLP-11` (the data-movement half). If Cortex Cloud App
Security is a separately-licensed capability, it needs a price-book entry before
Phase 3 can gate on it.

---

## 3 · The corpus over-claims differentiation in 76 of 161 scenarios

`S-13` fires 101 times. It is directional:

| | count |
|---|---|
| **Over-claim** (scenario claims a higher tier than the index) | **76** |
| Under-claim | 25 |

Worst offenders: `LEAD → PARITY` (42), `MOAT → PARITY` (17), `MOAT → LEAD` (17).
By plane: EDR 19, CDR 8, KOI 8, CLOUD_APP 6, ITDR 6.

**Deliberately not auto-corrected.** Aligning a positioning claim to the index
is a content-review decision — a scenario may genuinely demonstrate MOAT-tier
behaviour that the index has under-tiered, and mechanically overwriting 101
authored values would erase that signal. Review `old_moat_tier` vs `index_tier`
in [`crosswalk-v2.2.csv`](crosswalk-v2.2.csv) and correct whichever side is
wrong.

---

## 4 · 13 scenarios validate posture, not detections

`S-14` fires where a scenario binds a POS/PLT/AUT test case:

- **AI_SPM** — all 6 of `SIM-AISPM-001..006` map 1:1 onto `TC-AISP-01..06`,
  every one POS/PLT class.
- **ASM** — `SIM-ASM-001/002/003/005`.
- **CSPM** — `SIM-CSPM-001/002/004`.

These bindings are *correct*. The scenarios really do assert posture-finding
state rather than a fired detection. The warning is the signal the plan's Phase 4
wants: these need a **fixture harness**, not authored detection content. Building
one is the single largest effort saver left — the index carries 159 POS/PLT/AUT
rows and authoring a detection scenario per row would be wasted work.

**This is now built.** See §4a.

---

## 4a · The assertion mechanism — and its honest ceiling

The 159 POS/PLT/AUT rows now have an artifact type that is not a scenario:
`assertions/{pos,plt,aut}/*.yml`, loaded by `core/engine/assertions.py`, scored
by the same `verifier.score_run`. An artifact **cannot be authored unless it
can fail** — `A-17` proves falsifiability by execution at load time and is not
gated by `CORTEXSIM_STRICT_REFS`. Contract: [`assertions.md`](assertions.md).

| class | total | by scenario | by assertion | union | open | index-scoreable | tenant-verified |
|---|---:|---:|---:|---:|---:|---:|---:|
| DET | 102 | 63 | 0 | 63 | 39 | 49 | 0 |
| HNT | 5 | 4 | 0 | 4 | 1 | 1 | 0 |
| POS | 110 | 18 | 11 | 19 | 91 | 19 | 0 |
| PLT | 43 | 1 | 4 | 5 | 38 | 16 | 0 |
| AUT | 6 | 0 | 3 | 3 | 3 | 6 | 0 |
| **all** | **266** | **86** | **18** | **94** | **172** | **91** | **0** |

**The ceiling is not 266, and it is not close.** From the three design triages,
roughly **45 of the 172 open rows** are reachable by this substrate at all:
POS 13, PLT 12, AUT 3 (+1), DET 4. The rest are refused for stated reasons that
are decisions for the index owner, not engine work:

- **~92 POS rows carry `Qualitative pass`** and therefore clamp to
  `not_applicable` even when satisfied. Adopting **one** measurable POS-family
  threshold in `proposed-tc-v2.3.csv` — e.g. *"Planted-Finding Discovery
  Coverage ≥ 100 % within one scan cycle"* — converts 10 of the 11 authored POS
  assertions from `not_applicable` into real scored passes **with zero engine
  work**. This is the highest-leverage index change available.
- **~10 POS rows** name `Cortex Cloud Inventory (/api/v1/inventory)` as their
  `detection_source`. Every probe is XQL; there is no REST driver, and a driver
  that resolves `pending` for everything is indistinguishable from not having
  one.
- **8 of 12 "provable" PLT rows were refused on inspection**: third-party sensor
  shapes the engine cannot emit (TC-NDR-05), 6–12 months of resident data a POV
  tenant cannot have (TC-XTI-06, TC-XDL-05), wall-clock query latency and
  hot/warm/cold tier residency no probe can measure without folding the
  emitter's own delay into the platform's number (TC-XDL-04, TC-SIEM-01),
  differential RBAC needing two credentials in one evaluation (TC-PGE-02), and
  Marketplace connector provenance no read API exposes (TC-ITDR-04, TC-TH-04).
- **3 of 6 AUT rows are unreachable**: TC-APB-01's premise is a property of the
  *customer's* playbook, TC-APB-03 needs a human judging generated-playbook
  quality plus a write, TC-APB-04 needs a live integration deliberately broken —
  a destructive write outside the read-only charter. **AUT will never reach 6/6
  and must not be reported as though it could.**
- **31 of the 35 open DET rows carry `mitre_techniques: TBD`** and 23 name a
  product surface the engine cannot drive (AgentiX, Unit 42 managed services).
  They are POS/PLT/AUT work wearing a DET label and need an **index
  reclassification**, which is a human decision.

Two refusals are worth reading because they are the pattern:
**TC-CIEM-01** was dropped because the `cspm` fixture has no permissions
boundary or SCP, so a naïve attached-policy scanner satisfies any assertion
exactly as well as a real net-effective permission engine — the check could not
distinguish the capability from its trivial imitation. **TC-DLP-03** was dropped
because two of its three "independent" exfil channels both land in
`panw_ngfw_traffic_raw`, so a `count_distinct(channel) == 3` terminal measures
two things, not three.

**Not built: precision.** Every POS assertion measures *recall* against planted
ground truth. None measures false positives, so a posture engine that flags
every resource in the account satisfies all of them. Each artifact's
`scope_limitations` says so verbatim. The obvious fix — a planted "clean
control" asserted NOT flagged — was designed and rejected: a CIS-benchmarked
scan legitimately raises a LOW finding on any S3 bucket, so `flagged == 0` goes
red against a *correct* product. Manufactured red is as forbidden as false
green. It needs a severity-field binding confirmed against a live tenant first.

---

## 5 · 57 test cases cannot be machine-scored

57 of the 107 DET/HNT rows carry no measurable threshold (`Qualitative pass` or
blank). **The corpus binds 37 of them.**

`registry.unscoreable()` surfaces the list. Phase 2's verifier must emit
`not_applicable` for these and never `pass` — a silent pass on an unscoreable
test case is worse than no scoring at all, because it produces a green POV
readout that means nothing.

---

## 6 · Duplicate re-keyed in the v2.0 table

`v2.0-tc-mapping-table.csv` carried two different test cases both labelled
`TC-IR-02`. The second ("issue grouping stitches alerts across disparate
sources") is `TC-IR-13` in v2.2, so it was re-keyed rather than deleted.


---

## 7 · The index files two XSIAM capabilities under Cortex Cloud

Surfaced by the alignment audit, and the reason three EDR scenarios still derive
a cloud SKU after the over-binding fix:

| Test case | What it validates | Index UC | Product mapping |
|---|---|---|---|
| `TC-WAAS-04` | "Agent-Based Threat Detection (Runtime) — **Cortex Agent** detects web application attacks" | `UC-WAAS` | Cortex Cloud · Cloud AppSec |
| `TC-CITH-07` | "AI-Powered Alert Stitching — alert-to-incident ratio demonstrates >80% noise reduction" | `UC-CITH` | Cortex Cloud |

Both describe capabilities the **XDR agent and XSIAM** deliver, but their parent
use cases are mapped to Cortex Cloud. Because `required_addons` is unioned over
a scenario's whole evidence set, `SIM-EDR-014` / `SIM-EDR-020` (ASPX web shell
in `w3wp`) and `SIM-EDR-018` (AI SOC summarization) each inherit `Cloud Runtime`.

Those bindings are honest — an endpoint agent really is what catches the web
shell — so they were left in place and allowlisted by name in
`tests/engine/test_corpus_refs_strict.py`. Dropping them to tidy the SKU would
be the force-fit this work exists to avoid. **The fix belongs upstream:** either
re-file those two TCs, or split the product mapping so it can vary per test case
rather than per use case.

### Related: should entitlements derive from the whole evidence set?

`_derive_entitlements` unions over every `tc_ref`. That is why one aspirational
secondary ref could inflate a scenario's bill of materials. The alternative —
derive from the primary only — under-states scenarios that genuinely span
products. A third option is to keep the union but split the output into
`required_addons` (primary) and `optional_addons` (secondary-derived), so
`POST /api/pov/scope` can block on the first and merely note the second.

Left as-is pending a decision. It is a semantics call about what "required"
means in a POV quote, not a bug.
