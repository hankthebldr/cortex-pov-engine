# OpenWork kickoff — paste as the first message (workspace: cortex-pov-engine)

You are doing an **extended, autonomous detection-authoring pass** in the
`cortex-pov-engine` repo. Read `scripts/detdev/TASK.md` in full and follow it exactly.

Your job: author Cortex **Analytics / ABIOC** (behavioral) detections for the
**Windows and Linux endpoint (XDR Agent)** analytics alerts in
`scripts/detdev/targets-windows-analytics.txt` and
`scripts/detdev/targets-linux-analytics.txt`, as new TTP cards + scenarios, using the
pipeline in `scripts/detdev/` (`genfrag.py` → set platform + analytics_modules →
`package.py` → validate).

Rules (full detail in TASK.md):
- Use the **gateway coder model** (`cpp-coder` via `http://localhost:4000`), NOT the
  opencode agent / cpp-offsec. 2–4 targets per batch, verify each batch.
- Every run: `python3 detection_scanner/scripts/export_artifacts.py` then
  `python3 detection_scanner/scripts/validate.py --quiet` must end **"0 fail"**.
  After each batch: `make check-refs` must pass, and bump the size guard in
  `tests/engine/test_corpus_refs_strict.py`.
- Honor the conventions in TASK.md (XQL `dataset=xdr_data` + `comp` aggregation;
  detection_id auto-slug; schema enums; safe-detonation doctrine).
- Windows exemplar: `TTP-2026-0002` + `edr-002`. Linux exemplar: `TTP-2026-0032` + `edr-001`.
- **Review-gated: NEVER push, NEVER open a PR.** Leave everything on disk.
- Keep a running log of which alerts you covered (card id, scenario id, validate result).

Start with the high-value behavioral detectors listed in TASK.md, then work down the
lists. Stop and report if validate can't reach 0 fail after a reasonable fix attempt.
