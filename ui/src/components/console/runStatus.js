/**
 * runStatus — shared run-status helpers.
 *
 * The backend run-status enum is {pending | running | complete | failed |
 * aborted} (Phase 2). Earlier UI code compared against the token 'completed'
 * (with a trailing -d), which never matched a real run — the "last completed
 * run" fallback was silently dead (GAP-API-003).
 *
 * Normalize on the backend token 'complete' everywhere a real run status is
 * compared. These helpers tolerate the legacy 'completed' for safety so a
 * mixed-fleet SimCore (older build) still reads correctly, and they lower-case
 * defensively so callers don't each re-implement the comparison.
 */

/** True when the run finished successfully. Accepts the backend 'complete'
 * token and tolerates the legacy 'completed' alias. */
export function isRunComplete(status) {
  const s = (status || '').toLowerCase()
  return s === 'complete' || s === 'completed'
}

/** True when the run reached a terminal state (no further progress expected).
 * Phase 2 added 'aborted' alongside 'complete'/'failed'. */
export function isRunTerminal(status) {
  const s = (status || '').toLowerCase()
  return isRunComplete(s) || s === 'failed' || s === 'aborted'
}

/**
 * Canonical CSS-state token for a run status. Collapses the backend 'complete'
 * and legacy 'completed' onto the single 'completed' state class (matching the
 * existing `.--completed` styling), and passes the other terminal/active
 * tokens straight through. Unknown/empty falls back to 'unknown'.
 *
 * Returns one of: 'completed' | 'failed' | 'aborted' | 'running' | 'pending'
 * | 'unknown'.
 */
export function runStatusToken(status) {
  const s = (status || '').toLowerCase()
  if (isRunComplete(s)) return 'completed'
  if (s === 'failed' || s === 'aborted' || s === 'running' || s === 'pending') {
    return s
  }
  return 'unknown'
}

/** Display glyph for a run status row. */
export function runStatusGlyph(status) {
  const token = runStatusToken(status)
  if (token === 'completed') return '✓'  // ✓
  if (token === 'failed' || token === 'aborted') return '✗'  // ✗
  if (token === 'running') return '◐'  // ◐
  return '○'  // ○
}
