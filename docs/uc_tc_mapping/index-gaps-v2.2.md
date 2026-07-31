# Index gaps surfaced by the v2.2 crosswalk

What binding all 161 scenarios to the v2.2 master index revealed. Everything
here is a **decision for the index owner**, not an engine defect — the engine
side is closed (161/161 resolve, zero S-10/S-11/S-12/S-15).

Generated against index v2.2 on 2026-07-31 by `scripts/uctc_crosswalk_v2.2.py`.

---

## 1 · The index cannot absorb the corpus 1:1

| | count |
|---|---|
| Index test cases | 266 |
| …**DET/HNT** (detection-backable) | **107** |
| …POS/PLT/AUT (posture / platform / automation assertions) | 159 |
| Engine detection scenarios | **161** |

Per plane it is tighter still: 26 CDR scenarios against 3 `UC-CDR` DET rows,
20 ITDR against 4, 6 ASM against 1.

This is why the join is `tc_refs` (a set) rather than `tc_ref` (one id). The
index's own model already works this way — `scenario_library_v2.2.csv` binds one
POV-SC payload to many test cases — so the engine now matches it rather than
forcing a 1:1 the index never claimed.

**Coverage today: 84 of 266 test cases evidenced (65 of 107 DET/HNT).**

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
