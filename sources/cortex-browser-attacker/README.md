# cortex-browser-attacker

Playwright-driven browser action runner for **Cortex Prisma Browser**
detection validation. Drives the customer's managed Prisma Browser
(or plain headless Chromium for lab POVs) through a YAML-declared
sequence of browser actions and emits garak-shape JSONL.

Companion to the `browser_attack_runner` EAL plugin
(`core/eal_simulator/plugins/`) which forwards every `ActionResult`
into the CortexSim ECS audit pipeline. Prisma Browser → XSIAM is the
customer's existing data path; we just produce the activity.

Design grounded in [Phase 6 of the resolution
strategy](../../docs/brainstorm/2026-05-08-resolution-strategy.md).

## Install

```bash
cd sources/cortex-browser-attacker
pip install -e .[test,playwright]
playwright install chromium       # one-time, ~300MiB
```

The `playwright` extra is optional — without it the package still
loads (the `StubDriver` covers every unit test path). Install the
extra only when you actually want to drive a real browser.

## Run

```bash
# List actions
cortex-browser-attacker list-actions

# Validate a browser campaign without driving anything
cortex-browser-attacker validate \
    --campaign scenarios/browser/campaigns/cred-paste.yml

# Live run against managed Prisma Browser
cortex-browser-attacker run \
    --campaign scenarios/browser/campaigns/cred-paste.yml \
    --browser-channel prisma --headless \
    --out /tmp/browser-001.jsonl

# Stub run (no real browser; useful for plumbing tests)
cortex-browser-attacker run \
    --campaign scenarios/browser/campaigns/cred-paste.yml \
    --browser-channel stub --out -
```

## Browser-campaign schema

```yaml
campaign_id: BC-BROWSER-001
name: "Credential paste into untrusted origin"
authorized_by: "domain-consultant@paloaltonetworks.com"
simulation_authorized: true
target_allowlist:
  - login.cortexsim-test.invalid
dry_run: false
browser_channel: prisma           # prisma | chromium | stub
headless: true
actions:
  - action: navigate
    params:
      url: https://login.cortexsim-test.invalid/signin
      wait_for: 'input[name="password"]'
      expected_detection: "PB DLP — credential paste"
  - action: paste
    params:
      selector: 'input[name="password"]'
      content: "MyCorpSSO!@#-CORTEXSIM-CANARY"
      cortex_canary: "CANARY-CRED-001"
```

## Built-in actions

| Action | Purpose |
|---|---|
| `navigate` | Open URL; optional `wait_for` selector |
| `paste` | Type text into a selector |
| `copy` | Read text from a selector → clipboard |
| `click` | Click a selector |
| `download` | Wait for a JS-triggered download |
| `install_extension` | Sideload a `.crx` (expected to be blocked) |
| `screenshot` | `page.screenshot()` to a path |

## JSONL output

`stdout` is JSONL (one `run_meta` line first, then one
`action_attempt` per action). The summary lands on `stderr`. Field
naming intentionally rhymes with cortex-prompt-attacker's `Attempt`
shape so downstream tooling that reads one of those streams can read
this one.

```json
{
  "entry_type": "action_attempt",
  "uuid": "...",
  "action_name": "paste",
  "target_origin": "login.cortexsim-test.invalid",
  "outcome": "success",
  "duration_seconds": 0.018,
  "notes": {"selector": "input[name=\"password\"]", "chars_typed": 27},
  "cortex_canary": "CANARY-CRED-001",
  "expected_detection": "PB DLP — credential paste into non-sanctioned origin"
}
```

## Safety

- The runner authorises every `navigate` action's hostname against
  the campaign's `target_allowlist` before dispatching to Playwright.
- `dry_run: true` (the default) never spins up a real browser — useful
  for plumbing checks.
- Live execution requires `simulation_authorized: true` and a named
  `authorized_by`.
- `install_extension` reports `outcome=blocked` when the managed
  policy refuses the install — the *attempt* is the detection signal,
  not the install.

## Test

```bash
pip install -e .[test]    # no playwright extra needed; StubDriver suffices
pytest tests/ -v
```

44 tests cover every action via `StubDriver`, the campaign Pydantic
schema, the runner's allowlist enforcement, and the CLI.

## License

Apache-2.0. See [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md).
