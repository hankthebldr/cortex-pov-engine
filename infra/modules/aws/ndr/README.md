---
name: ndr
description: Segmented network topology for NDR scenarios — VPC Flow Logs, attack endpoint in DMZ, NGFW log collector. Three stitching patterns for firewall+XDR correlation.
providers: [aws]
required_params: [project_name]
optional_params: [stitching_pattern, collector_instance_type, attack_endpoint_instance_type]
dependencies: [base]
---

# ndr (AWS)

Provisions the network-level topology for validating Cortex NDR scenarios and, critically, **stitching detection** where XSIAM correlates firewall logs with XDR endpoint logs into a single incident.

## What gets provisioned

- **VPC Flow Logs** on the base VPC → CloudWatch log group with full format string (srcaddr, dstaddr, ports, tcp-flags, pkt-srcaddr/dst for VPC-to-VPC visibility)
- **Log collector EC2** in the private subnet — runs an nginx HTTP sink on port 8080 plus `ackbarx` (SNMP→HTTP forwarder) and `mocktaxii` (TAXII server). This is where your NGFW forwards session/threat logs that XSIAM then ingests.
- **Attack endpoint EC2** in the public (DMZ) subnet — runs controlled C2-style traffic against `testmynids.org` (known-safe NIDS test target) to generate network signal for the firewall to observe.
- **Security groups** wiring the collector to accept log forwarding (HTTP/syslog/SNMP) from either the base VPC CIDR or the DC's `dc_ssh_cidr`.

## Stitching patterns

The module supports three NGFW integration patterns via the `stitching_pattern` variable:

### Pattern A: `marketplace_vmseries` — PAN VM-Series from AWS Marketplace

The DC deploys VM-Series from the [AWS Marketplace](https://aws.amazon.com/marketplace/search/results?searchTerms=palo+alto+vm-series) into this VPC's public subnet, then configures PAN-OS HTTP log forwarding profile to point at the collector's private IP on port 8080. Requires accepting marketplace terms and providing a valid PANW license.

**Why:** Most realistic — the DC validates the full PAN NGFW + XDR + XSIAM stack end-to-end.

### Pattern B: `external_ngfw_forward` — existing customer NGFW forwards logs only

This is the default. The module provisions only the collector side. The DC configures the customer's existing on-prem or cloud NGFW (PAN or otherwise) to forward syslog (`:514`) or HTTP logs (`:8080`) to the collector's public/private IP. The module's security group already allows this.

**Why:** Most common POV scenario — customer has NGFW investment, POV proves XSIAM can stitch its logs with XDR endpoint signal.

### Pattern C: `suricata_lab` — self-contained Suricata stand-in

For labs without any NGFW available. The DC SSHs to the attack endpoint and installs Suricata in IDS mode, configuring eve.json output to forward to the collector. Not production-realistic but unblocks pure-simulation workflows.

**Why:** Unblocks labs without vendor hardware or marketplace subscriptions.

## Stitching scenarios this unlocks

See `scenarios/multi_plane/SIM-MP-*.yml` for the actual playbooks. The topology makes these cross-plane detections possible:

| Scenario | Signal sources | XSIAM correlation |
|----------|---------------|-------------------|
| C2 beacon — endpoint + firewall | XDR process event + NGFW outbound session | Same session ID linked across planes |
| DNS tunneling exfil | XDR DNS query event + NGFW DNS anomaly | Data volume + entropy correlated |
| Credential theft → SMB lateral | ITDR Kerberoast + XDR process + NDR SMB session | User/host/session graph |

## Traffic generation

The attack endpoint runs a beacon simulator (`/opt/cortexsim/attack/beacon.sh`) that periodically:
- Issues HTTP GETs to `testmynids.org` (known-safe NIDS test target)
- Emits short TXT DNS queries that fingerprint as DNS tunneling heuristics
- Does not actually exfiltrate data — the traffic pattern is what matters

The DC can trigger additional one-off activity by SSHing to the endpoint and running scripts from `/opt/cortexsim/content/ndr/testmynids/`.

## Content installed

Network simulation: testmynids.org, redirect.rules, C3, Chameleon.
Packet replay: tcpreplay.
Telemetry: RedELK.
Threat intel: mocktaxii (TAXII 2.1 server for IOC feed).

## Outputs

After apply:

```bash
terraform output collector_private_ip          # Point NGFW log forwarding here
terraform output attack_endpoint_public_ip     # SSH here to run additional attack scripts
terraform output stitching_guidance            # Pattern-specific next steps
```
