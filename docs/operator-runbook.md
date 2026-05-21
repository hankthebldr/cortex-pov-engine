# CortexSim Operator Runbook

For Palo Alto Networks Domain Consultants running detection-simulation
POVs in customer labs.

## Audience

You're a DC. You've been handed a CortexSim deployment that's already
running against a customer's Cortex environment (XDR / XSIAM / Cloud /
ITDR), and you need to drive a POV to a successful detection demo. This
runbook is the operator-side reference: what each console feature does,
when to reach for it, and what to do when something doesn't work.

If you need the installation or deployment side, see the in-tree
`install.sh` and `CORTEXSIM_AGENT_CONTEXT.md`.

## The console at a glance

Five tabs across the top:

| Tab | When you use it |
|---|---|
| **Operations** | Browse scenarios. Pick one. Click → drawer slides in with Launch. |
| **In-Flight** | Watch a running scenario. The narrative timeline is the POV money shot — screenshot it for the customer deck. |
| **Evidence** | Validate detections one by one. Export the POV report. |
| **Lab** | Generate IaC bundles to stand up the target environment. |
| **ATT&CK Coverage** | Two views: ATT&CK matrix (what techniques the library covers) and PANW Stack (which products carry the load). |

Two strips that frame the workspace:

| Strip | What it shows |
|---|---|
| **Telemetry strip** (top, when a run is active) | Active scenario, step `N/T`, elapsed time, detected `X/Y`, next step, Abort button. Always visible. |
| **Command strip** (bottom) | Keyboard hints and a live event ticker. |

## Keyboard shortcuts

| Keys | What |
|---|---|
| **⌘K** | Command palette — fuzzy-search scenarios, jump to any tab. |
| **⌘F** | Filter palette — slice scenarios by tactic, technique, actor, identity, difficulty, detection type, tag. |
| **⌘L** | Launch the currently selected scenario (drawer must be open). |
| **⌘E** | Export the POV report for the active or most recent run. |
| **⌘/** | Help overlay — this reference, plus shortcuts and tab cheatsheet. |
| **esc** | Close any open palette / drawer / overlay. |
| **↑ ↓ ↵** | Navigate and select within palettes. |
| **? theme=legacy** | (URL flag) revert to the legacy light theme during the soak period. |

First-time visitors see the help overlay automatically once per browser.

## A typical POV run

### 1. Plan

Open the **ATT&CK Coverage** tab and switch to the **PANW Stack** view.
This is the slide-deck artifact: a product × kill-chain matrix that
shows your customer where their Cortex investments overlap and where
XSIAM's stitching layer reinforces single-product detection signal.

Pick the scenarios that exercise the customer's biggest detection
priorities. Pin them (Operations → click card → Pin button) so they
surface in the rail and in ⌘K's quick-launch section for the rest of
the week.

### 2. Prep the lab (Lab tab)

For a hands-on lab the customer doesn't have today, hit **Lab**:

1. Pick the provider (AWS is current; GCP/Azure phased).
2. Pick the modules. `base` is always required. Click any other module
   and it auto-selects its dependencies (transitively — picking `cdr`
   also picks `base + tim`).
3. Fill the parameters. Project name + SSH CIDR are required; the
   form validates inline as you type.
4. **Generate Bundle** → tar.gz downloads. Hand it to the lab admin
   who runs `terraform apply` against the customer's sacrificial
   account.

### 3. Launch

From **Operations**:

1. Filter to the plane or technique you want (rail or ⌘F).
2. Click the scenario card → drawer slides in with the Launch CTA
   pinned at the top.
3. Pick mode (Pull = beacon agent executes; Push = self-contained
   bundle for offline execution), identity, and (for Pull) the
   target agent.
4. **Launch** (or ⌘L). Drawer closes; the telemetry strip lights
   up; the In-Flight tab is now live.

### 4. Watch (In-Flight tab)

The narrative timeline is the artifact. Each scenario step appears as
a node on a horizontal track:

- **Idle** node = step hasn't fired yet.
- **Pending** (amber pulse) = step is currently executing; SimCore is
  waiting for the agent's heartbeat.
- **Done** = step completed; detection cards underneath turn teal as
  each plane's signal lands.
- **Stitch arcs** physically draw between adjacent step nodes when
  XSIAM correlates them into a single incident. This is the POV
  money shot — a visual representation of "five alerts → one
  incident" that customers immediately understand.

Hit **Screenshot** in the footer to download a PNG at 2× pixel
ratio for the deck.

### 5. Validate (Evidence tab)

When the run completes, the scorecard fills in. For each expected
detection:

- ✓ Detected (green) — found the alert in Cortex console, copy the
  alert ID into the operator notes.
- ✗ Missed (red) — sometimes legitimate (e.g. report-only mode in
  the customer's environment); write up the reason.
- ○ Pending (amber) — waiting on you or on Cortex Data Lake
  ingestion (30–120s typical).

**Click any scorecard row** to open the drill-down side panel. There
you'll find:

- Timing: executed_at, observed_at, computed MTTD
- The raw alert ID with one-click copy (paste into the Cortex
  console search to jump to the causality graph)
- An operator notes textarea (notes ride with the validation into
  the POV report)
- Mark detected / missed / reset, all in one motion

Use **Validate all** when you've eyeballed every alert in the
Cortex console and confirmed everything fired. Or validate
row-by-row if you want notes attached.

### 6. Export (⌘E or Evidence → Export POV report)

Generates a Cortex-branded markdown report containing:

- Run metadata (scenario, identity, mode, agent)
- Detection scorecard with per-row notes
- MTTD distribution
- Coverage percentage
- XSIAM stitching incident count

Drop the markdown into your POV debrief deck or convert to PDF with
your tool of choice. The format is intentionally pandoc-friendly.

## When things go wrong

### "No agents connected"

If you're using Pull mode and the agent dropdown is empty:

```bash
./bin/cortexsim-agent --server http://<simcore-host>:8888 \
    --id <hostname> --interval 10
```

Confirm with `GET /api/agents`. The agent self-registers on first
heartbeat.

### "Run started" but no telemetry strip

The run is in SimCore but the agent isn't polling. Check:

1. Network — agent → SimCore on port 8888 reachable?
2. Agent identity — does the agent's `--id` match what you chose in
   the drawer?
3. Agent logs — `journalctl -u cortexsim-agent` or stdout if running
   interactively.

### Detection never arrives

The Cortex Data Lake ingestion window is 30–120s typical. After 5
minutes with no signal:

1. Check the **In-Flight** view's step status — did the step
   actually execute, or did the agent error out?
2. Check Cortex console directly — did the BIOC or correlation rule
   fire under a different alert title than expected? Update the
   expected `description` in the scenario YAML if so.
3. Check the customer's XDR policy — `Prevention mode = block`
   means the action was prevented before the BIOC fired. Switch to
   `Report` mode for the POV scenario.

### Scenario library is empty or partial

SimCore loads scenarios from `scenarios/` on startup. Confirm with
`GET /api/scenarios`. If the API returns less than the YAML count,
check SimCore's startup logs for Pydantic validation errors — a
malformed scenario YAML is rejected at boot but the rest still
load.

### Push bundle won't run on the target

If `./cortexsim-bundle-{id}.sh` fails on a freshly-provisioned
Ubuntu 22.04:

1. Confirm shellcheck passes locally — Tier B CI gate would have
   caught most issues. `shellcheck --severity=warning bundle.sh`.
2. Confirm every identity (`www-data`, `nobody`, `postgres`, etc.)
   exists on the target — `id www-data`. The IaC `base` module
   creates them; if you skipped that module, you'll need to
   `useradd` them by hand.
3. Confirm the agent identity harness functions are available —
   `runuser`, `su -s /bin/bash`, `sudo`. Standard on Ubuntu;
   missing on minimal containers.

## The PANW value narrative

When a customer asks "what does CortexSim actually prove for me?",
the answer in three lines:

1. **Coverage** — for every TTP a real adversary uses, here's the
   Palo Alto product that should detect it. The PANW Stack matrix
   shows where coverage is dense vs. where it leans on a single
   product.
2. **Stitching** — XDR alone catches the endpoint half. CDR alone
   catches the cloud half. Only **XSIAM** stitches them into one
   incident with a five-step timeline. The narrative timeline is
   that story rendered live.
3. **Response speed** — XSOAR auto-contain playbooks close the loop:
   from first BIOC to IAM key disabled in under 5 minutes. The
   POV report's MTTD distribution surfaces this number per
   detection.

## What's next

When you're done with the POV:

- Pin scenarios you'll reuse so next session's setup is one click.
- Export the report and attach it to the customer's CRM record.
- Capture the timeline screenshots for the executive debrief deck.
- File any "scenario didn't detect" findings against the scenarios/
  YAML so the library improves for the next DC who runs it.

## Pointers

- **Quick start (10 min from clone to first run)**: `docs/quick-start.md`
- **E2E methodology**: `docs/design/e2e-execution-methodology.md`
- **Console redesign design doc**: `docs/design/console-redesign.md`
- **In-tree spec**: `CORTEXSIM_AGENT_CONTEXT.md`
- **Help overlay (⌘/)**: in-app keyboard reference
