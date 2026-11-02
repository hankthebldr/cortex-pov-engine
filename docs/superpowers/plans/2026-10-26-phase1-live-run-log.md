# Phase 1 live paired run — log

> **Status: NOT RUN.** This is the skeleton for Task 18 of
> `2026-10-26-prevention-efficacy-phase1-engine-contract.md`. It cannot run in CI: it
> needs a Linux VM with the host-installed Cortex XDR agent, a tenant in soakless mode
> with BAS mode on that agent, and the `xsiam` reconcile credential in SimCore. Until
> every section below carries real output, **`tenant-verified` for prevention is 0** and
> nothing on this branch may be quoted as proof the agent prevented anything. Tier-D
> proved the three host witnesses on this host; it has no XDR agent and no run there
> reads `prevented`.

Record every response verbatim as it happens. Redact tenant hostnames and alert ids
only if the log leaves the lab; keep run ids.

## 0. Environment (fill in before step 1)

| item | value |
|---|---|
| Date / operator | |
| SimCore commit (`git rev-parse --short HEAD`) | |
| Beacon build (`cortexsim-agent --version` or `/api/agents/binary` sha256) | |
| Linux VM (distro, kernel) | |
| Cortex XDR agent version | |
| Agent profile name · modules set to **Block** | (owed: Henry's list, spec §6 provisional) |
| BAS mode on this agent | on / off |
| Tenant soakless | on / off · who set it |
| Scenario used | `SIM-TEST-PREV-001` (fixture) or the first Phase 3 scenario id |

## 1. Preconditions (Henry)

- [ ] Agent enrolled to the tenant, profile in **report** mode, BAS mode **enabled**.
- [ ] Soakless set on the tenant.
- [ ] `xsiam` reconcile credential configured (`PUT /api/credentials/integrations`).
- [ ] Beacon enrolled on the VM via the installer one-liner; `GET /api/agents` shows it `online`.
- [ ] `CORTEXSIM_CANARY_SCENARIO_LINUX` set if a canary scenario exists (else step 4 is attested-only).

## 2. Preflight

`POST /api/connectors/xsiam/preflight` — paste the response. The `alert_shape` stage
must report `keys.action` non-null. If it is null the stage reads
`PF_ALERT_SHAPE_PARTIAL` with "the sampled alert carries no action key"; **stop** —
a prevent expectation can never be credited without it (spec §9, Phase 0 first).

```json
```

`clock_skew` stage value (MTTD is measured across two clocks):

## 3. Report run

`POST /api/run` body:

```json
{"scenario_id": "<id>", "mode": "pull", "target_agent_id": "<agent>",
 "agent_profile_mode": "report", "bas_mode_attested": true,
 "soakless_attested": true, "attested_by": "<name>"}
```

- run_id:
- `GET /api/runs/{id}` after `complete` — `status`, `severed_at_step` (expect null), `step_outcomes[]`:

```json
```

- `POST /api/runs/{id}/reconcile?connector=xsiam` — response:

```json
```

- Expected: every expectation `detected`; the `prevent` expectation reads
  `detected / DETECTED_NOT_PREVENTED` (report mode cannot prevent). If it reads
  `ATTESTATION_CONTRADICTED`, the agent blocked anyway → the profile was not report; fix
  and re-run this step. Actual:

## 4. Flip the profile to block (Cortex console), then canary

- Profile change: who, when, which modules.
- `POST /api/runs/canary` body `{"target_agent_id": "<agent>"}` — response (or
  `409 CANARY_NOT_AVAILABLE` → proceed attested-only and say so here):

```json
```

- Canary run after `complete` + reconcile: the `prevent` Result and `block_mode_status`:

## 5. Block run

`POST /api/run` body:

```json
{"scenario_id": "<same id>", "mode": "pull", "target_agent_id": "<same agent>",
 "agent_profile_mode": "block", "bas_mode_attested": true,
 "soakless_attested": true, "attested_by": "<name>",
 "paired_with_run_id": "<report run id>", "canary_run_id": "<canary run id or omit>"}
```

- run_id:
- Expected on `complete`: `severed_at_step` set to the prevent step; the beacon still
  `online` (BAS mode); `step_outcomes[]` shows the prevent step `signaled` or
  `anchor_terminated` and later steps `not_run / UPSTREAM_SEVERED`.
- `GET /api/runs/{id}` — paste `status`, `severed_at_step`, `block_mode_status`, `step_outcomes[]`:

```json
```

- `POST /api/runs/{id}/reconcile?connector=xsiam` — response:

```json
```

- Expected: the `prevent` Result `prevented / pass` (two witnesses: an alert whose
  `action_pretty` starts with `Prevented` AND the host termination above); later steps'
  Results `not_reached`. If the Result is `pending`, record its `outcome_reason` — it
  names which witness is missing. Actual:

## 6. Readout

- `GET /api/runs/{block}/pair` — paste the `delta`:

```json
```

- `GET /api/runs/{block}/report` — paste the **Prevention Delta**, **Chain Integrity**
  and **Attestations** sections verbatim:

```
```

## 7. Count

Only when section 5 shows `prevented / pass` with both witnesses in this log: update
`CLAUDE.md`'s `tenant-verified` statement for prevention from 0 to 1, citing this file,
and commit:

```bash
git add CLAUDE.md docs/superpowers/plans/2026-10-26-phase1-live-run-log.md
git commit -m "docs: first tenant-verified prevention — paired run on the Linux VM"
```

If any step above stopped, leave the count at 0 and record where and why here:
