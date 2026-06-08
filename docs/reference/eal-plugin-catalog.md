# EAL (Emulated Attack Layer) Simulator — Plugin Catalog & Reference

> **Status:** canonical reference, generated 2026-06-07 from a full read of
> `core/eal_simulator/`, `core/eal_simulator/plugins/`, `core/api/eal.py`,
> `tests/eal_simulator/`, and `docs/eal-simulator/`.
> **Scope:** every shipped plugin, the campaign/executor/safety machinery they
> run inside, the Probe→Mutator→Target→Scorer pipeline used by the AIRS plugin,
> the complete safety model, and every gap found.

The EAL Traffic Simulator (the names "EAL simulator", "Emulated Attack Layer",
and "EAL Traffic Simulator" are used interchangeably across the tree) is a
self-contained subsystem of CortexSim that emits controlled, real network and
identity telemetry to trigger Palo Alto Networks **Enhanced Application Logs
(EALs)** and validate Cortex XDR / XSIAM detection analytics (NDR, ITDR,
Cloud App Security, AI Access, AIRS, Prisma Browser, KOI). It is **read-only
with respect to Cortex** — it generates signal INTO the environment, never
reads alerts OUT.

It is wired to the rest of CortexSim by:
- the `/api/eal/*` REST router (`core/api/eal.py`),
- the `EalCampaign` / `EalCampaignRun` ORM rows (`core/models.py`),
- the operator CLI (`scripts/eal_simulator/cli.py`),
- and (loosely, by naming convention) NDR/ITDR/Cloud-App/AI/AIRS/BROWSER/KOI
  scenario YAMLs that reference plugins by `Meta.name`.

---

## 1. Subsystem anatomy

| Component | File | Responsibility |
|-----------|------|----------------|
| `BaseSimulation` | `core/eal_simulator/base.py` | Abstract contract every plugin implements. Inner `Meta` class declares identity; `async run(ctx) -> SimulationResult` is the only method the executor calls. |
| `SimulationContext` | `core/eal_simulator/base.py` | Per-step dataclass injected into `run()`: `campaign_id`, `run_id`, `step_id`, `simulation_run_id`, `dry_run`, `target_allowlist`, `emit_event` callback, validated `params`, optional `deadline_at`. Exposes `telemetry_headers` property. |
| `SimulationResult` | `core/eal_simulator/base.py` | Structured outcome: `plugin`, `step_id`, `status` (`success`/`error`/`skipped`), `started_at`, `completed_at`, `events_emitted`, `bytes_sent`, `detail` dict, `error`. Has `duration_seconds` and `to_dict()`. |
| `PluginRegistry` | `core/eal_simulator/registry.py` | Dynamic loader. `load_package()` imports every submodule under `eal_simulator.plugins`; `load_directory()` loads out-of-tree `.py` drops. Case-insensitive lookup on `Meta.name`. `get_default_registry()` is the process-wide singleton. |
| `Campaign` / `CampaignStep` / `PluginInvocation` | `core/eal_simulator/campaign.py` | Pydantic declarative schema. Campaign carries the safety block + ordered steps. |
| `CampaignExecutor` / `ExecutorState` / `TaskQueue` / `InMemoryTaskQueue` | `core/eal_simulator/executor.py` | Async orchestrator. Runs steps **sequentially** (campaigns are narrative orderings, not parallel fan-outs). Constructs the `SafetyPolicy` once, gates the campaign, then per step: resolve plugin → validate params → build ctx → emit `step_started` → `plugin.run()` → emit `step_finished`. |
| `SafetyPolicy` / `SafetyError` | `core/eal_simulator/safety.py` | The single chokepoint. Campaign-level gate (`assert_campaign_authorized`) + per-target gate (`authorise`). |
| `AuditLogger` / `ecs_event` | `core/eal_simulator/audit.py` | Hand-built ECS 8.11 JSON event emitter (file sink + Python logging). |
| API | `core/api/eal.py` | `/api/eal/*` endpoints. |
| ORM | `core/models.py` (`EalCampaign`, `EalCampaignRun`) | History persistence in the shared SQLite DB. |
| CLI | `scripts/eal_simulator/cli.py` | `list-plugins`, `describe`, `run [--live]`, `worker` subcommands. |

### Plugin contract (enforced)

A plugin **must**:
1. Subclass `BaseSimulation` and define an inner `Meta` with at least `name`
   and `params_model` (a Pydantic `BaseModel` subclass). Optional Meta fields:
   `version` (default `"1.0.0"`), `description`, `mitre_techniques`,
   `eal_targets`. `metadata()` raises `TypeError` if `name`/`params_model`
   are missing — the registry then **skips** that class with an error log.
2. Implement `async run(ctx: SimulationContext) -> SimulationResult`.
3. Call `ctx.authorise(target)` before emitting any packet (raises
   `SafetyError` if the target is out of allowlist).
4. Branch on `ctx.dry_run` and return without traffic when true.
5. Emit at least one ECS event via `await ctx.emit_event(ecs_event(...))`.
6. Add `**ctx.telemetry_headers` to HTTP requests (the
   `X-Simulation-Run-ID` / `X-Simulation-Campaign-ID` / `X-Simulation-Source`
   trio) for SOC filtering.

> **Note on the `authorise` hook:** the executor does **not** put `authorise`
> on the `SimulationContext` dataclass surface. It stashes it via
> `setattr(ctx, "authorise", policy.authorise)` and `setattr(ctx, "_policy", policy)`
> after construction (`executor.py` lines 276-277). Every plugin therefore
> calls it through `getattr(ctx, "authorise")(host)`. The architecture doc and
> plugin-development doc show `ctx.authorise(...)` directly, which only works
> because of that runtime patch — **a static type checker will not see it**
> (gap EAL-G07).

### Campaign / executor flow

1. **Author** YAML/JSON, `POST /api/eal/campaigns`. Each step's plugin must be
   registered and its params must validate against the plugin's model, else
   422 (`PLUGIN_NOT_FOUND` / `PARAMS_INVALID`).
2. **Launch** `POST /api/eal/campaigns/{id}/launch` with `{dry_run, operator}`.
   The API synchronously pre-flights `SafetyPolicy.assert_campaign_authorized()`
   (returns 422 `SAFETY_VIOLATION` on failure), creates an `EalCampaignRun`
   row as `pending`, and schedules `_run_campaign_in_background` via FastAPI
   `BackgroundTasks`. The persisted `run_id` is passed into the executor so the
   DB row and ECS audit lines share one id.
3. **Executor** drives steps sequentially. `on_error: abort` stops the campaign
   (status → `aborted`); `on_error: continue` (default) keeps going.
4. **Persistence**: the background task mirrors the final `ExecutorState`
   (status, error, completed_at, step_results) back to the row. Poll
   `GET /api/eal/runs/{run_id}`.

### Audit event shape (`ecs_event`)

Every event is ECS 8.11-shaped: `@timestamp`, `ecs.version`, `event.{kind,
category, type, action, outcome, module, dataset}`, `host.name`,
`service.{name,type}`, `message`, and a `cortexsim` namespace carrying
`campaign_id`, `run_id`, `step_id`, `plugin`, `target`, `bytes_sent`, plus any
plugin `extra`. `None` values inside `cortexsim` are dropped. Lifecycle events
emitted by the executor itself: `campaign_started`, `campaign_refused`,
`campaign_finished`, `step_started`, `step_finished`.

---

## 2. Plugin catalog (all 14 plugins)

Plugins fall into three implementation families:
- **In-process HTTP emitters** (httpx): c2_http_beacon, bulk_https_exfil,
  llm_provider_egress, oauth_grant_emulator, idp_signin_emulator, agentic_egress.
- **In-process raw-socket / DNS emitters** (stdlib `socket`): dns_tunnel_exfil,
  stratum_tcp_connect, smb_rpc_sweep, ftp_egress, ssh_egress.
- **Attacker-shells-out CLI drivers** (`asyncio.create_subprocess_exec` →
  JSONL → ECS): airs_prompt_attack, browser_attack_runner.

### 2.1 Summary table

| # | Plugin `Meta.name` | Plane | Attack shape emitted | Target / destination | MITRE | File |
|---|--------------------|-------|----------------------|----------------------|-------|------|
| 1 | `c2_http_beacon` | NDR | Periodic HTTP/S beacon: rotating anomalous User-Agents, jittered interval, DGA-style query param | `target_url` (http/https) | T1071.001, T1071, T1568 | `plugins/c2_http_beacon.py` |
| 2 | `dns_tunnel_exfil` | NDR | DNS tunneling: high-entropy base32/base64/hex subdomain labels, A or TXT queries | `*.{base_domain}` via system or custom resolver | T1048.003, T1572 | `plugins/dns_tunnel_exfil.py` |
| 3 | `stratum_tcp_connect` | NDR | Stratum/cryptojacking JSON-RPC `mining.subscribe`+`login` over raw TCP | `target_host:target_port` | T1496 | `plugins/stratum_tcp_connect.py` |
| 4 | `smb_rpc_sweep` | NDR | Host-sweep TCP connect probes across SMB/RPC ports (445/139/135), optional null-session NTLM via impacket | hosts in `target_cidr` | T1018, T1021.002, T1046 | `plugins/smb_rpc_sweep.py` |
| 5 | `bulk_https_exfil` | NDR | Large outbound HTTPS upload (random bytes, streamed, up to 16 GiB ceiling) | `target_url` | T1041, T1567 | `plugins/bulk_https_exfil.py` |
| 6 | `ftp_egress` | NDR | Cleartext FTP control session (banner→USER→PASS→SYST→NOOP→PASV→STOR→QUIT) + optional STOR data channel | `target_host:target_port` (default 21) | T1071, T1048.003 | `plugins/ftp_egress.py` |
| 7 | `ssh_egress` | NDR | Outbound SSH banner (RFC-4253) + synthesised `SSH_MSG_KEXINIT` pseudo-frame; stops before key exchange | `target_host:target_port` (default 22) | T1021.004, T1572 | `plugins/ssh_egress.py` |
| 8 | `idp_signin_emulator` | ITDR | Synthetic IdP audit events (Okta/Microsoft Entra/Google shape) POSTed to a collector; 5 behavioural presets | operator `collector_url` | T1110.003, T1110.004, T1078.004, T1556.006, T1539 | `plugins/idp_signin_emulator.py` |
| 9 | `oauth_grant_emulator` | Cloud App / CASB | Outbound OAuth 2.0 authorize GETs with planted risky scopes + bogus client_id | Okta/Microsoft/Google authorize endpoint | T1550.001, T1528, T1078.004, T1098 | `plugins/oauth_grant_emulator.py` |
| 10 | `llm_provider_egress` | AI Access | Authentic-shape POSTs to OpenAI/Anthropic/Gemini carrying planted DLP markers (PII/secret/source/jailbreak), optional size padding | provider API host | T1567, T1041, T1552 | `plugins/llm_provider_egress.py` |
| 11 | `airs_prompt_attack` | AIRS | Shells out to `cortex-prompt-attacker`; Probe→Mutator→Target→Scorer pipeline against a vulnerable LLM; JSONL→ECS | `target_url` (the AIRS target) | T1656, T1059, T1499 | `plugins/airs_prompt_attack.py` |
| 12 | `browser_attack_runner` | BROWSER (Prisma) | Shells out to `cortex-browser-attacker` (Playwright/Chromium/Prisma); YAML-declared browser actions; JSONL→ECS | `allowlist_host` (browser navigates) | T1552, T1189, T1176, T1567, T1113 | `plugins/browser_attack_runner.py` |
| 13 | `agentic_egress` | KOI (supply-chain) | Tarballs an in-tree malicious-agentic-pack component and POSTs it (pypi_mirror does GET-probe + POST) | staging `target_url` | T1195, T1195.002, T1176, T1059 | `plugins/agentic_egress.py` |

> The summary table lists 13 rows because `ftp_egress` and `ssh_egress` are
> NDR siblings; all 14 plugin files are catalogued below (the 14th file is the
> count: c2, dns, stratum, smb, bulk, ftp, ssh, idp, oauth, llm, airs, browser,
> agentic = **13 plugin classes** total; there is no 14th hidden plugin — the
> directory's 14 `.py` files are these 13 plus `__init__.py`).

### 2.2 Per-plugin detail

Each entry: safety gating · params (with defaults & bounds) · output/result shape.

---

#### 1. `c2_http_beacon` (NDR)

**Attack shape.** Periodic outbound HTTP/S beacon. Each iteration: pick a
random User-Agent (default pool of 5 anomalous UAs all tagged `CortexSimBeacon`),
optionally append a 12-char DGA-style `?q=<token>` param, optional random POST
body, then jittered sleep. Emits `c2_beacon_request` per iteration.

**Safety gating.** `ctx.authorise(hostname-of-target_url)` once up front. **Per-target
allowlist only** — no env-var or consent gate. `verify=False` on the httpx client
(intentional: POVs MitM through the customer NGFW with a self-signed cert).

| Param | Type / default | Bounds |
|-------|----------------|--------|
| `target_url` | str (required) | must be http/https with a hostname |
| `iterations` | int = 10 | 1–10000 |
| `sleep_seconds` | float = 30.0 | 0.1–86400 |
| `jitter_pct` | float = 20.0 | 0–90 |
| `method` | str = "GET" | GET/POST/HEAD |
| `user_agents` | list[str] = 5 defaults | — |
| `dga_query_param` | bool = True | — |
| `request_timeout` | float = 10.0 | 0.5–120 |
| `body_size_bytes` | int = 0 | 0–1048576 |

**Output.** `detail`: `iterations_completed`, `target_url`, `method`. `bytes_sent`
accumulates body + URL bytes. Dry-run emits `c2_beacon_dry_run` and returns
`detail.dry_run=True`.

---

#### 2. `dns_tunnel_exfil` (NDR)

**Attack shape.** Encodes random in-plugin payloads as high-entropy subdomain
labels (`{label}.exfil-{i:04d}.{base_domain}`) and issues A (`socket.getaddrinfo`)
or TXT (hand-built stdlib UDP/53 packet) queries. NXDOMAIN is expected and
counted as success — the EAL trigger is the outbound query, not the answer.

**Safety gating.** `ctx.authorise(base_domain)`. Per-target allowlist only.
**Payloads are randomly generated inside the plugin — never sourced from outside**,
so no real data can exfiltrate. Labels clipped to 60 bytes (DNS 63-byte limit).

| Param | Type / default | Bounds |
|-------|----------------|--------|
| `base_domain` | str (required) | non-empty FQDN |
| `chunks` | int = 20 | 1–2000 |
| `chunk_size_bytes` | int = 24 | 4–48 |
| `sleep_seconds` | float = 1.0 | 0–300 |
| `encoding` | str = "base32" | base32/base64/hex |
| `query_type` | str = "A" | A/TXT |
| `resolver` | str? = None | optional resolver IP for TXT |

**Output.** `detail`: `queries_sent`, `base_domain`, `encoding`, `query_type`.
Per-query event `dns_tunnel_query`.

---

#### 3. `stratum_tcp_connect` (NDR)

**Attack shape.** Opens raw TCP sessions, sends `mining.subscribe` + `login`
JSON-RPC frames matching the Stratum/XMRig signature, idles briefly so the NGFW
App-ID engine fingerprints the protocol.

**Safety gating.** `ctx.authorise(target_host)` only. **Docstring claims "target
host AND port must appear in the allowlist" but the code never authorises the
port** — `SafetyPolicy.authorise` strips ports before matching, and the plugin
passes only the host. So a campaign authorising the host implicitly authorises
**any** port to that host (gap EAL-G02). `wallet` validator rejects disallowed
characters to avoid leaking a real wallet address.

| Param | Type / default | Bounds |
|-------|----------------|--------|
| `target_host` | str (required) | non-empty |
| `target_port` | int (required) | 1–65535 |
| `iterations` | int = 3 | 1–200 |
| `sleep_seconds` | float = 15.0 | 0–600 |
| `user_agent` | str = XMRig default | — |
| `wallet` | str = "cortexsim-test-wallet" | charset-restricted |
| `connect_timeout` | float = 5.0 | 0.5–30 |
| `idle_seconds` | float = 2.0 | 0–60 |

> Bug note: `_COMMON_STRATUM_PORTS` is `{3333, 4444, 5555, 7777, 14433, 14444, 14433}`
> — `14433` is listed twice (set dedups it). Cosmetic (gap EAL-G09).

**Output.** `detail`: `sessions_completed`, `target`. Per-session event
`stratum_session`.

---

#### 4. `smb_rpc_sweep` (NDR)

**Attack shape.** TCP connect-sweep across SMB/RPC ports for a CIDR (no auth by
default — just connect probes). If `probe_ntlm=True` **and** impacket is
importable, attempts a null SMB session per open 445 host.

**Safety gating.** Strongest per-target enforcement of any plugin: authorises
the CIDR base IP up front, then **re-authorises every individual host** inside
the sweep loop. A partial-overlap allowlist that admits the base IP but excludes
the rest of the range causes those hosts to be **skipped** (emits
`smb_sweep_skipped`, ECS category `iam`/`denied`) rather than probed. This is
the model other range-touching plugins should follow.

| Param | Type / default | Bounds |
|-------|----------------|--------|
| `target_cidr` | str (required) | valid CIDR or IP |
| `ports` | list[int] = [445,139,135] | each 1–65535, non-empty |
| `connect_timeout` | float = 1.5 | 0.1–10 |
| `inter_host_delay` | float = 0.05 | 0–10 |
| `max_hosts` | int = 256 | 1–4096 |
| `probe_ntlm` | bool = False | requires impacket (optional dep) |

**Output.** `detail`: `hosts_probed`, `hosts_skipped_unauthorised`,
`ports_probed`, `open_ports_observed`. Per-probe event `smb_sweep_probe`.

> `_probe_ntlm` is marked `pragma: no cover` (opt-in, untested). The local
> `from ..safety import SafetyError` re-import inside `run()` is to avoid a
> top-level import cycle.

---

#### 5. `bulk_https_exfil` (NDR)

**Attack shape.** Streams configurable-size random payloads to an HTTPS endpoint
to trip anomalous-bytes-out detectors. Hard ceiling **16 GiB** per run; can split
across N requests.

**Safety gating.** `ctx.authorise(hostname)` only. `verify=False`.

| Param | Type / default | Bounds |
|-------|----------------|--------|
| `target_url` | str (required) | http/https + hostname |
| `total_bytes` | int = 512 MiB | 1 – 16 GiB |
| `chunk_bytes` | int = 1 MiB | 1 – 64 MiB |
| `method` | str = "POST" | POST/PUT |
| `request_timeout` | float = 300.0 | 1–3600 |
| `request_count` | int = 1 | 1–64 |

**Output.** `detail`: `requests_completed`, `total_bytes_sent`, `target_url`.
Per-request event `bulk_exfil_request`.

> Inefficiency: `_random_chunks` builds each chunk with a Python-level
> `rng.getrandbits(8)` per byte — at multi-GiB sizes this is extremely slow
> (gap EAL-G10). Same per-byte pattern appears in c2/dns body generation.

---

#### 6. `ftp_egress` (NDR)

**Attack shape.** Real FTP control session walking the cleartext command
sequence so NGFW FTP App-ID, clear-text-credential EALs, and outbound-file-
transfer detections fire. Optional passive-mode STOR data channel.

**Safety gating.** `ctx.authorise(target_host)` only (port not gated). **CRLF /
NUL / control-byte injection is strictly rejected** in `username`, `password`,
`banner_agent` (`_clean_field`) because FTP is CRLF-framed — this prevents
smuggling extra protocol commands. Default credentials are sentinels
(`cortexsim` / `cortexsim-lab`) so any DLP match is attributable. **No filesystem
read** — STOR payload is a synthesised printable-ASCII buffer, so secrets cannot
leak.

| Param | Type / default | Bounds |
|-------|----------------|--------|
| `target_host` | str (required) | non-empty |
| `target_port` | int = 21 | 1–65535 |
| `username` | str = "cortexsim" | ≤64 chars, no control bytes |
| `password` | str = "cortexsim-lab" | ≤128 chars, no control bytes |
| `iterations` | int = 3 | 1–100 |
| `sleep_seconds` | float = 10.0 | 0–600 |
| `connect_timeout` | float = 5.0 | 0.5–30 |
| `idle_seconds` | float = 1.5 | 0–60 |
| `send_stor` | bool = True | — |
| `stor_bytes` | int = 4096 | 64 – 1 MiB |
| `banner_agent` | str = sentinel | ≤64 chars, no control bytes |

**Output.** `detail`: `sessions_completed`, `target`, `stor_enabled`.
Per-session event `ftp_session` (surfaces the username in the audit trail).

> The username appears in cleartext in the ECS audit event `extra.username`.
> Acceptable because it's a sentinel by default, but an operator override is
> logged in the clear (gap EAL-G11, low).

---

#### 7. `ssh_egress` (NDR)

**Attack shape.** Outbound SSH banner exchange + synthesised, parser-valid
`SSH_MSG_KEXINIT` packet (RFC-4253 §6/§7.1, 8-byte-aligned padding). Deliberately
stops **before** any key exchange — "shape, not substance". No password/key
material ever leaves.

**Safety gating.** `ctx.authorise(target_host)` only. `client_banner` must start
`SSH-2.0-`/`SSH-1.99-`, ≤253 chars, CRLF/NUL/control-byte scrubbed.

| Param | Type / default | Bounds |
|-------|----------------|--------|
| `target_host` | str (required) | non-empty |
| `target_port` | int = 22 | 1–65535 |
| `client_banner` | str = "SSH-2.0-CortexSim_eal_1.0" | RFC-validated |
| `iterations` | int = 3 | 1–200 |
| `sleep_seconds` | float = 15.0 | 0–600 |
| `connect_timeout` | float = 5.0 | 0.5–30 |
| `idle_seconds` | float = 2.0 | 0–60 |
| `send_kexinit` | bool = True | — |

**Output.** `detail`: `sessions_completed`, `target`, `client_banner`.
Per-session event `ssh_session`.

---

#### 8. `idp_signin_emulator` (ITDR)

**Attack shape.** Builds shape-true IdP audit events (Okta system-log,
Microsoft Entra signInLogs, Google Workspace login-activity subsets) and POSTs
them as JSON to an **operator-supplied collector URL** — never a real tenant.
Five behavioural presets, each producing a burst:

| `event_pattern` | What it emits |
|-----------------|---------------|
| `impossible_travel` | 2 successful sign-ins from geographically distant IPs (us-west then apac-east) |
| `mfa_fatigue` | `burst_count-1` failed MFA push attempts + 1 approval (push-bombing) |
| `credential_stuffing` | `burst_count` failed logins across `user000..N` from one source IP (sa-east) |
| `token_replay` | a session-start + token reuse from a different geo/UA with shared `session_token_id` |
| `brute_force_lockout` | `burst_count` failures + 1 `user.account.lock` event |

Source locations are 5 canned `_SourceLocation` entries (us-west, eu-central,
apac-east, africa-south, sa-east) using documentation-range IPs (203.0.113.x,
198.51.100.x, 192.0.2.x). Default target user is `*.invalid` canary.

**Safety gating.** `ctx.authorise(collector_url-host)` only. Each request gets a
per-request `x-simulation-run-id`. Body carries `cortexsim_run_id`. `verify=False`.

| Param | Type / default | Bounds |
|-------|----------------|--------|
| `collector_url` | str (required) | http/https + hostname |
| `provider` | str = "okta" | okta/microsoft/google |
| `event_pattern` | str = "impossible_travel" | the 5 presets |
| `target_user` | str = `ada.lovelace@cortexsim-canary.invalid` | must contain `@` |
| `iterations` | int = 1 | 1–200 |
| `sleep_seconds` | float = 0.0 | 0–600 |
| `request_timeout` | float = 15.0 | 1–300 |
| `burst_count` | int = 8 | 2–200 |
| `user_agent` | str? = None | optional override |

**Output.** `detail`: `provider`, `event_pattern`, `iterations_completed`,
`events_posted`, `response_status_counts`, `target`, `target_user`. Per-event
`idp_signin_emulator_event`.

---

#### 9. `oauth_grant_emulator` (Cloud App Security / CASB)

**Attack shape.** Sends OAuth 2.0 authorize-request GETs (RFC 6749 §4.1.1) to
real public IdP authorize endpoints with **bogus client_ids** and **planted
risky scopes**. The endpoint 4xx's on the fake client_id; detection is on the
**outbound request shape** at the proxy, not the response.

| `scope_preset` | Intent |
|----------------|--------|
| `benign` | openid/email/profile — control |
| `risky_drive` | drive / Files.ReadWrite.All — should fire CASB |
| `admin_consent` | admin-consent-required (Directory.ReadWrite.All, okta.users.manage) |
| `full_mailbox` | Mail.ReadWrite + offline_access — token-replay risk |

Per-provider fake client_ids are obvious canaries (e.g.
`0oaCORTEXSIMCANARYNOTREALCLIENT`). A marker query param `x_cortexsim_run_id`
appears in the URL so NGFW URL logs are filterable. `redirect_uri` defaults to
`https://cortexsim-canary.invalid/oauth/callback`.

**Safety gating.** `ctx.authorise(provider-host)` only. `verify=False`. **No real
OAuth client secrets are ever used.**

| Param | Type / default | Bounds |
|-------|----------------|--------|
| `provider` | str (required) | okta/microsoft/google |
| `scope_preset` | str = "risky_drive" | the 4 presets |
| `iterations` | int = 1 | 1–200 |
| `sleep_seconds` | float = 0.0 | 0–600 |
| `request_timeout` | float = 15.0 | 1–300 |
| `redirect_uri` | str = canary.invalid | http/https + hostname |
| `okta_tenant` | str? = None | okta only; default `cortexsim-canary` |
| `user_agent` | str? = None | optional override |

**Output.** `detail`: `provider`, `scope_preset`, `iterations_completed`,
`response_status_counts`, `target`. Per-request `oauth_grant_emulator_request`.

> `redirect_uri` validator only checks scheme+hostname; the docstring claims it
> "must contain the canary marker" but **no such check exists** — an operator
> can point `redirect_uri` at any http(s) URL (gap EAL-G03).

---

#### 10. `llm_provider_egress` (AI Access Security)

**Attack shape.** Authentic-shape POSTs to OpenAI (`/v1/chat/completions`),
Anthropic (`/v1/messages`), or Gemini (`/v1beta/.../generateContent?key=`)
carrying a planted DLP payload. Provider 401/403's on the fake key; detection is
at the proxy on the outbound request.

| `payload_type` | Planted marker |
|----------------|----------------|
| `benign` | generic refactor request (control) |
| `pii` | synthetic SSN block + fake card number, all `CORTEXSIMCANARY`-tagged |
| `secret` | `AKIA0000CORTEXSIMCANARY` AWS key + DB connection string |
| `source` | "proprietary" Python source snippet (`CORTEXSIM-CANARY` header) |
| `jailbreak` | DAN-style jailbreak frame |

`paste_padding_kb` pads the body with benign lorem-ipsum filler (no DLP markers)
to cross anomalous-size thresholds. Fake keys are obvious sentinels
(`sk-cortexsim-canary-NOT-A-REAL-KEY`, etc.).

**Safety gating.** `ctx.authorise(provider-host)` only. `verify=False`. **No real
provider keys are ever used.**

| Param | Type / default | Bounds |
|-------|----------------|--------|
| `provider` | str (required) | openai/anthropic/gemini |
| `payload_type` | str = "benign" | the 5 types |
| `iterations` | int = 1 | 1–200 |
| `sleep_seconds` | float = 0.0 | 0–600 |
| `paste_padding_kb` | int = 0 | 0–1024 |
| `request_timeout` | float = 15.0 | 1–300 |
| `user_agent` | str? = None | optional override |

**Output.** `detail`: `provider`, `payload_type`, `iterations_completed`,
`response_status_counts`, `target`. Per-request `llm_provider_egress_request`.

> The default Anthropic model string is `claude-3-5-sonnet-20241022` and OpenAI
> `gpt-4o` — these are just body fields the provider 401's on, not real calls,
> but they will drift over time (gap EAL-G12, low).

---

#### 11. `airs_prompt_attack` (AIRS) — Probe→Mutator→Target→Scorer driver

**Attack shape.** The first "attacker-shells-out" plugin. It
`asyncio.create_subprocess_exec`s the `cortex-prompt-attacker` CLI
(`<bin> run --probes <dir> --target-url <url> --iterations N --timeout T --out -`,
plus `--mutators`, `--scorers`, `--header k=v`, `--insecure`), streams the CLI's
line-buffered JSONL on stdout, and forwards every record into the ECS audit
pipeline. Records with `entry_type=="run_meta"` map to a run-started event; all
others are probe attempts. Outcomes tallied: `vuln` / `clean` / other (error).

**The Probe → Mutator → Target → Scorer pipeline** (lives in
`sources/cortex-prompt-attacker/`, design brief in
`docs/eal-simulator/research-dvllm-prompt-attacker.md`):

- **Probe** — a promptmap-compatible YAML (schema only, no GPL imports) under
  `scenarios/airs/probes/` describing an OWASP LLM01–10 attack family. A probe
  declares its `primary_scorer` as a class attribute (garak's Probe→Detector
  contract, mirrored).
- **Mutator** — a PyRIT-shape converter chain (`PromptConverter` analogue) that
  transforms the seed prompt (e.g. base64, role-play framing). Default chain is
  the `mutators` param; probes can override.
- **Target** — the AIRS validation target the CLI POSTs to, typically the
  in-tree `cortex-vulnerable-llm` Flask app (one blueprint per OWASP LLM01–10,
  each backed by a deterministic regex canary — no real LLM, no API keys).
- **Scorer** — a garak-`detector`-shape rule (base `Scorer` class, probes name
  their `primary_scorer`) that inspects the target's response for the canary and
  marks the attempt `vuln`/`clean`. Output is a garak-`Attempt`-shaped JSONL
  record (`entry_type`, `uuid`, `seq`, `status`, `probe_classname`, `prompt`,
  `outputs`, `detector_results`, etc.) so SOC tooling reading garak logs can
  read these too.

The plugin translates JSONL→ECS via `cortex_prompt_attacker.events`
(`run_meta_to_ecs`, `attempt_to_ecs`) when that package is importable, else a
`_fallback_event` wrapping the raw record.

**Safety gating.** `ctx.authorise(target_url-host)`. If the binary is not found
on PATH, returns `status=error` (does not crash). Subprocess killed on
`timeout_seconds`.

| Param | Type / default | Bounds |
|-------|----------------|--------|
| `target_url` | str (required) | http/https + hostname |
| `probes_dir` | str (required) | dir or comma-sep paths |
| `mutators` | list[str] = [] | optional default chain |
| `scorers` | list[str] = [] | optional default list |
| `iterations` | int = 1 | 1–200 |
| `timeout_seconds` | float = 120.0 | 1–3600 |
| `request_timeout` | float = 30.0 | 1–300 |
| `extra_headers` | dict = {} | passed as `--header k=v` |
| `insecure_tls` | bool = False | adds `--insecure` |
| `binary` | str? = None | override binary path |

**Output.** `detail`: `binary`, `exit_code`, `attempts_run`, `vuln_count`,
`clean_count`, `error_count`, `summary` (parsed from stderr's last JSON line),
`target_url`. `status` is `success` iff exit code 0.

> Internal wart: the `stats=lambda kind: None` argument to `_consume_stdout` is
> dead — counts are actually stashed on the ctx via `setattr` (`_airs_*`
> attributes). Brittle ctx-monkeypatching pattern shared with browser plugin
> (gap EAL-G08).

---

#### 12. `browser_attack_runner` (BROWSER / Prisma)

**Attack shape.** Same shell-out pattern as airs. Drives `cortex-browser-attacker`
(Playwright + Chromium / Prisma Browser) through a YAML-declared sequence of
browser actions (navigate, paste, copy, click, download, install_extension,
screenshot). CLI invoked `<bin> run --campaign <yaml> --browser-channel <ch>
--out - [--headless|--no-headless] --live`. Customer's Prisma Browser tenant
forwards its own telemetry to XSIAM; this plugin only **produces the activity**.

**Safety gating.** `ctx.authorise(allowlist_host)`. Fails fast (status=error) if
`campaign_path` doesn't exist or binary not on PATH. `campaign_path` validator
rejects null bytes. The browser action campaign YAML is **double-gated** — its
own Pydantic validator refuses `--live` without an authorisation block.
`browser_channel=stub` exists for hermetic unit tests (StubDriver, no real
browser).

| Param | Type / default | Bounds |
|-------|----------------|--------|
| `campaign_path` | str (required) | non-empty, no null bytes |
| `allowlist_host` | str (required) | authorised host |
| `browser_channel` | str = "chromium" | prisma/chromium/stub |
| `headless` | bool = True | — |
| `timeout_seconds` | float = 600.0 | 1–7200 |
| `binary` | str? = None | override binary path |

**Output.** `detail`: `binary`, `exit_code`, `actions_run`, `success_count`,
`blocked_count`, `failure_count`, `summary`, `campaign_path`, `browser_channel`.
JSONL→ECS via `cortex_browser_attacker.events` (`run_meta_to_ecs`,
`action_result_to_ecs`) or `_fallback_event`.

---

#### 13. `agentic_egress` (KOI / supply-chain)

**Attack shape.** Emulates an agentic-AI consumer client fetching a malicious
supply-chain artifact. Resolves a component directory in the in-tree
`sources/cortex-malicious-agentic-pack/`, **tarballs it at request time**, and
POSTs the bytes (real clients GET; this always POSTs so the NGFW DLP/SCA layer
inspects the body). `pypi_mirror` additionally does a GET index-probe first
(`pip download` shape). Per-component User-Agent fingerprints make the egress
look like the real client (npm/pip/VSCode/Chrome/claude-desktop).

| `component` | subdir | User-Agent | suffix |
|-------------|--------|-----------|--------|
| `mcp_server` | mcp/ | claude-desktop/0.7.0 mcp-client/0.1 | .tar.gz |
| `mcp_package` | mcp/ | npm/10.5.0 node/v22.0.0 ... | .tgz |
| `pypi_mirror` | pypi/ | pip/24.0 {impl} python/{ver} | .tar.gz |
| `claude_skill` | claude-skills/ | claude-desktop/0.7.0 skills/0.1 | .skill |
| `vscode_ext` | vscode/ | VSCode/1.85.0 (vsx-fetch) | .vsix |
| `chrome_ext` | chrome/ | Chrome/120.0.0.0 (extension-installer) | .crx |

**Safety gating.** `ctx.authorise(target_url-host)`. `artifact_name` validated
against `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` (no path traversal); the resolved
artifact dir is checked to stay **inside** the component directory
(`_resolve_artifact_dir` refuses escapes). Pack location resolved via explicit
override → `CORTEXSIM_BASE_DIR` env → walk-up search. The pack's own side effects
are gated on `CORTEXSIM_C2_URL` (per CLAUDE.md) so static scanning is safe.
`verify=False`.

| Param | Type / default | Bounds |
|-------|----------------|--------|
| `target_url` | str (required) | http/https + hostname |
| `component` | str (required) | the 6 components |
| `artifact_name` | str (required) | regex-validated subdir |
| `iterations` | int = 1 | 1–50 |
| `sleep_seconds` | float = 0.0 | 0–600 |
| `request_timeout` | float = 15.0 | 1–300 |
| `pack_root` | str? = None | override pack path |

**Output.** `detail`: `component`, `artifact`, `iterations_completed`, `target`.
Events `agentic_egress_index_probe` (pypi only) + `agentic_egress_artifact_fetch`.

> Minor bug: `_send_one` computes `sep = "&" if "?" in params.target_url else "?"`
> but for non-pypi components `sep` is computed and unused (the index-probe is
> the only consumer). Cosmetic (gap EAL-G13, low).

---

## 3. Safety model — exhaustive

The simulator emits **real** traffic indistinguishable from malicious activity
at the wire. The defenses, in layers:

### Layer 1 — Pydantic campaign validation (`campaign.py`)
- `campaign_id` must match `CMP-{LABEL}-{NNN}`; `step_id` must match `step-NN`;
  step IDs unique; at least one step.
- `model_validator` `_validate_authorisation_block`: if `dry_run=False`, then
  `simulation_authorized` must be true, `authorized_by` must be non-empty, and
  `target_allowlist` must be non-empty — **enforced at parse time**, so an
  unsafe live campaign cannot even deserialize.

### Layer 2 — API pre-flight (`api/eal.py launch_campaign`)
- Re-runs `SafetyPolicy(...).assert_campaign_authorized()` **synchronously**
  before scheduling the background task, returning a 422 `SAFETY_VIOLATION`
  with a useful message rather than failing silently in the background.
- Campaign creation (`POST /campaigns`) rejects unknown plugins / invalid params
  (422) and duplicate campaign IDs (409).

### Layer 3 — Executor campaign gate (`executor.py`)
- Constructs the `SafetyPolicy` from the campaign and calls
  `assert_campaign_authorized()`. On `SafetyError`, the campaign status →
  `failed` with `safety_violation: ...`, emits a `campaign_refused` ECS event
  (category `iam`, type `denied`), and **no steps run**.

### Layer 4 — `SafetyPolicy.assert_campaign_authorized` (`safety.py`)
- Dry-run → returns immediately (no checks; dry-runs emit no packets).
- Live → requires `simulation_authorized`, non-empty `authorized_by`, non-empty
  `target_allowlist`, else `SafetyError`.

### Layer 5 — Per-target gate `SafetyPolicy.authorise(target)` (called by plugins)
- Dry-run → always allowed (no packet).
- Allowlist tokens pre-parsed at construction into CIDRs (IPv4 → /32, IPv6 →
  /128 to avoid silently widening an IPv6 literal) and hostnames.
- IP target → must fall inside an authorised CIDR.
- Hostname target → suffix match (`testmynids.org` admits `foo.testmynids.org`),
  validated against `_HOSTNAME_RE`.
- Port suffix stripped before matching (handles `host:port` and `[v6]:port`).
- Anything not matched → `SafetyError`.

### Layer 6 — Per-plugin input hardening
- FTP/SSH: CRLF/NUL/control-byte scrubbing on user-supplied protocol fields
  (anti command-injection on CRLF-framed protocols).
- agentic_egress: regex + path-containment guard against directory traversal.
- stratum: wallet charset restriction.
- All credential/key material defaults to obvious **canary sentinels**
  (`*-CORTEXSIM-CANARY-*`, `*.invalid`), so any downstream DLP hit is
  attributable to the simulator and no real secret can leak.
- Exfil plugins generate random payloads in-process and never read the
  filesystem, so **no real customer data can be exfiltrated**.

### Layer 7 — Attribution / SOC filtering
- Every HTTP request carries `X-Simulation-Run-ID`, `X-Simulation-Campaign-ID`,
  `X-Simulation-Source`. Many plugins also inject a per-request
  `x-simulation-run-id` and a `cortexsim_run_id` / `x_cortexsim_run_id` body or
  query marker. Socket/DNS plugins tag the same ids in the ECS audit namespace.

### What this does NOT stop (weaponization caveats)
- **`target_allowlist` is operator-supplied** — a malicious operator can put any
  host in it. Safety is "no accidental fan-out / no typo'd beacon to a third
  party," not "cannot be aimed." This is by design (the simulator is an
  authorised DC tool), but worth stating.
- **`verify=False` everywhere** — every httpx client disables TLS verification.
  Justified for NGFW MitM, but means the simulator will happily talk to a
  TLS-MitM that isn't the customer's NGFW (gap EAL-G05).
- **No rate-limit / global byte budget** beyond per-plugin bounds — `bulk_https_exfil`
  alone permits 16 GiB; nothing caps aggregate campaign volume (gap EAL-G06).
- **The CLAUDE.md launch-consent gate (`consent.simulation_authorized` /
  `c2_authorized`) is a *scenario/tool-adapter* concept** (`core/api/runs.py`,
  `core/engine/orchestrator.py`, `core/tools/adapter_catalog.py`), **NOT** part
  of the EAL campaign path. EAL campaigns have their own `simulation_authorized`
  field but **no `c2_authorized` notion** — the two consent models are separate
  and not cross-wired (gap EAL-G01).

---

## 4. Test coverage map

| Plugin | Dedicated test module | In shared `test_plugins.py` dry-run param matrix |
|--------|----------------------|--------------------------------------------------|
| c2_http_beacon | — | ✅ |
| dns_tunnel_exfil | — | ✅ |
| stratum_tcp_connect | — | ✅ |
| smb_rpc_sweep | — | ✅ |
| bulk_https_exfil | — | ✅ |
| ftp_egress | ✅ `test_plugin_ftp_egress.py` | ❌ |
| ssh_egress | ✅ `test_plugin_ssh_egress.py` | ❌ |
| idp_signin_emulator | ✅ `test_plugin_idp_signin_emulator.py` | ❌ |
| oauth_grant_emulator | ✅ `test_plugin_oauth_grant_emulator.py` | ❌ |
| llm_provider_egress | ✅ `test_plugin_llm_provider_egress.py` | ❌ |
| airs_prompt_attack | ✅ `test_plugin_airs_prompt_attack.py` | ❌ |
| browser_attack_runner | ✅ `test_plugin_browser_attack_runner.py` | ❌ |
| agentic_egress | ✅ `test_plugin_agentic_egress.py` | ❌ |

**Every plugin has at least one test.** Gaps: the shared dry-run matrix in
`test_plugins.py` only covers the 5 original NDR plugins; the 8 newer plugins
have dedicated modules but are **absent from the central parametrized matrix**,
so a regression in the executor/dry-run contract would only be caught for the
5 NDR plugins (gap EAL-G04). The plugin-development doc instructs authors to
"add a row to `test_plugins.py`," which the 8 newer plugins did not do.

Untested code paths flagged `pragma: no cover`: `smb_rpc_sweep._probe_ntlm`
(impacket null session), registry import-failure branches, the
`cortex_prompt_attacker.events` / `cortex_browser_attacker.events` import-present
path (tests exercise the `_fallback_event` path because those packages are not
on the test sys.path).

---

## 5. Plane ↔ EAL plugin coverage

| Detection plane | EAL plugin(s) | Covered? |
|-----------------|---------------|----------|
| NDR | c2_http_beacon, dns_tunnel_exfil, stratum_tcp_connect, smb_rpc_sweep, bulk_https_exfil, ftp_egress, ssh_egress | ✅ (7) |
| ITDR | idp_signin_emulator | ✅ (1) |
| Cloud App / CASB | oauth_grant_emulator | ✅ (1) |
| AI Access | llm_provider_egress | ✅ (1) |
| AIRS | airs_prompt_attack | ✅ (1) |
| BROWSER (Prisma) | browser_attack_runner | ✅ (1) |
| KOI (supply-chain) | agentic_egress | ✅ (1) |
| **EDR** | — | ❌ no EAL plugin (EDR scenarios use agent/signalbench, not the EAL path) |
| **CDR** (Cloud / Compute) | — | ❌ no EAL plugin |
| **CSPM** | — | ❌ IaC-only, no EAL plugin |
| **ASM** | — | ❌ IaC-only, no EAL plugin |
| **TIM** | — | ❌ IaC-only (mocktaxii), no EAL plugin |
| **Analytics / multi-plane stitching** | — | ❌ relies on composing other plugins in one campaign; no dedicated stitching plugin |

The EAL simulator is fundamentally a **network/identity egress** engine, so the
absence of EDR/CDR/CSPM/ASM/TIM EAL plugins is architectural, not a bug — those
planes are served by the agent harness, signalbench, and IaC modules. But it
means a "single launch surface for all planes" does not exist (gap EAL-G14).

---

## 6. Cross-references

- **Scenarios** referencing EAL plugins live under `scenarios/{ndr,itdr,cloud_app,
  ai_access,airs,browser,koi}/` (38 files match an EAL plugin name). AIRS probe
  pack: `scenarios/airs/probes/`.
- **Companion in-tree tools** driven by the shell-out plugins:
  `sources/cortex-prompt-attacker/` (airs), `sources/cortex-browser-attacker/`
  (browser), `sources/cortex-malicious-agentic-pack/` (agentic),
  `sources/cortex-vulnerable-llm/` (airs target).
- **Design brief** for the AIRS pipeline: `docs/eal-simulator/research-dvllm-prompt-attacker.md`.
- **Tool adapter framework** (separate consent model): `docs/tool-adapters.md`,
  `core/tools/adapter_catalog.py`, `core/api/runs.py`.
- **Other EAL docs:** `docs/eal-simulator/architecture.md`,
  `docs/eal-simulator/runbook.md`, `docs/eal-simulator/plugin-development.md`.

---

## 7. Gap register (see structured output for severities)

- **EAL-G01** — CLAUDE.md describes a `consent.c2_authorized` launch gate; the
  EAL campaign path has no `c2_authorized` notion. Two separate consent models,
  not cross-wired. Docs imply otherwise.
- **EAL-G02** — `stratum_tcp_connect` docstring claims port-level allowlisting;
  the code authorises host only. Same host-only-not-port pattern in ftp/ssh.
- **EAL-G03** — `oauth_grant_emulator.redirect_uri` docstring claims a required
  canary marker; validator only checks scheme+host.
- **EAL-G04** — 8 of 13 plugins are absent from the central `test_plugins.py`
  dry-run regression matrix (contradicts plugin-development.md guidance).
- **EAL-G05** — `verify=False` on every httpx client; no opt-in TLS verify.
- **EAL-G06** — no aggregate campaign byte/rate budget (16 GiB per bulk run).
- **EAL-G07** — `ctx.authorise` is runtime-`setattr`'d, not on the dataclass;
  docs show `ctx.authorise(...)` which static checkers can't resolve.
- **EAL-G08** — airs/browser plugins stash counters via `setattr` on the ctx
  (`_airs_*`, `_browser_*`); brittle. airs has a dead `stats=lambda` arg.
- **EAL-G09** — `_COMMON_STRATUM_PORTS` lists `14433` twice (cosmetic).
- **EAL-G10** — per-byte `rng.getrandbits(8)` payload generation in
  bulk/c2/dns is pathologically slow at large sizes.
- **EAL-G11** — ftp_egress logs the (overridable) username in cleartext in the
  ECS audit event.
- **EAL-G12** — hardcoded provider model strings (`gpt-4o`,
  `claude-3-5-sonnet-20241022`) will drift.
- **EAL-G13** — unused `sep` variable for non-pypi components in agentic_egress.
- **EAL-G14** — no EAL plugins for EDR/CDR/CSPM/ASM/TIM/Analytics planes
  (architectural; documented here so it isn't mistaken for incomplete content).
- **EAL-G15** — `docs/eal-simulator/plugin-catalog.md` referenced by
  plugin-development.md does not exist; **this doc** is now the canonical
  catalog (the development doc should point here).
- **EAL-G16** — architecture.md / plugin-development.md document only the 5
  original NDR plugins in prose; the 8 newer plugins are never enumerated in the
  EAL docs (only in CLAUDE.md plane table). Stale docs.
