import React, { useMemo } from 'react'
import { preflightScenario, PF, PF_STATUS } from './readiness/scenarioPreflight.js'
import { StatusIcon } from './kit/index.js'

// Why these are checked before the run, as the Preflight label's tooltip.
const WHY_FIRST = "A step that did not run as authored reads as a gap in the customer's coverage."

/** A severity as the kit's StatusIcon and one word. */
function Severity({ status }) {
  const s = PF_STATUS[status] || PF_STATUS[PF.UNKNOWN]
  return <StatusIcon status={s.status} word={s.word} />
}

/**
 * One check, one line: state, label, the count or short summary. A check that
 * is not Ready keeps what it means and what to do behind a collapsed "Why"
 * (its detail and its fix, a line each); the line itself is the toggle.
 */
function CheckRow({ check: c }) {
  const head = (
    <>
      <Severity status={c.status} />
      <span className="preflight__check-label">{c.label}</span>
      <span className="preflight__check-summary">{c.summary}</span>
    </>
  )
  const why = c.status !== PF.PASS && (c.detail || c.remediation)
  return (
    <li
      className={`preflight__check preflight__check--${c.status}`}
      data-testid={`preflight-${c.id}`}
      data-status={c.status}
    >
      {why ? (
        <details className="preflight__why">
          <summary className="preflight__check-head">
            {head}
            <span className="preflight__why-toggle">Why</span>
          </summary>
          <div className="preflight__why-body">
            {c.detail && <p className="preflight__check-detail">{c.detail}</p>}
            {c.remediation && <p className="preflight__check-fix">{c.remediation}</p>}
          </div>
        </details>
      ) : (
        <div className="preflight__check-head">{head}</div>
      )}
    </li>
  )
}

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
 *   • are the ingestion doors its detections read from open? (advisory, never blocks)
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
  doorReadiness = null,
  doorRows = null,
  doorAgentId = null,
  doorError = null,
  onNavigate = null,
}) {
  const pf = useMemo(() => preflightScenario({
    scenario, target, agents, shelf, adapterIndex, integrations, identity, pushFormat,
    doorReadiness, doorRows, doorAgentId, doorError,
  }), [scenario, target, agents, shelf, adapterIndex, integrations, identity, pushFormat,
       doorReadiness, doorRows, doorAgentId, doorError])

  if (!scenario) return null

  return (
    <section className="launch-card launch-card--preflight" data-testid="scenario-preflight">
      <div className="launch-card__kicker">
        <span title={WHY_FIRST}>Preflight</span>
        <span className="preflight-verdict" data-testid="preflight-verdict" data-verdict={pf.verdict}>
          <Severity status={pf.verdict} />
        </span>
      </div>

      <ul className="preflight__checks">
        {pf.checks.map((c) => <CheckRow key={c.id} check={c} />)}
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
