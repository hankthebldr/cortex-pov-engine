# Lab lessons learned: running detections and verifying results (2026-11-10 → 11-13)

## Executive summary

Four days of lab work on the v1.2.0 targets showed that most "Cortex missed it" readings were not detection gaps.
They came from the environment or from the way results were read back. Typical causes: a test host still running
an earlier scenario's leftovers, fast steps whose telemetry never left the host, data that arrived in a different
field or dataset than the query looked at, and a tenant that answers "forbidden" for a dataset that does not exist
yet. Each of these produces a confident wrong answer in a POV report if nobody checks. The lab now has a measured
picture of every ingestion route used in the POV (endpoint agent, XDR Collector, Broker VM syslog and the HTTP
collector), with the delay before each one's data can be read and the field where its test marker lands. The
recommended action: run the v1.2.0 test pass with the rules below, keep the instance's saved credentials and
connected components (Henry, 2026-11-13), and fix the three verification defects listed under "Open" before
anyone quotes a verified result, along with the two run-path defects found in the 2026-11-14 sweep: NDR
simulator steps that cannot run on a target, and a database lock that can make a live run read as abandoned.

## Technical detail

Sources: [`2026-11-13-lab-targets-readiness.md`](2026-11-13-lab-targets-readiness.md) (measured state of every
target and the v1.2.0 test plan), [`lab-asset-lifecycle.md`](lab-asset-lifecycle.md) (spin-up/down and the
four-layer error review), [`2026-11-12-ingestion-sources-bringup.md`](2026-11-12-ingestion-sources-bringup.md)
(the plan; readiness §6 corrects it), the 1.2.0 detection review, and the 2026-11-10/11 lab runs.

### 1. Before a run: the environment

| Lesson | Evidence | Rule |
|---|---|---|
| The beacon dials one address | 2026-11-11: every beacon `offline` after the Mac's DHCP address moved, with no error anywhere | Pin the Mac (192.168.1.105) and the VM leases; check `ipconfig getifaddr en0` against the beacon's `--server` before a pass |
| The beacon never runs `cleanup.commands` | SIM-CDR-003's root cron kept writing every minute from 2026-11-11 to 2026-11-13 22:28Z: 1,436 `sh` process events a day in `xdr_data`, all shaped like T1053.003 | Clean the host between scenarios that persist anything (readiness §3.1). Treat cron and persistence matches on `pov-target-01` from before 2026-11-13 22:28Z as suspect |
| Service accounts must be able to log in | 2026-11-02: SIM-EDR-001 step 1 failed in ~120 ms ("account is currently not available") because `www-data` had `nologin` | Provision the identities first. Done 2026-11-13: 12 accounts with bash, a home directory and `runuser` access, uids 1001–1010 (readiness §3.2) |
| A connected XDR agent can still be warming up | 2026-11-10: run exits 0, nothing in `xdr_data` for the first steps | Compare the first `xdr_data` event time with the run start. The `cned`/`piud` collectors read STOPPED while process, file and network events still arrive (readiness §2.2): STOPPED is not a blocker |
| The tenant needs the content | 2026-11-11: the first live alerts were the built-in Analytics BIOC on `useradd`/`chpasswd`. Zero custom BIOCs were installed | A scenario's BIOC and correlation expectations cannot fire until that content is imported and enabled in the tenant. Check before the pass, not after the miss |
| Only one test driver may use `:8888` | 2026-11-12: a Tier-D e2e run from another session landed on the live SimCore (run 1539, #215) | During a lab pass nothing else drives `:8888`. Check `GET /api/runs` for runs you did not launch |
| Clocks | 2026-10: negative or huge MTTD, matcher found nothing | Run the connector preflight clock stage first |

### 2. While running

- **Fast steps lose telemetry.** In 2026-11-10/11 runs, steps 3–5 of fast chains never reached `xdr_data`. Space steps ≥ 40 s, and check that each step has events before you judge its detection.
- **MTTD runs from launch to the alert.** It includes queue wait and the runtime of earlier steps.
- **Low severity never groups into a case.** A stitched step whose detections are all Low forms no incident (G-02 on SIM-EMAIL-003/004). Read "no case" as a grouping fact, not as a miss.
- **Delivered is not ingested.** A 2xx from a collector only means it accepted the body. Ingestion is proven by reading the record back.
- **EAL steps in the NDR scenarios cannot run on a beacon.** Eight NDR scenarios (001–007, 012) have beacon steps that run `python3 -m scripts.eal_simulator.cli …`. That package lives in the CortexSim repo, not on the target, so the step fails with `ModuleNotFoundError: No module named 'scripts'` (NDR-001/002/003/005/006 in the 2026-11-14 sweep on the 1.1.1 `:8888`). This is a scenario defect, not a miss. Until fixed, send those signals as SimCore EAL campaigns (in-process, #231) and do not score the NDR steps.
- **A locked database can end a run as abandoned.** SIM-NDR-007 (run `5cc8c6b4`) was marked `complete` with `BEACON ABANDONED TASK`. In the same minute, `GET /api/agents/{id}/tasks` and the heartbeat sweep threw `sqlite3.OperationalError: database is locked` 26 times (03:10Z, 2026-11-14). A Tier-2 verify of run `c8fc2bf6` was running then (4 XQL calls) and held its session open across the tenant calls. `core/database.py` sets neither WAL nor a busy timeout. The same error came in bursts all evening: 152 in the container log, 00:57–03:10Z. An "abandoned" verdict in such a minute is SimCore's own contention, not the beacon. Avoid running a verify while a run is in flight until fixed.

### 3. The ingestion routes, measured

| Route | Dataset | Where the test marker lands | Measured delay | Gotchas |
|---|---|---|---|---|
| XDR agent | `xdr_data` | process command line | seconds | see §1 and §2 |
| XDR Collector (Filebeat profile) | `cortexsim_door_xdrc_raw` | the probe line in the raw log | ~80 s | The profile also ships `/var/log/syslog` and `auth.log` (63k rows) into the same dataset, so trim it. Ubuntu 24.04 is not on the vendor's supported list |
| Broker VM `bvm-01`, syslog applet | `cortexsim_door_syslog_raw` | CEF `cs1` (not a top-level field) | 5–8 s, one TCP row 80 s | UDP 514 and TCP 5514 both work. A CEF line cut after the signatureId arrives raw only. A sender can back-date `_time`. The source network is not restricted (wrong-source controls landed; Henry's call). The tenant also has an unknown broker `5X7S0G08`, so filter on `_broker_device_name` |
| HTTP collector `pov-engine-test` | `custom_custom_raw` (= `<vendor>_<product>_raw`, CUSTOM/custom) | top-level `cortexsim_canary` | not timed; read after a few minutes | **gzip only**: an uncompressed body gets 400, so SimCore's preflight read `collector_rejected` even when campaigns landed (fixed by #294, the target's `compress`). The collector honours a body `_time`. The key goes in `Authorization` with no `Bearer`. The key was pasted into a chat: rotate it |

### 4. Verifying results

1. **A new dataset answers 403, not zero rows.** XQL against a dataset that has not received its first record returned HTTP 403 through SimCore (XDR Collector, before its first row). Do not read a 403 on a new route as an auth failure. The HTTP collector arrival check from #263 maps a 403 to `TENANT_UNAUTHORIZED`, which is the wrong diagnosis on a brand-new collector: see Open.
2. **Filter on arrival, not event time.** Collectors honour a sender's `_time`, so a record can land days in the past. Use `_insert_time >= <send time>`, or a window that reaches back past every `_time` sent.
3. **Each route puts the marker somewhere else.** Top-level `cortexsim_canary` (HTTP collector JSON), CEF `cs1` (syslog), the raw probe line (XDR Collector), the process command line (agent). One query shape per route.
4. **Wait before you call it absent.** One heartbeat plus ingest is 3–5 minutes. A miss before then is "not yet".
5. **Corpus queries that cannot run on a tenant.** `to_timestamp(current_time() - N)` in 178 of 199 cards (dev, 2026-11-13) gives tenant HTTP 500. Bare `causality_id` (not an `xdr_data` field) in 38 cards. Tier-2 verify and the pasteable query library error on those. They resolve `pending`, never `fail`, but they prove nothing until fixed.
6. **XQL rows never raise Detection Accuracy.** The matcher maps XQL rows to the correlation family, so 47 scenarios set accuracy bars their own rows cannot reach (#272; four fixed in #273).
7. **Read results through the instance's own API.** The stdio MCP server (`scripts/mcp-stdio-docker.sh` → `docker exec -i <simcore> python -m mcp_server`) calls SimCore's REST API for runs, results, reconcile and verify. It needs no token and no `/mcp` mount (Henry, 2026-11-13: MCP is for API calls that verify).
8. **Every read-back costs tenant quota.** Each XQL start charges the per-tenant ledger. A local refusal by SimCore's own budget is not a tenant answer and is not charged (#251).

### 5. The instance and its credentials

- **Credentials live in the instance.** The tenant key, collector keys and enrollments are in SimCore's vault, encrypted with `.env`'s `CORTEXSIM_SECRET`. Back up the DB and `.env` together: one without the other cannot be opened. There is no 1Password on this Mac. Each POV starts from a blank engine. For this lab, Henry keeps the saved credentials and connected components across the v1.2.0 rebuild (backup `~/.dotfiles-backups/20261113-211255/simcore/`: DB with integrity ok, `.env`, non-secret inventory).
- **Tag the image you run, by ID.** On 2026-11-13 the `1.1.1` tag was rebuilt while `:8888` still ran the older image, so a rollback tagged by name would have saved the wrong image.
- **Settings are read at boot.** `CORTEXSIM_DOOR_PROBE_TENANTS`, `CORTEXSIM_PUBLIC_URLS` and `CORTEXSIM_ALLOWED_HOSTS` must be in `.env` before the restart. Compose passes them through. It does not pass `CORTEXSIM_MCP_ALLOWED_HOSTS`, so `/mcp` would accept loopback only.

### 6. Open

1. **Arrival 403 mapping** (#263): read HTTP 403 on the arrival query as "the dataset may not have received its first record yet, or the key's role cannot read it", not as `TENANT_UNAUTHORIZED`.
2. **Corpus XQL faults**: `to_timestamp(current_time() - N)` (178 cards) and bare `causality_id` (38 cards). No issue filed yet.
3. **Accuracy denominator**: take verify-only rows out of the Detection Accuracy denominator (#272).
4. **NDR EAL steps** run `python3 -m scripts.eal_simulator.cli` on the target, where the package does not exist (8 scenarios). Either stage the CLI with the step or move the step to a SimCore campaign.
5. **SQLite contention**: enable WAL and a busy timeout in `core/database.py`, and commit before outbound tenant calls (`verify_run_now` holds its session across them), so a verify cannot lock out `/tasks` and the heartbeat sweep.
6. Rotate the `pov-engine-test` key, then declare it on the rebuilt `:8888` with `compress: true` (readiness §6, step 1).
7. Confirm whose broker `5X7S0G08` is.
