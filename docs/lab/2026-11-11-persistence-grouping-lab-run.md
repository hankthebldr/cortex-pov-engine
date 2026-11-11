# Lab run plan — Linux persistence chains built to group into one case

**Scenarios:** SIM-EDR-023 (cron foothold, card TTP-2026-0198) and SIM-EDR-024
(sudo backdoor account + SSH key + systemd timer, card TTP-2026-0199).
**Target:** the standalone Cortex XDR agent on `pov-target-01` (lab VM, Ubuntu
24.04, agent 9.4.0.470, policy `SEBC-FY25-LAB-LinuxServerPolicy`), beacon
`pov-target-01-41a13a`, tenant `deathstar`.
**Status:** authored, not run. `tenant-verified` is 0. Nothing below runs
against the VM without Henry's explicit OK, given for that run.

## Executive summary

The lab's first detection runs showed that Cortex raised alerts for our test
activity but grouped none of them into an incident. Every alert was
low-severity, and low-severity alerts are never grouped. The one high-severity
alert, a malware verdict on Palo Alto's official test file, did open a case,
alone. These two scenarios are built so that a realistic Linux persistence
chain raises several medium-or-higher alerts across its stages. That includes
the re-executions that continue for 45 minutes after the operator leaves. The
alerts should land in one incident, which is the property a POV has to
demonstrate. Getting there needs one action from the operator: install ten
detection rules at Medium severity in the tenant before the run. CortexSim
never writes to the tenant. Recommended action: install the rules, approve one
run per scenario, and read the result against the pass criteria in section 3.

## Technical detail

### 0. What the previous runs produced (read back 2026-11-11, read-only)

| Run | Step | Reached `xdr_data` | Alert | Case |
|---|---|---|---|---|
| SIM-EDR-003 `9d91c650` | steps 1–2 | yes | none | — |
| | steps 3–5 | **no events** | none | — |
| spaced probe draft | useradd / chpasswd | yes | 2× Suspicious access to shadow file (Low) | none |
| SIM-CDR-012 `5e49a99d` | step 1 curl | yes | UNIX LOLBIN process connected to a rare external host (Low) | none |
| | step 2 sample exec | chmod only, **no exec** | none | — |
| SIM-NDR-009 `2268b761` | step 2 (10 lookups) / step 3 | **1 of 10** / **none** | none | — |
| WildFire ELF draft `b83b956e` | exec test ELF | yes | **Local Analysis Malware (HIGH)** | **51877** (alone) |

Design consequences carried into both scenarios:

- **Grouping needs Medium+.** Only Medium-or-higher issues create a case or
  join one (`docs/product-mechanics/ledger.yml`, row INC).
- **Steps are 40 s apart.** Back-to-back steps lost telemetry; spaced commands
  did not. The cause is not established.
- **The beacon is the tenant's CGO.** Every beacon-run step recorded
  `causality_actor_process_image_name = cortexsim-agent`, with one
  `actor_causality_id` across runs. So Medium+ issues from successive runs can
  share a case. Read the case by issue ids, not by "the newest case".
- **Re-executions are a second chain.** cron-run children record
  `actor_process_image_name = cron`. On systemd 255, a service's ExecStart
  child records its parent as `9` (`/proc/self/fd/9`, the fexecve'd
  systemd-executor), not `systemd`.
- **Local Analysis is deduplicated over one hour.** Never run SIM-EDR-023
  within an hour of any other execution of the same test ELF on the host. The
  last one was 2026-11-11 03:30Z (`b83b956e`).
- **Not yet seen in this lab:** file events under `/etc/cron.d`,
  `/etc/systemd/system` and `~/.ssh`. File events have only been seen in
  `/tmp` and `/var/tmp`, plus opens of `/etc/passwd` and `/etc/shadow`. BIOCs
  1, 6 and 7 in section 6 assume the agent records those writes. If one of
  them reads missed, check `xdr_data` for the FILE event before calling it a
  Cortex miss.
- **Lab blockers left as they are:** `www-data` has a nologin shell (both new
  scenarios run as root, so they do not need it), and no correlation rule in
  the corpus is importable. The two rules here are pasted by hand from section 6.

### 1. Before the run, in this order

1. **Load the scenarios.** The SimCore on :8888 runs image `1.1.1-dev` and does
   not have SIM-EDR-023/024. Rebuild it from branch
   `feature/scenarios-linux-persistence-grouping` with `scripts/dev-up.sh`.
   Reuse the root `.env` so the vault (and the `deathstar` credential)
   decrypts, and bump `CORTEXSIM_VERSION` in this worktree's `.env` so the old
   tag is not overwritten. The compose project is pinned, so this replaces the
   running SimCore. The cortex-mcp `run.sh` hardcodes the SimCore container
   name; update it if the name changes.
2. **Install the rules (section 6).** Eight BIOCs and two correlation rules.
   Use **exactly** the names given: SimCore's matcher credits a detection most
   strongly when the tenant alert name equals the card's name. Severity
   **Medium** for all ten. BIOCs analyse historical data once enabled, so they
   may raise issues on past lab activity; that is expected and does not count
   for this run.
3. **Confirm the endpoint.** `pov-target-01` is CONNECTED in the tenant
   (endpoint `0ad53c9061f04741b1b750dafb107952`), and the beacon
   `pov-target-01-41a13a` is online in SimCore.
4. **Check the clock.** `POST /api/connectors/xsiam_tenant/preflight` reports
   clock skew within a few seconds.
5. **Ask Henry for the OK** for the specific scenario about to run.

### 2. Launch

Run one scenario at a time, at least an hour apart, and clean up between
them. Otherwise the second run's issues can join the first run's case through
the shared host, and neither run proves anything on its own.

```bash
curl -s -X POST http://localhost:8888/api/runs -H 'content-type: application/json' -d '{"scenario_id":"SIM-EDR-023","mode":"pull","target_agent_id":"pov-target-01-41a13a"}'
```

```bash
curl -s -X POST http://localhost:8888/api/runs -H 'content-type: application/json' -d '{"scenario_id":"SIM-EDR-024","mode":"pull","target_agent_id":"pov-target-01-41a13a"}'
```

Neither scenario wires an external tool, so no `consent` block is needed.
Both are **pull only** (`push_supported: false`). A push bundle runs the
cleanup on exit, which would remove the footholds before they re-run.
SIM-EDR-023's Local Analysis row is declared `outcome: prevent`: the lab's
report-mode policy scores it as detected, and a later block-mode paired run
would grade the kill. Allow about 1h45m after launch before a block-mode run,
because the cron job keeps re-running the ELF for 45 minutes and Local
Analysis deduplicates over an hour.
Expected timeline per scenario:

| t | SIM-EDR-023 | SIM-EDR-024 |
|---|---|---|
| 0:00 | step-01 curl test ELF | step-01 useradd + chpasswd |
| 0:40 | step-02 exec ELF → Local Analysis (HIGH) | step-02 SSH key |
| 1:20 | step-03 cron drop-in | step-03 systemd unit + timer |
| 2:00 | step-04 touch -r | step-04 chattr +i |
| ~2:20 | run `complete` | run `complete` |
| ≤5:00, then every 5 min | cron re-run → BIOC issue | timer run (first at ~2:20) → BIOC issue |
| ≤ ~15:00 | CR-EDR-0023 fires (10-min schedule) | CR-EDR-0024 fires |
| 45:00 | foothold deletes its cron file | payload disables its timer |

### 3. What proves the alerts were grouped

Read the tenant after the run, read-only (Cortex MCP oracle, or the SimCore
read-back below). Use the window from the run's `started_at` to +60 min.

1. **Collect the run's issues.** Use `get_issues` with `detection.method` in
   `["BIOC", "CORRELATION", "TRAPS", "ANALYTICS_BIOC"]`, then keep the issues
   whose `name` is in the scenario's set (section 6, plus `Local Analysis
   Malware` and the built-in ABIOC names) and whose `observation_time` falls in
   the window.
2. **Group them by case.** Every Medium+ issue in that set must carry the
   **same single** `case_ids[0]`. `get_cases` on that id must list them in
   `issue_ids`, with host `pov-target-01`.

| Verdict | SIM-EDR-023 | SIM-EDR-024 |
|---|---|---|
| **PASS** | Local Analysis Malware + the 3 BIOCs (cron BIOC ≥ 2 runs) + CR-EDR-0023 all in ONE case; ≥ 7 of the 8 step-detections observed in SimCore | the 5 BIOCs (timer BIOC ≥ 2 runs) + CR-EDR-0024 all in ONE case; ≥ 6 of the 7 step-detections observed |
| **Grouping FAIL** | the Medium+ issues exist but carry two or more case ids | same |
| **Detection gap** | an expected Medium+ issue never appears; check `xdr_data` for the step's events before calling it a Cortex miss (section 0: telemetry has dropped before) | same |
| **Not a fail** | the Low ABIOCs sit outside the case; that is the product's rule, and they score for detection only. Both already fired on this host on 2026-11-11 and judge rarity, so each may read missed on a healthy tenant; the thresholds above allow that one miss | same |

3. **Record it in SimCore.**
   - `POST /api/runs/{run_id}/reconcile?connector=xsiam` pulls the alerts and
     marks the alert-backed Results observed.
   - `POST /api/runs/{run_id}/verify` runs each row's `verification_xql`.
     Only SIM-EDR-023's two XQL rows carry one. Verify looks back one hour from
     the moment it runs, so call it within the hour.
   - Both are outbound **reads**. Only a run that passes here may move
     `tenant-verified` off 0, and only for these two scenarios.

### 4. Optional: reboot survival

Inside the 45-minute window, reboot the VM. Then run the hunt queries
`EDR-023 Cron Foothold Re-Executions Across A Reboot` and
`EDR-024 Systemd Timer Foothold Fired After Boot` from the cards. A
`last_run` after the boot time shows the foothold ran again after the reboot.
This leg is observed, not scored.

### 5. Cleanup (the beacon does not run `cleanup.commands`)

Run after the read-back, from Henry's terminal on the VM, or as a one-step
`identity: root` draft through the beacon:

```bash
sudo sh -c 'rm -f /etc/cron.d/cortexsim-ice; rm -rf /var/tmp/.cortexsim-ice'
```

```bash
sudo sh -c 'systemctl disable --now cortexsim-sync.timer || true; rm -f /etc/systemd/system/cortexsim-sync.service /etc/systemd/system/cortexsim-sync.timer; systemctl daemon-reload; chattr -i /home/cortexsim-svc/.ssh/authorized_keys || true; userdel -r cortexsim-svc || true; rm -rf /var/tmp/.cortexsim-sshd'
```

Both footholds also expire on their own after 45 minutes. The account is
created already expired, so it cannot log in at any point.

### 6. Rules to install in the tenant before the run

Generated from the committed cards (`detection_scanner/ttps/TTP-2026-0198-*.json`, `TTP-2026-0199-*.json`); the same text is in `detection_scanner/exports/xql/TTP-2026-019{8,9}.xql`. Every query below parsed in the `deathstar` tenant on 2026-11-11 (read-only XQL, status SUCCESS). The ones that could match past lab activity did match it: the `useradd -G sudo` BIOC and the systemd-executor actor clause.

Install order: all ten before the first launch. BIOCs: severity **Medium**, name exactly as given, XQL as given (filter-only on `xdr_data`, as the BIOC rule builder requires). Correlations: severity **Medium**, alert name = rule name, **Scheduled every 10 minutes, query time frame 1 hour**, alert field mapping `agent_hostname` → Host Name and `agent_id` → Agent ID.

#### TTP-2026-0198 · SIM-EDR-023

**1. BIOC — `EDR-023 Cron Drop-In Written By Shell Outside Package Manager`**  
Severity: Medium · Category: Persistence · MITRE: T1053.003

```
dataset = xdr_data
| filter event_type = ENUM.FILE and event_sub_type in (ENUM.FILE_CREATE_NEW, ENUM.FILE_WRITE)
| filter action_file_path ~= "^/etc/cron[.](d|hourly|daily|weekly|monthly)/" or action_file_path ~= "^/var/spool/cron/"
| filter actor_process_image_name in ("sh", "bash", "dash", "zsh", "tee", "cp", "mv", "cat", "python3", "perl")
```

**2. BIOC — `EDR-023 Cron Runs Payload From Hidden Var Tmp Directory`**  
Severity: Medium · Category: Persistence · MITRE: T1053.003, T1564.001

```
dataset = xdr_data
| filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START
| filter actor_process_image_name = "cron"
| filter action_process_image_command_line ~= "/var/tmp/[.][^/ ]+/"
```

**3. BIOC — `EDR-023 Persistence Artifact Timestomped With Touch Reference`**  
Severity: Medium · Category: Evasion · MITRE: T1070.006

```
dataset = xdr_data
| filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START
| filter action_process_image_name = "touch"
| filter action_process_image_command_line ~= "[ ]-(r|t|d)[ ]" or action_process_image_command_line ~= "--(reference|date)"
| filter action_process_image_command_line ~= "(/etc/cron|/etc/systemd/system/|/var/tmp/[.])"
```

**4. Correlation — `EDR-023 Cron Foothold Planted Then Re-Executed On Schedule`** (CR-EDR-0023)  
Severity: Medium · Scheduled every 10 min · time frame 1 h · map `agent_hostname` → Host Name, `agent_id` → Agent ID

```
dataset = xdr_data
| filter agent_os_type = ENUM.AGENT_OS_LINUX
| alter leg = if(event_type = ENUM.FILE and action_file_path ~= "^/etc/cron[.]d/" and actor_process_image_name in ("sh", "bash", "dash", "zsh", "tee", "cp", "mv", "cat", "python3", "perl"), "planted", if(event_type = ENUM.PROCESS and actor_process_image_name = "cron" and action_process_image_command_line ~= "/var/tmp/[.][^/ ]+/", "reexecuted", "other"))
| filter leg in ("planted", "reexecuted")
| comp count_distinct(leg) as legs, count() as events, min(_time) as first_seen, max(_time) as last_seen by agent_hostname, agent_id
| filter legs >= 2
```

#### TTP-2026-0199 · SIM-EDR-024

**5. BIOC — `EDR-024 Local Account Created In Sudo Group By Root Shell`**  
Severity: Medium · Category: Persistence · MITRE: T1136.001, T1098

```
dataset = xdr_data
| filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START
| filter action_process_image_name in ("useradd", "adduser", "usermod")
| filter action_process_image_command_line ~= "(-G|--groups|-aG)[ =]?[^ ]*(sudo|wheel|admin)"
| filter actor_process_image_name in ("sh", "bash", "dash", "zsh", "python3", "perl")
```

**6. BIOC — `EDR-024 SSH Authorized Keys Written For Local Account`**  
Severity: Medium · Category: Persistence · MITRE: T1098.004

```
dataset = xdr_data
| filter event_type = ENUM.FILE and event_sub_type in (ENUM.FILE_CREATE_NEW, ENUM.FILE_WRITE)
| filter action_file_path ~= "/[.]ssh/authorized_keys2?$"
| filter actor_process_image_name in ("sh", "bash", "dash", "zsh", "tee", "cp", "mv", "cat", "python3", "perl")
```

**7. BIOC — `EDR-024 Systemd Unit Written Under etc systemd system By Shell`**  
Severity: Medium · Category: Persistence · MITRE: T1543.002, T1053.006

```
dataset = xdr_data
| filter event_type = ENUM.FILE and event_sub_type in (ENUM.FILE_CREATE_NEW, ENUM.FILE_WRITE)
| filter action_file_path ~= "^/(etc|lib|usr/lib)/systemd/system/[^/]+[.](service|timer)$"
| filter actor_process_image_name in ("sh", "bash", "dash", "zsh", "tee", "cp", "mv", "cat", "python3", "perl")
```

**8. BIOC — `EDR-024 Systemd Service Runs Payload From Hidden Var Tmp Directory`**  
Severity: Medium · Category: Persistence · MITRE: T1053.006, T1564.001

```
dataset = xdr_data
| filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START
| filter actor_process_image_name in ("systemd", "9") or actor_process_image_path ~= "^/proc/self/fd/[0-9]+$"
| filter action_process_image_command_line ~= "/var/tmp/[.][^/ ]+/" or action_process_image_path ~= "^/var/tmp/[.]"
```

**9. BIOC — `EDR-024 Immutable Attribute Set On Persistence Artifact`**  
Severity: Medium · Category: Evasion · MITRE: T1222.002

```
dataset = xdr_data
| filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START
| filter action_process_image_name = "chattr"
| filter action_process_image_command_line ~= "[+]i"
| filter action_process_image_command_line ~= "(authorized_keys|/etc/systemd/system/|/etc/cron|/var/tmp/[.])"
```

**10. Correlation — `EDR-024 Backdoor Account Key And Timer Foothold Re-Executed`** (CR-EDR-0024)  
Severity: Medium · Scheduled every 10 min · time frame 1 h · map `agent_hostname` → Host Name, `agent_id` → Agent ID

```
dataset = xdr_data
| filter agent_os_type = ENUM.AGENT_OS_LINUX
| alter leg = if(event_type = ENUM.PROCESS and action_process_image_name in ("useradd", "adduser", "usermod") and action_process_image_command_line ~= "(-G|--groups|-aG)[ =]?[^ ]*(sudo|wheel|admin)", "account", if(event_type = ENUM.FILE and action_file_path ~= "/[.]ssh/authorized_keys2?$", "key", if(event_type = ENUM.FILE and action_file_path ~= "^/(etc|lib|usr/lib)/systemd/system/[^/]+[.](service|timer)$" and actor_process_image_name in ("sh", "bash", "dash", "zsh", "tee", "cp", "mv", "cat", "python3", "perl"), "unit", if(event_type = ENUM.PROCESS and (actor_process_image_name in ("systemd", "9") or actor_process_image_path ~= "^/proc/self/fd/[0-9]+$") and action_process_image_command_line ~= "/var/tmp/[.][^/ ]+/", "reexecuted", "other"))))
| filter leg in ("account", "key", "unit", "reexecuted")
| alter reexec = if(leg = "reexecuted", 1, 0)
| comp count_distinct(leg) as legs, max(reexec) as reexecuted, count() as events, min(_time) as first_seen, max(_time) as last_seen by agent_hostname, agent_id
| filter legs >= 3 and reexecuted = 1
```

Not installed (built in, and already firing in this lab): `Local Analysis Malware` (agent malware module), `UNIX LOLBIN process connected to a rare external host` and `Suspicious access to shadow file` (XDR Analytics BIOCs, Low).


### 7. Decisions still open

- **Correlation rules.** Henry agreed to install the BIOCs; the two
  correlation rules in section 6 are the same kind of console action. Without
  them, CR-EDR-0023/0024 read missed on a healthy tenant, and the Correlation
  share the coverage gate holds would be authored-only.
- **Run order and spacing** (section 2): at least an hour apart, with cleanup
  in between.
- **Case 51877** is still open and on the beacon's causality chain. SIM-EDR-023's
  Local Analysis issue may be deduplicated into it or join it. That is
  grouping, but it mixes with an earlier test. Resolving it first is Henry's
  call: it is a tenant write he makes in the console.
