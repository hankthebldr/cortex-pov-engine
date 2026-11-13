/**
 * verdictReason — why a run's test-case verdict is `pending` with nothing scored.
 *
 * `engine/verifier.py::score_run` sets `tc_verdict_detail.reason` only when no
 * Result was left to score (§1.3.1 of the API reference): `soak_pending` when
 * any row is still inside its soak window (it ran, and a read back after the
 * window closes can still score the run), `not_executed` when no row is (only
 * a re-run can). Neither is "verification outstanding", so the console names
 * it instead of leaving a bare `pending` chip.
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
    sentence: 'The detections that ran are still inside their Analytics activation or dedup window. '
      + 'Read the run back after the window closes to score it.',
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
