# Resolution Strategy — Phases 6 through 13

> Companion to [`2026-05-07-detection-engine-gaps.md`](./2026-05-07-detection-engine-gaps.md).
> The brainstorm names what is missing. This document names **how we
> close it** — sequencing, parallelisation, success criteria, and
> re-evaluation triggers.

## TL;DR

- **Re-order the existing roadmap.** Today's "Phase 6 = browser-attacker"
  is correct in scope but **wrong in priority**. The single biggest
  customer-impact lever right now is **G1 (UI for the EAL simulator)**.
  Without it, every other Phase 4–5 deliverable is API-only and the
  customer literally cannot see them. Recommend shipping **Phase 7 (UI)
  before Phase 6** *or* in parallel.
- **Group gaps into four workstreams** that can run concurrently:
  DC Experience, Plane Coverage, Platform, and Quality. The roadmap
  becomes a 2D matrix of (phase × workstream), not a linear chain.
- **One phase = one ship-able PR** of 1–2 weeks of work, never a
  6-month epic. Every phase ends with a measurable success criterion
  and a 30-minute customer-facing demo.

---

## Strategic priorities (the lens through which we sequence)

1. **Customer-experience-first.** Every phase produces something a
   Domain Consultant can walk a customer through within 30 minutes of
   the merge.
2. **Visibility before coverage.** Closing G1 (UI) before adding new
   planes turns invisible API surface into a saleable POV experience.
3. **Quality is a leg, not a phase.** E2E tests, FP baseline, load
   harness, and audit-chain hardening live in the Quality workstream
   and run **alongside** feature phases, not after them.
4. **Right-size phases.** 1–2 weeks per phase. If a gap looks bigger,
   split it. The 21 gaps in the brainstorm are deliberately bite-sized.
5. **Compliance is a separate leg, last.** FedRAMP / SBOM / FIPS / air-
   gap land in Phase 13 (Platform workstream's final phase) so the
   product is mature before we lock it down.

---

## Workstream model

| Workstream | What it ships | Owner archetype |
|---|---|---|
| **A — DC Experience** | UI, validation wizard, POV report, runbook polish, ATT&CK Navigator export | Frontend + Cortex DC |
| **B — Plane Coverage** | New planes (BROWSER, CLOUD_APP, SSPM, ITDR-scenarios) and the runners they need | Backend + detection engineer |
| **C — Platform** | Multi-tenant, Helm chart for SimCore, air-gapped install, SBOM, FIPS | Platform engineer |
| **D — Quality** | E2E integration tests, load harness, FP-baseline plugin, tamper-evident audit chain, branching campaigns | SRE / QA |

Phases below name the workstream(s) each maps to so two phases in
different workstreams can run concurrently.

---

## Phased plan

Every phase lists: **gaps closed** · **estimated effort (T-shirt)** ·
**workstream** · **dependencies** · **deliverables** · **success
criteria** · **risks** · **demo**.

### Phase 6 — `cortex-browser-attacker` (BROWSER plane)

- **Gaps:** Phase 6 (browser plane runner — already named on the existing roadmap)
- **Effort:** M (~1 week)
- **Workstream:** B — Plane Coverage
- **Depends on:** nothing
- **Deliverables:**
  - `sources/cortex-browser-attacker/` (Python + Playwright; Node not used)
  - `browser_attack_runner` EAL plugin (subprocess shell-out pattern, same as `airs_prompt_attack`)
  - Flips `scenarios/browser/sim-browser-001..005` from `draft` to `active`
- **Success criteria:**
  - 5 BROWSER scenarios load + pass dry-run in CI
  - Manual: drive Prisma Browser to credential-paste site, confirm PB telemetry → XSIAM
- **Risks:** Playwright is heavy (~300 MiB browser binaries). Mitigation: ship as a separate Docker image, only pulled when BROWSER scenarios run.
- **Demo:** Headless Chromium pastes a synthetic credential into a phishing form; Prisma Browser → XSIAM alert appears within 30s.

### Phase 7 — UI for EAL Simulator + Validation Wizard ⭐ *priority*

- **Gaps:** G1 + G2
- **Effort:** L (~2 weeks; the largest UI delta since Phase 1)
- **Workstream:** A — DC Experience
- **Depends on:** nothing (consumes `/api/eal/*` and `/api/results/*` that already exist)
- **Deliverables:**
  - `ui/src/components/EalCampaignBuilder.jsx` — plugin picker + Pydantic-form rendering
  - `ui/src/components/EalRunProgress.jsx` — live tail of step results + ECS audit
  - `ui/src/components/AirsAttemptInspector.jsx` — JSONL probe-attempt viewer
  - `ui/src/components/KoiArtifactBrowser.jsx` — artifact tree + "scan with Cortex Cloud Code" link
  - `ui/src/components/AiAccessProviderMatrix.jsx` — three-column provider view
  - `ui/src/components/ResultsValidationWizard.jsx` — guided XSIAM-mark-observed flow with XQL templates per plane
- **Success criteria:**
  - DC can run a complete AIRS POV from the UI without touching the CLI
  - Every Phase 4–5 deliverable becomes visible in the UI
  - `npm run build` size budget held (<2 MiB initial bundle)
- **Risks:**
  - Form rendering from Pydantic JSON schema — react-jsonschema-form is GPL-friendly but ugly; building custom is 2-3 days more work. **Lean: build custom; matches Cortex theme.**
  - Live tailing of ECS events needs a WebSocket or SSE endpoint — the API today is polling-only. Decision: ship Phase 7 with 2-second polling; SSE in Phase 12.
- **Demo:** DC clicks "New campaign" → fills params via a generated form → "Launch" → watches progress in real time → wizard pops up post-run, walks them through marking each expected detection observed.

### Phase 8 — POV Report Generator + ATT&CK Navigator export

- **Gaps:** G3 + G21
- **Effort:** M (~1 week)
- **Workstream:** A — DC Experience
- **Depends on:** Phase 7 (the report screenshots come from the new UI)
- **Deliverables:**
  - `core/engine/report_generator.py` (WeasyPrint or Playwright HTML→PDF)
  - Cortex-branded HTML template with cover page, exec summary, MTTD heatmap, coverage matrix, gap callouts
  - `GET /api/runs/{id}/report.pdf` and `report.zip` (bundle with screenshots + raw JSONL)
  - `GET /api/mitre/navigator-layer?run_id=...` — standard ATT&CK Navigator JSON
- **Success criteria:**
  - DC can hand the customer a branded PDF within 5 minutes of run completion
  - The Navigator JSON imports cleanly into `https://mitre-attack.github.io/attack-navigator/`
- **Risks:** WeasyPrint pulls in Cairo / Pango — large dependency. Mitigation: Playwright is already there for Phase 6; reuse it for HTML→PDF.
- **Demo:** Click "Export report" → PDF downloads. Open Navigator → "Import layer" → see the run's coverage in colour.

### Phase 9 — Plane Coverage (Cloud App + Identity, parallel tracks)

- **Gaps:** G5 + G6
- **Effort:** M each (~1 week per track, run concurrently)
- **Workstream:** B — Plane Coverage (two tracks: B-9a + B-9b)
- **Depends on:** nothing
- **Deliverables (B-9a, Cloud App):**
  - 5 scenarios `scenarios/cloud_app/sim-cloud-001..005.yml`
  - `oauth_grant_emulator` EAL plugin (consents to a fake "Helpful AI Assistant" OAuth app against a customer-supplied IdP)
  - Plane flips from "planned" to "active"
- **Deliverables (B-9b, Identity):**
  - 5 scenarios `scenarios/itdr/sim-itdr-001..005.yml` (today the IaC module exists but no scenarios)
  - `idp_signin_emulator` EAL plugin (synthetic logins to customer Okta / Entra dev tenant)
- **Success criteria:** Each plane has 5 active scenarios + 1 EAL plugin + UI integration + at least 1 end-to-end POV demo.
- **Risks:** Customer-supplied OAuth tenant requirement may block POVs without a dev IdP. Mitigation: ship a `mocktaxii`-style **mock IdP** in `sources/cortex-mock-idp/` for offline POVs.
- **Demo:** Two demos — risky-OAuth grant fires CASB; synthetic risky sign-in fires ITDR.

### Phase 10 — Multi-tenant + SimCore Helm chart

- **Gaps:** G4 + G11
- **Effort:** L (~2 weeks; touches the entire API surface)
- **Workstream:** C — Platform
- **Depends on:** Phase 7 (the multi-tenant switcher lives in the UI header)
- **Deliverables:**
  - `Tenant` ORM model + foreign keys on `Run`, `EalCampaignRun`, etc.
  - `Authorization: Bearer <tenant-token>` middleware in `core/main.py`
  - `core/api/tenants.py` (CRUD + token rotation)
  - "Switch tenant" dropdown in the UI header
  - `deploy/helm/cortexsim/` — umbrella chart for simcore-api / simcore-ui / eal-simulator
- **Success criteria:**
  - Single SimCore instance serves 5 simultaneous customer tenants without scenario / run / result leakage
  - `helm install cortexsim deploy/helm/cortexsim/` brings up the whole stack on a fresh K3s
- **Risks:**
  - Schema migration without a migrations system is awkward — we declared "no migrations" earlier because SimCore is a single-binary POV tool, but multi-tenant breaks that assumption. **Decision: introduce alembic for this single change**, then re-evaluate.
  - Auth model — static bearer-per-tenant vs. OIDC. **Recommend bearer for v1**, OIDC in a later phase.
- **Demo:** Two browser tabs, two tenant tokens, two distinct scenario libraries, two run histories — proven non-overlapping.

### Phase 11 — Custom-rule import + E2E integration test

- **Gaps:** G8 + G15
- **Effort:** M (~1 week)
- **Workstream:** A + D (DC Experience + Quality, concurrent)
- **Depends on:** Phase 7 (rule-import lives in the UI), Phase 10 (E2E tests need multi-tenant fixtures)
- **Deliverables:**
  - `POST /api/detection-rules` — accept customer's BIOC / Correlation / Analytics YAML
  - Auto-suggest matching scenarios based on technique IDs in the rule
  - `tests/integration/test_e2e_airs.py` — Docker-compose up the full stack inside CI, run a full campaign, assert Attempts land in JSONL
  - `tests/integration/test_e2e_koi.py` — same shape, KOI plane
- **Success criteria:**
  - DC drops a customer BIOC → CortexSim recommends 3 scenarios within 5 seconds
  - E2E tests run in CI within 5 minutes; catch any plugin-level regression
- **Risks:** Container-in-container CI is finicky; some CI providers (GitHub Actions default) require `service` containers, not docker-compose. Mitigation: testcontainers-python.
- **Demo:** DC pastes a BIOC YAML → UI shows "3 matching scenarios" → one-click "validate" runs them.

### Phase 12 — False-positive baseline + load harness

- **Gaps:** G9 + G16
- **Effort:** M (~1 week)
- **Workstream:** D — Quality
- **Depends on:** Phase 7 (FP results are reported in the UI/report)
- **Deliverables:**
  - `benign_baseline` EAL plugin (generates steady-state legitimate traffic shaped like the real customer baseline)
  - Every scenario gains a "false-positive rate" column in the report
  - `tests/load/airs_burst.py` (locust) — publishes baseline numbers in CI
  - SSE / WebSocket endpoint for the UI's live-tail (deferred from Phase 7)
- **Success criteria:**
  - Active campaigns ship with co-running benign-baseline; FP rate column appears in the report
  - Load test: 100 simultaneous campaigns drive ~1000 events/sec without queue starvation
- **Risks:** Locust + asyncio is fiddly. Fallback: vegeta + a Python harness.
- **Demo:** Side-by-side report rows: attack scenario fires both an attack detection (good) and a false positive on benign traffic (bad). Customer sees both.

### Phase 13 — Air-gap + SBOM + FIPS

- **Gaps:** G12 + G13 + G14
- **Effort:** L (~2 weeks; the compliance phase)
- **Workstream:** C — Platform
- **Depends on:** Phase 10 (Helm chart needs to be air-gap-aware)
- **Deliverables:**
  - `make airgap-bundle` produces a signed `.tar.gz` with vendored deps + image archives + Helm chart for private registry
  - `make sbom` → `sbom.spdx.json` + cosign signature; CI fails on new deps that aren't allowlisted
  - `CORTEXSIM_FIPS_MODE=1` env var that gates non-FIPS algorithms and asserts ssl module FIPS status at startup
  - `docs/compliance/` with FedRAMP control mappings
- **Success criteria:**
  - Air-gapped install completes on a host with no public-internet access (verified via `ip rule` block)
  - SBOM passes `cyclonedx-cli validate` and is referenced in the release artifacts
  - FIPS mode passes `python -c "import ssl; assert ssl.OPENSSL_VERSION_INFO[3] >= 0"` and rejects banned algorithms
- **Risks:** FIPS validation is **only** valid when the underlying OS provides FIPS-validated OpenSSL. Mitigation: ship a FIPS-compliant Docker base image (Red Hat UBI 9 FIPS).
- **Demo:** Pull the air-gap bundle to a host with no internet, `helm install`, run a scenario.

---

## Out-of-band quick wins (file in between phases)

These are small enough to land between phases without disrupting the plan:

| Gap | Quick win | Effort | Phase to bundle with |
|---|---|---|---|
| G17 (tamper-evident audit) | Add `prev_hash` to every ECS audit line | S (~2 days) | Phase 12 |
| G18 (POV runbook) | Already stubbed in the wiki — flesh out with real customer war stories | S | Continuous |
| G19 (detection-engineering playbook) | New `docs/detection-engineering/` with 3-5 worked examples | M | Phase 11 |
| G20 (real TAXII feed) | TAXII 2.1 client in `core/integrations/` | M | Phase 9-companion |
| G10 (branching campaigns) | Extend campaign schema with conditional `next_step` | M | Phase 12 |
| G7 (SSPM plane) | New plane + 5 scenarios; mirrors Phase 9 pattern | M | Phase 9 extension |

---

## Re-evaluation cadence

After **every phase merge**, run a 30-minute review:

1. Were the success criteria met?
2. Did the customer feedback (from any active POV) re-prioritise anything?
3. Are any open questions from the brainstorm now answerable?
4. Are there new gaps (newer Cortex products, new threat vectors) we should add?

Update [`Roadmap`](../wiki/Roadmap.md) on the wiki with the outcome.

---

## Open questions (decisions needed before / during)

| # | Question | Default if no decision | Latest-to-decide |
|---|---|---|---|
| 1 | **Phase 6 vs Phase 7 ordering** | Ship Phase 6 first, Phase 7 second (existing order) | Before Phase 6 starts |
| 2 | **XSIAM read-only API connector** — re-open the "no Cortex API connection" design rule? | Stay read-only-via-DC-clicks; revisit after Phase 8 | Phase 8 design review |
| 3 | **Multi-tenant auth model** — static bearer or OIDC v1? | Static bearer; OIDC in Phase 13 | Phase 10 design review |
| 4 | **Branding refresh against local-ai-platform** | Still blocked on sandbox host-allowlist — needs operator unblock | Before Phase 8 (the report uses the branding) |
| 5 | **Mock IdP for Phase 9 Identity track** — build, or require customer-supplied dev tenant only? | Build mock IdP; customer-supplied is an option | Before Phase 9-b starts |
| 6 | **Migrations system** — alembic for the multi-tenant change, then keep it, or one-off? | Adopt alembic going forward (post Phase 10) | Phase 10 design review |
| 7 | **Compliance scope** — FedRAMP Moderate only, or DoD IL4/5? | FedRAMP Moderate v1; DoD if a deal needs it | Phase 13 kick-off |

---

## Resource model

This is what the work *needs* in skills/hours, not who specifically does it.

| Workstream | Required skills | Concurrent capacity |
|---|---|---|
| A — DC Experience | React / Vite, FastAPI, ECS audit logging, Cortex DC product knowledge | 1 FE + 1 BE + 1 DC reviewer |
| B — Plane Coverage | Python, asyncio, httpx, OWASP / MITRE fluency, NGFW / XDR detection-engineering | 2 detection engineers (one per plane track) |
| C — Platform | Helm, Docker, Linux distros, FIPS / SBOM tooling | 1 platform engineer |
| D — Quality | pytest, locust, GitHub Actions, observability | 1 SRE / QA engineer (shared with C) |

Min viable team: 1 FE + 1 BE + 1 detection engineer + 1 platform engineer = 4 people, ~13 weeks for Phases 6–13.
Realistic team: 6–8 people, ~8 weeks for Phases 6–13.

---

## Risk register (cross-phase)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Customer POV pulls a contributor away mid-phase | High | Medium | Phases are 1-2 weeks; absorb the loss |
| New Cortex product surface emerges and shifts priorities | Medium | High | Re-evaluation cadence catches it |
| Codex P1 finding on a merged PR | Medium | Low | Codex catches them now; the fix has been 1-3 lines historically |
| Test flakiness on E2E containers (Phase 11) | Medium | Medium | Use testcontainers-python; mark new tests `@pytest.mark.flaky(reruns=2)` while stabilising |
| Licensing creep (GPL contamination) | Low | High | Per-tool `THIRD_PARTY_NOTICES.md`; CI grep for `import promptmap` etc. |
| Multi-tenant data leak | Low | High | Phase 10 ships with explicit cross-tenant integration test |
| FIPS-validated OpenSSL availability | Low | Medium | Ship a FIPS-base Docker image; document RHEL / UBI as the supported FIPS host |

---

## What we explicitly are NOT doing

Re-stated from the brainstorm so this strategy is self-contained:

- No real C2 framework (we're a detection-validation engine).
- No XSIAM write API (read-only is the line).
- No LLM-as-adversary mutators in the prompt-attacker (single-turn only per the Phase 3 brief).
- No multi-cloud cost reporter / posture management beyond CSPM IaC.
- No customer support / multi-tenant SSO from day 1 — static bearer is good enough for v1.

---

## Definition of done — per phase

A phase is **done** when:

1. All listed deliverables have shipped to `main` via a draft PR turned ready, reviewed, and merged.
2. Every success criterion has been verified — manually noted in the PR description.
3. Tests are green (`pytest`, `lint-shell`, any new integration tests).
4. The [[Roadmap]] wiki page has been updated with the phase's PR number and date.
5. Codex P1 / P2 findings on the merged PRs are either addressed or explicitly accepted with rationale.

---

## Sequence-of-record

The recommended path through the phases, accepting all defaults from the open-questions table:

```
Now                                                                 ~13 weeks
 │
 ├─ Phase 6  (BROWSER plane)         ──── Workstream B  (1 week)
 ├─ Phase 7  (UI + Validation Wizard) ─── Workstream A  (2 weeks)   [⭐ priority]
 ├─ Phase 8  (POV report + Navigator)─── Workstream A  (1 week)
 ├─ Phase 9a (Cloud App plane)       ──── Workstream B  (1 week, // 9b)
 ├─ Phase 9b (Identity plane)        ──── Workstream B  (1 week, // 9a)
 ├─ Phase 10 (Multi-tenant + Helm)   ──── Workstream C  (2 weeks)
 ├─ Phase 11 (Custom-rule + E2E)     ──── Workstreams A+D  (1 week)
 ├─ Phase 12 (FP baseline + load)    ──── Workstream D  (1 week)
 └─ Phase 13 (Air-gap + SBOM + FIPS) ──── Workstream C  (2 weeks)
```

If the team has bandwidth to run two workstreams in parallel:

```
Phase 6  +  start Phase 7              week 1
Phase 7 cont.                          week 2-3
Phase 8                                week 4
Phase 9a + 9b in parallel              week 5
Phase 10                               week 6-7
Phase 11                               week 8
Phase 12                               week 9
Phase 13                               week 10-11
```

Total wall-clock: **~10-11 weeks with two parallel workstreams**;
~13 weeks fully serial.

---

## Companion docs

- [`2026-05-07-detection-engine-gaps.md`](./2026-05-07-detection-engine-gaps.md) — the underlying 21-gap inventory.
- [`docs/wiki/Roadmap.md`](../wiki/Roadmap.md) — published phase status that gets auto-synced to the GitHub wiki.
- [`docs/eal-simulator/architecture.md`](../eal-simulator/architecture.md) — design rules that constrain the strategy.
- [`CLAUDE.md`](../../CLAUDE.md) — project-level design rules.
