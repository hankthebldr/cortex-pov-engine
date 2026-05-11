# scenarios/browser

Prisma Browser — detection of risky browser activity by managed users:
credential paste into untrusted origins, drive-by downloads from phishing
sites, risky/sideloaded extensions, copy-paste DLP across SaaS boundaries,
and screen-capture of sensitive content.

These scenarios drive a real Chromium / Prisma Browser instance via
Playwright through the in-tree **`cortex-browser-attacker`** (Phase 6)
plus the **`browser_attack_runner`** EAL plugin. The customer's
deployed Prisma Browser tenant forwards its own telemetry to the
customer XSIAM tenant via the existing PB→XSIAM path; we just
*produce the activity*.

Use case prefix: `UCS-BROWSER-NN`

## Layout

- `sim-browser-001..005.yml` — top-level CortexSim scenarios that
  invoke the EAL plugin against a per-scenario campaign YAML.
- `campaigns/*.yml` — `cortex-browser-attacker` browser-campaign
  YAMLs, one per scenario. These describe the actual browser actions
  (navigate, paste, copy, download, install_extension, screenshot).

## Status

Scenarios are `status: active` as of **Phase 6**.

Each scenario step:

1. Pre-flight — confirms the EAL plugin is registered.
2. Drive — invokes `python -m scripts.eal_simulator.cli run` against
   an inline EAL campaign that references the browser campaign in
   `campaigns/`.
3. Verify — operator confirms the matching alert(s) in XSIAM.
