import React, { useMemo } from 'react'
import { preflightScenario, PF, PF_CHIP, PF_LABEL } from './readiness/scenarioPreflight.js'

/**
 * ScenarioPreflightCard — the per-scenario readiness check, rendered where the
 * operator launches rather than on a page they will not visit.
 *
 * It answers, for THIS scenario against THIS target, right now:
 *   • are its tools staged, or will the target reach the internet mid-run?
 *   • is the beacon online, and does it advertise what this scenario needs?
 *   • can this target run the platforms the steps declare?
 *   • will the declared execution identity actually resolve on that host?
 *   • if the scenario carries a scoreable threshold, can anything measure it?
 *
 * It does NOT disable Launch. LaunchView's `blockers` list owns that, and it
 * only ever lists conditions SimCore itself will refuse. A preflight that
 * blocks a launch it merely disapproves of is worse than the failure it
 * prevents — a gate that stops legitimate work in front of a customer.
 * `block` here means "SimCore's own guard will refuse this", which is why
 * those rows are surfaced up into `blockers` by the caller, not by this card.
 */
export default function ScenarioPreflightCard({
  scenario = null,
  target = null,
  agents = [],
  shelf = null,
  adapterIndex = {},
  integrations = null,
  identity = '',
  pushFormat = 'bash',
  onNavigate = null,
}) {
  const pf = useMemo(() => preflightScenario({
    scenario, target, agents, shelf, adapterIndex, integrations, identity, pushFormat,
  }), [scenario, target, agents, shelf, adapterIndex, integrations, identity, pushFormat])

  if (!scenario) return null

  return (
    <section className="launch-card launch-card--preflight" data-testid="scenario-preflight">
      <div className="launch-card__kicker">
        Preflight
        <span
          className={PF_CHIP[pf.verdict]}
          style={{ marginLeft: 8 }}
          data-testid="preflight-verdict"
          data-verdict={pf.verdict}
        >
          {PF_LABEL[pf.verdict]}
        </span>
      </div>

      <p className="preflight__lede">
        Checked before the run, because every failure below produces the same artefact: a run that
        did not execute as authored, whose absent detections read in a POV report as a gap in the{' '}
        <strong>customer&apos;s</strong> coverage.
      </p>

      <ul className="preflight__checks">
        {pf.checks.map((c) => (
          <li
            key={c.id}
            className={`preflight__check preflight__check--${c.status}`}
            data-testid={`preflight-${c.id}`}
            data-status={c.status}
          >
            <div className="preflight__check-head">
              <span className={PF_CHIP[c.status]}>{PF_LABEL[c.status]}</span>
              <span className="preflight__check-label">{c.label}</span>
              <span className="preflight__check-summary mono">{c.summary}</span>
            </div>
            {c.status !== PF.PASS && c.detail && (
              <div className="preflight__check-detail">{c.detail}</div>
            )}
            {c.status !== PF.PASS && c.remediation && (
              <div className="preflight__check-fix">→ {c.remediation}</div>
            )}
          </li>
        ))}
      </ul>

      {pf.verdict === PF.UNKNOWN && (
        <div className="preflight__unknown" data-testid="preflight-unknown-note">
          One or more checks could not be run. <strong>Unknown is not ready.</strong> Launching now
          is a valid choice — you may know the environment better than this console does — but if
          the run comes back empty, look here first.
        </div>
      )}

      {onNavigate && (
        <div className="preflight__actions">
          <button type="button" className="btn btn--xs" onClick={() => onNavigate('readiness')}>
            Full readiness ▸
          </button>
        </div>
      )}
    </section>
  )
}

export { preflightScenario }
