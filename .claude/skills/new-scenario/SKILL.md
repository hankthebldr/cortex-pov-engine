---
name: new-scenario
description: Scaffold a new CortexSim scenario YAML (SIM-{PLANE}-{NNN}) from the schema — computes the next free ID for the plane, writes a schema-complete stub with UC/TC + MITRE + identity + ordered steps, and validates it loads. Use when creating a new detection scenario.
disable-model-invocation: true
---

# new-scenario

Scaffold a new CortexSim scenario YAML that loads cleanly on the first try.
Scenarios are the YAML source-of-truth under `scenarios/{plane}/`; the boot-time
loader rejects anything that fails `scenarios/_schema.yml`, so the goal here is a
**schema-complete, ref-valid** stub.

## Inputs to gather (ask only for what's missing from the invocation)

- **Plane** — one of: `edr cdr ndr itdr cloud_app analytics ai_access airs ai_spm
  browser koi asm cspm tim email` (dir names; `ANALYTICS` scenarios live in
  `scenarios/multi_plane/`). Uppercase form goes in the YAML `plane:` field.
- **Scenario name** — short human title.
- **Behavior / kill-chain** — what the steps do, and the MITRE technique(s).
- **UC/TC** — use-case + test-case this validates (`UCS-{PLANE}-{NN}` / `TC-{PLANE}-{NN}`).

## Steps

1. **Read the schema** `scenarios/_schema.yml` for the authoritative field list
   and the `detection_id` slug rules. Read one existing scenario in the target
   plane (e.g. `ls scenarios/<plane>/`) to match local convention.

2. **Compute the next ID.** Find the highest existing number for the plane and
   add one, zero-padded to 3 digits:
   ```bash
   ls scenarios/<plane>/ | grep -oE 'SIM-[A-Z]+-[0-9]+|[a-z]+-[0-9]+' ; \
   grep -rhoE 'scenario_id:\s*"?SIM-[A-Z]+-[0-9]+' scenarios/<plane>/ | sort -V | tail -1
   ```
   Never reuse an ID, even a deprecated one.

3. **Write the file** `scenarios/<plane>/<plane>-<NNN>-<slug>.yml` with ALL
   required fields (see schema). Skeleton:
   ```yaml
   scenario_id: SIM-<PLANE>-<NNN>
   name: "<name>"
   version: "1.0"
   status: active

   plane: <PLANE>
   detection_types: [<subset of BIOC|XQL|Analytics|Correlation|IOC|ABIOC>]

   uc_ref: UCS-<PLANE>-<NN>
   tc_ref: TC-<PLANE>-<NN>
   uc_name: "<use case name>"
   tc_name: "<test case name>"

   mitre_tactic: "TA00XX"
   mitre_tactic_name: "<tactic>"
   mitre_technique: "TXXXX[.XXX]"
   mitre_technique_name: "<technique>"

   threat_report: "<Unit42 / vendor report title>"      # convention
   threat_report_url: "https://..."                      # convention

   execution_identity:
     default: <identity>
     options: [<identity>, ...]

   push_supported: true
   pull_supported: true

   external_tools:            # optional — prefer adapter_ref to hand-rolled CLI
     - name: <tool>
       source: "https://..."
       type: script            # script | binary | service
       install_inline: false
       adapter_ref: TOOL-<...>  # must resolve to a tools/packs/*.yml id

   steps:
     - id: step-01
       name: "<step name>"
       command: "<shell — verbatim, harness wraps identity>"
       identity: <one of execution_identity.options>
       mitre_technique: "TXXXX"
       expected_detections:
         - plane: <PLANE>
           type: <BIOC|XQL|Analytics|Correlation|IOC|ABIOC>
           description: "<expected alert>"
           ttp_ref: "TTP-2026-XXXX"          # must be a real card id
           detection_id: "<slug resolving inside that card>"

   cleanup:
     - "<reverse side effects>"
   ```

4. **Resolve every reference you set.** `ttp_ref` must be a real `id` in
   `detection_scanner/ttps/*.json`; `detection_id` must resolve per the slug
   rules in the schema; `adapter_ref` must be an `id` in `tools/packs/*.yml`.
   Grep to confirm — dangling refs are corpus violations. If no card matches yet,
   either author the card first or omit the ref (both are optional fields).

5. **Validate it loads.** Run the detection validator and confirm no new failure:
   ```bash
   python3 detection_scanner/scripts/validate.py --quiet
   ```
   For a full boot-level check, `make validate`.

6. **Offer to review** with the `detection-corpus-reviewer` subagent, and to
   commit the new scenario on its own boundary (named-file `git add`, no `-A`).

Report the created path, the assigned `SIM-{PLANE}-{NNN}` ID, and the validator
verdict.
