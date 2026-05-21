# CortexSim Quick Start — 10 minutes from clone to first run

For Palo Alto Networks Domain Consultants who need CortexSim running
fast on a laptop or a lab jumpbox. After this, see
`docs/operator-runbook.md` for the operator-side workflow reference.

## Prerequisites (one-time)

```bash
# Python 3.11 + Go 1.21+ + Node 20+ + Docker
brew install python@3.11 go node docker          # macOS
# OR
sudo apt-get install -y python3.11 golang-1.21 nodejs docker.io shellcheck
```

A `CORTEXSIM_SECRET` env var with at least 32 bytes of entropy. For
local dev:

```bash
export CORTEXSIM_SECRET=$(openssl rand -hex 32)
```

In production point that at a real secrets manager (1Password CLI,
AWS Secrets Manager, HashiCorp Vault).

## Boot — local dev (3 minutes)

```bash
git clone https://github.com/hankthebldr/cortex-pov-engine
cd cortex-pov-engine

# SimCore (Python FastAPI)
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r core/requirements.txt
cd core && CORTEXSIM_ENV=development \
  CORTEXSIM_BASE_DIR=$(pwd)/.. \
  uvicorn main:app --host 0.0.0.0 --port 8888 --reload &
cd ..

# UI
cd ui
npm install
npm run dev &     # http://localhost:5173 (proxies /api to :8888)
cd ..

# Agent (run on the target host you want to attack)
cd agent && go build -o ../bin/cortexsim-agent .
cd ..
./bin/cortexsim-agent --server http://localhost:8888 --id $(hostname) --interval 10 &
```

You should now have:
- SimCore API at `http://localhost:8888`
- UI at `http://localhost:5173`
- One agent registered (visit `/api/agents`)

## Boot — Docker (1 minute)

```bash
export CORTEXSIM_SECRET=$(openssl rand -hex 32)
docker compose up -d --build

# UI is served by SimCore at http://localhost:8888 once `npm run build`
# has run and the dist/ contents are copied to core/static/
```

## First run (4 minutes)

1. Open `http://localhost:5173` (or `:8888` in Docker mode).
2. The help overlay appears — read it or dismiss with `esc`.
3. Hit **⌘K** → type `apt29` → ↵. The APT29 cloud cred theft
   scenario opens in the inspector drawer.
4. In the drawer:
   - Mode: **Pull**
   - Identity: **www-data** (default)
   - Agent: your registered agent should appear in the dropdown
5. Hit **Launch** (or **⌘L** with the drawer open).
6. The In-Flight tab activates automatically. Watch the timeline
   nodes light up as steps execute.
7. When done, jump to **Evidence**. Click any scorecard row to drill
   into the detection detail.
8. **⌘E** to export the POV report.

## Operator console verification (2 minutes)

Sanity-check the console on first install:

| Check | How |
|---|---|
| Default theme | Page loads with dark "Mission Ops Console" UI. (`?theme=legacy` reveals the old light UI as fallback.) |
| Help overlay | `⌘/` opens the keyboard reference. |
| Command palette | `⌘K` opens the search/jump palette. |
| Filter palette | `⌘F` opens the multi-criteria filter palette. |
| Pinned scenarios | Pin a scenario from any card or the drawer → reload → it's still pinned (localStorage). |
| Coverage views | ATT&CK Coverage tab → "PANW Stack" toggle switches to the product × kill chain matrix. |
| Theme escape hatch | `http://localhost:5173/?theme=legacy` reverts to the old light UI for any operator who needs it. |

## Run the tests

```bash
# UI unit + component tests
cd ui && npm test                       # ~1s, 117 tests

# Python — API, scenario catalog, push generator
.venv/bin/pytest tests/api tests/engine tests/smoke -v

# TTP static checks (tier A)
.venv/bin/pytest tests/e2e_isolated/test_tier_a_static.py -v

# Push bundle integrity (tier B) — every scenario YAML
.venv/bin/pytest tests/e2e_isolated/test_tier_b_push_bundle.py -v
```

## What's where (top-level repo layout)

```
core/         FastAPI backend — orchestrator, engine, scenario loader
agent/        Go beacon agent — pulls tasks, runs them with identity harness
ui/           React frontend — Mission Ops Console
scenarios/    Scenario YAML library + per-scenario delivery packages
infra/        IaC modules (Terraform — AWS today; GCP/Azure phased)
docs/         Design docs + operator runbook (you are here)
tests/        Pytest + vitest test suites
sources/      Submoduled OSS tools (sliver, atomic-red-team, etc.)
```

## When the install isn't working

| Symptom | Most likely cause |
|---|---|
| `MasterKeyError: CORTEXSIM_SECRET misconfigured` | Forgot `export CORTEXSIM_SECRET=$(openssl rand -hex 32)`. |
| `pydantic.ValidationError` on SimCore startup | A scenario YAML failed schema validation — check `scenarios/_schema.yml` against the failing file. |
| UI loads but `/api/scenarios` returns 404 | UI dev server is up but SimCore isn't — check `lsof -i :8888`. |
| Agent registered but no scenario steps fire | Identity harness can't find the user account. `id www-data` on the agent host. The IaC `base` module creates these; minimal containers won't have them. |
| Push bundle fails on target | Run `shellcheck --severity=warning bundle.sh` — should pass cleanly per the Tier B CI gate. If it doesn't, regenerate from the latest scenario YAML. |

## Next

- `docs/operator-runbook.md` — the workflow reference for running a POV
- `docs/design/console-redesign.md` — UI architecture and design tokens
- `docs/design/e2e-execution-methodology.md` — test methodology + Tier C roadmap
- `CORTEXSIM_AGENT_CONTEXT.md` — full architecture spec
