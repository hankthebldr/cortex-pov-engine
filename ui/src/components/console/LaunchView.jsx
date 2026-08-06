import React, { useEffect, useMemo, useState } from 'react'
import useLaunchScenario from './useLaunchScenario.js'
import { getToolAdapters, listIntegrations } from '../../api/client.js'
import useShelf from './useShelf.js'
import { SUPPLY, supplyOf, shortDigest } from './supplyState.js'
import ScenarioPreflightCard from './ScenarioPreflightCard.jsx'
import { preflightScenario } from './readiness/scenarioPreflight.js'

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
  onRunComplete = () => {},
  onError = () => {},
  onGoLibrary = () => {},
  onGoTargets = () => {},
  onNavigate = null,
}) {
  const launch = useLaunchScenario(scenario, { onRunComplete, onError, payloadPlan })

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

  const preflight = useMemo(() => preflightScenario({
    scenario,
    target: selectedTarget,
    agents: launch.agents,
    shelf: shelf.shelf,
    adapterIndex,
    integrations,
    identity: launch.identity,
    pushFormat: launch.pushFormat,
  }), [scenario, selectedTarget, launch.agents, shelf.shelf, adapterIndex, integrations,
       launch.identity, launch.pushFormat])

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
        `${name} is declared by this scenario but is not staged — SimCore will refuse the launch `
        + '(PAYLOAD_NOT_STAGED). Stage it in Tools & Payloads.',
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
          <p>Pick a scenario in the Library to arm it for launch.</p>
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
          <h1>Launch</h1>
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
          onNavigate={onNavigate}
        />

        {/* payload plan — what tooling this run carries, and who fetches it */}
        <PayloadPlanCard
          plan={payloadPlan}
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
          {targetMode === 'push' && (
            <label className="launch-field">
              <span className="launch-field__label">Bundle format</span>
              <select
                className="launch-select"
                value={launch.pushFormat}
                onChange={(e) => launch.setPushFormat(e.target.value)}
              >
                <option value="bash">bash (.sh)</option>
                <option value="k8s">kubernetes (.yml)</option>
              </select>
            </label>
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
                  onClick={launch.launch}
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
                onClick={launch.launch}
              >
                {launch.launching ? 'Launching…' : 'Launch run ▸'}
              </button>
            )}
          </div>

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

          {launch.lastRun && (
            <div className={`launch-result launch-result--${launch.lastRun.status}`} role="status">
              {launch.lastRun.message}
              {launch.lastRun.code && (
                <span className="launch-result__code mono"> [{launch.lastRun.code}]</span>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

/**
 * PayloadPlanCard — what tooling this run carries and who has to reach the
 * internet for it.
 *
 * Three states, none of them silent:
 *   plan present   → artifact, digest, destination, and whether it was renamed.
 *   no plan, tools → a WARNING that the target will fetch them itself. Launch
 *                    stays enabled: the DC may know egress is fine, and a
 *                    consent tick on 50 tools is ceremony. The goal is
 *                    legibility, not permission.
 *   nothing        → the card does not render.
 */
function PayloadPlanCard({
  plan, planAccepted, egressTools, shelfAvailable, onGoShelf, onEdit,
}) {
  const artifacts = plan?.artifacts || []
  if (!artifacts.length && !egressTools.length) return null

  return (
    <section className="launch-card launch-card--payload" data-testid="launch-payload-plan">
      <div className="launch-card__kicker">
        Payload plan
        {onEdit && artifacts.length > 0 && (
          <button type="button" className="btn btn--xs" style={{ marginLeft: 8 }} onClick={onEdit}>
            Edit ▸
          </button>
        )}
      </div>

      {artifacts.map((a) => (
        <div key={a.payload_name} className="launch-payload__row" data-testid={`launch-payload-${a.payload_name}`}>
          <div className="mono launch-payload__path">{a.payload_name} → {a.dest_path}</div>
          <div className="mono launch-payload__meta">
            sha256 {shortDigest(a.sha256)} · served from this SimCore
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
          unchanged while this card claimed a rename. The artifact is staged and served either way;
          the destination above is <strong>not</strong> in effect for this run.
        </div>
      )}

      {egressTools.length > 0 && (
        <div className="launch-payload__warn" data-testid="launch-payload-egress">
          <strong>{egressTools.length}</strong>{' '}
          {egressTools.length === 1 ? 'tool is' : 'tools are'} fetched by the target from the public
          internet at run time:{' '}
          <span className="mono">
            {egressTools.map((t) => t.adapter.name).join(' · ')}
          </span>
          . A default-deny egress policy blocks that, and the step then runs without its tool.
          {onGoShelf && (
            <button type="button" className="btn btn--xs" style={{ marginLeft: 8 }} onClick={onGoShelf}>
              Stage them ▸
            </button>
          )}
        </div>
      )}

      {shelfAvailable === false && (
        <div className="launch-payload__warn" data-testid="launch-payload-unknown">
          This SimCore does not report shelf state, so tool supply for this run is{' '}
          <strong>UNKNOWN</strong> — not &ldquo;fine&rdquo;.
        </div>
      )}
    </section>
  )
}
