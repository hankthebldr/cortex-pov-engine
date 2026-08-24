# Why CortexSim Beats Caldera, Atomic Red Team, and Prelude

> **Audience:** Domain Consultants running a Cortex (XDR / XSIAM / Cortex Cloud)
> proof-of-value, and the SEs / SAs who back them. This doc arms you for the
> "why not just use the free open-source tools?" objection.
>
> **As-of:** 2026-07-10 · branch `ultracode/full-revamp` · grounds in the
> Detection Proof Layer (`core/engine/storyline.py`, `core/engine/scorecard.py`,
> `core/models_evidence.py`, `core/connectors/matcher.py`).

## The one-line wedge

Caldera, Atomic Red Team, and Prelude all answer *"did an alert fire?"*
**CortexSim answers *"the ABIOC named `Behavioral: Credential Access via LSASS`
fired CRITICAL on host `web-01` at 14:03:22, mapped to T1059.001, credited
because both the MITRE technique **and** the BIOC id matched — here is the raw
alert record and a one-click link into YOUR tenant."***

That per-detection, Cortex-native proof is invisible to any attack-centric or
SIEM-agnostic tool, because none of them model the Cortex detection vocabulary
(`BIOC | XQL | Analytics | Correlation | IOC | ABIOC`, plus the XDM
modeling-rule substrate) or reconcile back against a *known* set of detections
that should have fired.

## Differentiation matrix

| Capability | **CortexSim** | Caldera (MITRE) | Atomic Red Team | Prelude (Operator / Detect) |
|---|---|---|---|---|
| **Operator UX** | DC-operated console: enroll-token one-liner onboarding, plane → scenario → launch, guided presenter view. Cortex-branded. | Adversary/ability authoring UI; C2-operator mental model, steep for a pre-sales motion. | Raw `Invoke-AtomicTest` CLI + YAML; no orchestration UI. | Polished SaaS UI, but SOC-subscription framing, not a bake-off deliverable. |
| **Live demo** | SSE-driven **Detection Storyline** presenter: nodes light at *step-start*, a `HuntingState` shows "Cortex analyzing — K of M pending", then the specific chip flips pending→detected with a pulse the instant a real alert reconciles. | Operator graph shows *what the attacker ran*; no detection-side timeline. | None — you read terminal output. | Dashboards update, but no per-detection "catch" beat tied to the engine that caught it. |
| **Detection proof** | Per-detection, typed to the Cortex engine: "the **BIOC** X fired", "the **ABIOC** Y fired", "the **Correlation** rule Z stitched". | "An event was produced" — detection is out of scope (it's the red-team half). | Same — it runs the TTP; detection is your problem. | "An alert fired" — generic, SIEM-agnostic; cannot name a Cortex engine. |
| **Evidence / provenance** | Persisted 1:N `ResultEvidence`: raw alert JSON, `external_id`, severity, host, the **actual firing detector id** (distinct from the seeded expected logic), `matched_on` rationale, tenant deep-link, and a machine-verified-vs-attested split. | None persisted for detection. | None. | Alert metadata, but no chain-of-custody back to a specific Cortex rule + raw record + deep-link. |
| **MTTD** | **Evidence-backed, automatic.** `matcher.py` correlates each seeded Result on technique-id OR firing detection-id OR strong name overlap within a time window, takes the *earliest* qualifying alert, and computes real detector latency (`observed_at − executed_at`, re-stamped at true step-start). p50 / p90, not just avg. | No MTTD (no detection half). | No MTTD. | MTTD exists but via generic alert correlation; VECTR-class tools rely on **manual entry**. |
| **Executive reporting** | One-click **Scorecard**: per-Cortex-product coverage %, MTTD p50/p90 distribution, detection-type mix across the six-value vocabulary, ranked gap list (expected-but-missed by technique + engine), provenance rollup. Plus Cortex-branded markdown/PDF POV report citing each alert. | None — you build the deck by hand. | None. | Good reporting, but product-agnostic; won't map to XDR/XSIAM/Cortex-Cloud SKUs. |
| **Cortex product mapping** | `plane_product_map.PLANE_TO_PRODUCT` rolls coverage up to the SKU the customer is evaluating (EDR→XDR, CDR→Cortex Cloud, ITDR→Cortex ITDR, …). | None. | None. | None — a generic SIEM/EDR abstraction. |
| **Ground-truth denominator** | Orchestrator seeds **one Result row per expected detection per step**, so coverage is measured against a *known* set → true per-engine false-**negative** (gap) accounting. | No denominator — can only report what ran. | No denominator. | Has a denominator, but not typed to Cortex engines. |
| **Signal fidelity** | **Identity harness**: every TTP step runs via a service account (`runuser -l` / `sudo -u` / `su -s`) to build authentic process-lineage chains — exactly what ABIOC / Analytics behavioral-ML detections key on. | Runs as the operator's own user → under-triggers causality-based detections. | Same — runs as the invoking user. | Agent-based, but not built to exercise Cortex's behavioral causality specifically. |
| **Commercial fit** | Purpose-built pre-sales / POV bake-off deliverable: offline self-contained push bundles (clean Ubuntu 22.04, no server dependency), gated/consented dual-use tooling, read-only tenant reconcile. | Free, but a red-team framework, not a POV instrument. | Free TTP library, not a product. | Always-on BAS **SaaS subscription** — wrong motion and wrong price for a POV. |

## The two-paragraph narrative (say this out loud to the customer)

> "The open-source tools — Caldera, Atomic Red Team — are excellent at the
> *attacker* half: they run the technique. But they stop at the airlock. They
> can tell you a process executed; they cannot tell you whether **your Cortex
> tenant caught it, which detection engine caught it, or how fast.** That last
> mile is exactly the question a proof-of-value has to answer, and it's the
> question those tools were never built to answer. Prelude and the BAS crowd go
> further and correlate alerts, but they're deliberately SIEM-agnostic — to them
> every detection is just 'an alert.' They can't say 'a **BIOC** fired' versus
> 'an **ABIOC** behavioral rule fired' versus 'a **Correlation** rule stitched
> three planes together,' because their model has no concept of the Cortex
> detection vocabulary."

> "CortexSim is built the other way around. We seed a known set of detections
> that *should* fire, generate high-fidelity signal — through a service-account
> identity harness so the behavioral ML actually triggers — and then read your
> tenant back, read-only, to correlate each real alert to the exact step and
> expected detection that produced it. What you get on screen is a live kill
> chain where each node lights up the moment your Cortex platform catches it,
> badged with the engine that caught it and the true mean-time-to-detect. And
> what you hand your CISO is a scorecard: coverage per Cortex product, MTTD at
> the median and 90th percentile, the detection-type mix, and a ranked list of
> the gaps — every green box backed by a raw alert record and a one-click link
> into your own console. Nobody else can produce that artifact, because nobody
> else models detections the way Cortex does."

## Where this is grounded in the codebase

| Claim | Source |
|---|---|
| Read-only tenant pull of real alerts | `core/connectors/xsiam.py` (`get_alerts_multi_events`, `_normalize_alert`) |
| Conservative technique/detection-id/name correlation → earliest MTTD | `core/connectors/matcher.py:114` (`reconcile`), `_correlation_keys:85` |
| One Result per expected detection per step (the denominator) | `core/engine/orchestrator.py` (auto-seed from `expected_detections`) |
| Persisted evidence: raw alert, firing detector id, deep-link, provenance | `core/models_evidence.py` (`ResultEvidence`), `core/connectors/evidence_capture.py` |
| Live "catch" surface (step-start → pending → detected) | `core/api/live_frames.py`, `ui/src/components/console/DetectionCatch.jsx` |
| Storyline + Scorecard as two faces of one spine | `core/engine/storyline.py`, `core/engine/scorecard.py`, `core/engine/plane_product_map.py` |
| Identity harness (service-account causality) | `spec/identity_harness.json`, `core/engine/identity_spec.py` |

See the operator walkthrough in
[`detection-proof-operator-runbook.md`](detection-proof-operator-runbook.md).
