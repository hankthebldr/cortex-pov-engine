# Roadmap

> Last updated: Phase 9 in progress (Cloud App + ITDR planes).

## Shipped

| Phase | Component | PR | Date |
|---|---|---|---|
| 1 | Schema + 20 declarative scenarios across `AI_ACCESS / AIRS / BROWSER / KOI` | #10 | 2026-05-05 |
| 2 | `sources/cortex-vulnerable-llm/` — Flask canary, OWASP LLM01–10 | #15 | 2026-05-06 |
| 3 | `sources/cortex-prompt-attacker/` + `airs_prompt_attack` EAL plugin | #17 | 2026-05-07 |
| 4 | `llm_provider_egress` EAL plugin (replaces curl in AI_ACCESS scenarios) | #18 | 2026-05-07 |
| 5 | `sources/cortex-malicious-agentic-pack/` + `agentic_egress` EAL plugin | #19 | 2026-05-08 |

## Shipped (continued)

### Phase 9 — Cloud App (CASB) plane + Identity (ITDR) plane ✅

Two new EAL plugins and 10 new scenarios that close the largest single
coverage gap from the brainstorm — `G5` (Cloud App / CASB) and `G6`
(Identity / ITDR).

**Track A — Cloud App (CASB) — `oauth_grant_emulator`:**

- HTTP GET against the public OAuth 2.0 authorize endpoints of three
  IdPs (Okta, Microsoft Identity Platform, Google Identity)
- Four scope presets: `benign` (FP control), `risky_drive`,
  `admin_consent`, `full_mailbox`
- 5 scenarios `scenarios/cloud_app/sim-cloud-001..005.yml` covering
  every preset + cross-provider rotation + benign baseline
- MITRE: T1550.001, T1528, T1078.004, T1098

**Track B — Identity (ITDR) — `idp_signin_emulator`:**

- POSTs synthetic IdP audit-log events (Okta system-log shape, Microsoft
  Entra signInLogs shape, Google Workspace login activity shape) at an
  operator-supplied collector URL — never touches the real tenant
- Five behavioural patterns: `impossible_travel`, `mfa_fatigue`,
  `credential_stuffing`, `token_replay`, `brute_force_lockout`
- 5 scenarios `scenarios/itdr/sim-itdr-001..005.yml`
- MITRE: T1078.004, T1110.003, T1110.004, T1539, T1550.004, T1556.006,
  T1621

Both plugins follow the same design rules as `llm_provider_egress`:
shell-out-free, target-allowlist enforced via `SafetyPolicy`, per-iteration
`X-Simulation-Run-ID` header for SOC filtering, ECS-formatted audit events,
stdlib + httpx only.

**Test coverage**: 37 tests for `oauth_grant_emulator`, 32 tests for
`idp_signin_emulator`. Both plugins use a `_RecordingClient` httpx stub —
no real outbound traffic during CI.

### Phase 8 — POV report generator + ATT&CK Navigator export ✅

Three new endpoints under `/api/runs/{run_id}/report/*` emit the exact
artifact shape demonstrated by the worked example in
[[Detection Coverage Lab]]:

- `GET .../report/matrix` — `detection_matrix.csv` (one row per
  expected detection)
- `GET .../report/navigator` — ATT&CK Navigator v4.5 layer JSON
  (DETECTED red, missed/pending grey)
- `GET .../report/bundle` — all three artifacts (matrix + navigator +
  `pov_narrative/exec_summary.md`) in one `tar.gz`

Generator lives at `core/engine/report_generator.py`; sourced from the
existing `Run` / `Result` / `Scenario` rows — no schema changes. 26
unit tests cover matrix shape, navigator round-trip, exec-summary
verdict tiers, bundle layout, and edge cases (no scenario, no results,
missing technique).

### Phase 7 — UI for EAL Simulator + Validation Wizard ✅

Three React components that turn the EAL simulator from API-only into a
DC-driveable experience:

- **`EalConsole.jsx`** — orchestrator with Campaigns / + New / Runs tabs;
  campaign list with one-click dry-run + confirm-modal live launch;
  drill-in to run detail
- **`EalCampaignBuilder.jsx`** — plugin picker + dynamic form rendered
  from each plugin's Pydantic JSON schema (`/api/eal/plugins/{name}`);
  supports `string`, `integer`, `number`, `boolean`, `array<string>`,
  `enum`, and `object` (JSON textarea) types
- **`EalRunProgress.jsx`** — 2-second polling tail of `EalCampaignRun`,
  stops polling on terminal status, surfaces step results + run-level
  error
- **`ResultsValidationWizard.jsx`** — guided "mark each detection
  observed" flow with copy-paste XQL templates per plane (NDR / EDR /
  CDR / AIRS / AI_ACCESS / BROWSER / KOI / ANALYTICS), inline notes,
  bulk "mark all observed", filter (all / pending / observed), MTTD
  KPI summary

Two new toggle buttons in the header — **EAL** + **Validate** —
mutually exclusive with the existing MITRE / Deploy / Runs toggles.

UI bundle: 76 KiB gzipped JS, 8 KiB gzipped CSS.

### Phase 6 — `cortex-browser-attacker` (BROWSER plane) ✅

Playwright-driven runner that exercises the deployed Prisma Browser:

- credential paste into untrusted origin
- drive-by download from phishing site
- risky/sideloaded extension install
- copy-paste DLP from sanctioned SaaS to webmail
- screen capture of sensitive page

`scenarios/browser/sim-browser-001..005` flipped from `draft` to `active`.
Driven by the `browser_attack_runner` EAL plugin (shell-out-to-CLI pattern,
same as `airs_prompt_attack`). Playwright is an optional install extra so
unit tests use a `StubDriver` and never spin up a real browser.

## Pending

## Beyond Phase 6 — gap analysis + resolution strategy

Two companion docs in the repo:

- [`docs/brainstorm/2026-05-07-detection-engine-gaps.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/docs/brainstorm/2026-05-07-detection-engine-gaps.md) — 21-gap inventory
- [`docs/brainstorm/2026-05-08-resolution-strategy.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/docs/brainstorm/2026-05-08-resolution-strategy.md) — Phases 6–13 sequencing, workstream model, success criteria, risk register, decision points

The brainstorm names **what** is missing. The strategy names **how** we close it.

Highlights (in rough priority order):

| Phase | Item | Why |
|---|---|---|
| **7** | UI for EAL simulator + Validation Wizard (`G1` + `G2`) | Single biggest customer-experience gap. Without this, Phases 4-5 are invisible to the DC's customer. |
| **8** | POV report generator + ATT&CK Navigator export (`G3` + `G21`) | Turns CortexSim from a tool into a deliverable. |
| **9** | Cloud App plane + Identity plane (`G5` + `G6`) | Largest single coverage gap (CASB / ITDR scenarios). |
| **10** | Multi-tenant + SimCore Helm chart (`G4` + `G11`) | Lets POV teams scale to 5+ concurrent customers. |
| **11** | Custom-rule import + E2E integration test (`G8` + `G15`) | Detection-as-code workflow + production confidence. |
| **12** | False-positive baseline + load harness (`G9` + `G16`) | Quality maturation. |
| **13** | Air-gap + SBOM + FIPS (`G12` + `G13` + `G14`) | FedRAMP / enterprise procurement gate. |

See the brainstorm doc for the full 21-gap inventory and the
gap-to-component traceability table.

## Out of scope (explicitly)

- Real C2 framework — we're a detection-validation engine, not a
  red-team tool
- Customer XSIAM **write** access — read-only is the line we
  shouldn't cross
- AI-generated attack chains (LLM-as-adversary) — explicitly out per
  the Phase 3 design brief
- Multi-cloud cost reporter — not our problem

## Open questions

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
