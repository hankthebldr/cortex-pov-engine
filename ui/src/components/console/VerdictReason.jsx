/**
 * VerdictReason — the chip that says why a run verdict is pending with nothing
 * scored, plus `VerdictReasonSentence`, the line that says what to do about it.
 * One element for every surface that shows a run verdict, so the Techniques
 * tab, the Topology Engine terminal and the UC / TC run rollup read the same.
 *
 * The dashed border is the "never ran" signal; the tone colours the ink only,
 * so the chip never competes with the verdict chip it sits beside. The
 * sentence is visible text, not a tooltip: a `title` is unreachable by
 * keyboard and by most screen readers.
 */
import React from 'react'
import { verdictReason } from './verdictReason.js'

function resolve(detail, reason) {
  return verdictReason(detail ?? (reason ? { reason } : null))
}

/**
 * @param {object} props
 * @param {object} [props.detail]  a run's `tc_verdict_detail`
 * @param {string} [props.reason]  a bare reason token (rollups carry no detail)
 * @param {number} [props.count]   prefix a run count ("1 nothing executed")
 * @param {string} [props.testId]
 */
export default function VerdictReason({ detail, reason, count, testId }) {
  const r = resolve(detail, reason)
  if (!r) return null
  return (
    <span
      className={`chip verdict-reason verdict-reason--${r.tone}`}
      data-testid={testId}
      data-reason={r.key}
      title={r.sentence || undefined}
    >
      {count == null ? r.label : `${count} ${r.label}`}
    </span>
  )
}

/**
 * @param {object} props
 * @param {object} [props.detail]
 * @param {string} [props.reason]
 * @param {string} [props.testId]
 * @param {string} [props.className]  the host surface's sentence class
 */
export function VerdictReasonSentence({ detail, reason, testId, className = '' }) {
  const r = resolve(detail, reason)
  if (!r?.sentence) return null
  return (
    <p className={`verdict-reason__sentence ${className}`.trim()} data-testid={testId}>
      {r.sentence}
    </p>
  )
}
