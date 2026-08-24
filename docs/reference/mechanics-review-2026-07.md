# Mechanics Review — 2026-07 (Fable Pass)

> A deep review of CortexSim's load-bearing mechanics across five facets, recorded as a
> **severity-ranked backlog of complementary components + actions**. This is analysis,
> not a change log — nothing here was implemented in the 2026-07-24 Kali-toolkit pass.
> It is the prioritized "what to build next / what to trust less" list.

The five facets reviewed:

1. Execution lifecycle (pull/push, orchestrator, chained beacon)
2. Identity harness + causality fidelity vs real XDR
3. Detection corpus + tool-adapter framework
4. Safety, consent, and OPSEC model
5. Measurement loop + POV evidence (the wedge vs Caldera)

---

## Consolidated severity-ranked backlog

### HIGH

| # | Facet | Action | Effort |
|---|---|---|---|
| H1 | Lifecycle | **Leased delivery on `queued_tasks`** (`dispatched_at`/`leased_by`; delete the row only on `/complete`; rehydrate re-enqueues in-flight tasks instead of marking their runs `failed`). Fixes false-failed-on-restart, at-most-once delivery loss, and the stuck-`running` strand in one stroke. Requires idempotent `/complete` + duplicate-delivery tolerance in the beacon. | M–L |
| H2 | Lifecycle | **Random per-session chain sentinel** (crypto/rand nonce, not the agent pid) + drain/isolate trailing backgrounded output before the sentinel. Prevents content-driven capture desync across all subsequent steps of a chained run. | S–M |
| H3 | Identity/Causality | **Stop presenting `cgo_anchor.image_name` as the on-wire CGO** — argv[0] rewrite does not change `comm`/`/proc/pid/exe`, so XDM `causality_actor_process_image_name` stays `sh`. Either implement a real exec-as-service path for hero scenarios or relabel the graph node "modeled CGO (telemetry shows sh)". | M (real exec) / S (honest relabel) |
| H4 | Identity/Causality | **Feed real process-tree evidence** (ppid/comm/euid emitted by the beacon per step) into `build_causality_graph` so structural edges upgrade to CONFIRMED from execution, not only alert observation. Today a silently star-fallen-back run still renders a fully connected DAG. | M |
| H5 | Identity/Causality | **Beacon privilege/tool preflight** (root? runuser present? setuid allowed?) that annotates the run + graph when impersonation could not occur, so the graph never models an actor tier (e.g. `www-data`) the host actually ran as the beacon uid. | S |
| H6 | Corpus/Adapters | **CI gate on scenario `(ttp_ref, detection_id)` resolution** — promote `lint-scenario.py`'s `_card_detection_ids` resolution from a client-side hook to a server-side gate over the whole corpus. Closes the rename-a-detection silent-rot hole; makes "N/N slugs resolve" an enforced invariant, not a manual claim. | L–M |
| H7 | Corpus/Adapters | **Unify the plane vocabulary** into one shared constant imported by both `scenario_loader` and `adapter_loader`; add ASM/CSPM/TIM/AI_SPM/EMAIL to `adapter_loader._VALID_PLANES`; then correct exposure-mgmt/OSINT adapters currently forced to mislabel their signal (e.g. theHarvester → NDR/ITDR). | M |
| H8 | Safety | **API authentication + operator identity** on at least `/api/runs` launch, `/api/infra` + bundle download, and `/api/agents/enroll/tokens`; tighten CORS off `allow_origins=['*'] + allow_credentials=True`. Today anyone on the jumpbox network can generate and download offensive dual-use bundles unauthenticated. | M |
| H9 | Safety | **Enforce lab-only targeting** for dual-use/c2 scenarios: require a non-empty `target_allowlist` + `authorized_by` at launch (mirror `eal_simulator/safety.py`) and validate every host/IP in resolved step commands against it in `_check_adapter_consent` before creating the Run. | M |
| H10 | Safety | **Runtime consent/target guard + SAFE-MODE preamble** baked into generated push and k8s bundles so the artifact re-checks scope and requires explicit authorization — the generation-time consent check does not travel with the downloadable script. | M |
| H11 | Measurement | **Persist reconciled `ObservedAlert` evidence** (new `ResultEvidence` table) and thread `observations=` into the served storyline/causality endpoints so detected detections show the real alert id/name/technique instead of a hollow `observed=true` shell. Without this, "evidence-backed" is a checkbox with a timestamp. | M |
| H12 | Measurement | **Provenance flag on `observed_at`** (connector vs manual); stop blending human-click time and real alert-fire time into one MTTD distribution in the scorecard; label the two tracks in the report. | S–M |
| H13 | Measurement | **Fix the dead "missed" branch** — make `efficacy_scorecard._status` and `report_generator._status_from_result` run-terminal-aware via one shared `_status(result, run_status)` so completed runs report true false-negatives and the three faces stop diverging (the scorecard currently never shows false-negatives, understating the coverage gap). | S |

### MEDIUM

| # | Facet | Action | Effort |
|---|---|---|---|
| M1 | Lifecycle | Close the **launch/complete race**: transition the Run to `running` in the same commit as (or before) making the task visible, and guard the running-transition to apply only while `status=='pending'`. | S |
| M2 | Lifecycle | **Push↔causality/adapter/consent parity**: nest push-bash steps under one anchor process; redesign the k8s chain to a single pod tree; run `_resolve_adapter_placeholders` + `_check_adapter_consent` on the `/download` path. | M |
| M3 | Lifecycle | **Incremental `append_output`** (per-chunk `RunOutput` rows assembled on read) to remove the O(n²) `run.output` rewrite; add a **server-side per-run execution deadline → `timed_out`** watchdog. | M |
| M4 | Identity/Causality | Port the **push-mode `runuser→sudo→su` fallback** into `WrapCommand` (harness.go) to remove push/pull divergence on images without util-linux `runuser`. | S |
| M5 | Identity/Causality | Document/flag that the `runuser` wrapper is itself an unnatural parent for service-account TTPs; offer a `setpriv`/`capsh` "clean-descent" alternative so detection authors don't key on (or get fooled by) the `runuser` signature. | M |
| M6 | Identity/Causality | Add a **Windows/identity-plane impersonation tier** (`CreateProcessAsUser`/token duplication, or `runas`) so ITDR chains carry SID/logon-session actor context instead of running as the agent identity. | H |
| M7 | Corpus/Adapters | **Derived-slug uniqueness check** in `validate.py` (fail per-`ttp_ref` duplicates) so the name-derived join key can't silently alias two detections onto one slug. | L |
| M8 | Corpus/Adapters | Escalate **unknown-dataset and dangling `ttp_ref` from WARN to ERROR** under a `--strict` CI invocation, so dataset typos and deleted-card references fail closed. | L |
| M9 | Safety | **Immutable consent+launch audit entry** (who authorized, when, scenario, gated adapters, allowlist) on every gated launch; the scenario path currently takes unlogged, unattributed booleans. | L |
| M10 | Safety | **Reclassify adversary-emulation / AiTM tooling**: make Caldera `c2-framework` (or add an `adversary-emulation` class that also triggers non-staging + `c2_authorized`); re-gate evilginx2/gophish/frp so the push non-staging refusal covers them; add a test asserting c2/tunnel/phishing-category packs are non-stageable. | L |
| M11 | Measurement | Pull **causality/entity data** (parent process, actor, CID, network 5-tuple) from XSIAM and persist entity columns on `Result` so the graph's CONFIRMED/BROKEN evidence edges are reconciled against the tenant instead of proxied by `step_detected`; then add a predicted-vs-actual Causality View diff. | H |
| M12 | Measurement | **Harden the matcher**: 1:1 alert-to-result assignment ranked by key strength (detection_id > exact technique > technique-base > name) to stop one alert crediting many results; make the correlation window per-detection-type (3600s default is too tight for Analytics/ABIOC). | M |

### LOW

| # | Facet | Action | Effort |
|---|---|---|---|
| L1 | Lifecycle | Bound the `_aborted` set (drop on any terminal transition); either enforce single-worker at startup or document the hard single-process constraint alongside the k8s/compose config. | S |
| L2 | Lifecycle | Fan `agent.status` to run-scoped SSE subscribers (or fix the docstring); emit authoritative server-side `run.step` boundary events instead of inferring step index from numeric ids. | S |
| L3 | Identity/Causality | Remove or wire up the dead `sudo_u`/`su` modes so the spec's 4-mode model matches `ResolveIdentity`'s real 2-mode behavior. | S |
| L4 | Identity/Causality | For CDR/KOI, run the chained anchor inside the target container namespace (`nsenter`/`docker exec`) so `container_id`-keyed graph edges are backed by real telemetry. | M |
| L5 | Corpus/Adapters | Minor cleanups: dead `_SIGMA_SEVERITY['info']` key (schema enum is `informational`); inconsistent YAML quoting of `category`/`safety_class` across packs. | L |
| L6 | Safety | Close the placeholder gate bypass: have `_check_adapter_consent` also scan resolved step commands for `{adapter:...}` references (not just `external_tools`); add a lint rule flagging gated placeholder adapters missing the matching consent field. | L |
| L7 | Measurement | Deep-link each detected `Result` to its XSIAM alert/incident URL in the report bundle; give push/staged runs a reconcile path (or explicitly badge them "proof pending — no read-back"). | M |

---

## Per-facet notes

### 1. Execution lifecycle
Launch flows through `Orchestrator.launch()` → seed one `Result` per expected detection
→ branch pull/push. The **durable queue** closes the obvious restart hole, but the row is
**deleted at delivery**, so the system cannot distinguish "never delivered" from
"delivered and legitimately in-flight" — hence H1 (restart during a minutes-long Kali
kill-chain = spurious failure; at-most-once loss; a launch/complete race). The **chained
executor** is well-built (subshell-per-step, concurrent stream drain, process-group
abort) but the pid-based sentinel and open-for-the-whole-run pipes create content-driven
desync and cross-step output bleed (H2). The causality contract is **pull-only** — push
bash is a star under one `bash`, push k8s is N disconnected Jobs (M2).

### 2. Identity harness + causality fidelity
The real fork/exec tree is a genuine upgrade over `sh -c` siblings, and the graph honestly
delegates coverage/MTTD to `detection_storyline`. But the **CGO image is cosmetic**: argv[0]
rewrite does not change `comm`/`exe`, so XDM shows `sh` forked by `cortexsim-agent`, not
`nginx`/`apache2` (H3). The chain root is still the beacon; `runuser -l` is itself an
unnatural parent for service-account TTPs (M5). The graph is **spec-derived, never
execution-derived** — a silent per-step fallback still renders a connected DAG (H4). The
documented 4-mode harness is really 2 modes (L3).

### 3. Detection corpus + tool-adapter framework
Two strict, parallel corpora (JSON-Schema 2020-12 + 13 semantic checks for cards; Pydantic
for adapters) bridged by scenarios on a **name-derived slug** join key. The `550/550`-style
"all slugs resolve" invariant is **not enforced in CI** — it lives only in a client-side
hook (H6), so renaming a detection silently dangles every scenario pointing at the old slug.
The **adapter plane enum diverges** from the scenario plane enum (H7) — ASM/CSPM/TIM/AI_SPM/
EMAIL tools are forced to mislabel their signal. No derived-slug uniqueness check (M7);
unknown datasets and dangling `ttp_ref` are WARN-only (M8).

### 4. Safety, consent, OPSEC
Two safety models of unequal maturity, and the weaker one guards the more dangerous surface.
The **EAL SafetyPolicy** is strong (mandatory `authorized_by` + non-empty `target_allowlist`
+ per-call `authorise()`). The **scenario/adapter path** takes only self-asserted booleans:
no auth, no attribution, no target/scope check (H8, H9). The one genuinely solid guard is
push c2-framework non-staging — but it keys strictly on `c2-framework`, so Caldera/evilginx2/
frp sit outside it under `dual-use` (M10). Downloaded bundles carry no leash (H10).

> Note: the global CLAUDE.md `scope.txt` / `pre_tool_use.py` hooks are the operator's personal
> Claude Code editing controls, **not** runtime engine controls. The repo `.claude/hooks/` are
> lint/format only.

### 5. Measurement loop + POV evidence
A clean, offline-safe, exhaustively-unit-testable pipeline with a **known seeded denominator**
(the real advantage over ad-hoc red-team scoring) — but what it **persists** as evidence is a
boolean + a timestamp. The rich reconciled `ObservedAlert` (name/external_id/techniques) is
computed transiently and thrown away; the served storyline/causality endpoints never pass
`observations=`, so DC-facing views show `observed=true` with `alert_name=None` even after a
successful reconcile (H11). MTTD provenance is silently mixed (H12); the "missed" branch is
effectively dead so false-negatives never surface (H13). The **causality graph is ~95% an
offline model** — `Result` has no entity columns and the connector pulls no 5-tuple/CID/actor,
so BROKEN (the high-value "demonstrable stitch gap") is practically unreachable (M11).

---

## Research highlights — content pipeline (Unit 42 + tradecraft)

Actionable, source-anchored candidates for future corpus expansion. Each maps cleanly onto the
causality contract (single root → typed pivots) and stresses a Cortex analytic the corpus
under-covers today. Grouped by theme; none are built yet.

### Edge-appliance → internal kill-chains (the CGO/parent_step→pivot exemplars)
- **Ivanti Connect Secure CVE-2025-0282** — appliance RCE → `ldap.pl` cred harvest → SPAWN
  persistence + log wipe → RDP lateral → MSBuild in-memory compile → memory dump (VM.txt XOR
  0x27). A textbook edge→endpoint `endpoint_network_stitch`/`process_lineage` chain the corpus
  lacks. (unit42.paloaltonetworks.com/threat-brief-ivanti-cve-2025-0282-cve-2025-0283/)
- **PAN-OS Captive Portal CVE-2026-0300** — nginx shellcode → **firewall service-account AD
  enumeration** (trusted-appliance-identity-turns-hostile `shared_entity` pivot) → EarthWorm/
  ReverseSocks5 multi-hop SOCKS5. New adapters TOOL-EARTHWORM / TOOL-REVERSESOCKS5.
  (unit42.paloaltonetworks.com/captive-portal-zero-day/)
- **Cisco ASA 5500-X CVE-2025-20333/-20362/-20363** — WebVPN RCE → RayInitiator GRUB bootkit →
  LINE VIPER covert C2 (C2 inside WebVPN HTTPS auth sessions; ICMP-request→raw-TCP-response
  asymmetric channel). Novel NDR signal shapes; below-OS firmware-tamper analytics.
  (unit42.paloaltonetworks.com/zero-day-vulnerabilities-affect-cisco-software/)

### Identity-first, malware-free, hypervisor-blind (the storyline/tempo thesis)
- **Muddled Libra help-desk → vSphere rogue VM → offline NTDS.dit theft** — marquee multi-plane
  identity causality demo; DC compromise never touches a monitored OS. (muddled-libra-ops-playbook)
- **Akira vCenter/hypervisor pivot** — DC VMDK theft → offline NTDS.dit + SYSTEM-hive extraction;
  the shared-entity (VMDK) links a vCenter session to an offline AD parse. (howling-scorpius-akira)
- **BYOVM ransomware detonation inside a hypervisor-mounted VM** — impact relocated off the
  monitored host; tests VM-registration + mass VM power-off telemetry. (howling-scorpius-akira)
- **Malware-free velocity: help-desk→DA in <40 min, 350GB LOTL exfil** — the archetype the
  causality/storyline spine exists to prove: individually-benign steps, anomalous as a
  rate/sequence. High-value ABIOC/Analytics target. (2025-unit-42-global-incident-response-report)
- **Payroll Pirates** — non-AD SaaS/HR identity takeover: external-email MFA persistence +
  direct-deposit redirect (ITDR + Cloud App + EMAIL). (social-engineering-payroll-pirates)
- **Chisel SSH-over-HTTPS tunnel (goon.zip from attacker S3)** — 15-hour flow on 443; TOOL-CHISEL
  adapter + `endpoint_network_stitch` off the rogue-VM node. (muddled-libra-ops-playbook)
- **ADRecon/ADExplorer SPN-targeted enum** (Veeam/MSSQL/Exchange/Hyper-V/TS) — the recon node
  between initial compromise and lateral; SPN-service-filtering is a specific signal. TOOL-ADRECON.
- **S3 Browser PST/DB exfil + "no-registration file host" enumeration** — closes the chain with a
  cloud-exfil node stitching EDR (PST access) → NDR (bulk HTTPS to S3). (muddled-libra-ops-playbook)

### BYOVD defense-evasion (highest-leverage missing pivot)
- **Akira 'Zemana' / The Gentlemen 'GentleKiller' + ThrottleStop.sys CVE-2025-7771** — dropper →
  sc.exe service-create loading a vulnerable driver → user-mode client → protected-process
  termination. Benign **TOOL-BYOVD-EMULATOR** (tier-1, dual-use-lab-only, SAFE-MODE, signal not
  kill). Stresses driver-load + service-creation + self-protection-tamper analytics in one chain.
- **The Gentlemen LOTL + SystemBC SOCKS C2 + wevtutil/vssadmin anti-forensics** — temporal +
  network_session chain; `systembc_socks_egress` EAL plugin (distinct from `c2_http_beacon`);
  anti-forensics (T1070.001) is under-represented today.

### Agentic / AI-plane (extends KOI/AIRS)
- **Agent Session Smuggling in A2A systems** — injected mid-session turns with no user anchor;
  the `parent_step` break a causality graph makes visible. Correlation + ABIOC on session-coherence.
- **Persistent AI-agent long-term memory poisoning** — trigger and payload in *different* sessions;
  a temporal + `shared_entity(memory_store)` stitch (the strongest non-star demo). Extends AIRS/KOI
  into persistence.
- **MCP sampling abuse** (server→client `createMessage`) — the *other* MCP trust gap vs SIM-KOI-006;
  maxTokens inflation + covert tool invocation from an LLM response (exposure_exploit→exploit_impact).
- **Autonomous multi-agent cloud attack ('Zealot')** — SSRF→IMDS token→IAM self-escalation→BigQuery
  exfil; the detection thesis is **cadence, not any one call** (velocity ABIOC + Correlation).

### CI/CD → runtime supply-chain (a plane the corpus lacks)
- **tj-actions/changed-files** — GitHub Actions token theft with in-log memory-dump exfil; zero
  network IOC, pure log/behavioral analytics. New `SIM-CICD-*` cluster + `gha-workflow-emitter`
  EAL plugin (ITDR-pattern synthetic runner-log events).
- **Shai-Hulud self-replicating npm worm** — pre-install fork-to-background → TruffleHog sweep →
  GitHub-as-exfil → self-hosted-runner persistence. One artifact spans EDR + registry + NDR + IAM.
  `SIM-KOI-007`.
- **OH-MY-DC OIDC-in-CI/CD misconfig** — poisoned pipeline → STS AssumeRoleWithWebIdentity into
  production cloud; THE build-to-runtime pivot on a shared OIDC-token entity. `SIM-CDR-016`.
- **TeamPCP** — weaponized security scanners → `/proc/pid/mem` secret scrape → K8s privileged
  DaemonSet → ICP-canister C2. The most complete CI→production-runtime narrative available.

### New chainable recon adapters (ProjectDiscovery pipeline + AD enum)
`subfinder → dnsx → naabu/rustscan → httpx → katana → ffuf/sqlmap/nuclei` is a five-tool connected
recon→enum→exploit spine (each stage's output is the next stage's `-l` input, a `shared_entity`
pivot). Candidate tier-4 adapters: **TOOL-SUBFINDER, TOOL-DNSX, TOOL-NAABU, TOOL-RUSTSCAN,
TOOL-HTTPX, TOOL-KATANA, TOOL-WAFW00F, TOOL-KERBRUTE** (safe/dual-use as noted; kerbrute's
no-lockout userenum is a specific ITDR Kerberos pre-auth detection target). These complement the
15 Kali adapters already shipped and would make the recon→enum handoff a real causality edge rather
than a manual jump.
