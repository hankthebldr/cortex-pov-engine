# Cortex Analytics POV Executive Summary

## Objective
The objective of this Proof of Value (POV) simulation was to validate the threat detection and incident stitching capabilities of Cortex XSIAM and XDR across a hybrid environment consisting of a compromised Linux endpoint, NGFW network telemetry (Enhanced Application Logging), and SaaS/IdP environments.

## Scope of Simulation
The simulation emulated an attacker gaining initial access via a web vulnerability on a Linux host (`T1059.004`), establishing a C2 beacon over HTTPS (`T1071.001`), conducting internal network recon and SMB lateral movement caught via NGFW EAL (`T1046`, `T1021.002`), and stealing an Okta session token to exfiltrate data from M365 (`T1528`, `T1078`, `T1530`).

## Key Findings
- **Comprehensive Visibility:** The integration of XDR Agent (DS-01), PAN NGFW (DS-02), and SaaS ITDR (DS-07) successfully detected the full kill chain from initial access to data exfiltration.
- **Enhanced Application Logging (EAL) Value:** EAL provided critical visibility into internal east-west traffic, allowing Cortex to detect the internal Nmap scan and SMB lateral movement attempts that would typically bypass perimeter-only defenses.
- **Cross-Source Incident Stitching:** Cortex XSIAM successfully correlated isolated alerts from the endpoint, network, and SaaS environment into a single, cohesive incident narrative. This drastically reduced alert fatigue and provided analysts with the full root cause and impact of the breach.
- **Identity Threat Detection and Response (ITDR):** The extraction and reuse of the Okta token triggered high-severity ITDR alerts for impossible travel and mass file downloads, validating the effectiveness of the ITDR add-on.

## Conclusion
The simulation proved the efficacy of Cortex analytics to not only detect advanced adversarial techniques at the individual data source level but to also correlate these disparate signals into actionable incidents. The addition of NGFW EAL logs significantly enhanced the network lateral movement detection capabilities of the platform.
