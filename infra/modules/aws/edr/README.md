---
name: edr
description: Linux target VMs for endpoint detection scenarios (credential dumping, reverse shell, persistence, defense evasion, lateral movement)
providers: [aws]
required_params: [project_name]
optional_params: [target_count, target_size]
dependencies: [base]
---

# edr (AWS)

Provisions 1-10 target Linux VMs in the private subnets of the base VPC, alternating between Ubuntu 22.04 and Amazon Linux 2 for diverse EDR telemetry. Hosts are reachable only from the jumpbox and each other.

## Content installed

Attack simulation: atomic-red-team, EDR-Testing-Script, LOLBAS, sliver.
Ransomware: CipherStrike, RanSim, simulate-black-basta, simulate-akira.
Samples: EVTX-ATTACK-SAMPLES, mordor.

Content is installed on the **jumpbox**, not the target hosts. The jumpbox uses the beacon agent to push TTP commands to targets.
