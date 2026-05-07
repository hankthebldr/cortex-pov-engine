# scenarios/browser

Prisma Browser — detection of risky browser activity by managed users:
credential paste into untrusted origins, drive-by downloads from phishing
sites, risky/sideloaded extensions, copy-paste DLP across SaaS boundaries,
and screen-capture of sensitive content.

These scenarios drive a **real Chromium** instance via Playwright (planned
component `sources/cortex-browser-attacker/`, Phase 6) and rely on the
customer's deployed Prisma Browser tenant to observe + alert. PB → XSIAM
is the customer's existing data path; we do not bridge it.

Use case prefix: `UCS-BROWSER-NN`

> **Status**: scenarios are `status: draft` until Phase 6 ships
> `cortex-browser-attacker`. Metadata, MITRE mapping, expected detections
> and step structure are production-shape; only the runner is missing.
