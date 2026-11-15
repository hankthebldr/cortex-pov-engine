/**
 * ResultVerdictLine — a reviewed run's verdict in words, with its reason.
 * The one line on Results that says how the run scored.
 *
 * `engine/verifier.py::score_run` writes `tc_verdict` (pass · fail · pending ·
 * not_applicable) and, on `tc_verdict_detail`, a `detail` sentence and — when
 * no row was left to score — a `reason` (`not_executed`, `soak_pending`). The
 * line shows the verdict as a StatusIcon and one word, then the reason: the
 * named one when there is one (its action sentence in the tooltip), otherwise
 * the scorer's own sentence.
 */
import React from 'react'
import { StatusIcon } from './kit/index.js'
import { verdictReason } from './verdictReason.js'

void React

const VERDICTS = {
  pass: { status: 'up', word: 'Passed' },
  fail: { status: 'down', word: 'Failed' },
  pending: { status: 'unknown', word: 'Pending' },
  not_applicable: { status: 'off', word: 'Not scoreable' },
}

/** What a verdict means when the scorer left no sentence of its own. */
const PLAIN = {
  pending: 'verification outstanding',
  not_applicable: 'no measurable threshold',
}

/**
 * @param {object} props
 * @param {object} props.run        the reviewed run (`tc_verdict`, `tc_verdict_detail`)
 * @param {string} [props.tcRef]    the test case it was scored against
 */
export default function ResultVerdictLine({ run, tcRef = null }) {
  const detail = run?.tc_verdict_detail && typeof run.tc_verdict_detail === 'object' ? run.tc_verdict_detail : null
  const key = String(run?.tc_verdict || detail?.verdict || 'pending').toLowerCase()
  const verdict = VERDICTS[key] || { status: 'unknown', word: key.replace(/_/g, ' ') }
  const reason = verdictReason(detail)
  const why = reason
    ? reason.label
    : (typeof detail?.detail === 'string' && detail.detail.trim()) || PLAIN[key] || null
  return (
    <div className="result-verdict" data-testid="result-verdict" data-verdict={key}>
      <span className="result-verdict__k">Verdict</span>
      <StatusIcon status={verdict.status} word={verdict.word} />
      {why && (
        <span className="result-verdict__why" title={reason?.sentence || why}>{why}</span>
      )}
      {tcRef && <span className="result-verdict__tc mono">{tcRef}</span>}
    </div>
  )
}
