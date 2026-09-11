import React, { useCallback, useEffect, useMemo, useState } from 'react'
// Colocated with the component so it ships inside the Composer's own lazy
// chunk rather than on first paint — the convention main.jsx documents for
// every destination stylesheet.
import '../../styles/destinations/composer.css'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import {
  getScenario,
  createDraft,
  updateDraft,
  listDrafts,
  getDraft,
  getRunCausality,
  getTtps,
  getToolAdapters,
} from '../../api/client.js'
import { agentIdOf, runIdOf } from '../../api/ids.js'
import { isRunTerminal } from './runStatus.js'
import useShelf from './useShelf.js'
import useLaunchScenario from './useLaunchScenario.js'
import { HS } from './readiness/healthModel.js'
import { causalityStepStates } from './composerLayout.js'
import ComposerCanvas from './ComposerCanvas.jsx'
import ComposerInspector from './ComposerInspector.jsx'
import ComposerPalette from './ComposerPalette.jsx'
import {
  addDetection,
  appendStep,
  bindTtpDetection,
  blankStep,
  clearLayout,
  DETECTION_TYPES,
  draftFromApi,
  draftFromScenario,
  draftSnapshot,
  draftToApi,
  duplicateStep,
  editStep,
  emitDraftYaml,
  emptyDraft,
  isDraftDirty,
  moveStep,
  nextStepId,
  PIVOTS,
  PLANES,
  removeDetection,
  removeStep,
  setCausalityParent,
  setNodePosition,
  setStepChannel,
  setStepEal,
  setStepTarget,
  validateDraft,
} from './composerDraft.js'
import { topologicallySortSteps } from './composerSpine.js'
import { setEntity, stitchInsertToken } from './stitchContext.js'

/**
 * ComposerView — the Simulation Composer's slim wiring layer.
 *
 * THE GAP THIS CLOSES
 * -------------------
 * The console could browse scenarios, launch them, and prove what they
 * detected — but it had no surface on which a chain was BUILT. A DC asked to
 * prove a technique the library does not cover had nowhere to go. The Composer
 * is that surface: build a chain, SAVE it as a `status='draft'` Scenario row,
 * and — once it is tc-bound and chain-valid — launch it through the existing
 * run path.
 *
 * FOUR REGIONS, one job each (each now its own component):
 *   palette    (`ComposerPalette`)   — what you can add (steps, library, TTP
 *                                       cards, tools, targets, payloads)
 *   canvas     (`ComposerCanvas`)     — the chain itself, Design and Run lenses
 *   inspector  (`ComposerInspector`)  — the configuration of the ONE selected
 *                                       step (or the workflow meta)
 *   workstream (this file)            — what the chain needs to actually run
 *                                       (payload · preflight · active · history)
 *
 * WHAT IT REFUSES TO INVENT (Gate A5)
 * -----------------------------------
 * The draft is seeded from a scenario the API returned (`?from=SIM-…`), never a
 * built-in demo chain. Launch runs the SAVED row, not the canvas — so an edited
 * chain must be saved first, and the button says so; a saved-but-UNBOUND draft
 * is refused with the server's own tc-bound reason rather than posted. The Run
 * lens renders only the real causality graph — a stitch outside the window
 * shows BROKEN, never a fabricated CONFIRMED (`causalityStepStates` enforces
 * this). Nothing here invents throughput or timing.
 *
 * WHY THE INSPECTOR IS RENDERED CONDITIONALLY
 * -------------------------------------------
 * `ComposerInspector` is editable, so its plane pickers render a real
 * `<option>EDR</option>`. The preserved suite asserts `getByText('EDR')`
 * resolves to exactly one node — the Proves-bar plane chip — at mount with no
 * step selected. So we render the full inspector only once a step is selected
 * (or the DC opens workflow-meta explicitly); with nothing selected the column
 * shows a plain placeholder that carries the scenario teardown but no plane
 * `<select>`. The prove chip stays the unique 'EDR', and the inspector's own
 * suite (`ComposerInspector.test.jsx`) still exercises every editable branch.
 */

const WS_TABS = [
  ['payload', 'Payload plan'],
  ['preflight', 'Preflight'],
  ['active', 'Active run'],
  ['history', 'History'],
]

// NICE-organized palette tabs (Build · Network · Identity · Cloud · Endpoint).
// Each group the container assembles carries a `tab`; the palette filters to the
// active one. No preserved test asserts palette content, so these are additive.
const PALETTE_TABS = [
  { id: 'build', label: 'Build' },
  { id: 'network', label: 'Network' },
  { id: 'identity', label: 'Identity' },
  { id: 'cloud', label: 'Cloud' },
  { id: 'endpoint', label: 'Endpoint' },
]

export default function ComposerView({ params = {}, setParams = () => {}, onNavigate = () => {} }) {
  const env = useEnvironment()
  const shelf = useShelf({ adapters: [] })

  // The origin scenario rides the URL (`#/composer?from=SIM-EDR-001`), so a
  // composed draft is a link a DC can send to a colleague and so Library →
  // "Open in Composer" is a plain navigation rather than hidden shared state.
  const fromId = params.from || null
  // ONE fetch, two consumers. `originDetail` is the raw API body — it is what
  // useLaunchScenario needs (execution_identity, pull/push support) — and
  // `origin` is the normalized draft derived from it.
  const [originDetail, setOriginDetail] = useState(null)
  const [origin, setOrigin] = useState(null)
  const [originError, setOriginError] = useState(null)
  const [loadingOrigin, setLoadingOrigin] = useState(false)

  const [steps, setSteps] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [metaOpen, setMetaOpen] = useState(false)
  // Editable workflow meta (name/plane/tc_ref/cgo) overlays the origin-derived
  // base so an edit does not have to round-trip through the origin fetch.
  const [draftMeta, setDraftMeta] = useState({})

  const [canvasView, setCanvasView] = useState('chain')   // 'chain' | 'yaml'
  const [lens, setLens] = useState('design')              // 'design' | 'run'
  const [wsTab, setWsTab] = useState('payload')
  const [wsOpen, setWsOpen] = useState(false)
  const [panelsHidden, setPanelsHidden] = useState(false)
  const [benchQuery, setBenchQuery] = useState('')
  const [paletteTab, setPaletteTab] = useState('build')
  const [notice, setNotice] = useState(null)
  // Design-lens stitch overlay is off by default (intent, not outcome).
  const [showStitch, setShowStitch] = useState(false)

  // ── Draft persistence ──────────────────────────────────────────────────────
  const [savedScenarioId, setSavedScenarioId] = useState(null)
  const [savedSnapshot, setSavedSnapshot] = useState(null)
  const [savedDetail, setSavedDetail] = useState(null)
  const [savedLaunchable, setSavedLaunchable] = useState(null)
  const [saving, setSaving] = useState(false)

  // ── Palette sources (API-fetched, never invented) ──────────────────────────
  const [ttps, setTtps] = useState([])
  const [adapters, setAdapters] = useState([])

  const say = useCallback((msg) => {
    setNotice(msg)
    // Long enough to read a sentence; the message also stays in the DOM as a
    // status region so a screen reader is not racing the timer.
    setTimeout(() => setNotice(null), 5000)
  }, [])

  // ── Load the origin scenario ───────────────────────────────────────────────
  // Detail shape, not the list shape: only `GET /api/scenarios/:id` carries the
  // per-step `command` and `causality` the inspector exists to show.
  useEffect(() => {
    if (!fromId) {
      setOrigin(null); setOriginDetail(null); setOriginError(null); setSteps([])
      setDraftMeta({}); setSelectedId(null); setMetaOpen(false)
      setSavedScenarioId(null); setSavedSnapshot(null); setSavedDetail(null)
      setSavedLaunchable(null)
      return undefined
    }
    let cancelled = false
    setLoadingOrigin(true)
    setOriginError(null)
    getScenario(fromId)
      .then((d) => {
        if (cancelled) return
        const next = draftFromScenario(d)
        setOriginDetail(d || null)
        setOrigin(next)
        setSteps(next.steps)
        setDraftMeta({})
        // No auto-select: the inspector renders a plane <select> whose
        // <option>EDR</option> would collide with the Proves-bar plane chip at
        // mount. The DC selects a step to configure it.
        setSelectedId(null)
        setMetaOpen(false)
        // A draft seeded from a corpus scenario is not itself a saved draft.
        setSavedScenarioId(null); setSavedSnapshot(null); setSavedDetail(null)
        setSavedLaunchable(null)
      })
      .catch((err) => {
        if (cancelled) return
        // Named, not swallowed: an unreachable SimCore must not render as
        // "this scenario has no steps".
        setOrigin(null)
        setOriginDetail(null)
        setSteps([])
        setOriginError(err?.message || `Could not load ${fromId}`)
      })
      .finally(() => { if (!cancelled) setLoadingOrigin(false) })
    return () => { cancelled = true }
  }, [fromId])

  // ── Palette sources ─────────────────────────────────────────────────────────
  // Fetched once; a SimCore without the TTP/adapter surfaces just yields an
  // empty group rather than a crash (the routes are opt-in and may 404).
  useEffect(() => {
    let cancelled = false
    getTtps({ status: 'active' })
      .then((d) => { if (!cancelled) setTtps(Array.isArray(d?.ttps) ? d.ttps : []) })
      .catch(() => { if (!cancelled) setTtps([]) })
    getToolAdapters()
      .then((d) => { if (!cancelled) setAdapters(Array.isArray(d?.adapters) ? d.adapters : []) })
      .catch(() => { if (!cancelled) setAdapters([]) })
    return () => { cancelled = true }
  }, [])

  const draft = useMemo(
    () => ({ ...(origin || emptyDraft()), ...draftMeta, steps }),
    [origin, draftMeta, steps],
  )
  const validation = useMemo(() => validateDraft(steps), [steps])
  const selected = useMemo(
    () => steps.find((s) => s.id === selectedId) || null,
    [steps, selectedId],
  )

  // A draft has diverged from what SimCore holds (the corpus origin) when its
  // step set differs — length, id order, or a hand-authored step.
  const edited = useMemo(() => {
    if (!origin) return steps.length > 0
    if (steps.length !== origin.steps.length) return true
    return steps.some((s, i) => s.id !== origin.steps[i]?.id || s.authored)
  }, [origin, steps])

  // Has the canvas drifted from the last successful save? A null snapshot is
  // dirty by definition (an unsaved draft), which is exactly what the launch
  // gate leans on.
  const dirty = useMemo(
    () => isDraftDirty(draftSnapshot(draft), savedSnapshot),
    [draft, savedSnapshot],
  )

  const tenantName = env.tenant ? (env.tenant.name || env.tenant.id) : null
  const agentName = env.agent ? (env.agent.hostname || agentIdOf(env.agent)) : null

  // ── Run lens data ────────────────────────────────────────────────────────────
  // The Run lens renders the REAL causality graph of the run belonging to
  // THIS draft's OWN origin scenario — in-flight or terminal — never a
  // globally-active run of some other scenario.
  //
  // Previously this keyed on env.activeRun alone
  // (docs/superpowers/plans/2026-09-08-run-lens-diagnosis.md), which fell
  // out to two defects: (A) EnvironmentContext derives env.activeRun as
  // `runs.find(r => r.status === 'running')` — app-wide AND running-only, so
  // a completed/failed run never triggered the fetch even though the backend
  // held a fully populated causality graph for it; (B) nothing guarded
  // env.activeRun by scenario id, so an in-flight run of a DIFFERENT
  // scenario could paint this canvas with another scenario's evidence.
  //
  // Field-name note (verified live against a running SimCore, not assumed —
  // see the Task 13 test file header): env.runs holds RAW API rows —
  // run_id / scenario_id / status / started_at. env.activeRun is NOT a raw
  // row; it's the DERIVED view-model EnvironmentContext.jsx's `activeRun`
  // useMemo builds — runId / scenarioId (camelCase) and no
  // run_id/scenario_id/status fields at all (its existence already means
  // "running"). `runIdOf()` reads run_id/id, so calling it on env.activeRun
  // as before always resolved to null — the "prefer the live run" branch was
  // dead code even before scoping, not merely unscoped.
  const scenarioId = draft.originId || null

  const scopedRun = useMemo(() => {
    if (!scenarioId) return null
    // Prefer the live run, but only when it is THIS scenario's.
    if (env.activeRun && env.activeRun.scenarioId === scenarioId) return env.activeRun
    const mine = (env.runs || []).filter((r) => r && r.scenario_id === scenarioId)
    if (mine.length === 0) return null
    return mine.reduce((best, r) =>
      Date.parse(r.started_at || 0) > Date.parse(best.started_at || 0) ? r : best)
  }, [scenarioId, env.activeRun, env.runs])

  // scopedRun is either env.activeRun (camelCase runId) or a raw run row
  // (snake_case run_id) — resolve both through the same fallback chain.
  const activeRunId = scopedRun ? (scopedRun.runId ?? runIdOf(scopedRun)) : null

  // Poll only while the matched run is non-terminal — a terminal run's graph
  // is settled, so keying on its mutable fields would refetch forever for no
  // new data. env.activeRun carries no `status` field (its existence already
  // means "running"), so object identity with env.activeRun also counts.
  const isLive = !!(scopedRun && (scopedRun === env.activeRun || scopedRun.status === 'running'))
  const runTick = isLive
    ? `${scopedRun.step}:${scopedRun.detected}:${scopedRun.status || 'running'}`
    : null

  // ComposerCanvas's `activeRun` prop is RAW-ROW shaped by contract
  // (run_id/status/stitch_binding — pinned by ComposerCanvas.test.jsx's own
  // "quotes the run's REAL persisted binding" test). scopedRun is NOT always
  // that shape — when it's env.activeRun (the live-run-of-this-scenario
  // branch) it's the camelCase view-model, which carries none of those three
  // fields. Passing it through unprojected would repeat exactly the bug this
  // task exists to fix: a shape mismatch that silently renders blank instead
  // of erroring. Project ONCE, here, into one canonical shape every
  // downstream consumer can rely on — not another `??` at each call site.
  const canonicalActiveRun = useMemo(() => {
    if (!scopedRun) return null
    // Already raw-row shaped (sourced from env.runs) — nothing to project.
    if (scopedRun.run_id != null) return scopedRun
    // scopedRun is env.activeRun. Its existence already means "running";
    // look up the matching raw row (once the runs poll has caught up) for
    // stitch_binding, which the view-model never carries at all.
    const raw = (env.runs || []).find((r) => r && runIdOf(r) === scopedRun.runId)
    return {
      ...(raw || {}),
      run_id: raw?.run_id ?? scopedRun.runId,
      status: raw?.status ?? 'running',
      stitch_binding: raw?.stitch_binding ?? null,
    }
  }, [scopedRun, env.runs])

  const [causalityGraph, setCausalityGraph] = useState(null)
  useEffect(() => {
    if (!activeRunId) { setCausalityGraph(null); return undefined }
    let cancelled = false
    getRunCausality(activeRunId)
      .then((g) => { if (!cancelled) setCausalityGraph(g || null) })
      .catch(() => { if (!cancelled) setCausalityGraph(null) })
    return () => { cancelled = true }
  }, [activeRunId, runTick])
  const causalityStates = useMemo(
    () => causalityStepStates(causalityGraph),
    [causalityGraph],
  )

  // ── Launch (the existing path, against the SAVED row when edited) ────────────
  // For an unedited chain we launch the corpus origin directly. For an edited
  // chain we launch the persisted draft row — SimCore runs the SAVED steps, and
  // the button refuses until they have been saved.
  const launchTarget = edited ? savedDetail : originDetail
  const launch = useLaunchScenario(launchTarget, {
    onRunComplete: (run) => {
      env.refreshRuns()
      onNavigate('runs', { run: runIdOf(run), tab: 'live' })
    },
    onError: (msg) => say(msg),
  })

  // Preflight is a real gate, not a spinner: it reports the health model's own
  // verdict plus the draft's validation, then unlocks Launch. A fresh save is a
  // fresh gate, so preflight resets on save and on structural change.
  const [preflighted, setPreflighted] = useState(false)
  useEffect(() => { setPreflighted(false) }, [fromId, steps.length, savedScenarioId])

  const runPreflight = useCallback(() => {
    setWsTab('preflight')
    setWsOpen(true)
    setPreflighted(true)
    const degraded = env.healthModel?.degraded?.length || 0
    say(
      `Preflight: ${degraded === 0 ? 'no degraded components' : `${degraded} component(s) degraded`}`
      + ` · ${validation.ok ? 'chain valid' : validation.problems[0]}`,
    )
  }, [env.healthModel, validation, say])

  // ── Save / load the draft ────────────────────────────────────────────────────
  const saveDraft = useCallback(async () => {
    setSaving(true)
    try {
      const body = draftToApi(draft, { author: draft.author || 'composer' })
      const res = savedScenarioId
        ? await updateDraft(savedScenarioId, body)
        : await createDraft(body)
      const id = res?.scenario_id || savedScenarioId
      setSavedScenarioId(id)
      setSavedDetail(res || null)
      setSavedLaunchable(res?.launchable || null)
      setSavedSnapshot(draftSnapshot(draft))
      setPreflighted(false)
      say(
        res?.launchable?.launchable
          ? `Saved ${id} — launchable`
          : `Saved ${id} — ${res?.launchable?.reasons?.[0] || 'not yet launchable'}`,
      )
    } catch (err) {
      say(err?.message || 'Could not save the draft')
    } finally {
      setSaving(false)
    }
  }, [draft, savedScenarioId, say])

  const loadDraft = useCallback(async () => {
    try {
      const { drafts = [] } = await listDrafts()
      if (!drafts.length) { say('No saved drafts on this SimCore yet'); return }
      // No draft picker in Phase 1 — load the most recent (list is newest-first
      // enough for a single-DC workflow); a fuller chooser is a later concern.
      const id = drafts[0].scenario_id || drafts[0].id
      const row = await getDraft(id)
      const loaded = draftFromApi(row)
      const { steps: loadedSteps, ...base } = loaded
      setOrigin(base)
      setOriginDetail(row || null)
      setSteps(loadedSteps)
      setDraftMeta({})
      setSelectedId(null)
      setMetaOpen(false)
      setOriginError(null)
      setSavedScenarioId(id)
      setSavedDetail(row || null)
      setSavedLaunchable(row?.launchable || null)
      setSavedSnapshot(draftSnapshot(loaded))
      setPreflighted(false)
      say(`Loaded draft ${id}`)
    } catch (err) {
      say(err?.message || 'Could not load drafts')
    }
  }, [say])

  const downloadYaml = useCallback(() => {
    const yaml = emitDraftYaml(draft, { tenant: tenantName, agent: agentName })
    const blob = new Blob([yaml], { type: 'text/yaml' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${draft.originId || savedScenarioId || 'sim-draft'}-draft.yml`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    say('Draft YAML downloaded — review it, then drop it into scenarios/ and reload SimCore')
  }, [draft, tenantName, agentName, savedScenarioId, say])

  // Declared BEFORE the palette memo that calls it: `const` bindings are in the
  // temporal dead zone until their initializer runs, and useMemo runs its
  // factory synchronously during render.
  const addBlank = useCallback((name) => {
    setSteps((prev) => {
      const id = nextStepId(prev)
      const next = appendStep(prev, blankStep(id, { name }))
      setSelectedId(id)
      return next
    })
  }, [])

  // ── Step & meta edit callbacks (all immutable, via composerDraft ops) ────────
  const onSelect = useCallback((id) => { setSelectedId(id); setMetaOpen(false) }, [])
  const onEditStep = useCallback((id, patch) => setSteps((p) => editStep(p, id, patch)), [])
  const onAddDetection = useCallback((id, det) => setSteps((p) => addDetection(p, id, det)), [])
  const onRemoveDetection = useCallback((id, i) => setSteps((p) => removeDetection(p, id, i)), [])
  // Inspector's manual "parent step" picker (`ComposerInspector.jsx`). Same
  // composition as `handleConnectSteps` below, and for the same reason: a DC
  // choosing a parent that sits AFTER the child in `steps[]` is a legal edit
  // (`setCausalityParent`'s forward-ref guard exists to mirror the loader's
  // array-order rule, not to refuse the edit outright), so `skipOrderCheck`
  // applies it and `topologicallySortSteps` immediately repairs array order
  // to match. Before this fix, this path had no such recovery — the guard's
  // silent same-array return meant the Inspector picker could select a
  // later-positioned parent and get nothing: no change, no error, no
  // explanation. The canvas's drag-to-connect affordance (below) already
  // worked this way; the two controls now share one guarantee instead of the
  // DC's outcome depending on which one they happened to use.
  const onSetCausalityParent = useCallback(
    (id, parentId, pivot) => setSteps((p) => topologicallySortSteps(
      setCausalityParent(p, id, parentId, pivot, { skipOrderCheck: true }),
    )),
    [],
  )
  // Canvas drag-to-connect (Task 10, direct-manipulation). `canConnect`
  // (composerSpine.js) already proved `fromId -> toId` legal on the
  // causality PARENT GRAPH before `ComposerCanvas` ever calls this — no
  // self-ref, no cycle, no second root — but that check says nothing about
  // array POSITION. `setCausalityParent` still enforces its own forward-ref
  // guard (parent index < child index, mirroring
  // core/engine/scenario_loader.py:394); passing `skipOrderCheck` here means
  // a canvas-approved edge is never silently swallowed by that guard — same
  // composition `onSetCausalityParent` above now applies for the Inspector's
  // manual picker. `topologicallySortSteps` then restores the array-order
  // invariant immediately, so what the operator drew and what the loader
  // will accept never diverge — see `composerSpine.js`'s header for the full
  // split between the two invariants this closes.
  const handleConnectSteps = useCallback((fromId, toId) => {
    setSteps((p) => topologicallySortSteps(
      setCausalityParent(p, toId, fromId, 'process_lineage', { skipOrderCheck: true }),
    ))
  }, [])
  const onBindTtp = useCallback((id) => onNavigate('ttps', { bind: id }), [onNavigate])
  const onEditMeta = useCallback((patch) => setDraftMeta((m) => ({ ...m, ...patch })), [])
  // Stitch context lives in the draftMeta overlay (like name/plane/tcRef/cgo):
  // `draft.stitchContext` resolves to `origin.stitchContext` until the DC edits
  // it, at which point the overlay carries it. `setEntity` refuses to author a
  // rejected value (incompatible directive / both-or-neither), so the container
  // never holds a spec the backend would 422.
  const onSetStitchEntity = useCallback((key, entry) => {
    setDraftMeta((m) => {
      const cur = (m.stitchContext !== undefined ? m.stitchContext : origin?.stitchContext) ?? null
      return { ...m, stitchContext: setEntity(cur, key, entry) }
    })
  }, [origin])
  // Insert a planted key's {stitch:KEY} token into a step's command. Goes through
  // onEditStep (not a controlled CommandEditor) — the effect re-seed on [stepId,
  // initial] carries the change into the uncontrolled editor.
  const onInsertStitch = useCallback((id, key) => {
    const cmd = steps.find((s) => s.id === id)?.command ?? ''
    onEditStep(id, { command: `${cmd} ${stitchInsertToken(key)}` })
  }, [steps, onEditStep])
  // Canvas node position (Task 9, direct-manipulation). Same draftMeta-overlay
  // shape as `onSetStitchEntity` right above: `draft.layout` resolves to
  // `origin.layout` until the DC drags a node, at which point the overlay
  // carries it (`composerDraft.js`'s own comment on `layout` calls this out
  // as the same round-trip-through-ONE-place shape as `stitchContext`).
  // `setNodePosition` already rounds and returns a NEW object rather than
  // mutating, so this is just the overlay's read-current/write-back pattern.
  const onNodeMoved = useCallback((stepId, x, y) => {
    setDraftMeta((m) => {
      const curLayout = m.layout !== undefined ? m.layout : (origin?.layout ?? null)
      return setNodePosition({ ...m, layout: curLayout }, stepId, x, y)
    })
  }, [origin])
  // Re-layout (Task 11): discard every dragged position and fall back to the
  // computed layout. Same overlay shape as `onNodeMoved` above — writing
  // `layout: null` into the `draftMeta` overlay is enough on its own
  // (`clearLayout` just does that spread), since `draft.layout` always reads
  // through the overlay when one is present.
  const onClearLayout = useCallback(() => {
    setDraftMeta((m) => clearLayout(m))
  }, [])
  const onMoveStep = useCallback((index, delta) => setSteps((p) => moveStep(p, index, delta)), [])
  const onDuplicateStep = useCallback((index) => setSteps((p) => duplicateStep(p, index)), [])
  const onRemoveStep = useCallback((index) => setSteps((p) => removeStep(p, index)), [])
  // Phase-3a channel routing. NOTE the distinction from the launch target:
  // `env.setAgent` + the palette 'Targets' group set the single-agent LAUNCH
  // target; `onSetTarget` here is the per-step SECOND-endpoint case (step.target).
  const onSetChannel = useCallback((id, ch) => setSteps((p) => setStepChannel(p, id, ch)), [])
  const onSetTarget = useCallback((id, t) => setSteps((p) => setStepTarget(p, id, t)), [])
  const onSetEal = useCallback((id, patch) => setSteps((p) => setStepEal(p, id, patch)), [])

  // ── Palette groups (every group API-sourced, never hardcoded content) ────────
  const paletteGroups = useMemo(() => {
    const groups = []

    groups.push({
      label: 'Step kinds',
      tone: 'action',
      tab: 'build',
      items: [
        { key: 'k-command', name: 'Command', meta: 'shell · identity-scoped',
          add: () => addBlank('New command step') },
        { key: 'k-wait', name: 'Wait / jitter', meta: 'pause between steps',
          add: () => addBlank('Wait') },
      ],
    })

    groups.push({
      label: 'Scenario library',
      tone: 'detected',
      tab: 'build',
      items: env.scenarios.slice(0, 24).map((s) => {
        const id = s.scenario_id || s.id
        return {
          key: `s-${id}`,
          name: id,
          meta: s.name || s.plane || '',
          // Opening a scenario REPLACES the draft rather than appending its
          // steps: two chains spliced together share no causality spine.
          add: () => setParams({ from: id }, { replace: true }),
        }
      }),
    })

    groups.push({
      label: 'TTP cards',
      tone: 'signal',
      tab: 'build',
      items: ttps.slice(0, 24).map((t) => {
        const id = t.ttp_id || t.id
        return {
          key: `t-${id}`,
          name: id,
          meta: t.name || t.mitre_technique || '',
          // Append a step already bound to this card's detection — the path
          // that satisfies the launch gate (a bound step is not a gap).
          add: () => setSteps((prev) => {
            const stepId = nextStepId(prev)
            const withStep = appendStep(prev, blankStep(stepId, {
              name: t.name || id,
              technique: t.mitre_technique || null,
            }))
            const bound = bindTtpDetection(withStep, stepId, t)
            setSelectedId(stepId)
            return bound
          }),
        }
      }),
    })

    groups.push({
      label: 'Tool adapters',
      tone: 'pending',
      tab: 'build',
      items: adapters.slice(0, 24).map((a) => {
        const id = a.adapter_id || a.id
        return {
          key: `ad-${id}`,
          name: id,
          meta: [a.plane, a.tier ? `tier ${a.tier}` : null].filter(Boolean).join(' · ') || 'adapter',
          add: () => onNavigate('adapters', { open: id }),
        }
      }),
    })

    groups.push({
      label: 'Targets',
      tone: 'signal',
      tab: 'endpoint',
      items: env.agents.slice(0, 16).map((a) => {
        const id = agentIdOf(a)
        return {
          key: `a-${id}`,
          name: a.hostname || id,
          meta: [a.os, a.status].filter(Boolean).join(' · ') || 'beacon',
          add: () => { env.setAgent(id); say(`Agent: ${a.hostname || id}`) },
        }
      }),
    })

    groups.push({
      label: 'Staged payloads',
      tone: 'pending',
      tab: 'build',
      items: (shelf.shelf?.payloads || []).slice(0, 16).map((p) => ({
        key: `p-${p.name}`,
        name: p.name,
        meta: p.adapter_id || 'unbound — no pack claims it',
        add: () => onNavigate('adapters'),
      })),
    })

    return groups
  }, [env.scenarios, env.agents, env.setAgent, ttps, adapters, shelf.shelf, addBlank, setParams, onNavigate, say])

  // ── Launch button state ──────────────────────────────────────────────────────
  const launchDisabled =
    launch.launching || saving || !launchTarget
      || (!preflighted
        ? true
        : edited
          ? (dirty || !savedLaunchable?.launchable)
          : launch.launchDisabled)

  const launchTitle = () => {
    if (edited && (dirty || !savedScenarioId)) {
      return 'Save the draft before launching — SimCore runs the SAVED chain, not the canvas edits.'
    }
    if (edited && savedScenarioId && savedLaunchable && !savedLaunchable.tc_bound) {
      return savedLaunchable.reasons?.join(' ') || 'Not launchable: bind tc_ref to a real FY27 index test case.'
    }
    if (edited && savedScenarioId && savedLaunchable && !savedLaunchable.chain_valid) {
      return savedLaunchable.reasons?.join(' ') || 'Not launchable: the chain is incomplete.'
    }
    if (!preflighted) return 'Run preflight first'
    return `Launch ${draft.originId || savedScenarioId || 'draft'} on ${agentName || 'the selected agent'}`
  }

  const yamlText = useMemo(
    () => emitDraftYaml(draft, { tenant: tenantName, agent: agentName }),
    [draft, tenantName, agentName],
  )

  // ── Render ─────────────────────────────────────────────────────────────────
  const showPanels = !panelsHidden

  return (
    <div className="composer" data-testid="composer-view">
      {/* ── Title row ── */}
      <div className="composer__head">
        <span className="composer__accent" aria-hidden="true" />
        <div className="composer__title-block">
          <h1 className="composer__title">Simulation Composer</h1>
          <div className="composer__provenance">
            {draft.originId || savedScenarioId ? (
              <>
                <span className="composer__from">from</span>
                <button
                  type="button"
                  className="linklike mono"
                  onClick={() => draft.originId && onNavigate('library', { open: draft.originId })}
                >
                  {draft.originId || savedScenarioId}
                </button>
                <span className="composer__meta">
                  · {validation.counts.steps} steps · {validation.counts.techniques} techniques
                  {savedScenarioId && <span className="composer__saved"> · saved {savedScenarioId}</span>}
                  {edited && dirty && <strong className="composer__edited"> · unsaved edits</strong>}
                </span>
              </>
            ) : (
              <span className="composer__meta">no scenario open — start one below</span>
            )}
          </div>
        </div>
        <span className="composer__spacer" />
        <button
          type="button"
          className="btn btn--xs"
          onClick={loadDraft}
          data-testid="composer-load-draft"
          title="Load a saved draft from this SimCore"
        >
          Load
        </button>
        <button
          type="button"
          className="btn btn--xs"
          onClick={saveDraft}
          data-testid="composer-save-draft"
          disabled={saving || !steps.length}
          title={savedScenarioId ? 'Update the saved draft row' : 'Persist this chain as a draft Scenario row'}
        >
          {saving ? 'Saving…' : savedScenarioId ? 'Save draft' : 'Save draft'}
        </button>
        <button
          type="button"
          className="btn btn--xs"
          onClick={downloadYaml}
          data-testid="composer-download-yaml"
          title="Emit this chain as scenario YAML you can drop into scenarios/"
        >
          Download draft YAML
        </button>
        <button
          type="button"
          className="btn btn--xs"
          onClick={onClearLayout}
          data-testid="composer-relayout"
          title="Discard dragged positions and re-run the automatic layout"
          disabled={!draft.layout}
        >
          Re-layout
        </button>
        <button
          type="button"
          className="btn btn--xs"
          onClick={runPreflight}
          data-testid="composer-preflight"
        >
          Run preflight
        </button>
        <button
          type="button"
          className="btn btn--xs btn--primary"
          data-testid="composer-launch"
          disabled={launchDisabled}
          onClick={() => launch.launch()}
          title={launchTitle()}
        >
          {launch.launching ? 'Launching…' : `Launch on ${agentName || 'agent'}`}
        </button>
      </div>

      {/* ── What this draft proves, and whether it can run ── */}
      <div className="composer__proves">
        <span className="composer__proves-label">Proves</span>
        {draft.tcRef && (
          <button type="button" className="prove-chip" onClick={() => onNavigate('uctc', { tc: draft.tcRef })}>
            <span className="prove-chip__kind">Test case</span>
            <span className="prove-chip__value mono">{draft.tcRef}</span>
          </button>
        )}
        {draft.ucRef && (
          <button type="button" className="prove-chip" onClick={() => onNavigate('uctc', { uc: draft.ucRef })}>
            <span className="prove-chip__kind">Use case</span>
            <span className="prove-chip__value mono">{draft.ucRef}</span>
          </button>
        )}
        {draft.plane && (
          <button type="button" className="prove-chip" onClick={() => onNavigate('coverage')}>
            <span className="prove-chip__kind">Plane</span>
            <span className="prove-chip__value mono">{draft.plane}</span>
          </button>
        )}
        {validation.techniques.slice(0, 3).map((t) => (
          <button key={t} type="button" className="prove-chip" onClick={() => onNavigate('coverage')}>
            <span className="prove-chip__kind">Technique</span>
            <span className="prove-chip__value mono">{t}</span>
          </button>
        ))}
        <span className="composer__spacer" />
        <span
          className={'chain-validation' + (validation.ok ? ' chain-validation--ok' : '')}
          data-testid="composer-validation"
        >
          <span className="chain-validation__dot" aria-hidden="true" />
          <span className="chain-validation__title">
            {validation.ok ? 'Chain valid' : 'Chain incomplete'}
          </span>
          <span className="chain-validation__detail">
            {validation.ok
              ? 'Every step declares an expected detection. Ready for preflight.'
              : validation.problems[0]}
          </span>
        </span>
      </div>

      {/* Always in the DOM: a polite live region only announces when it exists
          BEFORE its text is inserted. Empty state collapses visually (no chrome)
          but stays in the accessibility tree so every say() is spoken. */}
      <div
        className={'composer__notice' + (notice ? '' : ' composer__notice--empty')}
        role="status"
        aria-live="polite"
        data-testid="composer-notice"
      >
        {notice || ''}
      </div>

      {/* ── palette · canvas · inspector ── */}
      <div className={'composer__grid' + (showPanels ? '' : ' composer__grid--solo')}>
        {showPanels && (
          <ComposerPalette
            tabs={PALETTE_TABS}
            activeTab={paletteTab}
            onTab={setPaletteTab}
            groups={paletteGroups}
            query={benchQuery}
            onQuery={setBenchQuery}
            loading={shelf.loading}
          />
        )}

        <ComposerCanvas
          draft={draft}
          steps={steps}
          lens={lens}
          onLens={setLens}
          canvasView={canvasView}
          onCanvasView={setCanvasView}
          selectedId={selectedId}
          onSelect={onSelect}
          validation={validation}
          causalityGraph={causalityGraph}
          causalityStates={causalityStates}
          activeRun={canonicalActiveRun}
          hasRun={!!activeRunId}
          originError={originError}
          loadingOrigin={loadingOrigin}
          fromId={fromId}
          tenantName={tenantName}
          agentName={agentName}
          yamlText={yamlText}
          onMoveStep={onMoveStep}
          onDuplicateStep={onDuplicateStep}
          onRemoveStep={onRemoveStep}
          onNodeMoved={onNodeMoved}
          onConnectSteps={handleConnectSteps}
          onAddStep={() => addBlank('New command step')}
          onStartLibrary={() => onNavigate('library')}
          onStartTtp={() => onNavigate('ttps')}
          onStartBlank={() => addBlank('New command step')}
          onNavigate={onNavigate}
          stitchModel={draft.stitchContext}
          showStitch={showStitch}
          onToggleStitch={() => setShowStitch((v) => !v)}
          storedLayout={draft.layout}
        />

        {showPanels && (
          (selected || metaOpen) ? (
            <ComposerInspector
              selected={selected}
              draft={draft}
              steps={steps}
              onEditStep={onEditStep}
              onAddDetection={onAddDetection}
              onRemoveDetection={onRemoveDetection}
              onSetCausalityParent={onSetCausalityParent}
              onBindTtp={onBindTtp}
              onEditMeta={onEditMeta}
              pivots={PIVOTS}
              detectionTypes={DETECTION_TYPES}
              planes={PLANES}
              onNavigate={onNavigate}
              stitchModel={draft.stitchContext}
              onSetStitchEntity={onSetStitchEntity}
              onInsertStitch={onInsertStitch}
              agentName={agentName}
              agents={env.agents}
              onSetChannel={onSetChannel}
              onSetTarget={onSetTarget}
              onSetEal={onSetEal}
            />
          ) : (
            <NoSelectionAside draft={draft} onOpenMeta={() => setMetaOpen(true)} />
          )
        )}
      </div>

      {/* ── Workstream ── */}
      <div className="composer-ws">
        <div className="composer-ws__tabs" role="tablist" aria-label="Composer workstream">
          {WS_TABS.map(([id, label]) => (
            <button
              type="button"
              key={id}
              role="tab"
              aria-selected={wsTab === id && wsOpen}
              data-testid={`ws-tab-${id}`}
              className={'ws-tab' + (wsTab === id && wsOpen ? ' ws-tab--on' : '')}
              onClick={() => { setWsTab(id); setWsOpen(true) }}
            >
              {label}
              <span className="ws-tab__meta mono">{wsMeta(id, { env, shelf, validation })}</span>
            </button>
          ))}
          <span className="composer__spacer" />
          <button
            type="button"
            className="ws-ctl"
            onClick={() => setPanelsHidden((v) => !v)}
            title="Show or hide the palette and inspector"
          >
            {showPanels ? 'Hide panels' : 'Show panels'}
          </button>
          <button
            type="button"
            className="ws-ctl"
            onClick={() => setWsOpen((v) => !v)}
            aria-expanded={wsOpen}
            aria-label={wsOpen ? 'Collapse workstream' : 'Expand workstream'}
          >
            {wsOpen ? '▾' : '▴'}
          </button>
        </div>

        {wsOpen && (
          <div className="composer-ws__body" data-testid="composer-ws-body">
            {wsTab === 'payload' && <PayloadPane shelf={shelf} onNavigate={onNavigate} />}
            {wsTab === 'preflight' && <PreflightPane model={env.healthModel} validation={validation} onNavigate={onNavigate} />}
            {wsTab === 'active' && <ActivePane activeRun={env.activeRun} onNavigate={onNavigate} />}
            {wsTab === 'history' && <HistoryPane runs={env.runs} onNavigate={onNavigate} />}
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * The inspector column when NOTHING is selected. Deliberately NOT the full
 * `ComposerInspector` (whose plane <select> would render an <option>EDR</option>
 * that competes with the Proves-bar plane chip for `getByText('EDR')` at mount).
 * It carries the scenario-level teardown — the one piece of config that is not
 * per-step — and a way into the editable workflow meta.
 */
function NoSelectionAside({ draft, onOpenMeta }) {
  const teardown = Array.isArray(draft.teardown) ? draft.teardown : []
  return (
    <aside className="composer-inspector" aria-label="Step configuration" data-testid="composer-inspector-empty">
      <div className="composer-inspector__head">
        <span className="composer-inspector__title">Step config</span>
        <span className="composer__spacer" />
        <span className="mono composer-inspector__id">—</span>
      </div>
      <div className="composer-inspector__empty">
        Select a step on the canvas to configure it, or
        <button type="button" className="linklike" onClick={onOpenMeta}> edit the workflow meta</button>.
      </div>

      <div className="field-label">Teardown</div>
      <div className="field-value">
        {teardown.length ? (
          <>
            <span className="field-note">
              Scenario-level (the schema has no per-step cleanup):
            </span>
            <pre className="field-code mono">{teardown.join('\n')}</pre>
          </>
        ) : 'no cleanup declared for this scenario'}
      </div>
    </aside>
  )
}

/** Tab subtitles. Every one is a real count or an explicit unknown. */
function wsMeta(id, { env, shelf, validation }) {
  if (id === 'payload') {
    if (shelf.loading) return '…'
    if (shelf.available === false) return 'no shelf'
    return `${(shelf.shelf?.payloads || []).length} staged`
  }
  if (id === 'preflight') {
    if (!env.healthModel) return 'unknown'
    const d = env.healthModel.degraded.length
    return d === 0 ? 'all ok' : `${d} degraded`
  }
  if (id === 'active') return env.activeRun ? `step ${env.activeRun.step}/${env.activeRun.totalSteps}` : 'idle'
  if (id === 'history') return String(env.runs.length)
  return validation.ok ? 'ok' : ''
}

function PayloadPane({ shelf, onNavigate }) {
  if (shelf.available === false) {
    return (
      <div className="ws-empty">
        This SimCore exposes no payload shelf, so every tool a step needs is fetched by the
        TARGET from the public internet at run time. That is a real property of the run, not a
        missing panel.
      </div>
    )
  }
  const payloads = shelf.shelf?.payloads || []
  if (!payloads.length) {
    return (
      <div className="ws-empty">
        Nothing staged on this SimCore.
        <button type="button" className="btn btn--xs" onClick={() => onNavigate('adapters')}>
          Open Tools &amp; Payloads
        </button>
      </div>
    )
  }
  return (
    <div className="ws-rows">
      {payloads.map((p) => (
        <div className="ws-row" key={p.name}>
          <span className="mono ws-row__id">{p.name}</span>
          {/* An unbound artifact can be served but nothing can compose it — no
              adapter_ref resolves to it. Saying "—" would read as harmless. */}
          <span className="ws-row__mid mono">{p.adapter_id || 'unbound'}</span>
          <span className="ws-row__digest mono">{p.sha256 || 'unpinned'}</span>
          <span className={'chip ' + (p.sha256 ? 'chip--detected' : 'chip--pending')}>
            {p.sha256 ? 'pinned' : 'unpinned'}
          </span>
        </div>
      ))}
    </div>
  )
}

function PreflightPane({ model, validation, onNavigate }) {
  return (
    <div className="ws-preflight">
      <div className={'ws-verdict' + (validation.ok ? ' ws-verdict--ok' : '')}>
        <strong>Chain:</strong>{' '}
        {validation.ok ? 'every step declares an expected detection.' : validation.problems.join(' ')}
      </div>
      {!model ? (
        <div className="ws-empty">
          Readiness has not been probed yet — nothing here is a claim about this deployment.
        </div>
      ) : !model.reachable ? (
        <div className="ws-empty">SimCore is unreachable: {model.unreachableReason}</div>
      ) : (
        <div className="ws-grid">
          {model.components.map((c) => (
            <div className="ws-check" key={c.key}>
              <span className={
                'ws-check__dot'
                + (c.status === HS.OK ? ' ws-check__dot--ok' : '')
                + (c.status === HS.DEGRADED || c.status === HS.ERROR ? ' ws-check__dot--bad' : '')
              } />
              <span className="ws-check__text">
                <span className="ws-check__name">{c.label}</span>
                <span className="ws-check__detail mono">
                  {c.detail || c.disagreement || c.remediation || c.status}
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
      <button type="button" className="btn btn--xs" onClick={() => onNavigate('readiness')}>
        Full readiness ▸
      </button>
    </div>
  )
}

function ActivePane({ activeRun, onNavigate }) {
  if (!activeRun) {
    return <div className="ws-empty">No run in flight. Preflight, then launch.</div>
  }
  return (
    <div className="ws-rows">
      <div className="ws-row">
        <span className="mono ws-row__id">{activeRun.runId}</span>
        <span className="ws-row__mid">{activeRun.scenarioId}</span>
        <span className="ws-row__digest mono">
          step {activeRun.step}/{activeRun.totalSteps} · {activeRun.detected}/{activeRun.total} detected
        </span>
        <button
          type="button"
          className="btn btn--xs"
          onClick={() => onNavigate('runs', { run: activeRun.runId, tab: 'live' })}
        >
          Follow ▸
        </button>
      </div>
    </div>
  )
}

function HistoryPane({ runs, onNavigate }) {
  if (!runs.length) return <div className="ws-empty">No runs yet.</div>
  return (
    <div className="ws-rows">
      {runs.slice(0, 8).map((r) => {
        const id = runIdOf(r)
        return (
          <button
            type="button"
            className="ws-row ws-row--button"
            key={id}
            onClick={() => onNavigate('runs', {
              run: id,
              tab: isRunTerminal(r.status) ? 'evidence' : 'live',
            })}
          >
            <span className="mono ws-row__id">{id}</span>
            <span className="ws-row__mid">{r.scenario_id || '—'}</span>
            <span className="ws-row__digest mono">{r.started_at || ''}</span>
            <span className={'chip ' + (isRunTerminal(r.status) ? 'chip--detected' : 'chip--pending')}>
              {r.status || 'unknown'}
            </span>
          </button>
        )
      })}
    </div>
  )
}
