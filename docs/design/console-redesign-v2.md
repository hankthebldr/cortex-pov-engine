# Console Redesign v2 — Guided POV Workflow

> Decided 2026-06-01 in a live design loop. Supersedes the tab-based Mission
> Ops Console IA. Evolves the existing `.theme-console` system (tokens +
> components kept) into a guided workflow + Cortex-green visual language.

## Why

The tab IA (Operations / In-Flight / Evidence / Lab / Coverage) did not map to
a Domain Consultant's actual journey, there was **no visible "where does the
attack run" (target/agent) step**, "Launch" had no obvious destination, and
"Lab" did not convey IaC infra generation. Users could not tell how to use the
app.

## Locked decisions

1. **Navigation — Guided POV workflow stepper.** Primary nav is a numbered
   top stepper:

   ```
   ① TARGETS › ② LIBRARY › ③ LAUNCH › ④ LIVE › ⑤ EVIDENCE      More ▾
   ```

   - `More ▾` holds secondary tools: ATT&CK Coverage, Environments (the IaC
     generator, renamed from "Lab"), TTP authoring (browser/editor), Adapter
     registry, Competitive, PANW Stack coverage.
   - A persistent **collapsible left rail** keeps filters / pins / quick
     target switch (hybrid of stepper clarity + power-user rail).

2. **Color — Cortex green primary, teal secondary.** New token
   `--c-action: #6CC24A` (Cortex green) for primary actions / active / brand
   highlight; existing `--c-signal: #00C0E8` (teal) demoted to secondary /
   info. Detection-state colors unchanged (detected green-teal, missed red,
   pending amber). Dark navy/void base kept. Exact brand hex to be confirmed
   by the user on screen.

3. **Targets — unified hub (all 3 execution modes).** One `① Targets` surface
   presenting every path the backend already supports:
   - **Pull agents** — registered `cortexsim-agent` beacons (live/stale).
   - **Push bundles** — offline self-contained bundle download (no agent).
   - **IaC labs** — environments provisioned via the infra generator.

   Launch (`③`) always asks "against which target?" and auto-sets pull/push
   mode from the chosen target.

## View → step mapping

| Step | Source view | Notes |
|---|---|---|
| ① Targets | NEW | unify agents API + push + infra catalog |
| ② Library | `OperationsView` | scenario browse/filter; launch CTA moves to ③ |
| ③ Launch | `useLaunchScenario` + inspector launch block | target picker + mode + identity |
| ④ Live | `InflightView` | attack narrative timeline |
| ⑤ Evidence | `EvidenceView` | scorecard · validate · export POV |
| More ▾ | Coverage / Lab→Environments / Ttp* / Adapter / Competitive / Stack | secondary |

## Known layout bugs being fixed alongside

- **Flex-fit (FIXED):** `.theme-console .shell` was a descendant combinator but
  the class sits on the same element → compound `.theme-console.shell`.
- **Horizontal overflow ~420px:** ScenarioInspector drawer (`--w-inspector`)
  sat in-flow; make it an overlay/fixed drawer.
- **Plane-select underfill:** short grids leave the view unfilled; content
  region must fill the viewport gracefully (bounded scroll region).
