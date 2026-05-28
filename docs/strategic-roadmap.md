# CortexSim Strategic Roadmap

For: PANW product leadership, DC enablement, security architecture
For the 6-month horizon, with research-backed alignment to where the
broader security and BAS community is moving.

This is a *strategic* roadmap, not a feature list. It groups upcoming
work into horizons aligned with industry trends, and each item carries
a verifiable rationale: a competitive wedge, a customer-pain anchor,
or an alignment with a market shift CortexSim should ride.

## North star

> The best breach-attack-simulation tool on the market for proving
> Cortex value. A DC sits down in a customer lab, runs CortexSim, and
> within 90 minutes the customer's security architect has a
> deck-ready visualization of where Cortex outperforms their current
> stack. Detection, stitching, response, all measured against
> verifiable industry benchmarks.

## Progress log — cumulative shipped state

> Living section. Updated as phases land so leadership can read
> "where are we" without scraping the git log. Newest first.

### 2026-05 — Detection-content surface (TTP browser trilogy + Navigator export + search)

The Coverage tab is now the operator's detection-content home, not
just a heatmap. Six PRs landed an end-to-end loop from *discover a
technique* → *read the shipped detection* → *see what runs it* →
*prove coverage to the customer*:

- **#50 — TTP Browser.** New Coverage sub-tab over the
  `detection_scanner/ttps/*.json` corpus. Filter by status / tactic /
  platform; card grid → detail panel with identity, MITRE chain,
  threat actors, Cortex product mapping. Backend `GET /api/ttps` +
  `GET /api/ttps/{id}`. Reverse cross-link `referenced_by_adapters`
  closes the adapter→TTP navigation loop wired via the
  `cortex:navigate-ttp` custom event.
- **#51 — Detection-body reveal + copy.** The detail panel's
  detection section went from counts-only to a per-kind accordion
  (BIOC / XQL / correlation / IOC / analytics) with the raw logic
  body and copy-to-clipboard — operators paste straight into XSIAM
  Query Center.
- **#52 — Run history per TTP + drill-down.** New
  `GET /api/ttps/{id}/runs` rolls up every Run that exercised the
  TTP (expected/observed counts, min MTTD) by joining the indexed
  `Result.ttp_ref`. Detail panel renders a colour-coded run table;
  clicking a row emits `cortex:navigate-run` and jumps to the
  validation wizard.
- **#53 — Per-TTP ATT&CK Navigator export + branded Pages board.**
  "Export ATT&CK layer" button on the detail panel downloads a
  Navigator v4.5 layer scoped to the card's technique(s) — the
  briefing-ready artifact a DC pastes into the customer's Navigator
  to show "here's exactly what this detection covers." Ships
  alongside a refreshed `docs/site/` landing page with a Cortex-
  branded "Platform at a glance" coverage board.
- **#54 — Free-text search in the TTP grid.** Tokenised AND search
  over id / name / summary / tags / technique ids / actor names, plus
  a Clear button that resets search + chips. Discovery scales to an
  N-growing corpus.

**Net operator value:** the TTP card answers all four POV questions
in one panel — *what does it look like, what drives it, did we run it,
how do I prove it* — and every answer is a copy-paste-able artifact.
The grid is discoverable by chip filter *and* by full-text search.

### Test floor (2026-05-26)

- **Python:** 800 passed · 55 skipped
- **UI (vitest):** 249 passed
- **Build:** clean (~424 kB JS / ~120 kB gzipped)
- **Open issues:** tracked in the `roadmap:next` and `roadmap:deferred`
  labels — see "Tracker" section below.

### Earlier foundations (pre-2026-05)

- **#43–#44 — Enterprise console MVP.** Live event stream, PANW
  Advantage matrix, MTTD histogram, POV bundle export, run-history
  badges. 149→210 UI tests.
- **#45–#46 — Tool Adapter framework.** Static catalog under
  `tools/packs/*.yml`; Coverage "Tool Adapters" picker surfaces every
  offensive/defensive tool a scenario can reference via
  `external_tools[].adapter_ref`. 18 adapters across cloud-audit,
  Linux-credential, web-recon, identity, social.
- **#47 — "Tools Used" report section.** POV report now carries a
  licence + attribution audit trail of every tool a run touched.
- **#48 — IaC auto-pull.** `adapter_refs[]` auto-includes each
  adapter's declared `iac_module`; Lab picker pre-fills from a
  scenario's tool requirements.
- **Phase A/B IaC generator** — AWS feature-complete: 10 modules
  (`base`, `edr`, `cdr`, `content-library`, `itdr`, `ndr`, `cspm`,
  `asm`, `tim`, `telemetry-replay`) covering every active plane.

### Next steps (immediate, post-2026-05)

Smallest-first, each a focused PR. Tracked in GitHub Issues with the
`roadmap:next` label so the project board mirrors this list:

1. **Scenarios-by-TTP + "launch all"** — new
   `GET /api/scenarios?ttp_ref=` then a detail-panel button to launch
   every scenario citing the TTP. Closes the action loop the run
   history opened.
2. **Syntax highlighting** on the detection-body `<pre>` blocks
   (XQL / Sigma) — readability for the copy workflow.
3. **Promote draft TTPs** (`_drafts/TTP-2026-0008/0009/0010`) to
   active after a content review.

Deferred (`roadmap:deferred`):

- **TTP authoring UI** — corpus mutations are still filesystem-only
  + restart. Needs a git-write + reload story; reasonable scope as
  its own focused phase, not a follow-up.

Larger, horizon-aligned items continue below.

### Tracker

- `roadmap:next` → next-up queue mirrored from above
- `roadmap:deferred` → larger items waiting on dependencies or staffing
- Issues link back to this doc by section anchor so the conversation
  lives in one place.

## Where the market is moving — 2026 BAS landscape

Five trends shape the next 12 months of BAS investment:

1. **GenAI / LLM red teaming.** Every CISO is now asking "how do I
   secure my organization's LLM deployments?" — OWASP LLM Top 10
   went from draft to industry baseline in 18 months. CortexSim has
   first-mover advantage with `cortex-prompt-attacker` + AIRS
   scenarios, but the operator surface needs to expose this
   prominently.
2. **Identity-cloud convergence.** ITDR + CIEM are merging into
   single offerings. Customers no longer accept "the endpoint vendor
   doesn't see cloud identity." The PANW Stack Coverage matrix
   demonstrates this; CortexSim scenarios need to keep pushing the
   stitching narrative.
3. **Continuous validation, not point-in-time tests.** Gartner +
   Forrester both shifted BAS positioning in 2025: from "annual red
   team replacement" to "continuous detection-health validation."
   This means GitOps-style scenario libraries, scheduled validation,
   regression alerting.
4. **MITRE ATT&CK 16 + Cloud-specific tactics.** The 2026 ATT&CK
   release will expand cloud-specific TTPs significantly. CortexSim
   needs early coverage for the new IDs to maintain its position
   as the most-current scenario library.
5. **SOC analyst burnout → AI assistance.** Cortex XSIAM ships
   Purple AI for analyst assist; BAS tools need to show how their
   simulations specifically train the AI assistant + measure
   its accuracy.

## Horizon 1 — 0 to 3 months (post-MVP)

**Theme:** finish what's started. Ship the rest of the e2e
methodology, harden the integration surface, complete the Cortex
product coverage matrix.

### H1.1  Tier C — Isolated container execution (e2e methodology phase 4)
**Anchor:** docs/design/e2e-execution-methodology.md
**Wedge:** No competitor BAS validates that the right *binaries fire
under the right identities* — they validate "we ran some payload." Tier C
is the test that catches identity-harness regressions silently
degrading multi-identity scenarios. Ship in 2 commits:
- Container image + auditd + sinkhole infrastructure (3 weeks)
- Tier C reference scenarios (SIM-EDR-001, SIM-CDR-001, SIM-MP-004) (2 weeks)

### H1.2  Backend run-abort + run-events SSE
**Anchor:** UI surfaces already wired; the toast says "Abort endpoint
not yet implemented" — that's a TODO the backend owns.
**Outcome:** UI's ⌘+abort actually stops a running scenario. Live
event stream switches from polling fallback to real SSE. Acceptance
criteria: from confirm dialog click to agent SIGTERM under 5s.

### H1.3  Playwright spec migration
**Anchor:** legacy specs target `?theme=legacy` UI; soft-fail
toggled in CI. Time to migrate.
**Outcome:** all 5 specs rewritten against console-UI selectors, the
`?theme=legacy` escape hatch removed, e2e flips back to a hard gate.
~2 days of focused work; was deferred because structural changes
were in flight. They've settled.

### H1.4  Spec-validated scenario authoring wizard
**Anchor:** "New scenario" button in Operations head is disabled.
**Outcome:** Multi-step form that scaffolds a new scenario YAML
adhering to the Pydantic schema, runs Tier A + B locally before
save, lands a draft under `scenarios/_drafts/`. Lowers the floor
for DCs to contribute new content from the field.

### H1.5  Multi-run comparison view
**Anchor:** DCs running rule-tuning POVs need to compare "before
rule change" vs "after rule change."
**Outcome:** New sub-view of Evidence: pick 2-N runs, render
side-by-side scorecard + MTTD distribution + delta highlights.

---

## Horizon 2 — 3 to 6 months

**Theme:** lead in the categories competitors don't compete in yet.
GenAI red teaming. Continuous validation. Identity-cloud stitching.

### H2.1  GenAI / LLM red teaming as a first-class plane
**Anchor:** OWASP LLM Top 10. AIRS exists; AI Access Security
exists; CortexSim has prompt-attacker. The market gap: an operator
surface that makes LLM red teaming as discoverable as endpoint red
teaming.
**Outcome:**
- AIRS gets a top-level plane in the rail (separate from EDR/CDR/etc.)
- Promptmap-compatible probe library extended (currently 5 scenarios;
  target 25 covering all OWASP LLM01–10)
- New "AI Posture" tab? Or sub-view of Coverage that maps OWASP LLM
  Top 10 to scenario coverage like the ATT&CK matrix does for endpoint
- Wedge: no major EDR vendor has a first-party LLM red-teaming tool.
  AttackIQ has prompt-injection content packs but doesn't connect to
  a runtime LLM protection product.

### H2.2  Scheduled scenario validation + regression alerting
**Anchor:** Gartner BAS positioning shift.
**Outcome:** Cron-style schedules per scenario ("run SIM-MP-004
every Monday 9am"), webhook alerts if previously-detected scenarios
regress to undetected. Backend: new `/api/schedules/*` endpoints.
UI: new "Schedules" tab or integration into Operations card menus.

### H2.3  ATT&CK 16 coverage backfill
**Anchor:** MITRE ATT&CK 16 release expected Q3 2026.
**Outcome:** New scenarios covering the expanded cloud-specific
TTPs (ESXi-targeted ransomware, cloud-IAM federation abuse,
container-image supply chain). Coverage matrix updates to v16
within 30 days of MITRE's release.

### H2.4  Cortex POV briefing builder
**Anchor:** DCs hand-craft PowerPoints for every POV close.
**Outcome:** "Generate briefing" button (or `⌘B`) that bundles:
- Filled POV report (markdown → PDF)
- Timeline screenshots (PNG)
- ATT&CK Navigator layer + screenshot
- PANW Advantage matrix scoped to the customer's competitive incumbent
- Executive summary auto-generated from the run data + scenario notes

Output: a `.zip` with all the artifacts ready to drop into a deck
template.

### H2.5  Customer-facing "validation portal"
**Anchor:** Customers running CortexSim themselves between DC visits.
**Outcome:** Read-only console view that surfaces validation history,
trends, and a customer-friendly version of the PANW Advantage matrix
without exposing DC-specific tooling. Lives behind a per-tenant
auth layer.

---

## Horizon 3 — 6 to 12 months

**Theme:** platform. CortexSim as the default content surface for
PANW security architects globally. Multi-tenant, SaaS-ready, deeply
integrated with the Cortex product line.

### H3.1  Multi-tenant SaaS option
**Anchor:** Single CortexSim instance per DC laptop doesn't scale to
500+ DCs globally.
**Outcome:** Centrally hosted CortexSim that DCs log into with
PANW SSO. Per-customer workspaces, isolated scenario libraries,
RBAC.

### H3.2  Cortex XSIAM integration for results round-trip
**Anchor:** Today CortexSim is read-only against Cortex — we generate
signals INTO XSIAM. Going further: pull alerts BACK OUT to
auto-validate scenarios.
**Outcome:** Cortex XSIAM API connection. After a scenario runs,
CortexSim queries XSIAM for matching alerts in the time window,
auto-validates results, computes MTTD against ground-truth
observation timestamps. Replaces the manual "did you see the
alert?" DC workflow.

### H3.3  Custom-content authoring SDK
**Anchor:** Customers asking "can we add our own scenarios?"
**Outcome:** Python SDK with helpers for scenario authoring,
validators, packaging. Customers extend the library with their own
threat-actor-anchored scenarios; CortexSim consumes them
identically to first-party content.

### H3.4  Detection-as-Code GitOps integration
**Anchor:** Customers managing XDR/XSIAM rules in Git.
**Outcome:** CortexSim emits "rule recommendations" tied to specific
scenarios: "Scenario X expected BIOC Y; here's the XQL rule." Pull
request flow against the customer's content repo. Closes the loop
from "we ran a simulation" to "we shipped a detection."

### H3.5  Industry partnership / open-source content track
**Anchor:** ATT&CK is community-driven. CortexSim's scenario library
should be too.
**Outcome:** Public `cortexsim-community-content` repo with
scenarios contributed by the broader security community, vetted by
PANW. Drives ecosystem adoption + makes CortexSim the default
language for security architects to share TTPs.

---

## Backlog (no horizon committed)

Smaller items that don't fit a horizon theme but have real value:

- **Run history timeline tab** — aggregate view of all runs over
  time with detection trend lines
- **Pinned scenario reorder** — drag-and-drop in the rail
- **Detection drilldown enhancements** — link to Cortex console
  search URL (deep-link with alert ID pre-filled)
- **Filter palette persistence** — save filter combinations as
  named presets ("Q3 POV", "Healthcare vertical")
- **Keyboard shortcut customization** — per-DC remapping
- **Light theme polish** — final cleanup of `?theme=legacy` if any
  DCs prefer it permanently (or hard-deprecate after H1.3)
- **i18n** — Japanese, German, French strings for global DC team
- **Mobile companion view** — read-only run status on phone for DCs
  watching a long-running scenario while in customer meetings
- **Detection latency histogram** — full distribution chart in
  Evidence (today only median surfaces)
- **EAL plugin registry view** — surface available attack vectors
  by category in a dedicated UI

---

## Where competitive pressure lives — and how to stay ahead

| Quarter | Risk | Counter |
|---|---|---|
| Q2 2026 | CrowdStrike Foundry ships LLM red-teaming content packs | Ship H2.1 (AIRS as first-class plane + 25-scenario library) before they GA |
| Q3 2026 | AttackIQ Flex 5.0 adds vendor-specific content for CrowdStrike | H1.4 (authoring wizard) + H3.5 (community content) — make CortexSim the cheapest place to author Cortex-aware content |
| Q4 2026 | Microsoft Sentinel adds native BAS scheduling | H2.2 (scheduled validation) — ship before they do |
| 2027 | Multiple BAS vendors converge on "agentic AI threat" coverage | H2.1 momentum + Cortex AIRS integration = clearest first-party offering |

## Operating principles

Three rules CortexSim development should observe to stay best-of-class:

1. **Every feature ships with a verifiable claim.** "We support
   stitching across the cloud-identity boundary" lands with a
   scenario that demonstrates it AND a CompetitiveView row showing
   who else does and doesn't. No bare assertions.
2. **Operator workflow over UI variety.** Every new view answers
   "what does the DC do here, and how does it close their POV
   loop?" before we ship pixels.
3. **Test methodology travels with features.** Tier A + B + C
   coverage is the floor. New features get unit + smoke tests
   before merge.

## Pointers

- `docs/design/console-redesign.md` — UI architecture + tokens
- `docs/design/e2e-execution-methodology.md` — test methodology
- `docs/operator-runbook.md` — DC workflow
- `docs/quick-start.md` — 10-min onboarding
- `CORTEXSIM_AGENT_CONTEXT.md` — full architecture spec
- In-app: ⌘/ help overlay · Coverage tab "Advantage" view
