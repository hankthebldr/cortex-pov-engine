/**
 * Server timestamps → epoch milliseconds, read in the zone SimCore wrote them in.
 *
 * WHY THIS EXISTS: SimCore stamps every timestamp with `datetime.utcnow()` into
 * a timezone-less `DateTime` column and serialises it with `.isoformat()` — so
 * `/api/agents`, `/api/runs`, `/api/results` and the SSE bus all ship strings
 * like `2026-10-04T12:00:00.123456`. They are UTC by construction, but carry
 * NO zone designator, and ECMAScript parses a zone-less date-time as LOCAL
 * time. Every `new Date(serverTs)` in the console was therefore shifted by the
 * browser's UTC offset:
 *
 *   - a DC in NAM (UTC−4…−8) saw every timestamp hours in the FUTURE, so a
 *     beacon that died at 09:00 still read "live · seen 0s ago" until early
 *     afternoon, and a running run's elapsed time was negative;
 *   - a DC in EMEA/APAC (UTC+1…+10) saw every timestamp hours in the PAST, so
 *     a beacon that checked in a second ago read "stale · seen 2h ago".
 *
 * CI runs in UTC, where the offset is zero — which is why nothing caught it.
 *
 * A string that DOES carry a zone (`Z`, `+00:00`, `-0500`) is parsed as-is; only
 * a zone-less ISO date-time is read as UTC, because that is what SimCore means
 * by it. Anything else falls through to `Date.parse`, so garbage stays NaN and
 * callers keep rendering their dash rather than a fabricated time.
 */

// YYYY-MM-DD[T ]HH:MM[:SS[.fraction]] with nothing after it.
const NAIVE_ISO_DATETIME = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)$/

/**
 * @param {string|number|Date|null|undefined} value
 * @returns {number} epoch milliseconds, or NaN when the value is not a time
 */
export function parseServerTime(value) {
  if (value == null || value === '') return NaN
  if (value instanceof Date) return value.getTime()
  if (typeof value === 'number') return value
  const s = String(value).trim()
  const m = NAIVE_ISO_DATETIME.exec(s)
  if (m) return Date.parse(`${m[1]}T${m[2]}Z`)
  return Date.parse(s)
}

/**
 * Same as parseServerTime, as a Date (an Invalid Date when unparseable).
 * @param {string|number|Date|null|undefined} value
 * @returns {Date}
 */
export function serverDate(value) {
  return new Date(parseServerTime(value))
}
