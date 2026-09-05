# Lab-readiness manifest

> **Generated** by `scripts/lab_readiness.py` — do not hand-edit.
> A tier is about whether a scenario will *emit its authored
> telemetry on a correctly provisioned target*, never whether a
> Cortex detection was observed to fire. **tenant-verified is 0;
> authored is not proven.**

## What the tiers mean

- **GREEN — lab-ready.** Real signal on every detection-bearing step, and
  every tool is a stock binary or a digest-pinned payload-shelf artifact.
  Runs on a provisioned target under default-deny egress. Start a demo here.
- **YELLOW — provision first.** Real signal, but the scenario needs a tool
  fetched from the internet on the target, or a launch consent gate. Pre-stage
  the tool (or allow egress) / grant consent, then it runs.
- **RED — tabletop only.** Signal-free: it declares detections but no step
  invokes a real binary. **Do not run it expecting detections** — seeding its
  Result rows manufactures an all-missed POV. Convert (drive real telemetry
  via signalbench / telemetry-replay) or present it as a tabletop walk-through.

## Summary

- **Scenarios:** 177  (GREEN 146 · YELLOW 26 · RED 5)
- **Steps:** 667 total · 141 detection-bearing steps produce no real signal
- **Need target egress for a tool:** 26 scenarios
- **Consent-gated:** 4 scenarios
- **EAL-delivered (network/NGFW signal from SimCore, not the agent):** 55 scenarios
- **RED / signal-free (tabletop):** SIM-ASM-005, SIM-ASM-006, SIM-EDR-019, SIM-ITDR-016, SIM-TIM-005

## GREEN — 146 scenarios

| Scenario | Plane | Types | Signal | Delivery | Notes |
|---|---|---|---:|---|---|
| SIM-AIACC-001 | AI_ACCESS | BIOC/XQL | 3/3 | eal |  |
| SIM-AIACC-002 | AI_ACCESS | BIOC/XQL/IOC | 3/3 | eal |  |
| SIM-AIACC-003 | AI_ACCESS | XQL | 3/3 | eal |  |
| SIM-AIACC-004 | AI_ACCESS | BIOC/XQL | 3/3 | eal |  |
| SIM-AIACC-005 | AI_ACCESS | XQL | 3/3 | eal |  |
| SIM-AIRS-001 | AIRS | BIOC/XQL | 3/3 | agent |  |
| SIM-AIRS-002 | AIRS | BIOC/XQL | 3/3 | agent |  |
| SIM-AIRS-003 | AIRS | BIOC/XQL | 3/3 | agent |  |
| SIM-AIRS-004 | AIRS | BIOC/XQL | 3/3 | agent |  |
| SIM-AIRS-005 | AIRS | XQL | 3/3 | agent |  |
| SIM-AISPM-002 | AI_SPM | XQL | 3/3 | agent |  |
| SIM-AISPM-004 | AI_SPM | XQL | 3/3 | agent |  |
| SIM-AISPM-005 | AI_SPM | XQL | 3/3 | agent |  |
| SIM-AISPM-006 | AI_SPM | XQL | 3/3 | agent |  |
| SIM-CDR-004 | CDR | BIOC | 5/5 | agent |  |
| SIM-CDR-006 | CDR | BIOC/XQL/Correlation/IOC | 3/3 | agent |  |
| SIM-CDR-020 | CDR | ABIOC/Analytics/XQL/Correlation | 3/3 | eal |  |
| SIM-CDR-022 | CDR | Analytics/ABIOC/XQL/Correlation | 3/3 | eal |  |
| SIM-CDR-023 | CDR | ABIOC/XQL/Analytics/Correlation | 2/2 | eal |  |
| SIM-CDR-025 | CDR | ABIOC/XQL/Correlation | 2/2 | eal |  |
| SIM-CDR-027 | CDR | BIOC/Analytics | 1/1 | agent |  |
| SIM-CDR-028 | CDR | BIOC/Analytics | 1/1 | agent |  |
| SIM-CLOUD-001 | CLOUD_APP | BIOC/XQL | 3/3 | eal |  |
| SIM-CLOUD-002 | CLOUD_APP | BIOC/XQL | 3/3 | eal |  |
| SIM-CLOUD-003 | CLOUD_APP | BIOC/XQL | 3/3 | eal |  |
| SIM-CLOUD-004 | CLOUD_APP | XQL | 3/3 | eal |  |
| SIM-CLOUD-005 | CLOUD_APP | XQL | 3/3 | eal |  |
| SIM-CLOUD-008 | CLOUD_APP | Analytics/XQL/Correlation | 2/2 | eal |  |
| SIM-DLP-001 | DLP | BIOC/Analytics | 2/2 | agent |  |
| SIM-DLP-002 | DLP | BIOC/Analytics | 2/2 | agent |  |
| SIM-DLP-003 | DLP | BIOC/Analytics | 2/2 | agent |  |
| SIM-DLP-004 | DLP | BIOC/Correlation/Analytics | 3/3 | agent |  |
| SIM-DLP-005 | DLP | BIOC/Analytics | 2/2 | agent |  |
| SIM-EDR-002 | EDR | BIOC/XQL | 5/5 | agent |  |
| SIM-EDR-003 | EDR | BIOC/XQL | 5/5 | agent |  |
| SIM-EDR-004 | EDR | BIOC/XQL | 5/5 | agent |  |
| SIM-EDR-006 | EDR | BIOC/XQL/Correlation/IOC | 3/3 | agent |  |
| SIM-EDR-007 | EDR | BIOC/XQL/Correlation/IOC | 3/3 | agent |  |
| SIM-EDR-008 | EDR | BIOC/XQL/Correlation/IOC | 3/3 | agent |  |
| SIM-EDR-013 | EDR | ABIOC/XQL/Correlation | 3/3 | agent |  |
| SIM-EDR-017 | EDR | ABIOC/XQL/Correlation | 4/4 | agent |  |
| SIM-EDR-021 | EDR | BIOC/ABIOC/XQL/Correlation | 7/7 | agent |  |
| SIM-EMAIL-001 | EMAIL | BIOC/XQL | 3/3 | eal |  |
| SIM-EMAIL-002 | EMAIL | BIOC/XQL | 3/3 | eal |  |
| SIM-EMAIL-003 | EMAIL | BIOC/XQL/Correlation | 3/3 | eal |  |
| SIM-EMAIL-004 | EMAIL | BIOC/XQL/Correlation | 3/3 | eal |  |
| SIM-ITDR-001 | ITDR | BIOC/XQL | 3/3 | eal |  |
| SIM-ITDR-002 | ITDR | BIOC/XQL | 3/3 | eal |  |
| SIM-ITDR-003 | ITDR | BIOC/XQL | 3/3 | eal |  |
| SIM-ITDR-004 | ITDR | BIOC/XQL | 3/3 | eal |  |
| SIM-ITDR-005 | ITDR | BIOC/XQL | 3/3 | eal |  |
| SIM-ITDR-008 | ITDR | BIOC/XQL/Correlation | 3/3 | eal |  |
| SIM-ITDR-014 | ITDR | ABIOC/BIOC/XQL/Correlation | 5/5 | agent |  |
| SIM-ITDR-018 | ITDR | ABIOC/Analytics/XQL/Correlation | 3/3 | eal |  |
| SIM-KOI-004 | KOI | BIOC/XQL | 3/3 | eal |  |
| SIM-MP-007 | ANALYTICS | Analytics/BIOC/ABIOC/XQL/Correlation/IOC | 5/5 | agent |  |
| SIM-MP-020 | ANALYTICS | BIOC/ABIOC/XQL/Correlation | 6/6 | agent |  |
| SIM-NDR-001 | NDR | BIOC/XQL | 3/3 | eal |  |
| SIM-NDR-002 | NDR | BIOC/XQL | 3/3 | eal |  |
| SIM-NDR-003 | NDR | BIOC/XQL/IOC | 2/2 | eal |  |
| SIM-NDR-005 | NDR | XQL/BIOC/Correlation | 2/2 | eal |  |
| SIM-NDR-006 | NDR | BIOC/XQL/IOC | 2/2 | eal |  |
| SIM-NDR-007 | NDR | BIOC/XQL/IOC | 2/2 | eal |  |
| SIM-TIM-001 | TIM | IOC/XQL/BIOC/Correlation | 3/3 | eal |  |
| SIM-TIM-002 | TIM | IOC/XQL/BIOC/Correlation | 3/3 | eal |  |
| SIM-MP-018 | ANALYTICS | BIOC/ABIOC/XQL/Correlation | 6/7 | agent |  |
| SIM-MP-021 | ANALYTICS | BIOC/ABIOC/XQL/Analytics/Correlation | 6/7 | agent |  |
| SIM-CLOUD-010 | CLOUD_APP | ABIOC/XQL/Analytics/Correlation | 5/6 | agent |  |
| SIM-MP-016 | ANALYTICS | BIOC/XQL/ABIOC/Correlation | 5/6 | agent |  |
| SIM-MP-022 | ANALYTICS | ABIOC/BIOC/XQL/Analytics/Correlation | 5/6 | agent |  |
| SIM-CLOUD-007 | CLOUD_APP | ABIOC/XQL/Correlation | 4/5 | agent |  |
| SIM-CSPM-005 | CSPM | XQL/Correlation/IOC | 4/5 | agent |  |
| SIM-EDR-022 | EDR | BIOC/XQL/Correlation/IOC | 4/5 | agent | shelf: TOOL-LINPEAS, TOOL-PSPY, TOOL-SUID3NUM |
| SIM-MP-011 | ANALYTICS | BIOC/XQL/Correlation | 4/5 | agent |  |
| SIM-MP-015 | ANALYTICS | ABIOC/XQL/Correlation | 4/5 | agent |  |
| SIM-TIM-007 | TIM | XQL/BIOC/Correlation | 4/5 | agent |  |
| SIM-TIM-009 | TIM | ABIOC/XQL/Analytics/IOC/Correlation | 4/5 | agent |  |
| SIM-AIACC-006 | AI_ACCESS | ABIOC/XQL/Analytics/IOC/Correlation | 3/4 | agent |  |
| SIM-AISPM-001 | AI_SPM | XQL | 3/4 | agent |  |
| SIM-CDR-009 | CDR | ABIOC/XQL/Correlation | 3/4 | agent |  |
| SIM-CDR-010 | CDR | BIOC/XQL/IOC/Correlation | 3/4 | agent |  |
| SIM-CDR-011 | CDR | BIOC/XQL/Correlation | 3/4 | agent |  |
| SIM-CDR-012 | CDR | BIOC/XQL/IOC/Correlation | 3/4 | agent |  |
| SIM-CDR-017 | CDR | ABIOC/XQL/Correlation | 3/4 | agent |  |
| SIM-CDR-021 | CDR | Analytics/ABIOC/XQL/Correlation | 3/4 | eal |  |
| SIM-CLOUD-006 | CLOUD_APP | ABIOC/XQL/Correlation/IOC | 3/4 | agent |  |
| SIM-CLOUD-009 | CLOUD_APP | ABIOC/XQL/Correlation | 3/4 | eal |  |
| SIM-EDR-009 | EDR | BIOC/XQL/Correlation/IOC | 3/4 | agent |  |
| SIM-EDR-011 | EDR | ABIOC/XQL/Correlation | 3/4 | agent |  |
| SIM-EDR-015 | EDR | ABIOC/XQL/Correlation | 3/4 | agent |  |
| SIM-EDR-016 | EDR | ABIOC/XQL/Correlation/IOC | 3/4 | agent |  |
| SIM-EDR-018 | EDR | ABIOC/XQL/Correlation | 3/4 | agent |  |
| SIM-EMAIL-005 | EMAIL | XQL/Correlation | 3/4 | eal |  |
| SIM-MP-008 | ANALYTICS | ABIOC/XQL/Correlation | 3/4 | agent |  |
| SIM-MP-012 | ANALYTICS | BIOC/XQL/Correlation | 3/4 | agent |  |
| SIM-MP-014 | ANALYTICS | BIOC/ABIOC/XQL/Correlation | 3/4 | agent |  |
| SIM-NDR-009 | NDR | ABIOC/XQL/Correlation | 3/4 | agent |  |
| SIM-TIM-004 | TIM | IOC/XQL/BIOC/Correlation | 3/4 | agent |  |
| SIM-AISPM-003 | AI_SPM | XQL | 2/3 | agent |  |
| SIM-BROWSER-001 | BROWSER | Analytics/BIOC | 2/3 | eal |  |
| SIM-BROWSER-002 | BROWSER | Analytics/BIOC | 2/3 | eal |  |
| SIM-BROWSER-003 | BROWSER | Analytics/BIOC/IOC | 2/3 | eal |  |
| SIM-BROWSER-004 | BROWSER | Analytics/BIOC | 2/3 | eal |  |
| SIM-BROWSER-005 | BROWSER | Analytics/BIOC | 2/3 | eal |  |
| SIM-CDR-008 | CDR | BIOC/XQL/Correlation | 2/3 | agent |  |
| SIM-CDR-016 | CDR | ABIOC/XQL/Correlation | 2/3 | agent |  |
| SIM-CDR-019 | CDR | ABIOC/XQL/Correlation | 2/3 | eal |  |
| SIM-CDR-024 | CDR | ABIOC/XQL/Analytics/Correlation | 2/3 | eal |  |
| SIM-CDR-026 | CDR | Analytics/XQL/Correlation | 2/3 | eal |  |
| SIM-EDR-010 | EDR | ABIOC/XQL/Correlation | 2/3 | agent |  |
| SIM-EDR-012 | EDR | ABIOC/XQL/Correlation | 2/3 | agent |  |
| SIM-ITDR-009 | ITDR | ABIOC/XQL/Correlation | 2/3 | agent |  |
| SIM-ITDR-010 | ITDR | ABIOC/XQL/Correlation | 2/3 | agent |  |
| SIM-ITDR-011 | ITDR | ABIOC/XQL/Correlation | 2/3 | agent |  |
| SIM-ITDR-019 | ITDR | Analytics/ABIOC/XQL/Correlation | 2/3 | eal |  |
| SIM-ITDR-020 | ITDR | ABIOC/Analytics/XQL/Correlation | 2/3 | eal |  |
| SIM-KOI-001 | KOI | BIOC/XQL | 2/3 | eal |  |
| SIM-KOI-002 | KOI | BIOC/XQL | 2/3 | eal |  |
| SIM-KOI-003 | KOI | BIOC/XQL | 2/3 | eal |  |
| SIM-KOI-005 | KOI | BIOC/XQL | 2/3 | eal |  |
| SIM-KOI-006 | KOI | BIOC/XQL/Correlation | 2/3 | eal |  |
| SIM-KOI-007 | KOI | BIOC/ABIOC/XQL/Correlation | 2/3 | agent |  |
| SIM-MP-017 | ANALYTICS | ABIOC/XQL/Correlation | 4/6 | agent |  |
| SIM-NDR-008 | NDR | ABIOC/XQL/Correlation | 2/3 | agent |  |
| SIM-NDR-011 | NDR | ABIOC/XQL/Correlation | 2/3 | agent |  |
| SIM-NDR-012 | NDR | ABIOC/XQL/Correlation | 2/3 | eal |  |
| SIM-TIM-003 | TIM | ABIOC/XQL/Correlation | 2/3 | agent |  |
| SIM-TIM-006 | TIM | IOC/BIOC/XQL/Correlation | 2/3 | agent |  |
| SIM-TIM-008 | TIM | ABIOC/XQL/Analytics/IOC/Correlation | 4/6 | agent |  |
| SIM-AISPM-007 | AI_SPM | ABIOC/XQL/Correlation | 3/5 | agent |  |
| SIM-APB-001 | ANALYTICS | ABIOC/XQL/Analytics/Correlation | 3/5 | agent |  |
| SIM-EDR-014 | EDR | BIOC/XQL/Correlation/IOC | 3/5 | agent |  |
| SIM-BROWSER-006 | BROWSER | ABIOC/XQL/Correlation | 2/4 | agent |  |
| SIM-CDR-014 | CDR | ABIOC/XQL/Correlation | 2/4 | agent |  |
| SIM-CDR-018 | CDR | BIOC/XQL/Correlation | 2/4 | agent |  |
| SIM-KOI-008 | KOI | ABIOC/BIOC/XQL/Correlation | 3/6 | agent |  |
| SIM-MP-009 | ANALYTICS | ABIOC/XQL/Correlation | 2/4 | agent |  |
| SIM-NDR-010 | NDR | XQL/BIOC/Correlation | 2/4 | agent |  |
| SIM-MP-013 | ANALYTICS | ABIOC/XQL/Correlation | 3/8 | agent |  |
| SIM-CDR-013 | CDR | BIOC/XQL/IOC/Correlation | 1/3 | agent |  |
| SIM-CDR-015 | CDR | BIOC/XQL | 1/3 | agent |  |
| SIM-ITDR-012 | ITDR | BIOC/XQL/Correlation/IOC | 1/3 | agent |  |
| SIM-ITDR-013 | ITDR | ABIOC/BIOC/XQL/Correlation | 1/4 | agent |  |
| SIM-MP-010 | ANALYTICS | BIOC/XQL/Correlation/IOC | 1/5 | agent |  |
| SIM-EDR-020 | EDR | BIOC/ABIOC/XQL/Correlation | 1/6 | agent |  |
| SIM-ITDR-017 | ITDR | ABIOC/BIOC/XQL/Correlation | 1/6 | agent |  |

## YELLOW — 26 scenarios

| Scenario | Plane | Types | Signal | Delivery | Notes |
|---|---|---|---:|---|---|
| SIM-ASM-001 | ASM | XQL/BIOC/IOC/Correlation | 3/3 | agent | egress: TOOL-NMAP, TOOL-NUCLEI |
| SIM-ASM-002 | ASM | XQL/BIOC/IOC/Correlation | 3/3 | agent | egress: TOOL-NUCLEI |
| SIM-ASM-003 | ASM | XQL/BIOC/IOC/Correlation | 3/3 | agent | egress: TOOL-THEHARVESTER |
| SIM-ASM-004 | ASM | XQL/BIOC/IOC/Correlation | 7/7 | agent | egress: TOOL-CMSEEK, TOOL-COMMIX, TOOL-FEROXBUSTER, TOOL-GOBUSTER, TOOL-NIKTO, TOOL-SQLMAP, TOOL-WHATWEB |
| SIM-CDR-001 | CDR | BIOC | 5/5 | agent | egress: install_inline; shelf: TOOL-DEEPCE, TOOL-LINPEAS |
| SIM-CDR-002 | CDR | BIOC | 5/5 | agent | egress: install_inline |
| SIM-CDR-003 | CDR | BIOC | 5/5 | agent | egress: install_inline; shelf: TOOL-DEEPCE |
| SIM-CDR-005 | CDR | BIOC | 4/4 | agent | egress: install_inline |
| SIM-CDR-007 | CDR | BIOC/XQL/Correlation | 6/6 | agent | egress: TOOL-CLOUDSPLAINING, TOOL-GITLEAKS, TOOL-KUBE-BENCH, TOOL-KUBESCAPE, TOOL-TRIVY |
| SIM-CSPM-001 | CSPM | XQL/BIOC/Correlation | 4/4 | agent | egress: TOOL-PROWLER, TOOL-SCOUTSUITE |
| SIM-CSPM-002 | CSPM | XQL/Correlation | 4/4 | agent | egress: TOOL-PROWLER |
| SIM-EDR-001 | EDR | BIOC/XQL/IOC | 5/5 | agent | egress: install_inline |
| SIM-EDR-005 | EDR | BIOC/XQL | 5/5 | agent | egress: TOOL-NMAP |
| SIM-ITDR-006 | ITDR | BIOC/XQL/Correlation | 3/3 | agent | egress: TOOL-IMPACKET |
| SIM-ITDR-015 | ITDR | BIOC/XQL/ABIOC/Correlation/IOC | 7/7 | agent | egress: TOOL-ENUM4LINUX-NG, TOOL-EVIL-WINRM, TOOL-HASHCAT, TOOL-JOHN, TOOL-NETEXEC, TOOL-RESPONDER, TOOL-SMBMAP; consent |
| SIM-MP-001 | ANALYTICS | BIOC/XQL/IOC/Correlation | 4/4 | agent | egress: install_inline; consent; c2: TOOL-SLIVER |
| SIM-MP-002 | ANALYTICS | BIOC/XQL/Correlation | 4/4 | agent | egress: TOOL-IMPACKET |
| SIM-MP-003 | ANALYTICS | BIOC/XQL/IOC/Correlation | 4/4 | agent | egress: TOOL-SCAPY |
| SIM-MP-004 | ANALYTICS | BIOC/XQL/IOC | 5/5 | agent | egress: TOOL-PACU |
| SIM-MP-019 | ANALYTICS | XQL/BIOC/ABIOC/IOC/Correlation | 5/5 | agent | egress: TOOL-DNSRECON, TOOL-FFUF, TOOL-GOBUSTER, TOOL-HYDRA, TOOL-IMPACKET, TOOL-MASSCAN, TOOL-METASPLOIT, TOOL-NETEXEC, TOOL-NIKTO, TOOL-NMAP, TOOL-SQLMAP, TOOL-WHATWEB; consent |
| SIM-NDR-004 | NDR | BIOC/XQL | 2/2 | eal | egress: TOOL-MASSCAN, TOOL-NMAP |
| SIM-ITDR-007 | ITDR | BIOC/XQL/Correlation | 5/6 | agent | egress: TOOL-PYPYKATZ |
| SIM-CSPM-003 | CSPM | XQL/Correlation | 4/5 | agent | egress: TOOL-PROWLER |
| SIM-CSPM-004 | CSPM | XQL/ABIOC/Correlation | 3/4 | agent | egress: TOOL-PROWLER |
| SIM-MP-005 | ANALYTICS | BIOC/XQL/Correlation | 3/4 | agent | egress: TOOL-NMAP |
| SIM-MP-006 | ANALYTICS | XQL/Correlation | 2/3 | agent | egress: install_inline; consent; c2: TOOL-SLIVER |

## RED — 5 scenarios

| Scenario | Plane | Types | Signal | Delivery | Notes |
|---|---|---|---:|---|---|
| SIM-ASM-005 | ASM | ABIOC/BIOC/XQL/IOC/Correlation | 0/5 | agent | signal-free: declares detections but no step invokes a real binary (tabletop only) |
| SIM-ASM-006 | ASM | XQL/BIOC/ABIOC/Correlation | 0/4 | agent | signal-free: declares detections but no step invokes a real binary (tabletop only) |
| SIM-EDR-019 | EDR | BIOC/ABIOC/XQL/Correlation | 0/7 | agent | signal-free: declares detections but no step invokes a real binary (tabletop only) |
| SIM-ITDR-016 | ITDR | ABIOC/XQL/Correlation | 0/3 | agent | signal-free: declares detections but no step invokes a real binary (tabletop only) |
| SIM-TIM-005 | TIM | IOC/BIOC/XQL/Correlation | 0/4 | agent | signal-free: declares detections but no step invokes a real binary (tabletop only) |

