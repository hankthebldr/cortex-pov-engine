/**
 * RunVerdictLine — a run's test-case verdict with the reason it is pending
 * (design C3, "CHANGES SINCE v26").
 *
 * `engine/verifier.py::score_run` sets `tc_verdict_detail.reason` only when no
 * row was left to score: `not_executed` (no step that carries a detection ran)
 * or `soak_pending` (rows are still inside their window). The Topology and
 * Techniques tabs already show it; this puts the same verdict and reason where
 * a DC lands first, the run's own page:
 *
 *   variant="header"  under the lifecycle strip: "Test case" · PENDING ·
 *                     reason chip · the test case id, then the action as a
 *                     visible sentence (never a tooltip).
 *   variant="engine"  under the chain: the Cortex Engine terminal states the
 *                     same verdict and reason.
 *
 * Renders nothing when the verdict carries no reason, so a scored run's page
 * is unchanged.
 */
import React from 'react'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import { scenarioIdOf } from '../../api/ids.js'
import VerdictReason, { VerdictReasonSentence } from './VerdictReason.jsx'
import { verdictReason } from './verdictReason.js'

void React

export default function RunVerdictLine({ run, variant = 'header' }) {
  const { scenarios } = useEnvironment()
  const detail = run?.tc_verdict_detail
  if (!verdictReason(detail)) return null

  const verdict = String(run?.tc_verdict || 'pending').toLowerCase()
  const chip = (
    <span
      className={`chip run-topo__verdict-chip run-topo__verdict-chip--${verdict}`}
      data-testid={`run-verdict-${variant}-verdict`}
    >
      {verdict.replace(/_/g, ' ')}
    </span>
  )

  if (variant === 'engine') {
    return (
      <div className="run-verdict run-verdict--engine" data-testid="run-verdict-engine">
        <span className="run-verdict__engine-key mono">CORTEX ENGINE</span>
        <span className="run-verdict__k">terminal verdict</span>
        <span className="run-verdict__spacer" />
        {chip}
        <VerdictReason detail={detail} testId="run-verdict-engine-reason" />
      </div>
    )
  }

  const scenario = scenarios.find((s) => scenarioIdOf(s) === run?.scenario_id)
  const tcRef = scenario?.tc_ref || null
  return (
    <div className="run-verdict run-verdict--header" data-testid="run-verdict-header">
      <span className="run-verdict__k">Test case</span>
      <span className="run-verdict__body">
        <span className="run-verdict__chips">
          {chip}
          <VerdictReason detail={detail} testId="run-verdict-header-reason" />
          {tcRef && <span className="run-verdict__tc mono">{tcRef}</span>}
        </span>
        <VerdictReasonSentence detail={detail} className="run-verdict__sentence" />
      </span>
    </div>
  )
}
