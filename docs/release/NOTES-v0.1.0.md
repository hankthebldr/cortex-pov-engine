## CortexSim v0.1.0 — first tagged pre-release

CortexSim is an enterprise detection simulation engine for Palo Alto
Networks Domain Consultants: it generates controlled, high-fidelity signals
into a customer's Cortex environment (XSIAM/XDR) to validate detection
logic across `BIOC | XQL | Analytics | Correlation | IOC | ABIOC`, plus the
XDM modeling-rule substrate and cross-source stitching. No authentication —
it is built to run on a customer-lab jumpbox where the operating DC already
has full admin access.

**Container image** — `ghcr.io/hankthebldr/cortexsim:v0.1.0` (`linux/amd64`,
`linux/arm64`)

**One-line install (Linux jumpbox):**
```bash
curl -fsSL https://github.com/hankthebldr/cortex-pov-engine/releases/download/v0.1.0/install.sh | sudo bash
```
Verify artifact integrity with `SHA256SUMS` or `manifest.json` on this
release.

### What ships in this release

- 177 loadable detection scenarios across 16 planes (EDR, CDR, NDR, ITDR,
  CSPM, ASM, TIM, Cloud App, Analytics, AI Access, AIRS, Browser, KOI,
  AI_SPM, Email, DLP) — 175 TTP cards, 1,096 step-level expected detections,
  1,777 catalog detection objects.
- Pull-mode (Go beacon, 5 platform targets incl. Windows) and push-mode
  (self-contained bash/PowerShell bundle) execution, both through a shared
  identity harness for realistic process-causality chains.
- The FY27 UC/TC v2.2 alignment index, wired read-only in-product
  (`/api/uctc/*`, console UC/TC Index view), plus a POS/PLT/AUT assertion
  substrate for the ~140 index rows that aren't detection-shaped.
- An **optional, read-only** measurement loop: when a tenant credential is
  configured, SimCore reads alerts back and turns seeded results into
  evidence-backed MTTD. No credential configured, no outbound call — ever.
  `CORTEXSIM_XSIAM_ALLOW_WRITE` / `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE` stay
  off by default; SimCore never writes to a customer tenant.
- A tool-adapter framework (91 packs) with a digest-pinned payload shelf, so
  a default-deny customer network doesn't block a tier-4 tool at dispatch
  time.
- An IaC topology generator (AWS, 11 modules) for standing up target
  environments with intentional, documented findings.
- A `GET /api/health` that never reports green for something it did not
  check, and a console **Readiness** view that renders the connector ladder
  (Authored → Configured → Reachable → Verified) instead of collapsing it
  into one checkmark.

### Read before you brief a customer on this

- **`tenant-verified` is 0.** Nothing in this repo — no run, no assertion,
  no test — has ever executed against a live Cortex tenant. Every green
  result here comes from an injected transport. Authored is not proven; the
  console's Readiness view and `docs/reference/ground-truth.json`
  (`tenant_verified: 0`) say so explicitly.
- **A bare `ubuntu:22.04` target cannot run this corpus** — `www-data` ships
  `nologin`, the identity harness fails in milliseconds, and the run reads
  "failed" having executed nothing. Use `deploy/tier-d/Dockerfile.target`
  (or provision a real target the same way) — see
  `docs/reference/lab-runbook.md`.
- **At least 100 of the corpus's 654 total steps are `echo`/`printf`
  placeholders** that declare `expected_detections` without producing real
  signal — staged for content authoring, not yet load-bearing TTPs.
  Reproduce the count with the script in `docs/reference/lab-runbook.md`.
- **Only 59 of 177 scenarios declare an MTTD-shaped KPI**, the only KPI
  class this engine measures natively today; the rest score `pending`.

Full detail: [`CHANGELOG.md`](../../CHANGELOG.md).

### Verifying what you pulled

```bash
docker pull ghcr.io/hankthebldr/cortexsim:v0.1.0
docker run --rm -p 8888:8888 -e CORTEXSIM_ENV=development \
  -e CORTEXSIM_SECRET="$(openssl rand -hex 32)" \
  ghcr.io/hankthebldr/cortexsim:v0.1.0
curl -s http://localhost:8888/api/health | python3 -m json.tool
```
Expect `"version": "0.1.0"` and `"degraded_components": []`.

### Initial release

No prior tag exists — this is the first.
