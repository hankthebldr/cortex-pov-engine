# Run CortexSim alongside the SOC Framework

**Applies to:** an XSIAM POV tenant that runs the Palo-Cortex SOC Framework
(`soc-optimization-unified`), which CortexSim pins at 3.31.4 (secops-framework
commit `d222ba87`).
**Status:** authored, not run (the lab run of step 5 on `pov-target-01` is owed: ledger at T+0 expects `ONE_CASE`/`open`, at T+7 h `socfw_auto_triage`/sealed). `tenant-verified: 0`.
**Design:** `docs/superpowers/specs/2026-11-11-socfw-coexistence-design.md` §7.

CortexSim only reads the tenant. It never changes the framework, and it never
stars, reopens or closes a case for you. Every tenant-side step below is yours
to run.

## Steps

1. **Install the framework with povkit, in `pov` mode.**
   `povkit install <name> --apply` installs the packs and their instances.
   povkit does not choose a mode. Set it in the tenant's playground with
   `!SOCFWConfig command=mode mode=pov apply=yes`. Without `apply=yes`,
   SOCFWConfig only shows what it would change.

2. **Run CortexSim preflight.** Run
   `POST /api/connectors/xsiam_tenant/preflight`, or the Preflight button on
   Readiness. Read the `response_layer` stage: it reports the framework's state
   (`ABSENT`, `PRESENT_UNREAD`, `PRESENT` or `UNKNOWN`), its mode and its Auto
   Triage settings, and says which values it read and which it assumed. The
   result is stored for 24 hours. The SOC Framework row on Readiness and the
   launch advisories read the stored result. `ABSENT` degrades preflight,
   because every POV tenant is expected to run the framework.

3. **Required: protect the POV's cases from Auto Triage.** Auto Triage closes
   New, unstarred cases that are older than its window and score at or below
   its threshold. It closes them as `resolved_other`, and XSIAM never reopens
   them, so a later alert from the same chain opens a new case. Any one of
   these is enough:
   - star the POV's cases (Auto Triage skips starred cases);
   - run `!SOCFWConfig command=triage window_hours=<n> apply=yes`, with `<n>`
     longer than the POV, because Auto Triage never closes a case younger than
     the window;
   - switch the Auto Triage JOB off for the POV window with
     `!SOCFWConfig command=job job="Auto Triage V3" value=off apply=yes`, and back
     on afterwards with
     `!SOCFWConfig command=job job="Auto Triage V3" value=on apply=yes`.

   A launch whose case Auto Triage may seal carries the `SOCFW_TRIAGE_MAY_SEAL`
   advisory, at `warn` level on a paired run and `info` level for a scenario
   with ABIOC or Analytics detections. The advisory points here, and it never
   blocks the launch.

4. **Run the scenario.**

5. **Read the run's case and its disposition.** After the run is reconciled
   (`POST /api/runs/{id}/reconcile`), open the run view: the **Case** panel
   shows the run's `merge_class`, each case's status, who closed it and
   whether it is sealed. The same data is the `## Case` section of the
   markdown report and `case_ledger` in the json report
   (`GET /api/runs/{id}/report`); a paired run also shows a pair-level
   `merge` on `GET /api/runs/{id}/pair`.
   - `merge_class` is one of `ONE_CASE`, `SPLIT_SEALED` (a case was sealed by
     the response layer before the chain finished), `SPLIT_NATIVE` (the
     platform split the chain on its own), `LOW_ONLY_NO_CASE`, `NO_CASE_YET`
     or `PENDING` (a case could not be read; never `ONE_CASE`).
   - Disposition (`open`, `socfw_auto_triage`, `auto_resolved`,
     `other_resolved`; an issue can also be `socfw_ai_close` or `unread`)
     drifts after the run: Auto Triage seals cases about 6 h later. Re-read it
     with `POST /api/runs/{id}/case-ledger/refresh` (cases only, read-only,
     409 `NO_CASE_LEDGER` before the first reconcile). With
     `CORTEXSIM_AUTO_RECONCILE` on, the auto-reconcile loop also refreshes
     recent ledgers (`CORTEXSIM_CASE_LEDGER_WINDOW_HOURS` 24,
     `..._MAX_RUNS_PER_SWEEP` 20, `..._MIN_INTERVAL_MINUTES` 60; terminal
     ledgers are skipped).
   - Tenant disposition is shown for context. It is not detection evidence.
     It never changes `detected`, `missed`, MTTD or a detection verdict. The
     one effect: a `SPLIT_SEALED` run withholds a Cross-Source Correlation
     Rate FAIL as `pending` (a PASS is never touched), because that split is
     not the detection engine's.
   - A firewall-to-endpoint split on `SPLIT_NATIVE` carries a bridge
     diagnosis (`BRIDGED`, `UNBRIDGED_IP_HOST`, `BRIDGE_UNREAD`). Its
     endpoint reply fields come from the repo's transcription of the
     endpoints API (`docs/reference/xsiam-platform-apis/05-endpoints-api.md`)
     and are unverified against a live tenant.

## What `pov` mode does to an attack

In `pov` mode, the framework's AI may close an attack's issue live. CortexSim
reports this but doesn't prevent it.

`tenant-verified: 0`
