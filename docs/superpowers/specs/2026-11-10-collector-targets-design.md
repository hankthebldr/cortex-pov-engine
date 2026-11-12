# Collector targets — design

Date: 2026-11-10 · Branch: `feature/api-collector-targets` · Status: Henry said "keep
going" in place of answering the remaining questions, so the calls marked
**[my call]** are mine and open to veto.

## 1. Intent

A Domain Consultant enters the tenant-side ingestion doors once per POV (the XSIAM
HTTP log collector, a forwarder in front of it, and — once modelled — the Broker VM),
checks each one before the customer is watching, and every campaign and offline
bundle in that POV uses them with nothing re-entered.

Henry, verbatim: *"we need to also ensure that the different tenant components are
correctly configured/correctly e.g. BVM targets, or HTTP event Collector Targets --
we want this to be configurable per session and persistant across the test"*.

What he settled in this session:

- A **session is one POV** against one tenant.
- *"Each POV we will provision a lab environment, and then spin up a new
  instance/base image of the POV engine. It's not designed to be persistent or
  portable."* So **the SimCore instance is the session**. There is no session
  object. *Persistent* means surviving a SimCore restart or container recreate
  inside that POV, which the data volume already gives.

## 2. What is wrong today

A collector destination reaches an emitter by four paths that share nothing
(survey: `side-session-notes/…/collector-targets-survey.md`):

| Path | URL | Token |
|---|---|---|
| EAL campaign (console, API) | typed into each step's `params.collector_url` | `params.auth_token`, **stored in plaintext** in `eal_campaigns.spec` and redacted only on read |
| Scenario step on a beacon (89 steps, the EAL CLI) | `$COLLECTOR_URL` from the beacon's shell; **nothing sets it**, so it defaults to `collector.cortexsim-canary.invalid`. 6 steps hard-code the `.invalid` URL | none |
| Offline bundle | baked from the campaign | env var on the jumpbox |
| Composer in-process EAL step | `step.eal.params` | same; hard-coded dry-run |

The existing preflight (`POST /api/eal/campaigns/{id}/collectors/preflight`) is per
campaign, takes the token per call, and always writes a canary record. There is no
check that leaves the tenant untouched.

## 3. Goals, non-goals, anti-goals

**Goals**

1. Collector targets are instance configuration: created, edited and checked in the
   console and over the API; they survive a SimCore restart.
2. Each target's key lives in the existing encrypted vault and is never returned by
   any response.
3. Every campaign launch, campaign collector view, campaign preflight, offline bundle
   and Composer EAL dispatch resolves its destination from the targets.
4. A per-target preflight distinguishes **not configured · unreachable · refused ·
   accepted**, and only an explicit opt-in writes a record into the tenant.
5. A live launch with a missing or failing target is refused; one with an unproven
   target runs but says so on the run record.

**Non-goals (this branch)**

- **Agent-run scenario steps.** The 89 steps that run the EAL CLI on a beacon need
  the target, key included, delivered into a step that the identity harness runs
  under a login shell (`runuser -l`), which wipes the beacon's environment. Getting a
  key there without putting it on a command line (where `ps` shows it to every local
  user) is a beacon change: a per-step `env` on the task, a `step-env` capability,
  and environment pass-through on each identity wrapper. That is a separate
  `agent`-area branch (§10). Push bundles for those scenarios ride with it.
- **Broker VM targets — on hold.** Henry, 2026-11-10: *"hold off on the broker VM
  specifics -- I can confirm what applets shortly"*. Every collector emitter today is
  an HTTP POST. Which Broker VM applets a POV uses decides whether a Broker VM target
  is a new kind (an HTTP receiver) or a new transport (for example syslog). Note for
  that decision: the tenant-setup research
  (`docs/superpowers/specs/2026-11-10-tenant-setup-research/`) lists
  no HTTP receiver among the documented applets, and #178 reworded the delivery
  remediation on that basis, while the mechanics ledger still carries a "Broker VM
  HTTP applet" as an open question.
- Carrying targets between instances (export/import). Henry: not portable.
- Moving the plaintext `auth_token` that existing campaigns already store into the
  vault. Targets stop new keys landing there; the old field is left as it is.

**Anti-goals** — it fails even if it "works" when:

- a campaign reads green having ingested nothing;
- opening a page, or a background job, writes a record into the customer tenant;
- a collector key appears in any API response, log line, campaign spec, run record
  or bundle;
- "no targets configured" and "all targets healthy" render the same.

## 4. Model

New table `collector_targets` (created by `create_all`; no migration on existing
tables except §7's run column).

| field | notes |
|---|---|
| `name` | unique slug `^[a-z0-9][a-z0-9-]{1,47}$`, the handle steps and runs use |
| `kind` | `xsiam_http_collector` · `http_forwarder` **[my call]** — the label decides defaults and the configured-rung rules, not the transport. Broker VM kinds wait on §10 |
| `url` | full URL, http/https, host required. A pathless URL warns; an `xsiam_http_collector` whose path is not `/logs/v1/event` warns (the console shows the exact path, so a warning, not a refusal) |
| `dataset` | optional label. An XSIAM HTTP collector writes one dataset fixed in its own config; this is what the DC sees, not something sent |
| `auth_header` | default `Authorization` |
| `auth_scheme` | default empty — the XSIAM collector reads the bare key and refuses `Bearer` (#178) |
| `plugins` | the collector plugins this target serves |
| `is_default` | at most one target; serves collector plugins nobody bound |
| `secret_name` | vault `Secret` holding the key, `collector-target:<name>`; null when no key |
| `last_check` | JSON: the last preflight result (§6) |

**Several targets of one kind are normal [my call]**: an XSIAM HTTP collector is one
URL, one key and one dataset, so a POV streaming CloudTrail, Okta and NGFW logs needs
one per dataset.

**The key goes in the existing vault [my call].** `CredentialStore.put(...,
type_hint="collector_token")`. Not an `IntegrationCredential` row: those are read by
the connector and health code as "Cortex credentials configured", and a collector key
authorises nothing on the Cortex API.

## 5. Resolution — one function, every consumer

`core/eal_simulator/targets.py`, pure, no I/O. For each step whose plugin's params
model declares `collector_url`:

1. **explicit** — `params.collector_target: <name>`; an unknown name is an error.
2. **binding** — the one target whose `plugins` lists the step's plugin; two targets
   listing it is `COLLECTOR_TARGET_AMBIGUOUS`.
3. **default** — the default target.
4. **inline** — no target resolved but the step carries its own `collector_url`:
   used, and labelled `inline`.
5. **none** — `COLLECTOR_TARGET_NOT_CONFIGURED`.

**A resolved target wins over an inline URL [my call]**, and the resolution records
the URL it replaced. Henry asked for every launch to take its destination from the
session rather than from per-launch parameters, and an inline URL is a per-launch
parameter.

The resolver returns a params overlay (`collector_url`, `auth_header`,
`auth_scheme`, and `auth_token` only when the caller asks for secrets) plus the
target's exact `host:port`, which is added to the campaign's effective
`target_allowlist` **[my call]**: configuring a target is the operator naming that
destination, and the pin is port-exact so it widens nothing else.

Callers: campaign create (so a step may omit `collector_url`), `GET …/collectors`,
`POST …/collectors/preflight`, launch, bundle, and the Composer in-process dispatch.
The stored campaign spec is never rewritten; the key is read from the vault at launch
and lives only in the in-memory campaign handed to the executor.

## 6. Preflight — a ladder, with the write rung opt-in

`POST /api/collector-targets/{name}/preflight` `{ "write_canary": false }`

| rung | how | proves | does not prove |
|---|---|---|---|
| configured | URL valid; key present when the kind needs one (`xsiam_http_collector`) | the row can be used | anything about the network |
| reachable | DNS + TCP connect + TLS handshake; **no HTTP request** | the door exists from SimCore | that it accepts records, or that the jumpbox can reach it |
| accepted | only with `write_canary: true`: POST one discard-safe record (the existing canary) | the collector returned 2xx to SimCore's POST contract | that the record was parsed, landed in the dataset, or that a detection fires |

Verdict is the highest rung passed or the first failed:
`not_configured` · `unreachable` · `reachable` (accepted not checked) · `refused`
(401/403/404/405/redirect/intercept…, the existing delivery taxonomy) · `accepted`.
Every response carries `records_written` (0 or 1) and `tenant_verified: 0`.
The result is stored on the target. Transport is injectable; tests never touch a
network.

## 7. Launch gating

On a **live** `POST /api/eal/campaigns/{id}/launch`:

- a collector step resolving to `none`, an unknown target, or an ambiguous binding →
  **422**, naming the steps;
- a resolved target whose stored check is `not_configured`, `unreachable` or
  `refused` → **409 `COLLECTOR_TARGET_FAILING`**, "re-run its preflight";
- a target never checked, or only `reachable` → runs, with
  `COLLECTOR_TARGET_UNPROVEN` in the launch response's `degraded[]`;
- an inline URL → runs, with `COLLECTOR_TARGET_INLINE`.

The resolution snapshot (target name, URL, source, check verdict, `has_key` — never
the key) is stored on the run (`eal_campaign_runs.collector_resolution`, JSON, added
by a `database.py` migration helper) and returned by `GET /api/eal/runs/{id}`. The
delivery ledger still decides green: only 2xx counts.

Dry runs are never refused; their resolution is recorded the same way.

## 8. API

`/api/collector-targets` — `GET` (list + `collector_plugins[]` + `unbound_plugins[]`,
the plugins that would refuse a live launch), `GET /{name}`, `PUT /{name}` (create or
replace; `token` write-only, omitted = keep, `clear_token: true` = remove),
`DELETE /{name}`, `POST /{name}/preflight`. No response contains a key or a key tail.
The API is unauthenticated until the hardening work lands, so the guarantee is the
absence of the key from every response, not access control.

`GET /api/collector-targets` itself reads **degraded** (`NO_COLLECTOR_TARGETS`) with
zero targets, because a fresh instance cannot deliver collector signal.
**Deferred [my call]:** surfacing the same count in `/api/health` `eal_collectors`.
`core/api/health.py` is being rewritten on the in-flight hardening branch, so that
change waits for it to land. Until then the health probe's allowlist check can
read degraded for a campaign whose inline URL a target replaces: a false
degraded, never a false ok.

## 9. Console

The **Tenant** page (`#/tenants`) gains a *Collector targets* section under the
read-only tenant: list with kind, host and path, dataset, bound plugins, key yes/no,
and the last check; an add/edit form (key field write-only); **Check reachability**;
and **Send test record**, which states before it runs that it writes one
discard-safe record into the tenant. Plugins no target serves are listed as a
warning, never hidden. The campaign builder's `collector_url` field becomes optional
when a target serves that plugin.

## 10. Follow-ups

**Broker VM targets** — once Henry confirms which applets a POV uses: a new kind if
an applet receives HTTP, or a transport (and kind) if it is syslog or another
protocol, with its own reachable rung (port check) and accepted rung.

**Agent-run scenario steps (separate branch)**

`feature/agent-collector-env`. Resolve per step from the plugins named in the step's
command text (2 of 89 steps name two collector plugins; both NGFW); ship `COLLECTOR_URL`,
`COLLECTOR_HOST` and the key as a per-step `env` on the beacon task; the beacon sets
the child's environment and passes those names through `runuser -w` / `su -w`; a
beacon without the `step-env` capability is refused at launch; the EAL CLI reads
`CORTEXSIM_COLLECTOR_TOKEN` for steps with no key; the 6 hard-coded `.invalid` steps
move to `$COLLECTOR_URL`; push bundles default the URL and read the key from the
operator's environment.

## 11. Proof

Guard tests written first and observed failing: resolver precedence and refusal
codes; key absent from every response and from the stored campaign spec; targets
survive an engine dispose and re-open on the same SQLite file; each preflight verdict
from an injected transport, and no POST unless `write_canary`; each launch-gate
outcome; bundle manifest from the target with no key in the archive. Then the host
backend suite, vitest, and `make validate`. `tenant-verified` stays 0: nothing here
has run against a live collector.
