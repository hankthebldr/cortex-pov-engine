import React from 'react'

void React

/**
 * LaunchGateNotice — what the container-agent launch gate said about this launch.
 *
 * The gate (core/engine/agent_launch_gate.py) refuses a pull launch BEFORE
 * dispatch when the agent pod cannot run it, because a step that fails for lack
 * of privilege or tooling would read in the POV report as a detection Cortex
 * missed. It also attaches advisory `warnings[]` to a launch it lets through.
 * Neither may be silent in the console:
 *
 *   • warnings      → a non-blocking notice listing each code with its steps or
 *                     detail; the run has already started.
 *   • TOOL_ABSENT   → the missing tools per step, plus an explicit "Launch
 *                     anyway" that re-sends the same launch with the override.
 *   • POSTURE_...   → the missing findings / RBAC rules and the profile that
 *                     would satisfy them. No override: posture cannot be waived.
 *
 * Renders nothing for any other `lastRun`.
 */

export const GATE_TOOL_ABSENT = 'AGENT_TOOL_ABSENT'
export const GATE_POSTURE = 'AGENT_POSTURE_INSUFFICIENT'

export const LAUNCH_ANYWAY_LABEL =
  'Launch anyway (steps needing a missing tool will stop with TOOL_ABSENT)'

function ruleText(r) {
  const groups = (r.api_groups || []).map((g) => (g === '' ? 'core' : g)).join(', ')
  return `${r.scope || 'namespace'}: ${(r.resources || []).join(', ')} — ${(r.verbs || []).join(', ')} (apiGroups ${groups || 'core'})`
}

function Warnings({ warnings, onContinue }) {
  return (
    <div className="deploy-warn" role="status" data-testid="launch-gate-warnings">
      <strong>Run started with launch warnings</strong>
      <ul>
        {warnings.map((w, i) => (
          <li key={`${w.code}-${i}`}>
            <span className="mono">{w.code}</span>
            {w.agent_id && <> on <span className="mono">{w.agent_id}</span></>}
            {Array.isArray(w.steps) && w.steps.length > 0 && <> — steps {w.steps.join(', ')}</>}
            {typeof w.detail === 'string' && w.detail && <> — {w.detail}</>}
          </li>
        ))}
      </ul>
      {onContinue && (
        <button type="button" className="btn btn--xs" onClick={onContinue}>View run ▸</button>
      )}
    </div>
  )
}

function ToolAbsent({ gate, onLaunchAnyway, launching }) {
  const missing = Array.isArray(gate.missing) ? gate.missing : []
  return (
    <div className="launch-result launch-result--error" role="alert" data-testid="launch-gate-refusal">
      <strong>The agent pod lacks tools this scenario calls</strong>
      {gate.agent_id && <> (<span className="mono">{gate.agent_id}</span>)</>}
      <ul>
        {missing.map((m) => (
          <li key={m.step}>
            <span className="mono">{m.step}</span>: {(m.tools || []).join(', ')}
          </li>
        ))}
      </ul>
      {gate.prepare_status && (
        <p>Pod prepare status: <span className="mono">{gate.prepare_status}</span></p>
      )}
      <p>Re-deploy the agent with prepare=packages or an image that carries them, or:</p>
      {onLaunchAnyway && (
        <button type="button" className="btn" disabled={launching} onClick={onLaunchAnyway}>
          {LAUNCH_ANYWAY_LABEL}
        </button>
      )}
    </div>
  )
}

function PostureInsufficient({ gate }) {
  const findings = Array.isArray(gate.missing_findings) ? gate.missing_findings : []
  const rules = Array.isArray(gate.missing_rbac_rules) ? gate.missing_rbac_rules : []
  return (
    <div className="launch-result launch-result--error" role="alert" data-testid="launch-gate-refusal">
      <strong>The agent pod&apos;s posture cannot run this scenario</strong>
      {gate.agent_id && <> (<span className="mono">{gate.agent_id}</span>, profile {gate.agent_profile ?? 'unknown'})</>}
      {findings.length > 0 && (
        <>
          <p>Missing posture findings:</p>
          <ul>{findings.map((f) => <li key={f} className="mono">{f}</li>)}</ul>
        </>
      )}
      {rules.length > 0 && (
        <>
          <p>Missing RBAC rules:</p>
          <ul>{rules.map((r, i) => <li key={i} className="mono">{ruleText(r)}</li>)}</ul>
        </>
      )}
      <p>
        {gate.satisfying_profile
          ? <>Run it from an agent deployed with profile <span className="mono">{gate.satisfying_profile}</span>.</>
          : 'No agent profile provides this posture.'}
      </p>
    </div>
  )
}

export default function LaunchGateNotice({ lastRun, onLaunchAnyway, onContinue, launching = false }) {
  if (!lastRun) return null
  if (lastRun.status === 'success') {
    const warnings = Array.isArray(lastRun.warnings) ? lastRun.warnings : []
    return warnings.length ? <Warnings warnings={warnings} onContinue={onContinue} /> : null
  }
  if (lastRun.status !== 'error' || !lastRun.gate) return null
  if (lastRun.code === GATE_TOOL_ABSENT) {
    return <ToolAbsent gate={lastRun.gate} onLaunchAnyway={onLaunchAnyway} launching={launching} />
  }
  if (lastRun.code === GATE_POSTURE) return <PostureInsufficient gate={lastRun.gate} />
  return null
}
