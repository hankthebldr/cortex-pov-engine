# Third-Party Notices — cortex-browser-attacker

This package is original code (Apache-2.0). Runtime dependencies and
the design abstractions they inspired are listed below.

## Runtime dependencies (installed at use-time)

### Microsoft Playwright (Python)
- Project: https://github.com/microsoft/playwright-python
- License: Apache-2.0
- Used as: optional extra (`pip install cortex-browser-attacker[playwright]`)
  — the `PlaywrightDriver` wraps `sync_playwright()` for Chromium /
  Prisma Browser drive.
- Avoided: no source files copied; we wrap the public API only.

### Pydantic
- Project: https://github.com/pydantic/pydantic
- License: MIT
- Used as: campaign + action params schema. Standard runtime
  dependency.

### PyYAML
- Project: https://github.com/yaml/pyyaml
- License: MIT
- Used as: campaign YAML parsing.

## Design abstractions borrowed (pattern only, no source)

### NVIDIA garak
- Repo: https://github.com/NVIDIA/garak
- License: Apache-2.0
- Borrowed: the `Attempt`-shape JSONL record (we name ours
  `ActionResult` but the field naming intentionally rhymes —
  `entry_type`, `uuid`, `seq`, `status`, `outcome`, `duration_seconds`,
  `notes` — so a garak-aware reader can consume our stream with a
  trivial adapter). Schema field names are not copyrightable.

### cortex-prompt-attacker (sibling package)
- In-tree at `sources/cortex-prompt-attacker/`
- Same `Attempt` field shape; same `run_meta` header line convention;
  same "attacker shells out" EAL plugin pattern. The two attackers
  are siblings so SOC tooling that reads one JSONL can read the
  other.

## Out of scope

- No browser fingerprinting / anti-detection evasion (we *want* to
  look like an automated tool — that's the point).
- No real C2; no covert channels.
- No exploitation of browser vulnerabilities (we drive the browser
  through its public API and let the customer's managed policy do
  the detecting).
- No Selenium support (Playwright-only — modern, Python-native,
  headless-first).
