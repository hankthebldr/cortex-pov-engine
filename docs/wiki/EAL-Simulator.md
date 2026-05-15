# EAL Traffic Simulator

A plugin-based subsystem under `core/eal_simulator/` that emits
controlled network telemetry to validate Palo Alto Networks NGFW
**Enhanced Application Logs** and Cortex XDR / XSIAM NDR analytics.

## Plugin catalog (11 built-ins)

| Plugin | Purpose | EAL targets |
|---|---|---|
| `c2_http_beacon` | Periodic HTTP/S beacon | Unusual UA, periodic beaconing, DGA URI |
| `dns_tunnel_exfil` | DNS-tunnel exfiltration | DNS tunnelling, anomalous volume, high-entropy labels |
| `bulk_https_exfil` | Large outbound transfer | Anomalous data transfer size |
| `stratum_tcp_connect` | Cryptojacking JSON-RPC | Cryptojacking App-ID |
| `smb_rpc_sweep` | Lateral SMB / RPC sweep | Host sweeping, anomalous SMB / RPC |
| `airs_prompt_attack` | AIRS validation runner | AIRS prompt-injection / tool-abuse / RAG / DoS |
| `llm_provider_egress` | AI Access — outbound to public AI providers | AI Access — generative-AI App-ID, DLP secret/PII, jailbreak fingerprint |
| `agentic_egress` | KOI — agentic supply-chain artifact fetch | KOI — typosquat package fetch, extension marketplace risk, agentic skill fetch with hidden injection |
| `browser_attack_runner` | BROWSER — Playwright-driven Prisma Browser attacks | Credential paste, drive-by download, risky extension install, copy-paste DLP, screen capture |
| `oauth_grant_emulator` | CLOUD_APP — outbound OAuth authorize requests carrying risky scopes | CASB risky-scope grant, admin-consent-required grant, full-mailbox + offline_access, NGFW EAL `/authorize` App-ID |
| `idp_signin_emulator` | ITDR — synthetic IdP audit-log events to a collector URL | Impossible travel, MFA fatigue, credential stuffing, token replay, brute-force lockout |

## Campaign model

Operators describe an attack narrative as an ordered list of plugin
invocations bound to a single `Campaign`. The schema is Pydantic-native
so it round-trips cleanly through JSON, YAML, and the FastAPI request
body.

```yaml
campaign_id: CMP-NDR-001
name: NDR validation — C2 beacon + DNS exfil
authorized_by: hank@paloaltonetworks.com
simulation_authorized: true
target_allowlist:
  - testmynids.org
  - 10.0.0.0/24
dry_run: false
steps:
  - step_id: step-01
    plugin: c2_http_beacon
    params:
      target_url: http://testmynids.org/uid/index.html
      iterations: 10
      sleep_seconds: 30
  - step_id: step-02
    plugin: dns_tunnel_exfil
    params:
      base_domain: testmynids.org
      chunks: 20
```

## CLI

```bash
# List plugins
python -m scripts.eal_simulator.cli list-plugins | jq .

# Describe one
python -m scripts.eal_simulator.cli describe c2_http_beacon

# Run a campaign
python -m scripts.eal_simulator.cli run path/to/campaign.yml --live
```

Add `--live` to flip `dry_run=false` (the spec must also declare
`simulation_authorized: true` and a non-empty `target_allowlist`).

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/eal/plugins` | list registered plugins |
| `GET` | `/api/eal/plugins/{name}` | plugin metadata + JSON schema |
| `POST` | `/api/eal/campaigns` | persist a campaign definition |
| `GET` | `/api/eal/campaigns` | list persisted campaigns |
| `GET` | `/api/eal/campaigns/{id}` | single campaign detail |
| `POST` | `/api/eal/campaigns/{id}/launch` | launch (dry-run by default) |
| `GET` | `/api/eal/runs` | list executed runs |
| `GET` | `/api/eal/runs/{run_id}` | single run detail |

Long-running campaigns execute in the background via FastAPI
BackgroundTasks and update the `EalCampaignRun` row when complete.
Operators poll `GET /api/eal/runs/{run_id}` to track progress.

## Audit pipeline

Every campaign emits ECS-shaped JSON events through `AuditLogger`:

- `campaign_started` / `campaign_finished` / `campaign_refused`
- `step_started` / `step_finished`
- per-plugin events (`c2_beacon_request`, `dns_tunnel_query`,
  `stratum_session`, `smb_sweep_probe`, `airs_probe_attempt`,
  `llm_provider_egress_request`, `agentic_egress_artifact_fetch`, …)

Output sinks are configurable: stdout (default), Python logger, file.
Every HTTP request also injects:

```
X-Simulation-Run-ID: cortexsim-<uuid>-i<iteration>
X-Simulation-Campaign-ID: CMP-XXX-NNN
X-Simulation-Source: cortexsim-eal-simulator/1.0
```

so SOC analysts can filter simulator traffic out of incident reviews.

## Helm chart

`deploy/helm/eal-simulator/` deploys:

- `<release>-api` — FastAPI gateway pods (replicaCount 2)
- `<release>-worker` — worker pods scheduled onto nodes routed through
  the customer NGFW
  (`nodeSelector: cortexsim.paloaltonetworks.com/role=simulator`)
- `<release>-redis` — optional broker (off by default)
- Tailscale sidecar — recommended; never expose the API to public
  internet

HPA scales workers 1–5 by Redis queue depth.

## See also

- [[Plugin Development]] — adding a new plugin
- [[Architecture]] — three-tier design + plugin model
- [`docs/eal-simulator/architecture.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/docs/eal-simulator/architecture.md)
- [`docs/eal-simulator/runbook.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/docs/eal-simulator/runbook.md)
