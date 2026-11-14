# Lab run — the four promoted Linux drafts (SIM-EDR-026 to SIM-EDR-029)

**Date:** 2026-11-14 UTC (evening of 2026-11-13 in CST).
**Branch:** `feature/scenarios-promote-linux-drafts` (cut from `dev` at `8aadf131`).
**Target:** SimCore 1.1.1 at `http://localhost:8888` (container
`cortex-pov-engine-simcore-v1.1.1`), beacon `pov-target-01-4dcdce`, tenant
endpoint `pov-target-01` (Ubuntu 24.04.5, Cortex XDR agent 9.4.0.470, policy
`SEBC-FY25-LAB-LinuxServerPolicy`, report mode). The agent was first seen in
the tenant on 2026-11-10. All runs were pull mode, identity root, launched by
the promotion session with Henry's go-ahead ("Yes, stage and run"). Nothing
was written to the tenant: every tenant read below is a read-only XQL query,
an alert read-back or an issue list.

## Executive summary

Three of the four new Linux test scenarios ran end to end on the lab
endpoint, and the endpoint agent recorded the activity each one is built to
show. Two weaknesses surfaced, one in the agent's recording and one in our
own server.

The agent does not record every event of a fast burst. It kept the first 28
of 60 identical commands and silently dropped the rest, and in another
scenario it dropped the commands that followed a burst of file writes. One
check that depended on a dropped command has been removed. The other checks
were set with enough margin that they still pass.

Our server killed one run that nobody had stopped. A routine status check
failed for a moment while another request was holding the database, and the
endpoint software treated that one failure as an instruction to stop. A
separate fix has been proposed.

None of the built-in Cortex alerts these scenarios name was raised, which is
expected for two of them. The fourth scenario cannot run until one standard
package is installed on the endpoint. The custom correlation rules that would
turn this activity into a case have been checked as queries, but are not
installed.

Recommended action: approve the package install, install the four
correlation rules at Medium, re-run all four scenarios, and re-run the crond
scenario after 2026-11-24 to settle its built-in alert.

## Technical detail

### 1. Runs

| UTC | scenario (commit) | run id | result |
|---|---|---|---|
| 2026-11-13 23:47 | read-only tool probe (draft) | `90b1e800-cf06-4220-92c4-880243b7d6d8` | complete. Present: ssh, scp, sftp (OpenSSH 9.6p1), sudo 1.9.15p5 (`/usr/bin/sudoedit` → sudo), tar, gzip, dd, timeout, getent, hostname, grep. Absent: `sshpass`. `/var/tmp` 1777 on `/`, no noexec; `fs.protected_symlinks=1`; resolver 127.0.0.53, search `.`; `getent hosts` on the `.invalid` canaries rc 2 at once. |
| 00:50:47 – 00:52:57 | SIM-EDR-026 (`668f845a`) | `7714f4ce-625a-4650-90ed-8b6230d607f7` | complete, 4/4 steps rc 0 |
| 00:53:28 – 00:56:15 | SIM-EDR-027 (`896c2634`) | `e42f2409-2a15-4465-ac8c-0e3937556f12` | complete, 5/5 steps rc 0 |
| 01:05:02 – 01:07:55 | SIM-EDR-027 with an experimental `sleep 10` before the gzip leg (not committed) | `a1d6af86-5baa-407b-a137-d27416f61bc8` | complete, 5/5 steps rc 0 |
| 01:39:56 – 01:41:03 | SIM-EDR-028 (`2600840e`) | `328b3e45-e10d-4ebe-bd19-a589efc266a2` | failed: step-02 killed after 55 s by the beacon (finding F3), about 16 sudoedit starts |
| 01:42:44 – 01:45:06 | SIM-EDR-028 (`2600840e`) | `5c500f71-3b23-4f12-b6b0-97e4cdabe9a0` | complete, 3/3 steps rc 0 |
| — | SIM-EDR-029 (`a0b086ab`) | none | not run: `sshpass` is not installed (section 4) |

Each scenario's sandbox under `/var/tmp` was removed by its own last step.
The killed run left `/var/tmp/.cortexsim-sudoedit`, which the next run's
step-01 removed before it did anything else.

### 2. Results per scenario

**SIM-EDR-026 (crond masquerade).**

- **XQL rows:** Tier-2 verify at 00:57:30 issued 3 queries, and all 3 rows passed.
- **CR-EDR-0026:** its XQL, run as an ordinary query over 1 h at 01:11, was accepted by the tenant and returned one row for `pov-target-01`: legs 3, events 30.
- **Built-in "Masquerading as the Linux crond process":** not raised. No endpoint issue appeared between 00:50 and 01:55. Reconcile at 01:45 pulled 1 alert (an unrelated firewall alert) and matched 0.
- **How the engine scores it:** the declared ABIOC row is held `pending` with reason `ANALYTICS_ACTIVATION_PERIOD`, so it reads neither missed nor detected.
- **Why it did not fire is not established.**
  - The agent was 4 days into the 14-day activation period the alert's page states.
  - But "Suspicious data encryption", whose page states the same periods, fired on this endpoint on 2026-11-11, one day after install.

**SIM-EDR-027 (archive exfiltration attempt over SSH).**

- **The run itself:** in both runs scp and ssh exited 255 with `Could not resolve hostname ssh-drop.cortexsim-canary.invalid.`; no connection was made.
- **XQL rows that returned rows:**
  - step-01 decoy tree: 88 rows.
  - step-02 tar: 1 row per run.
  - step-03 scp: 1 row per run.
  - step-04 ssh: 1 row per run.
  - step-05 rm: 1 row per run.
- **The step-02 gzip row: no event in either run.** The agent recorded tar's archive events and tar's own `sh`, `gzip` and `tar -tzf` children. It recorded nothing for the rest of step-02: no `cat`, `gzip -9`, `gzip -t` or `ls`, and no file event for `ledger-all.csv`. A 10 s pause before the gzip did not change that.
- **What changed:** that row was removed (`d5994ad7`), leaving 5 XQL rows plus CR-EDR-0027.
- **CR-EDR-0027:** the primary XQL, including its ordering stage, and the fallback were both accepted as queries at 01:11. Each returned the chain's row: legs 2, events 8; the last archive start and the last SSH-client start were 90 s apart.
- **Built-in alert:** "A compressed file was exfiltrated over SSH" was not raised. It is hunt-only, because no SSH session opens.

**SIM-EDR-028 (sudoedit burst in the CVE-2021-3156 probe form).**

- **The run itself:** 60 `sudoedit -s /` starts in 60 s. All 60 were answered as a patched sudo (1.9.15p5).
- **What the sensor recorded:**
  - 28 of the 60: the first 28, one per second, 01:43:25.166 to 01:43:52.527.
  - Nothing after 01:43:52 arrived in any later upload, checked at 01:52 and again at 01:54:43.
  - The recorded image name is `sudoedit`, the parent is `timeout`, and all 28 share one `actor_causality_id`.
- **The floor of 20 held:** the two count rows and CR-EDR-0028 each need 20, and 28 were recorded.
- **XQL rows:** Tier-2 verify at 01:55:09 issued 4 queries, and all 4 rows passed.
- **CR-EDR-0028:** run as a query at 01:56, it returned `sudoedit_starts` 44. That is this run's 28 plus the killed run's starts.
- **Built-in alert:** "Sudoedit Brute force attempt" was not raised. It is hunt-only, because it needs a trained baseline.

### 3. Findings about the agent and SimCore

**F1. Telemetry arrives in batches about five minutes apart.**

- SIM-EDR-027 run 1's step-02 to step-05 events (00:54:15 to 00:56:15) all carry `insert_timestamp` 00:58:38.
- SIM-EDR-028's two uploads were 01:43:43.8 and 01:48:43.9.
- SimCore's Tier-2 verify, called 97 s after a run ended, wrote `fail` on rows whose events were not ingested yet, then refused a re-verify for 600 s.
- Rule now written into the scenarios: verify no sooner than six minutes after the run completes.

**F2. The agent stops recording part-way through a burst.**

- SIM-EDR-028 kept the first 28 of 60 identical process starts.
- SIM-EDR-027 kept tar's burst and then nothing for the rest of its step.
- The cause is not established.
- Every count-based row should keep a margin, and its first lab run should read the recorded count back.

**F3. One failed control poll kills a running step.**

- Run `328b3e45` was not aborted by anyone.
- At 01:40:53 another client's tenant XQL call through SimCore loaded the tenant credential. `CredentialStore.get_plaintext` bumps `last_accessed_at` and flushes without committing, so a SQLite write transaction stayed open through the 10 s outbound call.
- The beacon's 2-second `GET /api/runs/{id}/control` poll runs an `UPDATE agents SET last_seen`. That update hit `database is locked` and returned HTTP 500 (SimCore log ref `acf296e856f2`).
- The beacon (`agent/beacon/client.go`, `Control` → `controlLost`, `Abort: true`) treats any single failed poll as an abort. It killed step-02 with exit 130 and reported "aborted by operator".
- A re-run three minutes later, with no concurrent tenant call, completed.
- A fix for SimCore and the beacon is proposed as its own task; it is not on this branch.

**F4. The advisory runtime preflight reports tools as missing when they are present.**

- The beacon's interpreter roster knows only python, perl, ruby and node.
- So `runtime_dependency_gaps` on every run record listed `scp`/`ssh` (SIM-EDR-027) and `sudo`/`sudoedit` (SIM-EDR-028) as missing, although every step ran.
- It does not block a launch.

**F5. The correlation rules are accepted, but none is installed.**

- CR-EDR-0026, CR-EDR-0027 (primary and fallback) and CR-EDR-0028 were all accepted by the tenant as ordinary queries.
- Each returned its chain's row.
- None is installed, so none has run on a schedule and no case was opened.
- Issue suppression and the timestamp an aggregated correlation issue carries remain unobserved.

### 4. Not run

- **SIM-EDR-029** needs the `sshpass` package on the endpoint.
  - A one-step maintenance draft that installs it, `SIM-DRAFT-lab-maintenance-pov-target-01-install-sshpass-for-sim-edr-029-20261113`, is on the shared SimCore.
  - Its launch was refused by this session's permission classifier, so it was not run.
  - Its real sshpass burst has not been executed anywhere.

### 5. Lab steps that need Henry's approval

1. Install `sshpass` on `pov-target-01`:
   - either launch the maintenance draft above, or run `sudo apt-get install -y --no-install-recommends sshpass` on the VM;
   - do not run `sshpass` by hand afterwards.
2. Install CR-EDR-0026, CR-EDR-0027, CR-EDR-0028 and CR-EDR-0029 in the tenant from their cards:
   - severity Medium, schedule every 10 minutes, time frame 1 hour, no MITRE tag;
   - map `agent_hostname` to Host Name and `agent_id` to Agent ID;
   - set issue suppression where the card asks for it.
   This is a tenant write, so it is Henry's action.
3. Re-run SIM-EDR-026 to SIM-EDR-029 one at a time, at least 75 minutes apart per scenario. Verify each no sooner than six minutes after completion, then reconcile.
4. Re-run SIM-EDR-026 after 2026-11-24 to settle whether the built-in crond alert fires once the agent is past its 14-day activation period.

### 6. What this session changed on the shared lab

- Copied the eight scenario and card files into the worktree the shared SimCore mounts (`funny-bartik-cd5ed4`), as untracked files, and restarted the container three times.
- Undo: delete those eight files and restart the container.
- Created two drafts on the shared SimCore: the tool probe and the sshpass maintenance draft.
- Nothing was installed on the VM and nothing was written to the tenant.
