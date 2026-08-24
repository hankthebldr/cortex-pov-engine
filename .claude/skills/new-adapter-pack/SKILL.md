---
name: new-adapter-pack
description: Scaffold a new CortexSim tool-adapter pack (tools/packs/TOOL-*.yml) and wire it into a scenario — picks the right integration tier, writes a schema-valid pack, validates it against the real loader on the host, runs the tier-2 source preflight, and replaces hand-rolled CLI in a scenario with adapter_ref. Use when adding a security tool the engine should drive, or when closing adapter-wiring gaps. Peer of new-scenario and new-ttp-card.
disable-model-invocation: true
---

# new-adapter-pack

Add a security tool to the declarative adapter framework. An adapter pack tells
the engine where a tool lives, how to install and invoke it, its dual-use safety
class, and which Cortex plane its signal lands on — so scenarios reference
`adapter_ref: TOOL-X` instead of hand-rolling CLI strings.

Two halves, and the second is the one that usually gets skipped:

1. **Author the pack** — `tools/packs/<tool>.yml`
2. **Wire it into a scenario** — replace `external_tools[]` hand-rolled CLI with
   `adapter_ref`. 70 packs ship; only ~35 scenarios reference one (GAP-ADAPT-02).
   An unwired pack is inert.

Do both unless the user explicitly wants only the pack.

## Inputs to gather (ask only for what's missing)

- **Tool** — name + upstream repo URL.
- **Tier** — the single decision that determines everything else (see below).
- **Category** — `adversary-simulation · c2-framework · sandbox ·
  reverse-engineering · network-scan · web-app · identity-credential ·
  cloud-container · social-engineering · wireless-iot · analyst-workbench`
- **Safety class** — `safe · dual-use-lab-only · c2-framework · destructive`
- **Plane(s) + techniques** — which Cortex plane the signal lands on.
- **Target scenario** — which scenario should reference it (the wiring half).

## Picking the tier

Get this wrong and the install block is the wrong shape entirely.

| Tier | Meaning | Install block | Push-bundle behaviour |
|---|---|---|---|
| 1 | in-tree, we own the source (`sources/cortex-*`) | `source_path` + `build_cmd` + `binary` | assumed present |
| 2 | git submodule, OSS pinned (`sources/<tool>`) | same shape | assumed present |
| 3 | IaC-provisioned on the target VM | `iac_module` + `content_library_entry` | assumed present |
| 4 | runtime-fetched at dispatch | `runtime_install_command` + `binary` | **self-installs** |
| 5 | external-only, reference | may be empty | never invoked |

Tier 4 is the default for anything installable with one command. Reach for tier 2
only if the tool is already a submodule under `sources/` — adding a new submodule
is a separate, heavier decision.

## Steps

1. **Check it does not already exist.** 70 packs ship; near-duplicates are the
   most common waste.
   ```bash
   ls tools/packs/ | sed 's/\.yml$//'
   grep -rl "adapter_id:.*<TOOL>" tools/packs/
   ```
   If a close equivalent exists, prefer adding `equivalents:` to it over a new pack.

2. **Read the schema and a same-tier reference.** `tools/packs/_schema.yml`
   documents every field; `tools/packs/nmap.yml` is the tier-4 reference. Read one
   existing pack **of the tier you chose** — the install block differs per tier and
   copying the wrong one is the usual failure.

3. **Write `tools/packs/<tool>.yml`.** `adapter_id` must match `^TOOL-[A-Z0-9-]+$`
   and is never reused. Required: `adapter_id · name · version · tier · category ·
   upstream{repo,license,attribution} · cortex_signal{planes} · safety_class`.
   ```yaml
   adapter_id: TOOL-<NAME>
   name: <Display Name>
   version: "<pinned upstream version>"

   tier: 4
   category: <controlled vocabulary above>

   upstream:
     repo: https://github.com/<org>/<repo>
     license: <SPDX-ish string — CI fails on "unknown", look it up>
     attribution: <project owner; surfaced in customer-facing POV reports>

   cortex_signal:
     planes: [<PLANE>]
     expected_techniques: [T1234]

   safety_class: safe

   install:
     runtime_install_command: "command -v <bin> >/dev/null 2>&1 || apt-get install -y <pkg>"
     binary: <bin>

   invoke:
     target_platform: linux
     run_template: "{binary} {flags} {target}"
     default_args:
       flags: "<default flags>"
       target: "127.0.0.1"
     identity_required: root

   cleanup:
     commands:
       - "rm -f /tmp/<tool>-cortexsim-*"

   ttp_refs: []          # optional; dangling refs warn at load, never fail
   equivalents: []       # other adapters producing overlapping signal

   author: Henry Reed
   created: "<YYYY-MM-DD>"
   last_updated: "<YYYY-MM-DD>"
   tags: [<discovery|exploit|...>]
   ```
   **No wrapper code.** `run_template` is a shlex-safe format string passed to the
   real binary with its native flags — the engine is a process manager, not a
   translation layer. Every placeholder needs a `default_args` entry.

   **`cleanup.commands` is mandatory** when `safety_class: destructive`.

4. **Validate against the real loader** — runs on the host, no Docker needed:
   ```bash
   cd core && python3 - <<'PY'
   import sys, yaml, pathlib; sys.path.insert(0, ".")
   from tools.adapter_loader import ToolAdapterSchema
   bad = 0
   for p in sorted(pathlib.Path("../tools/packs").glob("*.yml")):
       if p.name == "_schema.yml": continue
       try: ToolAdapterSchema(**yaml.safe_load(p.read_text()))
       except Exception as e: bad += 1; print("FAIL", p.name, str(e).splitlines()[0])
   print(f"{bad} invalid")
   PY
   ```
   Then the CI adapter gate:
   ```bash
   CORTEXSIM_BASE_DIR=$(pwd) scripts/check-adapter-sources.sh
   ```
   Tier-2 sources **must exist on disk** (FAIL, GAP-ADAPT-01); tier-4 misses WARN.
   If a tier-2 source_path is missing, check the gitlink actually exists before
   reaching for `git submodule update` — an orphaned entry with no gitlink makes
   that command a silent no-op.

5. **Wire it into a scenario** (the half that matters). In the target scenario's
   `external_tools[]`, add `adapter_ref: TOOL-<NAME>` alongside the entry, and
   replace hand-rolled CLI in `steps[].command` with the adapter placeholder so the
   orchestrator resolves it (`_resolve_adapter_placeholders`). Confirm the step's
   `identity` is compatible with the pack's `invoke.identity_required`.
   ```bash
   grep -rn "adapter_ref" scenarios/<plane>/ | head    # local convention
   grep -rc "adapter_ref" scenarios/ --include=*.yml | grep -v ':0' | wc -l
   ```

6. **Gated adapters need consent.** `dual-use-lab-only` and `c2-framework` packs
   refuse to launch without `consent.simulation_authorized` / `c2_authorized`.
   Confirm the scenario declares it, and that push bundles WARN-and-skip rather
   than staging a C2 implant.

7. **Validate the corpus and review.** `make validate` for the full gate. Offer
   the `detection-corpus-reviewer` subagent for the scenario wiring, and
   `push-bundle-verifier` when the pack is tier 4 (it must self-install on a clean
   Ubuntu 22.04 host).

8. **Refresh the counts** if the pack count moved — the `refresh-inventory` skill
   reconciles CLAUDE.md and `docs/reference/`. Commit the pack and the scenario
   wiring on their own boundaries (named-file `git add`, no `-A`).

Report the created `adapter_id`, its tier, the validator verdict, the adapter
source preflight result, and which scenario now references it.
