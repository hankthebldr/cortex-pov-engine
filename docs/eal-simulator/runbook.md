# EAL Traffic Simulator — Operator Runbook

## Prerequisites

1. **Customer NGFW** in path. Worker pods (or jumpbox) must use it as the
   default gateway, and the firewall must:
   * permit outbound traffic to the campaign's `target_allowlist`,
   * have Anti-Spyware / Vulnerability / URL-Filtering profiles attached,
   * forward Enhanced Application Logs to Cortex Data Lake,
   * decrypt outbound HTTPS for any TLS-based plugins.
2. **Cortex Data Lake / XSIAM** ingestion confirmed.
3. **Authorisation in writing** from the customer DC. The simulator refuses
   to run live without this.

## First campaign — dry run

```bash
# 1. List available plugins
python -m scripts.eal_simulator.cli list-plugins

# 2. Author the campaign
cat > /tmp/first.yml <<'YML'
campaign_id: CMP-NDR-001
name: First dry-run validation
authorized_by: hank@paloaltonetworks.com
simulation_authorized: false
target_allowlist: []
dry_run: true
steps:
  - step_id: step-01
    plugin: c2_http_beacon
    params:
      target_url: http://testmynids.org/uid/index.html
      iterations: 5
      sleep_seconds: 30
YML

# 3. Run it (no traffic is emitted)
python -m scripts.eal_simulator.cli run /tmp/first.yml
```

The CLI prints the final `ExecutorState` as JSON. Inspect
`step_results[*].detail.dry_run` — it should be `true`.

## Going live

Edit the campaign:

```yaml
authorized_by: hank@paloaltonetworks.com   # required, named operator
simulation_authorized: true
target_allowlist:
  - testmynids.org      # hostname (suffix match)
  - 10.50.0.0/24        # CIDR
dry_run: false
```

Run with `--live`:

```bash
python -m scripts.eal_simulator.cli run /tmp/first.yml --live
```

## API workflow

```bash
BASE=http://cortexsim.tailnet:8888

# Persist
curl -fsS -X POST $BASE/api/eal/campaigns \
  -H 'content-type: application/json' \
  --data @first.json

# Launch
curl -fsS -X POST $BASE/api/eal/campaigns/CMP-NDR-001/launch \
  -H 'content-type: application/json' \
  -d '{"dry_run": false, "operator": "hank"}'

# Poll
curl -fsS $BASE/api/eal/runs/<run_id>
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `safety_violation: simulation_authorized must be true` on launch | dry_run flipped without filling auth block | set `simulation_authorized: true` and `authorized_by` |
| `Target ... not in allowlist` mid-run | plugin tried to hit a host not declared up front | add the host/CIDR to `target_allowlist` |
| HTTP plugin reports 502 / TLS error | NGFW SSL decryption not configured | enable Forward-Proxy decryption on the rule covering simulator traffic |
| No NGFW EAL fires despite traffic | log forwarding profile not attached to the rule | attach the EAL log forwarding profile and re-run |
| `params_invalid` on launch | schema mismatch (e.g. negative `iterations`) | check the plugin's params schema via `GET /api/eal/plugins/{name}` |

## Filtering simulator traffic in XSIAM

Every HTTP request injects:

```
X-Simulation-Run-ID: cortexsim-<uuid>
X-Simulation-Campaign-ID: CMP-NDR-001
X-Simulation-Source: cortexsim-eal-simulator/1.0
```

For DNS / TCP plugins, audit lines are tagged with the same identifiers in
the `cortexsim` ECS namespace. Use this to scope post-debrief queries.
