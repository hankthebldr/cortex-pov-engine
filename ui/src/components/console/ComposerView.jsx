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
import ConfirmDialog from './ConfirmDialog.jsx'
import { reparentOnDelete } from './composerGraph.js'
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
  editStep,
  emitDraftYaml,
  emptyDraft,
  isDraftDirty,
  moveStep,
  nextStepId,
  PIVOTS,
  PLANES,
  removeDetection,
  setCausalityParent,
  setNodePosition,
  setStepLane,
  setStepChannel,
  setStepEal,
  setStepTarget,
  spineProblems,
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

/**
 * Whether a media query matches, tracking changes. jsdom has no matchMedia,
 * so it reads false there (the wide layout), never throws.
 */
function useMediaQuery(query) {
  const read = () => (typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia(query).matches
    : false)
  const [matches, setMatches] = useState(read)
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined
    const mql = window.matchMedia(query)
    const sync = () => setMatches(mql.matches)
    sync()
    mql.addEventListener?.('change', sync)
    return () => mql.removeEventListener?.('change', sync)
  }, [query])
  return matches
}

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

export default function ComposerView({
  params = {}, setParams = () => {}, onNavigate = () => {}, onExit = null,
}) {
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
  // Which of this POV's workflows is open. The Composer used to name only the
  // scenario a draft was started FROM, so "which chain am I editing, is it
  // saved, and how do I start another" had no answer anywhere on screen.
  const [metaOpen, setMetaOpen] = useState(false)
  // Editable workflow meta (name/plane/tc_ref/cgo) overlays the origin-derived
  // base so an edit does not have to round-trip through the origin fetch.
  const [draftMeta, setDraftMeta] = useState({})

  const [canvasView, setCanvasView] = useState('chain')   // 'chain' | 'yaml'
  const [lens, setLens] = useState('design')              // 'design' | 'run'
  const [wsTab, setWsTab] = useState('payload')
  const [wsOpen, setWsOpen] = useState(false)
  const [benchQuery, setBenchQuery] = useState('')
  const [paletteTab, setPaletteTab] = useState('build')
  const [notice, setNotice] = useState(null)
  // Design-lens stitch overlay is off by default (intent, not outcome).
  const [showStitch, setShowStitch] = useState(false)

  // Composer-mode drawer state. In full-screen mode the palette (node creator)
  // and the inspector are overlay drawers on the canvas, not grid columns, so
  // the graph owns the whole stage. Palette opens by default (it is how you add
  // nodes); the inspector opens when a step is selected.
  // Both drawers start CLOSED: in composer mode the graph owns the stage. The
  // palette opens from `＋ Add`; the inspector opens from its tab, or by itself
  // when a step is selected or the workflow meta is opened,
  // and with nothing selected it carries the scenario teardown + the way into
  // the workflow meta (`NoSelectionAside`).
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  // Exit-confirm when leaving mode with unsaved edits, and the "Esc again to
  // exit" arming flag (a single Esc must never throw away an unsaved chain).
  // One confirm for every gesture that would discard unsaved work (leaving the
  // mode, opening another scenario over the chain): {title, body, confirmLabel,
  // onConfirm} or null.
  const [pendingConfirm, setPendingConfirm] = useState(null)

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
  //
  // Diverged means "what would EXECUTE differs" — not just the step id list.
  // The id-only check missed every in-place edit (a pivot, a command, a
  // detection, a channel, a re-parent), and an un-edited draft launches the
  // ORIGIN corpus row: so a DC could change a relationship on the canvas, hit
  // Launch, and run something other than what is on screen. Composer mode makes
  // those edits one click away (the edge pivot chip, Delete, reconnect), so the
  // comparison is the launch-relevant snapshot, minus pure presentation
  // (`layout`, `laneOverrides`): dragging a card never forces a draft save.
  const edited = useMemo(() => {
    if (!origin) return steps.length > 0
    if (steps.some((s) => s.authored)) return true
    const runShape = (d) => {
      const snap = draftSnapshot(d)
      if (!snap) return snap
      const { layout: _l, laneOverrides: _lo, ...rest } = snap
      return rest
    }
    return JSON.stringify(runShape(draft)) !== JSON.stringify(runShape(origin))
  }, [origin, steps, draft])

  // Has the canvas drifted from the last successful save? A null snapshot is
  // dirty by definition (an unsaved draft), which is exactly what the launch
  // gate leans on.
  const dirty = useMemo(
    () => isDraftDirty(draftSnapshot(draft), savedSnapshot),
    [draft, savedSnapshot],
  )

  // What leaving would actually lose. Before any save `dirty` is ALWAYS true
  // (a null snapshot), so it cannot be the test — an untouched library
  // scenario was told "This chain has unsaved edits". Before a save it is
  // `edited`; once a row exists (saved, or loaded) it is drift from that row.
  const unsaved = savedSnapshot ? dirty : edited
  const unsavedRef = React.useRef(unsaved)
  unsavedRef.current = unsaved

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
  //
  // Once the chain is saved as a draft row it launches AS that row, so its
  // runs belong to the row's id — showing the origin corpus scenario's runs
  // there would paint another chain's verdicts on these cards.
  const scenarioId = savedScenarioId || draft.originId || null

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
  // A card may only wear a run's verdict if that step is the step that RAN.
  // States are keyed by step id, so an edited step (new command, new pivot)
  // or a new step would otherwise show the old run's CONFIRMED for something
  // that never executed. Those cards read NOT_RUN instead. With no run graph
  // at all every card stays EXPECTED, as before.
  const shownRunStates = useMemo(() => {
    if (!causalityGraph) return causalityStates
    const shape = (s) => JSON.stringify(draftSnapshot({ steps: [s] }).steps[0])
    // What ran: the saved row once there is one (runs are scoped to it, see
    // `scenarioId`), else the origin scenario.
    const ran = new Map(
      savedScenarioId && savedSnapshot
        ? savedSnapshot.steps.map((ss) => [ss.id, JSON.stringify(ss)])
        : (origin?.steps || []).map((s) => [s.id, shape(s)]),
    )
    const out = {}
    for (const s of steps) {
      out[s.id] = ran.get(s.id) === shape(s)
        ? (causalityStates[s.id] || { state: 'EXPECTED' })
        : { state: 'NOT_RUN' }
    }
    return out
  }, [causalityGraph, causalityStates, origin, steps, savedScenarioId, savedSnapshot])

  // ── Launch (the existing path, against the SAVED row when edited) ────────────
  // An unedited corpus scenario launches the corpus row directly. Anything else
  // launches a SAVED draft row — an edited chain, or a draft that was loaded or
  // saved here — because SimCore runs the saved steps, never the canvas. A
  // loaded draft that was saved and then edited BACK to its load-time shape is
  // not "unedited": its row now holds the later save, so it must go this way
  // too, or Launch would run the save while the canvas shows the original.
  const viaSavedRow = edited || !!savedScenarioId
  const launchTarget = viaSavedRow ? savedDetail : originDetail
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
      // `origin` MUST carry `.steps` — the `edited` memo above reads
      // `origin.steps.length` unconditionally (same contract the `fromId`
      // load effect honours by storing its full `draftFromScenario` result).
      // Stripping steps out of origin here left it a "pristine snapshot"
      // with a missing snapshot: with no scenario open, clicking Load threw
      // `Cannot read properties of undefined (reading 'length')` on the very
      // next render, before Save/Download ever touched the loaded steps.
      setOrigin(loaded)
      setOriginDetail(row || null)
      setSteps(loaded.steps)
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
  // `+ Add step` / a click on a palette step kind EXTENDS the chain, the way a
  // node editor appends after the selected node: the new step is parented to
  // the selected step, else to the last leaf. That keeps a declared spine at one
  // root by default — an unparented append was a second root, which the loader
  // rejects at save. Two cases stay free: an empty chain (the new step IS the
  // root) and a legacy star with no declared causality and several roots, where
  // linking would itself make every other root off-chain.
  const selectedRef = React.useRef(selectedId)
  selectedRef.current = selectedId
  // Latest-state refs for handlers React Flow can call several times in ONE
  // tick (a multi-card lane drop calls onLaneChange once per card): reading the
  // render-time `draft` there made the second call overwrite the first.
  const draftRef = React.useRef(draft)
  draftRef.current = draft
  const stepsRef = React.useRef(steps)
  stepsRef.current = steps
  // Every step id used this session, plus every id the origin had. A deleted
  // id is never minted again, so a new step cannot inherit the deleted one's
  // stored position, lane override or — in the Run lens — its recorded state.
  const seenIdsRef = React.useRef(new Set())
  for (const s of steps) seenIdsRef.current.add(s.id)
  if (origin) for (const s of origin.steps) seenIdsRef.current.add(s.id)
  const freshId = useCallback((list) => nextStepId(list, seenIdsRef.current), [])
  const addBlank = useCallback((name) => {
    setSteps((prev) => {
      const id = freshId(prev)
      let next = appendStep(prev, blankStep(id, { name }))
      const ids = new Set(prev.map((s) => s.id))
      const declared = prev.some((s) => s.causalityParent && ids.has(s.causalityParent))
      if (prev.length === 1 || declared) {
        const parented = new Set(prev.map((s) => s.causalityParent).filter(Boolean))
        const leaves = prev.filter((s) => !parented.has(s.id))
        const parent = ids.has(selectedRef.current)
          ? selectedRef.current
          : (leaves[leaves.length - 1] || prev[prev.length - 1]).id
        next = topologicallySortSteps(
          setCausalityParent(next, id, parent, 'process_lineage', { skipOrderCheck: true }),
        )
      }
      setSelectedId(id)
      return next
    })
  }, [freshId])

  // ── Step & meta edit callbacks (all immutable, via composerDraft ops) ────────
  // Selecting a step always shows its config — including re-clicking the step
  // that is already selected after the drawer was closed (the effect below only
  // fires when the selection CHANGES).
  const onSelect = useCallback((id) => {
    setSelectedId(id)
    setMetaOpen(false)
    if (id) setInspectorOpen(true)
  }, [])
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
  // Lanes lens: a step dragged into another band. Persisted on the draft like
  // a position (so it marks the draft dirty and survives reload), and written
  // through to the step's channel where the lane is channel-backed — see
  // `setStepLane` for exactly which lanes change the step and which only
  // record intent.
  const onLaneChange = useCallback((stepId, lane) => {
    // Compute once from the MERGED draft, then write each half to the state
    // that owns it. Calling setSteps inside a setDraftMeta updater would be a
    // side effect inside an updater — StrictMode double-invokes those, and the
    // second call would see steps it had already replaced. Read the LATEST
    // draft through the ref and advance it immediately, so a second card in
    // the same multi-card drop builds on this one instead of erasing it.
    const cur = draftRef.current
    const next = setStepLane(cur, stepId, lane)
    if (next === cur) return
    draftRef.current = next
    stepsRef.current = next.steps
    if (next.steps !== cur.steps) setSteps(next.steps)
    setDraftMeta((m) => ({ ...m, laneOverrides: next.laneOverrides }))
  }, [])

  // computed layout. Same overlay shape as `onNodeMoved` above — writing
  // `layout: null` into the `draftMeta` overlay is enough on its own
  // (`clearLayout` just does that spread), since `draft.layout` always reads
  // through the overlay when one is present.
  const onClearLayout = useCallback(() => {
    setDraftMeta((m) => clearLayout(m))
  }, [])
  // Card ↑/↓. Siblings reorder freely; a move that would put a step before its
  // own parent is refused, with the reason, because the loader rejects that
  // array ("must be an EARLIER step") and the canvas must not author it.
  const onMoveStep = useCallback((index, delta) => {
    const prev = stepsRef.current
    const next = moveStep(prev, index, delta)
    if (next === prev) return
    const before = new Set(spineProblems(prev).forwardRefs || [])
    const broken = (spineProblems(next).forwardRefs || []).filter((id) => !before.has(id))
    if (broken.length) {
      const child = next.find((s) => s.id === broken[0])
      say(`${broken[0]} can't run before its parent ${child?.causalityParent} — re-link it on the canvas instead.`)
      return
    }
    stepsRef.current = next
    setSteps(next)
  }, [say])
  const onDuplicateStep = useCallback((index) => setSteps((p) => {
    if (index < 0 || index >= p.length) return p
    const out = p.slice()
    out.splice(index + 1, 0, { ...p[index], id: freshId(p), authored: true })
    return out
  }), [freshId])
  // Phase-3a channel routing. NOTE the distinction from the launch target:
  // `env.setAgent` + the palette 'Targets' group set the single-agent LAUNCH
  // target; `onSetTarget` here is the per-step SECOND-endpoint case (step.target).
  const onSetChannel = useCallback((id, ch) => setSteps((p) => setStepChannel(p, id, ch)), [])
  const onSetTarget = useCallback((id, t) => setSteps((p) => setStepTarget(p, id, t)), [])
  const onSetEal = useCallback((id, patch) => setSteps((p) => setStepEal(p, id, patch)), [])

  // ── Graph-edit handlers (Composer mode) ──────────────────────────────────────
  // ONE delete for every gesture — the card's × tool, Delete/Backspace on the
  // canvas, a multi-select — so two ways of deleting the same card cannot give
  // two different chains (the × tool used to filter the step out and leave its
  // children pointing at a parent that no longer exists). The step's children
  // re-attach to its parent (`reparentOnDelete`), the selection clears, and its
  // stored position and lane override go with it.
  const onDeleteStepId = useCallback((id) => {
    setSteps((p) => reparentOnDelete(p, id))
    setSelectedId((cur) => (cur === id ? null : cur))
    setDraftMeta((m) => {
      let out = m
      for (const key of ['layout', 'laneOverrides']) {
        const cur = m[key] !== undefined ? m[key] : (origin?.[key] ?? null)
        if (cur && Object.prototype.hasOwnProperty.call(cur, id)) {
          const { [id]: _gone, ...rest } = cur
          out = { ...out, [key]: Object.keys(rest).length ? rest : null }
        }
      }
      return out
    })
  }, [origin])
  const onRemoveStep = useCallback((index) => {
    const id = stepsRef.current[index]?.id
    if (id) onDeleteStepId(id)
  }, [onDeleteStepId])
  // Delete a causality EDGE — the child loses its parent and becomes a root
  // hanging off the CGO (allowed; the single-root rule is enforced at connect).
  const onEdgeDelete = useCallback((childId) => {
    setSteps((p) => setCausalityParent(p, childId, null))
  }, [])
  // Reconnect an edge's target end to a new parent (drag the arrowhead).
  const onReparent = useCallback((childId, parentId) => {
    setSteps((p) => {
      const cur = p.find((s) => s.id === childId)
      const pivot = cur?.causalityPivot || 'process_lineage'
      return topologicallySortSteps(
        setCausalityParent(p, childId, parentId, pivot, { skipOrderCheck: true }),
      )
    })
  }, [])
  // Change a causality edge's pivot (the pivot chip on the edge). Announced,
  // because the chip's own text changes silently for a screen reader.
  const onSetPivot = useCallback((childId, pivot) => {
    setSteps((p) => {
      const cur = p.find((s) => s.id === childId)
      if (!cur || !cur.causalityParent) return p
      return setCausalityParent(p, childId, cur.causalityParent, pivot, { skipOrderCheck: true })
    })
    say(`${childId} pivot: ${pivot}`)
  }, [say])
  // Create a blank step at a canvas position, optionally parented — the engine
  // behind drop-to-create (releasing a connection on empty canvas) and
  // palette drag-and-drop.
  const addBlankAt = useCallback((name, position, parentId = null) => {
    // Mint the id ONCE against the current steps so the position write below
    // targets the same node the step append creates (nextStepId is deterministic
    // for a given array, but computing it twice invites drift).
    const id = freshId(stepsRef.current)
    setSteps((prev) => {
      let next = appendStep(prev, blankStep(id, { name }))
      if (parentId) {
        next = topologicallySortSteps(
          setCausalityParent(next, id, parentId, 'process_lineage', { skipOrderCheck: true }),
        )
      }
      return next
    })
    setSelectedId(id)
    setInspectorOpen(true)
    if (position && typeof position.x === 'number') {
      setDraftMeta((m) => {
        const curLayout = m.layout !== undefined ? m.layout : (origin?.layout ?? null)
        return setNodePosition({ ...m, layout: curLayout }, id, position.x, position.y)
      })
    }
  }, [origin, freshId])
  const onDropCreate = useCallback(({ position, parentId }) => {
    addBlankAt('New command step', position, parentId)
  }, [addBlankAt])

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
          add: () => openScenarioRef.current(id),
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
            const stepId = freshId(prev)
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
  }, [env.scenarios, env.agents, env.setAgent, ttps, adapters, shelf.shelf, addBlank, freshId, onNavigate, say])

  // A dropped palette item resolves by its key prefix. Step- and TTP-creating
  // items place a node at the drop point; the rest reuse their click action
  // (navigate / set the launch agent), since they are not free-standing nodes.
  const onCanvasDrop = useCallback((key, position) => {
    if (!key) return
    if (key === 'k-command') return addBlankAt('New command step', position)
    if (key === 'k-wait') return addBlankAt('Wait', position)
    if (key.startsWith('s-')) return openScenarioRef.current(key.slice(2))
    if (key.startsWith('t-')) {
      const ttpId = key.slice(2)
      const card = ttps.find((t) => (t.ttp_id || t.id) === ttpId)
      if (!card) return undefined
      const id = freshId(stepsRef.current)
      setSteps((prev) => {
        const withStep = appendStep(prev, blankStep(id, {
          name: card.name || ttpId, technique: card.mitre_technique || null,
        }))
        return bindTtpDetection(withStep, id, card)
      })
      setSelectedId(id)
      setInspectorOpen(true)
      if (position && typeof position.x === 'number') {
        setDraftMeta((m) => {
          const curLayout = m.layout !== undefined ? m.layout : (origin?.layout ?? null)
          return setNodePosition({ ...m, layout: curLayout }, id, position.x, position.y)
        })
      }
      return undefined
    }
    if (key.startsWith('ad-')) return onNavigate('adapters', { open: key.slice(3) })
    if (key.startsWith('a-')) {
      const id = key.slice(2)
      const a = env.agents.find((x) => agentIdOf(x) === id)
      env.setAgent(id); say(`Agent: ${a?.hostname || id}`)
      return undefined
    }
    if (key.startsWith('p-')) return onNavigate('adapters')
    return undefined
  }, [addBlankAt, ttps, freshId, origin, onNavigate, env.agents, env.setAgent, say])

  // ── Launch button state ──────────────────────────────────────────────────────
  // The client validation gates a saved-row launch as well as the server's
  // verdict: the server's chain check accepts the placeholder command, so a
  // step still reading "# configure the command for this step" would run a
  // shell comment and report its expected detection as missed — a false
  // negative manufactured by the tool — while the pill says "Chain incomplete".
  const launchDisabled =
    launch.launching || saving || !launchTarget
      || (!preflighted
        ? true
        : viaSavedRow
          ? (dirty || !savedLaunchable?.launchable || !validation.ok)
          : launch.launchDisabled)

  const launchTitle = () => {
    if (viaSavedRow && (dirty || !savedScenarioId)) {
      return 'Save the draft before launching — SimCore runs the SAVED chain, not the canvas edits.'
    }
    if (viaSavedRow && !validation.ok) {
      return `Not launchable: ${validation.problems[0]}`
    }
    if (viaSavedRow && savedScenarioId && savedLaunchable && !savedLaunchable.tc_bound) {
      return savedLaunchable.reasons?.join(' ') || 'Not launchable: bind tc_ref to a real FY27 index test case.'
    }
    if (viaSavedRow && savedScenarioId && savedLaunchable && !savedLaunchable.chain_valid) {
      return savedLaunchable.reasons?.join(' ') || 'Not launchable: the chain is incomplete.'
    }
    if (!preflighted) return 'Run preflight first'
    // Name what the click will actually run — the saved row after an edit.
    const what = launchTarget?.scenario_id || draft.originId || savedScenarioId || 'draft'
    return `Launch ${what} on ${agentName || 'the selected agent'}`
  }

  const yamlText = useMemo(
    () => emitDraftYaml(draft, { tenant: tenantName, agent: agentName }),
    [draft, tenantName, agentName],
  )

  // ── Exit the mode (guarded on unsaved edits) ─────────────────────────────────
  const attemptExit = useCallback(() => {
    if (!onExit) return
    if (unsavedRef.current && stepsRef.current.length) {
      setPendingConfirm({
        title: 'Leave the Composer?',
        body: 'This chain has unsaved edits. Leaving the Composer discards them — save the draft first to keep them. (SimCore only ever runs a saved chain, never the canvas.)',
        confirmLabel: 'Discard and leave',
        onConfirm: onExit,
      })
    } else {
      onExit()
    }
  }, [onExit])

  // Opening a library scenario REPLACES the chain on the canvas (two chains
  // spliced together share no spine), so with unsaved edits it asks first, the
  // same way leaving the mode does.
  const openScenario = useCallback((id) => {
    const go = () => setParams({ from: id }, { replace: true })
    if (unsavedRef.current && stepsRef.current.length) {
      setPendingConfirm({
        title: 'Replace this chain?',
        body: `Opening ${id} replaces the chain on the canvas and discards its unsaved edits.`,
        confirmLabel: 'Discard and open',
        onConfirm: go,
      })
    } else {
      go()
    }
  }, [setParams])
  const openScenarioRef = React.useRef(openScenario)
  openScenarioRef.current = openScenario

  // Escape cascade: peel one layer per press, and NEVER exit on a single Esc
  // (a stray key must not discard an unsaved chain). A window bubble-phase
  // listener runs after every document listener, so `e.defaultPrevented` means
  // an overlay (⌘K palette, a dialog) already consumed the key — AppShell now
  // only preventDefaults Escape when it actually closed the palette/help.
  // The "again" window is as long as the hint is on screen (a screen reader
  // takes ~2s to speak it); when the window closes, so does the hint.
  const ESC_HINT = 'Press Esc again to exit the Composer'
  const escArmedRef = React.useRef(false)
  const escTimerRef = React.useRef(null)
  useEffect(() => () => clearTimeout(escTimerRef.current), [])
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape' || e.defaultPrevented || e.isComposing) return
      const t = e.target
      const tag = t && t.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
        || (t && t.isContentEditable)) { t.blur(); return }
      if (pendingConfirm) return
      if (inspectorOpen) { closeInspector(); return }
      if (selectedId) { setSelectedId(null); return }
      if (paletteOpen) { closePalette(); return }
      if (!onExit) return
      if (escArmedRef.current) {
        escArmedRef.current = false
        clearTimeout(escTimerRef.current)
        setNotice((n) => (n === ESC_HINT ? null : n))
        attemptExit()
        return
      }
      escArmedRef.current = true
      say(ESC_HINT)
      clearTimeout(escTimerRef.current)
      escTimerRef.current = setTimeout(() => {
        escArmedRef.current = false
        setNotice((n) => (n === ESC_HINT ? null : n))
      }, 3000)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // closePalette/closeInspector are stable (useCallback below, no deps).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingConfirm, inspectorOpen, selectedId, paletteOpen, onExit, attemptExit, say])

  // ── Drawers ─────────────────────────────────────────────────────────────────
  // Opening a drawer from its tab moves keyboard focus INTO it; closing returns
  // focus to the tab that opens it again. Without this the activated button
  // unmounted and focus fell to <body> — a keyboard user was lost, and nothing
  // was announced. (A drawer opened by SELECTING a step does not steal focus
  // from the canvas: Delete there must still reach the selected card.)
  const focusNextRef = React.useRef(null)
  useEffect(() => {
    const sel = focusNextRef.current
    if (!sel) return
    focusNextRef.current = null
    const el = document.querySelector(sel)
    if (el && typeof el.focus === 'function') el.focus()
  })
  const openPalette = useCallback(() => {
    focusNextRef.current = '.composer-mode__drawer--left .composer-bench__filter'
    setPaletteOpen(true)
  }, [])
  const closePalette = useCallback(() => {
    focusNextRef.current = '.composer-mode__reopen--left'
    setPaletteOpen(false)
  }, [])
  const openInspector = useCallback(() => {
    focusNextRef.current = '.composer-mode__drawer--right .composer-mode__drawer-title'
    setInspectorOpen(true)
  }, [])
  const closeInspector = useCallback(() => {
    focusNextRef.current = '.composer-mode__reopen--right'
    setInspectorOpen(false)
  }, [])

  // The pixels of the stage the overlay drawers and the dock cover, so a Fit
  // frames the graph in the VISIBLE region. Must agree with composer.css:
  // drawers 300px / 360px (248px / 300px at <= 1100px) plus 12px gutters,
  // and the ~48px dock tab row.
  const narrow = useMediaQuery('(max-width: 1100px)')
  const viewInsets = useMemo(() => ({
    top: 24,
    left: paletteOpen ? (narrow ? 260 : 312) : 0,
    right: inspectorOpen ? (narrow ? 312 : 372) : 0,
    bottom: 48,
  }), [paletteOpen, inspectorOpen, narrow])

  // Open the inspector drawer whenever a step becomes selected (and the DC has
  // not explicitly closed it this selection). Closing it is a one-press Esc or
  // the drawer's own ✕; re-selecting re-opens.
  useEffect(() => {
    if (selectedId || metaOpen) setInspectorOpen(true)
  }, [selectedId, metaOpen])

  // ── Render ─────────────────────────────────────────────

  return (
    <div className="composer composer--mode" data-testid="composer-view">
      {/* ── Workflow bar (the only chrome above the full-bleed canvas) ── */}
      <div className="composer__head composer-mode__bar">
        {onExit && (
          <button
            type="button"
            className="btn btn--xs composer-mode__exit"
            data-testid="composer-exit"
            onClick={attemptExit}
            title="Leave the Composer (or press Esc twice)"
          >
            ← Exit
          </button>
        )}
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
                  {unsaved && steps.length > 0 && <strong className="composer__edited"> · unsaved edits</strong>}
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

      {/* ── Stage: the full-bleed canvas, with the palette and inspector as
          overlay drawers on top of it rather than grid columns beside it ── */}
      <div className="composer-mode__stage">
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
          causalityStates={shownRunStates}
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
          onLaneChange={onLaneChange}
          laneOverrides={draft.laneOverrides}
          onConnectSteps={handleConnectSteps}
          onDeleteStepId={onDeleteStepId}
          onEdgeDelete={onEdgeDelete}
          onReparent={onReparent}
          onSetPivot={onSetPivot}
          onDropCreate={onDropCreate}
          onCanvasDrop={onCanvasDrop}
          onAddStep={() => addBlank('New command step')}
          onStartLibrary={() => onNavigate('library')}
          onStartTtp={() => onNavigate('ttps')}
          onStartBlank={() => addBlank('New command step')}
          onNavigate={onNavigate}
          stitchModel={draft.stitchContext}
          showStitch={showStitch}
          onToggleStitch={() => setShowStitch((v) => !v)}
          storedLayout={draft.layout}
          viewInsets={viewInsets}
        />

        {/* Palette drawer (the node creator) — left overlay, drag items onto
            the canvas or click to add to the selected step. */}
        {paletteOpen ? (
          <aside className="composer-mode__drawer composer-mode__drawer--left" aria-label="Node palette">
            <div className="composer-mode__drawer-head">
              <span className="composer-mode__drawer-title">Add to chain</span>
              <span className="composer__spacer" />
              <button
                type="button" className="ws-ctl" aria-label="Collapse palette"
                title="Collapse palette" onClick={closePalette}
              >
                ⟨
              </button>
            </div>
            <ComposerPalette
              tabs={PALETTE_TABS}
              activeTab={paletteTab}
              onTab={setPaletteTab}
              groups={paletteGroups}
              query={benchQuery}
              onQuery={setBenchQuery}
              loading={shelf.loading}
              draggable
            />
          </aside>
        ) : (
          <button
            type="button"
            className="composer-mode__reopen composer-mode__reopen--left"
            onClick={openPalette}
            title="Open the node palette"
          >
            ＋ Add
          </button>
        )}

        {/* Inspector drawer — right overlay. Shows the selected step's config,
            the workflow meta, or (nothing selected) the teardown + a way in. */}
        {inspectorOpen ? (
          <aside className="composer-mode__drawer composer-mode__drawer--right" aria-label="Step inspector">
            <div className="composer-mode__drawer-head">
              <span className="composer-mode__drawer-title mono" tabIndex={-1}>
                {selected ? selected.id : metaOpen ? 'Workflow' : 'Step config'}
              </span>
              <span className="composer__spacer" />
              <button
                type="button" className="ws-ctl" aria-label="Close inspector"
                title="Close inspector" onClick={closeInspector}
              >
                ⟩
              </button>
            </div>
            {(selected || metaOpen) ? (
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
            )}
          </aside>
        ) : (
          <button
            type="button"
            className="composer-mode__reopen composer-mode__reopen--right"
            onClick={openInspector}
            title="Open the inspector"
          >
            Inspector
          </button>
        )}

        {/* Workstream — bottom overlay dock (payload · preflight · active · history). */}
        <div className={'composer-ws composer-mode__dock' + (wsOpen ? ' composer-ws--open' : '')}>
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

      <ConfirmDialog
        open={!!pendingConfirm}
        title={pendingConfirm?.title}
        body={pendingConfirm?.body}
        confirmLabel={pendingConfirm?.confirmLabel}
        cancelLabel="Stay"
        confirmVariant="danger"
        // Focus the SAFE choice: a destructive default trains Enter-to-dismiss,
        // and the one time the dialog guards real work, Enter would discard it.
        initialFocus="cancel"
        onConfirm={() => { const go = pendingConfirm?.onConfirm; setPendingConfirm(null); if (go) go() }}
        onClose={() => setPendingConfirm(null)}
      />
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
