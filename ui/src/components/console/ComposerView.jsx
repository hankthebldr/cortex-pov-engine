import React, { useCallback, useEffect, useMemo, useState } from 'react'
// Colocated with the component so it ships inside the Composer's own lazy
// chunk rather than on first paint — the convention main.jsx documents for
// every destination stylesheet.
import '../../styles/destinations/composer.css'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import { getScenario } from '../../api/client.js'
import { agentIdOf, runIdOf } from '../../api/ids.js'
import { isRunTerminal } from './runStatus.js'
import useShelf from './useShelf.js'
import useLaunchScenario from './useLaunchScenario.js'
import { HS } from './readiness/healthModel.js'
import {
  appendStep,
  blankStep,
  draftFromScenario,
  duplicateStep,
  emitDraftYaml,
  emptyDraft,
  moveStep,
  nextStepId,
  removeStep,
  validateDraft,
} from './composerDraft.js'

/**
 * ComposerView — the Simulation Composer.
 *
 * THE GAP THIS CLOSES
 * -------------------
 * The console could browse scenarios, launch them, and prove what they
 * detected — but it had no surface on which a chain was BUILT. "New POV run"
 * (the hidden `guided` flow) picks a target for an already-authored scenario;
 * authoring itself happened in a YAML file, in an editor, outside the product.
 * A DC asked to prove a technique the library does not cover had nowhere to go.
 *
 * FOUR REGIONS, one job each:
 *   bench      — what you can add (steps, the scenario library, tools, targets)
 *   canvas     — the chain itself, read start → 01 → 02 → … → end
 *   inspector  — the configuration of the ONE selected step
 *   workstream — what the chain needs to actually run (payload · preflight ·
 *                active run · history)
 *
 * WHAT IT REFUSES TO INVENT
 * -------------------------
 * Everything on this surface is either real or explicitly absent. The draft is
 * seeded from a scenario the API returned (`?from=SIM-…`), never from a
 * built-in demo chain; the preflight tab renders `healthModel`'s real component
 * rows; the payload tab renders the real shelf; history is `env.runs`. Where
 * the redesign showed a field the backend has no concept of (per-step
 * `delay`/`timeout`), it is omitted rather than mocked — see composerDraft.js.
 *
 * Launch is the existing path, not a new one: `useLaunchScenario` against the
 * ORIGIN scenario, which is what SimCore can actually execute. A draft with
 * hand-added steps is explicitly NOT launchable, and says why, because posting
 * it would silently run the original chain while the canvas showed the edited
 * one — a false claim in a customer-facing report.
 */

const WS_TABS = [
  ['payload', 'Payload plan'],
  ['preflight', 'Preflight'],
  ['active', 'Active run'],
  ['history', 'History'],
]

/** Detection-type chip tone, matching the console's existing vocabulary. */
function detTone(type) {
  const t = String(type || '').toUpperCase()
  if (t === 'BIOC') return 'detected'
  if (t === 'XQL' || t === 'ANALYTICS') return 'signal'
  return 'pending'
}

export default function ComposerView({ params = {}, setParams = () => {}, onNavigate = () => {} }) {
  const env = useEnvironment()
  const shelf = useShelf({ adapters: [] })

  // The origin scenario rides the URL (`#/composer?from=SIM-EDR-001`), so a
  // composed draft is a link a DC can send to a colleague and so Library →
  // "Open in Composer" is a plain navigation rather than hidden shared state.
  const fromId = params.from || null
  // ONE fetch, two consumers. `originDetail` is the raw API body — it is what
  // useLaunchScenario needs (execution_identity, pull/push support) — and
  // `origin` is the normalized draft derived from it. Fetching twice would
  // double every scenario request and could leave the two out of step.
  const [originDetail, setOriginDetail] = useState(null)
  const [origin, setOrigin] = useState(null)
  const [originError, setOriginError] = useState(null)
  const [loadingOrigin, setLoadingOrigin] = useState(false)

  const [steps, setSteps] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [canvasView, setCanvasView] = useState('chain')   // 'chain' | 'yaml'
  const [wsTab, setWsTab] = useState('payload')
  const [wsOpen, setWsOpen] = useState(false)
  const [panelsHidden, setPanelsHidden] = useState(false)
  const [benchQuery, setBenchQuery] = useState('')
  const [notice, setNotice] = useState(null)

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
        setSelectedId(next.steps[0]?.id || null)
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

  const draft = useMemo(
    () => ({ ...(origin || emptyDraft()), steps }),
    [origin, steps],
  )
  const validation = useMemo(() => validateDraft(steps), [steps])
  const selected = useMemo(
    () => steps.find((s) => s.id === selectedId) || null,
    [steps, selectedId],
  )

  // A draft is launchable only while it still matches what SimCore would run.
  // See the doc comment: hand-edits are visible here but not on the server.
  const edited = useMemo(() => {
    if (!origin) return false
    if (steps.length !== origin.steps.length) return true
    return steps.some((s, i) => s.id !== origin.steps[i]?.id || s.authored)
  }, [origin, steps])

  const tenantName = env.tenant ? (env.tenant.name || env.tenant.id) : null
  const agentName = env.agent ? (env.agent.hostname || agentIdOf(env.agent)) : null

  // ── Launch (the existing path) ─────────────────────────────────────────────
  const launch = useLaunchScenario(originDetail, {
    onRunComplete: (run) => {
      env.refreshRuns()
      onNavigate('runs', { run: runIdOf(run), tab: 'live' })
    },
    onError: (msg) => say(msg),
  })

  // Preflight is a real gate, not a spinner: it reports the health model's own
  // verdict plus the draft's validation, then unlocks Launch.
  const [preflighted, setPreflighted] = useState(false)
  useEffect(() => { setPreflighted(false) }, [fromId, steps.length])

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

  const downloadYaml = useCallback(() => {
    const yaml = emitDraftYaml(draft, { tenant: tenantName, agent: agentName })
    const blob = new Blob([yaml], { type: 'text/yaml' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${draft.originId || 'sim-draft'}-draft.yml`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    say('Draft YAML downloaded — review it, then drop it into scenarios/ and reload SimCore')
  }, [draft, tenantName, agentName, say])

  // Declared BEFORE the bench memo that calls it: `const` bindings are in the
  // temporal dead zone until their initializer runs, and useMemo runs its
  // factory synchronously during render — so a bench defined first would throw
  // a ReferenceError on the very first paint, not on click.
  const addBlank = useCallback((name) => {
    setSteps((prev) => {
      const id = nextStepId(prev)
      const next = appendStep(prev, blankStep(id, { name }))
      setSelectedId(id)
      return next
    })
  }, [])

  // ── Bench ──────────────────────────────────────────────────────────────────
  // Every group is sourced, never hardcoded content: the scenario library, the
  // enrolled agents, and the shelf's real staged payloads. "Step kinds" is the
  // one static group, and legitimately so — it is a list of things this UI can
  // create, not data about the deployment.
  const bench = useMemo(() => {
    const q = benchQuery.trim().toLowerCase()
    const match = (a, b) => !q || `${a} ${b}`.toLowerCase().includes(q)

    const groups = []

    groups.push({
      label: 'Step kinds',
      tone: 'action',
      items: [
        { key: 'k-command', name: 'Command', meta: 'shell · identity-scoped',
          add: () => addBlank('New command step') },
        { key: 'k-wait', name: 'Wait / jitter', meta: 'pause between steps',
          add: () => addBlank('Wait') },
      ].filter((i) => match(i.name, i.meta)),
    })

    groups.push({
      label: 'Scenario library',
      tone: 'detected',
      items: env.scenarios
        .filter((s) => match(s.scenario_id || s.id, s.name || ''))
        .slice(0, 12)
        .map((s) => {
          const id = s.scenario_id || s.id
          return {
            key: `s-${id}`,
            name: id,
            meta: s.name || s.plane || '',
            // Opening a scenario REPLACES the draft rather than appending its
            // steps: two chains spliced together share no causality spine, so
            // the result would not be one provable narrative.
            add: () => setParams({ from: id }, { replace: true }),
          }
        }),
    })

    groups.push({
      label: 'Targets',
      tone: 'signal',
      items: env.agents
        .filter((a) => match(a.hostname || '', a.os || ''))
        .slice(0, 8)
        .map((a) => {
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
      items: (shelf.shelf?.payloads || [])
        .filter((p) => match(p.name || '', p.adapter_id || ''))
        .slice(0, 8)
        .map((p) => ({
          key: `p-${p.name}`,
          name: p.name,
          meta: p.adapter_id || 'unbound — no pack claims it',
          add: () => onNavigate('adapters'),
        })),
    })

    return groups.filter((g) => g.items.length)
  }, [benchQuery, env, shelf.shelf, onNavigate, setParams, say, addBlank])

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
            {draft.originId ? (
              <>
                <span className="composer__from">from</span>
                <button
                  type="button"
                  className="linklike mono"
                  onClick={() => onNavigate('library', { open: draft.originId })}
                >
                  {draft.originId}
                </button>
                <span className="composer__meta">
                  · {validation.counts.steps} steps · {validation.counts.techniques} techniques
                  {edited && <strong className="composer__edited"> · edited</strong>}
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
          onClick={downloadYaml}
          data-testid="composer-download-yaml"
          title="Emit this chain as scenario YAML you can drop into scenarios/"
        >
          Download draft YAML
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
          disabled={!preflighted || edited || launch.launchDisabled || !originDetail}
          onClick={() => launch.launch()}
          title={
            edited
              ? 'This draft has hand-edits SimCore does not have. Download the YAML and load it before launching.'
              : !preflighted
                ? 'Run preflight first'
                : `Launch ${draft.originId} on ${agentName || 'the selected agent'}`
          }
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

      {notice && (
        <div className="composer__notice" role="status" data-testid="composer-notice">{notice}</div>
      )}

      {/* ── bench · canvas · inspector ── */}
      <div className={'composer__grid' + (showPanels ? '' : ' composer__grid--solo')}>
        {showPanels && (
          <aside className="composer-bench" aria-label="Composer bench">
            <input
              className="composer-bench__filter"
              value={benchQuery}
              onChange={(e) => setBenchQuery(e.target.value)}
              placeholder="Filter bench…"
              aria-label="Filter the bench"
            />
            {bench.map((group) => (
              <div className="composer-bench__group" key={group.label}>
                <div className="composer-bench__group-title">{group.label}</div>
                {group.items.map((it) => (
                  <button
                    type="button"
                    key={it.key}
                    className="bench-item"
                    onClick={it.add}
                    title={it.meta}
                  >
                    <span className={`bench-item__dot bench-item__dot--${group.tone}`} aria-hidden="true" />
                    <span className="bench-item__text">
                      <span className="bench-item__name">{it.name}</span>
                      <span className="bench-item__meta mono">{it.meta}</span>
                    </span>
                    <span className="bench-item__add" aria-hidden="true">+</span>
                  </button>
                ))}
              </div>
            ))}
            {!bench.length && (
              <div className="composer-bench__empty">nothing matches “{benchQuery}”</div>
            )}
          </aside>
        )}

        <section className="composer-canvas" aria-label="Chain canvas">
          <div className="composer-canvas__head">
            <span className="mono composer-canvas__id">{draft.originId || 'no scenario'}</span>
            <span className="composer-canvas__name">{draft.name || 'Open a scenario, or add a step'}</span>
            <span className="composer__spacer" />
            <div className="composer-canvas__views" role="group" aria-label="Canvas view">
              {[['chain', 'Chain'], ['yaml', 'YAML']].map(([id, label]) => (
                <button
                  type="button"
                  key={id}
                  className={'canvas-view' + (canvasView === id ? ' canvas-view--on' : '')}
                  aria-pressed={canvasView === id}
                  onClick={() => setCanvasView(id)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="composer-canvas__meta">
            {validation.counts.steps} steps
            {draft.cgo && <> · single process_lineage spine · CGO {draft.cgo}</>}
          </div>

          {originError && (
            <div className="composer-canvas__error" role="alert" data-testid="composer-origin-error">
              <strong>{fromId} could not be loaded.</strong> {originError} — the canvas below is
              empty because nothing could be read, not because the scenario has no steps.
            </div>
          )}

          {canvasView === 'yaml' ? (
            <pre className="composer-yaml mono" data-testid="composer-yaml">
              {emitDraftYaml(draft, { tenant: tenantName, agent: agentName })}
            </pre>
          ) : (
            <>
              {!steps.length && !loadingOrigin && !originError && (
                <div className="composer-firstrun" data-testid="composer-firstrun">
                  <div className="composer-firstrun__title">Start a simulation three ways</div>
                  <p className="composer-firstrun__body">
                    A simulation is an ordered chain of steps run against one agent. Each step
                    declares the detection you expect Cortex to raise — that pairing is what a
                    POV proves.
                  </p>
                  <div className="composer-firstrun__options">
                    <button type="button" className="firstrun-option" onClick={() => onNavigate('library')}>
                      <span className="firstrun-option__num">1</span>
                      <span className="firstrun-option__text">
                        <span className="firstrun-option__title">Start from a library scenario</span>
                        <span className="firstrun-option__note">
                          Fastest path — the chains in the Library arrive complete with their
                          expected detections.
                        </span>
                      </span>
                      <span className="firstrun-option__arrow" aria-hidden="true">→</span>
                    </button>
                    <button type="button" className="firstrun-option" onClick={() => onNavigate('ttps')}>
                      <span className="firstrun-option__num">2</span>
                      <span className="firstrun-option__text">
                        <span className="firstrun-option__title">Start from a TTP card</span>
                        <span className="firstrun-option__note">
                          Pick the detection you need to prove; the card supplies the technique
                          and its detector.
                        </span>
                      </span>
                      <span className="firstrun-option__arrow" aria-hidden="true">→</span>
                    </button>
                    <button type="button" className="firstrun-option" onClick={() => addBlank('New command step')}>
                      <span className="firstrun-option__num">3</span>
                      <span className="firstrun-option__text">
                        <span className="firstrun-option__title">Start from a blank step</span>
                        <span className="firstrun-option__note">
                          Author the command yourself, then declare what Cortex should raise.
                        </span>
                      </span>
                      <span className="firstrun-option__arrow" aria-hidden="true">→</span>
                    </button>
                  </div>
                </div>
              )}

              {loadingOrigin && <div className="destination-loading">loading {fromId}…</div>}

              {!!steps.length && (
                <div className="chain" data-testid="composer-chain">
                  {/* START anchor — the chain has an explicit beginning, so it
                      reads start → 01 → 02 → … → end rather than as a pile of
                      equal cards. Scope lives here (not in the global bar)
                      because it is a property of THIS launch. */}
                  <div className="chain__anchor">
                    <div className="chain-node chain-node--start" data-testid="chain-start">
                      <div className="chain-node__kicker">Start</div>
                      <div className="chain-node__title">On launch</div>
                      <div className="chain-node__scope">
                        <button type="button" className="scope-link" onClick={() => onNavigate('tenants')}>
                          <span className="scope-link__label">Tenant</span>
                          <span className="scope-link__value mono">{tenantName || 'none selected'}</span>
                        </button>
                        <button type="button" className="scope-link" onClick={() => onNavigate('agents')}>
                          <span className="scope-link__label">Agent</span>
                          <span className="scope-link__value mono">{agentName || 'none selected'}</span>
                        </button>
                        <button type="button" className="scope-link" onClick={() => onNavigate('environments')}>
                          <span className="scope-link__label">Lab</span>
                          <span className="scope-link__value mono">environments</span>
                        </button>
                      </div>
                    </div>
                    <span className="chain__spine" aria-hidden="true" />
                  </div>

                  {steps.map((s, i) => (
                    <div className="chain__anchor" key={s.id}>
                      <div
                        className={
                          'chain-node chain-node--step'
                          + (selectedId === s.id ? ' chain-node--selected' : '')
                          + (s.detections.length ? '' : ' chain-node--nodetect')
                        }
                      >
                        <button
                          type="button"
                          className="chain-node__body"
                          onClick={() => setSelectedId(s.id)}
                          aria-pressed={selectedId === s.id}
                          data-testid={`chain-step-${s.id}`}
                        >
                          <span className="chain-node__row">
                            <span className="chain-node__kind">{s.authored ? 'new' : 'step'}</span>
                            <span className="chain-node__id mono">{s.id}</span>
                            <span className="composer__spacer" />
                            <span className="chain-node__order mono">
                              {String(i + 1).padStart(2, '0')}
                            </span>
                          </span>
                          <span className="chain-node__name">{s.name}</span>
                          <span className="chain-node__sub mono">
                            {s.technique || 'no technique'} · {s.identity || 'no identity'}
                          </span>
                          <span className="chain-node__chips">
                            {s.detections.length ? (
                              s.detections.map((d, k) => (
                                <span key={k} className={`chip chip--${detTone(d.type)}`}>
                                  {d.type || '?'}
                                </span>
                              ))
                            ) : (
                              /* Not decoration — this is the state that turns
                                 into a GAP in the POV readout. */
                              <span className="chip chip--missed">no expected detection</span>
                            )}
                          </span>
                        </button>
                        <div className="chain-node__tools">
                          <button type="button" title="Move earlier" aria-label={`Move ${s.id} earlier`}
                            onClick={() => setSteps((p) => moveStep(p, i, -1))}>↑</button>
                          <button type="button" title="Move later" aria-label={`Move ${s.id} later`}
                            onClick={() => setSteps((p) => moveStep(p, i, 1))}>↓</button>
                          <button type="button" title="Duplicate step" aria-label={`Duplicate ${s.id}`}
                            onClick={() => setSteps((p) => duplicateStep(p, i))}>⧉</button>
                          <button type="button" title="Remove step" aria-label={`Remove ${s.id}`}
                            onClick={() => setSteps((p) => removeStep(p, i))}>×</button>
                        </div>
                      </div>
                      <span className="chain__spine" aria-hidden="true" />
                    </div>
                  ))}

                  <button
                    type="button"
                    className="chain-node chain-node--add"
                    onClick={() => addBlank('New command step')}
                    data-testid="composer-add-step"
                  >
                    + Add step
                  </button>

                  <div className="chain__anchor">
                    <span className="chain__spine" aria-hidden="true" />
                    <div className="chain-node chain-node--end" data-testid="chain-end">
                      <div className="chain-node__kicker">End</div>
                      <div className="chain-node__title">Teardown &amp; proof</div>
                      <div className="chain-node__sub mono">
                        {validation.counts.detections} detections asserted
                        {draft.teardown.length
                          ? ` · ${draft.teardown.length} cleanup command${draft.teardown.length === 1 ? '' : 's'}`
                          : ' · no cleanup declared'}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </section>

        {showPanels && (
          <aside className="composer-inspector" aria-label="Step configuration">
            <div className="composer-inspector__head">
              <span className="composer-inspector__title">Step config</span>
              <span className="composer__spacer" />
              <span className="mono composer-inspector__id">{selected?.id || '—'}</span>
            </div>

            {!selected ? (
              <div className="composer-inspector__empty">
                Select a step on the canvas to configure it.
              </div>
            ) : (
              <>
                <div className="field-label">Step name</div>
                <div className="field-value">{selected.name}</div>

                <div className="field-label">Command</div>
                <pre className="field-code mono">
                  {selected.command
                    ?? 'not carried by this endpoint — open the scenario detail'}
                </pre>

                <div className="composer-inspector__pair">
                  <div>
                    <div className="field-label">Identity</div>
                    <div className="field-value mono">{selected.identity || 'not declared'}</div>
                  </div>
                  <div>
                    <div className="field-label">Technique</div>
                    <div className="field-value mono">{selected.technique || 'not declared'}</div>
                  </div>
                </div>

                <div className="field-label">Causality</div>
                <div className="field-value mono">
                  {selected.causalityParent
                    ? `parent ${selected.causalityParent} · pivot ${selected.causalityPivot || 'process_lineage'}`
                    : 'root of chain'}
                </div>

                <div className="field-label">Platforms</div>
                <div className="field-value mono">
                  {selected.platforms.length ? selected.platforms.join(' · ') : 'not declared'}
                </div>

                <div className="field-label">
                  Expected detections
                  <span className="field-label__count mono"> {selected.detections.length}</span>
                </div>
                {selected.detections.length ? (
                  selected.detections.map((d, k) => (
                    <div className="detection-card" key={k}>
                      <div className="detection-card__head">
                        <span className={`chip chip--${detTone(d.type)}`}>{d.type || '?'}</span>
                        {d.ttpRef && (
                          <button
                            type="button"
                            className="linklike mono"
                            onClick={() => onNavigate('ttps', { ttp: d.ttpRef })}
                          >
                            {d.ttpRef}
                          </button>
                        )}
                      </div>
                      <div className="detection-card__desc">{d.description || 'no description'}</div>
                      {d.detectionId && (
                        <div className="detection-card__id mono">{d.detectionId}</div>
                      )}
                    </div>
                  ))
                ) : (
                  <div className="detection-card detection-card--empty">
                    This step declares no expected detection. It will execute and then be
                    reported as a gap — bind a TTP card from the TTP Cards surface.
                    <button
                      type="button"
                      className="btn btn--xs"
                      onClick={() => onNavigate('ttps')}
                    >
                      Browse TTP cards
                    </button>
                  </div>
                )}

                <div className="field-label">Teardown</div>
                <div className="field-value">
                  {draft.teardown.length ? (
                    <>
                      <span className="field-note">
                        Scenario-level (the schema has no per-step cleanup):
                      </span>
                      <pre className="field-code mono">{draft.teardown.join('\n')}</pre>
                    </>
                  ) : 'no cleanup declared for this scenario'}
                </div>
              </>
            )}
          </aside>
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
            title="Show or hide the bench and inspector"
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
