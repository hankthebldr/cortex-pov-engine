# Cortex Analytics POV: Compromised Linux Host (Lab)

This repository contains the simulation scripts, expected detection matrix, and custom rules for proving Cortex XDR/XSIAM analytics detection coverage in a hybrid environment.

## Scenario
- **Target:** Ubuntu 22.04 Linux Host
- **Telemetry Sources:** XDR Agent (DS-01), PAN NGFW w/ EAL (DS-02), Okta/M365 SaaS (DS-07)
- **Threat Vector:** Initial access via web vulnerability, internal network recon, SMB lateral movement, and token-based cloud exfiltration.

## Prerequisites
- XDR Agent installed and heartbeating on `10.0.60.11` in Report-Only mode.
- PAN NGFW configured with Enhanced Application Logging (EAL) for the `10.0.60.x` subnet.
- Cortex ITDR add-on configured with Okta and M365 integrations.
- Execution requires an attacker node (Kali) at `10.0.50.100`.

## Execution
Run the full simulation sequence:
```bash
cd simulations
./run_all.sh
```
