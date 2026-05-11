# POV Runbook

A day-by-day playbook for a Cortex Domain Consultant running a
proof-of-value engagement with CortexSim.

> This is a living doc — extend it as you learn what works in real
> customer engagements.

## Day 0 — pre-engagement

**Prepare on your laptop, not the customer's jumpbox.**

1. Clone the repo, build the install bundle:
   ```bash
   git clone https://github.com/hankthebldr/cortex-pov-engine.git
   cd cortex-pov-engine
   ./install.sh   # sets up the docker compose stack on your laptop
   ```
2. Pull the latest container image:
   ```bash
   docker pull ghcr.io/hankthebldr/cortexsim:latest
   ```
3. Confirm the customer's POV scope:
   - Which Cortex products?
     XDR / XSIAM / AIRS / AI Access / Prisma Browser / KOI / Cortex Cloud / ITDR
   - Which UC/TCs are in scope?
   - Which detection planes do they care about?
   - What's the lab topology — on-prem K3s, cloud sandbox, or
     customer's existing dev tenant?
4. Map the UC/TC list against the [[Detection Planes]] table and the
   `scenarios/` library. Gaps → prep custom scenarios up front.
5. **Get the safety paperwork**. Every live campaign requires
   `simulation_authorized: true` + `authorized_by` + a non-empty
   `target_allowlist`. Confirm in writing what hosts the customer
   authorises traffic to.

## Day 1 — stand-up

1. SSH to the jumpbox (or laptop in lab mode), bring up SimCore:
   ```bash
   docker compose up -d --build
   docker compose logs -f simcore
   ```
2. Confirm health:
   ```bash
   curl http://localhost:8888/api/health
   curl http://localhost:8888/api/eal/plugins | jq '.plugins[].name'
   ```
3. Browse to the React UI on `http://localhost:8888/` — confirm the
   customer can see the scenario library + plane selector.
4. Walk through one safe scenario together — pick one that
   exercises the customer's primary plane. Mark every expected
   detection in XSIAM as you go.

## Day 2 — coverage runs

For each plane in scope:

1. **Browse** the plane's scenarios in the UI.
2. **Run** scenarios in dry-run first (`dry_run: true` in the campaign
   YAML, or the UI's dry-run toggle once it ships in Phase 7).
3. **Re-run live** with the customer's pre-approved
   `target_allowlist`.
4. **Validate** each expected detection in XSIAM:
   - Use the XQL templates the scenario provides.
   - Mark `observed_at` via `PUT /api/results/{id}/validate` (or the
     UI wizard once it ships).
   - Note MTTD per detection.

The four AI/Browser/Agentic planes (`AI_ACCESS`, `AIRS`, `BROWSER`,
`KOI`) get their own dedicated walkthrough — see [[AIRS Validation]],
[[KOI Validation]], etc.

## Day 3+ — iterate

- **Gap remediation** — for every expected detection that did **not**
  fire, recommend a Cortex Marketplace pack and re-run.
- **Custom scenarios** — author a YAML for any customer-specific
  TTP not covered by the library. See [[Scenario Authoring]].
- **Tune false positives** — if a detection fires on benign traffic,
  capture a baseline run alongside the attack run for the report.

## Day N — POV report

1. Generate the per-run markdown report:
   ```bash
   curl -fsSL "http://localhost:8888/api/runs/<run_id>/report?format=markdown" \
     > pov-run-<run_id>.md
   ```
2. (Phase 8) generate the Cortex-branded PDF:
   ```bash
   curl -fsSLO "http://localhost:8888/api/runs/<run_id>/report.pdf"
   ```
3. Hand the customer:
   - One report PDF per scenario
   - The MTTD heatmap
   - The gap-callout list with Marketplace remediations
   - The signed authorisation + target_allowlist used

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `safety_violation: simulation_authorized must be true` | Live run without auth block | Set `simulation_authorized: true` and `authorized_by` |
| `Target ... not in allowlist` mid-run | Plugin tried a host not declared up front | Add the host/CIDR to `target_allowlist` |
| HTTP plugin reports 502 / TLS error | NGFW SSL decryption not configured | Enable Forward-Proxy decryption on the rule covering simulator traffic |
| No NGFW EAL fires despite traffic | Log forwarding profile not attached | Attach the EAL log forwarding profile and re-run |
| Unknown plugin error in the executor | Plugin registry didn't pick it up | Confirm the plugin file is under `core/eal_simulator/plugins/`; restart SimCore |

## Filtering simulator traffic in XSIAM

Every HTTP request injects:

```
X-Simulation-Run-ID: cortexsim-<uuid>-i<iteration>
X-Simulation-Campaign-ID: CMP-<plane>-<NNN>
X-Simulation-Source: cortexsim-eal-simulator/1.0
```

For DNS / TCP plugins, audit lines are tagged with the same
identifiers in the `cortexsim` ECS namespace.

XQL example to scope a debrief:

```
dataset = panw_ngfw_traffic_raw
| filter http_request_method = "POST"
| filter "x-simulation-run-id" matches "cortexsim-.*"
| filter "x-simulation-campaign-id" = "CMP-AIACC-001"
```

## See also

- [[Architecture]] — what's running where
- [[EAL Simulator]] — plugin catalog + safety model
- [[Detection Planes]] — what's covered, what's pending
- [`docs/eal-simulator/runbook.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/docs/eal-simulator/runbook.md) — EAL-specific runbook
