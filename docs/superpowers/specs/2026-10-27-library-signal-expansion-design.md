# Library Signal Expansion — stitched, Unit 42-enrichable chains

**Date:** 2026-10-27 · **Status:** approved for execution (operator opted out of the
design conversation and asked for research + improvement to run) · **Branch:**
`feature/scenarios-unit42-stitched-chains` cut from local `dev` at 59612be.
**Predecessor:** `2026-10-26-prevention-detection-efficacy-design.md` §6/§7 (the
narrow content list there is superseded by §5 below; the engine contract is not).

## 1. Intent

The library pays off when a POV is accelerated and the customer's XSIAM fills with
one coherent adversary story per run: endpoint, network and cloud alerts stitched
into one incident, and the hashes, domains and IPs inside the telemetry enriched
automatically with Unit 42 threat intelligence so the case carries the actor, the
verdict and the threat report without the DC saying "trust me". The unit of value
is the **case**, not the scenario. The platform does the joining and the
attribution; the DC narrates what the product already found.

Measured on `dev` (2026-10-27, `scratchpad/lib/measure_signals.py`):

| property | value |
|---|---|
| scenarios / step-detections / steps | 177 / 1116 / 667 |
| scenarios spanning ≥2 of {endpoint, network, cloud-identity} | 40 (3 span all three) |
| with `stitching_key` declared | 41 |
| citing a Unit 42 report | 142 (80 distinct articles) |
| scenarios containing a SHA-256 / public IP / external domain anywhere | 5 / 17 / 42 |
| **approximate high-quality signals** (≥2 legs + Unit 42 + any indicator) | **12 scenarios · 105 step-detections (9.4 %)** |
| **strict high-quality signals** (§3 definition, needs `indicators[]`) | **0** — the field does not exist yet |
| marker-only steps (`echo`/`printf` only, or `curl` to localhost only) | **137 of 667 (20.5 %)**; SIM-EDR-019 7/7, SIM-EDR-020 6/6, SIM-MP-013 5/8 |
| card `iocs[]` objects | 301, but patterns (`lsass*.dmp`, `*.mega.nz`), and the card schema forbids a `source_url` |

Two gaps follow. **Fidelity:** a step that only echoes cannot produce the alert its
expected detection claims, so one fifth of the corpus narrates detections it cannot
fire. **Enrichment:** nothing in the library is enrichable by construction, because
no scenario carries a published indicator as structured data.

## 2. Goals, non-goals, anti-goals

**Goals**
1. Define "high-quality signal" as a property the repo counts and gates, so the gain
   is measured, never asserted.
2. Make a real Unit 42 indicator expressible in a scenario in a form that is safe by
   construction, lint-enforced.
3. Add 12 net-new stitched chains from Unit 42 reports outside the 80 already cited,
   each spanning at least two core legs (three where the report supports it), with
   zero marker-only steps and ≥3 published indicators.
4. Raise the coverage gate so it can go red on signal quality (today every floor is
   met and `coverage_report --strict` cannot fail).

**Non-goals**
- Tuning the prevention step or the paired run (operator instruction 2026-10-27).
- Retrofitting the 137 low-fidelity steps in existing scenarios. They are measured
  and reported; a later pass fixes them (backlog §9).
- Console rendering of indicators. The API exposes them; the UI pass is separate.
- Dispatching `channel: eal` steps to a beacon (Phase 3b remains pending). New
  content uses the existing heredoc convention (`python3 -m scripts.eal_simulator.cli
  run … --live`) exactly as `scenarios/ndr/ndr-012-*.yml` does.

**Anti-goals** (what would make this a failure even if every gate is green)
- A scenario that contacts adversary infrastructure. One live beacon to a real C2
  ends the programme.
- A synthetic log that claims a block. Fabricated prevention is a false claim about
  the customer's stack.
- Reporting authored chains as proven coverage. `tenant-verified` stays 0 until a
  chain runs against a live tenant.
- Counting the same story twice: a new chain that re-tells Muddled Libra, BlackSuit,
  Akira or TeamPCP adds citations, not stories.

## 3. Definitions (normative; the coverage report implements them verbatim)

**Core legs.** A plane maps to a leg: endpoint = `EDR | KOI | BROWSER`; network =
`NDR`; cloud-identity = `CDR | CSPM | CLOUD_APP | ITDR | AI_SPM | AI_ACCESS | AIRS`.
`ANALYTICS`, `EMAIL`, `TIM`, `ASM`, `DLP` are never a core leg. A scenario's legs are
computed from the union of `required_planes_in_incident[]` and every
`steps[].expected_detections[].plane`. **Authoring convention for new content:** a
step-detection carries the plane whose *sensor* raises the alert (`EDR`, `NDR`,
`CDR`, `ITDR`…); `ANALYTICS` is reserved for the correlation / stitch row.

**Stitched chain.** A scenario with (a) ≥2 core legs, (b) `stitching_key` set and
≥2 entries in `required_planes_in_incident`, (c) `threat_report_url` on
`unit42.paloaltonetworks.com`, and (d) ≥1 **in-band** entry in `indicators[]` (§4.1;
an entry with `in_band: false` does not count).

**Marker-only step.** An agent-channel step whose `command`, after splitting on
newlines, `;`, `&&`, `||` and `|`, has no fragment other than `echo`, `printf`,
`Write-Host`, `Write-Output`, `true`, a bare `:` or comment, or blank; or whose only
non-marker fragments are `curl` to `127.0.0.1`, `localhost` or `${STAGE_HOST…}`. A
missing command is marker-only. An EAL step (command invokes
`scripts.eal_simulator.cli` or `cortexsim-eal`) is never marker-only. (The 137 in
§1 was measured with a looser pre-rule approximation; the canonical rule's count is
the one the coverage report prints and the ground truth records.)

**High-quality signal (HQS).** A step-detection whose scenario is a stitched chain
and whose step is not marker-only. The count the operator asked to raise is the
number of HQS across the corpus.

## 4. Engine and schema changes (Track A — the minimum that makes §3 countable and safe)

### 4.1 Scenario `indicators[]` (scenario-level, optional, additive)

```yaml
indicators:
  - id: ind-01                      # unique within the scenario, ^ind-\d{2}$
    type: domain                    # sha256 | sha1 | md5 | domain | url | ipv4 | ipv6 | user_agent | ja3 | ja4
    value: "rn-loader-staging.example"   # the CLEAN value (no [.] / hxxp defanging) — enrichment needs the real string
    source_url: "https://unit42.paloaltonetworks.com/slow-pisces-new-custom-malware/"
    safe_form: dns_lookup           # log_field | dns_lookup | hash_only | wildfire_test_file | eicar
    surfaces_in: [step-03]          # steps whose telemetry carries the value
    description: "RN Loader second-stage host (report, Table 2)"
    in_band: true                   # optional bool, default true; false = published context this chain does not emit
```

`in_band` (amended 2026-10-27, final review): `false` marks an indicator the report
names but the chain never puts on the wire. The coverage report's
`signal_quality.indicators` counts in-band entries only, `indicators_not_in_band`
the rest, and a stitched chain needs ≥1 in-band entry. A non-bool is S-22.

Loader (`core/engine/scenario_loader.py`, `IndicatorSchema` + a model validator on
`ScenarioSchema`) raises **structural** codes, not gated by `CORTEXSIM_STRICT_REFS`:

| code | rule |
|---|---|
| S-22 | `type` outside the vocabulary, `in_band` present and not a bool, or `value` malformed for its type (sha256 = 64 hex, sha1 = 40, md5 = 32, ipv4 = dotted quad and not RFC1918/loopback/link-local, domain contains a dot and no scheme, url has a scheme) |
| S-23 | `source_url` not on `unit42.paloaltonetworks.com` or `github.com/PaloAltoNetworks/` |
| S-24 | `surfaces_in` empty, or names a step id the scenario does not have |
| S-25 | **unsafe surfacing** (tightened 2026-10-27, final review): an `ipv4`, `ipv6`, `domain` or `url` value — and a `url`'s host, scanned as a `domain` (or as an IP when it is a literal); for a domain, any subdomain too — appears in any executed text (every step `command`, every `platform_variants` value, every `cleanup.commands` entry) outside the two exemptions. **(a) Lookup line** — `domain` values only (an `ipv4`/`ipv6`/`url` value on a lookup line always fires): a DNS-lookup line (`dig`, `nslookup`, `host`, `getent hosts`, `resolvectl query`, `drill`) that **names its resolver** (`@<non-space token>` or the token `LAB_RESOLVER`; the `@` resolver may not itself be the indicator), with **no** fetch verb and **no** chaining, redirection, background or substitution (`;` `&&` `\|\|` `\|` `$(` `` ` `` `<` `>` `&` `<(` `>(`). **(b) `params:` block** — only inside a heredoc whose opener writes a campaign file (`cat > <path>.yml <<` or `.yaml`) and whose every `plugin:` is a collector-POST log streamer (`COLLECTOR_POST_PLUGINS`: `ngfw_eal_emitter`, `ngfw_threat_emitter`, `cloud_audit_emitter`, `cloud_storage_compute_emitter`, `azure_audit_emitter`, `k8s_audit_emitter`, `m365_activity_emitter`, `ad_windows_emitter`, `idp_signin_emulator`, `email_emitter`, `duo_auth_emitter`, `globalprotect_emitter`, `third_party_alert_emitter`, `third_party_firewall_emitter`, `third_party_vpn_emitter`); any other or unknown plugin (`c2_http_beacon`, `dns_tunnel_exfil`, `ssh_egress`, …) exempts nothing. A heredoc opener is `<<`/`<<-` with a word delimiter, never a `<<<` here-string, a comment or an arithmetic shift. The loader and the stdlib hook carry byte-identical regexes, pinned by `test_hook_unsafe_lines_match_loader` |
| S-26 | **artifact fetch**: a `sha256`/`sha1`/`md5` value appears on a line of that same executed text that also contains a fetch verb (`curl`, `wget`, `pip`, `npm`, `git clone`, `Invoke-WebRequest`, `iwr`, `certutil`, `bitsadmin`, `Start-BitsTransfer`, `aria2c`, `scp`, `tftp`, `ftp`) |
| S-27 | `safe_form` incompatible with `type`: `dns_lookup` only for `domain`; `hash_only`, `wildfire_test_file`, `eicar` only for hash types; `log_field` for any |

Persisted as `Scenario.indicators` (nullable JSON column, same additive pattern as
`cgo_anchor`; `_migrate_scenarios_columns` adds it; `to_dict()` exposes it so
`GET /api/scenarios/{id}` and the run report can list what the DC should search for
in the tenant).

`.claude/hooks/lint-scenario.py` mirrors S-22..S-27 as errors at edit time and adds a
**warning** (never an error) naming every marker-only step, so the authoring loop
sees fidelity before the gate does.

### 4.2 Card schema: `detections.iocs[]` gains provenance

`detection_scanner/schema/ttp-entry.schema.json` `iocs.items.properties` gains
`source_url` (string, uri), `published` (string, `YYYY-MM-DD` or `YYYY-MM`) and
`safe_form` (same enum as §4.1). `additionalProperties` stays `false`.
`validate.py` adds `check_ioc_provenance`: FAIL when `source_url` is present and not
on the two allowed hosts; WARN when a card whose primary reference is Unit 42 has an
`iocs[]` entry of type `ipv4|domain|url|file-sha256` with no `source_url`.

### 4.3 Emitters: let a synthetic leg carry the published indicator

- `ngfw_eal_emitter` (`core/eal_simulator/plugins/ngfw_eal_emitter.py`):
  `NgfwEalEmitterParams` gains `dst_domain: Optional[str]` and `dst_ip: Optional[str]`
  (both or neither; validator). When set they replace the preset `.invalid`
  destination on every session the pattern builds. `action` stays `allow`.
  **It also gains `src_ip`, `src_host`, `src_user` (each independently optional)**,
  because the record's source is a hardcoded constant today (`_SRC_HOST =
  "10.0.12.34"`): a synthetic network alert that names a host the real endpoint
  alert does not is three alerts, not one case. Unset keeps the constants.
- **New plugin `ngfw_threat_emitter`** (`_DATASET = "panw_ngfw_threat_raw"`, already in
  `validate.py` `KNOWN_DATASETS`): a PAN-OS threat/WildFire log record carrying
  `filedigest` (sha256), `filename`, `filetype`, `verdict` (`malicious | grayware |
  benign`, default `malicious`), `app`, `src`, `dst`, `dst_domain`, `category`,
  `action`, with the same `src_ip` / `src_host` / `src_user` overrides as the traffic
  emitter so the threat log lands on the run host. **`action` is restricted to `alert` and `allow`**: `block`, `deny`,
  `drop`, `reset-*`, `sinkhole` raise `ValueError` at param validation, because a
  synthetic log may show detection and enrichment but never prevention. Registered
  exactly like the other analytics streamers (so `check-streamer-fidelity.py` maps
  its dataset) and added to `docs/reference/eal-plugin-catalog.md`.
- `cloud_audit_emitter`: `CloudAuditEmitterParams` exposes `source_ip: Optional[str]`
  (the function already takes it); unset keeps today's `_ANOMALOUS_IP`.

### 4.4 Coverage gate: a section that can go red

`detection_scanner/scripts/coverage_report.py` gains a `signal_quality` block in the
JSON shape and the human render:

```json
"signal_quality": {
  "leg_map": {...}, "stitched_chains": N, "stitched_chain_ids": [...],
  "hq_signals": N, "step_detections": N, "hq_share": 0.0,
  "marker_only_steps": N, "steps": N, "marker_only_share": 0.0,
  "scenarios_with_indicators": N, "indicators": N
}
```

Three config keys, breaches listed in `strict_breaches` and therefore `--strict` exit
1: `hq_signal_floor` (absolute), `stitched_chain_floor` (absolute),
`marker_only_ceiling` (share). Defaults are set in the closing task to the values
measured after the content lands, so the gate fails the moment someone removes a
chain or adds an echo-only step. `scripts/generate_ground_truth.py` carries the
block into `docs/reference/ground-truth.{json,md}`.

## 5. Content (Track B — 12 stitched chains)

Selection from 41 harvested Unit 42 articles (`scratchpad/lib/research/*.json`,
2026-10-27), none in the 80 already cited, ranked by leg span then verified IOC
richness, filtered for Linux-first feasibility and for stories the corpus does not
already tell.

| # | scenario | card | report (unit42.paloaltonetworks.com/…) | actor / campaign | legs E/N/C | IOCs (sha256 · domains · IPs) | endpoint platform |
|---|---|---|---|---|---|---|---|
| 1 | SIM-MP-023 | TTP-2026-0182 | `advanced-backdoor-squidoor/` (2025-02) | CL-STA-0049 Squidoor / FinalDraft | 3/3/2 | 22 · 5 · 3 | linux (Windows variant) |
| 2 | SIM-MP-024 | TTP-2026-0183 | `ai-campaign-targets-linux-infrastructure/` (2026-10) | CL-UNK-1211 AI-driven Linux espionage | 3/3/≤2 | 10 · 6 · 17 | linux |
| 3 | SIM-MP-025 | TTP-2026-0184 | `blinder-tunnel-targets-critical-infrastructure/` (2026-10) | CL-STA-1178 Blinder Tunnel, GitHub-as-C2 | 3/3/2 | 8 · 9 · 4 | windows (linux variant) |
| 4 | SIM-MP-026 | TTP-2026-0185 | `windows-backdoor-for-novel-c2-communication/` (2025-07) | CL-STA-1020 HazyBeacon, AWS Lambda-URL C2 | 3/2/3 | 7 · 0 · 0 | windows (linux variant) |
| 5 | SIM-MP-027 | TTP-2026-0186 | `slow-pisces-new-custom-malware/` (2025-04) | Slow Pisces RN Loader / RN Stealer | 3/3/2 | 0 · 27 · 27 | linux / macos |
| 6 | SIM-MP-028 | TTP-2026-0187 | `chaindrop-npm-worm-analysis/` (2026-08) | ChainDrop npm worm, runner OIDC + IMDS | 3/3/2 | 4 · 4 · 2 | linux / container |
| 7 | SIM-MP-029 | TTP-2026-0188 | `shadow-campaigns-uncovering-global-espionage/` (2026-02) | TGR-STA-1030 Shadow Campaigns | 3/3/1 | 11 · 12 · 12 | windows (linux variant) |
| 8 | SIM-MP-030 | TTP-2026-0189 | `north-korean-threat-group-play-ransomware/` (2024-10) | Jumpy Pisces → Play (Fiddling Scorpius) hand-off | 3/3/1 | 6 · 1 · 1 | windows |
| 9 | SIM-MP-031 | TTP-2026-0190 | `repellent-scorpius-cicada3301-ransomware/` (2024-09) | Repellent Scorpius / Cicada3301 ESXi | 3/3/1 | 6 · 0 · 2 (+ GitHub appendix) | linux (ESXi-shaped) |
| 10 | SIM-MP-032 | TTP-2026-0191 | `beyondtrust-cve-2026-1731/` (2026-02) | mass exploitation of BeyondTrust RS/PRA | 3/3/0 | 9 · 9 · 19 | linux |
| 11 | SIM-MP-033 | TTP-2026-0192 | `infiltration-of-global-telecom-networks/` (2025-07) | CL-STA-0969 telecom, Cordscan / GTPDOOR | 3/3/1 | 23 · 0 · 0 | linux |
| 12 | SIM-MP-034 | TTP-2026-0193 | `cloud-based-gift-card-fraud-campaign/` (2025-10) | CL-CRI-1032 Jingle Thief, M365/Entra | 0/2/3 | 0 · 6 · 22 | cloud + identity only |

Per-chain requirements (the content reviewer checks every line):

1. File `scenarios/multi_plane/mp-0NN-<slug>.yml`, `plane: ANALYTICS`, ≥6 steps.
2. Legs: ≥2 core legs; 3 where the table says the report supports it. A leg the report
   does not describe is **not** invented (anti-goal). Jingle Thief has no endpoint leg
   and stays cloud + identity.
3. Synthetic legs use an EAL step per leg with the heredoc convention; the network
   leg uses `ngfw_eal_emitter` with `dst_domain`/`dst_ip` from the report, or
   `ngfw_threat_emitter` with a report `sha256`; the cloud leg uses
   `cloud_audit_emitter` / `k8s_audit_emitter` / `m365_activity_emitter` /
   `idp_signin_emulator` as the story demands.
3a. **Every leg carries the stitching entity, in-band.** The step that launches a
   synthetic leg first reads the run host's identity (`SRC_IP=$(hostname -I | awk
   '{print $1}')`, `SRC_HOST=$(hostname -s)`, `SRC_USER=$(id -un)`) and passes it in
   the campaign `params:` (`src_ip`, `src_host`, `src_user` on the NGFW emitters;
   `source_ip` on `cloud_audit_emitter`, with `${EGRESS_IP:-$SRC_IP}` because the
   cloud control plane sees the egress address). `stitching_key` names an entity
   that every leg of the chain carries (`src_host` for endpoint+network,
   `user_principal` for identity-led chains). A chain whose legs cannot share an
   entity is not authored.
4. Agent steps perform the real, safe behaviour the detection keys on: the process
   lineage, file paths, persistence mechanism, HTTP shape (URI, user-agent) against
   `$STAGE_HOST`, DNS lookups of report domains. **Zero marker-only steps.** A marker
   `echo` may accompany behaviour, never replace it.
5. `cgo_anchor`, per-step `causality` on every non-root step, `platforms` on every
   step, `platform_variants` where the table says "variant".
6. `stitching_key`, `required_planes_in_incident` (≥2), `correlation_window_seconds`.
7. `indicators[]`: ≥3 entries, every one with `source_url` = the report (or its
   linked PaloAltoNetworks GitHub IOC file), clean values **copied from the live
   page at authoring time**, never from the harvest counts. ≥1 indicator per
   synthetic leg *where the report publishes an indicator that leg can carry*: a
   network leg may be threat-log-only (a report sha256 through `ngfw_threat_emitter`)
   when the report lists no domain or IP (SIM-MP-026, SIM-MP-033); a leg the report
   gives no indicator for keeps the preset destination, carries the stitching entity
   only, and the header says that leg is un-enrichable. If the report URL cannot be
   fetched the chain task stops as BLOCKED and the controller swaps in a §9
   candidate; the count of chains is whatever that leaves, reported as measured.
8. `threat_report` / `threat_report_url` = the report; `tags` include `unit42`,
   `stitched-chain`, `actor:<constellation-or-name>`; a header comment (≤12 lines)
   tells the story and says what the DC points at in each leg.
9. `uc_ref` / `tc_ref` / `tc_refs[]` / `pov_scenario_id` valid under
   `CORTEXSIM_STRICT_REFS=true` (S-10..S-16). The crosswalk row is added by the
   closing task, not by the chain task (one shared file, one writer).
10. Card `detection_scanner/ttps/TTP-2026-01NN-<slug>.json`: every
    `expected_detections[].detection_id` resolves (lint hook slug rules); `iocs[]`
    carries the same indicators with `source_url`; datasets from `KNOWN_DATASETS`;
    `references[0]` is the report with `primary: true`; `validate.py` 0 fail / 0 warn.

## 6. Counting and gates (what "done" means)

- `python3 detection_scanner/scripts/coverage_report.py --json` reports
  `signal_quality.hq_signals` ≥ 105 + the new chains' step-detections (expected
  ≈ 100–130 more), `stitched_chains` ≥ 12 strict (today 0 strict), and
  `marker_only_share` ≤ 0.205 (it falls because the denominator grows).
- `make validate` green: `validate-detection`, `check-refs` (6 passed, 189
  scenarios), `check-uctc-sheet`, `check-streamer` (new emitter mapped),
  `check-ground-truth` (regenerated), shelf gates unchanged.
- `tests/engine/test_corpus_refs_strict.py` 177 → 189; `tests/api/test_uctc_index.py`
  170 → 182; both bumps carry a dated docstring line as the file's convention.
- Host pytest, e2e_isolated and vitest unchanged-or-better; the lint hook tests cover
  S-22..S-27 and the marker warning, each proven to fail without the rule.
- Docs: `README.md`, `CLAUDE.md`, `docs/reference/README.md`,
  `docs/reference/scenario-catalog.md` header, `docs/reference/eal-plugin-catalog.md`
  carry the new counts (189 scenarios · 187 cards · 27 EAL plugins) with the measured
  `signal_quality` numbers quoted from the ground truth, not typed.
- Every number in the report to the operator is the output of a command.

## 7. Decided here (the builder does not reopen these)

- `indicators[]` is scenario-level with `surfaces_in`, not per-step, because an
  indicator usually appears in more than one step and a case is enriched once.
- Clean values on disk, never defanged. Safety comes from S-25/S-26, not from
  spelling.
- A new `ngfw_threat_emitter` rather than a second dataset on `ngfw_eal_emitter`,
  because `check-streamer-fidelity.py` maps one `_DATASET` per emitter.
- Synthetic `action` can never be a block. Not configurable.
- The crosswalk row is written once for all chains in the closing task.
- Numbering: SIM-MP-023..034, TTP-2026-0182..0193, contiguous, in table order
  (checked 2026-10-27: no branch in the repository has ever added a file in either
  range).
- Floors are set to measured values at the end, not guessed at the start — the §6
  expectations are targets, not gates; if the measured count is below them the gate
  is still set to the measured value and the shortfall is reported, not hidden.
- **DNS lookup of a real published domain — provisional ruling, flagged to the
  operator.** `safe_form: dns_lookup` sends one query through the resolver the
  operator names (`@${LAB_RESOLVER}` for `dig`, or the positional resolver argument
  for `nslookup` on Windows; the lookup line must name the resolver and the scenario
  header must state it); no session is ever opened. The query may still
  reach the actor's authoritative nameserver when the domain is live, which exposes
  the resolver's address and nothing else. Chains that do this carry the tag
  `real-ioc-dns-lookup` so they can be found and switched to `log_field`-only in one
  pass if the operator rules the other way. The alternative — PANW's own DNS
  Security test domains — exercises the control but is not Unit 42-enrichable as the
  actor, which is the point of this pass.

## 8. Left to the builder

- XQL / BIOC / correlation bodies per card (must pass `validate.py` grammar and
  dataset checks).
- Exact step commands and identities, within §5 rule 4 and the safety codes.
- Which report indicators to carry (prefer domains and IPs for network, sha256 for
  the threat log, IPs for cloud `sourceIPAddress`).
- Per-chain `tc_refs` choice from the FY27 index (valid ids only; the chain brief
  carries a generated list of every `tc_id, ucs_id, uc_id, validation_class, title`
  and every `pov_scenario_id` from `docs/uc_tc_mapping/_v2.2-source/`).
- Fetching the report: the chain author fetches the live Unit 42 page (and the
  linked GitHub IOC file where the table says so) and copies indicator values from
  it; the harvest records under the session scratchpad are leads, not sources.

## 9. Backlog (not this pass)

- Fidelity retrofit of the 137 marker-only steps, worst first (SIM-EDR-019,
  SIM-EDR-020, SIM-MP-013, SIM-ITDR-017, SIM-ASM-005, SIM-AISPM-007).
- Indicators on the existing 20 multi-plane scenarios so they count as strict
  stitched chains.
- Remaining harvested candidates: PEERSTITCH FortiManager (CL-STA-0954), Stately
  Taurus SE-Asia, CL-STA-1087, Contagious Interview, RustDoor/Koi Stealer, Gold
  Melody machine keys, RansomHouse, Lynx, Axios supply chain, `monitoring-npm-supply-
  chain-attacks` (tracker, IOC list disputed between harvesters).
- Console: render `indicators[]` on the scenario drawer and the run report.
- Wire `channel: eal` dispatch (Phase 3b) so EAL legs stop needing a heredoc.
