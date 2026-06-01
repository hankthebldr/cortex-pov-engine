import React, { useEffect } from 'react'
import useLaunchScenario from './useLaunchScenario.js'

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
  onRunComplete = () => {},
  onError = () => {},
  onGoLibrary = () => {},
  onGoTargets = () => {},
}) {
  const launch = useLaunchScenario(scenario, { onRunComplete, onError })

  // Derive mode + agent from the chosen target — the operator picks a target,
  // not a transport. agent → pull; push/iac → push bundle.
  const targetMode = selectedTarget?.kind === 'agent' ? 'pull' : 'push'
  useEffect(() => {
    if (!selectedTarget) return
    launch.setMode(targetMode)
    if (selectedTarget.kind === 'agent') launch.setSelectedAgent(selectedTarget.id)
  }, [selectedTarget, targetMode]) // eslint-disable-line react-hooks/exhaustive-deps

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

          {/* primary action */}
          <div className="launch-actions">
            {targetMode === 'push' ? (
              <>
                <button
                  type="button"
                  className="btn btn--primary btn--lg"
                  disabled={launch.launching}
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
                disabled={launch.launchDisabled || !selectedTarget}
                onClick={launch.launch}
              >
                {launch.launching ? 'Launching…' : 'Launch run ▸'}
              </button>
            )}
          </div>

          {launch.lastRun && (
            <div className={`launch-result launch-result--${launch.lastRun.status}`}>
              {launch.lastRun.message}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
