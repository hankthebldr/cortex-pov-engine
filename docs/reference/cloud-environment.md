# Claude Code cloud environment — CortexSim

Field-by-field configuration for running this repo in a **Claude Code cloud
session** (the "cloud" sessions at [claude.ai/code](https://claude.ai/code)).

> **Read this first.** Claude Code cloud sessions do **not** read
> `.devcontainer/devcontainer.json`. Dev containers are a *local* sandboxing
> mechanism; cloud sessions run on Anthropic-managed Ubuntu 24.04 VMs whose
> configuration lives in the web UI, not in this repo. Adding a devcontainer
> file here and expecting cloud sessions to pick it up would silently do
> nothing — which is why this is a document plus a script rather than a config
> file. Sources: [Configure cloud
> environments](https://code.claude.com/docs/en/cloud-environments), [Claude
> Code on the web](https://code.claude.com/docs/en/claude-code-on-the-web),
> [Choose a sandbox environment](https://code.claude.com/docs/en/sandbox-environments).

What the repo *does* contribute is `scripts/cloud-setup.sh` (invoked by the
Setup script field below) and the usual `.claude/` + `CLAUDE.md`, which cloud
sessions read normally.

---

## 1. Where to configure

claude.ai/code → the environment selector (cloud icon above the message box) →
gear/settings on the environment. Everything in §2 is a field in that dialog.

---

## 2. The fields, line by line

### Name
```
cortex-pov-engine (dev)
```

### Network access
```
Custom
```
**Why not `Trusted`.** Trusted covers package registries and GitHub, which gets
`pip` / `npm` / `go` working — but this repo additionally needs the **Playwright
CDN** for the layout guard's chromium, and Docker Hub for base images pulled by
`make build`. `Full` would work but gives up the egress control that makes a
detection-simulation repo safe to run in a shared sandbox. Use `Custom` and
list only what is needed.

### Allowed domains
One per line:
```
registry.npmjs.org
pypi.org
files.pythonhosted.org
proxy.golang.org
sum.golang.org
static.crates.io
crates.io
index.crates.io
github.com
objects.githubusercontent.com
codeload.github.com
registry-1.docker.io
auth.docker.io
production.cloudflare.docker.com
playwright.azureedge.net
playwright-akamai.azureedge.net
playwright-verizon.azureedge.net
cdn.playwright.dev
```
Notes:
- The three `playwright*.azureedge.net` hosts plus `cdn.playwright.dev` are the
  browser-download endpoints. Omit them and `npx playwright install chromium`
  fails, which takes `make check-ui-layout` with it.
- The `registry-1/auth/production.cloudflare` Docker trio is what `docker pull`
  actually talks to; `docker.io` alone is not sufficient.
- `static.crates.io` + `index.crates.io` are only needed if you build the Rust
  submodule tools (`make rust-dist`). Drop them otherwise.

### Environment variables
`.env` format, one per line:
```
CORTEXSIM_ENV=development
CORTEXSIM_PORT=8888
CORTEXSIM_VERSION=1.0.0
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
PUPPETEER_SKIP_DOWNLOAD=1
```

**`CORTEXSIM_SECRET` is deliberately NOT in that list.** It is the credential
**vault master key** — `core/config.py::validate_master_key` enforces ≥32 bytes
and rejects a denylist of defaults, and anything encrypted under one value
cannot be read back under another. Two consequences:

1. **Do not generate it per session.** A fresh secret each time orphans every
   stored integration credential in the persistent volume.
2. **Do not paste it into Environment variables on a Team/Enterprise plan.**
   That field is plaintext and readable by everyone with access to the
   environment.

| Plan | How to supply it |
|---|---|
| Pro / Max | **API credentials** (added after the environment exists). The value stays outside the session VM. |
| Team / Enterprise | API credentials are not available yet. If you must use the env-var field, treat the value as shared-readable and use a **lab-only** secret — never one that has encrypted anything real. |

If a session only needs to build and test (no credential vault, no tenant
integration), set a throwaway value and accept that the vault is empty:
```
CORTEXSIM_SECRET=<64 hex chars, lab-only, never reused>
```

### Setup script
```bash
bash scripts/cloud-setup.sh
```
That is the whole field. The logic lives in the repo so it is reviewed and
versioned; see the header of `scripts/cloud-setup.sh` for what it does and,
more importantly, what it deliberately does not.

### API credentials *(Pro/Max only, after creation)*
Add `CORTEXSIM_SECRET` here rather than as an environment variable, per the
table above. Bind it to the hosts that actually need it.

---

## 3. What the setup script does — and the one constraint that shapes it

The setup script has a **~5 minute timeout** and must exit 0, or the
environment will not start. That single limit dictates the design:

| Done in setup | Deliberately deferred to the session |
|---|---|
| Python 3.11 via `uv` (the image pins only "Python 3.x"; sqlalchemy 2.0.30 does not import on 3.14) | `make build` / `make up` — four builder stages (python, Go beacon matrix, Rust musl, vite) would blow the timeout |
| `pip install -r core/requirements.txt` + pytest/pyyaml/httpx | `npx playwright install --with-deps chromium` (~200 MB) |
| `npm ci` in `ui/` with browser download skipped | Anything needing a running SimCore |
| Version checks for Node / Go / Docker, reported as an explicit summary | |

The script is **non-fatal by design and exits 0 unconditionally**: a session
that comes up with a warned-about gap is more useful than one that refuses to
start. It prints a summary block naming exactly what is present and what is
missing — the same rule this repo applies to `/api/health`, where a degraded
state must never render as a healthy one.

---

## 4. In-session commands

```bash
make build                 # build the SimCore image (not done at setup)
make up                    # bring the stack up on :8888
make test-backend          # pytest inside the built image
cd ui && npm test -- --run # vitest (jsdom — no layout engine, see below)

cd ui && npx playwright install --with-deps chromium
make check-ui-layout       # viewport-matrix layout guard (needs SimCore up)
```

`make check-ui-layout` is the one gate that cannot run under vitest: that suite
is jsdom, which has **no layout engine**, so `getBoundingClientRect()` returns
zeros and every geometry assertion passes vacuously. It needs a real browser and
a running SimCore, hence both commands above.

---

## 5. Limits worth knowing before you plan work

| Constraint | Value | What it means here |
|---|---|---|
| CPU / RAM / disk | 4 vCPU · 16 GB · 30 GB | `make build` is heavy; the Rust musl stage is the slowest part |
| Setup script | ~5 min, must exit 0 | Why the image is not pre-built |
| Environment cache | ~7 days | Setup re-runs after expiry; keep it fast |
| Idle timeout | session stops, VM reclaimed | Long unattended builds can be lost — prefer `make build` in the foreground |
| Egress | Anthropic filtering proxy, allowlisted | Anything not in §2 silently fails to resolve |
| Interactive auth | unavailable | No AWS SSO / OAuth browser flow / MFA — so no live-tenant work from a cloud session |
| Repo bundle | ~100 MB limit for local bundles | This repo pushes that; prefer connecting the GitHub repo over bundling |

The interactive-auth limit is the load-bearing one for this project: a cloud
session **cannot** authenticate to a real Cortex tenant, so it cannot move
`tenant-verified` off 0. Cloud sessions are for building and testing, not for
tenant proof.

---

## 6. Verifying the environment came up correctly

```bash
bash scripts/cloud-setup.sh   # idempotent; re-run to see the summary again
```
Then check the four lines of its summary block. A `NONE` or `MISSING` there is
the answer to "why did this gate not run", and is cheaper to read than the
failure it would otherwise cause twenty minutes later.
