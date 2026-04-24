---
name: content-library
description: Detection content repositories for customer hand-off (SIEM rules, XQL queries, BIOCs). Installs on the jumpbox only — no infrastructure provisioned.
providers: [aws, gcp, azure]
required_params: []
optional_params: []
dependencies: [base]
---

# content-library

Clones curated detection content repositories onto the jumpbox. Selecting this module produces no additional cloud resources — it only adds entries to the jumpbox content installer.

## Content installed

- Palo Alto Cortex: xql-hub, cortex-xql-queries, XDR_scripts, CortexXDR-BIOC
- Splunk: splunk/security_content
- Elastic: elastic/detection-rules
- Chronicle: chronicle/detection-rules
