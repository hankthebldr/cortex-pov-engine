// CortexSim — Finalization slice workflow (PREPARED, not yet run).
//
// Authored 2026-07-11 alongside docs/reference/finalization-implementation-plan.md.
// Implements the cleanly-parallelizable NOW slice: the two false-green tests
// (B1/B2), the cross-projection parity test (B3), the temporal causality edge
// (C1), and generates apply-ready patches for the contained UX wins (A2/A3/A5).
//
// HOW TO RUN (once the Anthropic usage limit has reset):
//   Workflow({ scriptPath: "docs/workflows/finalize-slice.workflow.js" })
//
// Design: every agent creates NEW files only OR returns an integration_patch for
// existing-file edits (no two agents touch the same file). A final review runs
// the gates. After it returns, the driver applies patches + commits per the plan.

export const meta = {
  name: 'finalize-slice',
  description: 'CortexSim finalization NOW slice — kill false-greens, add temporal causality edge, patch contained UX wins',
  phases: [
    { title: 'Build', detail: 'Tests (B1/B2/B3), temporal edge (C1), UX patches (A2/A3/A5) — disjoint agents' },
    { title: 'Review', detail: 'Run gates + static review + ordered integration todo' },
  ],
}

const ROOT = '/home/henry/Github/cortex-pov-engine'

const IMPL_SCHEMA = {
  type: 'object',
  properties: {
    task: { type: 'string' },
    files_written: { type: 'array', items: { type: 'string' } },
    integration_patch: { type: 'string' },
    summary: { type: 'string' },
    how_to_verify: { type: 'string' },
  },
  required: ['task', 'summary'],
}

const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    gate_results: { type: 'string' },
    findings: { type: 'array', items: { type: 'object', properties: {
      file: { type: 'string' }, issue: { type: 'string' }, fix: { type: 'string' },
    }, required: ['file', 'issue'] } },
    integration_todo: { type: 'array', items: { type: 'string' } },
    verdict: { type: 'string' },
  },
  required: ['integration_todo', 'verdict'],
}

const CTX = `CortexSim (repo root ${ROOT}), Cortex detection-simulation engine. Read docs/reference/finalization-implementation-plan.md and docs/reference/detection-engine-gap-analysis.md FIRST. Invariants: storyline/scorecard/causality are pure projections that delegate coverage/MTTD to detection_storyline.build_summary (never recompute); new API routers mirror core/api/storyline.py; UI normalizes at the client edge (ui/src/api/*.js); tests must not stub their own subject. Match repo conventions (async SQLAlchemy, Pydantic v2, plain-CSS React 18, Cortex tokens). Create NEW files only; for existing-file edits return a precise integration_patch instead. Cite file:line.`

phase('Build')
const impls = (await parallel([
  () => agent(`${CTX}\n\nTASK B1 (TC-01): kill the storyline false-green. tests/api/test_storyline_api.py stubs the real builder. Add a NEW non-stubbed integration test file tests/api/test_storyline_integration.py: seed a Run+Scenario+Results (mirror the seeded_run fixture), hit GET /api/runs/{id}/storyline WITHOUT stubbing engine.detection_storyline, and assert entries[]/coverage_pct come from the real build_storyline. files_written = the new test.`, { label: 'B1:storyline-integration-test', phase: 'Build', schema: IMPL_SCHEMA, agentType: 'general-purpose', effort: 'high' }),
  () => agent(`${CTX}\n\nTASK B2 (TC-02): test the scorecard report branch. core/api/runs.py serves ?format=scorecard and ?format=scorecard-html with zero tests. Add a NEW test file tests/api/test_scorecard_report.py over a seeded run asserting: 200; Content-Type text/plain for scorecard and text/html for scorecard-html; coverage numbers match the seeded Results. files_written = the new test.`, { label: 'B2:scorecard-report-test', phase: 'Build', schema: IMPL_SCHEMA, agentType: 'general-purpose', effort: 'high' }),
  () => agent(`${CTX}\n\nTASK B3 (TC-06): cross-projection parity test. Add a NEW test file tests/engine/test_status_parity.py with a parametrized test feeding one shared row-set (detected / reviewed-unfired=observed_at-without-observed / pending) through report_generator, efficacy_scorecard._status, and detection_storyline._entry_status, asserting equal detected/missed/pending counts. files_written = the new test.`, { label: 'B3:status-parity-test', phase: 'Build', schema: IMPL_SCHEMA, agentType: 'general-purpose', effort: 'high' }),
  () => agent(`${CTX}\n\nTASK C1 (CG-04): temporal causality edge. Edit ONLY core/engine/causality_graph.py: after nodes are built, compute pairwise observed_at deltas between detected nodes and emit 'temporal' edges where delta <= scenario.correlation_window_seconds (read that field from scenario_meta; the field is currently unused). Add unit cases to tests/engine/test_causality_graph.py (new test functions only). Keep it pure. If a clean edit to the existing file is not possible without conflict, return the exact code block as integration_patch instead. files_written = whatever you add; integration_patch for the causality_graph.py insertion.`, { label: 'C1:temporal-edge', phase: 'Build', schema: IMPL_SCHEMA, agentType: 'general-purpose', effort: 'high' }),
  () => agent(`${CTX}\n\nTASK A2/A3 (UX, patch-only — do NOT edit files, RETURN integration_patch): A2 — add an "Executive scorecard (.md / .html)" entry to ui/src/components/console/ExportMenu.jsx calling GET /api/runs/{id}/report?format=scorecard[-html], plus a small client wrapper. A3 — replace the static PLANE_META in ui/src/AppConsole.jsx with a list derived from the distinct planes of the loaded scenario list so CSPM/ASM/TIM/EMAIL become reachable. Read both files, return precise integration_patch blocks (file + before/after) for a human to apply. files_written = []`, { label: 'A2A3:ux-patches', phase: 'Build', schema: IMPL_SCHEMA, agentType: 'general-purpose', effort: 'high' }),
  () => agent(`${CTX}\n\nTASK A5 (UX bundle, patch-only — RETURN integration_patch): (1) thread the discarded detection arg: ui/src/AppConsole.jsx onOpenEvidence handlers (~:539,:549) currently drop the detection; pass it and pre-select the row in ui/src/components/console/EvidenceView.jsx (~:152 drawer). (2) sensor-health pill AppConsole.jsx:~122 is fabricated {xdr:'healthy',...} — render muted/'unknown' until real /healthcheck data. (3) fix empty-state copy EvidenceView.jsx:~69 + InflightView.jsx:~144 ("Operations tab" → real step names). (4) reassign the duplicate 'G T' chord (AppConsole.jsx:~294 and :~357). Return precise integration_patch blocks. files_written = []`, { label: 'A5:ux-polish-patches', phase: 'Build', schema: IMPL_SCHEMA, agentType: 'general-purpose', effort: 'high' }),
])).filter(Boolean)

phase('Review')
const newFiles = impls.flatMap((i) => i.files_written || [])
const patches = impls.map((i) => ({ task: i.task, patch: i.integration_patch || '' }))
const review = await agent(`${CTX}\n\nReview the finalization slice. NEW FILES: ${JSON.stringify(newFiles)}. PATCHES: ${JSON.stringify(patches)}.\n(1) py_compile each new .py (host lacks app deps — flag syntax/logic only). (2) run make validate. (3) confirm the new tests do NOT stub their own subject (B1 must hit the real builder). (4) check the temporal-edge change stays pure and delegates counts. (5) integration_todo = ordered existing-file edits to apply (dedupe the UX patches). verdict = readiness.`, { label: 'review:gate', phase: 'Review', schema: REVIEW_SCHEMA, agentType: 'general-purpose', effort: 'high' })

return { files_created: newFiles, patches, integration_todo: review.integration_todo, findings: review.findings, verdict: review.verdict }
