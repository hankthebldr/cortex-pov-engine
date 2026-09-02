# CortexSim — MVP / lab-readiness assessment (2026-09-01)

> A full-guts review across the three areas the program lead flagged — front-end
> design fidelity, the agent + NGFW lab execution path, and the Cortex detection
> substrate — plus the concrete hardening landed in this pass and a prioritized
> path to a demo-ready POV simulation engine. Every count here was re-verified on
> this branch on 2026-09-01; where a number is quoted it came from a command, not
> prose. **tenant-verified is still 0** — every green below is from an injected
> transport or a local target, never a live Cortex tenant.

---

## 0. Headline

CortexSim is **further along than the "no robust testing yet" framing suggests**,
and its defining honesty discipline (authored ≠ proven) is real and enforced. The
three worries resolve like this:

| Area | Short verdict |
|---|---|
| **Front end** | Banners, tooltips, live views, a11y **did land** and are strong. The real gap is that the shipped console is a *later* redesign that diverged from the handoff wireframes — two of those divergences are product **decisions**, not bugs. |
| **Agent + NGFW lab** | The pull lifecycle, durable queue, payload shelf, identity harness, and offline push path all **work** (proven once on a Docker target). Repeatability is gated by **target readiness** — the single biggest risk — and by a cloud path that stands up hosts but not runnable targets. |
| **Substrate** | Genuinely **moving toward real Cortex fidelity**. Six detection types map to authentic artifacts, XQL/correlation/ABIOC are real, causality is genuinely wired. The one credibility hole is **7 pure-narration "scenarios"** that declare detections nothing emits. |

**Status: GO for a supervised internal lab pilot on a provisioned target with a
curated scenario set. NOT yet GO for an unsupervised customer POV**, for the
reasons in §4.

---

## 1. Front end — did the redesign land?

**The worry ("banners, tooltips, wireframe features never made it back") is mostly
unfounded.** Verified present and, in several places, better than the handoff asked:

- **Honesty banners are rendered, not just API-side:** `tenant-verified: N`
  (`ReadinessView.jsx`), degraded-health (`ReadinessBanner.jsx`, mounted globally),
  SIMCORE UNREACHABLE, unstaged-adapter / `PAYLOAD_NOT_STAGED` / `PIN_MISMATCH`,
  and `IDENTITY NOT HONOURED` (in the launch preflight).
- **Tooltips / inline help:** ~110 `title=` tooltips, collapsed-rail hover labels,
  a `HelpOverlay` (⌘/) and an inline glossary `Term` component.
- **Live views (SSE):** `useRunEventStream` opens an `EventSource`, falls back to
  polling, cleans up on unmount, and renders distinct LIVE/POLL/ERR states — no
  leaked connections, `aria-live` regions on the async surfaces.
- **Accessibility:** native `<button>` throughout the console, skip link,
  `aria-current` nav, `role=tab*` on run sub-tabs, keyboard-operable causality nodes.
- **UI test suite: 78 files / 834 tests pass.**

**The real finding is different:** the shipped `ui/src` is a **v2 redesign that
superseded the handoff**. The handoff (`docs/design/handoff/`, 8 wireframes) is a
*dark-default, numbered-stepper* console in green `#6CC24A`; what ships is a
*light-default, grouped-sidebar* console in a different green, documented as
intentional in `docs/design/console-redesign-repair.md`. So a side-by-side against
the wireframes will diverge — because the wireframes are stale, not because the
work was lost.

Genuine issues, ranked:

| # | Finding | Severity | Note |
|---|---|---|---|
| A5 | **Storyline + Causality proof surfaces are not theme-aware** — `DetectionStoryline.css` has raw light-only hex; `CausalityGraph.css` themes off the OS `prefers-color-scheme`, not the app `[data-theme]` toggle. On a projector in dark/theater mode these two **demo-centerpiece** proof tabs misrender. | High | Correctness bug, independent of the theme-default decision. |
| A1 | Console **defaults to light**; every wireframe is dark and the spec calls dark "the product identity". | Decision | Deliberate in the repair pass — see §5. |
| B1/B2 | The handoff's LOCKED numbered-stepper IA was replaced by a grouped sidebar; `ConsoleStepper.jsx` is dead code; Targets/Launch are demoted out of primary nav. | Decision | Code and handoff disagree on the product's spine — see §5. |
| A4 | Contrast guard covers only 5 of 13 destination stylesheets; the core POV path (Library→Live/Evidence/Storyline/Causality→Coverage) is the unguarded set. | Should-fix | |
| A7 | Fonts are **CDN-only** (`fonts.googleapis.com`) with no self-hosting — a default-deny customer lab silently falls back to system fonts, breaking the type system. | Should-fix | The product is *built for* default-deny; self-host into `core/static/`. |
| C1 | CausalityGraph conveys detection outcome by **color alone** (no state text/aria on nodes); a color-blind or SR user can't tell fired from missed. | Should-fix | Sibling `DetectionStoryline` does this correctly (text pills). |
| — | Stale copy: empty-states said "launch from the Operations tab" (fixed this pass); `HelpOverlay` still narrates the ①②③ stepper. | Fixed / decision | HelpOverlay copy tracks the IA decision. |

---

## 2. Agent + NGFW lab execution path

**What works, proven end-to-end** (one real pull run: `SIM-EDR-001` against a
provisioned Docker target, `deploy/tier-d/`):

- **Enrollment** — token mint → one-line installer that needs no Go toolchain and
  no target egress (downloads a sha256-pinned beacon from SimCore), supervised
  systemd/launchd install with an honest `DEGRADED_NO_SUPERVISOR` fallback, and a
  liveness check that waits for `last_seen` to *advance* (closes "printed success,
  never polled").
- **Pull lifecycle + durable queue** — `queued_tasks` is a real write-through
  mirror, rehydrated on startup; a restart re-loads undelivered tasks and fails
  orphaned runs. SSE (scoped + global), abort, heartbeat sweep all wired.
- **Payload shelf** — `compose()` refuses at launch with `PAYLOAD_NOT_STAGED` /
  `PAYLOAD_PIN_MISMATCH`; the beacon stages all-or-nothing and sha256-verifies
  against the digest carried *in the task*, refuses redirects and symlink/overwrite.
  Proven: a tampered shelf file was refused before it reached the target.
- **Identity harness** — one spec drives push and pull identically; Windows honesty
  (`IDENTITY NOT HONOURED`) is real.
- **Push / offline** — bash and PowerShell bundles are genuinely SimCore-independent
  at runtime (verified: zero reachback); `409 BUNDLE_TARGET_UNSATISFIABLE` on
  unsatisfiable targets; C2 frameworks are never auto-staged.
- **EAL / NGFW signal** — network-shape plugins emit real egress; delivery
  accounting counts only 2xx (a captive-portal 200-HTML is *not* a delivery); an
  offline bundle pre-renders records to run behind the customer NGFW.

**The #1 risk to a repeatable demo: TARGET READINESS.** Three mechanisms all
converge on "a step that looks like it ran and didn't," which reads in a POV as
"Cortex missed it":

1. The identity harness dies in ~7 ms on an un-provisioned `www-data` (stock Ubuntu
   ships it `nologin`). **Only the Tier-D *Docker* image provisions the identities +
   interpreters the corpus needs** — the cloud IaC (`infra/modules/aws/edr/main.tf`)
   launches **bare** target instances with no `user_data`.
2. Tier-4 tools install from the public internet **on the target**, which default-deny
   blocks — only 8 of 56 tier-4 tools are shelf-staged.
3. A meaningful slice of corpus steps produce no real telemetry (see §3).

Prioritized punch list (agent/NGFW), deduplicated across the pull, push, and topology
reviews:

| # | Item | Why it matters | Where |
|---|---|---|---|
| L1 | **Provision cloud/BYOS targets like Tier-D does** — port the identity + home + shell + interpreter provisioning into `infra/modules/aws/{edr,cdr}` `user_data` and an on-prem Ansible role. | Closes the #1 risk for every non-Docker target. | `deploy/tier-d/Dockerfile.target` → `infra/modules/aws/edr/main.tf:71` |
| L2 | **Ship a pre-launch target-readiness gate** — extend the existing `/preflight` to verify each required identity has a login shell + home, surfaced as a hard warning *before* the run. | Catches "the host was wrong" before it looks like a Cortex miss. | `core/api/agents.py` `/preflight`, `runtime_preflight.py` |
| L3 | **Run-timeout watchdog** — a background sweep that fails a `running` run whose agent went offline mid-run, without waiting for a SimCore restart; surface it in `probe_task_queue`. | Today a beacon that dies mid-run leaves a run spinning forever; the health probe misses the delivered-then-died case. | `core/main.py` sweep, `core/api/health.py:504` |
| L4 | **Auto-enroll a beacon from cloud IaC** — target `user_data` fetches a token (SSM param) and runs the install one-liner, so `terraform apply` yields online, provisioned beacons. | Makes the cloud path as repeatable as `run-tier-d.sh` is for Docker. | `infra/modules/aws/base/userdata.sh.tftpl` |
| L5 | **Give the bash push bundle an offline tool path** — inline the digest-pinned shelf artifact bytes (base64) so a DC with no SimCore reachback still gets the tool; today the shelf is wired only for pull/k8s. | Push is the *most* likely default-deny path and has no offline tool delivery. | `core/engine/push_generator.py` `generate_bash` |
| L6 | **Persist install telemetry + de-collide agent ids** — move `/api/agents/install/attempts` off the in-memory 100-entry deque; stop stamping `online` at token redemption. | A restart erases onboarding forensics; a phantom "online" agent can be selected. | `core/api/agents.py:262,1310` |

**NGFW positioning (document + assert).** The EAL CampaignExecutor runs in
**SimCore's own process** — there is no EAL dispatch to an enrolled beacon. So an
NGFW only *sees* the c2/DNS/network signal when **SimCore's egress traverses that
NGFW**, or when the **offline bundle** runs on a host behind it. The offline bundle
is also *ineligible* for the C2-beacon / DNS-tunnel / browser plugins (they're in
`skipped_steps`). A DC who parks SimCore outside the NGFW path will generate zero
firewall signal while every campaign reads "delivered". This belongs in front of the
DC before a network-plane demo.

---

## 3. Cortex substrate — is it real?

**Verdict: genuinely moving toward real Cortex fidelity; for the ~95% of the corpus
that executes, it is credible.** Not a facade at the type/card level:

- **detection_type integrity** — zero invalid `detection_type` usages; the XDM
  modeling substrate is correctly kept out of `detection_types[]`. Cards carry real
  XQL-shaped logic with `comp count_distinct(...)`, `causality_actor_process_*`,
  container grouping — paste-able into a tenant.
- **XQL** — of 568 catalog queries, 100% carry `| filter`, 74% aggregate, **0 are
  trivial**.
- **Correlation** — 170 of 188 express a genuine multi-signal join; ~14 single-BIOC
  wrappers are mislabeled as correlation (should-fix).
- **ABIOC / causality** — genuinely wired, **not** the old synthetic star: 157/177
  scenarios declare `cgo_anchor`, 175/177 declare per-step `causality`, and all 66
  ABIOC scenarios carry causality; the graph builder resolves a real connected
  parent→child spine.

**The one credibility hole a skeptical DC would seize on: 7 pure-narration
scenarios** whose every step is `echo '[SAFE-MODE ...]'` yet declare detections that
cannot fire. The classifier added this pass (§4) flags **6 of them RED**
(`SIM-EDR-019`, `SIM-TIM-005`, `SIM-ASM-005`, `SIM-ASM-006`, `SIM-MP-020`,
`SIM-ITDR-016`); the 7th (`SIM-APB-001`) actually writes structured JSON files, so
it is defensible. `SIM-EDR-019` is the trap: it is marketed as a causality-strong
flagship but is 7-of-7 echo steps seeding 8 un-fireable detection Results — in a
live POV that is an all-missed report that makes Cortex look broken.

Substrate follow-ups (ranked): (1) convert or clearly quarantine the RED scenarios
(drive real telemetry via `signalbench`/telemetry-replay, or mark them tabletop and
stop seeding un-fireable Results); (2) add a "produces-signal" column to
`coverage_report.py` so authored breadth is visibly distinct from signal-backed
coverage; (3) reclassify the ~14 single-BIOC "correlations"; (4) backfill
`cgo_anchor` on the 6 cloud-audit ABIOC scenarios; (5) deepen the 5-rule XDM
modeling footprint for the log sources the analytics-streamer EAL family already
emits.

---

## 4. What this pass hardened

Five verified commits on `claude/lab-detection-mvp-readiness-xwla46`:

| Commit | What | Evidence |
|---|---|---|
| `fix(detection)` | Registered `panw_dlp_raw` (real PANW Enterprise DLP source) in `KNOWN_DATASETS`. | validator 356 pass / **0 warn** / 0 fail (was 1 warn); exports unchanged. |
| `feat(lab-ready)` | **Lab-readiness classifier + manifest** — tiers all 177 scenarios GREEN/YELLOW/RED by whether they emit signal in a lab; quote/comment/probe-aware so it catches narration a naive grep misses. `make lab-ready`, determinism gate, 10 guard tests. | GREEN 145 · YELLOW 26 · RED 6; 10/10 tests pass. |
| `fix(push)` | Tier-4 install and per-step non-zero exit **no longer abort** the bash bundle (guarded install + `run_as … \|\| true`); root/non-fatal contract documented in the header. | `bash -n` OK; blocked install + failing step both WARN and the bundle completes (rc=0). 115 push tests pass; golden regenerated (bash-only, PowerShell byte-identical). |
| `fix(ui)` | Evidence/In-flight empty-states point at the **Library**, not a nonexistent "Operations tab". | 834 UI tests pass. |

**The lab-readiness manifest (`docs/reference/lab-readiness.md`) is the most
directly useful artifact for the demo** — it is the "which scenarios can I safely
run in front of a customer" surface the product was missing, and it is the antidote
to the #1 risk: a DC starts from the 145 GREEN, treats the 26 YELLOW as
"provision-the-tool-first", and never demos a RED as a detection.

---

## 5. Decisions for the program lead (not mine to make)

1. **Console theme default.** Ship dark (matches every wireframe and the "dark is
   the product identity" spec) or keep the deliberate light-default-with-toggle? A
   one-line change in `AppShell.jsx`; the wireframes are stale either way. **Recommend
   dark for the demo** so the console looks like the product the wireframes sell.
2. **Navigation IA.** Restore the LOCKED numbered stepper, or formally retire it and
   update `docs/design/` so the wireframes stop being "the authority"? Right now code
   and handoff disagree on the product's spine, and `ConsoleStepper.jsx` is dead code.
3. **The 6 RED scenarios.** Convert them to real signal, or mark them `status: draft`
   / tabletop and stop seeding un-fireable Results? Until then, keep them out of any
   customer-facing run (the manifest flags them).
4. **Publish trigger.** Tag / image / release remain deliberately unpushed
   (`docs/MVP.md §4`); `release.yml`'s `lint-shell` still fails, so use the manual
   route.

---

## 6. Mapping to the demo workflows

| Workflow the lead must run | Ready? | Prep |
|---|---|---|
| **Deploy an agent in an on-prem / BYOS LAN** | ✅ (Docker) | `deploy/tier-d/run-tier-d.sh` is a repeatable one-command harness with an ENGINE/ENVIRONMENT/TTP classifier. For a real BYOS VM, provision the target like `Dockerfile.target` (L1). |
| **Deploy in a cloud lab** | ⚠️ partial | AWS IaC stands up hosts but not runnable targets (L1) and doesn't auto-enroll a beacon (L4). Do these before a cloud demo. GCP/Azure are design-only. |
| **Review the library / pick scenarios** | ✅ improved | New `docs/reference/lab-readiness.md` + `#/uctc` console. Start from the 145 GREEN. |
| **Add / iterate off the base library** | ✅ | Authoring is well-gated: `make validate`, `make check-refs`, `lint-scenario.py`, the assertions substrate. New content is caught at boot / CI. |
| **Accelerate DC tech-validation blueprints** | ✅ | UC/TC index (`/api/uctc`, `/api/pov`) scopes the corpus to an entitlement set and emits the upsell list with real PAN-* part numbers. |
| **Prove it in an XSIAM tenant** | ❌ not yet | `tenant-verified` is 0. The first real gate is `POST /api/connectors/{kind}/preflight` against a live tenant, then a reconcile/verify pass. Until then the lab proves *signal generation*, never *detection*. |

**Bottom line to get from here to a hardened POV engine:** the substrate and the
execution engine are sound; the gap is **operational repeatability on real targets**
(L1, L2, L4) and **honesty-at-the-point-of-selection** (the lab-readiness manifest,
now shipped, plus quarantining the 6 RED scenarios). Close L1–L2 and run one real
XSIAM-tenant preflight, and this is a POV engine a DC can drive unsupervised.
