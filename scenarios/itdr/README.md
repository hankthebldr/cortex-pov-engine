# scenarios/itdr

ITDR scenarios — identity-behavioural detection (impossible travel,
MFA fatigue, credential stuffing, token replay, brute-force lockout).

These scenarios exercise **Cortex ITDR** (Identity Threat Detection and
Response) and any third-party identity-log parser that the customer has
wired into XSIAM. The plane is `ITDR`. All Phase 9 scenarios drive the
`idp_signin_emulator` EAL plugin, which POSTs synthetic IdP audit events
(Okta system-log shape, Microsoft Entra signInLogs shape, or Google
Workspace login activity shape) at an operator-supplied collector URL.

The plugin **never talks to a real identity tenant** — it builds shape-true
JSON and POSTs it to the collector the customer has already trusted. This
exercises the parser + behavioural rules without locking out a real
account or burning a real session.

Use case prefix: `UCS-ITDR-xx`

## Scenarios (Phase 9 — 5 active)

| ID | Name | Pattern | MITRE |
|---|---|---|---|
| `SIM-ITDR-001` | Impossible Travel (Okta) | impossible_travel | T1078.004 |
| `SIM-ITDR-002` | MFA Fatigue / Push-Bombing | mfa_fatigue | T1621 |
| `SIM-ITDR-003` | Credential Stuffing | credential_stuffing | T1110.004 |
| `SIM-ITDR-004` | Session Token Replay Across Geo / UA | token_replay | T1539 / T1550.004 |
| `SIM-ITDR-005` | Brute-Force Causing Account Lockout | brute_force_lockout | T1110.003 |

## Plugin

[`idp_signin_emulator`](../../core/eal_simulator/plugins/idp_signin_emulator.py)
emits authentic-shape audit events (Okta / Microsoft / Google) to a
collector URL the operator passes via `collector_url`. Five behavioural
patterns are supported:

- `impossible_travel` — two successful sign-ins from geographically distant
  IPs within an impossible interval
- `mfa_fatigue` — N MFA challenges in a short window followed by an
  approval (push-bombing)
- `credential_stuffing` — N failed sign-ins across N user identifiers from
  the same source IP
- `token_replay` — same `session_token_id` reused from a different IP and
  user-agent than the original issuance
- `brute_force_lockout` — N failures against one identity followed by a
  `user.account.lock` state-transition event

Every event carries `cortexsim_run_id` in its body and an
`X-Simulation-Run-ID` HTTP header so SOC analysts can filter simulator
traffic in both the parsed audit log and the NGFW URL log.

## Other ITDR scenarios (not yet ported to EAL plugin)

Pre-Phase-9 ITDR detection (Kerberoast, Pass-the-Hash, DCSync, MFA bypass)
runs against the AD lab provisioned by the `itdr` IaC module — see
[`infra/modules/aws/itdr/`](../../infra/modules/aws/itdr/) and
[`docs/wiki/POV-Runbook.md`](../../docs/wiki/POV-Runbook.md). Those still
use Impacket / Rubeus / Certipy from the identity harness.
