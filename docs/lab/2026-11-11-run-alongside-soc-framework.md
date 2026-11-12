# Run CortexSim alongside the SOC Framework

**Applies to:** an XSIAM POV tenant that runs the Palo-Cortex SOC Framework
(`soc-optimization-unified`), which CortexSim pins at 3.31.4 (secops-framework
commit `d222ba87`).
**Status:** authored, not run. `tenant-verified: 0`.
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

5. **Read the run's case and its disposition.** The run view's Case panel
   arrives with Part B (the run case ledger). Until then, read the case in the
   tenant. Tenant disposition is shown for context. It is not detection
   evidence.

## What `pov` mode does to an attack

In `pov` mode, the framework's AI may close an attack's issue live. CortexSim
reports this but doesn't prevent it.

`tenant-verified: 0`
