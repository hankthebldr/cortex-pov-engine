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
network.** `docker-compose.yml` publishes the port with `host_ip` taken from
`CORTEXSIM_BIND` (long-form mapping, `"${CORTEXSIM_BIND:-}"`). Unset or empty
means every host address, IPv4 and IPv6, because beacons on the lab network
must reach the port; `0.0.0.0` means every IPv4 address. Set
`CORTEXSIM_BIND=127.0.0.1` for a console-only host, and keep the jumpbox on a
private lab segment. (The long-form mapping is authored and has not yet been
brought up under Docker.) The Helm chart's `api.bindAddress` is rendered into
the ConfigMap as `CORTEXSIM_BIND_ADDRESS`, which nothing in SimCore reads, so
it does **not** restrict where the pod listens; restrict exposure with the
Service type, a NetworkPolicy or the Tailscale ingress instead. `/api/health` states the exposure on every deployment: its
`api_auth` component is permanently `degraded` with `API_UNAUTHENTICATED`
until authentication lands (SEC-01, open). If you find a path that exposes it
beyond that, or a deploy artifact in this repo that binds it to a public
interface, that is a real finding.

**The repository contains offensive tooling and attack content.** 193 scenarios
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
  development mode logs that warning and boots anyway, **except** for the
  `.env.example` placeholder (anything starting `replace-me`), which is refused
  in every environment. If the key changes under a surviving database,
  `/api/health` reports `master_key: MASTER_KEY_MISMATCH` instead of reading
  `ok`.
- **The browser cannot be turned against the API.** CORS is not mounted unless
  `CORTEXSIM_CORS_ORIGINS` names origins, and it never allows credentials. A
  Host guard answers `400 HOST_NOT_ALLOWED` to any `Host` other than
  `localhost`, an IP literal, the machine's own name, or a name in
  `CORTEXSIM_ALLOWED_HOSTS`. That is the DNS-rebinding defence.
- **No caller-supplied argv.** `POST /api/tools/{tool}/start` fills only the
  placeholders a tool's run template leaves open, as single shlex-quoted
  tokens (`400 BAD_TOOL_PARAM`). Before 2026-11 a body could override the
  binary, which was a verified unauthenticated root RCE. The container no
  longer mounts `docker.sock`.
- **No XQL write stage.** An XQL `target` stage, which writes a dataset or
  lookup, is refused with `400 XQL_WRITE_STAGE_REFUSED` on every path that
  sends a query to a tenant.
- **Installer and bundle text is not injectable.** Installer parameters are
  validated before interpolation (`BAD_AGENT_ID` / `BAD_SERVER` / `BAD_TOKEN`
  / `BAD_INTERVAL`). Control characters are refused in single-line Composer
  draft fields and stripped from push-bundle comment lines.
- **`X-Forwarded-For` is trusted only from `CORTEXSIM_TRUSTED_PROXIES`.**
- **Staged tool artifacts are digest-pinned.** The payload shelf verifies
  SHA-256 against a value the consumer carried in, never one fetched from the
  server being trusted; a mismatch refuses the launch with
  `PAYLOAD_PIN_MISMATCH`.
- **Push bundles are self-contained.** A generated bash bundle runs on a clean
  Ubuntu 22.04 host, and a `.ps1` targets Windows PowerShell 5.1, with no
  SimCore dependency at runtime. A CI guard asserts no bundle references a
  SimCore endpoint. None of the 13 shipped `.ps1` bundles has executed on
  Windows; CI has parsed them under Windows PowerShell 5.1 and run one probe
  bundle.
- **C2 frameworks are never auto-staged.** Gated adapters require explicit
  launch consent (`consent.simulation_authorized` / `c2_authorized`).
- **Enrollment tokens are TTL-bounded, max-use-capped, and revocable**, but
  today they do **not** bound who may onboard. `POST /api/agents/register`
  still onboards, and overwrites, an agent row without a token, and minting a
  token is itself unauthenticated. Treat tokens as a convenience for the
  installer flow, not as access control, until operator and agent-plane
  authentication land (SEC-01, SEC-04). Tokens are stored in plaintext in
  SimCore's SQLite database, and a mint allows up to 1000 uses and 30 days
  (decision SEC-16 open);
  `scripts/lab/publish-agent.sh` refuses to bake one into a served installer.
- **The beacon digest proves integrity, not authenticity.** The installer
  fetches the beacon and its sha256 from the same SimCore over the same, by
  default plain-HTTP, channel, so an on-path attacker who can alter one can
  alter both. Both installers say so ("catches corruption, not tampering").
  The beacon is unsigned and runs as LocalSystem or root (SEC-12, CI-06 open).

## Operating boundary

Run CortexSim **only** against systems you own or are explicitly authorised in
writing to test. It produces telemetry that a SOC will treat as a genuine
intrusion — coordinate before you detonate. Using it against systems you do not
have permission to test is unlawful in most jurisdictions and is not a supported
use of this project.

## Known limits that are not yet closed

These are tracked, not hidden. A report that shows one of them is worse than
stated here is welcome.

- **No API authentication** (SEC-01) and **no agent-plane authentication**
  (SEC-04). Any host that reaches the port can drive SimCore or impersonate a
  beacon.
- **The installer checksum guards against corruption, not tampering** (SEC-12).
  SimCore serves plain HTTP, so the digest an installer verifies against comes
  from the same channel as the binary.
- **The Windows beacon is unsigned** (CI-06). Its sha256 and `go version -m`
  build info are published with each release.
- **The image still runs as root.** A non-root `USER` is deferred because it
  cannot be verified without an image run (SEC-03, partial).
- **Run output may contain harvested credentials** that a credential-access
  step printed (SEC-06, open).
