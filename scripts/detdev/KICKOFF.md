# OpenWork kickoff — paste as the first message (workspace: cortex-pov-engine)

You are doing an **extended, autonomous detection-drafting pass** in the
`cortex-pov-engine` repo. Read `scripts/detdev/TASK.md` in full and follow it exactly.

Your job: draft Cortex **Analytics / ABIOC** (behavioral) detections for the
**Windows and Linux endpoint (XDR Agent)** analytics alerts in
`scripts/detdev/targets-windows-analytics.txt` and
`scripts/detdev/targets-linux-analytics.txt`, as **staged draft** TTP cards +
scenarios under `drafts/detection-library/`, using the pipeline in `scripts/detdev/`
(`genfrag.py --platform … --alert …` → `package.py --apply` → `manifest.py --apply`).

Rules (full detail in TASK.md):
- Use the **gateway coder model** (`cpp-coder`), NOT the opencode agent / cpp-offsec.
  2–4 targets per batch, check each batch in `drafts/detection-library/INDEX.md`.
- **Drafts only.** Never write into `scenarios/` or `detection_scanner/ttps/`, never
  assign a real `SIM-*` / `TTP-2026-*` id, never bump a corpus pin or regenerate an
  export. Promotion is a separate, reviewed step.
- Always pass `--alert "<exact name from the target list>"`; never type a reference
  URL or ask the model for a hash.
- Honor the conventions in TASK.md (XQL `dataset = xdr_data` + `comp`; real commands
  in the scratch directory; TEST-NET-1 / `.invalid` destinations only).
- After each batch: `python3 scripts/detdev/manifest.py --apply`, then
  `python -m pytest tests/scripts/test_detdev_library.py` must pass.
- **Review-gated: NEVER push, NEVER open a PR.** Leave everything on disk.

Start with the high-value behavioral detectors listed in TASK.md, then work down the
lists. A finding in INDEX.md means regenerate the fragment, not hand-edit the pair.
