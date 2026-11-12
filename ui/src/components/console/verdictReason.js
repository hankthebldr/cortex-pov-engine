/**
 * verdictReason — why a run's test-case verdict is `pending` with nothing scored.
 *
 * `engine/verifier.py::score_run` sets `tc_verdict_detail.reason` only when no
 * Result was left to score (§1.3.1 of the API reference): `not_executed` when a
 * step never ran, `soak_pending` when every row is inside its soak window.
 * Neither is "verification outstanding" — verifying again will not move it —
 * so the console names it instead of leaving a bare `pending` chip.
 *
 * The labels match `report_generator.verdict_label`, so the console and the
 * report say the same thing. An unknown reason is shown as its raw token rather
 * than dropped: a reason nobody can see does not help anyone.
 */

export const VERDICT_REASONS = {
  not_executed: {
    label: 'nothing executed',
    tone: 'warn',
    sentence: 'No step that carries a detection ran, so nothing was tested. '
      + 'Re-run the scenario; verifying again will not change this verdict.',
  },
  soak_pending: {
    label: 'detectors soaking',
    tone: 'info',
    sentence: 'Every detection is inside its Analytics activation or dedup window. '
      + 'The verdict resolves when the window closes.',
  },
}

/**
 * @param {object|null|undefined} detail  a run's `tc_verdict_detail`
 * @returns {{key: string, label: string, tone: string, sentence: string|null}|null}
 */
export function verdictReason(detail) {
  const key = detail?.reason
  if (!key) return null
  const known = VERDICT_REASONS[key]
  if (known) return { key, ...known }
  return { key: String(key), label: String(key), tone: 'neutral', sentence: null }
}
