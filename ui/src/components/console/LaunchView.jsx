import React, { useEffect, useMemo, useState } from 'react'
import useLaunchScenario, { PUSH_FORMATS, bundleIsWindows } from './useLaunchScenario.js'
import LaunchErrorNotice, { DownloadOutcome, LaunchSuccessNotice, WindowsBundleNote } from './LaunchErrorNotice.jsx'
import AttestationFields, { isAttestationError } from './AttestationFields.jsx'
import LaunchGateNotice, { GATE_TOOL_ABSENT, GATE_POSTURE } from './LaunchGateNotice.jsx'
import { getToolAdapters, listIntegrations, getScenarioDoors, getDoors } from '../../api/client.js'
import useShelf from './useShelf.js'
import { SUPPLY, supplyOf, shortDigest } from './supplyState.js'
import ScenarioPreflightCard from './ScenarioPreflightCard.jsx'
import { preflightScenario } from './readiness/scenarioPreflight.js'
import { useFirstUseHint } from '../onboarding/useFirstUseHint.js'
import FirstUseHint from '../onboarding/FirstUseHint.jsx'

/**
 * The launch result as LaunchGateNotice reads it. Its warning list renders a
 * warning's `code`, `agent_id`, `steps` and string `detail`. A launch
 * reservation parsed from a string ("TARGET_AGENT_OFFLINE: …") carries its
 * sentence as `text` and has none of those fields, so it would show as a bare
 * code: the sentence that says the beacon is not polling would be dropped.
 * Such an entry gets its `text` as the `detail` the notice prints. A gate
 * warning object keeps its own fields untouched.
 */
function forGateNotice(lastRun) {
  if (!lastRun || lastRun.status !== 'success' || !Array.isArray(lastRun.warnings)) return lastRun
  return {
    ...lastRun,
    warnings: lastRun.warnings.map((w) => {
      if (!w || typeof w !== 'object') return w
      const shown = w.agent_id || (Array.isArray(w.steps) && w.steps.length)
        || (typeof w.detail === 'string' && w.detail)
      return shown || !w.text ? w : { ...w, detail: w.text }
    }),
  }
}

/**
 * LaunchView — ③ Launch: arm a scenario against a target and fire.
 *
 * The step that makes "Launch" legible. It composes two earlier choices —
 * the armed scenario (from ② Library) and the selected target (from
 * ① Targets) — then derives pull/push mode from the target so the operator
 * never has to reason about modes. See docs/design/console-redesign-v2.md.
 *
 * Props:
 *   scenario        — full scenario detail (or null)
 *   selectedTarget  — { kind:'agent'|'push'|'iac', id, label } | null
 *   onRunComplete   — (run) => void
 *   onError         — (msg) => void
 *   onGoLibrary     — () => void
 *   onGoTargets     — () => void
 */
export default function LaunchView({
  scenario = null,
  selectedTarget = null,
  payloadPlan = null,
  // True only while a `?plan=` deep link's chunk is still resolving (see
  // destinations.jsx::useDecodedPlan, I-1). Distinct from "no plan" — a run
  // launched in this window would silently drop the composed payload plan.
  payloadPlanResolving = false,
  onRunComplete = () => {},
  onError = () => {},
  onGoLibrary = () => {},
  onGoTargets = () => {},
  onNavigate = null,
}) {
  const launch = useLaunchScenario(scenario, {
    onRunComplete, onError, payloadPlan, payloadPlanResolving, holdOnWarnings: true,
  })
  // A gate refusal renders as LaunchGateNotice instead of the generic line: the
  // server's sentence names an API override the operator acts on via a button.
  const gateRefusal = launch.lastRun?.status === 'error' && launch.lastRun.gate
    && [GATE_TOOL_ABSENT, GATE_POSTURE].includes(launch.lastRun.code)
  // holdOnWarnings kept a warned launch here instead of navigating to its run.
  const heldOnWarnings = launch.lastRun?.status === 'success'
    && Array.isArray(launch.lastRun.warnings) && launch.lastRun.warnings.length > 0
  const gateNoticeRun = useMemo(() => forGateNotice(launch.lastRun), [launch.lastRun])

  // First-use hint on Launch (I6 / spec §5) — one of the five named
  // consequential controls (Arm · Launch · Abort · Reconcile · Export POV).
  // Clears on USE (the click that actually fires the run), never on the
  // hint's own dismiss — a dismissal you didn't act on is not evidence you
  // learned the control. `launchHintDismissed` is separate, SESSION-local
  // state for the dismiss (×) affordance; see FirstUseHint.jsx's hook-
  // integration test for why the two must stay independent.
  const launchHint = useFirstUseHint('launch')
  const [launchHintDismissed, setLaunchHintDismissed] = useState(false)
  const handleLaunch = () => { launchHint.onUse(); launch.launch() }

  // Derive mode + agent from the chosen target — the operator picks a target,
  // not a transport. agent → pull; push/iac → push bundle.
  const targetMode = selectedTarget?.kind === 'agent' ? 'pull' : 'push'
  useEffect(() => {
    if (!selectedTarget) return
    launch.setMode(targetMode)
    if (selectedTarget.kind === 'agent') launch.setSelectedAgent(selectedTarget.id)
  }, [selectedTarget, targetMode]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Adapter consent gate ──────────────────────────────────────────────
  // The orchestrator refuses launches that touch dual-use / c2 adapters
  // without matching consent. Resolve the scenario's external_tools adapter
  // refs against the catalog so we can prompt for exactly the right consent.
  const [adapterIndex, setAdapterIndex] = useState({}) // adapter_id → {name, safety_class}
  useEffect(() => {
    let cancelled = false
    getToolAdapters()
      .then((d) => {
        const list = Array.isArray(d) ? d : (d && d.adapters) || []
        if (cancelled) return
        setAdapterIndex(Object.fromEntries(list.map((a) => [a.adapter_id, a])))
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  const gated = useMemo(() => {
    const refs = (scenario?.external_tools || []).map((t) => t.adapter_ref).filter(Boolean)
    const resolved = refs.map((r) => adapterIndex[r]).filter(Boolean)
    return {
      dualUse: resolved.filter((a) => a.safety_class === 'dual-use-lab-only'),
      c2:      resolved.filter((a) => a.safety_class === 'c2-framework'),
    }
  }, [scenario, adapterIndex])

  // ── Payload supply ────────────────────────────────────────────────────
  // Which of this scenario's tools will be fetched by the TARGET at run time,
  // and whether anything the scenario DECLARES is missing from the shelf.
  const adapterList = useMemo(() => Object.values(adapterIndex), [adapterIndex])
  const shelf = useShelf({ adapters: adapterList })

  const scenarioTools = useMemo(() => {
    const refs = (scenario?.external_tools || [])
      .map((t) => t.adapter_ref).filter(Boolean)
    return Array.from(new Set(refs))
      .map((r) => adapterIndex[r])
      .filter(Boolean)
      .map((a) => ({ adapter: a, supply: supplyOf(a, shelf.shelf) }))
  }, [scenario, adapterIndex, shelf.shelf])

  const egressTools = scenarioTools.filter(
    (t) => t.supply.state === SUPPLY.UNSTAGED || t.supply.state === SUPPLY.RUNTIME_FETCH,
  )

  // A scenario that DECLARES cluster_posture.payloads and is missing one is a
  // hard stop: SimCore's own guard 409s PAYLOAD_NOT_STAGED, and a named dead
  // button beats a 409 mid-demo.
  const missingDeclaredPayloads = useMemo(() => {
    const declared = scenario?.cluster_posture?.payloads || []
    if (!declared.length || !shelf.shelf.available) return []
    return declared.filter((n) => !shelf.shelf.stagedNames.has(n))
  }, [scenario, shelf.shelf])

  // ── Preflight inputs ──────────────────────────────────────────────────
  // Integrations answer "can anything measure this run?". A failure here is
  // NULL, not [] — "we could not ask" and "nothing is configured" produce
  // different advice and must not render the same.
  const [integrations, setIntegrations] = useState(null)
  useEffect(() => {
    let cancelled = false
    listIntegrations()
      .then((list) => { if (!cancelled) setIntegrations(Array.isArray(list) ? list : null) })
      .catch(() => { if (!cancelled) setIntegrations(null) })
    return () => { cancelled = true }
  }, [])

  // Door readiness for THIS scenario against the selected agent. A failure is
  // null readiness plus the error text, rendered UNKNOWN by the Data path check
  // ("door readiness unavailable: …", as the library chip does), never a pass.
  const [doorReadiness, setDoorReadiness] = useState(null)
  const [doorRows, setDoorRows] = useState(null)
  const [doorError, setDoorError] = useState(null)
  const doorScenarioId = scenario ? (scenario.scenario_id || scenario.id) : null
  const doorAgentId = selectedTarget?.kind === 'agent' ? selectedTarget.id : null
  useEffect(() => {
    setDoorReadiness(null)
    setDoorRows(null)
    setDoorError(null)
    if (!doorScenarioId) return undefined
    let cancelled = false
    Promise.all([getScenarioDoors(doorScenarioId, doorAgentId), getDoors()])
      .then(([data, doors]) => {
        if (cancelled) return
        setDoorReadiness(data.readiness)
        setDoorRows(doors.doors)
      })
      .catch((err) => {
        if (cancelled) return
        setDoorReadiness(null)
        setDoorRows(null)
        setDoorError((err && err.message) || 'door readiness failed')
      })
    return () => { cancelled = true }
  }, [doorScenarioId, doorAgentId])

  const preflight = useMemo(() => preflightScenario({
    scenario,
    target: selectedTarget,
    agents: launch.agents,
    shelf: shelf.shelf,
    adapterIndex,
    integrations,
    identity: launch.identity,
    pushFormat: launch.pushFormat,
    doorReadiness,
    doorRows,
    doorAgentId,
    doorError,
  }), [scenario, selectedTarget, launch.agents, shelf.shelf, adapterIndex, integrations,
       launch.identity, launch.pushFormat, doorReadiness, doorRows, doorAgentId, doorError])

  const needsSim = gated.dualUse.length > 0
  const needsC2  = gated.c2.length > 0
  const consentBlocked =
    (needsSim && !launch.consent.simulation_authorized) ||
    (needsC2 && !launch.consent.c2_authorized)
  const toggleConsent = (key) =>
    launch.setConsent((c) => ({ ...c, [key]: !c[key] }))

  // Every independent reason Launch is dead, in one list the operator can read.
  // The hook owns scenario/agent readiness; the target pick and the consent
  // gate are this view's, so they are folded in here rather than duplicated.
  const blockersId = 'launch-blockers'
  const blockers = useMemo(() => {
    const out = [...(launch.blockers || [])]
    if (!selectedTarget) out.push('Pick a target — an agent (pull) or the offline bundle (push)')
    if (needsSim && !launch.consent.simulation_authorized) {
      out.push('Tick the lab-only consent for the dual-use tools this scenario uses')
    }
    if (needsC2 && !launch.consent.c2_authorized) {
      out.push('Tick the C2-framework consent for this scenario')
    }
    for (const name of missingDeclaredPayloads) {
      out.push(
        `${name} is declared by this scenario but is not ready on this instance — the launch will `
        + 'be refused (PAYLOAD_NOT_STAGED). Make it ready in Packages.',
      )
    }
    // Preflight rows marked BLOCK are conditions SimCore's own guards refuse
    // (or that cannot execute at all). They belong in the same list, in the
    // operator's words. `tools` and the no-target case are already stated
    // above; adding them again would read as two separate problems.
    for (const c of preflight.blocking) {
      if (c.id === 'tools') continue
      if (c.id === 'target' && !selectedTarget) continue
      out.push(`${c.label}: ${c.summary} — ${c.remediation || c.detail}`)
    }
    return out
  }, [launch.blockers, selectedTarget, needsSim, needsC2, launch.consent, missingDeclaredPayloads,
      preflight.blocking])

  // ── Guard rails — guide the operator back to the missing step ──────────
  if (!scenario) {
    return (
      <div className="launch launch--empty">
        <div className="launch-gate">
          <div className="launch-gate__num">②</div>
          <h2>No scenario armed</h2>
          <p>Pick a scenario in Simulate to arm it for launch.</p>
          <button type="button" className="btn btn--primary" onClick={onGoLibrary}>Go to Library ▸</button>
        </div>
      </div>
    )
  }

  const sid = scenario.scenario_id || scenario.id

  return (
    <div className="launch">
      <header className="view-head">
        <div>
          {/* h2: Launch is a step of the guided "New POV run" page, which owns
              the page's one h1. */}
          <h2>Launch</h2>
          <p className="view-head__meta">
            Arm <strong className="mono">{sid}</strong> against a target, then fire the simulation.
          </p>
        </div>
      </header>

      <div className="launch__cols">
        {/* armed scenario summary */}
        <section className="launch-card">
          <div className="launch-card__kicker">Armed scenario</div>
          <div className="launch-card__title">{scenario.name || sid}</div>
          <div className="launch-card__meta mono">
            {sid} · {scenario.plane} · {scenario.mitre_technique || '—'}
          </div>
          <p className="launch-card__desc">{scenario.tc_name || scenario.uc_name || ''}</p>
          <button type="button" className="btn" onClick={onGoLibrary}>Change scenario</button>
        </section>

        {/* preflight — every precondition, checked here rather than discovered
            mid-demo. Rendered before the payload plan because tool supply is
            one of the things it reports on. */}
        <ScenarioPreflightCard
          scenario={scenario}
          target={selectedTarget}
          agents={launch.agents}
          shelf={shelf.shelf}
          adapterIndex={adapterIndex}
          integrations={integrations}
          identity={launch.identity}
          pushFormat={launch.pushFormat}
          doorReadiness={doorReadiness}
          doorRows={doorRows}
          doorAgentId={doorAgentId}
          doorError={doorError}
          onNavigate={onNavigate}
        />

        {/* payload plan — what tooling this run carries, and who fetches it */}
        <PayloadPlanCard
          plan={payloadPlan}
          resolving={payloadPlanResolving}
          planAccepted={launch.payloadPlanAccepted}
          egressTools={egressTools}
          shelfAvailable={shelf.available}
          onGoShelf={onNavigate ? () => onNavigate('adapters', { supply: SUPPLY.UNSTAGED }) : null}
          onEdit={onNavigate && payloadPlan
            ? () => onNavigate('adapters', {
                tool: payloadPlan.artifacts?.[0]?.adapter_id || null,
                panel: 'compose',
                scenario: sid,
              })
            : null}
        />

        {/* target + config */}
        <section className="launch-card launch-card--config">
          <div className="launch-card__kicker">Target</div>
          {selectedTarget ? (
            <div className={`launch-target launch-target--${selectedTarget.kind}`}>
              <span className="launch-target__label mono">{selectedTarget.label || selectedTarget.id}</span>
              <span className="launch-target__mode">{targetMode} mode</span>
              <button type="button" className="btn btn--xs" onClick={onGoTargets}>Change</button>
            </div>
          ) : (
            <div className="launch-target launch-target--none">
              <span>No target selected</span>
              <button type="button" className="btn" onClick={onGoTargets}>Pick a target ▸</button>
            </div>
          )}

          {/* identity */}
          {launch.identityOptions.length > 0 && (
            <label className="launch-field">
              <span className="launch-field__label">Execution identity</span>
              <select
                className="launch-select"
                value={launch.identity}
                onChange={(e) => launch.setIdentity(e.target.value)}
              >
                {launch.identityOptions.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            </label>
          )}

          {/* pull: agent / push: format */}
          {targetMode === 'pull' && selectedTarget?.kind === 'agent' && (
            <div className="launch-field">
              <span className="launch-field__label">Beacon</span>
              <span className="mono">{selectedTarget.id}</span>
            </div>
          )}
          {targetMode === 'pull' && <AttestationFields launch={launch} />}
          {targetMode === 'push' && (
            <label className="launch-field">
              <span className="launch-field__label">Bundle format</span>
              <select
                className="launch-select"
                value={launch.pushFormat}
                onChange={(e) => launch.setPushFormat(e.target.value)}
              >
                {PUSH_FORMATS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
              </select>
            </label>
          )}
          {/* A .ps1 is authored and parse-checked only. Said BEFORE the
              download, where the format is chosen, not discovered at exit 2. */}
          {targetMode === 'push' && bundleIsWindows(scenario, launch.pushFormat) && (
            <WindowsBundleNote testId="launch-windows-bundle-note" />
          )}

          {/* dual-use / c2 consent gate */}
          {(needsSim || needsC2) && (
            <div className={'launch-consent' + (consentBlocked ? ' launch-consent--blocking' : '')}>
              <div className="launch-consent__title">
                ⚠ Tool consent required
                {consentBlocked && <span className="launch-consent__flag"> · blocking launch</span>}
              </div>
              {needsSim && (
                <label className="launch-consent__row">
                  <input
                    type="checkbox"
                    checked={!!launch.consent.simulation_authorized}
                    onChange={() => toggleConsent('simulation_authorized')}
                  />
                  <span>
                    I authorize <strong>lab-only</strong> use of dual-use tools (
                    {gated.dualUse.map((a) => a.name).join(', ')}).
                  </span>
                </label>
              )}
              {needsC2 && (
                <label className="launch-consent__row launch-consent__row--c2">
                  <input
                    type="checkbox"
                    checked={!!launch.consent.c2_authorized}
                    onChange={() => toggleConsent('c2_authorized')}
                  />
                  <span>
                    I authorize <strong>C2 framework</strong> execution (
                    {gated.c2.map((a) => a.name).join(', ')}) on authorized targets only.
                  </span>
                </label>
              )}
            </div>
          )}

          {/* primary action */}
          <div className="launch-actions">
            {targetMode === 'push' ? (
              <>
                <button
                  type="button"
                  className="btn btn--primary btn--lg"
                  disabled={launch.launching || blockers.length > 0}
                  aria-describedby={blockers.length ? blockersId : undefined}
                  onClick={handleLaunch}
                >
                  {launch.launching ? 'Launching…' : 'Launch run ▸'}
                </button>
                <button
                  type="button"
                  className="btn btn--lg"
                  disabled={launch.downloading}
                  onClick={launch.downloadPushBundle}
                >
                  {launch.downloading ? 'Building…' : '↓ Download bundle'}
                </button>
              </>
            ) : (
              <button
                type="button"
                className="btn btn--primary btn--lg"
                disabled={launch.launching || blockers.length > 0}
                aria-describedby={blockers.length ? blockersId : undefined}
                onClick={handleLaunch}
              >
                {launch.launching ? 'Launching…' : 'Launch run ▸'}
              </button>
            )}
            {launchHint.show && !launchHintDismissed && (
              <FirstUseHint
                show
                text="Launch fires the armed scenario against the selected target."
                onDismiss={() => setLaunchHintDismissed(true)}
              />
            )}
          </div>

          {/* The download's own outcome, beside the button that asked for it.
              A refusal names its steps; a refusal that another format can
              satisfy offers that format in one click. */}
          {targetMode === 'push' && (
            <DownloadOutcome launch={launch} scenario={scenario} testId="launch-download-result" />
          )}

          {/* Why the button is dead. Rendered next to it, tied by
              aria-describedby so a screen reader hears the reason too. */}
          {blockers.length > 0 && (
            <ul className="launch-blockers" id={blockersId} data-testid="launch-blockers">
              {blockers.map((b) => (
                <li key={b} className="launch-blockers__item">
                  <span aria-hidden="true">▸</span> {b}
                </li>
              ))}
            </ul>
          )}

          {/* A clean start is LaunchSuccessNotice's line. A launch held on its
              warnings is LaunchGateNotice's: it lists every warning (gate
              advisories and launch reservations alike) and owns the "View run"
              that continues — listing them in both would say it twice. */}
          {!heldOnWarnings && (
            <LaunchSuccessNotice result={launch.lastRun} testId="launch-success" />
          )}
          {launch.lastRun?.status === 'error' && !gateRefusal
            && !(targetMode === 'pull' && isAttestationError(launch.lastRun)) && (
            <LaunchErrorNotice result={launch.lastRun} testId="launch-error" />
          )}
          <LaunchGateNotice
            lastRun={gateNoticeRun}
            launching={launch.launching}
            onContinue={launch.continueToRun}
            onLaunchAnyway={() => launch.launch({ allowMissingTools: true })}
          />
        </section>
      </div>
    </div>
  )
}

/**
 * PayloadPlanCard — what tooling this run carries and who has to reach the
 * internet for it.
 *
 * Four states, none of them silent:
 *   resolving      → a `?plan=` deep link's chunk is still loading (I-1). This
 *                    is NOT "no plan" — rendering nothing here would let a
 *                    consultant launch before the plan ever reached the run.
 *   plan present   → artifact, digest, destination, and whether it was renamed.
 *   no plan, tools → a WARNING that the target will fetch them itself. Launch
 *                    stays enabled: the DC may know egress is fine, and a
 *                    consent tick on 50 tools is ceremony. The goal is
 *                    legibility, not permission.
 *   nothing        → the card does not render.
 */
function PayloadPlanCard({
  plan, resolving = false, planAccepted, egressTools, shelfAvailable, onGoShelf, onEdit,
}) {
  const artifacts = plan?.artifacts || []
  if (!resolving && !artifacts.length && !egressTools.length) return null

  if (resolving) {
    return (
      <section className="launch-card launch-card--payload" data-testid="launch-payload-plan-resolving">
        <div className="launch-card__kicker">Tools for this run</div>
        <p className="launch-payload__resolving">
          Resolving the tools chosen for this run from this link&apos;s{' '}
          <span className="mono">?plan=</span> parameter — Launch stays disabled until it settles,
          so the run cannot go out with them silently dropped.
        </p>
      </section>
    )
  }

  return (
    <section className="launch-card launch-card--payload" data-testid="launch-payload-plan">
      <div className="launch-card__kicker">
        Tools for this run
        {onEdit && artifacts.length > 0 && (
          <button type="button" className="btn btn--xs launch-edit-btn" onClick={onEdit}>
            Edit ▸
          </button>
        )}
      </div>

      {artifacts.map((a) => (
        <div key={a.payload_name} className="launch-payload__row" data-testid={`launch-payload-${a.payload_name}`}>
          <div className="mono launch-payload__path">{a.payload_name} → {a.dest_path}</div>
          <div className="mono launch-payload__meta">
            sha256 {shortDigest(a.sha256)} · supplied by this instance
          </div>
          {a.renamed && (
            <div className="launch-payload__renamed" data-testid="launch-payload-renamed">
              ⚠ renamed — detections keyed on the tool&apos;s own filename will not fire.
              <strong> That is the control, not a miss.</strong> Behavioural detections must still
              fire; if nothing fires, the finding is that the coverage was name-keyed.
            </div>
          )}
        </div>
      ))}

      {/* THE ANTI-FALSE-GREEN LINE. A plan the server would ignore must never
          render as a plan the run honoured. */}
      {artifacts.length > 0 && planAccepted === false && (
        <div className="launch-payload__warn" data-testid="launch-payload-unsupported">
          This SimCore&apos;s <span className="mono">POST /api/runs</span> does not accept a{' '}
          <span className="mono">payload_plan</span>, so the console will not send one — an unknown
          field is silently dropped, and the run would execute the scenario&apos;s own commands
          unchanged while this card claimed a rename. The tool is ready and supplied either way;
          the destination above is <strong>not</strong> in effect for this run.
        </div>
      )}

      {egressTools.length > 0 && (
        <div className="launch-payload__warn" data-testid="launch-payload-egress">
          <strong>{egressTools.length}</strong>{' '}
          {egressTools.length === 1 ? 'tool needs' : 'tools need'} internet access on the
          target:{' '}
          <span className="mono">
            {egressTools.map((t) => t.adapter.name).join(' · ')}
          </span>
          . A network that blocks it leaves the step running without its tool.
          {onGoShelf && (
            <button type="button" className="btn btn--xs launch-edit-btn" onClick={onGoShelf}>
              Make them ready ▸
            </button>
          )}
        </div>
      )}

      {shelfAvailable === false && (
        <div className="launch-payload__warn" data-testid="launch-payload-unknown">
          This instance does not report tool readiness, so the tools for this run are{' '}
          <strong>UNKNOWN</strong> — not &ldquo;fine&rdquo;.
        </div>
      )}
    </section>
  )
}
