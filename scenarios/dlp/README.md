# DLP — Cortex Enterprise Data Loss Prevention (Endpoint, Web & Storage)

This directory contains Cortex-branded simulation scenarios targeting **Cortex Enterprise DLP**, **Cortex XDR Agent DLP Sensor**, and **Prisma Browser DLP** capabilities.

## Scenarios

| Scenario ID | Name | Channel | Key Techniques | Target Detection |
|---|---|---|---|---|
| `SIM-DLP-001` | USB Removable Media Exfiltration Attempt | Removable Storage | `T1052.001` | Block/Alert on sensitive PII/PCI file copy to USB device |
| `SIM-DLP-002` | Encrypted Archive Staging of Sensitive Corporate Data | Local Staging | `T1560.001`, `T1074.001` | Behavioral BIOC on password-protected zip of sensitive files |
| `SIM-DLP-003` | Unsanctioned SaaS / Web Upload of Confidential Financial Data | Web Upload | `T1567.002` | Enterprise DLP inspection & policy block on browser upload |
| `SIM-DLP-004` | Cross-Channel DLP: Endpoint Local Staging to Network Exfil | Endpoint + Network | `T1074.001`, `T1048` | XSIAM correlation engine stitching local collection to network exfil |
| `SIM-DLP-005` | Clipboard and Print Spooler PII/PHI Harvesting | Clipboard / Print | `T1115`, `T1005` | Endpoint DLP detection on high-volume sensitive clipboard copies |
