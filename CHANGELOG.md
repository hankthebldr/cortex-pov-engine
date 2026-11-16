# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
From `v1.0.0` onward this project follows [Semantic Versioning](https://semver.org/);
the earlier `0.y.z` pre-releases (see `v0.1.0`) predate that commitment.

## [Unreleased]

### Changed — cloud posture and the IaC generator move to their own branch (1.4.x)

- **The CSPM, ASM and AI_SPM planes leave `dev`.** Their 18 scenarios and 18
  TTP cards now live on `feature/scenarios-cloud-posture`, which is planned for
  a 1.4.x release. The corpus is 187 scenarios across 13 planes, 1,328
  step-detections.
- **The IaC topology generator goes with them.** That covers `infra/`, the
  `/api/infra/*` routes, `/api/scenarios/{id}/infra-hints`, the jumpbox content
  loader, the console's Lab page and the IaC labs column on Agents. An old
  `#/environments` link opens Agents.
- **The 14 assertions whose stimulus is a Terraform module go too.** These are
  POS-CSPM, POS-DSPM, POS-AISP, POS-ASM and PLT-XTI-001; 14 assertions remain.
  POS-CSPM-004 (the Kubernetes arm) is now the only binder of TC-CSPM-02.
- **Coverage floor.** Reconnaissance (TA0043) has one scenario left, so
  `coverage-strict` takes a per-tactic floor of 1 for it until ASM returns.

### Fixed — the Launch gate says who runs cleanup, on kit controls

- **Teardown names who runs it.** A current agent: "run by the agent after the run"; an older one: "run them yourself".
- **Prevention context reads in sentence case.** "Attested, not verified" is one hint; the canary is the kit's **Run canary**.
- **Each preflight check is one line.** Status icon, one word and the count; the detail sits behind a collapsed **Why**.
- **Less text around them.** No intro paragraph (its point is the Preflight tooltip); tool consent is one compact row, **Required** until ticked.
- **Switch scenario is a kit button**, and the lines under the heading are one short line each.

### Changed — Results on kit controls, a live runtime log, and EAL consent at launch

- **A run's runtime log is live.** Output a step prints while it runs shows in
  that step's block: what the run record held when the page opened, then each
  chunk from the run's stream. When the step ends, its stored output replaces
  the live text. A step the agent has announced reads Running with the command
  the agent reported. Needs a beacon with live output; on an older one the
  page says "Live output needs the new beacon" while the run is live.
- **A command's one-line preview starts with what the step does.** It skips
  the scratch-folder set-up (the `mktemp` line, the shared-sandbox create and
  check lines, and the Windows `$sandbox` block). "Show all" still shows the
  whole command.
- **Cleanup is its own block after the steps.** It says Clean, "1 of 3
  failed", Not run or Pending, with each command, its output and its exit code
  behind one line. It is no longer mixed into the last step's output.
- **What SimCore said about the launch is one line on the run's page**, not
  raw `!!` lines in the log: an offline agent, an unknown agent OS, a missing
  interpreter, runtime install being off, a beacon that does not run cleanup
  or cannot relay EAL steps, and tools the target must already carry.
  SimCore's own sentence is in the tooltip.
- **Results › Detections uses one word per state.** Each row is a status icon
  and one of Detected · Prevented · Missed · Pending · Not reached · Not
  executed · Not reviewed (and Not prevented, for a block that only detected).
  "Awaiting reconcile" is gone: a row nothing has judged reads Pending on a
  reviewed run. The tiles are a label, one number and at most one short line
  ("3 of 6 detected · 2 not counted"); with no MTTD the MTTD panel is one line.
  Row actions and the detail drawer use the kit buttons.
- **A detection's hand marks do what they say.** Reset sent `observed: null`,
  which the API rejects, so it never worked; the ✗ "missed" button withdrew a
  hand mark and left the row Pending, because the API never writes a miss by
  hand. A row now offers Mark detected until it reads Detected, and Reset on a
  row a hand mark made detected (it sends `observed: false`). A row matched to
  an alert offers neither.
- **A scorecard row's MTTD reads the way the tile does** ("0.4s", "42s",
  "1m 15s"). A sub-second detection used to read "0:00" beside a tile saying
  0.4s.
- **Techniques and Topology no longer repeat the run verdict.** The verdict
  line at the top of Results says it once. Their totals and explanations are
  one line each, with the detail in a tooltip.
- **The Launch gate asks before sending live EAL traffic.** A scenario with an
  EAL step shows one row: "Send live EAL traffic", "Authorized by" (remembered
  in the browser) and "Includes C2-shaped traffic". Ticked, the launch sends
  `consent.simulation_authorized`, `authorized_by` and, with the third box,
  `consent.c2_authorized`. Unticked, the launch is unchanged and EAL steps run
  dry, as every console launch did before. The console cannot yet tell which
  plugins SimCore counts as C2-shaped, so the third box shows with the first.
- A launch warning object no longer prints its `level` as text.
- Removed four unmounted views (`InflightView`, `EventStream`,
  `NarrativeTimeline`, `useTimelineData`) and their styles.
- No API, scoring or beacon change.

### Added — Reports rolls up every execution on this engine

- **Reports › Executions.** The first tab of the Reports page lists everything
  this engine has run: runs by status, how many scenarios, techniques and test
  cases they touched, what the host recorded about each step, and the expected
  detections by outcome. One table shows it by scenario, by technique or by
  test case, with filters for time range, plane, status and agent. Each
  scenario row opens its last reviewed run in Results. It downloads as CSV or
  JSON.
- **`GET /api/reports/executions`** is the route behind it. It is read-only,
  makes no outbound call and never loads a run's output. A detection nobody
  reviewed is counted as not reviewed, never as missed. A scenario that never
  ran is counted, not listed at 0 %. Composer drafts are left out unless asked
  for. The CSV quotes text that a spreadsheet would run as a formula.

### Changed — Dashboards is now Reports

- **The Dashboards page is named Reports** in the rail, the heading, the
  breadcrumb and ⌘K. Its address is the same (`#/coverage`), so saved links
  still work. It opens on Executions; Detection source × Use case, MITRE
  ATT&CK and Planes × doors follow as before. The paragraph under the heading
  is gone.
- **One button verb was added** to the console kit: Open run.

### Added — scenario steps that call the EAL CLI run in pull mode (#325)

- **71 scenarios failed on a beacon** at the step that runs
  `python3 -m scripts.eal_simulator.cli`, because that CLI exists only inside
  SimCore. A beacon from this version runs that call through SimCore instead.
  Steps that post records to a collector are sent by SimCore, to the collector
  target set up on this instance. The NDR network steps (C2 beacon, DNS tunnel,
  Stratum, bulk HTTPS upload, SMB sweep, FTP, SSH) are sent by the beacon, so a
  firewall sees the endpoint as their source.
- **Sending needs consent on the launch**: `consent.simulation_authorized` and
  `authorized_by`. Without it the step is a dry run and its results read not
  executed, never missed.
- **Relayed NGFW log-emitter steps (`ngfw_eal_emitter`, `ngfw_threat_emitter`)
  and the C2, DNS, FTP and SSH steps also need `consent.c2_authorized` on the
  launch.** The console does not send `authorized_by` yet, so a console launch
  runs these steps dry until the console change lands. Launch through the API
  to send.
- **Not in pull mode yet:** `llm_provider_egress`, `oauth_grant_emulator`,
  `agentic_egress`, `browser_attack_runner` and `airs_prompt_attack`. Such a
  step stops with `EAL_WIRE_NOT_RELAYABLE` and its results read not executed.
  The FTP step sends its login but not the file, and the SMB sweep does not
  attempt an NTLM session.
- **An older beacon** still fails these steps. The launch now says so
  (`EAL_RELAY_UNAVAILABLE`); update the beacon.
- SIM-NDR-001 and SIM-TIM-001 give their 12-beacon step 600 s. The beacon's
  180 s default stopped it part way.
- The beacon reports every tool it looked for (`runtime_facts.probed`), and now
  looks for `ssh`, `scp`, `sshpass` and `sudoedit`, so a dependency on one of
  those reads found or missing instead of `unverified`.

### Added — pull runs clean up after themselves

- **A pull run left its files, units, cron lines and accounts on the target.**
  Only a push bundle ran the scenario's `cleanup.commands`. The beacon now
  runs them when a run ends: after the last step, a failed step, a timeout or
  an abort. The run's output ends with a `=== CLEANUP` section showing each
  command and its exit code. A failed cleanup does not fail the run (#324).
- **The report says when cleanup did not happen.** "Cleanup Not Run" now
  appears only when no beacon reported a cleanup, and "Cleanup Incomplete"
  when a command failed. A run that cleaned up has neither.
- **Update the beacons.** A beacon from before this change ignores the cleanup
  it is sent, and the launch says so (`CLEANUP_NOT_RUN`, a notice that does
  not hold the launch). Re-install the beacon from this SimCore to get it.
- Windows cleanup lines run under PowerShell or cmd; no Windows host has run
  this path yet. `cleanup.k8s_teardown` is not sent to a beacon.

### Added — the beacon reports each step's command and streams its output

- **A pull run was silent while a step ran, and never showed the command.**
  The beacon posted a step's output once, after the step ended, so a
  three-minute step said nothing for three minutes, and a finished run could
  not show what was attempted. The beacon now tells SimCore the command before
  each step runs and sends the step's output about once a second while it
  runs, chained steps included.
- **What is stored does not change.** The live output is shown on the run's
  event stream and kept in memory until the step ends; only the step's final
  output is stored on the run, exactly as before. A slow or unreachable
  SimCore loses live output, never a step. Outcomes and verdicts are untouched.
- `GET /api/runs/{id}` gains `step_starts` (the command, the account and the
  start time per step; kept after the run ends) and, while the run is in
  flight, `live_tail`. The run event stream gains `stepId`, `live`, `phase`,
  `command` and `seq`; its existing fields are unchanged.
- **Needs the new beacon on the target.** An older beacon keeps working and
  posts each step once, at the end. The console's runtime log view for this
  is a separate change.
- **`--stream-output` is retired.** It posted from inside the step's own
  output pipe, skipped chained steps, and its output was stored twice. The
  beacon still accepts the flag and ignores it; the Kubernetes agent manifest
  no longer passes it.
- Wire contract: `docs/reference/api-and-agent-surface.md` §1.3.3.

### Changed — Runs shows what was attempted; Results shows what Cortex saw

- **A run's page has one panel: the runtime log.** One block per step: the
  command that was attempted, the account it ran as, how it went (Queued ·
  Running · Ran · Failed · Timed out · Did not run · Aborted) with exit code
  and duration, and its output. A finished run is built from the stored output
  and the host's step record; a live run is re-read as its stream moves.
- **Runs no longer shows a detection verdict.** A row, a run's page, its chain
  and the live strip say how the run went and whether it is reviewed.
- **Results holds the detection views.** One verdict line, then Detections ·
  Storyline · Techniques · Topology · Causality · Tenant evidence
  (`#/proof?run=<id>&tab=<tab>`). A run that has not been reviewed says "Not
  reviewed" in every tab. What changes how the results read (another account,
  a platform variant, a refused step, launch warnings, attestations) is one
  collapsed line above the verdict.
- **Old links still work:** `#/runs?run=<id>&tab=evidence|storyline|
  techniques|topology|causality` open the same tab of Results.
- Validate all, the report Download list, and the Runs list's state, pager and
  Abort are console kit controls. Compare runs stays on the Runs list only.

### Fixed — dependency warnings read the beacon's tool facts and show at launch

- **A launch reported `sudo`, `ssh`, `scp` and `sshpass` missing on hosts that
  had them (#327).** The check compared every name in a step's
  `requires_interpreters` with the agent's interpreter list, which only holds
  python, perl, ruby and node. A name that is not an interpreter is now read
  from the tools the beacon reported: found is fine, looked for and not found
  is missing, and a tool the beacon never looked for is listed as `unverified`
  and is not a gap. `GET /api/agents/{id}/preflight` answers the same way.
- **A real gap, and an install request that was ignored, were silent at launch
  (#328).** A pull launch whose target lacks something a step needs now carries
  the warning `RUNTIME_DEPENDENCY_GAP`. One that asks for
  `allow_runtime_install` on a SimCore without
  `CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL` carries `RUNTIME_INSTALL_NOT_ENABLED`.
  Both are on the launch response, on `POST /api/runs/preflight` and in the
  run's output. Neither refuses the launch.
- A beacon's registration may now include `runtime_facts.probed`, the tools it
  looked for. The beacon sends it as of the entry above.

### Changed — the Launch gate is about one scenario; Get started tracks the app

- **The Launch gate read as both "this run" and "the whole app".** Under the
  armed scenario's New POV run it carried "This POVengine": the Cortex
  connector ladder, tenants, attested facts and the SimCore components. It now
  holds only the scenario's launch, says so ("It checks and starts one
  scenario's run"), and links to Get started for the app's own status.
- **Get started tracks this POVengine's status** under its setup steps: the
  same "This POVengine" view, with Re-check. The old Readiness route, Review
  scope on the safety banner, and the "see this POVengine" buttons in the
  Composer and the preflight card now land there.

### Changed — the Firewall page walks Strata Logging Service and the firewall's policy

- **The Firewall setup defaulted to syslog through a Broker VM.** Its two
  paths read as if that were how NGFW data reaches Cortex. The data a POV is
  after streams from the firewall through Strata Logging Service and lands in
  the `panw_ngfw_*` datasets, stitched into `network_story`; the Cortex docs
  keep the syslog path as a fallback for when that collector cannot be used.
  The page now walks that one path, in the documented order: add the NGFW in
  Cortex › Data Sources & Integrations, turn on cloud logging and Enhanced
  Application Logging, install the certificate, forward every log type to
  Strata Logging Service, then commit and confirm logs arrive. The old step
  that said only tenants with Strata Logging Service could use it was wrong:
  new tenants get the direct NGFW integration.
- **The rest of the setup is the firewall's own policy.** "Log and alert on
  the POV traffic" asks for rules that log at session end through the
  forwarding profile, with security profiles set to alert, and "Decrypt the
  POV traffic" for the decryption rule. Each Test runs the existing read-only
  check. The overview gains rows for the log forwarding profile, decryption,
  and the rule each enrolled agent's traffic meets.
- POVengine still never writes to the firewall, and the registration's
  forwarding path setting (auto · sls · syslog · both) is unchanged.

### Fixed — the Scenarios planes sidebar fills its column, and the header fits

- **The Detection planes sidebar floated as a short card.** It started 26 px
  right of the nav rail and 22 px below the top, and ended where its list
  ended, partway down the panel. It is now a full-height column flush against
  the nav rail. Its plane list stays in place while the cards scroll, parked
  against the top edge. Below 860 px, where the nav rail retracts to icons and
  the planes stack above the results, they are a grid instead of a
  one-per-row list: the list filled 572 px of a 620 px window and pushed every
  scenario card below the fold.
- **The header ran past the edge below about 1650 px.** Back / Forward and the
  breadcrumb (1.2.0) widened a bar that already filled 1280, so Search, the
  tour, the theme toggle and the avatar sat off screen, reachable only by
  scrolling the header sideways. The bar now gives way in steps: tenant and
  agent names end in an ellipsis, and the RUNNING pill's scenario and clock,
  Search's word, the uptime, the breadcrumb's journey crumb and the version
  drop out as the width narrows. An agent's state word always stays. It fits
  from 1100 px up, the desktop app's 1204 px browser pane included.
- **The POVengine wordmark drew at 20 px instead of 15 px.** A legacy rule
  out-ranked the header's own.

### Security — the remaining scratch paths: directories in 47 more scenarios and 151 fixed-name files

- **What was wrong.** The two classes the entry above left open. Recounted on
  that tree: 49 more scenarios create a fixed directory under `/tmp`,
  `/var/tmp` or `/dev/shm` (counting directories named through a variable or
  made as the parent of `mkdir -p P/sub`), and 186 root, `container-runtime`
  or `direct` steps in 91 scenarios write a fixed-name file straight into
  `/tmp`, 157 of them in 83 scenarios then running or reading it. Reproduced as
  root in the CI image with `fs.protected_symlinks` on: with another user's
  `/tmp/aiacc-001.yml` in place, SIM-AIACC-001 handed the EAL CLI a file that
  user owned in 40 of 40 runs, and their content in 10. SIM-AIRS-001's cleanup
  sourced `/tmp/airs-001.env` as root, so a planted file there ran as uid 0.
  SIM-EDR-008 read its list of files to rewrite and delete from
  `/tmp/cortexsim_targetlist.txt`.
- **Directories (47 scenarios, 50 directories).** The same rule as above: the
  creating step makes the directory with `mkdir -m` and no `-p`, and every other
  step checks it in one `stat` lookup first. A check accepts only a directory
  the running user or root owns: in five scenarios (SIM-AIACC-006, SIM-APB-001,
  SIM-CLOUD-010, SIM-MP-015, SIM-MP-021) steps running as another account,
  root among them, wrote their own files into a directory a service account
  made, which that account could have filled with links. Those steps now keep
  their files in their own private directory. A step that also runs on macOS
  adds `|| stat -f %HT:%u:%Lp` inside the same substitution, which on Linux can
  never print a passing value. SIM-CDR-008's per-run exfil directory
  is now made with `mktemp -d`. SIM-CDR-030 is unchanged: every step already
  makes its directory with `set -eu`, `umask 077` and `mkdir` without `-p`.
- **Files (143 root, `container-runtime` or `direct` steps in 76 scenarios,
  151 names).** A file only the writing step uses now lives in
  `C=$(mktemp -d /tmp/cortexsim.XXXXXX)`, which the step removes on exit; the
  old cleanup lines for those names are gone. In those steps a tool's output
  that used to go to a fixed `/tmp` name goes there too (SIM-EDR-001's
  `mimi_output.txt`, SIM-ITDR-006's AS-REP hashes, both readable by every user
  before). A file a later step reads moved into a new sandbox under the
  directory rule (12 scenarios: SIM-AIRS-001, -003, -004, SIM-AISPM-001, -002,
  -004, -005, -006, SIM-CDR-004, -010, SIM-EDR-008, SIM-MP-034). Cleanup that
  sources or rewrites through such a path now checks ownership first
  (SIM-AIRS-001, SIM-EDR-008).
  No detection, card query or test keyed on the old file paths; the cards'
  illustrative payload text still shows them.
- **What an operator will notice.** EAL campaign files, staged crontabs and the
  like no longer appear in `/tmp` after a step: they live in
  `/tmp/cortexsim.XXXXXX` for the step's lifetime only. A step whose creating
  step did not run exits 1 with `sandbox … is missing or not ours` (or `not
  owned by <account>`). On macOS, SIM-BROWSER-001 to -004 step-02 now write
  their own campaign file instead of running a `/tmp` file no macOS step wrote.
  The Windows bundles of SIM-NDR-005 and SIM-NDR-007 no longer list a POSIX
  cleanup residue, so they run without `CORTEXSIM_ACCEPT_RESIDUE=1`.
- **Counts are unchanged.** The lab-readiness and coverage classifiers now also
  treat a stored lookup (`s=$(stat ...)`) and the macOS fallback as no signal;
  `ground-truth` and `lab-readiness` regenerate with no diff.
- **Not fixed here.** SIM-CDR-006's root unit and watchdog were fixed in 1.2.0.
  SIM-EDR-003's macOS variant appends a `/tmp/.cortexsim_beacon` launcher to
  the beacon account's `~/.zshrc`, and cleanup does not remove it. Root steps
  still write about 35 output-only files to fixed `/tmp` names, and SIM-CDR-001
  keeps `/tmp/linpeas.sh` and `/tmp/deepce.sh`, the payload shelf's
  `stage_path`. Service-account steps (SIM-MP-028's EAL files, SIM-EDR-011's
  target list) were not in scope. The next entry covers SIM-EDR-007 and three
  Windows scenarios.

### Security — nothing a scenario installs or runs as root or SYSTEM points at a plantable path

- **What was wrong.** SIM-EDR-007 ran `ansible-playbook` as root on
  `/tmp/pov-esxi-hosts` and `/tmp/pov-encrypt.yml`, which no step writes, so a
  file planted there first became a root playbook against the ESXi lab. Three
  scenarios' Windows variants had the same shape, written for a beacon running
  as SYSTEM: SIM-MP-023 made `C:\ProgramData\cortexsim-mp023` with
  `New-Item -Force`, ran a copied debugger from it with a script from the same
  folder, and registered an `onstart` task that runs it at every boot;
  SIM-MP-026's LocalSystem service side-loads a DLL from
  `C:\ProgramData\cortexsim-mp026`; SIM-MP-029 extracted and ran its lure in
  `%TEMP%\cortexsim-mp-029`, which is `C:\Windows\Temp` for SYSTEM. Any account
  can create a folder or a junction at those paths first, and even a
  SYSTEM-made subfolder of `C:\ProgramData` lets users add files. None of the
  three resolves for Windows as a whole yet (some of their steps have no
  Windows variant), so a Windows launch is refused and no `.ps1` is served:
  these variants have not reached a Windows host. They are fixed now so they
  are safe on the day the rest of the scenario resolves.
- **The fix.** SIM-EDR-007 reads from `/var/lib/cortexsim/edr-007`, a root-only
  directory each step checks first; TTP-2026-0006's staging script writes
  there. The three Windows scenarios keep their folder names, which their
  detections key on, and make the folder like the Linux rule: `rmdir` (which
  removes a junction, not its target), then one
  `[System.IO.Directory]::CreateDirectory(path, DirectorySecurity)` call that
  gives the folder its protected ACL as it is made: SYSTEM, Administrators,
  the running account and a marker ACE unique to that folder. A `mkdir`
  followed by `Set-Acl` would leave the new folder open, between the two, to
  every account that may add files to `C:\ProgramData` or `C:\Windows\Temp`,
  and a handle opened then keeps its access after the ACL lands. A `Get-Acl`
  then refuses anything that was already at the path, and every later step
  runs the same check: a planted folder fails on its owner, and a junction
  fails on its owner or its missing marker. Removals that used
  `Remove-Item -Recurse`, which Windows PowerShell 5.1 can follow into a
  junction's target, use `rd /s /q`.
- **How it is proven.** Linux: the sandbox test runs SIM-EDR-007's guards as
  root in the CI image against planted directories, links and the exchange
  race. Windows: a new `sandbox` phase in the `windows` CI job runs each step's
  guard on Windows PowerShell 5.1 against a folder and a junction a standard
  account planted, and races one against the other. Those guards are read from
  the scenario's Windows variant, because the render phase writes only
  scenarios that resolve for Windows. Nothing here has run on Windows until
  that job runs on this change.
- **What an operator will notice.** Stage SIM-EDR-007's inventory and
  playbooks with TTP-2026-0006's payload, which now writes them to
  `/var/lib/cortexsim/edr-007`; a step finding anything else exits 1 and says
  so. A Windows step of SIM-MP-023, -026 or -029 whose first step did not run
  exits 1 with `sandbox … is missing or not ours`.

### Security — a Windows step no longer runs, loads or feeds a file another account can plant

- **What was wrong.** Six scenarios that a Windows host does run kept their
  scratch files in a folder with a fixed name under `%TEMP%`, which is
  `C:\Windows\Temp` (writable by every account) for the SYSTEM beacon. Each made
  the folder with `New-Item -Force`, which accepts a folder or junction someone
  else made first. SIM-EDR-013 compiled a DLL there and loaded it; SIM-EDR-017
  ran `cmd.exe` with that folder as its working directory, so a planted
  `makecab.bat` ran as SYSTEM, and unpacked a CAB into a subfolder a junction
  could point anywhere; SIM-EDR-006 wrote LSASS dumps straight into `%TEMP%`
  under fixed names, so an account that created the name first owned the file
  ProcDump wrote LSASS memory into; SIM-ITDR-006 wrote AS-REP and Kerberoast
  hashes into its folder; SIM-EDR-004 copied an executable into its folder and
  set its timestamps; SIM-EDR-003's interrupted-run cleanup copied a saved
  `authorized_keys` back from its folder without asking who made it. Each also
  removed the folder with `Remove-Item -Recurse`, which Windows PowerShell 5.1
  follows into a junction's target. Nine more scenarios have the same shape in
  Windows variants that no Windows host is sent yet, because other steps of
  theirs have none: SIM-ITDR-002..005 and SIM-TIM-002 write the EAL campaign
  file they hand to the EAL CLI with `--live`, SIM-MP-025 builds its lure in
  one, SIM-MP-035 runs a `.vbs` and SIM-MP-036 an `.exe` from one, and
  SIM-MP-014 copies Chrome's cookie store to a fixed name.
- **The fix.** All fifteen use the Windows sandbox from the entry above: a
  folder per scenario, same name as before where detections key on it
  (`\Temp\` in a module path for SIM-EDR-013, `Boat.pst` for SIM-EDR-017),
  made by its first step with `rmdir` and one `CreateDirectory` call that sets
  its ACL, and checked by every later step with one `Get-Acl`. SIM-EDR-006's
  dumps move into its folder, which sits in the system Temp directory when
  `%TEMP%` has a space, as before. In-step removals use
  `[System.IO.Directory]::Delete`, which neither follows a junction nor
  touches `$LASTEXITCODE`, so a native command that failed earlier in the step
  still fails it; SIM-EDR-017's last step, which runs no native command before
  it, uses `rmdir` and sets the exit code back. Cleanup lines use `rd /s /q`,
  and SIM-EDR-003's restore runs only when its folder passes the same check,
  and starts with `Set-Variable` so the bash bundle withholds it as Windows.
- **How it is proven.** The text test pins the create and check forms for all
  eighteen Windows folders and fails 16 cases on the old text. The `windows`
  CI job's `sandbox` phase runs every creating step's guard against a planted
  folder, a junction and an empty path, every checking step's guard against
  five plants, and races a folder against a junction on one folder under
  `C:\ProgramData` and one under `%TEMP%`. It has not run on Windows until that
  job runs on this change. `make check-powershell` parses all 13 Windows
  bundles and 35 rendered steps, the six changed scenarios' included.
- **Not fixed here.** Steps that only write output to a fixed name and never
  run or read it back: SIM-MP-001's downloaded stage page, SIM-MP-020's zip,
  SIM-EDR-004's `svchost.exe` copy, SIM-BROWSER-006, SIM-EDR-012, -016, -018,
  SIM-KOI-007 and SIM-MP-016. SIM-MP-030 stages in `C:\Users\Public\Music`
  because that is the technique, and resolves for no target.
- **What an operator will notice.** SIM-EDR-006 steps 02 and 03 and
  SIM-EDR-017 step 02 need step 01, and SIM-ITDR-006 step 03 needs step 02,
  to have made the folder: run alone, they exit 1 with
  `sandbox … is missing or not ours`.

### Added

- **Agents › Fleet: delete agent rows in one click, or in bulk (#339).** A
  row's Delete opens an inline "Delete `<id>`?" confirm on the same card; the
  host-side uninstall command sits behind a collapsed "Uninstall command"
  disclosure. A toolbar adds **Delete offline (N)**, which never counts the
  active agent or an online or stale one, and a **Select** mode with **Delete
  selected (N)**. Bulk deletes call `DELETE /api/agents/{id}` one at a time,
  announce progress, carry on past a refusal, keep each 409's run ids and
  Abort control on its row, and refresh the list once at the end. The Fleet
  page now uses kit controls only (new verbs Deploy, Set active, Download,
  Copy, Delete, Select and Refresh) and shows liveness as a status icon and
  one word. The backend is unchanged.

### Changed

- **The console's "Read back" action is now "Review".** The button on Runs, on
  a run's page, on Results, in Runs › Compare and on the UC / TC Gaps list says
  Review, and says Reviewing… while it runs. The fourth stage of the run
  lifecycle strip says Reviewed, and every line that said a run was or was not
  "read back" now says reviewed or not reviewed ("Ran — not reviewed", "Not
  reviewed yet — Review the run."). The Tenant page's tab is now Review &
  integrations. Only the words changed: the action still asks SimCore to read
  the tenant for the run (`POST /api/runs/{id}/reconcile`), and no route, API
  or backend behaviour moved.
- **A run's page speaks in one short line per state, with kit controls.** The
  run's status line, the Results notice and the case panel now say one short
  thing ("Finished · not reviewed", "Not reviewed — no results yet", "Not
  reviewed"), with any detail in a tooltip. The Live tab's stream uses kit
  buttons and status icons: its level filters are toggle buttons, Pause and
  Clear carry their icons, the connection state reads Live, Polling,
  Connecting, Error or Paused, and the footer's Screenshot and Download (the
  POV report) are kit verbs. Review and Open results buttons carry their icons
  everywhere. The kit gains the Pause, Clear and Screenshot verbs.

### Fixed

- A scenario whose file is gone is retired at startup, so an upgraded database stops listing the scenarios this release moved.
- **A correlation with nothing to join is not a Cortex miss** (#378). In the
  1.2.4 lab pass, SIM-CDR-019 and SIM-NDR-012 relayed their collector steps
  dry, so no input record left SimCore, yet the `echo` step carrying the
  correlation exited 0 and its row closed `missed` once the window shut: a
  Cortex miss (denominator 1, coverage 0 %) for a rule that had no alerts to
  fuse. A silent Correlation row now reads `not_executed` / `INPUTS_NOT_SENT`
  when every row it joins sent nothing, with "the steps it joins sent nothing"
  in its notes, and leaves the efficacy denominator. The corpus does not say
  which detections a correlation fuses, so its inputs are every other
  non-Correlation row on an earlier step of the run, or on its own step. An
  input sent nothing when it is already `not_executed` or `not_reached` (an
  EAL step that stayed dry or delivered no record, a refused or never-run
  step), or when its step failed, timed out or did not run on the host. If any
  input was delivered, the correlation reads `missed` as before. An alert that
  matches the correlation still credits it. The storyline reads "Signal never
  generated · inputs not sent".
- **A timed-out step is not a Cortex miss.** In the same lab pass SIM-MP-003
  step-03 hit its 180 s limit while DNS hung, so step-04 never ran, and
  step-03's silent rows read `missed` once the window closed. A step the host
  records `timeout` is now read like a failed one: its silent `detect` rows,
  and `prevent` rows scored as a detect, read `not_executed` /
  `STEP_TIMED_OUT`, with "the step timed out on the host" in the notes. An
  alert matched inside the window still credits the row. A block-mode
  `prevent` row on a timed-out step still reads `missed` / fail: the step ran
  its whole budget unprevented. `signaled` and `anchor_terminated` (the host
  stopped the step) still read `missed`. The storyline reads "Signal never
  generated · step timed out".
- **Rows already decided keep their verdict.** Review never re-judges a row
  it has written, so a Correlation row or a timed-out step's row that an
  earlier version wrote `missed` stays `missed`. Re-run the scenario to score
  it under these rules.
- **A finished run's stdout panel shows what the run printed** (#340). The
  panel on a run's Live tab showed only the live stream, so a run that had
  already ended said "waiting for agent…" even though SimCore had stored its
  whole output. A complete, failed, aborted or staged run now shows that stored
  output, one block per step, each marked Ran, Failed (exit N), Timed out or
  Did not run with its exit code. A run with no stored output says "No output
  recorded". A run still in progress keeps the live stream.
- **The header offers to switch off an offline agent** (#341). The active
  agent is remembered by the browser, and it was replaced only when the agent
  was deleted, so an agent that had gone offline stayed active and the next
  launch targeted a beacon that could not take it. When the active agent is
  offline and another agent is online, the header now shows the agent as
  offline with a **Switch to** button naming the online agent (the most
  recently seen, if there are several). It does not switch on its own.
- **Scenario cards keep Run and Customize whole** (#344). In a two-column
  grid the card footer ran out of room: Run was cut off at the card's edge and
  the run-history chip ("10× run · 1d ago") wrapped onto three lines. The step
  and detection counts and the history chip now shrink together, the chip stays
  on one line and truncates, and the buttons keep their full width.

## [1.2.4] - 2026-11-15

**Hotfix for v1.2.3: a run its beacon abandoned reads `failed`, and reading the
tenant no longer locks the database (#326).** One run status, one database
setting and a commit before each outbound call; no scenario, card, emitter,
beacon or schema change. The database file switches to WAL mode, so read the
upgrade notes before you back it up.

### Fixed

- **A run its beacon abandoned reads `failed`, not `complete`.** When a beacon
  stayed alive but stopped reporting on a run, SimCore closed the run as
  `complete` and said what had happened only in its output ("BEACON ABANDONED
  TASK ... Steps without host evidence are not a coverage measurement"). The
  Runs list, the report and the API then showed a run nobody saw finish as a
  finished run (run `5cc8c6b4`, SIM-NDR-007). Such a run now ends `failed`,
  which the console already shows as a run that proves nothing about coverage.
  Its output opens the marker with `RUN FAILED: BEACON_ABANDONED_TASK`, and its
  `run.status` frame carries the same code as `termination`. Its results are
  unchanged: the rows of the first step with no host record stay unjudged (a
  `prevent` row caps at `pending`), and later steps read `not_reached`. A run
  whose agent went offline mid-run still ends `complete`, severed at the step.
  Runs already closed as `complete` keep that status.
- **No more `database is locked` while SimCore reads the tenant.** While a
  review of one run waited on the tenant, the beacon's poll for work
  (`GET /api/agents/{id}/tasks`) and the heartbeat sweep failed with
  `database is locked`: 44 times in one minute on 2026-11-14, 152 in that day's
  log. A beacon that cannot poll looks gone, and that is how a live run came to
  be closed as abandoned. Three changes:
  - **No write is held open across an outbound call.** Reading a stored
    credential stamps its last-access time, which opens a write, and SQLite
    allows one writer. Verify, reconcile, the case-ledger refresh, the
    tenant-evidence read, the connector, firewall and collector preflights, the
    assertion runner and the `/api/xsiam` routes all went on to the network
    with that write open. Each now commits it first. A reconcile also commits
    its "credential verified" stamp before it reads incidents and cases, and
    a review that withdraws a cross-run credit (1.2.3) commits the withdrawal
    before those reads. The row is still judged again in the same review; if
    that review then fails, the withdrawal stays on the row with its note and
    the next review judges it.
  - **The database opens in WAL mode**, with a 5 s busy timeout and
    `synchronous=NORMAL`, so a reader and the writer no longer wait on each
    other. The busy timeout was already 5 s by the driver's default; SimCore
    now sets it itself.
  - **A busy database no longer fails the beacon's polls.** `/tasks` and
    `/control` skip their `last_seen` stamp for that poll and answer 200 (a
    500 on `/control` made the beacon stop its step); the heartbeat sweep
    skips its pass. A task whose delivery cannot be recorded stays queued for
    the next poll. Before, it was dropped from the queue until a restart.

### Upgrading from 1.2.3 or older

- **The database switches to WAL on first start, and a backup is now three
  files.** Two side files can appear next to `cortexsim.db`:
  `cortexsim.db-wal` and `cortexsim.db-shm`. Recent commits live in `-wal`
  until SQLite folds them back, so a copy of `cortexsim.db` alone can miss
  recent runs. Back up every `cortexsim.db*` file with SimCore stopped, or use
  SQLite's online backup while it runs; the commands are in
  `docs/release/mvp-launch-runbook.md` §8.5. When you restore, remove the old
  side files with the database they belonged to. The switch needs no action
  and is stored in the file; an older release opens a WAL database as it is.
- **Keep the database on storage local to one host.** SQLite's WAL is only
  safe when every process that opens the file runs on the same machine. The
  compose volume is. A Helm install whose `persistence` volume is shared by
  pods on different nodes (the `ReadWriteMany` option) is not: use one replica
  on node-local storage.

## [1.2.3] - 2026-11-15

**Hotfix for v1.2.2: an alert credits at most one run, and the alerts a run did
not expect are listed instead of dropped (#347, #346).** One attribution rule,
one matcher family rule and one nullable column; no scenario, card, emitter or
beacon change.

### Fixed

- **An alert credits at most one run (#347).** Reconcile reads the alerts of
  the run's endpoint, and a detection's match window (one hour, a day for
  Analytics / ABIOC) is longer than the run. On an endpoint running scenarios
  back to back, a later run's alert fell inside an earlier run's window and
  credited it on a shared technique: SIM-EDR-018 read "detected" from an alert
  SIM-MP-003 raised 32 minutes later, with an MTTD of 1,920 s. Now an alert
  matched only on a technique or name credits a detection only while its run
  holds the endpoint: from the run's launch (or, when it was queued behind
  another run, from the moment the beacon collected it) until the next run
  starts there. An alert that carries the detection's rule name, its detection
  id or the run's own id still credits it later, up to the end of the
  detection's window, so an Analytics alert that lags behind its run is not
  lost. If two runs match an alert and nothing ties it to one, it credits
  neither and is listed as unattributed. The reconcile summary names every
  alert a run was not given, why, and the run it belongs to (`not_credited`).
- **Alerts a run did not expect are listed as "Also raised by Cortex" (#346).**
  On the same sweep, 22 of the 25 alerts Cortex raised on the endpoint credited
  nothing in their own run and appeared nowhere. Now every one is either
  credited or listed on its run:
  - *An alert from an XDR Agent module on a run that expects no Prevention
    detection (15 of them)* is listed as an **additional detection**:
    "Also raised by Cortex (N)" on the Results page, one line per alert (name,
    source, step, time), with identical alerts on one line and a count;
    "Other alerts Cortex raised for this run" in the Markdown report;
    `additional_detections` in the JSON report, in `GET /api/results/{run_id}`
    and in the reconcile summary. It does not mark any expected detection
    "detected", and it stays out of coverage and Detection Accuracy, which
    still measure the expected detections only. The list holds the alerts
    raised on the run's endpoint, from its launch until the next run starts
    there or an hour after it ended, that match none of its expected
    detections.
  - *A BIOC raised where an Analytics BIOC was expected, or the reverse (4)*:
    the two labels are now one family when something else identifies the
    detection, namely the exact technique, the rule name or the detection id.
    Such a credit is marked: the detection's note reads "credited by an
    Analytics BIOC" (or "a BIOC"), the report prints it, and the API carries
    `matched_via`. One alert credits one detection this way, and never one
    the run also expects under the alert's own label. None of the sweep's four
    carries such a key (their techniques differ from the expected ones), so
    they are listed as additional detections rather than credited. An XDR
    Agent alert still never credits a BIOC, ABIOC, XQL, Correlation or IOC
    detection.
  - *A technique the scenario does not expect (3: SIM-EDR-024, SIM-KOI-012,
    SIM-MP-018)*: each is a built-in detection the scenario does not declare
    (an uncommon service start from the systemd timer, a second agent module,
    an analytics rule on the step's shell), not a wrong technique on a step,
    so the scenarios are unchanged and the alerts are listed as additional
    detections.

### Upgrading from 1.2.2

- **One new nullable column, added at startup:** `runs.alert_attribution`
  (JSON). It holds a run's additional detections and its unattributed alerts.
  No action is needed; a run stored before the upgrade reads `NULL`, meaning
  "not reviewed", and shows no "Also raised by Cortex" section until it is
  reviewed again. Nothing is back-filled at upgrade.
- **A stored cross-run credit is withdrawn on the next review of that run,
  with a note.** Nothing is rewritten at upgrade. A run is reviewed when it is
  reconciled again: `POST /api/runs/{id}/reconcile`,
  `POST /api/runs/{id}/observations`, or the same action in the console. The
  auto-reconcile sweep reviews a stored run only while it is inside the
  lookback, under the attempt cap and still has an unobserved detection, so
  review the runs of a back-to-back sweep yourself. On that review, a
  detection credited by an alert that belongs to another run goes back to
  unobserved, loses its MTTD and is judged again in the same pass (so it is
  credited from the run's own alert when there is one). The detection's note
  says which alert was withdrawn and why, the reconcile summary lists it under
  `withdrawn_credits` with a warning, and the alert stays on the run's record
  with no detection attached. A detection a person marked observed is never
  withdrawn. Coverage and a Detection Accuracy verdict that counted the false
  credit drop accordingly.

## [1.2.2] - 2026-11-15

**Hotfix for v1.2.1: Detection Accuracy no longer counts verify-only XQL rows
as misses (#272).** One scoring change and one scenario bar; no card, emitter,
beacon or schema change.

### Fixed

- **43 scenarios with a Detection Accuracy bar could never reach it.**
  Detection Accuracy divided the rows an alert matched by every row that ran.
  An XQL row whose card object is a query raises no alert, so reconcile wrote it
  `missed` and the measure counted it as a miss. Even with every alert-backed
  detection firing, 39 of those scenarios read FAIL on a healthy tenant. The
  other four (SIM-AIACC-003, SIM-AIACC-005, SIM-AIRS-005, SIM-CLOUD-004) have
  no alert-backed row at all.
- **A verify-only row now leaves the measure while no alert has matched it**,
  the way rows whose step never ran already do. A row is verify-only when its
  declared type is XQL and no alert-raising card object backs it: its card
  object is an XQL query or a modeling rule, or it has no card. An XQL row whose
  card is a BIOC or a correlation rule still counts. If an alert does match a
  verify-only row, the row counts as detected, once, on both sides of the
  ratio. A run with no alert-backed row left reads `pending`, never PASS, and
  says why in `tc_verdict_detail.measurement.withheld`. The rows themselves are
  unchanged: their outcome still reads `missed`, the coverage figures still
  count them, and their own verdict is the Tier-2 verify verdict when they carry
  a `verification_xql`. `measurement.verify_only` reports how many rows left the
  measure, how many were credited, and how many of those that left have no
  `verification_xql`, so Tier 2 cannot score them either.
- **Counted on this tree**, with every alert-backed row firing: the 39 now
  reach their bar. 18 read PASS and 21 read `not_applicable` because the index
  row they bind has no measurable threshold. The four with no alert-backed row
  stay `pending`. None of their XQL rows carries a `verification_xql`, so no
  path scores them until their content changes.
- **SIM-EDR-023's bar moves from 62.5% to 83.3%.** It still asks for 5 of its 6
  alert-backed rows; its two verify-only rows no longer count.

### Upgrading from 1.2.1

- **Re-scoring a stored run can change its Detection Accuracy verdict.** A run
  is re-scored when it is next read back: `POST /api/runs/{id}/reconcile`,
  `POST /api/runs/{id}/observations` or the auto-reconcile sweep. A run that read
  FAIL because its verify-only rows counted as misses can now read PASS, or
  `not_applicable` when its index row is qualitative. A run whose every row is
  verify-only, and which read PASS because an alert matched one of them, now
  reads `pending`. A re-score without a new read back (run completion, a Tier-2
  verify) keeps the stored measurement until then.

## [1.2.1] - 2026-11-15

**Hotfix for v1.2.0: a step that failed on the host no longer reads as
"Cortex missed it" (#323).** One scoring change; no scenario, card, emitter,
beacon or schema change.

### Fixed

- **A failed step is not a Cortex miss.** The beacon records a step that ends
  on its own as `exited`, whatever its exit status. Reconcile wrote a silent
  `detect` row on such a step `missed` once its window closed, so a step that
  failed put "Cortex missed it" in the POV report for activity that never ran:
  `python3 -m scripts.eal_simulator.cli` exiting 1 with `ModuleNotFoundError`
  on 15 scenarios, `npm: command not found` on SIM-MP-028, a missing runtime
  dependency. The host's exit status, already stored on every step record, now
  reaches that judgement. A non-zero exit reads `not_executed` /
  `STEP_FAILED`; exit 127 ("command not found") reads `not_executed` /
  `TOOL_ABSENT`; the row's notes carry the exit status. A `not_executed` row
  leaves the efficacy denominator, so it is neither a hit nor a miss, and a run
  whose only rows failed reads `pending`, never FAIL. `prevent` rows scored as
  a detect (report or unknown profile mode, or a module attested Report) follow
  the same rule. Exit 0, or a record with no exit status, still reads `missed`;
  `signaled`, `timeout` and `anchor_terminated` are unchanged; an alert matched
  inside the window still credits the row. The storyline reads "the step failed
  on the host, so a miss can't be claimed".

### Known limitations

- **A tool missing inside `|| true` still reads as a miss.** SIM-EDR-001
  step-05 prints `strings not found` and exits 0 through its `|| echo`, so the
  host record says the step ran. That half of #323 is still open: it needs a
  launch check against the host beacon's tool roster or a content change, not
  a scoring change.
- **Block-mode `prevent` rows do not read the exit status.** In a block-mode
  run a non-zero exit can be the agent's own block of a child process, so the
  two-witness rule is unchanged: a silent block-mode `prevent` row on an
  `exited` step still reads `missed` / fail.
- **A tool that exits non-zero after doing its work now under-claims.** A step
  whose last command is `grep` with no match exits 1 and reads `not_executed`,
  not `missed`.
- **Rows 1.2.0 already wrote `missed` keep it.** Reconcile never re-judges a
  decided row, so re-run a scenario to re-score it. A `not_executed` row cannot
  be attested by hand (`409 RESULT_STEP_NOT_RUN`), like every other
  `not_executed` row.

## [1.2.0] - 2026-11-14

**SimCore learns what a POV's tenant can receive before the run, and is
hardened for a beta on real endpoints.** Ingestion doors, collector targets,
the dataset requirement registry and a read-only NGFW (PAN-OS) connection say
which detections can fire; an MCP server whose tenant writes only preview, a
Kubernetes container agent and a read-only SOC Framework layer are added; and
two beta-readiness rounds, weighted to Windows, close a verified
unauthenticated root command-execution path, fix Windows push bundles that had
been silent no-ops and give the Windows beacon a service handler. 1.1.1's six
security fixes are carried. Counted on this tree: 199
scenarios · 197 TTP cards · 27 EAL plugins · 1411 step-detections · 2260
catalog detection objects · 28 assertion artifacts · 156 route decorators (166
served routes); lab-readiness GREEN 167 · YELLOW 27 · RED 5.

**`tenant-verified` is still 0.** Nothing in this release has executed against
a live Cortex tenant. The Windows path has run only in the `windows-2022` CI job
against CI probe scenarios, never a shipped scenario or a customer host; the
Kubernetes container agent and the MCP server are authored, not
tenant-verified. Authored is not proven.

This release carries two lines of work, merged with `git merge` (no rebase):
the **beta-readiness pass** summarised next, and the **features merged to
`dev` after 1.1.0** (PRs #169–#212), among them ingestion doors, collector
targets, the read-only NGFW connection, the MCP server, the Kubernetes
container agent, ingestion routes and the dataset requirement registry. The
sections below are grouped by kind of change, not by line of work, and the
upgrade notes cover both.

**Beta-readiness pass, weighted to Windows.** The pass hardens SimCore for a
beta where Domain Consultants deploy it into customer labs and enroll beacons on
real endpoints. It closes one verified unauthenticated root
command-execution path. It also finds that the Windows push bundles had been
silent no-ops since v1.0.0 (executed under pwsh 7.6; Windows PowerShell 5.1 is
inferred to behave the same, not observed), and that, lacking a service
handler, the elevated Windows service install would die about 30 s after it
reported success (inferred from the SCM contract and simulated; never observed
on a Windows host). On the Windows pull path it adds a service
handler, dispatch keyed to the target's OS, Job Object tree-kill and an ASCII
installer with an uninstaller. It also adds a `windows-2022` CI job. A second
round hardens the run record, the installers, the push bundles, the console
and the deployment files, and fixes the two 5.1 defects that job found. Counted on
the beta branch before `dev` was merged in (`c121c8f`): 193 scenarios · 191
TTP cards · 27 EAL plugins · **1369** step-detections (was 1377: the Windows
content now declares only detections a lab run can fire; round 2 changed none)
· 137 route decorators · 28 assertion artifacts. The merged tree's counts are
in `docs/reference/ground-truth.md`, regenerated after the merge.

> **What has run on Windows: one CI job, on a hosted runner.** The `windows`
> job ran green at `d7f4f29` (run 42933820773, 2026-11-10) and on the round-2
> code at `fb0f646` and `e434efa` (run 43037077022, 2026-11-11, all nine CI
> jobs green). That is a GitHub-hosted `windows-2022` runner with Windows
> PowerShell 5.1 Desktop, an elevated admin, code page 1252 and Defender
> real-time protection off. At `e434efa` it ran the beacon's native `go test`
> (409 passed, every one of the 43 Windows-only test functions among them), a
> 5.1 parse and a PSScriptAnalyzer pass over every PowerShell file the repo
> ships, and SimCore on the runner. It then ran the console's install
> one-liner (the service was still Running 75 s later), a pull run whose
> frames named `NT AUTHORITY\SYSTEM`, an abort whose row read
> `termination=aborted rc=130`, a `.ps1` push run with the IDENTITY table and
> the uninstaller, all against CI probe scenarios. The two runs before the
> first green were red and found the CLIXML defect fixed below; the run at
> `fb0f646` was red on `backend` only and found the Compose `host_ip` defect.
> **It does not cover:** any shipped scenario (none of the 13 Windows bundles
> has executed on any OS), reboot survival, the non-elevated install, a host
> with a Cortex XDR agent or AV in prevent mode, an https SimCore, Server Core,
> other code pages, AppLocker/WDAC/Constrained Language Mode, or any change
> after `e434efa`. Items below marked *not yet run on Windows* were written
> after `d7f4f29`; their native tests passed at `e434efa`, but no end-to-end
> Windows run has reached them. The job is not a required check (decision
> DOCS-12).
> **`tenant-verified` is still 0.**

### Changed — the console follows design v26 (#250, #252–#254, #257–#261, #265, #277–#281)

- **One colour rule.** Cortex green is the primary action (one filled button
  per screen), the selected state and the brand. Yellow is the only warning
  colour, coral means down or broken, and PANW orange is only the NGFW family
  tint. Every status shows an icon and a word. Buttons use a closed set of
  verbs: Configure · Test · Run · Customize · Add to chain · Read back ·
  Open results · Abort run.
- **The rail follows what a DC does, in order.** **Onboard & connect** (Get
  started, Components) · **Compose & simulate** (Scenarios, Build a chain,
  Launch gate) · **Run & observe** (Runs, Results) · **Reference** (Catalog:
  TTP cards, Packages, Data streams, UC / TC index). The header shows the
  instance's status, a **Running** pill only while a run is live, and ⌘K
  search with page icons.
- **Components.** A hub and eight pages: POVengine (with an API tab generated
  from OpenAPI), Agents, Tenant, HTTP collector, Broker VM, Firewall, XDR
  Collector and Cortex Engine. Broker VM, XDR Collector and Cortex Engine have
  no SimCore API, so they show what you confirmed, or Unknown.
- **Scenarios.** A card opens a sheet with a chain preview and **Customize** /
  **Run**. Filters are Plane · Component · Use case · Test case · Status. TTP
  cards, packages and data-stream emitters have **Add to chain**, which feeds a
  "Your chain" tray on Scenarios and Catalog (the tray is not saved).
- **Runs and Results.** A lifecycle strip reads Launched → Running →
  Finished / Failed / Aborted → Read back → Results. Until Read back (the
  reconcile pull) runs, steps say only Ran or Did not run. Run detail draws
  the live chain; a lane header opens its component page; a Broken step opens
  a troubleshooting sheet with the likely causes in the order to check them;
  Abort is an inline confirm. Results always names its run in a "Result of"
  bar, with a switcher across read-back runs, and never shows without a run.
- **Where things moved.** `#/agents`, `#/tenants` and `#/firewall` open the
  Agents, Tenant and Firewall component pages (their Fleet, Read-back and
  Sources tabs); `#/setup` and `#/scope` open Get started; `#/validation` opens
  Results; `#/readiness` opens the Launch gate (`#/preflight`). Scenarios keeps
  its `#/library` route, so old links still work.
- **Accessibility and theming.** Dark-theme aliases follow `[data-theme]`;
  a skip link and palette focus order; a page title and form labels on every
  page; agent capabilities shown on their cards.

### Added — ★ Favorites: the system's best scenarios, first in the Composer palette

The Composer palette has a new first tab, **★ Favorites**, and opens on it
whenever the library has any. A scenario earns the ★ by measurement, not by a
list anyone keeps: it is lab-ready (GREEN: no tool fetched from the internet,
no launch consent gate), every step makes real telemetry, it raises a
Correlation, and it spans at least four detection types. That is 27 of 201
scenarios today, 12 of them the stitched Unit 42 chains. The rule lives once,
in `core/engine/lab_readiness.py` (`favorite_reasons`), beside the
GREEN/YELLOW/RED classifier it reads; `scripts/lab_readiness.py` now renders
the manifest from that module and its output is byte-for-byte unchanged.
`GET /api/scenarios` rows and `GET /api/scenarios/{id}` carry `favorite` and
`favorite_why` (the four reasons, shown as the ★'s tooltip). Starred
scenarios also carry the ★ on the Scenarios tab. Palette items now carry tags
(plane, detection types, use case), and the active tab offers its most common
tags as chips that filter the list; tags that differ only in case are one chip.

### Added — a launch dry run, and a refused launch writes nothing

`POST /api/runs/preflight` takes the launch body and runs every check
`POST /api/runs` makes, in the same order, without launching. It returns
whether the launch would go, the refusal it would get (code and HTTP status),
every refusal at once, and each check as pass, block or skipped with a reason.
The checks are one function shared with the launch, so the two cannot
disagree; a test holds them to the same answer for every refusal code. Before,
the console could predict about six of the twenty-odd refusals.

Every refusal is now decided before the run row is written. Six refusals used
to fire after it — the payload shelf, a pull launch with no target, an invalid
stitch context, a composed run's unenrolled second endpoint, the
container-agent gate — and left a `failed` run with `not_executed` results in
the run list. They now leave nothing, like the consent gate always did. One
order change comes with it: a composed run with an unenrolled endpoint is now
refused `TARGET_AGENT_NOT_ENROLLED` before the container-agent gate judges its
other endpoints (it could answer `AGENT_TOOL_ABSENT` first).

New refusal codes: `SCENARIO_NOT_FOUND` (404; was `LAUNCH_FAILED` 422),
`PULL_TARGET_MISSING` (422), `STITCH_CONTEXT_INVALID` (409; was only in the
message) and, for the push cluster-consent clause, `CLUSTER_CONSENT_REQUIRED`,
`PRIVILEGED_K8S_DISABLED` and `CLUSTER_POSTURE_REFUSED` (409). The Composer's
draft verdict now reports `DRAFT_CHAIN_INVALID` when the chain is invalid
(it always said `DRAFT_NOT_TC_BOUND`). Spec:
`docs/superpowers/specs/2026-11-13-launch-gate-one-model-design.md`, slice 1.

### Changed — the console no longer says "shelf" or "staged" for tools

- **Tool readiness is said in plain words everywhere.** The launch gate row is
  **Tool readiness** (was "Payload shelf"). The launch check says a tool is
  **not ready**, or **needs internet access on the target**, and still says what
  that costs: on a network that blocks it the step runs without its tool and the
  missing detection reads as a coverage gap. The launch card is **Tools for this
  run**, the Composer's bottom tab is **Tools · N ready**, the scenario sheet's
  tool chips read READY / NOT READY / NEEDS INTERNET, and the dialog is **Make
  \<tool\> ready on this instance**. "Tools & Payloads" is **Packages**.
- **Coverage** calls a technique no run has exercised **Not run** (was
  "Staged"), on the page and in the exported Navigator layer.
- The About page no longer claims "nothing is fetched at dispatch": it now says
  a run that needs a tool this instance cannot supply is refused, and a tool the
  target must install for itself is flagged at the launch check.
- API fields, error codes (`PAYLOAD_NOT_STAGED`) and a push run's `staged`
  status are unchanged.

### Changed — Packages: a plain catalog, no supply mechanics

- **The Packages page no longer talks about the payload shelf.** How and when a
  tool reaches a host (staged on the instance, installed by the target at run
  time, provided by the lab) is the system's decision, not a catalog concept.
  The banner, the supply statistics, the supply chips, the egress lines and the
  Stage button are gone from the list, with the tier, safety and supply
  filters. A card shows the tool, its category and platform, the Cortex planes
  and techniques its signal lands on, and **Add to chain**.
- **One readiness fact stays.** A tool this instance cannot supply reads **Not
  ready** on its card, because a run that needs it is refused. The detail
  panel's first section is **Availability**, in plain words, with **Make
  ready** where the instance can fix it; a tool the target installs for itself
  says the target needs internet access. The install-mechanics section is
  removed from the detail.
- A link can still scope the list (`?supply=unstaged`, used by a refused
  launch); the scope is named in plain words and can be cleared.

### Changed — Composer: Start and Stop points, lanes without Launch/Proof, less chrome

- **The chain's ends are Start and Stop.** The trigger node reads **Start** and
  the proof node **Stop**, each with the play / stop icon, on the Design, Run
  and Lanes lenses, the scenario sheet and Run detail. The launch button is
  **Start on \<agent\>** with the same icon.
- **Launch and Proof are no longer lanes.** The Lanes lens draws the signal
  paths only; Start and Stop sit in a row above them, before the first step
  and after the last. Each lane shows its component's icon and name; its
  description is the tooltip.
- **The canvas dots stay visible at every zoom.** React Flow shrank a dot with
  the zoom, so the grid vanished on a fitted chain. Dots are now drawn at a
  constant size on screen; the spacing still scales with the zoom.
- **The "SimCore degraded" strip is removed.** The header's status (Degraded ·
  N issues, opening Components) carries it.
- **Icon buttons.** The palette and inspector toggles on the canvas are icons
  (named "Add to chain" and "Inspector" for assistive tech). A shared `Icon`
  set (play, stop, repeat, plus, sliders) joins the kit.

### Upgrading from 1.1.x (read before you re-run `dev-up.sh` on a jumpbox)

1.1.1 carries 1.1.0's schema code unchanged (`git diff v1.1.0 v1.1.1 --
core/models.py core/database.py` is empty), so everything below applies to
both.

- **Back up the database volume first, and roll back only by restoring it.**
  A database *created* by 1.1.0 or later code cannot be used by 1.0.2 or older
  code: `runs.agent_profile_mode` and `results.expected_outcome` are
  `NOT NULL` with no SQL default, so every launch from that older code fails
  with a 500 that does not name the schema (OPS-17, open). There is no schema
  stamp. Probed on 2026-11-12 through each version's own `init_db` and ORM
  (one Scenario, Run and Result inserted and read back), not through a launch:
  1.2.0 upgraded a database 1.1.1 created and one 1.0.2 created; 1.1.1 then
  wrote to a database 1.2.0 created and to one it upgraded, because every
  column 1.2.0 adds to an existing table is nullable; 1.0.2 wrote to the
  database 1.2.0 upgraded (the migration gives `agent_profile_mode` a SQL
  default) and failed on the one 1.2.0 created (`NOT NULL constraint failed:
  runs.agent_profile_mode`). Columns added since 1.0.0 are added automatically
  at boot; do not run the `ALTER TABLE` lines older notes mention, because
  SQLite rejects a duplicate column. The procedure is in
  `docs/release/mvp-launch-runbook.md` §8.5.
- **Re-running `scripts/dev-up.sh` over an existing `.env` (RG-GUARD-01).** A
  1.1.x `.env` has no `CORTEXSIM_ALLOWED_HOSTS`, so after this release's Host
  guard every request by the jumpbox's name would get
  `400 HOST_NOT_ALLOWED`. `dev-up.sh` now warns, starts compose with this
  host's short and fully qualified names for that bring-up only, and leaves
  `.env` untouched. `scripts/dev-up.sh --apply` writes the line and keeps the
  previous file under `.env-backups/<UTC>/.env`. A plain `docker compose up`
  afterwards drops an unpersisted value. Add any other name in use (a CNAME, an
  `/etc/hosts` alias, a Tailscale name) by hand. `dev-up.sh` now takes
  `[--dev] [--apply]` and refuses an unknown argument with exit 2. It also says
  when `CORTEXSIM_VERSION` differs from the code's version, because that
  changes the image tag and container name.
- **Where SimCore listens.** Compose now publishes with
  `"${CORTEXSIM_BIND:+${CORTEXSIM_BIND}:}${CORTEXSIM_PORT:-8888}:8888"`. Empty or unset means the mapping names no host address, so Docker
  publishes on every host address, IPv4 and IPv6, as in 1.1.0 (round 1 had made
  the default IPv4 only). `0.0.0.0` means IPv4 only. Write an IPv6 address
  without brackets. An intermediate long-form mapping with
  `host_ip: "${CORTEXSIM_BIND:-}"` did not parse when the variable was unset
  (Compose rejects an empty `host_ip`); CI run 43033286928's
  `docker compose config -q` step caught it before release. Needs Compose v2.
  `scripts/dev-up-native.sh` now binds where `CORTEXSIM_BIND` says; it used to
  bind every interface. *Parsed by Compose; not yet brought up under Docker.*
- **Container behaviour.** The image declares a `HEALTHCHECK`. It is liveness
  only: healthy means the process answers, not that nothing is degraded, and
  Docker does not restart an unhealthy container. Container logs are capped at
  5 × 20 MB. SimCore runs exactly one worker (`--workers 1`); before, a
  `WEB_CONCURRENCY` in the environment could start several SimCores sharing
  one database. *Authored; no container ran.*
- **Settings under compose.** `.env.example` now documents all 29 settings the
  beta pass covered, split at a marked heading; the merges with `dev` add the
  eleven dev declared (two NGFW, four MCP, `CORTEXSIM_STRICT_DATASETS`, three
  case-ledger, `CORTEXSIM_PUBLIC_URLS`), 40 in all. The container never reads
  `.env`: compose passes only `CORTEXSIM_ENV`, `_SECRET`, `_BIND`,
  `_ALLOWED_HOSTS`, `_CORS_ORIGINS`, `_TRUSTED_PROXIES` and `_PUBLIC_URLS`, so
  any other setting keeps its default in a compose deployment.
- **Helm chart 1.1.0.** The API defaults to one replica (it was 2, which gave
  two separate SimCores behind one Service). The chart now admits its own
  Service names, enabled Ingress hosts and `tailscale.hostname` in
  `CORTEXSIM_ALLOWED_HOSTS`; add anything else, a Tailscale MagicDNS FQDN for
  instance, with `--set api.allowedHosts[0]=<name>`. New values:
  `api.allowedHosts`, `api.trustedProxies`, `api.extraEnv`, `clusterDomain`.
  A changed allowlist rolls the pods. *Rendered with `helm template` only; not
  installed in a cluster.*
- **`install.sh`** prints the by-name URL only when `CORTEXSIM_ALLOWED_HOSTS`
  admits it; otherwise it leads with localhost and names the refused host.
- **XSIAM credentials need `"capabilities": ["xql"]` to keep running XQL**
  (from `dev`). A credential without the field stops running XQL after
  upgrade: the XQL routes refuse it, and `/verify`, the auto-verify sweep and
  assertion runs resolve `pending` with `reason: xql_not_authorised`. Add
  `"xql"` to each tenant credential's `config.capabilities`. The two
  "behaviour change you may veto" items under *Changed* below say exactly
  which routes.

- **`/mcp` now needs `CORTEXSIM_MCP_TOKEN`.** If you run with
  `CORTEXSIM_MCP_HTTP=true`, set `CORTEXSIM_MCP_TOKEN` (generate one with
  `python -c 'import secrets; print(secrets.token_urlsafe(32))'`) or `/mcp` is not
  mounted and `/api/health` reports `MCP_TOKEN_MISSING`. Clients send
  `Authorization: Bearer <token>`. MCP `run.launch` / `run.abort` now refuse
  unless `CORTEXSIM_MCP_ALLOW_HOST_ACTIONS=true`.
- **Tenant reads are metered per tenant.** REST catalog reads now spend the
  per-tenant request budget (`CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE`, default
  60) and can be refused with 429 `XSIAM_QUOTA_ERROR`. Raise the setting if a
  workflow legitimately needs more.
- **New table `tenant_evidence_reads`** is created at boot; no migration step.

### Added — four Linux scenarios promoted from the staged draft library

Four of the seven Linux pairs that `drafts/detection-library/INDEX.md` listed
with no content finding are now corpus content. Each was rewritten from the
draft and the alert's reference page by `ttp-engineer`, reviewed
adversarially by `detection-corpus-reviewer` until it passed (with
advisories, all closed), given real ids, and landed as its own commit. Each
draft pair was removed from staging in the same commit. Ground truth after
the four: **205 scenarios · 203 TTP cards · 1452 step-detections ·
Correlation 149 (10.3%)** (`docs/reference/ground-truth.md`); the staging
library holds 362 pairs.

| scenario | card | draft | targeted alert (reference page) | how it is declared |
|---|---|---|---|---|
| SIM-EDR-026 crond masquerade from a scratch path | TTP-2026-0552 | TTP-0000-0029 | Masquerading as the Linux crond process | the built-in alert as one ABIOC row, 3 XQL, CR-EDR-0026 |
| SIM-EDR-027 compressed-archive exfiltration attempt over SSH | TTP-2026-0553 | TTP-0000-0033 | A compressed file was exfiltrated over SSH | alert hunt-only (no SSH session opens); 5 XQL, CR-EDR-0027 |
| SIM-EDR-028 sudoedit burst in the CVE-2021-3156 probe form | TTP-2026-0554 | TTP-0000-0317 | Sudoedit Brute force attempt | alert hunt-only (needs a trained baseline); 4 XQL, CR-EDR-0028 |
| SIM-EDR-029 sshpass credential-stuffing burst | TTP-2026-0555 | TTP-0000-0321 | Suspicious sshpass command execution | the built-in alert as one ABIOC row, 4 XQL, CR-EDR-0029 |

- **Every step runs for real.** Each chain works inside one hidden `/var/tmp`
  sandbox and makes no real network call: SSH clients aim at a `.invalid`
  host and must fail at name resolution. Each chain removes its own sandbox.
  - The sandbox is created atomically (`mkdir -m 700`, no `-p`).
  - Every later step checks the sandbox with one `stat` of type, owner and
    mode before it touches it. A three-test check was shown racy where
    `fs.protected_symlinks` is off.
  - `sshpass`, `ssh`, `scp`, `sudo` and `sudoedit` are declared in
    `requires_interpreters` where a step needs them, so a host without them
    reads `not_executed`, not missed.
- **Lab runs on `pov-target-01` (2026-11-14)**, recorded in
  `docs/lab/2026-11-14-promoted-linux-drafts-lab-run.md`:
  - **All four completed** (SIM-EDR-029 after `sshpass` was installed on the
    endpoint), and every declared XQL row returned rows in the tenant.
  - **SIM-EDR-027:** step-02's gzip leg was never recorded by the agent, so
    that row was dropped.
  - **SIM-EDR-028:** the agent recorded the first 28 of 60 starts, above the
    floor of 20.
  - **Correlation rules:** CR-EDR-0026, 0027, 0028 and 0029 were accepted by
    the tenant as queries and return their chain's row. None is installed.
  - **Built-in alerts:** none was raised.
- **Left in staging, with nothing a lab run can fire:**
  - TTP-0000-0315 *Rare SSH Session* and TTP-0000-0323 *Uncommon SSH session
    was established*: both need a real SSH session to a second host plus a
    rarity baseline.
  - TTP-0000-0318 *Suspicious container orchestration job*: needs a container
    orchestrator job, and the lab is one VM with no cluster.

### Added — Run detail: a Broken step opens its troubleshooting sheet (design v26 §4.6)

- **A Broken card on a run's chain is now a button** that opens a sheet of
  likely causes, in the order to check, each opening the page that fixes it.
  The causes are read from the host's record of the step (how it ended, its
  exit code, its signal, and the identity and runtime notes in its frame),
  which the sheet shows above them; none is guessed. A step killed by a signal
  POVengine did not send is described as the host half of a prevention and
  opens that run's Results, not as a fault. Exit 127 opens Packages filtered to
  unstaged tools; a timeout opens Build a chain; an agent that stopped
  reporting opens Agents › Fleet; every step ends with its own output.
- **Only a Broken card is a button.** ChainStage takes an optional
  `canSelect(stepId, state)`, so the other live cards stay plain cards rather
  than buttons that do nothing.

### Fixed — four new scenarios set Detection Accuracy bars their rows could not reach

- **SIM-EDR-023, SIM-EDR-025, SIM-KOI-010 and SIM-KOI-011 read FAIL on a healthy
  tenant.** Detection Accuracy counts only rows an alert matched, and their
  Tier-2-verify XQL rows never become a match: verify sets only the row's KPI
  verdict, and reconcile writes an unmatched row as missed. Their bars (80% and
  90%) were above what their alert-backed rows can reach (75%, 33%, 43%, 67%),
  while their text said the opposite. Each bar now counts only alert-backed
  rows, as SIM-KOI-012's already did, and the text states the ceiling: 62.5%
  (EDR-023, with its installed BIOCs and correlation rule, allowing the
  rarity-based ABIOC one miss), 16.7% (EDR-025), 14.3% (KOI-010) and 16.7%
  (KOI-011), each one built-in ABIOC. The same flaw in 43 older scenarios is
  issue #272.
- **SIM-EDR-025 step-03's verify query could not fail.** It matched any
  `findmnt`, `df` or `find` on the host within the hour. It now matches `df` and
  `find` run against the `.cortexsim-akira` staging tree. Its four XQL rows no
  longer name card TTP-2026-0200 without a detection on it, which logged an
  unresolved-card warning at every boot.

### Fixed — the Composer refits when its canvas changes size after the fit

- **On a cold load the rule card could sit under "+ Add step".** The
  readiness strip above the Composer ("SIMCORE DEGRADED") appears only once
  `/api/health` answers. When that landed after the canvas had fitted, the
  canvas lost about 43 px of height while the view kept the zoom fitted to the
  taller one, and "+ Add step", pinned to the canvas bottom, rose into the
  rule card at 1024x768. The canvas now refits when its own size changes, as
  it already did when the rule card moved, and still never once the DC has
  panned or zoomed. This was the intermittent `e2e (smoke + playwright)`
  failure on #258, #260 and #270.

### Fixed — Results at phone width

At 375 px the "Result of" bar on Results squeezed its text column to about
one character, so the run's facts wrapped a letter per line. The run switcher
and the Open button kept their full widths in a three-column grid. The bar is
now a wrapping row: the text keeps at least 240 px, and the switcher and Open
drop below it. The Report header (This run / Compare runs, Validate all,
Export) now wraps instead of running past the view, where it was clipped. A
viewport test at 375 px and 845 px guards both and fails without the fix.

### Changed — the Launch gate is where a run starts: one Run (design C1, v33 MVP)

The Launch gate page now holds the whole launch, top to bottom: **What runs**
(the armed scenario) › **Readiness** (its preflight checks and tools) ›
**Where it runs** (every agent with its state, where an offline agent is
listed but cannot be chosen, plus the offline bundle) › **What it will do**
(steps, detections it should raise, who it runs as, "writes to Cortex or the
firewall: never", who tears down) › an authorisation tick that starts unticked ›
one **Run**. Run stays disabled until a target is chosen, the tick is given and
no check blocks, and every reason is listed under it. For the offline bundle,
Run builds and saves the bundle and records the run, which reads Staged; the
separate "Download bundle" button is gone. This POVengine's own readiness
follows below the panel. The "New POV run" and old readiness links land on
the Launch gate.

### Changed — the warning colour is a muted gold; greens come in shades

Henry, 2026-11-13: the console's yellow is muted to an accent and green
gets more than one shade. The warning colour is now the design system's
gold-600 (`#C7AA47`) instead of Strata yellow, in the dark theme's text and
in both themes' fills and borders (the light theme's warning text keeps its
AA-dark ochre). Every selected state (tabs, filter chips, segmented buttons,
the active rail item) now reads the same: the deep green wash with a matte
green line (the DS panw-green-700). Before, some were white on black and some
bright green. New tokens `--gr-matte`, `--gr-mid` and `--gr-pale` take the
DS's panw-green-700, green-500 and green-200, and the drift guard pins them.

Buttons follow one language. Every primary button (the kit's, `pov-btn` and
the legacy `.btn`) is the same matte deep green with white text in both
themes (`--btn` `#0A6231`, 7.5:1), with a hover a shade lighter and no glow or
brightness filter. Secondary buttons are greyscale with a matte-green hover
line. The error badge ("5 not ok") is greyscale and sized like a button; only
its "!" carries the gold or coral accent, so it no longer reads as a filled
alarm next to the action it describes.

### Fixed — the ANALYTICS plane is no longer PANW orange

Design v26 keeps PANW orange for the NGFW family tint alone, but the
ANALYTICS plane (the correlation engine) still used it as its categorical
tint, so analytics lanes, dots and chips read as firewall. ANALYTICS now uses
the DS ink-400 (`#9A9A9A`), a neutral for the engine that sits over every
plane rather than a sensor. The tokens and the plane vocabulary
(`povdata/planes.js`) agree, and the drift guard now pins it and asserts it is
not orange.

### Changed — TTP cards filter by subdomain, not status or platform

The TTP cards page now filters by **Subdomain** (the UC / TC index's
subdomain for the test cases each card evidences; a card in two subdomains
matches either) and Tactic. The Status filter, which was "active" for every
card, and the Platform filter are gone, as are the platform count and the
platform chips on each card. A card shows its status only when it is a
draft or deprecated.

### Added — run detail says why a test case is pending (design C3)

A run whose test-case verdict is pending with nothing scored now says why on
its own page, not only on the Topology and Techniques tabs. Under the
lifecycle strip a "Test case" line shows PENDING, the reason chip
("nothing executed" or "detectors soaking") and the test case id, with the
action as a visible sentence. Under the chain, the Cortex Engine terminal
states the same verdict and reason. A verdict without a reason draws neither.

### Added — deep dives open with "Why this matters"; sheet actions are verbs (design v33 C10)

- **The scenario sheet, the TTP card and an opened detection start with four
  parts: Thesis · Why we simulate it · What proves it · What it needs.** Each
  line is read off a field the document already carries; the console writes
  none. A scenario: its technique and the report it is modeled on; the test
  case, use case and method it evidences; its success criteria, threshold and
  expected detections; its tools, identity, Cortex platform, add-ons and the
  planes its incident must span. A TTP card: the first sentence of its
  description; its threat scenarios; its threat steps' objectives; its target,
  privilege and prerequisites. A detection: its description (or an IOC's
  context); the threat step whose success criteria name it, and the techniques
  it covers; those criteria and, for a query, its row floor; who owns the work
  (run it, deploy it, or built in) and the dataset it reads. A part the
  document does not carry says "Not written for this card yet." (or scenario,
  or detection) and nothing else. The derivation is one pure module,
  `sheetThesis.js`.
- **Sheet actions are verbs from the closed set, each with its icon.** The
  kit Button takes `verb` and draws that verb's icon (`VERB_ICONS`); the Icon
  set gains `check`, `gear`, `pencil`, `rotate`, `external` and `target`. On a
  TTP card, "Launch all…" is now Run and "Edit…" is Customize, beside Add to
  chain; the ATT&CK Navigator layer is a file, not an action, and reads as a
  link. On the scenario sheet, Run and Customize carry their icons, and the
  tools warning's "Review ▸" button is now a "Packages →" link.

### Changed — Runs · Compare: two or three finished runs, and no claim without a read back (design C2)

- **The Runs header has a primary Run beside a secondary Compare runs.** Run
  opens the New POV run page, where Scenarios and ⌘K start every launch. The
  design draws it as "New run"; that label is not in the console's closed verb
  set, and Run is.
- **Compare takes two or three finished runs**, picked as chips that say
  whether each run has been read back. A running or staged run is not offered.
  A fourth pick is refused with the reason. It used to list sixteen runs of any
  status and take four.
- **One row per expected detection, one column per run**, then four paired
  rows: Detected, Steps that ran, Fastest detection and Workflow (the scenario
  the run executed). Each column header carries the run's next action: Read
  back while it has not been read back, Open results once it has. Both use the
  console's existing Read back and Results route. Rows line up on the card's
  detection id; before, they keyed on fields a Result row does not carry.
- **Only a read-back run says Detected or Not seen.** Any other run says "Ran —
  not read back" or "Did not run", even when its rows carry a hand mark or an
  outcome, and its Detected and Fastest detection rows read a dash. The words
  are the chain's own (`LIVE_STATES`). Before, an unread run's hand-marked row
  read Detected, and the prevention delta for a report/block pair was drawn for
  runs that had not been read back; it now waits until both have been. A runs
  list or a run's results that fail to load are said to have failed, never shown
  as empty. The coverage percentages and the new/regression flags are gone with
  the old grid.

### Added — every component page explains itself (design v33 C7)

- **Each of the eight component pages now opens with four plain lines** under
  its header: What it is · Why the POV needs it · What it unlocks · Where it
  lives. They stay on every tab (Overview, Setup and the management tab). The
  copy lives in one module, `ui/src/components/console/componentAbout.js`,
  keyed by component id; a page whose id has no entry renders without the
  block rather than borrow another component's words.
- **The canvas copy was held to what the code does.** The Tenant and Firewall
  lines say POVengine never writes to either. The Agents line says nearly
  every scenario step runs on an agent (an offline bundle runs without one),
  in place of a five-plane list. The HTTP collector line names the log
  streams the collector-class emitters actually send; AI access and AI
  runtime scenarios do not go through it. Read-back is described as checking a
  run's expected detections, not as the source of every Detected result,
  because a result can still be marked by hand. The XDR Collector and Cortex
  Engine lines say POVengine maps no scenario to them yet (the canvas had the
  Engine unlocking ANALYTICS scenarios), and the Cortex paths match the ones
  their Setup steps already give.

### Added — header Back / Forward and a breadcrumb (design C9)

- **Back and Forward in the header** walk every page, tab, run and deep dive
  the URL has named, in the order you opened them. ⌘[ / ⌘] on a Mac and
  Ctrl+[ / Ctrl+] elsewhere do the same, except while focus is in a text field.
  Each button is disabled when there is nowhere to go.
- **The router keeps the history, not the browser.** Opening a run, a tab or a
  card rewrites the current browser entry (`setParams` uses `replaceState`),
  so `history.back()` could not see those steps. `useConsoleRouter` now keeps
  its own list (`app/navHistory.js`). A change to a list filter (`q`, `status`,
  `page`, …) rewrites the current entry rather than adding a step, a retired
  page that only redirects is never kept, and the browser's own Back onto the
  previous entry moves the header's position with it.
- **A breadcrumb beside the buttons: journey › page › object.** The journey is
  the rail group, the page is the rail item and the page itself when it is a
  tab ("Catalog › UC / TC index"), and the object is the run, scenario, card,
  test case, package or component the URL opens. Every crumb but the last is a
  link; the last is the current page, drawn in the selected state. When the
  header is crowded the outer crumbs shorten first and the bar scrolls below a
  floor, as its other controls do.

### Changed — UC / TC index: the proof path, by tab (design C5 / C6)

The four tabs now say how far each detection test case (DET and HNT) has got,
from what SimCore has recorded. A test case counts as covered only once a
read-back run of a scenario that evidences it saw a detection.

- **Index.** Four tiles over the table: Use cases, Partly proven, Pending and
  Blocked. The table gains a Verdict column (icon and word: Detected, Not seen
  yet, Pending, Blocked, No scenario, Can't check) and a Runs rollup (runs in
  progress, waiting, not read back and read back), with the VerdictReason chip
  for runs where nothing executed. The use-case rail shows each use case's
  verdict. A scenario or assertion binding is no longer drawn in the
  detected green.
- **Mechanisms.** One card per detection mechanism (BIOC, ABIOC, XQL,
  Correlation, IOC, and any other type a scenario declares): how many test
  cases rely on it and how many objects their scenarios declare. Below, one row
  per test case with its mechanisms and the component its signal comes from,
  which opens that component. No SimCore API reads whether a mechanism's
  content is in the tenant, so every tenant state reads Can't check.
- **Coverage.** The proof path as five counts (Has a scenario, Components
  ready, Ran, Read back, Detected), then a status per stage for each test case,
  with the VerdictReason chip where it stalls. A run that was not read back
  never counts as Detected, whatever its detection count. A staged push bundle
  reads Waiting, not ran.
- **Gaps.** Every test case not yet detected, earliest stall first, filterable
  by No scenario, Component not ready, Never ran, Not read back and Not seen
  yet, each with one sentence and one verb: Configure, Run, Read back or Open
  results. No scenario links to Build a chain instead.
- **A source that did not load reads Can't check, never zero.** A door counts
  as not ready only when it was checked and found closed; an unchecked door is
  Can't check. The console now records a failed `GET /api/runs` read
  (`errors.runs`) instead of treating it as an empty run list.
- **Removed:** the validation-class strip, the class-by-class Mechanisms mode,
  the per-use-case Coverage rollups and the closability Gaps list. The
  assertion catalog panel (refused, absent and empty states) stays at the foot
  of Mechanisms; the test-case drawer still names a row's assertion.

### Added — Scenarios: the extended library, a mapping on every card, and a compose → simulate stepper

- **The extended library is listed below the corpus.** The extended library
  is the staged draft pairs under `drafts/detection-library/` (362 today). A
  new read-only route, `GET /api/scenarios/staged`, serves them in the corpus
  row's shape. They are still not loaded as scenarios, cannot be opened, pinned
  or run, and are not counted as coverage. Each card shows its card id,
  platform and how many findings the manifest records for it (listed in the
  tooltip). A **Library** select lists both
  libraries (the default), the corpus or the extended library. A SimCore
  without the directory, such as any image, which does not ship `drafts/`,
  answers `loaded: false` with `STAGED_LIBRARY_ABSENT`. The page then says the
  extended library is not on this SimCore; it never shows an empty one.
- **Every card shows UC/TC · Source · MITRE.** Source is the ingestion doors
  the scenario's detections land on, read from a new `doors` field on door
  readiness (`GET /api/doors/readiness` and `/api/scenarios/{id}/doors`). The
  field lists every door a derived detection names, whatever the door's state.
  `not_open` could not stand in for it: an open door drops out of `not_open`,
  and with no agent selected an agent door is never listed there. The use case
  and test case moved from the tag row to this line. A mapping the API reports
  as absent reads **Not mapped**. One it did not answer (readiness not loaded,
  the dataset registry absent) reads **Not checked**. Nothing is inferred.
- **An Unmapped filter with its count.** The toggle lists the scenarios
  missing a UC/TC, a detection source or a MITRE technique, and its count
  follows the library in view. On this tree that is 8 corpus scenarios, whose
  detections all derive no door, and every staged pair, which is generated
  without UC/TC refs. A scenario whose source was not checked is not counted.
- **Scenarios › Build a chain › Launch gate › Run.** A stepper on those four
  pages marks the current step and links the others. It carries the chosen
  scenario where the next page takes it: `?open=` for Scenarios and the
  Composer, `?arm=` for the Launch gate and `?run=` for that scenario's newest
  run. In the Composer it carries a launch id only for a saved chain with no
  unsaved edits, and leaving through it asks first when edits are unsaved.

### Added — Dashboards on the rail (design C8)

- **The hidden Coverage page is now Dashboards**, a rail item under Run &
  observe after Results. Its route is still `#/coverage`, so links and
  bookmarks keep working; `?tab=` opens a view. Results is one page again and
  no longer draws a tab strip with a single tab.
- **Three views count every scenario from `GET /api/scenarios`.** *Detection
  source × Use case* puts use cases (the parent of each `uc_ref`) down and the
  planes the scenarios raise detections on across. *MITRE ATT&CK* draws
  `GET /api/mitre/coverage` on the 14 Enterprise tactics, with Plane, Detection
  source and Gaps only filters; a technique no scenario in scope exercises is
  drawn dashed. *Planes × doors* counts the scenarios on each plane waiting on
  each door that is not open (`GET /api/doors/readiness` for the agent the
  header has selected, and `GET /api/doors`); a scenario with no readiness,
  or one that needs an agent door while no agent is selected, reads Not
  checked, never ready. Any cell or technique lists the scenarios behind it,
  each opening its sheet on Scenarios. The old page's PANW stack and
  Competitive lenses stay as tabs.
- **What a scenario has proven comes from run read-back only.** Detected
  means a run of it was read back from the tenant and SimCore counted a
  detection on it. A run that finished but was never read back reads Pending,
  even when it has detections counted; a scenario that is only authored reads
  Never run; a run list that did not load reads Can't check. The coverage
  endpoint's own status no longer colours the ATT&CK tiles. Every state is an
  icon and a word.
- **The ATT&CK placement is the endpoint's.** It files a step's technique
  under its scenario's tactic; the page says so. A technique under a tactic id
  that is not one of the 14 gets its own column instead of vanishing.

### Fixed — an HTTP collector target keeps gzip, and the page says to use it

- **Saving a collector target turned compression off.** The API's
  `compress` defaults to false and a `PUT` replaces the target, and neither
  the Collector targets form nor the HTTP collector page's **Test** sent the
  field. So every save or edit stored `compress: false`, and an XSIAM HTTP
  collector created gzip-only rejected every POST that followed. Both forms
  now have a **Send gzip-compressed** tick, on by default, with a note that
  gzip-only collectors reject uncompressed POSTs, and they always send the
  value. An edit keeps the target's own setting.
- **The HTTP collector page recommended uncompressed JSON.** "Choose format
  and dataset" now asks for gzip to match the collector's Compression
  setting, and the example `curl` on the send step gzips its body with
  `Content-Encoding: gzip`.
- **A target row shows its compression**: `gzip`, or `none` with a warning
  that a gzip-only collector rejects these.

### Security

- **`cortexsim.run.list` and `cortexsim.run.pair` handed an agent the tenant's
  case ledger unprojected.** `run.get` already put a case's analyst
  `resolve_comment` under `tenant_text` and withheld an unsafe `xdr_url`; the
  two sibling tools returned both raw, with no `data_notice`. They now project
  every run's ledger the same way and add `data_notice` when a ledger has
  cases. The REST routes and the stored ledger are unchanged.
- **A leftover cron entry or systemd timer could run another user's script as
  root (SIM-EDR-023, SIM-EDR-024).** Each scenario installs a root job that
  runs a script from a fixed directory under `/var/tmp` every five minutes, and
  the job can outlive the directory: the beacon does not run a scenario's
  cleanup, and a tmp cleaner or a partial cleanup can remove it. A local user
  could then recreate the directory with a script of their own. The cron line
  and the unit's `ExecStart` now run nothing from the directory unless one
  `stat` lookup reads it as root's own 0700 directory; otherwise the cron entry
  deletes itself and the unit disables its timer. The check is in `ExecStart`
  because systemd before 243 ignores `ExecCondition=`. The step that recreates
  the directory first removes a cron entry or timer left by an earlier run. The
  re-executions keep the command-line path the cards' BIOCs match, so no
  detection changed. The two cards' illustrative payloads now run under
  `set -eu`, stop unless the directory they make is root's own 0700 directory,
  and install the checked entry.

- **A root systemd unit and root crontab lines ran programs from `/tmp` paths
  another user could create (SIM-CDR-006, SIM-CDR-002).** SIM-CDR-006 enabled a
  `Restart=always` unit whose `ExecStart` was a fixed `/tmp` file the scenario
  never created, and a root crontab watchdog that re-enabled it every 10
  minutes. SIM-CDR-002 added a root crontab line that runs `/tmp/.xmr/xmrig`.
  The beacon does not run a scenario's cleanup and `/tmp` is emptied at boot,
  so these entries outlived what they pointed at. SIM-CDR-006 now makes its
  marker in a root-owned 0700 directory, and the unit's `ExecStart` and the
  watchdog run nothing unless one `stat` lookup reads it that way; otherwise
  the unit disables itself and the watchdog removes itself. The check is in
  `ExecStart` because systemd before 243 ignores `ExecCondition=`. The step that
  makes the directory first retires a unit or watchdog left by an earlier run,
  and the marker now retires the foothold 45 minutes after install. Both
  SIM-CDR-002 crontab lines run the miner only from the 0755 directory step-01
  made, and only while the miner itself is a regular 0755 file owned by the
  same account. Step-02 extracts the archive with `--no-same-owner
  --no-same-permissions` and sets the miner's mode, because `tar` run as root
  otherwise keeps the archive's owner and mode. The two cards' illustrative
  payloads run under `set -eu`, stop unless
  the directory they make passes the same check, and install the checked
  entries. No detection query changed.

- **A path override on a tenant credential could send the tenant's API key to
  another host.** The connector checks the tenant's base URL, then appended the
  credential's `alerts_path` / `incidents_path` / `issues_path` / `cases_path` /
  `endpoints_path` as given. A path that does not start with `/` changes the
  host of the result (`…paloaltonetworks.com` + `@attacker.example/x` is a
  request to `attacker.example`), and the API key went with it. The credential
  API is unauthenticated, so anyone who could reach SimCore could set such a
  path on a stored credential. A path override must now start with `/`;
  anything else is refused with `XSIAM_CONFIG_ERROR` before any request is
  made. `alerts_path` and `incidents_path` have had this since 1.1.0; the other
  three arrived with the tenant reader in this release.

- **Closed an unauthenticated root command execution on `POST /api/tools/{tool}/start`
  (SEC-02, verified on cc26ddf).** The request's `params` were merged over the
  registry's own `binary` / `source_path` / `port`, so
  `{"params":{"binary":"…"}}` against `xdrtop` ran an arbitrary command as
  SimCore's user, which is root in the image. Caller params now fill only the
  placeholders the run template leaves open. Each value is one shlex-quoted
  argv token, and `*_path` must resolve under `CORTEXSIM_BASE_DIR`. A refusal
  is `400 BAD_TOOL_PARAM` and spawns nothing. `POST /api/tools/{tool}/install`
  runs off the event loop, one at a time; a concurrent call gets
  `409 INSTALL_IN_PROGRESS`.
- **The `docker.sock` mount is gone from `docker-compose.yml`.** Nothing in
  `core/` uses Docker. The mount only turned any command execution in the
  container into root on the DC's host. Shutdown is now bounded
  (`--timeout-graceful-shutdown 5`, `stop_grace_period: 15s`), so open SSE
  streams no longer get SimCore SIGKILLed mid-write. A non-root `USER` in the
  image is still deferred: it cannot be verified without an image run.
- **CORS is off by default, and a Host guard refuses DNS-rebinding requests.**
  CORS was `allow_origins=["*"]`, so with no authentication any web page a DC
  had open could read every API response. CORS is now mounted only for origins
  in `CORTEXSIM_CORS_ORIGINS` (empty default) and never with credentials. The
  new outermost middleware answers `400 HOST_NOT_ALLOWED` unless `Host` is
  `localhost`, an IP literal, this machine's own hostname/FQDN, or a name in
  `CORTEXSIM_ALLOWED_HOSTS` (`*.x` matches subdomains, `*` disables the
  guard). Inside the container the hostname is the container id, so
  `scripts/dev-up.sh` seeds `CORTEXSIM_ALLOWED_HOSTS` with the jumpbox's short
  and fully qualified names when it writes `.env`. **A beacon started with
  `--server http://<dns-name>:8888` must have that name allowed, or every poll
  gets `400 HOST_NOT_ALLOWED`.** Compose publishes on the address in
  `CORTEXSIM_BIND`; see *Upgrading from 1.1.0* for what an empty value means.
- **A Host-guard refusal is now recoverable and leaves a trace (round 2).**
  Allowlist entries written as URLs (`http://jumpbox.lab:8888`), with a
  trailing dot or as `.lab` are normalised to what the guard compares. An
  entry that can never match is dropped, warned about at boot and listed in
  `/api/health` (`api_auth.ignored_allowed_hosts`). `/api/health` also lists
  the last refused names (`host_guard_refusals`, `last_refused_hosts`; in
  memory, reset on restart). The installer route refuses a `?server=` name the
  guard would refuse (`400 SERVER_HOST_NOT_ALLOWED`) instead of handing out a
  script whose beacon can never poll. The Linux/macOS installer reports a
  guard refusal as `HOST_NOT_ALLOWED`, where it used to say `ENROLL_DENIED` and
  "mint a fresh token".
- **An ignored `X-Forwarded-For` is visible, and `CORTEXSIM_TRUSTED_PROXIES`
  works from `.env` (round 2).** The first ignored header from each unlisted
  peer logs one warning. `/api/health`'s `agents` component reads
  `XFF_FROM_UNTRUSTED_PEER` while there are any, or `AGENT_ADDRESS_SHARED` when
  distinct hosts share one recorded address, which is what an untrusted proxy
  does to every agent behind it. `CORTEXSIM_TRUSTED_PROXIES` is now a declared
  setting, so a host-run `.env` carrying it boots (it used to refuse with
  "Extra inputs are not permitted").
- **The IaC generator refuses request text that is not what its field names
  (SEC-17, round 2).** `region`, `jumpbox_size`, `tags` and `dc_ssh_cidr` are
  checked against an allow-list before any HCL is rendered, and answer
  `400 BAD_INFRA_PARAM` naming each field. A `dc_ssh_cidr` carrying an IPv6
  zone id could smuggle a `terraform_data` `local-exec` into `variables.tf`;
  it is refused.
- **`scripts/lab/publish-agent.sh` never bakes a token into the served
  installer (SEC-16, partial).** `--token` is refused (exit 2) without echoing
  the value; pass the token on each target at run time with
  `CORTEXSIM_TOKEN`. If an older run baked one in, revoke it
  (`DELETE /api/agents/enroll/tokens/<id>`) and re-run the script. How tokens
  are stored and the mint ceilings remain open decisions.
- **Installer text says what the checksum proves (INST-21).** Both installers
  now say the digest check "catches corruption, not tampering": the beacon and
  its digest come from the same SimCore over the same, by default plain-HTTP,
  channel. The old hint to "look for an intercepting TLS proxy" is gone.
  Authenticity (a pinned CA, a signed beacon, HTTPS) is still decision SEC-12.
- **`/api/health` now says the API is unauthenticated.** The new `api_auth`
  component reads `degraded` with `API_UNAUTHENTICATED` on every current
  deployment, by design, and boot logs a warning naming what is exposed. So
  the overall health `status` of every deployment is `degraded` until
  per-request authentication lands (SEC-01, still open). Any **other**
  degraded component is a real fault. The console labels this component as
  the expected standing state.
- **The `.env.example` placeholder secret is refused in every environment
  (SEC-11).** `replace-me-with-a-generated-32plus-byte-secret` is 46 characters
  and passed the production guard, so `cp .env.example .env && docker compose
  up` encrypted the vault under a public, committed key. Anything starting
  `replace-me` is now refused in development as well as production.
- **A changed master key is detected (OPS-12).** If `.env` was regenerated over
  a surviving `simcore_db` volume, every stored credential was undecryptable
  while `/api/health` still read `integrations: ok`. A key-check row now makes
  the new `master_key` component report `MASTER_KEY_MISMATCH`, and `dev-up.sh`
  warns before it does that. A secret name in the reserved `__cortexsim__/`
  namespace is now `400 RESERVED_SECRET_NAME` instead of a 500.
- **XQL `target` stages are refused before they reach a tenant (SEC-05).**
  `| target type=lookup append=false <name>` *replaces* a tenant lookup, which
  may be one the customer's correlation rules read. Four paths sent query
  strings to `start_xql_query` uninspected: the `/xql` route, the generic
  executor for `OP-XQL-START-XQL-QUERY` (catalogued `read`, so it ran with no
  consent), the verifier runners, and Composer draft `verification_xql`. One
  fail-closed guard inside `XsiamClient.start_xql_query` now returns
  `400 XQL_WRITE_STAGE_REFUSED` on every path. A follow-up closed a bypass
  where a stray quote inside a backtick field or a `'…'` literal hid the
  stage from the lexer. The guard reads 16 quote/comment/escape readings and
  refuses if any one sees a `target`. The catalog op is now tagged
  body-inspected.
- **Hostile text can no longer become a command in a push bundle (SEC-13).**
  Composer drafts refuse CR/LF, tab, NUL, ESC, DEL, NEL and U+2028/2029 in
  single-line fields (name, author, tags, step id/name, detection
  description/id/ttp_ref). The bash bundle also sanitises every
  scenario-sourced value it writes into a `#` comment, because a newline there
  ended the comment and the next line ran on the customer's endpoint.
- **Installer parameters can no longer inject script text.**
  `?id=x'; Write-Host PWNED; '` was served 200 and, on Windows, became the
  SYSTEM service's command line; the bash installer had the same flaw. Both
  installers now refuse malformed input with structured 400s: `BAD_AGENT_ID`,
  `BAD_SERVER`, `BAD_TOKEN` and `BAD_INTERVAL`. The `BAD_SERVER` rule also
  applies to the Host-derived default.
- **`X-Forwarded-For` is believed only from `CORTEXSIM_TRUSTED_PROXIES` (SEC-15).**
  Any host could stamp an arbitrary address on an agent row, and
  `Agent.last_ip` becomes the run's `src_ip`, which detections are stitched on.
  The header is now read right to left, and only from peers inside the listed
  CIDRs (empty by default).
- **Run output, observation batches and the run list are bounded (SEC-14 /
  OPS-06).** Run output is capped at 1 MiB per chunk and 32 MiB per run, with
  integrity lines reserved. An observation batch is capped at 5000 alerts.
  `GET /api/runs` is paged.
- **`dc_ssh_cidr` refuses prefixes shorter than /16 (IPv4) or /48 (IPv6)** with
  `422 VALIDATION_ERROR`, because the same CIDR also gates port 8888 on the
  generated jumpbox.
- **Release workflow least privilege (CI-12).** `release.yml` now grants
  `permissions: {}` at the top and per-job scopes beneath it, and the
  `workflow_dispatch` tag input reaches its script through `env`, not
  `${{ }}` interpolation. `.github/dependabot.yml` tracks GitHub Actions weekly
  against `dev`. In round 2 every third-party action was pinned to a full commit
  SHA with a `# vN` comment (60 `uses:` lines, 13 actions; no version changed).
  No runner has fetched a pinned action yet.
- **Writes through MCP preview by construction** (from `dev`): a write or
  destructive operation with `dry_run: false` returns
  `MCP_WRITE_NOT_EXECUTABLE` with the preview attached and the tenant is never
  called. There is no write path to Cortex through this surface;
  `CORTEXSIM_XSIAM_ALLOW_WRITE` and `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE` stay
  default-off. The `/mcp` mount is off by default and, like the rest of
  SimCore, has no authentication of its own.

### Fixed — Windows push bundles (`format=powershell`)

- **Every PowerShell-dialect push step was a silent no-op from v1.0.0 through
  v1.1.0 (PUSH-01, since `ea5a52f`).** The harness built each step file inside
  a double-quoted string, so `$ErrorActionPreference = 'Stop'` expanded to
  `Continue = 'Stop'`. That is the `continue` keyword, which outside a loop
  ends the step with exit 0 before the TTP line runs. The bundle then logged
  "completed successfully". At v1.1.0 this hit 48 of the 68 steps in the 13
  Windows bundles, and every PowerShell cleanup line. Steps now really run. A
  PowerShell step that skips its epilogue (top-level `return` / `break` /
  `continue` / `exit 0`) reads `EPILOGUE_NOT_REACHED`, not success. *The
  no-op and the fix were both executed under pwsh 7 on macOS/Linux only; 5.1
  is inferred to behave the same. The green `windows` job ran a CI probe
  bundle (its PowerShell steps ran, and a failing step was not logged as
  successful), not a shipped bundle.*
- **A `.ps1` bundle now has an exit-code contract and an outcome table.**
  `0` clean · `1` a step was non-zero, threw or timed out · `2` refused because
  cleanup residue was not acknowledged · `3` a step was not executed
  (`PREREQ_MISSING`, `OPERATOR_INPUT_MISSING`, `NOT_ELEVATED`,
  `SHELL_UNAVAILABLE`, `NOT_REACHED`) · `4` cleanup failed. Before this, a
  bundle exited 0 even when steps failed. Every run now ends with a
  per-step outcome table. Each step has a timeout (default 300 s); on timeout
  `taskkill /T /F` ends the step's whole process tree. A step needing admin is
  marked `NOT_ELEVATED` when the session is not elevated. Prerequisites are
  probed with `Get-Command` before a step runs, and the header lists the
  operator inputs each bundle requires. The bundle no longer claims "missing
  tools are logged as WARN". It installs nothing itself, and a missing tool
  means the step is not executed.
- **Cleanup residue is refused unless acknowledged.** A Windows bundle refuses
  to start (exit 2) when its scenario cleanup carries POSIX-only lines it cannot
  run on Windows, unless `CORTEXSIM_ACCEPT_RESIDUE=1` is set. Today 9 of the 13
  Windows bundles need it: `SIM-ASM-005`, `SIM-EDR-003`, `SIM-EDR-004`,
  `SIM-EDR-005`, `SIM-ITDR-006`, `SIM-MP-001`, `SIM-MP-020`, `SIM-NDR-005` and
  `SIM-NDR-007`. Only `SIM-EDR-006`, `-013`, `-017` and `-021` run without it.
- **The Windows step dialect is classified by structure, and a mixed dialect is
  refused (CONT-13).** The old classifier keyed on a hand-picked list of 28
  cmdlet verbs. It sent `wevtutil cl Security & … & Clear-History` to cmd.exe,
  where it runs correctly under neither interpreter. The new classifier reads
  structure: assignments, `[Type]::`, any `Get-Verb` verb, `$env:`, `%VAR%`,
  infix `&`, and so on. A step that is genuinely both now gets
  `409 BUNDLE_TARGET_UNSATISFIABLE` with reason `MIXED_DIALECT`. A bare
  `powershell.exe -Command "…"` step now runs through cmd, because an outer
  PowerShell emptied every `$x` inside the double quotes.
- **13 scenarios emit a Windows bundle (9 both-target, 4 Windows-only).**
  `SIM-EDR-004` stays in that set because its step-01 was rewritten as
  single-dialect PowerShell (below). The emittable spread over the 193
  scenarios is posix-only 179 · both 9 · windows-only 4 · none 1 (`SIM-MP-030`).
  Three scenarios are `push_supported: false`, so what a download serves is
  posix-only 177 · both 9 · windows-only 4 · none 3 (see `pushable_targets`
  below). None of the 13 bundles has executed on any OS. At `d7f4f29` each was
  parsed by Windows PowerShell 5.1 itself and passed PSScriptAnalyzer's 5.1
  profiles in the green `windows` job. The round-2 header and table changes
  are parsed only under pwsh 7.
- **Bash and K8s bundles no longer attempt a scenario's Windows teardown
  (round 2).** 25 Windows-only cleanup lines in 10 scenarios ran in the bash
  bundle and the K8s bootstrap, where they could only fail. They are now
  `# skipped (Windows-only): …` comments in bash and omitted from
  `bootstrap.sh`. A Linux teardown line with a cmdlet-shaped argument
  (`kubectl delete pod Test-Runner`, `pkill -f New-Relic`) still runs.
- **A `.ps1` step that cannot run as its declared account says so (round
  2).** Windows has no credential-free impersonation, so such a step runs as
  the user who launched the bundle. The header now lists those steps, the
  outcome table gains an IDENTITY column (`direct` / `elevated` / `DEGRADED`),
  and the run ends `cortexsim-identity-summary degraded=N ran=M`. 19 steps in
  5 of the 13 bundles read DEGRADED: `SIM-EDR-005` 5, `SIM-MP-001` 4,
  `SIM-MP-020` 6, `SIM-EDR-013` 1, `SIM-EDR-017` 3. A detection keyed on the
  declared account will not match those steps. Exit codes are unchanged.
  *Ran in the `windows` job's push phase at `e434efa` with a probe bundle
  (`degraded=0 ran=4`); no shipped bundle with a DEGRADED step has run.*
- **A cmd step whose `%` reads two ways is refused (PUSH-11, round 2).** The
  bundle runs single-line cmd text on the command line, the beacon always from
  a `.cmd` file, and cmd reads a FOR variable, `%%` and a lone `%` differently
  in the two. Such a step is now refused with reason `CMD_PERCENT_CONTEXT` on
  push and pull alike, with a hint. Write loops in PowerShell; `%NAME%` and
  `%NAME:~0,4%` are fine. `SIM-EDR-002` step-01 was rewritten. The cmd
  behaviour is taken from documentation, not executed.
- **`SIM-NDR-005` on Windows uploads only to a receiver you name (CONT-16,
  round 2).** Once round 1 made PowerShell push steps run, its Windows steps
  really POSTed 4 × 256 MiB to a hard-coded third-party host. Both now read
  `CORTEXSIM_EXFIL_URL` and refuse without an http(s) URL: the bundle
  reports `OPERATOR_INPUT_MISSING`, and pull fails the step by name before any
  request. The receiver must be one the NGFW sees as external (the volume
  BIOC excludes RFC1918), and the card's pre-flight XQL names
  `exfil-receiver.testmynids.org`, so substitute your host there. The Linux
  and macOS legs still use the hard-coded host. *Streaming the upload under
  .NET Framework is not yet run on Windows.*
- **The `.ps1` header explains Group Policy and AMSI (PUSH-16, round 2).** A
  MachinePolicy or UserPolicy execution policy outranks
  `-ExecutionPolicy Bypass` for the bundle and every child step, and AMSI or
  the endpoint agent can stop the whole file before step 1. The run logs
  `cortexsim-policy-warning reason=EXECUTION_POLICY_GPO` when either policy is
  AllSigned or Restricted. It warns only.
- **A step can set its own timeout (round 2).** `timeout_seconds` (1–86400)
  now survives the scenario loader and bounds that `.ps1` step; it used to be
  dropped, so every step ran with the 300 s default. No corpus scenario sets
  it yet.
- **A variant step says whose ATT&CK technique it shows (CONT-17, partial).**
  A `.ps1` step resolved from a Windows variant prints `# MITRE: <id>
  (authored for the primary command; this step runs its windows variant)`.
  Per-variant techniques need a schema decision that is still open.

### Fixed — Windows scenario content

- **Windows content now tears down what it creates.** `SIM-EDR-003` left behind
  a SYSTEM scheduled task, an auto-start service, a local administrator using
  the repo password `TempP@ss123`, a real `authorized_keys` line and an HKCU
  Run key. Every one of its cleanup lines was POSIX and was skipped on
  Windows. Every Windows create is now paired with an in-step delete.
  `authorized_keys` is backed up and restored byte-for-byte. Residue was also
  removed from `SIM-ITDR-006`, `SIM-MP-001/018/020/025/026/029/030` and
  `SIM-EDR-006`.
- **No scenario clears a real Windows event log or deletes recovery any more.**
  `SIM-EDR-004` cleared the real Security, System and Windows PowerShell logs.
  It now creates, clears and removes a `CortexSim-EDR004` channel, which keeps
  the clear-log behaviour but not Security event 1102. `SIM-EDR-011` deleted
  every shadow copy and the backup catalog, and disabled boot recovery. It now
  lists shadows and deletes only the nil shadow id.
- **The LSASS dump step waits for the dump before checking it.**
  `rundll32.exe` is a GUI-subsystem binary, so PowerShell's `&` did not wait
  for it. `SIM-EDR-006` step-02 therefore raced its own `Test-Path`, reported
  prevention on no evidence, and left a full LSASS dump in `%TEMP%`. The step
  now uses `Start-Process -Wait` and deletes the dump in-step. Card
  `TTP-2026-0002` keys on `action_process_image_name = rundll32.exe`.
- **Only detections a lab run can fire are declared (CONT-04/10).**
  `SIM-EDR-021` and `SIM-EDR-013` only wrote marker files, yet declared
  driver-load, module-load, scheduled-task and shadow-delete detections plus
  their correlations. A healthy tenant scored every one as a miss. Both now
  emit real, inert telemetry, and the unfirable detections moved to
  `purpose: hunt`. `SIM-EDR-017`'s egress leg now runs only against an
  operator-named `CORTEXSIM_C2_HOST`; before, it defaulted to loopback inside
  an empty `catch {}`. Corpus step-detections went 1377 to 1369; Correlation
  share is 10.0% (137 of 1369), exactly at the `coverage-strict` floor.
- **Windows variant text runs on 5.1 and cmd.** Fixed
  `%STAGE_HOST%`/`%ASM_TARGET_HOST%` used in PowerShell, which never expands
  them, and `-SkipCertificateCheck`, which 5.1 lacks. Also fixed
  `powershell -NoExit`, which never returns, `;` used as a cmd separator, and
  single-`%` loop variables in a `.cmd` file.
- **111 Windows labels with no Windows command behind them were relabelled, and
  three lint codes keep them out.** 100 steps in 28 scenarios declared `windows`
  with only POSIX text, and 11 more carried Windows primaries the resolver
  refuses. The console and per-OS counts read those labels as Windows coverage.
  Scenarios with a Windows-labelled step went **83 → 69** (steps 263 → 152). New
  boot **warnings** (not gated by `CORTEXSIM_STRICT_REFS`) keep it from
  recurring: `S-28` (a `windows` label nothing resolves), `S-29` (its POSIX
  mirror) and `S-30` (a doubled backslash in a Windows variant).
- **Bash bundles no longer die in cleanup on a Windows teardown line.** A
  Windows cleanup line containing `"$env:TEMP"` made bash, under `set -u`, end
  the EXIT trap with `env: unbound variable`. That skipped every Linux cleanup
  line after it, for example `SIM-MP-016`'s and `SIM-MP-025`'s.

### Fixed — Windows pull path (beacon and installer)

- **The Windows beacon now runs as a real service (BEACON / CI-02).** The
  elevated installer registered `CortexSimAgent`, but nothing in the beacon ever
  called `StartServiceCtrlDispatcherW`. By the SCM contract every elevated
  install would therefore have died with error 1053 about 30 s after reporting
  success, and again after every reboot. That is inferred and was simulated
  with a fake `sc.exe`; it was never observed on a Windows host. The beacon now has a stdlib-only advapi32 SCM handler: STOP and
  SHUTDOWN are handled, there is a 60 s stop deadline, and `go.mod` still has
  no dependencies. A new `--log-file` (env `CORTEXSIM_AGENT_LOG_FILE`) defaults
  under the SCM to `%ProgramData%\CortexSim\agent.log`, rotating at 10 MiB with
  one old generation. Under the SCM, stderr was a null handle and every log
  line was lost. The startup line ends `mode=console` or `mode=service`. *The
  start path ran once, in the green `windows` job: the service was still
  Running 75 s after install, with no SCM error events. Reboot survival and
  the stop deadline have not been observed.*
- **Pull dispatch is keyed to the target's OS (BEACON-01 / CONT-01).** The
  orchestrator shipped `steps[]` verbatim and never read `Agent.os`, so a
  Windows beacon ran POSIX primaries through PowerShell. A step like
  `SIM-EDR-003` step-01 exited 0 after doing nothing, and every expectation
  then read `missed`. SimCore now dispatches the resolved command plus its
  `shell` (`powershell` | `cmd`) and `resolved_from`, and records a
  `!! PLATFORM VARIANT` marker on variant steps. A launch the target's OS
  cannot satisfy is refused with `409 PULL_TARGET_UNSATISFIABLE` before any
  Result row is seeded. A launch at an agent that is not enrolled gets
  `409 TARGET_AGENT_NOT_ENROLLED`. On the host, the beacon refuses a step whose
  `platforms` exclude Windows: it records `!! STEP NOT RUN:
  PLATFORM_UNSATISFIABLE`, scores the step `not_executed` (never `missed`)
  and continues. A run where no step can run exits 78, never 0.
- **The Windows executor kills by Job Object, never by a released pid
  (BEACON-04).** The old tree-kill swept by bare pid after `Wait` had released
  the handle, and could adopt a recycled pid while running as SYSTEM. Each
  command is now assigned to a Job Object right after `Start`, and abort calls
  `TerminateJobObject`. `KILL_ON_JOB_CLOSE` is not set, so a successful step's
  background children survive. `powershell.exe` and `cmd.exe` now
  launch by absolute `%SystemRoot%` path. *The green `windows` job observed
  the abort: the step's processes were gone 0.7 s after `POST /abort`, and an
  unrelated process survived.*
- **A Windows step's errors reach the run record as text, not XML.** Round 1
  passed `-OutputFormat Text` and said that made PowerShell write text; that
  was checked under pwsh 7.6 only. The first two `windows` CI runs showed that
  Windows PowerShell 5.1 ignores the flag for `-EncodedCommand` and writes
  stderr as CLIXML (`#< CLIXML` + `<Objs>`), so every step's stderr and every
  cmdlet error reached the run record and the POV report as an XML envelope.
  The beacon now silences progress records first and decodes the envelope
  after capture, leaving any shape it does not recognise unchanged. *Observed
  fixed in the green run: "stderr is plain text, not PowerShell CLIXML".*
- **A chained pull run cleans up what its steps left running (round 2).** On
  Linux and macOS a background helper started by one run survived and answered
  the next run, which still read SUCCESS. An abort or fail-fast caught between
  steps also left earlier helpers running. The chain's process group is now
  torn down when the chain ends, however it ended. On Windows a clean chain
  still stops nothing; one that did not finish cleanly sweeps its Job Object
  once. *The Windows half is not yet run on Windows.*
- **A beacon stop is prompt (round 2).** With SimCore down, a stopped beacon
  used to spend up to 62 s in its retry ladder, and one whose SimCore never
  answered took 28 s to stop. The ladder now yields to a stop (one final
  attempt, 3 s per POST), and registration and the idle poll are cancelled at
  once. Under the SCM this should keep a stop inside its 60 s deadline. *Not
  yet run on Windows.*
- **A failed boot registration is retried (INST-16, round 2).** If SimCore was
  still starting when the beacon's service came up, the agent row kept the
  installer's capability list for the life of the process, and every
  artifact task was refused with `AGENT_CANNOT_STAGE_ARTIFACTS`. The beacon now
  re-registers before each poll until accepted. That refusal's text now names
  both causes (an old build, or a beacon that has not re-registered since
  enrollment) and an OS-correct restart or reinstall.
- **Windows checks a step's tool and interpreter before running it
  (BEACON-13, round 2).** `required_binaries` now yields `TOOL_ABSENT` on
  Windows as on Linux. A missing or alias-only `requires_interpreters` entry
  blocks the step (`RUNTIME_DEPENDENCY_MISSING`) instead of running it
  untooled; a Microsoft Store stub under `…\WindowsApps` is not counted, nor
  is a Store-installed Python at that path. No current Windows step declares
  either field. *Not yet run on Windows.*
- **The beacon names its route to SimCore, and takes `--proxy` (BEACON-19,
  round 2).** `--proxy <http|https|socks5 URL | direct>` (env
  `CORTEXSIM_AGENT_PROXY`) applies to every request the beacon makes. The
  Windows system proxy (WinINET/WinHTTP, PAC, WPAD) is not read, and the
  beacon now says so at startup. The installer does not yet write `--proxy`
  into the service; a machine-level `CORTEXSIM_AGENT_PROXY` works meanwhile.
- **Smaller beacon fixes (round 2).** A staged artifact served from cache is
  re-hashed as it is written, and a mismatching cache entry is refetched; on
  Windows the cache lives next to the beacon (`%ProgramData%\CortexSim\cache`
  for the service). A PowerShell step longer than 11,361 characters is refused
  before launch as `COMMAND_LINE_TOO_LONG`, instead of failing inside
  CreateProcess. Every Windows step frame names the account and token it ran
  under. The exit-wrapper's two differences from `sh -c` are documented, with
  a stderr note when they decide a step's code: a bare `exit` ends a step 0,
  so write `exit $LASTEXITCODE`; an earlier failed native fails the step unless
  you reset `$global:LASTEXITCODE`. Every step frame now ends with a newline,
  so each `=== STEP` header starts a line; 4 of 5 frames used to be glued to
  the line before. *Run 43037077022 at `e434efa` ran these on Windows: every
  frame named `NT AUTHORITY\SYSTEM (elevated token)` and none was glued; the
  bare-exit, earlier-native and length-guard rows passed as native tests.*
- **Privileged identities on Windows need an elevated token (CONT-14/07).**
  `root`, `container-runtime` and `administrator` on a non-elevated token used to
  be recorded as honoured. They are now recorded as `!! IDENTITY NOT HONOURED: …
  requires an elevated token`. A Windows beacon advertises `elevated` only when
  `TOKEN_ELEVATION` says so. The service is registered as LocalSystem, so its
  steps should run as `NT AUTHORITY\SYSTEM` and XDR causality should show
  SYSTEM, not the admin who installed. That follows from the configuration; no
  run has asserted the account yet. Round 2's step frame line ("executed as
  <account>") will show it.
- **A lost control channel is no longer reported as an operator abort
  (BEACON-11).** The beacon stays fail-closed, but the record now reads
  `control channel lost — step terminated (NOT an operator abort)` with
  `reason=CONTROL_CHANNEL_LOST`. `/complete` and step `/output` retry transport
  errors and 5xx (1, 2, 4, 8, 16 s). A 409 counts as success; other 4xx stop
  at once.
- **A staged tool that endpoint protection quarantines reads
  `ARTIFACT_QUARANTINE_SUSPECTED` (BEACON-16).** It used to read
  `ARTIFACT_WRITE_FAILED` and tell the DC the missing detection was "NOT a gap"
  — prejudging what may be the customer's prevention working.
- **The Windows installer was rewritten, and a Windows uninstaller added.** The
  served `.ps1` carried about 90 non-ASCII bytes. Windows PowerShell 5.1 reads
  a BOM-less file as cp1252, where an em dash breaks the parse ("Missing
  closing '}'"). `Cs-Fail` called `exit 1`, which under `iwr | iex` closed the
  operator's own shell, and the token leaked into that shell. The installer is
  now ASCII (checked byte by byte) and returns instead of exiting. That made it
  safe under `iex` under pwsh 7.6; the green `windows` job then ran it through
  Windows PowerShell 5.1's `iwr | iex` (exit 0). It has not met AMSI or an XDR
  agent in prevent mode. It refuses a non-elevated session with
  `NOT_ELEVATED` before enrolling; with `CORTEXSIM_ALLOW_UNSUPERVISED=1` it
  installs a per-user beacon that dies at logoff instead
  (`DEGRADED_NO_SUPERVISOR`). It locks the install dir's ACL at creation and
  refuses one an admin does not own (`INSTALL_DIR_UNTRUSTED`). It reports
  `OK` only when the service is Running, still Running 35 s later (past the
  1053 window), and its `last_seen` has advanced past a baseline. One install
  has been timed: about 47 s on the hosted runner. Allow up to about 70 s,
  which is the installer's own waits added up (35 s hold plus up to 30 s
  check-in), not a measured range. `?os=windows&uninstall=1` now returns a PowerShell
  uninstaller; it used to return the bash one. Both uninstallers now also
  remove the beacon's cache and staging roots, which hold staged dual-use
  tools. *Both ran in each green `windows` job, elevated (install exit 0,
  uninstall "removed 2 of 2 item(s)"), most recently at `e434efa`. The non-elevated and per-user paths have
  not run on Windows.*
- **Windows installer and uninstaller, round 2.** The installer adds TLS 1.2
  only to an explicit protocol list and restores the session's setting when it
  ends; before, it left the operator's shell pinned to TLS 1.2. A failure
  before enrollment reports `agent_id: null`, not the baked default
  (`jumpbox-01` used to appear in install attempts for hosts that never
  enrolled). An elevated uninstall now looks in every profile for a per-user
  beacon. It reports what it finds there and ends `UNINSTALL_INCOMPLETE`, but
  never deletes inside another user's profile, because that user could swap
  the directory for a junction between check and delete. Remove a per-user
  beacon as the account that installed it, from a normal PowerShell. *Not yet
  run on Windows.*
- **The POSIX uninstaller reports what it found, not just what it removed
  (INST-13).** A non-root run against a root install printed "nothing to remove"
  and exited 0 while the beacon was still installed. It now refuses with
  `NEEDS_ROOT` (`… | sudo bash`) and fails `UNINSTALL_INCOMPLETE` when
  something it found survives.
- **The POSIX uninstaller handles `sudo` and mixed installs (round 2).** A
  `| sudo bash` run used to report "removed 1 of 1" while a user-mode beacon
  under the invoking user kept running. A root run now also removes user-mode
  installs under `SUDO_USER` and under `/home` and `/Users`, stopping that
  login's unit; it ends `UNINSTALL_INCOMPLETE`, never "done", when it cannot.
  A non-root run removes its own install and then fails `NEEDS_ROOT`, naming
  the root items and the real server in the re-run hint. Run the uninstaller
  as the user who installed (`… | bash`); the installer's closing line prints
  `| sudo bash` only after a root install, and so does the console. *Driven
  with stubbed `id`, `getent`, `stat`, `systemctl`, `runuser`, `pgrep` and
  `pkill` under bash 5.3 and 3.2; not run under a real sudo, systemd or
  launchd.*

### Fixed — run integrity

- **An alert is never credited to a run that never executed (OPS-01).** A run
  lost on a restart, refused at launch, still queued, or aborted before
  delivery could have alerts pasted into `/observations`. On cc26ddf that
  produced `newly_matched 1, MTTD 720 s, coverage 100 %`. Such runs now have
  their Results stamped `not_executed` (`TASK_LOST_ON_RESTART`,
  `LAUNCH_REFUSED`) and are never matched.
- **An unscoped reconcile match is `pending`, not `detected` (DOCS-03).** A push
  run has no target host, so its reconcile considered every alert in the tenant.
  On cc26ddf an alert raised on an unrelated laptop was credited to a push run.
  An unscoped `detect` match now stays `pending` / `UNSCOPED_MATCH`, with no
  MTTD. To scope a push run, name the endpoint:
  `POST /api/runs/{id}/reconcile?host=<name>` or `host` in the
  `/observations` body. The summary gains `host_source`
  (`operator` | `run_target` | null).
- **A run its beacon abandoned is reaped (OPS-03).** If a beacon's `/complete`
  was lost across a SimCore restart, the run read `running` forever, because
  the agent kept polling and the offline sweep never fired. Such a run is now
  terminalised with a `BEACON ABANDONED TASK` marker, which no longer guesses
  the cause. A fan-out is reaped only when every endpoint has let go of its
  partition. A late `/complete` on a terminal run is now
  `409 RUN_ALREADY_TERMINAL`, not a 200.
- **An oversized output chunk is cut, never refused.** The beacon sends one
  chunk per step. A step over 1 MiB (linpeas, pspy) lost its whole chunk,
  including `=== STEP` and `!! IDENTITY NOT HONOURED`, and read in the report
  as an ordinary miss. The chunk is now accepted and cut, keeping every frame
  and integrity line and adding `[OUTPUT CHUNK TRUNCATED: N of M bytes kept …]`.
- **The POV report states identity, variant and cleanup caveats.** These are:
  steps that ran under `IDENTITY NOT HONOURED`, steps that ran a platform
  variant or were not run, and a "Cleanup Not Run" section. Pull mode never
  executes `cleanup.commands`, and the report now names the commands left
  unrun.
- **A closed console tab releases its SSE subscription** within about 1 s,
  instead of at the next failed 15 s keepalive (OPS-10).
- **An abort keeps the host's evidence (RG-RUNS-01, round 2).** After an
  abort, the beacon's `/complete` met `409 RUN_ALREADY_TERMINAL` and nothing in
  it was kept, so an aborted step's `step_outcomes` row read termination, signal
  and exit code empty. The Linux lab run and the `windows` job both showed it.
  For an aborted pull run that was delivered, the steps' outcomes are now
  recorded and projected onto its Results as `not_reached` / `not_executed`,
  which are never credited. The reply is still 409.
- **A retry is counted once (RG-AGENT-03 / RG-AGENT-06, round 2).** A beacon
  that retried `/complete` after a lost reply was counted as the next endpoint,
  so one endpoint's lost reply could close a fan-out run while another was
  mid-step. A retried `/output` frame was stored twice, duplicating its
  `=== STEP` block and integrity markers in the POV report. The beacon now
  names its task on `/complete` and keys each frame with a `frame_id`, and
  SimCore answers a repeat `200 {duplicate: true}` without applying it. A body
  from an older beacon, which has neither, is handled as before.
- **More runs that never executed stay out of the evidence (round 2).** A task
  refused at the artifact-staging gate now stamps its steps `not_executed`
  (`AGENT_CANNOT_STAGE_ARTIFACTS`), so pasted alerts no longer match it. A pull
  run whose `delivered_at` was never written still counts as executed when its
  output carries a beacon step frame. An unscoped reconcile no longer
  un-credits a row it has no evidence against. Retracting an uncredited
  `UNSCOPED_MATCH` candidate returns it to `pending` instead of
  `409 NOT_AN_ATTESTATION`.
- **An agent with runs owed or in flight cannot be deleted (OPS-08, round
  2).** Deleting such an agent returned 200, left its run `running`, and handed
  the old task to the next beacon registered under that id.
  `DELETE /api/agents/{id}` now answers `409 AGENT_HAS_OPEN_RUNS` with the run
  ids. The console shows why beside the row and offers an Abort per run.
  Nothing is cancelled for you, and there is still no queued-task expiry.
- **A launch at an offline target is marked on the run (RG-RUNS-06, round 2).**
  The launch still queues, but `!! TARGET_AGENT_OFFLINE: …` is now written onto
  the run's output too, so another browser and the exported report see it;
  before, only the tab that launched did. A beacon restarted inside its
  long-poll window can no longer lose a task to its own abandoned poll. The
  container-agent gate's advisories (`AGENT_NO_RBAC_FOR_KUBECTL`,
  `AGENT_TOOL_ROSTER_UNKNOWN`) are written the same way, one `!! CODE: …` line
  each naming the agent; the launch response still carries them as dicts.
- **The pull refusal says how to fix its reason (round 2).** A
  `409 PULL_TARGET_UNSATISFIABLE` for a step whose Windows variant is refused
  as `MIXED_DIALECT` or `CMD_PERCENT_CONTEXT` used to say "declare
  `platform_variants.windows`", which it already had.
- **The smoke suite runs against a real, degraded SimCore (RG-GUARD-02 /
  RG-CI-01, round 2).** It skipped every test whenever `/api/health` read
  `degraded`, which is every deployment, and reported success on zero tests.
  It now accepts `degraded` only for the expected components
  (`api_auth` / `API_UNAUTHENTICATED`, `payload_shelf` /
  `SHELF_ARTIFACTS_MISSING` as a warning). With `CORTEXSIM_SMOKE_URL` set, an
  unreachable SimCore or zero passed tests fails. `scripts/smoke/lab-smoke.sh`
  went from 16 skipped to 16 passed against a host-run SimCore.

### Fixed — console

- **The console offers the PowerShell push bundle and shows refusals in place.**
  Push formats are `auto | bash | PowerShell (.ps1) | k8s` everywhere, defaulting
  to `auto` like the server. Before, a Windows bundle could not be requested
  and would have been saved as `.sh`. A `409 BUNDLE_TARGET_UNSATISFIABLE`,
  `PULL_TARGET_UNSATISFIABLE` or `TARGET_AGENT_NOT_ENROLLED` now renders its
  offending steps.
- **Launch preflight judges platform from the server's `bundle_targets`, not
  step labels (UI-08).** About 100 mislabelled steps had blocked the Linux
  beacon that runs a scenario and cleared the Windows beacon that cannot.
- **Run Detail flags steps that did not run as authored** (`IDENTITY NOT
  HONOURED`, `PLATFORM VARIANT`, `STEP NOT RUN: PLATFORM_UNSATISFIABLE`).
- **The deploy console tells the truth about Windows.** It always said "No
  Windows beacon ships yet", cited a code SimCore never emits, and showed an
  enrolled-but-never-polled agent as online. It now reads the shelf state, and
  polls install attempts every 5 s while open. A row that has only enrolled
  reads "enrolled — waiting for first beacon poll". The Windows one-liner
  carries the TLS 1.2 opt-in, and the uninstall lines are per-OS (the POSIX
  one became `| bash`, run as the installing user, in round 2).
- **The console polls run summaries** (`GET /api/runs?fields=summary`) instead
  of every run's whole transcript every 10 s, and it reads every page past
  500 runs.
- **Library and guided-flow errors render** instead of going to a no-op
  `onError`.
- **Launch preflight blocks what will not run (round 2).** A Linux-only
  scenario read PASS against a macOS beacon, because the server's verdict is
  POSIX-or-Windows; the Platform row now checks step labels within the POSIX
  family, and says SimCore itself does not refuse that launch. A target that
  has only enrolled, never polled, now blocks with "enrolled — waiting for
  first beacon poll", and an unknown status reads UNKNOWN, not PASS. The
  launch view re-reads agents every 10 s, so the block clears itself.
- **Launch warnings reach the run and its screenshot (round 2).** The launch
  response's `warnings[]` (such as `TARGET_AGENT_OFFLINE`) are listed under
  their codes in the Launch step, the run view and the Composer's followed-run
  pane. The Live tab's Screenshot now includes the "did not run as authored"
  lines.
- **Install feedback tells a warning from a stop (round 2).**
  `CHECKSUM_SKIPPED` and `DEGRADED_NO_SUPERVISOR` read as warnings, not "The
  installer stopped". `SERVICE_START_FAILED` is a warning from the
  Linux/macOS installer, which falls back to `nohup`, and a stop from the
  Windows installer. The verdict is announced once to screen readers.
- **Get started checks the address you type (round 2).** An unlisted DNS name
  warns that SimCore's Host guard will refuse it, with the allowlist and both
  fixes; the loopback hint prefers the LAN IP.
- **Deploy and removal commands match the scripts (round 2).** The POSIX
  removal command is `| bash`, run as the installing user, with `| sudo bash`
  shown for a root install only. A legacy agent id the installer would refuse
  is refused in the console too, with the reason. The header's agent chip says
  whether the beacon is polling, in words and shape as well as colour. Beacon
  availability is read per architecture, so an amd64-only shelf names the
  missing Apple-silicon beacon. The deploy modal (Components › Agents › Fleet) traps Tab, names what each
  Copy button copies, fits the viewport and scrolls inside itself; with
  Windows selected it had grown past the screen with nothing scrolling.
- **A Windows install path that does not pipe a download into `iex` (UI-17,
  round 2).** Beside the one-liner, the console offers links to the installer
  (with your SimCore address baked in) and the beacon, the digest, a
  `Get-FileHash` line that must print `True`, and a run line that sets
  `CORTEXSIM_TOKEN` and `CORTEXSIM_BIN`. *Not yet run on Windows*; whether
  XDR or Defender in prevent mode allows either path is untested.
- **Windows copy says what is proven (round 2).** Where PowerShell is chosen,
  the Launch step says no `.ps1` bundle has run on a Windows host yet, and
  names the `CORTEXSIM_ACCEPT_RESIDUE=1` / exit 2 rule. The token note now says
  the token is kept out of the URL and proxy logs, and that it is in shell
  history. Refusals that rendered "[object Object]", and a Library fault that
  raised two alerts, now render once and readably.
- **A refused agent delete shows why (round 2).** See `AGENT_HAS_OPEN_RUNS`
  above. Legacy (`?theme=legacy`) Install All waits for each build and reports
  what failed.

### Added — tenant evidence on request (in-container tenant reader, phase 1)

- `POST /api/runs/{run_id}/tenant-evidence` reads the tenant once, on an
  explicit operator action. It reads the run's issues (`issue/search`, Security
  domain), their cases (`case/search`) and the run host's endpoint record
  (`get_endpoint`), and stores the result as a snapshot in the new
  `tenant_evidence_reads` table (created at boot). `GET` returns the latest
  snapshot and makes **zero** outbound calls. Nothing polls the tenant.
- Issues are attributed to the run only through the issue's own host
  (`normalized_fields` XDM hostnames, any value in either field, compared with
  the matcher's short-host rule). An issue with no host is never credited.
- An id probe reads each matched alert back from `issue/search`. The snapshot
  then reports agreement with the matcher in two classes, `id` and
  `name_host_window`, counting distinct alerts. Two reads of one tenant
  agreeing is not tenant verification, and the snapshot says so.
- Read-only and display-only. The read never changes `Result` scoring, MTTD,
  outcome or `tc_verdict`, and never marks a credential verified.
- Counts that were not measured are `null`. A window cut by the request budget
  or by truncation reads `complete: false`. Refusals by CortexSim's own budget
  are worded as such, never as the tenant's.
- Tenant-authored text is an allowlisted projection under `tenant_text`, with
  controls and invisible or bidirectional characters stripped and each field
  capped at 200 characters. Every reply carries `data_notice`.
- Two tabs or a double click send one metered read. A minimum interval per
  tenant (default 60 s) refuses a repeat unless `?force=true`.
- Push-mode runs and runs with no executed step answer `NO_RUN_WINDOW` with no
  call.
- General per-tenant reads for the `xsiam_tenant` credential:
  `POST /api/xsiam/tenants/{name}/issues/search`, `…/cases/search` and
  `…/endpoints/get`. A reconcile-only `xsiam` credential answers `pending` /
  `NO_TENANT_CONFIGURED`.
- New settings: `CORTEXSIM_TENANT_EVIDENCE_MAX_PAGES` (5),
  `CORTEXSIM_TENANT_EVIDENCE_MIN_INTERVAL_SECONDS` (60) and
  `CORTEXSIM_TENANT_EVIDENCE_JOIN_TOLERANCE_SECONDS` (300).
- Strict envelopes on these reads: a lowercase `data` issue reply, or a reply
  with no `reply` wrapper, raises `XSIAM_PARSE_ERROR`
  (`XSIAM_ENVELOPE_UNRECOGNISED`) rather than reading as empty. Part B's by-id
  issue read now raises on a lowercase `data` reply where it used to return
  `{}`.
- The run read's endpoint record reads the strict `endpoints` envelope too, so a
  lowercase `data` reply is `XSIAM_PARSE_ERROR`, never `ENDPOINT_NOT_FOUND`. A
  strict reply with a row that is not an object raises rather than reading as
  fewer rows. A refused id probe records `attempted` and `refused_locally` on
  `agreement.id_space`, as a section does. `xsiam.xql.query` and a live
  `xsiam.operation.execute` carry a fixed `data_notice` for the tenant's raw
  values, and a parse error on any named MCP shape names keys and types, never a
  value. The run case ledger leaves `GET /api/runs/{id}/tenant-evidence`,
  `cortexsim.run.tenant_evidence.get` and `cortexsim.run.get` projected: a case's
  `resolve_comment` under `tenant_text`, a rejected `xdr_url` null with
  `xdr_url_withheld`. `run.get` then adds `data_notice`.

### Added — Results: tenant evidence panel

- Results › Report shows a Tenant evidence panel below the scorecard: the
  agreement figures with the two-reads statement, the issues credited to the
  run's host, the issues not credited, their cases, the run's case ledger and
  the endpoint record as of the read (with the ledger's ip-to-host bridge code).
- Display only, and read on request only. Opening the page sends one `GET` of
  the stored snapshot and nothing polls. The tenant is read only when the DC
  presses the panel's refresh button, which sends one metered read per press
  (a double click sends one) and never changes a score or verdict.
- Every degraded state is a sentence, never an empty table: not read, no
  credential, no run window, unscoped, zero attributed, truncated, truncation
  unknown, quota refused and a failed section with its code and remediation.
  Tenant-written names render as text, and a case link is followed only when it
  is https on `paloaltonetworks.com`.
- A quota refusal names whose budget refused the read: this instance's own (the
  tenant was not asked), the tenant's rate limit (HTTP 429), or, when the stored
  read does not say, neither. It also says what was counted: floors, complete
  issue rows, rows of unknown completeness, or nothing. A read inside the minimum
  interval says a new read is possible now rather than "in 0 s", and the case
  panel shows "link withheld" for a case link it will not follow, never the link's
  text.
- The case panel's read line no longer shows the integration's name, on Results
  and on Run detail, and a failed request renders a fixed sentence by its code,
  never the error's own text.
- Read back on a run that already has a verdict (Results, and the run page's
  case panel) asks first: it reads the tenant and may change the verdict, and the
  confirm says refreshing tenant evidence never does. Cancel posts nothing, and
  the first focus is on Cancel. The unread phase's Read back stays one click.
- Run detail shows the case panel once (the evidence tab no longer embeds a
  second one), a run that has not been read back shows its tenant evidence, and
  the scorecard's "XSIAM Stitch" tile is now "Analytics plane" (same figure).

### Changed — one per-tenant request budget; any 429 trips once

- Every SimCore request to a tenant now spends one per-tenant request bucket
  (`CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE`, default 60). That covers catalog
  REST reads, XQL starts and polls, healthcheck, the clock probe and every
  measurement-loop connector page. **REST catalog reads were unmetered before**
  and can now be refused with 429 `XSIAM_QUOTA_ERROR` and
  `retry_after_seconds`. Reconcile answers 502 `CONNECTOR_PULL_FAILED` with
  `detail.code` and `detail.refused_locally`.
- Any tenant 429 opens the shared breaker exactly once. This includes the
  connector path and the clock probe, which previously never tripped it.
- A refusal by CortexSim's own budget is a local refusal. It never trips the
  breaker, never counts as a query issued, never marks the credential
  unverified, and never becomes a verify or reconcile attempt. A run therefore
  cannot reach the attempt cap ("…without the detection appearing in the
  tenant") through requests that never left. Preflights report such a refusal
  as not probed (`PF_NOT_PROBED`, or `PF_RESPONSE_LAYER_UNREAD` for the response
  layer), never as an unreachable tenant. Sweep summaries report
  `refused_locally` separately from `quota_tripped`.

### Security — `/mcp` requires a bearer token; host actions refuse by default

- With `CORTEXSIM_MCP_HTTP` on, `/mcp` requires
  `Authorization: Bearer <CORTEXSIM_MCP_TOKEN>`, compared in constant time; a
  missing or wrong token gets 401 `MCP_UNAUTHORIZED`. Without a token, `/mcp`
  is not mounted and `/api/health` reports `MCP_TOKEN_MISSING`. Compose passes
  both variables through.
- The token only admits a client to `/mcp`. It is not a tenant credential, and
  `/api` stays unauthenticated. The MCP tenant tools hold no key: they reach the
  tenant through SimCore's routes, using the integration credential from tenant
  onboarding and the same per-tenant budget.
- `/mcp` is bearer-only, not loopback-only. Under Docker Desktop a host-loopback
  request reaches the container as the bridge gateway (`172.17.0.1`), so a
  peer-address check cannot tell the host from anything Docker forwards.
- MCP host actions (`cortexsim.run.launch`, `cortexsim.run.abort`) now refuse
  with `MCP_HOST_ACTION_DISABLED` plus a `curl` a human can run, unless
  `CORTEXSIM_MCP_ALLOW_HOST_ACTIONS=true`. The same rule applies over stdio and
  HTTP.
- API reference §9.2 now leads with the stdio launcher as the way to verify runs;
  the HTTP `/mcp` registration is optional and its example reads the token from
  an environment variable.

### Added — MCP tools for the tenant reader

- Five tools project the tenant-reader routes, taking the MCP server from 41
  to 46 tools (27 `cortexsim.*`, 19 `xsiam.*`): `xsiam.issue.search`,
  `xsiam.case.search` and `xsiam.endpoint.get` (`xsiam_tenant` credential only;
  a reconcile-only vault answers `NO_TENANT_CONFIGURED` with no call), and
  `cortexsim.run.tenant_evidence.get` (stored snapshot, zero tenant queries) and
  `.read` (one metered read; `queries_issued` is the snapshot's when the reply
  says `read: true`, else 0). They hold no tenant key; SimCore reads with the
  credential stored at tenant onboarding.
- Tenant rows are framed as data. These five tools, plus `xsiam.alert.list`,
  `xsiam.incident.list` and `xsiam.correlation_rule.list`, return
  `data.data_notice`, and their descriptions repeat it. Tenant text never
  enters `statement`, `error` or `remediation`.
- `xsiam.alert.list` and `xsiam.incident.list` carry a `LEGACY` line, in their
  descriptions and in `data.degradations`, naming PANW's deprecation map and
  pointing to `xsiam.issue.search` and `xsiam.case.search`. Their field layout
  is unchanged. `make check-mcp-boot` now fails below 46 tools.

### Added — six parity reads over the MCP server

- Six named tenant reads close the named-read rows of the `~/cortex-mcp` parity
  table (tenant-reader spec §8.1), taking the MCP server from 46 to 52 tools
  (27 `cortexsim.*`, 25 `xsiam.*`): `xsiam.tenant_info.get`,
  `xsiam.audit_log.list`, `xsiam.asset.list`, `xsiam.asset.get`,
  `xsiam.vulnerability.list` and `xsiam.assessment_result.list`. Each runs one
  catalog op through SimCore's executor route, on the per-tenant request budget,
  with the `xsiam_tenant` credential only; they hold no tenant key.
- Fields are allowlisted from PANW's public API pages
  (`docs/reference/xsiam-platform-apis/15-parity-reads.md`). Free text, and any
  name someone chose for an object (an asset's display name, an assessment
  profile's or a custom standard's name), sits under `tenant_text`; every string
  is neutralised and capped as the tenant reader's are, and every result carries
  `data.data_notice`. An unrecognised reply is `XSIAM_PARSE_ERROR`, never an
  empty list or object, and its detail names reply keys, never a tenant value.
  `asset.get` refuses an id that is not one path segment with
  `XSIAM_CONFIG_ERROR` before any tenant call. The list tools read the tenant
  once and page over that reply, so `offset` cannot reach past the tenant's
  first page; `truncated: true` says it holds more.
- Each tenant-evidence snapshot spends one more request, last, on
  `get_tenant_info` and records `tenant_version` with `tenant_version_code`. PANW
  documents licence fields only in that reply, so a reply as documented records
  `null` with `TENANT_VERSION_NOT_REPORTED` (a `version` the tenant does send is
  recorded, a number as its text); a failed or refused sub-read records
  `null` with its code. It never changes the snapshot's code, status or
  completeness. `make check-mcp-boot` now fails below 52 tools.

### Added — playbook and script ZIP reads

- `playbooks/get` and `scripts/get` answer a ZIP holding the object's YAML, and
  the executor read every reply as JSON, so `OP-PLAYBOOKS-GET` always failed with
  "non-JSON response" and `xsiam.playbook.list` could never return a playbook.
  Both ops (`OP-SCRIPTS-GET` is new, taking the catalog to 152 operations, 83 of
  them reads) are tagged `zip-reply`: the executor fetches the bytes with
  `XsiamClient.request_archive`, on the per-tenant request budget, asking for an
  identity body (any other `Content-Encoding` is refused unread) and refusing it
  past 4 MiB of raw bytes, and reads the archive in memory with stdlib `zipfile`,
  never extracting it. At most 50 members; a member over 256 KiB, or past 1 MiB
  in all, or neither stored nor deflated, is refused unread; one that inflates
  past its declared size is never read past it; non-UTF-8 is `binary: true`. An
  archive with no member reads as 0 members. A JSON body goes through the shared
  application-error test, and a body that is neither ZIP nor JSON is
  `XSIAM_PARSE_ERROR`, never an empty archive.
- Two MCP tools, `xsiam.playbook.get` and `xsiam.script.get` (by `id` or `name`),
  close the last two `~/cortex-mcp` parity rows, taking the server to 54 tools
  (27 `cortexsim.*`, 27 `xsiam.*`). Each member's name and YAML sit only under
  `tenant_text`, neutralised with line breaks kept, beside `data.data_notice`; they
  hold no tenant key.
- `xsiam.playbook.list` is registered, not backed: the public API has no playbook
  listing, so it answers `TOOL_NOT_BACKED` and names `xsiam.playbook.get`.
  `make check-mcp-boot` now fails below 54 tools.

### Fixed — Results shows each result's alert id and source

- `GET /api/results/{run_id}` rows carry `alert_id`, `alert_source` and
  `observation_count`, using the first recorded observation per result. The
  scorecard's Alert ID column had always shown `—`. It now shows the alert id,
  with a new Source column beside it.

### Added — stdio MCP launcher for Claude Desktop

- `scripts/mcp-stdio-docker.sh [--print]` execs `python -m mcp_server` inside
  the one running SimCore container (selected by compose labels, narrowed by
  `CORTEXSIM_COMPOSE_PROJECT`); zero or several matches exit 2. stdio runs
  inside the container, so it needs no bearer token.
- `CORTEXSIM_MCP_ALLOW_HOST_ACTIONS` is forwarded only when set in the
  launcher's environment.
- `docs/reference/api-and-agent-surface.md` §9.2 now shows the authenticated
  HTTP registration with a `headersHelper`, the Desktop stdio entry and token
  rotation; the old unauthenticated install line is gone.

### Fixed — a run where nothing executed reads `pending`, not "not scoreable"

- When every Result left the scoring denominator (`not_reached`, `not_executed` or
  soak-pending) and no KPI was measured, the test-case verdict was `not_applicable`
  with "0 detection(s) carry no measurable threshold". The run report then labelled
  it "NOT SCOREABLE" and the console greyed it out, as if the test case could not be
  scored. In fact nothing had been tested. Such a run now scores `pending`, with
  `tc_verdict_detail.reason` set to `not_executed` (or `soak_pending` when any row
  is still soaking, because that row can still be scored), and its
  `detail` names each `outcome_reason`. `not_applicable` keeps its one meaning: the
  rows that ran carry no measurable threshold.
- The run report labels such a run "PENDING — nothing executed" (or "detectors
  are still soaking") rather than "verification outstanding".
  `GET /api/uctc/test-cases/{id}` adds `runs.pending_reasons` and
  `runs.latest.reason`.
- The console shows the reason as one element everywhere a run verdict
  appears: the Techniques tab, the Topology Engine terminal and the UC / TC run
  rollup. It is a dashed "nothing executed" (or "detectors soaking") chip beside
  `pending`, with a sentence that says to re-run or wait. Before this it was a
  raw `not_executed` token. The Techniques tab now counts excluded rows as
  "never ran" instead of "not reached".
- The run lifecycle stops offering Read back for a run whose reason is
  `not_executed`. The Runs list reads "nothing executed", and the run's page
  and Report say why and offer Run. A read back could not change that verdict,
  because reconcile and verify skip rows that never ran.

### Added — HTTP collector backend (declaration, detections list, arrival check)

- `GET /api/eal/data-streams/{source_key}/detections` lists the detections behind a data source.
- `POST /api/collector-targets/{name}/arrival` checks that a collector's example records arrived.
- `PUT /api/collector-targets/{name}` takes a `declaration` field; refusals: `DECLARATION_SOURCE_UNKNOWN`, `NATIVE_DATASET_NOT_LAB_TENANT`, `TARGET_DATASET_DECLARATION_MISMATCH`.
- New setting `CORTEXSIM_DOOR_PROBE_TENANTS` (lab `.env` only; compose passes it through).
- `collector_targets` gains `declaration` and `last_arrival` columns, added at boot.
- The email emitter carries a `canary_token` on its records.

### Added — ingestion doors, phase 1 (read API, scenario-card chip, launch check)

- **Ingestion doors read API** (`GET /api/doors`, `/api/doors/readiness`,
  `/api/scenarios/{id}/doors`): for each scenario, which of its detections can
  fire given which data doors are open. Read-only, zero outbound calls.
- **Doors chip** on every scenario card in Scenarios: `READY`, `PARTIAL k/n`, `NEEDS DOORS`
  or `NOT CHECKED`, plus an opt-in **Runnable here** filter that is off by
  default, so nothing is hidden.
- **Data path** as a sixth launch preflight check. Advisory only: it warns, it
  never blocks, and a missing or unreadable answer reads `unknown`, not `pass`.
- Nothing is excluded from scoring yet, and no door can read `open` or `closed`
  until phase 2 writes door events, so a fresh SimCore reads `NOT CHECKED` on
  every chip. `tenant-verified` is still 0.
- Until door checks land in phase 2, the Data path check reads "doors not checked yet":
  a door nobody has checked is unknown, not closed. Only a door with a recorded
  `closed` state produces a "will not be scored" warning; collector-route detections
  read as on a route this console has not resolved.
- The XDR agent doors read `configured` only when the beacon is online and an XQL
  tenant credential exists: an online CortexSim beacon is not the Cortex XDR agent.

### Fixed — a Composer fork keeps its origin's push refusal

Follow-up to the composer-mode branch (PR #161). A draft forked from a corpus
scenario that declares `push_supported: false` (`SIM-MP-030/031/034`) was
stored push-capable, because the drafts API re-derived push support from the
draft's own POSIX-shaped commands — measured 2 of 3 on 2026-10-28 — and
`GET /api/scenarios/{id}/download` then emitted a bundle the origin refuses
with 409 `PUSH_NOT_SUPPORTED`.

- **`push_supported: false` on the draft body is a floor, never widened:** the
  derivation may narrow true to false, an explicit false wins, and a PUT that
  omits it or sends true keeps the refusal (`effective_push_support`, the one
  rule for POST and PUT).
- **New column `scenarios.push_supported_declared`** (nullable; the loader
  copies the YAML value for corpus rows; `_migrate_scenarios_columns` adds it
  on boot). It keeps a carried refusal apart from a merely derived `false`, so
  a hand-authored draft whose first save had nothing emittable still recovers
  once its steps are fixed. Prod: `ALTER TABLE scenarios ADD COLUMN
  push_supported_declared BOOLEAN`.
- **The Composer fork carries it:** `draftFromScenario` reads
  `push_supported_declared` (never the derived `push_supported`), and
  `draftToApi` sends `push_supported: false` only for a carried refusal. The
  fork now also carries `threat_report` / `threat_report_url`, which the POV
  report and the bundle header cite and the fork used to drop.
- **Guard:** the drafts-API tests fork SIM-MP-031 from a golden pair under
  `tests/fixtures/composer_fork/`, pinned from both sides — pytest refuses it
  unless its document still equals the live corpus, and
  `composerForkGolden.test.js` fails unless the real console fork still
  produces its body. Both new guards were observed red against the unfixed
  code.
- `indicators[]` is deliberately **not** carried: the Composer has no
  indicator editor, so `S-24` would wedge a draft the moment a referenced step
  is removed; nothing in the run path reads the column; and
  `coverage_report.py` walks the YAML corpus, so a draft cannot count toward a
  stitched chain either way.

### Fixed — SIM-EDR-025 expects the encryption detection the lab saw fire

`SIM-EDR-025` step-05 declared the agent's Behavioral Threat Protection
verdict on its mass encryption (`prevention-anti-ransomware-mass-encryption`,
module `btp`) as `outcome: detect`. On `pov-target-01` (2026-11-11, report
mode) openssl encrypting about 40 files under `/var/tmp` raised only the
built-in Low Analytics BIOC "Suspicious data encryption" and no behavioral
threat issue, so a working tenant scored a miss (CONT-04/10).

- **Step-05 now expects "Suspicious data encryption"** (`type: ABIOC`,
  `abioc-suspicious-data-encryption`, new on card `TTP-2026-0200`), keyed on
  the built-in alert name verbatim. It is Low, so it creates or joins no case:
  the scenario's text now says the chain yields signals, not an incident.
- **The BTP row stays on the card, undeclared.** What makes Linux BTP act on
  mass encryption is open (PM-PRV-07). Declaring it only for the block half of
  a paired run is not expressible: every row is seeded into every run, and a
  report-mode run scores even an `outcome: prevent` row as a detect.
- Counts: Prevention step-detections 3 → 2, ABIOC 163 → 164, catalog
  detection objects 2260 → 2261; step-detections unchanged at 1411.

### Security — how 1.1.1's fixes sit on top of the beta hardening

The six fixes released in [1.1.1] (below) are on this line too. Its tool-start
fix is the SEC-02 fix recorded under *Security* above, landed on both lines
independently; the merged code is 1.1.1's, which also holds `mitre_id` to ASCII
digits and `config_path` to a file strictly below `CORTEXSIM_BASE_DIR`. The
installer keeps the beta's narrower formats (agent id 1-64 characters, token
`cxs_` + 20-128) and adds 1.1.1's quoting and own-URL rule;
`SERVER_HOST_NOT_ALLOWED` is checked after `SERVER_NOT_THIS_SIMCORE`. For infra
bundles both layers stand: 1.1.1's request-model shapes answer `422` for a bad
`region` or `jumpbox_size` before the beta's SEC-17 gate, which still answers
`400 BAD_INFRA_PARAM` for `tags` and a zone-id `dc_ssh_cidr`, and the templates
escape every string behind it.

### Added — grouping-key corpus gate (`make check-grouping`)

- **`core/engine/grouping_keys.py`** works out, for each expected detection,
  the native XSIAM case-grouping key classes it populates (host, user, file,
  IP) and its severity, and reports per scenario: **G-01** (WARN) Medium+
  detections share no key, **G-02** (WARN) a stitched step carries only
  below-Medium detections, **G-03** (ERROR) a correlation rule maps the
  declared `stitching_key` to a custom destination, **G-04** (INFO)
  `GROUPING_KEY_UNKNOWN`, **G-05** (INFO) `BRIDGEABLE_IP_HOST`. The native
  field list is ported from the SOC Framework's
  `tools/correlation_rule_grouping_check.py` (MIT, notice kept).
- **`scripts/check_grouping_keys.py`** (`make check-grouping`, a step in CI's
  `refs` job, part of `make validate`) exits 1 only on G-03. The ground truth
  gains a `grouping` block.
- **Measured:** 199 scenarios, 1419 step-detections: G-01 0, G-02 2
  (SIM-EMAIL-003/004, informational BIOCs on a stitched step), G-03 0, G-04
  **1050**, G-05 0. G-04 is the size of the later authoring program: no card
  carries `alert_fields`, and only custom-BIOC severity is read from the
  corpus. The analysis is by key class, never by value, so it is advisory.
  `tenant-verified` is still 0. Those counts are `dev`'s. On the beta tree,
  where the Windows content declares eight fewer step-detections, the gate
  reads 199 scenarios, 1411 step-detections, G-04 **1042**, the other codes
  unchanged (`scripts/check_grouping_keys.py`, after the merge).

### Added — CortexSim alongside the SOC Framework: the run case ledger (Part B)

- **`Run.case_ledger`**, built during reconcile (`core/connectors/case_ledger.py`):
  did the run's chain stay in one native case, and if not, why. `merge_class` is
  `ONE_CASE | SPLIT_SEALED | SPLIT_NATIVE | LOW_ONLY_NO_CASE | NO_CASE_YET | PENDING`
  with per-case status, `closed_by` and sealed flag. A case the tenant does not
  return is `PENDING`; an unrecognised case status raises and reads
  `CASE_STATUS_UNRECOGNIZED`, never `open`. Every reconcile rebuilds it from the
  stored alerts plus the new matches; alerts with no case id yet store
  `NO_CASE_YET` with no tenant read, and a failed case read keeps the stored
  ledger. A split is classified per boundary between cases: any native boundary
  is `SPLIT_NATIVE`, only all-seal is `SPLIT_SEALED`. The ledger records its
  integration and tenant (a refresh refuses a ledger whose integration now points
  at another tenant, `CASE_LEDGER_TENANT_MISMATCH`, and a pair across tenants is
  `PENDING`), and its reads are counted in `queries_issued` (they do not charge
  the per-tenant XQL ledger).
- **`POST /api/runs/{id}/case-ledger/refresh`** re-reads the cases (read-only;
  refused inside the minimum interval unless `force=true`); the auto-reconcile loop refreshes recent ledgers (a ledger is terminal when every case in it is sealed, i.e. resolved and not auto-resolved, its merge is not `PENDING` and every case an alert names is in it, and is skipped; otherwise it can still change, so it keeps being refreshed) behind
  `CORTEXSIM_CASE_LEDGER_{WINDOW_HOURS,MAX_RUNS_PER_SWEEP,MIN_INTERVAL_MINUTES}`.
  `GET /api/runs/{id}/pair` gains a pair-level `merge`; the markdown report gains
  a `## Case` section and the json report `case_ledger`; the console run view
  gains a Case panel. An unbridged firewall-to-endpoint split carries a bridge
  diagnosis.
- **Disposition is context, not detection evidence.** It never feeds
  detected/missed, MTTD or a detection verdict. One exception: `SPLIT_SEALED`
  withholds a Cross-Source Correlation Rate FAIL as `pending` when the ledger
  classified every alert the KPI counted (a PASS is never touched).
- Runbook step 5 now describes reading the Case panel; `docs/reference/lab-runbook.md`
  says `not_checked` names six claims.
- **Honesty.** `tenant-verified` is still 0. The endpoint reply fields used by
  the bridge diagnosis come from the repo's transcription
  (`docs/reference/xsiam-platform-apis/05-endpoints-api.md`) and are unverified
  against a live tenant. **Owed:** one SIM-EDR-023 run on `pov-target-01`
  reading the ledger at T+0 (`ONE_CASE`, case `open`) and T+7 h
  (`socfw_auto_triage`, sealed); not executed.

### Added — CortexSim alongside the SOC Framework: the response layer, read-only (Part A)

- **A `response_layer` stage on `POST /api/connectors/xsiam_tenant/preflight`**
  detects whether the Palo-Cortex SOC Framework (`soc-optimization-unified`,
  pinned to 3.31.4) runs on the tenant and what its Auto Triage settings are:
  installed packs, list names, the two config lists and one XQL over the
  framework's execution dataset. All reads are read-only and counted in
  `queries_issued`. Pass `response_layer=false` to omit it. Codes
  `PF_RESPONSE_LAYER_ABSENT`, `PF_RESPONSE_LAYER_UNREAD` and
  `PF_RESPONSE_LAYER_SHAPE_UNRECOGNIZED`; an unrecognised shape raises and
  reads `UNKNOWN`, never defaults or `ABSENT`. States are `ABSENT`,
  `PRESENT_UNREAD` (installed, config or evidence not readable), `PRESENT` and
  `UNKNOWN`; `ABSENT` degrades preflight.
  Every framework string lives in `core/connectors/socfw.py`.
- **Each Auto Triage value says where it came from.** `auto_triage` carries
  `live_source` (`xql_evidence` / `mode_inferred` / `unknown`) and
  `window_source` / `threshold_source` (`tenant_config` / `shipped_default` /
  `unknown`). For the usual `pov`-mode config, "live" is inferred from the mode's
  shipped jobs map and 6 h / 40 are shipped defaults, and every surface says
  "(inferred from mode, not read)" / "(shipped default, not read)". A refused
  list read names its HTTP status (403: the key's role; 404/405: the
  unverified path).
- **Readiness shows it.** The result is stored on the integration
  (`response_layer`, `response_layer_read_at`; migration added) with the
  stage's own verdict (`stage: {status, code, detail}`), returned as a
  `response_layer` block by `GET /api/readiness` with a 24 h staleness flag, and
  rendered as a SOC Framework row on the console's Launch gate page. The row is
  green only for a `PRESENT` layer whose stored stage is `ok`. A failed store
  returns the report with `stored: false` instead of a 500, and re-registering
  the integration clears the stored read. `GET /api/health` lists
  `socfw_response_layer` under `not_checked`.
- **Launch advisories, never a refusal.** `POST /api/runs` adds
  `SOCFW_TRIAGE_MAY_SEAL` or `SOCFW_RESPONSE_LAYER_UNKNOWN` to `warnings` when
  Auto Triage may close a case during a paired run (`warn`) or an
  ABIOC/Analytics scenario (`info`), including when only execution evidence
  proves it live (`PRESENT_UNREAD`). The remediation names the three
  tenant-side options at the pinned framework (star the POV's cases;
  `!SOCFWConfig command=triage window_hours=<n> apply=yes`; switch the JOB off
  for the POV window with `!SOCFWConfig command=job job="Auto Triage V3"
  value=off apply=yes`, and back on after). It reads the stored probe only; CortexSim
  does not change the tenant. The console holds the launcher only on a warning
  that is not `level: "info"`, and shows each warning's level, tenant and
  remediation.
- **Runbook:** `docs/lab/2026-11-11-run-alongside-soc-framework.md` (spec §7).
- **Three read-only operations** (`packs/socfw.yml`; catalog now 151) and the
  operation schema accepts the `/xsoar/` gateway prefix. `OP-SOCFW-LIST-GET`
  reads only `SOCFWTenantConfig` and `SOCFWConfigModes`: a new
  `path_param_allowlist` makes the executor refuse any other list before a
  tenant call, because XSOAR lists can hold integration config and secrets.
- **Honesty.** `tenant-verified` is still 0: none of this has run against a live
  tenant. The list-content read path (`GET /xsoar/lists/download/{name}`) is
  **unverified against a live tenant**; a refusal on it reads as unread, not
  absent. Tenant disposition is context, not detection evidence.

### Added — two Linux persistence chains built to group into one case

- **SIM-EDR-023 and SIM-EDR-024**, with cards TTP-2026-0198/0199. SIM-EDR-023
  is a cron foothold that re-runs the WildFire test ELF. SIM-EDR-024 plants a
  sudo backdoor account and an SSH key, then a systemd timer. Both footholds
  re-run every 5 minutes, survive a reboot and remove themselves after 45
  minutes. Both target the standalone Linux XDR agent.
- **Grounded in the lab tenant's read-back.** The low built-in alerts the lab
  raised never formed a case: Low issues neither create nor join one. Each
  chain's Medium+ alerts are the agent's Local Analysis verdict plus BIOCs and
  a correlation rule the operator installs at Medium from
  `docs/lab/2026-11-11-persistence-grouping-lab-run.md`. Steps are 40 s apart
  because back-to-back steps lost telemetry in the lab. Every BIOC, XQL and
  correlation body parsed in the tenant (read-only); the ones that could match
  past lab activity matched it.
- **The corpus's first Prevention expectation** (`prevention-local-analysis-malware`).
  The scenario lint hook now accepts the Prevention type, as the loader
  already did.
- **Counts:** 195 scenarios, 193 cards, 1392 step-detections, Correlation
  share 10.1% (141). `tenant-verified` is still 0; nothing here has run
  against the tenant.

### Added — three deep Linux-endpoint scenarios from the Unit 42 backlog (#188)

Each runs for real (not SAFE-MODE) inside a staged decoy tree under
`/var/tmp`, with full `cleanup`; every beacon targets a `.invalid` host with a
2 s timeout.

- **SIM-EDR-025** (`TTP-2026-0200`): Akira (Howling Scorpius) Linux
  ransomware. `openssl` AES-256 encrypts a staged victim tree, adds the
  `.akira` extension and ransom note, reads `/etc/shadow` and inhibits
  recovery.
- **SIM-KOI-010** (`TTP-2026-0201`): npm supply-chain credential theft on a
  developer endpoint. A real `preinstall` hook runs from a slopsquatted package
  against a `.invalid` registry, and `node` harvests staged decoy secrets. The
  target needs `node` (`requires_interpreters: [node]`).
- **SIM-KOI-011** (`TTP-2026-0202`): a malicious OpenClaw/ClawHub marketplace
  skill. A padded README evades scanners, a base64 `curl|bash` dropper calls a
  `.invalid` canary, and an infostealer reads staged credentials. It reuses
  `cortex-malicious-agentic-pack`.

### Added — Unit 42 backlog: a triage, two scenarios and eight log-tampering presets

- **Triage of the 20 Unit 42 posts that no card or scenario cited**
  (`docs/reference/unit42-backlog-triage.md`). Each post was read in full and
  given a verdict: 16 are not scenario material (reports, briefs and reference
  pages with no reproducible behaviour), one was built, one was built as
  authored-only with a second post as one of its legs, and one is deferred
  until the lab has a Windows host.
- **SIM-KOI-012**, with card TTP-2026-0203: the Axios npm supply-chain
  compromise on a Linux developer endpoint, from the postinstall dropper to the
  Python remote access tool. Seven steps. Its network leg fails closed: every
  beacon goes to a `.invalid` name, and if a resolver ever answers for that
  name the payload sends nothing. Not yet run on the lab endpoint; the run plan
  is `docs/lab/2026-11-11-unit42-backlog-lab-run-plan.md`.
- **Eight `cloud_audit_emitter` presets for cloud log tampering** against AWS
  CloudTrail and Google Cloud Logging: `log_stop`, `log_destination_delete`,
  `log_router_delete`, `log_key_swap`, `log_object_overwrite`,
  `log_router_create`, `log_router_redirect` and `stream_bucket_delete`. They
  emit the victim account's audit records only, with the request parameters
  each call carries.
- **SIM-CDR-030**, with card TTP-2026-0550: cloud logging re-routed, poisoned,
  stopped and destroyed, with a log-bucket hijack leg. Eight steps emit 56
  audit records through those presets to an HTTP collector. It declares eight
  XQL rows and one correlation rule the operator installs. Authored only: its
  steps read the collector address from the environment, which a stock pull
  install does not pass to a step, so it has not been delivered to a tenant.
- **Dataset registry row `cortexsim_cloud_audit_raw`**, the dataset an XSIAM
  HTTP collector writes those records to. The registry now lists 33 datasets.
- **Counts:** 200 scenarios, 198 cards, 1420 step-detections, Correlation
  share 10.1% (143), from `docs/reference/ground-truth.md`.

### Added — a staged draft detection library, and a packager that clones nothing

- **`drafts/detection-library/`** holds 366 generated scenario and card pairs
  (310 Windows, 56 Linux) that are **not corpus content**. They carry
  provisional ids (`SIM-DRAFT-NNN`, `TTP-0000-NNNN`) and `status: draft`. SimCore
  does not load them, the console does not show them, no count includes them
  and the image does not ship them. A pair enters the corpus by promotion, one
  reviewed pair at a time; the directory's README gives the path.
- **Why they are staged.** The pairs came from a local-model pipeline whose
  packager cloned a live scenario and card and replaced only part of each. Every
  pair therefore carried that exemplar's source citation, UC/TC refs and cleanup
  line, and 320 cards carried one invented SHA-256 marked malicious. The pairs
  were rebuilt from the model's own output and the Cortex analytics alert each
  was written for: 306 cite that alert, 60 have no recorded source and say so,
  and 331 hash indicators with no source were dropped.
- **`INDEX.md` and `MANIFEST.json`** list what each draft's content still
  shows, generated by `scripts/detdev/manifest.py`. 78 drafts name a host a
  command would really reach, 75 have a step that is a placeholder, and the 310
  Windows drafts cannot be lab-run without a Windows host. No draft has been
  reviewed or run.
- **The pipeline (`scripts/detdev/`)** now builds a pair from the model fragment
  and its recorded source only. `package.py` previews by default and writes into
  the staging directory with `--apply`, never into `scenarios/` or
  `detection_scanner/ttps/`. `genfrag.py` takes the target platform and alert,
  reads the gateway key from the environment, and no longer asks the model for
  an invented hash. The new prompt has not been run against a model.

### Security — a scenario's scratch directory can no longer be taken over by another local user

- **What was wrong.** Twelve scenarios keep their artifacts in a fixed directory
  under `/tmp`, `/var/tmp` or `/dev/shm` and made it with `mkdir -p`. A local
  user who created that directory first owned it, because `mkdir -p` succeeds on
  an existing directory and a later `chmod` does not change the owner. Steps
  then wrote into it and ran files from it. Reproduced in a container: with the
  directory planted, the script SIM-EDR-023's root cron entry runs was swapped
  and ran as uid 0; racing the gap between `rm -rf` and `mkdir -p` in
  SIM-KOI-012, the step carried on in the other user's directory in 391 of 400
  runs.
- **The fix.** The step that creates the directory removes whatever is at the
  path and makes it with `mkdir` and no `-p`, so an existing entry stops the
  step. Every other step that uses it first checks, in one `stat` lookup, that
  it is a directory, not a symlink, with the mode the creating step gave it and
  owned by the running user (or by root, where a service account runs inside a
  root-made directory). After the fix the same race gave 0 of 400 runs in the
  other user's directory: 7 ran clean and 393 stopped.
- **Why one lookup.** A first version of the check used three tests (`-d`,
  `! -L`, `-O`). Where root follows another user's symlink
  (`fs.protected_symlinks` off), a user exchanging a directory and a symlink
  between those tests got past it in 73 of 300 passes under dash and 50 of 300
  under bash. The one-lookup form let none through in the same test.
- **Which scenarios.** A root step used the directory in SIM-CDR-002, -005,
  -018, SIM-EDR-023, -024, SIM-KOI-012 and SIM-MP-036. In SIM-MP-019, -024,
  -032, -035 and -037 the directory belongs to a service account, so the
  exposure there was that account, not root.
- **What an operator will notice.** A step run without its creating step now
  exits 1 with `sandbox … is missing or not ours`. Launching a scenario again
  removes the previous run's scratch directory first. Under an active attempt
  to plant the directory, the scenario stops instead of running.
- **Counts are unchanged.** The guard is a `[ … ]` test over `stat` and `id`, an
  `echo` and an `exit`: a probe, not an attack action. The lab-readiness and
  coverage classifiers now treat it as no signal, so a narration step that
  gained a guard is still counted as narration.
- **Not fixed here.** Root steps in 64 scenarios write a fixed-name file
  straight into `/tmp` or `/var/tmp` (120 steps), mostly EAL campaign files; on
  a kernel with `fs.protected_symlinks` and `fs.protected_regular` off, a
  planted file or link there has the same effect. Another 45 scenarios create a
  fixed directory and were not examined. The cards' illustrative payload text
  still shows `mkdir -p`.

### Added — collector targets: the tenant's ingestion doors, entered once per POV

- **A DC enters the tenant-side collectors once and every in-process consumer
  uses them.** Before this, a collector destination reached an emitter by four
  unconnected paths: typed into each campaign step (with the key stored in
  plaintext in the campaign spec), read from an unset `$COLLECTOR_URL` on the
  beacon, baked into an offline bundle, or authored on a Composer step. Targets
  now live at `/api/collector-targets` and on the **Tenant** page, persist across
  a SimCore restart, and keep each key in the encrypted vault, never in a
  response. Campaign create, the collector view, campaign preflight, launch, the
  offline bundle and the Composer EAL dispatch all resolve through one function,
  `eal_simulator/targets.py`.
- **Preflight per target, with the write opt-in.** Configured → reachable (TCP
  and TLS, no HTTP request) → accepted (one discard-safe record, only with
  `write_canary: true`). Verdicts: not configured, unreachable, reachable,
  refused, accepted. `accepted` is a 2xx, not proof of ingestion;
  `tenant_verified` stays 0.
- **A live launch refuses a missing or failing door.** 422
  `COLLECTOR_TARGET_NOT_CONFIGURED` / `COLLECTOR_TARGET_UNKNOWN`, 409
  `COLLECTOR_TARGET_FAILING`; an unproven or inline destination runs and says so
  in `degraded[]` and on the run (`collector_resolution`). Schema:
  `eal_campaign_runs.collector_resolution` (added by a `database.py` migration
  helper) and a new `collector_targets` table.
- **Not covered yet:** agent-run scenario steps (the EAL CLI on a beacon) and
  Broker VM targets, which wait on confirmation of the applets in use.

### Fixed — assertion probes no longer measure a page the API may have truncated

- **A full page of XQL results was scored as the tenant's whole answer.** The
  assertion runner fetches one page at a limit of 100 rows, and every probe
  counts, dedups, divides or takes the earliest of what comes back. With 300
  alerts in the population and 150 in the numerator, `xql_ratio` read
  "100 of 100 = 100 %", a pass against a 90 % bar. `xql_rows` and
  `xql_distinct` could never clear a bar above what one page shows, and
  `xql_latency` could report `OUTCOME_ABSENT` (a hard fail) when the outcome
  that followed the trigger sat past row 100. A page that comes back full now
  resolves `PROBE_RESULT_TRUNCATED` → `pending`, unless the reply's
  `number_of_results` equals the page. A larger `number_of_results` is not
  taken as the count while PM-XQL-03 asks what the field counts. A count
  pushed into XQL (`| comp count()`) is one row and is unaffected. Scenario
  Tier-2 verification (`xsiam_query_runner`) is unchanged. Guard: the
  truncated-page cases in `tests/engine/test_assertions.py`, which drive the
  production runner over a tenant stand-in that pages like the API; 4 of 7
  failed before the change.
- **A query the runner refused was reported as an unreachable tenant.** The
  probe failure classifier matched exception class names exactly, so a
  subclass fell through to `TENANT_UNREACHABLE`. An assertion whose query
  carries a `| target` write stage is refused before it is sent, yet its
  remediation told the DC to check network egress. It now reads
  `PROBE_QUERY_FAILED`; the verdict was and stays `pending`. Guard:
  `test_a_write_stage_the_runner_refuses_reads_as_a_query_fault_not_an_outage`,
  which failed before the change.
- **A spent CortexSim query budget was also reported as an unreachable
  tenant.** The per-tenant ledger around the assertion runner raises its own
  errors when its breaker is open or its daily cap
  (`CORTEXSIM_XSIAM_MAX_QUERIES_PER_DAY`) is spent; they are not XSIAM errors,
  so they fell through to `TENANT_UNREACHABLE`. They now read
  `PROBE_QUOTA_EXHAUSTED`, whose remediation names CortexSim's budget as well
  as the tenant's. Verdict unchanged (`pending`). Guard:
  `test_a_spent_query_budget_reads_as_quota_not_as_an_unreachable_tenant`
  (breaker open, daily cap spent), both failed before the change.

### Fixed — a run's Topology tab no longer says "scenario not loaded" while it is loading

- **The tab drew its board before the scenario request came back.** The
  results and the run record usually land first, and the scenario id is read
  from the run record, so for a moment the page said "scenario … not loaded",
  placed no step and listed every step as unplaced, about a request still in
  flight. Since lanes became signal paths (read from each step's command),
  that moment has no cells at all, and `ci.yml`'s `ui (build + vitest)` job
  went red on `dev` whenever the FAILED-run test read the board inside it
  (about 1 run in 8). The board now waits until the scenario request
  succeeds or fails; a failed request still places no step and says why.
  Guard: the "holds the board while the scenario request is in flight" case
  in `RunTopologyView.test.jsx`, which failed on every run before the change.

### Fixed — alert read-back reads the Issues API field names

- **An Analytics BIOC issue could never satisfy an ABIOC expectation.** The
  matcher maps an alert's source onto an engine family by substring, and the
  Issues API's `detection.method` code `ANALYTICS_BIOC` has an underscore
  where the "analytics bioc" rule expects a space, so it read as `analytics`
  and the known-but-different families refused the pair. `TRAPS` and
  `MAGNIFIER` named no family at all, so an agent prevention issue could
  credit a BIOC expectation on technique alone. The six codes a family exists
  for now map exactly. TRAPS = XDR Agent and MAGNIFIER = XDR Analytics are
  inferred from the order of the docs' two value lists, and the code says so.
- **The connector reads `detection.rule_id`, `detection.method`, `case_ids`
  and `_insert_time`,** the names the public Issues API documents. Each is
  read after the existing keys of its role. `case_ids` gives an incident id
  only when it lists exactly one case. `_insert_time` dates a record only when
  no other timestamp is present; the order MTTD uses is unchanged.
- **Preflight `alert_shape` no longer reports `PF_ALERT_SHAPE_PARTIAL` for an
  issue that carries `detection.rule_id`.** It reads values through the
  connector's own `alert_key`, and a new test fails if preflight credits a
  key the connector does not read. Guard:
  `tests/connectors/test_issues_api_fields.py` (15 failures before the change).
  Nothing here ran against a tenant: tenant-verified is still 0.

### Fixed — a collector target carries its Compression, and the preflight sends it

- **Both collector preflights sent an uncompressed body whatever the step was
  set to.** The campaign preflight and a collector target's "Send test record"
  go through `preflight_collector`, which never read `compress`. In the
  deathstar lab (PMA-2026-11-13), a collector set to gzip answered every
  uncompressed POST with 400 `gzip compression error (invalid header)` and
  ingested nothing. So against a gzip collector the preflight read `refused`
  for a door a `compress: true` campaign used, and against an uncompressed one
  it read `accepted` while a `compress: true` campaign got 400. The preflight
  now gzips when the target says so.
- **A collector target records its instance's Compression setting.**
  `PUT /api/collector-targets/{name}` takes `compress` (default `false`, which
  is what every target sent until now), reports it back, and applies it to its
  own test record and to every bound step whose plugin can gzip, overriding
  the step's inline value, the same way it already overrides URL and key. A
  change of `compress` discards the stored check. `email_emitter` has no gzip
  transport, so it is left alone. The startup migration adds the column, and
  existing targets read as uncompressed.
- **The remediation for `collector_rejected` names what the lab saw.** It used
  to point at the content type, which the collector ignores. It now names
  invalid JSON and a compression mismatch (400), a wrong path (404), and a
  record over 5 MiB decompressed or a POST over 10 MiB on the wire (413). The
  `collector_error` text keeps the documented 500 for a Log Format mismatch
  and says that the two mismatches the lab tried came back as 400. The
  offline bundle's copy says the same.

### Fixed — EAL collector emitters send the XSIAM API key bare

- **The 14 collector emitters defaulted `auth_scheme` to `"Bearer"`,** so a
  campaign that supplied only a token sent `Authorization: Bearer <key>`. The
  XSIAM HTTP log collector expects `Authorization: <api_key>` with no scheme
  (XSIAM 3.x, "Set up an HTTP log collector to receive logs") and refuses the
  prefixed form with a 401, which delivery accounting reported as
  `not_delivered`. The default is now empty; `Bearer` or `Splunk` remain
  settable for a forwarder that wants one. Campaigns that already set
  `auth_scheme: ""` are unchanged. Guard:
  `tests/eal_simulator/test_collector_auth_default.py`, which discovers every
  emitter from the plugin registry and failed on all 14 before the change.
- Remediation and help text no longer send a DC to a "Broker VM HTTP applet".
  The Broker VM applet list has no HTTP receiver; the HTTP log collector is a
  tenant endpoint at `/logs/v1/event`, and a 500 from it means the body does
  not match the collector's Log Format.

### Fixed — `email_emitter` can authenticate to an XSIAM HTTP log collector

- **The EMAIL plane's `email_emitter` had no auth parameters,** so pydantic
  dropped a supplied `auth_token` and every POST went out without an
  `Authorization` header. An XSIAM collector answers 401, reported as
  `collector_unauthorized`, which meant the EMAIL scenarios could not reach a
  real tenant at all. It now takes `auth_token` / `auth_header` /
  `auth_scheme` with the same defaults as the other collector emitters
  (`Authorization: <api_key>`, bare key).
- The three fields are declared once in `core/eal_simulator/collector_auth.py`
  (`Annotated` aliases plus `apply_collector_auth()`), and both
  `AnalyticsEmitterParams` and `EmailEmitterParams` use them. The email emitter
  does not inherit `AnalyticsEmitterParams`, because that would add `batch` /
  `compress` / `canary_token` fields it ignores and change its `burst_count`
  default from 4 to 8. Field order in the console's campaign form is unchanged.
- The token goes on the HTTP client only. Campaign API responses redact it to
  `auth_token: null` + `auth_token_configured: true`, and the offline bundle
  manifest names `CORTEXSIM_COLLECTOR_TOKEN_<STEP>` rather than carrying it;
  both were already keyed on the param name and now apply to email steps.
  Guards: `tests/eal_simulator/test_email_emitter_collector_auth.py` (header,
  no leak into audit events, POST bodies, per-request headers or the bundle),
  and `test_collector_auth_default.py`, whose discovery now keys on the
  `auth_token` field and requires `email_emitter` among 15 emitters. Four of
  the new tests and the discovery test failed before the change. The API
  redaction test for an email step passed both before and after; it is a
  regression guard.

### Changed — detector readiness judges the delivery route and soakless mode

- **A detector no longer reads armed on a route analytics never reads.**
  `POST /api/eal/campaigns/{id}/detector-readiness` derives each step's route
  from its `collector_url` and judges it before training time. The vendor says
  Analytics runs only on normalised logs, a custom HTTP collector dataset is
  normalised only once an XDM rule maps it, and the Cribl generic UUID gets no
  out-of-the-box analytics. Every streamer here delivers through a custom HTTP
  collector, so a campaign that used to read `armed` now reads `unknown`
  (`DETECTOR_ROUTE_REQUIRES_XDM`) until the DC attests the rule in
  `xdm_modeled_sources`. A generic-UUID route reads `not_armable`.
- **Soakless mode reaches the readiness gate.** `soakless_attested: true` (with
  `attested_by`) arms a detector whose route feeds analytics, with
  `armed_basis: "attested:soakless"`. Run outcomes already honoured the flag;
  the gate did not, so a soakless tenant was told its detectors were not armed.
  Soakless never overrides a route that cannot feed analytics.

### Added — ingestion routes in the Data Streams catalogue (LogSimv2 port, phase 1)

- `core/eal_simulator/ingestion_routes.py`: for every catalogue source, each
  documented route into the tenant, with pipe, tenant dataset, Cribl source UUID,
  vendor/product and whether out-of-the-box analytics run on it. Dataset and
  analytics claims carry separate evidence. Facts come from
  [LogSimv2](https://github.com/phreezone/LogSimv2), re-checked against the
  vendor's Cribl UUID and NGFW ingest pages and content packs. No LogSimv2 code
  is copied, since it ships no licence. A fact only LogSimv2 states is marked
  `reported`.
- `GET /api/eal/data-streams` rows gain `routes[]` and `dataset_check[]`, plus
  top-level `generic_routes[]` and `ingestion_only[]` (Proofpoint TAP, Infoblox,
  Apache, ESXi: real data, no published analytics, outside the coverage figure).
  Counted: 21 of 31 addressable sources have a documented route, and 12
  emitter-to-source pairs name a dataset the tenant never creates
  (`counts.emitter_dataset_mismatch`). Measured, not renamed.
- Design for the remaining four phases (transports, wire-format generators,
  threat catalog, kill chains and correlation exports):
  `docs/superpowers/specs/2026-11-10-logsim-v2-catalog-port-design.md`.

### Removed — `sources/MITRE-Turla-Carbon`: the jumpbox bootstrap no longer fetches compiled implants

- **The submodule is gone (10 → 9).** Nothing in the engine read it. No `.py`,
  `.yml`, `.sh`, `.json`, `Makefile` or `Dockerfile` referenced it, and the
  image never copied it. But `install.sh::init_submodules` runs
  `git submodule update --init --recursive`, so every jumpbox it bootstrapped
  received `files/turla/Resources/Binaries/binaries.zip`, a password-zipped
  archive of compiled Turla emulation implants (EPIC, Carbon, keylogger,
  Mimikatz, Snake, LightNeuron) plus PsExec. CI's `submodules: recursive`
  checkouts and the session-start hook pulled it too. No tier-2 adapter
  declared it as a source.
- **New guard: `scripts/check-installer-submodules.py`**, in `make
  check-adapters` and the CI `adapters` job. It runs `install.sh`'s real
  `init_submodules` with real git, in a sandbox that registers the tree's
  `.gitmodules` byte for byte, never touches the network, and points every URL
  at one local placeholder repo. It FAILs if a denylisted submodule (matched
  by URL or path) is registered without `update = none` or gets checked out,
  or if any checked-out submodule ships a `Binaries/*.zip`. Run against
  `origin/dev` before this change, it fails on R1 and R2. Run against a checkout
  that has the Turla submodule populated, it also fails R3 on the real
  `binaries.zip`.
  The pytest twin (`tests/installer/test_install_sh_submodule_denylist.py`)
  carries the negative controls. It skips inside the prod image, which has no
  git.
- **Existing clones keep the files.** `git pull` leaves a populated submodule
  directory in place. `sources/README.md` gives the removal commands.

### Changed — Composer and Runs: a step's lane is its signal path, not its plane

- **The door badge, the Lanes lens and the Runs topology place a step by what
  it does.** They used to derive the door from the step's plane (NDR read
  "Broker VM relay", CDR read "Cloud"), which names the product that observes
  the signal, not how the step produces it. Measured on the corpus, that put
  427 of 796 steps behind a door they are not launched through. The lane is now
  the step's **signal path**, read from its command and channel: ENDPOINT
  RUNTIME (the beacon runs it, 506 steps), LOG INJECTION (an EAL collector
  plugin posts records to a Broker VM or HTTP collector, 89), LIVE TRAFFIC (an
  EAL traffic generator, 49), CLOUD CONTROL PLANE (the command calls `aws`,
  `gcloud`, `gsutil`, `az`, `bq` or `kubectl`, 41) and NO ACTION (it only
  echoes, prints, sleeps or comments, 111). The Engine band holds tenant
  content, never a step.
- **The badge says where the step runs and its path:** `AGT`, `AGT → WIRE`,
  `SIM → COLLECTOR`, `AGT · no action`. A step whose path cannot be read reads
  `AGT → EAL ?` in the error tone with the reason in its title, and is never
  drawn behind a door it does not use.
- **Lanes are read-only.** A card dropped in another lane snaps back with the
  reason; change the command, or the channel in the Inspector. Lane overrides
  are gone (the drafts API never stored them).
- **Runs topology without its scenario places no step.** A path is read from the
  command and no Result row carries one, so each step is listed as unplaced
  with that reason; the results are still counted.
- **Loader lint S-28** (structural, not gated by `CORTEXSIM_STRICT_REFS`): a
  command that runs the EAL simulator must name registered plugins, all of one
  class. No corpus scenario trips it. `GET /api/eal/plugins` gains `delivery:
  collector | wire`, and `core/engine/signal_path.py` is now the one definition
  of the EAL plugin classes, pinned to the live registry.

### Added

- **Dataset requirement registry and scenario prerequisites.**
  `spec/dataset_requirements.yml` records, for each of the 32 datasets the 191
  detection cards read, what must exist on the Cortex side, what the source
  product must emit, and which EAL plugin can stand in for it. A card reads a
  dataset through `xql_queries[].dataset` or through a `dataset =` / `preset =`
  anchor in its BIOC, ABIOC or correlation logic; one walker reads both, and
  every dataset row says which kinds did (`read_by`). Three read-only routes
  serve it: `GET /api/prerequisites/datasets`,
  `GET /api/scenarios/{id}/prerequisites` (derived through the cards a scenario
  cites, with note codes instead of silent gaps, including `DETECTION_UNBOUND`
  for an expected detection with no `ttp_ref`) and
  `GET /api/scenarios/{id}/query-library` (the cards' queries as text for XQL
  Search, every other line a `//` comment whose syntax is not yet confirmed in
  a tenant; `?purpose=` selects hunt, investigation, validation or metric). An
  absent registry is reported as degraded in `/api/health`, never as "no
  requirements"; a malformed one answers a structured 500;
  `CORTEXSIM_STRICT_DATASETS=true` (default off) refuses boot on either, or on
  a card dataset with no entry. Every row is `from documentation`: **no row is
  confirmed in a tenant, `tenant-verified` is still 0**, and every Cortex-side
  object name is `null` until read in a tenant. These routes return the error
  object at the top level, unlike the older scenario routes. See
  `docs/reference/dataset-requirements.md`.

### Added — Kubernetes container agent (phase 1; authored, not tenant-verified)

- **The Linux beacon can run inside a pod and enroll with SimCore**, so cluster
  scenarios are dispatched from the console with the normal pull lifecycle (abort,
  run record, MTTD). Output: chained execution delivers a step's output when the
  step ends, not live mid-step (observed in the kind run; pre-existing, not
  container-specific). `GET /api/agents/manifest` serves the manifest
  (`profile` `baseline` | `cluster-privilege` | `lab-privileged`, `image`,
  `prepare`, `arch`, `ttl_seconds` 60..43200, consent keys, `authorized_by`); the
  pod reads a `cortexsim-enroll` Secret holding `server` and `token`, and an
  optional `artifact-token` that `start.sh` exports as `CORTEXSIM_ARTIFACT_TOKEN`
  for the payload shelf (environment only, never argv). Gated
  profiles are served on the GET with consent in the query string; the push path's
  `GATED_FORMAT_REQUIRES_POST` on `download?format=k8s` is unchanged. A Kubernetes
  tab in the deploy-agent dialog builds both commands. Operator reference:
  `docs/reference/k8s-container-agent.md`.
- **Launch gate for container agents.** `409 AGENT_POSTURE_INSUFFICIENT` and `409
  AGENT_TOOL_ABSENT` (`allow_missing_tools` overrides tools only) before dispatch,
  and warnings `AGENT_NO_RBAC_FOR_KUBECTL` / `AGENT_TOOL_ROSTER_UNKNOWN`, so a step
  the pod could never run is not scored as a detection Cortex missed. **It applies
  only to an agent the manifest deployed** (a non-empty `posture_profile`): a
  beacon in Docker, Podman or a foreign pod reports `container: true` with no
  profile, and for it (`""`, whitespace, `null`, no key) the gate is a strict
  no-op, byte-identical to a host agent. Every agent target of a composed run is
  gated on its own facts; refusals and warnings carry `agent_id`. The console's
  Launch view lists launch warnings, offers "Launch anyway" (re-sends with
  `allow_missing_tools: true`) on `AGENT_TOOL_ABSENT`, and shows a posture
  refusal's missing findings, rules and satisfying profile.
- **Honest degradations.** `ANCHOR NOT HONOURED` when the image's `sh` is
  busybox/toybox and there is no `bash` to carry a renamed `cgo_anchor`; `IDENTITY NOT HONOURED` (existing text) now also
  covers an absent account.
- New beacon flags: `--enroll-token-file`, `--state-dir`, `--posture-profile`,
  `--prepare-status-file`, `--expires-at`, `--subreaper`. `--expires-at` is
  checked before enrollment: a beacon started after it logs `LIFETIME_EXPIRED` and
  exits 0 without redeeming the token or registering.
- **Removal.** Deleting the namespace alone leaves the agent and reaper
  ClusterRole/Binding behind. Remove with `kubectl delete clusterrole,clusterrolebinding
  -l cortexsim.io/agent=true` and then `kubectl delete namespace cortexsim-agent`.
  Apply the manifest before creating the Secret. The manifest header states this
  order, plus the image and prepare mode.
- **Proof so far.** A first kind run (2026-11-10) found three product defects
  (re-apply rejected, pod with no interpreter could not enroll, `alpine` could not
  create `www-data`) and review found a profile-downgrade hole; all fixed. A final
  run on arm64 against `cd7601e` passed with zero defect lines: enrollment, abort,
  re-apply, downgrade, digest mismatch, the anchor rename (then via bash) and
  `ANCHOR NOT HONOURED` on alpine. That run predates the anchor-shell fix below;
  a re-run on the PR head `2762d22` (arm64) passed with zero defect lines and
  observed the debian rename via `sh` (dash), the alpine `bash` anchor and the
  60 s expiry (namespace and both `cortexsim-agent` objects plus the reaper's
  ClusterRole deleted within 150 s). It also found the reaper left its own
  ClusterRoleBinding `cortexsim-reaper-agent` behind. PR #176 now deletes that
  binding first, so the one residue expected at expiry is the inert, unbound
  ClusterRole `cortexsim-reaper-agent`, removed by the label delete; not observed
  in a cluster since the fix. The hard-linked busybox detection postdates the
  re-run, unexercised in a pod.
  `lab-privileged` has never been run; no amd64 node, no CI run on GitHub, console tab not
  browser-checked, no Cortex detection observed. `tenant-verified` is still 0.
- **Delivery.** PR #171 merged Tasks 1-11 at ef71c30; the fixes for re-apply,
  interpreter-less enrollment, alpine accounts and downgrade neutralisation are in
  the follow-up.

### Changed — behaviour that reaches beyond containers

- **Absent-account degradation now applies on every POSIX beacon** (VM and macOS
  too), not only inside a failing `runuser`. A step whose declared service account
  does not exist runs as the beacon's own account with a visible `IDENTITY NOT
  HONOURED` note and a count in the run summary, instead of failing inside the
  wrapper. An account is absent only when `user.Lookup` fails **and** `id -u --
  <name>` exits 1; an inconclusive probe, or a name outside the portable account
  shape (uppercase, `user@domain`, `DOMAIN\user`), is read as present so a
  directory-joined host behaves as before.
- **The chained anchor shell on host beacons is UNCHANGED: `sh`** with argv[0]
  renamed, as before. Only when `sh` resolves to busybox/toybox does the anchor
  become `bash` renamed (if bash is on `PATH`), else `sh` un-renamed with `ANCHOR
  NOT HONOURED`. A hard-linked busybox `sh` is detected by comparing it with `busybox`/`toybox` on `PATH`. An intermediate build of this branch had switched every beacon
  with bash on `PATH` to a bash anchor; that is reverted.
- **Every beacon sends `runtime_facts` on register and enroll**, including legacy
  VM beacons (`container: false`). Stored on a new nullable `agents.runtime_facts`
  JSON column, returned in the agent row.
- **The server accepts `null` for `capabilities` and `interpreters`** on register
  and enroll and reads it as `[]`; the beacon no longer sends null. A shipped
  beacon on a host with no detectable interpreter would otherwise 422.
- **Migration.** `agents.runtime_facts` is added automatically at boot. By hand:
  `ALTER TABLE agents ADD COLUMN runtime_facts JSON`.

### Added — Composer: the correlation rules a chain needs are part of its story

- **A step's chips read as its proof contract, not as tags on the TTP.**
  `SIM-AIACC-003` step-02 showed `XQL XQL XQL`. It now reads
  `expects  run · 3 queries`. Chips are grouped by who owns the work: `run` (XQL
  the DC runs to confirm telemetry landed), `deploy` (BIOC, IOC and correlation
  rules that must exist in the tenant) and `built-in` (ABIOC, Analytics,
  Prevention, which ship with Cortex). A type outside the seven renders as an
  error chip instead of taking a default class.
- **Correlation rules are on the canvas.** Each rule on a card the chain cites is
  a node on a *Cortex Engine · tenant content* rail below the chain, joined to
  the steps whose detections it composes. Its status is printed as words on two
  axes: what the card holds (`INTENT ONLY · nothing to import`) and what the
  scenario asks for (`scored by this run` or `not declared by this scenario ·
  not scored`). Selecting a rule opens a read-only Inspector view that shows the
  card's logic line as an authoring note, not XQL, and where each input
  resolved. Rule nodes are derived and never persisted, and the Run lens draws
  none.
- **Read-only export.** `GET /api/scenarios/{id}/tenant-content` and
  `POST /api/scenarios/drafts/tenant-content` (an unsaved draft; persists
  nothing) return the rules, the BIOCs and IOCs to deploy, the queries to run and
  the built-in detectors a scenario leans on. The Composer's *Tenant content*
  control shows the rule count and the worst status, toggles the rail and
  downloads the manifest. Neither route contacts a tenant: `write_path` is
  `false` and `tenant_verified` is `0` in every response.
- **What this does not do.** ***No correlation rule in the library is
  deployable.*** All of them are a name, an id and a logic line such as
  `XQL(a) OR XQL(b)`; the card schema has no place for a rule body, and a test
  pins the deployable count at zero. A rule a scenario declares is a scored
  expectation that nothing CortexSim ships can raise. This release makes that
  visible. It does not author rule content, check whether a rule exists in a
  tenant, or change door badges; those are phases 2 to 4 of
  `docs/superpowers/specs/2026-11-10-composer-correlation-story-design.md`.

### Added — read-only NGFW (PAN-OS) connection

- **Read-only NGFW (PAN-OS) connection and a Firewall page under Components.** A
  firewall is registered as integration kind `panos_ngfw`, by pasting an API key
  (`PUT /api/credentials/integrations`) or with `POST /api/ngfw/keygen`, which
  exchanges an admin login for a key. `POST /api/connectors/panos_ngfw/preflight`
  then answers, before the POV, whether this firewall will write and forward the
  records the NGFW-dataset detections need: 11 firewall-wide stages (management
  reachability and TLS, API key, system, clock skew, Logging Service and/or syslog
  forwarding, Enhanced Application Logging, log-forwarding profiles, decryption,
  registered tags) and 7 stages per enrolled agent (observed source, current
  sessions, security-policy match per probe destination, the matched rule's profiles
  and log settings, NAT, User-ID, registered tags). NAT between an agent and the
  firewall, and two agents behind one observed address, are **blocked**, because
  the 5-tuple stitch cannot form; an unknown agent-side address is degraded, and no
  agents is degraded `PN_NO_AGENTS`, never an empty list. Every stage reports, a
  shape the parser does not know blocks its own stage (`PN_SHAPE_UNRECOGNISED`)
  rather than reading as empty, and `queries_issued` counts the calls actually sent.
  `PUT /api/ngfw/{name}/sources/{agent_id}` sets the per-agent addresses, and
  `GET /api/ngfw` lists firewalls. The console destination is **Components ›
  Firewall** (`#/component-firewall`; `#/firewall` opens its Sources tab): connect, check, sources. Parsers are proven against PAN-OS 10.2.8
  fixtures only. A green firewall preflight proves firewall configuration, not
  ingestion or stitching: **`tenant-verified` is still 0**. Every `PN_` code is in
  `docs/reference/api-and-agent-surface.md` §1.17. New settings:
  `CORTEXSIM_PANOS_MAX_CALLS_PER_PREFLIGHT` (default 120) and
  `CORTEXSIM_PANOS_ALLOW_WRITE` (default false, consumed by nothing). New dependency:
  `tzdata==2026.6`, because `python:3.11-slim` ships no zone database and a
  firewall's configured zone (e.g. `US/Eastern`) would otherwise resolve unknown,
  leaving the clock and log-age stages at `PN_CLOCK_TZ_UNKNOWN` on every deployed
  SimCore. Security: the
  client cannot write to a firewall. It sends only allowlisted `show`/`test`
  commands, re-serialised in its own canonical form, and `config action=show` reads
  whose xpath fully matches an anchored allowlist; a refused request raises before
  anything is sent. The API key travels in the `X-PAN-KEY` header and is scrubbed
  from every report string. The keygen body is parsed in the handler because
  FastAPI's default 422 echoed the password even with `hide_input_in_errors`; the
  password is never logged, stored or returned. Known exposure: keygen and preflight
  are unauthenticated, like the rest of `/api/credentials`, so anyone who reaches
  SimCore can use keygen to probe which internal hosts and ports answer (four
  distinct 502 codes). No stored secret leaks through it.

### Fixed

- **The correlation export no longer invents a severity.**
  `detection_scanner/exports/correlation/*.json` stamped `severity: "high"` and
  `description: null` on every rule. No card authored either. The files now
  carry what the card holds plus `content_status`, `deploy: null` and
  `importable_rules: 0`, and say that nothing in them can be imported as-is.
  The exporter raises on a rule shape it does not know instead of guessing. 190
  files regenerated.

- **Catalog › TTP cards can be navigated by use case, test case and FY27
  subdomain.** The page listed 191 cards in authoring-id order with status,
  raw `TA00xx` tactic id and platform as the only handles, so a DC planning a
  POV in index terms had no way in. It now has **Group by** (subdomain / use
  case / test case — a collapsible outline with counts, each use-case and
  test-case row opening the UC / TC index) and **Sort by** (card id, name,
  subdomain, use case, test case, detections); every card face names the use
  case, subdomain and test case it is bound to; search matches `TC-…` / `UC-…`
  ids, use-case names, subdomains and scenario ids; the card detail lists every
  binding. `#/ttps?group=&sort=&include=` deep-links the choice and survives a
  trip into the index and back. A card has no UC/TC ref of its own, so the
  binding is joined through the scenarios that cite it
  (`ui/src/components/console/catalogBindings.js`) and is labelled what it is:
  **authored, not proof a detection fired**. By default a card sits under every
  test case its scenario evidences (163 of 191 repeat by test case, 93 by
  subdomain) and the bar prints how many repeat; "Primary test case only" is the
  near-partition. When the join cannot run — index snapshot absent, or a SimCore
  whose scenario list carries no card references — grouping is **disabled with
  the reason**; it never renders as "0 use cases" or as 191 uncited cards.
- **`GET /api/scenarios` keeps `expected_detections[].ttp_ref` in the summary
  projection** (+35.8 KB on 193 scenarios, +6.3 %). It was the only bulk source
  of the card → scenario link and the list dropped it, so the join above had
  nothing to read. A detection authored without the field still gains no key.

### Fixed — connectors

- **An `xsiam_tenant` credential now serves alert read-back everywhere the
  console said it did.** `GET /api/connectors` reported the `xsiam` connector
  `configured: true` on a tenant-kind credential (listed under
  `equivalent_integrations`) while `POST /api/runs/{id}/reconcile?connector=xsiam`,
  `POST /api/connectors/xsiam/preflight` and the auto-reconcile sweep each
  filtered on kind `xsiam` alone — so the tenant a DC had just been told was
  configured answered `NO_INTEGRATION` / `PF_NO_INTEGRATION` a click later, and
  the sweep was a silent no-op every cycle. One table
  (`core/connectors/service.py::credential_kinds_for`) now backs all four
  surfaces. A dedicated `xsiam` row still takes precedence; `?integration=`
  resolves a name across both kinds. The equivalence is **one-way** — kind
  `xsiam` still never serves XQL. The preflight report gains
  `credential: {name, kind}` naming the vault entry it exercised.
- **`?integration=<name>` that matches nothing is a 404 `INTEGRATION_NOT_FOUND`,
  not a fallback to the first row.** A reconcile asked for tenant A that
  quietly read tenant B would have credited B's alerts to the run; the error
  lists the names that are available and pulls nothing.
- **Preflight probes the other alerts API version before calling a tenant empty
  (#166).** Reconcile reads alerts on `/public_api/v1/…`, which the vendor lists
  as legacy. When v1 answered 200 with nothing while v2 held the alerts,
  reconcile "worked", matched nothing, and every seeded detection read
  `missed`. Preflight now probes the other version and names the one config
  value that fixes it.
- **`/api/health` counts the corpus apart from Composer drafts (#167).** The
  `scenario_catalog` count read 194 while the Library showed 193. `count` is now
  the active corpus, `drafts` and `by_status` are reported beside it, and
  drafts alone never rescue `SCENARIO_CATALOG_EMPTY`.

### Added

- **`windows` CI job on `windows-2022` — green at `d7f4f29`, then on the
  round-2 code at `e434efa`.** This is the only job that executes anything on
  Windows. It ran three times on 2026-11-10: red at `c852eac` (CLIXML on
  stderr, POSIX file modes asserted on Windows, 13 analyzer findings), red at
  `3a9dc82` (a cmdlet error still arrived as CLIXML), then green at `d7f4f29`
  in run 42933820773. On 2026-11-11 it was green at `fb0f646` and at `e434efa`
  (run 43037077022, the run the summary at the top of this section describes). One step per phase
  of `scripts/ci/windows-native-e2e.ps1`: `floor` (PSEdition Desktop 5.1) ·
  `go` (native vet + test) · `build` (the shipped recipe, PE32+ / SHA256SUMS /
  Authenticode) · `render` · `parse` (5.1 `ParseFile` + `ParseInput`) · `pssa` ·
  `boot` (SimCore on 127.0.0.1) · `install` (the console's token one-liner,
  service) · `pull` · `abort` · `push` · `uninstall` · `collect`. Evidence goes
  to the `windows-proof` artifact. The job has no `paths:` filter, no `needs:`
  and no `continue-on-error`. Now that it has been observed green, whether to
  make it a required check is decision DOCS-12; it is not required today. Three
  CI-only probe drafts bind `UCS-EDR-02` / `TC-EDR-03` so the launch gate
  accepts them; drafts are never UC/TC evidence. Its green does **not**
  cover: an https SimCore under 5.1 TLS defaults, Server Core, code pages
  other than cp1252, AppLocker/WDAC/Constrained Language Mode, a real Cortex
  XDR agent, reboot survival, the non-admin install path, or a shipped
  scenario. Round 2 adds three parse-control fixtures (two of which 5.1 must
  reject) and two narrow analyzer waivers that print `STALE WAIVER` when they
  match nothing. The controls have not run yet. PSScriptAnalyzer 1.25.0 was
  installed unpinned and can now be pinned.
- **`make check-powershell`, the PowerShell 5.1 floor gate.** It renders every
  PowerShell artifact this repo hands to Windows: 4 installers, the
  uninstaller, 13 bundles, 35 PowerShell steps, 5 beacon-wrapper samples and 3
  tracked `.ps1` files (61 files; 12 cmd-dialect steps are listed, not parsed).
  It gates each artifact the way 5.1 reads it. It parses under pwsh 7, then
  decodes the bytes as 5.1 does: BOM means UTF-8, otherwise Windows-1252. It
  also checks the UTF-8 read `iwr | iex` uses and a denylist of PS7-only
  constructs. An empty category fails the gate. **Necessary, not sufficient:**
  this is not 5.1 execution, nor a 5.1 parse; the `windows` job does those. It
  runs in the `refs` job with `CORTEXSIM_REQUIRE_POWERSHELL=1`, and since
  round 2 it is also the last step of `make validate` (so of `make ci`). The
  one opt-out is `CORTEXSIM_SKIP_POWERSHELL_GATE=1`, for the aggregates only,
  printed as a banner and again as their last line.
- **CI fails, not skips, when PowerShell is missing (CI-08).** `agent` and `refs`
  (ci.yml) and `python-tests`, `ttp-static` and `go-tests` (test.yml) assert
  `pwsh --version` and set `CORTEXSIM_REQUIRE_POWERSHELL=1`. `backend` also runs
  `make check-agent-shelf`, which now reads each served beacon's magic bytes:
  ELF / Mach-O machine, and MZ/PE `0x8664`. Before, a Linux ELF named `.exe`
  passed. It was written without an image run and first observed green in the
  backend job at `d7f4f29`; it was green again at `e434efa`.
- **CI and `make` changes (round 2).** `make validate` builds the image first,
  then runs every gate including `check-lab-ready` and `check-powershell`, so
  it needs docker, pwsh 7, Go and the Python requirements. The `refs` job runs
  four gates (PowerShell floor, strict refs, ground truth, lab readiness), and
  a red one no longer masks the others. The `adapters` job runs the payload
  shelf drift check with `PAYLOAD_ALLOW_UNPINNED=0`. The `agent` job
  cross-compiles all five shipped beacon targets (it cross-built two). The `backend`
  job runs `docker compose config -q` on `docker-compose.yml`. Each is
  pinned by a test in `tests/ci/`.
  `scripts/build-agent-dist.sh` replaces the beacon shelf from a staging
  directory, so a failed build leaves the old shelf whole and a dropped target
  no longer lingers. *These changes have not run on GitHub yet.*
- **`/api/health` checks more of what a DC will hit (round 2).**
  `agent_binaries` now wants all five shipped beacons; an amd64-only shelf read
  ok while Apple-silicon and arm64 Linux hosts got a 404 and fell back to a
  source build. `api_auth` says `CORTEXSIM_BIND` is a compose setting it
  cannot verify (`bind_verified: false`) instead of "published on 127.0.0.1
  only".
- **The adapter list carries each pack's exemption reason (round 2).**
  `unstaged_adapters[]` and `GET /api/tools/adapters` now give each pack's own
  `reason_code`, reason and `revisit` (48 tier-4 packs, 8 codes), plus
  `shelf_backed`. Before, all 48 shared one generic sentence. The console does
  not show these yet.
- **`bundle_targets` on `GET /api/scenarios/{id}` and every list row** —
  `["posix"|"windows"…]`, `[]` for `push_supported: false`, computed by the
  same function the download route gates on.
- **`GET /api/runs?fields=summary&limit=&offset=`**: `summary` drops `output`
  and adds `output_bytes` + `output_tail` (last 2 KB). The default limit is
  500, the max 5000; `total` is the number of runs, not the page length.
- **Release beacons.** The GitHub Release now attaches the five unsigned
  beacons plus `cortexsim-agent-SHA256SUMS` and `cortexsim-agent-buildinfo.txt`,
  and `manifest.json` gains an `agent` block (`signed: false`). `release.yml` no
  longer ships a `stage2-windows.zip` that contained one bash file; a stage2
  asset ships only when `installer/stage2/<os>/` exists, and today none does.
  *The new `release.yml` has never run. The release job checks only the
  `.exe`'s structure; nothing has executed it on Windows.*
- **XSIAM operation catalog: 32 field-observed ops harvested (#165).** The ops
  come from cortex-bot, whose author ran them against a live tenant. The
  catalog grows to 148 ops in 7 new packs (System, Cases and Issues, Identity
  Threat, Triage, War Room, Managed Threat Hunting, Vulnerability Management).
  Each sits behind CortexSim's gate. Writes and destructives stay
  preview-only: `CORTEXSIM_XSIAM_ALLOW_WRITE` / `_DESTRUCTIVE` default off.
  Four docs that called the catalog "read-only" now say what it is.
- **Runs list, Techniques and Topology rebuilt on live run records (#168).**
  Every Runs row now shows scenario, start time, agent (or "push bundle"),
  duration, `detected / expected` and status, without being opened. This uses
  the new `expected_detections` and `detected_count` aggregates on
  `GET /api/runs`. A `pending` row is never counted as a hit. Techniques
  places each step through its TTP card. A staged push run is no longer
  tailed, and its steps read as staged. A Composer canvas launch is followed
  in the dock.
- **Console polish (#167).** With exactly one registered tenant, the console
  binds it. `?open=` is the deep link on every surface, with the legacy keys
  kept as aliases. The Composer opens every library scenario (it had been cut
  to 24). The Library prints no count before its list arrives. Page titles
  now repeat the tab name.
- **MCP server** (`core/mcp_server/`, `mcp==1.30.0`): 41 tools — 25
  `cortexsim.*` and 16 `xsiam.*` (37 backed, 4 registered-but-unbacked that
  answer `TOOL_NOT_BACKED`; count from `python -m mcp_server --list-tools`).
  Two transports over one implementation: stdio (`python -m mcp_server`) and an
  opt-in Streamable HTTP mount at `/mcp` behind `CORTEXSIM_MCP_HTTP=1`
  (default off), plus the P-2 manifest (`--manifest`). Every result carries an
  evidence envelope; `queries_issued` and `tenant_verified` may be `null`,
  meaning *not known*, never zero, and `tenant_answered` says whether the
  tenant's reply came back, apart from the metered count (a results poll
  answers at a cost of 0). **Authored is not proven:** no MCP tool has
  run against a live tenant; `tenant-verified` is still 0. Reference:
  `docs/reference/api-and-agent-surface.md` §9.
- `GET /api/readiness`: the connector ladder and `tenant_verified`, counted in
  Python, with zero outbound calls (§1.16).
- `GET /api/runs` accepts `status` and `scenario_id` equality filters, and
  `total` counts the rows matching them. `dev` capped `limit` at 500; merged
  with the paging above it is 1..5000, default 500, because the console's
  Runs poll sends `limit=5000`.
- `queries_issued` (and a `ledger` snapshot) on the XSIAM executor, XQL and
  metrics routes, on `POST /api/runs/{id}/reconcile`, and on
  `POST /api/assertions/{id}/run`.
- `/api/health` reports an `mcp` component.
- `make check-mcp-boot`: runs `python -m mcp_server --list-tools` inside the
  built image and requires at least 41 tools; part of `make validate` and the
  backend CI job.

### Changed

- **`POST /api/runs/{id}/complete` on a terminal run is `409
  RUN_ALREADY_TERMINAL`** (was `200 {already_terminal: true}`). The beacon now
  retries `/complete`, so a 200 could not be told apart from "accepted".
- **`POST /api/runs/{id}/output` no longer returns 422 for a chunk over 1 MiB.**
  It accepts and cuts the chunk, and the reply carries `chunk_truncated` /
  `offered_bytes` / `accepted_bytes`.
- **`GET /api/agents/install`**: `?os=windows&mode=foreground` and
  `uninstall=1&mode=foreground` are `400 BAD_MODE`; `?os=windows&uninstall=1`
  returns `uninstall-cortexsim-agent.ps1`. The POSIX installer's
  `UNSUPPORTED_OS` fix line now gives the Windows pull one-liner instead of
  "use PUSH mode".
- **Task steps carry `shell`, `resolved_from` and `platform_variants`**, and a
  Windows beacon honours them. POSIX beacons still ignore platform labels.
- **API changes in round 2.** `DELETE /api/agents/{id}` can answer
  `409 AGENT_HAS_OPEN_RUNS`. `POST /api/runs/{id}/complete` accepts `task_id` /
  `agent_id`, and `POST /api/runs/{id}/output` accepts `frame_id` (max 128);
  a repeat of either answers `200 {duplicate: true}`. `GET /api/agents/install`
  answers `400 SERVER_HOST_NOT_ALLOWED` for a `?server=` the Host guard would
  refuse. `POST /api/infra/generate` answers `400 BAD_INFRA_PARAM`. The pull
  and push refusals gain reason `CMD_PERCENT_CONTEXT`. `GET /api/tools/adapters`
  rows gain `shelf_backed` and `artifact_exempt`. New beacon flag `--proxy`
  (env `CORTEXSIM_AGENT_PROXY`). The full contract is in
  `docs/reference/api-and-agent-surface.md` §11 (§9 there is the MCP server
  from `dev`; the beta's failure-code index is §10).
- **MTTD is measured launch-to-alert.** No `run.step` start frames are
  published and `Result.executed_at` is not re-stamped at step start, so MTTD
  includes queue wait and the runtime of earlier steps. The operator runbooks
  that said otherwise are corrected. Changing when the clock starts is an open
  decision (OPS-07).
- **Behaviour change you may veto: XQL needs `"xql"` in `capabilities`.** The
  XSIAM executor and XQL routes (`POST …/operations/{op_id}`, `POST …/xql`,
  `GET …/xql/{query_id}`, `GET …/metrics`) now refuse with 403
  `XSIAM_CAPABILITY_MISSING` for a credential whose `config.capabilities` does
  not list `"xql"`, and charge the per-tenant daily ledger (429
  `XSIAM_QUOTA_ERROR` when spent). **An existing credential without the field
  stops running XQL after upgrade**; add `"capabilities": ["xql"]` to its config.
- **The same gate now covers assertion runs.** `POST /api/assertions/{id}/run`
  with `dry_run: false` on a credential without `"xql"` resolves `pending` with
  `reason: xql_not_authorised` and `queries_issued: 0` (each check carries the
  new pending code `XQL_NOT_AUTHORISED`); it builds no client and runs no XQL.
  It used to run metered XQL for such a credential.
- **Behaviour change you may veto: an absent `capabilities` field means not
  granted, on preflight and on the verify runner.** Preflight used to authorise
  XQL when the field was absent, and so did the Tier-2 verify runner
  (`resolve_tenant_runner`). Both now share one rule
  (`integrations/xsiam/guards.py`). **After upgrade, `POST /api/runs/{id}/verify`
  and the `CORTEXSIM_AUTO_VERIFY` sweep resolve `pending` with
  `reason: xql_not_authorised` for every legacy credential without the field**,
  until `"xql"` is added to its `config.capabilities`.
- XQL start refuses a malformed body (`XQL_REQUEST_SHAPE_INVALID`, 400) and a
  write-stage query (`XQL_WRITE_STAGE_REFUSED`, 400) before any tenant call;
  `OP-XQL-GET-QUOTA` and the results stream are gated like the other XQL ops.
- The global `XsiamError` body gains an additive `upstream_status`;
  `{error, code, detail}` is unchanged.
- Reconcile counts the page a failed read died on as a query.
- Pins: `uvicorn[standard]` 0.29.0 to 0.31.1 and `httpx` 0.27.0 to 0.27.2
  (forced by the MCP SDK).

### Fixed — Tier-2 verify queries the run's own window (#230)

- **A late verify no longer reads as a miss, and an early one cannot pass on
  another run's records.** `POST /api/runs/{id}/verify` and the
  `CORTEXSIM_AUTO_VERIFY` sweep sent every `verification_xql` with a
  `relativeTime` counted back from *now* (3600 s by default), so verifying a
  run more than an hour after it ran found 0 rows and scored `fail`. A pull run
  is now queried over `{from, to}` in epoch ms: `from` is the run start minus
  300 s, minus the most negative `eal.params.event_time_offset_seconds` its
  steps declare; `to` is the run end plus `max(correlation window, 300 s)`,
  clamped to now (`core/connectors/verify_window.py`). The window is recorded
  at `tc_verdict_detail.verification.window`.
- A push run keeps the `relativeTime` lookback and records `window: null` with
  `window_reason: "push_execution_time_unknown"`: its `completed_at` is when
  the bundle was generated, and SimCore never learns when the bundle ran. An
  explicit `timeframe_seconds` is still a full override.
- The fix covers pull runs of 12 of the 14 scenarios that carry
  `verification_xql`. `SIM-EDR-018` step-04 and `SIM-EDR-025` filter on
  `current_time()` inside their own queries and keep the old behaviour.

### Fixed — scenario lint on `channel: eal` steps (#232, #233)

- **S-25 now scans `eal.params`.** The loader rule and its mirror in
  `.claude/hooks/lint-scenario.py` checked step commands, `platform_variants`
  and `cleanup` for published indicator values, but not a `channel: eal`
  step's `eal.params`, so a scenario could point `c2_http_beacon` at actor
  infrastructure and load, and SimCore would send that traffic from its own
  process. Both now walk `eal.params` recursively (dicts, lists, strings); an
  `ipv4` / `ipv6` / `domain` / `url` indicator value, or a url's host, found
  there is S-25 unless the plugin is a collector-POST plugin, where it is a
  record field value rather than a peer. The message names the dotted path
  (for example `eal.params.extra.hosts.0`).
- **A `channel: eal` step is never marker-only.** Such a step carries a
  descriptive no-op `command` because `command` is required, and
  `coverage_report.is_marker_only_step` and the hook's marker-only warning
  counted it as emitting nothing. Neither does now. The corpus count is
  unchanged on this tree (98 marker-only steps of 823).

### Added — a scenario run's collector steps can send live, behind consent and keys (#231)

- **Before:** a run's `channel: eal` steps were dispatched in SimCore's own
  process but hard-wired to dry-run, so a scenario could never deliver its
  collector records in the same run as its agent steps, a dry-run ledger entry
  carried `code: null`, and undelivered steps stayed in the scoring
  denominator, so a later Tier-2 verify could read `fail` for records that
  were never sent.
- **Now:** the in-process campaign runs live only when the launch carries
  `consent.simulation_authorized` **and** a non-blank `authorized_by` (new on
  `LaunchRequest`), every eal step is registered, validates and resolves to a
  keyed, non-failing collector target, and the steps' total sleep is ≤ 30 s.
  Otherwise the whole campaign stays dry-run (never half-live), and each step
  records why: `EAL_DRY_RUN_NO_CONSENT`, `_NOT_COLLECTOR`,
  `_TARGET_UNRESOLVED`, `_TARGET_NO_KEY`, `_TARGET_FAILING`, `_SLEEP_CAP`,
  `_HELD`, or `EAL_STEP_INVALID`.
- Keys are decrypted only on the sending path and never reach the ledger.
  `Result.executed_at` comes from each step's real start; a step that delivered
  no record seeds its results as `outcome: not_executed` with the code as the
  reason, so it leaves the denominator.
- The MCP launch tool does not take `authorized_by`, so only a human operator
  calling the REST launch can start a live send. `tenant-verified` is still 0.

### Added — idp and m365 emitters gain the Pink chain shapes (#234)

For IOC-driven SaaS chains such as Unit 42's Pink (CL-CRI-1147: vishing,
Entra passkey enrolment, M365 exfiltration, internal extortion). No scenario
in this release uses them yet.

- **`idp_signin_emulator`**: `source_ip` replaces every attacker-side IP (the
  `us-west` home baseline is unchanged); `event_time_offset_seconds` (≤ 0, at
  most a day) back-dates record body times; pattern
  `vishing_passkey_enrollment` (Microsoft only) emits an interactive sign-in
  with an approved number-matched push and, 90 s later from the same IP, a
  "User registered security info" passkey record. Under `canary_token` the
  passkey record's `initiatedBy.user` follows the canary principal, so one
  person never reads as two. `docs/reference/eal-plugin-catalog.md` now lists
  all nine presets.
- **`m365_activity_emitter`**: `source_ip`; `user_agents`, written to each
  record's `UserAgent` and never to SimCore's POST header; `storage_targets`
  (`onedrive` / `sharepoint`, default `[onedrive]`); `event_time_offset_seconds`;
  and pattern `internal_extortion_notice`, an Exchange `Send` to N internal
  recipients followed by N Teams `MessageSent` records, each 3 s after the
  Send. Per-record timestamps are kept.
- Shared helper `analytics_emitter.body_time(offset_seconds, *, plus_seconds)`.
  Record shapes are taken from the Microsoft Graph `signIn` / `directoryAudit`
  documentation and the Office 365 Management Activity API schema, not
  observed in a tenant. With none of the new params set, every existing record
  keeps its shape.

## [1.1.1] - 2026-11-12

**Security hotfix for v1.1.0: unauthenticated routes let the caller choose a
program SimCore runs, text that becomes code on the target or in a Terraform
or Kubernetes bundle, or the server a target enrols with.** Each now refuses
the input with a structured error.
No scenario, card, emitter or scoring change; every route below is still
unauthenticated, like the rest of SimCore.

### Security

- **Tool start refuses caller-supplied argv.** `POST /api/tools/{tool}/start`
  merged the body's `params` over the registry's `binary` / `source_path` /
  `port` before `run_template.format`, so `{"params": {"binary": "<any command
  line>"}}` ran that command as SimCore's user (root in the image). Caller
  params now fill only the placeholders a tool's `run_template` leaves open
  (`mitre_id` for signalbench, `config_path` for ackbarx), each validated and
  quoted into one argv token; anything else is `400 BAD_TOOL_PARAM` and nothing
  is spawned, and a missing required placeholder is that 400 rather than a 500.
  `POST /api/tools/{tool}/install` runs its build off the event loop, one at a
  time; a concurrent install gets `409 INSTALL_IN_PROGRESS`.
- **EAL campaigns no longer pick the executable.** `airs_prompt_attack` and
  `browser_attack_runner` spawned whatever path a step's `params.binary` named,
  and `POST /api/eal/campaigns` + `/launch` take the step params and the consent
  gates from the same caller, so any program ran as SimCore's user. Both
  plugins now run only their own CLI (`cortex-prompt-attacker`,
  `cortex-browser-attacker`) from SimCore's PATH, and both routes refuse a step
  that sets `params.binary` with `400 CALLER_EXECUTABLE_REFUSED`, including a
  campaign stored before this release.
- **The agent installer no longer runs its query string.** `GET
  /api/agents/install` pasted `id`, `server` and `token` raw into the bash and
  PowerShell scripts that are piped into `bash` / `iex`, so `?id=$(…)` ran on
  the target. Each is now checked against its real format (agent id
  `A-Za-z0-9._-`, token `cxs_…`, server `http(s)://host[:port][/path]`, the
  derived one included) and baked in as a single-quoted literal; a bad value is
  `400 BAD_AGENT_ID` / `BAD_TOKEN` / `BAD_SERVER` with no script.
- **The agent installer names only this SimCore.** `?server=` chose the host
  the script downloads the beacon and its digest from and the beacon takes
  tasks from, and any well-formed URL was accepted. It must now be this
  SimCore: the origin the install request arrived on, or an entry in the new
  `CORTEXSIM_PUBLIC_URLS` setting (comma-separated http(s) origins for LAN or
  NAT names; scheme and host case, default ports and trailing slashes are
  normalised). Any other host is `400 SERVER_NOT_THIS_SIMCORE` with no script.
  The console's one-liner sends no `?server=` and is unaffected; if you pass
  `?server=` with an address the request did not arrive on, list it in
  `CORTEXSIM_PUBLIC_URLS` (`docker-compose.yml` passes it through).
- **Served k8s bundles carry only this SimCore's URL.** `POST
  /api/scenarios/{id}/bundle` (`format: k8s`) wrote the body's `server` raw
  into the manifest's header comment and its preflight command, so a newline
  in it added YAML documents, such as a `Pod`, that the header and the consent
  record did not describe. `server` is now held to the installer's rule:
  `400 BAD_SERVER` / `SERVER_NOT_THIS_SIMCORE`, and no manifest.
- **Infra bundles no longer take Terraform from the request.** `POST
  /api/infra/generate` rendered `params.jumpbox_size` (unbounded) and `region`
  raw into Terraform string literals, so a quote and newlines could add a
  `data "external"` block that `terraform plan` runs on the DC's machine.
  `region` must now be an AWS region and `jumpbox_size` an AWS instance type
  (422 otherwise), and the templates write string values, `tags` included, only
  as escaped HCL literals. Bundles for valid input carry the same values, and
  the same bytes unless a tag holds `'`, `<`, `>`, `&` or non-ASCII text
  (which `tojson` wrote as `\uXXXX`).

## [1.1.0] - 2026-11-02

**The library grows from 177 to 193 scenarios, fifteen of them Unit 42
chains (thirteen counted as stitched) plus a macOS agentic-endpoint
takeover; the engine learns to say what a run proved: an outcome axis that
tells a prevention from a failure, Detection Accuracy and correlation rate
measured from the reconcile pull; and the console shows real state only,
with the Composer as a full-screen node graph.** Counted on this tree: 193
scenarios · 191 TTP cards · 27 EAL plugins · 1377 step-detections · 2123
catalog detection objects; lab-readiness GREEN 161 · YELLOW 27 · RED 5.

**`tenant-verified` is still 0.** Nothing in this release has executed
against a live Cortex tenant; the paired block-mode run (plan Task 18) and
the macOS lab run are both still owed. Authored is not proven.

### Added — SIM-KOI-009: GlassWorm poisoned IDE extension on a macOS developer endpoint

Spec `docs/superpowers/specs/2026-10-28-koi-aes-glassworm-sim-design.md`, plan
`docs/superpowers/plans/2026-10-28-koi-009-glassworm-aes.md`; source report:
Unit 42, "GlassWorm Goes Mac: Fresh Infrastructure, New Tricks".

- **`SIM-KOI-009`** (plane KOI, `UCS-SCA-01` / `TC-SCA-01`, `POV-SC-012`;
  T1195 Supply Chain Compromise plus seven follow-on techniques), six steps on
  a macOS developer endpoint: install a poisoned "Prettier Pro" extension
  through the IDE's own CLI, activation spawns a scripting-host child, decoy
  Keychain access, a LaunchAgent written and loaded from the extension host,
  multi-credential read and local staging, archive and send to a canary
  destination. Runbook rung `## Agentic endpoint (macOS)` in
  `docs/reference/lab-runbook.md` lists the prerequisites and the teardown.
- **Card `TTP-2026-0197`**: 3 BIOCs, 4 XQL queries, 1 ABIOC, 1 correlation
  rule (terminating on the staging query), 2 analytics-module references,
  8 IOCs; exported at `detection_scanner/exports/xql/TTP-2026-0197.xql`.
- **Seven step-detections, all endpoint-reachable, by design.** The scenario
  first declared nine. The two network-side queries (the archive send from
  IDE lineage, and the NGFW session to a non-marketplace host) need a sink the
  documented lab does not have, and a declared detection that cannot fire
  scores `missed`, never `not_reached`, so six of nine would have printed
  "detection accuracy 67 %, below target" for a tenant whose detections all
  worked. Both stay on the card as `purpose: hunt`;
  `tests/engine/test_sim_koi_009_shape.py` guards the count of seven.
- **Decoy pack component**
  `sources/cortex-malicious-agentic-pack/vscode/prettier-pro-canary/`:
  URI-triggered, decoy-only; `tests/eal_simulator/test_agentic_pack_prettier_pro_canary.py`
  keeps it benign with a negative control through the real check.
- **UC/TC:** crosswalk row for `SIM-KOI-009`; `TC-AEPS-09` is proposed for the
  v2.3 index (`docs/uc_tc_mapping/proposed-tc-v2.3.csv`), not a v2.2 binding.
- Corpus: 192 -> 193 scenarios, 190 -> 191 cards; golden push-bundle digests
  and ground truth regenerated (1377 step-detections, 2123 catalog detection
  objects). Never executed on a Mac: `Last executed: never`,
  **`tenant-verified` remains 0**.

### Added — Unit 42 recent-posts chains: SIM-MP-035/036/037

Spec `docs/superpowers/specs/2026-10-28-unit42-xsiam-recent-posts-research.md`.

- **Three multi-plane scenarios** with cards `TTP-2026-0194..0196` and UC/TC crosswalk rows: `SIM-MP-035` (BUGSTER AI-assisted WordPress credential attack, database exfiltration, C2), `SIM-MP-036` (CVE-2026-0257 post-exploitation: VPN session, RMM recon, vCenter/ESXi, offline DC credential dump), `SIM-MP-037` (PHP2Shell post-exploitation under a PHP worker; detections are the published Unit 42 hunting queries).
- **Emitter extensions, no plugin-count change (27):** `ngfw_threat_emitter` event pattern `vulnerability_inbound`; `globalprotect_emitter` event pattern `gateway_connected_without_auth`; `ngfw_eal_emitter` accepts `dst_ip` without `dst_domain` plus `dst_port`, `app`, `process_name` overrides; `llm_provider_egress` providers `deepseek` and `openrouter`.
- **Signal quality, measured:** 13 stitched chains (was 11), 236 high-quality signals of 1370 step-detections (17.2%, was 195/1320), marker-only 100/790 (12.7%); floors set to `hq_signal_floor` 236, `stitched_chain_floor` 13, `marker_only_ceiling` 0.1266. **`SIM-MP-037` is authored but not counted as a high-quality signal, ruling R2: its report publishes no indicator.**
- Network-leg correlation rows `CR-MP-NET-0035` and `CR-MP-NET-0036` (NGFW-only joins) on `SIM-MP-035` and `SIM-MP-036` lift Correlation to 10.1% (138/1370); `make coverage-strict` exits 0.
- Corpus: 189 -> 192 scenarios, 187 -> 190 cards; exports, golden push-bundle digests and ground truth regenerated. `tenant-verified` remains 0.

### Added — library signal expansion: 12 stitched, Unit 42-enrichable chains

Spec `docs/superpowers/specs/2026-10-27-library-signal-expansion-design.md`.

- **`indicators[]`** on scenarios (published hashes, IPs, domains as structured data) with lint/loader codes **S-22..S-27**; marker-only steps now warn.
- **`ngfw_threat_emitter`** EAL plugin (27 plugins total) and source/destination overrides on the analytics emitters.
- **Campaign-id lint** rejects `campaign_id` values the EAL campaign validator would refuse.
- **Coverage `signal_quality`** in `coverage_report.py` with gates `hq_signal_floor`, `stitched_chain_floor`, `marker_only_ceiling`, set to the measured values: 11 stitched chains, 195 high-quality signals (195/1320, 14.8%), marker-only 100/765 (13.1%, was 137/667).
- **12 Unit 42 stitched chains** `SIM-MP-023..034` with cards `TTP-2026-0182..0193`; UC/TC crosswalk bound. `SIM-MP-034` is identity-led and is not counted as a stitched chain.
- Corpus: 177 -> 189 scenarios, 175 -> 187 cards, 26 -> 27 EAL plugins; exports and ground truth regenerated. `tenant-verified` remains 0.
- `make coverage-strict` exits 0 (Correlation 10.1%, 133/1320).
- `push_supported: false` is now honoured by the bundle suite and the download/bundle routes (409 `PUSH_NOT_SUPPORTED`).

### Changed — the Composer is a full-screen node graph ("composer mode")

The Composer was a window inside a window: the nav rail, the Library tab strip
and five stacked rows of its own around a fixed 620px canvas. See
`docs/superpowers/specs/2026-10-21-composer-mode-design.md`.

- **Composer mode.** Opening the Composer keeps only the console header and its
  banners; everything below is the graph. The palette, inspector and
  workstream float over the canvas as drawers (both start closed). Leave with
  `← Exit` or Esc twice; unsaved edits ask first. Destinations can opt in with
  `immersive: true`; nothing about it is persisted.
- **Node graph.** The CGO trigger, every step and the Proof terminal are nodes,
  flowing left to right without overlap. Edges carry their relationship: select
  one to change its pivot or delete it. Drags preview live, the MiniMap works,
  palette items drag onto the canvas, and releasing a connection on empty
  canvas creates a step there. Delete re-attaches a removed step's children.
- **Off-chain steps are named.** A second root (which the loader rejects once
  any step declares a parent) now reads as a named validation problem, an
  off-chain chip and a warning edge, instead of "Chain valid" and a 422 at save.
  `+ Add step` extends the chain from the selected step or last leaf.

### Fixed — Composer: what Launch runs, what a delete or a fork keeps, what validation names

- **Launch could run something other than the canvas.** Editing a step in place
  (a pivot, a command, a re-parent) left the draft "unedited", so Launch ran the
  original corpus scenario. Any change to what executes now requires saving the
  draft first; moving a card does not.
- **Deleting a step left dangling causality references** its children pointed
  at; they now re-attach to the deleted step's parent. The card's `×`, the
  Delete key and a multi-select now share one delete, and the Delete key only
  acts with focus inside the graph.
- **A forked scenario lost its `external_tools`** (and its measurement
  contract, k8s teardown, interpreters and verification XQL). The gated tool
  still ran from its `{adapter:…}` command, but without the launch consent
  gate or the payload staging that `external_tools` drives. A fork now carries
  every field the draft contract accepts; all 193 loaded scenarios' fork
  bodies validate against `DraftScenarioSchema` (re-measured 2026-10-28 on the
  rebased tree; 176 when authored). Outside that contract and still dropped on
  a fork: `indicators[]` (14 scenarios) and `threat_report` /
  `threat_report_url`; `push_supported` / `pull_supported` are re-derived from
  the draft's own content by the drafts API (2 of the 3 rows authored
  `push_supported: false` (SIM-MP-031, SIM-MP-034) derive `true`, so a draft
  forked from them is offered a push bundle its origin refuses).
- **One `+ Add step` could reorder a branching chain.** The spine repair
  level-sorted a valid array, changing the execution order of steps the DC
  never touched. A valid array is now left alone and an invalid one moves only
  the steps it must.
- **Launch accepted a placeholder command.** A saved draft whose step still
  read `# configure the command for this step` passed the server's chain check and would have
  run a shell comment, reporting that step's detection as missed. Launch stays
  off until validation passes.
- **The Run lens could lend a verdict to an edited step.** A step that changed
  since the run now reads "not in run" instead of the old run's state.
- **A step placed before its parent** (which the loader rejects) is named in
  validation, and the card `↑`/`↓` refuses the move with the reason.

### Added — prevention efficacy, phase 1: the engine can tell "the agent stopped it" from "it failed"

Spec `docs/superpowers/specs/2026-10-26-prevention-detection-efficacy-design.md`,
plan `docs/superpowers/plans/2026-10-26-prevention-efficacy-phase1-engine-contract.md`,
wire contract `docs/reference/api-and-agent-surface.md` §1.3.1. **No live
paired run has executed yet (plan Task 18), so `tenant-verified` for
prevention is 0;** the agent version and the modules a profile can set to
Block are still owed, and the spec's §6 is provisional until they land.

- **An outcome axis on every Result** (`core/engine/outcome.py`): `prevented ·
  detected · missed · pending · not_reached · not_executed`. A block-mode run
  used to be one `failed` run whose Results read as detection misses. Now the
  step the agent killed reads `prevented`, the steps after it `not_reached`,
  and a tool that never arrived `not_executed`.
- **`Prevention` is the seventh `detection_type`**, the only one an expectation
  may carry `outcome: prevent` on; cards gain the matching object kind and the
  scorecard's type breakdown a Prevention bucket.
- **Two witnesses, or `pending`.** A prevention is confirmed only when the host
  records that the step's process was terminated (`step_outcomes`, from the
  beacon's `/complete` `steps[]`) **and** the tenant alert's action reads
  Prevented (the connector now reads `action`; the matcher gains a `prevention`
  family and per-type windows). One witness lands on `pending` with its
  reason, a lost beacon can only ever read `pending`, and a prevention is never
  hand-marked.
- **Beacon:** per-step termination cause (`exited · signaled · anchor_terminated
  · beacon_lost · timeout · aborted · not_run`), direct detection of the
  anchor's death with the orphaned step reaped, and tool presence on
  `/complete`. The orchestrator projects each task's required binaries and
  surfaces the egress warning on the run.
- **Paired runs and the canary.** `POST /api/runs` takes the launch context
  (`agent_profile_mode`, `bas_mode_attested`, `soakless_attested`,
  `attested_by`, `paired_with_run_id`, `canary_run_id`); `GET
  /api/runs/{id}/pair` returns the report and block runs with the
  per-detection delta and `severed_at_step`; `POST /api/runs/canary` links a
  block-mode launch to proof that the profile is real (409
  `CANARY_NOT_AVAILABLE` without `CORTEXSIM_CANARY_SCENARIO_LINUX`). New
  `runs`/`results` columns arrive through the boot-time `ADD COLUMN` helper;
  new tables `step_outcomes` and `observations`.
- **A run whose beacon dies terminalises** (gated on the configured liveness
  threshold), blaming the in-flight step and holding its prevent expectations
  `pending`.
- **Surfaces:** the causality graph and storyline draw the severed chain,
  `not_reached` and `prevented`; the report carries outcome icons, chain
  integrity, the prevention delta and attestation lines, and excludes
  prevented rows from MTTD; every console surface renders the outcome axis,
  the launch form the attested context, Readiness the new rungs. A manual
  validation is a visible, bounded attestation that never overwrites an
  engine miss.
- **One denominator rule** for `/api/results` coverage, the MITRE heatmap and
  the TTP badge: `pending` is never a coverage hit, excluded rows leave the
  denominator, a row whose step never ran is never certified or scored, and a
  silent detect row is written `missed` only once its own window closes.
- **Tier-D end to end:** a kill injector produces the three host witnesses and
  the classifier carries them into the verdict.

### Changed — the console says what to do next, in fewer colours, on real state only

From DC feedback on a live install: overwhelming, no clear next task, colour
without purpose, and numbers that were not this instance's. See
`docs/design/DESIGN-SYNC.md` § Minimal pass.

- **Get started is the home page.** A five-step checklist — install an agent,
  connect the Cortex tenant, data collector (optional), Broker VM (optional),
  first simulation — read from SimCore (`/api/agents`, registered tenants,
  `/api/runs`). The agent step mints a one-use enrollment token and shows the
  install command, warns when the address is loopback or the server has no
  prebuilt beacons, and polls until the agent checks in. Collector and Broker
  VM have no SimCore API, so they are confirmed by the DC and labelled as
  such. A failed first run is not "done": the step says it stopped early and
  links to the run. The suggested first run opens the guided launch with the
  active online agent preselected as the target.
- **Task rail.** Seventeen phase-grouped destinations became five tasks
  (Get started · Simulate · Runs · Results · Catalog) plus Agents and Tenant;
  other pages are tabs inside a task. Every old route still resolves. The
  footer flow bar is removed; the tour no longer auto-starts.
- **Colour rule.** Orange is the single primary action; green means healthy /
  done / detected; amber and red are real warnings and failures; everything
  else is neutral. Fixes legacy aliases that painted healthy agents, detected
  chips and "ok" dots orange, and a dark `--crit` that read as brand orange.
- **Seed data removed from live surfaces.** The prototype's catalogs rendered
  as facts (tenant "Acme Financial" with 7/9 checks passing, a `jumpbox-lin-01`
  launch-gate PASS, 12 components, a fixed coverage matrix, seed workflows in
  the composer). The Tenant page now shows the real registry; Components, CLI
  items, the scoping wizard and Tenant validation are retired behind
  redirects; `povdata/` is lane/plane vocabulary only.

### Fixed — `install.sh` reported success when it had installed nothing

- **Outside a checkout, `install.sh` ended "Installation complete" with exit
  0.** It builds from the files next to it. Piped (`curl … | bash`) or run as
  a lone downloaded copy, it installed the Go/Rust/Node toolchain, skipped
  every build step and `docker compose up` with a warning, and still printed
  the success banner with nothing listening on :8888. It now refuses first,
  before the OS check or any `sudo`, with `INSTALL_NOT_A_CHECKOUT` (exit 2),
  the missing files, and the `git clone` / `scripts/dev-up.sh` fix. The
  in-tree skips (`.gitmodules`, `agent/`, `ui/`, `ui/dist`,
  `docker-compose.yml`) are now hard failures; absent submodule tools still
  warn. The published v1.0.0–v1.0.2 `install.sh` assets predate this guard.
  `tests/installer/test_install_sh_checkout_guard.py` runs the real script
  with every host-changing command stubbed.

### Fixed — the clock preflight measured event age, not clock skew

- `_probe_clock` read one sampled `xdr_data` event's `_time` and reported
  `|event_time - now|` as skew. A perfectly NTP-synced tenant read **836 s**
  because its newest event was about 14 minutes old, and a tenant with no
  events read unmeasured. True skew now comes from the tenant's own clock in
  the HTTP `Date` header (`XsiamClient.server_time`, present on every response
  including a 403, no XQL, no dependence on ingested data), with SimCore's
  local time captured around the read and compared at the round-trip
  midpoint rather than at preflight start, which had added the XQL-scope poll
  (~17 s) to the reading. Live: 17.2 s -> 0.3 s. A malformed `Date` is
  unmeasured; a failed probe is counted. Green under `TZ=UTC` and
  `America/Los_Angeles`.

### Fixed — gates that could pass on an empty or unreadable input now fail closed

From the infra-reliability pass (2026-10-04):

- The adapter-wiring lint fails on an empty or unreadable corpus and its lost
  fail-closed guard is restored; `coverage_report.py --strict` fails on an
  unreadable corpus file; the export-determinism gate fails on a stale or
  untracked export; the tier-2 adapter preflight fails on an empty submodule
  directory; Tier-D classifies a dash "not found" as ENVIRONMENT, not TTP.
- The rust-builder stage is pinned to linux/amd64 so arm64 build hosts
  produce the same static binaries; the payload-shelf CLI defaults its base
  to the repo root; the scenario lint hook agrees with the loader on planes
  and the CGO spine.
- Console: a failed agent or tenant list fetch keeps the operator's choice;
  the Tenant seed is never presented as the bound tenant's results; a failed
  scenario-list fetch reads as a failure in the Library; Launch runs the
  scenario the operator picked last, not the slowest response; SimCore's
  top-level 422 envelope is shown instead of "HTTP 422".

### Fixed — a finished run's verdict could change after the fact

- **A late or duplicate `/complete` reopened a finished run.** The only guard
  on `complete_run`'s UPDATE was `status != "aborted"`. A second `/complete`
  with exit 1 on a run that had completed with exit 0 flipped it `complete` →
  `failed`, re-stamped `completed_at`, appended a second summary, re-published
  the terminal SSE frame and re-scored it; a push-mode `staged` run flipped to
  `complete` the same way. The UPDATE now uses the in-flight guard `/abort`
  already uses, and a callback on a finished run is a 200 no-op with
  `already_terminal: true`. A sticky-failed fan-out still owed endpoints is in
  flight and keeps accepting them. The beacon does not retry `/complete`, so
  the trigger was a replay, a proxy or another caller.
- **An `AGENT_CANNOT_STAGE_ARTIFACTS` refusal overwrote writes that landed
  while it ran.** It read the run, checked it was in flight, then wrote back
  `failed`, `completed_at` and `output` computed from that read. A fan-out's
  other endpoints keep calling back meanwhile: their `/output` chunk or
  `/complete` summary was dropped, an operator abort was turned into `failed`
  with its marker gone, and two beacons refused at once published two terminal
  frames. The refusal is now one guarded UPDATE that appends its marker in
  SQL, the same in-flight guard `/complete` and `/abort` use, and only the
  request that failed the run publishes the frame. The beacon still gets the
  409.

### Fixed — the alert read-back path could not be trusted on a shared, busy tenant

Sprint 1 of `docs/uc_tc_mapping/IMPL-PLAN-xsiam-readback-and-index-honesty.md`,
from the findings in `docs/uc_tc_mapping/audit-2026-09-20.md`. **No new write
path to Cortex; `tenant-verified` is still 0.**

- **One page of 100 alerts, silently.** `XsiamConnector.pull` asked for a single
  `search_to: 100` page sorted ascending over the whole run window, so on any
  tenant raising more than 100 alerts in that window the run's own (newest)
  alerts were the ones dropped, and `reply.total_count` was never read. The
  pull now pages (`search_from`/`search_to`, ≤100/page, `max_pages` cap,
  default 20), reads `total_count`/`result_count` (including Cortex's capped
  `"9,999+"` string), and reports `truncated` / `truncation` in
  `PullResult.detail`; `reconcile_run` carries it into the summary with a
  warning that names coverage a **floor**. A non-200 on page N fails the whole
  pull rather than returning page 1 as the tenant's total.
- **No host scoping.** `ObservedAlert.host` was captured and never used, so an
  earlier alert from another endpoint won MTTD via the technique-base key.
  `reconcile_run` resolves `Run.target → Agent.hostname` and
  `matcher.scope_to_host` drops alerts naming a *different* host (host-less
  alerts are kept — identity/cloud alerts carry none). The summary reports
  `host_scope` and an unscoped run says so in `warning`.
- **One alert satisfied every detection type in a step.** All N `Result` rows
  of a step share the step's technique, so one BIOC firing marked BIOC, XQL
  and Correlation expectations observed. The alert's `source` is now captured
  (`ObservedAlert.alert_source`) and both sides map onto one family
  vocabulary (`bioc · abioc · analytics · correlation · ioc`); known-and-
  different families are not a match, agreement adds `source` to
  `matched_on`, and an unknown source ("XDR Agent") stays unconstrained but
  is recorded on the verdict.
- **Multi-technique strings lost every id after the first.** `_technique_id`
  used `re.search`; `findall` now keeps them all. `matching_service_rule_id`
  (the field Cortex documents) joins the rule-id keys.

### Changed — a malformed identity spec stops SimCore; an absent one shows on `/api/health`

`engine.identity_spec` used to answer any failure to read
`spec/identity_harness.json` with its hard-coded copy, and any empty key with
the same copy, logging one warning.

- **Present but malformed now raises `IdentitySpecError`, naming the file and
  the fault.** This covers invalid JSON, a non-object root, a missing list, a
  non-list, an empty list, a blank or non-string entry, a duplicate entry, and
  a path that exists but is not a readable file. The old reading was wrong, not
  merely quiet: `"service_accounts": "www-data"` served its characters, and on
  boot S-17 rejected 71 of 177 scenarios, each blaming the *scenario*
  ("identity 'www-data' is not declared"). `/api/health` then reported 106
  scenarios as ok. **SimCore now refuses to boot** with a malformed spec and
  scenarios present (uvicorn exit 3, the error is the last traceback line).
- **Absent still serves the built-in copy, which is pinned identical to the
  file, so bundle bytes do not change.** It is no longer silent:
  `/api/health` gains an `identity_spec` component that reports
  `IDENTITY_SPEC_ABSENT` (degraded, with the path and the `COPY spec/` fix) or
  `IDENTITY_SPEC_MALFORMED`. `identity_spec.spec_source()` says which source is
  in force.
- **Removed:** the import-time constants `DIRECT_IDENTITIES`,
  `SERVICE_ACCOUNTS` and `IMPERSONATION_PLATFORMS`. Call the accessors instead.
  Importing the module no longer reads the spec, so a malformed file raises at
  the call that needs it and the health probe can still import the module to
  report it.

### Added — the Composer canvas is direct manipulation, with a Lanes lens

Spec `docs/superpowers/specs/2026-09-08-composer-direct-manipulation-design.md`.

- **The design lens renders through React Flow.** Step nodes drag by a grip
  handle and their positions persist in `composer_layout`; a stored position
  overrides the computed layout and **Re-layout** clears it. Causality edges
  are drawn, and an illegal one (a forward or self reference, a second root
  once any parent is declared) is refused visibly through one spine-legality
  predicate shared with the parent picker. Zoom toolbar, stitch overlay and
  pivot typing on the edge; the Run lens is scoped to the open draft's own
  scenario; a saved draft keeps its `origin.steps`.
- **Lanes lens:** swimlanes by ingestion door (agent telemetry, EAL emitter,
  collector) with drag-to-retarget (`ui/src/components/console/composerLanes.js`).
  A step's channel wins over a stale lane override.

### Changed — the console moved to the PANW Executive design system

`docs/design/DESIGN-SYNC.md`. The Executive design-system tokens are vendored
under `ui/src/styles/ds/` and the accent contract inverted; the shell chrome
was rebuilt and the destination information architecture rewritten, with an
object sheet and five new surfaces; Coverage, the Launch Gate and the Tenant
page were brought to the design; the generated run topology is wired and the
seed catalogs guarded; every destination is guarded by a test, which caught
one; the 1200 px floor moved off the document and onto the bars. The minimal
pass below (2026-10-04) then reduced that architecture to five tasks.

### Added — a detector-readiness gate before the POV runs

`POST /api/eal/campaigns/{campaign_id}/detector-readiness`: the vendor alert
profiles are transcribed, every EAL detector is bound to the vendor alert it
should raise or the gap is stated, and a campaign can be gated on that
readiness before the customer is watching rather than after. Documented in
`docs/reference/eal-plugin-catalog.md`.

### Added — a high-density Library table

`ScenarioTable` (`ui/src/components/console/ScenarioTable.jsx`): the
simulation library as a dense table alongside the card grid, with the plane
and technique collectors exported for both.

### Added — sprint 2: the loop can now measure two more KPIs (still `tenant-verified: 0`)

- **Detection Accuracy is measured** (`core/connectors/measures.py`). The
  reconcile loop always computed matched and seeded and threw the ratio
  away; `reconcile_run` now hands the scorer a pull context (host scope,
  truncation, matched alerts' incident ids) and `score_run` receives
  `observed / seeded` as the measured value for the 47 scenarios whose
  primary KPI is Detection Accuracy. Machine-PASS-reachable DET/HNT rows go
  from 6 to 14 with zero extra tenant calls.
- **Correlation rate is measured** for the 21 scenarios whose KPI is
  Cross-Source Correlation Rate / Stitch Completeness / Correlation
  Coverage: the collapse ratio `(alerts − incidents) / (alerts − 1)`, from
  the `incident_id` the tenant stamps on alert objects when it does, else
  from one host-scoped `get_incidents` read (`XsiamConnector.pull_incidents`,
  same paging and truncation accounting). The basis is recorded
  (`alert_incident_ids` | `host_incidents`).
- **One honesty guard on both.** An **unscoped** pull may never produce a
  PASS (any endpoint's alert could have been credited; a FAIL stands since
  extra alerts only raise the number). A **truncated** pull may never
  produce a FAIL (the value is a floor; a floor that clears the bar passes).
  **Zero matched** stays `pending` — not landed yet is not missed. Every
  withheld value carries its reason in `tc_verdict_detail.measurement`, and
  a re-score with no new evidence keeps the last measured value rather than
  flipping a pass back to pending.
- **Exact rule-name key.** `Result.detection_name` is seeded from the card's
  detection name (new nullable column, migrated on boot) and the matcher
  consumes an alert taken on that key, so a step expecting N distinct
  detections needs N alerts instead of one alert N times. Whether the key
  fires on a real tenant depends on card names matching the tenant's alert
  names — authored content, reported per match in `matched_on`.
- **API paths are credential config.** `alerts_path` / `incidents_path`
  (v1 defaults) so a tenant on the newer alerts endpoint is a setting, not a
  patch; the path that answered is on `PullResult.detail.path` and the
  preflight `alert_shape` rung, which also reports which correlation basis
  the tenant will get.

### Added

- **Preflight `alert_shape` rung** on `POST /api/connectors/xsiam/preflight`:
  samples one alert and reports which of the keys the connector reads are
  present, the `source` value and `total_count`, with codes
  `PF_ALERT_SHAPE_PARTIAL` / `_UNMATCHABLE` / `_UNKNOWN`. An empty tenant is
  now **degraded, not ready** — auth can be green while nothing proves
  matching would work.
- `docs/uc_tc_mapping/audit-2026-09-20.md` (counted state vs the five in-tree
  "current state" documents; the generated scoreboard under-counts assertion
  bindings 4×) and the three-sprint implementation plan.
- **A non-UTC pass in the `backend` CI job.** The runner and the image are UTC,
  so a naive-UTC datetime read as host-local time went green in CI and red on
  DC laptops, twice. The step reruns the whole `tests/` tree in the same image
  and order under `TZ=America/Los_Angeles` (`scripts/test-backend-non-utc.sh`,
  `make test-backend-non-utc`). It refuses to start pytest if the zone resolves
  to UTC, because an unknown zone name or an image without tzdata does that
  silently.

### Docs

- Design specs: third-party content import (Sigma + ATT&CK Navigator,
  `docs/superpowers/specs/2026-09-29-third-party-content-import-spec.yaml`),
  the homelab detection-validation ladder
  (`docs/superpowers/specs/2026-10-04-homelab-detection-validation-ladder.md`),
  and the late-breaking-scenario authoring spec
  (`docs/authoring/late-breaking-scenarios.md`). The README's release claims
  were replaced with measured ones and the Pages site re-measured against the
  corpus and the v1.0.2 release.

### Dependencies

- `ui`: `@vitest/ui` 5.0.0 -> 5.0.5, `vite` 7.3.6 -> 7.3.7, `source-map-js`
  1.2.1 -> 1.2.2, and the vitest family bump of 2026-09-10 (`vitest`,
  `@vitest/mocker`, `@vitest/coverage-v8`). Dev-only; nothing ships in the
  image.

## [1.0.2] - 2026-09-08

**Three console defects that a DC would hit inside the first minute of a
demo, plus the deploy-hygiene defect that hid the fix for them.**

Nothing here changes a count, a verdict, or a detection claim.
**`tenant-verified` is still 0**; authored is not proven.

### Fixed

- **The destination rail presented a truncated list as a complete one.**
  `.shell` is a fixed-height `overflow: hidden` column stacking four bands
  above `.workspace` — the un-acknowledged safety gate (197px), header
  (56px), phase bar (59px) and readiness line (41px), 353px total — which
  left `.rail--nav` a 337px scrollport for 663px of destinations at
  1280x720. Chromium paints an *overlay* scrollbar there (measured
  `offsetWidth - clientWidth` = **1px**), so Readiness, Runs, EAL, Data
  Streams, Coverage and TTPs were scrolled off with no cue at all, and
  "Tools & Payloads" / "UC / TC Index" sat flush against the cut. A rail
  that silently ends does not read as scrolled; it reads as a console that
  has no Coverage surface. Density scoped to `--nav` reclaims 144px
  (663px → 519px), and a new `overflowEdges()` classifier publishes
  `data-overflow` on the nav which CSS turns into an edge fade over
  whichever side has more. The classifier fails toward *showing* the cue:
  unmeasurable scroll metrics return `both`, because a false "there is more
  below" costs one scroll and a false "that is everything" costs a
  destination the DC never opens. Routing was never implicated —
  `#/adapters` and `#/uctc` resolved correctly throughout.

- **A rebuilt console could keep serving the previous build.** The static
  mount was bare `StaticFiles`, which sends `ETag` + `Last-Modified` and no
  `Cache-Control`, handing the browser RFC 9111 heuristic caching (4.2.2):
  with no stated freshness a response may be reused *without* revalidating.
  `index.html` keeps its name across every deploy while `npm run build`
  rewrites every hashed chunk name and deploying replaces `core/static`
  wholesale, so a heuristically-fresh `index.html` kept requesting chunks
  that no longer existed. `ConsoleStatic` now states both halves explicitly
  — `assets/*` immutable for a year (the filename *is* the version),
  everything else `no-cache` (keep it, but revalidate every load, which the
  existing ETag turns into a 304). Deliberately not `no-store`, which would
  also defeat the back button.

- **The Composer's causality parent picker offered parents the model
  silently refuses.** It listed every step except the selected one,
  including *later* ones; `setCausalityParent` returns the same array
  unchanged for a forward or self reference, mirroring the loader's rule
  that a step may only descend from an earlier step. Selecting one produced
  no error, no change and no explanation. The picker is now restricted to
  steps preceding the selected one, so every offered option is one the
  model accepts.

### Changed

- **Both Palo Alto Networks marks removed from the console header.** The
  left brand block carried a `panw-mark` lockup beside the Cortex mark, and
  a second text wordmark rendered further down the header. CortexSim is not
  a Palo Alto Networks product and the header asserted otherwise in two
  places. The Cortex mark stays as the sole brand. A negative guard now
  checks class names *and* `innerHTML` — it is what located the second mark
  after the first pass missed it.

- **`CORTEXSIM_VERSION` default moves to `1.0.2`**, so the image tag and
  container name (`cortex-pov-engine-simcore-v1.0.2`) change with the code
  they carry. A container named for the old version cannot quietly serve the
  new one, or the reverse.

## [1.0.1] - 2026-09-07

**The first release that actually ships artifacts.** `v1.0.0` was tagged
correctly and then produced nothing: `release.yml` failed in its `lint-shell`
job, so there is no GHCR image, no stage2 bundles, no `SHA256SUMS`, and no
GitHub Release behind that tag. The engine at `v1.0.0` and at `v1.0.1` is
byte-identical apart from three comments — the version exists because the
release pipeline, not the product, was broken.

The honest limitations recorded under `[0.1.0]` still stand and are not
superseded by either version number. **`tenant-verified` is 0**; authored is
not proven.

### Fixed

- **`release.yml` could never publish a release** — `shellcheck install.sh`
  exits 1 on any finding regardless of severity, and it reported three, none
  of them errors: one `SC2154` and two `SC2016`, all on code that is
  deliberately written the way it is. `SC2154` is a false positive — `_ec=$?`
  *is* assigned, as the first statement in that trap's own body, but
  ShellCheck does not parse inside the single-quoted trap argument. The two
  `SC2016` findings are backwards: deferred expansion is the point, because
  the literal `$PATH` / `$HOME` must reach the user's profile unexpanded and
  resolve when they source it — taking the advice would bake the installer
  machine's `PATH` into `~/.bashrc`. Fixed with three targeted
  `# shellcheck disable=` directives that each carry their reason, rather than
  `shellcheck -S error`, which would have greened the job by disarming every
  future genuine warning in these scripts.

### Changed

- Application version reported by `GET /api/health` and the OpenAPI document
  is now `1.0.1`.

## [1.0.0] - 2026-09-04

First official launch release — the MVP. Promotes CortexSim from its `v0.1.0`
pre-release to a tagged 1.0. The engine, detection corpus, cross-platform
agent, and console described under `[0.1.0]` below are the shipping baseline;
this release adds the scenario Composer and closes the release-automation
loop. The honest limitations recorded under `[0.1.0]` — chiefly
**`tenant-verified` is 0**, authored is not proven — still stand and are not
superseded by the version number.

### Added

- **Scenario Composer (Phase 1)** — author, persist, and launch *draft*
  scenarios from the console: a draft schema + ORM converter, a
  `/api/scenarios/drafts` CRUD router, a TC-bound launch gate for draft runs,
  and a spine-constrained graph canvas with an editable inspector. Drafts are
  excluded from the Library, coverage, scope, and adapter surfaces, so an
  unfinished draft never inflates a published count.
- **Release automation off `main`** — `tag-on-main.yml` reads the version from
  `CHANGELOG.md` on a merge to `main`, cuts the matching `vX.Y.Z` tag, and
  dispatches `release.yml` (multi-arch GHCR image + GitHub Release). It is
  idempotent: an unchanged top version never re-releases, so the existing
  Pages and wiki syncs on `main` are unaffected.
- **Apache-2.0 licence, `NOTICE`, and `SECURITY.md`** — the repository was
  public with no licence, which legally reserved all rights and made it
  unusable by anyone who found it. `NOTICE` records the trademark and
  affiliation terms: CortexSim is an independent project, not an official Palo
  Alto Networks product. `SECURITY.md` gives the private disclosure path and,
  as importantly, states which properties are deliberate design rather than
  bugs — beginning with the fact that SimCore has no authentication on purpose,
  and naming the assumption that carries it (never exposed to an untrusted
  network).
- **`make launch-preflight`** (`scripts/launch-preflight.sh`) — Gate B as one
  command: branch topology, version coherence, no-abandoned-branches, the
  CLAUDE.md Gate A5 honesty invariants, the corpus gates, and image parity.
  Reports three outcomes, not two: a gate that could not RUN exits 2 and
  withholds the verdict rather than reading as a pass.
- **`docs/release/mvp-launch-runbook.md`** — the ordered `dev` -> `main`
  procedure with the measured state of every gate.

### Changed

- **Console UX overhaul** — the shell/console redesign lands: a code-split
  entry chunk for faster first paint, the TTP browser as a paged grid with a
  full-width breakout (and a visible way back), navigation aligned to the POV
  run phases, a session-scoped safety gate, and a set of accessibility and
  theming fixes (tour controls, dropdown clipping, Library height,
  always-dark bubble contrast).
- **SecOps assertion coverage** — new POS/PLT/AUT assertion artifacts close
  SecOps test cases (XTI/ERV/NDR/AGTX/APB) through the same
  `verifier.score_run` path, and UC/TC index rows whose success criteria
  contradicted their own title were corrected.
- **`generate_bash` now resolves through `_resolve_step_posix`.** It read
  `step["command"]` directly and ignored `platform_variants`, while
  `resolve_target(..., "posix")` consults `platform_variants.linux` when the
  primary command is Windows-shaped. A scenario could be judged POSIX-emittable
  *because of* its Linux variant and then ship the Windows-shaped primary —
  `SIM-MP-020`'s exfiltration step emitted `powershell.exe ... || curl
  http://127.0.0.1:8888/api/health` into a bundle whose cardinal rule is no
  SimCore dependency at runtime. On a clean Ubuntu 22.04 that step reported
  success having emitted no telemetry, so its absent detection read as "Cortex
  missed it". `generate_powershell` always resolved correctly; only the bash
  loop did not. Guarded by
  `test_bash_bundle_ships_what_the_resolver_resolved` plus `:8888` in
  `_SIMCORE_MARKERS`. Behaviour-preserving: all other scenarios emit
  byte-identical bundles.
- **`wiki-sync` no longer fails on a no-op.** The change check ran before
  `git add -A`, when `git rm -rfq .` had just staged a deletion for every page,
  so it always entered the commit branch; `git commit` then exited 1 with
  "nothing to commit" and `set -e` failed the job. A successful no-op published
  a red X, which is how a real publish failure would have gone unnoticed.
- **Corrected published counts** — Pages (28 assertions, 26 EAL plugins, 205
  authored, Correlation 114, IOC 39), README (134 routes / 23 router modules,
  26 EAL plugins), and the wiki (134 routes, 26 plugins, 28 assertions). Every
  figure is from `scripts/generate_ground_truth.py`, not retyped.
- **`CORTEXSIM_MASTER_KEY` -> `CORTEXSIM_SECRET`** across CLAUDE.md and three
  reference docs. Nothing has ever read `CORTEXSIM_MASTER_KEY`; an operator
  following the quick-start set a variable the code ignores.
- **`ci.yml` and `test.yml` declare `permissions: contents: read`.** Neither
  uses `GITHUB_TOKEN`; on a public repo an undeclared block is how a
  compromised action escalates from running tests to writing code.
- **Reported application version** is `1.0.0` (`GET /api/health`,
  OpenAPI/`/api/docs`), matching this tag.
- **`docs/reference/ground-truth.*`** regenerated: 134 route decorators, 25
  `APIRouter` instances, 23 router files.

### Fixed

- **`cp .env.example .env` no longer breaks the boot.** The quick-start's first
  line made `core/config.py::Settings()` raise, which took down the entire
  pytest suite and any local process constructing `Settings` from the repo root.
  `.env.example` gained `CORTEXSIM_VERSION` (it drives the image tag and
  container name) without a matching field on `Settings`, and pydantic-settings
  hands every dotenv key to the model — so an undeclared one is rejected, not
  ignored. The variable is now declared. Fixed by declaring it rather than by
  relaxing to `extra="ignore"`: rejecting unknown dotenv keys is the only thing
  that catches a typo'd setting name, which would otherwise read as "absent" and
  silently change boot behaviour. `tests/test_config.py` constructs `Settings()`
  against the shipped `.env.example` verbatim so the next compose-only variable
  cannot re-open this, and asserts an unknown key still raises so the guard
  cannot be removed quietly.

## [0.1.0] - 2026-08-31

First tagged pre-release. CortexSim is an enterprise detection simulation
engine for Palo Alto Networks Domain Consultants: it generates controlled,
high-fidelity signals into customer Cortex environments (XSIAM/XDR) to
validate detection logic across the full `detection_type` vocabulary
(`BIOC | XQL | Analytics | Correlation | IOC | ABIOC`), plus the XDM
modeling-rule normalization substrate and cross-source stitching. Think
"MITRE Caldera's opinionated nephew" — a detection quality-assurance engine,
not a red-team C2. There is no authentication anywhere in this app by design:
it is built to run on a customer-lab jumpbox where the operating DC already
has full admin access.

### Added

- **SimCore orchestrator** (FastAPI, `core/`) — scenario loading from
  versioned YAML with strict Pydantic schema validation, run lifecycle
  (launch → seed → execute → complete/abort), a durable task queue that
  survives a restart, and live progress over Server-Sent Events
  (`/api/runs/{id}/events`, `/api/events`).
- **Dual execution modes** — **pull**: a Go beacon (`cortexsim-agent`) polls
  SimCore, executes steps through an identity harness, and streams output
  back; **push**: SimCore renders a self-contained bash or PowerShell bundle
  the DC downloads and runs with no SimCore dependency at runtime.
- **Cross-platform agent** — the beacon cross-compiles for
  `linux/{amd64,arm64}`, `darwin/{amd64,arm64}`, and `windows/amd64`, served
  directly from the running SimCore image (`GET /api/agents/binary`); an
  enrollment-token flow (`POST /api/agents/enroll/tokens`) mints a scoped,
  revocable one-liner so an install script never carries a bare shared
  secret.
- **Identity harness** — every step runs under a realistic service-account
  identity (`www-data`, `postgres`, `node`, `svc-account`, …) to build honest
  process-causality chains in XSIAM, driven by one shared spec
  (`spec/identity_harness.json`) consumed by both push and pull.
- **Causality contract** — an optional, additive per-scenario/per-step
  contract (`cgo_anchor`, `causality`, `platforms`, `platform_variants`) that
  collapses the synthetic beacon "star" into a connected CGO-rooted
  process/network causality graph.
- **React console UI** (`ui/`) — scenario browser and launcher, MITRE ATT&CK
  coverage heatmap, results/validation views, UC/TC index explorer, adapter
  registry, causality view, and a **Readiness** surface that renders the
  connector ladder (Authored → Configured → Reachable → Verified) so a
  green screen never overstates what has actually been proven.
- **Detection content corpus** — 177 loadable scenarios across 16 detection
  planes (EDR, CDR, NDR, ITDR, CSPM, ASM, TIM, Cloud App, Analytics,
  AI Access, AIRS, Browser, KOI, AI_SPM, Email, DLP), 175 TTP cards, 1,096
  step-level expected detections, and 1,777 catalog detection objects across
  the `BIOC | XQL | Analytics | Correlation | IOC | ABIOC` vocabulary plus
  the XDM modeling-rule substrate.
- **UC/TC alignment (FY27 v2.2 index)** — every scenario carries a validated
  foreign-key reference (`uc_ref`/`tc_ref`/`tc_refs[]`/`pov_scenario_id`) into
  the sales-motion master index, enforced at load under
  `CORTEXSIM_STRICT_REFS` (default on), and surfaced read-only in-product at
  `GET /api/uctc/*` and the console's UC/TC Index view.
- **Assertion substrate (POS/PLT/AUT)** — a second proof mechanism for the
  140 index rows that are not detection-shaped (posture, capability-presence,
  outcome-within-budget), scored through the same `verifier.score_run` as
  scenarios, with a load-time guard (`A-17`) that rejects any check that
  structurally cannot fail.
- **Optional, read-only measurement loop** (`core/connectors/`) — when a
  tenant credential is configured, SimCore reads alerts back and
  auto-validates seeded results into evidence-backed MTTD (Tier 1, offline
  scoring; Tier 2, opt-in outbound XQL verification). No credential, no
  outbound call, ever.
- **Preflight** (`POST /api/connectors/{kind}/preflight`) — answers "is my
  connection working?" before the POV starts, staged config → DNS/TLS →
  auth → scope → datasets → clock skew, so a broken tenant integration is
  caught before it is quoted as tenant proof.
- **Tool adapter framework** — 91 declarative adapter packs across a 5-tier
  model (in-tree, submodule, IaC-provisioned, runtime-fetched,
  external-only), and a **payload shelf** that stages digest-pinned
  third-party tool bytes on the DC's own SimCore so a default-deny customer
  network never has to reach the public internet to run a tier-4 tool.
- **EAL Traffic Simulator** — 21 plugins covering signal-injection (network,
  identity, SaaS, AI, browser, email) and shape-true analytics log streaming
  (CloudTrail, Azure Activity/Audit, Kubernetes audit, M365, AD/Windows
  security, NGFW EAL, Okta/Entra sign-in) to an operator-supplied collector,
  with accounted (2xx-only) delivery verdicts.
- **IaC topology generator** — Terraform bundles (AWS, 11 modules: base,
  edr, cdr, content-library, itdr, ndr, cspm, asm, tim, telemetry-replay,
  ai-spm) that Torque or a bare `terraform apply` can consume to stand up a
  target environment with intentional, documented findings.
- **`GET /api/health`** — the one diagnostic surface: never reports green for
  something it did not check, makes zero outbound calls, and names its own
  `not_checked[]` boundary explicitly.
- **CI** — a 6-job matrix (backend, agent incl. Windows cross-compile, ui,
  detection-corpus validation, UC/TC ref strictness, adapter-source
  preflight) plus a deterministic `docs/reference/ground-truth.*` regeneration
  gate.

### Changed

- CORS now serves `allow_origins=["*"]` with `allow_credentials=False`. The
  app has no authentication anywhere, so nothing ever sent credentials — the
  previous `allow_credentials=True` combination is invalid per the CORS
  spec and browsers silently rejected it. This is a correctness fix, not a
  narrowing: every route stays open, exactly as designed for an
  unauthenticated jumpbox tool.
- Reported application version moved from a placeholder `1.0.0` to `0.1.0`
  (`GET /api/health`, OpenAPI/`/api/docs`) to match this first real,
  git-tagged release rather than implying nine prior ones that never
  existed.

### Known limitations (read before you brief a customer on this)

These are not bugs to be fixed quietly later — they are the honest current
state of the project, and they are restated here on purpose so a release
note never overstates what has been proven.

- **`tenant-verified` is 0.** No run, no assertion, and no test in this repo
  has ever executed against a live Cortex tenant. Every green result — in
  the test suite, the console, and this changelog — comes from an injected
  transport. **Authored is not the same as proven.** The console's Readiness
  surface states this verbatim, and `docs/reference/ground-truth.json`
  (`tenant_verified: 0`) is the machine-checkable form of the same fact.
- **A bare Ubuntu target cannot run this corpus.** Stock `ubuntu:22.04`
  ships `www-data` with `/usr/sbin/nologin` and no home directory, so the
  identity harness fails in milliseconds and the run reads "failed" having
  executed nothing — indistinguishable from a real miss unless you know to
  look for it. `deploy/tier-d/Dockerfile.target` documents (and provisions)
  what a target actually needs; see `docs/reference/lab-runbook.md`.
- **A meaningful slice of scenario steps are placeholders.** At minimum 100
  of the corpus's 654 total steps are pure `echo`/`printf` statements that
  declare `expected_detections` without producing the underlying signal a
  sensor could catch — staged for content authoring, not yet load-bearing
  TTPs. The reproduction command and the honest caveat that the true count
  is likely higher (once `|| echo` fallbacks on real commands are counted
  too) live in `docs/reference/lab-runbook.md`.
- **Only 59 of 177 scenarios declare an MTTD-shaped primary KPI** — the only
  KPI class the engine measures natively today. The rest declare thresholds
  the engine cannot yet produce a `measured_value` for and score `pending`
  indefinitely; that is a stated gap, not a silent one.
- **The Rust tool matrix (`signalbench`, `ackbarx`, `xdrtop`) is
  `linux/amd64`-only.** No arm64 or macOS build exists yet, for reasons
  recorded in `core/Dockerfile`'s `rust-builder` stage (an OpenSSL/vendoring
  constraint on a Rust submodule this repo does not own).
- **This release was built and proven only for the host's native
  architecture** (`linux/amd64`). The published multi-arch image
  (`linux/amd64` + `linux/arm64`) is produced by CI on tag push, not by this
  local build — see `docs/release/PUBLISH-v0.1.0.md`.

[Unreleased]: https://github.com/hankthebldr/cortex-pov-engine/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/hankthebldr/cortex-pov-engine/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/hankthebldr/cortex-pov-engine/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/hankthebldr/cortex-pov-engine/compare/v1.0.2...v1.1.0
[1.0.2]: https://github.com/hankthebldr/cortex-pov-engine/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/hankthebldr/cortex-pov-engine/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/hankthebldr/cortex-pov-engine/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/hankthebldr/cortex-pov-engine/releases/tag/v0.1.0
