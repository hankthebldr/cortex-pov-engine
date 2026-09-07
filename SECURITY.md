# Security Policy

CortexSim is a **detection quality-assurance engine**: it generates controlled,
attack-shaped signal so a Cortex (XSIAM / XDR) deployment can be measured
against detection logic it claims to have. That makes it dual-use by
construction, so this file states two separate things — how to report a
vulnerability *in CortexSim*, and which properties are deliberate design and
therefore not vulnerabilities.

## Reporting a vulnerability

Use **GitHub Security Advisories** — the "Report a vulnerability" button under
the [Security tab](https://github.com/hankthebldr/cortex-pov-engine/security).
That keeps the report private until a fix ships. Please do **not** open a public
issue for anything exploitable.

Include what you would want to receive: affected version or commit, the
component (`core/`, `agent/`, `ui/`, a scenario, an adapter pack), what an
attacker gains, and a minimal reproduction.

This is an independent project maintained by one person in the open. Expect an
acknowledgement within about a week, and a fix timeline that depends on
severity rather than a contractual SLA. If a report is credible and you want
credit, say so and you will be named in the release notes.

**Do not** send vulnerability reports to Palo Alto Networks. CortexSim is not a
PANW product (see `NOTICE`). A vulnerability in a *Cortex product* goes to
PANW's own disclosure process, not here.

## Supported versions

| Version | Supported |
|---|---|
| `1.x` | Yes — fixes land on `dev`, release from `main` |
| `0.1.x` | No — superseded by 1.0.0 |

Only the latest `1.x` receives fixes. There are no backports.

## What is NOT a vulnerability

These are deliberate, documented design decisions. Reports about them will be
closed with a pointer to this section — but if you can show one of the stated
assumptions is wrong, that *is* a finding worth reporting.

**There is no authentication in SimCore.** None, anywhere, on purpose. SimCore
is built to run on a customer-lab jumpbox where the operating consultant already
has full administrative access to the host. Adding auth would secure the API
against an attacker who, by construction, already owns the box. **The
assumption that carries this is that SimCore is never exposed to an untrusted
network.** Bind it to localhost or a private lab segment. If you find a path
that exposes it by default, or a deploy artifact in this repo that binds it
publicly, that is a real finding.

**The repository contains offensive tooling and attack content.** 177 scenarios
invoke real security tools and emulate real adversary behaviour, mapped to MITRE
ATT&CK. That is the product, not a leak.

**Some content is intentionally vulnerable.** `sources/cortex-vulnerable-llm`
(OWASP LLM01–LLM10) and `sources/cortex-malicious-agentic-pack` (typosquat MCP
server, backdoored package, over-scoped extensions) exist to be detected. Their
side effects are gated behind `CORTEXSIM_C2_URL` so static scanning is safe.
Scanner hits on these paths are expected.

**Scenarios run commands as service accounts.** The identity harness executes
steps via `runuser` / `sudo -u` / `su` to build realistic process-causality
chains. That is the mechanism under test.

## Security properties this project DOES hold

These are enforced and guarded, and a way around any of them is a genuine
vulnerability:

- **No write path to Cortex.** `CORTEXSIM_XSIAM_ALLOW_WRITE` and
  `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE` default off. Tenant access is opt-in and
  read-only; `make launch-preflight` asserts the effective runtime default.
- **Credentials are encrypted at rest** under `CORTEXSIM_SECRET`. With
  `CORTEXSIM_ENV=production`, SimCore refuses to boot when that key is empty, a
  known placeholder, or shorter than 32 bytes. Note the scope precisely:
  development mode logs the same warning and boots anyway, so a dev deployment
  left on a weak key is not protected by this guard.
- **Staged tool artifacts are digest-pinned.** The payload shelf verifies
  SHA-256 against a value the consumer carried in, never one fetched from the
  server being trusted; a mismatch refuses the launch with
  `PAYLOAD_PIN_MISMATCH`.
- **Push bundles are self-contained.** A generated bundle runs on a clean
  Ubuntu 22.04 host with no SimCore dependency at runtime, and a CI guard
  asserts no bundle references a SimCore endpoint.
- **C2 frameworks are never auto-staged.** Gated adapters require explicit
  launch consent (`consent.simulation_authorized` / `c2_authorized`).
- **Enrollment tokens are TTL-bounded, max-use-capped, and revocable.**

## Operating boundary

Run CortexSim **only** against systems you own or are explicitly authorised in
writing to test. It produces telemetry that a SOC will treat as a genuine
intrusion — coordinate before you detonate. Using it against systems you do not
have permission to test is unlawful in most jurisdictions and is not a supported
use of this project.
