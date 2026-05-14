# Detection Coverage Lab (Worked Example)

A complete, real-world POV deliverable lives at
[`lab_cortex_analytics_pov/`](https://github.com/hankthebldr/cortex-pov-engine/tree/main/lab_cortex_analytics_pov)
in the repo. It demonstrates exactly what a Cortex Domain Consultant
hands to a customer at the end of a POV — and it's also the template
the **Phase 8 report generator** (`core/engine/report_generator.py`)
emits from any CortexSim run.

> Use this page as your starting point when designing a new POV: pick
> the artifacts you want to produce, then drive the CortexSim
> scenarios that will produce them.

## Scenario

**Compromised Linux Host + Network EAL + ITDR**

- **Target:** Ubuntu 22.04 host at `10.0.60.11`
- **Telemetry:** XDR Agent (DS-01) + PAN NGFW with EAL (DS-02) +
  Okta / M365 SaaS via ITDR (DS-07)
- **Threat vector:** web-vuln initial access → C2 over HTTPS → internal
  Nmap recon → SMB lateral → Okta token theft → M365 mass download

## Artifacts in the lab

| File | What it is | Phase 8 endpoint that emits this from a run |
|---|---|---|
| [`detection_matrix.csv`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/lab_cortex_analytics_pov/detection_matrix.csv) | One row per expected alert (Alert Name / Source(s) / Status / Alert Type / ATT&CK TID / Severity) | `GET /api/runs/{run_id}/report/matrix` |
| [`attack_navigator_layer.json`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/lab_cortex_analytics_pov/attack_navigator_layer.json) | ATT&CK Navigator v4.5 layer — DETECTED techniques red, missed grey | `GET /api/runs/{run_id}/report/navigator` |
| [`pov_narrative/exec_summary.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/lab_cortex_analytics_pov/pov_narrative/exec_summary.md) | Exec-level markdown summary with objective, scope, findings, conclusion | bundled with `/bundle` below |
| [`simulations/run_all.sh`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/lab_cortex_analytics_pov/simulations/run_all.sh) | 4-phase driver that runs the simulation scripts in order | (manual operator step) |
| [`simulations/ds01_agent/T1059_004_unix_shell.sh`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/lab_cortex_analytics_pov/simulations/ds01_agent/T1059_004_unix_shell.sh) | DS-01 agent simulation — `T1059.004` | — |
| [`simulations/ds02_ngfw/T1071_001_https_c2.sh`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/lab_cortex_analytics_pov/simulations/ds02_ngfw/T1071_001_https_c2.sh) | DS-02 NGFW — C2 beacon `T1071.001` | — |
| [`simulations/ds02_ngfw/T1046_internal_port_scan.sh`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/lab_cortex_analytics_pov/simulations/ds02_ngfw/T1046_internal_port_scan.sh) | DS-02 NGFW EAL — internal port scan `T1046` | — |
| [`simulations/ds02_ngfw/T1021_002_smb_lateral.sh`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/lab_cortex_analytics_pov/simulations/ds02_ngfw/T1021_002_smb_lateral.sh) | DS-02 NGFW EAL — SMB lateral `T1021.002` | — |
| [`simulations/ds07_saas/T1528_token_theft.py`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/lab_cortex_analytics_pov/simulations/ds07_saas/T1528_token_theft.py) | DS-07 SaaS — Okta token theft `T1528` | — |
| [`simulations/ds07_saas/T1530_mass_download.py`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/lab_cortex_analytics_pov/simulations/ds07_saas/T1530_mass_download.py) | DS-07 SaaS — mass file exfil `T1530` | — |
| [`custom_rules/correlation_rules.xql`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/lab_cortex_analytics_pov/custom_rules/correlation_rules.xql) | Correlation rule joining DS-01 / DS-02 / DS-07 within a 60-min window | — |
| [`custom_rules/bioc_gap_rules.json`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/lab_cortex_analytics_pov/custom_rules/bioc_gap_rules.json) | Custom BIOC for the gap case (reverse shell over non-standard port) | — |

## Detection matrix shape (9 alerts in the lab)

| Alert Name | Source(s) | Status | Alert Type | ATT&CK TID | Severity |
|---|---|---|---|---|---|
| Unix Shell Execution Anomaly | DS-01 (Agent) | ENABLED | Analytics | T1059.004 | High |
| C2 over HTTPS/Non-standard Port | DS-02 (NGFW) | ENABLED | Analytics | T1071.001 | High |
| Internal Port Scan / Recon | DS-02 (NGFW EAL) | ENABLED | Analytics | T1046 | Medium |
| Unusual SMB/RPC Lateral Movement | DS-01 + DS-02 (EAL) | ENHANCED | Analytics | T1021.002 | High |
| Data Exfiltration over Alt Protocol | DS-02 (NGFW EAL) | ENABLED | Analytics | T1048 | High |
| Impossible Travel (SaaS) | DS-07 (SaaS) | ENABLED | ITDR | T1078 | Medium |
| Session Token Theft Pattern | DS-07 (Okta) | ENABLED | ITDR | T1528 | High |
| Mass File Download / Exfil | DS-07 (M365) | ENABLED | ITDR | T1530 | High |
| Cross-source Identity Threat | DS-01 + DS-07 | ENHANCED | Correlation | TA0006 | Critical |

## Generating this from a CortexSim run (Phase 8)

Once a scenario or EAL campaign has executed and the DC has validated
detections in the XSIAM console:

```bash
RUN=<run_id>
BASE=http://cortexsim.local:8888

# Single CSV
curl -fsSL "$BASE/api/runs/$RUN/report/matrix" \
    -o detection_matrix.csv

# Navigator layer (import into mitre-attack.github.io/attack-navigator/)
curl -fsSL "$BASE/api/runs/$RUN/report/navigator" \
    -o attack_navigator_layer.json

# All three artifacts in one tar.gz
curl -fsSL "$BASE/api/runs/$RUN/report/bundle" \
    -o cortexsim-pov-bundle.tar.gz
tar tzf cortexsim-pov-bundle.tar.gz
# detection_matrix.csv
# attack_navigator_layer.json
# pov_narrative/exec_summary.md
```

The structure of the bundle exactly mirrors the lab folder layout so a
customer who already understands the lab can read a bundle without
training.

## See also

- [[EAL Simulator]] — how to launch the runs whose artifacts feed this
  generator
- [[POV Runbook]] — DC playbook for a full POV from day 0 to PDF
  delivery
- [[Roadmap]] — Phase 8 entry that points back here
- [`core/engine/report_generator.py`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/core/engine/report_generator.py) — generator implementation
- [`tests/engine/test_report_generator.py`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/tests/engine/test_report_generator.py) — 26 unit tests covering matrix / navigator / summary / bundle / resilience
