# scenarios/cloud_app

Cloud App Security scenarios — OAuth grant abuse, risky-scope detection,
SaaS application takeover, cross-IdP rotation.

These scenarios exercise **Cortex Cloud App Security** (CASB) and the in-path
NGFW EAL stack against authentic-shape OAuth 2.0 authorize requests carrying
risky scope sets. The plane is `CLOUD_APP`. All scenarios drive the
`oauth_grant_emulator` EAL plugin (Phase 9) — no real OAuth client secrets
or app registrations are used; the request fails at the IdP on the bogus
client_id, but the detection happens at the proxy on the *outbound shape*.

Use case prefix: `UCS-CAPP-xx`

## Scenarios (Phase 9 — 5 active)

| ID | Name | Pattern |
|---|---|---|
| `SIM-CLOUD-001` | Okta Risky OAuth Drive-Scope Grant | risky_drive |
| `SIM-CLOUD-002` | Microsoft Admin-Consent-Required Scope Request | admin_consent |
| `SIM-CLOUD-003` | Google Full-Mailbox + Offline Token Replay Risk | full_mailbox |
| `SIM-CLOUD-004` | Cross-Provider OAuth Grant Rotation (Okta → MS → Google) | cross-IdP rotation |
| `SIM-CLOUD-005` | Benign OAuth Baseline (FP-suppression validation) | benign control |

## Plugin

[`oauth_grant_emulator`](../../core/eal_simulator/plugins/oauth_grant_emulator.py)
emits HTTP GET requests against the public authorize endpoints of three
providers (Okta, Microsoft Identity Platform, Google Identity) with planted
scope strings. Four scope presets cover the detection surfaces:

- `benign` — `openid email profile` only (control / FP validation)
- `risky_drive` — adds `Files.ReadWrite.All` / `https://www.googleapis.com/auth/drive`
- `admin_consent` — `Directory.ReadWrite.All`, `okta.users.manage`, etc.
- `full_mailbox` — `Mail.ReadWrite + offline_access` (token-replay risk)

Every request carries `X-Simulation-Run-ID` plus an `x_cortexsim_run_id`
query parameter so SOC analysts can filter the simulator traffic in the
NGFW URL log.
