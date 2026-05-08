# Detection Engine — Gap Analysis & Roadmap

> Brainstorm dated 2026-05-07.
> Audience: CortexSim maintainers + Domain Consultants running POVs.
> Goal: identify what's still missing **after Phases 1–5** to make CortexSim
> a complete enterprise-grade Cortex POV detection-validation platform.

## Where we are

Shipped to `main` (chronologically):

| Phase | Component | Coverage |
|---|---|---|
| 1 | 4 new detection planes + 20 declarative scenarios (`AI_ACCESS / AIRS / BROWSER / KOI`) | Schema, UC/TC, MITRE, OWASP-LLM10 mapping |
| 2 | `sources/cortex-vulnerable-llm/` | Flask canary, OWASP LLM01–10 endpoints |
| 3 | `sources/cortex-prompt-attacker/` + `airs_prompt_attack` plugin | Probe → mutator → target → scorer pipeline; promptmap-compatible YAML |
| 4 | `llm_provider_egress` EAL plugin | Authentic-shape egress to OpenAI / Gemini / Anthropic |
| 5 | `sources/cortex-malicious-agentic-pack/` + `agentic_egress` plugin | Six static artifact components + consumer-fingerprint fetch emulator |

**EAL Traffic Simulator** has 7 plugins (`c2_http_beacon`, `dns_tunnel_exfil`,
`bulk_https_exfil`, `stratum_tcp_connect`, `smb_rpc_sweep`,
`airs_prompt_attack`, `llm_provider_egress`, `agentic_egress`).

**Test surface:** 184 CortexSim core, 70 vulnerable-llm, 72 prompt-attacker,
26 agentic_egress = ~352 tests across the workspace.

## Critical gaps (block real POVs today)

These are the things a Domain Consultant **actually hits in week 1** of a
real customer engagement.

### G1. UI for the EAL simulator and the new planes

The whole Phase 4 / 5 deliverable is API-only. The existing React UI
(`ui/src/components/`) has views for `ScenarioBrowser`, `LaunchPanel`,
`ResultsViewer`, `MitreHeatmap`, `UCTCMapper`, `ToolStatusPanel`,
`InfraGenerator` — all built around the pre-EAL flow.

**Missing components:**

- `EalCampaignBuilder.jsx` — pick a plugin from the registry, fill the
  Pydantic params (the plugin's JSON schema is already exposed at
  `GET /api/eal/plugins/{name}`); save → run.
- `EalRunProgress.jsx` — live tail of `EalCampaignRun.step_results` +
  ECS audit events while a campaign runs.
- `AirsAttemptInspector.jsx` — render a JSONL stream of probe Attempts
  with detector-result chips, mutator chain, and pass/fail per OWASP
  class.
- `KoiArtifactBrowser.jsx` — tree view of `cortex-malicious-agentic-pack/`
  with a "Run as Cortex Cloud Code scan" button.
- `AiAccessProviderMatrix.jsx` — three-column view (OpenAI / Gemini /
  Anthropic) showing payload type, planted markers, last-run status.

**Why critical:** today a DC needs CLI access to the jumpbox to run a
campaign. The customer never sees the EAL simulator page. They see a
2024-vintage scenario browser missing half the planes we shipped.

### G2. Detection result feedback loop

The `Result` model has `executed_at` + `observed_at` for MTTD, and the
endpoint `PUT /api/results/{id}/validate` exists. But there is **no UI
flow that walks the DC through marking each expected detection as
observed in XSIAM**, and no client-side state for "which detections have
I confirmed in the customer console?"

This is the difference between a 30-minute POV check-in and a 3-hour
spreadsheet exercise.

**Build:**

- `ResultsValidationWizard.jsx` — given a `Run`, surface every expected
  detection grouped by step; each row has: copy-paste XSIAM XQL search
  string, "mark observed" button, free-text notes, screenshot
  attach. On save, flips `observed_at` and computes MTTD per row.
- Per-plane XQL templates baked in (e.g. AIRS gets a `dataset = airs
  | filter probe_classname = "ignore_previous_basic"` template).
- "Bulk-mark observed" for the common case of all-fired.

### G3. POV report generator

Today: `GET /api/runs/{id}/report?format=markdown` returns an unbranded
markdown blob. A real POV deliverable needs:

- **Cortex-branded PDF/HTML** with the customer logo on the cover and a
  proper executive summary.
- **MTTD heatmap** per plane (visual "where did detection fire fast,
  where did it lag?").
- **Coverage matrix** mapping the customer's signed UC/TC list against
  scenario IDs and observed/missed columns.
- **Gap callouts** — every expected detection that was *not* observed,
  with the recommended Cortex Marketplace pack to remediate.

**Build:** WeasyPrint or Playwright-driven HTML→PDF, run-id → bundle of
files, `GET /api/runs/{id}/report.zip`.

### G4. Multi-tenant / multi-customer support

Today: one SimCore instance ↔ one customer engagement. Database is a
single SQLite file, no concept of "customer" or "engagement" in the
schema. POV teams running multiple customers in parallel need to
either spin up multiple containers (no shared scenario library) or
manually annotate every run with a customer name in free text.

**Build:**

- `Tenant` ORM model + foreign-keys on `Run` and `EalCampaignRun`.
- `Authorization: Bearer <tenant-token>` middleware — separate auth
  scope per customer.
- Scenario library shared across tenants; runs / results / EAL
  campaigns scoped per tenant.
- "Switch tenant" dropdown in the UI header.

## Strategic gaps (table-stakes for enterprise)

Things customers expect on day-1 of an enterprise security tool, even
if a single POV doesn't immediately need them.

### G5. Cloud App plane (Cortex Cloud App Security / CASB)

`scenarios/cloud_app/README.md` exists and the plane is in the schema,
but no scenarios are checked in. CASB POVs are common; this plane is
visible-but-empty in the UI.

**Build:** 5 scenarios mirroring the AI_ACCESS pattern, plus an
`oauth_grant_emulator` EAL plugin (DC consents to a fake "Helpful AI
Assistant" OAuth app, exercises the risky-grant detection).

### G6. Identity plane (ITDR scenarios + IdP integration)

The ITDR IaC module exists (AD lab + roastable accounts) but there are
no checked-in scenarios. Customers buying Cortex ITDR need scenarios
that exercise:

- Kerberoasting, AS-REP Roasting, DCSync (already partially via
  multi-plane SIM-MP-002)
- Okta / Entra ID anomaly detection — risky sign-in, MFA fatigue,
  impossible-travel
- Service-account credential reuse

**Build:** 5 ITDR scenarios + a thin `idp_signin_emulator` plugin that
hits a customer-supplied Okta / Entra dev tenant with synthetic logins.

### G7. SaaS posture (SSPM)

Coverage gap entirely — SaaS posture isn't a plane in the current schema.
Cortex Cloud Posture has SSPM features (Salesforce, M365, GitHub Org
posture) that POVs increasingly want to demonstrate.

**Build:** new plane `SSPM`; scenarios that exercise drift detection
against a customer-supplied tenant; static artifact pack with bad
M365/Salesforce config (mirrors the KOI pattern).

### G8. Custom-rule import + validation loop

Detection-as-code is how mature SOCs operate. Today, a customer can
hand the DC a custom BIOC YAML and the DC has to manually decide which
scenario exercises it.

**Build:**

- `POST /api/detection-rules` — accept a customer's BIOC / Correlation
  / Analytics YAML.
- Auto-suggest matching scenarios based on technique IDs in the rule.
- "Run validation pass" — execute the matched scenarios + report
  whether the rule fired on the expected events.

### G9. False-positive testing (benign-traffic baseline)

Every scenario today validates "did detection fire on the attack?" but
none validate "does it fire **only** on the attack and not on benign
traffic?" A noisy detector is as bad as a missing one.

**Build:** `benign_baseline` plugin that runs concurrently with active
campaigns — generates steady-state legitimate traffic shaped like the
real customer baseline (DNS, HTTPS to allowlisted SaaS, pip installs of
canonical packages). Every scenario gains a "false-positive rate"
column in the report.

### G10. Multi-stage / branching adversary emulation

The `multi_plane/` scenarios chain steps linearly. Real adversary
emulation branches: "if persistence step succeeds, continue to lateral
movement; if it fails, fall back to recon".

**Build:** extend `Campaign` schema with conditional `next_step`
references. New `BranchExecutor` evaluates the prior step's outcome
against a YAML predicate before proceeding.

## Productization / packaging

### G11. Helm chart for SimCore itself

Today, only the EAL simulator has a chart (`deploy/helm/eal-simulator/`).
SimCore's own deployment is `docker compose up`. Customers running
on-prem K8s want a single chart for the whole platform.

**Build:** `deploy/helm/cortexsim/` — top-level umbrella chart that
sub-charts simcore-api, simcore-ui, eal-simulator, and the optional
agent registry.

### G12. Air-gapped install

Today, `install.sh` clones submodules, downloads from PyPI / npm /
crates.io / GHCR. Air-gapped customers (defence, financials) need a
pre-vetted offline bundle.

**Build:** `make airgap-bundle` produces a single `.tar.gz` with
vendored dependencies, signed image archives, and a Helm chart that
works behind a private registry.

### G13. SBOM + version pinning

Every in-tree tool (`cortex-vulnerable-llm`, `cortex-prompt-attacker`,
`cortex-malicious-agentic-pack`) has a `pyproject.toml` but the
top-level repo has no SPDX SBOM. Required for FedRAMP and many
enterprise procurement gates.

**Build:** `make sbom` → `sbom.spdx.json` + signature; CI fails on
new dependencies that aren't in the allowlist.

### G14. FIPS mode

For FedRAMP / DoD POVs, the EAL plugins' use of `httpx` (which uses
OpenSSL via Python's ssl module) needs to negotiate FIPS-validated
ciphers. Python 3.11 + OpenSSL 3.0 is OK but we don't gate / verify.

**Build:** `CORTEXSIM_FIPS_MODE=1` env var that disables non-FIPS
mutators (e.g. Unicode confusable, ROT13 — these aren't actually
crypto, but FIPS audits flag the names) and asserts ssl module FIPS
status at startup.

## Validation / quality

### G15. End-to-end integration test

Every test today is unit-scoped. A real CI run never spins up SimCore +
agent + canary together and runs through a scenario.

**Build:** `tests/integration/test_e2e_airs.py` — Docker-compose-up the
full stack inside CI, launch a campaign via the API, confirm Attempts
land in JSONL and audit pipeline.

### G16. Load-test harness

Can the EAL simulator drive 100 simultaneous campaigns? 1000 probes per
campaign? Nobody knows.

**Build:** `tests/load/airs_burst.py` using `locust` or `vegeta`;
publish baseline numbers in CI.

### G17. Tamper-evident audit chain

ECS audit lines are append-only but not chained. A compromised host
could rewrite history pre-export.

**Build:** every audit line carries `prev_hash` (sha256 of prior line);
periodic anchor commit to `EalCampaignRun.audit_anchor`.

## Documentation

### G18. POV runbook

We have docs/eal-simulator/runbook.md (specific to the EAL simulator).
We don't have a "your first day with CortexSim as a Cortex DC" doc.

**Build:** `docs/pov-runbook.md` — day-by-day playbook from "get the
container running on the customer jumpbox" to "hand the customer a
signed POV report".

### G19. Detection-engineering playbook

How to write a new BIOC that catches a SIM-NDR scenario. How to debug
why XSIAM didn't fire on an expected detection. How to interpret the
MTTD heatmap in a customer briefing.

**Build:** `docs/detection-engineering/`.

## Customer-facing distinctives (delight)

### G20. Threat-intel-feed integration (real, not mock)

We ship `mocktaxii` for offline POVs. Real customers want to see CortexSim
**ingest their actual TIM feed** (Mandiant Advantage, Recorded Future,
Anomali) and validate that downloaded IOCs trigger the expected
detection on a controlled test event.

**Build:** TAXII 2.1 client (Cortex TIM is the destination, not the
source) + scenario template that consumes a customer-supplied indicator
list.

### G21. ATT&CK Navigator export

Customers buying Cortex come from a MITRE-fluent culture. They want a
Navigator JSON layer showing their POV coverage.

**Build:** `GET /api/mitre/navigator-layer?run_id=...` — emits the
standard JSON schema with techniques colour-coded by observed/missed.

## What I'd recommend doing next

In rough priority order — pick the top 1–2 each phase:

| Phase | Items | Why |
|---|---|---|
| **6** *(already on roadmap)* | G–Phase6: `cortex-browser-attacker` (Playwright) | Ships the BROWSER plane, completes the AI/Browser/KOI quadrant |
| **7** | **G1 + G2** UI for EAL simulator + Validation Wizard | The single biggest customer-experience gap. Without this, Phases 4-5 are invisible to the DC's customer. |
| **8** | **G3 + G21** POV report generator + ATT&CK Navigator export | Turns CortexSim from a tool into a deliverable. |
| **9** | **G5 + G6** Cloud App plane + Identity plane | Largest single coverage gap (CASB / ITDR scenarios). |
| **10** | **G4 + G11** Multi-tenant + SimCore Helm chart | Lets POV teams scale to 5+ concurrent customers. |
| **11** | **G8 + G15** Custom-rule import + E2E integration test | Detection-as-code workflow + production confidence. |
| **12** | **G9 + G16** False-positive baseline + load harness | Quality maturation. |
| **13** | **G12 + G13 + G14** Air-gap, SBOM, FIPS | FedRAMP / enterprise procurement gate. |

## Out of scope (deliberately)

Not on the roadmap, not arguing for them:

- Real C2 framework (we're a detection-validation engine, not a red-team tool)
- Customer XSIAM API write access (read-only is the line we shouldn't cross)
- AI-generated attack chains (LLM-as-adversary is explicitly out per Phase 3 brief)
- Multi-cloud cost reporter (not our problem)

## Open questions for the team

1. **G2 vs G3 vs an XSIAM read-only connector?** A read-only alert pull
   (filtered by `time = run.started_at..run.completed_at`) auto-fills
   the validation wizard. The "no Cortex API connection" design rule
   was about *write* access — read-only is a different argument. Worth
   re-opening.
2. **Multi-tenancy auth model** — is a static bearer-per-tenant good
   enough for a POV tool, or do we need OIDC against the customer's
   IdP from day 1?
3. **Phase 6 (browser-attacker) vs Phase 7 (UI)** — the existing
   roadmap puts browser-attacker next, but Phase 7 (UI) unblocks every
   prior phase. Do we re-order?

---

## Appendix: gap-to-component traceability

| Gap | Touches |
|---|---|
| G1 | `ui/src/components/` (new), `core/api/eal.py` (existing) |
| G2 | `ui/src/components/ResultsViewer.jsx` (rewrite), `core/api/results.py` |
| G3 | new `core/engine/report_generator.py`, optional `weasyprint` dep |
| G4 | `core/models.py`, `core/api/*.py` middleware, UI header |
| G5 | new `scenarios/cloud_app/sim-cloud-001..005.yml`, new EAL plugin `oauth_grant_emulator` |
| G6 | new `scenarios/itdr/sim-itdr-001..005.yml`, new EAL plugin `idp_signin_emulator` |
| G7 | schema enum + new plane dir |
| G8 | new `core/api/detection_rules.py` |
| G9 | new EAL plugin `benign_baseline` |
| G10 | `core/eal_simulator/executor.py` extension |
| G11 | new `deploy/helm/cortexsim/` |
| G12 | `installer/` extension, signed offline bundle target |
| G13 | top-level `Makefile` + `cyclonedx-py` |
| G14 | env-var gate in `core/main.py`, audit line in EAL plugins |
| G15 | new `tests/integration/` |
| G16 | new `tests/load/` |
| G17 | `core/eal_simulator/audit.py` extension |
| G18 | new `docs/pov-runbook.md` |
| G19 | new `docs/detection-engineering/` |
| G20 | new `core/integrations/taxii_client.py` |
| G21 | new `core/api/mitre_navigator.py` |
